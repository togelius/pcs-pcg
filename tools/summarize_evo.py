"""Summarize an evolution run: trajectory, champions, fitness landscape.

Reads <rundir>/log.jsonl and produces:
  - a text summary (per-generation best, champion table)
  - <rundir>/trajectory.png: best-of-generation and the fitness of every
    evaluated board vs generation, coloured by verdict
    (learnable / trivial-gated / dead)

Usage: python3 tools/summarize_evo.py work/evo_learn [--out FIG.png]
"""

from __future__ import annotations

import argparse
import json
import os
import re

import numpy as np


def gen_of(tag: str) -> int:
    m = re.match(r"g(\d+)_", tag)
    return int(m.group(1)) if m else -1


def load(rundir: str) -> list[dict]:
    rows = []
    with open(os.path.join(rundir, "log.jsonl")) as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def verdict(r: dict) -> str:
    fit = r.get("fitness", 0)
    per = r.get("per_seed")
    if fit <= 0.5:
        if per:  # multi-seed record
            if all(d.get("holdout", 0) == 0 and d.get("rnd_med", 0) == 0
                   for d in per):
                return "dead"
            if any(d.get("ramp", 1) == 0 and d.get("pct", 0) >= 60 for d in per):
                return "trivial (gated)"
            return "unlearnable"
        if r.get("ramp", 1) == 0 and r.get("final3", 0) > 0 and r.get("pct", 0) >= 60:
            return "trivial (gated)"
        if r.get("final3", 0) == 0:
            return "dead"
        return "unlearnable"
    return "learnable"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rundir")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    rows = load(args.rundir)
    out = args.out or os.path.join(args.rundir, "trajectory.png")

    by_gen: dict[int, list] = {}
    for r in rows:
        by_gen.setdefault(gen_of(r["tag"]), []).append(r)

    print(f"=== {args.rundir}: {len(rows)} boards evaluated ===")
    counts = {}
    for r in rows:
        counts[verdict(r)] = counts.get(verdict(r), 0) + 1
    print("verdicts:", counts)
    best_per_gen = []
    for g in sorted(by_gen):
        fits = [r["fitness"] for r in by_gen[g]]
        b = max(by_gen[g], key=lambda r: r["fitness"])
        best_per_gen.append((g, b["fitness"]))
        extra = ""
        if b.get("per_seed"):
            hs = [d.get("holdout", 0) for d in b["per_seed"]]
            rs = [d.get("rnd_med", 0) for d in b["per_seed"]]
            extra = (f", holdout_meds={[int(h) for h in hs]} "
                     f"vs rnd_meds={[int(x) for x in rs]}")
        print(f"  gen {g}: n={len(fits)} best={b['fitness']:.1f} "
              f"({b['tag']}{extra})")

    cj = os.path.join(args.rundir, "champions.json")
    if os.path.exists(cj):
        print("\nchampions:")
        for c in json.load(open(cj)):
            print(f"  {c['tag']}: fit={c['fitness']:.1f} "
                  f"learned={c.get('final3',0):.0f} rnd_med={c.get('random_med',0):.0f} "
                  f"pct={c.get('pct',0):.0f}")

    # figure
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"learnable": "#2266cc", "trivial (gated)": "#cc8800",
              "dead": "#999999", "unlearnable": "#cc3333"}
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for v, c in colors.items():
        xs = [gen_of(r["tag"]) + np.random.uniform(-0.12, 0.12)
              for r in rows if verdict(r) == v]
        ys = [r["fitness"] for r in rows if verdict(r) == v]
        if xs:
            ax.scatter(xs, ys, s=36, color=c, edgecolor="k", linewidth=0.4,
                       label=f"{v} ({len(xs)})", zorder=3)
    gx = [g for g, _ in best_per_gen]
    gy = [f for _, f in best_per_gen]
    ax.plot(gx, gy, "k-o", lw=2, ms=5, label="best of generation", zorder=4)
    ax.set_xlabel("generation")
    ax.set_ylabel("learnability fitness (magnitude-gated percentile)")
    ax.set_title("Evolving boards for learnability")
    ax.set_ylim(-3, 103)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="center right")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
