"""Build the Synthea gap benchmark from Synthea FHIR R4 output.

Input: the patient bundles Synthea wrote (see eval/synthea/README.md for the exact
Synthea command). Output: one compact chart bundle per patient plus labels.jsonl.

What comes from where:
- Conditions, labs, medications, and notes are Synthea's. Nothing here writes chart text.
- SNOMED CT to ICD-10-CM comes from snomed_icd10cm.csv (NLM I-MAGIC, map release 20260901).
- Combination codes follow the ICD-10-CM Official Guidelines, FY 2027 (RULES below).
- Gaps are made by removing a seeded ~30% of the in-scope billed codes from the chart's
  Condition list. A removed code is scored only when the 12-month chart still supports it
  (SUPPORT below); otherwise it is neutral (neither a hit nor a miss nor a false positive).

Usage:
  python -m eval.synthea.build --synthea-dir OUT/fhir --out BENCH_DIR [--seed 20261027]
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MAP_CSV = HERE / "snomed_icd10cm.csv"

REFERENCE_DATE = date(2026, 10, 27)   # Synthea -r 20261027
WINDOW_DAYS = 365                     # the chart is the last 12 months
REMOVE_FRACTION = 0.30
UNSUPPORTED_FRACTION = 0.08           # patients that get one code Synthea says they do not have
NOTES_PER_CHART = 3
VALUES_PER_LOINC = 3

# Codes CodeSentinel's rules can propose and that Synthea's conditions map to.
# Only these are removed to create gaps. Everything else stays billed.
IN_SCOPE = {
    "E11.9", "E11.22", "E11.65", "N18.30", "N18.4", "I10", "I12.9", "I50.9",
    "E78.5", "E78.1", "D64.9", "F32.9",
}

# Codes used as unsupported existing hard negatives (the patient's Synthea record
# has nothing in that ICD-10-CM category).
UNSUPPORTED_POOL = ["I50.9", "E11.9", "N18.4", "F32.9", "J44.9", "E03.9"]

RULES = {
    "R1": "Diabetes with CKD: E11.22 plus the N18 stage, replacing E11.21/E11.29. FY2027 Guidelines I.A.15 'With' (p11) and Tabular E11.22 'Use additional code to identify stage of chronic kidney disease'.",
    "R2": "Diabetes with hyperglycemia: E11.65 replaces R73.9. ICD-10-CM Alphabetic Index, Diabetes, type 2, with hyperglycemia E11.65; Tabular R73 Excludes1 diabetes mellitus (E08-E13).",
    "R3": "Prediabetes R73.03 is dropped when an E11 code is present. Tabular R73 Excludes1 diabetes mellitus (E08-E13).",
    "R4": "E11.9 (without complications) is dropped when another E11 code is present. Tabular title of E11.9.",
    "R5": "Hypertension with CKD: I12.9 (CKD 1-4) or I12.0 (CKD 5/ESRD) replaces I10. FY2027 Guidelines I.C.9.a.2 (p50).",
    "R6": "Hypertension with heart failure: I11.0 replaces I10 (I50 code kept). FY2027 Guidelines I.C.9.a.1 (p50).",
    "R7": "Hypertension with heart failure and CKD: I13.0 (CKD 1-4) or I13.2 (CKD 5/ESRD) replaces I10/I11/I12. FY2027 Guidelines I.C.9.a.3 (p51).",
    "R8": "One CKD stage: Synthea leaves earlier stages active, so only the highest documented N18 stage is kept. FY2027 Guidelines I.C.14.a.1 (p62): with both a stage and ESRD documented, assign N18.6 only.",
}

SUPPORT = {
    "note": "A note in the 12-month window names the Synthea condition (its SNOMED display text).",
    "medication": "A medication in the chart has a Synthea reasonReference to the condition.",
    "lab": "The latest value in the window meets a published criterion (below).",
    "criteria": {
        "diabetes": "HbA1c (LOINC 4548-4) >= 6.5% (ADA Standards of Care, Section 2).",
        "hyperglycemia": "Diabetes supported and plasma glucose (2339-0 or 2345-7) >= 200 mg/dL (ADA random-glucose criterion).",
        "ckd3": "Two eGFR (33914-3) values < 60 at least 90 days apart, latest 30-59 (KDIGO 2024 chronicity and G3).",
        "ckd4": "Two eGFR values < 60 at least 90 days apart, latest 15-29 (KDIGO 2024 G4).",
        "hyperlipidemia": "LDL (18262-6 or 2089-1) >= 160 or total cholesterol (2093-3) >= 240 mg/dL (NCEP ATP III).",
        "hypertriglyceridemia": "Triglycerides (2571-8) >= 200 mg/dL (NCEP ATP III 'high').",
        "anemia": "Hemoglobin (718-7) < 13.0 g/dL in men or < 12.0 g/dL in women (WHO).",
    },
    "combination": "E11.22 needs both the diabetes and the CKD parts supported; I12.9 needs hypertension and CKD.",
}

DIABETES_SNOMED = {
    "44054006", "127013003", "90781000119102", "157141000119108", "368581000119106",
    "1551000119108", "1501000119109", "97331000119101",
}
CKD_STAGE = {"431855005": 1, "431856006": 2, "433144002": 3, "431857002": 4, "46177005": 5}
HTN = "59621000"
HF = "88805009"
HYPERGLYCEMIA = "80394007"

ICD_SYSTEM = "http://hl7.org/fhir/sid/icd-10-cm"


def load_map() -> dict[str, dict]:
    with MAP_CSV.open(encoding="utf-8") as fh:
        return {row["snomed"]: row for row in csv.DictReader(fh)}


def _d(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _in_window(text: str | None) -> bool:
    when = _d(text)
    return when is not None and REFERENCE_DATE - timedelta(days=WINDOW_DAYS) <= when <= REFERENCE_DATE


def _active(cond: dict) -> bool:
    status = ((cond.get("clinicalStatus") or {}).get("coding") or [{}])[0].get("code")
    return status == "active" and not cond.get("abatementDateTime")


def _resources(bundle: dict) -> list[dict]:
    return [e.get("resource") or {} for e in bundle.get("entry") or []]


def truth_codes(active: dict[str, dict], mapping: dict[str, dict]) -> tuple[dict[str, set[str]], list[str]]:
    """ICD-10-CM codes for the active Synthea conditions. Returns code -> SNOMED parts, rules applied."""
    codes: dict[str, set[str]] = {}
    for sct in active:
        row = mapping.get(sct)
        if row:
            codes.setdefault(row["icd10cm"], set()).add(sct)
    applied: list[str] = []
    has_dm = any(c.startswith("E11") for c in codes)
    stages = [CKD_STAGE[s] for s in active if s in CKD_STAGE]
    ckd_parts = {s for s in active if s in CKD_STAGE}
    stage = max(stages) if stages else None
    dm_parts = {s for s in active if s in DIABETES_SNOMED}

    if stage:
        keep = {1: "N18.1", 2: "N18.2", 3: "N18.30", 4: "N18.4", 5: "N18.6"}[stage]
        lower = [c for c in codes if c.startswith("N18") and c != keep]
        for c in lower:
            codes.pop(c)
        if lower:
            applied.append("R8")
    if has_dm and stage:
        for old in ("E11.21", "E11.29"):
            codes.pop(old, None)
        codes["E11.22"] = dm_parts | ckd_parts
        applied.append("R1")
    if has_dm and "R73.9" in codes and HYPERGLYCEMIA in active:
        codes.pop("R73.9")
        codes["E11.65"] = dm_parts | {HYPERGLYCEMIA}
        applied.append("R2")
    if any(c.startswith("E11") for c in codes) and "R73.03" in codes:
        codes.pop("R73.03")
        applied.append("R3")
    if "E11.9" in codes and any(c.startswith("E11") and c != "E11.9" for c in codes):
        codes.pop("E11.9")
        applied.append("R4")
    if HTN in active and "I10" in codes:
        has_hf = HF in active
        if has_hf and stage:
            codes.pop("I10")
            codes["I13.2" if stage >= 5 else "I13.0"] = {HTN, HF} | ckd_parts
            applied.append("R7")
        elif has_hf:
            codes.pop("I10")
            codes["I11.0"] = {HTN, HF}
            applied.append("R6")
        elif stage:
            codes.pop("I10")
            codes["I12.0" if stage >= 5 else "I12.9"] = {HTN} | ckd_parts
            applied.append("R5")
    return codes, applied


def _latest(observations: list[dict], loincs: tuple[str, ...]) -> list[tuple[date, float]]:
    found = []
    for obs in observations:
        coding = ((obs.get("code") or {}).get("coding") or [{}])[0]
        if coding.get("code") not in loincs:
            continue
        value = (obs.get("valueQuantity") or {}).get("value")
        when = _d(obs.get("effectiveDateTime"))
        if value is None or when is None:
            continue
        found.append((when, float(value)))
    return sorted(found)


def _lab_support(kind: str, labs: list[dict], gender: str) -> bool:
    if kind == "diabetes":
        vals = _latest(labs, ("4548-4",))
        return bool(vals) and vals[-1][1] >= 6.5
    if kind == "hyperglycemia":
        vals = _latest(labs, ("2339-0", "2345-7"))
        return any(v >= 200 for _d, v in vals)
    if kind in ("ckd3", "ckd4"):
        vals = [(d, v) for d, v in _latest(labs, ("33914-3",)) if v < 60]
        if len(vals) < 2 or (vals[-1][0] - vals[0][0]).days < 90:
            return False
        latest = _latest(labs, ("33914-3",))[-1][1]
        return 30 <= latest < 60 if kind == "ckd3" else 15 <= latest < 30
    if kind == "hyperlipidemia":
        ldl = _latest(labs, ("18262-6", "2089-1"))
        tc = _latest(labs, ("2093-3",))
        return (bool(ldl) and ldl[-1][1] >= 160) or (bool(tc) and tc[-1][1] >= 240)
    if kind == "hypertriglyceridemia":
        tg = _latest(labs, ("2571-8",))
        return bool(tg) and tg[-1][1] >= 200
    if kind == "anemia":
        hb = _latest(labs, ("718-7",))
        limit = 13.0 if gender == "male" else 12.0
        return bool(hb) and hb[-1][1] < limit
    return False


LAB_KIND = {
    "E11.9": "diabetes", "N18.30": "ckd3", "N18.4": "ckd4", "E78.5": "hyperlipidemia",
    "E78.1": "hypertriglyceridemia", "D64.9": "anemia",
}


def _part_support(sct: str, active: dict[str, dict], note_text: str, med_reasons: set[str],
                  labs: list[dict], gender: str) -> list[str]:
    cond = active.get(sct) or {}
    how = []
    display = (((cond.get("code") or {}).get("coding") or [{}])[0].get("display") or "").lower()
    display = display.split(" (")[0].strip()
    if display and display in note_text:
        how.append("note")
    if cond.get("id") and cond["id"] in med_reasons:
        how.append("medication")
    kind = None
    if sct in DIABETES_SNOMED:
        kind = "diabetes"
    elif sct == HYPERGLYCEMIA:
        kind = "hyperglycemia"
    elif sct in CKD_STAGE:
        kind = {3: "ckd3", 4: "ckd4"}.get(CKD_STAGE[sct])
    elif sct == "55822004":
        kind = "hyperlipidemia"
    elif sct == "302870006":
        kind = "hypertriglyceridemia"
    elif sct == "271737000":
        kind = "anemia"
    if kind and _lab_support(kind, labs, gender):
        how.append("lab")
    return how


def code_support(code: str, parts: set[str], active, note_text, med_reasons, labs, gender) -> dict[str, list[str]]:
    """Support per SNOMED part. A code is supported when every clinical part is."""
    per_part = {}
    for sct in sorted(parts):
        if code == "E11.22" and sct in DIABETES_SNOMED and sct != "44054006" and "44054006" in parts:
            continue
        per_part[sct] = _part_support(sct, active, note_text, med_reasons, labs, gender)
    if code == "E11.22":
        dm = [h for s, h in per_part.items() if s in DIABETES_SNOMED]
        ckd = [h for s, h in per_part.items() if s in CKD_STAGE]
        return {"diabetes": sorted({x for h in dm for x in h}), "ckd": sorted({x for h in ckd for x in h})}
    if code == "E11.65":
        dm = [h for s, h in per_part.items() if s in DIABETES_SNOMED]
        hg = per_part.get(HYPERGLYCEMIA, [])
        return {"diabetes": sorted({x for h in dm for x in h}), "hyperglycemia": hg}
    if code == "I12.9":
        ckd = [h for s, h in per_part.items() if s in CKD_STAGE]
        return {"hypertension": per_part.get(HTN, []), "ckd": sorted({x for h in ckd for x in h})}
    merged = sorted({x for h in per_part.values() for x in h})
    return {"condition": merged}


def _supported(support: dict[str, list[str]]) -> bool:
    return all(bool(v) for v in support.values())


def _med_with_concept(med: dict, meds_by_id: dict[str, dict]) -> dict:
    if med.get("medicationCodeableConcept"):
        return med
    ref = ((med.get("medicationReference") or {}).get("reference") or "").split("/")[-1].replace("urn:uuid:", "")
    target = meds_by_id.get(ref)
    out = dict(med)
    if target and target.get("code"):
        out["medicationCodeableConcept"] = target["code"]
    return out


def build_chart(bundle: dict, mapping: dict) -> tuple[dict, dict] | None:
    res = _resources(bundle)
    patient = next((r for r in res if r.get("resourceType") == "Patient"), None)
    if not patient:
        return None
    pid = patient["id"]
    active = {}
    for r in res:
        if r.get("resourceType") == "Condition" and _active(r):
            coding = ((r.get("code") or {}).get("coding") or [{}])[0]
            active.setdefault(coding.get("code") or "", r)
    labs = [
        r for r in res
        if r.get("resourceType") == "Observation"
        and ((r.get("category") or [{}])[0].get("coding") or [{}])[0].get("code") == "laboratory"
        and _in_window(r.get("effectiveDateTime"))
        and (r.get("valueQuantity") or {}).get("value") is not None
    ]
    by_loinc: dict[str, list[dict]] = {}
    for obs in sorted(labs, key=lambda o: o.get("effectiveDateTime") or "", reverse=True):
        code = ((obs.get("code") or {}).get("coding") or [{}])[0].get("code") or ""
        by_loinc.setdefault(code, [])
        if len(by_loinc[code]) < VALUES_PER_LOINC:
            by_loinc[code].append(obs)
    chart_labs = [o for group in by_loinc.values() for o in group]

    meds_by_id = {r.get("id"): r for r in res if r.get("resourceType") == "Medication"}
    meds = {}
    for med in sorted((r for r in res if r.get("resourceType") == "MedicationRequest"),
                      key=lambda m: m.get("authoredOn") or ""):
        if med.get("status") != "active" and not _in_window(med.get("authoredOn")):
            continue
        med = _med_with_concept(med, meds_by_id)
        name = (((med.get("medicationCodeableConcept") or {}).get("coding") or [{}])[0].get("display") or "").lower()
        if name:
            meds[name] = med
    chart_meds = list(meds.values())
    med_reasons = {
        (ref.get("reference") or "").split("/")[-1].replace("urn:uuid:", "")
        for med in chart_meds for ref in med.get("reasonReference") or []
    }
    docs = sorted(
        (r for r in res if r.get("resourceType") == "DocumentReference" and _in_window(r.get("date"))),
        key=lambda d: d.get("date") or "",
    )[-NOTES_PER_CHART:]
    note_text = " ".join(
        base64.b64decode(((d.get("content") or [{}])[0].get("attachment") or {}).get("data") or "").decode("utf-8", "replace")
        for d in docs
    ).lower()
    gender = patient.get("gender") or ""

    truth, applied = truth_codes(active, mapping)
    return {
        "patient": patient,
        "pid": pid,
        "active": active,
        "truth": truth,
        "rules": applied,
        "labs": chart_labs,
        "meds": chart_meds,
        "docs": docs,
        "note_text": note_text,
        "med_reasons": med_reasons,
        "gender": gender,
        "encounters": [r for r in res if r.get("resourceType") == "Encounter" and _in_window((r.get("period") or {}).get("start"))],
    }, {}


def _condition(pid: str, code: str, display: str) -> dict:
    return {
        "resourceType": "Condition",
        "id": f"{pid[:24]}-{code.replace('.', '')}",
        "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
        "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}]},
        "code": {"coding": [{"system": ICD_SYSTEM, "code": code, "display": display}], "text": display},
        "subject": {"reference": f"Patient/{pid}"},
    }


def chart_bundle(chart: dict, billed: list[str], displays: dict[str, str]) -> dict:
    pid = chart["pid"]
    entries = [{"resource": chart["patient"]}]
    entries += [{"resource": _condition(pid, code, displays.get(code, code))} for code in billed]
    entries += [{"resource": r} for r in chart["labs"]]
    entries += [{"resource": r} for r in chart["meds"]]
    entries += [{"resource": r} for r in chart["docs"]]
    entries += [{"resource": r} for r in chart["encounters"]]
    return {"resourceType": "Bundle", "type": "collection", "id": pid, "entry": entries}


DISPLAY_OVERRIDES = {
    "E11.22": "Type 2 diabetes mellitus with diabetic chronic kidney disease",
    "E11.65": "Type 2 diabetes mellitus with hyperglycemia",
    "I12.9": "Hypertensive chronic kidney disease with stage 1 through stage 4 chronic kidney disease, or unspecified chronic kidney disease",
    "I12.0": "Hypertensive chronic kidney disease with stage 5 chronic kidney disease or end stage renal disease",
    "I11.0": "Hypertensive heart disease with heart failure",
    "I13.0": "Hypertensive heart and chronic kidney disease with heart failure and stage 1 through stage 4 chronic kidney disease",
    "I13.2": "Hypertensive heart and chronic kidney disease with heart failure and with stage 5 chronic kidney disease, or end stage renal disease",
    "I50.9": "Heart failure, unspecified",
    "N18.4": "Chronic kidney disease, stage 4 (severe)",
    "N18.6": "End stage renal disease",
    "J44.9": "Chronic obstructive pulmonary disease, unspecified",
    "E03.9": "Hypothyroidism, unspecified",
    "F32.9": "Major depressive disorder, single episode, unspecified",
    "E11.9": "Type 2 diabetes mellitus without complications",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthea-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261027)
    args = ap.parse_args()

    mapping = load_map()
    displays = {row["icd10cm"]: row["snomed_display"].split(" (")[0] for row in mapping.values()}
    displays.update(DISPLAY_OVERRIDES)
    out = Path(args.out)
    (out / "bundles").mkdir(parents=True, exist_ok=True)

    charts = []
    for path in sorted(Path(args.synthea_dir).glob("*.json")):
        if path.name.startswith(("hospitalInformation", "practitionerInformation")):
            continue
        built = build_chart(json.loads(path.read_text(encoding="utf-8")), mapping)
        if built:
            charts.append(built[0])
    charts.sort(key=lambda c: c["pid"])

    rng = random.Random(args.seed)
    eligible = [(c["pid"], code) for c in charts for code in sorted(c["truth"]) if code in IN_SCOPE]
    removed = set(rng.sample(eligible, round(REMOVE_FRACTION * len(eligible))))
    hard_rng = random.Random(args.seed + 1)

    labels, manifest = [], []
    for chart in charts:
        pid = chart["pid"]
        truth = chart["truth"]
        gone = sorted(code for code in truth if (pid, code) in removed)
        billed = sorted(code for code in truth if (pid, code) not in removed)
        expected, neutral = [], []
        for code in gone:
            support = code_support(code, truth[code], chart["active"], chart["note_text"],
                                   chart["med_reasons"], chart["labs"], chart["gender"])
            entry = {"code": code, "snomed": sorted(truth[code]), "support": support, "type": "missing"}
            (expected if _supported(support) else neutral).append(entry)
        unsupported = []
        if hard_rng.random() < UNSUPPORTED_FRACTION:
            families = {code.split(".")[0] for code in truth}
            pool = [code for code in UNSUPPORTED_POOL if code.split(".")[0] not in families]
            if pool:
                code = hard_rng.choice(pool)
                billed.append(code)
                unsupported.append({"code": code, "reason": "Synthea record has no condition in this ICD-10-CM category."})
        bundle = chart_bundle(chart, sorted(billed), displays)
        text = json.dumps(bundle, sort_keys=True)
        (out / "bundles" / f"{pid}.json").write_text(text, encoding="utf-8")
        manifest.append({"patient_id": pid, "sha256": hashlib.sha256(text.encode()).hexdigest()})
        labels.append({
            "dataset": "synthea",
            "split": "synthea",
            "patient_id": pid,
            "scenario": "synthea",
            "seed": args.seed,
            "truth_codes": sorted(truth),
            "billed_codes": sorted(billed),
            "removed_codes": gone,
            "expected_gaps": expected,
            "neutral_codes": neutral,
            "unsupported_existing": unsupported,
            "must_not_flag": [],
            "rules_applied": chart["rules"],
            "chart_counts": {"labs": len(chart["labs"]), "medications": len(chart["meds"]), "notes": len(chart["docs"])},
            "not_author_labeled": True,
        })
    (out / "labels.jsonl").write_text("".join(json.dumps(l, sort_keys=True) + "\n" for l in labels), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps({
        "seed": args.seed, "reference_date": REFERENCE_DATE.isoformat(), "window_days": WINDOW_DAYS,
        "remove_fraction": REMOVE_FRACTION, "in_scope": sorted(IN_SCOPE), "rules": RULES, "support": SUPPORT,
        "eligible_pairs": len(eligible), "removed_pairs": len(removed), "charts": manifest,
    }, indent=1), encoding="utf-8")
    exp = sum(len(l["expected_gaps"]) for l in labels)
    neu = sum(len(l["neutral_codes"]) for l in labels)
    uns = sum(len(l["unsupported_existing"]) for l in labels)
    print(f"charts={len(labels)} eligible={len(eligible)} removed={len(removed)} expected={exp} neutral={neu} unsupported_existing={uns}")


if __name__ == "__main__":
    main()
