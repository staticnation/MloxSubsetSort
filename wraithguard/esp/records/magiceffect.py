"""The ``MGEF`` record -- a magic effect. A port of ``types/magiceffect.rs``.

The definition of one of the game's magic effects (fire damage, levitate, ...),
keyed by its effect id rather than a string. A thirty-six-byte ``MEDT`` block
holds its school, base cost, flags, particle colour, and particle sizing; the
rest is the icon, particle texture, and the many sounds and visual effects it
plays, plus a description.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import EffectId, EffectSchool
from wraithguard.esp.flags import MagicEffectFlags, ObjectFlags
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

_MEDT_SIZE = 36


@dataclass
class MagicEffectData:
    """The ``MEDT`` block: school, cost, flags, colour and particle sizing (36 bytes)."""

    school: EffectSchool = field(default_factory=EffectSchool.default)
    base_cost: float = 0.0
    flags: MagicEffectFlags = field(default_factory=lambda: MagicEffectFlags(0))
    color: tuple[int, int, int] = (0, 0, 0)
    speed: float = 0.0
    size: float = 0.0
    size_cap: float = 0.0

    @classmethod
    def load(cls, reader: Reader) -> MagicEffectData:
        """Read the 36-byte block; the colour is three i32 channels."""
        school = EffectSchool(reader.u32())
        base_cost = reader.f32()
        flags = MagicEffectFlags(reader.u32())
        color = (reader.i32(), reader.i32(), reader.i32())
        speed = reader.f32()
        size = reader.f32()
        size_cap = reader.f32()
        return cls(school, base_cost, flags, color, speed, size, size_cap)

    def save(self, writer: Writer) -> None:
        """Write the 36-byte block in field order."""
        writer.u32(int(self.school))
        writer.f32(self.base_cost)
        writer.u32(int(self.flags))
        for channel in self.color:
            writer.i32(channel)
        writer.f32(self.speed)
        writer.f32(self.size)
        writer.f32(self.size_cap)


@register
@dataclass
class MagicEffect(Record):
    """A ``MGEF`` record."""

    TAG: ClassVar[bytes] = b"MGEF"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    effect_id: EffectId = field(default_factory=EffectId.default)
    icon: str = ""
    texture: str = ""
    bolt_sound: str = ""
    cast_sound: str = ""
    hit_sound: str = ""
    area_sound: str = ""
    cast_visual: str = ""
    bolt_visual: str = ""
    hit_visual: str = ""
    area_visual: str = ""
    description: str = ""
    data: MagicEffectData = field(default_factory=MagicEffectData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> MagicEffect:
        """Read the magic effect's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"INDX":
                expect_size(reader, "MGEF", "INDX", 4)
                self.effect_id = EffectId(reader.i32())
            elif tag == b"MEDT":
                expect_size(reader, "MGEF", "MEDT", _MEDT_SIZE)
                self.data = MagicEffectData.load(reader)
            elif tag == b"ITEX":
                self.icon = reader.string()
            elif tag == b"PTEX":
                self.texture = reader.string()
            elif tag == b"BSND":
                self.bolt_sound = reader.string()
            elif tag == b"CSND":
                self.cast_sound = reader.string()
            elif tag == b"HSND":
                self.hit_sound = reader.string()
            elif tag == b"ASND":
                self.area_sound = reader.string()
            elif tag == b"CVFX":
                self.cast_visual = reader.string()
            elif tag == b"BVFX":
                self.bolt_visual = reader.string()
            elif tag == b"HVFX":
                self.hit_visual = reader.string()
            elif tag == b"AVFX":
                self.area_visual = reader.string()
            elif tag == b"DESC":
                self.description = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("MGEF", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order; empty strings omitted."""
        writer.tag(b"INDX")
        writer.u32(4)
        writer.i32(int(self.effect_id))
        put_fixed(writer, b"MEDT", _MEDT_SIZE, self.data)
        put_opt_string(writer, b"ITEX", self.icon)
        put_opt_string(writer, b"PTEX", self.texture)
        put_opt_string(writer, b"BSND", self.bolt_sound)
        put_opt_string(writer, b"CSND", self.cast_sound)
        put_opt_string(writer, b"HSND", self.hit_sound)
        put_opt_string(writer, b"ASND", self.area_sound)
        put_opt_string(writer, b"CVFX", self.cast_visual)
        put_opt_string(writer, b"BVFX", self.bolt_visual)
        put_opt_string(writer, b"HVFX", self.hit_visual)
        put_opt_string(writer, b"AVFX", self.area_visual)
        put_opt_string(writer, b"DESC", self.description)
        write_dele(writer, self.flags)
