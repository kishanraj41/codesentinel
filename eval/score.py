"""Item-level scoring for one configuration.

A gap item is (patient, ICD-10 code). Unsupported-existing flags are a separate
task and do not enter precision or recall. Review burden counts every item the
config sends to a human, including unsupported-existing flags and abstentions.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

SHOWN = frozenset({"accept", "review", "abstain"})


def _codes(entries: Iterable[dict], key: str = "code") -> set[str]:
    return {(e.get(key) or "").upper() for e in entries if e.get(key)}


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """95% Wilson interval. Returns (None, None) when n is 0."""
    if n <= 0:
        return (None, None)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return ordered[int(k)]
    return ordered[lo] * (hi - k) + ordered[hi] * (k - lo)


def score_chart(label: dict, items: list[dict]) -> dict[str, Any]:
    shown_gaps = [
        it for it in items
        if it.get("shown_to_coder") and it.get("gap_type") != "unsupported_existing"
    ]
    shown_codes = {(it.get("code") or "").upper() for it in shown_gaps}
    expected = _codes(label.get("expected_gaps") or [])
    traps = list(label.get("must_not_flag") or [])
    trap_codes = _codes(traps)
    unsupported_gold = _codes(label.get("unsupported_existing") or [])
    unsupported_shown = {
        (it.get("code") or "").upper()
        for it in items
        if it.get("shown_to_coder") and it.get("gap_type") == "unsupported_existing"
    }

    tp = shown_codes & expected
    fp = shown_codes - expected
    fn = expected - shown_codes

    hard = []
    for trap in traps:
        code = (trap.get("code") or "").upper()
        passed = code not in shown_codes
        hard.append({
            "code": code,
            "category": trap.get("category") or "unspecified",
            "passed": passed,
        })

    abstain = sum(1 for it in items if it.get("decision") == "abstain")
    unsupported_drops = sum(1 for it in items if it.get("drop_reason") == "unsupported_claim")
    considered = len(items)

    return {
        "patient_id": label.get("patient_id"),
        "scenario": label.get("scenario"),
        "split": label.get("split"),
        "tp": sorted(tp),
        "fp": sorted(fp),
        "fn": sorted(fn),
        "tp_count": len(tp),
        "fp_count": len(fp),
        "fn_count": len(fn),
        "shown_codes": sorted(shown_codes),
        "gold_codes": sorted(expected),
        "review_items": sum(1 for it in items if it.get("shown_to_coder")),
        "hard_negatives": hard,
        "hard_pass": sum(1 for h in hard if h["passed"]),
        "hard_total": len(hard),
        "unsupported_tp": sorted(unsupported_shown & unsupported_gold),
        "unsupported_fp": sorted(unsupported_shown - unsupported_gold),
        "unsupported_fn": sorted(unsupported_gold - unsupported_shown),
        "abstain_count": abstain,
        "candidates_considered": considered,
        "unsupported_claim_drops": unsupported_drops,
        "trap_codes": sorted(trap_codes),
    }


def aggregate(chart_scores: list[dict], *, costs: list[float], latencies: list[float],
              tavily_credits: list[float], tavily_cache_hits: list[float],
              tavily_uncached: list[float], guideline_changes: int) -> dict[str, Any]:
    tp = sum(c["tp_count"] for c in chart_scores)
    fp = sum(c["fp_count"] for c in chart_scores)
    fn = sum(c["fn_count"] for c in chart_scores)
    precision = tp / (tp + fp) if (tp + fp) else (1.0 if fn == 0 and tp == 0 else 0.0)
    # No predicted positives and no gold positives: precision is undefined; use None.
    if tp + fp == 0:
        precision_out: float | None = None
    else:
        precision_out = precision
    recall = tp / (tp + fn) if (tp + fn) else None
    n = len(chart_scores) or 1
    review_items = sum(c["review_items"] for c in chart_scores)
    hard_pass = sum(c["hard_pass"] for c in chart_scores)
    hard_total = sum(c["hard_total"] for c in chart_scores)

    by_cat: dict[str, list[int]] = {}
    for chart in chart_scores:
        for item in chart["hard_negatives"]:
            bucket = by_cat.setdefault(item["category"], [0, 0])
            bucket[1] += 1
            if item["passed"]:
                bucket[0] += 1
    by_category = {
        cat: {"pass": pair[0], "total": pair[1], "rate": pair[0] / pair[1] if pair[1] else None}
        for cat, pair in sorted(by_cat.items())
    }

    u_tp = sum(len(c["unsupported_tp"]) for c in chart_scores)
    u_fp = sum(len(c["unsupported_fp"]) for c in chart_scores)
    u_fn = sum(len(c["unsupported_fn"]) for c in chart_scores)
    abstain = sum(c["abstain_count"] for c in chart_scores)
    considered = sum(c["candidates_considered"] for c in chart_scores)

    def _mean(vals: list[float]) -> float | None:
        return sum(vals) / len(vals) if vals else None

    return {
        "n_charts": len(chart_scores),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision_out,
        "precision_ci95": list(wilson(tp, tp + fp)),
        "recall": recall,
        "recall_ci95": list(wilson(tp, tp + fn)),
        "f1": f1(precision_out, recall),
        "review_items": review_items,
        "review_burden_per_chart": review_items / n,
        "false_positives_per_chart": fp / n,
        "hard_negative_pass_rate": {
            "overall": (hard_pass / hard_total) if hard_total else None,
            "pass": hard_pass,
            "total": hard_total,
            "by_category": by_category,
        },
        "unsupported_existing": {
            "tp": u_tp,
            "fp": u_fp,
            "fn": u_fn,
            "precision": (u_tp / (u_tp + u_fp)) if (u_tp + u_fp) else None,
            "recall": (u_tp / (u_tp + u_fn)) if (u_tp + u_fn) else None,
        },
        "unsupported_claim_drops": sum(c["unsupported_claim_drops"] for c in chart_scores),
        "abstain_count": abstain,
        "abstain_rate": (abstain / considered) if considered else None,
        "candidates_considered": considered,
        "latency_p50_s": percentile(latencies, 0.50),
        "latency_p95_s": percentile(latencies, 0.95),
        "cost_per_chart_usd": _mean(costs),
        "tavily_credits_per_chart": _mean(tavily_credits),
        "tavily_cache_hits_per_chart": _mean(tavily_cache_hits),
        "tavily_credits_if_uncached_per_chart": _mean(tavily_uncached),
        "guideline_decision_changes": guideline_changes,
    }


def empty_score_counts() -> dict[str, int]:
    return {"tp": 0, "fp": 0, "fn": 0}
