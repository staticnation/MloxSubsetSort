"""The ``WEAP`` record -- a weapon. A port of the crate's ``types/weapon.rs``.

Two pieces, exactly as the crate splits them: :class:`Weapon`, the record with
its string subrecords (id, model, name, script, icon, enchantment) and its data
block; and :class:`WeaponData`, the fixed thirty-two-byte ``WPDT`` payload of
its numbers (weights, values, damages) and its type and flags. The load reads
the subrecords by tag; the save writes them back in the crate's order, omitting
the optional strings when empty -- so a weapon read and written is byte-for-byte
what it was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import WeaponType
from wraithguard.esp.flags import ObjectFlags, WeaponFlags
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

#: The fixed size of the ``WPDT`` subrecord, in bytes.
_WPDT_SIZE = 32


@dataclass
class WeaponData:
    """The ``WPDT`` block: a weapon's numbers, type and flags (32 bytes)."""

    weight: float = 0.0
    value: int = 0
    weapon_type: WeaponType = WeaponType.ShortBladeOneHand
    health: int = 0
    speed: float = 0.0
    reach: float = 0.0
    enchantment: int = 0
    chop_min: int = 0
    chop_max: int = 0
    slash_min: int = 0
    slash_max: int = 0
    thrust_min: int = 0
    thrust_max: int = 0
    flags: WeaponFlags = field(default_factory=lambda: WeaponFlags(0))

    @classmethod
    def load(cls, reader: Reader) -> WeaponData:
        """Read the 32-byte block in field order."""
        return cls(
            weight=reader.f32(),
            value=reader.u32(),
            weapon_type=WeaponType(reader.u16()),
            health=reader.u16(),
            speed=reader.f32(),
            reach=reader.f32(),
            enchantment=reader.u16(),
            chop_min=reader.u8(),
            chop_max=reader.u8(),
            slash_min=reader.u8(),
            slash_max=reader.u8(),
            thrust_min=reader.u8(),
            thrust_max=reader.u8(),
            flags=WeaponFlags(reader.u32()),
        )

    def save(self, writer: Writer) -> None:
        """Write the 32-byte block in field order."""
        writer.f32(self.weight)
        writer.u32(self.value)
        writer.u16(int(self.weapon_type))
        writer.u16(self.health)
        writer.f32(self.speed)
        writer.f32(self.reach)
        writer.u16(self.enchantment)
        writer.u8(self.chop_min)
        writer.u8(self.chop_max)
        writer.u8(self.slash_min)
        writer.u8(self.slash_max)
        writer.u8(self.thrust_min)
        writer.u8(self.thrust_max)
        writer.u32(int(self.flags))


@register
@dataclass
class Weapon(Record):
    """A ``WEAP`` record."""

    TAG: ClassVar[bytes] = b"WEAP"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    icon: str = ""
    enchanting: str = ""
    data: WeaponData = field(default_factory=WeaponData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Weapon:
        """Read the weapon's subrecords, dispatching on each tag."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"WPDT":
                expect_size(reader, "WEAP", "WPDT", _WPDT_SIZE)
                self.data = WeaponData.load(reader)
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"ITEX":
                self.icon = reader.string()
            elif tag == b"ENAM":
                self.enchanting = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("WEAP", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order; omit empty optional strings."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        put_fixed(writer, b"WPDT", _WPDT_SIZE, self.data)
        put_opt_string(writer, b"SCRI", self.script)
        put_opt_string(writer, b"ITEX", self.icon)
        put_opt_string(writer, b"ENAM", self.enchanting)
        write_dele(writer, self.flags)
