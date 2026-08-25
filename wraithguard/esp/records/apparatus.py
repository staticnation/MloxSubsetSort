"""The ``APPA`` record -- an alchemy apparatus. A port of ``types/apparatus.rs``.

A mortar, alembic, calcinator or retort: the item strings plus a sixteen-byte
``AADT`` block of its type, quality, weight and value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import ApparatusType
from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    expect_size,
    put_fixed,
    put_opt_string,
    put_string,
    read_dele,
    unexpected,
    write_dele,
)

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_AADT_SIZE = 16


@dataclass
class ApparatusData:
    """The ``AADT`` block: an apparatus's type, quality, weight and value (16 bytes)."""

    apparatus_type: ApparatusType = ApparatusType.MortarAndPestle
    quality: float = 0.0
    weight: float = 0.0
    value: int = 0

    @classmethod
    def load(cls, reader: Reader) -> ApparatusData:
        """Read the 16-byte block in field order."""
        return cls(
            apparatus_type=ApparatusType(reader.u32()),
            quality=reader.f32(),
            weight=reader.f32(),
            value=reader.u32(),
        )

    def save(self, writer: Writer) -> None:
        """Write the 16-byte block in field order."""
        writer.u32(int(self.apparatus_type))
        writer.f32(self.quality)
        writer.f32(self.weight)
        writer.u32(self.value)


@register
@dataclass
class Apparatus(Record):
    """An ``APPA`` record."""

    TAG: ClassVar[bytes] = b"APPA"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    icon: str = ""
    data: ApparatusData = field(default_factory=ApparatusData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Apparatus:
        """Read the apparatus's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"AADT":
                expect_size(reader, "APPA", "AADT", _AADT_SIZE)
                self.data = ApparatusData.load(reader)
            elif tag == b"ITEX":
                self.icon = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("APPA", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        put_opt_string(writer, b"SCRI", self.script)
        put_fixed(writer, b"AADT", _AADT_SIZE, self.data)
        put_opt_string(writer, b"ITEX", self.icon)
        write_dele(writer, self.flags)
