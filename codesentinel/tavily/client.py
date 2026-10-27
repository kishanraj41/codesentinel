"""Tavily search + extract for official ICD-10-CM / CMS pages, with a disk cache.

Recorded fixtures are used when TAVILY_API_KEY is unset. Fixture quotes are
labeled as fixtures. They are not presented as live guideline text.
"""

from __future__ import annotations

import hashlib
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
# Bump when passage matching changes so a previous empty lookup is not reused.
FILTER_VERSION = "2"
OFFICIAL_GUIDELINE_TITLE = "ICD-10-CM Official Guidelines for Coding and Reporting"
OFFICIAL_QUERY_SUFFIX = f'"{OFFICIAL_GUIDELINE_TITLE}" FY2026'

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
    source_tier: str = ""

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
# Heart-failure specificity does not require the literal string I50.22.
_HF_WORD = re.compile(r"heart[\s-]*failure|\bchf\b", re.IGNORECASE)
_HF_CODE = re.compile(r"\bi50\.[234]\d*", re.IGNORECASE)
_HF_TYPE = re.compile(
    r"acute\s+on\s+chronic|\bchronic\b|\bsystolic\b|\bdiastolic\b|\bhfref\b|\bhfpef\b|\bi50\.[234]",
    re.IGNORECASE,
)


def guideline_query(code_family: str, issue: str) -> str:
    """Search text. I50 specificity names the type; every query asks for the FY2026 guidelines."""
    family = (code_family or "").upper()
    issue_key = (issue or "").lower()
    if family == "I50" and issue_key == "specificity-upgrade":
        base = (
            "ICD-10-CM heart failure specificity systolic diastolic chronic "
            "acute on chronic HFrEF HFpEF I50.2 I50.3 I50.4"
        )
    else:
        base = ISSUE_QUERIES.get(issue_key) or (
            f"ICD-10-CM official guidelines {code_family} {issue_key.replace('-', ' ')}"
        )
    if OFFICIAL_QUERY_SUFFIX in base:
        return base
    return f"{base} {OFFICIAL_QUERY_SUFFIX}"


def lookup_key(code_family: str, issue: str, query: Optional[str] = None) -> str:
    """Cache identity: the query plus the passage-filter version."""
    text = query if query is not None else guideline_query(code_family, issue)
    family = (code_family or "").upper()
    issue_key = (issue or "").lower()
    return f"v{FILTER_VERSION}|{text}|{family}|{issue_key}"


def disk_cache_path(cache_dir: Path | str, code_family: str, issue: str) -> Path:
    """On-disk path for this query and filter version."""
    query = guideline_query(code_family, issue)
    return _path_for(Path(cache_dir), code_family, issue, query)


def _path_for(cache_dir: Path, code_family: str, issue: str, query: str) -> Path:
    digest = hashlib.sha256(lookup_key(code_family, issue, query).encode("utf-8")).hexdigest()[:24]
    family = re.sub(r"[^A-Za-z0-9]+", "", (code_family or "na").upper()) or "na"
    issue_safe = re.sub(r"[^A-Za-z0-9]+", "", (issue or "na").lower()) or "na"
    return cache_dir / f"{family}__{issue_safe}__{digest}.json"


def _heart_failure_specificity_score(text: str) -> int:
    """I50 specificity: heart failure plus a type term, or an I50.2/I50.3/I50.4 code.

    Exact billable strings such as I50.22 are sufficient and not required.
    HFrEF or HFpEF counts as the type when acuity or systolic/diastolic is also named.
    """
    if _HF_CODE.search(text):
        return 8
    has_type = _HF_TYPE.search(text) is not None
    if _HF_WORD.search(text) and has_type:
        return 7
    if re.search(r"\bhfref\b|\bhfpef\b", text, flags=re.IGNORECASE) and re.search(
        r"acute\s+on\s+chronic|\bchronic\b|\bsystolic\b|\bdiastolic\b",
        text,
        flags=re.IGNORECASE,
    ):
        return 7
    return 0


def _is_official_guidelines(url: str, title: str) -> bool:
    blob = f"{url} {title}".lower().replace("_", "-")
    if "official guidelines for coding and reporting" in blob:
        return True
    return "icd-10-cm-coding-guidelines" in blob


def _is_fy2026(url: str, title: str) -> bool:
    blob = f"{url} {title}".lower()
    return "fy-2026" in blob or "fy2026" in blob or "fy 2026" in blob


def source_tier_for(url: str, title: str) -> str:
    """`official` is the ICD-10-CM Official Guidelines PDF. Anything else is secondary."""
    if _is_official_guidelines(url, title):
        return "official"
    return "secondary"


def source_rank(url: str, title: str) -> int:
    """FY2026 official guidelines outrank other guideline years, then other CMS/CDC pages."""
    blob = f"{url} {title}".lower()
    rank = 0
    if _is_official_guidelines(url, title):
        rank += 1000
        if _is_fy2026(url, title):
            rank += 100
        else:
            rank += 40
    if ".pdf" in blob and _is_official_guidelines(url, title):
        rank += 5
    if "mmrr" in blob or "icd-9" in blob or "icd9" in blob:
        rank -= 80
    if "mearis" in blob or "ntap" in blob or "new-technology" in blob:
        rank -= 80
    return rank


def _prefers(candidate: dict, current: dict) -> bool:
    """Official guidelines beat every secondary page. FY2026 beats other official years."""
    if not current.get("quote"):
        return True
    new_key = (
        1 if candidate.get("tier") == "official" else 0,
        1 if candidate.get("fy2026") else 0,
        int(candidate.get("score") or 0),
        int(candidate.get("rank") or 0),
    )
    old_key = (
        1 if current.get("tier") == "official" else 0,
        1 if current.get("fy2026") else 0,
        int(current.get("score") or 0),
        int(current.get("rank") or 0),
    )
    return new_key > old_key


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
    family = (code_family or "").upper()
    issue_key = (issue or "").lower()
    score = 0
    for needle, weight in _needles(code_family, issue):
        if needle and needle in text:
            score += weight
    loose = 0
    if family == "I50" and issue_key == "specificity-upgrade":
        loose = _heart_failure_specificity_score(text)
        score = max(score, loose)
    if score <= 0:
        return 0
    # "Highest number of characters" mentions specificity without the condition.
    if _CODE_LENGTH.search(passage) and score < 6:
        return 0
    if issue_key == "combination-code-diabetes-ckd" and not any(
        term in text for term in ("diabetes", "diabetic", "e11")
    ):
        return 0
    if issue_key == "specificity-hyperglycemia" and not any(
        term in text for term in ("hyperglycemia", "diabetes", "e11")
    ):
        return 0
    if issue_key == "specificity-upgrade" and loose <= 0:
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
    if best_score > 0:
        return best[:600].strip()
    return _window_around_match(text, code_family, issue)


def _window_around_match(text: str, code_family: str, issue: str) -> Optional[str]:
    """A long extract with no line breaks still yields the sentence that matches."""
    raw = re.sub(r"\s+", " ", text or "").strip()
    if len(raw) < 24:
        return None
    lowered = raw.lower()
    positions: list[tuple[int, int]] = []
    for needle, weight in _needles(code_family, issue):
        if not needle:
            continue
        start = lowered.find(needle)
        if start >= 0:
            positions.append((weight, start))
    if (code_family or "").upper() == "I50" and (issue or "").lower() == "specificity-upgrade":
        for match in _HF_WORD.finditer(raw):
            positions.append((6, match.start()))
        for match in _HF_TYPE.finditer(raw):
            positions.append((7, match.start()))
        for match in _HF_CODE.finditer(raw):
            positions.append((8, match.start()))
    if not positions:
        return None
    positions.sort(key=lambda item: (-item[0], item[1]))
    start = positions[0][1]
    left = max(0, start - 180)
    window = raw[left:left + 600].strip()
    if passage_score(window, code_family, issue) <= 0:
        return None
    return window[:600].strip()


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
        fixture_key = f"{code_family}|{issue}"
        query = guideline_query(code_family, issue)
        key = lookup_key(code_family, issue, query)
        if key in self._memory:
            self.cache_hits += 1
            return self._memory[key]
        cached = self._read_disk(code_family, issue, query)
        if cached is not None:
            self.cache_hits += 1
            self._memory[key] = cached
            return cached
        if self.use_fixtures or not self.api_key:
            found = self._from_fixture(fixture_key, code_family, issue)
            self._memory[key] = found
            if found:
                self.fixture_hits += 1
            return found
        try:
            found = self._live(code_family, issue, query)
        except Exception as exc:
            self.degraded_reasons.append(f"Tavily lookup failed for {fixture_key}: {exc}")
            found = self._from_fixture(fixture_key, code_family, issue)
            if found:
                self.fixture_hits += 1
            self._memory[key] = found
            return found
        # Empty and matching lookups are both cached, keyed on the query and filter version.
        self._write_disk(code_family, issue, query, found)
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

    def _cache_path(self, code_family: str, issue: str, query: str) -> Path:
        return _path_for(self.cache_dir, code_family, issue, query)

    def _read_disk(self, code_family: str, issue: str, query: str) -> Optional[list[Citation]]:
        """Citations on a hit, an empty list for a cached no-match, or None on a miss.

        A file whose quotes all fail the current filter is a miss, so a junk cache
        does not stick. A file stored with no citations is a negative hit.
        """
        path = self._cache_path(code_family, issue, query)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(payload["cached_at"])
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return None
        if payload.get("query") != query or str(payload.get("filter_version")) != FILTER_VERSION:
            return None
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        if self._now - cached_at > self.ttl:
            return None
        rows = payload.get("citations") or []
        if not rows:
            return []
        citations = []
        for row in rows:
            focused = relevant_quote(row.get("quote") or "", code_family, issue)
            if not focused:
                self.degraded_reasons.append(
                    f"Dropped low-confidence cached citation for {code_family}|{issue}"
                )
                continue
            url = row.get("url") or ""
            title = row.get("title") or ""
            tier = row.get("source_tier") or source_tier_for(url, title)
            if tier not in {"official", "secondary"}:
                tier = source_tier_for(url, title)
            citations.append(
                Citation(
                    url=url,
                    quote=focused,
                    source=row.get("source") or _hostname(url),
                    title=title,
                    retrieved="cache",
                    cached_at=payload.get("cached_at"),
                    code_family=code_family,
                    issue=issue,
                    source_tier=tier,
                )
            )
        kept = [item for item in citations if item.url and item.quote]
        if not kept:
            return None
        return kept

    def _write_disk(self, code_family: str, issue: str, query: str, citations: list[Citation]) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "cached_at": self._now.isoformat(),
                "query": query,
                "filter_version": FILTER_VERSION,
                "citations": [
                    {
                        "url": item.url,
                        "quote": item.quote,
                        "source": item.source,
                        "title": item.title,
                        "source_tier": item.source_tier,
                    }
                    for item in citations
                ],
            }
            self._cache_path(code_family, issue, query).write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            self.degraded_reasons.append(f"guideline cache write failed: {exc}")

    def _live(self, code_family: str, issue: str, query: str) -> list[Citation]:
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
        best = {"url": "", "title": "", "quote": "", "score": 0, "rank": -10**9, "tier": "", "fy2026": False}

        def consider(url: str, title: str, text: str) -> None:
            focused = relevant_quote(text, code_family, issue)
            if not focused:
                return
            score = passage_score(focused, code_family, issue)
            if score <= 0:
                return
            tier = source_tier_for(url, title)
            candidate = {
                "url": url,
                "title": title,
                "quote": focused,
                "score": score,
                "rank": source_rank(url, title),
                "tier": tier,
                "fy2026": tier == "official" and _is_fy2026(url, title),
            }
            if _prefers(candidate, best):
                best.update(candidate)

        ordered = sorted(
            results,
            key=lambda row: (
                source_tier_for(row.get("url") or "", row.get("title") or "") == "official",
                source_rank(row.get("url") or "", row.get("title") or ""),
            ),
            reverse=True,
        )
        for row in ordered:
            consider(row.get("url") or "", row.get("title") or "", row.get("content") or "")
        official_rows = [
            row for row in ordered
            if source_tier_for(row.get("url") or "", row.get("title") or "") == "official"
        ]
        secondary_rows = [
            row for row in ordered
            if source_tier_for(row.get("url") or "", row.get("title") or "") != "official"
        ]
        # Extract official guideline pages, FY2026 first, before accepting a secondary page.
        extracts = 0
        for row in official_rows[:3]:
            if extracts >= 3:
                break
            url = row.get("url") or ""
            title = row.get("title") or ""
            if best["tier"] == "official" and source_rank(url, title) < best["rank"]:
                continue
            if self._extract_into(row, query, code_family, issue, consider):
                extracts += 1
            if best.get("fy2026"):
                break
        if best["tier"] != "official":
            # A matching secondary snippet is enough. Extract only when no snippet matched.
            if not best["quote"]:
                for row in secondary_rows:
                    if extracts >= 3:
                        break
                    if self._extract_into(row, query, code_family, issue, consider):
                        extracts += 1
                    if best["quote"]:
                        break
        if not best["quote"]:
            self.degraded_reasons.append(
                f"No relevant guideline passage for {code_family}|{issue}; dropped low-confidence citation"
            )
            return []
        return [
            Citation(
                url=best["url"],
                quote=best["quote"],
                source=_hostname(best["url"]),
                title=best["title"],
                retrieved="live",
                cached_at=None,
                code_family=code_family,
                issue=issue,
                source_tier=best["tier"] or source_tier_for(best["url"], best["title"]),
            )
        ]

    def _extract_into(self, row: dict, query: str, code_family: str, issue: str, consider) -> bool:
        """Extract one URL. Returns True when the request was counted."""
        url = row.get("url") or ""
        if not url:
            return False
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
            return False
        self.live_requests += 1
        self.credits += _credits(extracted, 1)
        consider(url, row.get("title") or "", _extract_quote(extracted))
        return True

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
