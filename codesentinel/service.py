"""Resolve a chart and run an audit. No FastAPI imports here."""

from __future__ import annotations

from codesentinel.fhir_guard import GuardError, validate_fhir_base_url, validate_patient_id
from codesentinel.phi import PHI_DETAIL, phi_detected

NOT_FOUND = (
    "Patient not found. Pass a pt-001…pt-010 id, an inline profile, or an allowlisted FHIR URL."
)
SYNTHETIC_IDS = {f"pt-{i:03d}" for i in range(1, 11)}

HOOKS = {
    "pt-001": "Coded E11.9; HbA1c 8.4% and CKD are documented in the plan",
    "pt-002": "Heart failure and COPD are documented and not coded",
    "pt-003": "Depression and hypothyroidism are supported by the medication list",
    "pt-004": "Diabetes, CKD, and heart failure: combination-code case",
    "pt-005": "Negation trap: denies diabetes, kidney disease, and heart failure",
    "pt-006": "Medications suggest diabetes and hypertension, and nothing is coded",
    "pt-007": "Abnormal labs only, with no note and no medications",
    "pt-008": "Diabetes is in the plan; family history of heart failure is a trap",
    "pt-009": "Screening PHQ-9 and possible anxiety should not become codes",
    "pt-010": "Several documented conditions, including obstructive sleep apnea",
}


class AuditError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def list_patients() -> dict:
    from test_dataset import build_test_patients, get_patient_profile

    dataset = build_test_patients()
    rows = []
    for patient_id in sorted(SYNTHETIC_IDS):
        profile = get_patient_profile(patient_id, dataset)
        rows.append(
            {
                "id": patient_id,
                "name": profile.get("demographics", {}).get("full_name", patient_id),
                "hook": HOOKS[patient_id],
            }
        )
    return {"patients": rows}


def execute_audit(payload: dict, headers: dict | None = None) -> dict:
    if not isinstance(payload, dict):
        raise AuditError(422, "Request body must be a JSON object.")
    include_rules = payload.get("include_rules_only", True)
    if not isinstance(include_rules, bool):
        raise AuditError(422, "include_rules_only must be a boolean.")
    profile, demographics = resolve_chart(payload, headers or {})
    from gap_pipeline import run_gap_pipeline

    result = run_gap_pipeline(profile, demographics)
    audit = result.get("audit")
    if not audit:
        raise AuditError(500, "Verification did not return an audit.")
    if not include_rules:
        audit = dict(audit)
        audit["rules_only"] = None
    return audit


def resolve_chart(payload: dict, headers: dict):
    lowered = {str(key).lower(): value for key, value in headers.items()}
    profile = payload.get("profile")
    if isinstance(profile, dict) and profile:
        chart = dict(profile)
        patient_id = str(
            chart.get("fhir_patient_id")
            or payload.get("patient_id")
            or lowered.get("x-patient-id")
            or "inline"
        )
        chart["fhir_patient_id"] = patient_id
        chart.setdefault("clinical_notes", [])
        chart.setdefault("observations", [])
        chart.setdefault("medications", [])
        chart.setdefault("conditions", [])
        chart.setdefault("encounters", [])
        demographics = chart.get("demographics") if isinstance(chart.get("demographics"), dict) else {}
        demographics = dict(demographics)
        demographics.setdefault("full_name", demographics.get("full_name") or patient_id)
        demographics.setdefault("found", True)
        chart["demographics"] = demographics
        return chart, demographics

    patient_id = str(payload.get("patient_id") or lowered.get("x-patient-id") or "")
    if patient_id in SYNTHETIC_IDS:
        from test_dataset import get_patient_profile

        chart = get_patient_profile(patient_id)
        if chart.get("error"):
            raise AuditError(404, NOT_FOUND)
        return chart, chart.get("demographics") or {}

    fhir_url = payload.get("fhir_server_url") or lowered.get("x-fhir-server-url") or ""
    if not patient_id or not fhir_url:
        raise AuditError(404, NOT_FOUND)
    try:
        validate_patient_id(patient_id)
        fhir_url = validate_fhir_base_url(str(fhir_url))
    except GuardError as exc:
        status = 422 if "patient_id" in str(exc) else 403
        raise AuditError(status, str(exc)) from exc

    from fhir_adapter import FHIRClient, FHIRPatientExtractor

    token = str(lowered.get("x-fhir-access-token") or "")
    try:
        client = FHIRClient(fhir_url, token)
        extractor = FHIRPatientExtractor(client)
        demographics = extractor.get_patient_demographics(patient_id)
        if not demographics.get("found"):
            raise AuditError(404, f"Patient {patient_id} not found on the FHIR server.")
        chart = extractor.extract_patient(patient_id)
    except AuditError:
        raise
    except GuardError as exc:
        raise AuditError(403, str(exc)) from exc
    except Exception as exc:
        raise AuditError(502, f"FHIR fetch failed: {exc}") from exc
    return chart, demographics


def body_has_phi(raw: str) -> bool:
    return phi_detected(raw)


def phi_detail() -> str:
    return PHI_DETAIL
