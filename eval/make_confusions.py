#!/usr/bin/env python3
"""Confusion-matrix PNGs per reason + a P/R/F1 summary (docs/EVALUATION.md), from eval/results/metrics.json.

Outputs (eval/results/):
  confusion_<REASON>.png   2x2: human reason present (rows) vs agent reason present (cols), micro-summed
                           over events. Cell fill = row share (one-hue blue sequential, so recall reads
                           down the diagonal); every cell is labelled with count + row %.
  confusion_REJECT.png     the same for the overall verdict (agent review counted as not-reject).
  reason_prf.png           small multiples: precision | recall | F1, one horizontal bar per reason,
                           value at the bar tip; reasons with no support are listed as "n/a".
  verdicts_by_event.png    label-independent: share of keep / reject / review per event (100 % stacked
                           horizontal bars, categorical slots 1-3, direct labels + legend).
  group_sizes.png          label-independent: number of worker groups by size bin per event (small multiples,
                           one hue, count at the bar tip).

Before photographer labels exist every confusion matrix still renders, with n=0 cells and the subtitle
"no photographer labels yet (n=0)", so the report layout is final today.

Styling follows the dataviz skill's reference palette (light surface #fcfcfb, sequential blue ramp,
text in ink tokens, hairline solid axes). Static PNG for the PDF report; the table twin of every
figure is eval/results/metrics.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, to_rgb  # noqa: E402

HERE = Path(__file__).resolve().parent
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES_1 = "#2a78d6"
# Categorical slots 1-3 of the reference palette (validated all-pairs, light mode). Verdicts are categories,
# not status, so status colours are not used. Slot 3 is below 3:1 on the surface: every segment carries a
# visible label (relief rule).
VERDICT_COLORS = {"keep": "#2a78d6", "reject": "#eb6834", "review": "#1baf7a"}
NO_PHOTOGRAPHER = "no photographer labels yet (n=0)"
SIZE_BINS = (("2", 2, 2), ("3-5", 3, 5), ("6-10", 6, 10), ("11-20", 11, 20), ("21+", 21, 10**9))
# Sequential blue ramp, steps 100 -> 700 of the reference palette.
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
CMAP = LinearSegmentedColormap.from_list("seq_blue", SEQ)
FONT = ["system-ui", "Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"]


def _style() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": FONT, "font.size": 10,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.labelcolor": INK_2,
        "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK,
        "axes.titlesize": 11, "axes.titleweight": "semibold", "axes.titlecolor": INK,
    })


def _ink_for(fill_hex: str) -> str:
    r, g, b = to_rgb(fill_hex)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#ffffff" if lum < 0.45 else INK


def confusion_png(name: str, m: dict, out: Path, subtitle: str) -> Path:
    tp, fp, fn, tn = m["tp"], m["fp"], m["fn"], m.get("tn", 0)
    rows = [("Human: present", [tp, fn]), ("Human: absent", [fp, tn])]
    fig, ax = plt.subplots(figsize=(4.8, 4.3), dpi=200)
    for i, (_, (a, b)) in enumerate(rows):
        tot = a + b
        for j, v in enumerate((a, b)):
            share = v / tot if tot else 0.0
            fill = CMAP(share)
            hexfill = matplotlib.colors.to_hex(fill)
            ax.add_patch(plt.Rectangle((j + 0.02, 1 - i + 0.02), 0.96, 0.96, color=hexfill, lw=0))
            pct = f"{100 * share:.0f}% of row" if tot else "no rows"
            ax.text(j + 0.5, 1 - i + 0.56, f"{v:,}", ha="center", va="center", fontsize=15,
                    fontweight="semibold", color=_ink_for(hexfill))
            ax.text(j + 0.5, 1 - i + 0.32, pct, ha="center", va="center", fontsize=8.5,
                    color=_ink_for(hexfill))
    ax.set_xlim(0, 2)
    ax.set_ylim(0, 2)
    ax.set_xticks([0.5, 1.5], ["Agent: present", "Agent: absent"])
    ax.set_yticks([1.5, 0.5], [r[0] for r in rows])
    ax.tick_params(length=0, labelsize=9, colors=INK_2)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_aspect("equal")
    p, r, f = (m.get(k) for k in ("precision", "recall", "f1"))
    fmt = lambda x: "n/a" if x is None else f"{x:.2f}"  # noqa: E731
    fig.suptitle(name.replace("_", " ").title() if name != "REJECT" else "Reject verdict", x=0.04, ha="left",
                 fontsize=12, fontweight="semibold", color=INK)
    ax.set_title(f"precision {fmt(p)} · recall {fmt(r)} · F1 {fmt(f)}\n{subtitle}", loc="left",
                 fontsize=8.5, fontweight="normal", color=INK_2, pad=8, linespacing=1.4)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = out / f"confusion_{name}.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def prf_png(per_reason: dict, out: Path, subtitle: str) -> Path:
    names = list(per_reason)
    fig, axes = plt.subplots(1, 3, figsize=(9.6, 0.42 * len(names) + 1.5), dpi=200, sharey=True)
    for ax, key, title in zip(axes, ("precision", "recall", "f1"), ("Precision", "Recall", "F1")):
        for y, n in enumerate(names):
            v = per_reason[n].get(key)
            yy = len(names) - 1 - y
            if v is None:
                ax.text(0.03, yy + 0.05, "n/a (no support)", va="center", fontsize=8, color=MUTED)
                continue
            ax.barh(yy, v, height=0.5, color=SERIES_1, lw=0)
            ax.text(min(v, 1.0) + 0.02, yy, f"{v:.2f}", va="center", fontsize=8.5, color=INK)
        ax.set_xlim(0, 1.12)
        ax.set_ylim(-0.6, len(names) - 0.4)
        ax.set_xticks([0, 0.5, 1.0], ["0", "0.5", "1.0"])
        ax.grid(axis="x", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        ax.set_title(title, loc="left")
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.tick_params(length=0)
    axes[0].set_yticks(range(len(names)),
                       [f"{n}  (n={per_reason[n]['support']})" for n in reversed(names)], color=INK_2)
    fig.suptitle(f"Agent reason vs human reason, per reason code · {subtitle}", x=0.01, ha="left",
                 fontsize=11, fontweight="semibold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = out / "reason_prf.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def verdicts_png(events: list[dict], out: Path) -> Path:
    rows = [(e["event_id"], (e.get("run_counts") or {}).get("verdicts") or {}) for e in events]
    rows = [(n, v) for n, v in rows if sum(v.values())]
    fig, ax = plt.subplots(figsize=(8.0, 0.62 * len(rows) + 1.6), dpi=200)
    for y, (name, v) in enumerate(reversed(rows)):
        tot = sum(v.values())
        x = 0.0
        for k in ("keep", "reject", "review"):
            share = v.get(k, 0) / tot
            if share <= 0:
                continue
            ax.barh(y, share, left=x, height=0.56, color=VERDICT_COLORS[k], lw=0, edgecolor=SURFACE)
            if share >= 0.06:
                ax.text(x + share / 2, y, f"{100 * share:.0f}%", ha="center", va="center", fontsize=8,
                        color=_ink_for(VERDICT_COLORS[k]))
            x += share + 0.004  # 2px-ish surface gap between segments
        ax.text(1.03, y, f"n={tot:,}", va="center", fontsize=8, color=INK_2)
    ax.set_yticks(range(len(rows)), [n for n, _ in reversed(rows)], color=INK_2)
    ax.set_xlim(0, 1.12)
    ax.set_xticks([0, 0.5, 1.0], ["0", "50%", "100%"])
    ax.set_xlabel("share of the event's photos (final verdict in decisions.jsonl)")
    ax.grid(axis="x", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.tick_params(length=0)
    handles = [plt.Rectangle((0, 0), 1, 1, color=VERDICT_COLORS[k]) for k in ("keep", "reject", "review")]
    ax.legend(handles, ["keep", "reject", "review"], ncol=3, frameon=False, loc="lower left",
              bbox_to_anchor=(0, 1.0), fontsize=8.5, labelcolor=INK_2)
    fig.suptitle("Verdicts per event", x=0.01, ha="left", fontsize=11, fontweight="semibold")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    path = out / "verdicts_by_event.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def group_sizes_png(events: list[dict], out: Path) -> Path:
    evs = [e for e in events if (e.get("group_shape") or {}).get("size_histogram")]
    fig, axes = plt.subplots(1, max(1, len(evs)), figsize=(2.6 * max(1, len(evs)) + 0.6, 3.0), dpi=200,
                             sharey=False, squeeze=False)
    for ax, e in zip(axes[0], evs):
        hist = {int(k): v for k, v in e["group_shape"]["size_histogram"].items()}
        counts = [sum(v for k, v in hist.items() if lo <= k <= hi) for _, lo, hi in SIZE_BINS]
        xs = range(len(SIZE_BINS))
        ax.bar(xs, counts, width=0.6, color=SERIES_1, lw=0)
        top = max(counts) or 1
        for x, c in zip(xs, counts):
            ax.text(x, c + top * 0.03, str(c), ha="center", va="bottom", fontsize=8, color=INK)
        ax.set_xticks(list(xs), [b for b, _, _ in SIZE_BINS], fontsize=8)
        ax.set_ylim(0, top * 1.18)
        ax.set_title(f"{e['event_id']}  ({e['group_shape']['n_groups']} groups, "
                     f"{e['group_shape']['n_singletons']} singles)", loc="left", fontsize=9)
        ax.set_xlabel("frames per group", fontsize=8.5)
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.tick_params(length=0)
    axes[0][0].set_ylabel("number of groups")
    fig.suptitle("Worker group (burst) sizes per event", x=0.01, ha="left", fontsize=11, fontweight="semibold")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    path = out / "group_sizes.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def render_all(rep: dict, out: Path) -> list[Path]:
    """Every figure the report embeds; label-dependent ones render with n=0 before labels exist."""
    _style()
    out.mkdir(parents=True, exist_ok=True)
    agg = rep["aggregate"]
    labelled = bool(agg.get("labelled_events"))
    sub = (f"{len(agg.get('labelled_events') or [])} photographer-labelled event(s), {agg['n_scored']} photos"
           if labelled else NO_PHOTOGRAPHER)
    zero = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "precision": None, "recall": None, "f1": None, "support": 0}
    import sys

    sys.path.insert(0, str(HERE))
    from compute_metrics import SCORED_REASONS

    per_reason = agg.get("per_reason") or {r: dict(zero) for r in SCORED_REASONS}
    paths = [confusion_png(r, m, out, sub) for r, m in per_reason.items()]
    paths.append(confusion_png("REJECT", agg.get("reject") or dict(zero), out, sub))
    paths.append(prf_png(per_reason, out, sub))
    paths.append(verdicts_png(rep["events"], out))
    paths.append(group_sizes_png(rep["events"], out))
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--metrics", type=Path, default=HERE / "results" / "metrics.json")
    ap.add_argument("--out-dir", type=Path, default=HERE / "results")
    args = ap.parse_args()
    import sys

    sys.path.insert(0, str(HERE))
    rep = json.loads(args.metrics.read_text())
    paths = render_all(rep, args.out_dir)
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
