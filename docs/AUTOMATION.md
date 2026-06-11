# Driving PCS headlessly: the working pipeline

PCS is now driven fully autonomously in MAME: boot → load a named board
through the real LOAD dialog → PLAY GAME → play with scripted/random
input → read fitness from memory. `tools/evaluate.py` wraps the whole
loop; `harness/autoplay.lua` is the in-emulator driver.

```
.PB file ──tools/dos33.py──▶ EVOLVED.PB on a copy of pcs.dsk
        ──MAME + harness/autoplay.lua──▶ per-frame CSV + final score
        ──tools/evaluate.py──▶ {score, ball_lifetime, activity, coverage}
```

Validated end to end: DEMO2 ("Meta-Pin") injected as `EVOLVED.PB` scored
40,278 over 6,000 play frames with random paced flippers, ball coverage
of 231 playfield cells. Wall-clock ≈ 2.5 min per evaluation (mostly the
~50 emulated seconds of boot/UI, run at ~30–60× real time).

## The verified facts the driver is built on

Hard-won, all measured directly:

- **Buttons: `set_value(1)` = pressed** ($C061 bit 7 high), `0`/default
  = released. An early measurement artifact suggested the opposite, and
  the inverted polarity (holding buttons down constantly) produced a
  session's worth of confusing behavior. The clean test is steady-state:
  set once, read several frames later.
- **Boot timeline** (deterministic for this disk + `-autoboot_delay 1`):
  Apple ][ banner ~f200, boot splash (BudgeCo logo) ~f340–650, editor
  ready ~f660. All UI interaction must come after that. The logo also
  reappears during module swaps — it's a loading screen, and early on we
  repeatedly mistook it for triggered gameplay or attract mode.
- **Cursor**: position lives at `$82` (Y), `$83` (X÷7), `$84` (X mod 7);
  movement is paddle-velocity-driven, so with the joystick centered (128)
  a poked position is stable. Menus dispatch **on button release** while
  the cursor is inside the item rect (`DOMENU`/`SELECT` in CDRAW.S).
- **UI geometry** (from EDIT.S/DISK.S, confirmed on screen): DISK icon
  x 252–279, y 171–184; disk-screen LOAD box y 75–85 and PLAY GAME box
  y 147–167 at x 165–195. The LOAD dialog takes typed input readable via
  MAME's natural keyboard (`natkeyboard:post`), appends `.PB` itself,
  and the "INSERT GAME DISK" prompt accepts any key.
- **The disk menu's PLAY GAME** runs the real game engine
  (`SWAPUSER` → `PLAYGAME` → `DISKPLAY`, `GAMEMODE=0`): attract delay
  (~5 s), a player-select that starts on button press+release, then a
  5-ball game that loops back to attract forever. ESC would quit to the
  editor — never send it.
- **Plunger**: hold button 0 with paddle 1 setting spring strength,
  release to fire. Button 0 doubles as the left flipper, so the driver
  separates plunger cycles from flipper activity.
- **Score**: player 1's score is 8 decimal digit bytes (MSD first) at
  **`$8952`** (found by diffing RAM dumps mid-game; the displayed score
  appends a fixed trailing 0, i.e. display = array × 10). It resets when
  the game ends and attract restarts, so the harness tracks the maximum.
  Ball state: bbox at `$D2–$D5`, signed velocity at `$D6/$D7` (positive
  `BDY` = upward).

## Traps documented for posterity

- **Do not poke a board into `$4000` while the editor is live.** The
  editor's object heap sits directly after the current board's data;
  overwriting it corrupts every subsequent draw. Poking after PLAY GAME
  dispatch works for physics but skips the visual redraw (the playfield
  image carries over). The clean path — used by the pipeline — is the
  real LOAD dialog, which restores visuals (`DECOMPRESS`) and rebuilds
  state coherently.
- **The shipped 4am-crack binary differs from the GitHub source** (e.g.
  it has a FORMAT menu item; code labels are shifted). Source is the map,
  not the territory: addresses taken from source EQUs held up, but
  pattern-matching source code sequences against the binary fails.
- **A synthesized standalone game disk** (dump `$177D–$8EFF` + BSAVE +
  `BRUN`, replicating MAKE) boots but crashes — the engine image needs
  whatever `SWAPUSER` loads beyond what's resident during editor test
  play. Dead end for now; the disk-menu PLAY GAME path made it
  unnecessary. (`tools/dos33.py` retains Applesoft/typed-file writing
  from this experiment.)

## Knobs

`harness/autoplay.lua` env vars: `PCS_BOARDNAME` (catalog name to load,
no `.PB`), `PCS_FRAMES` (play length), `PCS_SEED` (action RNG),
`PCS_LOG`, `PCS_SNAP`. Video: pass `-aviwrite out.avi` to MAME (then
ffmpeg → MP4/GIF; see work/anim examples).
