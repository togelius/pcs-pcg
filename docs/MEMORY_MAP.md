# PCS runtime memory map (fitness signals)

Zero-page layout while a board is **playing**, derived from
`third_party/PCS_AppleII/source_disc2/RUN.S` (the `EQU` chain starting at
`RUNLEN = $C0`) and `source_disc1/RUN2.S` (score display). These are the
addresses the harness reads for fitness.

## Zero page during play

| Addr | Symbol | Meaning |
|---|---|---|
| `$C0` | `RUNLEN` | RLE run length (internal) |
| `$C1` | `PDL0` | paddle 0 |
| `$C2` | `PDL1` | paddle 1 |
| `$C3` | `BTN0` | button 0 (flipper) |
| `$C4` | `BTN1` | button 1 (flipper) |
| `$C5`–`$C6` | `PTM1`/`PTM2` | paddle timers |
| `$C7` | `KBD` | last key |
| `$C8` | `SERIES` | sound series |
| `$C9` | `SLICE` | sound slice |
| `$CA` | `ST` | (score temp) |
| **`$CB`** | **`DSCORE`** | **score delta**, flushed to the player score then zeroed each frame |
| `$CC` | `DBONUS` | bonus delta |
| `$CD` | `BMULT` | bonus multiplier (1–5) |
| `$CE` | `INITMODE` | nonzero while a ball is launching/initialising |
| `$CF`–`$D0` | `SCB` | score base pointer |
| `$D1` | `BST` | ball status/state |
| **`$D2`** | **`X1`** | **ball bbox left** (hi-res X) |
| **`$D3`** | **`Y1`** | **ball bbox top** (hi-res Y) |
| `$D4` | `X2` | ball bbox right |
| `$D5` | `Y2` | ball bbox bottom |
| **`$D6`** | **`BDX`** | **ball velocity X** (signed) |
| **`$D7`** | **`BDY`** | **ball velocity Y** (signed) |
| `$D8`–`$D9` | `BXACC`/`BYACC` | sub-pixel accumulators |
| `$DA` | `BMOVE` | ball-moved flag |
| `$DB` | `MIDX` | playfield mid X |

Also useful: `OBJ` = `$91`/`$92` (current object pointer during the
per-frame object scan), and the board object count at `PBDATA = $401C`.

## Score

The visible score is **not** a single integer. `DSCORE` (`$CB`) is a
0–255 delta that `DOSCORE`/`TALLY` (RUN2.S) flush into a per-player
array of decimal digits (one digit per byte), then reset to 0.

**Player 1's digit array is at `$8952`** — 8 bytes, most significant
digit first (located empirically by diffing RAM dumps during a scoring
game; the shipped binary's labels differ from the GitHub source). The
on-screen score appends a fixed trailing zero, i.e. displayed value =
array value × 10. The array resets when the 5-ball game ends and the
attract loop restarts, so `harness/autoplay.lua` tracks the maximum
value seen. This direct read replaced an earlier `DSCORE` write-tap
approach, which proved unreliable.

## Suggested fitness terms

- **Score**: `M.score` (tapped cumulative `DSCORE`). Primary signal.
- **Ball lifetime**: frames until the ball drains (ball leaves play /
  `BST` enters a drain state). Rewards boards where the ball survives.
- **Activity / coverage**: variance of `(ball.x, ball.y)` over the run,
  or count of distinct playfield cells visited — rewards boards that
  send the ball around rather than into a dead pocket.
- **Velocity**: time-averaged `|(BDX, BDY)|` — penalises boards where the
  ball gets stuck.

> The addresses are confirmed against source. Their *live* values still
> need to be validated against on-screen play once the harness can start
> a game (drive `LOAD` then `PLAY` from the editor menu). At the boot
> editor screen these locations hold uninitialised values, as expected.
