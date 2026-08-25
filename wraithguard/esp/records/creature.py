"""The ``CREA`` record -- a creature. A port of ``types/creature.rs``.

The creature counterpart of the NPC, sharing its inventory, spells and AI
machinery. It differs in its data: a fixed ninety-six-byte ``NPDT`` of the
creature's type, level, attributes, pools, soul value, the AI weights and three
attack ranges; a sound (in ``CNAM``); and an optional scale (``XSCL``) that the
crate clamps to 0.5..2.0 and omits when it is the default 1.0 -- reproduced here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import CreatureType
from wraithguard.esp.flags import CreatureFlags, ObjectFlags
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._ai import (
    AiData,
    AiPackage,
    TravelDestination,
    is_ai_tag,
    load_ai_package,
    save_ai_package,
)
from wraithguard.esp.records._common import (
    expect_size,
    put_fixed_string,
    put_opt_string,
    put_string,
    read_dele,
    unexpected,
    write_dele,
)

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_NPDT_SIZE = 96
_AIDT_SIZE = 12
_DODT_SIZE = 24
_FLAG_SIZE = 4
_XSCL_SIZE = 4
_NPCO_SIZE = 36
_ITEM_ID_SIZE = 32
_SPELL_SIZE = 32
_CREATURE_FLAG_MASK = 0xFF
_BLOOD_SHIFT = 10
_BLOOD_MASK = 0b111
_SCALE_MIN = 0.5
_SCALE_MAX = 2.0
_SCALE_EPSILON = 1e-6
#: The sixteen ``u32`` scalars between the creature type and the attack ranges,
#: in block order.
_SCALARS = (
    "level",
    "strength",
    "intelligence",
    "willpower",
    "agility",
    "speed",
    "endurance",
    "personality",
    "luck",
    "health",
    "magicka",
    "fatigue",
    "soul",
    "combat",
    "magic",
    "stealth",
)


@dataclass
class CreatureData:
    """The ``NPDT`` block: type, level, stats, pools, soul, AI weights, attacks."""

    creature_type: CreatureType = field(default_factory=CreatureType.default)
    level: int = 0
    strength: int = 0
    intelligence: int = 0
    willpower: int = 0
    agility: int = 0
    speed: int = 0
    endurance: int = 0
    personality: int = 0
    luck: int = 0
    health: int = 0
    magicka: int = 0
    fatigue: int = 0
    soul: int = 0
    combat: int = 0
    magic: int = 0
    stealth: int = 0
    attack1: tuple[int, int] = (0, 0)
    attack2: tuple[int, int] = (0, 0)
    attack3: tuple[int, int] = (0, 0)
    gold: int = 0

    @classmethod
    def load(cls, reader: Reader) -> CreatureData:
        """Read the 96-byte block in field order (every field a u32)."""
        self = cls(creature_type=CreatureType(reader.u32()))
        for name in _SCALARS:
            setattr(self, name, reader.u32())
        self.attack1 = (reader.u32(), reader.u32())
        self.attack2 = (reader.u32(), reader.u32())
        self.attack3 = (reader.u32(), reader.u32())
        self.gold = reader.u32()
        return self

    def save(self, writer: Writer) -> None:
        """Write the 96-byte block in field order."""
        writer.u32(int(self.creature_type))
        for name in _SCALARS:
            writer.u32(getattr(self, name))
        for low, high in (self.attack1, self.attack2, self.attack3):
            writer.u32(low)
            writer.u32(high)
        writer.u32(self.gold)


def _unpack_flags(value: int) -> tuple[CreatureFlags, int]:
    """Split the ``FLAG`` word into creature flags (low byte) and blood type."""
    return CreatureFlags(value & _CREATURE_FLAG_MASK), (value >> _BLOOD_SHIFT) & _BLOOD_MASK


def _pack_flags(creature_flags: CreatureFlags, blood_type: int) -> int:
    """Fold creature flags and blood type back into the ``FLAG`` word."""
    return int(creature_flags) | ((blood_type & _BLOOD_MASK) << _BLOOD_SHIFT)


@register
@dataclass
class Creature(Record):
    """A ``CREA`` record."""

    TAG: ClassVar[bytes] = b"CREA"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    sound: str = ""
    scale: float | None = None
    creature_flags: CreatureFlags = field(default_factory=lambda: CreatureFlags(0))
    blood_type: int = 0
    inventory: list[tuple[int, str]] = field(default_factory=list)
    spells: list[str] = field(default_factory=list)
    ai_data: AiData = field(default_factory=AiData)
    ai_packages: list[AiPackage] = field(default_factory=list)
    travel_destinations: list[TravelDestination] = field(default_factory=list)
    data: CreatureData = field(default_factory=CreatureData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Creature:
        """Read the creature's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"CNAM":
                self.sound = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"NPDT":
                expect_size(reader, "CREA", "NPDT", _NPDT_SIZE)
                self.data = CreatureData.load(reader)
            elif tag == b"FLAG":
                expect_size(reader, "CREA", "FLAG", _FLAG_SIZE)
                self.creature_flags, self.blood_type = _unpack_flags(reader.u32())
            elif tag == b"XSCL":
                expect_size(reader, "CREA", "XSCL", _XSCL_SIZE)
                self.scale = reader.f32()
            elif tag == b"NPCO":
                expect_size(reader, "CREA", "NPCO", _NPCO_SIZE)
                count = reader.i32()
                self.inventory.append((count, reader.string_of(_ITEM_ID_SIZE)))
            elif tag == b"NPCS":
                self.spells.append(reader.string())
            elif tag == b"AIDT":
                expect_size(reader, "CREA", "AIDT", _AIDT_SIZE)
                self.ai_data = AiData.load(reader)
            elif tag == b"DODT":
                expect_size(reader, "CREA", "DODT", _DODT_SIZE)
                self.travel_destinations.append(TravelDestination.load(reader))
            elif is_ai_tag(tag):
                self.ai_packages.append(load_ai_package(reader, tag, "CREA"))
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("CREA", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"CNAM", self.sound)
        put_opt_string(writer, b"FNAM", self.name)
        put_opt_string(writer, b"SCRI", self.script)
        writer.tag(b"NPDT")
        writer.u32(_NPDT_SIZE)
        self.data.save(writer)
        writer.tag(b"FLAG")
        writer.u32(_FLAG_SIZE)
        writer.u32(_pack_flags(self.creature_flags, self.blood_type))
        self._save_scale(writer)
        for count, item_id in self.inventory:
            writer.tag(b"NPCO")
            writer.u32(_NPCO_SIZE)
            writer.i32(count)
            writer.fixed_string(item_id, _ITEM_ID_SIZE)
        for spell in self.spells:
            put_fixed_string(writer, b"NPCS", spell, _SPELL_SIZE)
        writer.tag(b"AIDT")
        writer.u32(_AIDT_SIZE)
        self.ai_data.save(writer)
        for destination in self.travel_destinations:
            writer.tag(b"DODT")
            writer.u32(_DODT_SIZE)
            destination.save(writer)
        for package in self.ai_packages:
            save_ai_package(writer, package)
        write_dele(writer, self.flags)

    def _save_scale(self, writer: Writer) -> None:
        """Write ``XSCL`` only for a present, non-default scale, clamped, as the crate."""
        if self.scale is None:
            return
        scale = min(max(self.scale, _SCALE_MIN), _SCALE_MAX)
        if abs(scale - 1.0) < _SCALE_EPSILON:
            return
        writer.tag(b"XSCL")
        writer.u32(_XSCL_SIZE)
        writer.f32(scale)
