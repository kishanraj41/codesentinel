"""Two-way check for coded conditions the chart does not support.

Production code lives here so the cascade does not import the eval package.
`eval.policy` re-exports `unsupported_existing_items` and wraps the rows as
eval items. The cue lists match the offline policy; this module does not read
gold labels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Terms that tie an ICD-10 code to a sentence. Kept in sync with eval.lexicon.CODE_TERMS.
_CODE_TERMS: dict[str, tuple[str, ...]] = {
    "E11.65": ("diabetes", "diabetic", "hyperglycemia"),
    "E11.9": ("diabetes", "diabetic"),
    "E11.22": ("diabetic nephropathy", "diabetic kidney", "diabetic ckd", "diabetic chronic kidney"),
    "N18.3": ("chronic kidney", "ckd", "kidney disease"),
    "N18.4": ("chronic kidney", "ckd", "kidney disease"),
    "N18.5": ("chronic kidney", "ckd", "kidney disease"),
    "N18.9": ("chronic kidney", "ckd", "kidney disease"),
    "I50.9": ("heart failure", "chf", "congestive"),
    "I50.22": ("heart failure", "reduced ejection", "systolic", "hfref"),
    "I50.20": ("heart failure", "systolic"),
    "J44.1": ("copd", "chronic obstructive", "emphysema"),
    "E03.9": ("hypothyroid", "underactive thyroid"),
    "E78.5": ("hyperlipidemia", "dyslipidemia", "high cholesterol"),
    "E78.0": ("hypercholesterolemia", "high cholesterol"),
    "I10": ("hypertension", "high blood pressure", "htn"),
    "I12.9": ("hypertensive ckd", "hypertensive chronic kidney", "hypertensive kidney"),
    "D64.9": ("anemia", "anemic"),
    "D50.9": ("iron deficiency", "ferritin"),
    "E55.9": ("vitamin d",),
    "E79.0": ("uric acid", "hyperuricemia"),
    "F32.9": ("major depressive", "depressive disorder", "mdd"),
    "F41.1": ("generalized anxiety", "gad"),
    "G47.33": ("sleep apnea", "cpap", "osa"),
    "I48.91": ("atrial fibrillation", "afib", "a-fib"),
    "K21.0": ("gerd", "acid reflux", "heartburn", "esophagitis"),
    "K76.89": ("fatty liver", "nafld", "cirrhosis", "liver disease"),
    "G62.9": ("peripheral neuropathy", "neuropathic"),
    "I63.9": ("stroke", "cerebral infarct"),
    "J45.20": ("asthma",),
}

_NEGATION_CUES: tuple[str, ...] = (
    "does not have",
    "do not have",
    "did not have",
    "no longer has",
    "denies",
    "denied",
    "no evidence of",
    "no history of",
    "no sign of",
    "no signs of",
    "negative for",
    "not an active",
    "in error",
    "entered in error",
    "was retracted",
    "never smoked",
    "never had",
    "never diagnosed",
    "this is not",
    "please remove",
    "do not use",
)

_FAMILY_CUES: tuple[str, ...] = (
    "mother",
    "father",
    "sister",
    "brother",
    "grandmother",
    "grandfather",
    "maternal",
    "paternal",
    "family story",
    "family history",
)

_HIGH_SPECIFICITY_DRUGS: dict[str, tuple[str, ...]] = {
    "E11.9": ("metformin", "insulin", "glipizide", "glyburide", "glimepiride"),
    "E11.65": ("metformin", "insulin", "glipizide", "glyburide", "glimepiride"),
    "E78.5": ("atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin", "pitavastatin"),
    "E78.0": ("atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin", "pitavastatin"),
    "E03.9": ("levothyroxine", "synthroid", "liothyronine"),
    "J44.1": ("tiotropium", "umeclidinium", "spiriva"),
    "I10": ("lisinopril", "amlodipine", "losartan", "valsartan", "hydrochlorothiazide", "hctz"),
}

_HEADER_RE = re.compile(
    r"(?im)^(history of present illness|hpi|problem list|past medical history|"
    r"assessment and plan|assessment|medications|laboratory|physical exam)\s*:?\s*$"
)


@dataclass
class UnsupportedExisting:
    """A coded condition the note contradicts, or that has no MEAT at all."""

    code: str
    condition_name: str
    assertion_status: str
    quote: str
    decision: str
    drop_reason: str | None = None
    shown_to_coder: bool = True
    gap_type: str = "unsupported_existing"
    source_tier: str = "TWO_WAY"
    quote_verified: bool = True
    stage: str = "triage"


def unsupported_existing_items(profile: dict) -> list[UnsupportedExisting]:
    """Coded conditions the note contradicts, or that have no MEAT at all."""
    text = _note_text(profile)
    sentences = _sentences(text)
    items: list[UnsupportedExisting] = []
    meds = " ".join(_med_names(profile))
    for cond in profile.get("conditions") or []:
        code = (cond.get("icd10_code") or "").upper()
        if not code:
            continue
        terms = _terms_for(code)
        matched = [(sentence, section) for sentence, section in sentences if terms and _sentence_matches(sentence, terms)]
        contradicted = None
        affirmed = False
        for sentence, _section in matched:
            low = sentence.lower()
            if _has_cue(sentence, _NEGATION_CUES) or "in error" in low or "please remove" in low:
                contradicted = sentence
            elif not _has_cue(sentence, _FAMILY_CUES):
                affirmed = True
        if contradicted and not affirmed:
            items.append(UnsupportedExisting(
                code=code,
                condition_name=cond.get("display") or code,
                assertion_status="negated",
                quote=contradicted,
                decision="accept",
                drop_reason=None,
            ))
            continue
        if matched or affirmed:
            continue
        # No mention. Unsupported only when the chart also lacks a supporting drug and lab.
        high = _HIGH_SPECIFICITY_DRUGS.get(code, ())
        if any(drug in meds for drug in high):
            continue
        if _lab_supports(code, profile):
            continue
        if not text.strip() and not profile.get("medications") and not profile.get("observations"):
            continue
        items.append(UnsupportedExisting(
            code=code,
            condition_name=cond.get("display") or code,
            assertion_status="unsupported_existing",
            quote="No mention, medication, or supporting lab in this chart",
            decision="review",
            drop_reason="no_meat",
        ))
    return items


def _note_text(profile: dict) -> str:
    return "\n".join(note.get("text") or "" for note in profile.get("clinical_notes") or [])


def _sentences(text: str) -> list[tuple[str, str]]:
    section = "unknown"
    found: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _HEADER_RE.match(line):
            section = line.rstrip(":").strip().lower()
            continue
        parts = re.split(r"(?<=[.!?])\s+", line)
        for part in parts:
            part = part.strip()
            if part:
                found.append((part, section))
    return found


def _has_cue(sentence: str, cues: tuple[str, ...]) -> bool:
    low = sentence.lower()
    return any(cue in low for cue in cues)


def _terms_for(code: str) -> tuple[str, ...]:
    if code in _CODE_TERMS:
        return _CODE_TERMS[code]
    prefix = code.split(".")[0]
    for key, terms in _CODE_TERMS.items():
        if key.split(".")[0] == prefix:
            return terms
    return ()


def _sentence_matches(sentence: str, terms: tuple[str, ...]) -> bool:
    low = sentence.lower()
    return any(term in low for term in terms)


def _med_names(profile: dict) -> list[str]:
    return [(med.get("name") or "").lower() for med in profile.get("medications") or []]


def _lab_supports(code: str, profile: dict) -> bool:
    for obs in profile.get("observations") or []:
        loinc = obs.get("loinc") or ""
        try:
            value = float(obs.get("value"))
        except (TypeError, ValueError):
            continue
        if code.startswith("E11") and loinc == "4548-4" and value >= 6.5:
            return True
        if code.startswith("N18") and loinc == "33914-3" and value < 60:
            return True
        if code.startswith("I50") and loinc == "42637-9" and value > 100:
            return True
        if code.startswith("E03") and loinc == "3016-3" and value > 4.5:
            return True
        if code.startswith("E78") and loinc in {"2089-1", "2093-3"} and value >= 160:
            return True
        if code.startswith("D64") and loinc == "718-7" and value < 12:
            return True
    return False
