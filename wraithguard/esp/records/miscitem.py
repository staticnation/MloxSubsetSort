"""The ``MISC`` record -- a miscellaneous item. A port of ``types/miscitem.rs``.

Gold, keys, tools, clutter: the item strings plus a twelve-byte ``MCDT`` block
of weight, value and a flag that marks the item a key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import MiscItemFlags, ObjectFlags
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

_MCDT_SIZE = 12


@dataclass
class MiscItemData:
    """The ``MCDT`` block: weight, value and flags (12 bytes)."""

    weight: float = 0.0
    value: int = 0
    flags: MiscItemFlags = field(default_factory=lambda: MiscItemFlags(0))

    @classmethod
    def load(cls, reader: Reader) -> MiscItemData:
        """Read the 12-byte block in field order."""
        return cls(
            weight=reader.f32(),
            value=reader.u32(),
            flags=MiscItemFlags(reader.u32()),
        )

    def save(self, writer: Writer) -> None:
        """Write the 12-byte block in field order."""
        writer.f32(self.weight)
        writer.u32(self.value)
        writer.u32(int(self.flags))


@register
@dataclass
class MiscItem(Record):
    """A ``MISC`` record."""

    TAG: ClassVar[bytes] = b"MISC"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    icon: str = ""
    data: MiscItemData = field(default_factory=MiscItemData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> MiscItem:
        """Read the item's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"MCDT":
                expect_size(reader, "MISC", "MCDT", _MCDT_SIZE)
                self.data = MiscItemData.load(reader)
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"ITEX":
                self.icon = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("MISC", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        put_fixed(writer, b"MCDT", _MCDT_SIZE, self.data)
        put_opt_string(writer, b"SCRI", self.script)
        put_opt_string(writer, b"ITEX", self.icon)
        write_dele(writer, self.flags)
