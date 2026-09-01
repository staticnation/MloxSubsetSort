"""Small guard clauses and edge branches left uncovered across the tree.

Each of these is a one-liner -- an error guard, an early return, a property, a
revisit-skip -- that a module's main tests never happened to reach. They are
grouped here rather than scattered because none belongs to a larger feature;
together they close the last statement or two in several near-complete files.
"""

from __future__ import annotations

import pytest

from wraithguard.configurator.cfglines import orphan_cfg_entries
from wraithguard.land.cells import MergedCellRecord, merge_cell_into
from wraithguard.land.cleaning import CleaningReport
from wraithguard.land.curvature import curvature_map
from wraithguard.land.diff import RelativeGrid, is_deleted
from wraithguard.land.textures import NO_TEXTURE, KnownTextures
from wraithguard.nif.report import Shape, Structure, texture_key
from wraithguard.patch.status import conflict_this
from wraithguard.sort.graph import would_create_cycle
from wraithguard.tes3fields.naming import subrecord_for
from wraithguard.viz.geometry import group_by_cell, parse_grid
from wraithguard.viz.housekeeping import _remove_tree
from wraithguard.viz.terrain3d import _sample


def test_cleaning_report_dropped_sums_both_reasons() -> None:
    """``dropped`` totals the unmodified and single-source cells removed."""
    assert CleaningReport(unmodified=2, single_source=3).dropped == 5


def test_curvature_map_rejects_a_too_small_grid() -> None:
    """Curvature needs neighbours, so a sub-2x2 grid is an error."""
    with pytest.raises(ValueError, match="2x2"):
        curvature_map([[1.0]])


def test_parse_grid_rejects_a_non_string_id() -> None:
    """A record id that is not a string carries no coordinates."""
    assert parse_grid(123) is None


def test_structure_totals_sum_across_shapes() -> None:
    """The vertex/triangle totals add up every shape's counts."""
    structure = Structure(
        shapes=[Shape(name="a", vertices=3, triangles=1), Shape(name="b", vertices=4, triangles=2)]
    )
    assert structure.total_vertices == 7
    assert structure.total_triangles == 3


def test_texture_key_strips_prefix_and_extension() -> None:
    """The comparison key drops the ``textures/`` prefix and the file extension."""
    assert texture_key("textures/foo.dds") == "foo"
    # A bare stem with no extension comes back unchanged.
    assert texture_key("bar") == "bar"
    # Doubled separators are collapsed so two spellings compare equal.
    assert texture_key("textures//sub//foo.dds") == "sub/foo"


def test_remove_tree_deletes_a_nested_directory(tmp_path: object) -> None:
    """``_remove_tree`` removes a folder and its subdirectories, not just files."""
    from pathlib import Path

    root = Path(str(tmp_path)) / "gen"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "file.txt").write_text("x", encoding="utf-8")
    assert _remove_tree(root) is True
    assert not root.exists()


def test_remove_tree_skips_an_entry_that_is_neither_file_dir_nor_symlink(
    tmp_path: object, monkeypatch
) -> None:
    """A special entry (device/FIFO) matches no branch; removal fails cleanly.

    Every predicate is forced false to stand in for such an entry portably, so
    the walker leaves it in place and the parent ``rmdir`` reports the folder is
    not empty rather than crashing.
    """
    from pathlib import Path

    root = Path(str(tmp_path)) / "gen"
    root.mkdir()
    (root / "weird").write_text("x", encoding="utf-8")
    # is_dir stays real so rglob still walks the folder; the child file is
    # disguised as neither file nor symlink so it matches no removal branch.
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    assert _remove_tree(root) is False


def test_texture_translation_skips_an_index_with_no_known_texture() -> None:
    """A table index whose identifier was never registered is left out.

    ``translation`` guards against a ``_table`` entry with no matching
    ``_by_id`` record rather than emitting a bogus VTEX mapping for it.
    """
    textures = KnownTextures()
    textures._table[5] = "ghost.tga"  # never registered in _by_id
    mapping = textures.translation()
    assert mapping == {NO_TEXTURE: NO_TEXTURE}


def test_group_by_cell_handles_a_conflict_with_no_winner() -> None:
    """A conflict that records no winner still counts, with an empty tally."""
    grouped = group_by_cell([{"id": "Foo (-1, 2)", "type": "CELL", "plugins": ["a.esp"]}])
    cell = next(iter(grouped))
    assert grouped[cell].winners == {}
    assert grouped[cell].total == 1


def test_sample_with_a_trivial_stride_copies_the_grid() -> None:
    """Stride 1 means no reduction -- the grid comes back unchanged."""
    grid = [[1.0, 2.0], [3.0, 4.0]]
    assert _sample(grid, 1) == grid


def test_conflict_this_of_nothing_is_empty() -> None:
    """No values in play yields an empty result, not an index error."""
    assert conflict_this([]) == []


def test_would_create_cycle_is_true_when_target_reaches_start() -> None:
    """The proposed edge closes a loop: target already reaches start."""
    assert would_create_cycle({"target": ["start"]}, "start", "target") is True


def test_is_deleted_reads_a_list_of_flags() -> None:
    """A record whose ``flags`` is a list is deleted iff DELETED is among them."""
    assert is_deleted({"flags": ["DELETED"]}) is True
    assert is_deleted({"flags": ["PERSISTENT"]}) is False


def test_known_textures_skips_a_deleted_land_texture() -> None:
    """A deleted LTEX must not enter the shared table or claim an index."""
    table = KnownTextures()
    mapping = table.observe(
        "modA",
        [{"type": "LandscapeTexture", "id": "tx_gone", "index": 0, "flags": "DELETED"}],
    )
    assert len(table) == 0  # nothing registered
    assert mapping == {NO_TEXTURE: NO_TEXTURE}  # only the identity entry


def test_subrecord_for_unknown_type_and_tag_is_none() -> None:
    """An undocumented record type falls through to the shared pages, then None."""
    assert subrecord_for("ZZZZ_NOT_A_TYPE", "ZZZZ_NOT_A_TAG") is None


def test_subrecord_for_known_type_unknown_tag_is_none() -> None:
    """A real record with a tag it does not define, and none shared, is None."""
    assert subrecord_for("ACTI", "ZZZZ_NOT_A_TAG") is None


def test_subrecord_for_falls_back_to_a_shared_page() -> None:
    """A tag the record itself omits is found on a shared subrecord page."""
    # ACTI does not define CNDT, but the shared condition page does.
    found = subrecord_for("ACTI", "CNDT")
    assert found is not None
    assert found.name == "CNDT"


def test_orphan_cfg_entries_skips_a_non_data_line() -> None:
    """A ``data_lines`` entry that is not a data= line is ignored, not parsed."""
    content_orphans, data_orphans = orphan_cfg_entries(
        [],
        ["this is not a data= line"],
        curated_lower=set(),
        needs_cleaning_lower=set(),
        declared_plugins_lower=set(),
        declared_data_norms=set(),
    )
    assert content_orphans == []
    assert data_orphans == []


def test_merge_cell_into_without_a_data_dict_still_merges_flags() -> None:
    """A cell whose incoming version carries no ``data`` block skips that path."""
    target = MergedCellRecord(coords=(0, 0), record={"id": "c", "flags": "A"})
    merge_cell_into(target, {"id": "c", "flags": "B"}, "modB")
    assert "B" in target.record["flags"]
    assert "A" in target.record["flags"]


def test_relative_grid_set_deltas_rejects_a_wrong_delta_count() -> None:
    """A single-component grid takes one delta per vertex; two is an error."""
    grid = RelativeGrid([0, 0, 0, 0], side=2)
    with pytest.raises(ValueError, match="delta"):
        grid.set_deltas(0, 0, (1, 2))


def test_relative_grid_to_rows_rejects_a_multi_component_grid() -> None:
    """``to_rows`` is meaningful only for a single value per vertex."""
    grid = RelativeGrid([0] * 8, side=2, components=2)
    with pytest.raises(ValueError, match="single-component"):
        grid.to_rows()


def test_relative_grid_to_rows_reshapes_a_single_component_grid() -> None:
    """With one value per vertex, ``to_rows`` reshapes the flat grid by row."""
    grid = RelativeGrid([1, 2, 3, 4], side=2)
    assert grid.to_rows() == [[1, 2], [3, 4]]


def test_would_create_cycle_skips_a_node_reached_twice() -> None:
    """A diamond reaches one node by two paths; the second visit is skipped."""
    adjacency = {"target": ["a", "b"], "a": ["c"], "b": ["c"]}
    assert would_create_cycle(adjacency, "z", "target") is False
