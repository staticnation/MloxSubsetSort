"""The ``DOOR`` record -- a door. A port of ``types/door.rs``.

Like an activator, plus the two sounds a door makes: the ``SNAM`` it opens with
and the ``ANAM`` it closes with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    put_opt_string,
    put_string,
    read_dele,
    unexpected,
    write_dele,
)

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer


@register
@dataclass
class Door(Record):
    """A ``DOOR`` record."""

    TAG: ClassVar[bytes] = b"DOOR"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    open_sound: str = ""
    close_sound: str = ""

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Door:
        """Read the door's subrecords."""
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
            elif tag == b"SNAM":
                self.open_sound = reader.string()
            elif tag == b"ANAM":
                self.close_sound = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("DOOR", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        put_opt_string(writer, b"SCRI", self.script)
        put_opt_string(writer, b"SNAM", self.open_sound)
        put_opt_string(writer, b"ANAM", self.close_sound)
        write_dele(writer, self.flags)
