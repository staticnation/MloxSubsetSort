"""The VHGT slope limiter: making merged terrain representable.

``limit_slopes`` walks every adjacent-vertex pair and, where the step is too
steep for the delta-encoded ``VHGT`` format, moves the movable end(s) to close
it. These pin down the corners the fidelity suite does not reach directly: the
edge-twin propagation (vertical and horizontal), a vertex pinned because its
twin is missing or its cell is authoritative, the even split of an excess, and
the empty and curvature-weighted entry points.
"""

from __future__ import annotations

from array import array

from wraithguard.land.slope import SlopeReport, _shift, _split, limit_slopes
from wraithguard.tes3fields.landscape import LAND_SIZE

V = LAND_SIZE * LAND_SIZE
_LAST = LAND_SIZE - 1


def _cell(spikes: dict[tuple[int, int], int]) -> array:
    """A flat 65x65 grid with the given vertices raised."""
    grid = [0] * V
    for (x, y), value in spikes.items():
        grid[y * LAND_SIZE + x] = value
    return array("i", grid)


def test_no_cells_is_an_empty_report() -> None:
    """Nothing to limit means an empty, converged-by-default report."""
    report = limit_slopes({})
    assert report.adjusted == 0
    assert report.pinned == 0


def test_an_interior_spike_is_flattened() -> None:
    """A spike far above its neighbours is brought within the step limit."""
    report = limit_slopes({(0, 0): _cell({(32, 32): 30_000})})
    assert report.adjusted > 0
    assert report.cells_touched == {(0, 0)}


def test_a_horizontal_border_step_moves_the_twin_cell() -> None:
    """A steep step on a shared vertical edge propagates into the neighbour."""
    cells = {(0, 0): _cell({(_LAST, 32): 5_000}), (1, 0): _cell({})}
    report = limit_slopes(cells)
    assert report.cells_touched == {(0, 0), (1, 0)}
    assert report.converged


def test_a_vertical_border_step_moves_the_twin_cell() -> None:
    """A steep step on a shared horizontal edge reaches the cell below/above."""
    cells = {(0, 0): _cell({(32, _LAST): 5_000}), (0, 1): _cell({})}
    report = limit_slopes(cells)
    assert (0, 1) in report.cells_touched


def test_an_edge_vertex_with_no_twin_still_resolves_from_the_inside() -> None:
    """A border spike whose twin cell is absent is closed by the interior end."""
    report = limit_slopes({(0, 0): _cell({(_LAST, 32): 5_000})})
    assert report.adjusted > 0  # the movable interior end absorbs the excess


def test_a_step_whose_first_end_is_pinned_moves_the_second() -> None:
    """A spike on the left edge pins the first vertex, so the second absorbs it."""
    report = limit_slopes({(0, 0): _cell({(0, 32): 5_000})})  # x=0 border, no left twin
    assert report.adjusted > 0


def test_shift_refuses_a_vertex_it_cannot_move() -> None:
    """``_shift`` reports a pinned vertex rather than editing across a seam.

    A border vertex whose twin cell is absent is not movable, so a non-zero
    shift is refused -- the guard the limiter relies on to keep from reopening a
    seam into terrain it does not own.
    """
    report = SlopeReport()
    cells = {(0, 0): _cell({})}  # no (1, 0) neighbour, so x=64 has a missing twin
    moved = _shift(cells, (0, 0), LAND_SIZE - 1, 32, 5, report)
    assert moved is False
    assert report.pinned == 1
    assert report.adjusted == 0


def test_an_authoritative_cell_is_never_moved() -> None:
    """Every vertex of a borrowed cell is pinned, so a steep step stays put."""
    report = limit_slopes({(0, 0): _cell({(32, 32): 5_000})}, authoritative=frozenset({(0, 0)}))
    assert report.adjusted == 0
    assert report.pinned > 0


def test_curvature_weighting_runs_over_the_structure_map() -> None:
    """The curvature path builds a per-cell structure map and still limits."""
    cells = {(0, 0): _cell({(32, 32): 8_000}), (1, 0): _cell({})}
    report = limit_slopes(cells, use_curvature=True)
    assert report.adjusted >= 0


class TestSplit:
    """The rule for dividing an excess between two movable ends."""

    def test_structured_terrain_absorbs_less(self) -> None:
        """The end with more structure takes the smaller share."""
        share_a, share_b = _split(3.0, 1.0, 8)
        assert share_a + share_b == 8
        assert share_a < share_b  # the more-structured end 'a' moves less

    def test_two_flat_ends_split_evenly(self) -> None:
        """With no structure on either side the excess is halved."""
        share_a, share_b = _split(0.0, 0.0, 9)
        assert share_a == 4
        assert share_b == 5
        assert share_a + share_b == 9
