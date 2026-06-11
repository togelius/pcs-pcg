# Driving PCS headlessly: findings and status

This documents what has been **verified** about automating Pinball
Construction Set under MAME, and what remains open. It is deliberately
precise about the difference, because the emulator hides a few traps.

## Verified primitives

These are confirmed by experiment (`harness/play.lua` builds on them):

- **Memory + CPU control.** MAME 0.264 Lua exposes
  `maincpu.spaces["program"]` for `read_u8`/`write_u8`,
  `install_write_tap`, the `state["PC"]` register, screenshots, and
  per-frame hooks. All work headlessly.
- **Cursor positioning.** The pointer is held in zero page
  (`CURSORY=$82`, `CURSORXDIV7=$83`, `CURSORXMOD7=$84`). The editor's
  `UPDATECRSR` adds a *paddle-relative* delta each frame, so with the
  emulated paddles centered (`joystick=128`) the delta is zero and a
  poked cursor position stays put. Verified: poking `$83=0x24,$82=120`
  holds across hundreds of frames.
- **Button polarity is inverted.** For the game-I/O buttons,
  `field:set_value(0)` drives `$C061`/`$C062` bit 7 **high** ("pressed"),
  and `set_value(1)` reads back low ("released"). This was measured
  directly and is the opposite of the intuitive mapping — it bit every
  early attempt.
- **Toolbar geometry** (from `EDIT.S`): the PLAY icon (`PLAYB`) is at
  `xdiv7=$24`, `y 115..127`; the DISK icon (`DISKB`) at `xdiv7=$24`,
  `y 171..184`; the whole toolbar (`TOOLB`) is `xdiv7=$24`, full height.
  The editor `MAIN` loop waits for a button press, then routes the click
  to the playfield (`TABLEB`) or the command menu (`DOCMD`/`DOMENU`).

## Open items

- **Attract mode.** Left idle, PCS periodically shows the BudgeCo logo
  (a screensaver/attract) and returns to the editor. This was initially
  mistaken for triggered play. Reliable menu automation has to account
  for the editor not always sitting in its input loop.
- **Two different play engines.** The editor's *test play*
  (`PLAYSTART`, reached from the PLAY icon) and the *standalone game*
  built by the MAKE command (the `RUN.S` engine) are different overlays
  with **different memory layouts**. The clean zero-page fitness map in
  `docs/MEMORY_MAP.md` (ball at `$D2..$D7`, score delta at `$CB`) is the
  **standalone** engine's. During editor test-play those addresses are
  static; the live state instead churns in the `$80..$A6` region (the
  editor's object/cursor workspace). The standalone engine is therefore
  the better, more reproducible evaluation target.
- **Loading a specific board.** PCS does **not** auto-load `NEW.PB` at
  boot — it starts with an empty editor — so evaluating a specific board
  requires either driving DISK → LOAD → type filename → Return, or
  building/booting a standalone game from the board. `tools/dos33.py`
  injects the `.PB` onto the disk; the remaining step is the reliable
  menu drive (or direct memory injection into `$4000`).

## Recommended next step

Target the **standalone game** path:

1. Inject the board, drive PCS's MAKE once to BSAVE a standalone game
   (or replicate MAKE's packaging from `DISK.S`, which simply BSAVEs
   `GAMEBTM..GAMETOP` and applies a small patch table).
2. Boot the standalone game directly — no editor, no attract, and its
   memory matches `docs/MEMORY_MAP.md`, so `harness/play.lua`'s fitness
   reading applies as-is.

`tools/evaluate.py` already implements the surrounding loop (inject →
run headless → parse CSV → fitness); only the in-emulator drive sequence
in `harness/play.lua` (`drive()`) needs to be pointed at whichever play
path is chosen.
