"""PPO-based learnability estimator (the second inner learner).

Same measurement protocol as the ES estimator (tools/learn_es.py +
evolve.eval_learnability), different learner:

  1. random baseline: N fresh episodes of uniform-random play
  2. train PPO (small MLP) on the board for a fixed step budget
  3. HELD-OUT evaluation: the trained (stochastic) policy plays N fresh
     episodes; its median raw score is the learned estimate
  4. fitness = percentile(holdout within random) x magnitude ramp

Reward during training mirrors the ES shaping philosophy (log-scaled
score delta + small survival bonus); the *fitness* is always computed
from raw scores on held-out episodes, so ES and PPO verdicts are
directly comparable.

Usage:
  python3 tools/learn_ppo.py work/DEMO2.PB --steps 40000 --seed 0
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from pcs_env import PCSPinballEnv  # noqa: E402
from learn_es import features  # noqa: E402


def make_gym_env(pb_path: str, max_episode_steps: int = 150,
                 survival_bonus: float = 0.02):
    """Gymnasium wrapper: features as observation, shaped reward."""
    import gymnasium as gym

    class PCSGym(gym.Env):
        metadata = {"render_modes": []}

        def __init__(self):
            super().__init__()
            self.observation_space = gym.spaces.Box(-4.0, 4.0, shape=(7,),
                                                    dtype=np.float32)
            self.action_space = gym.spaces.Discrete(4)
            self.env = PCSPinballEnv(pb_path,
                                     max_episode_steps=max_episode_steps)
            self._score = 0

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            obs = self.env.reset()
            self._score = 0
            return features(obs).astype(np.float32), {}

        def step(self, action):
            obs, _, done, info = self.env.step(int(action))
            score = info.get("score", self._score)
            # log-scaled score delta + survival bonus (mirrors ES shaping)
            reward = (np.log1p(max(score, 0)) - np.log1p(max(self._score, 0))
                      + survival_bonus)
            self._score = score
            terminated = bool(done) and not info.get("dead", False)
            truncated = bool(info.get("dead", False))
            return (features(obs).astype(np.float32), float(reward),
                    terminated, truncated, {"score": score})

        def close(self):
            self.env.close()

    return PCSGym()


def _episodes(env: PCSPinballEnv, policy, n: int,
              rng: np.random.Generator) -> list[int]:
    """policy(obs)->action or None for random. Returns raw scores."""
    out = []
    for _ in range(n):
        obs = env.reset(plunger=int(rng.integers(140, 256)))
        done, score = False, 0
        while not done:
            a = policy(obs) if policy else int(rng.integers(0, 4))
            obs, _, done, info = env.step(a)
            score = info.get("score", score)
        out.append(score)
    return out


def ppo_learnability(pb_path: str, *, seed: int = 0, steps: int = 40_000,
                     baseline_eps: int = 15, holdout_eps: int = 15,
                     max_episode_steps: int = 150) -> dict:
    from stable_baselines3 import PPO

    rng = np.random.default_rng(seed)

    # 1. random baseline
    base_env = PCSPinballEnv(pb_path, max_episode_steps=max_episode_steps)
    try:
        base_scores = _episodes(base_env, None, baseline_eps, rng)
    finally:
        base_env.close()
    rnd_med = float(np.median(base_scores))
    if max(base_scores) == 0:
        # possibly dead; still attempt training (scoring may need skill),
        # but a cheap 5-episode probe decides whether to spend the budget
        pass

    # 2. train PPO
    genv = make_gym_env(pb_path, max_episode_steps)
    try:
        model = PPO("MlpPolicy", genv, seed=seed, verbose=0,
                    n_steps=512, batch_size=128, learning_rate=3e-4,
                    gamma=0.99, ent_coef=0.01,
                    policy_kwargs=dict(net_arch=[32, 32]))
        model.learn(total_timesteps=steps, progress_bar=False)

        # 3. held-out: stochastic policy, fresh episodes
        def pol(obs):
            a, _ = model.predict(features(obs).astype(np.float32),
                                 deterministic=False)
            return int(a)
        hold_scores = _episodes(genv.env, pol, holdout_eps, rng)
    finally:
        genv.close()

    # 4. gated fitness (identical formula to the ES estimator)
    holdout_med = float(np.median(hold_scores))
    rnd = np.array(base_scores, float)
    pct = float(100.0 * np.mean(rnd < holdout_med))
    gain = holdout_med - rnd_med
    ramp = float(np.clip(gain / max(150.0, rnd_med), 0.0, 1.0))
    playable = 1.0 if (max(base_scores) > 0 or max(hold_scores) > 0) else 0.0
    return {"fitness": float(pct * ramp * playable), "pct": pct,
            "ramp": round(ramp, 3), "holdout_median": holdout_med,
            "holdout_scores": hold_scores, "random_med": rnd_med,
            "random_scores": base_scores, "steps": steps}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("boards", nargs="+")
    ap.add_argument("--steps", type=int, default=40_000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    results = []
    for b in args.boards:
        r = ppo_learnability(b, seed=args.seed, steps=args.steps)
        r["board"] = os.path.basename(b)
        results.append(r)
        print(f"{r['board']}: fit={r['fitness']:.1f} "
              f"holdout_med={r['holdout_median']:.0f} "
              f"rnd_med={r['random_med']:.0f} pct={r['pct']:.0f} "
              f"ramp={r['ramp']}", flush=True)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2)
        print("wrote", args.out)


if __name__ == "__main__":
    main()
