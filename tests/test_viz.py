"""Tests for the conflict visualisations.

Fixtures are synthetic, deliberately, for the same reason
``tests/test_tes3fields.py`` uses synthetic ones: the expected answer is exact
by construction, and no third-party mod data is committed to this repository.

The emphasis here is on the things a screenshot would not catch -- that the
severity ramp is monotonic, that a length-prefixed path grid is not
mis-attributed, that untrusted plugin names cannot inject markup, and that the
pages stay self-contained.
"""

from __future__ import annotations

import base64
import struct

import pytest

from mlox_subset.viz import (
    build_conflict_map,
    build_height_delta,
    build_pathgrid_graph,
    build_terrain_3d,
    cells_with_conflicts,
)
from mlox_subset.viz.geometry import Cell, bounds, group_by_cell, is_interior, parse_grid
from mlox_subset.viz.heightdelta import HeightDeltaError
from mlox_subset.viz.html import escape, table
from mlox_subset.viz.palette import divergence, legend_stops, saturation_point, severity
from mlox_subset.viz.terrain3d import Terrain3DError


def vhgt(bumps: dict[int, int] | None = None) -> str:
    """Build a VHGT payload with specific delta bytes set.

    Args:
        bumps: Flat vertex index to signed delta.

    Returns:
        The base64 field value as tes3conv would write it.
    """
    deltas = [0] * (65 * 65)
    for index, value in (bumps or {}).items():
        deltas[index] = value
    return base64.b64encode(struct.pack("<4225b", *deltas)).decode()


def pgrd(edges: list[int], *, prefixed: bool = True) -> str:
    """Build a PGRC connections payload.

    Args:
        edges: Flat edge targets.
        prefixed: Whether to add tes3conv's uint32 length prefix.

    Returns:
        The base64 field value.
    """
    body = struct.pack(f"<{len(edges)}I", *edges)
    if prefixed:
        body = struct.pack("<I", len(edges)) + body
    return base64.b64encode(body).decode()


CONFLICTS = [
    {
        "type": "Landscape",
        "id": "(43, -45)",
        "plugins": ["a.esp", "b.esp"],
        "winner": "b.esp",
        "involves_subset": True,
    },
    {
        "type": "Landscape",
        "id": "(43, -45)",
        "plugins": ["a.esp", "b.esp"],
        "winner": "b.esp",
        "involves_subset": False,
    },
    {
        "type": "PathGrid",
        "id": "Balmora (-3, -2)",
        "plugins": ["a.esp", "c.esp"],
        "winner": "c.esp",
        "involves_subset": False,
    },
    {
        "type": "Npc",
        "id": "fargoth",
        "plugins": ["a.esp", "b.esp"],
        "winner": "b.esp",
        "involves_subset": False,
    },
]


class TestGeometry:
    def test_bare_grid_id_parses(self):
        assert parse_grid("(43, -45)") == Cell(43, -45)

    def test_cell_scoped_id_parses(self):
        assert parse_grid("Balmora (-3, -2)") == Cell(-3, -2)

    def test_named_record_has_no_grid(self):
        assert parse_grid("fargoth") is None

    def test_a_name_containing_parentheses_does_not_mislead(self):
        """Only a trailing coordinate pair counts, not one buried in a name."""
        assert parse_grid("Some Mod (v1.2) chest") is None

    def test_absurd_coordinates_are_rejected(self):
        """A garbage grid field must not stretch the map across the universe."""
        assert parse_grid("(999999, 3)") is None

    def test_interiors_are_identified_by_having_no_coordinates(self):
        assert is_interior("Balmora, Guild of Fighters")
        assert not is_interior("(43, -45)")
        assert not is_interior("")

    def test_grouping_counts_and_attributes(self):
        grouped = group_by_cell(CONFLICTS)
        assert set(grouped) == {Cell(43, -45), Cell(-3, -2)}
        landscape = grouped[Cell(43, -45)]
        assert landscape.total == 2
        assert landscape.mine == 1
        assert landscape.types == {"Landscape": 2}
        assert landscape.winners == {"b.esp": 2}

    def test_grouping_skips_non_spatial_records(self):
        """An NPC is a conflict but not a place; it belongs in the list view."""
        assert sum(c.total for c in group_by_cell(CONFLICTS).values()) == 3

    def test_bounds_of_nothing_is_none(self):
        assert bounds([]) is None


class TestPalette:
    def test_severity_is_monotonic(self):
        """More conflicts must never render as a cooler colour."""
        seen = [severity(n, 50) for n in range(1, 51)]
        reds = [int(c[1:3], 16) for c in seen]
        assert reds == sorted(reds)

    def test_severity_of_nothing_is_neutral(self):
        assert severity(0, 10) == severity(5, 0)

    def test_divergence_is_signed(self):
        """Raised and lowered must be visually opposite, not just different."""
        up, down = divergence(100, 100), divergence(-100, 100)
        assert int(up[1:3], 16) > int(up[5:7], 16)
        assert int(down[5:7], 16) > int(down[1:3], 16)

    def test_divergence_clamps_rather_than_wraps(self):
        """One extreme vertex must not wrap the ramp and read as its opposite."""
        assert divergence(10_000, 100) == divergence(100, 100)

    def test_legend_stops_ascend(self):
        counts = [count for count, _colour in legend_stops(30)]
        assert counts == sorted(counts)
        assert not legend_stops(0)

    def test_an_ordinary_cell_stays_green_beside_a_hot_one(self):
        """The defect that rendering the map exposed.

        With a square-root ramp, 3 conflicts against a worst of 30 came out
        yellow, so a busy load order made the entire map look urgent and
        nothing stood out. Green here means "green is still reachable".
        """
        colour = severity(3, 30)
        assert int(colour[3:5], 16) > int(colour[1:3], 16)

    def test_saturation_ignores_a_single_pathological_cell(self):
        """One 400-conflict cell must not rescale the other ninety-nine."""
        counts = [*([2] * 99), 400]
        assert saturation_point(counts) < 100

    def test_saturation_of_nothing_is_zero(self):
        assert saturation_point([]) == 0


class TestHtmlEscaping:
    def test_plugin_names_cannot_inject_markup(self):
        """Plugin filenames come from disk and are not trusted."""
        assert "<script>" not in escape("<script>alert(1)</script>")

    def test_a_hostile_plugin_name_reaches_the_page_escaped(self):
        hostile = dict(CONFLICTS[0])
        hostile["winner"] = "<script>alert(1)</script>.esp"
        hostile["plugins"] = [hostile["winner"]]
        page = build_conflict_map([hostile])
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page

    def test_empty_table_says_so(self):
        assert "Nothing to show" in table(["a"], [])


class TestConflictMap:
    def test_page_is_self_contained(self):
        """No CDN: the tool runs offline and ships as a frozen binary."""
        page = build_conflict_map(CONFLICTS)
        assert "http://" not in page
        assert "https://" not in page
        assert "<svg" in page

    def test_one_rect_per_conflicted_cell(self):
        """Sparse rendering: a dense world grid would be unopenable."""
        assert build_conflict_map(CONFLICTS).count("<rect") == 2

    def test_cells_of_your_own_mods_are_marked(self):
        assert 'class="mine"' in build_conflict_map(CONFLICTS)

    def test_non_spatial_conflicts_are_reported_not_dropped(self):
        """The NPC conflict is real; the map just cannot place it."""
        assert "Non-spatial" in build_conflict_map(CONFLICTS)

    def test_empty_input_renders_a_page_rather_than_failing(self):
        assert "<html" in build_conflict_map([])

    def test_it_says_what_kind_of_record_is_being_edited(self):
        """A count alone cannot distinguish reshaped terrain from a moved barrel."""
        page = build_conflict_map(CONFLICTS)
        assert "What is being edited" in page
        assert "terrain shape" in page
        assert "strand NPCs" in page

    def test_it_links_to_the_cell_map_without_altering_it(self):
        """The coverage map is a parallel view, deliberately left untouched."""
        assert "cell_map.html" in build_conflict_map(CONFLICTS)
        assert "cell_map.html" not in build_conflict_map(CONFLICTS, cell_map_href="")

    def test_cross_link_set_matches_the_map(self):
        assert cells_with_conflicts(CONFLICTS) == {(43, -45), (-3, -2)}


class TestHeightDelta:
    def test_one_changed_delta_byte_moves_a_whole_row_tail(self):
        """The entire reason this view exists.

        VHGT is doubly cumulative, so bumping the delta at row 10 column 20
        raises columns 20..64 of that row -- 45 vertices. The raw fields differ
        in every byte from there on, which is why comparing them is misleading.
        """
        page = build_height_delta(
            vhgt({65 * 10 + 20: 9}),
            vhgt(),
            winner_name="mine.esp",
            loser_name="theirs.esp",
        )
        assert "45 of 4225 vertices differ" in page

    def test_identical_terrain_is_stated_plainly(self):
        page = build_height_delta(vhgt(), vhgt(), winner_name="a.esp", loser_name="b.esp")
        assert "terrain is identical" in page

    def test_offsets_shift_both_grids_equally_so_the_diff_is_unchanged(self):
        """The offset is the cell's base height; it cannot create a difference."""
        same = build_height_delta(
            vhgt({100: 5}), vhgt(), winner_name="a", loser_name="b", winner_offset=0, loser_offset=0
        )
        shifted = build_height_delta(
            vhgt({100: 5}),
            vhgt(),
            winner_name="a",
            loser_name="b",
            winner_offset=500,
            loser_offset=500,
        )
        assert same.count("<rect") == shifted.count("<rect")

    def test_undecodable_field_raises_rather_than_rendering_a_lie(self):
        with pytest.raises(HeightDeltaError):
            build_height_delta("not base64 at all!!", vhgt(), winner_name="a", loser_name="b")


class TestPathGrid:
    POINTS = [
        {"location": [0, 0, 0], "connection_count": 2},
        {"location": [100, 0, 0], "connection_count": 2},
        {"location": [100, 100, 0], "connection_count": 2},
    ]

    def test_triangle_has_three_undirected_edges(self):
        """Each connection is stored from both ends; it must count once."""
        page = build_pathgrid_graph(pgrd([1, 2, 0, 2, 0, 1]), self.POINTS, winner_name="a.esp")
        assert page.count("<line") == 3

    def test_removed_edges_are_coloured_distinctly(self):
        page = build_pathgrid_graph(
            pgrd([1, 2, 0, 2, 0, 1]),
            self.POINTS,
            winner_name="mine.esp",
            loser_value=pgrd([1, 1, 0, 0, 0, 0]),
            loser_points=self.POINTS,
            loser_name="theirs.esp",
        )
        assert "#5cc45c" in page or "#e05561" in page

    def test_a_grid_that_only_loses_edges_is_called_out(self):
        """The signature of an accidentally rebuilt path grid.

        The winner keeps only 0-1, dropping 1-2 and 0-2 and adding nothing --
        so its connection counts are 1, 1, 0 rather than the triangle's 2s.
        """
        thinned = [
            {"location": [0, 0, 0], "connection_count": 1},
            {"location": [100, 0, 0], "connection_count": 1},
            {"location": [100, 100, 0], "connection_count": 0},
        ]
        page = build_pathgrid_graph(
            pgrd([1, 0]),
            thinned,
            winner_name="mine.esp",
            loser_value=pgrd([1, 2, 0, 2, 0, 1]),
            loser_points=self.POINTS,
            loser_name="theirs.esp",
        )
        assert "only removes connections" in page

    def test_unprefixed_grid_decodes_too(self):
        """Raw plugin subrecords carry no length prefix; tes3conv's do."""
        page = build_pathgrid_graph(
            pgrd([1, 2, 0, 2, 0, 1], prefixed=False), self.POINTS, winner_name="a.esp"
        )
        assert page.count("<line") == 3

    def test_an_unreadable_loser_still_draws_the_winner(self):
        """A broken overridden record is no reason to show nothing."""
        page = build_pathgrid_graph(
            pgrd([1, 2, 0, 2, 0, 1]),
            self.POINTS,
            winner_name="mine.esp",
            loser_value="!!not base64!!",
            loser_points=self.POINTS,
            loser_name="theirs.esp",
        )
        assert page.count("<line") == 3

    def test_edge_naming_a_missing_point_is_skipped_not_fatal(self):
        page = build_pathgrid_graph(pgrd([99, 0, 0, 0, 0, 0]), self.POINTS, winner_name="a.esp")
        assert "<html" in page


class TestTerrain3D:
    def test_surface_is_self_contained_and_has_no_library(self):
        page = build_terrain_3d({"a.esp": (vhgt(), 0.0)})
        assert "<canvas" in page
        assert "http://" not in page and "https://" not in page
        assert "three" not in page.lower().split("<script>")[-1][:400]

    def test_multiple_plugins_become_switchable(self):
        page = build_terrain_3d({"a.esp": (vhgt(), 0.0), "b.esp": (vhgt({50: 7}), 0.0)})
        # The attribute also appears once in the script's querySelectorAll, so
        # count the buttons by their value rather than the bare name.
        assert page.count('data-surface="') == 2

    def test_one_bad_record_does_not_lose_the_good_one(self):
        page = build_terrain_3d({"good.esp": (vhgt(), 0.0), "bad.esp": ("!!nope!!", 0.0)})
        assert "data-surface" in page
        assert "could not be decoded" in page

    def test_no_decodable_surface_raises(self):
        with pytest.raises(Terrain3DError):
            build_terrain_3d({"bad.esp": ("!!nope!!", 0.0)})
