"""The ``ARMO`` record -- a piece of armor. A port of ``types/armor.rs``.

The item strings, a twenty-four-byte ``AODT`` block (type, weight, value, health,
enchantment points and armor rating), and a list of biped-object groups -- the
body slots the armor covers and their meshes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import ArmorType
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

_AODT_SIZE = 24


@dataclass
class ArmorData:
    """The ``AODT`` block: type, weight, value, health, enchantment, rating (24 bytes)."""

    armor_type: ArmorType = field(default_factory=ArmorType.default)
    weight: float = 0.0
    value: int = 0
    health: int = 0
    enchantment: int = 0
    armor_rating: int = 0

    @classmethod
    def load(cls, reader: Reader) -> ArmorData:
        """Read the 24-byte block in field order."""
        return cls(
            armor_type=ArmorType(reader.u32()),
            weight=reader.f32(),
            value=reader.u32(),
            health=reader.u32(),
            enchantment=reader.u32(),
            armor_rating=reader.u32(),
        )

    def save(self, writer: Writer) -> None:
        """Write the 24-byte block in field order."""
        writer.u32(int(self.armor_type))
        writer.f32(self.weight)
        writer.u32(self.value)
        writer.u32(self.health)
        writer.u32(self.enchantment)
        writer.u32(self.armor_rating)


@register
@dataclass
class Armor(Record):
    """An ``ARMO`` record."""

    TAG: ClassVar[bytes] = b"ARMO"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    icon: str = ""
    enchanting: str = ""
    biped_objects: list[BipedObject] = field(default_factory=list)
    data: ArmorData = field(default_factory=ArmorData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Armor:
        """Read the armor's subrecords, collecting each biped-object group."""
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
            elif tag == b"AODT":
                expect_size(reader, "ARMO", "AODT", _AODT_SIZE)
                self.data = ArmorData.load(reader)
            elif tag == b"ITEX":
                self.icon = reader.string()
            elif tag == b"INDX":
                self.biped_objects.append(BipedObject.load(reader))
            elif tag == b"ENAM":
                self.enchanting = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("ARMO", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order; biped groups after the icon."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        put_opt_string(writer, b"SCRI", self.script)
        put_fixed(writer, b"AODT", _AODT_SIZE, self.data)
        put_opt_string(writer, b"ITEX", self.icon)
        for biped in self.biped_objects:
            biped.save(writer)
        put_opt_string(writer, b"ENAM", self.enchanting)
        write_dele(writer, self.flags)
