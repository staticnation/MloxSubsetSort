"""The ``GMST`` record -- a game setting. A port of ``types/gamesetting.rs``.

A named engine constant. Its value is one of three types, and which subrecord
carries it says which: a string in ``STRV``, a float in ``FLTV``, an integer in
``INTV``. The value's Python type stands in for the crate's tagged union -- a
``str``, ``float`` or ``int`` -- and is written back to the matching subrecord.
There is no ``DELE`` here; a game setting is never a deletion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import expect_size, put_string, unexpected

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_VALUE_SIZE = 4


@register
@dataclass
class GameSetting(Record):
    """A ``GMST`` record: a named value that is a string, float or integer."""

    TAG: ClassVar[bytes] = b"GMST"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    value: str | float | int = 0.0

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> GameSetting:
        """Read the id and whichever of ``STRV``/``FLTV``/``INTV`` carries the value."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"STRV":
                self.value = reader.string()
            elif tag == b"FLTV":
                expect_size(reader, "GMST", "FLTV", _VALUE_SIZE)
                self.value = reader.f32()
            elif tag == b"INTV":
                expect_size(reader, "GMST", "INTV", _VALUE_SIZE)
                self.value = reader.i32()
            else:
                raise unexpected("GMST", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the id, then the value in the subrecord its type calls for."""
        put_string(writer, b"NAME", self.id)
        # bool is an int subclass but no game setting is a bool, so str/float/int
        # cover the union exactly.
        if isinstance(self.value, str):
            writer.tag(b"STRV")
            writer.string(self.value)
        elif isinstance(self.value, float):
            writer.tag(b"FLTV")
            writer.u32(_VALUE_SIZE)
            writer.f32(self.value)
        else:
            writer.tag(b"INTV")
            writer.u32(_VALUE_SIZE)
            writer.i32(self.value)
