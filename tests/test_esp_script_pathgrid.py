"""The script record and the path grid.

The script keeps its variable table and bytecode as opaque byte blocks preserved
verbatim, plus a fixed-width name and a header of counts. The path grid derives
its point and connection counts from the byte length of each block, and each
point carries two bytes of padding that must be reproduced. Both round-trip
byte-for-byte.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import (
    EspError,
    PathGrid,
    PathGridPoint,
    Script,
    read_plugin,
    write_plugin,
)
from wraithguard.esp.flags import ObjectFlags


def _sub(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack("<I", len(body)) + body


def _string_sub(tag: bytes, text: str) -> bytes:
    return _sub(tag, text.encode("cp1252") + b"\x00")


def _record(tag: bytes, subrecords: bytes, flags: int = 0) -> bytes:
    return (
        tag
        + struct.pack("<I", len(subrecords))
        + struct.pack("<I", 0)
        + struct.pack("<I", flags)
        + subrecords
    )


def _fixed(text: str, size: int) -> bytes:
    return text.encode("cp1252").ljust(size, b"\x00")


class TestScript:
    def test_round_trips_with_blocks(self) -> None:
        schd = _fixed("myScript", 32) + struct.pack("<5I", 1, 2, 3, 40, 12)
        body = (
            _sub(b"SCHD", schd)
            + _sub(b"SCVR", b"a\x00b\x00count\x00")
            + _sub(b"SCDT", b"\x01\x02\x03\x04")
            + _string_sub(b"SCTX", "begin myScript\nend")
        )
        original = _record(b"SCPT", body)
        (script,) = read_plugin(original)
        assert isinstance(script, Script)
        assert script.id == "myScript"
        assert script.header.num_shorts == 1
        assert script.header.bytecode_length == 40
        assert script.variables == b"a\x00b\x00count\x00"
        assert script.bytecode == b"\x01\x02\x03\x04"
        assert script.text == "begin myScript\nend"
        assert write_plugin([script]) == original

    def test_minimal_script_round_trips(self) -> None:
        schd = _fixed("empty", 32) + struct.pack("<5I", 0, 0, 0, 0, 0)
        original = _record(b"SCPT", _sub(b"SCHD", schd))
        (script,) = read_plugin(original)
        assert script.variables == b""
        assert script.bytecode == b""
        assert write_plugin([script]) == original

    def test_unexpected_tag_and_deletion(self) -> None:
        schd = _fixed("s", 32) + struct.pack("<5I", 0, 0, 0, 0, 0)
        with pytest.raises(EspError, match="Unexpected Tag: SCPT"):
            read_plugin(_record(b"SCPT", _sub(b"SCHD", schd) + b"ZZZZ" + struct.pack("<I", 0)))
        body = _sub(b"SCHD", schd) + b"DELE" + struct.pack("<II", 4, 0)
        original = _record(b"SCPT", body, flags=int(ObjectFlags.DELETED))
        (script,) = read_plugin(original)
        assert ObjectFlags.DELETED in script.flags
        assert write_plugin([script]) == original


def _point(x: int, y: int, z: int, auto: int, conns: int) -> bytes:
    return struct.pack("<3iBB", x, y, z, auto, conns) + b"\x00\x00"


class TestPathGrid:
    def test_points_and_connections_round_trip(self) -> None:
        data = struct.pack("<iiHH", -2, 3, 0, 2)
        pgrp = _point(0, 0, 100, 0, 1) + _point(500, 0, 100, 0, 1)
        pgrc = struct.pack("<2I", 1, 0)
        body = (
            _sub(b"DATA", data)
            + _string_sub(b"NAME", "Balmora")
            + _sub(b"PGRP", pgrp)
            + _sub(b"PGRC", pgrc)
        )
        original = _record(b"PGRD", body)
        (grid,) = read_plugin(original)
        assert isinstance(grid, PathGrid)
        assert grid.cell == "Balmora"
        assert grid.data.grid == (-2, 3)
        assert grid.data.point_count == 2
        assert len(grid.points) == 2
        assert grid.points[1].location == (500, 0, 100)
        assert grid.connections == [1, 0]
        assert write_plugin([grid]) == original

    def test_point_padding_is_preserved(self) -> None:
        point = PathGridPoint(location=(1, 2, 3), auto_generated=1, connection_count=4)
        assert point.location == (1, 2, 3)
        data = struct.pack("<iiHH", 0, 0, 0, 1)
        body = (
            _sub(b"DATA", data)
            + _string_sub(b"NAME", "cell")
            + _sub(b"PGRP", _point(1, 2, 3, 1, 4))
        )
        original = _record(b"PGRD", body)
        (grid,) = read_plugin(original)
        assert write_plugin([grid]) == original

    def test_empty_grid_and_deletion(self) -> None:
        data = struct.pack("<iiHH", 0, 0, 0, 0)
        body = (
            _sub(b"DATA", data) + _string_sub(b"NAME", "gone") + b"DELE" + struct.pack("<II", 4, 0)
        )
        original = _record(b"PGRD", body, flags=int(ObjectFlags.DELETED))
        (grid,) = read_plugin(original)
        assert grid.points == []
        assert grid.connections == []
        assert ObjectFlags.DELETED in grid.flags
        assert write_plugin([grid]) == original

    def test_unexpected_tag_is_refused(self) -> None:
        data = struct.pack("<iiHH", 0, 0, 0, 0)
        with pytest.raises(EspError, match="Unexpected Tag: PGRD"):
            read_plugin(_record(b"PGRD", _sub(b"DATA", data) + b"ZZZZ" + struct.pack("<I", 0)))
