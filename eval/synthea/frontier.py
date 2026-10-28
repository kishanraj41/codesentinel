"""Draw the cost/accuracy frontier: review items per chart vs $ per chart for C1..C4.

Needs matplotlib (not a runtime dependency):
  pip install matplotlib
  python -m eval.synthea.frontier eval/results/synthea/summary.json docs/assets/synthea-frontier
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

INK = "#08382d"
LIME = "#dff25a"
PAPER = "#f6f1e6"
MUTED = "#6b7f78"


def main(summary_path: str, out_stem: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in summary["configs"]}
    cascade = [by_id[c] for c in ("C1", "C2", "C3", "C4") if c in by_id]
    rules = by_id.get("C0+")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=150)
    fig.patch.set_facecolor(PAPER)
    ax.set_facecolor(PAPER)

    xs = [c["cost_per_chart_usd"] * 1000 for c in cascade]
    total = [c["review_burden_per_chart"] for c in cascade]
    gaps = [c["gap_rows_per_chart"] for c in cascade]
    ax.plot(xs, total, color=INK, linewidth=1.6, zorder=2)
    ax.scatter(xs, total, s=90, color=INK, zorder=3, label="All review rows (gaps + unsupported-code flags)")
    ax.plot(xs, gaps, color=INK, linewidth=1.2, linestyle="--", zorder=2)
    ax.scatter(xs, gaps, s=90, color=LIME, edgecolor=INK, linewidth=1.4, zorder=3, label="Gap rows only")
    for c, x, y, g in zip(cascade, xs, total, gaps):
        recall = c.get("recall")
        text = f"{c['id']}  recall {recall:.2f}" if recall is not None else c["id"]
        ax.annotate(text, (x, y), textcoords="offset points", xytext=(8, 8), color=INK, fontsize=10, fontweight="bold")
    if rules:
        ax.axhline(rules["gap_rows_per_chart"], color=MUTED, linewidth=1, linestyle=":")
        ax.annotate(
            f"C0+ rules queue, $0  (recall {rules['recall']:.2f})",
            (0, rules["gap_rows_per_chart"]), textcoords="offset points", xytext=(4, 5), color=MUTED, fontsize=9,
        )
    ax.set_xlabel("Token Factory cost per chart (USD x 1,000)", color=INK)
    ax.set_ylabel("Rows a coder reviews per chart", color=INK)
    d = summary["dataset"]
    ax.set_title(f"Cost vs review burden on {d['charts']} Synthea charts", color=INK, fontsize=13, fontweight="bold", loc="left")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(INK)
    ax.tick_params(colors=INK)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, fontsize=9, loc="lower right", labelcolor=INK)
    fig.tight_layout()
    for ext in ("svg", "png"):
        fig.savefig(f"{out_stem}.{ext}", facecolor=PAPER, metadata={"Date": None} if ext == "svg" else None)
    print(f"Wrote {out_stem}.svg and {out_stem}.png")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
