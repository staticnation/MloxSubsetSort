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
        index = core.PluginFileIndex([str(data_dir)])

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
        index = core.PluginFileIndex([str(data_dir)])

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
        index = core.PluginFileIndex([str(data_dir)])

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
        index = core.PluginFileIndex([str(data_dir)])

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
        index = core.PluginFileIndex([str(data_dir)])

        core.sync_plugin_master_sizes(plugin, index)
        backup = plugin.with_name(plugin.name + ".masterfix.bak")
        assert backup.read_bytes() == original

        updated, _, error = core.sync_plugin_master_sizes(plugin, index)
        assert error is None and updated == []
        assert backup.read_bytes() == original, "backup was overwritten on re-run"

    def test_unresolved_master_is_left_untouched(self, core, data_dir):
        plugin = write_plugin(data_dir / "Dep.esp", masters=("Nowhere.esm",), sizes=(42,))
        index = core.PluginFileIndex([str(data_dir)])

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
        index = core.PluginFileIndex([str(data_dir)])

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
        index = core.PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(["Cells.esp"], index, subset_names=["Cells.esp"])

        fog = [w for w in warnings if "[FOGBUG]" in w]
        assert len(fog) == 1 and "dark room" in fog[0]

    def test_missing_pathgrid_resolves_across_the_whole_load_order(self, core, data_dir):
        """Improves on the reference script: a grid supplied by any plugin
        counts, not just an earlier one."""
        write_plugin(
            data_dir / "Cell.esp",
            masters=VANILLA,
            extra=interior_cell("shared vault", 0.4) + interior_cell("lonely", 0.4),
        )
        write_plugin(data_dir / "Grid.esp", masters=VANILLA, extra=pathgrid("shared vault"))
        index = core.PluginFileIndex([str(data_dir)])

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
        index = core.PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(
            ["Wolf.esp", "Ok.esp"], index, subset_names=["Wolf.esp", "Ok.esp"]
        )

        dep = [w for w in warnings if "[EXP-DEP]" in w]
        assert len(dep) == 1
        assert "Wolf.esp" in dep[0] and "BecomeWerewolf" in dep[0]
        assert "PlaceAtMe" not in dep[0], "comment text was scanned"

    def test_scripts_twin_mismatch(self, core, data_dir):
        write_plugin(data_dir / "Twin.omwaddon", masters=VANILLA)
        (data_dir / "Twin.omwscripts").write_text("return {}")
        index = core.PluginFileIndex([str(data_dir)])

        warnings, _ = core.lint_plugins(["Twin.omwaddon"], index, subset_names=["Twin.omwaddon"])

        twin = [w for w in warnings if "[TWIN]" in w]
        assert len(twin) == 1 and "Twin.omwscripts" in twin[0]

    def test_vanilla_masters_are_skipped(self, core, data_dir):
        evil = rec("GMST", sub("NAME", zstr("sProfitValue")) + sub("STRV", b"Profit Value"))
        write_plugin(data_dir / "Tribunal.esm", extra=evil)
        index = core.PluginFileIndex([str(data_dir)])

        warnings, stats = core.lint_plugins(["Tribunal.esm"], index)

        assert warnings == []
        assert stats["scanned"] == 0

    def test_blank_header_flagged_for_custom_plugins_only(self, core, data_dir):
        write_plugin(data_dir / "Mine.esp", masters=VANILLA, author="", description="")
        write_plugin(data_dir / "Theirs.esp", masters=VANILLA, author="", description="")
        index = core.PluginFileIndex([str(data_dir)])

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
