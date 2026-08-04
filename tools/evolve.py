"""Outer loop: evolve PCS pinball boards.

(mu+lambda) evolution over board genotypes (tools/board.py) with a
pluggable fitness:

  cheap         ~20 random episodes: launchability, log-score, coverage,
                survival. Fast (~30-60 s/board); good for smoke tests
                and seeding.
  learnability  the calibrated estimator (tools/learn_es.py): percentile
                of ES-learned performance within the random-episode
                distribution, plus small tiebreakers. ~3-4 min/board.

Each evaluated board is written to the run directory together with a
JSONL log; the per-board eval disk/save state are cached by content
hash as usual (tools/pcs_env.py).

Usage:
  python3 tools/evolve.py --out work/evo1 --gens 10 --mu 3 --lam 6 \
      --fitness cheap --seed 0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from board import Board, generate, load_library, mutate  # noqa: E402
from learn_es import Evaluator, calibrate_board  # noqa: E402


def eval_cheap(pb_path: str, seed: int, episodes: int = 20,
               n_envs: int = 4) -> dict:
    """Random-play statistics as a cheap fitness."""
    rng = np.random.default_rng(seed)
    ev = Evaluator(pb_path, n_envs, max_episode_steps=300)
    try:
        jobs = [(None, int(rng.integers(140, 256)), int(rng.integers(2**31)))
                for _ in range(episodes)]
        results = ev.scores(jobs)
    finally:
        ev.close()
    scores = np.array([r[0] for r in results], float)
    frames = np.array([r[1] for r in results], float)
    cells = np.array([r[2] for r in results], float)
    alive = float(np.mean(frames > 30))      # launches that entered play
    fit = (2.0 * alive
           + 0.3 * float(np.mean(np.log1p(scores)))
           + 0.2 * float(np.mean(np.log1p(cells)))
           + 0.1 * float(np.mean(np.log1p(frames))))
    return {"fitness": fit, "alive": alive,
            "score_med": float(np.median(scores)),
            "cells_mean": float(np.mean(cells)),
            "frames_mean": float(np.mean(frames))}


def eval_learnability(pb_path: str, seed: int) -> dict:
    """Magnitude-gated percentile-of-random learnability.

    Fitness = percentile(learned within random) x magnitude_ramp, where
    the ramp scales 0->1 with how much the learned policy beats the
    random MEDIAN. The percentile alone is fooled by tight-variance
    trivial boards (e.g. learned 1084.6 vs random 1083.0 -> pct 95 with
    no real learning, because every random episode scores ~1083); the
    ramp demands a real performance gap, so:
      - genuinely learnable (random low, learned high) -> high fitness
      - trivial (learned ~ random, any variance) -> ~0
      - random-beats-learned -> low percentile -> ~0
      - dead/unplayable -> 0
    max_episode_steps capped at 120 to bound long-survival board cost.
    """
    r = calibrate_board(pb_path, gens=12, episodes=3, n_envs=4,
                        baseline_eps=15, max_episode_steps=120, seed=seed)
    rnd = np.array(r["random_scores"], float)
    # fitness is computed from the HELD-OUT median (fresh episodes of the
    # final policy), not final3: final3 is selection-biased and the outer
    # loop learned to exploit that (see docs/EVOLUTION.md addendum)
    learned = r.get("holdout_median", r["final3"])
    final3 = r["final3"]
    rnd_med = float(np.median(rnd)) if len(rnd) else 0.0
    pct = float(100.0 * np.mean(rnd < learned)) if len(rnd) else 0.0
    gain = learned - rnd_med
    # ramp reaches 1.0 once the learned policy beats the random median by
    # max(150 pts, the median itself) -- absolute floor stops tiny-score
    # boards from qualifying on a tiny relative gain.
    ramp = float(np.clip(gain / max(150.0, rnd_med), 0.0, 1.0))
    playable = 1.0 if (r["random_mean"] > 0 or final3 > 0) else 0.0
    fitness = pct * ramp * playable
    return {"fitness": float(fitness), "pct": pct, "ramp": round(ramp, 3),
            "gain": float(gain), "final3": final3,
            "holdout_median": r.get("holdout_median", 0.0),
            "random_mean": r["random_mean"], "random_med": rnd_med,
            "curve_raw": r["curve_raw"]}


EVALS = {"cheap": eval_cheap, "learnability": eval_learnability}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gens", type=int, default=10)
    ap.add_argument("--mu", type=int, default=3)
    ap.add_argument("--lam", type=int, default=6)
    ap.add_argument("--fitness", choices=EVALS, default="cheap")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    lib = load_library()
    log = open(os.path.join(args.out, "log.jsonl"), "a")
    evaluate = EVALS[args.fitness]

    def assess(board: Board, tag: str, parent: str = "") -> dict:
        pb = os.path.join(args.out, tag + ".pb")
        with open(pb, "wb") as f:
            f.write(board.compile())
        t0 = time.time()
        try:
            r = evaluate(pb, seed=args.seed)
        except Exception as e:          # infrastructure failure != fitness 0
            r = {"fitness": -1.0, "error": str(e)}
        r.update(tag=tag, parent=parent, records=len(board.records),
                 wall_s=round(time.time() - t0, 1))
        log.write(json.dumps(r) + "\n")
        log.flush()
        print(f"  {tag}: fit={r['fitness']:.2f} "
              + " ".join(f"{k}={v:.1f}" for k, v in r.items()
                         if isinstance(v, float) and k != "fitness"),
              flush=True)
        return r

    def save_champions(pop):
        # incremental: an interruption never loses the best-so-far
        for rank, (fit, b, r) in enumerate(pop):
            with open(os.path.join(args.out, f"best_{rank}.pb"), "wb") as f:
                f.write(b.compile())
        with open(os.path.join(args.out, "champions.json"), "w") as f:
            json.dump([{"rank": i, **r} for i, (fit, b, r) in enumerate(pop)],
                      f, indent=2)

    # initial population: random boards from the generator
    pop = []
    print("=== init ===", flush=True)
    for i in range(args.mu + args.lam):
        b = generate(rng, library=lib)
        r = assess(b, f"g000_i{i}", parent="random")
        pop.append((r["fitness"], b, r))
    pop.sort(key=lambda t: -t[0])
    pop = pop[:args.mu]
    save_champions(pop)

    for g in range(1, args.gens + 1):
        print(f"=== gen {g} ===", flush=True)
        offspring = []
        for k in range(args.lam):
            pfit, pb_board, pr = pop[k % len(pop)]
            child = mutate(pb_board, rng, library=lib)
            r = assess(child, f"g{g:03d}_o{k}", parent=pr["tag"])
            offspring.append((r["fitness"], child, r))
        pop = sorted(pop + offspring, key=lambda t: -t[0])[:args.mu]
        save_champions(pop)
        best = pop[0]
        print(f"gen {g}: best={best[0]:.2f} ({best[2]['tag']}, "
              f"{best[2]['records']} records)", flush=True)

    print("done; champions in", args.out, flush=True)


if __name__ == "__main__":
    main()
