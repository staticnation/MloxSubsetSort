"""Tests for :mod:`wraithguard.land.merge`.

Two properties carry the weight. First, that a vertex only one plugin moved
survives *exactly* -- that is the entire gain merging offers, and an averaging
bug that quietly pulled it toward the reference would erase it while every
count still looked right. Second, that averaging is refused on texture indices,
where a plausible number means an unrelated texture.
"""

from __future__ import annotations

import pytest

from wraithguard.land.diff import LandData, RelativeGrid
from wraithguard.land.merge import (
    DEFAULT_STRATEGY,
    ConflictParams,
    ConflictStrategy,
    Severity,
    average_delta,
    merge_layer,
    weighted_delta,
)

SIDE = 65


def grid(moves: dict[int, int], side: int = SIDE, components: int = 1) -> RelativeGrid:
    """Build a difference grid from ``{flat index: new value}``.

    Args:
        moves: Which flat positions to change, and to what.
        side: Vertices per edge.
        components: Values per vertex.

    Returns:
        The grid, as a difference against an all-zero reference.
    """
    reference = [0] * (side * side * components)
    plugin = list(reference)
    for index, value in moves.items():
        plugin[index] = value
    return RelativeGrid.from_difference(reference, plugin, side, components)


class TestUncontestedVertices:
    """The three cases that need no decision, which is most of them."""

    def test_an_untouched_vertex_stays_at_the_reference(self) -> None:
        """Neither plugin moved it, so nothing moves."""
        merged, report = merge_layer(LandData.VERTEX_HEIGHTS, grid({}), grid({}))
        assert not merged.is_modified
        assert report.contested == 0

    def test_only_the_first_plugin_moved_it(self) -> None:
        """The edit survives exactly -- not averaged toward anything."""
        merged, report = merge_layer(LandData.VERTEX_HEIGHTS, grid({10: 500}), grid({}))
        assert merged.delta_at(10, 0) == 500
        assert report.taken_from_one == 1
        assert report.contested == 0

    def test_only_the_second_plugin_moved_it(self) -> None:
        """Symmetric, and equally exact."""
        merged, report = merge_layer(LandData.VERTEX_HEIGHTS, grid({}), grid({10: -300}))
        assert merged.delta_at(10, 0) == -300
        assert report.taken_from_two == 1

    def test_disjoint_edits_both_survive_whole(self) -> None:
        """The gain merging exists for: a load order would keep only one."""
        merged, report = merge_layer(LandData.VERTEX_HEIGHTS, grid({10: 500}), grid({900: 700}))
        assert merged.delta_at(10, 0) == 500
        assert merged.delta_at(900 % SIDE, 900 // SIDE) == 700
        assert report.contested == 0
        assert report.mergeable == 2


class TestStrategies:
    """What happens where both plugins moved the same vertex."""

    def test_overwrite_takes_the_later_plugin(self) -> None:
        """Last in the load order wins, but only at the contested vertex."""
        merged, report = merge_layer(
            LandData.VERTEX_HEIGHTS,
            grid({10: 100}),
            grid({10: 900}),
            strategy=ConflictStrategy.OVERWRITE,
        )
        assert merged.delta_at(10, 0) == 900
        assert report.contested == 1

    def test_ignore_keeps_the_earlier_plugin(self) -> None:
        """The opposite choice, same shape."""
        merged, _ = merge_layer(
            LandData.VERTEX_HEIGHTS,
            grid({10: 100}),
            grid({10: 900}),
            strategy=ConflictStrategy.IGNORE,
        )
        assert merged.delta_at(10, 0) == 100

    def test_overwrite_still_merges_the_uncontested_vertices(self) -> None:
        """OVERWRITE is not "the later plugin wins the whole cell"."""
        merged, _ = merge_layer(
            LandData.VERTEX_HEIGHTS,
            grid({10: 100, 20: 55}),
            grid({10: 900}),
            strategy=ConflictStrategy.OVERWRITE,
        )
        assert merged.delta_at(10, 0) == 900
        assert merged.delta_at(20, 0) == 55

    def test_resolve_lands_between_the_two_edits(self) -> None:
        """A compromise is inside the interval its inputs span."""
        merged, _ = merge_layer(
            LandData.VERTEX_HEIGHTS,
            grid({10: 100}),
            grid({10: 900}),
            strategy=ConflictStrategy.RESOLVE,
        )
        assert 100 < merged.delta_at(10, 0) < 900

    def test_resolve_leans_toward_the_larger_edit(self) -> None:
        """A big deliberate change should not be halved by a small incidental one."""
        merged, _ = merge_layer(
            LandData.VERTEX_HEIGHTS,
            grid({10: 20}),
            grid({10: 800}),
            strategy=ConflictStrategy.RESOLVE,
        )
        assert merged.delta_at(10, 0) > (20 + 800) / 2

    def test_two_matching_edits_are_a_minor_conflict(self) -> None:
        """When both plugins make the same small edit, the merge is minor.

        The vertex is still contested -- both moved it -- but averaging changes
        nothing, so it is counted as a minor resolution rather than a major one.
        """
        _merged, report = merge_layer(
            LandData.VERTEX_HEIGHTS,
            grid({10: 3}),
            grid({10: 3}),
            strategy=ConflictStrategy.RESOLVE,
        )
        assert report.contested == 1
        assert report.minor == 1
        assert report.major == 0


class TestAutoStrategy:
    """AUTO picks per layer, and the texture case is the one that matters."""

    @pytest.mark.parametrize(
        "layer",
        [
            LandData.VERTEX_HEIGHTS,
            LandData.VERTEX_NORMALS,
            LandData.VERTEX_COLORS,
            LandData.WORLD_MAP,
        ],
    )
    def test_every_layer_defaults_to_load_order_winner(self, layer: LandData) -> None:
        """The default is Overwrite for every layer: the later plugin wins the
        vertices it changed. Blending is opt-in per layer via a sidecar, because
        averaging every contested vertex synthesises terrain neither mod wrote."""
        assert DEFAULT_STRATEGY[layer] is ConflictStrategy.OVERWRITE

    def test_textures_overwrite(self) -> None:
        """Identifiers cannot be averaged, so textures never default to it."""
        assert DEFAULT_STRATEGY[LandData.TEXTURES] is ConflictStrategy.OVERWRITE

    def test_auto_on_textures_does_not_average(self) -> None:
        """The merged index is one of the two, never something between."""
        merged, report = merge_layer(
            LandData.TEXTURES, grid({5: 3}, side=16), grid({5: 7}, side=16)
        )
        assert merged.delta_at(5, 0) in (3, 7)
        assert report.strategy is ConflictStrategy.OVERWRITE

    def test_resolve_on_textures_is_refused(self) -> None:
        """Averaging index 3 and 7 gives 5, a third unrelated texture.

        Refusing is the point: this would not raise, crash or look wrong. It
        would paint terrain with something neither mod chose.
        """
        with pytest.raises(ValueError, match="identifiers"):
            merge_layer(
                LandData.TEXTURES,
                grid({5: 3}, side=16),
                grid({5: 7}, side=16),
                strategy=ConflictStrategy.RESOLVE,
            )


class TestAverageDelta:
    """The weighted compromise, and its severity rating."""

    def test_equal_edits_average_to_themselves(self) -> None:
        """Two mods agreeing is not really a conflict."""
        value, _ = average_delta(100, 100, ConflictParams())
        assert value == 100

    def test_the_result_lies_between_the_inputs(self) -> None:
        """A weighted mean cannot escape its inputs."""
        value, _ = average_delta(10, 200, ConflictParams())
        assert 10 <= value <= 200

    def test_opposite_edits_cancel(self) -> None:
        """Equal and opposite intents average to no change, which is honest."""
        value, _ = average_delta(50, -50, ConflictParams())
        assert value == 0

    def test_two_zero_deltas_do_not_divide_by_zero(self) -> None:
        """Not reachable from the merge loop, but the function stays total."""
        assert average_delta(0, 0, ConflictParams()) == (0, Severity.MINOR)

    def test_a_close_compromise_is_minor(self) -> None:
        """Small disagreements should not flood a report."""
        _, severity = average_delta(100, 102, ConflictParams())
        assert severity is Severity.MINOR

    def test_a_distant_compromise_is_major(self) -> None:
        """A vertex pulled hundreds of units from one mod's intent is worth naming."""
        _, severity = average_delta(10, 900, ConflictParams())
        assert severity is Severity.MAJOR

    def test_severity_does_not_change_the_value(self) -> None:
        """Classification is for reporting; the merged value is the same either way."""
        params = ConflictParams()
        lenient = ConflictParams(minor_threshold_min=1e9, minor_threshold_max=1e9)
        assert average_delta(10, 900, params)[0] == average_delta(10, 900, lenient)[0]


class TestReport:
    """The counts a caller reports to a user."""

    def test_major_conflicts_are_located(self) -> None:
        """A report has to be able to point at the cell and the vertex."""
        _, report = merge_layer(
            LandData.VERTEX_HEIGHTS,
            grid({SIDE + 3: 10}),
            grid({SIDE + 3: 900}),
            strategy=ConflictStrategy.RESOLVE,
        )
        assert report.major == 1
        assert report.major_vertices == [(3, 1)]

    def test_mergeable_counts_both_directions(self) -> None:
        """Whichever plugin moved it, an uncontested edit is a gain."""
        _, report = merge_layer(LandData.VERTEX_HEIGHTS, grid({10: 5}), grid({20: 5, 30: 5}))
        assert report.taken_from_one == 1
        assert report.taken_from_two == 2
        assert report.mergeable == 3

    def test_the_reported_strategy_is_the_one_used(self) -> None:
        """AUTO is resolved before reporting, so the report is not a guess.

        The default AUTO resolves to the load-order winner (Overwrite), so that
        is what the report names -- not the strategy the caller passed in.
        """
        _, report = merge_layer(LandData.VERTEX_HEIGHTS, grid({}), grid({}))
        assert report.strategy is ConflictStrategy.OVERWRITE


class TestMultiComponent:
    """Normals and colours carry three values per vertex."""

    def test_all_components_merge(self) -> None:
        """Each component is decided independently."""
        first = grid({0: 10, 1: 20, 2: 30}, components=3)
        second = grid({(900 * 3): 40}, components=3)
        merged, report = merge_layer(LandData.VERTEX_NORMALS, first, second)
        assert merged.deltas_at(0, 0) == (10, 20, 30)
        assert report.mergeable == 2

    def test_one_major_component_makes_the_vertex_major(self) -> None:
        """A normal badly wrong on one axis is badly wrong."""
        first = grid({0: 1, 1: 1, 2: 1}, components=3)
        second = grid({0: 2, 1: 2, 2: 900}, components=3)
        _, report = merge_layer(
            LandData.VERTEX_NORMALS, first, second, strategy=ConflictStrategy.RESOLVE
        )
        assert report.major == 1


class TestCurvatureStrategy:
    """Weighting by introduced structure rather than by magnitude.

    The case this exists for: a mod that bulk-shifts a cell 500 units should
    not silently erase a mod that carved a 60-unit road cut. Magnitude says the
    shift wins eight to one; curvature says the shift introduced no structure
    at all, because moving every vertex together leaves the surface identical.
    """

    def _slope(self, step: float = 32.0) -> list[list[float]]:
        """A uniform slope, which carries no structure however steep."""
        return [[(x + y) * step for x in range(SIDE)] for y in range(SIDE)]

    def _flat(self, rows: list[list[float]]) -> list[int]:
        """Flatten a grid of heights."""
        return [int(v) for row in rows for v in row]

    def _pair(self) -> tuple[RelativeGrid, RelativeGrid]:
        """A featureless bulk shift against a structural road cut."""
        base = self._slope()
        bulk = [[v + 500.0 for v in row] for row in base]
        cut = [list(row) for row in base]
        for x in range(SIDE):
            cut[32][x] -= 60.0
        reference = self._flat(base)
        return (
            RelativeGrid.from_difference(reference, self._flat(bulk), SIDE),
            RelativeGrid.from_difference(reference, self._flat(cut), SIDE),
        )

    def test_it_gives_the_structural_edit_more_say_than_magnitude_would(self) -> None:
        """The whole point, stated as a comparison against RESOLVE."""
        bulk, cut = self._pair()
        by_size, _ = merge_layer(
            LandData.VERTEX_HEIGHTS, bulk, cut, strategy=ConflictStrategy.RESOLVE
        )
        by_shape, _ = merge_layer(
            LandData.VERTEX_HEIGHTS, bulk, cut, strategy=ConflictStrategy.CURVATURE
        )
        # Both blend toward the +500 shift, but curvature lets the -60 cut pull
        # the result meaningfully further back toward itself.
        assert by_shape.delta_at(32, 32) < by_size.delta_at(32, 32)

    def test_the_result_still_lies_between_the_two_edits(self) -> None:
        """A weighting is not a licence to invent a value outside the inputs."""
        bulk, cut = self._pair()
        merged, _ = merge_layer(
            LandData.VERTEX_HEIGHTS, bulk, cut, strategy=ConflictStrategy.CURVATURE
        )
        assert -60 <= merged.delta_at(32, 32) <= 500

    def test_uncontested_vertices_are_untouched(self) -> None:
        """Curvature changes only what a conflict forced a choice about."""
        bulk, cut = self._pair()
        merged, report = merge_layer(
            LandData.VERTEX_HEIGHTS, bulk, cut, strategy=ConflictStrategy.CURVATURE
        )
        assert report.strategy is ConflictStrategy.CURVATURE
        assert merged.side == SIDE

    def test_it_is_not_the_default(self) -> None:
        """CURVATURE is opt-in: the default is the load-order winner, not it."""
        assert DEFAULT_STRATEGY[LandData.VERTEX_HEIGHTS] is not ConflictStrategy.CURVATURE
        assert DEFAULT_STRATEGY[LandData.VERTEX_HEIGHTS] is ConflictStrategy.OVERWRITE

    def test_it_is_refused_on_layers_with_no_surface(self) -> None:
        """Colours have no gradient to bend, so the request is a caller error."""
        with pytest.raises(ValueError, match="no surface"):
            merge_layer(
                LandData.VERTEX_COLORS,
                grid({}, components=3),
                grid({}, components=3),
                strategy=ConflictStrategy.CURVATURE,
            )

    def test_it_is_refused_on_textures(self) -> None:
        """Categorical layers are refused before the surface check is reached."""
        with pytest.raises(ValueError, match="identifiers"):
            merge_layer(
                LandData.TEXTURES,
                grid({}, side=16),
                grid({}, side=16),
                strategy=ConflictStrategy.CURVATURE,
            )


class TestWeightedDelta:
    """The caller-supplied weighting underneath CURVATURE."""

    def test_weights_decide_the_blend(self) -> None:
        """All the weight on one side returns that side's edit."""
        value, _ = weighted_delta(100, 900, 1.0, 0.0, ConflictParams())
        assert value == 100

    def test_zero_weights_fall_back_to_magnitude(self) -> None:
        """Discarding both edits would be worse than ignoring the weighting."""
        assert weighted_delta(100, 900, 0.0, 0.0, ConflictParams()) == average_delta(
            100, 900, ConflictParams()
        )

    def test_equal_weights_give_a_plain_mean(self) -> None:
        """No bias when neither side is favoured."""
        value, _ = weighted_delta(100, 200, 1.0, 1.0, ConflictParams())
        assert value == 150


class TestShapeValidation:
    """Grids must agree before they can be compared."""

    def test_mismatched_sides_are_refused(self) -> None:
        """A 65x65 grid and a 16x16 one are different layers, not a merge."""
        with pytest.raises(ValueError, match="cannot merge"):
            merge_layer(LandData.VERTEX_HEIGHTS, grid({}), grid({}, side=16))

    def test_mismatched_components_are_refused(self) -> None:
        """Heights against normals would silently misalign every vertex."""
        with pytest.raises(ValueError, match="cannot merge"):
            merge_layer(LandData.VERTEX_HEIGHTS, grid({}), grid({}, components=3))
