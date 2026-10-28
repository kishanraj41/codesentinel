"""Draw the Gold-B frontier: precision vs review rows per chart for C0, C0+ and C1..C4.

Same style as eval/synthea/frontier.py. Needs matplotlib (not a runtime dependency):
  pip install matplotlib
  python scripts/goldb_frontier.py eval/results/live/summary.json docs/assets/goldb-frontier
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

INK = "#08382d"
LIME = "#dff25a"
PAPER = "#f6f1e6"
MUTED = "#6b7f78"

ORDER = ("C0", "C0+", "C1", "C2", "C3", "C4")


def main(summary_path: str, out_stem: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in summary["configs"]}
    configs = [by_id[c] for c in ORDER if c in by_id]
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


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
