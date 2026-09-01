"""Edge branches of the seam-repair helpers.

The end-to-end repair is covered in the fidelity suite; these pin the small
guards its fully-populated cells never hit: the empty-average error, the size
check, and each early exit in the feathering pass.
"""

from __future__ import annotations

from array import array

import pytest

from wraithguard.land.seams import (
    SeamReport,
    feather_corrections,
    mask_normals_to_moved_heights,
    mean,
    repair_edges,
    repair_seams,
)
from wraithguard.tes3fields.landscape import LAND_SIZE

V = LAND_SIZE * LAND_SIZE


def _flat() -> array:
    return array("i", [0] * V)


class TestMean:
    def test_averaging_no_values_is_an_error(self) -> None:
        """There is no meaningful average of nothing, so it is refused."""
        with pytest.raises(ValueError, match="cannot average no values"):
            mean([])

    def test_an_average_rounds_to_an_integer(self) -> None:
        """Heights are integers, so the mean is too."""
        assert mean([1, 2, 3]) == 2


def test_repair_seams_refuses_a_wrongly_sized_cell() -> None:
    """A grid that is not exactly 65x65 is a programming error, not terrain."""
    with pytest.raises(ValueError, match="expected"):
        repair_seams({(0, 0): array("i", [0] * 10)})


class TestFeatherCorrections:
    """Spreading a boundary move inward, and the cases where it does nothing."""

    def test_a_depth_below_two_is_a_no_op(self) -> None:
        """Feathering needs at least two vertices to spread across."""
        report = SeamReport()
        cells = {(0, 0): _flat()}
        feather_corrections(cells, {(0, 0): {0: 100}}, depth=1, report=report)
        assert report.feathered_vertices == 0
        assert all(v == 0 for v in cells[(0, 0)])

    def test_a_move_in_an_absent_cell_is_skipped(self) -> None:
        """A correction for a cell not present is ignored rather than fatal."""
        report = SeamReport()
        feather_corrections({}, {(5, 5): {0: 100}}, depth=4, report=report)
        assert report.feathered_vertices == 0

    def test_a_zero_move_feathers_nothing(self) -> None:
        """A boundary vertex that did not move spreads no correction."""
        report = SeamReport()
        cells = {(0, 0): _flat()}
        feather_corrections(cells, {(0, 0): {0: 0}}, depth=4, report=report)
        assert report.feathered_vertices == 0

    def test_a_move_too_small_to_share_stops_early(self) -> None:
        """When a share rounds to zero the feathering stops rather than looping."""
        report = SeamReport()
        cells = {(0, 0): _flat()}
        # A mid-edge vertex moved by 1 over depth 2: the first inward share is
        # 1 * (2 - 1) // 2 == 0, so the inner loop breaks immediately.
        edge_offset = 32 * LAND_SIZE  # (x=0, y=32)
        feather_corrections(cells, {(0, 0): {edge_offset: 1}}, depth=2, report=report)
        assert report.feathered_vertices == 0

    def test_a_real_correction_is_spread_inward(self) -> None:
        """A large mid-edge move nudges the interior vertices along its row."""
        report = SeamReport()
        cells = {(0, 0): _flat()}
        edge_offset = 32 * LAND_SIZE  # (x=0, y=32): a left-edge, non-corner vertex
        feather_corrections(cells, {(0, 0): {edge_offset: 400}}, depth=4, report=report)
        assert report.feathered_vertices > 0
        assert cells[(0, 0)][edge_offset + 1] != 0  # the next vertex inward moved


class TestRepairEdges:
    """Corner and authoritative guards along a shared border."""

    def test_a_pinned_corner_that_still_differs_is_counted_not_raised(self) -> None:
        """A corner shared with an absent cell cannot move, so it is left alone.

        Called without ``repair_corners`` first, so the corner is still unequal;
        because it is pinned (a neighbour is missing) the mismatch is recorded
        rather than treated as the corner-order bug.
        """
        left = _flat()
        left[32 * LAND_SIZE + LAND_SIZE - 1] = 0  # keep the middle equal
        left[0 * LAND_SIZE + (LAND_SIZE - 1)] = 100  # corner (x=64, y=0) differs
        cells = {(0, 0): left, (1, 0): _flat()}  # (0,-1)/(1,-1) absent -> corner pinned
        report = SeamReport()
        repair_edges(cells, report)
        assert report.pinned_corners >= 1

    def test_two_authoritative_cells_are_never_moved(self) -> None:
        """A disagreement between two borrowed cells predates the merge; leave it."""
        left = _flat()
        left[32 * LAND_SIZE + (LAND_SIZE - 1)] = 50  # a mid-border difference
        cells = {(0, 0): left, (1, 0): _flat()}
        report = SeamReport()
        repair_edges(cells, report, authoritative=frozenset({(0, 0), (1, 0)}))
        assert left[32 * LAND_SIZE + (LAND_SIZE - 1)] == 50  # untouched

    def test_an_unpinned_corner_that_still_differs_is_the_ordering_bug(self) -> None:
        """A differing corner with every neighbour present means corners ran late.

        With all four cells present the shared corner is not pinned, so an
        unequal corner is the "corners must be repaired before edges" bug and is
        raised rather than averaged twice.
        """
        left = _flat()
        last = LAND_SIZE - 1
        left[last * LAND_SIZE + last] = 100  # corner (x=64, y=64), shared with all four
        cells = {(0, 0): left, (1, 0): _flat(), (0, 1): _flat(), (1, 1): _flat()}
        with pytest.raises(ValueError, match="Corners must be repaired"):
            repair_edges(cells, SeamReport())


class TestMaskNormals:
    """Keep recomputed normals only where the height moved."""

    def test_moved_and_out_of_range_keep_the_recomputed_normal(self) -> None:
        """A moved vertex, and any vertex beyond the original grid, use the new value."""
        recomputed = [[(1, 1, 1), (2, 2, 2)], [(3, 3, 3), (4, 4, 4)]]
        original = [[(9, 9, 9)]]  # smaller than recomputed
        masked = mask_normals_to_moved_heights(recomputed, original, moved={(0, 0)})
        assert masked[0][0] == (1, 1, 1)  # (0,0) moved -> recomputed
        assert masked[0][1] == (2, 2, 2)  # x beyond original -> recomputed
        assert masked[1][0] == (3, 3, 3)  # y beyond original -> recomputed

    def test_an_unmoved_vertex_keeps_the_inherited_normal(self) -> None:
        """Where the height did not move, the original hand-authored normal stays."""
        recomputed = [[(1, 1, 1)]]
        original = [[(9, 9, 9)]]
        masked = mask_normals_to_moved_heights(recomputed, original, moved=set())
        assert masked[0][0] == (9, 9, 9)  # unmoved -> inherited


def test_repair_seams_can_skip_feathering() -> None:
    """A feather depth below two runs the repair without the feather pass."""
    report = repair_seams({(0, 0): _flat()}, feather=1)
    assert report.feathered_vertices == 0
