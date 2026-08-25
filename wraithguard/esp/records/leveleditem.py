"""The ``LEVI`` record -- a leveled item list. A port of ``types/leveleditem.rs``.

A list that resolves, when the game asks, to an item chosen by the player's
level: a chance the list yields nothing, and the entries themselves -- each an
item id (``INAM``) paired with the minimum player level it appears at (``INTV``).
An ``INDX`` gives the count up front; the pairs follow, id then level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import LeveledItemFlags, ObjectFlags
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
class LeveledItem(Record):
    """A ``LEVI`` record: a level-gated list of items."""

    TAG: ClassVar[bytes] = b"LEVI"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    leveled_item_flags: LeveledItemFlags = field(default_factory=lambda: LeveledItemFlags(0))
    chance_none: int = 0
    items: list[tuple[str, int]] = field(default_factory=list)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> LeveledItem:
        """Read the list: settings, then id/level pairs from ``INAM``/``INTV``."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"DATA":
                expect_size(reader, "LEVI", "DATA", _DATA_SIZE)
                self.leveled_item_flags = LeveledItemFlags(reader.u32())
            elif tag == b"NNAM":
                expect_size(reader, "LEVI", "NNAM", _NNAM_SIZE)
                self.chance_none = reader.u8()
            elif tag == b"INDX":
                expect_size(reader, "LEVI", "INDX", _COUNT_SIZE)
                reader.u32()  # entry count -- a hint; the list is built from the pairs
            elif tag == b"INAM":
                self.items.append((reader.string(), 0))
            elif tag == b"INTV":
                expect_size(reader, "LEVI", "INTV", _LEVEL_SIZE)
                if not self.items:
                    raise EspError("LEVI: INTV level without a preceding INAM item")
                self.items[-1] = (self.items[-1][0], reader.u16())
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("LEVI", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the settings, then the count and the id/level pairs."""
        put_string(writer, b"NAME", self.id)
        writer.tag(b"DATA")
        writer.u32(_DATA_SIZE)
        writer.u32(int(self.leveled_item_flags))
        writer.tag(b"NNAM")
        writer.u32(_NNAM_SIZE)
        writer.u8(self.chance_none)
        if self.items:
            writer.tag(b"INDX")
            writer.u32(_COUNT_SIZE)
            writer.u32(len(self.items))
            for item, level in self.items:
                writer.tag(b"INAM")
                writer.string(item)
                writer.tag(b"INTV")
                writer.u32(_LEVEL_SIZE)
                writer.u16(level)
        write_dele(writer, self.flags)
