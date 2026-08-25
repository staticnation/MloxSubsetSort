"""The ``CLOT`` record -- a piece of clothing. A port of ``types/clothing.rs``.

The same shape as armor, with a smaller twelve-byte ``CTDT`` block (type, weight,
value and enchantment points) and the same list of biped-object groups for the
body slots it covers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import ClothingType
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
from wraithguard.esp.records.bipedobject import BipedObject

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_CTDT_SIZE = 12


@dataclass
class ClothingData:
    """The ``CTDT`` block: type, weight, value and enchantment (12 bytes)."""

    clothing_type: ClothingType = field(default_factory=ClothingType.default)
    weight: float = 0.0
    value: int = 0
    enchantment: int = 0

    @classmethod
    def load(cls, reader: Reader) -> ClothingData:
        """Read the 12-byte block; value and enchantment are 16-bit."""
        return cls(
            clothing_type=ClothingType(reader.u32()),
            weight=reader.f32(),
            value=reader.u16(),
            enchantment=reader.u16(),
        )

    def save(self, writer: Writer) -> None:
        """Write the 12-byte block in field order."""
        writer.u32(int(self.clothing_type))
        writer.f32(self.weight)
        writer.u16(self.value)
        writer.u16(self.enchantment)


@register
@dataclass
class Clothing(Record):
    """A ``CLOT`` record."""

    TAG: ClassVar[bytes] = b"CLOT"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    icon: str = ""
    enchanting: str = ""
    biped_objects: list[BipedObject] = field(default_factory=list)
    data: ClothingData = field(default_factory=ClothingData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Clothing:
        """Read the clothing's subrecords, collecting each biped-object group."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"CTDT":
                expect_size(reader, "CLOT", "CTDT", _CTDT_SIZE)
                self.data = ClothingData.load(reader)
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"ITEX":
                self.icon = reader.string()
            elif tag == b"INDX":
                self.biped_objects.append(BipedObject.load(reader))
            elif tag == b"ENAM":
                self.enchanting = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("CLOT", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order; biped groups after the icon."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        put_fixed(writer, b"CTDT", _CTDT_SIZE, self.data)
        put_opt_string(writer, b"SCRI", self.script)
        put_opt_string(writer, b"ITEX", self.icon)
        for biped in self.biped_objects:
            biped.save(writer)
        put_opt_string(writer, b"ENAM", self.enchanting)
        write_dele(writer, self.flags)
