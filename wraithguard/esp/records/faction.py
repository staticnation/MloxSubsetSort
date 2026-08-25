"""The ``FACT`` record -- a faction. A port of ``types/faction.rs``.

A joinable faction: its display name, its rank titles, a large ``FADT`` block of
its favoured attributes and skills and the requirements to advance through each
of its ten ranks, and its opinion of other factions. Each rank title is a fixed
thirty-two-byte ``RNAM``; each reaction is an ``ANAM`` faction id followed by an
``INTV`` disposition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import AttributeId, SkillId
from wraithguard.esp.flags import FactionFlags, ObjectFlags
from wraithguard.esp.io import EspError
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    expect_size,
    put_fixed,
    put_fixed_string,
    put_opt_string,
    put_string,
    read_dele,
    unexpected,
    write_dele,
)

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_FADT_SIZE = 240
_RANKS = 10
_SKILLS = 7
_RANK_NAME_SIZE = 32
_REACTION_SIZE = 4


@dataclass
class FactionRequirement:
    """One rank's advancement requirements (20 bytes): attributes, skills, rep."""

    attributes: tuple[int, int] = (0, 0)
    primary_skill: int = 0
    favored_skill: int = 0
    reputation: int = 0

    @classmethod
    def load(cls, reader: Reader) -> FactionRequirement:
        """Read the 20-byte requirement in field order."""
        attributes = (reader.i32(), reader.i32())
        primary_skill = reader.i32()
        favored_skill = reader.i32()
        reputation = reader.i32()
        return cls(attributes, primary_skill, favored_skill, reputation)

    def save(self, writer: Writer) -> None:
        """Write the 20-byte requirement in field order."""
        writer.i32(self.attributes[0])
        writer.i32(self.attributes[1])
        writer.i32(self.primary_skill)
        writer.i32(self.favored_skill)
        writer.i32(self.reputation)


def _default_requirements() -> tuple[FactionRequirement, ...]:
    """One default :class:`FactionRequirement` per rank, for a fresh ``FADT``."""
    return tuple(FactionRequirement() for _ in range(_RANKS))


@dataclass
class FactionData:
    """The ``FADT`` block: favoured attributes/skills, per-rank requirements, flags."""

    favored_attributes: tuple[AttributeId, AttributeId] = field(
        default_factory=lambda: (AttributeId.default(), AttributeId.default())
    )
    requirements: tuple[FactionRequirement, ...] = field(default_factory=_default_requirements)
    favored_skills: tuple[SkillId, ...] = field(
        default_factory=lambda: tuple(SkillId.default() for _ in range(_SKILLS))
    )
    flags: FactionFlags = field(default_factory=lambda: FactionFlags(0))

    @classmethod
    def load(cls, reader: Reader) -> FactionData:
        """Read the 240-byte block: two attrs, ten requirements, seven skills, flags."""
        favored_attributes = (AttributeId(reader.i32()), AttributeId(reader.i32()))
        requirements = tuple(FactionRequirement.load(reader) for _ in range(_RANKS))
        favored_skills = tuple(SkillId(reader.i32()) for _ in range(_SKILLS))
        flags = FactionFlags(reader.u32())
        return cls(favored_attributes, requirements, favored_skills, flags)

    def save(self, writer: Writer) -> None:
        """Write the 240-byte block in field order."""
        writer.i32(int(self.favored_attributes[0]))
        writer.i32(int(self.favored_attributes[1]))
        for requirement in self.requirements:
            requirement.save(writer)
        for skill in self.favored_skills:
            writer.i32(int(skill))
        writer.u32(int(self.flags))


@dataclass
class FactionReaction:
    """A faction's disposition toward another (an ``ANAM`` id + ``INTV`` value)."""

    faction: str = ""
    reaction: int = 0

    @classmethod
    def load(cls, reader: Reader) -> FactionReaction:
        """Read the ``ANAM`` faction id (already tagged) then its ``INTV`` value."""
        faction = reader.string()
        intv = reader.tag()
        if intv != b"INTV":
            raise EspError(f"FACT: expected INTV after ANAM, got {intv!r}")
        expect_size(reader, "FACT", "INTV", _REACTION_SIZE)
        return cls(faction=faction, reaction=reader.i32())

    def save(self, writer: Writer) -> None:
        """Write the ``ANAM`` id and the ``INTV`` disposition."""
        writer.tag(b"ANAM")
        writer.string(self.faction)
        writer.tag(b"INTV")
        writer.u32(_REACTION_SIZE)
        writer.i32(self.reaction)


@register
@dataclass
class Faction(Record):
    """A ``FACT`` record."""

    TAG: ClassVar[bytes] = b"FACT"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    rank_names: list[str] = field(default_factory=list)
    reactions: list[FactionReaction] = field(default_factory=list)
    data: FactionData = field(default_factory=FactionData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Faction:
        """Read the faction's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"RNAM":
                self.rank_names.append(reader.string())
            elif tag == b"FADT":
                expect_size(reader, "FACT", "FADT", _FADT_SIZE)
                self.data = FactionData.load(reader)
            elif tag == b"ANAM":
                self.reactions.append(FactionReaction.load(reader))
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("FACT", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"FNAM", self.name)
        for rank_name in self.rank_names:
            put_fixed_string(writer, b"RNAM", rank_name, _RANK_NAME_SIZE)
        put_fixed(writer, b"FADT", _FADT_SIZE, self.data)
        for reaction in self.reactions:
            reaction.save(writer)
        write_dele(writer, self.flags)
