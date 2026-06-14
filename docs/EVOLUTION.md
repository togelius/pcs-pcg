# Evolving boards: smoke run, lineage, and learned policies

First end-to-end run of the full automatic-game-design loop
(`tools/evolve.py`): (mu+lambda) over board genomes, here with the
**cheap** (random-play score) fitness as a mechanism test. mu=2, lam=3,
4 generations, seed 0.

## The loop works

Best fitness climbed 4.83 -> 6.75 over 4 generations; the population
discovered and propagated a high-scoring board. Generation, mutation,
selection, and improvement all function.

## A clean lineage

Record-set overlap (each board is a strict superset of its parent)
reconstructs the champion's ancestry:

```
g000_i0  (31 recs, random score ~130)
   -> g001_o2  (32 recs, ~172)
      -> g002_o0  (37 recs, ~102,780)   <- the jump: +5 parts
         -> g003_o0  (38 recs)
            -> g004_o1 (37 recs, ~103,585)  champion
```

The decisive event is `g002_o0`: a single mutation added five library
parts that formed a **scoring hotspot** — a bumper cluster the ball
falls into and racks up points in passively. Random-play score jumped
from ~172 to ~103k in one step.

## What the cheap fitness selected for — and why it matters

This is the cautionary result the 2008 paper predicts. The cheap
score-fitness rewarded raw points, so it evolved straight to a
**trivial** board: one where *random play already scores ~100k* because
the hotspot scores without skill. That is exactly the kind of board the
**learnability** fitness is designed to reject (high random baseline =>
low percentile gap => low learnability). The smoke run thus doubles as
a demonstration of why learnability fitness is the right objective:
score-fitness finds passive jackpots, not games.

## How the learned policies work

Policies are 7-feature linear softmax controllers
(phi = [1, x, y, dx, dy, x*dx, y*dy], all normalised) trained by the
same (1+lambda)-ES used in the estimator. The most legible summary is
the **action map**: the greedy action as a function of ball position
for a downward-moving ball.

- **g000_i0 (the learnable ancestor).** Trained policy scores **1817 vs
  ~130 random — a 14x improvement**, real skill differentiation. The
  action map shows a height-based rule: **hold the left flipper while
  the ball is high, switch to the right flipper as it descends into the
  flipper zone**, and never leave both idle. The switch boundary slopes
  (higher on the right), so the policy anticipates which flipper the
  ball is heading for. This is genuine positional control.

- **g002_o0 / g004_o1 (the trivial hotspot boards).** A direct probe —
  median score over 8 balls under three controllers — shows the points
  are **policy-independent**:

  | board | flippers idle | random | trained |
  |---|---|---|---|
  | g000_i0 (ancestor) | 120 | 94 | (needs longer episodes — see below) |
  | g002_o0 (hotspot) | 17,225 | 17,120 | 17,075 |
  | g004_o1 (champion) | 17,365 | 17,250 | 17,220 |

  (50-step episodes; absolute numbers scale with episode length, the
  ratios are the point.) On the hotspot boards idle ≈ random ≈ trained
  to within 1%: the ball falls into the bumper cluster and scores with
  the flippers switched **off**. Skill is irrelevant. Training a policy
  there is also prohibitively slow precisely because the ball survives
  passively in the hotspot for the whole episode — the cost *is* the
  triviality.

The contrast is the learnability story in one lineage: on the ancestor a
trained policy reaches ~1817 vs ~130 random at the full training budget
(**~14x**, real skill), while on the evolved champion the controller
makes no difference at all. That is exactly the failure mode the
learnability fitness is built to avoid, and the argument for swapping
the outer fitness from `cheap` to `learnability` for the real
experiment.

(Aside: at the very short 50-step budget even the ancestor scores little
under any policy — its scoring needs longer play to express, so
learnability there is budget-dependent. The hotspot triviality, by
contrast, holds at every budget.)

Artifacts: `tools/train_policy.py` (train + action map),
`tools/analyze_policies.py` (combined figure + weight tables),
`harness/replay.lua` + `tools/record_replay.py` (gameplay video).
Boards and per-board metrics are in `work/evo_smoke/`.

---

# The learnability experiment

The headline run: the same (mu+lambda) loop, now with **learnability
fitness** instead of cheap score fitness. This is the Togelius &
Schmidhuber 2008 idea proper — evolve boards a random player does poorly
on but a learner can improve on.

Setup: mu=3, lam=4, 6 generations, seed 0. Fitness =
percentile-of-random x magnitude ramp (see below), ~31 board
evaluations, `work/evo_learn/` (full log committed under
`docs/champions/learnability_run_log.jsonl`).

## A metric flaw, caught mid-run

The first attempt used the raw percentile (learned score's rank within
the board's random-play distribution), the metric validated in the
replication study. It has a hole: a **tight-variance trivial board**
fools it. One evolved board scored the 95th percentile while the learned
policy beat random by 1.6 points (learned 1085 vs random 1083) — every
random episode scored ~1083, so a marginally higher learned score still
"beat" 95% of them. The replication study missed this because its
trivial boards (passive hotspots) had *high*-variance random scores, so
learned ≈ random landed at the 50th percentile.

Fix: multiply the percentile by a **magnitude ramp** that scales 0->1 as
the learned policy beats the random *median* by max(150 pts, the median
itself). Validated against the flawed run's logged data: the trivial
board dropped 95 -> 0.1; every genuinely learnable board (random low,
learned high) was unchanged. The run was restarted with the corrected
metric.

## Result

![trajectory](figures/evo_learn_trajectory.png)

- **The search stays in the learnable region.** Every surviving board
  sits at the 80-100th percentile with a real learned-vs-random gap;
  26 of 31 evaluated boards were learnable.
- **Trivial, dead, and random-beats-learned boards are pinned at 0** and
  excluded from breeding — concentrated in the early random generations
  and bred out (gens 4-6 have far fewer zeros).
- **Best fitness:** held at 93.3 for five generations (percentile
  saturation makes the top gentle), then climbed to **100.0** in
  generation 6 (`g006_o1`): a board where the trained policy beats
  *every* random episode.
- The champions are textbook learnable boards: `g006_o1` (learned 4936
  vs **random median 26**), `g002_o0` (1675 vs 33), `g000_i3` (6218 vs
  452). On all of them a random player essentially fails and a learner
  succeeds.

## Cheap vs learnability: same shape, opposite games

| | cheap-fitness champion (`g002_o0`, evo_smoke) | learnability champion (`g006_o1`, evo_learn) |
|---|---|---|
| random play | **~103,000** (passive jackpot) | **median 26** (random fails) |
| trained play | ~same as random (skill irrelevant) | ~4936 (skill required) |
| what it is | a board that plays *itself* | a board you must *learn* |

![cheap champion](figures/champion_cheap.png)
![learnability champion](figures/champion_learnability.png)

The two champions look alike — chassis plus scattered parts — but they
are opposite games. Score fitness found a passive bumper cluster that
racks up 100k with the flippers switched off; learnability fitness found
a board where the points are only reachable with flipper skill. That is
the whole thesis of the 2008 paper, reproduced end to end in a 1983
game running in emulation: *learning progress, not score, is what
selects for a game rather than a toy.*

## Caveats / next steps

- The percentile saturates near the top, so selection pressure among
  good learnable boards is gentle (hence the long 93.3 plateau). A
  magnitude-forward metric (e.g. log learned/random gap) would sharpen
  top-end differentiation.
- Single seed, short run (6 generations). Multi-seed runs and a longer
  horizon would establish whether learnability keeps climbing or plateaus.
- A cheap pre-screen for unscoreable bouncy boards would cut the ~15 min
  spent rejecting each dead board in the random initial population.
