"""The ``ALCH`` record -- a potion. A port of ``types/alchemy.rs``.

The item strings (its icon carried in a ``TEXT`` subrecord, not ``ITEX``), a
twelve-byte ``ALDT`` block of weight, value and an auto-calculate flag, and a
list of ``ENAM`` effect blocks -- one per magic effect the potion has.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import AlchemyFlags, ObjectFlags
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

_ALDT_SIZE = 12


@dataclass
class AlchemyData:
    """The ``ALDT`` block: weight, value and flags (12 bytes)."""

    weight: float = 0.0
    value: int = 0
    flags: AlchemyFlags = field(default_factory=lambda: AlchemyFlags(0))

    @classmethod
    def load(cls, reader: Reader) -> AlchemyData:
        """Read the 12-byte block in field order."""
        return cls(
            weight=reader.f32(),
            value=reader.u32(),
            flags=AlchemyFlags(reader.u32()),
        )

    def save(self, writer: Writer) -> None:
        """Write the 12-byte block in field order."""
        writer.f32(self.weight)
        writer.u32(self.value)
        writer.u32(int(self.flags))


@register
@dataclass
class Alchemy(Record):
    """An ``ALCH`` record."""

    TAG: ClassVar[bytes] = b"ALCH"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    icon: str = ""
    effects: list[Effect] = field(default_factory=list)
    data: AlchemyData = field(default_factory=AlchemyData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Alchemy:
        """Read the potion's subrecords, collecting each ``ENAM`` effect."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"TEXT":
                self.icon = reader.string()
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"ALDT":
                expect_size(reader, "ALCH", "ALDT", _ALDT_SIZE)
                self.data = AlchemyData.load(reader)
            elif tag == b"ENAM":
                expect_size(reader, "ALCH", "ENAM", EFFECT_SIZE)
                self.effects.append(Effect.load(reader))
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("ALCH", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order; one ``ENAM`` per effect."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"TEXT", self.icon)
        put_opt_string(writer, b"SCRI", self.script)
        put_opt_string(writer, b"FNAM", self.name)
        put_fixed(writer, b"ALDT", _ALDT_SIZE, self.data)
        for effect in self.effects:
            put_fixed(writer, b"ENAM", EFFECT_SIZE, effect)
        write_dele(writer, self.flags)
