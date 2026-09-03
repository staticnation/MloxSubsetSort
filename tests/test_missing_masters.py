"""check_missing_masters: cross-checking every active plugin's TES3 header
masters against the load order itself.

Three distinct warning categories come out of one pass over the active list,
and each has a real consequence attached that the message text has to get
right: [MISSING MASTER] with two different phrasings depending on whether the
master exists anywhere at all (hard launch failure) or is merely disabled
(an easy fix); [MASTER ORDER] for a master that's active but loads too late;
and [MASTER SIZE], whose own hint text branches again on whether the
recorded size is exactly zero (a damaged tes3cmd sync) or just different (a
different version of the master, usually harmless). None of the four
outcomes -- these three plus the clean/no-warnings case -- had coverage
before this file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from conftest import rec, sub, write_plugin, zstr

import wraithguard_toolkit as core
from wraithguard.plugins import PluginFileIndex

if TYPE_CHECKING:
    import pytest


def _index(data_dir: Path) -> PluginFileIndex:
    return PluginFileIndex([str(data_dir)])


class TestCleanLoadOrder:
    def test_every_master_present_and_correctly_ordered_produces_no_warnings(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        master = write_plugin(data_dir / "Morrowind.esm")
        write_plugin(
            data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(master.stat().st_size,)
        )
        order = ["Morrowind.esm", "Mine.esp"]

        missing, order_problems, size_notes, checked, problem_names = core.check_missing_masters(
            order, _index(data_dir)
        )

        assert missing == order_problems == size_notes == []
        assert problem_names == set()
        assert checked == 2

    def test_a_plugin_the_index_cannot_locate_is_skipped_not_counted(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        write_plugin(data_dir / "Morrowind.esm")
        order = ["Morrowind.esm", "GhostPlugin.esp"]  # never written to disk

        _missing, _order, _size, checked, _names = core.check_missing_masters(
            order, _index(data_dir)
        )

        assert checked == 1  # only Morrowind.esm was actually readable


class TestMissingMaster:
    def test_a_master_not_installed_anywhere_gets_the_not_found_phrasing(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        write_plugin(data_dir / "Mine.esp", masters=("Ghost.esm",), sizes=(0,))
        order = ["Mine.esp"]

        missing, _order, _size, _checked, problem_names = core.check_missing_masters(
            order, _index(data_dir)
        )

        assert len(missing) == 1
        assert "NOT FOUND in any data folder" in missing[0]
        assert "Mine.esp" in missing[0] and "Ghost.esm" in missing[0]
        assert problem_names == {"Mine.esp"}

    def test_a_master_installed_but_disabled_gets_the_enable_it_phrasing(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        write_plugin(data_dir / "Required.esm")  # on disk...
        write_plugin(data_dir / "Mine.esp", masters=("Required.esm",), sizes=(0,))
        order = ["Mine.esp"]  # ...but not in the active order

        missing, _order, _size, _checked, problem_names = core.check_missing_masters(
            order, _index(data_dir)
        )

        assert len(missing) == 1
        assert "installed but not in the load order" in missing[0]
        assert problem_names == {"Mine.esp"}

    def test_the_subset_origin_tag_is_included_when_given(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        write_plugin(data_dir / "Mine.esp", masters=("Ghost.esm",), sizes=(0,))
        order = ["Mine.esp"]

        missing, *_rest = core.check_missing_masters(
            order, _index(data_dir), subset_origins={"mine.esp": "MyMod List"}
        )

        assert "[MyMod List]" in missing[0]


class TestMasterOrder:
    def test_a_master_loading_after_its_dependent_is_flagged(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        write_plugin(data_dir / "Morrowind.esm")
        write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(0,))
        # Wrong order: the dependent loads before its own master.
        order = ["Mine.esp", "Morrowind.esm"]

        _missing, order_problems, _size, _checked, problem_names = core.check_missing_masters(
            order, _index(data_dir)
        )

        assert len(order_problems) == 1
        assert "loads BEFORE its master" in order_problems[0]
        assert "Mine.esp" in order_problems[0] and "Morrowind.esm" in order_problems[0]
        assert problem_names == {"Mine.esp"}


class TestMasterSize:
    def test_a_different_recorded_size_gets_the_different_version_hint(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        (data_dir / "Morrowind.esm").write_bytes(b"x" * 500)
        write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(999,))
        order = ["Morrowind.esm", "Mine.esp"]

        _missing, _order, size_notes, _checked, _names = core.check_missing_masters(
            order, _index(data_dir)
        )

        assert len(size_notes) == 1
        assert "999" in size_notes[0] and "500" in size_notes[0]
        assert "different version of the master" in size_notes[0]

    def test_a_recorded_size_of_exactly_zero_gets_the_damaged_sync_hint(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        (data_dir / "Morrowind.esm").write_bytes(b"x" * 500)
        write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(0,))
        order = ["Morrowind.esm", "Mine.esp"]

        _missing, _order, size_notes, _checked, _names = core.check_missing_masters(
            order, _index(data_dir)
        )

        assert len(size_notes) == 1
        assert "likely damaged by a tes3cmd sync" in size_notes[0]

    def test_a_matching_recorded_size_produces_no_note(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        (data_dir / "Morrowind.esm").write_bytes(b"x" * 500)
        write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(500,))
        order = ["Morrowind.esm", "Mine.esp"]

        _missing, _order, size_notes, _checked, _names = core.check_missing_masters(
            order, _index(data_dir)
        )

        assert size_notes == []

    def test_a_missing_master_does_not_also_produce_a_size_note(self, tmp_path: Path) -> None:
        # The size check only runs once a master is confirmed active and
        # positioned -- a master that's flagged [MISSING MASTER] shouldn't
        # also get a spurious [MASTER SIZE] note about itself.
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        write_plugin(data_dir / "Mine.esp", masters=("Ghost.esm",), sizes=(999,))
        order = ["Mine.esp"]

        _missing, _order, size_notes, _checked, _names = core.check_missing_masters(
            order, _index(data_dir)
        )

        assert size_notes == []


class TestNonPluginEntriesAndUnresolvableMasters:
    def test_an_omwscripts_entry_with_no_masters_is_skipped_not_counted(
        self, tmp_path: Path
    ) -> None:
        """.omwscripts is a valid VFS entry (PLUGIN_EXTS) but never carries a master list.

        The extension guard here is narrower than PLUGIN_EXTS on purpose: an
        omwscripts file legitimately has no MAST/DATA pairs, and that must
        not count as "checked" the way an empty .esp genuinely would.
        """
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        write_plugin(data_dir / "Extra.omwscripts")
        order = ["Extra.omwscripts"]

        _missing, _order, _notes, checked, _names = core.check_missing_masters(
            order, _index(data_dir)
        )

        assert checked == 0

    def test_a_master_listed_in_the_order_but_with_no_physical_file_is_skipped(
        self, tmp_path: Path
    ) -> None:
        """A master's name can be in the load order text without ever resolving to a file.

        ``ml in pos`` only checks the load-order *text*, so a broken load
        order that lists a master which was never actually installed must
        not crash the size check when it tries to resolve that master's path.
        """
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        write_plugin(data_dir / "Mine.esp", masters=("GhostMaster.esm",), sizes=(999,))
        order = ["GhostMaster.esm", "Mine.esp"]  # listed, but never installed

        missing, order_problems, size_notes, _checked, _names = core.check_missing_masters(
            order, _index(data_dir)
        )

        assert missing == []
        assert order_problems == []
        assert size_notes == []

    def test_a_master_whose_size_cannot_be_measured_produces_no_note(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stat() failing on an otherwise-resolvable master must not crash the size check."""
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        (data_dir / "Morrowind.esm").write_bytes(b"x" * 500)
        write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(999,))
        order = ["Morrowind.esm", "Mine.esp"]
        index = _index(data_dir)
        index.find("Morrowind.esm")  # force _build() with the real stat, before patching it
        real_stat = Path.stat

        def flaky_stat(self: Path, *args: object, **kwargs: object) -> object:
            if self.name == "Morrowind.esm":
                raise OSError("stat failed")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", flaky_stat)

        _missing, _order, size_notes, _checked, _names = core.check_missing_masters(order, index)

        assert size_notes == []

    def test_a_master_with_no_recorded_size_skips_the_size_check_only(self, tmp_path: Path) -> None:
        """A MAST with no DATA (rec_size is None) is a structurally odd but real header shape.

        Distinct from a recorded size of exactly 0 (test_a_recorded_size_of_
        exactly_zero_gets_the_damaged_sync_hint): here there is nothing to
        compare at all, so the size check must skip cleanly rather than
        treat ``None`` as a size of zero.
        """
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        (data_dir / "Morrowind.esm").write_bytes(b"x" * 500)
        # Two MASTs in a row: the first never gets a paired DATA subrecord.
        body = sub("MAST", zstr("Morrowind.esm")) + sub("MAST", zstr("Morrowind.esm"))
        (data_dir / "Mine.esp").write_bytes(rec("TES3", body))
        order = ["Morrowind.esm", "Mine.esp"]

        missing, order_problems, size_notes, _checked, _names = core.check_missing_masters(
            order, _index(data_dir)
        )

        assert missing == []
        assert order_problems == []
        assert size_notes == []
