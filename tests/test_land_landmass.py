"""Tests for :mod:`wraithguard.land.textures` and :mod:`wraithguard.land.landmass`.

The sharpest test here is the texture one. Three real mods in this repository's
sample all number their first texture ``0`` and mean three unrelated things by
it, so a merge that compares raw ``VTEX`` values repaints terrain while looking
entirely successful. These tests reproduce that collision deliberately.
"""

from __future__ import annotations

import pytest

from wraithguard.land.diff import LandData, LandscapeLayers
from wraithguard.land.landmass import (
    CellContention,
    Landmass,
    PluginRecords,
    _mask_layers,
    build_reference,
    merge_master_layers,
    plugin_differences,
    survey,
)
from wraithguard.land.meta import MergeSettings, PluginMeta
from wraithguard.land.textures import (
    NO_TEXTURE,
    KnownTextures,
    compact_textures,
    fallback_texture_index,
    ltex_of,
    translate_indices,
    vtex_of,
)


def _tex_record(coords: tuple[int, int], fill: int) -> dict[str, object]:
    """A Landscape record whose 16x16 texture grid is every cell set to ``fill``."""
    import base64
    import struct

    data = base64.b64encode(struct.pack("<256H", *([fill] * 256))).decode()
    return {
        "type": "Landscape",
        "grid": list(coords),
        "landscape_flags": "USES_TEXTURES",
        "texture_indices": {"data": data},
    }


def ltex(identifier: str, index: int, file_name: str | None = None) -> dict[str, object]:
    """Build a ``LandscapeTexture`` record.

    Args:
        identifier: The record's id.
        index: Its zero-based index.
        file_name: The texture file, if any.

    Returns:
        The record.
    """
    record: dict[str, object] = {
        "type": "LandscapeTexture",
        "id": identifier,
        "index": index,
    }
    if file_name is not None:
        record["file_name"] = file_name
    return record


class TestIndexConversion:
    """VTEX is LTEX plus one, and zero means nothing."""

    def test_vtex_is_one_more_than_ltex(self) -> None:
        """The off-by-one that repaints a whole cell if it is missed."""
        assert vtex_of(0) == 1
        assert vtex_of(41) == 42

    def test_zero_has_no_record_behind_it(self) -> None:
        """VTEX 0 means unpainted, so there is no LTEX index to return."""
        assert ltex_of(NO_TEXTURE) is None

    def test_conversions_invert(self) -> None:
        """Round-tripping an index returns it."""
        assert ltex_of(vtex_of(7)) == 7


class TestKnownTextures:
    """The shared table, and the collision it exists to survive."""

    def test_a_texture_is_registered_by_identity(self) -> None:
        """Textures are matched by id, the only part that survives merging."""
        known = KnownTextures()
        known.observe("a.esp", [ltex("Rock", 0, "rock.tga")])
        entry = known.get("Rock")
        assert entry is not None
        assert entry.file_name == "rock.tga"
        assert entry.source == "a.esp"

    def test_two_mods_claiming_index_zero_get_distinct_shared_indices(self) -> None:
        """The real collision: same number, different textures, both survive."""
        known = KnownTextures()
        known.observe("a.esp", [ltex("RM_rock_01", 0)])
        known.observe("b.esp", [ltex("Rock_Coastal", 0)])
        first = known.get("RM_rock_01")
        second = known.get("Rock_Coastal")
        assert first is not None
        assert second is not None
        assert first.index != second.index
        assert len(known) == 2

    def test_the_second_mod_translates_away_from_the_first(self) -> None:
        """After the collision, b.esp's index 0 no longer means a.esp's texture."""
        known = KnownTextures()
        known.observe("a.esp", [ltex("RM_rock_01", 0)])
        mapping = known.observe("b.esp", [ltex("Rock_Coastal", 0)])
        rock = known.get("RM_rock_01")
        coastal = known.get("Rock_Coastal")
        assert rock is not None
        assert coastal is not None
        assert mapping[vtex_of(0)] == vtex_of(coastal.index)
        assert mapping[vtex_of(0)] != vtex_of(rock.index)

    def test_the_same_texture_in_two_mods_keeps_one_index(self) -> None:
        """Identical ids are one texture however many plugins declare them."""
        known = KnownTextures()
        known.observe("a.esp", [ltex("Rock", 0)])
        known.observe("b.esp", [ltex("Rock", 5)])
        assert len(known) == 1

    def test_a_later_file_name_wins_and_is_attributed(self) -> None:
        """The last plugin to supply a file name owns it, and is recorded."""
        known = KnownTextures()
        known.observe("a.esp", [ltex("Rock", 0, "old.tga")])
        known.observe("b.esp", [ltex("Rock", 0, "new.tga")])
        entry = known.get("Rock")
        assert entry is not None
        assert entry.file_name == "new.tga"
        assert entry.source == "b.esp"

    def test_no_texture_always_maps_to_itself(self) -> None:
        """Unpainted terrain must never be translated into a real texture."""
        known = KnownTextures()
        mapping = known.observe("a.esp", [ltex("Rock", 0)])
        assert mapping[NO_TEXTURE] == NO_TEXTURE

    @pytest.mark.parametrize(
        "record",
        [
            {"type": "LandscapeTexture", "index": 0},
            {"type": "LandscapeTexture", "id": "Rock"},
            {"type": "LandscapeTexture", "id": 5, "index": 0},
        ],
        ids=["no-id", "no-index", "id-not-a-string"],
    )
    def test_unusable_records_are_skipped_not_invented(self, record: dict[str, object]) -> None:
        """A texture that cannot be matched or referenced is skipped."""
        known = KnownTextures()
        known.observe("a.esp", [record])
        assert len(known) == 0

    def test_other_record_types_are_ignored(self) -> None:
        """A whole plugin can be passed without filtering it first."""
        known = KnownTextures()
        known.observe("a.esp", [{"type": "Static", "id": "x"}, ltex("Rock", 0)])
        assert len(known) == 1

    def test_sorted_is_in_shared_index_order(self) -> None:
        """Emitting LTEX records requires a stable order."""
        known = KnownTextures()
        known.observe("a.esp", [ltex("B", 1), ltex("A", 0)])
        assert [t.index for t in known.sorted()] == [0, 1]


class TestTranslateIndices:
    """Rewriting a grid, and admitting what could not be rewritten."""

    def test_values_are_translated(self) -> None:
        """Each local value becomes its shared equivalent."""
        result = translate_indices([0, 1, 2], {0: 0, 1: 5, 2: 9})
        assert result.values == [0, 5, 9]
        assert result.is_complete

    def test_an_unknown_index_is_passed_through_and_reported(self) -> None:
        """Substituting zero would silently repaint; dropping loses other edits."""
        result = translate_indices([1, 42, 42], {0: 0, 1: 1})
        assert result.values == [1, 42, 42]
        assert not result.is_complete
        assert result.unknown == {42: 2}


class TestLandmass:
    """Reference assembly, in load order."""

    def _land(self, coords: tuple[int, int]) -> dict[str, object]:
        """Build a minimal Landscape record with no grids."""
        return {"type": "Landscape", "grid": list(coords)}

    def test_masters_apply_in_order(self) -> None:
        """A later master replaces an earlier one's version of a cell."""
        landmass, _ = build_reference(
            [
                PluginRecords("Morrowind.esm", [self._land((1, 1))]),
                PluginRecords("Tribunal.esm", [self._land((1, 1))]),
            ]
        )
        assert landmass.sources[(1, 1)] == "Tribunal.esm"

    def test_cells_accumulate_across_masters(self) -> None:
        """Different cells from different masters all land in the reference."""
        landmass, _ = build_reference(
            [
                PluginRecords("Morrowind.esm", [self._land((0, 0))]),
                PluginRecords("Bloodmoon.esm", [self._land((-19, 27))]),
            ]
        )
        assert len(landmass) == 2

    def test_a_masters_excluded_layer_is_dropped_from_the_reference(self) -> None:
        """A master's .mergedlands.toml applies here, since it is not diffed.

        A layer a master sets ``included = false`` must not enter the reference
        -- the whole point of excluding a master like distant_seafloor. Masked
        via _mask_layers below; here the plumbing is checked end to end.
        """
        layers = LandscapeLayers(
            coords=(0, 0),
            declared=LandData.VERTEX_HEIGHTS | LandData.WORLD_MAP,
            heights=[0] * 4,
            world_map=[0] * 4,
        )
        _mask_layers(layers, LandData.VERTEX_HEIGHTS)  # world map excluded
        assert layers.world_map is None
        assert not (layers.declared & LandData.WORLD_MAP)
        assert layers.heights == [0] * 4  # an included layer is untouched
        assert layers.declared & LandData.VERTEX_HEIGHTS

    def test_a_master_marked_a_previous_merge_is_skipped(self) -> None:
        """A merged output left in the masters must not seed the reference."""
        metas = {"Old Merge.esm": PluginMeta(meta_type="MergedLands")}
        landmass, _ = build_reference(
            [
                PluginRecords("Morrowind.esm", [self._land((0, 0))]),
                PluginRecords("Old Merge.esm", [self._land((5, 5))]),
            ],
            metas=metas,
        )
        assert (0, 0) in landmass.cells
        assert (5, 5) not in landmass.cells, "a previous merge seeded the reference"

    def test_a_master_with_an_included_layer_is_a_no_op(self) -> None:
        """Settings that exclude nothing leave the reference exactly as before.

        The common case -- a settings file that only tweaks a mod, or none at
        all -- must not change how the masters build.
        """
        metas = {
            "Morrowind.esm": PluginMeta(layers={"world_map_data": MergeSettings(included=True)})
        }
        with_meta, _ = build_reference(
            [PluginRecords("Morrowind.esm", [self._land((0, 0))])], metas=metas
        )
        without, _ = build_reference([PluginRecords("Morrowind.esm", [self._land((0, 0))])])
        assert with_meta.cells.keys() == without.cells.keys()

    def test_an_unreadable_record_is_skipped_not_fatal(self) -> None:
        """One malformed cell must not abandon a whole load order."""
        landmass, _ = build_reference(
            [
                PluginRecords(
                    "broken.esm",
                    [{"type": "Landscape"}, self._land((2, 2))],
                )
            ]
        )
        assert len(landmass) == 1

    def test_missing_cell_returns_none(self) -> None:
        """A cell the masters never had has no reference terrain."""
        assert Landmass(name="empty").get((0, 0)) is None


class TestPluginDifferences:
    """Per-plugin changes against the reference."""

    def _land_with_textures(self, coords: tuple[int, int], values: list[int]) -> dict[str, object]:
        """Build a Landscape record whose texture grid is supplied directly."""
        return {"type": "Landscape", "grid": list(coords), "_textures": values}

    def test_an_unchanged_plugin_yields_nothing(self) -> None:
        """Rewriting a record without altering it is not a change."""
        master = PluginRecords("m.esm", [{"type": "Landscape", "grid": [0, 0]}])
        reference, known = build_reference([master])
        changes = plugin_differences(
            reference,
            PluginRecords("mod.esp", [{"type": "Landscape", "grid": [0, 0]}]),
            known,
        )
        assert changes == []

    def test_a_plugin_registers_its_textures(self) -> None:
        """The shared table grows as each plugin is surveyed."""
        reference, known = build_reference([PluginRecords("m.esm", [ltex("A", 0)])])
        plugin_differences(reference, PluginRecords("mod.esp", [ltex("B", 0)]), known)
        assert len(known) == 2

    def test_an_unreadable_record_is_skipped(self) -> None:
        """A landscape record that cannot be decoded contributes no change."""
        reference, known = build_reference([])
        changes = plugin_differences(
            reference,
            PluginRecords("mod.esp", [{"type": "Landscape"}]),  # no grid -> undecodable
            known,
        )
        assert changes == []

    def test_a_texture_edit_is_a_change_even_with_an_unknown_index(self) -> None:
        """A plugin repainting a cell is a change; an undefined index is warned.

        The plugin paints VTEX 5 with no LTEX behind it (a missing master), so
        the index is left untranslated but the cell still counts as modified.
        """
        reference, known = build_reference([PluginRecords("m.esm", [_tex_record((0, 0), 0)])])
        changes = plugin_differences(
            reference, PluginRecords("mod.esp", [_tex_record((0, 0), 5)]), known
        )
        assert len(changes) == 1
        assert changes[0].is_modified


class TestMergeMasterLayers:
    """Masters combine per layer, not per whole record."""

    def test_a_declared_carried_layer_is_folded_in(self) -> None:
        """A later master's declared, present layer replaces the earlier value."""
        existing = LandscapeLayers(coords=(0, 0), declared=LandData(0))
        incoming = LandscapeLayers(
            coords=(0, 0), declared=LandData.VERTEX_HEIGHTS, heights=[1, 2, 3, 4]
        )
        merge_master_layers(existing, incoming)
        assert existing.heights == [1, 2, 3, 4]
        assert existing.declared & LandData.VERTEX_HEIGHTS

    def test_a_declared_but_absent_layer_is_not_copied(self) -> None:
        """Declaring a layer without carrying it leaves the earlier value alone."""
        existing = LandscapeLayers(
            coords=(0, 0), declared=LandData.VERTEX_HEIGHTS, heights=[9, 9, 9, 9]
        )
        incoming = LandscapeLayers(coords=(0, 0), declared=LandData.VERTEX_HEIGHTS, heights=None)
        merge_master_layers(existing, incoming)
        assert existing.heights == [9, 9, 9, 9]  # untouched


class TestSurvey:
    """Grouping every plugin's changes by cell."""

    def test_an_empty_load_order_finds_nothing(self) -> None:
        """No mods, no contention."""
        reference, known = build_reference([])
        assert survey(reference, [], known) == {}

    def test_two_plugins_editing_one_cell_share_its_contention(self) -> None:
        """A second plugin editing the same cell is appended to the same entry.

        This exercises the ``get``-then-append path where the cell's contention
        already exists from the first plugin.
        """
        reference, known = build_reference([PluginRecords("m.esm", [_tex_record((0, 0), 0)])])
        result = survey(
            reference,
            [
                PluginRecords("a.esp", [_tex_record((0, 0), 5)]),
                PluginRecords("b.esp", [_tex_record((0, 0), 6)]),
            ],
            known,
        )
        assert list(result) == [(0, 0)]
        assert result[(0, 0)].plugins == ["a.esp", "b.esp"]
        assert result[(0, 0)].is_contested

    def test_layers_can_be_declined(self) -> None:
        """``allowed`` lets a caller merge terrain without touching colours."""
        reference, known = build_reference([])
        result = survey(reference, [], known, allowed=LandData.VERTEX_HEIGHTS)
        assert result == {}


class TestCellContention:
    """The contested/mergeable split, which is the whole argument for merging."""

    def test_one_plugin_is_not_contested(self) -> None:
        """A single editor cannot conflict with anyone."""
        from wraithguard.land.diff import LandscapeDiff

        cell = CellContention(coords=(0, 0), changes=[LandscapeDiff((0, 0), "a.esp")])
        assert not cell.is_contested

    def test_disjoint_edits_are_all_mergeable(self) -> None:
        """Two mods moving different vertices lose nothing to a merge."""
        from wraithguard.land.diff import LandscapeDiff, RelativeGrid

        def grid_moving(index: int) -> RelativeGrid:
            reference = [0] * (65 * 65)
            plugin = list(reference)
            plugin[index] = 1
            return RelativeGrid.from_difference(reference, plugin, side=65)

        cell = CellContention(
            coords=(0, 0),
            changes=[
                LandscapeDiff((0, 0), "a.esp", heights=grid_moving(10)),
                LandscapeDiff((0, 0), "b.esp", heights=grid_moving(900)),
            ],
        )
        contested, mergeable = cell.height_overlap()
        assert contested == 0
        assert mergeable == 2

    def test_the_same_vertex_is_contested(self) -> None:
        """Two mods moving one vertex genuinely disagree, and it is counted once."""
        from wraithguard.land.diff import LandscapeDiff, RelativeGrid

        def grid_moving(index: int, value: int) -> RelativeGrid:
            reference = [0] * (65 * 65)
            plugin = list(reference)
            plugin[index] = value
            return RelativeGrid.from_difference(reference, plugin, side=65)

        cell = CellContention(
            coords=(0, 0),
            changes=[
                LandscapeDiff((0, 0), "a.esp", heights=grid_moving(10, 1)),
                LandscapeDiff((0, 0), "b.esp", heights=grid_moving(10, 2)),
            ],
        )
        contested, mergeable = cell.height_overlap()
        assert contested == 1
        assert mergeable == 0

    def test_plugins_are_listed_in_load_order(self) -> None:
        """Reports name the plugins in the order they were surveyed."""
        from wraithguard.land.diff import LandscapeDiff

        cell = CellContention(
            coords=(0, 0),
            changes=[LandscapeDiff((0, 0), "a.esp"), LandscapeDiff((0, 0), "b.esp")],
        )
        assert cell.plugins == ["a.esp", "b.esp"]

    def test_new_land_is_flagged_when_any_change_is_new(self) -> None:
        """A change on terrain the masters never had marks the cell as new land."""
        from wraithguard.land.diff import LandscapeDiff

        old = CellContention(coords=(0, 0), changes=[LandscapeDiff((0, 0), "a.esp")])
        new = CellContention(coords=(0, 0), changes=[LandscapeDiff((0, 0), "a.esp", new_land=True)])
        assert old.is_new_land is False
        assert new.is_new_land is True

    def test_height_overlap_skips_changes_with_no_heights(self) -> None:
        """A change that edited no heights contributes nothing to the overlap."""
        from wraithguard.land.diff import LandscapeDiff, RelativeGrid

        def grid_moving(index: int) -> RelativeGrid:
            reference = [0] * (65 * 65)
            plugin = list(reference)
            plugin[index] = 1
            return RelativeGrid.from_difference(reference, plugin, side=65)

        cell = CellContention(
            coords=(0, 0),
            changes=[
                LandscapeDiff((0, 0), "a.esp", heights=None),  # no height edit -> skipped
                LandscapeDiff((0, 0), "b.esp", heights=grid_moving(10)),
            ],
        )
        contested, mergeable = cell.height_overlap()
        assert contested == 0
        assert mergeable == 1


class TestUnknownTextureFallback:
    """A merged cell can paint an index no LTEX defines (missing master).

    By default the value is remapped to the fallback -- the smallest valid
    painted texture -- so the written plugin always loads; opting out passes it
    through as a dangling index. Either way ``unresolved`` reports it, so the
    emit can say what it did.
    """

    def _known(self) -> KnownTextures:
        known = KnownTextures()
        known.observe("a.esp", [ltex("Rock", 0), ltex("Sand", 1)])
        return known

    def test_default_substitutes_the_unknown_with_the_fallback(self) -> None:
        """Safe by default: the unknown index resolves to a real texture."""
        known = self._known()
        used = {vtex_of(0), vtex_of(1), 999}  # 999 has no LTEX behind it
        mapping, kept, unresolved = compact_textures(known, used)
        assert unresolved == [999]
        assert mapping[999] == fallback_texture_index(mapping)
        # The fallback is a real, kept texture index -- not a dangle.
        assert mapping[999] in {vtex_of(t.index) for t in kept}

    def test_opting_out_passes_the_unknown_through(self) -> None:
        """A CLI caller can keep the honest dangling index instead."""
        known = self._known()
        used = {vtex_of(0), 999}
        mapping, _kept, unresolved = compact_textures(known, used, substitute_unknown=False)
        assert unresolved == [999]
        assert 999 not in mapping
        assert mapping.get(999, 999) == 999  # emit passes it through

    def test_no_unknowns_reports_none(self) -> None:
        """The common case: everything resolves, nothing to report."""
        known = self._known()
        _mapping, _kept, unresolved = compact_textures(known, {vtex_of(0), vtex_of(1)})
        assert unresolved == []

    def test_fallback_is_the_minimum_real_index(self) -> None:
        """fallback_texture_index skips NO_TEXTURE and takes the lowest real."""
        mapping = {NO_TEXTURE: NO_TEXTURE, 5: vtex_of(3), 6: vtex_of(0), 7: vtex_of(1)}
        assert fallback_texture_index(mapping) == vtex_of(0)

    def test_fallback_with_nothing_painted_is_no_texture(self) -> None:
        """An empty merge has only NO_TEXTURE to fall back to."""
        assert fallback_texture_index({NO_TEXTURE: NO_TEXTURE}) == NO_TEXTURE


class TestLandscapeRecordFilter:
    """``_landscape_records`` keeps only live landscape records."""

    def test_non_landscape_and_deleted_records_are_dropped(self) -> None:
        """A non-LAND record and a deleted LAND are both skipped."""
        from wraithguard.land.landmass import LANDSCAPE_TYPE, _landscape_records

        kept = _landscape_records(
            [
                {"type": "Npc"},
                {"type": LANDSCAPE_TYPE, "flags": "DELETED"},
                {"type": LANDSCAPE_TYPE, "id": "live"},
            ]
        )
        assert [r.get("id") for r in kept] == ["live"]
