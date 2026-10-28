"""Guideline citations: the current fiscal year's official text, found live by Tavily.

Live mode (TAVILY_API_KEY set):

1. The current edition of the ICD-10-CM Official Guidelines is chosen from the
   date (FY 2027 from 2026-10-01). Its PDF is fetched once per process from
   cms.gov or cdc.gov and indexed for verbatim checks.
2. One Tavily search per code family and issue, restricted to cms.gov and
   cdc.gov, returns passages from that edition's PDF. A passage is cited only
   if it appears verbatim in the fetched PDF text. The citation carries the
   fiscal year, URL, section, and page, with provenance ``tavily_live``.
3. Curated anchors are the fallback and the cross-check. Each anchor is
   re-verified against the current PDF. If it is still there and no live
   passage verified, the anchor is cited as ``curated_fallback``. If the
   current edition no longer contains it, the anchor is ``superseded``: it is
   never cited, and the gate decides on the live passage or holds the row.

Mock mode (no key) cites curated anchors, which tests check against the
committed excerpt of the current edition. Codes without an anchor fall back to
labeled fixtures; those are not presented as live guideline text.
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

from codesentinel.guidelines import OFFICIAL_PDF_URL, OFFICIAL_TITLE, anchor_for, load_anchors
from codesentinel.guidelines.editions import EDITIONS, Edition, current_edition, edition_for_url
from codesentinel.guidelines.official_text import (
    SECTION_MARKERS,
    OfficialText,
    fold,
    load_official_text,
    reset_official_text_cache,
    strip_headers,
)

FIXTURE_PATH = Path(__file__).with_name("fixtures.json")
FIXTURE_CACHED_AT = "2026-10-24T00:00:00+00:00"
SEARCH_URL = "https://api.tavily.com/search"
EXTRACT_URL = "https://api.tavily.com/extract"
OFFICIAL_SUFFIXES = ("cms.gov", "cdc.gov")
# Bump when passage matching changes so a previous empty lookup is not reused.
FILTER_VERSION = "4"
OFFICIAL_GUIDELINE_TITLE = "ICD-10-CM Official Guidelines for Coding and Reporting"
OFFICIAL_QUERY_SUFFIX = f'"{OFFICIAL_GUIDELINE_TITLE}" FY{current_edition().fiscal_year}'
MAX_QUOTE_CHARS = 900

# Live queries name the rule text a coder would cite. The edition label is added.
LIVE_QUERIES = {
    "E11|combination-code-diabetes-ckd": (
        '"with" convention presumes a causal relationship between the two conditions '
        "diabetes chronic kidney disease"
    ),
    "E11|specificity-hyperglycemia": (
        "diabetes mellitus codes are combination codes type of diabetes body system affected "
        "complications assign as many codes from categories E08-E13"
    ),
    "I12|combination-code-hypertensive-ckd": (
        "Assign codes from category I12 Hypertensive chronic kidney disease when both hypertension "
        "and a condition classifiable to category N18 are present"
    ),
    "I13|combination-code-hypertensive-ckd": (
        "category I13 Hypertensive heart and chronic kidney disease combination codes "
        "hypertension heart disease chronic kidney disease"
    ),
    "I50|specificity-upgrade": (
        "hypertensive heart and chronic kidney disease if heart failure is present assign an additional "
        "code from category I50 to identify the type of heart failure"
    ),
    "N18|specificity-upgrade": (
        "ICD-10-CM classifies CKD based on severity stages 1-5 stage 3 codes N18.30-N18.32 moderate CKD"
    ),
}

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
    source_label: str = ""
    section: str = ""
    page: Optional[int] = None
    # Provenance: tavily_live, curated_fallback, or fixture.
    provenance: str = ""
    fiscal_year: str = ""
    verified_verbatim: bool = False
    verified_against: str = ""
    pdf_sha256: str = ""
    # verified, superseded, unverified, or none (no curated anchor for this code).
    anchor_status: str = ""
    supersedes: Optional[dict] = None

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
# Official long titles for the codes this auditor cites. A passage that prints a
# different title next to the code is a mislabeled list and is not evidence.
OFFICIAL_CODE_TITLES: dict[str, str] = {
    "I50.20": "Unspecified systolic (congestive) heart failure",
    "I50.21": "Acute systolic (congestive) heart failure",
    "I50.22": "Chronic systolic (congestive) heart failure",
    "I50.23": "Acute on chronic systolic (congestive) heart failure",
    "I50.30": "Unspecified diastolic (congestive) heart failure",
    "I50.31": "Acute diastolic (congestive) heart failure",
    "I50.32": "Chronic diastolic (congestive) heart failure",
    "I50.33": "Acute on chronic diastolic (congestive) heart failure",
    "I50.40": "Unspecified combined systolic (congestive) and diastolic (congestive) heart failure",
    "I50.41": "Acute combined systolic (congestive) and diastolic (congestive) heart failure",
    "I50.42": "Chronic combined systolic (congestive) and diastolic (congestive) heart failure",
    "I50.43": "Acute on chronic combined systolic (congestive) and diastolic (congestive) heart failure",
    "I50.9": "Heart failure, unspecified",
    "E11.9": "Type 2 diabetes mellitus without complications",
    "E11.21": "Type 2 diabetes mellitus with diabetic nephropathy",
    "E11.22": "Type 2 diabetes mellitus with diabetic chronic kidney disease",
    "E11.29": "Type 2 diabetes mellitus with other diabetic kidney complication",
    "E11.65": "Type 2 diabetes mellitus with hyperglycemia",
    "I12.0": "Hypertensive chronic kidney disease with stage 5 chronic kidney disease or end stage renal disease",
    "I12.9": "Hypertensive chronic kidney disease with stage 1 through stage 4 chronic kidney disease, or unspecified chronic kidney disease",
    "N18.1": "Chronic kidney disease, stage 1",
    "N18.2": "Chronic kidney disease, stage 2 (mild)",
    "N18.3": "Chronic kidney disease, stage 3 (moderate)",
    "N18.30": "Chronic kidney disease, stage 3 unspecified",
    "N18.31": "Chronic kidney disease, stage 3a",
    "N18.32": "Chronic kidney disease, stage 3b",
    "N18.4": "Chronic kidney disease, stage 4 (severe)",
    "N18.5": "Chronic kidney disease, stage 5",
    "N18.6": "End stage renal disease",
    "N18.9": "Chronic kidney disease, unspecified",
}
# (acuity, kind). I50.9 has no systolic/diastolic/combined kind.
_HF_EXPECT: dict[str, tuple[str, str]] = {
    "I50.20": ("unspecified", "systolic"),
    "I50.21": ("acute", "systolic"),
    "I50.22": ("chronic", "systolic"),
    "I50.23": ("acute on chronic", "systolic"),
    "I50.30": ("unspecified", "diastolic"),
    "I50.31": ("acute", "diastolic"),
    "I50.32": ("chronic", "diastolic"),
    "I50.33": ("acute on chronic", "diastolic"),
    "I50.40": ("unspecified", "combined"),
    "I50.41": ("acute", "combined"),
    "I50.42": ("chronic", "combined"),
    "I50.43": ("acute on chronic", "combined"),
    "I50.9": ("unspecified", ""),
}
_DM_COMPLICATION: dict[str, frozenset[str]] = {
    "E11.9": frozenset(),
    "E11.21": frozenset({"kidney"}),
    "E11.22": frozenset({"kidney"}),
    "E11.29": frozenset({"kidney"}),
    "E11.65": frozenset({"hyperglycemia"}),
}
# "1-4" accepts stage 1 through 4 or unspecified, and rejects stage 5 / ESRD.
_STAGE_EXPECT: dict[str, str] = {
    "I12.9": "1-4",
    "I12.0": "5",
    "N18.1": "1",
    "N18.2": "2",
    "N18.3": "3",
    "N18.30": "3",
    "N18.31": "3",
    "N18.32": "3",
    "N18.4": "4",
    "N18.5": "5",
    "N18.6": "5",
    "N18.9": "unspecified",
}
_ICD_TOKEN = re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d{1,4})?\b", re.IGNORECASE)
_LABEL_SKIP = re.compile(
    r"^(rather|when|if|for|is|are|was|were|than|instead|which|that|to|and|or|the|a|an|of|in|on|as|by|from|with|versus|vs|not|only)\b",
    re.IGNORECASE,
)


def source_label_for(tier: str) -> str:
    """Human-readable source label. Secondary is shown as such in the UI and API."""
    if tier == "secondary":
        return "Secondary source"
    if tier == "official":
        return "Official guidelines"
    return ""


def _hf_observed(text: str) -> tuple[str, str]:
    lowered = text.lower()
    if "acute on chronic" in lowered:
        acuity = "acute on chronic"
    elif re.search(r"\bunspecified\b", lowered):
        acuity = "unspecified"
    elif re.search(r"\bacute\b", lowered):
        acuity = "acute"
    elif re.search(r"\bchronic\b", lowered):
        acuity = "chronic"
    else:
        acuity = ""
    if "combined" in lowered or ("systolic" in lowered and "diastolic" in lowered):
        kind = "combined"
    elif "systolic" in lowered:
        kind = "systolic"
    elif "diastolic" in lowered:
        kind = "diastolic"
    else:
        kind = ""
    return acuity, kind


def _code_labels(text: str) -> list[tuple[str, str]]:
    """Code plus the title glued to it, stopping at the next code or a sentence break."""
    pairs: list[tuple[str, str]] = []
    for match in _ICD_TOKEN.finditer(text or ""):
        code = match.group(0).upper()
        if code not in OFFICIAL_CODE_TITLES:
            continue
        rest = text[match.end(): match.end() + 140]
        nxt = _ICD_TOKEN.search(rest)
        if nxt:
            rest = rest[: nxt.start()]
        rest = re.split(r"[.;]", rest, maxsplit=1)[0]
        rest = rest.strip(" \t,:–—-")
        if not rest or _LABEL_SKIP.match(rest):
            continue
        pairs.append((code, rest))
    return pairs


def _hf_conflict(code: str, desc: str) -> bool:
    expected_acuity, expected_kind = _HF_EXPECT[code]
    observed_acuity, observed_kind = _hf_observed(desc)
    if not observed_acuity and not observed_kind:
        return False
    if observed_acuity and expected_acuity and observed_acuity != expected_acuity:
        return True
    if observed_kind and expected_kind and observed_kind != expected_kind:
        return True
    if observed_kind and not expected_kind:
        return True
    return False


def _dm_conflict(code: str, desc: str) -> bool:
    expected = _DM_COMPLICATION[code]
    lowered = desc.lower()
    observed: set[str] = set()
    if "hyperglycemia" in lowered or "out of control" in lowered:
        observed.add("hyperglycemia")
    if re.search(r"kidney|ckd|nephrop", lowered):
        observed.add("kidney")
    if not observed:
        return False
    if not expected:
        return True
    return not observed.issubset(expected)


def _stage_conflict(code: str, desc: str) -> bool:
    expected = _STAGE_EXPECT[code]
    lowered = desc.lower()
    end_stage = "end stage" in lowered or "end-stage" in lowered or re.search(r"\besrd\b", lowered) is not None
    if expected == "1-4":
        return "stage 5" in lowered or end_stage
    if expected == "5":
        if "stage 5" in lowered or end_stage:
            return False
        return re.search(r"stage\s*[1-4]\b", lowered) is not None
    if expected == "unspecified":
        return re.search(r"stage\s*[1-5]\b", lowered) is not None or end_stage
    found = re.search(r"stage\s*([1-5])", lowered)
    if not found:
        return False
    return found.group(1) != expected


def description_conflict(text: str) -> bool:
    """True when a code is printed next to a title that is not the official one.

    I50.20 is unspecified systolic failure. A list that calls it acute combined
    systolic and diastolic failure is rejected, even if neighboring codes are right.
    """
    for code, desc in _code_labels(text):
        if code in _HF_EXPECT and _hf_conflict(code, desc):
            return True
        if code in _DM_COMPLICATION and _dm_conflict(code, desc):
            return True
        if code in _STAGE_EXPECT and _stage_conflict(code, desc):
            return True
    return False


def _rule_bonus(text: str) -> int:
    """Rule sentences outrank a bare code list. The words have to state the rule."""
    lowered = (text or "").lower()
    bonus = 0
    if "should be coded" in lowered or "should be assigned" in lowered:
        bonus += 6
    if re.search(r"\bassign(?:ed|ing)?\b", lowered):
        bonus += 5
    if "presum" in lowered:
        bonus += 5
    if "use additional code" in lowered:
        bonus += 5
    if re.search(r"\bwith\b", lowered) and any(
        token in lowered for token in ("convention", "presum", "causal", "associated condition", "interpreted")
    ):
        bonus += 5
    return bonus


def _is_pure_code_list(text: str) -> bool:
    """Three or more codes and no instruction is a list, not a guideline rule."""
    if len(_ICD_TOKEN.findall(text or "")) < 3:
        return False
    return _rule_bonus(text) < 5


def passage_is_strong(quote: str, tier: str) -> bool:
    """Official rule text. A secondary page, or a list, is not strong enough to stop."""
    return bool(quote) and tier == "official" and _rule_bonus(quote) >= 5


def guideline_query(code_family: str, issue: str) -> str:
    """Search text. I50 specificity names the type; every query asks for the current-year guidelines."""
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


def rule_query(code_family: str, issue: str) -> str:
    """Second search, aimed at the guideline section, when the first passage is weak."""
    family = (code_family or "").upper()
    issue_key = (issue or "").lower()
    if issue_key == "combination-code-diabetes-ckd":
        base = (
            'ICD-10-CM Official Guidelines section I.A.15 "With" convention '
            '"Diabetes mellitus and the associated conditions" presumed causal relationship'
        )
    elif issue_key == "specificity-hyperglycemia":
        base = (
            'ICD-10-CM Official Guidelines section I.C.4.a diabetes '
            '"out of control" hyperglycemia assign E11.65'
        )
    elif issue_key == "combination-code-hypertensive-ckd":
        base = (
            "ICD-10-CM Official Guidelines hypertensive chronic kidney disease "
            "assign I12 presumed relationship use additional code"
        )
    elif family == "I50" and issue_key == "specificity-upgrade":
        base = (
            "ICD-10-CM Official Guidelines heart failure code to type and acuity "
            "systolic diastolic combined acute chronic hypertensive heart disease assign"
        )
    else:
        base = (
            f"ICD-10-CM Official Guidelines assign {family} {issue_key.replace('-', ' ')} "
            "should be coded"
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


def _is_current_fy(url: str, title: str) -> bool:
    """The edition in force today (FY 2027 from 2026-10-01)."""
    edition = current_edition()
    if edition.matches_url(url):
        return True
    blob = f"{url} {title}".lower()
    year = edition.fiscal_year
    return f"fy-{year}" in blob or f"fy{year}" in blob or f"fy {year}" in blob


# Kept for callers that imported the old name.
_is_fy2026 = _is_current_fy


def source_tier_for(url: str, title: str) -> str:
    """`official` is the ICD-10-CM Official Guidelines PDF. Anything else is secondary."""
    if _is_official_guidelines(url, title):
        return "official"
    return "secondary"


def source_rank(url: str, title: str) -> int:
    """Current-year official guidelines outrank other guideline years, then other CMS/CDC pages."""
    blob = f"{url} {title}".lower()
    rank = 0
    if _is_official_guidelines(url, title):
        rank += 1000
        if _is_current_fy(url, title):
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
    """Official guidelines beat every secondary page. The current year beats other official years."""
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
    terms. Generic code-length text, a pure code list, and a passage that
    mislabels a code score zero. Rule language raises a matching passage.
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
    if description_conflict(passage):
        return 0
    if _is_pure_code_list(passage):
        return 0
    return score + _rule_bonus(passage)


def relevant_quote(text: str, code_family: str, issue: str) -> Optional[str]:
    """Pick the passage that mentions the target. None when every passage is off-topic.

    A code glued to the wrong title rejects the whole extract. Official rule text
    is preferred over quoting a neighboring line from that list.
    """
    if description_conflict(text):
        return None
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


_official_text_failures: dict[int, str] = {}


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


def reset_pdf_confirmation() -> None:
    """Clear process-wide guideline state (PDF text and fetch failures). Tests call this."""
    _official_text_failures.clear()
    reset_official_text_cache()


def live_query(code_family: str, issue: str, edition: Optional[Edition] = None) -> str:
    """Tavily query for the current edition's rule text."""
    edition = edition or current_edition()
    family = (code_family or "").upper()
    issue_key = (issue or "").lower()
    base = LIVE_QUERIES.get(f"{family}|{issue_key}") or (
        f"{family} {issue_key.replace('-', ' ')} assign code"
    )
    return f'"{OFFICIAL_GUIDELINE_TITLE}" {edition.label} {base}'


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])[\"”’)]?\s+(?=[A-Z0-9“\"(])")
_CHUNK_BREAK = re.compile(r"\s*\[\s*(?:\.\.\.|…)\s*\]\s*|\s*(?:\.\.\.|…)\s+")


def _heading_breaks(text: str) -> str:
    """Section headings end a chunk, so a quote never straddles two sections."""
    for _section, heading in SECTION_MARKERS:
        text = text.replace(heading, " [...] ")
    return text


def _sentences(content: str) -> list[list[str]]:
    """Chunks split into sentences. A sentence cut by a chunk break is dropped later by verification."""
    groups: list[list[str]] = []
    collapsed = re.sub(r"\s+", " ", strip_headers(content or ""))
    for chunk in _CHUNK_BREAK.split(_heading_breaks(collapsed)):
        cleaned = re.sub(r"\s+", " ", chunk).strip()
        if len(cleaned) < 24:
            continue
        groups.append([part.strip() for part in _SENTENCE_BREAK.split(cleaned) if part.strip()])
    return groups


def verified_passage(
    content: str,
    text: OfficialText,
    code_family: str,
    issue: str,
    *,
    prefer_span: Optional[tuple[int, int]] = None,
):
    """The best run of whole sentences from a Tavily passage that is verbatim in the PDF.

    Returns the PDF match (page, section, exact text) or None. A run that
    overlaps ``prefer_span`` (where the curated anchor sits) wins, then the
    relevance score, then the overlap, then the longer run.
    """
    best = None
    best_key = None
    for sentences in _sentences(content):
        for i in range(len(sentences)):
            for j in range(i + 1, min(len(sentences), i + 5) + 1):
                candidate = " ".join(sentences[i:j])
                if len(candidate) > MAX_QUOTE_CHARS:
                    break
                # Whole sentences only: a chunk can start or stop mid-sentence.
                if not re.match(r"[A-Z0-9“\"(]", candidate) or not re.search(r"[.!?][\"”’)]?$", candidate):
                    continue
                match = text.find(candidate)
                if match is None:
                    continue
                score = passage_score(match.text, code_family, issue)
                overlap = 0
                if prefer_span:
                    overlap = max(0, min(match.end, prefer_span[1]) - max(match.start, prefer_span[0]))
                if score <= 0 and overlap <= 0:
                    continue
                # A passage where the curated anchor sits is on topic even when it never names
                # the code (the "with" convention does not mention diabetes).
                key = (overlap > 0, score, overlap, len(match.text))
                if best_key is None or key > best_key:
                    best, best_key = match, key
    return best


def _anchor_neighborhood(row: Optional[dict], text: OfficialText) -> Optional[tuple[int, int]]:
    """Where a superseded anchor used to sit: the span of its sentences that still match."""
    if not row:
        return None
    spans = []
    for group in _sentences(str(row.get("quote") or "")):
        for sentence in group:
            match = text.find(sentence)
            if match is not None:
                spans.append((match.start, match.end))
    if not spans:
        return None
    return min(start for start, _ in spans), max(end for _, end in spans)


class GuidelineClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        cache_dir: Optional[str] = None,
        use_fixtures: Optional[bool] = None,
        transport: Optional[object] = None,
        now: Optional[datetime] = None,
        official_text: Optional[OfficialText] = None,
        pdf_fetch: Optional[object] = None,
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
        self.edition = current_edition(self._now)
        self._official_text = official_text
        if pdf_fetch is not None:
            self._pdf_fetch = pdf_fetch
        elif transport is not None:
            # An injected Tavily transport means a test: never fetch the real PDF.
            self._pdf_fetch = _no_pdf_fetch
        else:
            self._pdf_fetch = requests.get
        self.credits = 0.0
        self.cache_hits = 0
        self.live_requests = 0
        self.fixture_hits = 0
        self.live_citations = 0
        self.fallback_citations = 0
        self.superseded_anchors: list[str] = []
        self.degraded_reasons: list[str] = []
        # Informational: a fallback that is working as designed, not a failure.
        self.notes: list[str] = []
        self._memory: dict[str, list[Citation]] = {}

    # ---- entry point -------------------------------------------------------------------

    def lookup(self, code_family: str, issue: str) -> list[Citation]:
        family = (code_family or "").upper()
        issue_key = (issue or "").strip().lower()
        mem_key = f"{self.edition.fiscal_year}|{family}|{issue_key}"
        if mem_key in self._memory:
            self.cache_hits += 1
            return self._memory[mem_key]
        if self.use_fixtures or not self.api_key:
            found = self._offline(family, issue_key)
        else:
            found = self._current_year(family, issue_key)
        self._memory[mem_key] = found
        return found

    # ---- offline ------------------------------------------------------------------------

    def _offline(self, family: str, issue: str) -> list[Citation]:
        anchored = self._from_anchor(family, issue)
        if anchored is not None:
            self.fallback_citations += 1
            return anchored
        return self._legacy_lookup(family, issue)

    # ---- live, current fiscal year ------------------------------------------------------

    def _load_text(self) -> Optional[OfficialText]:
        if self._official_text is not None:
            return self._official_text
        year = self.edition.fiscal_year
        if year in _official_text_failures:
            reason = _official_text_failures[year]
            if reason not in self.degraded_reasons:
                self.degraded_reasons.append(reason)
            return None
        try:
            self._official_text = load_official_text(
                self.edition, cache_dir=self.cache_dir, fetch=self._pdf_fetch
            )
        except Exception as exc:
            reason = (
                f"Official {self.edition.label} guideline PDF could not be loaded for verbatim checks "
                f"({_safe_reason(exc)}); curated anchors used"
            )
            _official_text_failures[year] = reason
            self.degraded_reasons.append(reason)
            return None
        return self._official_text

    def _anchor_check(self, row: Optional[dict], text: Optional[OfficialText]):
        """(status, match) for the curated anchor against the current edition."""
        if not row:
            return "none", None
        quote = str(row.get("quote") or "")
        if text is None:
            return "unverified", None
        match = text.find(quote)
        if match is None:
            return "superseded", None
        return "verified", match

    def _current_year(self, family: str, issue: str) -> list[Citation]:
        text = self._load_text()
        row = anchor_for(family, issue)
        status, anchor_match = self._anchor_check(row, text)
        live: Optional[Citation] = None
        if text is not None:
            prefer = (anchor_match.start, anchor_match.end) if anchor_match else _anchor_neighborhood(row, text)
            try:
                live = self._live_verified(family, issue, text, prefer)
            except Exception as exc:
                self.degraded_reasons.append(
                    f"Tavily lookup failed for {family}|{issue}: {type(exc).__name__}"
                )
                live = None
        if live is not None:
            live.anchor_status = status
            if status == "superseded":
                live.supersedes = {
                    "fiscal_year": str(row.get("verified_fiscal_year") or load_anchors().get("fiscal_year") or ""),
                    "section": str(row.get("section") or ""),
                    "page": row.get("page"),
                    "quote": str(row.get("quote") or ""),
                }
                self.superseded_anchors.append(f"{family}|{issue}")
            self.live_citations += 1
            return [live]
        if status == "superseded":
            self.superseded_anchors.append(f"{family}|{issue}")
            self.degraded_reasons.append(
                f"Curated passage for {family}|{issue} is not in the {self.edition.label} guidelines and no "
                "live passage verified; no citation, so the row stays in review"
            )
            return []
        if row:
            anchored = self._from_anchor(family, issue, match=anchor_match, status=status, text=text)
            if anchored is not None:
                self.fallback_citations += 1
                return anchored
        return self._legacy_lookup(family, issue, text=text)

    def _live_cache_path(self, family: str, issue: str, query: str) -> Path:
        digest = hashlib.sha256(
            f"v{FILTER_VERSION}|{self.edition.fiscal_year}|{query}|{family}|{issue}".encode("utf-8")
        ).hexdigest()[:24]
        safe_family = re.sub(r"[^A-Za-z0-9]+", "", family or "na") or "na"
        safe_issue = re.sub(r"[^A-Za-z0-9]+", "", issue or "na") or "na"
        return self.cache_dir / f"live_fy{self.edition.fiscal_year}__{safe_family}__{safe_issue}__{digest}.json"

    def _live_verified(
        self, family: str, issue: str, text: OfficialText, prefer: Optional[tuple[int, int]]
    ) -> Optional[Citation]:
        query = live_query(family, issue, self.edition)
        path = self._live_cache_path(family, issue, query)
        results = None
        cached_at = None
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                stamp = datetime.fromisoformat(payload["cached_at"])
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if payload.get("query") == query and self._now - stamp <= self.ttl:
                    results = payload.get("results") or []
                    cached_at = payload["cached_at"]
            except (OSError, ValueError, KeyError):
                results = None
        if results is None:
            search = self._post_json(
                SEARCH_URL,
                {
                    "api_key": self.api_key,
                    "query": query,
                    "search_depth": "advanced",
                    "include_domains": list(OFFICIAL_SUFFIXES),
                    "chunks_per_source": 3,
                    "max_results": 5,
                    "include_usage": True,
                },
            )
            self.live_requests += 1
            self.credits += _credits(search, 2)
            results = [
                {"url": row.get("url") or "", "title": row.get("title") or "", "content": row.get("content") or ""}
                for row in (search.get("results") or [])
                if isinstance(row, dict) and _official(row.get("url") or "")
            ]
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps({"cached_at": self._now.isoformat(), "query": query, "results": results}),
                    encoding="utf-8",
                )
            except OSError as exc:
                self.degraded_reasons.append(f"guideline cache write failed: {exc}")
        else:
            self.cache_hits += 1
        best = None
        best_row = None
        best_key = None
        for row in results:
            url = row.get("url") or ""
            if not self.edition.matches_url(url):
                continue  # another year's PDF, or a CMS page that is not the guidelines
            match = verified_passage(row.get("content") or "", text, family, issue, prefer_span=prefer)
            if match is None:
                continue
            overlap = 0
            if prefer:
                overlap = max(0, min(match.end, prefer[1]) - max(match.start, prefer[0]))
            key = (overlap > 0, passage_score(match.text, family, issue), overlap, len(match.text))
            if best_key is None or key > best_key:
                best, best_row, best_key = match, row, key
        if best is None:
            self.notes.append(
                f"No {self.edition.label} passage from Tavily for {family}|{issue} matched the official PDF verbatim"
            )
            return None
        url = best_row.get("url") or self.edition.pdf_url
        return Citation(
            url=url,
            quote=best.text,
            source=_hostname(url),
            title=f"{OFFICIAL_TITLE} {self.edition.label}",
            retrieved="cache" if cached_at else "live",
            cached_at=cached_at,
            code_family=family,
            issue=issue,
            source_tier="official",
            source_label=source_label_for("official"),
            section=best.section,
            page=best.page,
            provenance="tavily_live",
            fiscal_year=self.edition.label,
            verified_verbatim=True,
            verified_against=text.source_url,
            pdf_sha256=text.sha256,
        )

    # ---- curated anchors -----------------------------------------------------------------

    def _from_anchor(
        self,
        code_family: str,
        issue: str,
        *,
        match=None,
        status: Optional[str] = None,
        text: Optional[OfficialText] = None,
    ) -> Optional[list[Citation]]:
        row = anchor_for(code_family, issue)
        if not row:
            return None
        quote = str(row.get("quote") or "").strip()
        if not quote or "[...]" in quote:
            return None
        meta = load_anchors()
        page = match.page if match is not None else row.get("page")
        try:
            page_number = int(page) if page is not None else None
        except (TypeError, ValueError):
            page_number = None
        if match is not None and text is not None:
            fiscal_year = text.edition.label
            url = text.source_url
            sha = text.sha256
            verified = True
        else:
            # Offline: the committed excerpt of this edition is what the tests check the quote against.
            fiscal_year = str(meta.get("fiscal_year") or "")
            url = str(meta.get("pdf_url") or OFFICIAL_PDF_URL)
            sha = str(meta.get("pdf_sha256") or "")
            verified = status != "unverified"
        return [
            Citation(
                url=url,
                quote=quote,
                source=_hostname(url) or "cms.gov",
                title=f"{OFFICIAL_TITLE} {fiscal_year}".strip(),
                retrieved="anchor",
                cached_at=None,
                code_family=(code_family or "").upper(),
                issue=(issue or "").strip().lower(),
                source_tier="official",
                source_label=source_label_for("official"),
                section=str(row.get("section") or ""),
                page=page_number,
                provenance="curated_fallback",
                fiscal_year=fiscal_year,
                verified_verbatim=verified,
                verified_against=url if verified else "",
                pdf_sha256=sha,
                anchor_status=status or "verified",
            )
        ]

    # ---- codes with no curated anchor ----------------------------------------------------

    def _legacy_lookup(self, code_family: str, issue: str, text: Optional[OfficialText] = None) -> list[Citation]:
        fixture_key = f"{code_family}|{issue}"
        query = guideline_query(code_family, issue)
        cached = self._read_disk(code_family, issue, query)
        if cached is not None:
            self.cache_hits += 1
            return self._mark_verified(cached, text)
        if self.use_fixtures or not self.api_key:
            found = self._from_fixture(fixture_key, code_family, issue)
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
            return found
        # Empty and matching lookups are both cached, keyed on the query and filter version.
        self._write_disk(code_family, issue, query, found)
        return self._mark_verified(found, text)

    def _mark_verified(self, found: list[Citation], text: Optional[OfficialText]) -> list[Citation]:
        for item in found:
            item.provenance = item.provenance or "tavily_live"
            item.anchor_status = item.anchor_status or "none"
            edition = edition_for_url(item.url)
            item.fiscal_year = item.fiscal_year or (edition.label if edition else "")
            if text is not None and edition is not None and edition.fiscal_year == text.edition.fiscal_year:
                match = text.find(item.quote)
                if match is not None:
                    item.verified_verbatim = True
                    item.verified_against = text.source_url
                    item.pdf_sha256 = text.sha256
                    item.section = item.section or match.section
                    item.page = item.page or match.page
        if found:
            self.live_citations += 1
        return found

    def _from_fixture(self, key: str, code_family: str, issue: str) -> list[Citation]:
        fixtures = _load_fixtures()
        row = fixtures.get(key) or fixtures.get(f"*|{issue}")
        if not row:
            return []
        tier = source_tier_for(row["url"], row.get("title") or "")
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
                source_tier=tier,
                source_label=source_label_for(tier),
                provenance="fixture",
                anchor_status="none",
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
                    source_label=source_label_for(tier),
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
                        "source_label": item.source_label,
                    }
                    for item in citations
                ],
            }
            self._cache_path(code_family, issue, query).write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            self.degraded_reasons.append(f"guideline cache write failed: {exc}")

    def _live(self, code_family: str, issue: str, query: str) -> list[Citation]:
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
                "fy2026": tier == "official" and _is_current_fy(url, title),
            }
            if _prefers(candidate, best):
                best.update(candidate)

        self._search_pass(query, code_family, issue, consider, best)
        # A code list or a secondary page is weak. Ask once more for the rule section.
        if not passage_is_strong(best.get("quote") or "", best.get("tier") or ""):
            follow = rule_query(code_family, issue)
            if follow != query:
                try:
                    self._search_pass(follow, code_family, issue, consider, best)
                except Exception as exc:
                    self.degraded_reasons.append(
                        f"Tavily rule-section lookup failed for {code_family}|{issue}: {exc}"
                    )
        if not best["quote"]:
            self.degraded_reasons.append(
                f"No relevant guideline passage for {code_family}|{issue}; dropped low-confidence citation"
            )
            return []
        tier = best["tier"] or source_tier_for(best["url"], best["title"])
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
                source_tier=tier,
                source_label=source_label_for(tier),
            )
        ]

    def _search_pass(self, query: str, code_family: str, issue: str, consider, best: dict) -> None:
        """One Tavily search, then extract. Official rule text stops the pass early."""
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
            return
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
            if best.get("fy2026") and passage_is_strong(best.get("quote") or "", best.get("tier") or ""):
                break
        if best["tier"] != "official" and not best["quote"]:
            for row in secondary_rows:
                if extracts >= 3:
                    break
                if self._extract_into(row, query, code_family, issue, consider):
                    extracts += 1
                if best["quote"]:
                    break

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


def _safe_reason(exc: Exception) -> str:
    """The loader's own message lists hosts and exception classes only; anything else is reduced to its class."""
    text = str(exc)
    if isinstance(exc, RuntimeError) and text.startswith("could not load the"):
        return text
    return type(exc).__name__


def _no_pdf_fetch(url, **_kwargs):
    raise RuntimeError("PDF fetch is disabled for an injected transport")


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
