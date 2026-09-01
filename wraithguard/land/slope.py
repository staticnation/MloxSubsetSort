"""Guarantee that merged terrain is representable in ``VHGT``.

**The constraint.** ``VHGT`` stores heights as signed-byte deltas along each
row, and down column zero. Two adjacent vertices therefore cannot differ by
more than 127 stored units -- 1,016 world units. Real terrain never approaches
this; merged terrain can, because seam repair pulls a shared border to an
agreed height while the vertex beside it stays where its mod put it.

Feathering (:mod:`~wraithguard.land.seams`) removes most of that by spreading
the correction inward. It is a good heuristic and not a guarantee: measured on
two overlapping Solstheim mods it took 125 unencodable vertices down to 6. This
module removes the rest, by construction.

**How.** Walk every constrained pair. Where the step exceeds the limit, move the
two ends toward each other until it fits, and repeat until nothing exceeds. Each
pass strictly reduces the total excess, so it terminates.

**Which end moves: an even split, and curvature weighting measured worse.**

It seemed obvious that the excess should be pushed away from structured terrain
-- protect the lip of a carved cliff, flatten the plateau beside it -- the same
argument :mod:`~wraithguard.land.curvature` makes about which *edit* matters.
Measured on two overlapping Solstheim mods:

=========================== ======= ============== =========
split                       passes  vertices moved clamped
=========================== ======= ============== =========
even                        7       42             0
inverse to local structure  24+      129            0
=========================== ======= ============== =========

Both reach representable terrain. The weighted split needs three times the
passes and touches three times the vertices, because **an asymmetric correction
propagates**: moving one end a long way to spare the other creates a fresh
over-limit step with that vertex's *next* neighbour, which the following pass
has to fix, and so on outward. An even split disturbs both sides equally and
settles.

So the default is an even split. ``use_curvature`` remains available because
the argument for it is sound on a single step in isolation -- it is the
interaction between steps that defeats it -- and a landscape with very sparse,
very steep features may yet behave differently.

**Shared vertices move in lockstep, or not at all.** A cell's boundary vertex
is the same ground as its neighbour's. Moving one without the other would
reopen the seam that repair just closed, so every adjustment to a boundary
vertex is applied to its twin -- up to three of them at a corner -- in the same
pass.

That only works when every cell sharing the vertex is *in the set being
written*. Where one is not -- terrain the merge does not own and will not emit
-- the vertex is **pinned**: moving it would tear the merged landscape against
ground that stays exactly where it is. Measured before this rule existed, the
limiter left 3 such borders disagreeing by up to 1,227 world units, each one a
crack against untouched vanilla.

Pinning can leave a step that cannot be made representable, because the only
vertices able to absorb it are ones we may not move. That is reported rather
than forced: a clamped vertex is a small local error, and a tear against
vanilla is a visible seam across the world.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from wraithguard.land.curvature import curvature_at
from wraithguard.tes3fields.landscape import HEIGHT_SCALE, LAND_SIZE

if TYPE_CHECKING:
    from array import array

_log: Final = logging.getLogger(__name__)

#: Exterior cell coordinates.
Coords = tuple[int, int]

#: The widest step two adjacent vertices may have, in world units. A stored
#: delta is a signed byte and every height is divided by
#: :data:`~wraithguard.tes3fields.landscape.HEIGHT_SCALE`, so 127 * 8.
MAX_STEP: Final = 127 * HEIGHT_SCALE

#: Leave a little room below the true limit. Encoding divides by
#: :data:`HEIGHT_SCALE` with truncation, so a step sitting exactly on the
#: boundary can round the wrong way and clamp anyway.
_SAFETY: Final = HEIGHT_SCALE

#: How much local structure shields a vertex from being moved. At 0 the split
#: is even; higher values protect authored features more strongly. Curvature is
#: in radians and rarely exceeds 0.5, so this scales it into a useful range.
_STRUCTURE_WEIGHT: Final = 12.0

#: Passes before giving up. Each pass strictly reduces total excess, so this is
#: a guard against a pathological input rather than an expected limit.
MAX_PASSES: Final = 24

_LAST: Final = LAND_SIZE - 1


@dataclass(slots=True)
class SlopeReport:
    """What the limiter had to do.

    Attributes:
        adjusted: Vertices moved to make a step representable.
        worst_excess: The widest over-limit step found, in world units.
        passes: How many sweeps were needed.
        converged: Whether every step fits. ``False`` means the terrain still
            cannot be encoded exactly and the writer will clamp.
        cells_touched: Cells that needed any adjustment.
        pinned: Adjustments refused because the vertex is shared with terrain
            outside the merge. Moving it would tear against ground that stays
            put, which is worse than the clamp it avoids.
    """

    adjusted: int = 0
    pinned: int = 0
    worst_excess: int = 0
    passes: int = 0
    converged: bool = True
    cells_touched: set[Coords] = field(default_factory=set)


def _twins(coords: Coords, x: int, y: int) -> list[tuple[Coords, int, int]]:
    """Every other cell that shares this vertex.

    Args:
        coords: The cell.
        x: Column.
        y: Row.

    Returns:
        ``(cell, x, y)`` for each cell holding the same ground, excluding the
        one asked about. Empty for an interior vertex.
    """
    cx, cy = coords
    xs: list[tuple[int, int]] = [(cx, x)]
    ys: list[tuple[int, int]] = [(cy, y)]
    if x == 0:
        xs.append((cx - 1, _LAST))
    elif x == _LAST:
        xs.append((cx + 1, 0))
    if y == 0:
        ys.append((cy - 1, _LAST))
    elif y == _LAST:
        ys.append((cy + 1, 0))

    out: list[tuple[Coords, int, int]] = []
    for gx, vx in xs:
        for gy, vy in ys:
            if (gx, gy) != coords:
                out.append(((gx, gy), vx, vy))
    return out


def _is_movable(
    cells: dict[Coords, array[int]],
    coords: Coords,
    x: int,
    y: int,
    authoritative: frozenset[Coords] = frozenset(),
) -> bool:
    """Whether a vertex can move without tearing against terrain we do not own.

    Two ways it cannot. A cell sharing the vertex may be absent, so moving ours
    would tear against ground the game still holds. Or a cell sharing it may be
    *authoritative* -- borrowed unedited so a border could be reconciled -- in
    which case moving the vertex would edit vanilla terrain, and the cell
    beyond the borrowed one would tear against the result. Seam repair already
    refuses to move those; the limiter runs afterwards and has to refuse too,
    or it undoes the repair.

    Args:
        cells: The landmass being written.
        coords: The cell.
        x: Column.
        y: Row.
        authoritative: Cells that carry no edits and must not be moved.

    Returns:
        ``True`` when every cell sharing this vertex is present and editable.
    """
    if coords in authoritative:
        return False
    return all(twin in cells and twin not in authoritative for twin, _, _ in _twins(coords, x, y))


def _shift(
    cells: dict[Coords, array[int]],
    coords: Coords,
    x: int,
    y: int,
    delta: int,
    report: SlopeReport,
    authoritative: frozenset[Coords] = frozenset(),
) -> bool:
    """Move a vertex, and every copy of it in a neighbouring cell.

    Args:
        cells: The landmass, modified in place.
        coords: The cell.
        x: Column.
        y: Row.
        delta: How far to move it.
        report: Updated with the adjustment.
        authoritative: Cells that carry no edits and must not be moved.

    Returns:
        ``True`` if the vertex moved. ``False`` means it is shared with a cell
        outside the set and had to be left alone.
    """
    if delta == 0:
        return True
    if not _is_movable(cells, coords, x, y, authoritative):
        report.pinned += 1
        return False
    cells[coords][y * LAND_SIZE + x] += delta
    report.adjusted += 1
    report.cells_touched.add(coords)
    for twin, tx, ty in _twins(coords, x, y):
        grid = cells.get(twin)
        # A vertex only reaches here once _is_movable confirmed every one of its
        # twins is present, so this get never misses; the guard stays defensive.
        if grid is not None:  # pragma: no branch
            grid[ty * LAND_SIZE + tx] += delta
            report.cells_touched.add(twin)
    return True


def _structure_map(grid: array[int]) -> list[float]:
    """Local structure at every vertex of one cell.

    Computed once per cell rather than per pass. The limiter's own adjustments
    change the surface slightly, but the *shape* of a landscape -- where the
    ridges and flats are -- does not move meaningfully under corrections small
    enough to be worth making.

    Args:
        grid: The cell's heights.

    Returns:
        Curvature in radians, flat, one per vertex.
    """
    rows = [[float(v) for v in grid[y * LAND_SIZE : (y + 1) * LAND_SIZE]] for y in range(LAND_SIZE)]
    return [curvature_at(rows, x, y) for y in range(LAND_SIZE) for x in range(LAND_SIZE)]


def _split(structure_a: float, structure_b: float, excess: int) -> tuple[int, int]:
    """Divide an over-limit step between its two ends.

    A vertex carrying structure resists; a flat one absorbs. With no structure
    on either side this is an even split, which is the right default.

    Args:
        structure_a: Curvature at the first vertex.
        structure_b: Curvature at the second.
        excess: How much the step must shrink by, always positive.

    Returns:
        How far each end moves. The two always sum to ``excess``.
    """
    weight_a = 1.0 / (1.0 + structure_a * _STRUCTURE_WEIGHT)
    weight_b = 1.0 / (1.0 + structure_b * _STRUCTURE_WEIGHT)
    total = weight_a + weight_b
    if total <= 0.0:  # pragma: no cover - curvature is a non-negative angle, so each
        # weight is 1/(1+non-negative) and strictly positive; their sum can never be
        # <= 0. Kept as a divide-by-zero guard against a future signed structure metric.
        share = excess // 2
        return share, excess - share
    share_a = round(excess * weight_a / total)
    share_a = max(0, min(excess, share_a))
    return share_a, excess - share_a


def _pairs() -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Every pair of vertices ``VHGT`` stores a delta between.

    The encoding is doubly cumulative: each row accumulates along itself, and
    column zero accumulates down the rows. Those are exactly the pairs whose
    difference has to fit in a signed byte -- a diagonal neighbour has no
    stored delta and no constraint.

    Returns:
        ``((x1, y1), (x2, y2))`` for each constrained pair.
    """
    pairs: list[tuple[tuple[int, int], tuple[int, int]]] = [
        ((x - 1, y), (x, y)) for y in range(LAND_SIZE) for x in range(1, LAND_SIZE)
    ]
    pairs.extend(((0, y - 1), (0, y)) for y in range(1, LAND_SIZE))
    return pairs


_PAIRS: Final = _pairs()


def limit_slopes(
    cells: dict[Coords, array[int]],
    limit: int = MAX_STEP - _SAFETY,
    use_curvature: bool = False,
    authoritative: frozenset[Coords] = frozenset(),
) -> SlopeReport:
    """Make every merged cell representable in ``VHGT``.

    Args:
        cells: Merged absolute heights per cell, flat 65x65 arrays. Modified
            in place.
        limit: The widest step to allow, in world units.
        authoritative: Cells carrying no edits, borrowed only so their borders
            could be reconciled. Their vertices are never moved -- doing so
            would edit vanilla terrain and reopen the seam repair just closed.
        use_curvature: Distribute corrections away from structured terrain
            instead of splitting them evenly. Measured slower and more invasive
            -- see the module docstring -- so it is off by default.

    Returns:
        What was adjusted, and whether it converged.
    """
    report = SlopeReport()
    if not cells:
        return report

    structure: dict[Coords, list[float]] = {}
    if use_curvature:
        structure = {coords: _structure_map(grid) for coords, grid in cells.items()}
    flat = [0.0] * (LAND_SIZE * LAND_SIZE)
    # Sorted once, not once per pass. The order exists to make the result
    # deterministic -- a vertex moved by two neighbours must be moved in the
    # same sequence every run -- and no pass adds or removes a cell, only
    # mutates the grids behind them. On a real load order this was 24 sorts of
    # 17,560 tuples to produce the same list 24 times.
    in_order = sorted(cells)

    for attempt in range(1, MAX_PASSES + 1):
        report.passes = attempt
        excessive = 0

        for coords in in_order:
            grid = cells[coords]
            shape = structure.get(coords, flat)

            for (ax, ay), (bx, by) in _PAIRS:
                first = ax + ay * LAND_SIZE
                second = bx + by * LAND_SIZE
                step = grid[second] - grid[first]
                if -limit <= step <= limit:
                    continue

                excessive += 1
                excess = abs(step) - limit
                report.worst_excess = max(report.worst_excess, excess)
                # If only one end can move it has to absorb the whole excess,
                # otherwise the step never closes and the sweep spins.
                movable_a = _is_movable(cells, coords, ax, ay, authoritative)
                movable_b = _is_movable(cells, coords, bx, by, authoritative)
                if movable_a and movable_b:
                    share_a, share_b = _split(shape[first], shape[second], excess)
                elif movable_a:
                    share_a, share_b = excess, 0
                elif movable_b:
                    share_a, share_b = 0, excess
                else:
                    report.pinned += 1
                    continue

                # Close the gap from both ends. The signs are opposite so the
                # step shrinks rather than the whole cell drifting.
                direction = 1 if step > 0 else -1
                _shift(cells, coords, ax, ay, direction * share_a, report)
                _shift(cells, coords, bx, by, -direction * share_b, report)

        if excessive == 0:
            _log.info(
                "slope limiter: converged after %d pass(es), %d adjustment(s)",
                attempt,
                report.adjusted,
            )
            return report

    # The sweep targets a slightly stricter limit than the encoder enforces, so
    # running out of passes does not necessarily mean the terrain is
    # unencodable. Judge convergence on what the encoder will actually reject.
    remaining = count_unencodable(cells)
    report.converged = remaining == 0
    if report.converged:
        _log.info(
            "slope limiter: used all %d passes but every step is encodable " "(%d adjustment(s))",
            MAX_PASSES,
            report.adjusted,
        )
    else:
        _log.warning(
            "slope limiter did not converge in %d passes; %d step(s) remain too "
            "steep and will be clamped on write",
            MAX_PASSES,
            remaining,
        )
    return report


def count_unencodable(cells: dict[Coords, array[int]], limit: int = MAX_STEP) -> int:
    """How many steps exceed what ``VHGT`` can store.

    A cheap check for reporting and for tests, independent of the encoder.

    Args:
        cells: Merged heights per cell.
        limit: The widest representable step, in world units.

    Returns:
        The number of over-limit steps.
    """
    total = 0
    for grid in cells.values():
        for (ax, ay), (bx, by) in _PAIRS:
            step = grid[bx + by * LAND_SIZE] - grid[ax + ay * LAND_SIZE]
            if step > limit or step < -limit:
                total += 1
    return total
