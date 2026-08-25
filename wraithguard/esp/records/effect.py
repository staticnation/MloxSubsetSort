"""The ``ENAM`` effect block -- a port of the crate's ``types/effect.rs``.

One magic effect as it appears on a potion, an enchantment or a spell: which
effect, on what skill or attribute it acts, its range, area, duration and
magnitude range. Twenty-four bytes, and shared by every record that lists
effects -- alchemy, enchanting, spell -- so it lives on its own here.

The narrow enum widths matter: the effect id is a signed 16-bit, the skill and
attribute signed bytes, all defaulting to -1 ("none"). A wider read would
misalign every field after it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from wraithguard.esp.enums import AttributeId2, EffectId2, EffectRange, SkillId2

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

#: The size of an ``ENAM`` effect subrecord, in bytes.
EFFECT_SIZE = 24


@dataclass
class Effect:
    """One magic effect (an ``ENAM`` subrecord, 24 bytes)."""

    magic_effect: EffectId2 = field(default_factory=EffectId2.default)
    skill: SkillId2 = field(default_factory=SkillId2.default)
    attribute: AttributeId2 = field(default_factory=AttributeId2.default)
    range: EffectRange = field(default_factory=EffectRange.default)
    area: int = 0
    duration: int = 0
    min_magnitude: int = 0
    max_magnitude: int = 0

    @classmethod
    def load(cls, reader: Reader) -> Effect:
        """Read the 24-byte effect; the id/skill/attribute widths are 2/1/1."""
        return cls(
            magic_effect=EffectId2(reader.i16()),
            skill=SkillId2(reader.i8()),
            attribute=AttributeId2(reader.i8()),
            range=EffectRange(reader.u32()),
            area=reader.u32(),
            duration=reader.u32(),
            min_magnitude=reader.u32(),
            max_magnitude=reader.u32(),
        )

    def save(self, writer: Writer) -> None:
        """Write the 24-byte effect in field order."""
        writer.i16(int(self.magic_effect))
        writer.i8(int(self.skill))
        writer.i8(int(self.attribute))
        writer.u32(int(self.range))
        writer.u32(self.area)
        writer.u32(self.duration)
        writer.u32(self.min_magnitude)
        writer.u32(self.max_magnitude)
