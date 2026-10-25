"""MTSamples review-pile measurement.

The notes are not gold-labeled. This module counts how many rules "review"
items the offline cascade still shows. It does not report precision or recall,
and it does not write note text.
"""

from __future__ import annotations

import csv
import os
from collections import Counter
from typing import Any

from eval.adapter import AuditAdapter

# Same specialty filter as test_mtsamples.run_mtsamples_test. The leading
# spaces are in the Kaggle CSV.
SPECIALTIES = [
    " General Medicine", " Cardiovascular / Pulmonary",
    " Consult - History and Phy.", " Endocrinology",
    " Nephrology", " Gastroenterology", " Neurology",
    " Psychiatry / Psychology", " Hematology - Oncology",
    " SOAP / Chart / Progress Notes", " Discharge Summary",
    " Emergency Room Reports", " Pain Management",
    " Internal Medicine",
]

DOCUMENTED_BASELINE_REVIEW = 1188


def measure(csv_path: str, adapter: AuditAdapter | None = None) -> dict[str, Any]:
    adapter = adapter or AuditAdapter()
    # Imported lazily so generating the corpus does not load the pipeline twice.
    import sys
    from pathlib import Path
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from test_mtsamples import parse_note_to_profile

    notes = 0
    skipped = 0
    errors = 0
    approved = 0
    review = 0
    rejected = 0
    review_still_shown = 0
    approved_still_shown = 0
    drop_reasons: Counter[str] = Counter()

    with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            if row.get("medical_specialty", "") not in SPECIALTIES:
                continue
            text = row.get("transcription") or ""
            if len(text) < 100:
                continue
            rows.append(row)

    for i, row in enumerate(rows):
        text = row.get("transcription") or ""
        specialty = row.get("medical_specialty") or ""
        profile = parse_note_to_profile(text, f"mt-{i:04d}", specialty)
        if not profile:
            skipped += 1
            continue
        notes += 1
        try:
            rules = adapter.rules_for(profile)
            audit = adapter.audit(profile, "C4", mode="mock")
        except Exception:
            errors += 1
            continue
        shown = {
            (it.get("code") or "").upper()
            for it in audit["items"]
            if it.get("shown_to_coder") and it.get("gap_type") != "unsupported_existing"
        }
        reason_by_code = {}
        for it in audit["items"]:
            code = (it.get("code") or "").upper()
            if code and not it.get("shown_to_coder"):
                reason_by_code[code] = it.get("drop_reason") or it.get("assertion_status") or "dropped"
        for gap in rules.get("approved") or []:
            approved += 1
            if (gap.get("icd10_code") or "").upper() in shown:
                approved_still_shown += 1
        for gap in rules.get("review") or []:
            review += 1
            code = (gap.get("icd10_code") or "").upper()
            if code in shown:
                review_still_shown += 1
            else:
                drop_reasons[reason_by_code.get(code, "not_shown")] += 1
        rejected += len(rules.get("rejected") or [])
        if notes % 250 == 0:
            print(f"  mtsamples {notes} notes, review {review}, still shown {review_still_shown}")

    reduction = None
    if review:
        reduction = (review - review_still_shown) / review
    return {
        "status": "measured",
        "accuracy_claimed": False,
        "csv_present": True,
        "documented_baseline_review_items": DOCUMENTED_BASELINE_REVIEW,
        "documented_baseline_source": "test_mtsamples.py module docstring, prior rules run",
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "notes_in_specialty_filter": len(rows),
        "notes_processed": notes,
        "notes_skipped_short_or_empty": skipped,
        "errors": errors,
        "rules_approved_items": approved,
        "rules_review_items": review,
        "rules_rejected_items": rejected,
        "c4_review_items_still_shown": review_still_shown,
        "c4_approved_items_still_shown": approved_still_shown,
        "review_items_removed": review - review_still_shown,
        "review_pile_reduction_vs_measured": reduction,
        "drop_reasons_for_removed_review_items": dict(drop_reasons.most_common()),
        "note": (
            "Workload only. MTSamples has no gold labels, so this is not precision or recall. "
            "The filter is the offline policy, not a live Nemotron run. "
            "The historical 1,188 depends on which conditions parse_note_to_profile marked as "
            "already coded, and that uses Python's salted hash() unless PYTHONHASHSEED is set. "
            "Compare c4_review_items_still_shown with rules_review_items from this same process."
        ),
    }


def pending(reason: str) -> dict[str, Any]:
    return {
        "status": "pending_csv",
        "accuracy_claimed": False,
        "csv_present": False,
        "reason": reason,
        "documented_baseline_review_items": DOCUMENTED_BASELINE_REVIEW,
        "documented_baseline_source": "test_mtsamples.py module docstring, prior rules run",
        "rules_review_items": None,
        "c4_review_items_still_shown": None,
        "note": (
            "The Kaggle CSV is not in the repo and was not passed to this run. "
            "Do not invent a reduced count. Re-run with --mtsamples PATH. "
            "Accuracy is not claimed on this corpus."
        ),
    }
