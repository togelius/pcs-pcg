"""Parse Pinball Construction Set .PB board files.

A .PB payload loads at $4000 (PBBASE). See docs/PB_FORMAT.md.

This parses the object list (count + size table + records) and decodes
POLYGON / BPOLYGON records into vertices. Component records are reported
with their raw bytes for now (their internal layout is still being
pinned down empirically).
"""

from __future__ import annotations

from dataclasses import dataclass, field

PBBASE = 0x4000
PBDATA_OFF = 0x1C          # offset of the object-count byte within the payload

# Object IDs (from PPAK.S: LIBOBJ=3, POLYGON/BPOLYGON are the high ids).
# POLYGON/BPOLYGON ids are compared as bytes in source; we detect polygons
# structurally and treat low ids as library/component placements.
LIBOBJ = 3


@dataclass
class Polygon:
    obj_id: int               # object class (1..3 seen in demo boards)
    fill: int
    vertices: list[tuple[int, int]]


@dataclass
class Component:
    obj_id: int
    raw: bytes = field(repr=False)


@dataclass
class Board:
    logic: bytes              # 24 bytes at $4000
    wset: bytes               # 4 bytes at $4018
    count: int                # object count at $401C
    sizes: list[int]          # per-object size table
    objects: list             # Polygon | Component
    tail: bytes = field(repr=False)  # compressed bitmap remainder


def _looks_like_polygon(rec: bytes) -> bool:
    """A polygon record is id, fill, vcount, then 2*vcount coords."""
    if len(rec) < 3:
        return False
    vcount = rec[2]
    return vcount > 0 and len(rec) >= 3 + 2 * vcount


def parse(payload: bytes) -> Board:
    """payload is the bytes that load at $4000 (no DOS BSAVE header)."""
    logic = payload[0:24]
    wset = payload[24:28]
    count = payload[PBDATA_OFF]

    # Size table: count+1 bytes starting at PBDATA (the count byte is the
    # first addend in GETNEXTOBJ). Object k starts at
    #   (PBDATA+1) + sum(sizes[0..k]).
    table_base = PBDATA_OFF
    sizes = list(payload[table_base:table_base + count + 1])

    objects: list = []
    obj_off = PBDATA_OFF + 1
    acc = sizes[0] if sizes else 0
    # First object sits at (PBDATA+1)+sizes[0]; thereafter add each size.
    for k in range(count):
        start = obj_off + acc
        end = obj_off + acc + (sizes[k + 1] if k + 1 < len(sizes) else 0)
        rec = payload[start:end] if end > start else payload[start:start + 1]
        obj_id = rec[0] if rec else 0
        if _looks_like_polygon(rec):
            v = rec[2]
            xs = rec[3:3 + v]
            ys = rec[3 + v:3 + 2 * v]
            objects.append(Polygon(obj_id=obj_id, fill=rec[1],
                                   vertices=list(zip(xs, ys))))
        else:
            objects.append(Component(obj_id=obj_id, raw=bytes(rec)))
        if k + 1 < len(sizes):
            acc += sizes[k + 1]

    last_end = obj_off + acc
    tail = payload[last_end:]
    return Board(logic=logic, wset=wset, count=count, sizes=sizes,
                 objects=objects, tail=tail)


def main() -> None:
    import sys
    from dos33 import Dos33Disk
    if len(sys.argv) != 3:
        print("usage: pb.py DISK.dsk BOARD.PB", file=sys.stderr)
        sys.exit(2)
    _, load = Dos33Disk.open(sys.argv[1]).read_binary(sys.argv[2])
    b = parse(load)
    print(f"{sys.argv[2]}: {len(load)} bytes, object count = {b.count}")
    print(f"  size table: {b.sizes}")
    polys = [o for o in b.objects if isinstance(o, Polygon)]
    comps = [o for o in b.objects if isinstance(o, Component)]
    print(f"  {len(polys)} polygon(s), {len(comps)} component(s), "
          f"{len(b.tail)} tail bytes")
    for i, o in enumerate(b.objects):
        if isinstance(o, Polygon):
            print(f"  [{i}] polygon id={o.obj_id} fill={o.fill} "
                  f"verts={len(o.vertices)} "
                  f"{o.vertices[:4]}{'...' if len(o.vertices) > 4 else ''}")
        else:
            print(f"  [{i}] component id={o.obj_id} raw={o.raw[:8].hex()}")


if __name__ == '__main__':
    main()
