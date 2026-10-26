"""Tavily search + extract for official ICD-10-CM / CMS pages, with a disk cache.

Recorded fixtures are used when TAVILY_API_KEY is unset. Fixture quotes are
labeled as fixtures. They are not presented as live guideline text.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

FIXTURE_PATH = Path(__file__).with_name("fixtures.json")
FIXTURE_CACHED_AT = "2026-10-24T00:00:00+00:00"
SEARCH_URL = "https://api.tavily.com/search"
EXTRACT_URL = "https://api.tavily.com/extract"
OFFICIAL_SUFFIXES = ("cms.gov", "cdc.gov")

ISSUE_QUERIES = {
    "combination-code-diabetes-ckd": (
        "ICD-10-CM official guidelines 'with' convention diabetes chronic kidney disease combination code E11.22"
    ),
    "combination-code-hypertensive-ckd": (
        "ICD-10-CM official guidelines hypertension chronic kidney disease combination code I12.9"
    ),
    "specificity-hyperglycemia": (
        "ICD-10-CM official guidelines diabetes hyperglycemia specificity E11.65 versus E11.9"
    ),
    "combination-code": "ICD-10-CM official guidelines combination code conventions",
    "specificity-upgrade": "ICD-10-CM official guidelines code to highest level of specificity",
}


@dataclass
class Citation:
    url: str
    quote: str
    source: str
    title: str
    retrieved: str
    cached_at: Optional[str]
    code_family: str
    issue: str

    def as_dict(self) -> dict:
        return asdict(self)


def _load_fixtures() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _hostname(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _official(url: str) -> bool:
    host = _hostname(url)
    return any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_SUFFIXES)


# Distinctive terms. Generic "level of specificity" / code-length text is not enough,
# and a kidney-transplant passage is not a diabetes combination citation.
_ISSUE_NEEDLES: dict[str, tuple[tuple[str, int], ...]] = {
    "specificity-hyperglycemia": (
        ("e11.65", 8),
        ("hyperglycemia", 6),
        ("e11.9", 5),
        ("diabetes", 4),
        ("e11", 4),
    ),
    "combination-code-diabetes-ckd": (
        ("e11.22", 8),
        ("diabetic", 5),
        ("diabetes", 5),
        ("e11", 4),
    ),
    "combination-code-hypertensive-ckd": (
        ("i12.9", 8),
        ("i13", 6),
        ("i12", 6),
        ("hypertensive", 5),
    ),
    "combination-code": (
        ("combination code", 6),
        ("combination codes", 6),
    ),
}
_FAMILY_NEEDLES: dict[str, tuple[tuple[str, int], ...]] = {
    "E11": (("e11", 5), ("diabetes", 5), ("hyperglycemia", 4)),
    "E78": (("e78", 5), ("hyperlipidemia", 5), ("cholesterol", 4)),
    "I10": (("i10", 5), ("hypertension", 5)),
    "I11": (("i11", 5), ("hypertensive heart", 5)),
    "I12": (("i12", 5), ("hypertensive chronic kidney", 6), ("hypertensive ckd", 6)),
    "I13": (("i13", 5), ("hypertensive heart", 5)),
    "I50": (("i50", 6), ("heart failure", 6), ("congestive", 4)),
    "J44": (("j44", 5), ("copd", 5), ("chronic obstructive", 5)),
    "N18": (("n18", 5), ("chronic kidney", 5), ("ckd", 4)),
    "E03": (("e03", 5), ("hypothyroid", 5)),
}
_CODE_LENGTH = re.compile(
    r"highest number of characters|3,\s*4,\s*5,\s*6,?\s*or\s*7 characters|level of detail in coding",
    re.IGNORECASE,
)


def _needles(code_family: str, issue: str) -> tuple[tuple[str, int], ...]:
    family = (code_family or "").upper()
    issue_key = (issue or "").lower()
    if issue_key == "specificity-upgrade":
        return _FAMILY_NEEDLES.get(family, ((family.lower(), 5),) if family else ())
    specific = _ISSUE_NEEDLES.get(issue_key)
    if specific:
        return specific
    return _FAMILY_NEEDLES.get(family, ((family.lower(), 5),) if family else ())


def iter_passages(text: str) -> list[str]:
    """Paragraphs and sentences, so a relevant sentence can beat a junk lead-in."""
    raw = text or ""
    found: list[str] = []
    seen: set[str] = set()

    def add(chunk: str) -> None:
        cleaned = re.sub(r"\s+", " ", chunk or "").strip()
        key = cleaned.lower()
        if len(cleaned) < 24 or key in seen:
            return
        seen.add(key)
        found.append(cleaned[:600])

    for paragraph in re.split(r"\n+", raw):
        add(paragraph)
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            add(sentence)
    return found


def passage_score(passage: str, code_family: str, issue: str) -> int:
    """How well a passage matches the code family and guideline issue.

    Zero means the passage does not mention the target code, category, or issue
    terms. Generic code-length text and an off-topic code list score zero.
    """
    text = (passage or "").lower()
    if len(text) < 24:
        return 0
    score = 0
    for needle, weight in _needles(code_family, issue):
        if needle and needle in text:
            score += weight
    if score <= 0:
        return 0
    # "Highest number of characters" mentions specificity without the condition.
    if _CODE_LENGTH.search(passage) and score < 6:
        return 0
    issue_key = (issue or "").lower()
    if issue_key == "combination-code-diabetes-ckd" and not any(
        term in text for term in ("diabetes", "diabetic", "e11")
    ):
        return 0
    if issue_key == "specificity-hyperglycemia" and not any(
        term in text for term in ("hyperglycemia", "diabetes", "e11")
    ):
        return 0
    if issue_key == "specificity-upgrade":
        family = (code_family or "").upper()
        family_terms = [needle for needle, _weight in _FAMILY_NEEDLES.get(family, ())]
        if family_terms and not any(term in text for term in family_terms):
            return 0
    return score


def relevant_quote(text: str, code_family: str, issue: str) -> Optional[str]:
    """Pick the passage that mentions the target. None when every passage is off-topic."""
    best = ""
    best_score = 0
    for passage in iter_passages(text):
        score = passage_score(passage, code_family, issue)
        if score > best_score or (score == best_score and score > 0 and best and len(passage) < len(best)):
            best_score = score
            best = passage
    if best_score <= 0:
        return None
    return best[:600].strip()


def _credits(payload: dict, default: float) -> float:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if isinstance(usage, dict):
        for key in ("credits", "credit", "total_credits"):
            if usage.get(key) is not None:
                try:
                    return float(usage[key])
                except (TypeError, ValueError):
                    break
    return default


class GuidelineClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        cache_dir: Optional[str] = None,
        use_fixtures: Optional[bool] = None,
        transport: Optional[object] = None,
        now: Optional[datetime] = None,
    ):
        if api_key is None:
            api_key = os.environ.get("TAVILY_API_KEY", "")
        self.api_key = (api_key or "").strip()
        self.use_fixtures = (not self.api_key) if use_fixtures is None else use_fixtures
        cache_root = cache_dir or os.environ.get("GUIDELINE_CACHE_DIR") or ".cache/guidelines"
        self.cache_dir = Path(cache_root)
        self.ttl = timedelta(seconds=int(os.environ.get("GUIDELINE_CACHE_TTL_SECONDS", str(7 * 24 * 3600))))
        self._post = transport or requests.post
        self._now = now or datetime.now(timezone.utc)
        self.credits = 0.0
        self.cache_hits = 0
        self.live_requests = 0
        self.fixture_hits = 0
        self.degraded_reasons: list[str] = []
        self._memory: dict[str, list[Citation]] = {}

    def lookup(self, code_family: str, issue: str) -> list[Citation]:
        key = f"{code_family}|{issue}"
        if key in self._memory:
            self.cache_hits += 1
            return self._memory[key]
        cached = self._read_disk(key, code_family, issue)
        if cached:
            self.cache_hits += 1
            self._memory[key] = cached
            return cached
        if self.use_fixtures or not self.api_key:
            found = self._from_fixture(key, code_family, issue)
            self._memory[key] = found
            if found:
                self.fixture_hits += 1
            return found
        try:
            found = self._live(code_family, issue)
        except Exception as exc:
            self.degraded_reasons.append(f"Tavily lookup failed for {key}: {exc}")
            found = self._from_fixture(key, code_family, issue)
            if found:
                self.fixture_hits += 1
            self._memory[key] = found
            return found
        if found:
            self._write_disk(key, found)
        self._memory[key] = found
        return found

    def _from_fixture(self, key: str, code_family: str, issue: str) -> list[Citation]:
        fixtures = _load_fixtures()
        row = fixtures.get(key) or fixtures.get(f"*|{issue}")
        if not row:
            return []
        return [
            Citation(
                url=row["url"],
                quote=row["quote"],
                source=row.get("source") or _hostname(row["url"]),
                title=row.get("title") or "",
                retrieved="fixture",
                cached_at=FIXTURE_CACHED_AT,
                code_family=code_family,
                issue=issue,
            )
        ]

    def _cache_path(self, key: str) -> Path:
        safe = key.replace("|", "__").replace("/", "_")
        return self.cache_dir / f"{safe}.json"

    def _read_disk(self, key: str, code_family: str, issue: str) -> list[Citation]:
        path = self._cache_path(key)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(payload["cached_at"])
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return []
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        if self._now - cached_at > self.ttl:
            return []
        citations = []
        for row in payload.get("citations") or []:
            focused = relevant_quote(row.get("quote") or "", code_family, issue)
            if not focused:
                self.degraded_reasons.append(
                    f"Dropped low-confidence cached citation for {code_family}|{issue}"
                )
                continue
            citations.append(
                Citation(
                    url=row.get("url") or "",
                    quote=focused,
                    source=row.get("source") or _hostname(row.get("url") or ""),
                    title=row.get("title") or "",
                    retrieved="cache",
                    cached_at=payload.get("cached_at"),
                    code_family=code_family,
                    issue=issue,
                )
            )
        return [item for item in citations if item.url and item.quote]

    def _write_disk(self, key: str, citations: list[Citation]) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "cached_at": self._now.isoformat(),
                "citations": [
                    {"url": item.url, "quote": item.quote, "source": item.source, "title": item.title}
                    for item in citations
                ],
            }
            self._cache_path(key).write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            self.degraded_reasons.append(f"guideline cache write failed: {exc}")

    def _live(self, code_family: str, issue: str) -> list[Citation]:
        query = ISSUE_QUERIES.get(issue) or (
            f"ICD-10-CM official guidelines {code_family} {issue.replace('-', ' ')}"
        )
        search = self._post_json(
            SEARCH_URL,
            {
                "api_key": self.api_key,
                "query": query,
                "search_depth": "advanced",
                "include_domains": ["cms.gov", "cdc.gov"],
                "chunks_per_source": 3,
                "max_results": 5,
                "include_usage": True,
            },
        )
        self.live_requests += 1
        self.credits += _credits(search, 2)
        results = [row for row in (search.get("results") or []) if _official(row.get("url") or "")]
        if not results:
            return []
        best_url = ""
        best_title = ""
        best_quote = ""
        best_score = 0

        def consider(url: str, title: str, text: str) -> None:
            nonlocal best_url, best_title, best_quote, best_score
            focused = relevant_quote(text, code_family, issue)
            if not focused:
                return
            score = passage_score(focused, code_family, issue)
            if score > best_score:
                best_score = score
                best_url = url
                best_title = title
                best_quote = focused

        for row in results:
            consider(row.get("url") or "", row.get("title") or "", row.get("content") or "")
        # One extract of the top page when a snippet already matches. Otherwise try the next pages.
        extract_targets = results[:1] if best_score else results[:3]
        for row in extract_targets:
            url = row.get("url") or ""
            if not url:
                continue
            try:
                extracted = self._post_json(
                    EXTRACT_URL,
                    {
                        "api_key": self.api_key,
                        "urls": [url],
                        "query": query,
                        "chunks_per_source": 3,
                        "include_usage": True,
                    },
                )
            except Exception as exc:
                self.degraded_reasons.append(f"Tavily extract failed for {url}: {exc}")
                continue
            self.live_requests += 1
            self.credits += _credits(extracted, 1)
            consider(url, row.get("title") or "", _extract_quote(extracted))
            if best_score:
                break
        if not best_quote:
            self.degraded_reasons.append(
                f"No relevant guideline passage for {code_family}|{issue}; dropped low-confidence citation"
            )
            return []
        return [
            Citation(
                url=best_url,
                quote=best_quote,
                source=_hostname(best_url),
                title=best_title,
                retrieved="live",
                cached_at=None,
                code_family=code_family,
                issue=issue,
            )
        ]

    def _post_json(self, url: str, body: dict) -> dict:
        response = self._post(url, json=body, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Tavily response was not a JSON object")
        return payload


def _extract_quote(payload: dict) -> str:
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    chunks: list[str] = []
    for row in results if isinstance(results, list) else []:
        if not isinstance(row, dict):
            continue
        for key in ("raw_content", "content", "text"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                chunks.append(value.strip())
                break
        for chunk in row.get("chunks") or []:
            if isinstance(chunk, str) and chunk.strip():
                chunks.append(chunk.strip())
            elif isinstance(chunk, dict):
                text = chunk.get("text") or chunk.get("content") or ""
                if str(text).strip():
                    chunks.append(str(text).strip())
    return "\n".join(chunks[:3]).strip()
