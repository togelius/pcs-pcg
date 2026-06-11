#!/usr/bin/env bash
# Fetch (or verify) the Pinball Construction Set disk image.
#
# Target: the 4am & san inc crack, which is the clean preservation copy and
# the exact disk catalogued in MAME's apple2_flop_clcracked software list:
#     size 143360 bytes
#     sha1 9a809dd030da376f17857b4cd74fba4cad68f01c
#
# archive.org is not reachable from the default Claude Code cloud network
# policy, so this script will only succeed locally or after adding
# archive.org to the environment's allowed domains. Alternatively, download
# the file yourself and place it at disks/pcs.dsk:
#     https://archive.org/details/PinballConstructionSet4amCrack
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/disks/pcs.dsk"
URL="https://archive.org/download/PinballConstructionSet4amCrack/Pinball%20Construction%20Set%20%284am%20and%20san%20inc%20crack%29.dsk"
SHA1="9a809dd030da376f17857b4cd74fba4cad68f01c"

if [ "${1:-}" != "--verify-only" ] && [ ! -f "$DEST" ]; then
    echo "==> Downloading PCS disk image from archive.org"
    curl -fL -o "$DEST" "$URL"
fi

if [ ! -f "$DEST" ]; then
    echo "error: $DEST not found" >&2
    exit 1
fi

echo "==> Verifying $DEST"
actual="$(sha1sum "$DEST" | cut -d' ' -f1)"
size="$(stat -c%s "$DEST")"
if [ "$actual" = "$SHA1" ] && [ "$size" = "143360" ]; then
    echo "    sha1 $actual  size $size  OK (matches MAME softlist 'pinbcset')"
else
    echo "    WARNING: file does not match the expected 4am crack:"
    echo "      got  sha1=$actual size=$size"
    echo "      want sha1=$SHA1 size=143360"
    echo "    A different dump may still work, but results won't be canonical."
fi
