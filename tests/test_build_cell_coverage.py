"""build_cell_coverage without a session, and its within-plugin dedup.

test_hardening.py already exercises this function against a folder of
malformed plugins (the session=None path, but with nothing readable in it).
These use well-formed plugins so the actual yield -- and a plugin defining
the same cell twice -- get exercised for real.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from conftest import rec, static_record, sub, write_plugin, zstr

import wraithguard_toolkit as core
from wraithguard.plugins import PluginFileIndex

if TYPE_CHECKING:
    from pathlib import Path


def _exterior_cell(gx: int, gy: int) -> bytes:
    return rec("CELL", sub("NAME", zstr("")) + sub("DATA", struct.pack("<iii", 0, gx, gy)))


def _interior_cell(name: str) -> bytes:
    return rec("CELL", sub("NAME", zstr(name)) + sub("DATA", struct.pack("<iii", 1, 0, 0)))


class TestWithoutASession:
    def test_exterior_and_interior_cells_are_both_placed(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        body = static_record("torch_01") + _exterior_cell(3, -2) + _interior_cell("Balmora, Guild")
        write_plugin(data_dir / "Mine.esp", extra=body)
        index = PluginFileIndex([str(data_dir)])

        coverage = core.build_cell_coverage(["Mine.esp"], index)

        assert coverage["exterior"][(3, -2)] == ["Mine.esp"]
        assert coverage["interior"]["Balmora, Guild"] == ["Mine.esp"]
        assert coverage["scanned"] == 1


class TestWithinPluginDedup:
    def test_the_same_exterior_cell_defined_twice_places_the_plugin_once(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        body = _exterior_cell(1, 1) + _exterior_cell(1, 1)
        write_plugin(data_dir / "Mine.esp", extra=body)
        index = PluginFileIndex([str(data_dir)])

        coverage = core.build_cell_coverage(["Mine.esp"], index)

        assert coverage["exterior"][(1, 1)] == ["Mine.esp"]  # not ["Mine.esp", "Mine.esp"]

    def test_the_same_interior_cell_defined_twice_places_the_plugin_once(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        body = _interior_cell("Balmora, Guild") + _interior_cell("Balmora, Guild")
        write_plugin(data_dir / "Mine.esp", extra=body)
        index = PluginFileIndex([str(data_dir)])

        coverage = core.build_cell_coverage(["Mine.esp"], index)

        assert coverage["interior"]["Balmora, Guild"] == ["Mine.esp"]
