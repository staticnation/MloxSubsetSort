"""Tests for the self-contained parts of ``tools/build_merged_lands.py``.

The bulk of the tool is a pipeline over the (separately, fully tested)
``wraithguard.land`` package. What lives *in* the tool and nowhere else is the
per-cell fold (``merge_cell``), the grid-to-rows shaping the writer needs
(``_rows_from`` / ``_int_rows_from`` / ``_triples_from`` / ``_normal_rows``), the
master-size lookup, and ``main``'s argument guards. Those are exercised here with
real ``RelativeGrid`` / ``LandscapeDiff`` values rather than through the whole
merge, which the land tests already cover end to end.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

from tools.build_merged_lands import (
    _int_rows_from,
    _normal_rows,
    _rows_from,
    _triples_from,
    main,
    master_sizes,
    merge_cell,
)
from wraithguard.land.diff import LandscapeDiff, RelativeGrid
from wraithguard.land.emit import build_landscape_record
from wraithguard.land.merge import ConflictStrategy
from wraithguard.tes3fields.landscape import LAND_SIZE


def _flat_heights(level: float = 0.0) -> list[list[float]]:
    """A whole grid raised to ``level`` -- a uniform terrain at that height."""
    return [[level] * LAND_SIZE for _ in range(LAND_SIZE)]


def _dump(folder: Path, first_by_file: dict[str, float]) -> Path:
    """Write a tes3conv-style JSON dump: one LAND record per named plugin."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, first in first_by_file.items():
        record, _clamped = build_landscape_record((0, 0), heights=_flat_heights(first))
        (folder / f"{name}.json").write_text(json.dumps([record]), encoding="utf-8")
    return folder


def _textures(index: int) -> list[list[int]]:
    return [[index] * 16 for _ in range(16)]


def _dump_textured(folder: Path, spec: dict[str, tuple[float, int]]) -> Path:
    """A dump where each plugin carries both heights and a texture grid."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, (level, index) in spec.items():
        record, _clamped = build_landscape_record(
            (0, 0), heights=_flat_heights(level), textures=_textures(index)
        )
        (folder / f"{name}.json").write_text(json.dumps([record]), encoding="utf-8")
    return folder


def _dump_at(folder: Path, coords: tuple[int, int], spec: dict[str, float]) -> None:
    """Append a LAND record at ``coords`` for each named plugin (merged into any dump)."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, level in spec.items():
        record, _clamped = build_landscape_record(coords, heights=_flat_heights(level))
        path = folder / f"{name}.json"
        existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        existing.append(record)
        path.write_text(json.dumps(existing), encoding="utf-8")


def _masters(folder: Path) -> Path:
    """Drop dummy vanilla master .esm files so the header can be sized."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm"):
        (folder / name).write_bytes(b"x" * 16)
    return folder


def _grid(moves: dict[int, int], side: int = LAND_SIZE, components: int = 1) -> RelativeGrid:
    reference = [0] * (side * side * components)
    plugin = list(reference)
    for index, value in moves.items():
        plugin[index] = value
    return RelativeGrid.from_difference(reference, plugin, side, components)


def _heights(plugin: str, first: int, *, new_land: bool = False) -> LandscapeDiff:
    return LandscapeDiff(coords=(0, 0), plugin=plugin, new_land=new_land, heights=_grid({0: first}))


class TestRowHelpers:
    def test_rows_from_shapes_a_single_component_grid_into_float_rows(self) -> None:
        rows = _rows_from(_grid({0: 3, 3: 7}, side=2))
        assert rows == [[3.0, 0.0], [0.0, 7.0]]

    def test_int_rows_from_shapes_into_integer_rows(self) -> None:
        rows = _int_rows_from(_grid({1: 5}, side=2))
        assert rows == [[0, 5], [0, 0]]
        assert all(isinstance(v, int) for row in rows for v in row)

    def test_triples_from_clamps_each_component_to_a_byte(self) -> None:
        """A merge can average colours past a byte; wrapping would show as specks."""
        rows = _triples_from(_grid({0: 300, 1: -5, 2: 128}, side=2, components=3))
        assert rows[0][0] == (255, 0, 128)  # 300 -> 255, -5 -> 0, 128 unchanged

    def test_normal_rows_is_none_when_the_pipeline_supplied_none(self) -> None:
        assert _normal_rows(None) is None

    def test_normal_rows_reads_interleaved_bytes_back_as_triples(self) -> None:
        flat = list(range(LAND_SIZE * LAND_SIZE * 3))
        rows = _normal_rows(flat)
        assert rows is not None
        assert len(rows) == LAND_SIZE
        assert len(rows[0]) == LAND_SIZE
        assert rows[0][0] == (0, 1, 2)
        assert rows[0][1] == (3, 4, 5)


class TestMergeCell:
    def test_a_single_editor_is_taken_as_is_with_no_contest(self) -> None:
        heights, textures, world_map, colors, contested, major = merge_cell(
            [_heights("ModA.esp", 20)], ConflictStrategy.AUTO
        )
        assert heights is not None
        assert textures is None and world_map is None and colors is None
        assert contested == 0 and major == 0

    def test_two_editors_of_the_same_vertex_are_contested(self) -> None:
        _h, _t, _w, _c, contested, _m = merge_cell(
            [_heights("ModA.esp", 20), _heights("ModB.esp", 60)], ConflictStrategy.AUTO
        )
        assert contested >= 1  # both moved vertex 0, so it had to be settled

    def test_a_new_land_cell_forces_last_wins(self) -> None:
        """When any editor added land the masters lacked, blending is meaningless.

        The fold switches to OVERWRITE; the later plugin's height is what remains.
        """
        merged, *_rest = merge_cell(
            [_heights("ModA.esp", 20), _heights("ModB.esp", 60, new_land=True)],
            ConflictStrategy.AUTO,
        )
        assert merged is not None
        assert merged.delta_at(0, 0) == 60  # ModB, applied last, wins outright

    def test_every_layer_is_folded_when_present(self) -> None:
        """Heights, textures, world map and colours each get merged, not defaulted."""

        def _full(plugin: str, seed: int) -> LandscapeDiff:
            return LandscapeDiff(
                coords=(0, 0),
                plugin=plugin,
                heights=_grid({0: seed}),
                textures=_grid({0: seed}, side=16),
                world_map=_grid({0: seed}, side=9),
                colors=_grid({0: seed, 1: seed, 2: seed}, components=3),
            )

        heights, textures, world_map, colors, _c, _m = merge_cell(
            [_full("ModA.esp", 3), _full("ModB.esp", 7)], ConflictStrategy.AUTO
        )
        assert heights is not None
        assert textures is not None
        assert world_map is not None
        assert colors is not None


class TestMasterSizes:
    def test_it_reads_each_masters_size_from_disk(self, tmp_path: Path) -> None:
        master = tmp_path / "Master.esm"
        master.write_bytes(b"x" * 42)
        assert master_sizes(tmp_path, ["Master.esm"]) == [("Master.esm", 42)]

    def test_it_matches_case_insensitively(self, tmp_path: Path) -> None:
        (tmp_path / "Master.esm").write_bytes(b"abc")
        assert master_sizes(tmp_path, ["master.esm"]) == [("Master.esm", 3)]

    def test_no_folder_is_fatal(self) -> None:
        with pytest.raises(SystemExit, match="only be read from the real files"):
            master_sizes(None, ["Master.esm"])

    def test_a_master_missing_from_the_folder_is_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="is not in"):
            master_sizes(tmp_path, ["Ghost.esm"])


class TestMainGuards:
    def test_giving_both_a_folder_and_a_json_dir_is_refused(self, tmp_path: Path, capsys) -> None:
        rc = main([str(tmp_path), "--json-dir", str(tmp_path)])
        assert rc == 2
        assert "not both" in capsys.readouterr().err

    def test_giving_neither_a_folder_nor_a_json_dir_is_refused(self, capsys) -> None:
        rc = main([])  # folder is optional and json-dir defaults to None
        assert rc == 2
        assert "not both" in capsys.readouterr().err


class TestMainDryRun:
    def test_a_dry_run_over_a_json_dump_reports_but_writes_nothing(
        self, tmp_path: Path, capsys
    ) -> None:
        """The whole read/reference/merge/finish pipeline, end to end, writing nothing.

        Two mods contest the same cell against a flat master, so the merge has a
        real result to report; --dry-run exercises every stage but the writer.
        """
        dump = _dump(
            tmp_path / "json",
            {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0},
        )
        out = tmp_path / "Merged Lands.esp"
        rc = main(["--json-dir", str(dump), "--out", str(out), "--dry-run", "--cells"])
        report = capsys.readouterr().out
        assert rc == 0
        assert "reference:" in report
        assert "--dry-run: nothing was written." in report
        assert not out.exists()

    def test_a_single_mod_dump_has_nothing_to_merge(self, tmp_path: Path, capsys) -> None:
        """One mod's edit is delivered by the load order already, so nothing merges."""
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0})
        rc = main(["--json-dir", str(dump), "--dry-run"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Nothing to merge" in out


class TestMainWrite:
    def test_it_writes_a_plugin_with_the_native_writer_when_tes3conv_is_absent(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        """No tes3conv: the tool encodes the merged plugin in process and writes it."""
        import tools.build_merged_lands as bml

        monkeypatch.setattr(bml, "find_tes3conv", lambda _arg: None)  # force the native path
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0})
        _masters(dump)
        out = tmp_path / "Merged Lands.esp"
        rc = main(["--json-dir", str(dump), "--out", str(out), "--data-files", str(dump)])
        report = capsys.readouterr().out
        assert rc == 0
        assert out.is_file()
        assert out.stat().st_size > 0
        assert "wrote" in report
        assert "Place it LAST" in report

    def test_the_tes3conv_path_runs_the_converter(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        """With a converter configured, the JSON is handed to it and the output kept."""
        import tools.build_merged_lands as bml

        monkeypatch.setattr(bml, "find_tes3conv", lambda _arg: "tes3conv")

        def fake_run(argv, *_a, **_k):
            Path(argv[2]).write_bytes(b"ESP")  # tes3conv "writes" the plugin
            return __import__("types").SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(bml.subprocess, "run", fake_run)
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0})
        _masters(dump)
        out = tmp_path / "Merged Lands.esp"
        rc = main(["--json-dir", str(dump), "--out", str(out), "--data-files", str(dump)])
        assert rc == 0
        assert out.read_bytes() == b"ESP"
        assert "wrote" in capsys.readouterr().out

    def test_a_converter_nonzero_exit_is_reported(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        import tools.build_merged_lands as bml

        monkeypatch.setattr(bml, "find_tes3conv", lambda _arg: "tes3conv")

        def fake_run(_argv, *_a, **_k):
            return __import__("types").SimpleNamespace(returncode=1, stdout="", stderr="nope")

        monkeypatch.setattr(bml.subprocess, "run", fake_run)
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0})
        _masters(dump)
        rc = main(
            ["--json-dir", str(dump), "--out", str(tmp_path / "o.esp"), "--data-files", str(dump)]
        )
        assert rc == 1
        assert "tes3conv refused" in capsys.readouterr().err

    def test_a_converter_that_cannot_be_run_is_reported(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        import tools.build_merged_lands as bml

        monkeypatch.setattr(bml, "find_tes3conv", lambda _arg: "tes3conv")

        def fake_run(*_a, **_k):
            raise OSError("no such executable")

        monkeypatch.setattr(bml.subprocess, "run", fake_run)
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0})
        _masters(dump)
        rc = main(
            ["--json-dir", str(dump), "--out", str(tmp_path / "o.esp"), "--data-files", str(dump)]
        )
        assert rc == 2
        assert "could not be run" in capsys.readouterr().err

    def test_a_native_encode_failure_is_reported(self, tmp_path: Path, capsys, monkeypatch) -> None:
        """If the in-process writer refuses the document, that is a clean exit 2."""
        import tools.build_merged_lands as bml
        from wraithguard.esp import EspError

        monkeypatch.setattr(bml, "find_tes3conv", lambda _arg: None)
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0})
        _masters(dump)

        from wraithguard import esp

        def boom(_doc):
            raise EspError("cannot encode this")

        monkeypatch.setattr(esp, "plugin_from_json", boom)
        rc = main(
            ["--json-dir", str(dump), "--out", str(tmp_path / "o.esp"), "--data-files", str(dump)]
        )
        assert rc == 2
        assert "could not encode the merged plugin natively" in capsys.readouterr().err


class TestMainReporting:
    def test_a_dump_without_the_masters_is_a_clean_error(self, tmp_path: Path) -> None:
        """No vanilla master in the dump means no reference terrain: a clean exit 2."""
        dump = _dump(tmp_path / "json", {"ModA": 20.0})  # no Morrowind/Tribunal/Bloodmoon
        assert main(["--json-dir", str(dump), "--dry-run"]) == 2

    def test_a_conflicts_dir_writes_a_severity_png(self, tmp_path: Path, capsys) -> None:
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0})
        conflicts = tmp_path / "conflicts"
        rc = main(["--json-dir", str(dump), "--dry-run", "--conflicts-dir", str(conflicts)])
        assert rc == 0
        assert (conflicts / "MERGED.png").is_file()
        assert "one block per cell" in capsys.readouterr().out

    def test_verbose_runs_the_merge_the_same_way(self, tmp_path: Path, capsys) -> None:
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0})
        rc = main(["--json-dir", str(dump), "--dry-run", "--verbose"])
        assert rc == 0
        assert "writing 1 cell(s)" in capsys.readouterr().out

    def test_the_skip_flags_bypass_repair_cleaning_and_slope_limiting(
        self, tmp_path: Path, capsys
    ) -> None:
        """--no-seam-repair/--no-clean/--no-slope-limit take the report's other branches."""
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0})
        rc = main(
            [
                "--json-dir",
                str(dump),
                "--dry-run",
                "--no-seam-repair",
                "--no-clean",
                "--no-slope-limit",
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "seam repair moved" not in out  # repair was skipped
        assert "cleaning dropped" not in out  # cleaning was skipped

    def test_a_mod_mentioning_landscape_but_holding_no_records_is_unreadable(
        self, tmp_path: Path, capsys
    ) -> None:
        """A file the pre-scan flags but that yields no record list is reported, not merged."""
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0})
        # A JSON object (not a list) that still contains the word Landscape.
        (dump / "Broken.json").write_text('{"note": "Landscape here"}', encoding="utf-8")
        rc = main(["--json-dir", str(dump), "--dry-run"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "could not be read" in out
        assert "Broken" in out

    def test_a_mod_with_a_broken_sidecar_is_a_clean_error(self, tmp_path: Path, capsys) -> None:
        """A .mergedlands.toml that will not parse stops the run with exit 2."""
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0})
        (dump / "ModA.mergedlands.toml").write_text("this = = not toml", encoding="utf-8")
        rc = main(["--json-dir", str(dump), "--data-files", str(dump), "--dry-run"])
        assert rc == 2
        assert "could not be trusted" in capsys.readouterr().err

    def test_textured_mods_under_an_order_emit_land_textures(self, tmp_path: Path, capsys) -> None:
        """Two mods painting a contested cell differently drive the texture-merge path."""
        dump = _dump_textured(
            tmp_path / "json",
            {"Morrowind": (0.0, 0), "ModA": (20.0, 1), "ModB": (60.0, 2)},
        )
        order = tmp_path / "order.txt"
        order.write_text("Morrowind.esm\nModA.esp\nModB.esp\n", encoding="utf-8")
        rc = main(["--json-dir", str(dump), "--dry-run", "--order", str(order)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "land textures:" in out  # the compact/emit path ran

    def test_a_cell_the_masters_never_had_is_counted_as_new_land(
        self, tmp_path: Path, capsys
    ) -> None:
        """A cell only the mods define is new land, reported apart from recovered conflicts."""
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0})
        _dump_at(dump, (1, 1), {"ModA": 30.0, "ModB": 70.0})  # a cell Morrowind lacks
        rc = main(["--json-dir", str(dump), "--dry-run"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "land the masters never had" in out

    def test_debug_vertex_colors_warns_after_a_write(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        import tools.build_merged_lands as bml

        monkeypatch.setattr(bml, "find_tes3conv", lambda _arg: None)
        dump = _dump(tmp_path / "json", {"Morrowind": 0.0, "ModA": 20.0, "ModB": 60.0})
        _masters(dump)
        out = tmp_path / "Merged Lands.esp"
        rc = main(
            [
                "--json-dir",
                str(dump),
                "--out",
                str(out),
                "--data-files",
                str(dump),
                "--add-debug-vertex-colors",
            ]
        )
        assert rc == 0
        assert "--add-debug-vertex-colors was on" in capsys.readouterr().out
