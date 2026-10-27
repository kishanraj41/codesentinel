"""Text of the official ICD-10-CM guideline PDF, for verbatim checks.

A passage counts as official only when it appears in the PDF that CMS or CDC
publishes for that fiscal year. Matching ignores whitespace, the running page
header, and typographic quote and dash variants, because extractors differ on
those. Everything else has to match character for character.
"""

from __future__ import annotations

import bisect
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from codesentinel.guidelines.editions import OFFICIAL_DOMAINS, Edition

_HEADER = re.compile(
    r"ICD-10-CM Official Guidelines for Coding and Reporting\s*FY\s*\d{4}\s*Page\s*\d+\s*of\s*\d+",
    re.IGNORECASE,
)
_FOLD = str.maketrans({
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
    "\u2018": "'", "\u2019": "'", "\u2032": "'", "`": "'",
    "\u2013": "-", "\u2014": "-", "\u2012": "-", "\u2212": "-", "\u00ad": "",
    "\u00a0": " ",
})
MIN_VERBATIM_CHARS = 40

# Section headings in the order they appear. A passage gets the last heading
# that starts before it. Table-of-contents entries (dot leaders) are skipped.
SECTION_MARKERS: tuple[tuple[str, str], ...] = (
    ("I.A.15", "15. “With”"),
    ("I.A.16", "16. “See” and “See Also”"),
    ("I.B", "B. General Coding Guidelines"),
    ("I.C.4", "Chapter 4: Endocrine, Nutritional, and Metabolic Diseases"),
    ("I.C.4.a", "a. Diabetes mellitus"),
    ("I.C.4.a.1", "1) Type of diabetes"),
    ("I.C.4.a.2", "2) Type of diabetes mellitus not documented"),
    ("I.C.4.a.3", "3) Diabetes mellitus and the use of insulin"),
    ("I.C.4.a.4", "4) Diabetes mellitus in pregnancy"),
    ("I.C.4.a.5", "5) Complications due to insulin pump malfunction"),
    ("I.C.4.a.6", "6) Secondary diabetes mellitus"),
    ("I.C.5", "Chapter 5: Mental, Behavioral and Neurodevelopmental disorders"),
    ("I.C.9", "Chapter 9: Diseases of the Circulatory System"),
    ("I.C.9.a", "a. Hypertension"),
    ("I.C.9.a.1", "1) Hypertension with Heart Disease"),
    ("I.C.9.a.2", "2) Hypertensive Chronic Kidney Disease"),
    ("I.C.9.a.3", "3) Hypertensive Heart and Chronic Kidney Disease"),
    ("I.C.9.a.4", "4) Hypertensive Cerebrovascular Disease"),
    ("I.C.9.a.5", "5) Hypertensive Retinopathy"),
    ("I.C.9.a.6", "6) Hypertension, Secondary"),
    ("I.C.9.b", "b. Atherosclerotic Coronary Artery Disease"),
    ("I.C.10", "Chapter 10: Diseases of the Respiratory System"),
    ("I.C.14", "Chapter 14: Diseases of Genitourinary System"),
    ("I.C.14.a", "a. Chronic kidney disease"),
    ("I.C.14.a.1", "1) Stages of chronic kidney disease (CKD)"),
    ("I.C.14.a.2", "2) Chronic kidney disease and kidney transplant status"),
    ("I.C.14.a.3", "3) Chronic kidney disease with other conditions"),
    ("I.C.15", "Chapter 15: Pregnancy, Childbirth, and the Puerperium"),
)


def _fold_char(ch: str) -> str:
    return unicodedata.normalize("NFKC", ch.translate(_FOLD))


def strip_headers(text: str) -> str:
    return _HEADER.sub(" ", text or "")


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def fold(text: str) -> str:
    """Whitespace removed, quotes and dashes folded, headers removed."""
    out = []
    for ch in strip_headers(text):
        folded = _fold_char(ch)
        for piece in folded:
            if not piece.isspace():
                out.append(piece)
    return "".join(out)


@dataclass
class Match:
    page: int
    start: int
    end: int
    text: str
    section: str


class OfficialText:
    """Per-page text of one guideline PDF with a folded index for verbatim lookups."""

    def __init__(self, edition: Edition, pages: list[str], sha256: str = "", source_url: str = ""):
        self.edition = edition
        self.sha256 = sha256
        self.source_url = source_url or edition.pdf_url
        self.page_texts = pages
        parts: list[str] = []
        self.page_starts: list[int] = []
        offset = 0
        for raw in pages:
            cleaned = collapse(strip_headers(raw))
            self.page_starts.append(offset)
            parts.append(cleaned)
            offset += len(cleaned) + 1
        self.text = " ".join(parts)
        folded: list[str] = []
        index: list[int] = []
        for position, ch in enumerate(self.text):
            for piece in _fold_char(ch):
                if not piece.isspace():
                    folded.append(piece)
                    index.append(position)
        self._folded = "".join(folded)
        self._index = index
        self._markers = self._find_markers()

    @property
    def sha_matches_pin(self) -> bool:
        return bool(self.edition.sha256) and self.sha256 == self.edition.sha256

    def _find_markers(self) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []
        for section, heading in SECTION_MARKERS:
            for match in re.finditer(re.escape(collapse(heading)), self.text):
                tail = self.text[match.end(): match.end() + 400]
                if re.match(r"[^.]{0,300}\.{5,}", tail):
                    continue  # table of contents entry
                found.append((match.start(), section))
        found.sort()
        return found

    def section_at(self, position: int) -> str:
        section = ""
        for start, name in self._markers:
            if start > position:
                break
            section = name
        return section

    def page_at(self, position: int) -> int:
        return bisect.bisect_right(self.page_starts, position)

    def find(self, quote: str) -> Optional[Match]:
        needle = fold(quote)
        if len(needle) < MIN_VERBATIM_CHARS:
            return None
        at = self._folded.find(needle)
        if at < 0:
            return None
        start = self._index[at]
        end = self._index[at + len(needle) - 1] + 1
        return Match(
            page=self.page_at(start),
            start=start,
            end=end,
            text=self.text[start:end],
            section=self.section_at(start),
        )

    def contains(self, quote: str) -> bool:
        return self.find(quote) is not None


def _official_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS)


def extract_pages(pdf_bytes: bytes) -> list[str]:
    """pypdf when installed, else poppler's pdftotext."""
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(io.BytesIO(pdf_bytes))
        return [page.extract_text() or "" for page in reader.pages]
    except ImportError:
        pass
    binary = shutil.which("pdftotext")
    if not binary:
        raise RuntimeError("no PDF text extractor: install pypdf or poppler-utils")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "guidelines.pdf"
        path.write_bytes(pdf_bytes)
        out = subprocess.run([binary, str(path), "-"], capture_output=True, check=True)
    return out.stdout.decode("utf-8", errors="replace").split("\f")


_memo: dict[int, OfficialText] = {}
_memo_lock = threading.Lock()


def reset_official_text_cache() -> None:
    with _memo_lock:
        _memo.clear()


def remember(text: OfficialText) -> None:
    with _memo_lock:
        _memo[text.edition.fiscal_year] = text


def load_official_text(
    edition: Edition,
    *,
    cache_dir: Optional[Path | str] = None,
    fetch: Optional[Callable] = None,
) -> OfficialText:
    """Fetch the edition's PDF from cms.gov or cdc.gov once per process.

    Extracted pages are cached on disk next to the Tavily cache. Raises when
    no official URL could be fetched and read.
    """
    with _memo_lock:
        cached = _memo.get(edition.fiscal_year)
    if cached is not None:
        return cached
    disk = Path(cache_dir) / "official_text" if cache_dir else None
    if disk is not None:
        for path in sorted(disk.glob(f"fy{edition.fiscal_year}_*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                text = OfficialText(edition, payload["pages"], payload.get("sha256", ""), payload.get("url", ""))
            except (OSError, ValueError, KeyError):
                continue
            remember(text)
            return text
    if fetch is None:
        import requests

        fetch = requests.get
    errors: list[str] = []
    for url in edition.urls:
        if not _official_url(url):
            errors.append(f"{url}: not an official domain")
            continue
        try:
            response = fetch(url, timeout=60, headers={"User-Agent": "CodeSentinel guideline verifier"})
            response.raise_for_status()
            body = response.content
            if not body.startswith(b"%PDF"):
                raise ValueError("response is not a PDF")
            pages = extract_pages(body)
        except Exception as exc:  # network, HTTP, parse
            errors.append(f"{urlparse(url).hostname}: {type(exc).__name__}")
            continue
        digest = hashlib.sha256(body).hexdigest()
        text = OfficialText(edition, pages, digest, url)
        if disk is not None:
            try:
                disk.mkdir(parents=True, exist_ok=True)
                (disk / f"fy{edition.fiscal_year}_{digest[:12]}.json").write_text(
                    json.dumps({"url": url, "sha256": digest, "pages": pages}), encoding="utf-8"
                )
            except OSError:
                pass
        remember(text)
        return text
    raise RuntimeError(f"could not load the {edition.label} guideline PDF ({'; '.join(errors)})")
