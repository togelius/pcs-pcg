# Board genotype, generator, and mutator

`tools/board.py` implements the evolvable board representation chosen in
the design discussion: a **fixed playable chassis plus a list of typed
part records**, operating directly on the `.PB` record structure.

## Why this shape

- **Parts as opaque templates.** Part records (bumpers, kickers,
  spinners...) carry trailing behavior bytes we have not fully decoded.
  We never synthesize them; we *harvest* complete records from the five
  demo boards and relocate them by shifting vertex bytes only. Every
  part on a generated board is therefore a byte-exact copy of a part
  that demonstrably works.
- **Chassis = the protected records of a demo board** (default: Meta-Pin/
  DEMO2): outline, launcher-lane furniture, ball start, flippers,
  slings, drain. Heuristic classification: many-vertex polygons
  (outline/walls), anything touching the lane column (x ≥ 130), the
  flipper/drain band (y ≥ 150). The chassis guarantees the env's
  reset/launch/drain machinery works on every genome.
- **Append-only structure.** The LOGIC wiring table (24 bytes at $4000,
  copied verbatim from the chassis) may reference object indices. New
  parts are appended and only mutation-added parts may be deleted, so
  chassis indices never shift. (Decoding LOGIC to allow free deletion
  and rewiring is future work.)
- **Compiled tail = RLE of a blank screen.** The real LOAD path runs
  DECOMPRESS then DRAWDISPLAY, and DRAWDISPLAY draws every object from
  the record list — so generated boards render correctly without us
  rasterising a hi-res screen in Python.

## Operators (`mutate`)

| op | description | guard |
|---|---|---|
| add | place a random library part | rejection-sampled position: inside field (x 6–126, y 14–148), 3px clearance, no bbox overlap |
| move | translate a movable record ±20px | revert if it no longer fits |
| dup | copy an existing movable record to a new spot | as add |
| jitter | nudge one vertex of a pure polygon ±3px | parts move rigidly only |
| del | remove a mutation-added part | chassis/original parts never deleted |
| recolor | change a pure polygon's fill | part fills untouched (may encode behavior) |

`generate(rng)` = chassis + 4–12 random library parts.

## Validation chain

A genome is checked at three levels, each catching what the previous
can't: (1) geometric constraints at mutation time; (2) `compile()`
bounds (payload must fit below the collision-table region at $6F40);
(3) the environment itself — launch-degenerate or unplayable boards are
detected at reset and receive floor fitness, exactly like DEMO1's lane
trap in the calibration.

## Outer loop

`tools/evolve.py`: (mu+lambda) over genomes with pluggable fitness —
`cheap` (random-play statistics, ~30–60 s/board, for smoke tests and
seeding) and `learnability` (the calibrated percentile-of-random
estimator at reduced budget, ~3–4 min/board). Every evaluated board and
its metrics go to the run directory as `.pb` + JSONL.

## Validation (in-emulator)

All confirmed against real MAME runs:

- **Round-trip is byte-exact.** Parsing then recompiling each of the six
  shipped boards reproduces the logic/wset/size-table/records region
  byte-for-byte (the tail differs by design — we emit a blank-screen
  RLE).
- **The blank tail renders and plays.** A recompiled DEMO2 (blank tail)
  loads, draws the full board, and plays — one random episode scored
  257k. Collisions are rebuilt from the object records by DRAWDISPLAY,
  so the screen bitmap is purely cosmetic and a blank background is
  fine. Screenshots of a generated board and a DEMO2 mutant show the
  chassis plus placed parts rendered correctly.
- **Generator and mutator produce playable boards.** 87 part templates
  harvested from the five demo boards. Across 5 generated boards and 5
  DEMO2 mutants, **60/60 episodes launched** — the chassis guarantees a
  serveable ball. Generated (random-layout) boards score low under
  random play (median 0–126); DEMO2 mutants retain the rich chassis's
  scoring potential (max 12k–244k). A generated board that launches but
  never scores (GEN1) is a legitimate low-fitness genome, exactly what
  the learnability fitness is meant to cull.
