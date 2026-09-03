"""Tests for the pure parts of ``tools/survey_landscape.py``.

The load-order and pre-scan helpers decide which plugins are read and in what
order, and both can be wrong silently: a mis-ordered survey mistranslates
texture indices, and a pre-scan that skips the wrong file reports terrain as
unedited. Neither failure raises, so both are tested directly.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.survey_landscape import (
    _from_dump,
    _from_plugins,
    apply_order,
    main,
    mentions_landscape,
    read_json_records,
    read_order,
    to_records,
)
from wraithguard.land.emit import build_landscape_record


class TestReadOrder:
    """Load order files come from mlox, mod managers and hand editing."""

    def test_names_are_read_in_order(self, tmp_path: Path) -> None:
        """The file's order is the load order."""
        path = tmp_path / "order.txt"
        path.write_text("B.esp\nA.esp\n", encoding="utf-8")
        assert read_order(path) == ["b.esp", "a.esp"]

    def test_comments_and_blanks_are_ignored(self, tmp_path: Path) -> None:
        """mlox output carries both, and neither is a plugin."""
        path = tmp_path / "order.txt"
        path.write_text("# a comment\n\nA.esp\n   \n", encoding="utf-8")
        assert read_order(path) == ["a.esp"]

    def test_paths_are_reduced_to_names(self, tmp_path: Path) -> None:
        """A list of full paths is still a load order."""
        path = tmp_path / "order.txt"
        path.write_text("C:/Games/Data Files/A.esp\n", encoding="utf-8")
        assert read_order(path) == ["a.esp"]

    def test_a_missing_file_stops_the_run(self, tmp_path: Path) -> None:
        """Silently surveying alphabetically after being given an order would
        produce a wrong answer that looks like the requested one."""
        with pytest.raises(SystemExit):
            read_order(tmp_path / "nope.txt")

    def test_openmw_cfg_content_lines(self, tmp_path: Path) -> None:
        """An openmw.cfg is what users actually have, so it must work directly."""
        path = tmp_path / "openmw.cfg"
        path.write_text(
            "encoding=win1252\n"
            "fallback=lightattenuation_useconstant,1\n"
            "data=C:/Games/Data Files\n"
            "content=Morrowind.esm\n"
            "content=Tribunal.esm\n"
            "content=Some Mod.esp\n",
            encoding="utf-8",
        )
        assert read_order(path) == ["morrowind.esm", "tribunal.esm", "some mod.esp"]

    def test_settings_lines_are_not_mistaken_for_plugins(self, tmp_path: Path) -> None:
        """The bug this guards against, reproduced.

        Taking every non-comment line as a plugin turned a real openmw.cfg into
        2,836 "plugins" -- ``encoding=win1252`` and friends -- none of which
        matched anything. The load order silently had no effect *and* suppressed
        the "no order given" warning, so the run merged alphabetically while
        reporting that it had not.
        """
        path = tmp_path / "openmw.cfg"
        path.write_text("encoding=win1252\nfallback=x,1\ncontent=Real.esp\n", encoding="utf-8")
        assert read_order(path) == ["real.esp"]

    def test_morrowind_ini_game_files(self, tmp_path: Path) -> None:
        """The other config people have."""
        path = tmp_path / "Morrowind.ini"
        path.write_text(
            "[Game Files]\nGameFile0=Morrowind.esm\nGameFile1=Mod.esp\n", encoding="utf-8"
        )
        assert read_order(path) == ["morrowind.esm", "mod.esp"]

    @pytest.mark.parametrize("suffix", [".esm", ".esp", ".omwaddon", ".omwgame"])
    def test_every_plugin_extension_is_recognised(self, tmp_path: Path, suffix: str) -> None:
        """OpenMW's own names for the format count too."""
        path = tmp_path / "order.txt"
        path.write_text(f"Thing{suffix}\n", encoding="utf-8")
        assert read_order(path) == [f"thing{suffix}"]

    def test_a_file_with_no_plugins_is_refused(self, tmp_path: Path) -> None:
        """Refusing beats proceeding: an empty order silently means alphabetical."""
        path = tmp_path / "openmw.cfg"
        path.write_text("encoding=win1252\nfallback=x,1\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="no plugin entries"):
            read_order(path)


class TestApplyOrder:
    """Sorting found plugins by a declared order."""

    def test_the_declared_order_wins(self) -> None:
        """Alphabetical order is discarded in favour of the load order."""
        assert apply_order(["A", "B"], ["b.esp", "a.esp"]) == ["B", "A"]

    def test_unlisted_plugins_go_last_and_are_kept(self) -> None:
        """An incomplete load order narrows the guesswork; it does not drop data."""
        assert apply_order(["A", "B", "Z"], ["z.esp"]) == ["Z", "A", "B"]

    def test_matching_ignores_case(self) -> None:
        """Windows file names and mlox output disagree on case constantly."""
        assert apply_order(["Alpha"], ["ALPHA.ESP"]) == ["Alpha"]

    def test_an_empty_order_leaves_the_input_order(self) -> None:
        """Nothing declared means nothing reordered."""
        assert apply_order(["B", "A"], []) == ["A", "B"]


class TestMentionsLandscape:
    """The pre-scan that skips plugins holding no terrain."""

    def test_a_landscape_record_is_found(self, tmp_path: Path) -> None:
        """The common case."""
        path = tmp_path / "a.json"
        path.write_text('[{"type": "Landscape", "grid": [0, 0]}]', encoding="utf-8")
        assert mentions_landscape(path)

    def test_a_land_texture_is_found(self, tmp_path: Path) -> None:
        """``LandscapeTexture`` contains the marker, so one check covers both."""
        path = tmp_path / "a.json"
        path.write_text('[{"type": "LandscapeTexture", "id": "x"}]', encoding="utf-8")
        assert mentions_landscape(path)

    def test_a_plugin_without_terrain_is_skipped(self, tmp_path: Path) -> None:
        """The whole point: do not parse a hundred megabytes to find nothing."""
        path = tmp_path / "a.json"
        path.write_text('[{"type": "Static", "id": "rock"}]', encoding="utf-8")
        assert not mentions_landscape(path)

    def test_the_marker_is_found_across_a_chunk_boundary(self, tmp_path: Path) -> None:
        """A word split between two reads must still be found.

        This is the failure the overlap exists to prevent, and it would show up
        only on files of particular sizes -- so it is pinned here rather than
        left to chance.
        """
        path = tmp_path / "a.json"
        filler = "x" * ((1 << 20) - 4)
        path.write_text(f"{filler}Landscape rest", encoding="utf-8")
        assert mentions_landscape(path)

    def test_a_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        """An unreadable file is reported by the caller, not raised here."""
        assert not mentions_landscape(tmp_path / "gone.json")


class TestReadJsonRecords:
    """Reading an already-converted plugin."""

    def test_a_record_list_is_returned(self, tmp_path: Path) -> None:
        """The normal case."""
        path = tmp_path / "a.json"
        path.write_text('[{"type": "Static"}]', encoding="utf-8")
        assert read_json_records(path) == [{"type": "Static"}]

    @pytest.mark.parametrize("content", ["not json", '{"type": "Static"}', ""])
    def test_unusable_content_yields_nothing(self, tmp_path: Path, content: str) -> None:
        """Malformed, or valid JSON that is not a record list."""
        path = tmp_path / "a.json"
        path.write_text(content, encoding="utf-8")
        assert read_json_records(path) == []

    def test_a_missing_file_yields_nothing(self, tmp_path: Path) -> None:
        """One unreadable plugin must not stop a survey."""
        assert read_json_records(tmp_path / "gone.json") == []


def _heights(fill: float) -> list[list[float]]:
    return [[fill] * 65 for _ in range(65)]


class TestToRecords:
    def test_a_subprocess_failure_yields_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        def _boom(*_a: object, **_k: object) -> None:
            raise OSError("simulated: tes3conv is not runnable")

        monkeypatch.setattr(subprocess, "run", _boom)
        plugin = tmp_path / "a.esp"
        plugin.write_bytes(b"\x00")

        assert to_records("tes3conv", plugin, tmp_path) == []

    def test_a_nonzero_exit_yields_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        def fake_run(*_a: object, **_k: object) -> types.SimpleNamespace:
            return types.SimpleNamespace(returncode=1)

        monkeypatch.setattr(subprocess, "run", fake_run)
        plugin = tmp_path / "a.esp"
        plugin.write_bytes(b"\x00")

        assert to_records("tes3conv", plugin, tmp_path) == []

    def test_a_non_list_json_yields_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            Path(argv[2]).write_text('{"not": "a list"}', encoding="utf-8")
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        plugin = tmp_path / "a.esp"
        plugin.write_bytes(b"\x00")

        assert to_records("tes3conv", plugin, tmp_path) == []

    def test_a_success_returns_the_records_and_cleans_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            Path(argv[2]).write_text('[{"type": "Static"}]', encoding="utf-8")
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        plugin = tmp_path / "a.esp"
        plugin.write_bytes(b"\x00")

        records = to_records("tes3conv", plugin, tmp_path)

        assert records == [{"type": "Static"}]
        assert not (tmp_path / "a.json").exists()

    def test_unparseable_json_output_yields_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            Path(argv[2]).write_text("{not json", encoding="utf-8")
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        plugin = tmp_path / "a.esp"
        plugin.write_bytes(b"\x00")

        assert to_records("tes3conv", plugin, tmp_path) == []


class TestFromDump:
    def test_not_a_directory_stops_the_run(self, tmp_path: Path, capsys) -> None:
        loaded, _mods, _unreadable = _from_dump(tmp_path / "gone", [], 0)
        assert loaded is None
        assert "not a directory" in capsys.readouterr().err

    def test_no_masters_present_stops_the_run(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "SomeMod.json").write_text("[]", encoding="utf-8")
        loaded, _mods, _unreadable = _from_dump(tmp_path, [], 0)
        assert loaded is None
        assert "no reference terrain" in capsys.readouterr().err

    def test_masters_and_mods_are_read_with_an_order_and_a_limit(
        self, tmp_path: Path, capsys
    ) -> None:
        (tmp_path / "Morrowind.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
        )
        (tmp_path / "B.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(50.0))[0]]), encoding="utf-8"
        )
        (tmp_path / "A.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(50.0))[0]]), encoding="utf-8"
        )
        # A sidecar file must not be mistaken for a plugin dump.
        (tmp_path / "A.keys.json").write_text("[]", encoding="utf-8")

        masters, mods, unreadable = _from_dump(tmp_path, ["b.esp", "a.esp"], 1)

        assert masters is not None
        assert [m.name for m in masters] == ["Morrowind.esm"]
        assert [m.name for m in mods] == ["B.esp"]  # order applied, then limit=1
        assert unreadable == []
        assert "masters: Morrowind.esm" in capsys.readouterr().out

    def test_a_plugin_with_no_landscape_data_is_skipped_and_counted(
        self, tmp_path: Path, capsys
    ) -> None:
        (tmp_path / "Morrowind.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
        )
        (tmp_path / "NoTerrain.json").write_text(
            json.dumps([{"type": "Static", "id": "torch"}]), encoding="utf-8"
        )

        _masters, mods, _unreadable = _from_dump(tmp_path, [], 0)

        assert mods == []
        assert "skipped 1 plugin(s) with no landscape data" in capsys.readouterr().out

    def test_an_unreadable_mod_is_reported_not_dropped_silently(self, tmp_path: Path) -> None:
        (tmp_path / "Morrowind.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
        )
        # Mentions landscape (so it is not skipped) but is not a record list.
        (tmp_path / "Bad.json").write_text('"Landscape but not a list"', encoding="utf-8")

        _masters, mods, unreadable = _from_dump(tmp_path, [], 0)

        assert mods == []
        assert unreadable == ["Bad"]

    def test_a_master_present_but_unreadable_is_skipped_like_absent(
        self, tmp_path: Path, capsys
    ) -> None:
        """A masters JSON file that exists but won't parse is treated the same as missing."""
        (tmp_path / "Morrowind.json").write_text("not valid json", encoding="utf-8")

        loaded, _mods, _unreadable = _from_dump(tmp_path, [], 0)

        assert loaded is None
        assert "no reference terrain" in capsys.readouterr().err

    def test_progress_is_printed_every_hundred_mods(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "Morrowind.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
        )
        for i in range(100):
            (tmp_path / f"M{i:03}.json").write_text(
                json.dumps([build_landscape_record((0, 0), _heights(50.0))[0]]), encoding="utf-8"
            )

        _masters, mods, _unreadable = _from_dump(tmp_path, [], 0)

        assert len(mods) == 100
        assert "100/100" in capsys.readouterr().out


class TestFromPlugins:
    def test_tes3conv_not_found_stops_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        import tools.survey_landscape as sl

        monkeypatch.setattr(sl, "find_tes3conv", lambda *_a, **_k: None)

        loaded, _mods, _unreadable = _from_plugins(tmp_path, [], 0, None)

        assert loaded is None
        assert "tes3conv was not found" in capsys.readouterr().err

    def test_not_a_directory_stops_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        import tools.survey_landscape as sl

        monkeypatch.setattr(sl, "find_tes3conv", lambda *_a, **_k: "tes3conv")

        loaded, _mods, _unreadable = _from_plugins(tmp_path / "gone", [], 0, None)

        assert loaded is None
        assert "not a directory" in capsys.readouterr().err

    def test_no_masters_present_stops_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        import tools.survey_landscape as sl

        monkeypatch.setattr(sl, "find_tes3conv", lambda *_a, **_k: "tes3conv")
        (tmp_path / "SomeMod.esp").write_bytes(b"\x00")

        loaded, _mods, _unreadable = _from_plugins(tmp_path, [], 0, None)

        assert loaded is None
        assert "no reference terrain" in capsys.readouterr().err

    def test_a_master_that_will_not_convert_stops_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        import subprocess

        import tools.survey_landscape as sl

        monkeypatch.setattr(sl, "find_tes3conv", lambda *_a, **_k: "tes3conv")
        (tmp_path / "Morrowind.esm").write_bytes(b"\x00")

        def fake_run(*_a: object, **_k: object) -> types.SimpleNamespace:
            return types.SimpleNamespace(returncode=1)

        monkeypatch.setattr(subprocess, "run", fake_run)

        loaded, _mods, _unreadable = _from_plugins(tmp_path, [], 0, None)

        assert loaded is None
        assert "could not read Morrowind.esm" in capsys.readouterr().err

    def test_masters_and_mods_are_converted_and_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        import subprocess

        import tools.survey_landscape as sl

        monkeypatch.setattr(sl, "find_tes3conv", lambda *_a, **_k: "tes3conv")
        (tmp_path / "Morrowind.esm").write_bytes(b"\x00")
        (tmp_path / "Mine.esp").write_bytes(b"\x00")

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            Path(argv[2]).write_text(
                json.dumps([build_landscape_record((0, 0), _heights(50.0))[0]]), encoding="utf-8"
            )
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        loaded, mods, unreadable = _from_plugins(tmp_path, [], 0, "tes3conv")

        assert loaded is not None
        assert [m.name for m in loaded] == ["Morrowind.esm"]
        assert [m.name for m in mods] == ["Mine.esp"]
        assert unreadable == []
        assert "masters: Morrowind.esm" in capsys.readouterr().out

    def test_a_mod_that_will_not_convert_is_reported_not_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        import tools.survey_landscape as sl

        monkeypatch.setattr(sl, "find_tes3conv", lambda *_a, **_k: "tes3conv")
        (tmp_path / "Morrowind.esm").write_bytes(b"\x00")
        (tmp_path / "Bad.esp").write_bytes(b"\x00")

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            if "Morrowind" in argv[1]:
                Path(argv[2]).write_text(
                    json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
                )
                return types.SimpleNamespace(returncode=0)
            return types.SimpleNamespace(returncode=1)

        monkeypatch.setattr(subprocess, "run", fake_run)

        loaded, mods, unreadable = _from_plugins(tmp_path, [], 0, "tes3conv")

        assert loaded is not None
        assert mods == []
        assert unreadable == ["Bad.esp"]

    def test_a_limit_is_applied_after_ordering(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        import tools.survey_landscape as sl

        monkeypatch.setattr(sl, "find_tes3conv", lambda *_a, **_k: "tes3conv")
        (tmp_path / "Morrowind.esm").write_bytes(b"\x00")
        (tmp_path / "A.esp").write_bytes(b"\x00")
        (tmp_path / "B.esp").write_bytes(b"\x00")

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            Path(argv[2]).write_text(
                json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
            )
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        _loaded, mods, _unreadable = _from_plugins(tmp_path, [], 1, "tes3conv")

        assert [m.name for m in mods] == ["A.esp"]

    def test_progress_is_printed_every_twenty_five_mods(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        import subprocess

        import tools.survey_landscape as sl

        monkeypatch.setattr(sl, "find_tes3conv", lambda *_a, **_k: "tes3conv")
        (tmp_path / "Morrowind.esm").write_bytes(b"\x00")
        for i in range(25):
            (tmp_path / f"M{i:03}.esp").write_bytes(b"\x00")

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            Path(argv[2]).write_text(
                json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
            )
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        _loaded, mods, _unreadable = _from_plugins(tmp_path, [], 0, "tes3conv")

        assert len(mods) == 25
        assert "25/25" in capsys.readouterr().out


class TestMain:
    def _dump(self, tmp_path: Path, heights_a: float, heights_b: float) -> Path:
        dump = tmp_path / "dump"
        dump.mkdir()
        (dump / "Morrowind.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
        )
        (dump / "A.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(heights_a))[0]]), encoding="utf-8"
        )
        (dump / "B.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(heights_b))[0]]), encoding="utf-8"
        )
        return dump

    def test_giving_both_a_folder_and_json_dir_is_refused(self, tmp_path: Path, capsys) -> None:
        assert main([str(tmp_path), "--json-dir", str(tmp_path)]) == 2
        assert "not both" in capsys.readouterr().err

    def test_giving_neither_a_folder_nor_json_dir_is_refused(self, tmp_path: Path, capsys) -> None:
        assert main([]) == 2
        assert "not both" in capsys.readouterr().err

    def test_no_order_given_prints_a_note(self, tmp_path: Path, capsys) -> None:
        dump = self._dump(tmp_path, 50.0, 50.0)
        main(["--json-dir", str(dump)])
        assert "no --order given" in capsys.readouterr().out

    def test_an_order_given_skips_the_note(self, tmp_path: Path, capsys) -> None:
        dump = self._dump(tmp_path, 50.0, 50.0)
        order = tmp_path / "order.txt"
        order.write_text("A.esp\nB.esp\n", encoding="utf-8")

        main(["--json-dir", str(dump), "--order", str(order)])

        assert "no --order given" not in capsys.readouterr().out

    def test_a_source_that_cannot_load_returns_2(self, tmp_path: Path, capsys) -> None:
        assert main(["--json-dir", str(tmp_path / "gone")]) == 2

    def test_no_contested_cells_reports_nothing_to_merge(self, tmp_path: Path, capsys) -> None:
        """Exactly one mod touches the cell -- changed, but not contested."""
        dump = tmp_path / "dump"
        dump.mkdir()
        (dump / "Morrowind.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
        )
        (dump / "A.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(50.0))[0]]), encoding="utf-8"
        )

        rc = main(["--json-dir", str(dump)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "No cell is edited by two mods" in out

    def test_a_folder_source_runs_through_from_plugins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        import subprocess

        import tools.survey_landscape as sl

        monkeypatch.setattr(sl, "find_tes3conv", lambda *_a, **_k: "tes3conv")
        data = tmp_path / "Data Files"
        data.mkdir()
        (data / "Morrowind.esm").write_bytes(b"\x00")

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            Path(argv[2]).write_text(
                json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
            )
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = main([str(data)])

        assert rc == 0
        assert "reference landmass" in capsys.readouterr().out

    def test_unreadable_plugins_are_listed(self, tmp_path: Path, capsys) -> None:
        dump = tmp_path / "dump"
        dump.mkdir()
        (dump / "Morrowind.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
        )
        (dump / "Bad.json").write_text('"Landscape but not a list"', encoding="utf-8")

        rc = main(["--json-dir", str(dump)])

        assert rc == 0
        assert "1 plugin(s) could not be read: Bad" in capsys.readouterr().out

    def test_more_than_three_plugins_on_one_cell_are_summarised_with_a_count(
        self, tmp_path: Path, capsys
    ) -> None:
        dump = tmp_path / "dump"
        dump.mkdir()
        (dump / "Morrowind.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
        )
        for i, height in enumerate([10.0, 20.0, 30.0, 40.0]):
            (dump / f"M{i}.json").write_text(
                json.dumps([build_landscape_record((0, 0), _heights(height))[0]]),
                encoding="utf-8",
            )

        rc = main(["--json-dir", str(dump)])

        assert rc == 0
        assert ", +1" in capsys.readouterr().out

    def test_added_land_with_no_vanilla_contested_cells_skips_the_ranked_table(
        self, tmp_path: Path, capsys
    ) -> None:
        """All contested cells are new land -- the 'vanilla' branch has nothing to print."""
        dump = tmp_path / "dump"
        dump.mkdir()
        (dump / "Morrowind.json").write_text(
            json.dumps([build_landscape_record((0, 0), _heights(0.0))[0]]), encoding="utf-8"
        )
        (dump / "A.json").write_text(
            json.dumps([build_landscape_record((1, 1), _heights(10.0))[0]]), encoding="utf-8"
        )
        (dump / "B.json").write_text(
            json.dumps([build_landscape_record((1, 1), _heights(20.0))[0]]), encoding="utf-8"
        )

        rc = main(["--json-dir", str(dump)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "vanilla cells, most to gain from merging" not in out
        assert "are added land" in out

    def test_contested_vanilla_and_added_cells_are_both_reported(
        self, tmp_path: Path, capsys
    ) -> None:
        """A and B disagree on the same vanilla cell -- genuinely contested."""
        dump = self._dump(tmp_path, 50.0, 100.0)

        rc = main(["--json-dir", str(dump), "--cells", "1"])

        out = capsys.readouterr().out
        assert rc == 0
        assert "contested by more than one mod" in out
        assert "vanilla cells and" in out
        assert "vanilla cells, most to gain from merging" in out
        assert "Nothing was written" in out
