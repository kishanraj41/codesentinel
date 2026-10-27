"""Scripted synthetic audits in the POST /audit shape (contract 1.0.0).

Every verified quote is an exact substring of that chart's note. Guideline
quotes are labeled offline fixtures. Receipt dollars are catalog estimates.
"""

from __future__ import annotations

import copy
import re

from app.catalog import LIGHTNING, SUPER, ULTRA, cost_usd, price_table

RECORDED_AT = "2026-10-24T19:00:00-05:00"
CONTRACT = "1.0.0"
CMS_URL = "https://www.cms.gov/medicare/coding-billing/icd-10-codes"
FIXTURE_STAMP = "Recorded offline fixture, not a live guideline excerpt."

_ICD = re.compile(r"\b[A-Z]\d{2}(?:\.[0-9A-Z]+)?\b")

HCC_TABLE = {
    "E11": ("HCC 37", "Diabetes without Complication", 0.166),
    "E11.2": ("HCC 18", "Diabetes with Chronic Complications", 0.302),
    "E11.4": ("HCC 18", "Diabetes with Neurological Complications", 0.302),
    "E11.6": ("HCC 18", "Diabetes with Other Complications", 0.302),
    "E11.65": ("HCC 37", "DM2 with Hyperglycemia", 0.166),
    "N18.3": ("HCC 138", "CKD Stage 3", 0.069),
    "I50.2": ("HCC 85", "Systolic Heart Failure", 0.323),
    "I50.9": ("HCC 85", "Heart Failure Unspecified", 0.323),
    "E78.0": ("Non-HCC", "Pure Hypercholesterolemia", 0.0),
    "E78.5": ("Non-HCC", "Hyperlipidemia", 0.0),
    "F32.9": ("HCC 155", "Major Depression", 0.309),
    "E03.9": ("Non-HCC", "Hypothyroidism", 0.0),
    "E11.9": ("HCC 37", "Diabetes without Complication", 0.166),
}

STEPS = (
    ("rules", "Rules", None),
    ("triage", "Lightning triage", LIGHTNING),
    ("verifier", "Quote verifier", None),
    ("guidelines", "Tavily guidelines", None),
    ("adjudication", "Super adjudication", SUPER),
    ("escalation", "Ultra escalation", ULTRA),
    ("physician_query", "Physician query", ULTRA),
)


def q(note: str, snippet: str) -> str:
    if snippet not in note:
        raise ValueError(f"Quote is not in the note: {snippet!r}")
    return snippet


def hcc_for(code: str) -> tuple[str, str, float]:
    if code in HCC_TABLE:
        return HCC_TABLE[code]
    for length in range(len(code) - 1, 2, -1):
        prefix = code[:length]
        if prefix in HCC_TABLE:
            return HCC_TABLE[prefix]
    return ("Non-HCC", "", 0.0)


def cite(code: str, issue: str, sentence: str) -> dict:
    quote = f"{FIXTURE_STAMP} {sentence}"
    return {
        "url": CMS_URL,
        "quote": quote,
        "source": "cms.gov",
        "title": "ICD-10-CM codes and official guidelines",
        "retrieved": "fixture",
        "cached_at": RECORDED_AT,
        "code_family": code[:3],
        "issue": issue,
        "source_tier": "secondary",
        "source_label": "Secondary source",
    }


def work(
    note: str,
    *,
    pid: str,
    code: str,
    display: str,
    gap_type: str,
    decision: str,
    confidence: float,
    quotes: list[str],
    section: str,
    rationale: str,
    model: str,
    tier: str,
    meat: dict,
    lab: dict | None = None,
    upgrade_from: str | None = None,
    query: str | None = None,
    citations: list[dict] | None = None,
    assertion: str = "present",
    sources: list[str] | None = None,
) -> dict:
    verified = [q(note, snippet) for snippet in quotes]
    meat_out = {}
    for key in ("monitor", "evaluate", "assess", "treat"):
        value = meat.get(key) if meat else None
        meat_out[key] = q(note, value) if value else None
    hcc, desc, raf = hcc_for(code)
    return {
        "id": f"{pid}:{code}",
        "icd10_code": code,
        "code_display": display,
        "gap_type": gap_type,
        "upgrade_from": upgrade_from,
        "decision": decision,
        "confidence": confidence,
        "assertion_status": assertion,
        "section": section,
        "supporting_quotes": list(verified),
        "verified_quotes": list(verified),
        "meat": meat_out,
        "lab": lab,
        "hcc_category": hcc,
        "hcc_description": desc,
        "raf_value": raf,
        "annual_revenue_impact": round(raf * 11000, 2),
        "rationale": rationale,
        "physician_query_needed": bool(query),
        "physician_query": query,
        "decided_by": model,
        "tier": tier,
        "guideline_citations": citations or [],
        "evidence_sources": sources or [section],
    }


def drop(
    note: str,
    *,
    pid: str,
    code: str,
    display: str,
    reason: str,
    detail: str,
    quotes: list[str],
    by: str,
    model: str | None,
    gap_type: str = "missing",
    tier: str = "TIER_3_NOTE_NER",
) -> dict:
    return {
        "id": f"{pid}:{code}",
        "icd10_code": code,
        "code_display": display,
        "gap_type": gap_type,
        "tier": tier,
        "reason": reason,
        "reason_detail": detail,
        "supporting_quotes": [q(note, snippet) for snippet in quotes],
        "dropped_by": by,
        "model": model,
    }


def make_timeline(specs: dict[str, dict]) -> list[dict]:
    rows = []
    for step, label, default_model in STEPS:
        spec = specs.get(step, {})
        status = spec.get("status", "skipped")
        model = None if step in ("rules", "verifier") else spec.get("model", default_model)
        rows.append(
            {
                "step": step,
                "label": label,
                "model": model,
                "status": status,
                "latency_ms": int(spec.get("latency_ms", 0)),
                "detail": spec.get("detail", "Not needed on this chart."),
                "mock": True,
            }
        )
    return rows


def make_receipt(timeline: list[dict], usage: dict[str, dict]) -> dict:
    steps = []
    for row in timeline:
        use = usage.get(row["step"], {})
        model = use.get("model", row["model"])
        incoming = int(use.get("input_tokens", 0))
        outgoing = int(use.get("output_tokens", 0))
        calls = int(use.get("calls", 0))
        credits = int(use.get("tavily_credits", 0))
        hits = int(use.get("cache_hits", 0))
        if "usage_source" in use:
            source = use["usage_source"]
        elif calls or incoming or outgoing:
            source = "estimated"
        else:
            source = "none"
        steps.append(
            {
                "step": row["step"],
                "model": model,
                "calls": calls,
                "input_tokens": incoming,
                "output_tokens": outgoing,
                "latency_ms": row["latency_ms"],
                "cost_usd": cost_usd(model, incoming, outgoing),
                "mock": True,
                "usage_source": source,
                "tavily_credits": credits,
                "cache_hits": hits,
            }
        )
    models_used: list[str] = []
    for step in steps:
        if step["model"] and step["calls"] and step["model"] not in models_used:
            models_used.append(step["model"])
    totals = {
        "input_tokens": sum(step["input_tokens"] for step in steps),
        "output_tokens": sum(step["output_tokens"] for step in steps),
        "latency_ms": sum(step["latency_ms"] for step in steps),
        "cost_usd": round(sum(step["cost_usd"] for step in steps), 6),
        "tavily_credits": sum(step["tavily_credits"] for step in steps),
        "tavily_cache_hits": sum(step["cache_hits"] for step in steps),
        "calls": sum(step["calls"] for step in steps),
    }
    return {
        "steps": steps,
        "totals": totals,
        "models_used": models_used,
        "catalog_prices_per_million": price_table(),
        "kind": "catalog_estimate",
        "disclaimer": (
            "Catalog-price estimate for this scripted cache. "
            "Token counts are estimated, not a Token Factory bill."
        ),
    }


def headline(summary: dict, worklist: list[dict]) -> str:
    count = summary["worklist_count"]
    noun = "code" if count == 1 else "codes"
    parts = [f"{count} {noun} to review"]
    upgrades = summary["specificity_upgrades"]
    if upgrades:
        parts.append(f"{upgrades} specificity upgrade" + ("s" if upgrades != 1 else ""))
    combos = summary["combination_codes"]
    if combos:
        parts.append(f"{combos} combination code" + ("s" if combos != 1 else ""))
    abstains = summary["abstain_count"]
    if abstains == 1:
        parts.append("1 abstain, held for a human")
    elif abstains:
        parts.append(f"{abstains} abstains, held for a human")
    unsupported = sum(1 for item in worklist if item.get("gap_type") == "unsupported_existing")
    if unsupported == 1:
        parts.append("1 unsupported code on the claim")
    elif unsupported:
        parts.append(f"{unsupported} unsupported codes on the claim")
    return " · ".join(parts)


def summarize(worklist: list[dict], dropped: list[dict], rules_rejected: int = 0) -> dict:
    candidates = len(worklist) + len(dropped)
    accepts = sum(item["decision"] == "accept" for item in worklist)
    return {
        "candidates_generated": candidates,
        "rules_approved": accepts,
        "rules_review": candidates - accepts,
        "rules_rejected": rules_rejected,
        "worklist_count": len(worklist),
        "accept_count": accepts,
        "review_count": sum(item["decision"] == "review" for item in worklist),
        "abstain_count": sum(item["decision"] == "abstain" for item in worklist),
        "specificity_upgrades": sum(item["gap_type"] == "specificity_upgrade" for item in worklist),
        "combination_codes": sum(item["gap_type"] == "combination" for item in worklist),
        "dropped_count": len(dropped),
    }


def citations_of(worklist: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    union: list[dict] = []
    for item in worklist:
        for item_cite in item["guideline_citations"]:
            key = (item_cite["issue"], item_cite["quote"])
            if key in seen:
                continue
            seen.add(key)
            union.append(item_cite)
    return union


def finish(
    *,
    pid: str,
    name: str,
    note: str,
    coded: list[dict],
    worklist: list[dict],
    dropped: list[dict],
    timeline_specs: dict,
    usage: dict,
    rules_rejected: int = 0,
) -> dict:
    order = {"accept": 0, "review": 1, "abstain": 2}
    worklist = sorted(worklist, key=lambda item: (order[item["decision"]], -item["confidence"]))
    summary = summarize(worklist, dropped, rules_rejected)
    summary["headline"] = headline(summary, worklist)
    timeline = make_timeline(timeline_specs)
    audit = {
        "contract_version": CONTRACT,
        "audit_id": f"cache-{pid}",
        "patient_id": pid,
        "patient_name": name,
        "mode": "mock",
        "guideline_mode": "fixture",
        "degraded_reasons": [],
        "summary": summary,
        "worklist": worklist,
        "dropped": dropped,
        "timeline": timeline,
        "receipt": make_receipt(timeline, usage),
        "citations": citations_of(worklist),
        "rules_only": {
            "approved": [],
            "review": [],
            "rejected": [],
            "revenue_summary": None,
            "omitted": "Scripted cache stores counts on summary, not the raw rules rows.",
        },
    }
    return {
        "recorded_at": RECORDED_AT,
        "chart": {"note": note, "coded": coded},
        "audit": audit,
    }


# ── notes ──────────────────────────────────────────────────────────────

MARGARET_NOTE = """PROGRESS NOTE — Margaret Whitmore
Date: 2026-03-12 | Provider: Dr. Helen Cho, MD
Synthetic chart for the CodeSentinel demo.

CHIEF COMPLAINT:
Diabetes follow-up. Shortness of breath on one flight of stairs.

HISTORY OF PRESENT ILLNESS:
68-year-old woman with type 2 diabetes and hypertension. She reports increased thirst and blurred vision. She denies chest pain. Her mother had CHF. A PHQ-9 was administered for annual screening. Score 3.

PROBLEM LIST (copied forward from 2024, not reassessed today):
Obesity. GERD. Coronary artery disease.

PAST HISTORY:
UTI in 2019, resolved. No irregular rhythm on last ECG.

REVIEW OF SYSTEMS:
Occasional back pain. Negative for chest pain and cough.

MEDICATIONS:
Metformin 1000 mg twice daily. Lisinopril 20 mg daily. Furosemide 40 mg daily. Carvedilol 12.5 mg twice daily.

PHYSICAL EXAM:
BP 148/86, HR 78, weight 196 lb, BMI 31. Mild bibasilar crackles. Bilateral ankle edema. Monofilament sensation reduced on both feet.

LABS:
HbA1c 8.9%. eGFR 42 mL/min. BNP 620 pg/mL. LDL 142 mg/dL.

ASSESSMENT AND PLAN:
1. Type 2 diabetes mellitus — uncontrolled with hyperglycemia. HbA1c 8.9%. Continue metformin. Will add empagliflozin. Peripheral neuropathy on exam.
2. Chronic kidney disease, stage 3 — eGFR 42, down from 51. Consistent with diabetic nephropathy. Continue lisinopril. Avoid NSAIDs.
3. Heart failure — dyspnea, edema, BNP 620, on furosemide and carvedilol. Echo last month EF 35%. Continue current regimen and send for repeat echo.
4. Hypertension — not at goal. Continue lisinopril.
5. Evaluate for fatty liver if enzymes rise.

FOLLOW UP: 4 weeks.
"""

MARIA_NOTE = """PROGRESS NOTE — Maria Rodriguez — 2026-01-15

HISTORY OF PRESENT ILLNESS:
68-year-old Hispanic female with Type 2 diabetes presents for follow-up.
Reports blurred vision and increased thirst. Compliant with Metformin.
Bilateral foot numbness progressing over past 3 months.

ASSESSMENT AND PLAN:
1. Type 2 diabetes mellitus — UNCONTROLLED with hyperglycemia. HbA1c risen from 7.8% to 8.4%.
   Developing peripheral neuropathy. Add Empagliflozin 10mg for glycemic and renal protection.
2. Chronic kidney disease, Stage 3 — eGFR 48, declining. Likely diabetic nephropathy.
   Continue ACE inhibitor. Refer nephrology if continues declining.
3. Hypercholesterolemia — LDL 198 despite Atorvastatin 40mg. Increase to 80mg.
4. Anemia — Hemoglobin 11.2. Likely anemia of chronic kidney disease. Check iron studies.
5. Hypertension — BP 142/88. Continue Lisinopril. Consider adding Amlodipine.
"""

ROBERT_NOTE = """PROGRESS NOTE — Robert Williams — 2026-01-20

ASSESSMENT AND PLAN:
1. Type 2 diabetes mellitus — POORLY CONTROLLED. HbA1c 9.1%. On insulin glargine + metformin.
   Increasing insulin dose. Peripheral neuropathy bilateral feet. Diabetic nephropathy progressing.
2. Chronic kidney disease Stage 3b — eGFR 38. Worsening. Likely diabetic nephropathy.
   Continue Losartan for renal protection. Avoid nephrotoxic medications.
3. Heart failure with reduced ejection fraction — EF 30%. BNP 720.
   Significant fluid overload. Increase Furosemide to 80mg BID. Continue Carvedilol.
4. Anemia of chronic disease — Hgb 10.5. In setting of CKD + CHF. Check EPO level.
5. Hypercholesterolemia — Total cholesterol 265. On max dose Atorvastatin. Add Ezetimibe.
"""

PATRICIA_NOTE = """PROGRESS NOTE — Patricia Davis — 2026-01-25

HISTORY OF PRESENT ILLNESS:
56-year-old female presents for annual physical.
Family history of diabetes in mother and sister.

ASSESSMENT AND PLAN:
1. No evidence of diabetes — HbA1c 5.2%, normal. Family history noted.
   Patient denies any symptoms of diabetes. Continue monitoring annually.
2. No history of kidney disease — eGFR 92, normal.
3. Denies depression or anxiety. PHQ-9 score 2 (minimal).
4. Rule out hypothyroidism — patient reports fatigue. Check TSH.
5. No signs of heart failure or COPD.
"""

JENNIFER_NOTE = """PROGRESS NOTE — Jennifer Wilson — 2026-02-08

ASSESSMENT AND PLAN:
1. Screening for depression — PHQ-9 administered as part of annual wellness visit.
   Score 4 (minimal). No treatment indicated at this time.
2. Screening for colon cancer — due for colonoscopy. Referral placed.
3. Possible anxiety — patient reports occasional worry about work stress.
   Not meeting criteria for GAD at this time. Reassess at next visit.
4. Hyperlipidemia — stable on current regimen.
"""

SPECIFICITY_CITE = (
    "Pointer only: when the record documents hyperglycemia, diabetes is not left unspecified. "
    "This sentence was written for the demo cache."
)
COMBINATION_CITE = (
    "Pointer only: diabetes with chronic kidney disease is reported with a combination code. "
    "This sentence was written for the demo cache."
)


def _lab(name, loinc, value, unit, when, oid):
    return {
        "name": name,
        "loinc": loinc,
        "value": value,
        "unit": unit,
        "date": when,
        "observation_id": oid,
    }


def _margaret() -> dict:
    note = MARGARET_NOTE
    pid = "margaret-whitmore"
    copied = "Obesity. GERD. Coronary artery disease."
    spec = cite("E11.65", "specificity-hyperglycemia", SPECIFICITY_CITE)
    combo = cite("E11.22", "combination-code-diabetes-ckd", COMBINATION_CITE)
    items = [
        work(
            note, pid=pid, code="E11.65",
            display="Type 2 diabetes mellitus with hyperglycemia",
            gap_type="specificity_upgrade", decision="accept", confidence=0.93,
            quotes=[
                "Type 2 diabetes mellitus — uncontrolled with hyperglycemia.",
                "HbA1c 8.9%.",
            ],
            section="Assessment and Plan",
            rationale=(
                "The plan documents uncontrolled hyperglycemia and an HbA1c of 8.9%. "
                "That upgrades unspecified diabetes. Reconcile it with the combination code before signing both."
            ),
            model=SUPER, tier="SPECIFICITY_UPGRADE",
            upgrade_from="E11.9",
            meat={
                "monitor": "HbA1c 8.9%.",
                "assess": "Type 2 diabetes mellitus — uncontrolled with hyperglycemia.",
                "treat": "Will add empagliflozin.",
            },
            lab=_lab("HbA1c", "4548-4", 8.9, "%", "2026-03-12", "mw-a1c"),
            citations=[spec],
        ),
        work(
            note, pid=pid, code="N18.32",
            display="Chronic kidney disease, stage 3b",
            gap_type="missing", decision="accept", confidence=0.9,
            quotes=["Chronic kidney disease, stage 3 — eGFR 42, down from 51."],
            section="Assessment and Plan",
            rationale="Stage 3 chronic kidney disease is assessed, the eGFR is 42, and lisinopril is continued. It is not on the claim.",
            model=LIGHTNING, tier="TIER_1_STRUCTURED",
            meat={
                "monitor": "eGFR 42, down from 51.",
                "assess": "Chronic kidney disease, stage 3 — eGFR 42, down from 51.",
                "treat": "Continue lisinopril.",
            },
            lab=_lab("eGFR", "33914-3", 42, "mL/min/1.73m2", "2026-03-12", "mw-egfr"),
        ),
        work(
            note, pid=pid, code="I50.22",
            display="Chronic systolic heart failure",
            gap_type="missing", decision="review", confidence=0.74,
            quotes=[
                "Heart failure — dyspnea, edema, BNP 620, on furosemide and carvedilol.",
                "Echo last month EF 35%.",
            ],
            section="Assessment and Plan",
            rationale="Heart failure is assessed and treated, with BNP 620 and an ejection fraction of 35%. Acuity is not explicit, so a coder should confirm it.",
            model=ULTRA, tier="TIER_2_PHENOTYPE",
            meat={
                "monitor": "BNP 620, on furosemide and carvedilol.",
                "assess": "Heart failure — dyspnea, edema, BNP 620, on furosemide and carvedilol.",
                "treat": "Continue current regimen and send for repeat echo.",
            },
            lab=_lab("BNP", "42637-9", 620, "pg/mL", "2026-03-12", "mw-bnp"),
            query=(
                "Please clarify whether the heart failure findings in this encounter are active "
                "and being monitored, evaluated, assessed, or treated: dyspnea, edema, BNP 620 pg/mL, "
                "furosemide, carvedilol, and a prior ejection fraction of 35%."
            ),
        ),
        work(
            note, pid=pid, code="E11.22",
            display="Type 2 diabetes mellitus with diabetic chronic kidney disease",
            gap_type="combination", decision="review", confidence=0.71,
            quotes=[
                "Consistent with diabetic nephropathy.",
                "Chronic kidney disease, stage 3 — eGFR 42, down from 51.",
            ],
            section="Assessment and Plan",
            rationale=(
                "Diabetes and stage 3 kidney disease are both in the plan, including diabetic nephropathy. "
                "A combination code may replace the separate codes. It was not auto-accepted."
            ),
            model=ULTRA, tier="COMBINATION_CODE",
            upgrade_from="E11.9",
            meat={
                "assess": "Consistent with diabetic nephropathy.",
                "monitor": "eGFR 42, down from 51.",
                "treat": "Continue lisinopril.",
            },
            citations=[combo],
        ),
    ]
    dropped = [
        drop(note, pid=pid, code="I20.9", display="Angina pectoris, unspecified",
             reason="negated", detail="She denies chest pain, so angina is not a gap.",
             quotes=["She denies chest pain."], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="I50.9", display="Heart failure, unspecified",
             reason="family_history",
             detail="Her mother had CHF. That history is not her diagnosis. Her own heart-failure assessment is a separate row.",
             quotes=["Her mother had CHF."], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="F32.9", display="Major depressive disorder, single episode, unspecified",
             reason="screening_or_rule_out",
             detail="A PHQ-9 was given for annual screening. Score 3 is not a diagnosis.",
             quotes=["A PHQ-9 was administered for annual screening."], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="Z13.31", display="Encounter for screening for depression",
             reason="screening_or_rule_out",
             detail="Screening encounters are not missed diagnosis codes.",
             quotes=["A PHQ-9 was administered for annual screening."], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="E66.9", display="Obesity, unspecified",
             reason="copied_forward_unclear",
             detail="Obesity sits on a copied problem list and is not assessed or treated today.",
             quotes=[copied], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="K21.9", display="Gastro-esophageal reflux disease without esophagitis",
             reason="copied_forward_unclear",
             detail="GERD sits on a copied problem list and is not assessed or treated today.",
             quotes=[copied], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="I25.10", display="Atherosclerotic heart disease without angina",
             reason="copied_forward_unclear",
             detail="Coronary artery disease was copied forward. This encounter does not monitor, evaluate, assess, or treat it.",
             quotes=[copied], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="N39.0", display="Urinary tract infection, site not specified",
             reason="historical_resolved", detail="The urinary tract infection is documented as resolved in 2019.",
             quotes=["UTI in 2019, resolved."], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="I48.91", display="Unspecified atrial fibrillation",
             reason="negated", detail="The last ECG had no irregular rhythm.",
             quotes=["No irregular rhythm on last ECG."], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="M54.50", display="Low back pain, unspecified",
             reason="rejected", detail="Back pain is only in the review of systems, with no assessment or treatment.",
             quotes=["Occasional back pain."], by="adjudicator", model=SUPER),
        drop(note, pid=pid, code="E78.00", display="Pure hypercholesterolemia, unspecified",
             reason="rejected", detail="LDL is resulted and never assessed in the plan, so it is not a code to add.",
             quotes=["LDL 142 mg/dL."], by="adjudicator", model=SUPER),
        drop(note, pid=pid, code="J44.9", display="Chronic obstructive pulmonary disease, unspecified",
             reason="unsupported", detail="No verbatim quote for COPD survived the verifier.",
             quotes=[], by="verifier", model=None),
        drop(note, pid=pid, code="E55.9", display="Vitamin D deficiency, unspecified",
             reason="unsupported", detail="No verbatim quote for vitamin D deficiency survived the verifier.",
             quotes=[], by="verifier", model=None),
        drop(note, pid=pid, code="G47.33", display="Obstructive sleep apnea",
             reason="unsupported", detail="No verbatim quote for sleep apnea survived the verifier.",
             quotes=[], by="verifier", model=None),
        drop(note, pid=pid, code="J45.909", display="Unspecified asthma, uncomplicated",
             reason="unsupported", detail="No verbatim quote for asthma survived the verifier.",
             quotes=[], by="verifier", model=None),
        drop(note, pid=pid, code="K76.0", display="Fatty liver, not elsewhere classified",
             reason="hypothetical_plan", detail="“Evaluate for fatty liver” is a plan, not a diagnosis.",
             quotes=["Evaluate for fatty liver if enzymes rise."], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="I10", display="Essential hypertension",
             reason="rejected", detail="Hypertension is already on the claim. Continuing lisinopril does not open a new gap.",
             quotes=["Hypertension — not at goal."], by="adjudicator", model=SUPER),
    ]
    return finish(
        pid=pid, name="Margaret Whitmore", note=note,
        coded=[
            {"code": "E11.9", "display": "Type 2 diabetes mellitus without complications"},
            {"code": "I10", "display": "Essential hypertension"},
        ],
        worklist=items, dropped=dropped,
        timeline_specs={
            "rules": {"status": "ok", "latency_ms": 46, "detail": "21 candidates from the note, labs, and medications."},
            "triage": {"status": "ok", "latency_ms": 1680, "detail": "Lightning classified all 21. High-confidence CKD stayed on the fast path."},
            "verifier": {"status": "ok", "latency_ms": 28, "detail": "Dropped 4 candidates whose quotes were not in the chart."},
            "guidelines": {"status": "ok", "latency_ms": 16, "detail": "Fixture pointers for hyperglycemia specificity and the diabetes-CKD combination. No live Tavily call."},
            "adjudication": {"status": "ok", "latency_ms": 2140, "detail": "Super accepted the hyperglycemia upgrade and sent the hard cases up."},
            "escalation": {"status": "ok", "latency_ms": 2480, "detail": "Ultra reviewed the combination code and the heart-failure acuity."},
            "physician_query": {"status": "ok", "latency_ms": 860, "detail": "Ultra drafted one non-leading query on heart failure. It does not name a code."},
        },
        usage={
            "triage": {"calls": 21, "input_tokens": 31500, "output_tokens": 5880, "model": LIGHTNING},
            "adjudication": {"calls": 3, "input_tokens": 8400, "output_tokens": 2100, "model": SUPER},
            "escalation": {"calls": 2, "input_tokens": 7200, "output_tokens": 1800, "model": ULTRA},
            "physician_query": {"calls": 1, "input_tokens": 2200, "output_tokens": 600, "model": ULTRA},
        },
    )


def _maria() -> dict:
    note = MARIA_NOTE
    pid = "pt-001"
    spec = cite("E11.65", "specificity-hyperglycemia", SPECIFICITY_CITE)
    items = [
        work(
            note, pid=pid, code="E11.65",
            display="Type 2 diabetes mellitus with hyperglycemia",
            gap_type="specificity_upgrade", decision="accept", confidence=0.91,
            quotes=[
                "Type 2 diabetes mellitus — UNCONTROLLED with hyperglycemia.",
                "HbA1c risen from 7.8% to 8.4%.",
            ],
            section="Assessment and Plan",
            rationale="The claim says unspecified diabetes. The plan says uncontrolled hyperglycemia and the HbA1c rose to 8.4%.",
            model=SUPER, tier="SPECIFICITY_UPGRADE", upgrade_from="E11.9",
            meat={
                "monitor": "HbA1c risen from 7.8% to 8.4%.",
                "assess": "Type 2 diabetes mellitus — UNCONTROLLED with hyperglycemia.",
                "treat": "Add Empagliflozin 10mg for glycemic and renal protection.",
            },
            lab=_lab("HbA1c", "4548-4", 8.4, "%", "2026-01-15", "o1-1"),
            citations=[spec],
        ),
        work(
            note, pid=pid, code="N18.31",
            display="Chronic kidney disease, stage 3a",
            gap_type="missing", decision="accept", confidence=0.88,
            quotes=["Chronic kidney disease, Stage 3 — eGFR 48, declining."],
            section="Assessment and Plan",
            rationale="Stage 3 chronic kidney disease is assessed, the eGFR is 48 and falling, and the ACE inhibitor is continued. It is not coded.",
            model=LIGHTNING, tier="TIER_1_STRUCTURED",
            meat={
                "monitor": "eGFR 48, declining.",
                "assess": "Chronic kidney disease, Stage 3 — eGFR 48, declining.",
                "treat": "Continue ACE inhibitor.",
            },
            lab=_lab("eGFR", "33914-3", 48, "mL/min/1.73m2", "2026-01-15", "o1-2"),
        ),
        work(
            note, pid=pid, code="E78.00",
            display="Pure hypercholesterolemia, unspecified",
            gap_type="missing", decision="accept", confidence=0.86,
            quotes=["Hypercholesterolemia — LDL 198 despite Atorvastatin 40mg."],
            section="Assessment and Plan",
            rationale="Hypercholesterolemia is assessed and the statin dose is increased. LDL is 198. It is not on the claim.",
            model=LIGHTNING, tier="TIER_1_STRUCTURED",
            meat={
                "monitor": "LDL 198 despite Atorvastatin 40mg.",
                "assess": "Hypercholesterolemia — LDL 198 despite Atorvastatin 40mg.",
                "treat": "Increase to 80mg.",
            },
            lab=_lab("LDL", "2089-1", 198, "mg/dL", "2026-01-15", "o1-3"),
        ),
        work(
            note, pid=pid, code="D63.1",
            display="Anemia in chronic kidney disease",
            gap_type="missing", decision="review", confidence=0.62,
            quotes=["Anemia — Hemoglobin 11.2. Likely anemia of chronic kidney disease."],
            section="Assessment and Plan",
            rationale="Anemia is called likely, and iron studies are still ordered. That is a review, not an accept.",
            model=SUPER, tier="TIER_1_STRUCTURED",
            meat={
                "monitor": "Hemoglobin 11.2.",
                "assess": "Anemia — Hemoglobin 11.2. Likely anemia of chronic kidney disease.",
                "treat": "Check iron studies.",
            },
            lab=_lab("Hemoglobin", "718-7", 11.2, "g/dL", "2026-01-15", "o1-4"),
        ),
    ]
    dropped = [
        drop(note, pid=pid, code="H53.9", display="Unspecified visual disturbance",
             reason="rejected", detail="Blurred vision is a symptom in the history. The note never assesses retinopathy.",
             quotes=["Reports blurred vision and increased thirst."], by="adjudicator", model=SUPER),
        drop(note, pid=pid, code="E11.40", display="Type 2 diabetes mellitus with diabetic neuropathy, unspecified",
             reason="rejected",
             detail="Neuropathy is mentioned inside the diabetes plan. It was not signed as its own code in this cached run.",
             quotes=["Developing peripheral neuropathy."], by="adjudicator", model=SUPER),
        drop(note, pid=pid, code="J44.9", display="Chronic obstructive pulmonary disease, unspecified",
             reason="unsupported", detail="No verbatim quote for COPD survived the verifier.",
             quotes=[], by="verifier", model=None),
        drop(note, pid=pid, code="I50.9", display="Heart failure, unspecified",
             reason="unsupported", detail="No verbatim quote for heart failure survived the verifier.",
             quotes=[], by="verifier", model=None),
        drop(note, pid=pid, code="E66.9", display="Obesity, unspecified",
             reason="unsupported", detail="No verbatim quote for obesity survived the verifier.",
             quotes=[], by="verifier", model=None),
    ]
    return finish(
        pid=pid, name="Maria Elena Rodriguez", note=note,
        coded=[
            {"code": "E11.9", "display": "Type 2 diabetes mellitus without complications"},
            {"code": "I10", "display": "Essential hypertension"},
        ],
        worklist=items, dropped=dropped,
        timeline_specs={
            "rules": {"status": "ok", "latency_ms": 38, "detail": "9 candidates from labs and the assessment."},
            "triage": {"status": "ok", "latency_ms": 940, "detail": "Lightning kept the lab-backed CKD and cholesterol codes."},
            "verifier": {"status": "ok", "latency_ms": 18, "detail": "Dropped 3 candidates with no quote in the note."},
            "guidelines": {"status": "ok", "latency_ms": 12, "detail": "Fixture pointer for the hyperglycemia upgrade. No live Tavily call."},
            "adjudication": {"status": "ok", "latency_ms": 1510, "detail": "Super accepted the specificity upgrade and left anemia as a review."},
            "escalation": {"status": "skipped", "latency_ms": 0, "detail": "No conflict and no high-value upgrade for Ultra."},
            "physician_query": {"status": "skipped", "latency_ms": 0, "detail": "No physician query was needed."},
        },
        usage={
            "triage": {"calls": 9, "input_tokens": 12600, "output_tokens": 2400, "model": LIGHTNING},
            "adjudication": {"calls": 2, "input_tokens": 5400, "output_tokens": 1400, "model": SUPER},
        },
    )


def _robert() -> dict:
    note = ROBERT_NOTE
    pid = "pt-004"
    combo = cite("E11.22", "combination-code-diabetes-ckd", COMBINATION_CITE)
    items = [
        work(
            note, pid=pid, code="E11.22",
            display="Type 2 diabetes mellitus with diabetic chronic kidney disease",
            gap_type="combination", decision="accept", confidence=0.9,
            quotes=[
                "Diabetic nephropathy progressing.",
                "Chronic kidney disease Stage 3b — eGFR 38.",
                "Type 2 diabetes mellitus — POORLY CONTROLLED. HbA1c 9.1%.",
            ],
            section="Assessment and Plan",
            rationale=(
                "The plan links progressing diabetic nephropathy with stage 3b chronic kidney disease. "
                "The combination code is the one to sign. A separate hyperglycemia code was dropped so the two are not both submitted."
            ),
            model=ULTRA, tier="COMBINATION_CODE", upgrade_from="E11.9",
            meat={
                "monitor": "HbA1c 9.1%.",
                "assess": "Diabetic nephropathy progressing.",
                "treat": None,
            },
            lab=_lab("HbA1c", "4548-4", 9.1, "%", "2026-01-20", "o4-1"),
            citations=[combo],
        ),
        work(
            note, pid=pid, code="I50.22",
            display="Chronic systolic heart failure",
            gap_type="missing", decision="review", confidence=0.73,
            quotes=[
                "Heart failure with reduced ejection fraction — EF 30%. BNP 720.",
                "Increase Furosemide to 80mg BID.",
            ],
            section="Assessment and Plan",
            rationale="Systolic heart failure is assessed and the diuretic is increased. The note does not say acute or chronic, so confirm before coding.",
            model=ULTRA, tier="TIER_2_PHENOTYPE",
            meat={
                "monitor": "BNP 720.",
                "assess": "Heart failure with reduced ejection fraction — EF 30%. BNP 720.",
                "treat": "Increase Furosemide to 80mg BID.",
            },
            lab=_lab("BNP", "42637-9", 720, "pg/mL", "2026-01-20", "o4-3"),
            query=(
                "Please clarify whether the heart failure treated in this note is active this encounter: "
                "ejection fraction 30%, BNP 720, and the furosemide increase. Include whether it is being "
                "monitored, evaluated, assessed, or treated."
            ),
        ),
        work(
            note, pid=pid, code="E78.00",
            display="Pure hypercholesterolemia, unspecified",
            gap_type="missing", decision="accept", confidence=0.84,
            quotes=["Hypercholesterolemia — Total cholesterol 265."],
            section="Assessment and Plan",
            rationale="Hypercholesterolemia is assessed on maximum statin therapy, and ezetimibe is added.",
            model=SUPER, tier="TIER_3_NOTE_NER",
            meat={
                "assess": "Hypercholesterolemia — Total cholesterol 265.",
                "treat": "Add Ezetimibe.",
            },
        ),
        work(
            note, pid=pid, code="D63.8",
            display="Anemia in other chronic diseases",
            gap_type="missing", decision="review", confidence=0.58,
            quotes=["Anemia of chronic disease — Hgb 10.5."],
            section="Assessment and Plan",
            rationale="Anemia of chronic disease is assessed and an EPO level is ordered. The cause is split between kidney disease and heart failure, so a person should pick the code.",
            model=SUPER, tier="TIER_3_NOTE_NER",
            meat={
                "assess": "Anemia of chronic disease — Hgb 10.5.",
                "treat": "Check EPO level.",
            },
            lab=_lab("Hemoglobin", "718-7", 10.5, "g/dL", "2026-01-20", "o4-4"),
        ),
    ]
    dropped = [
        drop(
            note, pid=pid, code="E11.65",
            display="Type 2 diabetes mellitus with hyperglycemia",
            gap_type="specificity_upgrade", tier="SPECIFICITY_UPGRADE",
            reason="rejected",
            detail="Hyperglycemia is documented. This cached decision keeps the combination code and does not also add a second diabetes code.",
            quotes=["Type 2 diabetes mellitus — POORLY CONTROLLED. HbA1c 9.1%."],
            by="escalation", model=ULTRA,
        ),
        drop(
            note, pid=pid, code="N18.32",
            display="Chronic kidney disease, stage 3b",
            reason="rejected",
            detail="Stage 3b chronic kidney disease is already on the claim as N18.3, and the note supports it. The gap is the combination code.",
            quotes=["Chronic kidney disease Stage 3b — eGFR 38."],
            by="adjudicator", model=SUPER,
        ),
        drop(
            note, pid=pid, code="I10", display="Essential hypertension",
            reason="rejected",
            detail="Hypertension is already on the claim. This note does not open a new hypertension gap.",
            quotes=[], by="adjudicator", model=SUPER,
        ),
        drop(
            note, pid=pid, code="J44.9",
            display="Chronic obstructive pulmonary disease, unspecified",
            reason="unsupported", detail="No verbatim quote for COPD survived the verifier.",
            quotes=[], by="verifier", model=None,
        ),
    ]
    return finish(
        pid=pid, name="Robert Williams", note=note,
        coded=[
            {"code": "E11.9", "display": "Type 2 diabetes mellitus without complications"},
            {"code": "I10", "display": "Essential hypertension"},
            {"code": "N18.3", "display": "Chronic kidney disease, stage 3"},
        ],
        worklist=items, dropped=dropped,
        timeline_specs={
            "rules": {"status": "ok", "latency_ms": 41, "detail": "8 candidates, including a separate hyperglycemia upgrade and a combination code."},
            "triage": {"status": "ok", "latency_ms": 880, "detail": "Lightning passed the diabetes pair and the heart-failure phenotype upward."},
            "verifier": {"status": "ok", "latency_ms": 15, "detail": "Dropped COPD. Every kept quote is in the note."},
            "guidelines": {"status": "ok", "latency_ms": 14, "detail": "Fixture pointer for the diabetes-with-CKD combination. Without it, the accept would have been forced back to review."},
            "adjudication": {"status": "ok", "latency_ms": 1760, "detail": "Super disagreed with a naive pair of diabetes codes and escalated."},
            "escalation": {"status": "ok", "latency_ms": 2320, "detail": "Ultra accepted E11.22 and dropped the separate hyperglycemia code."},
            "physician_query": {"status": "ok", "latency_ms": 740, "detail": "Ultra drafted a non-leading query on heart failure. It does not name a code."},
        },
        usage={
            "triage": {"calls": 8, "input_tokens": 11200, "output_tokens": 2100, "model": LIGHTNING},
            "adjudication": {"calls": 3, "input_tokens": 9000, "output_tokens": 2400, "model": SUPER},
            "escalation": {"calls": 2, "input_tokens": 6800, "output_tokens": 1600, "model": ULTRA},
            "physician_query": {"calls": 1, "input_tokens": 1800, "output_tokens": 480, "model": ULTRA},
        },
    )


def _patricia() -> dict:
    note = PATRICIA_NOTE
    pid = "pt-005"
    items = [
        work(
            note, pid=pid, code="E11.9",
            display="Type 2 diabetes mellitus without complications",
            gap_type="unsupported_existing", decision="review", confidence=0.86,
            quotes=["No evidence of diabetes — HbA1c 5.2%, normal."],
            section="Assessment and Plan",
            rationale="E11.9 is already on the claim. This note says there is no evidence of diabetes and the HbA1c is 5.2%. Review the code before it stays.",
            model=ULTRA, tier="TIER_3_NOTE_NER",
            meat={
                "monitor": "HbA1c 5.2%, normal.",
                "assess": "No evidence of diabetes — HbA1c 5.2%, normal.",
            },
            lab=_lab("HbA1c", "4548-4", 5.2, "%", "2026-01-25", "o5-1"),
            query=(
                "Please clarify whether diabetes is currently present. The note states there is no evidence "
                "of diabetes, the HbA1c is 5.2%, and it records a family history rather than a personal diagnosis."
            ),
        ),
        work(
            note, pid=pid, code="E03.9",
            display="Hypothyroidism, unspecified",
            gap_type="missing", decision="abstain", confidence=0.33,
            quotes=["Rule out hypothyroidism — patient reports fatigue. Check TSH."],
            section="Assessment and Plan",
            rationale="Fatigue plus an order to check TSH is a rule-out. Evidence is too thin to code, so the decision is abstain and a person reviews it.",
            model=SUPER, tier="TIER_3_NOTE_NER",
            assertion="hypothetical_plan",
            meat={
                "assess": "Rule out hypothyroidism — patient reports fatigue.",
                "evaluate": "Check TSH.",
            },
        ),
    ]
    dropped = [
        drop(note, pid=pid, code="Z83.3", display="Family history of diabetes mellitus",
             reason="family_history",
             detail="Her mother and sister had diabetes. That is not her diagnosis.",
             quotes=["Family history of diabetes in mother and sister."], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="I50.9", display="Heart failure, unspecified",
             reason="negated", detail="The note says there are no signs of heart failure.",
             quotes=["No signs of heart failure or COPD."], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="J44.9", display="Chronic obstructive pulmonary disease, unspecified",
             reason="negated", detail="The note says there are no signs of COPD.",
             quotes=["No signs of heart failure or COPD."], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="N18.9", display="Chronic kidney disease, unspecified",
             reason="negated", detail="The note says there is no history of kidney disease, and the eGFR is 92.",
             quotes=["No history of kidney disease — eGFR 92, normal."], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="F32.9", display="Major depressive disorder, single episode, unspecified",
             reason="negated", detail="She denies depression.",
             quotes=["Denies depression or anxiety."], by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="Z13.31", display="Encounter for screening for depression",
             reason="screening_or_rule_out",
             detail="PHQ-9 score 2 at a physical is screening, not a diagnosis to add.",
             quotes=["PHQ-9 score 2 (minimal)."], by="triage", model=LIGHTNING),
    ]
    return finish(
        pid=pid, name="Patricia Davis", note=note,
        coded=[
            {"code": "E11.9", "display": "Type 2 diabetes mellitus without complications"},
            {"code": "I10", "display": "Essential hypertension"},
            {"code": "E78.5", "display": "Hyperlipidemia, unspecified"},
        ],
        worklist=items, dropped=dropped,
        timeline_specs={
            "rules": {"status": "ok", "latency_ms": 33, "detail": "8 candidates. Several are negations the rules still surfaced."},
            "triage": {"status": "ok", "latency_ms": 760, "detail": "Lightning dropped negation, family history, and the screening score."},
            "verifier": {"status": "ok", "latency_ms": 12, "detail": "The remaining quotes are in the note."},
            "guidelines": {"status": "skipped", "latency_ms": 0, "detail": "No combination or specificity accept to ground."},
            "adjudication": {"status": "ok", "latency_ms": 1280, "detail": "Super abstained on the hypothyroidism rule-out."},
            "escalation": {"status": "ok", "latency_ms": 1640, "detail": "Ultra flagged the diabetes code already on the claim. The note does not support it."},
            "physician_query": {"status": "ok", "latency_ms": 690, "detail": "Ultra drafted a non-leading query asking whether diabetes is present. It does not name a code."},
        },
        usage={
            "triage": {"calls": 8, "input_tokens": 9600, "output_tokens": 1900, "model": LIGHTNING},
            "adjudication": {"calls": 2, "input_tokens": 4800, "output_tokens": 1100, "model": SUPER},
            "escalation": {"calls": 1, "input_tokens": 3200, "output_tokens": 800, "model": ULTRA},
            "physician_query": {"calls": 1, "input_tokens": 1600, "output_tokens": 420, "model": ULTRA},
        },
    )


def _jennifer() -> dict:
    note = JENNIFER_NOTE
    pid = "pt-009"
    dropped = [
        drop(note, pid=pid, code="F32.9", display="Major depressive disorder, single episode, unspecified",
             reason="screening_or_rule_out",
             detail="Depression screening at a wellness visit, with no treatment, is not a diagnosis.",
             quotes=["Screening for depression — PHQ-9 administered as part of annual wellness visit."],
             by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="Z12.11", display="Encounter for screening for malignant neoplasm of colon",
             reason="screening_or_rule_out",
             detail="A colonoscopy referral is screening, not a missed diagnosis.",
             quotes=["Screening for colon cancer — due for colonoscopy."],
             by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="F41.9", display="Anxiety disorder, unspecified",
             reason="hypothetical_plan",
             detail="Possible anxiety that does not meet criteria is not a code to add.",
             quotes=[
                 "Possible anxiety — patient reports occasional worry about work stress.",
                 "Not meeting criteria for GAD at this time.",
             ],
             by="triage", model=LIGHTNING),
        drop(note, pid=pid, code="E78.5", display="Hyperlipidemia, unspecified",
             reason="rejected",
             detail="Hyperlipidemia is already on the claim and the plan calls it stable. Not a gap.",
             quotes=["Hyperlipidemia — stable on current regimen."],
             by="adjudicator", model=SUPER),
    ]
    return finish(
        pid=pid, name="Jennifer Wilson", note=note,
        coded=[{"code": "E78.5", "display": "Hyperlipidemia, unspecified"}],
        worklist=[], dropped=dropped,
        timeline_specs={
            "rules": {"status": "ok", "latency_ms": 29, "detail": "4 candidates. All of them are screening, hypothetical, or already coded."},
            "triage": {"status": "ok", "latency_ms": 640, "detail": "Lightning dropped the screening and the possible anxiety."},
            "verifier": {"status": "ok", "latency_ms": 9, "detail": "Quotes offered for the drops are in the note."},
            "guidelines": {"status": "skipped", "latency_ms": 0, "detail": "Nothing left that needs a guideline."},
            "adjudication": {"status": "ok", "latency_ms": 420, "detail": "Super rejected the one survivor. Hyperlipidemia is already coded and stable."},
            "escalation": {"status": "skipped", "latency_ms": 0, "detail": "Nothing for Ultra to adjudicate."},
            "physician_query": {"status": "skipped", "latency_ms": 0, "detail": "No query. There is nothing to ask a physician to code."},
        },
        usage={
            "triage": {"calls": 4, "input_tokens": 4800, "output_tokens": 900, "model": LIGHTNING},
            "adjudication": {"calls": 1, "input_tokens": 1800, "output_tokens": 360, "model": SUPER},
        },
    )


def _build() -> dict[str, dict]:
    bundles = [_margaret(), _maria(), _robert(), _patricia(), _jennifer()]
    return {bundle["audit"]["patient_id"]: bundle for bundle in bundles}


CACHE: dict[str, dict] = _build()

CARDS = [
    {
        "id": "margaret-whitmore",
        "name": "Margaret Whitmore",
        "hook": "Specificity, negation, and a family-history trap in one chart.",
        "featured": True,
        "setting": "Outpatient follow-up · synthetic",
    },
    {
        "id": "pt-001",
        "name": "Maria Elena Rodriguez",
        "hook": "Coded E11.9. The plan says uncontrolled hyperglycemia, and CKD is uncoded.",
        "featured": False,
        "setting": "Outpatient follow-up · synthetic",
    },
    {
        "id": "pt-004",
        "name": "Robert Williams",
        "hook": "Diabetes with CKD. The combination code is the decision.",
        "featured": False,
        "setting": "Outpatient follow-up · synthetic",
    },
    {
        "id": "pt-005",
        "name": "Patricia Davis",
        "hook": "Negation trap. The claim says diabetes. The note says there is no evidence of it.",
        "featured": False,
        "setting": "Annual physical · synthetic",
    },
    {
        "id": "pt-009",
        "name": "Jennifer Wilson",
        "hook": "Screening trap. A PHQ-9 at a wellness visit is not depression.",
        "featured": False,
        "setting": "Annual wellness visit · synthetic",
    },
]


def margaret_profile() -> dict:
    return {
        "fhir_patient_id": "margaret-whitmore",
        "demographics": {"full_name": "Margaret Whitmore", "found": True},
        "clinical_notes": [{"text": MARGARET_NOTE}],
        "observations": [
            _lab("HbA1c", "4548-4", 8.9, "%", "2026-03-12", "mw-a1c"),
            _lab("eGFR", "33914-3", 42, "mL/min/1.73m2", "2026-03-12", "mw-egfr"),
            _lab("BNP", "42637-9", 620, "pg/mL", "2026-03-12", "mw-bnp"),
            _lab("LDL", "2089-1", 142, "mg/dL", "2026-03-12", "mw-ldl"),
        ],
        "medications": [
            {"medication_id": "mw-m1", "name": "Metformin 1000 mg", "status": "active"},
            {"medication_id": "mw-m2", "name": "Lisinopril 20 mg", "status": "active"},
            {"medication_id": "mw-m3", "name": "Furosemide 40 mg", "status": "active"},
            {"medication_id": "mw-m4", "name": "Carvedilol 12.5 mg", "status": "active"},
        ],
        "conditions": [
            {"condition_id": "mw-c1", "icd10_code": "E11.9", "display": "Type 2 diabetes mellitus without complications", "clinical_status": "active"},
            {"condition_id": "mw-c2", "icd10_code": "I10", "display": "Essential hypertension", "clinical_status": "active"},
        ],
        "encounters": [
            {"encounter_id": "mw-e1", "type": "Office visit", "start_date": "2026-03-12"},
        ],
    }


def get_cached(patient_id: str) -> dict | None:
    bundle = CACHE.get(patient_id)
    if bundle is None:
        return None
    return copy.deepcopy(bundle)


def validate_cache() -> None:
    step_ids = [step for step, _label, _model in STEPS]
    for pid, bundle in CACHE.items():
        note = bundle["chart"]["note"]
        audit = bundle["audit"]
        summary = audit["summary"]
        worklist = audit["worklist"]
        dropped = audit["dropped"]
        ids = [item["id"] for item in worklist + dropped]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate ids for {pid}")
        if summary["worklist_count"] != len(worklist) or summary["dropped_count"] != len(dropped):
            raise ValueError(f"Counts drifted for {pid}")
        if summary["candidates_generated"] != len(worklist) + len(dropped):
            raise ValueError(f"Candidate count drifted for {pid}")
        if summary["headline"] != headline(summary, worklist):
            raise ValueError(f"Headline drifted for {pid}")
        if [row["step"] for row in audit["timeline"]] != step_ids:
            raise ValueError(f"Timeline order drifted for {pid}")
        if audit["receipt"]["totals"]["latency_ms"] != sum(row["latency_ms"] for row in audit["timeline"]):
            raise ValueError(f"Latency drifted for {pid}")
        priced = 0.0
        for step in audit["receipt"]["steps"]:
            expected = cost_usd(step["model"], step["input_tokens"], step["output_tokens"])
            if abs(step["cost_usd"] - expected) > 1e-9:
                raise ValueError(f"Cost drifted for {pid} {step['step']}")
            priced += expected
        if abs(audit["receipt"]["totals"]["cost_usd"] - round(priced, 6)) > 1e-6:
            raise ValueError(f"Total cost drifted for {pid}")
        for item in worklist:
            if not item["verified_quotes"]:
                raise ValueError(f"Worklist row without a quote: {item['id']}")
            if len(item["rationale"].split()) > 60:
                raise ValueError(f"Rationale too long: {item['id']}")
            for snippet in item["verified_quotes"]:
                if snippet not in note:
                    raise ValueError(snippet)
            for value in item["meat"].values():
                if value and value not in note:
                    raise ValueError(value)
            query = item.get("physician_query") or ""
            if query and _ICD.search(query):
                raise ValueError(f"Query names a code: {item['id']}")
            if (
                item["decision"] == "accept"
                and item["gap_type"] in ("specificity_upgrade", "combination")
                and not item["guideline_citations"]
            ):
                raise ValueError(f"Accept without a guideline: {item['id']}")
            for item_cite in item["guideline_citations"]:
                if "not a live" not in item_cite["quote"]:
                    raise ValueError(f"Fixture quote is not labeled: {item['id']}")
        for item in dropped:
            for snippet in item["supporting_quotes"]:
                if snippet not in note:
                    raise ValueError(snippet)
        scan_blob = note
        from app.phi_guard import scan_phi

        if scan_phi(scan_blob):
            raise ValueError(f"Synthetic note tripped the PHI guard: {pid}")


validate_cache()
