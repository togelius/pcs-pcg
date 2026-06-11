#!/usr/bin/env python3
"""Evaluate a PCS board by running it headlessly in MAME and reading fitness.

Pipeline (see docs/AUTOMATION.md for the current automation status):

  1. Inject a .PB board onto a working copy of the PCS disk (as a chosen
     filename) using tools/dos33.py.
  2. Launch MAME headless with harness/play.lua, which drives PCS and logs
     per-frame fitness state to a CSV.
  3. Parse the CSV and return a fitness summary (cumulative score, ball
     activity, lifetime).

This is the orchestration layer for a search-based PCG loop: a generator
produces a .PB (see tools/pb.py), evaluate() scores it, and the optimiser
selects/mutates accordingly.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dos33 import Dos33Disk  # noqa: E402


@dataclass
class Fitness:
    score: int
    frames: int
    ball_lifetime: int      # frames with the ball in motion
    activity: float         # mean |velocity| while moving
    coverage: int           # distinct ~8px playfield cells visited
    log_path: str


def inject_board(base_disk: str, pb_path: str, out_disk: str,
                 name: str = "EVOLVED.PB") -> None:
    """Write a .PB file onto a copy of base_disk as `name`."""
    shutil.copy(base_disk, out_disk)
    disk = Dos33Disk.open(out_disk)
    with open(pb_path, "rb") as f:
        payload = f.read()
    # .PB payloads load at $4000; tools/pb.py emits raw payloads.
    disk.write_binary(name, 0x4000, payload)
    disk.save(out_disk)


def run_mame(disk: str, frames: int, log_path: str,
             board_name: str = "EVOLVED", seed: int = 1,
             snapshot: str | None = None, avi: str | None = None,
             timeout: int = 600) -> None:
    env = dict(os.environ, PCS_LOG=log_path, PCS_FRAMES=str(frames),
               PCS_BOARDNAME=board_name, PCS_SEED=str(seed))
    if snapshot:
        env["PCS_SNAP"] = snapshot
    cmd = [
        os.path.join(ROOT, "scripts", "run_mame.sh"),
        "-flop1", disk, "-gameio", "joy",
        "-video", "none", "-sound", "none", "-nothrottle",
        # UI phase takes ~50 emulated seconds before play starts
        "-seconds_to_run", str(frames // 60 + 60),
        "-autoboot_delay", "1",
        "-autoboot_script", os.path.join(ROOT, "harness", "autoplay.lua"),
    ]
    if avi:
        cmd += ["-aviwrite", avi]
    subprocess.run(cmd, env=env, timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def summarize(log_path: str) -> Fitness:
    rows = []
    with open(log_path) as f:
        for r in csv.DictReader(f):
            rows.append({k: int(v) for k, v in r.items()})
    if not rows:
        return Fitness(0, 0, 0, 0.0, 0, log_path)

    score = max(r["score"] for r in rows)
    moving = [r for r in rows if r["balldx"] or r["balldy"]]
    lifetime = len(moving)
    activity = (sum(abs(r["balldx"]) + abs(r["balldy"]) for r in moving)
                / lifetime) if moving else 0.0
    cells = {(r["ballx"] // 8, r["bally"] // 8) for r in moving}
    return Fitness(score=score, frames=len(rows), ball_lifetime=lifetime,
                   activity=round(activity, 2), coverage=len(cells),
                   log_path=log_path)


def evaluate(pb_path: str, *, base_disk: str | None = None,
             frames: int = 6000, seed: int = 1,
             workdir: str | None = None, keep: bool = False,
             avi: str | None = None) -> Fitness:
    base_disk = base_disk or os.path.join(ROOT, "disks", "pcs.dsk")
    tmp = workdir or tempfile.mkdtemp(prefix="pcs_eval_")
    os.makedirs(tmp, exist_ok=True)
    disk = os.path.join(tmp, "eval.dsk")
    log = os.path.join(tmp, "state.csv")
    inject_board(base_disk, pb_path, disk)
    run_mame(disk, frames, log, seed=seed, avi=avi)
    fit = summarize(log)
    if not keep and workdir is None:
        shutil.rmtree(tmp, ignore_errors=True)
    return fit


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("board", help="path to a .PB board file (raw $4000 payload)")
    ap.add_argument("--frames", type=int, default=6000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--base-disk", default=None)
    ap.add_argument("--workdir", default=None,
                    help="keep intermediates here instead of a temp dir")
    ap.add_argument("--avi", default=None, help="record gameplay video here")
    args = ap.parse_args()
    fit = evaluate(args.board, base_disk=args.base_disk, frames=args.frames,
                   seed=args.seed, workdir=args.workdir, keep=True,
                   avi=args.avi)
    for k, v in asdict(fit).items():
        print(f"{k:14s} {v}")


if __name__ == "__main__":
    main()
