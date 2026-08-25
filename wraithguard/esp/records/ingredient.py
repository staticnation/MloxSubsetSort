"""The ``INGR`` record -- an alchemy ingredient. A port of ``types/ingredient.rs``.

The item strings plus a fifty-six-byte ``IRDT`` block: weight, value, and three
parallel arrays of four -- the effects the ingredient can have, and the skill or
attribute each acts on. Every array element is a signed 32-bit id defaulting to
-1, so an unused slot is ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import AttributeId, EffectId, SkillId
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

_IRDT_SIZE = 56
_SLOTS = 4


def _four_effects() -> tuple[EffectId, ...]:
    """The four default effect ids an ingredient's ``IRDT`` block starts with."""
    return (EffectId.default(),) * _SLOTS


def _four_skills() -> tuple[SkillId, ...]:
    """The four default skill ids for an ingredient's effect slots."""
    return (SkillId.default(),) * _SLOTS


def _four_attributes() -> tuple[AttributeId, ...]:
    """The four default attribute ids for an ingredient's effect slots."""
    return (AttributeId.default(),) * _SLOTS


@dataclass
class IngredientData:
    """The ``IRDT`` block: weight, value, and the effect/skill/attribute arrays."""

    weight: float = 0.0
    value: int = 0
    effects: tuple[EffectId, ...] = field(default_factory=_four_effects)
    skills: tuple[SkillId, ...] = field(default_factory=_four_skills)
    attributes: tuple[AttributeId, ...] = field(default_factory=_four_attributes)

    @classmethod
    def load(cls, reader: Reader) -> IngredientData:
        """Read the 56-byte block: two scalars then three arrays of four i32 ids."""
        weight = reader.f32()
        value = reader.u32()
        effects = tuple(EffectId(reader.i32()) for _ in range(_SLOTS))
        skills = tuple(SkillId(reader.i32()) for _ in range(_SLOTS))
        attributes = tuple(AttributeId(reader.i32()) for _ in range(_SLOTS))
        return cls(weight, value, effects, skills, attributes)

    def save(self, writer: Writer) -> None:
        """Write the 56-byte block in field order."""
        writer.f32(self.weight)
        writer.u32(self.value)
        for effect in self.effects:
            writer.i32(int(effect))
        for skill in self.skills:
            writer.i32(int(skill))
        for attribute in self.attributes:
            writer.i32(int(attribute))


@register
@dataclass
class Ingredient(Record):
    """An ``INGR`` record."""

    TAG: ClassVar[bytes] = b"INGR"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    name: str = ""
    script: str = ""
    mesh: str = ""
    icon: str = ""
    data: IngredientData = field(default_factory=IngredientData)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Ingredient:
        """Read the ingredient's subrecords."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"MODL":
                self.mesh = reader.string()
            elif tag == b"FNAM":
                self.name = reader.string()
            elif tag == b"IRDT":
                expect_size(reader, "INGR", "IRDT", _IRDT_SIZE)
                self.data = IngredientData.load(reader)
            elif tag == b"SCRI":
                self.script = reader.string()
            elif tag == b"ITEX":
                self.icon = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("INGR", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order."""
        put_string(writer, b"NAME", self.id)
        put_opt_string(writer, b"MODL", self.mesh)
        put_opt_string(writer, b"FNAM", self.name)
        put_fixed(writer, b"IRDT", _IRDT_SIZE, self.data)
        put_opt_string(writer, b"SCRI", self.script)
        put_opt_string(writer, b"ITEX", self.icon)
        write_dele(writer, self.flags)
