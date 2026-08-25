"""The ``SKIL`` record -- a skill definition. A port of ``types/skill.rs``.

One of the game's twenty-seven skills: which skill (``INDX``), a twenty-four-byte
``SKDT`` block (its governing attribute, its specialization, and the four
use-value gains that raise it), and a description. A skill has no id string --
it is identified by its index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import SkillId
from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    expect_size,
    put_fixed,
    put_opt_string,
    read_dele,
    unexpected,
    write_dele,
)

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_INDX_SIZE = 4
_SKDT_SIZE = 24
_ACTIONS = 4


@dataclass
class SkillData:
    """The ``SKDT`` block: governing attribute, specialization, action gains (24 bytes)."""

    governing_attribute: int = 0
    specialization: int = 0
    actions: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0)

    @classmethod
    def load(cls, reader: Reader) -> SkillData:
        """Read the 24-byte block: two i32 ids then four f32 gains."""
        governing_attribute = reader.i32()
        specialization = reader.i32()
        actions = tuple(reader.f32() for _ in range(_ACTIONS))
        return cls(governing_attribute, specialization, actions)

    def save(self, writer: Writer) -> None:
        """Write the 24-byte block in field order."""
        writer.i32(self.governing_attribute)
        writer.i32(self.specialization)
        for action in self.actions:
            writer.f32(action)


@register
@dataclass
class Skill(Record):
    """A ``SKIL`` record."""

    TAG: ClassVar[bytes] = b"SKIL"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    skill_id: SkillId = field(default_factory=SkillId.default)
    data: SkillData = field(default_factory=SkillData)
    description: str = ""

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Skill:
        """Read the skill's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"INDX":
                expect_size(reader, "SKIL", "INDX", _INDX_SIZE)
                self.skill_id = SkillId(reader.i32())
            elif tag == b"SKDT":
                expect_size(reader, "SKIL", "SKDT", _SKDT_SIZE)
                self.data = SkillData.load(reader)
            elif tag == b"DESC":
                self.description = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("SKIL", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        writer.tag(b"INDX")
        writer.u32(_INDX_SIZE)
        writer.i32(int(self.skill_id))
        put_fixed(writer, b"SKDT", _SKDT_SIZE, self.data)
        put_opt_string(writer, b"DESC", self.description)
        write_dele(writer, self.flags)
