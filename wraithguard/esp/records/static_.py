"""The ``STAT`` record -- a static object. A port of ``types/static_.rs``.

The simplest record there is: an id and a mesh. Nothing but a model placed in
the world.
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
class Static(Record):
    """A ``STAT`` record."""

    TAG: ClassVar[bytes] = b"STAT"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    mesh: str = ""

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Static:
        """Read the static's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("STAT", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        write_dele(writer, self.flags)
