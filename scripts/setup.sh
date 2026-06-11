#!/usr/bin/env bash
# Set up the MAME-based Apple II+ emulation environment for PCS research.
#
# Installs MAME, assembles the apple2p ROM set from publicly hosted sources
# (AppleWin and izapple2 repos on GitHub), verifies every file against the
# CRCs MAME expects, and fetches a DOS 3.3 disk used as a boot smoke test.
#
# The Pinball Construction Set disk image itself cannot be downloaded from
# GitHub; see scripts/get_pcs_disk.sh and README.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPLEWIN_RAW="https://raw.githubusercontent.com/AppleWin/AppleWin/master/resource"
IZAPPLE2_RAW="https://raw.githubusercontent.com/ivanizag/izapple2/master/resources"

echo "==> Checking MAME"
if ! command -v mame >/dev/null && [ ! -x /usr/games/mame ]; then
    echo "==> Installing MAME via apt"
    sudo apt-get update -qq || apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y mame mame-tools \
        || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y mame mame-tools
fi
MAME="$(command -v mame || echo /usr/games/mame)"
"$MAME" -version

echo "==> Downloading Apple II+ system ROMs (from AppleWin repo)"
mkdir -p "$ROOT/roms/apple2p" "$ROOT/roms/a2diskiing" "$ROOT/roms/d2fdc" \
         "$ROOT/disks" "$ROOT/work"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -sfL -o "$TMP/Apple2_Plus.rom"  "$APPLEWIN_RAW/Apple2_Plus.rom"
curl -sfL -o "$TMP/Apple2_Video.rom" "$APPLEWIN_RAW/Apple2_Video.rom"
curl -sfL -o "$TMP/DISK2.rom"        "$APPLEWIN_RAW/DISK2.rom"
echo "==> Downloading Disk II P6 sequencer PROM (from izapple2 repo)"
curl -sfL -o "$TMP/p6.bin" \
    "$IZAPPLE2_RAW/Apple%20Disk%20II%2016%20Sector%20Interface%20Card%20ROM%20P6%20-%20341-0028.bin"

echo "==> Slicing and verifying ROMs against MAME's expected CRCs"
python3 - "$TMP" "$ROOT" <<'EOF'
import sys, zlib, shutil, os
tmp, root = sys.argv[1], sys.argv[2]

rom = open(f'{tmp}/Apple2_Plus.rom','rb').read()
assert len(rom) == 12288, f"Apple2_Plus.rom wrong size: {len(rom)}"

# (set dir, filename, expected CRC32, data)
files = []
slices = [('341-0011.d0','6f05f949'),('341-0012.d8','1f08087c'),
          ('341-0013.e0','2b8d9a89'),('341-0014.e8','5719871a'),
          ('341-0015.f0','9a04eecf'),('341-0020-00.f8','079589c4')]
for i,(name,crc) in enumerate(slices):
    files.append(('apple2p', name, crc, rom[i*2048:(i+1)*2048]))
files.append(('apple2p',    '341-0036.chr',   '64f415c6', open(f'{tmp}/Apple2_Video.rom','rb').read()))
files.append(('a2diskiing', '341-0027-a.p5',  'ce7144f6', open(f'{tmp}/DISK2.rom','rb').read()))
files.append(('d2fdc',      '341-0028-a.rom', 'b72a2c70', open(f'{tmp}/p6.bin','rb').read()))

for setname, name, expect, data in files:
    crc = f"{zlib.crc32(data):08x}"
    if crc != expect:
        sys.exit(f"CRC mismatch for {setname}/{name}: got {crc}, want {expect}")
    with open(os.path.join(root, 'roms', setname, name), 'wb') as f:
        f.write(data)
    print(f"  {setname}/{name}  crc32={crc}  OK")
EOF

echo "==> Fetching DOS 3.3 test disk (from apple2tc repo)"
if [ ! -f "$ROOT/disks/dos33_test.dsk" ]; then
    curl -sfL -o "$ROOT/disks/dos33_test.dsk" \
        "https://raw.githubusercontent.com/tmikov/apple2tc/master/dsk/dos33.dsk"
fi

echo "==> Cloning Pinball Construction Set source code (billbudge/PCS_AppleII)"
if [ ! -d "$ROOT/third_party/PCS_AppleII" ]; then
    mkdir -p "$ROOT/third_party"
    git clone --depth 1 https://github.com/billbudge/PCS_AppleII.git \
        "$ROOT/third_party/PCS_AppleII"
fi

if [ -f "$ROOT/disks/pcs.dsk" ]; then
    "$ROOT/scripts/get_pcs_disk.sh" --verify-only
else
    echo
    echo "NOTE: PCS disk image not present yet. See scripts/get_pcs_disk.sh"
fi

echo
echo "==> Setup complete. Run scripts/smoke_test.sh to verify the emulator."
