"""The ``LAND`` record -- a landscape cell. A port of ``types/landscape.rs``.

The terrain of one exterior cell: which cell (``INTV``), which data it carries
(``DATA``), and then large fixed-size grids -- vertex normals (``VNML``), a
height map with its base offset (``VHGT``), the coarse world-map colours
(``WNAM``), vertex colours (``VCLR``) and the per-quad texture indices (``VTEX``).

The grids are kept as raw bytes here: the library's job is to preserve them
exactly, and :mod:`wraithguard.land` already decodes them into heights, normals
and layers. Which grids are written is gated by the landscape flags, exactly as
the crate does, so a cell round-trips byte-for-byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import LandscapeFlags, ObjectFlags
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    expect_size,
    read_dele,
    unexpected,
    write_dele,
)

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_GRID_SIZE = 8
_FLAGS_SIZE = 4
_VNML_SIZE = 12675  # 65 x 65 x 3 signed bytes
_VHGT_SIZE = 4232  # f32 offset + 65 x 65 signed bytes + 3 padding
_VHGT_DATA = 4225  # 65 x 65
_WNAM_SIZE = 81  # 9 x 9 signed bytes
_VCLR_SIZE = 12675  # 65 x 65 x 3 bytes
_VTEX_SIZE = 512  # 16 x 16 u16
#: Any of these flags means the coarse world-map data (``WNAM``) is written.
_WNAM_FLAGS = (
    LandscapeFlags.USES_VERTEX_HEIGHTS_AND_NORMALS
    | LandscapeFlags.USES_VERTEX_COLORS
    | LandscapeFlags.USES_TEXTURES
)


@register
@dataclass
class Landscape(Record):
    """A ``LAND`` record: one exterior cell's terrain grids."""

    TAG: ClassVar[bytes] = b"LAND"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    grid: tuple[int, int] = (0, 0)
    landscape_flags: LandscapeFlags = field(default_factory=lambda: LandscapeFlags(0))
    vertex_normals: bytes = field(default_factory=lambda: bytes(_VNML_SIZE))
    vertex_heights_offset: float = 0.0
    vertex_heights: bytes = field(default_factory=lambda: bytes(_VHGT_DATA))
    world_map_data: bytes = field(default_factory=lambda: bytes(_WNAM_SIZE))
    vertex_colors: bytes = field(default_factory=lambda: bytes(_VCLR_SIZE))
    texture_indices: bytes = field(default_factory=lambda: bytes(_VTEX_SIZE))

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Landscape:
        """Read the cell's subrecords, keeping each grid as raw bytes."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"INTV":
                expect_size(reader, "LAND", "INTV", _GRID_SIZE)
                self.grid = (reader.i32(), reader.i32())
            elif tag == b"DATA":
                expect_size(reader, "LAND", "DATA", _FLAGS_SIZE)
                self.landscape_flags = LandscapeFlags(reader.u32())
            elif tag == b"VNML":
                expect_size(reader, "LAND", "VNML", _VNML_SIZE)
                self.vertex_normals = reader.raw(_VNML_SIZE)
            elif tag == b"VHGT":
                expect_size(reader, "LAND", "VHGT", _VHGT_SIZE)
                self.vertex_heights_offset = reader.f32()
                self.vertex_heights = reader.raw(_VHGT_DATA)
                reader.skip(3)  # padding
            elif tag == b"WNAM":
                expect_size(reader, "LAND", "WNAM", _WNAM_SIZE)
                self.world_map_data = reader.raw(_WNAM_SIZE)
            elif tag == b"VCLR":
                expect_size(reader, "LAND", "VCLR", _VCLR_SIZE)
                self.vertex_colors = reader.raw(_VCLR_SIZE)
            elif tag == b"VTEX":
                expect_size(reader, "LAND", "VTEX", _VTEX_SIZE)
                self.texture_indices = reader.raw(_VTEX_SIZE)
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("LAND", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the grids the landscape flags call for, in the crate's order."""
        writer.tag(b"INTV")
        writer.u32(_GRID_SIZE)
        writer.i32(self.grid[0])
        writer.i32(self.grid[1])
        writer.tag(b"DATA")
        writer.u32(_FLAGS_SIZE)
        writer.u32(int(self.landscape_flags))
        if LandscapeFlags.USES_VERTEX_HEIGHTS_AND_NORMALS in self.landscape_flags:
            writer.tag(b"VNML")
            writer.u32(_VNML_SIZE)
            writer.raw(self.vertex_normals)
            writer.tag(b"VHGT")
            writer.u32(_VHGT_SIZE)
            writer.f32(self.vertex_heights_offset)
            writer.raw(self.vertex_heights)
            writer.raw(b"\x00\x00\x00")  # padding
        if self.landscape_flags & _WNAM_FLAGS:
            writer.tag(b"WNAM")
            writer.u32(_WNAM_SIZE)
            writer.raw(self.world_map_data)
        if LandscapeFlags.USES_VERTEX_COLORS in self.landscape_flags:
            writer.tag(b"VCLR")
            writer.u32(_VCLR_SIZE)
            writer.raw(self.vertex_colors)
        if LandscapeFlags.USES_TEXTURES in self.landscape_flags:
            writer.tag(b"VTEX")
            writer.u32(_VTEX_SIZE)
            writer.raw(self.texture_indices)
        write_dele(writer, self.flags)
