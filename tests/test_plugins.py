"""Tests for the TES3 binary readers and the checks built on them.

Covers the master check, the in-app master-size resync (which replaced
tes3cmd's own, because that one corrupts headers on a multi-folder OpenMW
layout), the tes3lint-derived checks, and the savegame dependency check.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from conftest import (
    interior_cell,
    pathgrid,
    rec,
    script_record,
    sub,
    write_plugin,
    zstr,
)

from wraithguard.plugins import PluginFileIndex

VANILLA = ("Morrowind.esm",)


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "Data Files"
    d.mkdir()
    return d


class TestMasterReading:
    def test_reads_master_names(self, core, data_dir):
        plugin = write_plugin(data_dir / "Dep.esp", masters=("Morrowind.esm", "Tribunal.esm"))
        assert core.read_plugin_masters(plugin) == ["Morrowind.esm", "Tribunal.esm"]

    def test_reads_masters_with_recorded_sizes(self, core, data_dir):
        plugin = write_plugin(data_dir / "Dep.esp", masters=("Morrowind.esm",), sizes=(1234,))
        assert core.read_plugin_masters_with_sizes(plugin) == [("Morrowind.esm", 1234)]

    def test_non_tes3_file_yields_nothing(self, core, data_dir):
        junk = data_dir / "notes.omwscripts"
        junk.write_text("return {}")
        assert core.read_plugin_masters(junk) == []

    def test_truncated_file_does_not_raise(self, core, data_dir):
        broken = data_dir / "Broken.esp"
        broken.write_bytes(b"TES3\x10\x00")
        assert core.read_plugin_masters(broken) == []


class TestMasterCheck:
    def test_reports_missing_installed_and_absent_masters(self, core, data_dir):
        write_plugin(data_dir / "Morrowind.esm")
        write_plugin(data_dir / "Disabled.esm")
        write_plugin(
            data_dir / "MyMod.esp",
            masters=("Morrowind.esm", "Disabled.esm", "Gone.esm"),
        )
        index = PluginFileIndex([str(data_dir)])

        missing, order_problems, _sizes, checked, problems = core.check_missing_masters(
            ["Morrowind.esm", "MyMod.esp"], index
        )

        assert checked == 2
        assert not order_problems
        text = "\n".join(missing)
        assert "Disabled.esm" in text and "not in the load order" in text
        assert "Gone.esm" in text and "NOT FOUND" in text
        assert problems == {"MyMod.esp"}

    def test_detects_master_loading_after_its_dependent(self, core, data_dir):
        write_plugin(data_dir / "Late.esm")
        write_plugin(data_dir / "Early.esp", masters=("Late.esm",))
        index = PluginFileIndex([str(data_dir)])

        _missing, order_problems, _sizes, _checked, problems = core.check_missing_masters(
            ["Early.esp", "Late.esm"], index
        )

        assert len(order_problems) == 1
        assert "Late.esm" in order_problems[0]
        assert problems == {"Early.esp"}

    def test_flags_zeroed_size_as_likely_tes3cmd_damage(self, core, data_dir):
        """A failed tes3cmd sync writes 0 sizes; that must be called out."""
        master = write_plugin(data_dir / "Morrowind.esm")
        write_plugin(data_dir / "Dep.esp", masters=("Morrowind.esm",), sizes=(0,))
        index = PluginFileIndex([str(data_dir)])

        _m, _o, size_notes, _c, _p = core.check_missing_masters(["Morrowind.esm", "Dep.esp"], index)

        assert len(size_notes) == 1
        assert "0 bytes" in size_notes[0] and "damaged" in size_notes[0]
        assert str(master.stat().st_size) in size_notes[0]


class TestMasterSizeResync:
    def test_rewrites_only_the_size_fields(self, core, data_dir):
        master = write_plugin(data_dir / "Morrowind.esm", extra=b"J" * 500)
        plugin = write_plugin(
            data_dir / "Dep.esp", masters=("Morrowind.esm",), sizes=(0,), extra=b"K" * 40
        )
        original = plugin.read_bytes()
        index = PluginFileIndex([str(data_dir)])

        updated, unresolved, error = core.sync_plugin_master_sizes(plugin, index)

        assert error is None and not unresolved
        assert updated == [("Morrowind.esm", 0, master.stat().st_size)]
        after = plugin.read_bytes()
        assert len(after) == len(original)
        differing = [i for i, (a, b) in enumerate(zip(original, after)) if a != b]
        assert len(differing) <= 8, "more than the 8-byte size field changed"

    def test_keeps_a_one_time_backup_and_is_idempotent(self, core, data_dir):
        write_plugin(data_dir / "Morrowind.esm", extra=b"J" * 500)
        plugin = write_plugin(data_dir / "Dep.esp", masters=("Morrowind.esm",), sizes=(0,))
        original = plugin.read_bytes()
        index = PluginFileIndex([str(data_dir)])

        core.sync_plugin_master_sizes(plugin, index)
        backup = plugin.with_name(plugin.name + ".masterfix.bak")
        assert backup.read_bytes() == original

        updated, _, error = core.sync_plugin_master_sizes(plugin, index)
        assert error is None and updated == []
        assert backup.read_bytes() == original, "backup was overwritten on re-run"

    def test_unresolved_master_is_left_untouched(self, core, data_dir):
        plugin = write_plugin(data_dir / "Dep.esp", masters=("Nowhere.esm",), sizes=(42,))
        index = PluginFileIndex([str(data_dir)])

        updated, unresolved, error = core.sync_plugin_master_sizes(plugin, index)

        assert error is None and updated == []
        assert unresolved == ["Nowhere.esm"]
        assert core.read_plugin_masters_with_sizes(plugin) == [("Nowhere.esm", 42)]

    def test_refuses_a_non_tes3_file(self, core, data_dir):
        script = data_dir / "thing.omwscripts"
        script.write_text("return {}")
        _updated, _unresolved, error = core.sync_plugin_master_sizes(script, data_dir)
        assert error is not None and "not a TES3" in error


class TestLintChecks:
    def test_evil_gmst_flagged_only_on_exact_value_match(self, core, data_dir):
        """A mod deliberately changing one of these settings is legitimate."""
        evil = rec("GMST", sub("NAME", zstr("sProfitValue")) + sub("STRV", b"Profit Value"))
        changed = rec("GMST", sub("NAME", zstr("sDeleteNote")) + sub("STRV", b"My Own Text"))
        write_plugin(data_dir / "Dirty.esp", masters=VANILLA, extra=evil + changed)
        index = PluginFileIndex([str(data_dir)])

        warnings, _stats = core.lint_plugins(["Dirty.esp"], index, subset_names=["Dirty.esp"])

        evil_lines = [w for w in warnings if "[EVLGMST]" in w]
        assert len(evil_lines) == 1
        assert "sprofitvalue" in evil_lines[0]
        assert "sdeletenote" not in evil_lines[0]

    def test_fog_bug_and_behave_like_exterior_exemption(self, core, data_dir):
        write_plugin(
            data_dir / "Cells.esp",
            masters=VANILLA,
            extra=interior_cell("dark room", 0.0)
            + interior_cell("lit room", 0.7)
            + interior_cell("openish", 0.0, flags=1 | 128)  # behaves as exterior
            + pathgrid("dark room")
            + pathgrid("lit room")
            + pathgrid("openish"),
        )
        index = PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(["Cells.esp"], index, subset_names=["Cells.esp"])

        fog = [w for w in warnings if "[FOGBUG]" in w]
        assert len(fog) == 1 and "dark room" in fog[0]

    def test_an_exterior_cell_is_not_fog_checked_and_does_not_crash_the_scan(self, core, data_dir):
        """_lint_cell returns None for an exterior cell; the scan must just move past it."""
        exterior = rec(
            "CELL",
            sub("NAME", zstr("Seyda Neen"))
            + sub("DATA", struct.pack("<iii", 0, 1, 1)),  # flags=0: exterior
        )
        write_plugin(data_dir / "Ext.esp", masters=VANILLA, extra=exterior)
        index = PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(["Ext.esp"], index, subset_names=["Ext.esp"])

        assert not [w for w in warnings if "[FOGBUG]" in w]

    def test_an_exterior_pathgrid_does_not_count_toward_missing_interior_pathgrids(
        self, core, data_dir
    ):
        """A PGRD for grid != (0, 0) is exterior; it must not satisfy an interior's requirement."""
        write_plugin(data_dir / "Cell.esp", masters=VANILLA, extra=interior_cell("lonely", 0.4))
        write_plugin(data_dir / "Grid.esp", masters=VANILLA, extra=pathgrid("elsewhere", 2, 3))
        index = PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(
            ["Cell.esp", "Grid.esp"], index, subset_names=["Cell.esp", "Grid.esp"]
        )

        missing = [w for w in warnings if "[NO PATHGRID]" in w]
        assert len(missing) == 1 and "lonely" in missing[0]

    def test_missing_pathgrid_resolves_across_the_whole_load_order(self, core, data_dir):
        """Improves on the reference script: a grid supplied by any plugin
        counts, not just an earlier one."""
        write_plugin(
            data_dir / "Cell.esp",
            masters=VANILLA,
            extra=interior_cell("shared vault", 0.4) + interior_cell("lonely", 0.4),
        )
        write_plugin(data_dir / "Grid.esp", masters=VANILLA, extra=pathgrid("shared vault"))
        index = PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(
            ["Cell.esp", "Grid.esp"], index, subset_names=["Cell.esp", "Grid.esp"]
        )

        missing = [w for w in warnings if "[NO PATHGRID]" in w]
        assert len(missing) == 1 and "lonely" in missing[0]

    def test_expansion_functions_without_the_master(self, core, data_dir):
        write_plugin(
            data_dir / "Wolf.esp",
            masters=VANILLA,
            extra=script_record("s", "begin s\n; PlaceAtMe in a comment\nBecomeWerewolf\nend"),
        )
        write_plugin(
            data_dir / "Ok.esp",
            masters=("Morrowind.esm", "Bloodmoon.esm"),
            extra=script_record("t", "begin t\nBecomeWerewolf\nend"),
        )
        index = PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(
            ["Wolf.esp", "Ok.esp"], index, subset_names=["Wolf.esp", "Ok.esp"]
        )

        dep = [w for w in warnings if "[EXP-DEP]" in w]
        assert len(dep) == 1
        assert "Wolf.esp" in dep[0] and "BecomeWerewolf" in dep[0]
        assert "PlaceAtMe" not in dep[0], "comment text was scanned"

    def test_tribunal_functions_without_the_master(self, core, data_dir):
        """Mirrors test_expansion_functions_without_the_master for the Tribunal side."""
        write_plugin(
            data_dir / "Assassins.esp",
            masters=VANILLA,
            extra=script_record("s", "begin s\nAddToLevCreature\nend"),
        )
        index = PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(["Assassins.esp"], index, subset_names=["Assassins.esp"])

        dep = [w for w in warnings if "[EXP-DEP]" in w]
        assert len(dep) == 1
        assert "Assassins.esp" in dep[0] and "AddToLevCreature" in dep[0]

    def test_scripts_twin_mismatch(self, core, data_dir):
        write_plugin(data_dir / "Twin.omwaddon", masters=VANILLA)
        (data_dir / "Twin.omwscripts").write_text("return {}")
        index = PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(["Twin.omwaddon"], index, subset_names=["Twin.omwaddon"])

        twin = [w for w in warnings if "[TWIN]" in w]
        assert len(twin) == 1 and "Twin.omwscripts" in twin[0]

    def test_omwscripts_twin_mismatch_the_other_direction(self, core, data_dir):
        """An active .omwscripts with an un-loaded .esp/.omwaddon twin is flagged too."""
        (data_dir / "Twin.omwscripts").write_text("return {}")
        write_plugin(data_dir / "Twin.esp", masters=VANILLA)
        index = PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(
            ["Twin.omwscripts"], index, subset_names=["Twin.omwscripts"]
        )

        twin = [w for w in warnings if "[TWIN]" in w]
        assert len(twin) == 1 and "Twin.esp" in twin[0]
        assert "scripts may reference content that never loads" in twin[0]

    def test_a_subset_plugin_the_index_cannot_resolve_is_skipped_by_the_twin_check(
        self, core, data_dir
    ):
        """A load-order entry that doesn't actually resolve to a file has no twin to check."""
        index = PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(["Ghost.esp"], index, subset_names=["Ghost.esp"])

        assert not [w for w in warnings if "[TWIN]" in w]

    def test_an_omwscripts_plugin_with_neither_twin_present_gets_no_warning(self, core, data_dir):
        """Both candidate extensions are checked; neither existing must not raise or warn."""
        (data_dir / "Standalone.omwscripts").write_text("return {}")
        index = PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(
            ["Standalone.omwscripts"], index, subset_names=["Standalone.omwscripts"]
        )

        assert not [w for w in warnings if "[TWIN]" in w]

    def test_a_non_esp_non_omwscripts_subset_plugin_has_no_twin_check_at_all(self, core, data_dir):
        """.esm (a master) is neither of the two extensions the twin check looks at."""
        write_plugin(data_dir / "Base.esm")
        index = PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(["Base.esm"], index, subset_names=["Base.esm"])

        assert not [w for w in warnings if "[TWIN]" in w]

    def test_vanilla_masters_are_skipped(self, core, data_dir):
        evil = rec("GMST", sub("NAME", zstr("sProfitValue")) + sub("STRV", b"Profit Value"))
        write_plugin(data_dir / "Tribunal.esm", extra=evil)
        index = PluginFileIndex([str(data_dir)])

        warnings, stats = core.lint_plugins(["Tribunal.esm"], index)

        assert warnings == []
        assert stats["scanned"] == 0

    def test_blank_header_flagged_for_custom_plugins_only(self, core, data_dir):
        write_plugin(data_dir / "Mine.esp", masters=VANILLA, author="", description="")
        write_plugin(data_dir / "Theirs.esp", masters=VANILLA, author="", description="")
        index = PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(
            ["Mine.esp", "Theirs.esp"], index, subset_names=["Mine.esp"]
        )

        header = [w for w in warnings if "[HEADER]" in w]
        assert len(header) == 1 and "Mine.esp" in header[0]


class TestSavegameCheck:
    def _save(self, path: Path, content_files: tuple[str, ...]) -> Path:
        body = sub("PLNA", zstr("Tester")) + sub("PLLE", struct.pack("<i", 12))
        for name in content_files:
            body += sub("DEPE", name.encode())
        body += sub("SCRN", b"\xff\xd8jpeg")
        path.write_bytes(rec("TES3", sub("HEDR", b"\x00" * 300)) + rec("SAVE", body))
        return path

    def test_missing_dependency_is_reported(self, core, tmp_path):
        save = self._save(tmp_path / "char.omwsave", ("Morrowind.esm", "Gone.esp"))
        files, missing, error = core.check_savegame_against_order(save, ["Morrowind.esm"])
        assert error is None
        assert files == ["Morrowind.esm", "Gone.esp"]
        assert missing == ["Gone.esp"]

    def test_all_present(self, core, tmp_path):
        save = self._save(tmp_path / "char.omwsave", ("Morrowind.esm",))
        _files, missing, error = core.check_savegame_against_order(save, ["Morrowind.esm"])
        assert error is None and missing == []

    def test_non_save_file_reports_an_error(self, core, tmp_path):
        bogus = tmp_path / "bogus.omwsave"
        bogus.write_bytes(b"NOPE")
        _files, _missing, error = core.check_savegame_against_order(bogus, [])
        assert error is not None


class TestBackupScanner:
    def test_finds_every_backup_flavour_and_links_originals(self, core, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "A.esp").write_text("x")
        (tmp_path / "A.esp.preclean.bak").write_text("x")
        (tmp_path / "B.esp.masterfix.bak").write_text("x")
        (tmp_path / "sub" / "C.esp").write_text("x")
        (tmp_path / "sub" / "C~1.esp").write_text("x")
        (tmp_path / "cfg.toml.bak-20260101-120000").write_text("x")
        (tmp_path / "openmw.cfg.backup.999").write_text("x")
        (tmp_path / "NotABackup.esp").write_text("x")

        found = core.scan_backups([str(tmp_path)])

        kinds = sorted(kind for _p, _o, kind in found)
        assert kinds == [
            "configurator .backup",
            "masterfix.bak",
            "preclean.bak",
            "tes3cmd ~N",
            "timestamped .bak",
        ]
        preclean = next(entry for entry in found if entry[2] == "preclean.bak")
        assert Path(preclean[1]).name == "A.esp"

    def test_original_is_reported_even_when_it_no_longer_exists(self, core, tmp_path):
        """Restoring a backup whose original was deleted is valid recovery, so
        the original path is always reported (the caller renders its own
        'original missing' marker)."""
        (tmp_path / "Gone.esp.preclean.bak").write_text("backup contents")

        found = core.scan_backups([str(tmp_path)])

        assert len(found) == 1
        _backup_path, original, kind = found[0]
        assert kind == "preclean.bak"
        assert original is not None and Path(original).name == "Gone.esp"
        assert not Path(original).exists()


class TestPluginFileIndexResolution:
    """A name several data folders provide resolves to the latest one.

    OpenMW lets a later ``data=`` line shadow an earlier one, so a plugin split
    across ``00 Core`` and ``01 Patch`` must resolve to the patch copy. The
    index once kept the first copy (``setdefault``), which fed every downstream
    read -- tes3conv JSON, the conflict scan, Merged Lands -- the pre-patch
    file. These pin the corrected last-wins behaviour.
    """

    def test_the_last_data_folder_wins(self, tmp_path: Path) -> None:
        """Directories arrive in load order; the latest copy is returned."""
        core = tmp_path / "00 Core"
        patch = tmp_path / "01 Patch"
        core.mkdir()
        patch.mkdir()
        write_plugin(core / "Dwemer Airship_Exterior.esp")
        write_plugin(patch / "Dwemer Airship_Exterior.esp")

        index = PluginFileIndex([str(core), str(patch)])

        assert index.find("Dwemer Airship_Exterior.esp") == patch / "Dwemer Airship_Exterior.esp"

    def test_a_single_provider_is_unaffected(self, tmp_path: Path) -> None:
        """The common case -- one folder owns the plugin -- still resolves."""
        core = tmp_path / "00 Core"
        core.mkdir()
        write_plugin(core / "Solo.esp")

        index = PluginFileIndex([str(core)])

        assert index.find("Solo.esp") == core / "Solo.esp"

    def test_case_only_collision_still_takes_the_later_folder(self, tmp_path: Path) -> None:
        """Last-wins holds when the two copies differ only in filename case."""
        core = tmp_path / "00 Core"
        patch = tmp_path / "01 Patch"
        core.mkdir()
        patch.mkdir()
        write_plugin(core / "Foo.ESP")
        write_plugin(patch / "Foo.esp")

        index = PluginFileIndex([str(core), str(patch)])

        assert index.find("Foo.esp") == patch / "Foo.esp"


class TestPluginPaths:
    """The plugin -> path map the diff/patch features read, built without a scan.

    It is only a last-wins PluginFileIndex lookup per name, so establishing it
    on demand is what lets the field diff and tree view open without first
    running Check Conflicts.
    """

    def test_resolves_every_present_plugin(self, core, tmp_path: Path) -> None:
        core_dir = tmp_path / "00 Core"
        core_dir.mkdir()
        write_plugin(core_dir / "A.esp")
        write_plugin(core_dir / "B.esp")
        index = PluginFileIndex([str(core_dir)])

        paths = core.plugin_paths(["A.esp", "B.esp"], index)

        assert set(paths) == {"A.esp", "B.esp"}
        assert paths["A.esp"] == str(core_dir / "A.esp")

    def test_omits_a_plugin_the_folders_do_not_hold(self, core, tmp_path: Path) -> None:
        core_dir = tmp_path / "00 Core"
        core_dir.mkdir()
        write_plugin(core_dir / "Here.esp")
        index = PluginFileIndex([str(core_dir)])

        paths = core.plugin_paths(["Here.esp", "Gone.esp"], index)

        assert set(paths) == {"Here.esp"}

    def test_takes_the_last_data_folder_for_a_shared_name(self, core, tmp_path: Path) -> None:
        """It inherits PluginFileIndex's last-wins, matching OpenMW."""
        core_dir = tmp_path / "00 Core"
        patch_dir = tmp_path / "01 Patch"
        core_dir.mkdir()
        patch_dir.mkdir()
        write_plugin(core_dir / "Shared.esp")
        write_plugin(patch_dir / "Shared.esp")
        index = PluginFileIndex([str(core_dir), str(patch_dir)])

        paths = core.plugin_paths(["Shared.esp"], index)

        assert paths["Shared.esp"] == str(patch_dir / "Shared.esp")
