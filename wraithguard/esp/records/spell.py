"""The ``SPEL`` record -- a spell. A port of ``types/spell.rs``.

A castable spell (or a granted ability, disease or curse): a name, a twelve-byte
``SPDT`` block (its kind, its cost and its flags) and a list of ``ENAM`` effects,
the shared effect block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import SpellType
from wraithguard.esp.flags import ObjectFlags, SpellFlags
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
from wraithguard.esp.records.effect import EFFECT_SIZE, Effect

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_SPDT_SIZE = 12


@dataclass
class SpellData:
    """The ``SPDT`` block: spell type, cost and flags (12 bytes)."""

    spell_type: SpellType = field(default_factory=SpellType.default)
    cost: int = 0
    flags: SpellFlags = field(default_factory=lambda: SpellFlags(0))

    @classmethod
    def load(cls, reader: Reader) -> SpellData:
        """Read the 12-byte block in field order."""
        return cls(
            spell_type=SpellType(reader.u32()),
            cost=reader.u32(),
            flags=SpellFlags(reader.u32()),
        )

    def save(self, writer: Writer) -> None:
        """Write the 12-byte block in field order."""
        writer.u32(int(self.spell_type))
        writer.u32(self.cost)
        writer.u32(int(self.flags))


@register
@dataclass
class Spell(Record):
    """A ``SPEL`` record."""

    TAG: ClassVar[bytes] = b"SPEL"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    effects: list[Effect] = field(default_factory=list)
    data: SpellData = field(default_factory=SpellData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Spell:
        """Read the spell's subrecords, collecting each ``ENAM`` effect."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"SPDT":
                expect_size(reader, "SPEL", "SPDT", _SPDT_SIZE)
                self.data = SpellData.load(reader)
            elif tag == b"ENAM":
                expect_size(reader, "SPEL", "ENAM", EFFECT_SIZE)
                self.effects.append(Effect.load(reader))
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("SPEL", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order; one ``ENAM`` per effect."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"FNAM", self.name)
        put_fixed(writer, b"SPDT", _SPDT_SIZE, self.data)
        for effect in self.effects:
            put_fixed(writer, b"ENAM", EFFECT_SIZE, effect)
        write_dele(writer, self.flags)
