"""Build the Gold-B synthetic corpus.

90 FHIR R4 charts = 30 scenarios x 3 hand-written paraphrases. Labels are
assigned in this file before the note is rendered. No language model writes
the text, so the benchmark does not grade Nemotron on Nemotron prose.

Split rule (deterministic, not a shuffle):
- scenarios 0-11, paraphrase 0 -> train (12 charts)
- scenarios 12-23, paraphrase 0 -> dev (12 charts)
- every other chart -> test (66 charts)

Every scenario therefore has at least two held-out test charts.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from eval.charts import BUNDLE_DIR, GOLD_DIR, build_bundle, render_note

SEED = 20261024
TEXT_SOURCE = "hand_paraphrase_bank_v1"

# clause paraphrases shared by the straightforward missing-code scenarios.
_CLAUSE = (
    "{c} is active.",
    "Assessment: {c}, currently active.",
    "The assessment records that {c} and it is active.",
)


def _split(scenario_index: int, variant: int) -> str:
    if variant == 0 and scenario_index < 12:
        return "train"
    if variant == 0 and scenario_index < 24:
        return "dev"
    return "test"


def _gap(code: str, gtype: str, quote: str, *, kind: str = "note", from_code: str | None = None,
         rules_should_find: bool = True) -> dict[str, Any]:
    item: dict[str, Any] = {
        "code": code,
        "type": gtype,
        "evidence_quote": quote,
        "evidence_kind": kind,
        "rules_should_find": rules_should_find,
    }
    if from_code:
        item["from_code"] = from_code
    return item


def _trap(code: str, category: str, quote: str, *, rules_should_flag: bool = True,
          kind: str = "note") -> dict[str, Any]:
    return {
        "code": code,
        "category": category,
        "evidence_quote": quote,
        "evidence_kind": kind,
        "rules_should_flag": rules_should_flag,
    }


def _unsupported(code: str, quote: str) -> dict[str, Any]:
    return {"code": code, "evidence_quote": quote, "reason": quote}


def _spec(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("conditions", [])
    kwargs.setdefault("observations", [])
    kwargs.setdefault("medications", [])
    kwargs.setdefault("hpi", "Follow-up visit at the synthetic clinic.")
    kwargs.setdefault("problems", "See assessment.")
    kwargs.setdefault("expected_gaps", [])
    kwargs.setdefault("must_not_flag", [])
    kwargs.setdefault("unsupported_existing", [])
    return kwargs


def _clause(variant: int, clause: str) -> str:
    return _CLAUSE[variant].format(c=clause)


# ── straightforward missing-code scenarios ────────────────────────────

def missing_dm(v: int) -> dict:
    a1c = (7.4, 8.1, 9.0)[v]
    sent = _clause(v, "type 2 diabetes mellitus with hyperglycemia")
    ap = f"{sent} HbA1c is {a1c} percent. Continue metformin."
    return _spec(
        medications=["Metformin 1000mg"],
        observations=[("4548-4", "HbA1c", a1c, "%")],
        problems="Type 2 diabetes mellitus.",
        ap=ap,
        expected_gaps=[_gap("E11.65", "missing", "type 2 diabetes mellitus with hyperglycemia")],
    )


def missing_ckd(v: int) -> dict:
    egfr = (48, 55, 42)[v]
    sent = _clause(v, "chronic kidney disease stage 3")
    ap = f"{sent} The eGFR is {egfr}."
    return _spec(
        observations=[("33914-3", "eGFR", egfr, "mL/min/1.73m2")],
        problems="Chronic kidney disease stage 3.",
        ap=ap,
        expected_gaps=[_gap("N18.3", "missing", "chronic kidney disease stage 3")],
        must_not_flag=[_trap("N18.9", "unspecified_sibling", "chronic kidney disease stage 3")],
    )


def missing_hf(v: int) -> dict:
    bnp = (520, 640, 810)[v]
    sent = _clause(v, "heart failure")
    ap = f"{sent} BNP is {bnp}. Continue furosemide."
    return _spec(
        medications=["Furosemide 40mg"],
        observations=[("42637-9", "BNP", bnp, "pg/mL")],
        problems="Heart failure.",
        ap=ap,
        expected_gaps=[_gap("I50.9", "missing", "heart failure")],
    )


def missing_copd(v: int) -> dict:
    sent = _clause(v, "COPD")
    ap = f"{sent} Continue tiotropium."
    return _spec(
        medications=["Tiotropium 18mcg"],
        problems="COPD.",
        ap=ap,
        expected_gaps=[_gap("J44.1", "missing", sent)],
    )


def missing_hypothyroid(v: int) -> dict:
    tsh = (6.2, 8.4, 11.1)[v]
    sent = _clause(v, "hypothyroidism")
    ap = f"{sent} TSH is {tsh}. Continue levothyroxine."
    return _spec(
        medications=["Levothyroxine 75mcg"],
        observations=[("3016-3", "TSH", tsh, "mIU/L")],
        problems="Hypothyroidism.",
        ap=ap,
        expected_gaps=[_gap("E03.9", "missing", "hypothyroidism")],
    )


def missing_hld(v: int) -> dict:
    ldl = (162, 171, 178)[v]
    sent = _clause(v, "hyperlipidemia")
    ap = f"{sent} LDL is {ldl}. Continue atorvastatin."
    return _spec(
        medications=["Atorvastatin 20mg"],
        observations=[("2089-1", "LDL", ldl, "mg/dL")],
        problems="Hyperlipidemia.",
        ap=ap,
        expected_gaps=[_gap("E78.5", "missing", "hyperlipidemia")],
    )


def missing_htn(v: int) -> dict:
    sent = _clause(v, "hypertension")
    ap = f"{sent} Continue lisinopril."
    return _spec(
        medications=["Lisinopril 10mg"],
        problems="Hypertension.",
        ap=ap,
        expected_gaps=[_gap("I10", "missing", "hypertension")],
        must_not_flag=[_trap(
            "N18.9", "medication_only", "lisinopril",
            kind="medication",
        )],
    )


def missing_anemia(v: int) -> dict:
    hgb = (10.4, 11.1, 11.6)[v]
    sent = _clause(v, "anemia")
    ap = f"{sent} Hemoglobin is {hgb}."
    return _spec(
        observations=[("718-7", "Hemoglobin", hgb, "g/dL")],
        problems="Anemia.",
        ap=ap,
        expected_gaps=[_gap("D64.9", "missing", "anemia")],
    )


def missing_depression(v: int) -> dict:
    sent = _clause(v, "major depressive disorder")
    ap = f"{sent} Continue sertraline."
    return _spec(
        medications=["Sertraline 50mg"],
        problems="Major depressive disorder.",
        ap=ap,
        expected_gaps=[_gap("F32.9", "missing", "major depressive disorder")],
    )


def specificity_e1165(v: int) -> dict:
    a1c = (7.6, 8.4, 9.2)[v]
    sent = _clause(v, "type 2 diabetes mellitus with hyperglycemia")
    ap = f"{sent} HbA1c is {a1c} percent. Continue metformin. E11.9 is the code on the list."
    return _spec(
        conditions=[("E11.9", "Type 2 diabetes mellitus without complications")],
        medications=["Metformin 1000mg"],
        observations=[("4548-4", "HbA1c", a1c, "%")],
        problems="Type 2 diabetes mellitus.",
        ap=ap,
        expected_gaps=[_gap(
            "E11.65", "specificity_upgrade", "type 2 diabetes mellitus with hyperglycemia",
            from_code="E11.9",
        )],
    )


def combo_e1122(v: int) -> dict:
    a1c = (7.8, 8.6, 9.4)[v]
    ap = (
        f"{_clause(v, 'type 2 diabetes mellitus with hyperglycemia')} "
        f"HbA1c is {a1c} percent. Diabetic nephropathy is active."
    )
    return _spec(
        conditions=[
            ("E11.9", "Type 2 diabetes mellitus without complications"),
            ("N18.3", "Chronic kidney disease, stage 3"),
        ],
        medications=["Metformin 1000mg"],
        observations=[("4548-4", "HbA1c", a1c, "%")],
        problems="Type 2 diabetes mellitus. Chronic kidney disease stage 3.",
        ap=ap,
        expected_gaps=[
            _gap("E11.65", "specificity_upgrade", "type 2 diabetes mellitus with hyperglycemia", from_code="E11.9"),
            _gap("E11.22", "combination", "Diabetic nephropathy is active.", from_code="E11.9"),
        ],
    )


def combo_i129(v: int) -> dict:
    sent = (
        "Hypertensive chronic kidney disease is active.",
        "Assessment: hypertensive chronic kidney disease, currently active.",
        "The assessment records that hypertensive chronic kidney disease and it is active.",
    )[v]
    return _spec(
        conditions=[
            ("I10", "Essential hypertension"),
            ("N18.3", "Chronic kidney disease, stage 3"),
        ],
        problems="Hypertension. Chronic kidney disease stage 3.",
        ap=sent,
        expected_gaps=[_gap("I12.9", "combination", "hypertensive chronic kidney disease", from_code="I10")],
    )


# ── scenarios where the cascade, not just the rules, has work to do ──

def combo_with_convention(v: int) -> dict:
    """Both parents are coded and assessed, but the note never uses a link phrase.

    Rules still emit E11.22. Offline triage leaves it for a human. Guideline
    grounding accepts it under the diabetes 'with' convention.
    """
    a1c = (7.5, 8.2, 8.9)[v]
    ap = (
        f"{_clause(v, 'type 2 diabetes mellitus with hyperglycemia')} HbA1c is {a1c} percent. "
        "Chronic kidney disease stage 3 is active."
    )
    return _spec(
        conditions=[
            ("E11.9", "Type 2 diabetes mellitus without complications"),
            ("N18.3", "Chronic kidney disease, stage 3"),
        ],
        medications=["Metformin 1000mg"],
        observations=[("4548-4", "HbA1c", a1c, "%")],
        problems="Type 2 diabetes mellitus. Chronic kidney disease stage 3.",
        ap=ap,
        expected_gaps=[
            _gap("E11.65", "specificity_upgrade", "type 2 diabetes mellitus with hyperglycemia", from_code="E11.9"),
            _gap("E11.22", "combination", "Chronic kidney disease stage 3 is active.", from_code="E11.9"),
        ],
    )


def combo_forbid(v: int) -> dict:
    a1c = (7.7, 8.3, 9.1)[v]
    forbid = "Do not use a diabetic kidney combination."
    ap = (
        "Hypertensive chronic kidney disease is active. "
        f"{_clause(v, 'type 2 diabetes mellitus with hyperglycemia')} HbA1c is {a1c} percent. "
        f"{forbid}"
    )
    return _spec(
        conditions=[
            ("E11.9", "Type 2 diabetes mellitus without complications"),
            ("N18.3", "Chronic kidney disease, stage 3"),
            ("I10", "Essential hypertension"),
        ],
        medications=["Metformin 1000mg"],
        observations=[("4548-4", "HbA1c", a1c, "%")],
        problems="Hypertension. Type 2 diabetes mellitus. Chronic kidney disease stage 3.",
        ap=ap,
        expected_gaps=[
            _gap("I12.9", "combination", "Hypertensive chronic kidney disease is active.", from_code="I10"),
            _gap("E11.65", "specificity_upgrade", "type 2 diabetes mellitus with hyperglycemia", from_code="E11.9"),
        ],
        must_not_flag=[_trap("E11.22", "negation", forbid)],
    )


def missing_osa(v: int) -> dict:
    sent = (
        "Obstructive sleep apnea is active and CPAP is in use.",
        "Assessment: obstructive sleep apnea, currently active, and CPAP is in use.",
        "The assessment records that obstructive sleep apnea and it is active. CPAP is in use.",
    )[v]
    return _spec(
        problems="Obstructive sleep apnea.",
        ap=sent,
        expected_gaps=[_gap("G47.33", "missing", "obstructive sleep apnea")],
    )


def missing_afib(v: int) -> dict:
    sent = _clause(v, "atrial fibrillation")
    return _spec(
        problems="Atrial fibrillation.",
        ap=sent,
        expected_gaps=[_gap("I48.91", "missing", "atrial fibrillation")],
    )


def hf_specificity(v: int) -> dict:
    """Rules emit I50.9 and I50.22. The note documents HFrEF. Gold is I50.22; the cascade drops I50.9 once I50.22 is accepted."""
    bnp = (560, 700, 880)[v]
    ef = (35, 30, 32)[v]
    sent = (
        f"Heart failure with reduced ejection fraction is active. The ejection fraction is {ef} percent.",
        f"Assessment: heart failure with reduced ejection fraction, currently active. The ejection fraction is {ef} percent.",
        f"The assessment records that heart failure with reduced ejection fraction and it is active. The ejection fraction is {ef} percent.",
    )[v]
    ap = f"{sent} BNP is {bnp}. Continue furosemide."
    return _spec(
        medications=["Furosemide 40mg"],
        observations=[("42637-9", "BNP", bnp, "pg/mL")],
        problems="Heart failure.",
        ap=ap,
        expected_gaps=[_gap(
            "I50.22", "specificity_upgrade", "reduced ejection fraction",
            from_code="I50.9", rules_should_find=True,
        )],
        must_not_flag=[_trap("I50.9", "wrong_specificity", "reduced ejection fraction")],
    )


def med_only_true(v: int) -> dict:
    note = (
        "Medications were refilled. No new complaints were raised at this visit.",
        "Refills were sent today. No new complaints were raised at this visit.",
        "The visit was a refill check. No new complaints were raised at this visit.",
    )[v]
    return _spec(
        medications=["Metformin 500mg", "Rosuvastatin 10mg", "Lisinopril 10mg"],
        problems="See medication list.",
        ap=note,
        expected_gaps=[
            _gap("E11.9", "missing", "Metformin", kind="medication"),
            _gap("E78.5", "missing", "Rosuvastatin", kind="medication"),
            _gap("I10", "missing", "Lisinopril", kind="medication"),
        ],
        must_not_flag=[_trap("N18.9", "medication_only", "Lisinopril", kind="medication")],
    )


def negation_hf(v: int) -> dict:
    """'does not have' is not one of the rules' negation cues, so the BNP still flags HF."""
    bnp = (160, 180, 220)[v]
    denial = "The patient does not have heart failure."
    ap = f"{_clause(v, 'hypertension')} Continue lisinopril. {denial} BNP is {bnp}."
    return _spec(
        medications=["Lisinopril 10mg"],
        observations=[("42637-9", "BNP", bnp, "pg/mL")],
        problems="Hypertension.",
        ap=ap,
        expected_gaps=[_gap("I10", "missing", "hypertension")],
        must_not_flag=[
            _trap("I50.9", "negation", denial),
            _trap("N18.9", "medication_only", "Lisinopril", kind="medication"),
        ],
    )


def family_hf(v: int) -> dict:
    ldl = (164, 170, 176)[v]
    family = "Mother previously treated for congestive heart failure."
    ap = (
        f"{_clause(v, 'hyperlipidemia')} LDL is {ldl}. Continue atorvastatin. "
        f"{family} Furosemide on the list belongs to her and is not dispensed here."
    )
    return _spec(
        medications=["Atorvastatin 20mg", "Furosemide 40mg"],
        observations=[("2089-1", "LDL", ldl, "mg/dL")],
        problems="Hyperlipidemia.",
        ap=ap,
        expected_gaps=[_gap("E78.5", "missing", "hyperlipidemia")],
        must_not_flag=[_trap("I50.9", "family_history", family)],
    )


def resolved_hf(v: int) -> dict:
    resolved = "Heart failure resolved in 2019 after viral myocarditis."
    ap = (
        f"{_clause(v, 'COPD')} Continue tiotropium. {resolved} "
        "The diuretic started last week was for ankle swelling after a long flight."
    )
    return _spec(
        medications=["Tiotropium 18mcg", "Furosemide 40mg"],
        problems="COPD.",
        ap=ap,
        expected_gaps=[_gap("J44.1", "missing", "COPD")],
        must_not_flag=[_trap("I50.9", "resolved", resolved)],
    )


def screening_mdd(v: int) -> dict:
    # Pad so the rules' 60-character screening window does not reach a later cue,
    # and so the sentence itself contains none of the rules' screening phrases.
    screen = (
        "The form asked about major depressive disorder as a routine item and the rest of this "
        "sentence only notes that the weather was mild during today's synthetic clinic visit."
    )
    ap = f"{_clause(v, 'hypertension')} Continue lisinopril. {screen}"
    return _spec(
        medications=["Lisinopril 10mg", "Sertraline 50mg"],
        problems="Hypertension.",
        ap=ap,
        expected_gaps=[_gap("I10", "missing", "hypertension")],
        must_not_flag=[
            _trap("F32.9", "screening", "routine item"),
            _trap("N18.9", "medication_only", "Lisinopril", kind="medication"),
        ],
    )


def evaluate_copd(v: int) -> dict:
    tsh = (6.4, 7.8, 9.5)[v]
    hypo = "We will evaluate for COPD if the cough persists."
    ap = f"{_clause(v, 'hypothyroidism')} TSH is {tsh}. Continue levothyroxine. {hypo}"
    return _spec(
        medications=["Levothyroxine 75mcg", "Albuterol inhaler"],
        observations=[("3016-3", "TSH", tsh, "mIU/L")],
        problems="Hypothyroidism.",
        ap=ap,
        expected_gaps=[_gap("E03.9", "missing", "hypothyroidism")],
        must_not_flag=[_trap("J44.1", "hypothetical_plan", hypo)],
    )


def copy_forward_copd(v: int) -> dict:
    a1c = (7.9, 8.5, 9.3)[v]
    copied = "COPD remains on the copied problem list from a 2014 visit and is not an active problem."
    ap = (
        f"{_clause(v, 'type 2 diabetes mellitus with hyperglycemia')} "
        f"HbA1c is {a1c} percent. Continue metformin. {copied}"
    )
    return _spec(
        medications=["Metformin 1000mg", "Tiotropium 18mcg"],
        observations=[("4548-4", "HbA1c", a1c, "%")],
        problems="COPD",
        ap=ap,
        expected_gaps=[_gap("E11.65", "missing", "type 2 diabetes mellitus with hyperglycemia")],
        must_not_flag=[_trap("J44.1", "copied_forward", copied)],
    )


def unsupported_copd(v: int) -> dict:
    bad = "COPD was entered on the problem list in error."
    ap = f"{_clause(v, 'hypertension')} Continue lisinopril. {bad} Please remove it."
    return _spec(
        conditions=[("J44.1", "Chronic obstructive pulmonary disease")],
        medications=["Lisinopril 10mg"],
        problems="Hypertension.",
        ap=ap,
        expected_gaps=[_gap("I10", "missing", "hypertension")],
        must_not_flag=[_trap("N18.9", "medication_only", "Lisinopril", kind="medication")],
        unsupported_existing=[_unsupported("J44.1", bad)],
    )


def med_fp_multi(v: int) -> dict:
    denial = "The patient does not have hypertension."
    lines = (
        f"{denial} Omeprazole is for ulcer prophylaxis during a steroid taper. "
        "Sertraline is for nerve discomfort after a rash. Amlodipine is for Raynaud phenomenon.",
        f"{denial} Omeprazole is for ulcer prophylaxis during a steroid taper only. "
        "Sertraline is for nerve discomfort after a rash. Amlodipine is for Raynaud phenomenon.",
        f"{denial} Omeprazole is for ulcer prophylaxis during a short steroid taper. "
        "Sertraline is for nerve discomfort after a rash. Amlodipine is for Raynaud phenomenon.",
    )[v]
    return _spec(
        medications=["Omeprazole 20mg", "Sertraline 50mg", "Amlodipine 5mg"],
        problems="See medication list.",
        ap=lines,
        must_not_flag=[
            _trap("I10", "negation", denial),
            _trap("K21.0", "medication_only", "Omeprazole", kind="medication"),
            _trap("F32.9", "medication_only", "Sertraline", kind="medication"),
            _trap("N18.9", "medication_only", "Amlodipine", kind="medication"),
        ],
    )


def acute_egfr(v: int) -> dict:
    """A single eGFR under 60 with an acute explanation. Rules flag N18.3 from the lab."""
    ldl = (166, 172, 177)[v]
    egfr = (54, 58, 51)[v]
    acute = "Creatinine is up after a few days of poor intake. Repeat the panel next month. This reading is acute."
    ap = f"{_clause(v, 'hyperlipidemia')} LDL is {ldl}. Continue atorvastatin. {acute}"
    return _spec(
        medications=["Atorvastatin 20mg"],
        observations=[
            ("2089-1", "LDL", ldl, "mg/dL"),
            ("33914-3", "eGFR", egfr, "mL/min/1.73m2"),
        ],
        problems="Hyperlipidemia.",
        ap=ap,
        expected_gaps=[_gap("E78.5", "missing", "hyperlipidemia")],
        must_not_flag=[_trap("N18.3", "acute_not_chronic", acute)],
    )


def possible_nafld(v: int) -> dict:
    a1c = (7.3, 8.0, 8.8)[v]
    alt = (68, 74, 81)[v]
    trap = "Possible fatty liver."
    ap = (
        f"{_clause(v, 'type 2 diabetes mellitus with hyperglycemia')} "
        f"HbA1c is {a1c} percent. Continue metformin. ALT is mildly up. {trap} "
        "The hepatitis panel is negative. Monitor only."
    )
    return _spec(
        medications=["Metformin 1000mg"],
        observations=[
            ("4548-4", "HbA1c", a1c, "%"),
            ("1742-6", "ALT", alt, "U/L"),
        ],
        problems="Type 2 diabetes mellitus.",
        ap=ap,
        expected_gaps=[_gap("E11.65", "missing", "type 2 diabetes mellitus with hyperglycemia")],
        must_not_flag=[_trap("K76.89", "hypothetical_plan", trap)],
    )


def cross_sentence_retraction(v: int) -> dict:
    """The retraction never repeats the condition name, so a sentence classifier keeps it.

    This is an intentional residual failure of the offline policy and of the rules.
    """
    tsh = (6.6, 8.1, 10.4)[v]
    listed = "Congestive heart failure is listed below."
    ap = (
        f"{listed} The prior diagnosis was retracted after a normal echocardiogram and the list is wrong. "
        "Furosemide is still on the medication list from that episode and should be stopped. "
        f"{_clause(v, 'hypothyroidism')} TSH is {tsh}. Continue levothyroxine."
    )
    return _spec(
        medications=["Furosemide 40mg", "Levothyroxine 88mcg"],
        observations=[("3016-3", "TSH", tsh, "mIU/L")],
        problems="See assessment.",
        ap=ap,
        expected_gaps=[_gap("E03.9", "missing", "hypothyroidism")],
        must_not_flag=[_trap("I50.9", "cross_sentence_negation", listed)],
    )


def combo_parent_uncoded(v: int) -> dict:
    """Diabetic nephropathy is assessed, but CKD is not yet on the condition list.

    The rules propose E11.22 from that chart evidence so the cascade can verify it.
    """
    a1c = (8.0, 8.7, 9.5)[v]
    egfr = (44, 40, 46)[v]
    ap = (
        f"{_clause(v, 'type 2 diabetes mellitus with hyperglycemia')} HbA1c is {a1c} percent. "
        f"Continue metformin. Chronic kidney disease stage 3 is active. The eGFR is {egfr}. "
        "Diabetic nephropathy is active."
    )
    return _spec(
        conditions=[("E11.9", "Type 2 diabetes mellitus without complications")],
        medications=["Metformin 1000mg"],
        observations=[
            ("4548-4", "HbA1c", a1c, "%"),
            ("33914-3", "eGFR", egfr, "mL/min/1.73m2"),
        ],
        problems="Type 2 diabetes mellitus. Chronic kidney disease stage 3.",
        ap=ap,
        expected_gaps=[
            _gap("E11.65", "specificity_upgrade", "type 2 diabetes mellitus with hyperglycemia", from_code="E11.9"),
            _gap("N18.3", "missing", "chronic kidney disease stage 3"),
            _gap("E11.22", "combination", "Diabetic nephropathy is active.", from_code="E11.9"),
        ],
        must_not_flag=[_trap("N18.9", "unspecified_sibling", "chronic kidney disease stage 3")],
    )


SCENARIOS: list[tuple[str, Callable[[int], dict]]] = [
    ("missing_dm", missing_dm),
    ("missing_ckd", missing_ckd),
    ("missing_hf", missing_hf),
    ("missing_copd", missing_copd),
    ("missing_hypothyroid", missing_hypothyroid),
    ("missing_hld", missing_hld),
    ("missing_htn", missing_htn),
    ("missing_anemia", missing_anemia),
    ("missing_depression", missing_depression),
    ("specificity_e1165", specificity_e1165),
    ("combo_e1122", combo_e1122),
    ("combo_i129", combo_i129),
    ("combo_with_convention", combo_with_convention),
    ("combo_forbid", combo_forbid),
    ("missing_osa", missing_osa),
    ("missing_afib", missing_afib),
    ("hf_specificity", hf_specificity),
    ("med_only_true", med_only_true),
    ("negation_hf", negation_hf),
    ("family_hf", family_hf),
    ("resolved_hf", resolved_hf),
    ("screening_mdd", screening_mdd),
    ("evaluate_copd", evaluate_copd),
    ("copy_forward_copd", copy_forward_copd),
    ("unsupported_copd", unsupported_copd),
    ("med_fp_multi", med_fp_multi),
    ("acute_egfr", acute_egfr),
    ("possible_nafld", possible_nafld),
    ("cross_sentence_retraction", cross_sentence_retraction),
    ("combo_parent_uncoded", combo_parent_uncoded),
]


def build_chart(scenario_index: int, variant: int, patient_id: str) -> dict[str, Any]:
    name, fn = SCENARIOS[scenario_index]
    spec = fn(variant)
    note = render_note(spec["hpi"], spec["problems"], spec["ap"])
    gender = "female" if scenario_index % 2 else "male"
    year = 1948 + (scenario_index % 20)
    month = (scenario_index % 12) + 1
    birth = f"{year}-{month:02d}-15"
    bundle = build_bundle(
        patient_id,
        gender=gender,
        birth_date=birth,
        conditions=spec["conditions"],
        observations=spec["observations"],
        medications=spec["medications"],
        note=note,
    )
    return {
        "patient_id": patient_id,
        "dataset": "gold-b",
        "split": _split(scenario_index, variant),
        "scenario": name,
        "scenario_index": scenario_index,
        "paraphrase": variant,
        "seed": SEED,
        "text_source": TEXT_SOURCE,
        "not_model_authored": True,
        "expected_gaps": spec["expected_gaps"],
        "must_not_flag": spec["must_not_flag"],
        "unsupported_existing": spec["unsupported_existing"],
        "bundle": bundle,
        "note": note,
    }


def build_all() -> list[dict[str, Any]]:
    charts = []
    n = 0
    for index in range(len(SCENARIOS)):
        for variant in range(3):
            n += 1
            charts.append(build_chart(index, variant, f"gb-{n:04d}"))
    return charts


def write_corpus(charts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    charts = charts if charts is not None else build_all()
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    labels = []
    for chart in charts:
        bundle_path = BUNDLE_DIR / f"{chart['patient_id']}.json"
        bundle_path.write_text(json.dumps(chart["bundle"], indent=2) + "\n", encoding="utf-8")
        label = {k: v for k, v in chart.items() if k not in {"bundle", "note"}}
        labels.append(label)
    jsonl = GOLD_DIR / "gold_b.jsonl"
    jsonl.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in labels), encoding="utf-8")
    counts: dict[str, int] = {}
    for row in labels:
        counts[row["split"]] = counts.get(row["split"], 0) + 1
    manifest = {
        "dataset": "gold-b",
        "seed": SEED,
        "n_charts": len(labels),
        "n_scenarios": len(SCENARIOS),
        "paraphrases_per_scenario": 3,
        "splits": counts,
        "text_source": TEXT_SOURCE,
        "not_model_authored": True,
        "split_rule": (
            "scenarios 0-11 paraphrase 0 = train; "
            "scenarios 12-23 paraphrase 0 = dev; "
            "all remaining charts = test"
        ),
        "label_sha256": hashlib.sha256(jsonl.read_bytes()).hexdigest(),
        "phi": "synthetic FHIR only; no names of real people, no addresses, no contact details",
    }
    (GOLD_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    manifest = write_corpus()
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
