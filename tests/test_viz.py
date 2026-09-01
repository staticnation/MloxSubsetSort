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
import json
import re
import struct
from itertools import pairwise

import pytest

from wraithguard.tes3fields.landscape import (
    HEIGHT_SCALE,
    LAND_SIZE,
    LAND_VERTEX_SPACING,
)
from wraithguard.viz import (
    build_conflict_map,
    build_height_delta,
    build_pathgrid_graph,
    build_terrain_3d,
    cells_with_conflicts,
)
from wraithguard.viz.geometry import Cell, bounds, group_by_cell, is_interior, parse_grid
from wraithguard.viz.heightdelta import HeightDeltaError, _subtract
from wraithguard.viz.html import escape, table
from wraithguard.viz.palette import (
    NEUTRAL,
    TINT_RAMPS,
    coverage_heat,
    divergence,
    severity_banded,
    severity_legend_rows,
    tint_ramp,
)
from wraithguard.viz.pathgrid import _points_and_edges, _project
from wraithguard.viz.terrain3d import _STRIDE, Terrain3DError


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


def payload_of(page: str, global_name: str) -> dict:
    r"""Extract and decode a page's embedded ``window.__<name>`` JSON payload.

    ``html.script_json`` escapes ``<``, ``>`` and ``&`` to ``\uXXXX`` so the
    JSON is inert inside a ``<script>`` block. That makes raw substring checks
    unreliable, so tests decode the payload and assert on the data.

    Args:
        page: The rendered HTML document.
        global_name: The global without the ``window.__`` prefix.

    Returns:
        The decoded payload.
    """
    marker = f"window.__{global_name}="
    start = page.index(marker) + len(marker)
    end = page.index(";</script>", start)
    return json.loads(page[start:end])


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


def channel_distance(first: str, second: str) -> int:
    """Total per-channel difference between two ``#rrggbb`` colors.

    A crude stand-in for perceptual distance, and deliberately so: the question
    is whether two swatches read as different colors at a glance, and summed
    channel difference answers it without importing a color-science library.

    Args:
        first: A ``#rrggbb`` string.
        second: A ``#rrggbb`` string.

    Returns:
        The sum of the absolute red, green and blue differences, 0-765.
    """
    return sum(abs(int(first[i : i + 2], 16) - int(second[i : i + 2], 16)) for i in (1, 3, 5))


class TestPalette:
    def test_severity_is_monotonic(self):
        """More conflicts must never render as a cooler color."""
        seen = [severity_banded(n, 50) for n in range(1, 51)]
        reds = [int(c[1:3], 16) for c in seen]
        assert reds == sorted(reds)

    def test_severity_of_nothing_is_neutral(self):
        """A cell with no conflicts must not be colored as if it had some."""
        assert severity_banded(0, 10) == NEUTRAL

    def test_a_contradictory_worst_still_yields_a_color(self):
        """Five conflicts on a map whose worst is zero cannot both be true.

        It happens when a caller reuses a stale maximum. Matching
        :func:`coverage_heat`'s handling exactly -- the lowest band rather than
        an exception -- because the two maps sit side by side and a degenerate
        input should not make them disagree about anything.
        """
        # Both maps must answer, and neither may answer NEUTRAL -- there is a
        # count here, so "nothing to report" would be the one wrong reply.
        for got in (severity_banded(5, 0), coverage_heat(5, 0)):
            assert got.startswith("#") and len(got) == 7
            assert got != NEUTRAL

    def test_divergence_is_signed(self):
        """Raised and lowered must be visually opposite, not just different."""
        up, down = divergence(100, 100), divergence(-100, 100)
        assert int(up[1:3], 16) > int(up[5:7], 16)
        assert int(down[5:7], 16) > int(down[1:3], 16)

    def test_divergence_clamps_rather_than_wraps(self):
        """One extreme vertex must not wrap the ramp and read as its opposite."""
        assert divergence(10_000, 100) == divergence(100, 100)

    def test_the_legend_has_one_row_per_band(self):
        """The legend is the map's key, so it cannot be a sample of a ramp."""
        rows = severity_legend_rows(30)
        assert [row[0] for row in rows][:5] == ["1", "2", "3", "4", "5"]
        assert rows[5][0] == "6-10"
        assert not severity_legend_rows(0)

    def test_the_first_counts_are_told_apart(self):
        """The reason for banding: one, two and three conflicts differ.

        Measured as a color *distance*, not as inequality. A linear ramp does
        give these three different values -- 1/30, 2/30, 3/30 -- but they land
        within a few units per channel of each other, which is invisible on a
        nine-pixel square. Asserting only that they differ passes on the very
        ramp banding exists to replace; this was caught by a negative control
        doing exactly that.
        """
        low, mid, high = (severity_banded(n, 30) for n in (1, 2, 3))

        assert channel_distance(low, mid) >= 40
        assert channel_distance(mid, high) >= 40

    def test_an_ordinary_cell_stays_green_beside_a_hot_one(self):
        """The defect that rendering the map exposed.

        With a square-root ramp, 3 conflicts against a worst of 30 came out
        yellow, so a busy load order made the entire map look urgent and
        nothing stood out. Green here means "green is still reachable".
        """
        color = severity_banded(3, 30)
        assert int(color[3:5], 16) > int(color[1:3], 16)

    def test_one_pathological_cell_does_not_flatten_the_rest(self):
        """What the 95th-percentile clamp used to be for.

        Banding replaced it: a 400-conflict outlier lands in the open-ended top
        band and costs the ordinary counts nothing, so the map can now be drawn
        against the true maximum and the legend describes the real range.
        """
        assert severity_banded(2, 400) != severity_banded(3, 400)

    def test_the_top_band_is_open_ended_on_a_wide_map(self):
        """Otherwise a huge worst case would need hundreds of legend rows."""
        assert severity_legend_rows(400)[-1][0].endswith("+")


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


class TestConflictMapScale:
    """The map is banded, and banded against the *true* worst cell."""

    @staticmethod
    def busy(count: int) -> list[dict]:
        """A single cell carrying ``count`` conflicts.

        Args:
            count: How many conflicting records to put in the cell.

        Returns:
            Conflict records the map can group.
        """
        return [
            {
                "type": "Landscape",
                "id": "(1, 1)",
                "plugins": ["a.esp", "b.esp"],
                "winner": "b.esp",
                "involves_subset": False,
            }
            for _ in range(count)
        ]

    def test_the_legend_reaches_the_worst_cell(self):
        """Scaled to the true maximum, not to a percentile of it.

        Percentile clamping existed for the continuous ramp; with banding an
        outlier lands in the top band harmlessly, and clamping would instead
        make the legend describe a range the map does not have. Forty
        conflicts must produce a band that contains forty.
        """
        page = build_conflict_map(self.busy(40))

        assert "36-40" in page, "the legend stops short of the worst cell"

    def test_the_cell_itself_is_painted_the_legend_color(self):
        """Asserted on the ``<rect>`` fill, not on the page text.

        Searching the whole page finds the color in the *legend* even when the
        cell was painted something else entirely, so the map and its own key
        could disagree with the test still green -- which a negative control
        proved by rescaling only the fill.
        """
        page = build_conflict_map(self.busy(12))
        fills = re.findall(r'<rect[^>]*\bfill="(#[0-9a-f]{6})"', page)

        assert fills == [severity_banded(12, 12)]

    def test_the_first_counts_get_their_own_bands(self):
        """One conflict and two must not share a swatch in the legend."""
        page = build_conflict_map(self.busy(3))

        assert severity_banded(1, 3) in page
        assert severity_banded(2, 3) in page


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

    def test_it_stands_alone_without_linking_to_the_cell_map(self):
        """The two maps are independent views, generated by separate paths.

        The conflict map is built by the Conflicts window; the cell map answers
        coverage and is built by its own button. Neither links to the other, so
        neither can depend on the other having been generated first -- an
        ordering dependency that previously cost the cell map its button
        whenever conflict generation was slow or failed.
        """
        page = build_conflict_map(CONFLICTS)
        assert "cell_map.html" not in page

    def test_cross_link_set_matches_the_map(self):
        assert cells_with_conflicts(CONFLICTS) == {(43, -45), (-3, -2)}


class TestHeightDelta:
    """Height differences as a CHAIN of edits, not a star against the winner.

    Morrowind landscape records do not merge -- the last plugin to touch one
    replaces it wholesale. So the useful question at each step is "what did
    THIS plugin change relative to the one before it", which is what the
    surfaces mapping expresses. The Base step has nothing before it and is
    shown on an absolute elevation ramp instead.
    """

    def test_one_changed_delta_byte_moves_a_whole_row_tail(self):
        """The entire reason this view exists.

        VHGT is doubly cumulative, so bumping the delta at row 10 column 20
        raises columns 20..64 of that row -- 45 vertices. The raw fields differ
        in every byte from there on, which is why comparing them is misleading.
        The count is computed client-side now, so assert on the embedded grids
        rather than on rendered prose.
        """
        page = build_height_delta(
            {"base.esm": (vhgt(), 0.0), "mine.esp": (vhgt({65 * 10 + 20: 9}), 0.0)},
            winner_name="mine.esp",
        )
        data = payload_of(page, "heightdelta")
        assert data["chain"] == ["base.esm", "mine.esp"]
        base, mine = data["surfaces"]["base.esm"], data["surfaces"]["mine.esp"]
        # The client subtracts these; verify the 45-vertex tail is really there.
        differing = sum(
            1
            for row in range(65)
            for col in range(65)
            if abs(mine[row][col] - base[row][col]) >= data["noise_floor"]
        )
        assert differing == 45

    def test_the_chain_is_ordered_and_opens_on_the_winner(self):
        """The page opens on the winner's own step -- the live change."""
        page = build_height_delta(
            {
                "base.esm": (vhgt(), 0.0),
                "mid.esp": (vhgt({100: 3}), 0.0),
                "mine.esp": (vhgt({200: 5}), 0.0),
            },
            winner_name="mine.esp",
        )
        data = payload_of(page, "heightdelta")
        assert data["chain"] == ["base.esm", "mid.esp", "mine.esp"]
        # The page opens on the winner's own step -- the change that is live.
        assert data["default_step"] == len(data["chain"]) - 1
        assert page.count("data-step=") == 3

    def test_offsets_shift_the_whole_grid_and_not_the_diff(self):
        """The offset is the cell's base height; it cannot create a difference."""
        flat = build_height_delta({"a": (vhgt(), 0.0), "b": (vhgt({100: 5}), 0.0)}, winner_name="b")
        raised = build_height_delta(
            {"a": (vhgt(), 500.0), "b": (vhgt({100: 5}), 500.0)}, winner_name="b"
        )
        # Same number of embedded vertices either way; only the absolute values move.
        assert flat.count(",") == raised.count(",")

    def test_undecodable_winner_raises_rather_than_rendering_a_lie(self):
        with pytest.raises(HeightDeltaError):
            build_height_delta(
                {"a": (vhgt(), 0.0), "bad": ("not base64 at all!!", 0.0)}, winner_name="bad"
            )

    def test_a_winner_with_no_comparable_sibling_raises(self):
        """One surface alone has nothing to diff against."""
        with pytest.raises(HeightDeltaError):
            build_height_delta({"only.esp": (vhgt(), 0.0)}, winner_name="only.esp")

    def test_an_undecodable_sibling_is_skipped_not_fatal(self):
        """A non-winner surface that will not decode is dropped, not raised on."""
        page = build_height_delta(
            {
                "a": (vhgt(), 0.0),
                "bad": ("not base64 at all!!", 0.0),  # skipped
                "b": (vhgt({100: 5}), 0.0),
            },
            winner_name="b",
        )
        # The page still renders from the two decodable surfaces.
        assert "data-step=" in page

    def test_subtract_rejects_grids_of_different_shapes(self):
        """The vertex-by-vertex diff needs both grids the same shape."""
        with pytest.raises(HeightDeltaError):
            _subtract([[0.0, 0.0]], [[0.0]])

    def test_subtract_gives_signed_deltas(self):
        """Winner-minus-loser is positive where the winner is higher."""
        assert _subtract([[3.0, 1.0]], [[1.0, 4.0]]) == [[2.0, -3.0]]


class TestPathgridProjection:
    """The path-grid graph's coordinate projection and point/edge decode."""

    def test_project_of_no_points_is_a_trivial_extent(self):
        """With no nodes there is nothing to project, and the scale is 1:1."""
        assert _project([]) == ([], 1.0, 1.0)

    def test_points_and_edges_without_a_points_list_has_no_nodes(self):
        """Given no points, the edges still decode but carry no positioned nodes."""
        value = struct.pack("<3I", 2, 0, 1)  # length prefix 2, then edges 0 and 1
        coords, _edges = _points_and_edges(value, None)
        assert coords == []


class TestPathGrid:
    """The nav graph, also as a chain of edits over the winner's node layout."""

    POINTS = [
        {"location": [0, 0, 0], "connection_count": 2},
        {"location": [100, 0, 0], "connection_count": 2},
        {"location": [100, 100, 0], "connection_count": 2},
    ]
    THINNED = [
        {"location": [0, 0, 0], "connection_count": 1},
        {"location": [100, 0, 0], "connection_count": 1},
        {"location": [100, 100, 0], "connection_count": 0},
    ]

    def test_a_lone_grid_draws_its_nodes_with_no_chain_controls(self):
        """One plugin has nothing to diff against, so no payload is emitted.

        The page falls back to the plain server-rendered graph -- three nodes,
        no tab strip -- which is what it looked like before chaining existed.
        """
        page = build_pathgrid_graph(
            {"a.esp": (pgrd([1, 2, 0, 2, 0, 1]), self.POINTS)}, winner_name="a.esp"
        )
        assert page.count("<circle") == len(self.POINTS)
        assert "data-step=" not in page
        assert "window.__pathgrid" not in page

    def test_an_unprefixed_grid_decodes_too(self):
        """Raw plugin subrecords carry no length prefix; tes3conv's do.

        Both forms must yield the same three undirected edges, which the chain
        payload exposes once there is a second plugin to compare against.
        """
        prefixed = build_pathgrid_graph(
            {
                "base.esm": (pgrd([1, 0]), self.THINNED),
                "a.esp": (pgrd([1, 2, 0, 2, 0, 1]), self.POINTS),
            },
            winner_name="a.esp",
        )
        raw = build_pathgrid_graph(
            {
                "base.esm": (pgrd([1, 0], prefixed=False), self.THINNED),
                "a.esp": (pgrd([1, 2, 0, 2, 0, 1], prefixed=False), self.POINTS),
            },
            winner_name="a.esp",
        )
        assert payload_of(prefixed, "pathgrid")["edges"]["a.esp"] == [[0, 1], [0, 2], [1, 2]]
        assert payload_of(raw, "pathgrid")["edges"]["a.esp"] == [[0, 1], [0, 2], [1, 2]]

    def test_a_thinned_grid_is_embedded_alongside_the_fuller_one(self):
        """Dropped edges are the failure worth seeing; both steps must be present."""
        page = build_pathgrid_graph(
            {
                "base.esm": (pgrd([1, 2, 0, 2, 0, 1]), self.POINTS),
                "mine.esp": (pgrd([1, 0]), self.THINNED),
            },
            winner_name="mine.esp",
        )
        data = payload_of(page, "pathgrid")
        assert data["chain"] == ["base.esm", "mine.esp"]
        # The winner dropped two of the triangle's three edges.
        assert len(data["edges"]["mine.esp"]) < len(data["edges"]["base.esm"])

    def test_an_undecodable_sibling_still_draws_the_winner(self):
        """A broken overridden record is no reason to show nothing."""
        page = build_pathgrid_graph(
            {
                "bad.esm": ("!!not base64!!", self.POINTS),
                "mine.esp": (pgrd([1, 2, 0, 2, 0, 1]), self.POINTS),
            },
            winner_name="mine.esp",
        )
        # With the only sibling unreadable, the chain collapses to the winner
        # alone -- it still draws, which is the point.
        assert "<html" in page
        assert page.count("<circle") == len(self.POINTS)

    def test_an_edge_naming_a_missing_point_is_not_fatal(self):
        page = build_pathgrid_graph(
            {"a.esp": (pgrd([99, 0, 0, 0, 0, 0]), self.POINTS)}, winner_name="a.esp"
        )
        assert "<html" in page

    def test_a_missing_winner_key_is_a_programming_error(self):
        """Documented as raising KeyError -- the caller must pass a real winner."""
        with pytest.raises(KeyError):
            build_pathgrid_graph({"a.esp": (pgrd([1, 0]), self.POINTS)}, winner_name="absent.esp")


class TestTerrain3DIsDrawnToScale:
    """The vertical must be the same scale as the ground, not normalised.

    The reported symptom was "correct from the top, way too extreme from the
    side", which is the signature of a normalised height axis: looking straight
    down hides the vertical entirely, so only an oblique view shows it.

    The cause was ``((z-lo)/span)*110`` -- every cell drawn 110 units tall
    whatever its actual relief, on a footprint 32 units wide. A cell with 512
    world units of relief should stand 2 units tall; it stood 110. Fifty-five
    times too steep, and *worse the flatter the terrain*, which is why gentle
    hills looked like cliffs.
    """

    @staticmethod
    def payload(units_per_step: float) -> bytes:
        """A ramp rising a fixed number of world units per vertex step north.

        Args:
            units_per_step: World units of rise between adjacent vertices.

        Returns:
            A VHGT delta payload.
        """
        deltas = bytearray(LAND_SIZE * LAND_SIZE)
        for y in range(1, LAND_SIZE):
            deltas[y * LAND_SIZE] = round(units_per_step / HEIGHT_SCALE)
        return bytes(deltas)

    @staticmethod
    def payload_data(page: str) -> dict:
        """Pull the embedded terrain payload back out of a rendered page.

        Args:
            page: The rendered HTML.

        Returns:
            The decoded payload.
        """
        return json.loads(re.search(r"window\.__terrain=(\{.*?\});</script>", page, re.S).group(1))

    def test_a_45_degree_slope_is_drawn_at_45_degrees(self):
        """The whole property, stated as the one angle anybody can check.

        A ramp rising exactly one vertex-spacing per vertex step is at 45
        degrees in the world. One unit of height per unit of ground on screen
        is 45 degrees there too.
        """
        page = build_terrain_3d({"a.esp": (self.payload(LAND_VERTEX_SPACING), 0.0)})
        data = self.payload_data(page)
        grid = data["surfaces"][0]["grid"]

        rise = grid[1][0] - grid[0][0]
        assert rise / data["units_per_step"] == pytest.approx(1.0)

    def test_the_sampling_stride_is_accounted_for(self):
        """The grid is drawn at every other vertex, so the step is twice as wide.

        Dividing by the unsampled spacing would exaggerate by exactly the
        stride -- a subtler version of the same bug, and one that would have
        looked plausible.
        """
        data = self.payload_data(build_terrain_3d({"a.esp": (self.payload(64), 0.0)}))

        assert data["units_per_step"] == LAND_VERTEX_SPACING * _STRIDE

    def test_a_gentle_cell_and_a_steep_one_differ(self):
        """The defect itself: normalising made every cell the same height.

        Two cells whose relief differs by a factor of four must not draw to the
        same height. Under the old renderer they did, exactly.
        """
        gentle = self.payload_data(build_terrain_3d({"a.esp": (self.payload(16), 0.0)}))
        steep = self.payload_data(build_terrain_3d({"a.esp": (self.payload(64), 0.0)}))

        def height(data: dict) -> float:
            grid = data["surfaces"][0]["grid"]
            flat = [v for row in grid for v in row]
            return (max(flat) - min(flat)) / data["units_per_step"]

        assert height(steep) == pytest.approx(height(gentle) * 4, rel=0.02)

    def test_true_scale_is_the_default(self):
        """Exaggeration is opt-in; the view must not lie unless asked to."""
        assert self.payload_data(build_terrain_3d({"a.esp": (vhgt(), 0.0)}))["exaggeration"] == 1.0

    def test_exaggeration_can_be_chosen_and_is_labelled(self):
        """A distorted view is fine as long as it says it is distorted."""
        page = build_terrain_3d({"a.esp": (vhgt(), 0.0)})

        assert 'id="exag"' in page
        assert "exaggerated" in self.payload_data(page)["labels"]


class TestTerrainShading:
    """A hillshade layer with a hypsometric tint composited over it.

    The old renderer flat-filled one color per quad, mixing slope and height
    into a single number. That loses both: a smooth hillside came out as 1,024
    visible facets, and "which way does this face" could not be read apart from
    "how high is it". The two are now separate layers, shaded per pixel.
    """

    def test_every_palette_is_handed_over_rather_than_reimplemented(self):
        """The client shades pixels, so it needs each curve as data.

        Same reasoning as the conflict map's band table: a ramp written out
        twice is a ramp that will eventually disagree with itself.
        """
        data = payload_of(build_terrain_3d({"a.esp": (vhgt(), 0.0)}), "terrain")

        assert set(data["palettes"]) == set(TINT_RAMPS)
        for ramp in data["palettes"].values():
            assert len(ramp) == 256
            assert all(len(rgb) == 3 for rgb in ramp)
            assert all(0 <= c <= 255 for rgb in ramp for c in rgb)

    def test_the_ramps_match_the_palette_module(self):
        """What the page draws must be what :func:`tint_ramp` says."""
        data = payload_of(build_terrain_3d({"a.esp": (vhgt(), 0.0)}), "terrain")

        for name in TINT_RAMPS:
            expected = [[int(color[i : i + 2], 16) for i in (1, 3, 5)] for color in tint_ramp(name)]
            assert data["palettes"][name] == expected, name

    def test_an_unknown_palette_is_refused(self):
        """Silently drawing the wrong colors is worse than failing to build."""
        with pytest.raises(KeyError, match="unknown tint"):
            tint_ramp("chartreuse")

    def test_the_rainbow_resolves_more_than_the_hypsometric_ramp(self):
        """Which is the whole reason it is offered.

        On nearly flat ground a sequential tint is one shade of green; a
        rainbow turns the same range into distinguishable bands. Measured as
        the mean step between neighbouring samples.
        """

        def spread(name: str) -> float:
            ramp = tint_ramp(name, 32)
            return sum(channel_distance(a, b) for a, b in pairwise(ramp)) / (len(ramp) - 1)

        assert spread("rainbow") > spread("hypsometric")

    def test_the_tint_runs_low_to_high(self):
        """Hypsometric convention: green valleys, pale summits.

        Checked as lightness rather than by naming colors, so the stops can be
        retuned without rewriting the test that says which way they go.
        """
        ramp = tint_ramp("hypsometric")
        low = sum(int(ramp[0][i : i + 2], 16) for i in (1, 3, 5))
        high = sum(int(ramp[-1][i : i + 2], 16) for i in (1, 3, 5))

        assert high > low

    def test_the_tint_is_continuous(self):
        """A visible step in the ramp reads as a contour line that is not there."""
        ramp = tint_ramp("hypsometric")
        jumps = [channel_distance(a, b) for a, b in pairwise(ramp)]

        # Two units per channel, summed over three -- roughly the point where
        # a step in a smooth gradient stops being visible. At 64 samples the
        # steepest segment stepped by seven per channel, which does show.
        assert max(jumps) <= 6, f"a step of {max(jumps)} would show as a band"

    def test_the_default_opacity_lets_the_hillshade_through(self):
        """Neither layer may be doing all the work.

        Below about 0.4 the color stops reading as elevation; above about 0.7
        the shading that carries the shape is washed out.
        """
        data = payload_of(build_terrain_3d({"a.esp": (vhgt(), 0.0)}), "terrain")

        assert 0.4 <= data["tint_alpha"] <= 0.7

    def test_the_opacity_is_a_slider_from_zero(self):
        """Hillshade alone is a legitimate way to read a shape."""
        page = build_terrain_3d({"a.esp": (vhgt(), 0.0)})

        assert 'id="tint"' in page
        assert 'min="0"' in page

    def test_the_lighting_is_fully_exposed(self):
        """Azimuth, altitude, light count and scale count, all adjustable.

        Asserted on the *kind* of control and its range, not merely on the id
        being present: an element with the right id and the wrong type passes a
        presence check while being unusable, which a negative control proved by
        swapping the azimuth slider for a checkbox.
        """
        page = build_terrain_3d({"a.esp": (vhgt(), 0.0)})

        for control in ("lights", "detail", "palette", "shading", "exag"):
            assert f'<select id="{control}"' in page, control
        for control in ("hillshade", "contours"):
            assert f'type="checkbox" id="{control}"' in page, control
        # A compass needs the whole circle; solar elevation needs a quadrant.
        assert 'type="range" id="azimuth" min="0" max="359"' in page
        assert 'type="range" id="altitude" min="1" max="90"' in page
        assert 'type="range" id="tint" min="0" max="100"' in page

    def test_the_default_light_is_the_one_this_view_has_always_used(self):
        """Exposing a value must not quietly change it.

        The hard-coded vector was south-west at a shallow angle; the defaults
        are the same direction expressed in degrees, so turning the controls on
        does not restyle anybody's existing view.
        """
        data = payload_of(build_terrain_3d({"a.esp": (vhgt(), 0.0)}), "terrain")

        assert data["azimuth"] == 225
        assert 35 <= data["altitude"] <= 42
        assert data["lights"] == 1
        assert data["detail"] == 1
        assert data["hillshade"] is True

    def test_reset_restores_every_control(self):
        """A Reset that misses one control is worse than none: it looks done.

        The defaults are shipped as a block keyed by the script's own state
        names, so Reset needs no mapping table -- which is the thing that goes
        stale when a tenth control is added.
        """
        data = payload_of(build_terrain_3d({"a.esp": (vhgt(), 0.0)}), "terrain")
        page = build_terrain_3d({"a.esp": (vhgt(), 0.0)})

        adjustable = {
            "shading",
            "hillshade",
            "lights",
            "detail",
            "azimuth",
            "altitude",
            "palette",
            "tint",
            "contours",
            "exag",
        }
        covered = set(data["defaults"])
        # "exag" is the element id; "exaggeration" is the state key it sets.
        covered.add("exag") if "exaggeration" in covered else None

        assert adjustable <= covered, f"not restored by Reset: {adjustable - covered}"
        for control in adjustable:
            assert f'id="{control}"' in page, control

    def test_contours_are_on_by_default(self):
        """They are the cheapest way to read absolute height off the surface."""
        assert payload_of(build_terrain_3d({"a.esp": (vhgt(), 0.0)}), "terrain")["contours"]

    def test_the_contour_interval_is_labelled(self):
        """A contour without a stated interval measures nothing."""
        data = payload_of(build_terrain_3d({"a.esp": (vhgt(), 0.0)}), "terrain")

        assert "%(step)s" in data["labels"]["contours"]

    def test_the_viewpoint_presets_are_offered(self):
        """Neither can be reached by dragging with any accuracy."""
        page = build_terrain_3d({"a.esp": (vhgt(), 0.0)})

        assert 'id="isoView"' in page
        assert 'id="topView"' in page

    def test_relief_shading_is_the_default(self):
        """Flat is the fallback, not the starting point."""
        data = payload_of(build_terrain_3d({"a.esp": (vhgt(), 0.0)}), "terrain")

        assert data["shading"] == "relief"

    def test_the_shading_mode_can_be_switched(self):
        """The old faceted look is still reachable; some questions want it."""
        page = build_terrain_3d({"a.esp": (vhgt(), 0.0)})

        assert 'id="shading"' in page
        assert 'value="flat"' in page
        assert 'value="relief"' in page

    def test_switching_shading_cannot_change_the_geometry(self):
        """The scale fix is correctness, not style, and both modes keep it.

        Everything the projection depends on -- the height grid, the world
        spacing, the exaggeration -- is shared, so no shading mode can
        reintroduce the vertical distortion that mode was originally drawn
        with. Asserted on the payload because that is the only thing the
        renderer projects from.
        """
        data = payload_of(build_terrain_3d({"a.esp": (vhgt({70: 9}), 0.0)}), "terrain")

        assert data["units_per_step"] == LAND_VERTEX_SPACING * _STRIDE
        assert data["exaggeration"] == 1.0
        # One grid, not one per mode: two grids could drift apart.
        assert len(data["surfaces"]) == 1

    def test_a_two_step_ramp_is_refused(self):
        """A ramp needs two ends; fewer cannot describe one."""
        with pytest.raises(ValueError, match="at least two"):
            tint_ramp("hypsometric", 1)


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
