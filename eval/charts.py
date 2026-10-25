"""FHIR R4 bundles and the profile dict the rules pipeline expects.

Gold-A profiles come from test_dataset.get_patient_profile. Gold-B profiles are
built from the committed Bundle JSON so a scorer never depends on regenerating
prose.
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

ICD_SYSTEM = "http://hl7.org/fhir/sid/icd-10-cm"
LOINC = "http://loinc.org"

EVAL_DIR = Path(__file__).resolve().parent
GOLD_DIR = EVAL_DIR / "gold"
BUNDLE_DIR = GOLD_DIR / "bundles"
RESULTS_DIR = EVAL_DIR / "results"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def render_note(hpi: str, problems: str, ap: str) -> str:
    return (
        "PROGRESS NOTE — synthetic chart — not a real patient\n"
        "\n"
        "HISTORY OF PRESENT ILLNESS:\n"
        f"{hpi.strip()}\n"
        "\n"
        "PROBLEM LIST:\n"
        f"{problems.strip()}\n"
        "\n"
        "ASSESSMENT AND PLAN:\n"
        f"{ap.strip()}\n"
    )


def build_bundle(
    patient_id: str,
    *,
    gender: str,
    birth_date: str,
    conditions: list[tuple[str, str]],
    observations: list[tuple[str, str, float, str]],
    medications: list[str],
    note: str,
    note_date: str = "2026-02-15",
) -> dict[str, Any]:
    """Build a FHIR R4 collection Bundle. All content is synthetic."""
    patient = {
        "resourceType": "Patient",
        "id": patient_id,
        "meta": {"tag": [{"system": "http://codesentinel.local/synthetic", "code": "synthetic"}]},
        "name": [{"given": ["Case"], "family": patient_id.upper()}],
        "gender": gender,
        "birthDate": birth_date,
    }
    encounter = {
        "resourceType": "Encounter",
        "id": f"{patient_id}-enc",
        "status": "finished",
        "class": {"code": "AMB"},
        "type": [{"text": "Office Visit"}],
        "subject": {"reference": f"Patient/{patient_id}"},
        "period": {"start": note_date},
    }
    entries: list[dict[str, Any]] = [{"resource": patient}, {"resource": encounter}]
    for i, (code, display) in enumerate(conditions, start=1):
        entries.append({"resource": {
            "resourceType": "Condition",
            "id": f"{patient_id}-c{i}",
            "clinicalStatus": {"coding": [{"code": "active"}]},
            "code": {"coding": [{"system": ICD_SYSTEM, "code": code, "display": display}], "text": display},
            "subject": {"reference": f"Patient/{patient_id}"},
        }})
    for i, (loinc, name, value, unit) in enumerate(observations, start=1):
        entries.append({"resource": {
            "resourceType": "Observation",
            "id": f"{patient_id}-o{i}",
            "status": "final",
            "code": {"coding": [{"system": LOINC, "code": loinc, "display": name}], "text": name},
            "subject": {"reference": f"Patient/{patient_id}"},
            "effectiveDateTime": note_date,
            "valueQuantity": {"value": value, "unit": unit},
        }})
    for i, name in enumerate(medications, start=1):
        entries.append({"resource": {
            "resourceType": "MedicationRequest",
            "id": f"{patient_id}-m{i}",
            "status": "active",
            "intent": "order",
            "medicationCodeableConcept": {"coding": [{"display": name}], "text": name},
            "subject": {"reference": f"Patient/{patient_id}"},
            "authoredOn": note_date,
        }})
    entries.append({"resource": {
        "resourceType": "DocumentReference",
        "id": f"{patient_id}-note",
        "status": "current",
        "date": note_date,
        "type": {"text": "Progress Note"},
        "subject": {"reference": f"Patient/{patient_id}"},
        "content": [{"attachment": {
            "contentType": "text/plain",
            "data": _b64(note),
        }}],
    }})
    return {"resourceType": "Bundle", "type": "collection", "id": patient_id, "entry": entries}


def _age(birth_date: str) -> int | None:
    try:
        born = datetime.strptime(birth_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (date.today() - born).days // 365


def bundle_to_profile(bundle: dict) -> dict[str, Any]:
    """Convert a Bundle into the dict shape gap_pipeline.run_gap_pipeline expects."""
    resources = [e.get("resource") or {} for e in bundle.get("entry") or []]
    by_type: dict[str, list[dict]] = {}
    for res in resources:
        by_type.setdefault(res.get("resourceType") or "", []).append(res)

    patient = (by_type.get("Patient") or [{}])[0]
    pid = patient.get("id") or bundle.get("id") or ""
    names = patient.get("name") or [{}]
    given = " ".join((names[0].get("given") or []) if names else [])
    family = (names[0].get("family") if names else "") or ""
    birth = patient.get("birthDate") or ""

    notes = []
    for doc in by_type.get("DocumentReference") or []:
        for content in doc.get("content") or []:
            att = content.get("attachment") or {}
            data = att.get("data") or ""
            text = base64.b64decode(data).decode("utf-8", errors="replace") if data else ""
            notes.append({
                "attachment_id": doc.get("id") or "",
                "doc_type": (doc.get("type") or {}).get("text") or "Progress Note",
                "doc_date": (doc.get("date") or "")[:10],
                "content_type": att.get("contentType") or "text/plain",
                "text": text,
            })

    observations = []
    for obs in by_type.get("Observation") or []:
        coding = ((obs.get("code") or {}).get("coding") or [{}])[0]
        qty = obs.get("valueQuantity") or {}
        observations.append({
            "observation_id": obs.get("id") or "",
            "loinc": coding.get("code") or "",
            "name": coding.get("display") or (obs.get("code") or {}).get("text") or "",
            "value": qty.get("value"),
            "unit": qty.get("unit") or "",
            "date": (obs.get("effectiveDateTime") or "")[:10],
            "status": obs.get("status") or "final",
            "ref_range_low": None,
            "ref_range_high": None,
            "interpretation": "",
        })

    conditions = []
    coded: set[str] = set()
    for cond in by_type.get("Condition") or []:
        coding = ((cond.get("code") or {}).get("coding") or [{}])[0]
        status = ((cond.get("clinicalStatus") or {}).get("coding") or [{}])[0].get("code") or "active"
        code = coding.get("code") or ""
        conditions.append({
            "condition_id": cond.get("id") or "",
            "icd10_code": code,
            "display": coding.get("display") or (cond.get("code") or {}).get("text") or "",
            "clinical_status": status,
            "verification_status": "confirmed",
            "onset_date": "",
            "recorded_date": "",
        })
        if code:
            coded.add(code)

    medications = []
    for med in by_type.get("MedicationRequest") or []:
        mc = med.get("medicationCodeableConcept") or {}
        coding = (mc.get("coding") or [{}])[0]
        medications.append({
            "medication_id": med.get("id") or "",
            "name": coding.get("display") or mc.get("text") or "",
            "code": coding.get("code") or "",
            "status": med.get("status") or "active",
            "dosage": "",
            "date": (med.get("authoredOn") or "")[:10],
        })

    encounters = []
    for enc in by_type.get("Encounter") or []:
        period = enc.get("period") or {}
        types = enc.get("type") or [{}]
        encounters.append({
            "encounter_id": enc.get("id") or "",
            "type": (types[0].get("text") if types else "") or "",
            "status": enc.get("status") or "finished",
            "start_date": (period.get("start") or "")[:10],
            "end_date": (period.get("end") or "")[:10],
            "class": (enc.get("class") or {}).get("code") or "AMB",
        })

    return {
        "fhir_patient_id": pid,
        "ensure_patient_id": pid,
        "demographics": {
            "patient_id": pid,
            "found": True,
            "first_name": given,
            "last_name": family,
            "full_name": f"{given} {family}".strip(),
            "dob": birth,
            "age": _age(birth),
            "gender": patient.get("gender") or "",
            "ensure_patient_id": pid,
        },
        "clinical_notes": notes,
        "observations": observations,
        "medications": medications,
        "conditions": conditions,
        "encounters": encounters,
        "procedures": [],
        "claims_codes": set(coded),
    }


def load_bundle(patient_id: str) -> dict:
    path = BUNDLE_DIR / f"{patient_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def iter_gold_b(split: str | None = None) -> list[tuple[dict, dict]]:
    """Return (label, profile) pairs. `split` filters train, dev, or test."""
    path = GOLD_DIR / "gold_b.jsonl"
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        label = json.loads(line)
        if split and label.get("split") != split:
            continue
        profile = bundle_to_profile(load_bundle(label["patient_id"]))
        rows.append((label, profile))
    return rows


def load_gold_a() -> list[tuple[dict, dict]]:
    import sys
    root = EVAL_DIR.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from test_dataset import build_test_patients, get_patient_profile

    payload = json.loads((GOLD_DIR / "gold_a.json").read_text(encoding="utf-8"))
    dataset = build_test_patients()
    rows = []
    for label in payload["patients"]:
        profile = get_patient_profile(label["patient_id"], dataset)
        rows.append((label, profile))
    return rows
