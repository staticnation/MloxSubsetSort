"""The ``BODY`` record -- a body part. A port of ``types/bodypart.rs``.

One mesh for one part of one race's body (a head, a hand, a tail): an id, the
race it belongs to, its mesh, and a four-byte ``BYDT`` block naming the part, a
vampire flag, its flags, and whether it is skin, armor or clothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import BodypartId, BodypartType
from wraithguard.esp.flags import BodypartFlags, ObjectFlags
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

_BYDT_SIZE = 4


@dataclass
class BodypartData:
    """The ``BYDT`` block: part, vampire flag, flags and type (4 bytes)."""

    part: BodypartId = field(default_factory=BodypartId.default)
    vampire: bool = False
    flags: BodypartFlags = field(default_factory=lambda: BodypartFlags(0))
    bodypart_type: BodypartType = field(default_factory=BodypartType.default)

    @classmethod
    def load(cls, reader: Reader) -> BodypartData:
        """Read the 4 bytes: part, vampire (a byte), flags, type."""
        return cls(
            part=BodypartId(reader.u8()),
            vampire=reader.u8() != 0,
            flags=BodypartFlags(reader.u8()),
            bodypart_type=BodypartType(reader.u8()),
        )

    def save(self, writer: Writer) -> None:
        """Write the 4 bytes in field order."""
        writer.u8(int(self.part))
        writer.u8(1 if self.vampire else 0)
        writer.u8(int(self.flags))
        writer.u8(int(self.bodypart_type))


@register
@dataclass
class Bodypart(Record):
    """A ``BODY`` record."""

    TAG: ClassVar[bytes] = b"BODY"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    race: str = ""
    mesh: str = ""
    data: BodypartData = field(default_factory=BodypartData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Bodypart:
        """Read the body part's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"FNAM":
                self.race = reader.string()
            elif tag == b"BYDT":
                expect_size(reader, "BODY", "BYDT", _BYDT_SIZE)
                self.data = BodypartData.load(reader)
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("BODY", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.race)
        put_fixed(writer, b"BYDT", _BYDT_SIZE, self.data)
        write_dele(writer, self.flags)
