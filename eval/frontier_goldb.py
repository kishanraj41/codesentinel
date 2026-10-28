"""Gold-B frontier charts from eval/results/live/summary.json.

Two views of the same live run (needs matplotlib, not a runtime dependency):

  # README image: review rows and false positives per chart vs Token Factory cost,
  # C0+ to C3 plotted, C4 (+ Ultra) in the stats box.
  python -m eval.frontier_goldb eval/results/live/summary.json docs/assets/cascade-frontier

  # Precision vs review rows per chart for C0, C0+ and C1..C4.
  python -m eval.frontier_goldb eval/results/live/summary.json docs/assets/goldb-frontier --precision

Same style as eval/synthea/frontier.py. Every plotted value is read from summary.json.
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

PRECISION_ORDER = ("C0", "C0+", "C1", "C2", "C3", "C4")


def cascade(summary_path: str, out_stem: str) -> None:
    """Review rows and false positives per chart vs cost, C0+ to C3; C4 in the stats box."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in summary["configs"]}
    # Plot the steps that change the queue; C4 (Ultra: conflicts and physician queries) is
    # reported in the stats box so the line does not end in a flat cost-only segment.
    order = ["C0+", "C1", "C2", "C3"]
    cfgs = [by_id[c] for c in order if c in by_id]
    c4 = by_id.get("C4")

    xs = [c["cost_per_chart_usd"] * 1000 for c in cfgs]
    fp = [c["false_positives_per_chart"] for c in cfgs]
    rows = [c["review_burden_per_chart"] for c in cfgs]
    prec = [c["precision"] for c in cfgs]
    hn = [c["hard_negative_pass_rate"]["overall"] for c in cfgs]

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, ax = plt.subplots(figsize=(8.4, 4.8), dpi=150)
    fig.patch.set_facecolor(PAPER)
    ax.set_facecolor(PAPER)

    ax.plot(xs, rows, color=INK, linewidth=1.6, zorder=2)
    ax.scatter(xs, rows, s=95, color=INK, zorder=3, label="Rows a coder reviews per chart")
    ax.plot(xs, fp, color=RED, linewidth=1.4, linestyle="--", zorder=2)
    ax.scatter(xs, fp, s=95, color=LIME, edgecolor=RED, linewidth=1.4, zorder=3, label="False positives per chart")

    labels = {
        "C0+": "Rules only",
        "C1": "Lightning triage\n+ quote verifier",
        "C2": "+ Super",
        "C3": "+ Tavily guidelines",
        "C4": "+ Ultra escalation",
    }
    offsets = {"C0+": (10, 6), "C1": (-6, -34), "C2": (-6, 12), "C3": (10, 12), "C4": (-10, 10)}
    ha = {"C0+": "left", "C1": "left", "C2": "right", "C3": "left", "C4": "right"}
    for c, x, y, p in zip(cfgs, xs, rows, prec):
        cid = c["id"]
        ax.annotate(
            f"{cid}  {labels[cid]}\nprecision {p:.3f}",
            (x, y), textcoords="offset points", xytext=offsets[cid], ha=ha[cid],
            color=INK, fontsize=8.5, fontweight="bold", linespacing=1.15,
        )

    base = cfgs[0]
    full = c4 or cfgs[-1]
    cut = 100 * (1 - full["review_burden_per_chart"] / base["review_burden_per_chart"])
    bh, fh = base["hard_negative_pass_rate"], full["hard_negative_pass_rate"]
    ax.text(
        0.99, 0.97,
        f"{base['id']} -> {full['id']}\n"
        f"Review queue {base['review_burden_per_chart']:.3f} -> {full['review_burden_per_chart']:.3f} rows/chart  ({cut:.1f}% less)\n"
        f"False positives {base['false_positives_per_chart']:.3f} -> {full['false_positives_per_chart']:.3f} per chart\n"
        f"Hard negatives passed {bh['pass']}/{bh['total']} -> {fh['pass']}/{fh['total']}   recall {full['recall']:.3f}\n"
        f"Full cascade C4 (+ Ultra for conflicts, physician queries): ${full['cost_per_chart_usd']:.5f}/chart",
        transform=ax.transAxes, va="top", ha="right", fontsize=9, color=INK,
        family="DejaVu Sans Mono",
        bbox={"facecolor": PAPER, "edgecolor": INK, "linewidth": 0.6, "boxstyle": "round,pad=0.4"},
    )

    ax.set_xlim(-0.03, max(xs) * 1.35)
    ax.set_ylim(-0.06, max(rows) * 1.3)
    ax.set_xlabel("Nebius Token Factory cost per chart (USD x 1,000)", color=INK)
    ax.set_ylabel("Per chart", color=INK)
    d = summary["dataset"]
    ax.set_title(
        f"Each Nemotron step lowers the coder's queue ({d['n_charts']} charts, live on Token Factory)",
        color=INK, fontsize=11.5, fontweight="bold", loc="left",
    )
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(INK)
    ax.tick_params(colors=INK)
    ax.legend(frameon=False, fontsize=9, loc="lower left", labelcolor=INK)
    fig.tight_layout()
    for ext in ("svg", "png"):
        fig.savefig(f"{out_stem}.{ext}", facecolor=PAPER, metadata={"Date": None} if ext == "svg" else None)
    print(f"Wrote {out_stem}.svg and {out_stem}.png")



def precision(summary_path: str, out_stem: str) -> None:
    """Precision vs review rows per chart for C0, C0+ and C1..C4 (docs/assets/goldb-frontier)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in summary["configs"]}
    configs = [by_id[c] for c in PRECISION_ORDER if c in by_id]
    rules = [c for c in configs if c["id"] in ("C0", "C0+")]
    cascade = [c for c in configs if c["id"] not in ("C0", "C0+")]

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=150)
    fig.patch.set_facecolor(PAPER)
    ax.set_facecolor(PAPER)

    def xy(cs):
        return [c["review_burden_per_chart"] for c in cs], [c["precision"] for c in cs]

    rx, ry = xy(rules)
    ax.scatter(rx, ry, s=90, color=PAPER, edgecolor=MUTED, linewidth=1.4, zorder=3, label="Rules only (no model)")
    cx, cy = xy(cascade)
    ax.plot(cx, cy, color=INK, linewidth=1.6, zorder=2)
    ax.scatter(cx, cy, s=90, color=LIME, edgecolor=INK, linewidth=1.4, zorder=3, label="Nemotron cascade")

    # Points that share a position get one label ("C3 / C4").
    groups: dict[tuple[float, float], list[str]] = {}
    for c in configs:
        key = (round(c["review_burden_per_chart"], 6), round(c["precision"], 6))
        groups.setdefault(key, []).append(c["id"])
    offsets = {"C0": (8, -4), "C0+": (-26, 10), "C1": (6, -16), "C2": (-22, -18), "C3": (-38, 8), "C4": (-38, 8)}
    for (x, y), ids in groups.items():
        color = MUTED if ids[0] in ("C0", "C0+") else INK
        ax.annotate(" / ".join(ids), (x, y), textcoords="offset points", xytext=offsets.get(ids[0], (8, 8)),
                    color=color, fontsize=10, fontweight="bold")

    def hn(c):
        h = c.get("hard_negative_pass_rate") or {}
        return h.get("overall") or 0.0

    table = "\n".join(
        f"{c['id']:<4} P {c['precision']:.3f}  review {c['review_burden_per_chart']:.3f}  hard-neg {hn(c):.3f}  ${c['cost_per_chart_usd']:.5f}/chart"
        for c in configs
    )
    ax.text(0.02, 0.04, table, transform=ax.transAxes, va="bottom", ha="left", fontsize=8.5, color=INK,
            family="DejaVu Sans Mono", bbox={"facecolor": PAPER, "edgecolor": INK, "linewidth": 0.6, "boxstyle": "round,pad=0.4"})

    ax.set_xlabel("Rows a coder reviews per chart", color=INK)
    ax.set_ylabel("Precision", color=INK)
    d = summary["dataset"]
    ax.set_title(f"Review rows vs precision on {d['n_charts']} hard-negative charts", color=INK, fontsize=13,
                 fontweight="bold", loc="left")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(INK)
    ax.tick_params(colors=INK)
    xs = rx + cx
    ax.set_xlim(min(xs) - 0.15, max(xs) + 0.15)
    ax.set_ylim(min(ry + cy) - 0.33, 1.04)
    ax.legend(frameon=False, fontsize=9, loc="upper right", labelcolor=INK)
    fig.tight_layout()
    for ext in ("svg", "png"):
        fig.savefig(f"{out_stem}.{ext}", facecolor=PAPER, metadata={"Date": None} if ext == "svg" else None)
    print(f"Wrote {out_stem}.svg and {out_stem}.png")



def main(argv: list[str]) -> None:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2:
        raise SystemExit(__doc__)
    (precision if "--precision" in argv else cascade)(args[0], args[1])


if __name__ == "__main__":
    main(sys.argv[1:])
