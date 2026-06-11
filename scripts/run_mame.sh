#!/usr/bin/env bash
# Run MAME as a 48K Apple II+ with Disk II, configured for this project.
#
# The default Mockingboard in slot 4 is removed (-sl4 "") so the emulated
# machine matches a stock PCS-era Apple II+ and needs no Votrax speech ROM.
#
# Typical headless use:
#   scripts/run_mame.sh -flop1 disks/pcs.dsk \
#       -video none -sound none -nothrottle -seconds_to_run 30
#
# Interactive use (needs a display): scripts/run_mame.sh -flop1 disks/pcs.dsk
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAME="$(command -v mame || echo /usr/games/mame)"

# SDL complains if XDG_RUNTIME_DIR is unset (as in containers); harmless.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/xdg-runtime}"
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"

exec "$MAME" apple2p \
    -rompath "$ROOT/roms" \
    -sl4 "" \
    -cfg_directory      "$ROOT/work/cfg" \
    -nvram_directory    "$ROOT/work/nvram" \
    -snapshot_directory "$ROOT/work/snap" \
    -state_directory    "$ROOT/work/sta" \
    "$@"
