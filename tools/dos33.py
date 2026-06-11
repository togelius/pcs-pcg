"""Minimal DOS 3.3 filesystem access for .dsk images (DOS sector order).

Enough to catalog a disk, extract files, and add/replace binary files --
which is all the PCS evolution pipeline needs to inject .PB board files
onto the work disk.

Layout reference: Beneath Apple DOS. 35 tracks x 16 sectors x 256 bytes.
VTOC at track 17 sector 0; catalog sectors chained from it; files are
described by a chain of track/sector list sectors pointing at data sectors.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

TRACKS = 35
SECTORS = 16
SECTOR_SIZE = 256
DISK_SIZE = TRACKS * SECTORS * SECTOR_SIZE  # 143360

FILE_TYPES = {0x00: 'T', 0x01: 'I', 0x02: 'A', 0x04: 'B',
              0x08: 'S', 0x10: 'R', 0x20: 'a', 0x40: 'b'}


@dataclass
class CatalogEntry:
    name: str           # 30-char name, trailing spaces stripped
    file_type: str      # 'B' for binary, etc.
    locked: bool
    sectors: int        # sector count from the catalog
    ts_track: int       # first track/sector list location
    ts_sector: int
    cat_track: int      # where this entry lives (for rewrites)
    cat_sector: int
    cat_offset: int


class Dos33Disk:
    def __init__(self, data: bytearray):
        if len(data) != DISK_SIZE:
            raise ValueError(f"expected {DISK_SIZE} byte .dsk, got {len(data)}")
        self.data = data

    @classmethod
    def open(cls, path: str) -> 'Dos33Disk':
        with open(path, 'rb') as f:
            return cls(bytearray(f.read()))

    def save(self, path: str) -> None:
        with open(path, 'wb') as f:
            f.write(self.data)

    # --- sector access -------------------------------------------------

    def _off(self, track: int, sector: int) -> int:
        if not (0 <= track < TRACKS and 0 <= sector < SECTORS):
            raise ValueError(f"bad track/sector {track}/{sector}")
        return (track * SECTORS + sector) * SECTOR_SIZE

    def read_sector(self, track: int, sector: int) -> bytes:
        o = self._off(track, sector)
        return bytes(self.data[o:o + SECTOR_SIZE])

    def write_sector(self, track: int, sector: int, payload: bytes) -> None:
        if len(payload) != SECTOR_SIZE:
            raise ValueError("sector payload must be 256 bytes")
        o = self._off(track, sector)
        self.data[o:o + SECTOR_SIZE] = payload

    # --- VTOC / free map ------------------------------------------------

    def vtoc(self) -> bytes:
        return self.read_sector(17, 0)

    def _free_map(self) -> list[list[bool]]:
        """free[track][sector] -> True if free.

        VTOC bitmap per track: byte 0 bits 7..0 = sectors F..8,
        byte 1 bits 7..0 = sectors 7..0 (Beneath Apple DOS, p. 4-3).
        """
        v = self.vtoc()
        free = []
        for t in range(TRACKS):
            b0, b1 = v[0x38 + 4 * t], v[0x39 + 4 * t]
            row = [False] * 16
            for i in range(8):
                row[15 - i] = bool(b0 & (0x80 >> i))
                row[7 - i] = bool(b1 & (0x80 >> i))
            free.append(row)
        return free

    def _set_free(self, track: int, sector: int, is_free: bool) -> None:
        o = self._off(17, 0) + 0x38 + 4 * track
        idx, bit = (o, sector - 8) if sector >= 8 else (o + 1, sector)
        mask = 1 << bit
        if is_free:
            self.data[idx] |= mask
        else:
            self.data[idx] &= ~mask & 0xFF

    def _alloc_sector(self) -> tuple[int, int]:
        free = self._free_map()
        # Prefer tracks just outside the catalog, like DOS does.
        order = list(range(18, TRACKS)) + list(range(16, 0, -1))
        for t in order:
            for s in range(SECTORS - 1, -1, -1):
                if free[t][s]:
                    self._set_free(t, s, False)
                    return t, s
        raise IOError("disk full")

    # --- catalog ---------------------------------------------------------

    def catalog(self) -> list[CatalogEntry]:
        entries = []
        v = self.vtoc()
        t, s = v[1], v[2]
        seen = set()
        while t != 0 and (t, s) not in seen:
            seen.add((t, s))
            sec = self.read_sector(t, s)
            for i in range(7):
                off = 0x0B + 35 * i
                e = sec[off:off + 35]
                if e[0] in (0x00,):       # never used
                    continue
                if e[0] == 0xFF:          # deleted
                    continue
                ftype = e[2]
                name = bytes(b & 0x7F for b in e[3:33]).decode('ascii').rstrip()
                entries.append(CatalogEntry(
                    name=name,
                    file_type=FILE_TYPES.get(ftype & 0x7F, '?'),
                    locked=bool(ftype & 0x80),
                    sectors=e[33] | (e[34] << 8),
                    ts_track=e[0], ts_sector=e[1],
                    cat_track=t, cat_sector=s, cat_offset=off))
            t, s = sec[1], sec[2]
        return entries

    def _ts_chain(self, track: int, sector: int):
        """Yield (track, sector) of each data sector of a file, in order."""
        seen = set()
        while track != 0 and (track, sector) not in seen:
            seen.add((track, sector))
            ts = self.read_sector(track, sector)
            for i in range(122):
                dt, ds = ts[0x0C + 2 * i], ts[0x0D + 2 * i]
                if dt == 0 and ds == 0:
                    continue
                yield dt, ds
            track, sector = ts[1], ts[2]

    def read_file(self, name: str) -> bytes:
        """Raw file content (for B files: 4-byte addr/len header + data)."""
        for e in self.catalog():
            if e.name == name:
                out = b''.join(self.read_sector(t, s)
                               for t, s in self._ts_chain(e.ts_track, e.ts_sector))
                return out
        raise FileNotFoundError(name)

    def read_binary(self, name: str) -> tuple[int, bytes]:
        """(load_address, data) of a DOS 3.3 B file."""
        raw = self.read_file(name)
        addr, length = struct.unpack('<HH', raw[:4])
        return addr, raw[4:4 + length]

    # --- writing ----------------------------------------------------------

    def delete_file(self, name: str) -> None:
        for e in self.catalog():
            if e.name != name:
                continue
            # free all data sectors and ts-list sectors
            t, s = e.ts_track, e.ts_sector
            seen = set()
            while t != 0 and (t, s) not in seen:
                seen.add((t, s))
                ts = self.read_sector(t, s)
                for i in range(122):
                    dt, ds = ts[0x0C + 2 * i], ts[0x0D + 2 * i]
                    if dt or ds:
                        self._set_free(dt, ds, True)
                self._set_free(t, s, True)
                t, s = ts[1], ts[2]
            # mark catalog entry deleted
            o = self._off(e.cat_track, e.cat_sector) + e.cat_offset
            self.data[o] = 0xFF
            return
        raise FileNotFoundError(name)

    def write_binary(self, name: str, load_addr: int, payload: bytes) -> None:
        """Add (or replace) a DOS 3.3 B file."""
        try:
            self.delete_file(name)
        except FileNotFoundError:
            pass

        raw = struct.pack('<HH', load_addr, len(payload)) + payload
        sectors = [raw[i:i + SECTOR_SIZE] for i in range(0, len(raw), SECTOR_SIZE)]
        if sectors and len(sectors[-1]) < SECTOR_SIZE:
            sectors[-1] = sectors[-1] + b'\0' * (SECTOR_SIZE - len(sectors[-1]))

        if len(sectors) > 122:
            raise IOError("file too large for a single t/s list (not needed for .PB)")

        data_locs = []
        for payload_sec in sectors:
            t, s = self._alloc_sector()
            self.write_sector(t, s, payload_sec)
            data_locs.append((t, s))
        ts_t, ts_s = self._alloc_sector()
        ts = bytearray(SECTOR_SIZE)
        for i, (t, s) in enumerate(data_locs):
            ts[0x0C + 2 * i] = t
            ts[0x0D + 2 * i] = s
        self.write_sector(ts_t, ts_s, bytes(ts))

        # find a free catalog slot
        v = self.vtoc()
        t, s = v[1], v[2]
        while t != 0:
            sec = bytearray(self.read_sector(t, s))
            for i in range(7):
                off = 0x0B + 35 * i
                if sec[off] in (0x00, 0xFF):
                    sec[off] = ts_t
                    sec[off + 1] = ts_s
                    sec[off + 2] = 0x04  # B file, unlocked
                    padded = name.ljust(30)[:30]
                    sec[off + 3:off + 33] = bytes((ord(c) | 0x80) for c in padded)
                    count = len(sectors) + 1
                    sec[off + 33] = count & 0xFF
                    sec[off + 34] = count >> 8
                    self.write_sector(t, s, bytes(sec))
                    return
            t, s = sec[1], sec[2]
        raise IOError("catalog full")


def main() -> None:
    import sys
    if len(sys.argv) < 3 or sys.argv[1] not in ('catalog', 'extract'):
        print("usage: dos33.py catalog DISK.dsk\n"
              "       dos33.py extract DISK.dsk FILENAME OUT", file=sys.stderr)
        sys.exit(2)
    disk = Dos33Disk.open(sys.argv[2])
    if sys.argv[1] == 'catalog':
        for e in disk.catalog():
            lock = '*' if e.locked else ' '
            print(f"{lock}{e.file_type} {e.sectors:03d} {e.name}")
    else:
        addr, data = disk.read_binary(sys.argv[3])
        with open(sys.argv[4], 'wb') as f:
            f.write(data)
        print(f"extracted {sys.argv[3]}: load=${addr:04X} len=${len(data):04X} "
              f"({len(data)} bytes) -> {sys.argv[4]}")


if __name__ == '__main__':
    main()
