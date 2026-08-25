"""The ``CLAS`` record -- a character class. A port of ``types/class.rs``.

A class is its two favoured attributes, its specialization, its ten
minor/major skills, and its flags and the services it offers -- all in a
sixty-byte ``CLDT`` block -- plus a name and description.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import AttributeId, SkillId, Specialization
from wraithguard.esp.flags import ClassFlags, ObjectFlags, ServiceFlags
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

_CLDT_SIZE = 60
_SKILLS = 10


@dataclass
class ClassData:
    """The ``CLDT`` block: attributes, specialization, skills, flags, services."""

    attribute1: AttributeId = field(default_factory=AttributeId.default)
    attribute2: AttributeId = field(default_factory=AttributeId.default)
    specialization: Specialization = field(default_factory=Specialization.default)
    skills: tuple[SkillId, ...] = field(
        default_factory=lambda: tuple(SkillId.default() for _ in range(_SKILLS))
    )
    flags: ClassFlags = field(default_factory=lambda: ClassFlags(0))
    services: ServiceFlags = field(default_factory=lambda: ServiceFlags(0))

    @classmethod
    def load(cls, reader: Reader) -> ClassData:
        """Read the 60-byte block: two attrs, specialization, ten skills, two flags.

        The ten skills are read in the crate's interleaved minor/major order and
        kept in that order, so writing them back is byte-identical.
        """
        attribute1 = AttributeId(reader.i32())
        attribute2 = AttributeId(reader.i32())
        specialization = Specialization(reader.i32())
        skills = tuple(SkillId(reader.i32()) for _ in range(_SKILLS))
        flags = ClassFlags(reader.u32())
        services = ServiceFlags(reader.u32())
        return cls(attribute1, attribute2, specialization, skills, flags, services)

    def save(self, writer: Writer) -> None:
        """Write the 60-byte block in field order."""
        writer.i32(int(self.attribute1))
        writer.i32(int(self.attribute2))
        writer.i32(int(self.specialization))
        for skill in self.skills:
            writer.i32(int(skill))
        writer.u32(int(self.flags))
        writer.u32(int(self.services))


@register
@dataclass
class Class(Record):
    """A ``CLAS`` record."""

    TAG: ClassVar[bytes] = b"CLAS"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    description: str = ""
    data: ClassData = field(default_factory=ClassData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Class:
        """Read the class's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"CLDT":
                expect_size(reader, "CLAS", "CLDT", _CLDT_SIZE)
                self.data = ClassData.load(reader)
            elif tag == b"DESC":
                self.description = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("CLAS", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"FNAM", self.name)
        put_fixed(writer, b"CLDT", _CLDT_SIZE, self.data)
        put_opt_string(writer, b"DESC", self.description)
        write_dele(writer, self.flags)
