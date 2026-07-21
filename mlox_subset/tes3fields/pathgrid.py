"""Decode the ``PGRD`` (path grid) ``connections`` field.

``PGRC`` is a flat ``uint32`` array and is meaningless on its own: it is a
*concatenation* of each point's neighbour list, with no delimiters. The slicing
lives in ``PGRP`` -- every point carries the number of connections that belong
to it. Read alone, the field is a wall of integers; read together with the
points, it is a readable adjacency list, and a diff shows which node's edges a
mod actually rewired.

This mirrors how a script's ``bytecode`` is disassembled using the record's
``text``: the neighbouring field is what makes the blob mean something.

Format sources, both permissively licensed: the UESP `PGRD record page
<https://en.uesp.net/wiki/Morrowind_Mod:Mod_File_Format/PGRD>`_ and
**TES3Tool** (MIT). The slicing model was confirmed against real plugins: the
sum of every point's connection count equals the edge count exactly
(``CODE_REVIEW.md`` §22).
"""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence
from typing import Any

from mlox_subset.mwscript.tes3conv import BytecodeDecodeError, decode_bytecode_field


class PathGridDecodeError(Exception):
    """Raised when a path-grid field cannot be decoded."""


def decode_connections(value: str | bytes, expected_edges: int | None = None) -> list[int]:
    """Decode ``PGRC`` into the flat list of point indices.

    **tes3conv prefixes this field with a ``uint32`` count**, the same wrapping
    it puts on ``variables`` (see
    :func:`~mlox_subset.mwscript.tes3conv.decode_variables_field`, which strips
    the same four bytes). The raw subrecord inside a plugin has no such prefix,
    so both shapes reach this function and it detects rather than assumes.

    Measured across 717 path grids in 120 cached tes3conv dumps: **100%** carry
    the prefix. The landscape fields in the same dumps carry none -- all 290
    records decode to exactly their documented sizes -- so this is specific to
    the length-prefixed fields, not a blanket property of tes3conv output.

    Leaving the prefix in place is not a cosmetic error: it shifts every
    subsequent edge by one slot, so each point is attributed its neighbour's
    connections. On the record this was found with, it also made the first
    "edge" the value 224 in a grid of 62 points -- an index that cannot exist.

    Args:
        value: The ``connections`` field as tes3conv wrote it.
        expected_edges: The sum of every point's connection count, when known.
            Used to confirm the prefix rather than infer it from shape alone.

    Returns:
        Every edge target, in storage order, with any length prefix removed.
        Slicing them into per-point neighbour lists needs the points -- see
        :func:`render_connections`.

    Raises:
        PathGridDecodeError: If the value cannot be decoded, or its length is
            not a whole number of ``uint32`` values.
    """
    try:
        raw = decode_bytecode_field(value)
    except BytecodeDecodeError as exc:
        raise PathGridDecodeError(str(exc)) from exc
    if len(raw) % 4:
        raise PathGridDecodeError(
            f"connections is {len(raw)} bytes, which is not a whole number of "
            f"uint32 edges. The field is truncated or is not a PGRC payload."
        )
    edges = list(struct.unpack_from(f"<{len(raw) // 4}I", raw, 0))
    # A leading value equal to the count of everything after it is a length
    # prefix, not an edge. When the points are to hand their total confirms it
    # outright; without them the self-describing shape is evidence enough.
    looks_prefixed = bool(edges) and edges[0] == len(edges) - 1
    confirmed = expected_edges is None or expected_edges == len(edges) - 1
    if looks_prefixed and confirmed:
        return edges[1:]
    return edges


# Any: one entry of tes3conv's `points` JSON. Its shape has varied across
# tes3conv versions, which is exactly why this function probes rather than
# assumes -- a narrower annotation here would be a claim we cannot make.
def _point_fields(point: Any) -> tuple[tuple[int, int, int] | None, int | None]:  # noqa: ANN401
    """Pull ``(x, y, z)`` and the connection count out of one PGRP entry.

    tes3conv has used more than one spelling for these over its versions, so
    each is looked up under several names rather than assuming one shape. A
    name that is missing yields ``None`` and the caller degrades gracefully.

    Args:
        point: One entry of the record's ``points`` list.

    Returns:
        ``(location, connection_count)``, either of which may be ``None``.
    """
    if not isinstance(point, Mapping):
        return None, None
    location = point.get("location") or point.get("position")
    coords: tuple[int, int, int] | None = None
    if isinstance(location, Sequence) and not isinstance(location, str) and len(location) >= 3:
        try:
            coords = (int(location[0]), int(location[1]), int(location[2]))
        except (TypeError, ValueError):
            coords = None
    elif all(k in point for k in ("x", "y", "z")):
        try:
            coords = (int(point["x"]), int(point["y"]), int(point["z"]))
        except (TypeError, ValueError):
            coords = None
    count = point.get("connection_count")
    if count is None:
        count = point.get("connections") or point.get("connection_num")
    try:
        return coords, int(count) if count is not None else None
    except (TypeError, ValueError):
        return coords, None


# Any: the record's `points` value straight out of the JSON -- may be a list
# of mappings, absent, or something unexpected from a malformed plugin.
def render_connections(value: str | bytes, points: Any = None) -> str:  # noqa: ANN401
    """Render ``PGRC`` as a per-point adjacency list.

    Args:
        value: The ``connections`` field.
        points: The record's ``points`` list, when available. Without it the
            edges cannot be attributed to a point, so they are listed flat and
            the output says why.

    Returns:
        One line per point (``0 (x, y, z) -> 1, 6, 3``), or a flat edge list
        when the points are unavailable.

    Raises:
        PathGridDecodeError: If the connections field cannot be decoded.
    """
    # Pass the points' own total when we have it, so the length-prefix
    # detection is confirmed rather than inferred from shape.
    expected: int | None = None
    if isinstance(points, Sequence) and not isinstance(points, str):
        counts = [_point_fields(p)[1] for p in points]
        if counts and all(c is not None for c in counts):
            expected = sum(c for c in counts if c is not None)
    edges = decode_connections(value, expected)
    if not edges:
        return "; PGRC -- no connections (every path point is isolated)"

    if not isinstance(points, Sequence) or isinstance(points, str) or not points:
        return "\n".join(
            [
                f"; PGRC -- {len(edges)} edge(s), flat",
                "; the 'points' field was not available, so these could not be attributed",
                "; to their source points -- each value is an index into the point list",
                *(f"  {i:4d}: -> {t}" for i, t in enumerate(edges)),
            ]
        )

    lines = [f"; PGRC -- {len(edges)} edge(s) over {len(points)} point(s)"]
    cursor = 0
    unknown_counts = 0
    for index, point in enumerate(points):
        coords, count = _point_fields(point)
        where = f"({coords[0]}, {coords[1]}, {coords[2]})" if coords else "(location unread)"
        if count is None:
            unknown_counts += 1
            lines.append(f"  {index:4d} {where} -> ? (connection count unread)")
            continue
        targets = edges[cursor : cursor + count]
        cursor += count
        arrow = ", ".join(str(t) for t in targets) if targets else "(none)"
        lines.append(f"  {index:4d} {where} -> {arrow}")

    if unknown_counts:
        lines.insert(
            1,
            f"; NOTE: {unknown_counts} point(s) had no readable connection count, so the "
            f"edges after the first are not reliably attributed",
        )
    if cursor < len(edges):
        lines.append(
            f"; NOTE: {len(edges) - cursor} trailing edge(s) unaccounted for: "
            f"{', '.join(str(t) for t in edges[cursor:][:16])}"
        )
    elif cursor > len(edges):
        lines.append(
            "; NOTE: the points claim more connections than the field holds -- "
            "the record is inconsistent"
        )
    return "\n".join(lines)
