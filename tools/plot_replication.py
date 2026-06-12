#!/usr/bin/env python3
"""Figures for the learnability replication study.

Reads work/replication/*.json and produces:

  fig_curves_std.png    learning curves (per board, all standard seeds)
                        on a log score axis, with the random-baseline
                        median and IQR band
  fig_curves_long.png   long-run (75-gen) curves for DEMO2/DEMO3
  fig_percentiles.png   strip/summary plot: percentile of learned
                        performance within the random distribution,
                        per board and budget

Usage: python3 tools/plot_replication.py [outdir]
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REP = os.path.join(ROOT, "work", "replication")

BOARD_LABELS = {
    "NEW": "NEW (empty board)",
    "DEMO1": "DEMO1 (launcher trap)",
    "DEMO2": "DEMO2 (Meta-Pin)",
    "DEMO3": "DEMO3",
}
BOARD_COLORS = {"NEW": "#999999", "DEMO1": "#cc7722",
                "DEMO2": "#2266cc", "DEMO3": "#22aa55"}


def load_runs() -> list[dict]:
    rows = []
    for path in sorted(glob.glob(os.path.join(REP, "*.json"))):
        m = re.match(r"(\w+?)_(std|long)_s(\d+)", os.path.basename(path)[:-5])
        with open(path) as f:
            r = json.load(f)
        rows.append({**r, "board": m.group(1), "kind": m.group(2),
                     "seed": int(m.group(3))})
    return rows


def smooth(xs, w=3):
    xs = np.asarray(xs, dtype=float)
    if len(xs) < w:
        return xs
    return np.convolve(xs, np.ones(w) / w, mode="valid")


def plot_curves(runs, kind, outpath, boards=("DEMO2", "DEMO3", "DEMO1", "NEW")):
    sel_boards = [b for b in boards
                  if any(r["board"] == b and r["kind"] == kind for r in runs)]
    fig, axes = plt.subplots(1, len(sel_boards),
                             figsize=(4.2 * len(sel_boards), 3.6),
                             sharey=True)
    if len(sel_boards) == 1:
        axes = [axes]
    for ax, board in zip(axes, sel_boards):
        rs = [r for r in runs if r["board"] == board and r["kind"] == kind]
        rnd_all = np.concatenate([np.array(r["random_scores"], float) for r in rs])
        med = np.median(rnd_all)
        q1, q3 = np.percentile(rnd_all, 25), np.percentile(rnd_all, 75)
        for r in rs:
            curve = smooth(r["curve_raw"])
            gens = np.arange(len(curve)) + 1
            ax.plot(gens, np.asarray(curve) + 1, alpha=0.8, lw=1.5,
                    color=BOARD_COLORS[board],
                    label=f"seed {r['seed']}" if len(rs) <= 6 else None)
        ax.axhline(med + 1, color="k", ls="--", lw=1, label="random median")
        ax.axhspan(q1 + 1, q3 + 1, color="k", alpha=0.10, label="random IQR")
        ax.set_yscale("log")
        ax.set_title(BOARD_LABELS.get(board, board), fontsize=10)
        ax.set_xlabel("ES generation")
        ax.grid(alpha=0.25)
    axes[0].set_ylabel("episode score + 1 (log)")
    handles, labels = axes[0].get_legend_handles_labels()
    # dedupe legend
    seen, h2, l2 = set(), [], []
    for h, l in zip(handles, labels):
        if l not in seen:
            seen.add(l)
            h2.append(h); l2.append(l)
    axes[0].legend(h2, l2, fontsize=7, loc="upper left")
    budget = "25 gens × 4 eps" if kind == "std" else "75 gens × 6 eps"
    fig.suptitle(f"Learning curves, smoothed (3-gen) — {budget}", fontsize=11)
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)


def plot_percentiles(runs, outpath):
    fig, ax = plt.subplots(figsize=(7, 4))
    boards = ["NEW", "DEMO1", "DEMO2", "DEMO3"]
    xticks, xlabels = [], []
    xi = 0
    for board in boards:
        for kind in ("std", "long"):
            rs = [r for r in runs if r["board"] == board and r["kind"] == kind]
            if not rs:
                continue
            pcts = []
            for r in rs:
                rnd = np.array(r["random_scores"], float)
                pcts.append(100.0 * np.mean(rnd < r["final3"]))
            jitter = (np.random.default_rng(0).random(len(pcts)) - 0.5) * 0.25
            ax.scatter(np.full(len(pcts), xi) + jitter, pcts, s=42,
                       color=BOARD_COLORS[board],
                       edgecolor="k", linewidth=0.5, zorder=3,
                       marker="o" if kind == "std" else "s")
            ax.scatter([xi], [np.mean(pcts)], marker="_", s=600, color="k",
                       zorder=4)
            xticks.append(xi)
            xlabels.append(f"{board}\n{kind}")
            xi += 1
    ax.axhline(50, color="gray", ls=":", lw=1)
    ax.text(xi - 0.45, 52, "random median", fontsize=8, color="gray",
            ha="right")
    ax.set_xticks(xticks)
    ax.set_xticklabels(xlabels, fontsize=9)
    ax.set_ylabel("learned score percentile within random episodes")
    ax.set_ylim(-5, 105)
    ax.grid(alpha=0.25, axis="y")
    ax.set_title("Learnability per board: where the learned policy lands\n"
                 "in the random-play score distribution (one point per seed)",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)


def main() -> None:
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "work", "figures")
    os.makedirs(outdir, exist_ok=True)
    runs = load_runs()
    plot_curves(runs, "std", os.path.join(outdir, "fig_curves_std.png"))
    if any(r["kind"] == "long" for r in runs):
        plot_curves(runs, "long", os.path.join(outdir, "fig_curves_long.png"),
                    boards=("DEMO2", "DEMO3"))
    plot_percentiles(runs, os.path.join(outdir, "fig_percentiles.png"))
    print(f"figures written to {outdir}")


if __name__ == "__main__":
    main()
