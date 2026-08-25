"""The ``LIGH`` record -- a light. A port of ``types/light.rs``.

The item strings, an optional carry ``SNAM`` sound, and a twenty-four-byte
``LHDT`` block: weight, value, burn time, radius, an RGBA colour, and the light's
flags (dynamic, carriable, flickering, and so on).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import LightFlags, ObjectFlags
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

_LHDT_SIZE = 24


@dataclass
class LightData:
    """The ``LHDT`` block: weight, value, time, radius, colour, flags (24 bytes)."""

    weight: float = 0.0
    value: int = 0
    time: int = 0
    radius: int = 0
    color: bytes = b"\x00\x00\x00\x00"
    flags: LightFlags = field(default_factory=lambda: LightFlags(0))

    @classmethod
    def load(cls, reader: Reader) -> LightData:
        """Read the 24-byte block; colour is four raw bytes (R, G, B, A)."""
        return cls(
            weight=reader.f32(),
            value=reader.u32(),
            time=reader.i32(),
            radius=reader.u32(),
            color=reader.raw(4),
            flags=LightFlags(reader.u32()),
        )

    def save(self, writer: Writer) -> None:
        """Write the 24-byte block in field order."""
        writer.f32(self.weight)
        writer.u32(self.value)
        writer.i32(self.time)
        writer.u32(self.radius)
        writer.raw(self.color)
        writer.u32(int(self.flags))


@register
@dataclass
class Light(Record):
    """A ``LIGH`` record."""

    TAG: ClassVar[bytes] = b"LIGH"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    icon: str = ""
    sound: str = ""
    data: LightData = field(default_factory=LightData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Light:
        """Read the light's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"ITEX":
                self.icon = reader.string()
            elif tag == b"LHDT":
                expect_size(reader, "LIGH", "LHDT", _LHDT_SIZE)
                self.data = LightData.load(reader)
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"SNAM":
                self.sound = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("LIGH", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        put_opt_string(writer, b"ITEX", self.icon)
        put_fixed(writer, b"LHDT", _LHDT_SIZE, self.data)
        put_opt_string(writer, b"SCRI", self.script)
        put_opt_string(writer, b"SNAM", self.sound)
        write_dele(writer, self.flags)
