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


def test_a_native_merge_reads_and_merges_without_a_converter(tmp_path: Path) -> None:
    """The read/merge/seam pipeline runs with the built-in reader (converter=None).

    A master and a mod edit the same cell; the reconstructed heights match, so
    the merge has nothing net to write and takes the "nothing to merge" exit --
    which still walks the whole reading and merging orchestration first.
    """
    from wraithguard.land.service import build_merged_lands

    _plugin_with_height(tmp_path / "Master.esm", 0)
    _plugin_with_height(tmp_path / "Mod.esp", 0)  # same terrain -> nothing net to write
    output = tmp_path / "Merged Lands.esp"
    lines: list[str] = []
    build_merged_lands(
        [tmp_path], ["Master.esm", "Mod.esp"], None, output=output, report=lines.append
    )
    assert any("reading masters" in line for line in lines)
    assert any("merging" in line for line in lines)
    assert not output.exists()  # nothing net changed, so no plugin was written
