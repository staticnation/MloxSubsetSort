"""Decode the binary ``LAND`` (landscape) fields tes3conv writes as base64.

A landscape record is five fixed-size binary blobs. In the field-diff window
they arrive base64-encoded (and zstd-compressed underneath), so *any* edit --
one vertex nudged by a hand's width -- looks like a total rewrite of an opaque
string. Decoded into row-per-line grids, the diff says which rows changed.

Format sources, both permissively licensed and already credited in
``CREDITS.md``: the UESP `LAND record page
<https://en.uesp.net/wiki/Morrowind_Mod:Mod_File_Format/LAND>`_ for the
subrecord layout, and **TES3Tool** (MIT), whose
``TES3Lib/Subrecords/LAND/*.cs`` gives the field order and the
height-reconstruction semantics. Where the documentation left a choice open it
was settled by measurement against real plugins rather than assumption -- see
``CODE_REVIEW.md`` §22.

======  ===========  ============================================
Field   Size         Contents
======  ===========  ============================================
VNML    12,675       65x65 vertex normals, ``int8`` x/y/z
VHGT    4,232        ``float32`` offset + 65x65 ``int8`` deltas + 3 unused
WNAM    81           9x9 ``int8`` low-res heightmap for the world map
VCLR    12,675       65x65 vertex colours, ``uint8`` RGB
VTEX    512          16x16 ``uint16`` LTEX indices
======  ===========  ============================================

The detail that is easy to get wrong: **heights are doubly cumulative, not a
running total.** Each row's first delta accumulates onto a carried row height;
the rest of that row accumulates from there. Flat-summing all 4,225 deltas
produces a plausible-looking surface that is completely wrong.
"""

from __future__ import annotations

import struct
from typing import Final

from mlox_subset.mwscript.tes3conv import BytecodeDecodeError, decode_bytecode_field

#: Vertices per side of a landscape cell.
LAND_SIZE: Final = 65

#: Total vertices in a cell (65x65).
LAND_NUM_VERTS: Final = LAND_SIZE * LAND_SIZE

#: Land-texture slots per side.
TEXTURE_SIZE: Final = 16

#: Total texture slots in a cell.
NUM_TEXTURES: Final = TEXTURE_SIZE * TEXTURE_SIZE

#: Side of the low-resolution world-map heightmap.
WNAM_SIZE: Final = 9

#: Stored heights are pre-divided by this factor.
HEIGHT_SCALE: Final = 8


class LandscapeDecodeError(Exception):
    """Raised when a landscape field is not the size its format requires."""


def _payload(value: str | bytes, expected: int, what: str) -> bytes:
    """Decode a field to bytes and check it is long enough to be that field.

    Args:
        value: The field value as tes3conv wrote it.
        expected: The minimum number of bytes the format requires.
        what: Subrecord name, used in the error message.

    Returns:
        The decoded bytes.

    Raises:
        LandscapeDecodeError: If the value cannot be decoded, or is shorter
            than the format requires.
    """
    try:
        raw = decode_bytecode_field(value)
    except BytecodeDecodeError as exc:
        raise LandscapeDecodeError(str(exc)) from exc
    if len(raw) < expected:
        raise LandscapeDecodeError(
            f"{what} is {len(raw)} bytes, but the format requires {expected}. "
            f"Refusing to render a partial grid: it would silently misplace "
            f"every value after the truncation."
        )
    return raw


def decode_vertex_heights(value: str | bytes, offset: float = 0.0) -> list[list[float]]:
    """Reconstruct absolute vertex heights from the stored deltas.

    The encoding is doubly cumulative. A carried row height accumulates the
    first delta of each row; every later column in that row accumulates from
    the row's height. All values are scaled by :data:`HEIGHT_SCALE`.

    Args:
        value: The ``vertex_heights.data`` field.
        offset: The record's ``vertex_heights.offset`` -- the cell's base
            height. Defaults to 0.0 when the caller does not have it, which
            shifts the whole grid but leaves its shape (and the diff) intact.

    Returns:
        65 rows of 65 absolute heights in world units. Row 0 is the south
        edge: the stored data runs bottom-up.

    Raises:
        LandscapeDecodeError: If the field is too short to be a VHGT payload.
    """
    raw = _payload(value, LAND_NUM_VERTS, "VHGT height data")
    deltas = struct.unpack_from(f"<{LAND_NUM_VERTS}b", raw, 0)
    rows: list[list[float]] = []
    row_height = float(offset)
    for y in range(LAND_SIZE):
        base = y * LAND_SIZE
        row_height += deltas[base]
        height = row_height
        row = [height * HEIGHT_SCALE]
        for x in range(1, LAND_SIZE):
            height += deltas[base + x]
            row.append(height * HEIGHT_SCALE)
        rows.append(row)
    return rows


def decode_vertex_normals(value: str | bytes) -> list[list[tuple[int, int, int]]]:
    """Decode the 65x65 grid of ``int8`` vertex normals.

    Args:
        value: The ``vertex_normals.data`` field.

    Returns:
        65 rows of 65 ``(x, y, z)`` triples.

    Raises:
        LandscapeDecodeError: If the field is too short to be a VNML payload.
    """
    raw = _payload(value, 3 * LAND_NUM_VERTS, "VNML normals")
    flat = struct.unpack_from(f"<{3 * LAND_NUM_VERTS}b", raw, 0)
    return [
        [
            (
                flat[(y * LAND_SIZE + x) * 3 + 0],
                flat[(y * LAND_SIZE + x) * 3 + 1],
                flat[(y * LAND_SIZE + x) * 3 + 2],
            )
            for x in range(LAND_SIZE)
        ]
        for y in range(LAND_SIZE)
    ]


def decode_vertex_colours(value: str | bytes) -> list[list[tuple[int, int, int]]]:
    """Decode the 65x65 grid of ``uint8`` RGB vertex colours.

    Args:
        value: The ``vertex_colors.data`` field.

    Returns:
        65 rows of 65 ``(r, g, b)`` triples.

    Raises:
        LandscapeDecodeError: If the field is too short to be a VCLR payload.
    """
    raw = _payload(value, 3 * LAND_NUM_VERTS, "VCLR colours")
    return [
        [
            (
                raw[(y * LAND_SIZE + x) * 3 + 0],
                raw[(y * LAND_SIZE + x) * 3 + 1],
                raw[(y * LAND_SIZE + x) * 3 + 2],
            )
            for x in range(LAND_SIZE)
        ]
        for y in range(LAND_SIZE)
    ]


def decode_world_map(value: str | bytes) -> list[list[int]]:
    """Decode the 9x9 ``int8`` world-map heightmap.

    Args:
        value: The ``world_map_data.data`` field.

    Returns:
        9 rows of 9 signed values.

    Raises:
        LandscapeDecodeError: If the field is too short to be a WNAM payload.
    """
    side = WNAM_SIZE
    raw = _payload(value, side * side, "WNAM world map data")
    flat = struct.unpack_from(f"<{side * side}b", raw, 0)
    return [list(flat[y * side : (y + 1) * side]) for y in range(side)]


def decode_texture_indices(value: str | bytes, *, deswizzle: bool = True) -> list[list[int]]:
    """Decode the 16x16 grid of ``uint16`` land-texture indices.

    VTEX is not stored row-major. The 16x16 grid is written as sixteen 4x4
    blocks, so the stored sequence walks block-row, block-column, then row and
    column *within* the block. Index ``k`` therefore belongs at grid position
    ``(y1 * 4 + y2, x1 * 4 + x2)`` for the four base-4 digits of ``k``.

    That ordering was confirmed empirically rather than taken on trust:
    de-swizzling raises the fraction of equal orthogonally-adjacent cells from
    **0.714 to 0.852** across **2,190 non-uniform landscape cells** from real
    plugins, and wins in **97%** of them individually. Real terrain has large
    contiguous texture regions, so the more spatially coherent reading is the
    correct one by a wide margin.

    One assumption remains, and it is cheap to check: this presumes tes3conv
    writes the subrecord payload through unchanged, as a lossless converter
    would. If a decoded grid ever looks scrambled in a repeating 4x4 pattern,
    tes3conv is already reordering and this should be called with
    ``deswizzle=False``.

    Args:
        value: The ``texture_indices.data`` field.
        deswizzle: Undo the 4x4 block ordering. Leave on unless the grids come
            out scrambled; diffing is unaffected either way, since both sides
            use the same ordering.

    Returns:
        16 rows of 16 LTEX indices, in visual order. ``0`` means no texture.

    Raises:
        LandscapeDecodeError: If the field is too short to be a VTEX payload.
    """
    raw = _payload(value, 2 * NUM_TEXTURES, "VTEX texture indices")
    flat = struct.unpack_from(f"<{NUM_TEXTURES}H", raw, 0)
    if not deswizzle:
        return [list(flat[y * TEXTURE_SIZE : (y + 1) * TEXTURE_SIZE]) for y in range(TEXTURE_SIZE)]
    grid = [[0] * TEXTURE_SIZE for _ in range(TEXTURE_SIZE)]
    for k, index in enumerate(flat):
        x2, y2, x1, y1 = k % 4, (k // 4) % 4, (k // 16) % 4, k // 64
        grid[y1 * 4 + y2][x1 * 4 + x2] = index
    return grid


def _grid_lines(rows: list[list[str]], width: int) -> list[str]:
    """Render rows as ``rNN``-prefixed, column-aligned lines."""
    return [
        f"r{y:02d} " + " ".join(cell.rjust(width) for cell in row) for y, row in enumerate(rows)
    ]


#: Explains the row ordering shared by the 65x65 grids.
_BOTTOM_UP: Final = "; row 0 is the SOUTH edge -- the stored data runs bottom-up"


def render_vertex_heights(value: str | bytes, offset: float = 0.0) -> str:
    """Render VHGT as a grid of absolute heights, one row per line."""
    rows = decode_vertex_heights(value, offset)
    flat = [h for row in rows for h in row]
    header = [
        f"; VHGT -- {LAND_SIZE}x{LAND_SIZE} vertex heights, reconstructed to absolute world units",
        f"; offset={offset:g}  min={min(flat):g}  max={max(flat):g}"
        f"  (deltas scaled x{HEIGHT_SCALE})",
        _BOTTOM_UP,
    ]
    return "\n".join(header + _grid_lines([[f"{h:.0f}" for h in row] for row in rows], 6))


def render_vertex_normals(value: str | bytes) -> str:
    """Render VNML as a grid of ``(x,y,z)`` triples, one row per line."""
    rows = decode_vertex_normals(value)
    header = [f"; VNML -- {LAND_SIZE}x{LAND_SIZE} vertex normals (int8 x,y,z)", _BOTTOM_UP]
    body = _grid_lines([[f"({x},{y},{z})" for x, y, z in row] for row in rows], 14)
    return "\n".join(header + body)


def render_vertex_colours(value: str | bytes) -> str:
    """Render VCLR as a grid of ``#rrggbb`` values, one row per line."""
    rows = decode_vertex_colours(value)
    distinct = {c for row in rows for c in row}
    header = [
        f"; VCLR -- {LAND_SIZE}x{LAND_SIZE} vertex colours, {len(distinct)} distinct",
        _BOTTOM_UP,
    ]
    body = _grid_lines([[f"#{r:02x}{g:02x}{b:02x}" for r, g, b in row] for row in rows], 7)
    return "\n".join(header + body)


def render_world_map(value: str | bytes) -> str:
    """Render WNAM as a 9x9 grid, one row per line."""
    rows = decode_world_map(value)
    header = [
        f"; WNAM -- {WNAM_SIZE}x{WNAM_SIZE} low-resolution heightmap for the world map",
        "; derived from VHGT; the game draws the map from this without loading the cell",
    ]
    return "\n".join(header + _grid_lines([[str(v) for v in row] for row in rows], 4))


def render_texture_indices(value: str | bytes) -> str:
    """Render VTEX as a 16x16 grid of LTEX indices, one row per line."""
    rows = decode_texture_indices(value)
    used = sorted({v for row in rows for v in row})
    shown = ", ".join(str(v) for v in used[:16])
    header = [
        f"; VTEX -- {TEXTURE_SIZE}x{TEXTURE_SIZE} land texture indices, in visual order",
        "; (stored as sixteen 4x4 blocks; un-swizzled here so the grid maps to the cell)",
        f"; each value is an LTEX index; 0 = none. {len(used)} distinct: "
        f"{shown}{' ...' if len(used) > 16 else ''}",
    ]
    return "\n".join(header + _grid_lines([[str(v) for v in row] for row in rows], 5))
