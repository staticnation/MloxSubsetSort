"""Reading one plugin's landscape records, and every way it can fail.

``_records_via`` prefers a fresh sidecar, then the configured tes3conv, then an
in-process native read. The converter subprocess is faked here so its failure
branches -- non-zero exit, no output, timeout, out of memory, non-list JSON --
are covered without a real tes3conv.
"""

from __future__ import annotations

import json
import struct
import subprocess
import types
from typing import TYPE_CHECKING

import pytest

from wraithguard.land import service
from wraithguard.land.native import KEYS_VERSION
from wraithguard.land.service import _records_natively, _records_via
from wraithguard.tes3fields.landscape import LAND_SIZE

if TYPE_CHECKING:
    from pathlib import Path

_VERTICES = LAND_SIZE * LAND_SIZE


def _sub(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<I", len(payload)) + payload


def _plugin_with_landscape(path: Path) -> Path:
    """Write a minimal TES3 plugin that genuinely carries a LAND record."""
    vhgt = struct.pack("<f", 0.0) + bytes(_VERTICES) + b"\x00\x00\x00"
    body = _sub(b"INTV", struct.pack("<ii", 0, 0)) + _sub(b"DATA", struct.pack("<I", 1))
    body += _sub(b"VHGT", vhgt)
    land = b"LAND" + struct.pack("<III", len(body), 0, 0) + body
    header = b"TES3" + struct.pack("<III", 0, 0, 0)
    path.write_bytes(header + land)
    return path


def _run(returncode: int = 0, stdout: str = "", stderr: str = ""):
    def fake(*_args: object, **_kwargs: object) -> types.SimpleNamespace:
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    return fake


def test_a_fresh_sidecar_is_used_instead_of_the_converter(tmp_path: Path) -> None:
    """A current ``.land.json`` short-circuits the whole converter step."""
    import os

    plugin = _plugin_with_landscape(tmp_path / "P.esp")
    side = tmp_path / "P.land.json"
    side.write_text(
        json.dumps({"v": KEYS_VERSION, "d": [{"type": "Landscape", "grid": [0, 0]}]}),
        encoding="utf-8",
    )
    os.utime(plugin, (side.stat().st_mtime - 10, side.stat().st_mtime - 10))
    records, failure = _records_via("tes3conv", plugin, tmp_path, sidecar_dir=tmp_path)
    assert failure == ""
    assert records == [{"type": "Landscape", "grid": [0, 0]}]


def test_no_converter_falls_back_to_a_native_read_failure(tmp_path: Path) -> None:
    """With no converter, an unreadable plugin surfaces the native error."""
    directory = tmp_path / "adir.esp"
    directory.mkdir()  # a directory cannot be read as a plugin
    records, failure = _records_via(None, directory, tmp_path)
    assert records == []
    assert "failed" in failure


def test_a_nonzero_converter_exit_is_reported(tmp_path: Path, monkeypatch) -> None:
    """A converter that exits non-zero reports its message."""
    monkeypatch.setattr(service.subprocess, "run", _run(returncode=2, stderr="bad plugin"))
    plugin = _plugin_with_landscape(tmp_path / "P.esp")
    records, failure = _records_via("tes3conv", plugin, tmp_path)
    assert records == []
    assert "exited 2" in failure
    assert "bad plugin" in failure


def test_a_converter_that_writes_no_json_is_reported(tmp_path: Path, monkeypatch) -> None:
    """A zero exit with no output file is still a failure."""
    monkeypatch.setattr(service.subprocess, "run", _run(returncode=0))  # writes nothing
    plugin = _plugin_with_landscape(tmp_path / "P.esp")
    records, failure = _records_via("tes3conv", plugin, tmp_path)
    assert records == []
    assert "wrote no JSON" in failure


def test_a_converter_timeout_is_reported(tmp_path: Path, monkeypatch) -> None:
    """A converter that runs too long is abandoned, not waited on forever."""

    def timeout(*_args: object, **_kwargs: object):
        raise subprocess.TimeoutExpired("tes3conv", 600)

    monkeypatch.setattr(service.subprocess, "run", timeout)
    plugin = _plugin_with_landscape(tmp_path / "P.esp")
    _records, failure = _records_via("tes3conv", plugin, tmp_path)
    assert "timed out" in failure


def test_running_out_of_memory_is_reported(tmp_path: Path, monkeypatch) -> None:
    """A MemoryError decoding huge JSON is a clean failure, not a crash."""

    def oom(*_args: object, **_kwargs: object):
        raise MemoryError

    monkeypatch.setattr(service.subprocess, "run", oom)
    plugin = _plugin_with_landscape(tmp_path / "P.esp")
    _records, failure = _records_via("tes3conv", plugin, tmp_path)
    assert "out of memory" in failure


def test_json_that_is_not_a_record_list_is_reported(tmp_path: Path, monkeypatch) -> None:
    """A converter that writes something other than a list is refused."""

    def run_and_write(argv, *_args: object, **_kwargs: object) -> types.SimpleNamespace:
        from pathlib import Path as _Path

        _Path(argv[2]).write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(service.subprocess, "run", run_and_write)
    plugin = _plugin_with_landscape(tmp_path / "P.esp")
    records, failure = _records_via("tes3conv", plugin, tmp_path)
    assert records == []
    assert "not a record list" in failure


def test_a_plugin_with_no_terrain_returns_nothing(tmp_path: Path) -> None:
    """A plugin the pre-scan finds no landscape in is skipped, not an error."""
    plugin = tmp_path / "NoLand.esp"
    plugin.write_bytes(b"TES3" + struct.pack("<III", 0, 0, 0))  # header only, no LAND
    records, failure = _records_via(None, plugin, tmp_path)
    assert records == []
    assert failure == ""


def test_a_converter_that_cannot_be_run_is_reported(tmp_path: Path, monkeypatch) -> None:
    """An OSError launching the converter is a clean failure, not a traceback."""

    def cannot_run(*_args: object, **_kwargs: object):
        raise OSError("no such executable")

    monkeypatch.setattr(service.subprocess, "run", cannot_run)
    plugin = _plugin_with_landscape(tmp_path / "P.esp")
    records, failure = _records_via("tes3conv", plugin, tmp_path)
    assert records == []
    assert "could not run tes3conv" in failure


def test_json_that_will_not_parse_is_reported(tmp_path: Path, monkeypatch) -> None:
    """A converter that writes malformed JSON is a clean failure, not a crash."""

    def run_and_write_garbage(argv, *_args: object, **_kwargs: object) -> types.SimpleNamespace:
        from pathlib import Path as _Path

        _Path(argv[2]).write_text("{not valid json", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(service.subprocess, "run", run_and_write_garbage)
    plugin = _plugin_with_landscape(tmp_path / "P.esp")
    records, failure = _records_via("tes3conv", plugin, tmp_path)
    assert records == []
    assert "will not parse" in failure


def test_records_natively_reports_an_unreadable_plugin(tmp_path: Path) -> None:
    """The native fallback reports why a plugin could not be read."""
    directory = tmp_path / "b.esp"
    directory.mkdir()
    records, failure = _records_natively(directory, "tes3conv said no")
    assert records == []
    assert failure


def _plugin_with_height(path: Path, first_delta: int) -> Path:
    """A LAND-bearing plugin whose terrain rises by ``first_delta``."""
    vhgt = struct.pack("<f", 0.0) + bytes([first_delta]) + bytes(_VERTICES - 1) + b"\x00\x00\x00"
    body = _sub(b"INTV", struct.pack("<ii", 0, 0)) + _sub(b"DATA", struct.pack("<I", 1))
    body += _sub(b"VHGT", vhgt)
    land = b"LAND" + struct.pack("<III", len(body), 0, 0) + body
    path.write_bytes(b"TES3" + struct.pack("<III", 0, 0, 0) + land)
    return path


def test_a_single_mod_edit_needs_no_merge(tmp_path: Path) -> None:
    """One mod editing a cell is delivered as-is, so the merge writes nothing.

    This walks the whole read/merge/clean orchestration and takes the
    "nothing to merge" exit, which is the common single-editor case.
    """
    from wraithguard.land.service import build_merged_lands

    _plugin_with_height(tmp_path / "Master.esm", 0)
    _plugin_with_height(tmp_path / "Mod.esp", 20)  # one editor -> cleaning drops it
    output = tmp_path / "Merged Lands.esp"
    lines: list[str] = []
    build_merged_lands(
        [tmp_path], ["Master.esm", "Mod.esp"], None, output=output, report=lines.append
    )
    assert any("nothing to merge" in line for line in lines)
    assert not output.exists()


def test_two_mods_contesting_a_cell_are_merged_and_written(tmp_path: Path) -> None:
    """Two mods editing the same cell produce a real merge, written natively.

    A contested cell differs from every single mod's version, so it survives
    cleaning and drives the whole write path -- encode, write, and marker.
    """
    from wraithguard.land.service import build_merged_lands

    _plugin_with_height(tmp_path / "Master.esm", 0)
    _plugin_with_height(tmp_path / "ModA.esp", 20)
    _plugin_with_height(tmp_path / "ModB.esp", 60)  # a different edit -> contested
    output = tmp_path / "Merged Lands.esp"
    result = build_merged_lands(
        [tmp_path], ["Master.esm", "ModA.esp", "ModB.esp"], None, output=output, include_cells=True
    )
    assert output.is_file()
    assert result.output == output
    assert (tmp_path / "Merged Lands.mergedlands.toml").is_file()  # the "ignore me" marker


def test_a_master_not_in_any_data_folder_is_fatal(tmp_path: Path) -> None:
    """A master the search cannot find aborts the merge with a clear message."""
    from wraithguard.land.service import MergeServiceError, build_merged_lands

    _plugin_with_height(tmp_path / "Mod.esp", 20)
    with pytest.raises(MergeServiceError, match="not in any of"):
        build_merged_lands([tmp_path], ["Ghost.esm", "Mod.esp"], None, output=tmp_path / "o.esp")


def test_a_master_with_a_broken_sidecar_is_reported(tmp_path: Path) -> None:
    """A master whose .mergedlands.toml will not parse stops the merge."""
    from wraithguard.land.service import MergeServiceError, build_merged_lands

    _plugin_with_height(tmp_path / "Master.esm", 0)
    (tmp_path / "Master.mergedlands.toml").write_text("this = = not toml", encoding="utf-8")
    _plugin_with_height(tmp_path / "Mod.esp", 20)
    with pytest.raises(MergeServiceError):
        build_merged_lands([tmp_path], ["Master.esm", "Mod.esp"], None, output=tmp_path / "o.esp")


def test_a_mod_with_a_broken_sidecar_is_reported(tmp_path: Path) -> None:
    """A mod whose .mergedlands.toml will not parse stops the merge."""
    from wraithguard.land.service import MergeServiceError, build_merged_lands

    _plugin_with_height(tmp_path / "Master.esm", 0)
    _plugin_with_height(tmp_path / "Mod.esp", 20)
    (tmp_path / "Mod.mergedlands.toml").write_text("this = = not toml", encoding="utf-8")
    with pytest.raises(MergeServiceError):
        build_merged_lands([tmp_path], ["Master.esm", "Mod.esp"], None, output=tmp_path / "o.esp")


def test_a_mod_that_is_not_installed_is_noted_not_fatal(tmp_path: Path) -> None:
    """A load-order entry with no file on disk is reported, verbosely, and skipped."""
    from wraithguard.land.service import build_merged_lands

    _plugin_with_height(tmp_path / "Master.esm", 0)
    _plugin_with_height(tmp_path / "Mod.esp", 20)
    lines: list[str] = []
    build_merged_lands(
        [tmp_path],
        ["Master.esm", "Mod.esp", "Gone.esp"],  # Gone.esp is not on disk
        None,
        output=tmp_path / "o.esp",
        report=lines.append,
        verbose=True,
    )
    assert any("could not be read" in line for line in lines)
    assert any("Gone.esp" in line and "not found" in line for line in lines)


def test_a_master_tes3conv_refuses_is_read_natively(tmp_path: Path, monkeypatch) -> None:
    """When the converter fails a master, the native reader rescues it."""
    from wraithguard.land import service
    from wraithguard.land.service import build_merged_lands

    _plugin_with_height(tmp_path / "Master.esm", 0)
    _plugin_with_height(tmp_path / "ModA.esp", 20)
    _plugin_with_height(tmp_path / "ModB.esp", 60)
    # A converter that always fails forces the native fallback on every read,
    # and also refuses the final merged JSON, so the write raises after the
    # reads were rescued -- covering both the rescue and the write-refusal.
    from wraithguard.land.service import MergeServiceError

    monkeypatch.setattr(service.subprocess, "run", _run(returncode=1, stderr="nope"))
    lines: list[str] = []
    with pytest.raises(MergeServiceError, match="refused the merged JSON"):
        build_merged_lands(
            [tmp_path],
            ["Master.esm", "ModA.esp", "ModB.esp"],
            "tes3conv",
            output=tmp_path / "Merged Lands.esp",
            report=lines.append,
        )
    assert any("read directly" in line for line in lines)  # rescued by the native reader


def test_a_mod_with_no_terrain_is_silently_skipped_and_progress_is_reported(
    tmp_path: Path,
) -> None:
    """Fifty terrain-free mods contribute nothing and trip the every-50 progress line.

    A mod the pre-scan finds no LAND in reads as records=[] with no failure, so
    it is neither merged nor reported as unreadable -- and with fifty of them the
    read loop crosses the ``index % 50`` progress checkpoint.
    """
    from wraithguard.land.service import build_merged_lands

    _plugin_with_height(tmp_path / "Master.esm", 0)
    load_order = ["Master.esm"]
    for i in range(50):
        name = f"Empty{i:02d}.esp"
        (tmp_path / name).write_bytes(b"TES3" + struct.pack("<III", 0, 0, 0))  # header only
        load_order.append(name)
    lines: list[str] = []
    build_merged_lands(
        [tmp_path], load_order, None, output=tmp_path / "o.esp", report=lines.append
    )
    assert any("50/50" in line for line in lines)  # the progress checkpoint fired
    assert any("nothing to merge" in line for line in lines)  # no mod had terrain


def test_unreadable_plugins_are_summarised_tersely_without_verbose(tmp_path: Path) -> None:
    """Without verbose, a missing mod is summarised on one line, not itemised."""
    from wraithguard.land.service import build_merged_lands

    _plugin_with_height(tmp_path / "Master.esm", 0)
    _plugin_with_height(tmp_path / "Mod.esp", 20)
    lines: list[str] = []
    build_merged_lands(
        [tmp_path],
        ["Master.esm", "Mod.esp", "Gone.esp"],  # Gone.esp is not on disk
        None,
        output=tmp_path / "o.esp",
        report=lines.append,
    )
    assert any("could not be read" in line and "Gone.esp" in line for line in lines)


def test_a_previous_merge_output_is_skipped_on_a_re_merge(tmp_path: Path) -> None:
    """Re-merging a load order that still lists a prior Merged Lands.esp skips it."""
    from wraithguard.land.service import build_merged_lands

    _plugin_with_height(tmp_path / "Master.esm", 0)
    _plugin_with_height(tmp_path / "ModA.esp", 20)
    _plugin_with_height(tmp_path / "ModB.esp", 60)
    merged = tmp_path / "Merged Lands.esp"
    build_merged_lands([tmp_path], ["Master.esm", "ModA.esp", "ModB.esp"], None, output=merged)
    assert merged.is_file()  # and its .mergedlands.toml marker now exists

    lines: list[str] = []
    build_merged_lands(
        [tmp_path],
        ["Master.esm", "ModA.esp", "ModB.esp", "Merged Lands.esp"],
        None,
        output=tmp_path / "Merged Lands 2.esp",
        report=lines.append,
    )
    assert any("skipped Merged Lands.esp" in line for line in lines)
