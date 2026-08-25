"""The ``BOOK`` record -- a book or scroll. A port of ``types/book.rs``.

The item strings plus a ``TEXT`` (the pages) and a twenty-byte ``BKDT`` block:
weight, value, whether it is a book or a scroll, the skill a skill-book teaches,
and any enchantment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import BookType, SkillId
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

_BKDT_SIZE = 20


@dataclass
class BookData:
    """The ``BKDT`` block: weight, value, type, skill and enchantment (20 bytes)."""

    weight: float = 0.0
    value: int = 0
    book_type: BookType = BookType.Book
    skill: SkillId = SkillId.None_
    enchantment: int = 0

    @classmethod
    def load(cls, reader: Reader) -> BookData:
        """Read the 20-byte block in field order (skill id is signed)."""
        return cls(
            weight=reader.f32(),
            value=reader.u32(),
            book_type=BookType(reader.u32()),
            skill=SkillId(reader.i32()),
            enchantment=reader.u32(),
        )

    def save(self, writer: Writer) -> None:
        """Write the 20-byte block in field order."""
        writer.f32(self.weight)
        writer.u32(self.value)
        writer.u32(int(self.book_type))
        writer.i32(int(self.skill))
        writer.u32(self.enchantment)


@register
@dataclass
class Book(Record):
    """A ``BOOK`` record."""

    TAG: ClassVar[bytes] = b"BOOK"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    icon: str = ""
    enchanting: str = ""
    text: str = ""
    data: BookData = field(default_factory=BookData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Book:
        """Read the book's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"BKDT":
                expect_size(reader, "BOOK", "BKDT", _BKDT_SIZE)
                self.data = BookData.load(reader)
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"ITEX":
                self.icon = reader.string()
            elif tag == b"TEXT":
                self.text = reader.string()
            elif tag == b"ENAM":
                self.enchanting = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("BOOK", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        put_fixed(writer, b"BKDT", _BKDT_SIZE, self.data)
        put_opt_string(writer, b"SCRI", self.script)
        put_opt_string(writer, b"ITEX", self.icon)
        put_opt_string(writer, b"TEXT", self.text)
        put_opt_string(writer, b"ENAM", self.enchanting)
        write_dele(writer, self.flags)
