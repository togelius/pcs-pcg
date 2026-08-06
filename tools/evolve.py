"""Outer loop: evolve PCS pinball boards.

(mu+lambda) evolution over board genotypes (tools/board.py) with a
pluggable fitness:

  cheap         ~20 random episodes: launchability, log-score, coverage,
                survival. Fast (~30-60 s/board); good for smoke tests
                and seeding.
  learnability  multi-seed held-out estimator: median over n_seeds
                independent inner-learning runs of (percentile of the
                final policy's HELD-OUT median within random play,
                magnitude-gated). ~10-20 min/board; slow but honest.

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


def eval_learnability(pb_path: str, seed: int, n_seeds: int = 4,
                      gens: int = 12, episodes: int = 3,
                      baseline_eps: int = 15) -> dict:
    """Multi-seed, held-out, magnitude-gated learnability.

    Runs the inner learning loop n_seeds times independently; each seed's
    fitness = percentile(holdout_median within that seed's random
    distribution) x magnitude ramp; the board's fitness is the MEDIAN
    across seeds. Using the held-out median (final policy on fresh
    episodes) removes the selection bias that a previous run exploited;
    the multi-seed median removes single-run learner luck in both
    directions. Dead boards (no scoring under any play) abort after the
    first seed -- deadness is deterministic.

    `seed` should be FRESH per evaluation (drawn by the caller from the
    outer rng): reusing the same inner seeds for every board across the
    whole run lets the outer loop overfit boards to those particular
    serve sequences (measured on the evo_holdout run: champion 70
    in-run -> 30 on fresh seeds).
    """
    per_seed = []
    detail = []
    for k in range(n_seeds):
        r = calibrate_board(pb_path, gens=gens, episodes=episodes,
                            n_envs=4, baseline_eps=baseline_eps,
                            max_episode_steps=120,
                            seed=seed * 1000 + k)
        rnd = np.array(r["random_scores"], float)
        learned = r.get("holdout_median", 0.0)
        rnd_med = float(np.median(rnd)) if len(rnd) else 0.0
        pct = float(100.0 * np.mean(rnd < learned)) if len(rnd) else 0.0
        gain = learned - rnd_med
        ramp = float(np.clip(gain / max(150.0, rnd_med), 0.0, 1.0))
        playable = 1.0 if (r["random_mean"] > 0 or r["final3"] > 0
                           or learned > 0) else 0.0
        f = pct * ramp * playable
        per_seed.append(f)
        detail.append({"seed": k, "fit": round(f, 1),
                       "holdout": learned, "rnd_med": rnd_med,
                       "pct": round(pct, 1), "ramp": round(ramp, 2)})
        if k == 0 and playable == 0.0:
            break                      # dead board: no need for more seeds
    fitness = float(np.median(per_seed))
    return {"fitness": fitness, "per_seed": detail,
            "seed_fits": [round(f, 1) for f in per_seed],
            "n_seeds_run": len(per_seed)}


def eval_dual(pb_path: str, seed: int, es_seeds: int = 2,
              ppo_seeds: int = 2, ppo_steps: int = 30_000,
              mode: str = "min") -> dict:
    """Dual-learner fitness: combine ES and PPO learnability.

    mode="min":  min(ES, PPO) -- a board scores well only if BOTH
                 learners can learn it (robust learnability; guards
                 against boards that exploit one learner's quirks, which
                 the ES-vs-PPO comparison showed is the default outcome
                 of single-learner evolution).
    mode="diff": |ES - PPO| -- maximally learner-differentiating boards
                 (the closing proposal of Togelius & Schmidhuber 2008).

    Cost control: ES runs first (~4 min/seed vs ~15 min/seed for PPO).
    Under mode="min", ES fitness 0 short-circuits: min(0, PPO) = 0, so
    the PPO budget is only spent on boards that already pass the ES
    gate. Under mode="diff" both are always needed.
    """
    es = eval_learnability(pb_path, seed, n_seeds=es_seeds)
    es_fit = es["fitness"]
    out = {"es_fit": round(es_fit, 1), "es_per_seed": es["per_seed"],
           "mode": mode}
    if mode == "min" and es_fit <= 0.0:
        out.update(fitness=0.0, ppo_fit=None, ppo_skipped=True)
        return out

    from learn_ppo import ppo_learnability
    ppo_fits, ppo_detail = [], []
    for k in range(ppo_seeds):
        r = ppo_learnability(pb_path, seed=seed * 1000 + 500 + k,
                             steps=ppo_steps, baseline_eps=12,
                             holdout_eps=12, max_episode_steps=120)
        ppo_fits.append(r["fitness"])
        ppo_detail.append({"seed": k, "fit": round(r["fitness"], 1),
                           "holdout": r["holdout_median"],
                           "rnd_med": r["random_med"]})
    ppo_fit = float(np.median(ppo_fits))
    if mode == "min":
        fitness = min(es_fit, ppo_fit)
    else:
        fitness = abs(es_fit - ppo_fit)
    out.update(fitness=float(fitness), ppo_fit=round(ppo_fit, 1),
               ppo_per_seed=ppo_detail, ppo_skipped=False)
    return out


def eval_dual_diff(pb_path: str, seed: int) -> dict:
    return eval_dual(pb_path, seed, mode="diff")


EVALS = {"cheap": eval_cheap, "learnability": eval_learnability,
         "dual": eval_dual, "dual_diff": eval_dual_diff}


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
        # fresh inner seeds per evaluation: a fixed seed for the whole
        # run lets evolution overfit boards to particular serve
        # sequences (fourth Goodhart entry, measured 70 -> 30)
        eval_seed = int(rng.integers(1, 2**20))
        try:
            r = evaluate(pb, seed=eval_seed)
        except Exception as e:          # infrastructure failure != fitness 0
            r = {"fitness": -1.0, "error": str(e)}
        r.update(tag=tag, parent=parent, records=len(board.records),
                 eval_seed=eval_seed, wall_s=round(time.time() - t0, 1))
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
