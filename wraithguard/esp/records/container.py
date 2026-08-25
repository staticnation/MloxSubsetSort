"""The ``CONT`` record -- a container. A port of ``types/container.rs``.

A chest, barrel, corpse or sack: its model and script, a carry-weight capacity,
its flags (organic, respawns), and its contents. Each ``NPCO`` inventory entry is
a count paired with a fixed thirty-two-byte item id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import ContainerFlags, ObjectFlags
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

_CNDT_SIZE = 4
_FLAG_SIZE = 4
_NPCO_SIZE = 36
_ITEM_ID_SIZE = 32


@register
@dataclass
class Container(Record):
    """A ``CONT`` record."""

    TAG: ClassVar[bytes] = b"CONT"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    encumbrance: float = 0.0
    container_flags: ContainerFlags = field(default_factory=lambda: ContainerFlags(0))
    inventory: list[tuple[int, str]] = field(default_factory=list)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Container:
        """Read the container's subrecords, collecting each inventory entry."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"CNDT":
                expect_size(reader, "CONT", "CNDT", _CNDT_SIZE)
                self.encumbrance = reader.f32()
            elif tag == b"FLAG":
                expect_size(reader, "CONT", "FLAG", _FLAG_SIZE)
                self.container_flags = ContainerFlags(reader.u32())
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"NPCO":
                expect_size(reader, "CONT", "NPCO", _NPCO_SIZE)
                count = reader.i32()
                self.inventory.append((count, reader.string_of(_ITEM_ID_SIZE)))
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("CONT", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order; each inventory entry fixed."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        writer.tag(b"CNDT")
        writer.u32(_CNDT_SIZE)
        writer.f32(self.encumbrance)
        writer.tag(b"FLAG")
        writer.u32(_FLAG_SIZE)
        writer.u32(int(self.container_flags))
        put_opt_string(writer, b"SCRI", self.script)
        for count, item_id in self.inventory:
            writer.tag(b"NPCO")
            writer.u32(_NPCO_SIZE)
            writer.i32(count)
            writer.fixed_string(item_id, _ITEM_ID_SIZE)
        write_dele(writer, self.flags)
