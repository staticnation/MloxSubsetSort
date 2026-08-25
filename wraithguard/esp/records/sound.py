"""The ``SOUN`` record -- a sound. A port of ``types/sound.rs``.

An id, the sound file it plays, and a three-byte ``DATA`` block: a volume and a
min/max audible range.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import ObjectFlags
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

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_DATA_SIZE = 3


@dataclass
class SoundData:
    """The ``DATA`` block: volume and the (min, max) range (3 bytes)."""

    volume: int = 0
    range: tuple[int, int] = (0, 0)

    @classmethod
    def load(cls, reader: Reader) -> SoundData:
        """Read the 3 bytes: volume, then the two range bytes."""
        return cls(volume=reader.u8(), range=(reader.u8(), reader.u8()))

    def save(self, writer: Writer) -> None:
        """Write the 3 bytes in field order."""
        writer.u8(self.volume)
        writer.u8(self.range[0])
        writer.u8(self.range[1])


@register
@dataclass
class Sound(Record):
    """A ``SOUN`` record."""

    TAG: ClassVar[bytes] = b"SOUN"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    sound_path: str = ""
    data: SoundData = field(default_factory=SoundData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Sound:
        """Read the sound's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"FNAM":
                self.sound_path = reader.string()
            elif tag == b"DATA":
                expect_size(reader, "SOUN", "DATA", _DATA_SIZE)
                self.data = SoundData.load(reader)
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("SOUN", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"FNAM", self.sound_path)
        put_fixed(writer, b"DATA", _DATA_SIZE, self.data)
        write_dele(writer, self.flags)
