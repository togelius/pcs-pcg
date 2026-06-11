"""Gym-style environment for PCS pinball running in MAME.

One env = one headless MAME process running harness/bridge.lua, talking
over a tiny file protocol on tmpfs. Episodes are single balls: reset()
restores a save state taken at the player-select screen (created once
per board by harness/make_state.lua), starts a game, serves and launches
the ball with a chosen plunger strength; step() applies a flipper action
for PCS_SKIP frames (default 10 frames = 6 actions/s) and then auto-skips
while the ball is outside the actionable region. The episode ends when
the ball drains (detected as: served back to its start position and
stationary).

Observation: np.array([x, y, dx, dy], float32), playfield coords.
Actions: 0 = none, 1 = left flipper, 2 = right flipper, 3 = both.
Reward: score delta (the engine's own score, read from RAM).

Usage:
    env = PCSPinballEnv("work/DEMO2.PB")
    obs = env.reset(plunger=200)
    obs, reward, done, info = env.step(1)
    env.close()
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dos33 import Dos33Disk  # noqa: E402

STATE_CACHE = os.path.join(ROOT, "work", "states")


def _build_eval_disk(pb_path: str, out_disk: str, base_disk: str) -> None:
    shutil.copy(base_disk, out_disk)
    disk = Dos33Disk.open(out_disk)
    with open(pb_path, "rb") as f:
        disk.write_binary("EVOLVED.PB", 0x4000, f.read())
    disk.save(out_disk)


def _mame_cmd(disk: str, script: str, extra: list[str] | None = None) -> list[str]:
    cmd = [
        os.path.join(ROOT, "scripts", "run_mame.sh"),
        "-flop1", disk, "-gameio", "joy",
        "-video", "none", "-sound", "none", "-nothrottle",
        "-autoboot_delay", "1", "-autoboot_script", script,
    ]
    return cmd + (extra or [])


def ensure_state(pb_path: str, base_disk: str | None = None,
                 force: bool = False) -> tuple[str, str]:
    """Create (or reuse cached) eval disk + save state for a board.

    Returns (disk_path, state_path). Cached by board content hash.
    """
    base_disk = base_disk or os.path.join(ROOT, "disks", "pcs.dsk")
    with open(pb_path, "rb") as f:
        digest = hashlib.sha1(f.read()).hexdigest()[:16]
    os.makedirs(STATE_CACHE, exist_ok=True)
    disk = os.path.join(STATE_CACHE, f"{digest}.dsk")
    state = os.path.join(STATE_CACHE, f"{digest}.sta")
    if force or not (os.path.exists(disk) and os.path.exists(state)):
        _build_eval_disk(pb_path, disk, base_disk)
        if os.path.exists(state):
            os.remove(state)
        env = dict(os.environ, PCS_BOARDNAME="EVOLVED", PCS_STATE=state)
        cmd = _mame_cmd(disk, os.path.join(ROOT, "harness", "make_state.lua"),
                        ["-seconds_to_run", "70"])
        subprocess.run(cmd, env=env, timeout=600,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not os.path.exists(state):
            raise RuntimeError(f"state creation failed for {pb_path}")
    return disk, state


class PCSPinballEnv:
    """Single-ball pinball episodes against a PCS board in MAME."""

    NUM_ACTIONS = 4

    def __init__(self, pb_path: str, *, base_disk: str | None = None,
                 skip: int = 10, act_y: int = 100, skip_cap: int = 300,
                 max_episode_steps: int = 600, rng: np.random.Generator | None = None):
        self.max_episode_steps = max_episode_steps
        self.rng = rng or np.random.default_rng()
        self._steps = 0
        self._seq = 0
        self._score = 0
        self._proc = None

        self.disk, self.state = ensure_state(pb_path, base_disk)
        self.ipc = tempfile.mkdtemp(prefix="pcs_ipc_", dir="/dev/shm")
        env = dict(os.environ,
                   PCS_IPC_DIR=self.ipc, PCS_STATE=self.state,
                   PCS_SKIP=str(skip), PCS_ACT_Y=str(act_y),
                   PCS_SKIP_CAP=str(skip_cap))
        self._proc = subprocess.Popen(
            _mame_cmd(self.disk, os.path.join(ROOT, "harness", "bridge.lua")),
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._wait_for(os.path.join(self.ipc, "ready"), timeout=120)

    # ---- protocol -------------------------------------------------------

    def _wait_for(self, path: str, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(path):
                return
            if self._proc and self._proc.poll() is not None:
                raise RuntimeError("MAME exited during startup")
            time.sleep(0.01)
        raise TimeoutError(f"timed out waiting for {path}")

    def _rpc(self, command: str, timeout: float = 120.0) -> str:
        self._seq += 1
        tmp = os.path.join(self.ipc, "cmd.tmp")
        with open(tmp, "w") as f:
            f.write(f"{self._seq} {command}")
        os.replace(tmp, os.path.join(self.ipc, "cmd"))
        rsp = os.path.join(self.ipc, "rsp")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with open(rsp) as f:
                    line = f.read().strip()
                if line.startswith(f"{self._seq} "):
                    return line.split(" ", 1)[1]
            except FileNotFoundError:
                pass
            if self._proc.poll() is not None:
                raise RuntimeError("MAME exited mid-episode")
            time.sleep(0.001)
        raise TimeoutError(f"no response to: {command}")

    # ---- gym-ish API ------------------------------------------------------

    def reset(self, plunger: int | None = None) -> np.ndarray:
        if plunger is None:
            plunger = int(self.rng.integers(140, 256))
        parts = self._rpc(f"reset {plunger}").split()
        if parts[0] != "ok":
            raise RuntimeError(f"reset failed: {' '.join(parts)}")
        x, y, dx, dy, score, in_play = map(int, parts[1:7])
        self._score = score
        self._steps = 0
        self._dead = in_play == 0   # launch-degenerate board
        return np.array([x, y, dx, dy], dtype=np.float32)

    def step(self, action: int):
        if getattr(self, "_dead", False):
            return (np.zeros(4, dtype=np.float32), 0.0, True,
                    {"score": self._score, "frames": 0, "dead": True})
        parts = self._rpc(f"step {int(action)}").split()
        x, y, dx, dy, score, done, frames = map(int, parts)
        reward = score - self._score
        self._score = score
        self._steps += 1
        terminated = bool(done)
        truncated = self._steps >= self.max_episode_steps
        obs = np.array([x, y, dx, dy], dtype=np.float32)
        info = {"score": score, "frames": frames}
        return obs, float(reward), terminated or truncated, info

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._rpc("quit", timeout=5)
            except Exception:
                self._proc.kill()
            self._proc.wait(timeout=10)
        shutil.rmtree(self.ipc, ignore_errors=True)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def main() -> None:
    """Smoke test: run random episodes and report stats + timing."""
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("board", help=".PB payload path")
    ap.add_argument("--episodes", type=int, default=3)
    args = ap.parse_args()

    t0 = time.time()
    env = PCSPinballEnv(args.board)
    print(f"env up in {time.time()-t0:.1f}s (incl. state creation if uncached)")
    rng = np.random.default_rng(0)
    for ep in range(args.episodes):
        t1 = time.time()
        obs = env.reset()
        total, steps = 0.0, 0
        done = False
        while not done:
            obs, r, done, info = env.step(int(rng.integers(0, 4)))
            total += r
            steps += 1
        print(f"ep{ep}: steps={steps} score={total:.0f} "
              f"frames={info['frames']} wall={time.time()-t1:.1f}s")
    env.close()


if __name__ == "__main__":
    main()
