"""The ingredient and potion records -- enum arrays and an effect list.

The ingredient's ``IRDT`` packs three parallel arrays of four signed ids, whose
order and width must be exact; the potion carries a variable number of 24-byte
effect blocks (a shared struct), and stores its icon under ``TEXT``. Both are
read to the right fields and written back byte-for-byte, including a potion with
several effects.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import Alchemy, Effect, Ingredient, read_plugin, write_plugin
from wraithguard.esp.enums import (
    AttributeId,
    AttributeId2,
    EffectId,
    EffectId2,
    EffectRange,
    SkillId,
    SkillId2,
)
from wraithguard.esp.flags import AlchemyFlags, ObjectFlags


def _sub(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack("<I", len(body)) + body


def _string_sub(tag: bytes, text: str) -> bytes:
    return _sub(tag, text.encode("cp1252") + b"\x00")


def _record(tag: bytes, subrecords: bytes, flags: int = 0) -> bytes:
    return (
        tag
        + struct.pack("<I", len(subrecords))
        + struct.pack("<I", 0)
        + struct.pack("<I", flags)
        + subrecords
    )


def _effect_bytes(effect: int, skill: int, attribute: int, rng: int, *rest: int) -> bytes:
    """A 24-byte ENAM: i16 effect, i8 skill, i8 attribute, u32 range, 4x u32."""
    return struct.pack("<hbbI", effect, skill, attribute, rng) + struct.pack("<4I", *rest)


class TestIngredient:
    def test_arrays_round_trip_in_order(self) -> None:
        block = struct.pack("<fI", 0.3, 5)
        block += struct.pack("<4i", int(EffectId.FireDamage), -1, -1, -1)
        block += struct.pack("<4i", -1, int(SkillId.Alchemy), -1, -1)
        block += struct.pack("<4i", int(AttributeId.Strength), -1, -1, -1)
        body = _string_sub(b"NAME", "ingred_id") + _sub(b"IRDT", block)
        original = _record(b"INGR", body)
        (ingr,) = read_plugin(original)
        assert isinstance(ingr, Ingredient)
        assert ingr.data.effects[0] is EffectId.FireDamage
        assert ingr.data.skills[1] is SkillId.Alchemy
        assert ingr.data.attributes[0] is AttributeId.Strength
        assert int(ingr.data.effects[1]) == -1
        assert write_plugin([ingr]) == original

    def test_all_optional_fields_and_deletion_round_trip(self) -> None:
        block = struct.pack("<fI", 0.3, 5) + struct.pack("<12i", *([-1] * 12))
        body = (
            _string_sub(b"NAME", "ingred_id")
            + _string_sub(b"MODL", "m\\ingred.nif")
            + _string_sub(b"FNAM", "Ingredient")
            + _sub(b"IRDT", block)
            + _string_sub(b"SCRI", "ingredScript")
            + _string_sub(b"ITEX", "t\\ingred.dds")
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"INGR", body, flags=int(ObjectFlags.DELETED))
        (ingr,) = read_plugin(original)
        assert (ingr.name, ingr.script, ingr.icon) == (
            "Ingredient",
            "ingredScript",
            "t\\ingred.dds",
        )
        assert ObjectFlags.DELETED in ingr.flags
        assert write_plugin([ingr]) == original

    def test_unexpected_tag_is_refused(self) -> None:
        from wraithguard.esp import EspError

        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: INGR"):
            read_plugin(_record(b"INGR", body))


class TestAlchemy:
    def test_potion_with_two_effects_round_trips(self) -> None:
        e1 = _effect_bytes(
            int(EffectId2.RestoreHealth),
            int(SkillId2.None_),
            int(AttributeId2.None_),
            int(EffectRange.OnSelf),
            0,
            5,
            10,
            20,
        )
        e2 = _effect_bytes(
            int(EffectId2.FortifyAttribute),
            int(SkillId2.None_),
            int(AttributeId2.Strength),
            int(EffectRange.OnSelf),
            0,
            30,
            5,
            5,
        )
        body = (
            _string_sub(b"NAME", "potion_id")
            + _string_sub(b"MODL", "m\\potion.nif")
            + _string_sub(b"TEXT", "t\\potion.dds")
            + _string_sub(b"SCRI", "potionScript")
            + _string_sub(b"FNAM", "Potion of Health")
            + _sub(b"ALDT", struct.pack("<fII", 0.5, 40, int(AlchemyFlags.AUTO_CALCULATE)))
            + _sub(b"ENAM", e1)
            + _sub(b"ENAM", e2)
        )
        original = _record(b"ALCH", body)
        (potion,) = read_plugin(original)
        assert isinstance(potion, Alchemy)
        assert potion.icon == "t\\potion.dds"
        assert len(potion.effects) == 2
        assert potion.effects[0].magic_effect is EffectId2.RestoreHealth
        assert potion.effects[1].attribute is AttributeId2.Strength
        assert AlchemyFlags.AUTO_CALCULATE in potion.data.flags
        assert write_plugin([potion]) == original

    def test_potion_with_no_effects_round_trips(self) -> None:
        body = _string_sub(b"NAME", "empty") + _sub(b"ALDT", struct.pack("<fII", 0.1, 1, 0))
        original = _record(b"ALCH", body)
        (potion,) = read_plugin(original)
        assert potion.effects == []
        assert write_plugin([potion]) == original

    def test_effect_defaults_are_the_crate_defaults(self) -> None:
        effect = Effect()
        assert int(effect.magic_effect) == -1
        assert int(effect.skill) == -1

    def test_unexpected_tag_is_refused(self) -> None:
        from wraithguard.esp import EspError

        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: ALCH"):
            read_plugin(_record(b"ALCH", body))

    def test_deleted_potion_round_trips(self) -> None:
        body = (
            _string_sub(b"NAME", "gone")
            + _sub(b"ALDT", struct.pack("<fII", 0.0, 0, 0))
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"ALCH", body, flags=int(ObjectFlags.DELETED))
        (potion,) = read_plugin(original)
        assert ObjectFlags.DELETED in potion.flags
        assert write_plugin([potion]) == original
