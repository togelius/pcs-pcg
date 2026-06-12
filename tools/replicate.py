#!/usr/bin/env python3
"""Replication study for the learnability estimator.

Runs the calibration across multiple seeds and budgets to check that the
first-pass results were not luck:

  standard runs: 25 gens x 4 episodes, seeds 0..4 on DEMO2/DEMO3,
                 seeds 0..2 on the degenerate boards (cheap sanity)
  long runs:     75 gens x 6 episodes, seeds 10,11 on DEMO2/DEMO3
                 (does DEMO3 stay unlearnable with 3.4x the budget?)

Writes one JSON per run to work/replication/ and a summary at the end.
Progress is printed one line per completed run (monitor-friendly).
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from learn_es import calibrate_board  # noqa: E402

OUT = os.path.join(ROOT, "work", "replication")
os.makedirs(OUT, exist_ok=True)

RUNS = []
for seed in range(3):
    RUNS.append(("NEW", "std", seed, dict(gens=25, episodes=4)))
    RUNS.append(("DEMO1", "std", seed, dict(gens=25, episodes=4)))
for seed in range(5):
    RUNS.append(("DEMO2", "std", seed, dict(gens=25, episodes=4)))
    RUNS.append(("DEMO3", "std", seed, dict(gens=25, episodes=4)))
for seed in (10, 11):
    RUNS.append(("DEMO2", "long", seed, dict(gens=75, episodes=6)))
    RUNS.append(("DEMO3", "long", seed, dict(gens=75, episodes=6)))


def main() -> None:
    results = []
    for board, kind, seed, kw in RUNS:
        tag = f"{board}_{kind}_s{seed}"
        path = os.path.join(OUT, tag + ".json")
        if os.path.exists(path):
            with open(path) as f:
                r = json.load(f)
            print(f"SKIP {tag} (cached) lp_z={r['lp_z']:.2f}", flush=True)
            results.append((board, kind, seed, r))
            continue
        r = calibrate_board(os.path.join(ROOT, "work", board + ".PB"),
                            n_envs=5, baseline_eps=30, seed=seed, **kw)
        with open(path, "w") as f:
            json.dump(r, f, indent=2)
        print(f"DONE {tag} lp_z={r['lp_z']:.2f} final3={r['final3']:.0f} "
              f"rnd={r['random_mean']:.0f}±{r['random_std']:.0f} "
              f"wall={r['wall_s']}s", flush=True)
        results.append((board, kind, seed, r))

    # summary
    import numpy as np
    print("\n=== SUMMARY (LP_z per board) ===", flush=True)
    for board in ("NEW", "DEMO1", "DEMO2", "DEMO3"):
        for kind in ("std", "long"):
            zs = [r["lp_z"] for b, k, s, r in results if b == board and k == kind]
            if zs:
                print(f"{board:6s} {kind:5s} n={len(zs)} "
                      f"LP_z: {np.mean(zs):+.2f} ± {np.std(zs):.2f}  "
                      f"({' '.join(f'{z:+.2f}' for z in zs)})", flush=True)


if __name__ == "__main__":
    main()
