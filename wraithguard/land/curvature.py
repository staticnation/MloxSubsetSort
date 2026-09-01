r"""Estimate how much structure a height grid carries at each vertex.

**Why a merger needs this.** :func:`~wraithguard.land.merge.average_delta`
settles a contested vertex by weighting the two edits by magnitude, on the
assumption that the larger edit is the more deliberate one. That assumption
fails in a case that is not rare: a mod which bulk-shifts a whole cell by 500
units will dominate one which carved a precise 60-unit road cut, even though
the cut is the structural, intentional edit and the shift is the blunt one.

Magnitude cannot tell those apart. *Curvature* can. Terrain that a human
deliberately shaped -- a road, a terrace, a building pad, a cliff lip --
introduces local structure; a bulk offset introduces none, because every vertex
moves together and the surface keeps its shape exactly.

**The measure.** Following Zhao, Jiang and Guo (2022), "A Novel Quadratic Error
Metric Mesh Simplification Algorithm for 3D Building Models Based on
'Local-Vertex' Texture Features" (ISPRS Archives XLVIII-3/W2-2022, 109--115,
CC BY 4.0), section 2.3: a vertex's curvature is the mean angle between its own
normal and the normals of the faces around it.

.. math:: c_{v_i} = \frac{\sum_k \alpha(n_{v_i}, n_i)}{k}

The paper uses it to *raise* the cost of collapsing an edge in a
feature-rich region, so simplification eats flat areas first. The quantity is
the same one either way: "how much shape is here". We are not simplifying a
mesh, so the quadratic error metric itself does not apply -- but this term
does, and it is the part that was missing.

**Applied to a regular grid.** The paper works on an irregular triangle mesh
where face areas vary, so it area-weights the vertex normal. A ``LAND`` record
is a regular 65x65 grid: every quad is the same size, so the area weighting is
a constant and cancels. That makes the measure cheaper here than in the paper,
not more expensive.

**What this module deliberately does not do.** It does not decide anything. It
reports a number per vertex, and :mod:`~wraithguard.land.merge` decides what to
do with it. Keeping the measurement separate from the policy means the policy
can change without re-deriving the geometry.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

from wraithguard.tes3fields.landscape import HEIGHT_SCALE, LAND_SIZE

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Horizontal distance between adjacent vertices, in the same units the
#: heights use once divided by HEIGHT_SCALE. Matches the step
#: :func:`~wraithguard.land.heights.vertex_normals_from_heights` uses, so the
#: two agree about what a slope is.
_STEP: Final = 128.0 / HEIGHT_SCALE

#: Neighbour offsets, in the order the faces around a vertex are visited.
_NEIGHBOURS: Final[tuple[tuple[int, int], ...]] = ((1, 0), (0, 1), (-1, 0), (0, -1))


def _normal_at(rows: Sequence[Sequence[float]], x: int, y: int) -> tuple[float, float, float]:
    """The surface normal at one vertex, from its eastern and northern steps.

    Args:
        rows: Absolute heights in world units.
        x: Column.
        y: Row.

    Returns:
        A unit normal.
    """
    limit = len(rows) - 1
    fx = x - 1 if x == limit else x
    fy = y - 1 if y == limit else y
    scale = float(HEIGHT_SCALE)

    here = rows[fy][fx] / scale
    east = rows[fy][fx + 1] / scale
    north = rows[fy + 1][fx] / scale

    nx = -(east - here) * _STEP
    ny = -(north - here) * _STEP
    nz = _STEP * _STEP
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length == 0.0:  # pragma: no cover - nz is _STEP**2 > 0, so length is never zero
        return (0.0, 0.0, 1.0)
    return (nx / length, ny / length, nz / length)


def curvature_at(rows: Sequence[Sequence[float]], x: int, y: int) -> float:
    """How much the surface bends at one vertex.

    The mean angle, in radians, between this vertex's normal and those of its
    immediate neighbours. Zero on a flat plane *and* on a uniform slope --
    which is the point: a constant gradient carries no structure, however
    steep it is.

    Args:
        rows: Absolute heights in world units, 65x65.
        x: Column.
        y: Row.

    Returns:
        The mean angle in radians. Larger means more local structure.
    """
    side = len(rows)
    here = _normal_at(rows, x, y)
    total = 0.0
    counted = 0

    for dx, dy in _NEIGHBOURS:
        nx, ny = x + dx, y + dy
        if not (0 <= nx < side and 0 <= ny < side):
            continue
        other = _normal_at(rows, nx, ny)
        dot = here[0] * other[0] + here[1] * other[1] + here[2] * other[2]
        # Clamp before acos: floating point can push a dot product of two unit
        # vectors a hair past 1.0, and math.acos raises on that rather than
        # returning zero, which would abort a merge over a rounding error.
        total += math.acos(max(-1.0, min(1.0, dot)))
        counted += 1

    return total / counted if counted else 0.0


def curvature_map(rows: Sequence[Sequence[float]]) -> list[list[float]]:
    """Curvature at every vertex of a height grid.

    Args:
        rows: Absolute heights in world units, 65x65.

    Returns:
        A grid of mean angles in radians.

    Raises:
        ValueError: If the grid is not square, or smaller than 2x2.
    """
    side = len(rows)
    if side < 2 or any(len(row) != side for row in rows):
        raise ValueError(f"expected a square grid of at least 2x2, got {side} rows")
    return [[curvature_at(rows, x, y) for x in range(side)] for y in range(side)]


def structure_introduced(
    reference: Sequence[Sequence[float]], edited: Sequence[Sequence[float]], x: int, y: int
) -> float:
    """How much structure an edit *added* at one vertex.

    This is the quantity a merge actually wants. Terrain that was already a
    cliff scores high on curvature whoever touched it; what distinguishes a
    deliberate edit is that it made the surface *more* structured than it
    found it.

    A bulk offset scores zero here however large it is, because shifting every
    vertex together leaves the shape untouched. A road cut scores high even
    though it moves vertices far less.

    Args:
        reference: The terrain before the edit.
        edited: The terrain after it.
        x: Column.
        y: Row.

    Returns:
        The increase in curvature, in radians. Never negative: an edit that
        *smooths* terrain has introduced no structure, and treating that as a
        negative weight would let it argue for itself by flattening harder.
    """
    before = curvature_at(reference, x, y)
    after = curvature_at(edited, x, y)
    return max(0.0, after - before)


def is_land_grid(rows: Sequence[Sequence[float]]) -> bool:
    """Whether a grid is the size a ``LAND`` record uses.

    Args:
        rows: The candidate grid.

    Returns:
        ``True`` for a 65x65 grid.
    """
    return len(rows) == LAND_SIZE and all(len(row) == LAND_SIZE for row in rows)
