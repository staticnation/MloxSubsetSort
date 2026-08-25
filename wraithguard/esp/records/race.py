"""The ``RACE`` record -- a playable race. A port of ``types/race.rs``.

A race's name, its granted spells, its description, and a 140-byte ``RADT`` block:
seven skill bonuses, the male/female range of each of the eight attributes, the
male/female height and weight, and its flags (playable, beast). Each spell is a
fixed thirty-two-byte ``NPCS`` id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import SkillId
from wraithguard.esp.flags import ObjectFlags, RaceFlags
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

_RADT_SIZE = 140
_SKILL_BONUSES = 7
_NPCS_SIZE = 32
#: The eight attributes, each a male/female pair, in their block order.
_ATTRS = (
    "strength",
    "intelligence",
    "willpower",
    "agility",
    "speed",
    "endurance",
    "personality",
    "luck",
)


@dataclass
class RaceData:
    """The ``RADT`` block: skill bonuses, attribute/size ranges, flags (140 bytes)."""

    skill_bonuses: tuple[tuple[SkillId, int], ...] = field(
        default_factory=lambda: tuple((SkillId.default(), 0) for _ in range(_SKILL_BONUSES))
    )
    strength: tuple[int, int] = (0, 0)
    intelligence: tuple[int, int] = (0, 0)
    willpower: tuple[int, int] = (0, 0)
    agility: tuple[int, int] = (0, 0)
    speed: tuple[int, int] = (0, 0)
    endurance: tuple[int, int] = (0, 0)
    personality: tuple[int, int] = (0, 0)
    luck: tuple[int, int] = (0, 0)
    height: tuple[float, float] = (0.0, 0.0)
    weight: tuple[float, float] = (0.0, 0.0)
    flags: RaceFlags = field(default_factory=lambda: RaceFlags(0))

    @classmethod
    def load(cls, reader: Reader) -> RaceData:
        """Read the 140-byte block in field order."""
        bonuses = tuple((SkillId(reader.i32()), reader.i32()) for _ in range(_SKILL_BONUSES))
        attrs = {name: (reader.i32(), reader.i32()) for name in _ATTRS}
        height = (reader.f32(), reader.f32())
        weight = (reader.f32(), reader.f32())
        flags = RaceFlags(reader.u32())
        return cls(skill_bonuses=bonuses, height=height, weight=weight, flags=flags, **attrs)

    def save(self, writer: Writer) -> None:
        """Write the 140-byte block in field order."""
        for skill, bonus in self.skill_bonuses:
            writer.i32(int(skill))
            writer.i32(bonus)
        for name in _ATTRS:
            low, high = getattr(self, name)
            writer.i32(low)
            writer.i32(high)
        writer.f32(self.height[0])
        writer.f32(self.height[1])
        writer.f32(self.weight[0])
        writer.f32(self.weight[1])
        writer.u32(int(self.flags))


@register
@dataclass
class Race(Record):
    """A ``RACE`` record."""

    TAG: ClassVar[bytes] = b"RACE"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    spells: list[str] = field(default_factory=list)
    description: str = ""
    data: RaceData = field(default_factory=RaceData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Race:
        """Read the race's subrecords, collecting each granted spell id."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"RADT":
                expect_size(reader, "RACE", "RADT", _RADT_SIZE)
                self.data = RaceData.load(reader)
            elif tag == b"NPCS":
                self.spells.append(reader.string())
            elif tag == b"DESC":
                self.description = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("RACE", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order; each spell a fixed field."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"FNAM", self.name)
        put_fixed(writer, b"RADT", _RADT_SIZE, self.data)
        for spell in self.spells:
            put_fixed_string(writer, b"NPCS", spell, _NPCS_SIZE)
        put_opt_string(writer, b"DESC", self.description)
        write_dele(writer, self.flags)
