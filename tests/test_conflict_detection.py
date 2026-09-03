"""detect_conflicts, list_subset_singles/list_other_singles, and their report
formatters -- the record-level conflict scanner the GUI's conflict window and
the CLI's --conflicts both sit on top of.

Nothing in the suite called these directly before this file: _scan_touch,
detect_conflicts, _list_singles, list_subset_singles, list_other_singles,
format_conflict_report, and write_conflict_csv were all at 0%. Both engines
_scan_touch can take are exercised here -- the builtin binary parser
(session=None, the default) and the Tes3ConvSession path, seeded with
pre-written JSON the same way test_tes3conv_session.py seeds one, so no real
tes3conv binary is needed for either.
"""

from __future__ import annotations

import csv
import json
from typing import TYPE_CHECKING, Any

import pytest
from conftest import rec, sub, write_plugin, zstr

import wraithguard_toolkit as core
from wraithguard.plugins import PluginFileIndex

if TYPE_CHECKING:
    from pathlib import Path


def _stat(record_id: str, mesh: str = "x.nif", *, deleted: bool = False) -> bytes:
    """A STAT record -- the simplest thing that counts as real game content."""
    body = sub("NAME", zstr(record_id)) + sub("MODL", zstr(mesh))
    if deleted:
        body += sub("DELE", b"")
    return rec("STAT", body)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "Data Files"
    d.mkdir()
    return d


class TestScanTouchBuiltin:
    """_scan_touch/detect_conflicts via the built-in binary parser (session=None)."""

    def test_a_record_two_plugins_both_define_is_a_conflict(self, data_dir: Path) -> None:
        write_plugin(data_dir / "A.esp", extra=_stat("torch_01"))
        write_plugin(data_dir / "B.esp", extra=_stat("torch_01"))
        index = PluginFileIndex([str(data_dir)])

        conflicts, stats = core.detect_conflicts(["A.esp", "B.esp"], index)

        assert len(conflicts) == 1
        c = conflicts[0]
        assert c["type"] == "STAT"
        assert c["id"] == "torch_01"
        assert c["plugins"] == ["A.esp", "B.esp"]
        assert c["winner"] == "B.esp"  # last in load order wins
        assert stats["scanned"] == 2
        assert stats["records"] == 2
        assert stats["conflicts"] == 1
        assert stats["engine"] == "builtin"
        assert stats["paths"]["A.esp"] == str(data_dir / "A.esp")

    def test_a_record_only_one_plugin_defines_is_not_a_conflict(self, data_dir: Path) -> None:
        write_plugin(data_dir / "A.esp", extra=_stat("torch_01"))
        index = PluginFileIndex([str(data_dir)])

        conflicts, stats = core.detect_conflicts(["A.esp"], index)

        assert conflicts == []
        assert stats["records"] == 1

    def test_a_missing_plugin_is_reported_unreadable_not_fatal(self, data_dir: Path) -> None:
        write_plugin(data_dir / "A.esp", extra=_stat("torch_01"))
        index = PluginFileIndex([str(data_dir)])

        conflicts, stats = core.detect_conflicts(["A.esp", "Ghost.esp"], index)

        assert stats["unreadable"] == ["Ghost.esp"]
        assert stats["scanned"] == 1
        assert conflicts == []

    def test_the_same_plugin_defining_a_record_twice_counts_once(self, data_dir: Path) -> None:
        # Two STAT records sharing an id in the SAME plugin -- a malformed or
        # duplicate entry, not two independent contributors to the conflict.
        write_plugin(data_dir / "A.esp", extra=_stat("torch_01") + _stat("torch_01"))
        write_plugin(data_dir / "B.esp", extra=_stat("torch_01"))
        index = PluginFileIndex([str(data_dir)])

        conflicts, _stats = core.detect_conflicts(["A.esp", "B.esp"], index)

        assert len(conflicts) == 1
        assert conflicts[0]["plugins"] == ["A.esp", "B.esp"]

    def test_deleted_by_lists_only_the_plugins_that_deleted_it(self, data_dir: Path) -> None:
        write_plugin(data_dir / "A.esp", extra=_stat("torch_01"))
        write_plugin(data_dir / "B.esp", extra=_stat("torch_01", deleted=True))
        index = PluginFileIndex([str(data_dir)])

        conflicts, _stats = core.detect_conflicts(["A.esp", "B.esp"], index)

        assert conflicts[0]["deleted_by"] == ["B.esp"]

    def test_involves_subset_true_when_a_custom_plugin_is_in_the_chain(
        self, data_dir: Path
    ) -> None:
        write_plugin(data_dir / "A.esp", extra=_stat("torch_01"))
        write_plugin(data_dir / "Mine.esp", extra=_stat("torch_01"))
        index = PluginFileIndex([str(data_dir)])

        conflicts, _stats = core.detect_conflicts(
            ["A.esp", "Mine.esp"], index, subset_names=["Mine.esp"]
        )

        assert conflicts[0]["involves_subset"] is True

    def test_subset_matching_is_case_insensitive(self, data_dir: Path) -> None:
        write_plugin(data_dir / "A.esp", extra=_stat("torch_01"))
        write_plugin(data_dir / "Mine.esp", extra=_stat("torch_01"))
        index = PluginFileIndex([str(data_dir)])

        conflicts, _stats = core.detect_conflicts(
            ["A.esp", "Mine.esp"], index, subset_names=["MINE.ESP"]
        )

        assert conflicts[0]["involves_subset"] is True

    def test_conflicts_sort_subset_involved_first_then_by_type_and_id(self, data_dir: Path) -> None:
        write_plugin(data_dir / "A.esp", extra=_stat("z_torch") + _stat("a_torch"))
        write_plugin(data_dir / "Mine.esp", extra=_stat("a_torch"))  # conflicts, involves subset
        write_plugin(data_dir / "C.esp", extra=_stat("z_torch"))  # conflicts, not subset
        index = PluginFileIndex([str(data_dir)])

        conflicts, _stats = core.detect_conflicts(
            ["A.esp", "Mine.esp", "C.esp"], index, subset_names=["Mine.esp"]
        )

        # subset-involving conflicts sort first; each group alphabetical by id.
        assert [c["id"] for c in conflicts] == ["a_torch", "z_torch"]
        assert [c["involves_subset"] for c in conflicts] == [True, False]

    def test_an_omwscripts_plugin_uses_the_text_parser(self, data_dir: Path) -> None:
        (data_dir / "A.omwscripts").write_text(
            "PLAYER_STARTUP: scripts/foo.lua\n", encoding="utf-8"
        )
        (data_dir / "B.omwscripts").write_text(
            "PLAYER_STARTUP: scripts/foo.lua\n", encoding="utf-8"
        )
        index = PluginFileIndex([str(data_dir)])

        conflicts, stats = core.detect_conflicts(["A.omwscripts", "B.omwscripts"], index)

        assert stats["scanned"] == 2
        assert conflicts == [
            {
                "type": "LuaScript",
                "id": "scripts/foo.lua",
                "plugins": ["A.omwscripts", "B.omwscripts"],
                "winner": "B.omwscripts",
                "involves_subset": False,
                "deleted_by": [],
            }
        ]


class TestScanTouchViaSession:
    """The Tes3ConvSession branch of _scan_touch -- no real tes3conv binary needed.

    Seeded exactly as test_tes3conv_session.py seeds a session: a JSON file
    dropped straight into the dump dir, keyed by the plugin's stem, reads back
    as though tes3conv had just produced it. is_omwscripts still routes past
    this branch even when a session is supplied, since .omwscripts isn't a
    TES3 file tes3conv can read at all.
    """

    @staticmethod
    def _seed(dump_dir: Path, stem: str, records: list[dict[str, Any]]) -> None:
        (dump_dir / f"{stem}.json").write_text(json.dumps(records), encoding="utf-8")

    def test_a_conflict_is_found_through_the_session(self, data_dir: Path, tmp_path: Path) -> None:
        (data_dir / "A.esp").write_bytes(b"\x00")
        (data_dir / "B.esp").write_bytes(b"\x00")
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()
        self._seed(dump_dir, "A", [{"type": "Armor", "id": "cuirass"}])
        self._seed(dump_dir, "B", [{"type": "Armor", "id": "cuirass"}])
        session = core.Tes3ConvSession(exe="unused", dump_dir=str(dump_dir), keep=True)
        index = PluginFileIndex([str(data_dir)])

        conflicts, stats = core.detect_conflicts(["A.esp", "B.esp"], index, session=session)

        assert stats["engine"] == "tes3conv"
        assert len(conflicts) == 1
        assert conflicts[0]["id"] == "cuirass"

    def test_the_same_plugin_defining_a_record_twice_counts_once_via_the_session(
        self, data_dir: Path, tmp_path: Path
    ) -> None:
        """Mirrors TestScanTouchBuiltin's version of this, for the session-backed branch.

        record_keys() is itself already deduped, first-wins, when built fresh
        from tes3conv JSON -- so this has to seed the .keys.json sidecar
        directly with a duplicate to exercise _scan_touch's own defensive
        ``seen_here`` guard, in case a hand-edited or stale cache ever hands
        one back.
        """
        (data_dir / "A.esp").write_bytes(b"\x00")
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()
        session = core.Tes3ConvSession(exe="unused", dump_dir=str(dump_dir), keep=True)
        (dump_dir / "A.keys.json").write_text(
            json.dumps(
                {
                    "v": session._SIDECAR_VER,
                    "d": [["Armor", "cuirass", False], ["Armor", "cuirass", False]],
                }
            ),
            encoding="utf-8",
        )
        index = PluginFileIndex([str(data_dir)])

        _conflicts, stats = core.detect_conflicts(["A.esp"], index, session=session)

        assert stats["records"] == 1

    def test_an_omwscripts_plugin_skips_the_session_even_when_one_is_given(
        self, data_dir: Path, tmp_path: Path
    ) -> None:
        (data_dir / "A.omwscripts").write_text(
            "PLAYER_STARTUP: scripts/foo.lua\n", encoding="utf-8"
        )
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()
        session = core.Tes3ConvSession(exe="unused", dump_dir=str(dump_dir), keep=True)
        index = PluginFileIndex([str(data_dir)])

        conflicts, stats = core.detect_conflicts(["A.omwscripts"], index, session=session)

        # Reached the text parser, not tes3conv -- no JSON was ever seeded for it.
        assert stats["records"] == 1
        assert conflicts == []


class TestListSingles:
    """list_subset_singles / list_other_singles: exactly-one-contributor records."""

    def test_subset_singles_keeps_only_your_own_lone_records(self, data_dir: Path) -> None:
        write_plugin(data_dir / "Mine.esp", extra=_stat("my_thing"))
        write_plugin(data_dir / "Vanilla.esp", extra=_stat("their_thing"))
        index = PluginFileIndex([str(data_dir)])

        records, stats = core.list_subset_singles(
            ["Mine.esp", "Vanilla.esp"], index, subset_names=["Mine.esp"]
        )

        assert [r["id"] for r in records] == ["my_thing"]
        assert records[0]["involves_subset"] is True
        assert stats["singles"] == 1

    def test_other_singles_keeps_everything_that_is_not_yours(self, data_dir: Path) -> None:
        write_plugin(data_dir / "Mine.esp", extra=_stat("my_thing"))
        write_plugin(data_dir / "Vanilla.esp", extra=_stat("their_thing"))
        index = PluginFileIndex([str(data_dir)])

        records, stats = core.list_other_singles(
            ["Mine.esp", "Vanilla.esp"], index, subset_names=["Mine.esp"]
        )

        assert [r["id"] for r in records] == ["their_thing"]
        assert records[0]["involves_subset"] is False
        assert stats["singles"] == 1

    def test_a_two_plugin_conflict_appears_in_neither_singles_list(self, data_dir: Path) -> None:
        write_plugin(data_dir / "Mine.esp", extra=_stat("shared"))
        write_plugin(data_dir / "Vanilla.esp", extra=_stat("shared"))
        index = PluginFileIndex([str(data_dir)])

        subset, _s1 = core.list_subset_singles(
            ["Mine.esp", "Vanilla.esp"], index, subset_names=["Mine.esp"]
        )
        other, _s2 = core.list_other_singles(
            ["Mine.esp", "Vanilla.esp"], index, subset_names=["Mine.esp"]
        )

        assert subset == []
        assert other == []

    def test_other_singles_with_no_subset_returns_every_lone_record(self, data_dir: Path) -> None:
        write_plugin(data_dir / "A.esp", extra=_stat("thing_a"))
        index = PluginFileIndex([str(data_dir)])

        records, _stats = core.list_other_singles(["A.esp"], index, subset_names=[])

        assert [r["id"] for r in records] == ["thing_a"]

    def test_results_sort_by_type_then_id(self, data_dir: Path) -> None:
        write_plugin(data_dir / "A.esp", extra=_stat("z_thing") + _stat("a_thing"))
        index = PluginFileIndex([str(data_dir)])

        records, _stats = core.list_other_singles(["A.esp"], index, subset_names=[])

        assert [r["id"] for r in records] == ["a_thing", "z_thing"]

    def test_a_single_deleted_record_is_reported_deleted(self, data_dir: Path) -> None:
        write_plugin(data_dir / "A.esp", extra=_stat("gone_thing", deleted=True))
        index = PluginFileIndex([str(data_dir)])

        records, _stats = core.list_other_singles(["A.esp"], index, subset_names=[])

        assert records[0]["deleted_by"] == ["A.esp"]


class TestFormatConflictReport:
    def test_no_conflicts_says_so(self) -> None:
        stats = {"scanned": 2, "records": 4, "conflicts": 0, "unreadable": []}
        assert "No conflicts to show." in core.format_conflict_report([], stats)

    def test_summary_line_reports_scanned_records_and_subset_count(self) -> None:
        conflicts = [
            {
                "type": "STAT",
                "id": "a",
                "plugins": ["A.esp", "B.esp"],
                "winner": "B.esp",
                "involves_subset": True,
                "deleted_by": [],
            },
            {
                "type": "STAT",
                "id": "b",
                "plugins": ["A.esp", "C.esp"],
                "winner": "C.esp",
                "involves_subset": False,
                "deleted_by": [],
            },
        ]
        stats = {"scanned": 3, "records": 5, "conflicts": 2, "unreadable": []}

        report = core.format_conflict_report(conflicts, stats)

        assert (
            "Scanned 3 plugin(s), 5 record(s): 2 conflicting record(s), 1 involving your custom mods."
            in report
        )
        assert "[STAT] a" in report
        assert "A.esp  ->  B.esp   (wins: B.esp)" in report

    def test_subset_only_hides_non_subset_conflicts(self) -> None:
        conflicts = [
            {
                "type": "STAT",
                "id": "mine",
                "plugins": ["A.esp"],
                "winner": "A.esp",
                "involves_subset": True,
                "deleted_by": [],
            },
            {
                "type": "STAT",
                "id": "theirs",
                "plugins": ["A.esp"],
                "winner": "A.esp",
                "involves_subset": False,
                "deleted_by": [],
            },
        ]
        stats = {"scanned": 1, "records": 2, "conflicts": 2, "unreadable": []}

        report = core.format_conflict_report(conflicts, stats, subset_only=True)

        assert "[STAT] mine" in report
        assert "[STAT] theirs" not in report

    def test_limit_caps_the_listed_conflicts_and_notes_the_rest(self) -> None:
        conflicts = [
            {
                "type": "STAT",
                "id": str(i),
                "plugins": ["A.esp"],
                "winner": "A.esp",
                "involves_subset": False,
                "deleted_by": [],
            }
            for i in range(5)
        ]
        stats = {"scanned": 1, "records": 5, "conflicts": 5, "unreadable": []}

        report = core.format_conflict_report(conflicts, stats, limit=2)

        assert "[STAT] 0" in report
        assert "[STAT] 1" in report
        assert "[STAT] 2" not in report
        assert "... and 3 more" in report

    def test_unreadable_plugins_get_a_note(self) -> None:
        stats = {"scanned": 1, "records": 1, "conflicts": 0, "unreadable": ["Ghost.esp"]}

        report = core.format_conflict_report([], stats)

        assert "could not be read" in report
        assert "Ghost.esp" in report

    def test_deleted_by_is_shown_when_present(self) -> None:
        conflicts = [
            {
                "type": "STAT",
                "id": "a",
                "plugins": ["A.esp", "B.esp"],
                "winner": "B.esp",
                "involves_subset": False,
                "deleted_by": ["B.esp"],
            }
        ]
        stats = {"scanned": 2, "records": 2, "conflicts": 1, "unreadable": []}

        report = core.format_conflict_report(conflicts, stats)

        assert "deleted by: B.esp" in report


class TestWriteConflictCsv:
    def test_writes_header_and_one_row_per_conflict(self, tmp_path: Path) -> None:
        conflicts = [
            {
                "type": "STAT",
                "id": "torch_01",
                "plugins": ["A.esp", "B.esp"],
                "winner": "B.esp",
                "involves_subset": True,
                "deleted_by": ["B.esp"],
            }
        ]
        out = tmp_path / "conflicts.csv"

        core.write_conflict_csv(out, conflicts)

        with out.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))

        assert rows[0] == [
            "record_type",
            "record_id",
            "winner",
            "involves_custom",
            "deleted_by",
            "plugins_load_order",
        ]
        assert rows[1] == ["STAT", "torch_01", "B.esp", "yes", "B.esp", "A.esp -> B.esp"]

    def test_involves_custom_is_no_when_false(self, tmp_path: Path) -> None:
        conflicts = [
            {
                "type": "STAT",
                "id": "x",
                "plugins": ["A.esp"],
                "winner": "A.esp",
                "involves_subset": False,
                "deleted_by": [],
            }
        ]
        out = tmp_path / "conflicts.csv"

        core.write_conflict_csv(out, conflicts)

        rows = list(csv.reader(out.open(encoding="utf-8", newline="")))
        assert rows[1][3] == "no"
        assert rows[1][4] == ""

    def test_an_empty_conflict_list_writes_only_the_header(self, tmp_path: Path) -> None:
        out = tmp_path / "conflicts.csv"

        core.write_conflict_csv(out, [])

        rows = list(csv.reader(out.open(encoding="utf-8", newline="")))
        assert len(rows) == 1
