"""Board genotype: parse, validate, compile, generate, and mutate PCS
boards as lists of raw object records.

Representation (see docs/PB_FORMAT.md): a .PB payload loads at $4000 as

    LOGIC (24 bytes)  wiring/logic table  -- copied verbatim from chassis
    WSET  (4 bytes)                       -- copied verbatim
    count (1 byte)    number of objects
    sizes (count bytes) record lengths
    records...        id, fill, vcount, xs[v], ys[v] [, extra bytes]
    RLE tail          compressed hi-res screen

Records with extra bytes after the vertex lists are *parts* (bumpers,
flippers, kickers, ...): the extra bytes carry the library/behavior
reference, which we treat as an opaque template -- relocating a part
means shifting its vertex bytes only.

The compiled tail is an RLE-encoded all-zero screen: the real LOAD path
runs DECOMPRESS (tail) and then DRAWDISPLAY, which draws every object
from the record list, so a blank background yields a correct, fully
rendered board (verified against a recompiled DEMO2).

Mutation invariants:
  * structural records (outline, drain, launcher-lane furniture, ball,
    flippers) are protected -- never moved or removed;
  * new parts are APPENDED, never inserted, so existing object indices
    (which the LOGIC wiring table may reference) stay stable;
  * deletion is limited to parts that were themselves added by mutation
    (again protecting LOGIC references into the chassis);
  * geometry stays inside the playfield with a clearance margin and no
    bounding-box overlap with other records.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from dos33 import Dos33Disk  # noqa: E402

LOGIC_LEN, WSET_LEN = 24, 4
PBDATA_OFF = 0x1C
# playfield interior where free parts may live (left of the launcher
# lane, below the top arch, above the flipper zone)
FIELD = dict(x0=6, y0=14, x1=126, y1=148)
CLEARANCE = 3
MAX_PAYLOAD = 0x2F40 - 64        # keep well under PBDX ($6F40-$4000)
ZERO_TAIL = b"\x01\x00" * 32 + b"\x01\x01"   # RLE of an all-zero screen


@dataclass
class Record:
    raw: bytearray
    protected: bool = False
    added: bool = False           # introduced by mutation (deletable)

    @property
    def oid(self) -> int: return self.raw[0]
    @property
    def fill(self) -> int: return self.raw[1]
    @fill.setter
    def fill(self, v: int) -> None: self.raw[1] = v & 0xFF
    @property
    def vcount(self) -> int: return self.raw[2]
    @property
    def xs(self) -> bytearray: return self.raw[3:3 + self.vcount]
    @property
    def ys(self) -> bytearray: return self.raw[3 + self.vcount:3 + 2 * self.vcount]
    @property
    def extra(self) -> bytes: return bytes(self.raw[3 + 2 * self.vcount:])
    @property
    def is_part(self) -> bool: return len(self.extra) > 0

    def bbox(self) -> tuple[int, int, int, int]:
        return (min(self.xs), min(self.ys), max(self.xs), max(self.ys))

    def translate(self, dx: int, dy: int) -> None:
        v = self.vcount
        for i in range(v):
            self.raw[3 + i] = (self.raw[3 + i] + dx) & 0xFF
            self.raw[3 + v + i] = (self.raw[3 + v + i] + dy) & 0xFF

    def copy(self) -> "Record":
        return Record(bytearray(self.raw), protected=False, added=True)


@dataclass
class Board:
    logic: bytes
    wset: bytes
    records: list[Record] = field(default_factory=list)

    # ---- parse / compile -------------------------------------------------

    @classmethod
    def parse(cls, payload: bytes) -> "Board":
        count = payload[PBDATA_OFF]
        sizes = list(payload[PBDATA_OFF:PBDATA_OFF + count + 1])
        records, off, acc = [], PBDATA_OFF + 1, sizes[0]
        for k in range(count):
            start = off + acc
            ln = sizes[k + 1]
            records.append(Record(bytearray(payload[start:start + ln])))
            acc += ln
        return cls(logic=bytes(payload[:LOGIC_LEN]),
                   wset=bytes(payload[LOGIC_LEN:LOGIC_LEN + WSET_LEN]),
                   records=records)

    @classmethod
    def from_disk(cls, disk_path: str, name: str) -> "Board":
        _, payload = Dos33Disk.open(disk_path).read_binary(name)
        return cls.parse(payload)

    def compile(self) -> bytes:
        count = len(self.records)
        if count > 254:
            raise ValueError("too many records")
        sizes = bytes([count] + [len(r.raw) for r in self.records])
        body = b"".join(bytes(r.raw) for r in self.records)
        payload = self.logic + self.wset + sizes + body + ZERO_TAIL
        if len(payload) > MAX_PAYLOAD:
            raise ValueError(f"board too large: {len(payload)}")
        return payload

    # ---- classification ----------------------------------------------------

    def classify(self) -> None:
        """Mark structural records as protected (chassis heuristics):
        the outline (many vertices), anything touching the launcher lane
        (x >= 130), anything in the flipper/drain band (y >= 150), and
        the ball-start marker (small square part)."""
        for r in self.records:
            x0, y0, x1, y1 = r.bbox()
            r.protected = (
                r.vcount >= 10                # outline / big walls
                or x1 >= 130                  # launcher lane furniture
                or y1 >= 150                  # flippers, slings, drain
                or (x1 - x0 <= 5 and y1 - y0 <= 5 and not r.is_part
                    and x0 >= 130)            # ball start marker (in lane)
            )

    def movable(self) -> list[int]:
        return [i for i, r in enumerate(self.records) if not r.protected]

    # ---- geometry helpers ---------------------------------------------------

    def _fits(self, rec: Record, skip: int | None = None) -> bool:
        x0, y0, x1, y1 = rec.bbox()
        if not (FIELD["x0"] <= x0 and x1 <= FIELD["x1"]
                and FIELD["y0"] <= y0 and y1 <= FIELD["y1"]):
            return False
        for j, other in enumerate(self.records):
            if j == skip:
                continue
            a0, b0, a1, b1 = other.bbox()
            if not (x1 + CLEARANCE < a0 or a1 + CLEARANCE < x0
                    or y1 + CLEARANCE < b0 or b1 + CLEARANCE < y0):
                # overlap is fine with the outline record (it surrounds
                # the field); reject contact with everything else
                if other.vcount < 10:
                    return False
        return True

    def place_randomly(self, rec: Record, rng: np.random.Generator,
                       tries: int = 60) -> bool:
        w = max(r for r in rec.xs) - min(rec.xs)
        h = max(rec.ys) - min(rec.ys)
        for _ in range(tries):
            tx = int(rng.integers(FIELD["x0"], FIELD["x1"] - w + 1))
            ty = int(rng.integers(FIELD["y0"], FIELD["y1"] - h + 1))
            rec.translate(tx - min(rec.xs), ty - min(rec.ys))
            if self._fits(rec):
                return True
        return False


# ---- template library -------------------------------------------------------


def load_library(disk_path: str | None = None,
                 boards: tuple[str, ...] = ("DEMO1.PB", "DEMO2.PB", "DEMO3.PB",
                                            "DEMO4.PB", "DEMO5.PB")) -> list[Record]:
    """Harvest movable part/obstacle templates from the demo boards.

    Returns deduplicated records (by id+extra signature+shape) that are
    not structural on their source board.
    """
    disk_path = disk_path or os.path.join(ROOT, "disks", "pcs.dsk")
    seen, lib = set(), []
    for name in boards:
        try:
            b = Board.from_disk(disk_path, name)
        except FileNotFoundError:
            continue
        b.classify()
        for i in b.movable():
            r = b.records[i]
            x0, y0, _, _ = r.bbox()
            shape = bytes((x - x0 for x in r.xs)) + bytes((y - y0 for y in r.ys))
            key = (r.oid, r.vcount, bytes(r.extra), shape)
            if key in seen:
                continue
            seen.add(key)
            lib.append(r.copy())
    return lib


# ---- generator / mutator ------------------------------------------------------


def make_chassis(disk_path: str | None = None, base: str = "DEMO2.PB") -> Board:
    """A playable skeleton: the protected records of a demo board."""
    disk_path = disk_path or os.path.join(ROOT, "disks", "pcs.dsk")
    b = Board.from_disk(disk_path, base)
    b.classify()
    b.records = [r for r in b.records if r.protected]
    return b


def generate(rng: np.random.Generator, *, n_parts: tuple[int, int] = (4, 12),
             library: list[Record] | None = None,
             chassis: Board | None = None) -> Board:
    """Random board: chassis + N random library parts at random positions."""
    lib = library if library is not None else load_library()
    board = chassis if chassis is not None else make_chassis()
    import copy as _copy
    board = Board(board.logic, board.wset,
                  [Record(bytearray(r.raw), protected=True) for r in board.records])
    n = int(rng.integers(n_parts[0], n_parts[1] + 1))
    for _ in range(n):
        rec = lib[int(rng.integers(len(lib)))].copy()
        if board.place_randomly(rec, rng):
            board.records.append(rec)
    return board


def mutate(board: Board, rng: np.random.Generator,
           library: list[Record] | None = None,
           n_ops: int | None = None) -> Board:
    """Return a mutated copy. Operators: move, jitter, duplicate,
    add-from-library, delete (added parts only), recolor."""
    lib = library if library is not None else load_library()
    b = Board(board.logic, board.wset,
              [Record(bytearray(r.raw), r.protected, r.added) for r in board.records])
    ops = n_ops if n_ops is not None else 1 + int(rng.geometric(0.5))
    for _ in range(ops):
        movable = b.movable()
        choices = ["add"]
        if movable:
            choices += ["move", "jitter", "dup", "recolor"]
        if any(b.records[i].added for i in movable):
            choices.append("del")
        op = choices[int(rng.integers(len(choices)))]

        if op == "add":
            rec = lib[int(rng.integers(len(lib)))].copy()
            if b.place_randomly(rec, rng):
                b.records.append(rec)
        elif op == "move":
            i = movable[int(rng.integers(len(movable)))]
            r = b.records[i]
            old = bytearray(r.raw)
            dx = int(rng.integers(-20, 21))
            dy = int(rng.integers(-20, 21))
            r.translate(dx, dy)
            if not b._fits(r, skip=i):
                r.raw[:] = old
        elif op == "jitter":
            i = movable[int(rng.integers(len(movable)))]
            r = b.records[i]
            if r.is_part:
                continue              # parts move rigidly only
            old = bytearray(r.raw)
            vi = int(rng.integers(r.vcount))
            r.raw[3 + vi] = (r.raw[3 + vi] + int(rng.integers(-3, 4))) & 0xFF
            r.raw[3 + r.vcount + vi] = (r.raw[3 + r.vcount + vi]
                                        + int(rng.integers(-3, 4))) & 0xFF
            if not b._fits(r, skip=i):
                r.raw[:] = old
        elif op == "dup":
            i = movable[int(rng.integers(len(movable)))]
            rec = b.records[i].copy()
            if b.place_randomly(rec, rng):
                b.records.append(rec)
        elif op == "del":
            added = [i for i in movable if b.records[i].added]
            del b.records[added[int(rng.integers(len(added)))]]
        elif op == "recolor":
            i = movable[int(rng.integers(len(movable)))]
            r = b.records[i]
            if not r.is_part:         # part fills may encode behavior
                r.fill = int(rng.choice([2, 4, 6, 10, 16]))
    return b


# ---- CLI -----------------------------------------------------------------------


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("inspect", help="classify a board's records")
    p.add_argument("board", help=".PB payload path or DISKNAME on pcs.dsk")

    p = sub.add_parser("roundtrip", help="parse+recompile a demo board")
    p.add_argument("name", help="catalog name, e.g. DEMO2.PB")
    p.add_argument("out", help="output payload path")

    p = sub.add_parser("generate", help="random board")
    p.add_argument("out")
    p.add_argument("--seed", type=int, default=0)

    p = sub.add_parser("mutate", help="mutate an existing payload")
    p.add_argument("board")
    p.add_argument("out")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--ops", type=int, default=None)

    args = ap.parse_args()
    if args.cmd == "inspect":
        with open(args.board, "rb") as f:
            b = Board.parse(f.read())
        b.classify()
        for i, r in enumerate(b.records):
            flag = "P" if r.protected else " "
            print(f"[{i:2d}]{flag} id={r.oid} fill={r.fill:2d} v={r.vcount:2d} "
                  f"extra={len(r.extra):2d} bbox={r.bbox()}")
        print(f"{len(b.movable())} movable of {len(b.records)}")
    elif args.cmd == "roundtrip":
        b = Board.from_disk(os.path.join(ROOT, "disks", "pcs.dsk"), args.name)
        with open(args.out, "wb") as f:
            f.write(b.compile())
        print(f"recompiled {args.name} -> {args.out} ({len(b.records)} records)")
    elif args.cmd == "generate":
        b = generate(np.random.default_rng(args.seed))
        with open(args.out, "wb") as f:
            f.write(b.compile())
        print(f"generated {args.out}: {len(b.records)} records")
    elif args.cmd == "mutate":
        with open(args.board, "rb") as f:
            b = Board.parse(f.read())
        b.classify()
        m = mutate(b, np.random.default_rng(args.seed), n_ops=args.ops)
        with open(args.out, "wb") as f:
            f.write(m.compile())
        print(f"mutated {args.board} -> {args.out}: "
              f"{len(b.records)} -> {len(m.records)} records")


if __name__ == "__main__":
    main()
