"""The ``ENCH`` record -- an enchantment. A port of ``types/enchanting.rs``.

The enchantment carried by an enchanted item: a sixteen-byte ``ENDT`` block (how
it is cast, its cost, its charge and its flags) and a list of ``ENAM`` effects,
the same shared effect block potions and spells use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import EnchantType
from wraithguard.esp.flags import EnchantingFlags, ObjectFlags
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    expect_size,
    put_fixed,
    put_string,
    read_dele,
    unexpected,
    write_dele,
)
from wraithguard.esp.records.effect import EFFECT_SIZE, Effect

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_ENDT_SIZE = 16


@dataclass
class EnchantingData:
    """The ``ENDT`` block: cast type, cost, max charge and flags (16 bytes)."""

    enchant_type: EnchantType = field(default_factory=EnchantType.default)
    cost: int = 0
    max_charge: int = 0
    flags: EnchantingFlags = field(default_factory=lambda: EnchantingFlags(0))

    @classmethod
    def load(cls, reader: Reader) -> EnchantingData:
        """Read the 16-byte block in field order."""
        return cls(
            enchant_type=EnchantType(reader.u32()),
            cost=reader.u32(),
            max_charge=reader.u32(),
            flags=EnchantingFlags(reader.u32()),
        )

    def save(self, writer: Writer) -> None:
        """Write the 16-byte block in field order."""
        writer.u32(int(self.enchant_type))
        writer.u32(self.cost)
        writer.u32(self.max_charge)
        writer.u32(int(self.flags))


@register
@dataclass
class Enchanting(Record):
    """An ``ENCH`` record."""

    TAG: ClassVar[bytes] = b"ENCH"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    effects: list[Effect] = field(default_factory=list)
    data: EnchantingData = field(default_factory=EnchantingData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Enchanting:
        """Read the enchantment's subrecords, collecting each ``ENAM`` effect."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"ENDT":
                expect_size(reader, "ENCH", "ENDT", _ENDT_SIZE)
                self.data = EnchantingData.load(reader)
            elif tag == b"ENAM":
                expect_size(reader, "ENCH", "ENAM", EFFECT_SIZE)
                self.effects.append(Effect.load(reader))
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("ENCH", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order; one ``ENAM`` per effect."""
        put_string(writer, b"NAME", self.id)
        put_fixed(writer, b"ENDT", _ENDT_SIZE, self.data)
        for effect in self.effects:
            put_fixed(writer, b"ENAM", EFFECT_SIZE, effect)
        write_dele(writer, self.flags)
