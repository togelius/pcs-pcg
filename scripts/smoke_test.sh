#!/usr/bin/env bash
# Headless emulator smoke test.
#
# 1. Boot the Apple II+ with no disk controller -> Applesoft BASIC banner.
# 2. Boot the DOS 3.3 test disk through the Disk II -> proves the full
#    ROM + floppy chain works.
# 3. If disks/pcs.dsk exists, boot Pinball Construction Set.
#
# Each stage saves a screenshot under work/smoke/ for visual inspection.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/work/smoke"
rm -rf "$OUT"
COMMON=(-video none -sound none -nothrottle)

run_stage() {
    local name="$1" seconds="$2"; shift 2
    echo "==> Stage: $name"
    "$ROOT/scripts/run_mame.sh" "${COMMON[@]}" -seconds_to_run "$seconds" \
        -snapshot_directory "$OUT/$name" "$@" 2>&1 \
        | grep -E "Average speed|error" || true
    local snap="$OUT/$name/apple2p/0000.png"
    if [ -f "$snap" ]; then
        echo "    screenshot: $snap"
    else
        echo "    FAILED: no screenshot produced" >&2
        exit 1
    fi
}

run_stage basic_prompt 15 -sl6 ""
run_stage dos33_boot   30 -flop1 "$ROOT/disks/dos33_test.dsk"
if [ -f "$ROOT/disks/pcs.dsk" ]; then
    run_stage pcs_boot 60 -flop1 "$ROOT/disks/pcs.dsk"
else
    echo "==> Stage: pcs_boot SKIPPED (disks/pcs.dsk not present)"
fi

echo
echo "Smoke test passed."
