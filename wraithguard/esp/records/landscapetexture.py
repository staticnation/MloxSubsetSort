"""The ``LTEX`` record -- a landscape texture. A port of ``types/landscapetexture.rs``.

Names a terrain texture and gives it the index that landscape records refer to
it by: an id, a four-byte ``INTV`` index, and the texture file in ``DATA``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

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

_INTV_SIZE = 4


@register
@dataclass
class LandscapeTexture(Record):
    """An ``LTEX`` record."""

    TAG: ClassVar[bytes] = b"LTEX"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    index: int = 0
    file_name: str = ""

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> LandscapeTexture:
        """Read the texture's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"INTV":
                expect_size(reader, "LTEX", "INTV", _INTV_SIZE)
                self.index = reader.u32()
            elif tag == b"DATA":
                self.file_name = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("LTEX", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        writer.tag(b"INTV")
        writer.u32(_INTV_SIZE)
        writer.u32(self.index)
        put_opt_string(writer, b"DATA", self.file_name)
        write_dele(writer, self.flags)
