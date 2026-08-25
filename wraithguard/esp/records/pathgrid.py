"""The ``PGRD`` record -- a path grid. A port of ``types/pathgrid.rs``.

The navigation mesh for one cell: a twelve-byte ``DATA`` header (the cell's grid
coordinates, a granularity, and the point count), the cell name, the points
themselves (``PGRP`` -- each a 3D location plus connection bookkeeping and two
bytes of padding), and the flat connection table (``PGRC``). Point and connection
blocks carry their byte length, from which the counts are derived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    expect_size,
    put_string,
    read_dele,
    unexpected,
    write_dele,
)

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_DATA_SIZE = 12
_POINT_SIZE = 16
_CONNECTION_SIZE = 4


@dataclass
class PathGridData:
    """The ``DATA`` header: grid coordinates, granularity, point count (12 bytes)."""

    grid: tuple[int, int] = (0, 0)
    granularity: int = 0
    point_count: int = 0

    @classmethod
    def load(cls, reader: Reader) -> PathGridData:
        """Read the 12-byte header in field order."""
        grid = (reader.i32(), reader.i32())
        granularity = reader.u16()
        point_count = reader.u16()
        return cls(grid, granularity, point_count)

    def save(self, writer: Writer) -> None:
        """Write the 12-byte header in field order."""
        writer.i32(self.grid[0])
        writer.i32(self.grid[1])
        writer.u16(self.granularity)
        writer.u16(self.point_count)


@dataclass
class PathGridPoint:
    """One path node (16 bytes): a 3D location and its connection bookkeeping."""

    location: tuple[int, int, int] = (0, 0, 0)
    auto_generated: int = 0
    connection_count: int = 0

    @classmethod
    def load(cls, reader: Reader) -> PathGridPoint:
        """Read the point: three i32 coordinates, two bytes, then two of padding."""
        location = (reader.i32(), reader.i32(), reader.i32())
        auto_generated = reader.u8()
        connection_count = reader.u8()
        reader.skip(2)  # padding
        return cls(location, auto_generated, connection_count)

    def save(self, writer: Writer) -> None:
        """Write the point and its two padding bytes."""
        writer.i32(self.location[0])
        writer.i32(self.location[1])
        writer.i32(self.location[2])
        writer.u8(self.auto_generated)
        writer.u8(self.connection_count)
        writer.raw(b"\x00\x00")


@register
@dataclass
class PathGrid(Record):
    """A ``PGRD`` record."""

    TAG: ClassVar[bytes] = b"PGRD"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    cell: str = ""
    data: PathGridData = field(default_factory=PathGridData)
    points: list[PathGridPoint] = field(default_factory=list)
    connections: list[int] = field(default_factory=list)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> PathGrid:
        """Read the path grid's subrecords, deriving list lengths from byte sizes."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.cell = reader.string()
            elif tag == b"DATA":
                expect_size(reader, "PGRD", "DATA", _DATA_SIZE)
                self.data = PathGridData.load(reader)
            elif tag == b"PGRP":
                count = reader.u32() // _POINT_SIZE
                self.points = [PathGridPoint.load(reader) for _ in range(count)]
            elif tag == b"PGRC":
                count = reader.u32() // _CONNECTION_SIZE
                self.connections = [reader.u32() for _ in range(count)]
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("PGRD", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order (``DATA`` before ``NAME``)."""
        writer.tag(b"DATA")
        writer.u32(_DATA_SIZE)
        self.data.save(writer)
        put_string(writer, b"NAME", self.cell)
        if self.points:
            writer.tag(b"PGRP")
            writer.u32(len(self.points) * _POINT_SIZE)
            for point in self.points:
                point.save(writer)
        if self.connections:
            writer.tag(b"PGRC")
            writer.u32(len(self.connections) * _CONNECTION_SIZE)
            for connection in self.connections:
                writer.u32(connection)
        write_dele(writer, self.flags)
