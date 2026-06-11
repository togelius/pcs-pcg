# Pinball board (`.PB`) file format

Derived from Bill Budge's released source (`third_party/PCS_AppleII`),
primarily `source_disc2/DISK.S` (load/save) and `source_disc1/PPAK.S`
(the object-list walker `GETOBJ`/`DRAWDISPLAY`).

## On-disk container

`.PB` files are ordinary DOS 3.3 binary (`B`) files. Their 4-byte BSAVE
header (`load_addr`, `length`, little-endian) is consumed by DOS; the
payload loads at **`$4000` (`PBBASE`)**. `tools/dos33.py` reads and writes
these files directly (`read_binary` / `write_binary`).

The `SAVE` routine BSAVEs the region `$4000 .. MIDBTM`, where `MIDBTM` is
the end of the board data after `COMPRESS` appends the playfield bitmap.

## In-memory layout at `$4000`

| Offset | Addr | Symbol | Meaning |
|---|---|---|---|
| 0 | `$4000` | `LOGIC` | 24 bytes of game logic / scoring settings |
| 24 | `$4018` | `WSET` | 4 bytes (working set / mode) |
| 28 | `$401C` | `PBDATA` | **object count** `N` of the board |
| 29 | `$401D` | — | start of the per-object size table |

### Object list (`PBDATA` onward)

`GETOBJ`/`GETNEXTOBJ` in `PPAK.S` walk the list like this:

1. `PBDATA` (`$401C`) holds the object count `N`.
2. The bytes from `PBDATA` form a **size table**: the address of object
   *k* is `($401C + 1) + sum(PBDATA[0..k])`. Each object's record length is
   how the walker advances, so records are variable-length and packed
   immediately after the table.

### Object records

**Every object on a board is a polygon.** Walls, bumpers, flippers and
targets are all stored as filled polygons; their *class* is the first
byte and their *appearance* is the fill colour. Each record is:

- `+0` object id / class (values **1, 2, 3** seen across the demo
  boards; `DRAWOBJ` in `PPAK.S` branches on `id` vs `LIBOBJ=3`)
- `+1` `FILLCOLOR`
- `+2` `VRTXCOUNT` (vertex count `V`)
- `+3 .. +3+V-1` X coordinates (one byte each, hi-res playfield space)
- `+3+V .. +3+2V-1` Y coordinates

This was confirmed by parsing the real demo boards with `tools/pb.py`:
the size table predicts each record length exactly, and the decoded
vertices are clean playfield coordinates (X ≈ 0–147, Y ≈ 10–190). Fill
values observed: `2`/`4` (walls), `6` (flippers/lanes), `10`, `16`
(bumpers).

> This is the key result for PCG: a board is just a list of colored
> polygons, so evolution can operate directly on vertex coordinates and
> fill colors without an opaque component format in the way.

The trailing bytes of the file are the RLE-compressed hi-res playfield
produced by `COMPRESS` (a simple run-length scheme over `$2000..$3FFF`;
the inverse is `DECOMPRESS`). For evolution we regenerate boards through
the object list and let PCS redraw the bitmap, so the compressed tail can
be left to the game rather than synthesised by hand.

## PCG implications

- **Mutating geometry**: nudging polygon vertex coordinates or component
  X/Y bytes is a safe, low-level genotype operation that keeps the record
  structure intact.
- **Adding/removing parts**: requires updating the object count at
  `$401C` and the size table — straightforward but must stay consistent.
- **Validation**: after editing, the cleanest correctness check is to
  inject the `.PB` onto the work disk (`tools/dos33.py`), boot PCS, and
  confirm it loads and plays (see the harness).

> Status: the container, memory layout, and object-walk are confirmed
> against the source. The exact byte layout of each *component* record
> (which library id maps to which part, and where its X/Y/orientation
> live) is the next thing to pin down empirically by diffing real demo
> boards (`DEMO1.PB` … `DEMO5.PB`, extractable with `tools/dos33.py`).
