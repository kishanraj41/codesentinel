"""Attach the Nemotron audit to a rules-pipeline result."""

from __future__ import annotations

import copy
import uuid

from codesentinel import CONTRACT_VERSION
from codesentinel.api_models import AuditResponse
from codesentinel.nemotron.cascade import run_cascade


def apply_verification(
    profile,
    demographics,
    rules,
    nemotron_client=None,
    guideline_client=None,
    cascade_config: str = "C4",
) -> dict:
    snapshot = {
        "approved": copy.deepcopy(rules.get("approved") or []),
        "review": copy.deepcopy(rules.get("review") or []),
        "rejected": copy.deepcopy(rules.get("rejected") or []),
        "revenue_summary": copy.deepcopy(rules.get("revenue_summary") or {}),
    }
    cascade = run_cascade(
        profile,
        snapshot,
        nemotron_client=nemotron_client,
        guideline_client=guideline_client,
        config=cascade_config,
    )
    approved = cascade["legacy"]["approved"]
    review = cascade["legacy"]["review"]
    rejected = list(cascade["legacy"]["rejected"]) + list(snapshot["rejected"])
    approved.sort(key=lambda gap: gap.get("annual_revenue_impact") or 0, reverse=True)
    audit = _audit_dict(profile, demographics, snapshot, cascade)
    # Validate once so a contract drift fails in tests instead of in the UI.
    AuditResponse.model_validate(audit)
    return {
        "approved": approved,
        "review": review,
        "rejected": rejected,
        "revenue_summary": _revenue(approved, review),
        "verification_applied": True,
        "audit": audit,
        "worklist": audit["worklist"],
        "dropped": audit["dropped"],
        "timeline": audit["timeline"],
        "receipt": audit["receipt"],
        "citations": audit["citations"],
        "info": audit["info"],
    }


def _audit_dict(profile, demographics, snapshot, cascade) -> dict:
    worklist = cascade["worklist"]
    dropped = cascade["dropped"]
    patient_id = str(profile.get("fhir_patient_id") or (demographics or {}).get("patient_id") or "")
    patient_name = ""
    if isinstance(demographics, dict):
        patient_name = str(demographics.get("full_name") or "")
    return {
        "contract_version": CONTRACT_VERSION,
        "audit_id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "patient_name": patient_name,
        "mode": cascade["mode"],
        "guideline_mode": cascade["guideline_mode"],
        "guideline_provenance": cascade.get("guideline_provenance") or {},
        "degraded_reasons": list(cascade["degraded_reasons"]),
        "summary": {
            "candidates_generated": len(snapshot["approved"]) + len(snapshot["review"]),
            "rules_approved": len(snapshot["approved"]),
            "rules_review": len(snapshot["review"]),
            "rules_rejected": len(snapshot["rejected"]),
            "worklist_count": len(worklist),
            "accept_count": sum(1 for item in worklist if item["decision"] == "accept"),
            "review_count": sum(1 for item in worklist if item["decision"] == "review"),
            "abstain_count": sum(1 for item in worklist if item["decision"] == "abstain"),
            "specificity_upgrades": sum(1 for item in worklist if item["gap_type"] == "specificity_upgrade"),
            "combination_codes": sum(1 for item in worklist if item["gap_type"] == "combination"),
            "dropped_count": len(dropped),
            "info_count": len(cascade.get("info") or []),
        },
        "worklist": worklist,
        "info": list(cascade.get("info") or []),
        "dropped": dropped,
        "timeline": cascade["timeline"],
        "receipt": cascade["receipt"],
        "citations": cascade["citations"],
        "rules_only": snapshot,
    }


def _revenue(approved, review) -> dict:
    approved_total = sum(float(gap.get("annual_revenue_impact") or 0) for gap in approved)
    review_total = sum(float(gap.get("annual_revenue_impact") or 0) for gap in review)
    return {
        "approved_annual_impact": round(approved_total, 2),
        "review_annual_impact": round(review_total, 2),
        "total_potential_impact": round(approved_total + review_total, 2),
    }
