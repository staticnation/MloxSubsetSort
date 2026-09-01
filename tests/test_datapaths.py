"""Inferring an anchor for a ``data=`` insert from what its folder contains.

``infer_data_path_anchors`` reads each folder on disk: when it holds a plugin
that also appears in the sorted order, the insert is anchored next to whichever
existing ``data=`` line owns the neighbouring plugin. These build real folders
so the on-disk lookups run, and cover the backward/forward anchor search plus
the several "cannot infer, leave it" guards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wraithguard.configurator.datapaths import infer_data_path_anchors, insert_data_paths

if TYPE_CHECKING:
    from pathlib import Path


def _folder(base: Path, name: str, *plugins: str) -> str:
    """Create ``base/name`` holding ``plugins`` and return its bare path value."""
    folder = base / name
    folder.mkdir()
    for plugin in plugins:
        (folder / plugin).write_bytes(b"TES3")
    return str(folder)


def test_an_insert_is_anchored_after_the_preceding_owner(tmp_path: Path) -> None:
    """A folder whose plugin sorts after a base folder's is anchored after it."""
    cfg = tmp_path / "openmw.cfg"
    base = _folder(tmp_path, "Base", "base.esp")
    new = _folder(tmp_path, "New", "new.esp")
    inserts = [{"value": new, "after": None, "before": None}]
    infer_data_path_anchors(inserts, [f'data="{base}"'], ["base.esp", "new.esp"], cfg)
    assert inserts[0]["after"] == base


def test_an_insert_is_anchored_before_a_following_owner(tmp_path: Path) -> None:
    """With nothing owned behind it, the search looks forward for an anchor."""
    cfg = tmp_path / "openmw.cfg"
    base = _folder(tmp_path, "Base", "base.esp")
    new = _folder(tmp_path, "New", "new.esp")
    inserts = [{"value": new, "after": None, "before": None}]
    # new.esp sorts *before* base.esp, so only a forward neighbour is available.
    infer_data_path_anchors(inserts, [f'data="{base}"'], ["new.esp", "base.esp"], cfg)
    assert inserts[0]["before"] == base


def test_an_insert_with_an_explicit_anchor_is_left_alone(tmp_path: Path) -> None:
    """A stated anchor is the user's intent and is never second-guessed."""
    cfg = tmp_path / "openmw.cfg"
    new = _folder(tmp_path, "New", "new.esp")
    inserts = [{"value": new, "after": "Something", "before": None}]
    infer_data_path_anchors(inserts, [], ["new.esp"], cfg)
    assert inserts[0]["after"] == "Something"  # untouched


def test_an_empty_folder_yields_no_anchor(tmp_path: Path) -> None:
    """A folder with no plugins gives nothing to infer from."""
    cfg = tmp_path / "openmw.cfg"
    empty = _folder(tmp_path, "Empty")  # no plugins
    inserts = [{"value": empty, "after": None, "before": None}]
    infer_data_path_anchors(inserts, [], ["base.esp"], cfg)
    assert inserts[0]["after"] is None
    assert inserts[0]["before"] is None


def test_a_data_line_with_no_path_value_is_skipped(tmp_path: Path) -> None:
    """A malformed existing ``data=`` line contributes no owner and is ignored."""
    cfg = tmp_path / "openmw.cfg"
    new = _folder(tmp_path, "New", "new.esp")
    inserts = [{"value": new, "after": None, "before": None}]
    # "junk" is not a data= line, so it yields no path value (the continue guard).
    infer_data_path_anchors(inserts, ["junk", 'data=""'], ["new.esp"], cfg)
    assert inserts[0]["after"] is None  # no owner anywhere -> nothing inferred


def test_no_final_order_means_nothing_to_anchor_against(tmp_path: Path) -> None:
    """With no sorted order this run, there is nothing to place inserts against."""
    cfg = tmp_path / "openmw.cfg"
    new = _folder(tmp_path, "New", "new.esp")
    inserts = [{"value": new, "after": None, "before": None}]
    infer_data_path_anchors(inserts, [], [], cfg)
    assert inserts[0]["after"] is None


def test_a_folders_plugin_absent_from_the_sort_infers_nothing(tmp_path: Path) -> None:
    """A folder whose plugins are not part of this run's order is left unanchored."""
    cfg = tmp_path / "openmw.cfg"
    new = _folder(tmp_path, "New", "unsorted.esp")
    inserts = [{"value": new, "after": None, "before": None}]
    infer_data_path_anchors(inserts, [], ["something_else.esp"], cfg)
    assert inserts[0]["after"] is None


def test_the_backward_search_skips_an_unowned_neighbour(tmp_path: Path) -> None:
    """An unowned plugin directly behind is stepped over to reach an owned one."""
    cfg = tmp_path / "openmw.cfg"
    base = _folder(tmp_path, "Base", "base.esp")
    new = _folder(tmp_path, "New", "new.esp")
    inserts = [{"value": new, "after": None, "before": None}]
    # gap.esp owns no data= line, so the search continues past it to base.esp.
    order = ["base.esp", "gap.esp", "new.esp"]
    infer_data_path_anchors(inserts, [f'data="{base}"'], order, cfg)
    assert inserts[0]["after"] == base


def test_the_forward_search_skips_an_unowned_neighbour(tmp_path: Path) -> None:
    """With nothing behind, an unowned plugin ahead is stepped over to an owned one."""
    cfg = tmp_path / "openmw.cfg"
    base = _folder(tmp_path, "Base", "base.esp")
    new = _folder(tmp_path, "New", "new.esp")
    inserts = [{"value": new, "after": None, "before": None}]
    # new.esp is first, then an unowned gap, then the owned base.esp.
    order = ["new.esp", "gap.esp", "base.esp"]
    infer_data_path_anchors(inserts, [f'data="{base}"'], order, cfg)
    assert inserts[0]["before"] == base


class TestInsertDataPaths:
    """Placing new data= lines relative to existing ones."""

    def test_a_duplicate_insert_is_skipped(self, capsys) -> None:
        """An insert whose path is already present is dropped, with a note."""
        rows = insert_data_paths(
            ['data="Base"'], [{"value": "Base", "after": None, "before": None}]
        )
        values = [line for line, _new, _val in rows]
        assert values == ['data="Base"']  # nothing added
        assert "already present" in capsys.readouterr().out

    def test_an_anchor_that_is_absent_appends_at_the_end(self, capsys) -> None:
        """An anchor no existing line matches falls back to appending, with a warning."""
        rows = insert_data_paths(
            ['data="Base"'], [{"value": "New", "after": "NotThere", "before": None}]
        )
        values = [line for line, _new, _val in rows]
        assert values[-1] == 'data="New"'  # appended at the end
        assert "not found" in capsys.readouterr().out

    def test_a_before_anchor_places_the_line_ahead(self) -> None:
        """A ``before`` anchor emits the new line immediately before its target."""
        rows = insert_data_paths(
            ['data="Base"'], [{"value": "New", "after": None, "before": "Base"}]
        )
        values = [line for line, _new, _val in rows]
        assert values.index('data="New"') < values.index('data="Base"')

    def test_an_after_anchor_places_the_line_behind(self) -> None:
        """An ``after`` anchor emits the new line immediately after its target."""
        rows = insert_data_paths(
            ['data="Base"'], [{"value": "New", "after": "Base", "before": None}]
        )
        values = [line for line, _new, _val in rows]
        assert values.index('data="New"') > values.index('data="Base"')

    def test_an_empty_value_is_still_placed(self, capsys) -> None:
        """An insert whose value normalises to empty skips the dedupe bookkeeping."""
        rows = insert_data_paths([], [{"value": "", "after": None, "before": None}])
        # It has no anchor, so it lands in the leftover-appended tail.
        assert any(new for _line, new, _val in rows)
