"""The ``BSGN`` record -- a birthsign. A port of ``types/birthsign.rs``.

A name, a menu texture, a description, and the spells or abilities it grants.
Each granted spell is an ``NPCS`` subrecord holding a fixed thirty-two-byte id,
so the list is written with that fixed width and read back null-truncated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    put_fixed_string,
    put_opt_string,
    put_string,
    read_dele,
    unexpected,
    write_dele,
)

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

#: The fixed width of an ``NPCS`` spell-id field, in bytes.
_NPCS_SIZE = 32


@register
@dataclass
class Birthsign(Record):
    """A ``BSGN`` record."""

    TAG: ClassVar[bytes] = b"BSGN"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    texture: str = ""
    description: str = ""
    spells: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Birthsign:
        """Read the birthsign's subrecords, collecting each granted spell id."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"TNAM":
                self.texture = reader.string()
            elif tag == b"DESC":
                self.description = reader.string()
            elif tag == b"NPCS":
                self.spells.append(reader.string())
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("BSGN", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order; each spell as a fixed field."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"FNAM", self.name)
        put_opt_string(writer, b"TNAM", self.texture)
        put_opt_string(writer, b"DESC", self.description)
        for spell in self.spells:
            put_fixed_string(writer, b"NPCS", spell, _NPCS_SIZE)
        write_dele(writer, self.flags)
