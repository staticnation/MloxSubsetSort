"""Convert between absolute vertex heights and the stored ``VHGT`` encoding.

:mod:`wraithguard.tes3fields.landscape` already decodes ``VHGT`` into absolute
world-unit heights for the diff window. Merging needs the *inverse*: having
combined two mods' terrain, the result has to be written back as an offset plus
a grid of ``int8`` deltas. This module is that direction, plus the vertex
normals that must be recomputed to match any height that moved.

**The encoding, and the trap in it.** ``VHGT`` stores a ``float32`` base height
followed by 65x65 signed byte deltas, all pre-divided by
:data:`~wraithguard.tes3fields.landscape.HEIGHT_SCALE` (8). The deltas are
*doubly* cumulative: each row's first delta accumulates onto a carried row
height, and every later column accumulates along that row. Reversing it means
differencing along both axes in the same order.

**Not every landscape is representable, and that matters.** A delta is one
signed byte, so two adjacent vertices cannot differ by more than 127 stored
units -- 1,016 world units, about eleven feet per vertex step. Real terrain
almost never approaches that, but a *merged* landscape can: splice the bottom
of one mod's canyon against the top of another's plateau and the seam between
them may be steeper than the format can express.

Merged Lands clamps the gradient and then asserts that decoding reproduces the
input, which aborts the whole run on the one cell that cannot round-trip. That
is the right instinct -- silently writing a cliff the user did not ask for is
worse -- but the wrong remedy for a tool someone runs on two hundred plugins.
Here the clamp is reported instead: :func:`encode_vertex_heights` returns the
vertices it could not represent, so the caller can name the cell, skip it, or
fall back to one mod's version, and the other two hundred plugins still merge.
"""

from __future__ import annotations

import math
import struct
from typing import Final

from wraithguard.tes3fields.landscape import (
    HEIGHT_SCALE,
    LAND_NUM_VERTS,
    LAND_SIZE,
)

#: The widest gradient one signed byte can hold, in stored (pre-scale) units.
MAX_GRADIENT: Final = 127

#: The narrowest. Not simply ``-MAX_GRADIENT``: two's complement reaches one
#: further down than up, and giving away that step would be a bug of the kind
#: that only shows on the steepest terrain in a load order.
MIN_GRADIENT: Final = -128

#: Trailing bytes after the delta grid. The subrecord is 4,232 bytes: a f32
#: offset, 4,225 deltas, then three unused. They are written as zero.
_VHGT_PADDING: Final = 3

#: Length of a well-formed ``VHGT`` payload.
VHGT_SIZE: Final = 4 + LAND_NUM_VERTS + _VHGT_PADDING


class HeightEncodeError(Exception):
    """Raised when a height grid is not the shape ``VHGT`` requires."""


def _to_signed_byte(value: float) -> int:
    """Convert a float to a signed byte, saturating, with NaN mapped to zero.

    Ports Merged Lands' ``f32_to_i8_saturating``. An ``int8`` field cannot hold
    every value the geometry might produce, and ``struct.pack('b', ...)`` raises
    on anything outside ``[-128, 127]`` -- so a single out-of-range or
    non-finite component (a normal computed from a merged or corrupt vertex)
    would abort encoding the whole cell rather than round one number. Clamping
    keeps the cell. ``int()`` truncates toward zero, matching the rest of this
    module and Rust's ``as i8``.

    Args:
        value: The component to store.

    Returns:
        ``int(value)`` clamped to ``[MIN_GRADIENT, MAX_GRADIENT]``; ``0`` when
        ``value`` is NaN, ``MAX_GRADIENT``/``MIN_GRADIENT`` when it is
        infinite.
    """
    if math.isnan(value):
        return 0
    if value >= MAX_GRADIENT:
        return MAX_GRADIENT
    if value <= MIN_GRADIENT:
        return MIN_GRADIENT
    return int(value)


def _finite_height(value: float) -> float:
    """Return ``value`` if finite, else zero, so encoding never raises.

    ``int(nan)`` and ``int(inf)`` raise, and a merged vertex can arrive
    non-finite -- a division in slope or curvature weighting, or garbage in a
    source ``VHGT``. That must not lose the whole cell, so a non-finite height
    is treated as flat ground rather than crashing the pack. Extreme *finite*
    heights need no guard: their gradient is clamped by :func:`encode_vertex_heights`.

    Args:
        value: A world-unit height.

    Returns:
        ``value`` when finite, otherwise ``0.0``.
    """
    return value if math.isfinite(value) else 0.0


def _check_grid(rows: list[list[float]]) -> None:
    """Verify a grid is 65x65 before anything indexes into it.

    Args:
        rows: The candidate height grid.

    Raises:
        HeightEncodeError: If the grid is not 65 rows of 65 values.
    """
    if len(rows) != LAND_SIZE:
        raise HeightEncodeError(f"expected {LAND_SIZE} rows, got {len(rows)}")
    for y, row in enumerate(rows):
        if len(row) != LAND_SIZE:
            raise HeightEncodeError(f"row {y} has {len(row)} values, expected {LAND_SIZE}")


def encode_vertex_heights(
    rows: list[list[float]],
) -> tuple[float, bytes, list[tuple[int, int]]]:
    """Encode absolute world-unit heights as ``VHGT`` offset and deltas.

    The exact inverse of
    :func:`~wraithguard.tes3fields.landscape.decode_vertex_heights`, with one
    documented exception: gradients too steep for a signed byte are clamped,
    and every clamped vertex is reported rather than hidden.

    Args:
        rows: 65 rows of 65 absolute heights in world units. Row 0 is the
            south edge, matching the decoder.

    Returns:
        A triple of the ``float32`` offset, the 4,232-byte payload, and the
        ``(x, y)`` coordinates of every vertex whose gradient had to be
        clamped. An empty list means the landscape round-trips exactly.

    Raises:
        HeightEncodeError: If the grid is not 65x65.
    """
    _check_grid(rows)

    # Work in stored units throughout. The grid arrives multiplied by
    # HEIGHT_SCALE, so divide first; integer division matches the original,
    # which truncates rather than rounds. _finite_height keeps a NaN or inf
    # vertex -- which int() would raise on -- from losing the whole cell.
    stored = [[int(_finite_height(value)) // HEIGHT_SCALE for value in row] for row in rows]
    offset = float(stored[0][0])
    base = int(offset)

    deltas = [[0] * LAND_SIZE for _ in range(LAND_SIZE)]
    clamped: list[tuple[int, int]] = []

    def _fit(value: int, x: int, y: int) -> int:
        """Clamp one gradient to a signed byte, recording it if it did not fit."""
        if value > MAX_GRADIENT:
            clamped.append((x, y))
            return MAX_GRADIENT
        if value < MIN_GRADIENT:
            clamped.append((x, y))
            return MIN_GRADIENT
        return value

    # Column 0 carries the row-to-row difference; every other column carries
    # the difference along its own row. Both are relative to the base offset,
    # which cancels in the subtraction but is kept explicit to mirror the
    # decoder's structure.
    for y in range(1, LAND_SIZE):
        deltas[y][0] = _fit((stored[y][0] - base) - (stored[y - 1][0] - base), 0, y)
    for y in range(LAND_SIZE):
        for x in range(1, LAND_SIZE):
            deltas[y][x] = _fit(stored[y][x] - stored[y][x - 1], x, y)

    flat = [deltas[y][x] for y in range(LAND_SIZE) for x in range(LAND_SIZE)]
    payload = struct.pack("<f", offset)
    payload += struct.pack(f"<{LAND_NUM_VERTS}b", *flat)
    payload += bytes(_VHGT_PADDING)
    return offset, payload, clamped


def decode_heights_from_deltas(offset: float, deltas: list[int]) -> list[list[float]]:
    """Rebuild absolute heights from an offset and a flat delta list.

    A variant of the tes3fields decoder that takes unpacked integers rather
    than an encoded field, so :func:`encode_vertex_heights` can be checked
    against it without a serialisation round trip in between.

    Args:
        offset: The record's base height, in stored units.
        deltas: 4,225 signed deltas in row-major order.

    Returns:
        65 rows of 65 absolute heights in world units.

    Raises:
        HeightEncodeError: If ``deltas`` is not 4,225 long.
    """
    if len(deltas) != LAND_NUM_VERTS:
        raise HeightEncodeError(f"expected {LAND_NUM_VERTS} deltas, got {len(deltas)}")
    rows: list[list[float]] = []
    row_height = float(offset)
    for y in range(LAND_SIZE):
        start = y * LAND_SIZE
        row_height += deltas[start]
        height = row_height
        row = [height * HEIGHT_SCALE]
        for x in range(1, LAND_SIZE):
            height += deltas[start + x]
            row.append(height * HEIGHT_SCALE)
        rows.append(row)
    return rows


def round_trips(rows: list[list[float]]) -> bool:
    """Report whether a height grid survives encoding unchanged.

    Merged Lands performs this check as a runtime assertion on every cell. It
    is a function here so that a caller can act on the answer -- naming the
    cell and continuing -- rather than the process ending on it.

    Args:
        rows: 65 rows of 65 absolute heights in world units.

    Returns:
        ``True`` when decoding the encoded form reproduces the input exactly.

    Raises:
        HeightEncodeError: If the grid is not 65x65.
    """
    offset, payload, _ = encode_vertex_heights(rows)
    deltas = list(struct.unpack_from(f"<{LAND_NUM_VERTS}b", payload, 4))
    rebuilt = decode_heights_from_deltas(offset, deltas)
    truncated = [[float(int(v) // HEIGHT_SCALE * HEIGHT_SCALE) for v in row] for row in rows]
    return rebuilt == truncated


def vertex_normals_from_heights(rows: list[list[float]]) -> list[list[tuple[int, int, int]]]:
    """Recompute vertex normals to match a height grid.

    A normal that no longer matches its terrain is not a cosmetic problem: the
    engine lights the surface from it, so a merged cell whose heights moved but
    whose normals did not is lit as though the old terrain were still there.
    Any merge that changes a height must recompute this.

    The normal at a vertex comes from the cross product of the step to its
    eastern and northern neighbours, scaled to fit a signed byte. Vertices on
    the far edge have no neighbour in one direction and reuse the previous
    row or column, which is what the original does.

    Args:
        rows: 65 rows of 65 absolute heights in world units.

    Returns:
        65 rows of 65 ``(x, y, z)`` signed-byte normals.

    Raises:
        HeightEncodeError: If the grid is not 65x65.
    """
    _check_grid(rows)
    scale = float(HEIGHT_SCALE)
    step = 128.0 / scale
    limit = LAND_SIZE - 1
    normals: list[list[tuple[int, int, int]]] = []

    for y in range(LAND_SIZE):
        # Reuse the last interior vertex on the far edge rather than sampling
        # past it. The alternative -- wrapping, or a zero normal -- produces a
        # visible lighting seam along the north and east borders of every cell.
        fy = y - 1 if y == limit else y
        row: list[tuple[int, int, int]] = []
        for x in range(LAND_SIZE):
            fx = x - 1 if x == limit else x

            # _finite_height keeps a non-finite vertex from poisoning the whole
            # normal (nan propagates through the cross product and length).
            here = _finite_height(rows[fy][fx]) / scale
            east = _finite_height(rows[fy][fx + 1]) / scale
            north = _finite_height(rows[fy + 1][fx]) / scale

            # v1 runs east, v2 runs north; their cross product faces up.
            nx = -(east - here) * step
            ny = -(north - here) * step
            nz = step * step

            length = (nx * nx + ny * ny + nz * nz) ** 0.5
            if length == 0.0:  # pragma: no cover - degenerate only if step were zero; guard anyway
                row.append((0, 0, 127))
                continue
            unit = length / 127.0
            # Saturating: a component that rounds to +-128 (or a non-finite one)
            # must not raise in pack_vertex_normals -- clamp, keep the cell.
            row.append(
                (_to_signed_byte(nx / unit), _to_signed_byte(ny / unit), _to_signed_byte(nz / unit))
            )
        normals.append(row)
    return normals


def pack_vertex_normals(normals: list[list[tuple[int, int, int]]]) -> bytes:
    """Pack a normals grid into a ``VNML`` payload.

    Args:
        normals: 65 rows of 65 ``(x, y, z)`` signed-byte triples.

    Returns:
        The 12,675-byte payload.

    Raises:
        HeightEncodeError: If the grid is not 65x65.
    """
    if len(normals) != LAND_SIZE or any(len(row) != LAND_SIZE for row in normals):
        raise HeightEncodeError(f"expected a {LAND_SIZE}x{LAND_SIZE} normals grid")
    # Saturate to the signed byte the format stores. Recomputed normals are
    # already in range (vertex_normals_from_heights clamps), but a *carried*
    # normal reconstructed as reference + delta can land just outside, and
    # ``struct.pack("b", ...)`` raises on that -- which would abort the merge on
    # one vertex. This mirrors the OpenMW fork's saturating RelativeTo::add.
    flat = [
        max(-128, min(127, component)) for row in normals for triple in row for component in triple
    ]
    return struct.pack(f"<{3 * LAND_NUM_VERTS}b", *flat)
