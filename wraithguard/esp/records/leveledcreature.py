"""The ``LEVC`` record -- a leveled creature list. A port of ``types/leveledcreature.rs``.

The creature counterpart of the leveled item list, identical in shape: a chance
of nothing, and creature id (``CNAM``) / minimum-level (``INTV``) pairs, counted by
an ``INDX``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import LeveledCreatureFlags, ObjectFlags
from wraithguard.esp.io import EspError
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

_DATA_SIZE = 4
_COUNT_SIZE = 4
_NNAM_SIZE = 1
_LEVEL_SIZE = 2


@register
@dataclass
class LeveledCreature(Record):
    """A ``LEVC`` record: a level-gated list of creatures."""

    TAG: ClassVar[bytes] = b"LEVC"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    leveled_creature_flags: LeveledCreatureFlags = field(
        default_factory=lambda: LeveledCreatureFlags(0)
    )
    chance_none: int = 0
    creatures: list[tuple[str, int]] = field(default_factory=list)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> LeveledCreature:
        """Read the list: settings, then id/level pairs from ``CNAM``/``INTV``."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"DATA":
                expect_size(reader, "LEVC", "DATA", _DATA_SIZE)
                self.leveled_creature_flags = LeveledCreatureFlags(reader.u32())
            elif tag == b"NNAM":
                expect_size(reader, "LEVC", "NNAM", _NNAM_SIZE)
                self.chance_none = reader.u8()
            elif tag == b"INDX":
                expect_size(reader, "LEVC", "INDX", _COUNT_SIZE)
                reader.u32()  # entry count -- a hint; the list is built from the pairs
            elif tag == b"CNAM":
                self.creatures.append((reader.string(), 0))
            elif tag == b"INTV":
                expect_size(reader, "LEVC", "INTV", _LEVEL_SIZE)
                if not self.creatures:
                    raise EspError("LEVC: INTV level without a preceding CNAM creature")
                self.creatures[-1] = (self.creatures[-1][0], reader.u16())
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("LEVC", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the settings, then the count and the id/level pairs."""
        put_string(writer, b"NAME", self.id)
        writer.tag(b"DATA")
        writer.u32(_DATA_SIZE)
        writer.u32(int(self.leveled_creature_flags))
        writer.tag(b"NNAM")
        writer.u32(_NNAM_SIZE)
        writer.u8(self.chance_none)
        if self.creatures:
            writer.tag(b"INDX")
            writer.u32(_COUNT_SIZE)
            writer.u32(len(self.creatures))
            for creature, level in self.creatures:
                writer.tag(b"CNAM")
                writer.string(creature)
                writer.tag(b"INTV")
                writer.u32(_LEVEL_SIZE)
                writer.u16(level)
        write_dele(writer, self.flags)
