"""Run the six-config eval and write eval/results plus eval/RESULTS.md.

Default dataset is the Gold-B held-out test split. Gold-A is written beside it.
Live Nemotron/Tavily runs only when the API keys are set and EVAL_OFFLINE is not 1.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval.adapter import AuditAdapter, guardrail_probe
from eval.charts import GOLD_DIR, RESULTS_DIR, iter_gold_b, load_gold_a
from eval.generate import write_corpus
from eval.lexicon import CONFIG_LABELS, CONFIGS
from eval.mtsamples_eval import measure as measure_mtsamples
from eval.mtsamples_eval import pending as pending_mtsamples
from eval.score import aggregate, score_chart

CAUSES = {
    "cross_sentence_retraction": (
        "The retraction is in the next sentence and does not repeat the condition name. "
        "If this row is still here, the neighbor window did not mark the code negated."
    ),
    "combo_parent_uncoded": (
        "E11.22 is proposed from the chart when diabetes and chronic kidney disease are both documented. "
        "Unspecified N18.9 is dropped only after a more specific N18 code in the same family is accepted."
    ),
    "hf_specificity": (
        "The note documents reduced ejection fraction or systolic heart failure. "
        "The cascade proposes I50.22 as a specificity upgrade and accepts it only with a verified quote "
        "and a guideline citation. I50.9 is dropped only after that more specific code is accepted."
    ),
    "acute_egfr": (
        "A single low eGFR or acute kidney lab does not establish an N18 code without a documented "
        "CKD diagnosis or chronicity of at least 3 months."
    ),
    "hand_labeled": (
        "Hand-labeled Gold-A chart. See the rationale in eval/gold/gold_a.json."
    ),
}

FAILURE_PRIORITY = (
    "cross_sentence_retraction",
    "combo_parent_uncoded",
    "hf_specificity",
    "acute_egfr",
)


def _round(value: Any, places: int = 6) -> Any:
    if isinstance(value, float):
        return round(value, places)
    if isinstance(value, list):
        return [_round(v, places) for v in value]
    if isinstance(value, dict):
        return {k: _round(v, places) for k, v in value.items()}
    return value


def _ensure_gold_b() -> None:
    if not (GOLD_DIR / "gold_b.jsonl").exists():
        write_corpus()


def _run_split(adapter: AuditAdapter, rows: list[tuple[dict, dict]], *, mode: str) -> dict[str, Any]:
    per_config: dict[str, dict] = {}
    for config in CONFIGS:
        chart_scores = []
        costs, latencies = [], []
        credits, hits, uncached = [], [], []
        changes = 0
        modes = []
        details = []
        for label, profile in rows:
            audit = adapter.audit(profile, config, mode=mode)
            scored = score_chart(label, audit["items"])
            scored["latency_s"] = audit.get("latency_s")
            scored["cost_usd"] = audit.get("cost_usd")
            scored["mode"] = audit.get("mode")
            chart_scores.append(scored)
            costs.append(float(audit.get("cost_usd") or 0))
            latencies.append(float(audit.get("latency_s") or 0))
            credits.append(float(audit.get("tavily_credits") or 0))
            hits.append(float(audit.get("tavily_cache_hits") or 0))
            # Advanced Tavily search is 2 credits. Mock runs count cache hits instead.
            uncached.append(float(audit.get("tavily_would_search") or 0) * 2.0)
            changes += int(audit.get("guideline_decision_changes") or 0)
            modes.append(audit.get("mode"))
            details.append({
                "patient_id": scored["patient_id"],
                "scenario": scored["scenario"],
                "tp": scored["tp"],
                "fp": scored["fp"],
                "fn": scored["fn"],
                "shown_codes": scored["shown_codes"],
                "gold_codes": scored["gold_codes"],
                "review_items": scored["review_items"],
                "latency_s": scored["latency_s"],
                "cost_usd": scored["cost_usd"],
                "mode": scored["mode"],
                "degraded_reasons": list(audit.get("degraded_reasons") or []),
            })
        summary = aggregate(
            chart_scores,
            costs=costs,
            latencies=latencies,
            tavily_credits=credits,
            tavily_cache_hits=hits,
            tavily_uncached=uncached,
            guideline_changes=changes,
        )
        summary["id"] = config
        summary["label"] = CONFIG_LABELS[config]
        summary["latency_scope"] = "local_offline" if set(modes) <= {"mock", "rules"} else "mixed"
        summary["cost_basis"] = (
            "none"
            if config in {"C0", "C0+"}
            else "estimated_from_prompt_chars_x_catalog_price"
        )
        if any(m == "live" for m in modes):
            summary["cost_basis"] = "token_factory_usage_x_catalog_price"
            summary["latency_scope"] = "live_wall_clock"
        per_config[config] = {"summary": summary, "charts": details, "scores": chart_scores}
    return per_config


def _stability(adapter: AuditAdapter, rows: list[tuple[dict, dict]], *, mode: str) -> dict[str, Any]:
    first = []
    second = []
    for label, profile in rows:
        a = adapter.audit(profile, "C4", mode=mode)
        b = adapter.audit(profile, "C4", mode=mode)
        first.append({(it["code"], it["decision"]) for it in a["items"] if it.get("shown_to_coder")})
        second.append({(it["code"], it["decision"]) for it in b["items"] if it.get("shown_to_coder")})
    agree = sum(1 for a, b in zip(first, second) if a == b)
    n = len(first) or 1
    mock = mode != "live" and not os.environ.get("NEBIUS_API_KEY")
    return {
        "config": "C4",
        "runs": 2,
        "charts": len(first),
        "decision_agreement": agree / n,
        "charts_identical": agree,
        "note": (
            "Mock C4 is the recorded cascade, which is deterministic, so agreement is 1 by "
            "construction and is not a live-model stability measurement. Re-run with "
            "NEBIUS_API_KEY set for a live pair."
            if mock or mode == "mock" else
            "Two live C4 passes. Agreement is the share of charts with the same shown (code, decision) set."
        ),
    }


def _failures(scores: list[dict], *, dataset: str, limit: int = 5) -> list[dict]:
    rows = []
    for score in scores:
        scenario = score.get("scenario") or "hand_labeled"
        for code in score["fp"]:
            rows.append({
                "dataset": dataset,
                "patient_id": score["patient_id"],
                "split": score.get("split"),
                "scenario": scenario,
                "code": code,
                "failure_type": "false_positive",
                "configs_affected": ["C4"],
                "cause": CAUSES.get(scenario, "C4 still showed a code that is not in the gold gaps."),
            })
        for code in score["fn"]:
            rows.append({
                "dataset": dataset,
                "patient_id": score["patient_id"],
                "split": score.get("split"),
                "scenario": scenario,
                "code": code,
                "failure_type": "false_negative",
                "configs_affected": ["C4"],
                "cause": CAUSES.get(scenario, "C4 did not show a gold gap. If the rules never proposed it, every config misses it."),
            })
    rows.sort(key=lambda row: (
        FAILURE_PRIORITY.index(row["scenario"]) if row["scenario"] in FAILURE_PRIORITY else len(FAILURE_PRIORITY),
        row["failure_type"],
        row["patient_id"] or "",
        row["code"],
    ))
    picked = []
    seen = set()
    for row in rows:
        key = (row["scenario"], row["failure_type"], row["code"])
        if key in seen:
            continue
        seen.add(key)
        picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def _comparisons(configs: list[dict]) -> dict[str, Any]:
    by_id = {c["id"]: c for c in configs}
    base = by_id["C0+"]
    out = {}
    for cid in ("C1", "C2", "C3", "C4"):
        cur = by_id[cid]
        base_burden = base["review_burden_per_chart"] or 0
        reduction = None
        if base_burden:
            reduction = (base_burden - cur["review_burden_per_chart"]) / base_burden
        out[cid] = {
            "review_burden_per_chart_from": base_burden,
            "review_burden_per_chart_to": cur["review_burden_per_chart"],
            "review_burden_reduction_vs_c0_plus": reduction,
            "recall_from": base["recall"],
            "recall_to": cur["recall"],
            "precision_from": base["precision"],
            "precision_to": cur["precision"],
        }
    return {"versus": "C0+", "configs": out}


def _markdown(summary: dict) -> str:
    lines = []
    lines.append("# CodeSentinel eval results")
    lines.append("")
    lines.append(summary["mode_detail"])
    lines.append("")
    ds = summary["dataset"]
    lines.append(
        f"Dataset **{ds['name']}** split `{ds['split']}`: {ds['n_charts']} synthetic FHIR charts, "
        f"seed {ds.get('seed')}. Text source: {ds.get('text_source')}."
    )
    lines.append("")
    lines.append("## Configuration table")
    lines.append("")
    lines.append("| Config | Precision | Recall | F1 | TP | FP | FN | Review items / chart | FP / chart | Hard-negative pass | Abstain rate | P50 s | P95 s | $ / chart | Tavily credits / chart |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for cfg in summary["configs"]:
        def fmt(value: Any, digits: int = 3) -> str:
            if value is None:
                return "—"
            if isinstance(value, float):
                return f"{value:.{digits}f}"
            return str(value)
        hard = (cfg.get("hard_negative_pass_rate") or {}).get("overall")
        lines.append(
            "| {id} | {p} | {r} | {f} | {tp} | {fp} | {fn} | {rev} | {fpc} | {hard} | {abs_} | {p50} | {p95} | {cost} | {tav} |".format(
                id=cfg["id"],
                p=fmt(cfg["precision"]),
                r=fmt(cfg["recall"]),
                f=fmt(cfg["f1"]),
                tp=cfg["tp"], fp=cfg["fp"], fn=cfg["fn"],
                rev=fmt(cfg["review_burden_per_chart"]),
                fpc=fmt(cfg["false_positives_per_chart"]),
                hard=fmt(hard),
                abs_=fmt(cfg["abstain_rate"]),
                p50=fmt(cfg["latency_p50_s"], 4),
                p95=fmt(cfg["latency_p95_s"], 4),
                cost=fmt(cfg["cost_per_chart_usd"], 5),
                tav=fmt(cfg["tavily_credits_per_chart"], 2),
            )
        )
    lines.append("")
    lines.append("C0 is the approved bucket only. C0+ is approved plus review, which is the queue a coder actually sees. The headline comparison is C4 against C0+.")
    lines.append("")
    lines.append("Latency on a mock run is local Python time. It is not Token Factory latency. Cost on a mock run is `characters/4` times the public catalog price and is labeled as an estimate.")
    lines.append("")
    lines.append("## Review burden vs recall")
    lines.append("")
    lines.append("| Config | Recall | Review items / chart | $ / chart |")
    lines.append("|---|---:|---:|---:|")
    for row in summary["chart_series"]:
        lines.append(
            f"| {row['config']} | {row['recall'] if row['recall'] is not None else '—'} | "
            f"{row['review_burden_per_chart']:.3f} | {row['cost_per_chart_usd']:.5f} |"
        )
    lines.append("")
    comp = summary["comparisons"]["configs"].get("C4") or {}
    red = comp.get("review_burden_reduction_vs_c0_plus")
    if red is not None:
        lines.append(
            f"C4 review burden is {red:.1%} lower than C0+ on this split "
            f"({comp.get('review_burden_per_chart_from'):.3f} → {comp.get('review_burden_per_chart_to'):.3f} items per chart). "
            f"Recall {comp.get('recall_from')} → {comp.get('recall_to')}."
        )
        lines.append("")
    lines.append("## Hard-negative pass rate (C4)")
    lines.append("")
    c4 = next(c for c in summary["configs"] if c["id"] == "C4")
    lines.append("| Category | Pass | Total | Rate |")
    lines.append("|---|---:|---:|---:|")
    for cat, info in (c4["hard_negative_pass_rate"]["by_category"] or {}).items():
        rate = info["rate"]
        lines.append(f"| {cat} | {info['pass']} | {info['total']} | {rate:.3f} |")
    lines.append("")
    lines.append("## Known failures")
    lines.append("")
    lines.append("These are misses of mock C4 (the real cascade with the recorded client) on the held-out split, plus Gold-A misses. They are not a claim about live Nemotron.")
    lines.append("")
    if not summary["known_failures"]:
        lines.append("None recorded.")
    for fail in summary["known_failures"]:
        lines.append(
            f"- **{fail['patient_id']}** ({fail['dataset']}, {fail['scenario']}) "
            f"{fail['failure_type']} `{fail['code']}`. {fail['cause']}"
        )
    lines.append("")
    counts = summary["known_failure_counts"]
    lines.append(
        f"C4 on {counts['dataset']}: {counts['false_positives']} false-positive codes and "
        f"{counts['false_negatives']} false-negative codes across {counts['charts']} charts. "
        f"The list above is a diverse sample, not every miss."
    )
    lines.append("")
    lines.append("## Unsupported existing codes")
    lines.append("")
    lines.append("| Config | TP | FP | FN | Precision | Recall |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for cfg in summary["configs"]:
        u = cfg["unsupported_existing"]
        lines.append(
            f"| {cfg['id']} | {u['tp']} | {u['fp']} | {u['fn']} | {u['precision']} | {u['recall']} |"
        )
    lines.append("")
    lines.append("This task is separate from gap precision and recall. C0 and C0+ do not run the two-way pass.")
    lines.append("")
    lines.append("## Degraded runs")
    lines.append("")
    lines.extend(format_degraded_section(summary.get("degraded_reasons") or {}))
    lines.append("")
    lines.append("## Guideline decisions")
    lines.append("")
    lines.append("| Config | Decisions changed by the guideline step | Unsupported-claim drops |")
    lines.append("|---|---:|---:|")
    for cfg in summary["configs"]:
        lines.append(f"| {cfg['id']} | {cfg['guideline_decision_changes']} | {cfg['unsupported_claim_drops']} |")
    lines.append("")
    probe = summary["guardrail_probe"]
    lines.append(
        f"Quote-verifier probe (`{probe['name']}`): {'passed' if probe['passed'] else 'FAILED'}. "
        "A candidate whose only quote is absent from the chart is dropped. "
        "Rules candidates in this corpus quote the chart, so unsupported-claim drops on the split can be zero while the probe still passes."
    )
    lines.append("")
    lines.append("## MTSamples review pile")
    lines.append("")
    mt = summary["mtsamples"]
    lines.append(mt["note"])
    lines.append("")
    if mt.get("status") == "measured":
        lines.append(
            f"Measured rules review items: **{mt['rules_review_items']}**. "
            f"Still shown after offline C4: **{mt['c4_review_items_still_shown']}**. "
            f"Removed: **{mt['review_items_removed']}** "
            f"({mt['review_pile_reduction_vs_measured']:.1%} of the measured review pile)."
        )
        lines.append("")
        lines.append(
            f"Historical docstring baseline: {mt['documented_baseline_review_items']} review items. "
            f"PYTHONHASHSEED={mt.get('pythonhashseed')!s}. "
            "Approved items in this run: "
            f"{mt['rules_approved_items']} rules, {mt['c4_approved_items_still_shown']} still shown. "
            "Notes processed: "
            f"{mt['notes_processed']}. Errors: {mt['errors']}."
        )
        lines.append("")
        lines.append("Accuracy is not claimed.")
    else:
        lines.append(f"Status: {mt.get('status')}. Historical baseline remains {mt['documented_baseline_review_items']} review items. No reduced count is reported.")
    lines.append("")
    lines.append("## Stability")
    lines.append("")
    stab = summary["stability"]
    lines.append(f"C4 decision agreement across {stab['runs']} passes: {stab['decision_agreement']}. {stab['note']}")
    lines.append("")
    lines.append("## What not to claim")
    lines.append("")
    lines.append("- Do not cite mock precision, recall, or dollars as Nemotron or Tavily measurements.")
    lines.append("- Do not cite MTSamples for accuracy.")
    lines.append("- Gold-A is 10 hand labels and is not the headline benchmark. A human coder has not signed those labels yet (`review_status` in `eval/gold/gold_a.json`).")
    lines.append("- Gold-B labels were written by construction, by the same author as the offline policy. The policy does not read the labels, but it was designed around the same phenomena. Held-out means the test charts were not used to edit prompts; it does not mean an independent model.")
    lines.append("")
    return "\n".join(lines) + "\n"


def degraded_report(per_config: dict) -> dict[str, Any]:
    """Charts that fell back, and the unique reason strings. No prompts or keys."""
    charts = 0
    reasons: list[str] = []
    seen: set[str] = set()
    by_config: dict[str, int] = {}
    for cid, payload in per_config.items():
        count = 0
        for chart in payload.get("charts") or []:
            chart_reasons = [str(item) for item in (chart.get("degraded_reasons") or []) if str(item).strip()]
            if chart.get("mode") == "degraded" or chart_reasons:
                charts += 1
                count += 1
                for reason in chart_reasons:
                    if reason not in seen:
                        seen.add(reason)
                        reasons.append(reason)
        by_config[str(cid)] = count
    return {"charts": charts, "reasons": reasons, "by_config": by_config}


def format_degraded_section(report: dict) -> list[str]:
    charts = int(report.get("charts") or 0)
    reasons = [str(item) for item in (report.get("reasons") or []) if str(item).strip()]
    lines = [f"Charts that fell back to the recorded client: {charts}."]
    if not reasons:
        lines.append("No degraded reasons recorded.")
        return lines
    lines.append("Reasons:")
    for reason in reasons:
        lines.append(f"- {reason}")
    return lines


def _dataset_block(name: str, split: str, n: int) -> dict[str, Any]:
    manifest_path = GOLD_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    return {
        "name": name,
        "split": split,
        "n_charts": n,
        "seed": manifest.get("seed"),
        "text_source": manifest.get("text_source"),
        "not_model_authored": manifest.get("not_model_authored"),
        "label_sha256": manifest.get("label_sha256"),
        "splits": manifest.get("splits"),
    }


def run_eval(split: str = "test", *, mtsamples: str | None = None, mode: str = "auto",
             skip_gold_a: bool = False, skip_mtsamples: bool = False) -> dict[str, Any]:
    if mode == "live":
        missing = [
            name for name in ("NEBIUS_API_KEY", "TAVILY_API_KEY") if not os.environ.get(name)
        ]
        if missing:
            raise SystemExit(
                "mode=live needs both NEBIUS_API_KEY and TAVILY_API_KEY "
                f"({', '.join(missing)} unset). This command will not relabel a mock run as live."
            )
    _ensure_gold_b()
    adapter = AuditAdapter()
    rows = iter_gold_b(split)
    print(f"Gold-B {split}: {len(rows)} charts")
    per_config = _run_split(adapter, rows, mode=mode)
    configs = [_round(per_config[cid]["summary"]) for cid in CONFIGS]
    failures = _failures(per_config["C4"]["scores"], dataset="gold-b")
    counts = {
        "dataset": f"gold-b:{split}",
        "charts": len(rows),
        "false_positives": sum(len(s["fp"]) for s in per_config["C4"]["scores"]),
        "false_negatives": sum(len(s["fn"]) for s in per_config["C4"]["scores"]),
    }
    if not skip_gold_a:
        gold_a_rows = load_gold_a()
        print(f"Gold-A: {len(gold_a_rows)} charts")
        gold_a = _run_split(adapter, gold_a_rows, mode=mode)
        for extra in _failures(gold_a["C4"]["scores"], dataset="gold-a", limit=2):
            if len(failures) >= 7:
                break
            failures.append(extra)
        _write_dataset_results("gold_a", gold_a, dataset_name="gold-a", split_name="gold-a")
    else:
        gold_a = None

    if skip_mtsamples:
        mt = pending_mtsamples("skipped")
    elif mtsamples:
        print(f"MTSamples: {mtsamples}")
        mt = measure_mtsamples(mtsamples, adapter)
    else:
        env_path = os.environ.get("MTSAMPLES_CSV")
        if env_path and Path(env_path).exists():
            print(f"MTSamples: {env_path}")
            mt = measure_mtsamples(env_path, adapter)
        else:
            mt = pending_mtsamples("MTSAMPLES_CSV is unset and --mtsamples was not passed")

    modes = sorted({cfg["summary"]["latency_scope"] for cfg in per_config.values()})
    if mode == "live":
        run_mode = "live"
    elif mode == "mock" or not os.environ.get("NEBIUS_API_KEY") or os.environ.get("EVAL_OFFLINE") == "1":
        run_mode = "mock"
    else:
        run_mode = "live"
    if run_mode == "live":
        detail = (
            "C0 and C0+ stay on the rules pipeline. C1–C4 ran through the real cascade on Token Factory. "
            "Nemotron responses were recorded under eval/fixtures/nemotron. "
            "Guideline citations come from curated sections of the official FY2026 guidelines. "
            "Tavily confirms that source and is the fallback for a code without a curated section. "
            "Replay those Nemotron completions with CODESENTINEL_REPLAY=1."
        )
    else:
        detail = (
            "NEBIUS_API_KEY and TAVILY_API_KEY were unset, or the run was forced to mock. "
            "C0 and C0+ are the rules pipeline. C1–C3 decisions come from the deterministic policy in "
            "eval/policy.py. C4 decisions come from the CodeSentinel cascade in mock mode: the recorded "
            "client applies the same chart-text instructions as the live prompts (negation, family history, "
            "screening, and medication-only). Curated FY2026 sections are cited for the codes they cover; "
            "other guideline quotes are labeled fixtures. Do not cite the mock C4 row as live model quality."
        )
    summary = {
        "schema": "codesentinel.eval.summary.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": run_mode,
        "mode_detail": detail,
        "keys": {
            "nebius": bool(os.environ.get("NEBIUS_API_KEY")),
            "tavily": bool(os.environ.get("TAVILY_API_KEY")),
        },
        "backend": "codesentinel.nemotron" if adapter.backend else None,
        "live_errors": adapter.live_errors,
        "dataset": _dataset_block("gold-b", split, len(rows)),
        "configs": configs,
        "chart_series": [
            {
                "config": c["id"],
                "recall": c["recall"],
                "precision": c["precision"],
                "review_burden_per_chart": c["review_burden_per_chart"],
                "cost_per_chart_usd": c["cost_per_chart_usd"],
            }
            for c in configs
        ],
        "comparisons": _round(_comparisons(configs)),
        "degraded_reasons": degraded_report(per_config),
        "known_failures": failures,
        "known_failure_counts": counts,
        "stability": _round(_stability(adapter, rows, mode="mock" if run_mode == "mock" else mode)),
        "guardrail_probe": guardrail_probe(adapter),
        "mtsamples": mt,
        "latency_note": modes,
        "limitations": [
            "Mock C1–C3 is an offline policy. Mock C4 is the real cascade with the recorded client applying the live prompt instructions to chart text, not a live Nemotron measurement. Live C1–C4 require both API keys and record responses for replay.",
            "Catalog prices are the 2026-10-24 Token Factory public list. Mock cost uses a 4-characters-per-token estimate.",
            "Mock latency is local and is not a P50 from Token Factory.",
            "Gold-B text is a hand paraphrase bank, not a non-Nemotron model sample. A live paraphraser was not run because NEBIUS_API_KEY was unset.",
            "Labels and the offline policy were written by the same author. The policy function does not take gold labels as input.",
            "Gold-A is pending coder sign-off.",
            "MTSamples accuracy is not measured.",
        ],
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _write_config_files(per_config, prefix="")
    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    results_md = Path(__file__).resolve().parent / "RESULTS.md"
    results_md.write_text(_markdown(summary), encoding="utf-8")
    print(f"Wrote {RESULTS_DIR / 'summary.json'}")
    print(f"Wrote {results_md}")
    return summary


def _write_config_files(per_config: dict, *, prefix: str) -> None:
    for cid, payload in per_config.items():
        name = prefix + cid.replace("+", "_plus") + ".json"
        body = {"summary": _round(payload["summary"]), "charts": payload["charts"]}
        (RESULTS_DIR / name).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def _write_dataset_results(stem: str, per_config: dict, *, dataset_name: str, split_name: str) -> None:
    configs = [_round(per_config[cid]["summary"]) for cid in CONFIGS]
    body = {
        "schema": "codesentinel.eval.summary.v1",
        "dataset": {"name": dataset_name, "split": split_name, "n_charts": configs[0]["n_charts"] if configs else 0},
        "configs": configs,
        "chart_series": [
            {
                "config": c["id"],
                "recall": c["recall"],
                "precision": c["precision"],
                "review_burden_per_chart": c["review_burden_per_chart"],
                "cost_per_chart_usd": c["cost_per_chart_usd"],
            }
            for c in configs
        ],
        "degraded_reasons": degraded_report(per_config),
    }
    (RESULTS_DIR / f"{stem}_summary.json").write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    _write_config_files(per_config, prefix=f"{stem}_")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CodeSentinel eval harness")
    parser.add_argument("--split", default="test", choices=["train", "dev", "test", "all"])
    parser.add_argument("--mtsamples", default=None, help="Path to mtsamples.csv. The CSV is not committed.")
    parser.add_argument("--skip-mtsamples", action="store_true")
    parser.add_argument("--skip-gold-a", action="store_true")
    parser.add_argument("--mode", default="auto", choices=["auto", "mock", "live"])
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    if args.generate_only:
        print(json.dumps(write_corpus(), indent=2))
        return
    split = None if args.split == "all" else args.split
    # iter_gold_b(None) returns every split. The published file is still named summary.json.
    run_eval(
        split if split is not None else "test",
        mtsamples=args.mtsamples,
        mode=args.mode,
        skip_gold_a=args.skip_gold_a,
        skip_mtsamples=args.skip_mtsamples,
    )
    if args.split == "all":
        _ensure_gold_b()
        adapter = AuditAdapter()
        per_config = _run_split(adapter, iter_gold_b(None), mode=args.mode)
        _write_dataset_results("gold_b_all", per_config, dataset_name="gold-b", split_name="all")


if __name__ == "__main__":
    main()
