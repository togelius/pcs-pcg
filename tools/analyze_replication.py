#!/usr/bin/env python3
"""Aggregate the replication study with robust learnability metrics.

LP_z ((final3 - random_mean)/(random_std+1)) breaks down when a single
unattended-jackpot random episode inflates the std (pinball score
distributions are extremely heavy-tailed). Two robust alternatives,
computed from the stored per-run data:

  pct    percentile of the learned final3 within the random-episode
         score distribution (rank-based; 50 = no better than median
         random, 100 = above every random episode)
  z_med  (final3 - random_median) / (IQR + 1)

Reads work/replication/*.json, prints per-run and aggregate tables.
"""

from __future__ import annotations

import glob
import json
import os
import re

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REP = os.path.join(ROOT, "work", "replication")


def robust_metrics(r: dict) -> dict:
    rnd = np.array(r["random_scores"], dtype=float)
    final3 = r["final3"]
    pct = float(100.0 * np.mean(rnd < final3)) if len(rnd) else 0.0
    med = float(np.median(rnd))
    iqr = float(np.percentile(rnd, 75) - np.percentile(rnd, 25))
    z_med = (final3 - med) / (iqr + 1.0)
    return {"pct": pct, "z_med": z_med, "median": med, "iqr": iqr}


def main() -> None:
    rows = []
    for path in sorted(glob.glob(os.path.join(REP, "*.json"))):
        name = os.path.basename(path)[:-5]
        m = re.match(r"(\w+?)_(std|long)_s(\d+)", name)
        with open(path) as f:
            r = json.load(f)
        rm = robust_metrics(r)
        rows.append({**r, **rm, "board": m.group(1), "kind": m.group(2),
                     "seed": int(m.group(3))})
        print(f"{name:18s} lp_z={r['lp_z']:+6.2f}  pct={rm['pct']:5.1f}  "
              f"z_med={rm['z_med']:+8.2f}  final3={r['final3']:9.0f}  "
              f"rnd_med={rm['median']:7.0f}")

    print("\n=== AGGREGATE ===")
    for board in ("NEW", "DEMO1", "DEMO2", "DEMO3"):
        for kind in ("std", "long"):
            sel = [x for x in rows if x["board"] == board and x["kind"] == kind]
            if not sel:
                continue
            pcts = [x["pct"] for x in sel]
            zs = [x["lp_z"] for x in sel]
            print(f"{board:6s} {kind:5s} n={len(sel)}  "
                  f"pct: {np.mean(pcts):5.1f} ± {np.std(pcts):4.1f}  "
                  f"lp_z: {np.mean(zs):+5.2f} ± {np.std(zs):4.2f}")


if __name__ == "__main__":
    main()
