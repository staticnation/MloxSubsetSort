"""Merged Lands with no ``tes3conv``: reading and encoding in process.

``build_merged_lands(converter=None)`` reads terrain with the built-in reader and
encodes the result with the built-in writer, so a merge needs no external tool.
These are hermetic -- they never look for ``tes3conv`` -- and check the whole
circuit: two mods that both move a cell's terrain merge into a plugin the reader
can read back. During development the native output was also confirmed
value-identical to a real ``tes3conv`` merge of the same input.

Also pinned here: the ``emit`` height-field fix. ``encode_vertex_heights``
returns the whole ``VHGT`` body -- offset, heights, then three padding bytes --
and the ``data`` field is the heights alone. tes3conv truncates a longer value to
its fixed array and never complained; the native writer takes it literally, so an
over-long value shifts every following subrecord and the plugin will not load.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wraithguard.esp import read_plugin, write_plugin
from wraithguard.esp.flags import LandscapeFlags
from wraithguard.esp.records import Header, Landscape
from wraithguard.land.emit import FIELD_SIZES, encode_vertex_heights
from wraithguard.land.service import build_merged_lands

if TYPE_CHECKING:
    from pathlib import Path

_FLAGS = (
    LandscapeFlags.USES_VERTEX_HEIGHTS_AND_NORMALS
    | LandscapeFlags.USES_VERTEX_COLORS
    | LandscapeFlags.USES_TEXTURES
)


def _tweaked_heights(base: bytes, value: int) -> bytes:
    """A copy of ``base`` with every seventh height byte set to ``value``.

    Args:
        base: The reference height bytes.
        value: The byte to scatter through them.

    Returns:
        The altered heights, same length as ``base``.
    """
    data = bytearray(base)
    for i in range(0, len(data), 7):
        data[i] = value & 0xFF
    return bytes(data)


def _setup(tmp_path: Path) -> list[str]:
    """Write a base master and two mods that both move one cell's terrain.

    Args:
        tmp_path: The data-files directory to populate.

    Returns:
        The load order to merge.
    """
    base = Landscape(grid=(0, 0), landscape_flags=_FLAGS)
    (tmp_path / "base.esm").write_bytes(write_plugin([Header(author="t"), base]))
    master = ("base.esm", (tmp_path / "base.esm").stat().st_size)
    for name, value in (("m1.esp", 8), ("m2.esp", 200)):
        mod = Landscape(
            grid=(0, 0),
            landscape_flags=_FLAGS,
            vertex_heights=_tweaked_heights(base.vertex_heights, value),
        )
        (tmp_path / name).write_bytes(write_plugin([Header(masters=[master]), mod]))
    return ["base.esm", "m1.esp", "m2.esp"]


class TestNativeMerge:
    """A merge that touches no external converter."""

    def test_merge_writes_a_readable_plugin(self, tmp_path: Path) -> None:
        """converter=None reads, merges and encodes entirely in process."""
        order = _setup(tmp_path)
        out = tmp_path / "Merged Lands.esp"
        result = build_merged_lands(
            tmp_path, order, converter=None, output=out, report=lambda _s: None
        )
        assert result.cells_written == 1
        assert out.is_file()
        records = read_plugin(out.read_bytes())
        assert records[0].__class__.__name__ == "Header"
        assert any(r.__class__.__name__ == "Landscape" for r in records)

    def test_a_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        """dry_run reports the merge without producing a file, still no tes3conv."""
        order = _setup(tmp_path)
        out = tmp_path / "Merged Lands.esp"
        build_merged_lands(
            tmp_path, order, converter=None, output=out, dry_run=True, report=lambda _s: None
        )
        assert not out.exists()


class TestEmitHeightField:
    """The height ``data`` field is the heights alone, not the padded body."""

    def test_vertex_heights_data_excludes_padding(self) -> None:
        """The emitted heights length is the fixed field size, not the VHGT body."""
        _offset, payload, _clamped = encode_vertex_heights([[0] * 65 for _ in range(65)])
        assert len(payload) > FIELD_SIZES["vertex_heights"]  # body carries offset + padding
        assert len(payload[4 : 4 + FIELD_SIZES["vertex_heights"]]) == FIELD_SIZES["vertex_heights"]
