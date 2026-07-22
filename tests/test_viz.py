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
import re
import struct

import pytest

from mlox_subset.viz import (
    build_cell_page,
    build_cell_pages,
    build_conflict_map,
    build_explorer,
    build_height_delta,
    build_pathgrid_graph,
    build_terrain_3d,
    cell_page_detail,
    cells_with_conflicts,
    collect_detail,
    detail_cells,
)
from mlox_subset.viz.geometry import Cell, bounds, group_by_cell, is_interior, parse_grid
from mlox_subset.viz.heightdelta import HeightDeltaError
from mlox_subset.viz.html import escape, table
from mlox_subset.viz.palette import divergence, legend_stops, saturation_point, severity
from mlox_subset.viz.sidecar import cell_filename, neighbours, write_sidecars
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


class TestDetailCollection:
    """`collect_detail` bounds what it decodes, because size is the constraint."""

    @staticmethod
    def _conflicts(n: int) -> list[dict]:
        return [
            {
                "type": "Landscape",
                "id": f"({i}, 0)",
                "plugins": ["a.esm", "b.esp"],
                "winner": "b.esp",
                "involves_subset": i < 3,
            }
            for i in range(n)
        ]

    @staticmethod
    def _fields(_conflict):
        return {
            "a.esm": {"vertex_heights.data": vhgt(), "vertex_heights.offset": 0.0},
            "b.esp": {"vertex_heights.data": vhgt({100: 5}), "vertex_heights.offset": 0.0},
        }

    def test_it_respects_the_limit(self):
        """Embedding every landscape record would exceed what a browser opens."""
        out = collect_detail(self._conflicts(50), self._fields, limit=10)
        assert len(out) == 10

    def test_your_own_mods_are_decoded_first(self):
        """When the budget is small, spend it on cells the user cares about."""
        out = collect_detail(self._conflicts(50), self._fields, limit=3)
        assert set(out) == {"0,0", "1,0", "2,0"}

    def test_a_failing_lookup_loses_one_cell_not_the_page(self):
        calls = {"n": 0}

        def flaky(conflict):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("tes3conv fell over")
            return self._fields(conflict)

        out = collect_detail(self._conflicts(4), flaky, limit=10)
        assert len(out) == 3

    def test_cells_with_no_decodable_payload_are_omitted(self):
        out = collect_detail(self._conflicts(2), lambda _c: {"a.esm": {"name": "x"}}, limit=10)
        assert out == {}

    def test_detail_cells_round_trips_the_keys(self):
        out = collect_detail(self._conflicts(3), self._fields, limit=3)
        assert detail_cells(out) == {Cell(0, 0), Cell(1, 0), Cell(2, 0)}

    def test_a_junk_key_does_not_break_the_round_trip(self):
        assert detail_cells({"not-a-cell": {}, "4,5": {}}) == {Cell(4, 5)}


class TestExplorer:
    DETAIL = {
        "43,-45": {
            "land": {
                "a.esm": {"heights": [0] * 4225, "min": 0, "max": 0},
                "mine.esp": {"heights": [9] * 4225, "min": 9, "max": 9},
            },
            "pgrd": {},
            "plugins": ["a.esm", "mine.esp"],
        }
    }

    def _page(self):
        return build_explorer(CONFLICTS, detail=self.DETAIL)

    def test_it_is_self_contained(self):
        """No CDN. The SVG xmlns is a URI but is not a fetch, so it is exempt."""
        page = self._page().replace('xmlns="http://www.w3.org/2000/svg"', "")
        assert "http://" not in page and "https://" not in page
        assert "<canvas" in page and "<svg" in page

    def test_it_has_every_mode(self):
        page = self._page()
        for label in ("Conflict map", "Exterior list", "Interior list", "Cell detail"):
            assert label in page
        for sub in ("Terrain surface", "Terrain difference", "Nav grid"):
            assert sub in page

    def test_the_map_is_scrollable_like_the_cell_map(self):
        assert "mapwrap" in self._page()

    def test_it_offers_a_mod_focus_dropdown(self):
        page = self._page()
        assert 'id="focus"' in page and "vizFocus" in page
        assert "mine.esp" in page

    def test_tooltips_are_delegated_not_per_element(self):
        """Per-rect listeners are what make a big map's tooltip feel laggy."""
        page = self._page()
        assert 'document.addEventListener("mouseover"' in page
        assert "addEventListener" not in page.split("<svg")[1].split("</svg>")[0]

    def test_cells_with_detail_are_marked_and_clickable(self):
        page = self._page()
        assert "hasdetail" in page
        assert "vizSelect('43,-45',1)" in page

    def test_cells_without_detail_still_appear(self):
        """A bounded detail budget must not silently drop cells from the map."""
        page = build_explorer(CONFLICTS, detail={})
        assert "vizSelect('43,-45',1)" in page
        # The class is always defined in the stylesheet; what matters is that
        # no rect carries it when there is no detail to open.
        assert 'class="cell mine hasdetail"' not in page
        assert 'class="cell hasdetail"' not in page

    def test_list_rows_select_the_same_cell_as_the_map(self):
        """Both directions must reach the local view (map click and list click)."""
        assert "vizSelect('43,-45',0)" in self._page()

    def test_interiors_are_listed_rather_than_dropped(self):
        """They cannot be mapped, but they still conflict."""
        page = build_explorer(
            [
                {
                    "type": "Npc",
                    "id": "Balmora, Guild of Fighters",
                    "plugins": ["a.esp", "b.esp"],
                    "winner": "b.esp",
                    "involves_subset": False,
                }
            ]
        )
        assert "Balmora, Guild of Fighters" in page

    def test_it_links_back_to_the_cell_map(self):
        assert "cell_map.html" in self._page()

    def test_client_strings_are_marked_for_translation(self):
        """A string living only in the JS constant could never be translated."""
        page = self._page()
        assert "labels" in page
        assert "Drag to rotate" in page

    def test_a_hostile_plugin_name_is_escaped_everywhere(self):
        hostile = [
            {
                "type": "Landscape",
                "id": "(1, 1)",
                "plugins": ["<script>alert(1)</script>.esp"],
                "winner": "<script>alert(1)</script>.esp",
                "involves_subset": False,
            }
        ]
        page = build_explorer(hostile)
        assert "<script>alert(1)</script>" not in page


class TestSidecarKeepsThePageSmall:
    """The page must stay openable however many cells have detail.

    Embedding full-resolution terrain for every detailed cell produced roughly
    25 MB of JSON in one document. That froze the app while it was assembled
    and would not have opened afterwards -- the defect this split exists to
    fix, so it is pinned rather than trusted.
    """

    @staticmethod
    def _conflicts(n: int) -> list[dict]:
        return [
            {
                "type": "Landscape",
                "id": f"({i}, 0)",
                "plugins": ["a.esm", "mine.esp"],
                "winner": "mine.esp",
                "involves_subset": True,
            }
            for i in range(n)
        ]

    @staticmethod
    def _fields(_conflict):
        return {
            "a.esm": {"vertex_heights.data": vhgt(), "vertex_heights.offset": 0.0},
            "mine.esp": {"vertex_heights.data": vhgt({100: 7}), "vertex_heights.offset": 0.0},
        }

    def test_the_overview_is_sampled_not_full_resolution(self):
        out = collect_detail(self._conflicts(1), self._fields)
        grid = out["0,0"]["land"]["a.esm"]
        # 9x9: one value per pixel of the 11px cell the world map draws.
        assert grid["side"] == 9
        assert len(grid["heights"]) == 9 * 9

    def test_full_resolution_is_available_on_request(self):
        out = collect_detail(self._conflicts(1), self._fields, stride=1)
        grid = out["0,0"]["land"]["a.esm"]
        assert grid["side"] == 65
        assert len(grid["heights"]) == 65 * 65

    def test_sampling_does_not_distort_the_reported_height_range(self):
        """The range is what the user reads, so it comes from the full grid."""
        sampled = collect_detail(self._conflicts(1), self._fields)["0,0"]["land"]["mine.esp"]
        full = collect_detail(self._conflicts(1), self._fields, stride=1)["0,0"]["land"]["mine.esp"]
        assert sampled["min"] == full["min"]
        assert sampled["max"] == full["max"]

    def test_sixty_detailed_cells_still_produce_a_small_page(self):
        """The regression guard: this is the case that hung the app."""
        conflicts = self._conflicts(60)
        detail = collect_detail(conflicts, self._fields)
        page = build_explorer(conflicts, detail=detail, data_dir="x_data", embed_detail=False)
        assert len(page) < 250_000, f"page grew to {len(page)} bytes"

    def test_the_sidecar_page_stays_small_while_embedding_blows_up(self):
        """The property that actually fixes it, in absolute terms.

        Sixty two-plugin cells at full resolution embed ~2.6 MB of heights into
        one document -- the case that froze the app. With the data in sidecars
        (and, since the externalise pass, the scripts in shared assets too) the
        same page is a small shell of map rects and list rows. Stated as byte
        bounds rather than a growth ratio: externalising the scripts shrank the
        base page, which would inflate any ratio while being exactly the win.
        """
        conflicts = self._conflicts(60)
        as_sidecar = build_explorer(
            conflicts,
            detail=collect_detail(conflicts, self._fields),
            data_dir="d",
            embed_detail=False,
        )
        embedded = build_explorer(
            conflicts, detail=collect_detail(conflicts, self._fields, limit=60, stride=1)
        )
        assert len(as_sidecar) < 120_000, f"sidecar page grew to {len(as_sidecar)} bytes"
        assert len(embedded) > 2_000_000, "the failing case stopped failing; re-check the split"

    def test_embedding_full_resolution_is_what_blew_up(self):
        """Kept as a measurement so the reason for the sidecar stays visible."""
        conflicts = self._conflicts(60)
        full = collect_detail(conflicts, self._fields, limit=60, stride=1)
        assert len(build_explorer(conflicts, detail=full)) > 2_000_000
        assert (
            len(build_explorer(conflicts, detail=full, data_dir="x", embed_detail=False)) < 100_000
        )

    def test_sidecars_are_scripts_because_fetch_cannot_read_file_urls(self, tmp_path):
        page = tmp_path / "explorer.html"
        page.write_text("<html></html>", encoding="utf-8")
        folder = write_sidecars(page, {"detail": {"1,2": {}}}, {"1,2": {"land": {}}})
        overview = (folder / "overview.js").read_text(encoding="utf-8")
        assert overview.startswith("window.__vizOverview=")
        cell = (folder / "cells" / "1_2.js").read_text(encoding="utf-8")
        assert cell.startswith('window.__vizCellLoaded("1,2"')

    def test_negative_coordinates_get_a_filesystem_safe_name(self):
        assert cell_filename("-3,-45") == "m3_m45.js"

    def test_neighbours_are_the_eight_surrounding_cells(self):
        """Seams show at cell edges, so the local view can reach next door."""
        around = neighbours(Cell(0, 0))
        assert len(around) == 8
        assert Cell(0, 0) not in around
        assert Cell(1, 1) in around and Cell(-1, 0) in around


class TestCoveragePopulatesTheExplorer:
    """Reached from the cell map with no record scan, the page still populates.

    The cell map computes coverage, not record conflicts, so the explorer used
    to open empty beside a busy map -- which reads as broken. It now falls back
    to coverage overlap, framed as coverage rather than conflict so the two
    questions stay distinct.
    """

    @staticmethod
    def _coverage_rows() -> list[dict]:
        return [
            {
                "type": "Cell (coverage)",
                "id": f"({x}, {y})",
                "plugins": ["a.esm", "b.esp", "mine.esp"],
                "winner": "mine.esp",
                "involves_subset": True,
            }
            for x in range(4)
            for y in range(4)
        ]

    def test_coverage_rows_populate_the_map_and_lists(self):
        page = build_explorer(self._coverage_rows(), coverage_only=True)
        assert page.count('onclick="vizSelect') >= 16
        assert "Exterior list" in page

    def test_it_says_plainly_that_this_is_coverage_not_conflict(self):
        """The distinction the two maps exist to keep must not be blurred."""
        page = build_explorer(self._coverage_rows(), coverage_only=True)
        assert "coverage" in page.lower()
        assert "Check Conflicts" in page

    def test_a_record_scan_shows_no_coverage_banner(self):
        page = build_explorer(self._coverage_rows(), coverage_only=False)
        assert 'class="banner"' not in page

    def test_coverage_heat_follows_mod_count_not_row_count(self):
        """One row per cell would make every cell equally hot -- a wall of red.

        Coverage heat must track how many mods touch a cell, like the cell map,
        so a lightly-touched cell is not the same colour as a contested one.
        """
        light = {
            "type": "Cell (coverage)",
            "id": "(0, 0)",
            "plugins": ["a.esm", "b.esp"],
            "winner": "b.esp",
            "involves_subset": False,
        }
        heavy = {
            "type": "Cell (coverage)",
            "id": "(1, 0)",
            "plugins": ["a.esm", "b.esp", "c.esp", "d.esp", "e.esp", "f.esp"],
            "winner": "f.esp",
            "involves_subset": False,
        }
        page = build_explorer([light, heavy], coverage_only=True)
        fills = {
            m.group(2): m.group(1)
            for m in re.finditer(r'fill="(#[0-9a-f]{6})"[^>]*?vizSelect\(.(\d+,\d+)', page)
        }
        assert fills["0,0"] != fills["1,0"], "light and heavy cells rendered the same colour"


class TestCellPages:
    """Full-resolution standalone single-cell pages, with neighbour seams."""

    @staticmethod
    def _grid(value: float) -> dict:
        return {"heights": [value] * (65 * 65), "min": value, "max": value, "side": 65}

    def _detail(self) -> dict:
        return {
            "0,0": {
                "land": {"a.esm": self._grid(0), "mine.esp": self._grid(0)},
                "pgrd": {},
                "plugins": ["a.esm", "mine.esp"],
            },
            "1,0": {  # east neighbour, sitting 500 higher -> a seam
                "land": {"a.esm": self._grid(500)},
                "pgrd": {},
                "plugins": ["a.esm"],
            },
        }

    def test_seams_are_attached_from_neighbouring_detailed_cells(self):
        withseams = cell_page_detail(self._detail())
        # (0,0) sees its east neighbour, and (1,0) sees it back to the west --
        # the relationship is symmetric.
        assert {(s["dx"], s["dy"]) for s in withseams["0,0"]["seams"]} == {(1, 0)}
        assert {(s["dx"], s["dy"]) for s in withseams["1,0"]["seams"]} == {(-1, 0)}

    def test_a_cell_with_no_neighbours_in_the_set_has_no_seams(self):
        lonely = {"5,5": {"land": {"a": self._grid(0)}, "pgrd": {}, "plugins": ["a"]}}
        assert cell_page_detail(lonely)["5,5"]["seams"] == []

    def test_a_page_is_self_contained_and_full_resolution(self):
        withseams = cell_page_detail(self._detail())
        page = build_cell_page("(0, 0)", withseams["0,0"])
        assert "http://" not in page and "https://" not in page
        assert "window.VizDraw" in page  # shares the one drawing implementation
        assert "<canvas" in page
        # Full resolution: the embedded grid is 65x65, not sampled.
        assert '"side":65' in page.replace(" ", "")

    def test_one_page_is_built_per_detailed_cell(self):
        pages = build_cell_pages(self._detail())
        assert set(pages) == {"0,0", "1,0"}
        assert all("<canvas" in html for html in pages.values())

    def test_a_plugin_name_cannot_break_out_of_the_inline_json(self):
        """A filename is attacker-controlled; embedded JSON must stay inert.

        json.dumps does not escape ``<``, so a plugin literally named
        ``</script>...`` would close the inline script and inject markup.
        script_json escapes it; this is the regression guard for that hole.
        """
        evil = "</script><script>alert(1)</script>"
        detail = {"0,0": {"land": {evil: self._grid(0)}, "pgrd": {}, "plugins": [evil]}}
        page = build_cell_page("(0, 0)", cell_page_detail(detail)["0,0"])
        assert "</script><script>alert(1)" not in page
        assert "\\u003c/script\\u003e" in page


class TestDetailCache:
    """The mtime cache: re-decode only when a plugin actually changed."""

    def _fields(self, _conflict):
        return {"a.esm": {"vertex_heights.data": vhgt(), "vertex_heights.offset": 0.0}}

    def _conflicts(self):
        return [
            {
                "type": "Landscape",
                "id": "(0, 0)",
                "plugins": ["a.esm"],
                "winner": "a.esm",
                "involves_subset": True,
            }
        ]

    def test_a_matching_signature_skips_the_decode(self):
        """The whole point: an unchanged cell is served from cache, not re-decoded."""
        from mlox_subset.viz.cache import DetailCache

        calls = {"n": 0}

        def counting_fields(conflict):
            calls["n"] += 1
            return self._fields(conflict)

        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            cache = DetailCache(folder)
            sig = lambda _c: "sig-1"  # noqa: E731 - terse fake signature
            first = collect_detail(
                self._conflicts(), counting_fields, cache=cache, signature_for=sig
            )
            second = collect_detail(
                self._conflicts(), counting_fields, cache=cache, signature_for=sig
            )
        assert first == second
        assert calls["n"] == 1, "the second pass re-decoded despite an unchanged signature"

    def test_a_changed_signature_forces_a_redecode(self):
        from mlox_subset.viz.cache import DetailCache

        calls = {"n": 0}

        def counting_fields(conflict):
            calls["n"] += 1
            return self._fields(conflict)

        import tempfile

        sigs = iter(["sig-1", "sig-2"])
        with tempfile.TemporaryDirectory() as folder:
            cache = DetailCache(folder)
            for _ in range(2):
                current = next(sigs)
                collect_detail(
                    self._conflicts(),
                    counting_fields,
                    cache=cache,
                    signature_for=lambda _c, s=current: s,
                )
        assert calls["n"] == 2, "a changed plugin signature did not invalidate the cache"

    def test_overview_and_full_resolution_do_not_share_a_cache_entry(self):
        """Sampled and full decode the same cell differently; keys must differ."""
        import tempfile

        from mlox_subset.viz.cache import DetailCache

        with tempfile.TemporaryDirectory() as folder:
            cache = DetailCache(folder)
            sig = lambda _c: "sig"  # noqa: E731
            sampled = collect_detail(
                self._conflicts(), self._fields, cache=cache, signature_for=sig
            )
            full = collect_detail(
                self._conflicts(), self._fields, stride=1, cache=cache, signature_for=sig
            )
        assert sampled["0,0"]["land"]["a.esm"]["side"] == 9
        assert full["0,0"]["land"]["a.esm"]["side"] == 65

    def test_the_signature_changes_with_mtime(self, tmp_path):
        from mlox_subset.viz.cache import plugin_signature

        plugin = tmp_path / "a.esm"
        plugin.write_bytes(b"one")
        paths = {"a.esm": str(plugin)}
        before = plugin_signature(["a.esm"], paths)
        import os

        os.utime(plugin, (1_000_000, 1_000_000))
        after = plugin_signature(["a.esm"], paths)
        assert before != after
        assert plugin_signature(["a.esm"], {}) != before  # missing path is its own signature


class TestExternalAssets:
    """JS/CSS as static files, not one inline blob.

    The inline pages were undebuggable -- a browser saw one anonymous script of
    thousands of lines, with no file to breakpoint and no way to edit but a
    Python string literal. With a data folder the shared assets are written as
    files and referenced with script/link tags, which work from file://.
    """

    def test_the_explorer_references_assets_instead_of_inlining(self):
        page = build_explorer(
            [
                {
                    "type": "Landscape",
                    "id": "(0, 0)",
                    "plugins": ["a"],
                    "winner": "a",
                    "involves_subset": False,
                }
            ],
            data_dir="d",
            embed_detail=False,
        )
        assert '<script src="d/assets/draw.js">' in page
        assert '<link rel="stylesheet" href="d/assets/explorer.css">' in page
        # The big constants must NOT be inlined in external mode.
        assert "function surface(" not in page

    def test_without_a_data_folder_it_still_inlines_and_stands_alone(self):
        """Tests and one-offs get a self-contained page."""
        page = build_explorer(
            [
                {
                    "type": "Landscape",
                    "id": "(0, 0)",
                    "plugins": ["a"],
                    "winner": "a",
                    "involves_subset": False,
                }
            ]
        )
        assert "function surface(" in page
        assert "assets/draw.js" not in page

    def test_cell_pages_reference_shared_assets_one_level_up(self):
        detail = {
            "0,0": {
                "land": {"a": {"heights": [0] * 4225, "min": 0, "max": 0, "side": 65}},
                "pgrd": {},
                "plugins": ["a"],
            }
        }
        pages = build_cell_pages(detail)
        html = pages["0,0"]
        assert "../assets/draw.js" in html
        assert "../assets/cellpage.css" in html

    def test_write_assets_produces_valid_shared_files(self, tmp_path):
        from mlox_subset.viz import write_assets

        folder = write_assets(tmp_path / "d")
        names = {p.name for p in folder.glob("*")}
        assert names == {"draw.js", "explorer.js", "explorer.css", "cellpage.js", "cellpage.css"}
        # draw.js must define the shared drawing API the pages depend on.
        assert "window.VizDraw" in (folder / "draw.js").read_text(encoding="utf-8")

    def test_the_world_terrain_toggle_is_held_back(self):
        """Shipping without the 3D world map; its toggle must be gone."""
        page = build_explorer(
            [
                {
                    "type": "Landscape",
                    "id": "(0, 0)",
                    "plugins": ["a"],
                    "winner": "a",
                    "involves_subset": False,
                }
            ],
            data_dir="d",
            embed_detail=False,
        )
        assert "World terrain" not in page
        assert "vizWorldMode" not in page


class TestTwoPhaseSidecarWrites:
    """The map renders from a cheap overview; cell pages fill in behind.

    This is the fix for the freeze: the heavy full-resolution decode moved off
    the path to seeing the map, into a background pass. That pass must add
    per-cell files and pages WITHOUT clobbering the overview the first pass
    wrote, and neither pass may decode the (held-back) world terrain.
    """

    def test_a_background_pass_does_not_clobber_the_overview(self, tmp_path):
        page = tmp_path / "explorer.html"
        page.write_text("<html></html>", encoding="utf-8")
        # Phase 1: the map's overview.
        folder = write_sidecars(page, {"detail": {"1,2": {"land": {}}}})
        overview_before = (folder / "overview.js").read_text(encoding="utf-8")
        # Phase 2 (background): per-cell + pages, no overview argument.
        write_sidecars(
            page, per_cell={"1,2": {"land": {}}}, cell_pages={"1,2": "<html>cell</html>"}
        )
        assert (folder / "overview.js").read_text(encoding="utf-8") == overview_before
        assert (folder / "cells" / "1_2.js").exists()
        assert (folder / "pages" / "1_2.html").exists()

    def test_world_js_is_not_written_unless_asked(self, tmp_path):
        """The 3D world terrain is held back; its file must not be generated."""
        page = tmp_path / "explorer.html"
        page.write_text("<html></html>", encoding="utf-8")
        folder = write_sidecars(page, {"detail": {}})
        assert not (folder / "world.js").exists()

    def test_the_explorer_does_not_reference_world_js(self):
        """A held-back file must not be requested, or it only 404s."""
        page = build_explorer(
            [
                {
                    "type": "Landscape",
                    "id": "(0, 0)",
                    "plugins": ["a"],
                    "winner": "a",
                    "involves_subset": False,
                }
            ],
            data_dir="d",
            embed_detail=False,
        )
        assert "world.js" not in page
