"""Train a linear flipper policy on a board and save its weights.

Reuses the calibrated ES (tools/learn_es) but returns the best weight
matrix so it can drive harness/replay.lua and be analysed. Also renders
an "action map": for a grid of ball positions (at a representative
downward velocity) it shows which action the greedy policy takes, which
is the most legible summary of what the policy learned.

Usage:
  python3 tools/train_policy.py work/evo_smoke/g004_o1.pb \
      --gens 25 --episodes 4 --out work/policies/g004_o1
  -> writes <out>.txt (28 weights) and <out>_actionmap.png
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from learn_es import (Evaluator, N_ACTIONS, N_FEATURES, features,  # noqa: E402
                      fitness)

ACTION_NAMES = ["none", "left", "right", "both"]
ACTION_COLORS = ["#dddddd", "#3366cc", "#cc3333", "#9933cc"]


def train(pb_path: str, *, gens: int, episodes: int, children: int = 6,
          sigma: float = 0.3, n_envs: int = 4, seed: int = 0,
          max_episode_steps: int = 300):
    rng = np.random.default_rng(seed)
    ev = Evaluator(pb_path, n_envs, max_episode_steps=max_episode_steps)
    try:
        parent = rng.normal(0, 0.1, size=(N_ACTIONS, N_FEATURES))
        best_w, best_fit, curve = parent, -1e9, []
        for g in range(gens):
            draws = [(int(rng.integers(140, 256)), int(rng.integers(2**31)))
                     for _ in range(episodes)]
            cands = [parent] + [parent + rng.normal(0, sigma, parent.shape)
                                for _ in range(children)]
            jobs, owner = [], []
            for ci, w in enumerate(cands):
                for p, s in draws:
                    jobs.append((w, p, s)); owner.append(ci)
            res = ev.scores(jobs)
            by = [[] for _ in cands]
            for ci, r in zip(owner, res):
                by[ci].append(r)
            fits = [fitness(b) for b in by]
            bi = int(np.argmax(fits))
            if fits[bi] > fits[0] + 0.05:
                parent = cands[bi]
            f = max(fits)
            if f > best_fit:
                best_fit, best_w = f, cands[int(np.argmax(fits))]
            curve.append(float(np.mean([r[0] for r in by[int(np.argmax(fits))]])))
        return best_w, best_fit, curve
    finally:
        ev.close()


def greedy_action(w: np.ndarray, obs) -> int:
    return int(np.argmax(w @ features(np.asarray(obs, float))))


def action_map(w: np.ndarray, outpath: str, dy: float = 8.0) -> dict:
    """Render which action the greedy policy takes over the playfield
    (at downward velocity dy), and return the action histogram."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    xs = np.arange(6, 148, 3)
    ys = np.arange(14, 192, 3)
    grid = np.zeros((len(ys), len(xs)), dtype=int)
    for iy, y in enumerate(ys):
        for ix, x in enumerate(xs):
            # ball heading down toward the flippers (dx small rightward)
            grid[iy, ix] = greedy_action(w, (x, y, 2.0, dy))
    hist = {ACTION_NAMES[a]: int(np.sum(grid == a)) for a in range(N_ACTIONS)}

    fig, ax = plt.subplots(figsize=(4.4, 5.2))
    ax.imshow(grid, origin="upper", aspect="auto",
              extent=[xs[0], xs[-1], ys[-1], ys[0]],
              cmap=ListedColormap(ACTION_COLORS), vmin=0, vmax=3)
    # flipper zone marker
    ax.axhline(150, color="k", ls="--", lw=1, alpha=0.6)
    ax.text(76, 154, "flipper zone", ha="center", fontsize=8)
    ax.set_xlabel("ball x"); ax.set_ylabel("ball y (down →)")
    ax.set_title("Greedy action by ball position\n(ball moving downward)",
                 fontsize=10)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in ACTION_COLORS]
    ax.legend(handles, ACTION_NAMES, fontsize=8, loc="upper right",
              framealpha=0.9, title="action")
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)
    return hist


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("board")
    ap.add_argument("--gens", type=int, default=25)
    ap.add_argument("--episodes", type=int, default=4)
    ap.add_argument("--steps", type=int, default=300,
                    help="max episode steps (lower = faster on long-survival boards)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    w, fit, curve = train(args.board, gens=args.gens, episodes=args.episodes,
                          seed=args.seed, max_episode_steps=args.steps)
    np.savetxt(args.out + ".txt", w.reshape(1, -1), fmt="%.5f")
    hist = action_map(w, args.out + "_actionmap.png")
    print(f"{os.path.basename(args.board)}: best_fit={fit:.2f} "
          f"final_score~{curve[-1]:.0f}")
    print(f"  action histogram (downward ball): {hist}")
    print(f"  weights -> {args.out}.txt   actionmap -> {args.out}_actionmap.png")


if __name__ == "__main__":
    main()
