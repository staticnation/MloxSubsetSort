"""The ``NPC_`` record -- a non-player character. A port of ``types/npc.rs``.

The most involved record: a name, race, class, faction, head and hair meshes and
a script; an ``NPDT`` stats block that comes in two forms (full stats, or a short
auto-calculated one); a packed ``FLAG`` word that folds the NPC flags together
with a three-bit blood type; an inventory and a spell list; and the shared AI
data, AI packages and travel destinations. Every piece round-trips byte-for-byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import NpcFlags, ObjectFlags
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

_AIDT_SIZE = 12
_DODT_SIZE = 24
_FLAG_SIZE = 4
_NPCO_SIZE = 36
_ITEM_ID_SIZE = 32
_SPELL_SIZE = 32
#: The bits ``NpcFlags`` defines; the ``FLAG`` word's low byte, truncated to
#: these, is the flags, and bits 10-12 are the blood type (as the crate does).
_NPC_FLAG_MASK = 0x1F
_BLOOD_SHIFT = 10
_BLOOD_MASK = 0b111

_NPDT_FULL = 52
_NPDT_AUTO = 12
_ATTRS_SIZE = 8
_SKILLS_SIZE = 27


@dataclass
class NpcStats:
    """The full ``NPDT`` stats: attributes, skills and the derived pools (42 bytes)."""

    attributes: bytes = field(default_factory=lambda: bytes(_ATTRS_SIZE))
    skills: bytes = field(default_factory=lambda: bytes(_SKILLS_SIZE))
    health: int = 0
    magicka: int = 0
    fatigue: int = 0

    @classmethod
    def load(cls, reader: Reader) -> NpcStats:
        """Read the eight attributes, 27 skills (a byte of padding), then pools."""
        attributes = reader.raw(_ATTRS_SIZE)
        skills = reader.raw(_SKILLS_SIZE)
        reader.skip(1)  # padding
        return cls(attributes, skills, reader.u16(), reader.u16(), reader.u16())

    def save(self, writer: Writer) -> None:
        """Write the stats in field order."""
        writer.raw(self.attributes)
        writer.raw(self.skills)
        writer.raw(b"\x00")
        writer.u16(self.health)
        writer.u16(self.magicka)
        writer.u16(self.fatigue)


@dataclass
class NpcData:
    """The ``NPDT`` block: level, optional full stats, standing, and gold."""

    level: int = 0
    stats: NpcStats | None = None
    disposition: int = 0
    reputation: int = 0
    rank: int = 0
    gold: int = 0

    @classmethod
    def load(cls, reader: Reader) -> NpcData:
        """Read the block, whose own size word is 52 (full) or 12 (auto-calc)."""
        length = reader.u32()
        self = cls(level=reader.i16())
        if length == _NPDT_FULL:
            self.stats = NpcStats.load(reader)
            self.disposition = reader.i8()
            self.reputation = reader.i8()
            self.rank = reader.i8()
            reader.skip(1)  # padding
        elif length == _NPDT_AUTO:
            self.disposition = reader.i8()
            self.reputation = reader.i8()
            self.rank = reader.i8()
            reader.skip(3)  # padding
        else:
            from wraithguard.esp.io import EspError

            raise EspError(f"NPC_::NPDT size {length}, expected 52 or 12")
        self.gold = reader.u32()
        return self

    def save(self, writer: Writer) -> None:
        """Write the block: the full form when stats are present, else auto-calc."""
        if self.stats is not None:
            writer.u32(_NPDT_FULL)
            writer.i16(self.level)
            self.stats.save(writer)
            writer.i8(self.disposition)
            writer.i8(self.reputation)
            writer.i8(self.rank)
        else:
            writer.u32(_NPDT_AUTO)
            writer.i16(self.level)
            writer.i8(self.disposition)
            writer.i8(self.reputation)
            writer.i8(self.rank)
            writer.raw(b"\x00\x00")  # padding
        writer.raw(b"\x00")  # padding
        writer.u32(self.gold)


def _unpack_flags(value: int) -> tuple[NpcFlags, int]:
    """Split the ``FLAG`` word into NPC flags (low byte) and blood type."""
    return NpcFlags(value & _NPC_FLAG_MASK), (value >> _BLOOD_SHIFT) & _BLOOD_MASK


def _pack_flags(npc_flags: NpcFlags, blood_type: int) -> int:
    """Fold NPC flags and blood type back into the ``FLAG`` word."""
    return int(npc_flags) | ((blood_type & _BLOOD_MASK) << _BLOOD_SHIFT)


@register
@dataclass
class Npc(Record):
    """An ``NPC_`` record."""

    TAG: ClassVar[bytes] = b"NPC_"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    race: str = ""
    class_: str = ""
    faction: str = ""
    head: str = ""
    hair: str = ""
    npc_flags: NpcFlags = field(default_factory=lambda: NpcFlags(0))
    blood_type: int = 0
    inventory: list[tuple[int, str]] = field(default_factory=list)
    spells: list[str] = field(default_factory=list)
    ai_data: AiData = field(default_factory=AiData)
    ai_packages: list[AiPackage] = field(default_factory=list)
    travel_destinations: list[TravelDestination] = field(default_factory=list)
    data: NpcData = field(default_factory=NpcData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Npc:
        """Read the NPC's many subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"RNAM":
                self.race = reader.string()
            elif tag == b"CNAM":
                self.class_ = reader.string()
            elif tag == b"ANAM":
                self.faction = reader.string()
            elif tag == b"BNAM":
                self.head = reader.string()
            elif tag == b"KNAM":
                self.hair = reader.string()
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"NPDT":
                self.data = NpcData.load(reader)
            elif tag == b"FLAG":
                expect_size(reader, "NPC_", "FLAG", _FLAG_SIZE)
                self.npc_flags, self.blood_type = _unpack_flags(reader.u32())
            elif tag == b"NPCO":
                expect_size(reader, "NPC_", "NPCO", _NPCO_SIZE)
                count = reader.i32()
                self.inventory.append((count, reader.string_of(_ITEM_ID_SIZE)))
            elif tag == b"NPCS":
                self.spells.append(reader.string())
            elif tag == b"AIDT":
                expect_size(reader, "NPC_", "AIDT", _AIDT_SIZE)
                self.ai_data = AiData.load(reader)
            elif tag == b"DODT":
                expect_size(reader, "NPC_", "DODT", _DODT_SIZE)
                self.travel_destinations.append(TravelDestination.load(reader))
            elif is_ai_tag(tag):
                self.ai_packages.append(load_ai_package(reader, tag, "NPC_"))
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("NPC_", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        put_opt_string(writer, b"RNAM", self.race)
        put_opt_string(writer, b"CNAM", self.class_)
        put_opt_string(writer, b"ANAM", self.faction)
        put_opt_string(writer, b"BNAM", self.head)
        put_opt_string(writer, b"KNAM", self.hair)
        put_opt_string(writer, b"SCRI", self.script)
        writer.tag(b"NPDT")
        self.data.save(writer)
        writer.tag(b"FLAG")
        writer.u32(_FLAG_SIZE)
        writer.u32(_pack_flags(self.npc_flags, self.blood_type))
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
