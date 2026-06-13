"""Calibration experiment: is learning progress a usable fitness signal?

Trains a tiny linear policy with a (1+4)-ES on a set of reference boards
and compares the learning curve against a random-policy baseline. This is
the estimator-calibration step for learnability-as-fitness (Togelius &
Schmidhuber 2008 transplanted to PCS pinball): before trusting learning
progress inside an outer evolution loop, it must separate boards we
already understand:

  NEW    empty board        -> no game starts; LP must be ~0
  DEMO1  launcher-trap      -> launch-degenerate; LP must be ~0
  DEMO2  Meta-Pin           -> rich playable board; LP should be > 0
  DEMO3  another demo board -> playable; LP comparison point

Policy: argmax over 4 actions of W @ phi(s), phi = 7 normalized features.
ES: (1+4) hill climber, Gaussian sigma, common-random-number plunger
draws per generation (parent and children see identical serves).
Fitness inside the ES: mean log(1+score) over E episodes (log tames the
heavy-tailed score distribution).

Usage:
  python3 tools/learn_es.py --boards work/NEW.PB work/DEMO1.PB \
      work/DEMO2.PB work/DEMO3.PB --gens 20 --out work/calibration.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from pcs_env import PCSPinballEnv  # noqa: E402

N_FEATURES = 7
N_ACTIONS = 4


def features(obs: np.ndarray) -> np.ndarray:
    x, y, dx, dy = obs
    xn, yn, dxn, dyn = x / 160.0, y / 192.0, dx / 16.0, dy / 16.0
    return np.array([1.0, xn, yn, dxn, dyn, xn * dxn, yn * dyn])


def act(weights: np.ndarray, obs: np.ndarray) -> int:
    return int(np.argmax(weights @ features(obs)))


def run_episode(env: PCSPinballEnv, weights: np.ndarray | None,
                plunger: int, rng: np.random.Generator) -> tuple[int, int, int, int]:
    """One ball. weights=None -> uniform random policy.

    Policies are softmax-stochastic (temperature 1) so that the fitness
    landscape is smooth; the rng seed is part of the job, so common
    random numbers across ES candidates still apply.

    Returns (score, frames_survived, cells_visited, scoring_spots):
    cells = distinct 16x16-px playfield cells the ball visited
    (exploration); scoring_spots = distinct cells in which score
    increments happened (proxy for "different items touched").
    """
    obs = env.reset(plunger=plunger)
    done, info, frames = False, {"score": 0}, 0
    cells, spots = set(), set()
    while not done:
        if weights is None:
            a = int(rng.integers(0, N_ACTIONS))
        else:
            z = weights @ features(obs)
            z = z - z.max()
            p = np.exp(z); p /= p.sum()
            a = int(rng.choice(N_ACTIONS, p=p))
        obs, r, done, info = env.step(a)
        frames += info.get("frames", 0)
        cell = (int(obs[0]) // 16, int(obs[1]) // 16)
        cells.add(cell)
        if r > 0:
            spots.add(cell)
    return info["score"], frames, len(cells), len(spots)


class Evaluator:
    """Thread pool of envs evaluating (weights, plunger) episode lists."""

    def __init__(self, pb_path: str, n_envs: int, max_episode_steps: int):
        # Create the save state once up front; otherwise N envs racing to
        # build it each run a ~70s make_state and can blow timeouts.
        from pcs_env import ensure_state
        ensure_state(pb_path)
        self.envs = [PCSPinballEnv(pb_path, max_episode_steps=max_episode_steps)
                     for _ in range(n_envs)]
        self.pool = ThreadPoolExecutor(max_workers=n_envs)
        self._free = list(self.envs)
        import threading
        self._lock = threading.Lock()

    def _episode(self, weights, plunger, seed):
        with self._lock:
            env = self._free.pop()
        try:
            rng = np.random.default_rng(seed)
            return run_episode(env, weights, plunger, rng)
        finally:
            with self._lock:
                self._free.append(env)

    def scores(self, jobs: list[tuple]) -> list[int]:
        """jobs: list of (weights|None, plunger, seed) -> scores in order."""
        futures = [self.pool.submit(self._episode, *j) for j in jobs]
        return [f.result() for f in futures]

    def close(self):
        self.pool.shutdown(wait=True)
        for e in self.envs:
            e.close()


SURVIVAL_WEIGHT = 0.5
EXPLORE_WEIGHT = 0.2     # distinct playfield cells visited
SPOTS_WEIGHT = 0.2       # distinct cells where scoring happened

def fitness(results: list[tuple[int, int, int, int]]) -> float:
    """Mean of log score plus minor shaped terms.

    Survival (frames) has far lower variance than score and ball-keeping
    is the core learnable skill; exploration (cells visited) and variety
    (distinct scoring spots ~ different items touched) are deliberately
    minor: with these weights their combined ceiling is ~2 fitness units
    against ~10 for a good score.
    """
    return float(np.mean([np.log1p(s)
                          + SURVIVAL_WEIGHT * np.log1p(f)
                          + EXPLORE_WEIGHT * np.log1p(c)
                          + SPOTS_WEIGHT * np.log1p(k)
                          for s, f, c, k in results]))


def calibrate_board(pb_path: str, *, gens: int, children: int = 4,
                    episodes: int = 2, sigma: float = 0.3,
                    n_envs: int = 4, baseline_eps: int = 20,
                    max_episode_steps: int = 300, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    ev = Evaluator(pb_path, n_envs, max_episode_steps)
    t0 = time.time()
    try:
        # random baseline
        base_jobs = [(None, int(rng.integers(140, 256)), int(rng.integers(2**31)))
                     for _ in range(baseline_eps)]
        base_results = ev.scores(base_jobs)
        base_scores = [r[0] for r in base_results]

        # (1+4)-ES on the linear policy
        parent = rng.normal(0, 0.1, size=(N_ACTIONS, N_FEATURES))
        curve_raw, curve_fit = [], []
        for g in range(gens):
            # common random numbers: identical (plunger, policy-seed)
            # pairs for the parent and every child this generation
            draws = [(int(rng.integers(140, 256)), int(rng.integers(2**31)))
                     for _ in range(episodes)]
            cands = [parent] + [parent + rng.normal(0, sigma, parent.shape)
                                for _ in range(children)]
            jobs, owner = [], []
            for ci, w in enumerate(cands):
                for p, s in draws:
                    jobs.append((w, p, s))
                    owner.append(ci)
            results = ev.scores(jobs)
            by_cand = [[] for _ in cands]
            for ci, r in zip(owner, results):
                by_cand[ci].append(r)
            fits = [fitness(r) for r in by_cand]
            best = int(np.argmax(fits[1:])) + 1
            # replace only on a clear margin (noise guard)
            if fits[best] > fits[0] + 0.05:
                parent = cands[best]
                chosen = best
            else:
                chosen = 0
            curve_fit.append(fits[chosen])
            curve_raw.append(float(np.mean([r[0] for r in by_cand[chosen]])))

        rnd_mean = float(np.mean(base_scores))
        rnd_std = float(np.std(base_scores))
        final3 = float(np.mean(curve_raw[-3:])) if curve_raw else 0.0
        lp_z = (final3 - rnd_mean) / (rnd_std + 1.0)
        auc = float(np.mean([c - rnd_mean for c in curve_raw]))
        return {
            "board": os.path.basename(pb_path),
            "random_mean": rnd_mean, "random_std": rnd_std,
            "random_scores": base_scores,
            "curve_raw": curve_raw, "curve_fit": curve_fit,
            "final3": final3, "lp_z": lp_z, "auc_above_random": auc,
            "wall_s": round(time.time() - t0, 1),
        }
    finally:
        ev.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--boards", nargs="+", required=True)
    ap.add_argument("--gens", type=int, default=20)
    ap.add_argument("--episodes", type=int, default=4)
    ap.add_argument("--children", type=int, default=4)
    ap.add_argument("--sigma", type=float, default=0.3)
    ap.add_argument("--envs", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    results = []
    for b in args.boards:
        print(f"=== {b} ===", flush=True)
        r = calibrate_board(b, gens=args.gens, episodes=args.episodes,
                            children=args.children, sigma=args.sigma,
                            n_envs=args.envs, seed=args.seed)
        results.append(r)
        print(f"  random: {r['random_mean']:.0f} ± {r['random_std']:.0f}   "
              f"final3: {r['final3']:.0f}   LP_z: {r['lp_z']:.2f}   "
              f"AUC>rnd: {r['auc_above_random']:.0f}   "
              f"({r['wall_s']}s)", flush=True)
        print(f"  curve: {[int(c) for c in r['curve_raw']]}", flush=True)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
