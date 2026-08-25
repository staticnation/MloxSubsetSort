"""The ``CELL`` record -- an interior or exterior cell. A port of ``types/cell.rs``.

A cell's header (its name, flags and grid, and optional region, map colour, water
height and lighting), followed by every object reference placed in it. Each
reference is framed by an ``FRMR`` carrying a packed master/object index; a moved
reference is preceded by an ``MVRF``/``CNDT`` giving its destination cell; and an
``NAM0`` marks where the persistent references end and the temporary ones begin.

References are kept in file order (the crate re-derives that order from a hash
map, but a file already in it round-trips identically), so a cell read and
written is byte-for-byte its original.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import CellFlags, ObjectFlags
from wraithguard.esp.io import EspError
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    expect_size,
    put_string,
    read_dele,
    unexpected,
    write_dele,
)
from wraithguard.esp.records.reference import Reference

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_DATA_SIZE = 12
_AMBI_SIZE = 16
_NAM5_SIZE = 4
_MVRF_SIZE = 4
_CNDT_SIZE = 8
_FRMR_SIZE = 4
_MAST_SHIFT = 24
_REFR_MASK = 0xFFFFFF


def _unpack(packed: int) -> tuple[int, int]:
    """Split a packed reference index into ``(master index, object index)``."""
    return packed >> _MAST_SHIFT, packed & _REFR_MASK


def _pack(mast_index: int, refr_index: int) -> int:
    """Fold a master and object index back into one packed word."""
    return refr_index | (mast_index << _MAST_SHIFT)


@dataclass
class CellData:
    """The ``DATA`` block: the cell's flags and its exterior grid (12 bytes)."""

    cell_flags: CellFlags = field(default_factory=lambda: CellFlags(0))
    grid: tuple[int, int] = (0, 0)

    @classmethod
    def load(cls, reader: Reader) -> CellData:
        """Read the 12-byte block in field order."""
        return cls(CellFlags(reader.u32()), (reader.i32(), reader.i32()))

    def save(self, writer: Writer) -> None:
        """Write the 12-byte block in field order."""
        writer.u32(int(self.cell_flags))
        writer.i32(self.grid[0])
        writer.i32(self.grid[1])


@dataclass
class AtmosphereData:
    """The ``AMBI`` block: the cell's interior lighting colours and fog (16 bytes)."""

    ambient_color: bytes = b"\x00\x00\x00\x00"
    sunlight_color: bytes = b"\x00\x00\x00\x00"
    fog_color: bytes = b"\x00\x00\x00\x00"
    fog_density: float = 0.0

    @classmethod
    def load(cls, reader: Reader) -> AtmosphereData:
        """Read the three RGBA colours and the fog density."""
        return cls(reader.raw(4), reader.raw(4), reader.raw(4), reader.f32())

    def save(self, writer: Writer) -> None:
        """Write the colours and fog density."""
        writer.raw(self.ambient_color)
        writer.raw(self.sunlight_color)
        writer.raw(self.fog_color)
        writer.f32(self.fog_density)


@register
@dataclass
class Cell(Record):
    """A ``CELL`` record."""

    TAG: ClassVar[bytes] = b"CELL"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    name: str = ""
    data: CellData = field(default_factory=CellData)
    region: str | None = None
    map_color: bytes | None = None
    water_height: float | None = None
    atmosphere_data: AtmosphereData | None = None
    references: list[Reference] = field(default_factory=list)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Cell:
        """Read the cell header and its references."""
        self = cls(flags=flags)
        num_temp_refs = 0
        pending_moved: tuple[int, int] | None = None
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.name = reader.string()
            elif tag == b"DATA":
                expect_size(reader, "CELL", "DATA", _DATA_SIZE)
                self.data = CellData.load(reader)
            elif tag == b"RGNN":
                self.region = reader.string()
            elif tag == b"NAM5":
                expect_size(reader, "CELL", "NAM5", _NAM5_SIZE)
                self.map_color = reader.raw(_NAM5_SIZE)
            elif tag == b"WHGT":
                expect_size(reader, "CELL", "WHGT", 4)
                self.water_height = reader.f32()
            elif tag == b"AMBI":
                size = reader.u32()
                self.atmosphere_data = AtmosphereData.load(reader)
                reader.skip(size - _AMBI_SIZE)  # some editors pad this subrecord
            elif tag == b"NAM0":
                expect_size(reader, "CELL", "NAM0", 4)
                num_temp_refs = reader.i32()
            elif tag == b"MVRF":
                expect_size(reader, "CELL", "MVRF", _MVRF_SIZE)
                reader.u32()  # packed index; reconstructed from the reference on save
                cndt = reader.tag()
                if cndt != b"CNDT":
                    raise EspError(f"CELL: expected CNDT after MVRF, got {cndt!r}")
                expect_size(reader, "CELL", "CNDT", _CNDT_SIZE)
                pending_moved = (reader.i32(), reader.i32())
            elif tag == b"FRMR":
                expect_size(reader, "CELL", "FRMR", _FRMR_SIZE)
                mast_index, refr_index = _unpack(reader.u32())
                reference = Reference.load(reader)
                reference.mast_index = mast_index
                reference.refr_index = refr_index
                reference.temporary = num_temp_refs > 0
                num_temp_refs -= 1
                reference.moved_cell = pending_moved
                pending_moved = None
                self.references.append(reference)
            elif tag == b"INTV":
                expect_size(reader, "CELL", "INTV", 4)
                self.water_height = float(reader.i32())  # old esp: an integer height
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("CELL", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the cell header, then the references in file order."""
        put_string(writer, b"NAME", self.name)
        writer.tag(b"DATA")
        writer.u32(_DATA_SIZE)
        self.data.save(writer)
        if self.region is not None:
            writer.tag(b"RGNN")
            writer.string(self.region)
        if self.map_color is not None:
            writer.tag(b"NAM5")
            writer.u32(_NAM5_SIZE)
            writer.raw(self.map_color)
        if self.water_height is not None:
            writer.tag(b"WHGT")
            writer.u32(4)
            writer.f32(self.water_height)
        if self.atmosphere_data is not None:
            writer.tag(b"AMBI")
            writer.u32(_AMBI_SIZE)
            self.atmosphere_data.save(writer)
        write_dele(writer, self.flags)
        self._save_references(writer)

    def _save_references(self, writer: Writer) -> None:
        """Write the references, with the ``NAM0`` temp marker and moved framing."""
        first_temp = next(
            (i for i, reference in enumerate(self.references) if not reference.persistent),
            None,
        )
        for i, reference in enumerate(self.references):
            if i == first_temp:
                writer.tag(b"NAM0")
                writer.u32(4)
                writer.u32(len(self.references) - i)
            packed = _pack(reference.mast_index, reference.refr_index)
            if reference.moved_cell is not None:
                writer.tag(b"MVRF")
                writer.u32(_MVRF_SIZE)
                writer.u32(packed)
                writer.tag(b"CNDT")
                writer.u32(_CNDT_SIZE)
                writer.i32(reference.moved_cell[0])
                writer.i32(reference.moved_cell[1])
            writer.tag(b"FRMR")
            writer.u32(_FRMR_SIZE)
            writer.u32(packed)
            reference.save(writer)
