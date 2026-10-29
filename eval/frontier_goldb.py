"""Cascade frontier (v9), the README image docs/assets/cascade-frontier.{svg,png}.

Left: Gold-B review rows and false positives per chart by cascade depth (C0+, C1, C2, C3 and
the shipped adaptive default C4a; fixed C4 is in the full tables). Right: Synthea held-out cost
per chart, full cascade C4 against adaptive C4a. Every plotted value is read from the two
summary files. Needs matplotlib (not a runtime dependency).

  python -m eval.frontier_goldb eval/results/live-v9/summary.json \
      eval/results/synthea/heldout-v9/summary.json docs/assets/cascade-frontier
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

INK = "#08382d"
LIME = "#dff25a"
PAPER = "#f6f1e6"
MUTED = "#6b7f78"
RED = "#b23a2a"


def load(path: str) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return {c["id"]: c for c in d["configs"]} | {"_dataset": d.get("dataset", {})}


def main(goldb_path: str, synthea_path: str, out_stem: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    gb = load(goldb_path)
    sy = load(synthea_path)

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig = plt.figure(figsize=(11.5, 4.9), dpi=150)
    fig.patch.set_facecolor(PAPER)
    gs = GridSpec(1, 2, width_ratios=[2.4, 1], wspace=0.30, left=0.06, right=0.975, top=0.84, bottom=0.14)
    ax = fig.add_subplot(gs[0])
    bx = fig.add_subplot(gs[1])
    for a in (ax, bx):
        a.set_facecolor(PAPER)
        for spine in ("top", "right"):
            a.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            a.spines[spine].set_color(INK)
        a.tick_params(colors=INK)

    ids = ["C0+", "C1", "C2", "C3", "C4a"]
    cfgs = [gb[c] for c in ids]
    xs = list(range(len(ids)))
    rows = [c["review_burden_per_chart"] for c in cfgs]
    fp = [c["false_positives_per_chart"] for c in cfgs]
    ticks = ["Rules only\n(C0+)", "+ Lightning triage\n+ quote verifier (C1)", "+ Super\n(C2)", "+ Tavily\nguidelines (C3)", "+ Ultra, adaptive\n(C4a, default)"]

    ax.plot(xs, rows, color=INK, linewidth=1.6, zorder=2)
    ax.scatter(xs[:-1], rows[:-1], s=90, color=INK, zorder=3, label="Rows a coder reviews per chart")
    ax.plot(xs, fp, color=RED, linewidth=1.4, linestyle="--", zorder=2)
    ax.scatter(xs[:-1], fp[:-1], s=90, color=LIME, edgecolor=RED, linewidth=1.4, zorder=3, label="False positives per chart")
    a = gb["C4a"]
    ax.scatter([xs[-1]], [rows[-1]], s=300, marker="*", color=LIME, edgecolor=INK, linewidth=1.6, zorder=4, label="Shipped default")
    ax.scatter([xs[-1]], [fp[-1]], s=170, marker="*", color=LIME, edgecolor=RED, linewidth=1.4, zorder=4)

    for x, y, c in zip(xs, rows, cfgs):
        ax.annotate(f"{y:.2f}\nP {c['precision']:.3f}", (x, y), textcoords="offset points", xytext=(0, 14), ha="center",
                    color=INK, fontsize=8.8, fontweight="bold", linespacing=1.15)
    for x, y in zip(xs, fp):
        ax.annotate(f"{y:.2f}", (x, y), textcoords="offset points", xytext=(0, -16), ha="center", color=RED, fontsize=8.5)

    base = gb["C0+"]
    cut = 100 * (1 - a["review_burden_per_chart"] / base["review_burden_per_chart"])
    hn = a["hard_negative_pass_rate"]
    ue = a["unsupported_existing"]
    ax.text(
        0.99, 0.985,
        f"Review queue {base['review_burden_per_chart']:.2f} -> {a['review_burden_per_chart']:.2f} rows/chart ({cut:.1f}% less)\n"
        f"False positives {base['false_positives_per_chart']:.2f} -> {a['false_positives_per_chart']:.2f} per chart\n"
        f"Hard negatives {hn['pass']}/{hn['total']} passed, unsupported codes {ue['tp']}/{ue['tp']+ue['fn']} caught\n"
        f"Default escalates {a['escalated_fraction']*100:.0f}% of charts; ${a['cost_per_chart_usd']:.5f}/chart",
        transform=ax.transAxes, va="top", ha="right", fontsize=8.6, color=INK, family="DejaVu Sans Mono",
        bbox={"facecolor": PAPER, "edgecolor": INK, "linewidth": 0.6, "boxstyle": "round,pad=0.4"},
    )
    ax.set_xticks(xs)
    ax.set_xticklabels(ticks, fontsize=8.6, color=INK)
    ax.set_xlim(-0.4, len(ids) - 0.6)
    ax.set_ylim(-0.32, max(rows) * 1.5)
    ax.set_ylabel("Per chart", color=INK)
    n = gb["_dataset"].get("n_charts")
    ax.set_title(f"Coder's queue and false positives by cascade depth ({n} hard-negative charts, live)", color=INK,
                 fontsize=10.5, fontweight="bold", loc="left")
    ax.legend(frameon=False, fontsize=8.5, loc="center", bbox_to_anchor=(0.72, 0.27), labelcolor=INK)

    full = sy["C4"]
    adap = sy["C4a"]
    costs = [full["cost_per_chart_usd"] * 1000, adap["cost_per_chart_usd"] * 1000]
    bars = bx.bar(["C4 full cascade", "C4a adaptive"], costs, color=[MUTED, LIME], edgecolor=INK, linewidth=1.2, width=0.58)
    saving = 100 * (1 - adap["cost_per_chart_usd"] / full["cost_per_chart_usd"])
    for b, v in zip(bars, costs):
        bx.text(b.get_x() + b.get_width() / 2, v + max(costs) * 0.03, f"${v/1000:.5f}", ha="center", va="bottom",
                color=INK, fontsize=9, family="DejaVu Sans Mono")
    bx.text(0.5, 0.97, f"{saving:.0f}% cheaper\nescalates {adap['escalated_fraction']*100:.0f}% of charts\nP {adap['precision']:.3f} vs {full['precision']:.3f}\nR {adap['recall']:.3f} vs {full['recall']:.3f}",
            transform=bx.transAxes, ha="center", va="top", color=INK, fontsize=9, fontweight="bold", linespacing=1.3)
    bx.set_ylim(0, max(costs) * 1.55)
    bx.set_ylabel("Cost per chart (USD x 1,000)", color=INK)
    ns = sy["_dataset"].get("charts") or sy["_dataset"].get("n_charts")
    bx.set_title(f"Adaptive depth,\n{ns} Synthea held-out charts", color=INK, fontsize=10.5, fontweight="bold", loc="left")

    for ext in ("svg", "png"):
        fig.savefig(f"{out_stem}.{ext}", facecolor=PAPER, metadata={"Date": None} if ext == "svg" else None)
    print(f"Wrote {out_stem}.svg and {out_stem}.png")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
