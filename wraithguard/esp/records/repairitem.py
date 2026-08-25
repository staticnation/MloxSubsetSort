"""The ``REPA`` record -- a repair item. A port of ``types/repairitem.rs``.

A repair hammer or prong: the item strings plus a sixteen-byte ``RIDT`` block.
Its block orders uses before quality, unlike the lockpick's and probe's -- the
one thing that distinguishes them on the wire.
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

_RIDT_SIZE = 16


@dataclass
class RepairItemData:
    """The ``RIDT`` block: weight, value, uses and quality (16 bytes)."""

    weight: float = 0.0
    value: int = 0
    uses: int = 0
    quality: float = 0.0

    @classmethod
    def load(cls, reader: Reader) -> RepairItemData:
        """Read the 16-byte block in field order."""
        return cls(
            weight=reader.f32(),
            value=reader.u32(),
            uses=reader.u32(),
            quality=reader.f32(),
        )

    def save(self, writer: Writer) -> None:
        """Write the 16-byte block in field order."""
        writer.f32(self.weight)
        writer.u32(self.value)
        writer.u32(self.uses)
        writer.f32(self.quality)


@register
@dataclass
class RepairItem(Record):
    """A ``REPA`` record."""

    TAG: ClassVar[bytes] = b"REPA"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    icon: str = ""
    data: RepairItemData = field(default_factory=RepairItemData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> RepairItem:
        """Read the repair item's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"RIDT":
                expect_size(reader, "REPA", "RIDT", _RIDT_SIZE)
                self.data = RepairItemData.load(reader)
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"ITEX":
                self.icon = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("REPA", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        put_fixed(writer, b"RIDT", _RIDT_SIZE, self.data)
        put_opt_string(writer, b"SCRI", self.script)
        put_opt_string(writer, b"ITEX", self.icon)
        write_dele(writer, self.flags)
