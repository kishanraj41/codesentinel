"""Load eval/results JSON when the harness has written it.

Until then the tab shows the rules-only MTSamples queue counts already
recorded in test_mtsamples.py, and the failure classes the audit is built
to catch. Those counts are not precision or recall.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

BASELINE = {
    "label": "Rules pipeline on MTSamples",
    "source": "test_mtsamples.py expected output. Unlabeled transcriptions.",
    "notes": 2227,
    "approved": 279,
    "review": 1188,
    "warning": (
        "Queue counts only. MTSamples has no gold labels, so this is not "
        "precision, recall, or a measured Nemotron result."
    ),
}

FAILURE_CLASSES = [
    {
        "id": "negation",
        "title": "Negation",
        "detail": "“Denies” and “no evidence of” are not diagnoses. Patricia Davis is the chart.",
    },
    {
        "id": "family_history",
        "title": "Family history",
        "detail": "A parent’s condition is not the patient’s. Margaret’s mother had CHF.",
    },
    {
        "id": "screening",
        "title": "Screening",
        "detail": "A PHQ-9 at a wellness visit is not depression. Jennifer Wilson is the chart.",
    },
    {
        "id": "copy_forward",
        "title": "Copied-forward problem list",
        "detail": "A problem list with no MEAT in this encounter is not support.",
    },
    {
        "id": "hypothetical",
        "title": "Rule-out and hypothetical plans",
        "detail": "“Rule out” and “evaluate for” stay with a human. The audit abstains.",
    },
    {
        "id": "combination",
        "title": "Combination codes",
        "detail": "Diabetes with CKD is a coding decision, not two unrelated codes. Robert Williams is the chart.",
    },
]


def _dirs() -> list[Path]:
    found: list[Path] = []
    env = os.environ.get("EVAL_RESULTS_DIR", "").strip()
    if env:
        found.append(Path(env))
    here = Path(__file__).resolve()
    # The published live run first, then mock regression output, then legacy paths.
    found.append(here.parents[2] / "eval" / "results" / "live")
    found.append(here.parents[2] / "eval" / "results" / "mock")
    found.append(here.parents[2] / "eval" / "results")
    found.append(here.parents[1] / "eval_results" / "live")
    found.append(here.parents[1] / "eval_results")
    return found


def _configs_from(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("configs", "results", "runs"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    if "id" in payload or "precision" in payload or "review_burden" in payload:
        return [payload]
    return []


def _failures_from(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("known_failures") or payload.get("failures") or []
    if not isinstance(rows, list):
        return []
    cleaned = []
    for row in rows:
        if isinstance(row, dict) and (row.get("title") or row.get("cause")):
            cleaned.append(row)
    return cleaned


def _summary_path() -> Path | None:
    for directory in _dirs():
        path = directory / "summary.json"
        if path.is_file():
            return path
    return None


def _for_table(row: dict) -> dict:
    shown = dict(row)
    if "review_burden" not in shown and "review_burden_per_chart" in shown:
        shown["review_burden"] = shown["review_burden_per_chart"]
    if "usd_per_chart" not in shown and "cost_per_chart_usd" in shown:
        shown["usd_per_chart"] = shown["cost_per_chart_usd"]
    if "p50_latency_s" not in shown and "latency_p50_s" in shown:
        shown["p50_latency_s"] = shown["latency_p50_s"]
    return shown


def _for_failure(row: dict) -> dict:
    title = row.get("title")
    if not title:
        scenario = str(row.get("scenario") or "Miss")
        code = str(row.get("code") or "")
        title = f"{scenario} {code}".strip()
    example = row.get("example")
    if not example and row.get("patient_id"):
        kind = row.get("failure_type") or ""
        example = f"{row['patient_id']} · {kind}".strip(" ·")
    return {**row, "title": title, "example": example}


def load_eval() -> dict:
    """The Eval tab reads one summary.json: eval/results/live first, then eval/results/mock."""
    path = _summary_path()
    payload = None
    if path is not None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
    configs = [_for_table(row) for row in _configs_from(payload)]
    measured = [_for_failure(row) for row in _failures_from(payload)]
    loaded = bool(configs or measured)
    note = (
        (
            "Rows are from eval/results/live/summary.json, the published live run on Nebius Token Factory. "
            "The MTSamples counts above stay queue counts, not precision."
            if isinstance(payload, dict) and payload.get("mode") == "live"
            else "Rows are from eval/results/mock/summary.json. The MTSamples counts above stay "
            "queue counts, not precision. Mock C4 is the cascade with the recorded client, "
            "not a live Nemotron score."
        )
        if loaded
        else (
            "Measured precision, recall, review burden, and dollars per chart "
            "appear here when eval/results/live/summary.json is present."
        )
    )
    return {
        "status": "loaded" if loaded else "awaiting_harness",
        "source": str(path) if path and loaded else "eval/results/live/summary.json",
        "files": [str(path)] if path and loaded else [],
        "mode": payload.get("mode") if isinstance(payload, dict) else None,
        "mode_detail": payload.get("mode_detail") if isinstance(payload, dict) else None,
        "baseline": BASELINE,
        "known_failure_classes": FAILURE_CLASSES,
        "measured_failures": measured,
        "configs": configs,
        "metrics_note": note,
    }
