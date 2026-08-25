"""The ``SSCR`` record -- a start script. A port of ``types/startscript.rs``.

A script the game runs at startup. Unusually, its id is stored under ``DATA`` and
the script name under ``NAME`` -- the reverse of the usual convention, so the
tags are mapped deliberately here.
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
class StartScript(Record):
    """An ``SSCR`` record."""

    TAG: ClassVar[bytes] = b"SSCR"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    script: str = ""

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> StartScript:
        """Read the subrecords: ``DATA`` is the id, ``NAME`` the script."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"DATA":
                self.id = reader.string()
            elif tag == b"NAME":
                self.script = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("SSCR", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"DATA", self.id)
        put_opt_string(writer, b"NAME", self.script)
        write_dele(writer, self.flags)
