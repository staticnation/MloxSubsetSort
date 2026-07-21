"""Spatial helpers: getting world coordinates back out of conflict records.

The conflict scanner keys id-less records by their grid coordinates, because
exterior cells, ``LAND`` and ``PGRD`` records have no name to key on (see
``_tes3conv_record_key`` in the engine). That makes the id a *string* like
``"(43, -45)"`` or ``"Balmora (-3, -2)"`` -- readable, and stable across the
two scanning engines, but not directly usable as a coordinate.

This module turns those ids back into integers so conflicts can be placed on a
map. It parses rather than re-derives: the id is what the rest of the tool
already agreed the record is called, and re-deriving coordinates from the
plugin would risk the map disagreeing with the list beside it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

#: Grid coordinates as the conflict scanner writes them: a trailing
#: ``(x, y)`` with optional sign and whitespace. Anchored at the end so a cell
#: whose *name* contains parentheses does not match the wrong pair.
_GRID_RE = re.compile(r"\((-?\d+)\s*,\s*(-?\d+)\)\s*$")

#: Sanity bound for exterior grid coordinates, mirroring the engine's
#: ``CELL_GRID_LIMIT``. A garbage grid field on an interior cell can otherwise
#: place a marker millions of cells away and flatten the whole map.
GRID_LIMIT = 128


class Cell(NamedTuple):
    """One exterior cell's position on the world grid.

    Attributes:
        x: Grid X, increasing east.
        y: Grid Y, increasing north.
    """

    x: int
    y: int


def parse_grid(record_id: object) -> Cell | None:
    """Extract grid coordinates from a conflict record's id.

    Handles both shapes the scanner produces: a bare ``"(43, -45)"`` for
    landscape and exterior cells, and ``"Balmora (-3, -2)"`` for cell-scoped
    records such as path grids.

    Args:
        record_id: The conflict's ``id``. Anything non-string yields ``None``
            rather than raising -- ids come from scanned third-party plugins
            and are not guaranteed well-formed.

    Returns:
        The cell, or ``None`` if the id carries no usable coordinates or they
        fall outside :data:`GRID_LIMIT`.
    """
    if not isinstance(record_id, str):
        return None
    match = _GRID_RE.search(record_id)
    if match is None:
        return None
    x, y = int(match.group(1)), int(match.group(2))
    if abs(x) > GRID_LIMIT or abs(y) > GRID_LIMIT:
        return None
    return Cell(x, y)


def is_interior(record_id: object) -> bool:
    """Report whether a cell-scoped record id names an interior cell.

    An interior's path grid carries grid ``(0, 0)`` and a cell name; an
    exterior's carries real coordinates. A name with no coordinates at all is
    therefore an interior.

    Args:
        record_id: The conflict's ``id``.

    Returns:
        ``True`` for a named cell with no grid coordinates.
    """
    return isinstance(record_id, str) and bool(record_id.strip()) and parse_grid(record_id) is None


class CellConflicts(NamedTuple):
    """Every conflict landing on one exterior cell.

    Attributes:
        cell: Where it is.
        total: How many conflicting records touch this cell.
        mine: How many of those involve the user's own mods.
        types: Record type to count, so the map can say *what* collided.
        plugins: Every plugin involved, in load order of first appearance.
        winners: Winning plugin to the number of records it wins here.
    """

    cell: Cell
    total: int
    mine: int
    types: dict[str, int]
    plugins: list[str]
    winners: dict[str, int]


def group_by_cell(conflicts: Iterable[Mapping[str, Any]]) -> dict[Cell, CellConflicts]:
    """Aggregate conflict records onto the world grid.

    Aggregation is the point: a real load order produces tens of thousands of
    conflicts, and a map that drew one marker per record would be unreadable
    and slow. Records with no coordinates (objects, dialogue, interiors) are
    skipped -- they are not spatial and belong in the list view.

    Args:
        conflicts: Conflict dicts as ``detect_conflicts`` returns them, each
            with ``type``, ``id``, ``plugins``, ``winner`` and
            ``involves_subset``.

    Returns:
        One :class:`CellConflicts` per exterior cell that has any.
    """
    out: dict[Cell, CellConflicts] = {}
    for conflict in conflicts:
        cell = parse_grid(conflict.get("id"))
        if cell is None:
            continue
        entry = out.get(cell)
        if entry is None:
            entry = CellConflicts(cell, 0, 0, {}, [], {})
        rectype = str(conflict.get("type") or "?")
        entry.types[rectype] = entry.types.get(rectype, 0) + 1
        plugins = conflict.get("plugins") or []
        for plugin in plugins:
            if plugin not in entry.plugins:
                entry.plugins.append(plugin)
        winner = conflict.get("winner")
        if winner:
            entry.winners[winner] = entry.winners.get(winner, 0) + 1
        out[cell] = entry._replace(
            total=entry.total + 1,
            mine=entry.mine + (1 if conflict.get("involves_subset") else 0),
        )
    return out


def bounds(cells: Iterable[Cell]) -> tuple[int, int, int, int] | None:
    """Compute the inclusive bounding box of a set of cells.

    Args:
        cells: The cells to bound.

    Returns:
        ``(min_x, min_y, max_x, max_y)``, or ``None`` if there are no cells.
    """
    xs = [c.x for c in cells]
    ys = [c.y for c in cells]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)
