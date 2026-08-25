"""The ``SNDG`` record -- a creature sound generator. A port of ``types/soundgen.rs``.

Binds a creature to a sound for a kind of event (a footstep, a moan, a swim
stroke): an id, a four-byte ``DATA`` giving the event type, the creature id, and
the sound id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import SoundGenType
from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    expect_size,
    put_opt_string,
    put_string,
    read_dele,
    unexpected,
    write_dele,
)

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_DATA_SIZE = 4


@register
@dataclass
class SoundGen(Record):
    """An ``SNDG`` record."""

    TAG: ClassVar[bytes] = b"SNDG"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    sound_gen_type: SoundGenType = field(default_factory=SoundGenType.default)
    creature: str = ""
    sound: str = ""

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> SoundGen:
        """Read the sound generator's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"DATA":
                expect_size(reader, "SNDG", "DATA", _DATA_SIZE)
                self.sound_gen_type = SoundGenType(reader.u32())
            elif tag == b"CNAM":
                self.creature = reader.string()
            elif tag == b"SNAM":
                self.sound = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("SNDG", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        writer.tag(b"DATA")
        writer.u32(_DATA_SIZE)
        writer.u32(int(self.sound_gen_type))
        put_opt_string(writer, b"CNAM", self.creature)
        put_opt_string(writer, b"SNAM", self.sound)
        write_dele(writer, self.flags)
