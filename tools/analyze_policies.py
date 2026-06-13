"""Combine trained-policy action maps into one comparison figure and
print a weight interpretation.

For each board's 4x7 weight matrix, renders the greedy action over the
playfield (ball moving down) and prints the per-action weight vector so
the strategy is legible.

Usage:
  python3 tools/analyze_policies.py g000_i0 g002_o0 g004_o1 \
      --policydir work/policies --out work/figures/policies.png
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from learn_es import features  # noqa: E402

ACTIONS = ["none", "left", "right", "both"]
COLORS = ["#dddddd", "#3366cc", "#cc3333", "#9933cc"]
FEAT_NAMES = ["bias", "x", "y", "dx", "dy", "x*dx", "y*dy"]


def greedy(w, x, y, dx, dy):
    return int(np.argmax(w @ features(np.array([x, y, dx, dy], float))))


def action_grid(w, dy=8.0):
    xs = np.arange(6, 148, 3)
    ys = np.arange(14, 192, 3)
    g = np.zeros((len(ys), len(xs)), int)
    for iy, y in enumerate(ys):
        for ix, x in enumerate(xs):
            g[iy, ix] = greedy(w, x, y, 2.0, dy)
    return g, xs, ys


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--policydir", default=os.path.join(ROOT, "work", "policies"))
    ap.add_argument("--out", default=os.path.join(ROOT, "work", "figures", "policies.png"))
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    weights = {}
    for tag in args.tags:
        p = os.path.join(args.policydir, tag + ".txt")
        if not os.path.exists(p):
            print(f"skip {tag}: no weights")
            continue
        weights[tag] = np.loadtxt(p).reshape(4, 7)

    n = len(weights)
    fig, axes = plt.subplots(1, n, figsize=(3.6 * n, 4.6), squeeze=False)
    for ax, (tag, w) in zip(axes[0], weights.items()):
        g, xs, ys = action_grid(w)
        ax.imshow(g, origin="upper", aspect="auto",
                  extent=[xs[0], xs[-1], ys[-1], ys[0]],
                  cmap=ListedColormap(COLORS), vmin=0, vmax=3)
        ax.axhline(150, color="k", ls="--", lw=1, alpha=0.6)
        ax.set_title(tag, fontsize=10)
        ax.set_xlabel("ball x")
        hist = {ACTIONS[a]: int(np.sum(g == a)) for a in range(4)}
        dom = max(hist, key=hist.get)
        ax.set_xlabel(f"ball x\nmostly: {dom}", fontsize=9)
    axes[0][0].set_ylabel("ball y (downward →)")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in COLORS]
    fig.legend(handles, ACTIONS, loc="upper center", ncol=4, fontsize=9,
               title="greedy action (ball moving down)")
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    fig.savefig(args.out, dpi=150)
    plt.close(fig)
    print(f"wrote {args.out}")

    print("\n=== weight interpretation (greedy action = argmax_a W[a]·phi) ===")
    for tag, w in weights.items():
        print(f"\n{tag}:")
        print("  " + " ".join(f"{f:>6s}" for f in FEAT_NAMES))
        for a in range(4):
            print(f"  {ACTIONS[a]:5s} " + " ".join(f"{v:6.2f}" for v in w[a]))


if __name__ == "__main__":
    main()
