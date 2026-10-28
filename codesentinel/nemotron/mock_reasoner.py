"""Recorded triage, adjudication, and physician-query drafts.

Used when NEBIUS_API_KEY is unset and when a live call fails.
Quotes are copied from the chart or from exact structured lines so the
deterministic verifier can still drop anything that is not in the record.
"""

from __future__ import annotations

import re

from codesentinel.nemotron.schemas import (
    AdjudicationResult,
    MeatQuotes,
    PhysicianQueryResult,
    TriageResult,
    limit_words,
)
from codesentinel.nemotron.verifier import lab_line, medication_line

_FAMILY_KEYWORDS = {
    "E11": ["diabetes", "hyperglycemia", "hba1c", "a1c"],
    "R73": ["prediabetes", "impaired fasting"],
    "N18": ["kidney", "ckd", "egfr", "nephropathy"],
    "E78": ["cholesterol", "hypercholesterolemia", "hyperlipidemia", "ldl"],
    "D64": ["anemia", "hemoglobin"],
    "D50": ["ferritin", "iron deficiency"],
    "I50": ["heart failure", "chf", "bnp", "ejection fraction"],
    "J44": ["copd", "emphysema", "chronic bronchitis"],
    "E03": ["hypothyroid", "tsh", "levothyroxine"],
    "E05": ["thyrotoxicosis", "hyperthyroid"],
    "F32": ["depression", "depressive", "phq-9", "phq9"],
    "F33": ["depression", "depressive"],
    "F41": ["anxiety", "gad"],
    "I10": ["hypertension", "htn"],
    "I12": ["hypertensive", "kidney", "ckd"],
    "G47": ["sleep apnea", "cpap", "osa"],
    "E55": ["vitamin d"],
    "E79": ["uric"],
    "K76": ["liver", "alt", "nafld"],
    "K21": ["gerd", "reflux"],
    "J45": ["asthma"],
}

_LOINC_FAMILIES = {
    "4548-4": ("E11", "R73"),
    "17856-6": ("E11",),
    "1558-6": ("E11",),
    "2345-7": ("E11",),
    "2339-0": ("E11",),
    "33914-3": ("N18",),
    "48642-3": ("N18",),
    "62238-1": ("N18",),
    "2160-0": ("N18",),
    "42637-9": ("I50",),
    "33762-6": ("I50",),
    "2089-1": ("E78",),
    "2093-3": ("E78",),
    "2571-8": ("E78",),
    "718-7": ("D64",),
    "3016-3": ("E03", "E05"),
    "2498-4": ("D50",),
    "1989-3": ("E55",),
    "3084-1": ("E79",),
    "1742-6": ("K76",),
    "1920-8": ("K76",),
}

_LEADING = re.compile(
    r"\b(please add|should be added|should be coded|add (the )?code|please code|"
    r"confirm that .{0,80} is present)\b",
    re.IGNORECASE,
)


def mock_payload(schema_name: str, context: dict):
    if schema_name == "triage":
        return mock_triage(context)
    if schema_name == "adjudication" and "prior_decision" in context:
        return mock_ultra(context)
    if schema_name == "adjudication":
        return mock_adjudicate(context)
    if schema_name == "physician_query":
        return mock_physician_query(context)
    raise ValueError(f"Unknown schema {schema_name}")


def keywords_for(code: str, condition_name: str) -> list[str]:
    prefix = (code or "")[:3].upper()
    words = list(_FAMILY_KEYWORDS.get(prefix, []))
    for token in re.split(r"[^A-Za-z0-9]+", condition_name or ""):
        if len(token) >= 5:
            words.append(token.lower())
    # Preserve order, drop duplicates.
    seen = set()
    unique = []
    for word in words:
        if word not in seen:
            seen.add(word)
            unique.append(word)
    return unique


# A later sentence can take back a diagnosis without naming it again.
_RETRACTION = re.compile(
    r"\bretract(?:ed|ion|s)?\b"
    r"|\bthe list is wrong\b"
    r"|\bentered in error\b"
    r"|\bdisregard(?:ed)?\b"
    r"|\bno longer (?:has|present|active)\b"
    r"|\bincorrectly (?:listed|documented|coded)\b"
    r"|\b(?:was|is) (?:incorrect|wrong|an error)\b",
    re.IGNORECASE,
)


def sentence_retracts(sentence: str) -> bool:
    return bool(sentence and _RETRACTION.search(sentence))


def mention_windows(notes: str, keywords: list[str]) -> list[dict]:
    """Each hit plus the next sentence, so a retraction that omits the name is visible."""
    sentences = _sentences(notes)
    windows = []
    for index, sentence in enumerate(sentences):
        lowered = sentence.lower()
        if not any(key in lowered for key in keywords):
            continue
        following = sentences[index + 1] if index + 1 < len(sentences) else ""
        windows.append(
            {
                "sentence": sentence,
                "following_sentence": following,
                "retracted_by_following_sentence": sentence_retracts(following),
            }
        )
        if len(windows) >= 6:
            break
    return windows


# These sentences are the triage instructions. The recorded client implements them.
# Live calls send the same text. Neither path reads gold labels.
TRIAGE_INSTRUCTIONS = (
    "Decide the assertion status from the chart only. "
    "supporting_quotes must be verbatim chart text or an exact structured lab or medication line. "
    "mention_windows pairs each sentence that names the condition with the following sentence. "
    "A retraction, correction, or 'the list is wrong' in that next sentence applies even when it "
    "does not repeat the condition name: assertion_status is negated. "
    "Negation in the mention itself also counts: denies, no evidence of, does not have, "
    "negative for, not an active problem, entered in error, or do not use. "
    "If a parent, sibling, or other relative is the person with the condition, and the sentence "
    "does not say the patient has it, assertion_status is family_history. "
    "A routine screening item, a PHQ-9 that was only administered, or 'screening for' is "
    "screening_or_rule_out, not a diagnosis. "
    "Rule out, evaluate for, possible, or suspected is hypothetical_plan. "
    "Resolved or no longer active is historical_resolved. "
    "A copied problem list is not current support. "
    "An explicit negation, family history, screening, hypothetical, or resolved mention outranks "
    "a bare listing of the same condition. "
    "A lab or a medication does not override that kind of sentence. "
    "A medication alone is not an assessment. Metformin, insulin, a statin, levothyroxine, or an "
    "antihypertensive discussed as treatment of hypertension can be sent to a human (do not accept "
    "it on the medication alone). An ACE inhibitor, ARB, beta blocker, loop diuretic, PPI, or "
    "antidepressant does not establish CKD, heart failure, GERD, or depression by itself: "
    "assertion_status is copied_forward_unclear when that is the only evidence. "
    "An active medication list is current treatment evidence, not a copied problem list. "
    "When no note sentence affirms or rules out the condition (the note is silent, a template, a "
    "refill check, or says see medication list) and an active drug specifically treats the code (metformin or "
    "insulin for diabetes, a statin for hyperlipidemia, levothyroxine for hypothyroidism, an "
    "antihypertensive for hypertension), assertion_status is medication_supported. Do not mark that "
    "pattern copied_forward_unclear. An explicit negation, family history, screening, hypothetical, "
    "or resolved sentence still outranks the medication list. "
    "An empty or silent note is not a negation. When no note sentence names the condition and the "
    "only support is an abnormal lab for that code, assertion_status is lab_only, not negated. "
    "If the note says not to use a combination code, assertion_status for that combination is negated. "
    "An N18 code needs a documented CKD diagnosis (chronic kidney disease, CKD, nephropathy, or dialysis) "
    "or kidney-disease chronicity of at least 3 months, including two abnormal kidney labs at least 90 days apart. "
    "One low eGFR, one creatinine, or an acute kidney lab is not that diagnosis: assertion_status is hypothetical_plan."
)

_FAMILY_CUE = re.compile(
    r"\b(mother|father|sister|brother|grandmother|grandfather|maternal|paternal|"
    r"family history|family story)\b",
    re.IGNORECASE,
)
_NEGATION_CUE = re.compile(
    r"\bdoes not have\b|\bdo not have\b|\bdid not have\b|\bno evidence\b|\bno history\b|"
    r"\bno signs?\b|\bdenies\b|\bdenied\b|\bnegative for\b|\bwithout evidence\b|"
    r"\bnot diagnosed\b|\babsence of\b|\bnot an active\b|\bin error\b|\bdo not use\b|"
    r"\bplease remove\b",
    re.IGNORECASE,
)
_SCREEN_CUE = re.compile(
    r"\bscreen(?:ing|ed)?\b|\broutine item\b|\bas a screening\b|\bpreventive only\b|"
    r"phq-?9\s+(?:was\s+)?(?:only\s+)?(?:administered|completed)|"
    r"\bno treatment indicated\b|\bnot meeting criteria\b",
    re.IGNORECASE,
)
_HYPOTHETICAL_CUE = re.compile(
    r"\brule out\b|\br/o\b|\bevaluate for\b|\bpossible\b|\bsuspected\b",
    re.IGNORECASE,
)
_RESOLVED_CUE = re.compile(
    r"\bresolved\b|\bremote history\b|\bno longer active\b",
    re.IGNORECASE,
)
_COPIED_CUE = re.compile(
    r"copied problem list|not an active problem|remains on the copied",
    re.IGNORECASE,
)
_AFFIRMING = re.compile(
    r"\bis active\b|\bcurrently active\b|\bremains active\b|\buncontrolled\b|"
    r"\bwith hyperglycemia\b|\breduced ejection fraction\b|\bsystolic heart failure\b|"
    r"\bstage \d\b",
    re.IGNORECASE,
)
_NEGATIVE_STATUS = (
    "negated",
    "family_history",
    "historical_resolved",
    "hypothetical_plan",
    "screening_or_rule_out",
    "copied_forward_unclear",
)

# Chart-text stand-in for the medication paragraph in TRIAGE_INSTRUCTIONS.
_SPECIFIC_DRUGS = {
    "E11": ("metformin", "insulin", "glipizide", "glyburide", "glimepiride"),
    "E78": ("atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin", "pitavastatin"),
    "E03": ("levothyroxine", "synthroid", "liothyronine"),
    "I10": ("lisinopril", "amlodipine", "losartan", "valsartan", "hydrochlorothiazide", "hctz"),
    "J44": ("tiotropium", "umeclidinium"),
}
_NONSPECIFIC_DRUGS = {
    "N18": (
        "lisinopril", "losartan", "valsartan", "enalapril", "ramipril", "irbesartan",
        "benazepril", "amlodipine", "candesartan", "telmisartan", "olmesartan",
    ),
    "I50": ("carvedilol", "metoprolol", "bisoprolol", "furosemide", "bumetanide", "torsemide"),
    "K21": ("omeprazole", "pantoprazole", "esomeprazole", "lansoprazole", "famotidine"),
    "F32": (
        "sertraline", "fluoxetine", "escitalopram", "citalopram", "paroxetine",
        "venlafaxine", "duloxetine", "bupropion", "mirtazapine", "trazodone",
    ),
    "F33": (
        "sertraline", "fluoxetine", "escitalopram", "citalopram", "paroxetine",
        "venlafaxine", "duloxetine", "bupropion",
    ),
}


def classify_sentence(sentence: str) -> str:
    text = sentence or ""
    lowered = text.lower()
    if _FAMILY_CUE.search(text) and "the patient has" not in lowered and "patient himself" not in lowered:
        return "family_history"
    if sentence_retracts(text) or _NEGATION_CUE.search(text):
        return "negated"
    if _RESOLVED_CUE.search(text):
        return "historical_resolved"
    if _HYPOTHETICAL_CUE.search(text):
        return "hypothetical_plan"
    if _SCREEN_CUE.search(text):
        return "screening_or_rule_out"
    if _COPIED_CUE.search(text):
        return "copied_forward_unclear"
    return "present"


def affirming_sentence(sentence: str) -> bool:
    return bool(sentence and _AFFIRMING.search(sentence) and classify_sentence(sentence) == "present")


def drug_specificity(code: str, medications: list[dict]) -> str:
    """high: the drug can support review. low: the drug does not establish this code."""
    prefix = (code or "")[:3].upper()
    names = " ".join(str(med.get("name") or "") for med in medications).lower()
    if any(drug in names for drug in _SPECIFIC_DRUGS.get(prefix, ())):
        return "high"
    if prefix in _NONSPECIFIC_DRUGS:
        return "low"
    return "unknown"


def _sentences(text: str) -> list[str]:
    parts = re.split(r"\n+|(?<=[\.\!\?])\s+", text or "")
    return [part.strip() for part in parts if part.strip()]


def _section_at(notes: str, sentence: str) -> str:
    from gap_pipeline import detect_section

    pos = notes.find(sentence)
    if pos < 0:
        return "unknown"
    return detect_section(notes, pos)


def _related_labs(code: str, observations: list[dict]) -> list[str]:
    prefix = (code or "")[:3].upper()
    lines = []
    for observation in observations:
        families = _LOINC_FAMILIES.get(str(observation.get("loinc") or ""), ())
        if prefix in families and observation.get("value") is not None:
            lines.append(lab_line(observation))
    return lines


def _related_meds(code: str, medications: list[dict]) -> list[str]:
    from gap_pipeline import PHENOTYPE_RULES

    prefix = (code or "")[:3].upper()
    lines = []
    for rule in PHENOTYPE_RULES.values():
        rule_prefix = str(rule.get("icd10") or "")[:3].upper()
        if rule_prefix != prefix:
            continue
        markers = [m.lower() for m in rule.get("medication_markers") or []]
        for medication in medications:
            name = str(medication.get("name") or "")
            lowered = name.lower()
            if any(marker in lowered for marker in markers):
                lines.append(medication_line(medication))
    return lines


# Structured lines sent to triage. Short charts are sent whole and in chart order. Long charts
# send the lines that bear on the candidate first (its family's labs, drugs that treat or
# merely accompany it, the threshold lab), then every other active medication, then the
# remaining labs, newest first, within the line and character budget.
TRIAGE_LINE_LIMIT = 60
TRIAGE_CHAR_BUDGET = 7000
_SHORT_CHART_LINES = 40


def _drug_lines_for(code: str, medications: list[dict]) -> list[str]:
    prefix = (code or "")[:3].upper()
    drugs = _SPECIFIC_DRUGS.get(prefix, ()) + _NONSPECIFIC_DRUGS.get(prefix, ())
    out = []
    for medication in medications:
        name = str(medication.get("name") or "").lower()
        if any(drug in name for drug in drugs):
            out.append(medication_line(medication))
    return out


def triage_structured_lines(gap: dict, profile: dict, all_lines: list[str]) -> list[str]:
    if len(all_lines) <= _SHORT_CHART_LINES:
        return list(all_lines)
    code = str((gap or {}).get("icd10_code") or "")
    medications = [m for m in profile.get("medications") or [] if isinstance(m, dict)]
    observations = [o for o in profile.get("observations") or [] if isinstance(o, dict)]
    ordered: list[str] = []
    lab = supporting_lab_observation(code, observations, gap)
    if lab is not None:
        ordered.append(lab_line(lab))
    ordered += _related_meds(code, medications) + _drug_lines_for(code, medications)
    ordered += _related_labs(code, observations)
    ordered += [medication_line(m) for m in _active_medications(medications)]
    ordered += [medication_line(m) for m in medications]
    dated = sorted(observations, key=lambda o: str(o.get("date") or o.get("effective") or ""), reverse=True)
    ordered += [lab_line(o) for o in dated]
    known = set(all_lines)
    picked: list[str] = []
    seen: set[str] = set()
    used = 0
    for line in ordered:
        if line in seen or line not in known:
            continue
        if len(picked) >= TRIAGE_LINE_LIMIT or used + len(line) > TRIAGE_CHAR_BUDGET:
            break
        seen.add(line)
        picked.append(line)
        used += len(line)
    return picked


_CKD_DIAGNOSIS = re.compile(
    r"\bchronic\s+kidney\b|\bchronic\s+renal\b|\bckd\b|\bnephropathy\b|"
    r"\besrd\b|\bend[- ]stage renal\b|\bdialysis\b",
    re.IGNORECASE,
)
_DURATION_NUMBER = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(day|days|week|weeks|month|months|year|years)\b",
    re.IGNORECASE,
)
_DURATION_WORD = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve|eighteen)\s+"
    r"(day|days|week|weeks|month|months|year|years)\b",
    re.IGNORECASE,
)
_WORD_NUMBER = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "eighteen": 18,
}
_KIDNEY_LABS = {
    "33914-3": ("lt", 60.0),
    "48642-3": ("lt", 60.0),
    "62238-1": ("lt", 60.0),
    "2160-0": ("ge", 1.5),
    "14959-1": ("ge", 30.0),
}


def _span_is_at_least_3_months(count: float, unit: str) -> bool:
    unit = unit.lower()
    if unit.startswith("year"):
        return count >= 1 or count * 12 >= 3
    if unit.startswith("month"):
        return count >= 3
    if unit.startswith("week"):
        return count >= 13
    if unit.startswith("day"):
        return count >= 90
    return False


def _duration_at_least_3_months(sentence: str) -> bool:
    for match in _DURATION_NUMBER.finditer(sentence or ""):
        if _span_is_at_least_3_months(float(match.group(1)), match.group(2)):
            return True
    for match in _DURATION_WORD.finditer(sentence or ""):
        count = _WORD_NUMBER.get(match.group(1).lower())
        if count is not None and _span_is_at_least_3_months(count, match.group(2)):
            return True
    return False


def _abnormal_kidney_lab(observation: dict) -> bool:
    rule = _KIDNEY_LABS.get(str(observation.get("loinc") or ""))
    if rule is None or observation.get("value") is None:
        return False
    try:
        value = float(observation["value"])
    except (TypeError, ValueError):
        return False
    op, threshold = rule
    if op == "lt":
        return value < threshold
    return value >= threshold


def _lab_date(observation: dict):
    from datetime import date

    raw = str(observation.get("date") or observation.get("effectiveDateTime") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _abnormal_kidney_labs_span(observations: list, days: int = 90) -> bool:
    dated = []
    for observation in observations or []:
        if not _abnormal_kidney_lab(observation):
            continue
        found = _lab_date(observation)
        if found is not None:
            dated.append(found)
    if len(dated) < 2:
        return False
    return (max(dated) - min(dated)).days >= days


def kidney_disease_is_established(notes: str, observations: list | None = None) -> bool:
    """CKD is documented, or the kidney abnormality has lasted at least 3 months.

    One low eGFR, one creatinine, or an acute kidney lab is not enough.
    """
    for sentence in _sentences(notes or ""):
        if classify_sentence(sentence) in _NEGATIVE_STATUS:
            continue
        if _CKD_DIAGNOSIS.search(sentence) or _duration_at_least_3_months(sentence):
            return True
    return _abnormal_kidney_labs_span(observations or [])


def _hold_unestablished_n18(result: TriageResult, code: str, notes: str, observations: list) -> TriageResult:
    if result.assertion_status not in {"present", "lab_only"} or not str(code).upper().startswith("N18"):
        return result
    if kidney_disease_is_established(notes, observations):
        return result
    return TriageResult(
        assertion_status="hypothetical_plan",
        supporting_quotes=list(result.supporting_quotes),
        section=result.section,
        meat=MeatQuotes(),
        confidence=0.86,
    )


def _forbidden_combination(code: str, notes: str) -> str:
    """A 'do not use' sentence negates only the combination it names.

    'Do not use a diabetic kidney combination' is E11.22. It does not negate
    hypertensive CKD (I12.9). An unnamed combination sentence applies to both.
    """
    if code not in {"E11.22", "I12.9"}:
        return ""
    for sentence in _sentences(notes):
        if not re.search(r"do not use", sentence, re.IGNORECASE):
            continue
        names_diabetes = re.search(r"diabetic|diabetes", sentence, re.IGNORECASE)
        names_hypertensive = re.search(r"hypertensive", sentence, re.IGNORECASE)
        names_combination = re.search(r"combination", sentence, re.IGNORECASE)
        if code == "E11.22" and names_diabetes:
            return sentence
        if code == "I12.9" and names_hypertensive:
            return sentence
        if names_combination and not names_diabetes and not names_hypertensive:
            return sentence
    return ""


_MED_SUPPORT_CUE = re.compile(
    r"refills were sent|medications were refilled|refill check|see medication list",
    re.IGNORECASE,
)
_RESCUE_STATUSES = {"present", "negated", "copied_forward_unclear"}


def _note_empty(notes: str) -> bool:
    return not (notes or "").strip()


def _active_medications(medications: list | None) -> list[dict]:
    active = []
    for medication in medications or []:
        status = str(medication.get("status") or "active").strip().lower()
        if status in {"", "active", "current"}:
            active.append(medication)
    return active


def _threshold_holds(op: str, value: float, threshold: float) -> bool:
    if op == ">=":
        return value >= threshold
    if op == ">":
        return value > threshold
    if op == "<=":
        return value <= threshold
    if op == "<":
        return value < threshold
    return False


def supporting_lab_observation(code: str, observations: list | None, gap: dict | None = None) -> dict | None:
    """Return the observation whose LOINC threshold supports this exact code."""
    from gap_pipeline import LOINC_ICD10_MAP

    target = (code or "").upper()
    preferred = str((gap or {}).get("lab_loinc") or "")
    ranked = list(observations or [])
    if preferred:
        ranked.sort(key=lambda obs: str(obs.get("loinc") or "") != preferred)
    for observation in ranked:
        mapping = LOINC_ICD10_MAP.get(str(observation.get("loinc") or ""))
        if not mapping or observation.get("value") is None:
            continue
        try:
            value = float(observation["value"])
        except (TypeError, ValueError):
            continue
        for threshold in mapping.get("thresholds") or []:
            if str(threshold.get("icd10") or "").upper() != target:
                continue
            try:
                cutoff = float(threshold["value"])
            except (TypeError, ValueError, KeyError):
                continue
            if _threshold_holds(str(threshold.get("op") or ""), value, cutoff):
                return observation
    return None


def _blocked_by_chart_context(code: str, condition: str, notes: str) -> bool:
    """A real negation, retraction, family history, or copied-forward sentence outranks meds and labs."""
    keys = keywords_for(code, condition)
    if not keys:
        return False
    if any(window.get("retracted_by_following_sentence") for window in mention_windows(notes, keys)):
        return True
    for sentence in _sentences(notes):
        lowered = sentence.lower()
        if not any(key in lowered for key in keys):
            continue
        if classify_sentence(sentence) in _NEGATIVE_STATUS:
            return True
    return False


# Lab and questionnaire names. "Lipid panel with direct LDL" or "Hemoglobin A1c" in a list of
# completed lab reports names a test, not the condition, so it does not count as a mention
# that would stop a medication or lab rescue.
_ANALYTE_KEYWORDS = frozenset({
    "hba1c", "a1c", "egfr", "ldl", "cholesterol", "bnp", "tsh", "hemoglobin", "ferritin", "uric",
    "alt", "phq-9", "phq9", "gad", "vitamin d", "ejection fraction",
})


def _has_present_mention(code: str, condition: str, notes: str) -> bool:
    keys = [key for key in keywords_for(code, condition) if key not in _ANALYTE_KEYWORDS]
    if not keys:
        return False
    for sentence in _sentences(notes):
        lowered = sentence.lower()
        if any(key in lowered for key in keys) and classify_sentence(sentence) == "present":
            return True
    return False


# A billed code in these categories already accounts for a drug that treats the candidate.
_COVERING_FAMILIES = {
    "I10": ("I10", "I11", "I12", "I13", "I15", "I16"),
}


def _billed_code_explains_drug(code: str, profile: dict) -> bool:
    """The drug already has a billed indication in the same category (or a combination code)."""
    prefix = (code or "")[:3].upper()
    covering = _COVERING_FAMILIES.get(prefix, (prefix,))
    billed = {str(c).upper() for c in profile.get("claims_codes") or []}
    billed |= {str(c.get("icd10_code") or "").upper() for c in profile.get("conditions") or [] if isinstance(c, dict)}
    return any(b[:3] in covering and b != (code or "").upper() for b in billed if b)


def _medication_rescue(result: TriageResult, code: str, condition: str, notes: str, profile: dict) -> TriageResult | None:
    if result.assertion_status not in _RESCUE_STATUSES and result.assertion_status != "medication_supported":
        return None
    # The note is silent or neutral about this condition. A note that affirms it is left to the
    # model, and a negation, family-history, screening, resolved, or copied-forward sentence wins.
    if _has_present_mention(code, condition, notes) or _blocked_by_chart_context(code, condition, notes):
        return None
    if _billed_code_explains_drug(code, profile):
        return None
    medications = _active_medications(profile.get("medications") or [])
    if drug_specificity(code, medications) != "high":
        return None
    med_lines = _related_meds(code, medications)
    if not med_lines:
        return None
    quotes = []
    for sentence in _sentences(notes):
        if _MED_SUPPORT_CUE.search(sentence):
            quotes.append(sentence)
            break
    if med_lines[0] not in quotes:
        quotes.append(med_lines[0])
    return TriageResult(
        assertion_status="medication_supported",
        supporting_quotes=quotes,
        section="structured",
        meat=MeatQuotes(treat=med_lines[0]),
        confidence=0.55,
    )


def _lab_only_rescue(
    result: TriageResult, code: str, notes: str, profile: dict, gap: dict | None, condition: str = ""
) -> TriageResult | None:
    if not _note_empty(notes) and (
        _has_present_mention(code, condition, notes) or _blocked_by_chart_context(code, condition, notes)
    ):
        return None
    if str(code).upper().startswith("N18") and result.assertion_status == "hypothetical_plan":
        return None
    if result.assertion_status not in _RESCUE_STATUSES | {"lab_only", "hypothetical_plan"}:
        return None
    if result.assertion_status == "hypothetical_plan" and str(code).upper().startswith("N18"):
        return None
    observation = supporting_lab_observation(code, profile.get("observations") or [], gap)
    if observation is None:
        return None
    quote = lab_line(observation)
    return TriageResult(
        assertion_status="lab_only",
        supporting_quotes=[quote],
        section="structured",
        meat=MeatQuotes(monitor=quote),
        confidence=min(result.confidence or 0.62, 0.62) if result.assertion_status == "lab_only" else 0.62,
    )


def reconcile_triage(
    result: TriageResult,
    *,
    code: str,
    condition: str,
    notes: str,
    profile: dict,
    gap: dict | None = None,
) -> TriageResult:
    """Keep medication-list and lab evidence from being dropped when the note is silent.

    A high-specificity active drug becomes medication_supported (shown for review) and a
    lab past the code's threshold becomes lab_only (review) when no note sentence affirms or
    rules out the condition. Template or unrelated note text no longer blocks the rescue.

    Runs on the recorded client and on a live triage tag. It does not read gold labels.
    An explicit negation, retraction, family-history, or copied-forward sentence still wins.
    """
    if not isinstance(result, TriageResult):
        return result
    rescued = _medication_rescue(result, code, condition, notes, profile)
    if rescued is None:
        rescued = _lab_only_rescue(result, notes=notes, code=code, profile=profile, gap=gap, condition=condition)
    if rescued is None:
        rescued = result
    if rescued.assertion_status == "medication_supported" and _billed_code_explains_drug(code, profile):
        rescued = TriageResult(
            assertion_status="copied_forward_unclear",
            supporting_quotes=list(rescued.supporting_quotes),
            section=rescued.section,
            confidence=min(rescued.confidence or 0.5, 0.5),
        )
    return _hold_unestablished_n18(rescued, code, notes, profile.get("observations") or [])


def mock_triage(context: dict) -> TriageResult:
    """Apply TRIAGE_INSTRUCTIONS to the chart. No gold labels."""
    result = _triage_from_chart(context)
    candidate = context.get("candidate") or {}
    return reconcile_triage(
        result,
        code=str(candidate.get("icd10_code") or ""),
        condition=str(candidate.get("condition_name") or ""),
        notes=context.get("notes") or "",
        profile=context.get("profile") or {},
        gap=candidate,
    )


def _triage_from_chart(context: dict) -> TriageResult:
    """Chart-text triage before medication-list and lab-only reconciliation."""
    candidate = context.get("candidate") or {}
    profile = context.get("profile") or {}
    notes = context.get("notes") or ""
    code = str(candidate.get("icd10_code") or "")
    condition = str(candidate.get("condition_name") or "")
    keys = keywords_for(code, condition)
    forbid = _forbidden_combination(code, notes)
    if forbid:
        return TriageResult(
            assertion_status="negated",
            supporting_quotes=[forbid],
            section=_section_at(notes, forbid),
            meat=MeatQuotes(),
            confidence=0.86,
        )
    sentences = _sentences(notes)
    mentions: list[tuple[str, str, str, str]] = []
    for index, sentence in enumerate(sentences):
        lowered = sentence.lower()
        if not any(key in lowered for key in keys):
            continue
        following = sentences[index + 1] if index + 1 < len(sentences) else ""
        status = classify_sentence(sentence)
        if status == "present" and sentence_retracts(following):
            status = "negated"
        neighbor = following if status == "negated" and sentence_retracts(following) else ""
        mentions.append((status, _section_at(notes, sentence), sentence, neighbor))

    labs = _related_labs(code, profile.get("observations") or [])
    meds = _related_meds(code, profile.get("medications") or [])
    chosen = _choose_mention(mentions)
    if chosen is not None:
        status, section, sentence, neighbor = chosen
        quotes = [sentence]
        if neighbor and neighbor not in quotes:
            quotes.append(neighbor)
        meat = MeatQuotes()
        if status == "present" and section == "assessment_plan":
            meat.assess = sentence
        if status == "present" and labs:
            quotes.append(labs[0])
            meat.monitor = labs[0]
        if status == "present" and meds and drug_specificity(code, profile.get("medications") or []) != "low":
            quotes.append(meds[0])
            meat.treat = meds[0]
        confidence = _confidence(status, section, bool(labs), bool(meds))
        return _hold_unestablished_n18(
            TriageResult(
                assertion_status=status,
                supporting_quotes=quotes,
                section=section,
                meat=meat,
                confidence=confidence,
            ),
            code,
            notes,
            profile.get("observations") or [],
        )

    if labs:
        return _hold_unestablished_n18(
            TriageResult(
                assertion_status="present",
                supporting_quotes=[labs[0]],
                section="structured",
                meat=MeatQuotes(monitor=labs[0]),
                confidence=0.78,
            ),
            code,
            notes,
            profile.get("observations") or [],
        )
    if meds:
        specificity = drug_specificity(code, profile.get("medications") or [])
        if specificity == "low":
            return TriageResult(
                assertion_status="copied_forward_unclear",
                supporting_quotes=[meds[0]],
                section="structured",
                meat=MeatQuotes(),
                confidence=0.86,
            )
        return TriageResult(
            assertion_status="present",
            supporting_quotes=[meds[0]],
            section="structured",
            meat=MeatQuotes(treat=meds[0]),
            confidence=0.55,
        )
    return TriageResult(
        assertion_status="copied_forward_unclear",
        supporting_quotes=[],
        section="unknown",
        meat=MeatQuotes(),
        confidence=0.3,
    )


def _choose_mention(mentions: list[tuple[str, str, str, str]]):
    """An assessed affirmation wins. Otherwise an explicit negative wins over a bare listing."""
    if not mentions:
        return None
    affirming = [row for row in mentions if row[0] == "present" and affirming_sentence(row[2])]
    if affirming:
        affirming.sort(key=lambda row: _rank(row[0], row[1]), reverse=True)
        return affirming[0]
    negatives = [row for row in mentions if row[0] in _NEGATIVE_STATUS]
    if negatives:
        negatives.sort(key=lambda row: _NEGATIVE_STATUS.index(row[0]))
        return negatives[0]
    present = [row for row in mentions if row[0] == "present"]
    if present:
        present.sort(key=lambda row: _rank(row[0], row[1]), reverse=True)
        return present[0]
    return mentions[0]


def _rank(status: str, section: str) -> int:
    if status == "present" and section == "assessment_plan":
        return 100
    if status == "present":
        return 70
    if status == "negated":
        return 40
    if status == "family_history":
        return 30
    if status == "screening_or_rule_out":
        return 25
    return 10


def _confidence(status: str, section: str, has_lab: bool, has_med: bool) -> float:
    if status != "present":
        return 0.86
    if section == "assessment_plan" and (has_lab or has_med):
        return 0.9
    if section == "assessment_plan":
        return 0.84
    if has_lab:
        return 0.78
    if has_med:
        return 0.55
    return 0.62


def _neighbor_retracts(context: dict) -> bool:
    for window in context.get("mention_windows") or []:
        if isinstance(window, dict) and window.get("retracted_by_following_sentence"):
            return True
        following = window.get("following_sentence") if isinstance(window, dict) else ""
        if sentence_retracts(str(following or "")):
            return True
    return sentence_retracts(str(context.get("following_sentence") or ""))


def mock_adjudicate(context: dict) -> AdjudicationResult:
    gap_type = context.get("gap_type") or "missing"
    citations = context.get("citations") or []
    confidence = float(context.get("confidence") or 0)
    code = str(context.get("icd10_code") or "")
    display = str(context.get("code_display") or code)
    needs_guideline = gap_type in {"specificity_upgrade", "combination"}
    assertion = str(context.get("assertion_status") or "")
    if assertion in _NEGATIVE_STATUS:
        decision = "reject"
        rationale = (
            "The chart does not support this code. The mention is negated, family history, "
            "screening, hypothetical, resolved, or only a non-specific medication."
        )
        query_needed = False
    elif assertion == "medication_supported":
        decision = "abstain"
        rationale = (
            "An active medication list with a current refill supports human review, not an automatic add."
        )
        query_needed = True
    elif assertion == "lab_only":
        decision = "review"
        rationale = "The chart has lab evidence only. A coder should confirm MEAT before this is billed."
        query_needed = True
    elif _neighbor_retracts(context):
        decision = "reject"
        rationale = (
            "The next sentence retracts this diagnosis and does not have to repeat the condition name."
        )
        query_needed = False
    elif needs_guideline and not citations:
        decision = "review"
        rationale = (
            "Guideline evidence was missing, so this combination or specificity code cannot be accepted."
        )
        query_needed = True
    elif confidence < 0.5:
        decision = "abstain"
        rationale = "Evidence is thin, so this candidate is abstained to a human reviewer."
        query_needed = True
    elif needs_guideline and citations and confidence >= 0.6:
        decision = "accept"
        rationale = "Verified chart quote and a guideline citation support this combination or specificity code."
        query_needed = False
    elif confidence >= 0.8:
        decision = "accept"
        rationale = "Verified evidence is sufficient to accept this candidate."
        query_needed = False
    elif confidence >= 0.6:
        decision = "review"
        rationale = "Evidence is suggestive but not strong enough to accept without a coder."
        query_needed = True
    else:
        decision = "abstain"
        rationale = "Confidence is too low to accept or reject. Abstaining to a human."
        query_needed = True
    return AdjudicationResult(
        decision=decision,
        icd10_code=code,
        code_display=display,
        guideline_citations=[],
        rationale=limit_words(rationale, 60),
        physician_query_needed=query_needed,
    )


def mock_ultra(context: dict) -> AdjudicationResult:
    prior = str(context.get("prior_decision") or "review")
    assertion = str(context.get("assertion_status") or "present")
    citations = context.get("citations") or []
    gap_type = str(context.get("gap_type") or "missing")
    code = str(context.get("icd10_code") or "")
    display = str(context.get("code_display") or code)
    if _neighbor_retracts(context) or assertion == "negated":
        decision = "reject"
        rationale = (
            "The next sentence retracts this diagnosis and does not have to repeat the condition name."
        )
        query_needed = False
    elif assertion == "present" and prior == "reject":
        decision = "abstain"
        rationale = "Triage and adjudication disagreed, so this case is abstained to a human reviewer."
        query_needed = True
    elif prior == "accept" and (gap_type not in {"specificity_upgrade", "combination"} or citations):
        decision = "accept"
        rationale = "Ultra confirmed the adjudication on this high-value code."
        query_needed = False
    elif prior in {"accept", "review", "reject", "abstain"}:
        decision = prior
        rationale = "Ultra reviewed the high-value case and left the prior decision in place."
        query_needed = decision in {"review", "abstain"}
    else:
        decision = "abstain"
        rationale = "Ultra could not confirm this case and abstained to a human reviewer."
        query_needed = True
    return AdjudicationResult(
        decision=decision,
        icd10_code=code,
        code_display=display,
        guideline_citations=[],
        rationale=limit_words(rationale, 60),
        physician_query_needed=query_needed,
    )


def mock_physician_query(context: dict) -> PhysicianQueryResult:
    return PhysicianQueryResult(
        query=non_leading_query(
            str(context.get("icd10_code") or ""),
            str(context.get("code_display") or ""),
            list(context.get("quotes") or []),
        ),
        leading=False,
    )


def non_leading_query(code: str, display: str, quotes: list[str]) -> str:
    excerpt = quotes[0] if quotes else "the chart does not contain a single clear statement"
    return (
        "Clinical documentation query\n\n"
        "The record contains this statement:\n"
        f"\"{excerpt}\"\n\n"
        "Please clarify whether this finding is active and being managed, historical or resolved, "
        "or not clinically significant. This query does not ask you to add, remove, or confirm a diagnosis code.\n\n"
        f"Coding-staff reference only (not part of the question): {code} ({display})."
    )


def safe_physician_query(text: str, code: str, display: str, quotes: list[str]) -> str:
    cleaned = (text or "").strip()
    if not cleaned or _LEADING.search(cleaned):
        return non_leading_query(code, display, quotes)
    return cleaned


def gap_type_of(gap: dict) -> str:
    tier = str(gap.get("tier") or "")
    if tier == "COMBINATION_CODE":
        return "combination"
    if tier == "SPECIFICITY_UPGRADE":
        return "specificity_upgrade"
    return "missing"


def guideline_issue(gap: dict) -> str | None:
    gap_type = gap_type_of(gap)
    code = str(gap.get("icd10_code") or "")
    if gap_type == "combination" and code.startswith("E11"):
        return "combination-code-diabetes-ckd"
    if gap_type == "combination" and code.startswith(("I12", "I13")):
        return "combination-code-hypertensive-ckd"
    if gap_type == "specificity_upgrade" and code.startswith("E11"):
        return "specificity-hyperglycemia"
    if gap_type == "combination":
        return "combination-code"
    if gap_type == "specificity_upgrade":
        return "specificity-upgrade"
    return None


def needs_super(confidence: float, gap_type: str) -> bool:
    if gap_type in {"specificity_upgrade", "combination"}:
        return True
    return confidence < 0.8


def needs_escalation(
    *,
    assertion_status: str,
    super_decision: str | None,
    gap_type: str,
    raf: float,
    super_called: bool,
) -> bool:
    if super_called and super_decision is not None:
        if assertion_status == "present" and super_decision == "reject":
            return True
        if assertion_status != "present" and super_decision == "accept":
            return True
    if gap_type in {"specificity_upgrade", "combination"} and raf >= 0.3:
        return True
    return False


def apply_guideline_gate(decision: str, gap_type: str, citations: list, rationale: str) -> tuple[str, str]:
    """Combination and specificity rows cannot be accepted without a citation.

    The explanation is attached even when an earlier step already chose review,
    so the coder can see that the missing guideline is why it was not accepted.
    """
    needs_citation = gap_type in {"specificity_upgrade", "combination"}
    superseded = [
        item for item in citations or []
        if isinstance(item, dict) and item.get("anchor_status") == "superseded" and item.get("provenance") == "tavily_live"
    ]
    if needs_citation and superseded:
        old = superseded[0].get("supersedes") or {}
        note = (
            f"Decided on the {superseded[0].get('fiscal_year') or 'current'} guideline text "
            f"({superseded[0].get('section') or 'section'} p. {superseded[0].get('page') or '?'}); "
            f"the curated {old.get('fiscal_year') or 'prior-year'} passage was superseded."
        )
        if note not in rationale:
            rationale = f"{note} {rationale}".strip()
    if needs_citation and not citations and decision == "accept":
        decision = "review"
    if needs_citation and not citations and decision == "review":
        prefix = "Guideline evidence was missing, so this combination or specificity code cannot be accepted."
        if prefix not in rationale:
            rationale = f"{prefix} {rationale}".strip()
    return decision, limit_words(rationale, 60)
