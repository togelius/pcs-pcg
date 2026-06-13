# pcs-pcg — Search-Based PCG for Pinball Construction Set (1982)

Research project: evolving pinball boards for Bill Budge's *Pinball
Construction Set* (Apple II) using search-based procedural content
generation, with the original game running in an emulator as the
evaluation environment.

## Emulation stack

| Component | Choice | Why |
|---|---|---|
| Emulator | MAME (`apple2p` driver) | Lua scripting, headless operation, save states, deterministic replay |
| Machine | 48K Apple II+ with Disk II | The machine PCS shipped for |
| Game dump | 4am & san inc crack (`.dsk`) | Clean, unprotected preservation copy; catalogued in MAME's `apple2_flop_clcracked` software list as `pinbcset` |
| Ground truth | [billbudge/PCS_AppleII](https://github.com/billbudge/PCS_AppleII) | Bill Budge's released source — board file format and physics can be read instead of reverse-engineered |

Headless evaluation runs at 30–60× real time per instance
(`-video none -sound none -nothrottle`), and MAME still renders and can
snapshot the screen, which is useful for debugging and figures.

## Setup

```sh
scripts/setup.sh        # installs MAME, assembles + verifies ROMs,
                        # fetches DOS 3.3 test disk and PCS source code
scripts/smoke_test.sh   # headless boot tests with screenshots in work/smoke/
```

The Apple II+ ROM set is assembled from files published in the
[AppleWin](https://github.com/AppleWin/AppleWin) and
[izapple2](https://github.com/ivanizag/izapple2) repositories and every
file is verified against the exact CRCs MAME expects (`-verifyroms`
clean except the Votrax speech ROM, which belongs to the default
Mockingboard card that `scripts/run_mame.sh` removes).

### The PCS disk image

The game disk is the one thing setup cannot fetch automatically in a
Claude Code cloud session: it lives on archive.org, which the default
network policy blocks. Two options:

1. Add `archive.org` (and `*.us.archive.org`) to the environment's
   allowed domains, then run `scripts/get_pcs_disk.sh`, or
2. Download it yourself from
   <https://archive.org/details/PinballConstructionSet4amCrack> and put
   it at `disks/pcs.dsk`.

Either way the script verifies it against the canonical fingerprint
(143360 bytes, sha1 `9a809dd030da376f17857b4cd74fba4cad68f01c`).

The same archive.org item also contains a **work disk**
(`disks/pcs_work.dsk`) — PCS saves boards to a separate disk, so this
is where generated/evolved boards will live during evaluation.

## Running

```sh
# Headless, auto-exit after 60 emulated seconds, screenshot on exit:
scripts/run_mame.sh -flop1 disks/pcs.dsk \
    -video none -sound none -nothrottle -seconds_to_run 60

# With a Lua script driving the evaluation:
scripts/run_mame.sh -flop1 disks/pcs.dsk \
    -video none -sound none -nothrottle -autoboot_script eval.lua
```

## Tools

| Path | What it does |
|---|---|
| `tools/dos33.py` | Read/write DOS 3.3 `.dsk` images — catalog, extract, and inject `.PB` files. Round-trip verified; injected boards boot in PCS. |
| `tools/pb.py` | Parse `.PB` board files into polygons (validated against the demo boards). |
| `harness/autoplay.lua` | **The play driver.** Boots PCS, drives the real UI (DISK → LOAD → types the board name → PLAY GAME), then plays with paced random flippers and plunger cycles, logging ball state and the live score. |
| `harness/pcs.lua` | Minimal harness — memory reads, CSV logging, screenshots (kept for ad-hoc probing). |
| `tools/pcs_env.py` | **Gym-style RL environment** — save-state fast reset (0.2–1.6 s/episode), deterministic, single-ball episodes with geometric drain detection and launch-degeneracy pre-filtering. See `docs/RL_ENV.md`. |
| `harness/make_state.lua` | One-time per-board save-state creation at the player-select screen. |
| `harness/bridge.lua` | In-MAME side of the env: file-IPC step/reset server with zero idle frames. |
| `tools/board.py` | **Board genotype** — parse/compile/generate/mutate boards as chassis + harvested part templates (`docs/BOARD_GENOTYPE.md`). |
| `tools/evolve.py` | Outer evolution loop — (mu+lambda) over board genomes with cheap or learnability fitness. |
| `tools/learn_es.py` | Learnability estimator — (1+4)-ES over a tiny policy, percentile-of-random metric (`docs/RL_ENV.md`). |
| `tools/evaluate.py` | Orchestrator — inject a `.PB` as `EVOLVED.PB`, run MAME headless with `autoplay.lua`, parse the CSV, return a fitness summary (score, ball lifetime, activity, coverage). Optionally records gameplay video. |

See `docs/PB_FORMAT.md` (board file format), `docs/MEMORY_MAP.md`
(fitness signals), and `docs/AUTOMATION.md` (the working headless drive
pipeline and the traps found along the way).

Evaluate a board (and record video):
```sh
python3 tools/dos33.py extract disks/pcs.dsk DEMO2.PB /tmp/board.pb
python3 tools/evaluate.py /tmp/board.pb --frames 6000 --avi /tmp/run.avi
# -> score 40278, ball_lifetime 6001, activity 22.87, coverage 231
```

Quick look at a board:
```sh
python3 tools/pb.py disks/pcs.dsk DEMO1.PB
```

## Pipeline (working)

1. **Genotype → board file**: boards are lists of filled polygons
   (`tools/pb.py`, `docs/PB_FORMAT.md`) — emit/mutate `.PB` payloads in
   Python.
2. **Inject**: `tools/dos33.py` writes the board onto a copy of the
   game disk as `EVOLVED.PB`.
3. **Evaluate**: `tools/evaluate.py` boots PCS headless, drives the
   real LOAD + PLAY GAME UI (`harness/autoplay.lua`), plays with
   seeded-random flippers, and reads score/ball telemetry from RAM →
   fitness `{score, ball_lifetime, activity, coverage}`.

Still open for the evolution loop proper: a board *generator/mutator*
(the parts records' trailing bytes — behavior/wiring — still need
decoding before adding new parts; vertex/position mutation of existing
records is already safe), and multi-seed evaluation for noise reduction.

## Layout

```
scripts/      setup, run wrapper, smoke test, disk fetch/verify
roms/         MAME ROM sets (generated by setup.sh, not committed)
disks/        disk images (not committed)
third_party/  PCS source code clone (not committed)
work/         emulator output: configs, snapshots, save states
```
