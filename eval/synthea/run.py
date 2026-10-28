"""Run C0..C4 on the Synthea gap benchmark and write summary.json and RESULTS.md.

Charts are read with the production FHIR adapter (fhir_adapter.FHIRPatientExtractor)
through an in-memory FHIR client, so the profile is what the server path would build.

Usage:
  python -m eval.synthea.run --bench BENCH_DIR --out OUT_DIR --mode live [--limit 20] [--workers 4]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.adapter import AuditAdapter  # noqa: E402
from eval.lexicon import CONFIGS  # noqa: E402
from eval import run as eval_run  # noqa: E402


class BundleFHIRClient:
    """Answers the FHIRPatientExtractor's reads from one FHIR collection Bundle."""

    def __init__(self, bundle: dict):
        self._by_type: dict[str, list[dict]] = {}
        for entry in bundle.get("entry") or []:
            res = entry.get("resource") or {}
            self._by_type.setdefault(res.get("resourceType") or "", []).append(res)

    def get(self, resource_type: str, params: dict | None = None, resource_id: str | None = None) -> dict:
        for res in self._by_type.get(resource_type, []):
            if resource_id is None or res.get("id") == resource_id:
                return res
        raise KeyError(f"{resource_type}/{resource_id}")

    def search(self, resource_type: str, params: dict) -> list[dict]:
        found = list(self._by_type.get(resource_type, []))
        category = (params or {}).get("category")
        if category:
            found = [
                r for r in found
                if any(c.get("code") == category for cat in r.get("category") or [] for c in cat.get("coding") or [])
            ]
        return found


SPLIT_FILE = Path(__file__).resolve().parent / "split.json"


def split_ids(split: str) -> set[str] | None:
    """Patient ids for a split in eval/synthea/split.json. "all" returns None."""
    if split == "all":
        return None
    data = json.loads(SPLIT_FILE.read_text(encoding="utf-8"))
    return set(data[split])


def load_rows(bench: Path, limit: int | None, seed: int, split: str = "all") -> list[tuple[dict, dict]]:
    import logging
    from fhir_adapter import FHIRPatientExtractor

    logging.getLogger("gapdetect.fhir").setLevel(logging.ERROR)
    labels = [json.loads(line) for line in (bench / "labels.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    keep = split_ids(split)
    if keep is not None:
        labels = [label for label in labels if label["patient_id"] in keep]
    if limit:
        labels = random.Random(seed).sample(labels, limit)
        labels.sort(key=lambda l: l["patient_id"])
    rows = []
    for label in labels:
        bundle = json.loads((bench / "bundles" / f"{label['patient_id']}.json").read_text(encoding="utf-8"))
        profile = FHIRPatientExtractor(BundleFHIRClient(bundle)).extract_patient(label["patient_id"])
        rows.append((label, profile))
    return rows


def _normalize(items: list[dict], label: dict) -> list[dict]:
    """Score at the code the label uses, and drop neutral rows.

    A shown code that is a dotted parent of a label code counts as that code
    (CodeSentinel writes N18.3; the billable FY2027 code is N18.30). Rows for removed
    codes the 12-month chart cannot support are neutral: not a hit, miss, or false positive.
    """
    known = set(label.get("truth_codes") or []) | {g["code"] for g in label.get("expected_gaps") or []}
    known |= {g["code"] for g in label.get("neutral_codes") or []}
    neutral = {g["code"] for g in label.get("neutral_codes") or []}
    out = []
    for item in items:
        code = (item.get("code") or "").upper()
        if "." in code and code not in known:
            children = sorted(k for k in known if k.startswith(code) and len(k) == len(code) + 1)
            if len(children) == 1:
                item = dict(item, code=children[0], emitted_code=code)
                code = children[0]
        if code in neutral and item.get("gap_type") != "unsupported_existing":
            continue
        out.append(item)
    return out


class CachedAdapter:
    def __init__(self, cache: dict):
        self.cache = cache

    def audit(self, profile: dict, config: str, *, mode: str = "auto") -> dict[str, Any]:
        return self.cache[(profile["fhir_patient_id"], config)]


def run_all(rows, mode: str, workers: int) -> tuple[dict, list[str]]:
    adapter = AuditAdapter()
    labels = {label["patient_id"]: label for label, _p in rows}
    cache: dict = {}

    def one(args):
        (label, profile), config = args
        return label["patient_id"], config, adapter.audit(profile, config, mode=mode)

    jobs = [((label, profile), config) for config in CONFIGS for (label, profile) in rows]
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (pid, config, payload) in enumerate(pool.map(one, jobs), 1):
            normalized = dict(payload)
            normalized["items"] = _normalize(list(payload.get("items") or []), labels[pid])
            cache[(pid, config)] = normalized
            if done % 25 == 0:
                print(f"{done}/{len(jobs)} audits, {time.time() - started:.0f}s", flush=True)
    return cache, adapter.live_errors


def _gap_burden(cache: dict, rows, config: str) -> float:
    total = 0
    for label, _p in rows:
        items = cache[(label["patient_id"], config)]["items"]
        total += sum(1 for it in items if it.get("shown_to_coder") and it.get("gap_type") != "unsupported_existing")
    return total / (len(rows) or 1)


def _per_code(per_config: dict, config: str) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = {}
    for score in per_config[config]["scores"]:
        for key in ("tp", "fn", "fp"):
            for code in score[key]:
                out.setdefault(code, Counter())[key] += 1
    return {code: dict(counts) for code, counts in sorted(out.items())}


def _gold_fate(cache: dict, rows, config: str) -> dict[str, int]:
    """What happened to each gold gap: the shown decision, or the drop reason."""
    fate: Counter = Counter()
    for label, _p in rows:
        items = cache[(label["patient_id"], config)]["items"]
        for gap in label["expected_gaps"]:
            match = [it for it in items if (it.get("code") or "").upper() == gap["code"] and it.get("gap_type") != "unsupported_existing"]
            shown = [it for it in match if it.get("shown_to_coder")]
            if shown:
                fate[f"shown:{shown[0].get('decision')}"] += 1
            elif match:
                fate[f"dropped:{match[0].get('assertion_status') or match[0].get('drop_reason') or 'unknown'}"] += 1
            else:
                fate["not_proposed"] += 1
    return dict(sorted(fate.items()))


def _fmt(value, digits=3):
    return eval_run._fmt(value, digits)


CAVEATS = [
    "Synthea notes are template text and rarely name chronic conditions, so most evidence is medications and labs. The cascade sends lab-only and medication-only rows to review instead of accepting them.",
    "The cascade prompt carries the first 40 structured lines of the chart, labs first, so medication lines can be cut on lab-heavy charts.",
    "Synthea sometimes records a diabetic kidney condition without a type 2 diabetes condition; the NLM map still maps it to an E11 code. Those charts rarely support an E11.22 gap, so most are neutral.",
    "Gold gaps are dominated by E78.5 and I10, which Synthea links to statins and antihypertensives. Diabetes, CKD and combination-code gaps are few, so their per-code numbers are anecdotal.",
    "Codes outside CodeSentinel's rules (for example I11.0, I13.0, N18.1, N18.2, E11.29, E11.40) stay billed and are never removed. The benchmark measures gap finding on the codes the rules cover.",
    "Labels are derived by code from Synthea's conditions; no certified coder reviewed them.",
]


def markdown(summary: dict) -> str:
    d = summary["dataset"]
    lines = [
        "# CodeSentinel eval results: Synthea gap benchmark",
        "",
        f"Run: {summary['mode']}, commit `{summary.get('commit', '')}`, finished {summary['generated_local']}. "
        "Raw numbers: [summary.json](summary.json).",
        "",
        f"{d['charts']} Synthea patients (Synthea {d['synthea_version']}, seed {d['seed']}, {d['state']}, ages {d['ages']}, "
        f"reference date {d['reference_date']}). Conditions are Synthea's, mapped to ICD-10-CM with the NLM SNOMED CT to "
        "ICD-10-CM map and the FY 2027 combination-code guidelines. "
        f"A seeded {int(d['remove_fraction'] * 100)}% of the in-scope billed codes were removed from each chart's condition list; "
        f"the removed codes the 12-month chart still supports are the gold gaps ({d['expected_gaps']} gaps on {d['charts_with_gaps']} charts). "
        f"{d['neutral_codes']} removed codes with no support in the chart are neutral. "
        f"{d['unsupported_existing']} charts carry one billed code Synthea says the patient does not have.",
        "",
        "| Config | Precision | Recall | F1 | Review items / chart | Gap rows / chart | $ / chart | P50 / P95 s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for c in summary["configs"]:
        cost = "0" if c["id"] in {"C0", "C0+"} else _fmt(c.get("cost_per_chart_usd"), 5)
        lat = "—" if c["id"] in {"C0", "C0+"} else f"{_fmt(c.get('latency_p50_s'), 1)} / {_fmt(c.get('latency_p95_s'), 1)}"
        lines.append(
            f"| {c['id']} {c['label']} | {_fmt(c.get('precision'))} | {_fmt(c.get('recall'))} | {_fmt(c.get('f1'))} | "
            f"{_fmt(c.get('review_burden_per_chart'), 2)} | {_fmt(c.get('gap_rows_per_chart'), 2)} | {cost} | {lat} |"
        )
    lines += [
        "",
        "Review items count every row sent to a coder, including unsupported-existing flags on billed codes. Gap rows count only proposed additions.",
        "",
        "## 95% intervals (Wilson)",
        "",
        "| Config | Precision CI | Recall CI | TP / FP / FN |",
        "|---|---|---|---|",
    ]
    for c in summary["configs"]:
        pci = c.get("precision_ci95") or [None, None]
        rci = c.get("recall_ci95") or [None, None]
        lines.append(f"| {c['id']} | {_fmt(pci[0])}–{_fmt(pci[1])} | {_fmt(rci[0])}–{_fmt(rci[1])} | {c['tp']} / {c['fp']} / {c['fn']} |")
    lines += ["", "## Per code (C0+ rules vs C4 cascade)", "", "| Code | Gold gaps | C0+ TP / FP | C4 TP / FP |", "|---|---:|---:|---:|"]
    pc0, pc4 = summary["per_code"]["C0+"], summary["per_code"]["C4"]
    gold = summary["gold_by_code"]
    for code in sorted(set(pc0) | set(pc4) | set(gold)):
        a, b = pc0.get(code, {}), pc4.get(code, {})
        lines.append(f"| {code} | {gold.get(code, 0)} | {a.get('tp', 0)} / {a.get('fp', 0)} | {b.get('tp', 0)} / {b.get('fp', 0)} |")
    lines += ["", "## What happened to the gold gaps", "", "| Config | Outcome counts |", "|---|---|"]
    for cid, fate in summary.get("gold_gap_fate", {}).items():
        lines.append(f"| {cid} | " + ", ".join(f"{k} {v}" for k, v in fate.items()) + " |")
    c4 = [c for c in summary["configs"] if c["id"] == "C4"][0]
    u = c4.get("unsupported_existing") or {}
    lines += [
        "",
        "## Unsupported existing codes (C4)",
        "",
        f"Planted: {d['unsupported_existing']}. Flagged and planted: {u.get('tp')}. "
        f"Flagged on a billed code Synthea does have: {u.get('fp')}. Missed: {u.get('fn')}.",
        "",
        "## How the labels are made",
        "",
        "- Generator: Synthea, run offline with the command in `eval/synthea/README.md`. The author wrote no chart text, condition, lab or medication.",
        "- Mapping: `eval/synthea/snomed_icd10cm.csv`, one row per SNOMED concept with its NLM I-MAGIC source (SNOMED CT to ICD-10-CM Map release 20260901).",
        "- Combination codes: rules R1–R8 in `eval/synthea/build.py`, each citing the FY 2027 ICD-10-CM Official Guidelines or Tabular List.",
        "- Support: a removed code is a gold gap only if the chart window has a note naming it, a medication Synthea links to it, or a lab meeting a published criterion (`manifest.json`).",
        "- Scoring: a shown code that is the dotted parent of a gold code counts as that code (CodeSentinel writes `N18.3`; the FY 2027 billable code is `N18.30`).",
        "",
        "## Caveats",
        "",
    ]
    lines += [f"- {c}" for c in summary["caveats"]]
    if summary.get("live_errors"):
        lines += ["", "## Live errors", ""] + [f"- {e}" for e in summary["live_errors"][:20]]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", default="live", choices=["live", "mock"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--commit", default="")
    ap.add_argument("--split", default="all", choices=["all", "dev", "held_out"])
    args = ap.parse_args()
    if args.mode == "live" and not (os.environ.get("NEBIUS_API_KEY") and os.environ.get("TAVILY_API_KEY")):
        raise SystemExit("live mode needs NEBIUS_API_KEY and TAVILY_API_KEY")

    bench = Path(args.bench)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((bench / "manifest.json").read_text(encoding="utf-8"))
    rows = load_rows(bench, args.limit, args.seed, args.split)
    print(f"Synthea charts: {len(rows)}", flush=True)
    cache, errors = run_all(rows, args.mode, args.workers)
    per_config = eval_run._run_split(CachedAdapter(cache), rows, mode=args.mode)
    configs = []
    for cid in CONFIGS:
        summary = per_config[cid]["summary"]
        summary["gap_rows_per_chart"] = _gap_burden(cache, rows, cid)
        configs.append(eval_run._round(summary))
    labels = [label for label, _p in rows]
    gold_by_code = Counter(g["code"] for label in labels for g in label["expected_gaps"])
    summary = {
        "dataset": {
            "name": "synthea",
            "synthea_version": "v4.0.0",
            "seed": manifest["seed"],
            "state": "Texas",
            "ages": "45-90",
            "reference_date": manifest["reference_date"],
            "remove_fraction": manifest["remove_fraction"],
            "charts": len(rows),
            "subset": f"seeded sample of {args.limit}" if args.limit else "all",
            "split": args.split,
            "expected_gaps": sum(len(l["expected_gaps"]) for l in labels),
            "charts_with_gaps": sum(1 for l in labels if l["expected_gaps"]),
            "neutral_codes": sum(len(l["neutral_codes"]) for l in labels),
            "unsupported_existing": sum(len(l["unsupported_existing"]) for l in labels),
            "in_scope": manifest["in_scope"],
        },
        "mode": args.mode,
        "commit": args.commit,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_local": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "configs": configs,
        "gold_by_code": dict(sorted(gold_by_code.items())),
        "per_code": {cid: _per_code(per_config, cid) for cid in ("C0+", "C1", "C2", "C3", "C4")},
        "gold_gap_fate": {cid: _gold_fate(cache, rows, cid) for cid in CONFIGS},
        "degraded_reasons": eval_run.degraded_report(per_config),
        "live_errors": errors,
        "total_cost_usd": round(sum((c.get("cost_per_chart_usd") or 0) * len(rows) for c in configs), 4),
        "tavily_credits": round(sum((c.get("tavily_credits_per_chart") or 0) * len(rows) for c in configs), 1),
        "caveats": CAVEATS,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (out / "RESULTS.md").write_text(markdown(summary), encoding="utf-8")
    (out / "charts.json").write_text(json.dumps({cid: per_config[cid]["charts"] for cid in CONFIGS}) + "\n", encoding="utf-8")
    print(f"Wrote {out / 'summary.json'}  cost=${summary['total_cost_usd']} tavily={summary['tavily_credits']}", flush=True)


if __name__ == "__main__":
    main()
