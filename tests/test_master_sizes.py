"""read_plugin_masters, read_plugin_masters_with_sizes, and sync_plugin_master_sizes.

All three read (or rewrite) the MAST/DATA pairs in a TES3 header -- the
master-file dependency list every plugin declares, and the recorded size
tes3cmd's own ``--synchronize`` gets wrong in a multi-folder OpenMW layout
(per this function's own docstring: it resolves each master across every
data folder via the index, rather than assuming one flat Data Files folder).
None of the three had any coverage before this file.
"""

from __future__ import annotations

import shutil
import struct
from pathlib import Path
from typing import TYPE_CHECKING

from conftest import rec, sub, write_plugin, zstr

import wraithguard_toolkit as core
from wraithguard.plugins import PluginFileIndex

if TYPE_CHECKING:
    import pytest


class TestReadPluginMasters:
    def test_the_masters_a_plugin_declares_are_returned_in_order(self, tmp_path: Path) -> None:
        path = write_plugin(tmp_path / "Mine.esp", masters=("Morrowind.esm", "Tribunal.esm"))

        assert core.read_plugin_masters(path) == ["Morrowind.esm", "Tribunal.esm"]

    def test_a_plugin_with_no_masters_returns_an_empty_list(self, tmp_path: Path) -> None:
        path = write_plugin(tmp_path / "Standalone.esp")

        assert core.read_plugin_masters(path) == []

    def test_a_non_tes3_file_returns_an_empty_list(self, tmp_path: Path) -> None:
        path = tmp_path / "not-a-plugin.esp"
        path.write_bytes(b"this is not a TES3 file")

        assert core.read_plugin_masters(path) == []

    def test_a_missing_file_returns_an_empty_list_not_an_exception(self, tmp_path: Path) -> None:
        assert core.read_plugin_masters(tmp_path / "does-not-exist.esp") == []


class TestReadPluginMastersWithSizes:
    def test_masters_are_paired_with_their_recorded_sizes(self, tmp_path: Path) -> None:
        path = write_plugin(
            tmp_path / "Mine.esp",
            masters=("Morrowind.esm", "Tribunal.esm"),
            sizes=(79837557, 4234906),
        )

        assert core.read_plugin_masters_with_sizes(path) == [
            ("Morrowind.esm", 79837557),
            ("Tribunal.esm", 4234906),
        ]

    def test_a_master_with_no_data_subrecord_pairs_with_none(self, tmp_path: Path) -> None:
        # A MAST with nothing after it at all -- the header ends mid-pair.
        body = sub(
            "HEDR",
            struct.pack("<fi", 1.3, 0)
            + zstr("tester", 32)
            + zstr("fixture", 256)
            + struct.pack("<i", 1),
        ) + sub("MAST", zstr("Orphan.esm"))
        path = tmp_path / "trailing.esp"
        path.write_bytes(rec("TES3", body))

        assert core.read_plugin_masters_with_sizes(path) == [("Orphan.esm", None)]

    def test_two_masts_in_a_row_pair_the_first_with_none(self, tmp_path: Path) -> None:
        # MAST, MAST, DATA -- the first master's DATA never comes; only the
        # second gets paired with the size that follows it.
        body = sub(
            "HEDR",
            struct.pack("<fi", 1.3, 0)
            + zstr("tester", 32)
            + zstr("fixture", 256)
            + struct.pack("<i", 1),
        )
        body += sub("MAST", zstr("First.esm"))
        body += sub("MAST", zstr("Second.esm"))
        body += sub("DATA", struct.pack("<Q", 12345))
        path = tmp_path / "double-mast.esp"
        path.write_bytes(rec("TES3", body))

        assert core.read_plugin_masters_with_sizes(path) == [
            ("First.esm", None),
            ("Second.esm", 12345),
        ]

    def test_a_non_tes3_file_returns_an_empty_list(self, tmp_path: Path) -> None:
        path = tmp_path / "not-a-plugin.esp"
        path.write_bytes(b"garbage")

        assert core.read_plugin_masters_with_sizes(path) == []


class TestSyncPluginMasterSizes:
    def test_a_wrong_recorded_size_is_corrected_and_reported(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        master = data_dir / "Morrowind.esm"
        master.write_bytes(b"x" * 500)  # the real, current size
        plugin = write_plugin(
            data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(999,)  # stale/wrong
        )
        index = PluginFileIndex([str(data_dir)])

        updated, unresolved, error = core.sync_plugin_master_sizes(plugin, index)

        assert error is None
        assert updated == [("Morrowind.esm", 999, 500)]
        assert unresolved == []
        # The header on disk now records the corrected size.
        assert core.read_plugin_masters_with_sizes(plugin) == [("Morrowind.esm", 500)]

    def test_an_already_correct_size_is_left_alone(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        master = data_dir / "Morrowind.esm"
        master.write_bytes(b"x" * 500)
        plugin = write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(500,))
        index = PluginFileIndex([str(data_dir)])

        updated, unresolved, error = core.sync_plugin_master_sizes(plugin, index)

        assert updated == []
        assert unresolved == []
        assert error is None

    def test_a_master_the_index_cannot_find_is_reported_unresolved(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        plugin = write_plugin(data_dir / "Mine.esp", masters=("MissingMaster.esm",), sizes=(999,))
        index = PluginFileIndex([str(data_dir)])

        updated, unresolved, error = core.sync_plugin_master_sizes(plugin, index)

        assert updated == []
        assert unresolved == ["MissingMaster.esm"]
        assert error is None

    def test_a_non_tes3_file_reports_an_error_without_writing_anything(
        self, tmp_path: Path
    ) -> None:
        plugin = tmp_path / "not-a-plugin.esp"
        original_bytes = b"this is not a TES3 file at all, at all"
        plugin.write_bytes(original_bytes)
        index = PluginFileIndex([str(tmp_path)])

        updated, unresolved, error = core.sync_plugin_master_sizes(plugin, index)

        assert updated == []
        assert unresolved == []
        assert error == "not a TES3 plugin (no TES3 header)"
        assert plugin.read_bytes() == original_bytes  # untouched

    def test_a_backup_is_written_before_the_first_change(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        (data_dir / "Morrowind.esm").write_bytes(b"x" * 500)
        plugin = write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(999,))
        original_bytes = plugin.read_bytes()
        index = PluginFileIndex([str(data_dir)])

        core.sync_plugin_master_sizes(plugin, index, make_backup=True)

        backup = plugin.with_name(plugin.name + ".masterfix.bak")
        assert backup.exists()
        assert backup.read_bytes() == original_bytes  # the pre-fix original

    def test_make_backup_false_still_updates_but_writes_no_backup(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        (data_dir / "Morrowind.esm").write_bytes(b"x" * 500)
        plugin = write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(999,))
        index = PluginFileIndex([str(data_dir)])

        updated, _unresolved, _error = core.sync_plugin_master_sizes(
            plugin, index, make_backup=False
        )

        assert updated == [("Morrowind.esm", 999, 500)]
        assert not plugin.with_name(plugin.name + ".masterfix.bak").exists()

    def test_an_existing_backup_is_not_overwritten(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        (data_dir / "Morrowind.esm").write_bytes(b"x" * 500)
        plugin = write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(999,))
        backup = plugin.with_name(plugin.name + ".masterfix.bak")
        backup.write_bytes(b"a much earlier backup, from a previous run")
        index = PluginFileIndex([str(data_dir)])

        core.sync_plugin_master_sizes(plugin, index, make_backup=True)

        assert backup.read_bytes() == b"a much earlier backup, from a previous run"

    def test_a_missing_file_reports_the_read_error(self, tmp_path: Path) -> None:
        index = PluginFileIndex([str(tmp_path)])

        updated, unresolved, error = core.sync_plugin_master_sizes(
            tmp_path / "does-not-exist.esp", index
        )

        assert updated == []
        assert unresolved == []
        assert error is not None and "can't read" in error

    def test_a_data_subrecord_shorter_than_8_bytes_is_left_alone(self, tmp_path: Path) -> None:
        """A malformed DATA field (too short to even hold a size) is skipped, not crashed on."""
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        (data_dir / "Morrowind.esm").write_bytes(b"x" * 500)
        body = sub("MAST", zstr("Morrowind.esm")) + sub("DATA", struct.pack("<I", 999))
        plugin = data_dir / "Mine.esp"
        plugin.write_bytes(rec("TES3", body))
        index = PluginFileIndex([str(data_dir)])

        updated, unresolved, error = core.sync_plugin_master_sizes(plugin, index)

        assert updated == []
        assert unresolved == []
        assert error is None

    def test_a_master_whose_size_cannot_be_measured_is_left_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stat() failing on a resolved master (e.g. a permissions race) must not crash the fix."""
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        (data_dir / "Morrowind.esm").write_bytes(b"x" * 500)
        plugin = write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(999,))
        index = PluginFileIndex([str(data_dir)])
        index.find("Morrowind.esm")  # force _build() with the real stat, before patching it
        real_stat = Path.stat

        def flaky_stat(self: Path, *args: object, **kwargs: object) -> object:
            if self.name == "Morrowind.esm":
                raise OSError("stat failed")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", flaky_stat)

        updated, unresolved, error = core.sync_plugin_master_sizes(plugin, index)

        assert updated == []
        assert unresolved == []
        assert error is None

    def test_a_backup_write_failure_aborts_before_touching_the_plugin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the safety backup can't be written, the plugin itself must stay untouched."""
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        (data_dir / "Morrowind.esm").write_bytes(b"x" * 500)
        plugin = write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(999,))
        original_bytes = plugin.read_bytes()
        index = PluginFileIndex([str(data_dir)])

        def failing_copy2(*_a: object, **_k: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(shutil, "copy2", failing_copy2)

        updated, _unresolved, error = core.sync_plugin_master_sizes(plugin, index)

        assert updated == []
        assert error is not None and "couldn't write backup" in error
        assert plugin.read_bytes() == original_bytes  # not modified

    def test_a_plugin_write_failure_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The final rewrite can fail too (e.g. the plugin became read-only mid-run)."""
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        (data_dir / "Morrowind.esm").write_bytes(b"x" * 500)
        plugin = write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(999,))
        index = PluginFileIndex([str(data_dir)])

        def failing_write_bytes(self: Path, *_a: object, **_k: object) -> int:
            raise OSError("read-only file system")

        monkeypatch.setattr(Path, "write_bytes", failing_write_bytes)

        updated, _unresolved, error = core.sync_plugin_master_sizes(
            plugin, index, make_backup=False
        )

        assert updated == []
        assert error is not None and "couldn't write plugin" in error
