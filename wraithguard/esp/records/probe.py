"""The ``PROB`` record -- a probe. A port of ``types/probe.rs``.

Identical in shape to a lockpick: the item strings plus a sixteen-byte ``PBDT``
block of weight, value, quality and uses.
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

_PBDT_SIZE = 16


@dataclass
class ProbeData:
    """The ``PBDT`` block: weight, value, quality and uses (16 bytes)."""

    weight: float = 0.0
    value: int = 0
    quality: float = 0.0
    uses: int = 0

    @classmethod
    def load(cls, reader: Reader) -> ProbeData:
        """Read the 16-byte block in field order."""
        return cls(
            weight=reader.f32(),
            value=reader.u32(),
            quality=reader.f32(),
            uses=reader.u32(),
        )

    def save(self, writer: Writer) -> None:
        """Write the 16-byte block in field order."""
        writer.f32(self.weight)
        writer.u32(self.value)
        writer.f32(self.quality)
        writer.u32(self.uses)


@register
@dataclass
class Probe(Record):
    """A ``PROB`` record."""

    TAG: ClassVar[bytes] = b"PROB"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    icon: str = ""
    data: ProbeData = field(default_factory=ProbeData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Probe:
        """Read the probe's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"PBDT":
                expect_size(reader, "PROB", "PBDT", _PBDT_SIZE)
                self.data = ProbeData.load(reader)
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"ITEX":
                self.icon = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("PROB", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        put_fixed(writer, b"PBDT", _PBDT_SIZE, self.data)
        put_opt_string(writer, b"SCRI", self.script)
        put_opt_string(writer, b"ITEX", self.icon)
        write_dele(writer, self.flags)
