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

# Drugs that treat a whole ICD-10-CM category, used only as support for an already billed code.
_FAMILY_DRUGS: dict[str, tuple[str, ...]] = {
    "E11": ("metformin", "insulin", "glipizide", "glyburide", "glimepiride", "sitagliptin",
            "liraglutide", "semaglutide", "empagliflozin", "canagliflozin", "dapagliflozin", "pioglitazone"),
    "E78": ("atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin",
            "pitavastatin", "ezetimibe", "fenofibrate", "gemfibrozil"),
    "E03": ("levothyroxine", "synthroid", "liothyronine"),
    "I10": ("lisinopril", "amlodipine", "losartan", "valsartan", "hydrochlorothiazide", "hctz",
            "chlorthalidone", "olmesartan", "irbesartan"),
    "I12": ("lisinopril", "losartan", "valsartan", "irbesartan", "amlodipine"),
    # Carvedilol and furosemide are not specific enough to excuse an I13 code on their own.
    "I13": ("lisinopril", "losartan", "valsartan"),
    "I25": ("nitroglycerin", "isosorbide", "clopidogrel", "ranolazine"),
    "I48": ("warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban", "digoxin"),
    # Carvedilol (also hypertension) and furosemide (also edema, CKD) are not specific to heart
    # failure, so they do not support or excuse an I50 code on their own.
    "I50": ("sacubitril", "spironolactone", "eplerenone"),
    "J44": ("tiotropium", "umeclidinium", "spiriva", "fluticasone / salmeterol", "budesonide / formoterol"),
    "J45": ("albuterol", "fluticasone", "montelukast", "budesonide"),
    "G30": ("donepezil", "memantine", "galantamine", "rivastigmine"),
    "G40": ("levetiracetam", "lamotrigine", "phenytoin", "carbamazepine", "valproate"),
    "M81": ("alendronate", "risedronate", "ibandronate", "denosumab", "zoledronic"),
}

# Words that are not specific enough to tie a sentence to a condition on their own.
_GENERIC_WORDS = frozenset({
    "acute", "chronic", "disease", "disorder", "finding", "situation", "unspecified", "other",
    "without", "with", "complications", "complication", "type", "stage", "mellitus", "single",
    "episode", "primary", "secondary", "essential", "history", "syndrome", "body", "mass", "index",
    "site", "left", "right", "bilateral", "unilateral", "the", "and", "of", "or", "in", "to", "due",
})


def _display_terms(display: str) -> tuple[str, ...]:
    """Phrases from the Condition display text, for codes the term list does not cover."""
    low = re.sub(r"\((disorder|finding|situation|procedure)\)", " ", (display or "").lower())
    terms: list[str] = []
    for chunk in re.split(r"[,;:()\[\]]|\s-\s|\bwith\b|\bwithout\b|\bdue to\b", low):
        words = [w for w in re.findall(r"[a-z0-9']+", chunk)]
        content = [w for w in words if w not in _GENERIC_WORDS]
        if not content:
            continue
        phrase = " ".join(words).strip()
        if len(content) == 1 and len(content[0]) < 6:
            continue
        if len(phrase) >= 6:
            terms.append(phrase)
    return tuple(dict.fromkeys(terms))


def _structured_link(profile: dict, terms: tuple[str, ...]) -> bool:
    """A medication or encounter in the chart whose FHIR reason names this condition."""
    if not terms:
        return False
    for row in list(profile.get("medications") or []) + list(profile.get("encounters") or []):
        for reason in row.get("reasons") or []:
            if _sentence_matches(str(reason), terms):
                return True
    return False


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


# Decision for a billed code with no evidence on a chart whose notes name no condition.
# It is shown to the coder as context, but it is not review work and is not scored.
INFO_DECISION = "info"
INFO_DROP_REASON = "no_condition_mentions"


def _lexicon_terms() -> tuple[str, ...]:
    terms: list[str] = []
    for values in _CODE_TERMS.values():
        terms.extend(values)
    return tuple(dict.fromkeys(terms))


# A clause that names a procedure, an order or a screening instrument is not a condition
# mention even when it contains a condition word: "diabetic retinal eye exam (procedure)",
# "sleep apnea assessment (procedure)", "generalized anxiety disorder 7 item (gad-7)".
_NON_CONDITION_CLAUSE = re.compile(
    r"\((?:procedure|regime/therapy)\)|\bquestionnaire\b|\b\d+[- ]item\b|\b(?:gad|phq)-?\d+\b",
    re.IGNORECASE,
)


def _clause_mentions(sentence: str, terms: tuple[str, ...]) -> bool:
    for clause in re.split(r"[,;]", sentence):
        if _sentence_matches(clause, terms) and not _NON_CONDITION_CLAUSE.search(clause):
            return True
    return False


def condition_mentions(profile: dict) -> list[str]:
    """Note sentences that name a condition this check can recognize.

    A sentence is a condition mention when it contains a term for any code in the
    code lexicon (`_CODE_TERMS`, the conditions CodeSentinel audits) or a term for any
    code on the chart's own condition list (lexicon terms for that code plus phrases
    from its Condition display text). Negated, family-history and historical sentences
    still count: a note that says "denies diabetes" is discussing conditions. Section
    headers ("Assessment and Plan") on their own do not count, and neither does a
    comma- or semicolon-separated clause that names a procedure, order or screening
    instrument (tagged "(procedure)" or "(regime/therapy)", a questionnaire, an
    "N item" scale, GAD-n or PHQ-n).
    """
    sentences = _sentences(_note_text(profile))
    terms = list(_lexicon_terms())
    for cond in profile.get("conditions") or []:
        code = (cond.get("icd10_code") or "").upper()
        if code:
            terms.extend(_terms_for(code))
        terms.extend(_display_terms(cond.get("display") or ""))
    terms_t = tuple(dict.fromkeys(t for t in terms if t))
    if not terms_t:
        return []
    return [sentence for sentence, _section in sentences if _clause_mentions(sentence, terms_t)]


def notes_mention_conditions(profile: dict) -> bool:
    """False only when the chart window's notes name no recognizable condition at all.

    This is the gate for unsupported-existing flags. It is deliberately conservative:
    one mention of any audited or billed condition anywhere in any note keeps the
    normal review behavior for every billed code on the chart. A chart with no note
    text has no condition mentions.
    """
    return bool(condition_mentions(profile))


def unsupported_existing_items(profile: dict) -> list[UnsupportedExisting]:
    """Coded conditions the note contradicts, or that have no MEAT at all.

    A billed code with no evidence is a review item when the notes discuss conditions
    (the clinician documented the problem list and this code is absent from it). When
    the notes name no condition at all (`notes_mention_conditions` is False), silence
    about one code is not evidence against it, so the row is informational: decision
    `info`, not shown as review work, not counted in review items or scoring.
    Contradicted codes always need a mention, so they are never informational.
    """
    text = _note_text(profile)
    sentences = _sentences(text)
    items: list[UnsupportedExisting] = []
    silent_notes: bool | None = None
    meds = " ".join(_med_names(profile))
    for cond in profile.get("conditions") or []:
        code = (cond.get("icd10_code") or "").upper()
        if not code:
            continue
        terms = _terms_for(code) + _display_terms(cond.get("display") or "")
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
        # No mention. Unsupported only when the chart also lacks structured evidence: a
        # medication or encounter whose FHIR reason names the condition, a drug that treats
        # it, or a lab past the code's threshold. The Condition row itself is the claim under
        # audit, so it is not counted as its own support.
        if _structured_link(profile, terms):
            continue
        high = _HIGH_SPECIFICITY_DRUGS.get(code, ()) + _FAMILY_DRUGS.get(code.split(".")[0], ())
        if any(drug in meds for drug in high):
            continue
        if _lab_supports(code, profile):
            continue
        if not text.strip() and not profile.get("medications") and not profile.get("observations"):
            continue
        if silent_notes is None:
            silent_notes = not notes_mention_conditions(profile)
        if silent_notes:
            items.append(UnsupportedExisting(
                code=code,
                condition_name=cond.get("display") or code,
                assertion_status="unsupported_existing",
                quote="No mention, medication, or supporting lab in this chart; the notes name no condition",
                decision=INFO_DECISION,
                drop_reason=INFO_DROP_REASON,
                shown_to_coder=False,
            ))
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
    from codesentinel.nemotron.mock_reasoner import supporting_lab_observation

    return supporting_lab_observation(code, profile.get("observations") or []) is not None
