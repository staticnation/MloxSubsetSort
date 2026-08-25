"""The ``LAND`` landscape record -- large fixed-size terrain grids.

Which grids are written is gated by the landscape flags, so a cell that uses
heights/normals, colours and textures must write exactly those blocks (and the
world-map block any of them implies), each at its fixed size, with the height
map's three padding bytes reproduced. Round-trips byte-for-byte, and a
flags-only cell writes no grids.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import Landscape, read_plugin, write_plugin
from wraithguard.esp.flags import LandscapeFlags, ObjectFlags


def _sub(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack("<I", len(body)) + body


def _record(tag: bytes, subrecords: bytes, flags: int = 0) -> bytes:
    return (
        tag
        + struct.pack("<I", len(subrecords))
        + struct.pack("<I", 0)
        + struct.pack("<I", flags)
        + subrecords
    )


_ALL = (
    LandscapeFlags.USES_VERTEX_HEIGHTS_AND_NORMALS
    | LandscapeFlags.USES_VERTEX_COLORS
    | LandscapeFlags.USES_TEXTURES
)


def _full_land() -> bytes:
    vnml = bytes(range(256)) * 49 + bytes(12675 - 256 * 49)
    vhgt = (
        struct.pack("<f", -256.0)
        + bytes(range(200)) * 21
        + bytes(4225 - 200 * 21)
        + b"\x00\x00\x00"
    )
    wnam = bytes(range(81))
    vclr = (b"\x10\x20\x30" * 4225)[:12675]
    vtex = struct.pack("<256H", *range(256))
    return (
        _sub(b"INTV", struct.pack("<ii", -3, 5))
        + _sub(b"DATA", struct.pack("<I", int(_ALL)))
        + _sub(b"VNML", vnml)
        + _sub(b"VHGT", vhgt)
        + _sub(b"WNAM", wnam)
        + _sub(b"VCLR", vclr)
        + _sub(b"VTEX", vtex)
    )


class TestLandscape:
    def test_full_cell_round_trips(self) -> None:
        original = _record(b"LAND", _full_land())
        (land,) = read_plugin(original)
        assert isinstance(land, Landscape)
        assert land.grid == (-3, 5)
        assert LandscapeFlags.USES_TEXTURES in land.landscape_flags
        assert land.vertex_heights_offset == pytest.approx(-256.0)
        assert len(land.vertex_normals) == 12675
        assert len(land.vertex_heights) == 4225
        assert len(land.texture_indices) == 512
        assert write_plugin([land]) == original

    def test_flags_only_cell_writes_no_grids(self) -> None:
        body = _sub(b"INTV", struct.pack("<ii", 0, 0)) + _sub(b"DATA", struct.pack("<I", 0))
        original = _record(b"LAND", body)
        (land,) = read_plugin(original)
        assert land.landscape_flags == LandscapeFlags(0)
        assert write_plugin([land]) == original

    def test_heights_only_cell_still_writes_world_map(self) -> None:
        # USES_VERTEX_HEIGHTS_AND_NORMALS alone still implies a WNAM block.
        flags = LandscapeFlags.USES_VERTEX_HEIGHTS_AND_NORMALS
        vhgt = struct.pack("<f", 0.0) + bytes(4225) + b"\x00\x00\x00"
        body = (
            _sub(b"INTV", struct.pack("<ii", 1, 1))
            + _sub(b"DATA", struct.pack("<I", int(flags)))
            + _sub(b"VNML", bytes(12675))
            + _sub(b"VHGT", vhgt)
            + _sub(b"WNAM", bytes(81))
        )
        original = _record(b"LAND", body)
        (land,) = read_plugin(original)
        assert write_plugin([land]) == original

    def test_bad_block_size_is_refused(self) -> None:
        from wraithguard.esp import EspError

        body = _sub(b"INTV", struct.pack("<ii", 0, 0)) + _sub(b"VNML", bytes(100))
        with pytest.raises(EspError, match="VNML size"):
            read_plugin(_record(b"LAND", body))

    def test_unexpected_tag_and_deletion(self) -> None:
        from wraithguard.esp import EspError

        with pytest.raises(EspError, match="Unexpected Tag: LAND"):
            read_plugin(
                _record(
                    b"LAND",
                    _sub(b"INTV", struct.pack("<ii", 0, 0)) + b"ZZZZ" + struct.pack("<I", 0),
                )
            )
        body = _sub(b"INTV", struct.pack("<ii", 0, 0)) + _sub(b"DATA", struct.pack("<I", 0))
        body += b"DELE" + struct.pack("<II", 4, 0)
        original = _record(b"LAND", body, flags=int(ObjectFlags.DELETED))
        (land,) = read_plugin(original)
        assert ObjectFlags.DELETED in land.flags
        assert write_plugin([land]) == original
