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

- **g002_o0 / g004_o1 (the trivial hotspot boards).** [filled from the
  reduced-budget runs] Trained performance barely exceeds random,
  because the points come from the passive hotspot rather than from
  flipper skill — the action map carries little structure. This is the
  trivial-board signature.

The contrast — a 14x trainable gap on the ancestor versus ~no gap on
the evolved champion — is the learnability story in one lineage, and
the argument for swapping the outer fitness from `cheap` to
`learnability` for the real experiment.

Artifacts: `tools/train_policy.py` (train + action map),
`tools/analyze_policies.py` (combined figure + weight tables),
`harness/replay.lua` + `tools/record_replay.py` (gameplay video).
Boards and per-board metrics are in `work/evo_smoke/`.
