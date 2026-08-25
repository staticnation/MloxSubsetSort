"""Class, birthsign, enchantment and spell.

The class packs two attributes, a specialization and ten skills in one block --
their interleaved minor/major order must be preserved. The birthsign lists its
granted spells as fixed thirty-two-byte fields. Enchantment and spell reuse the
shared effect block. All round-trip byte-for-byte.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import (
    Birthsign,
    Class,
    Enchanting,
    EspError,
    Spell,
    read_plugin,
    write_plugin,
)
from wraithguard.esp.enums import (
    AttributeId,
    EffectId2,
    EnchantType,
    SkillId,
    Specialization,
    SpellType,
)
from wraithguard.esp.flags import ObjectFlags, ServiceFlags, SpellFlags


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


def _effect(effect: int, mag: int) -> bytes:
    return struct.pack("<hbbI", effect, -1, -1, 0) + struct.pack("<4I", 0, 0, mag, mag)


class TestClass:
    def test_round_trips_with_ordered_skills(self) -> None:
        skills = [
            int(SkillId.LongBlade),
            int(SkillId.Block),
            int(SkillId.Armorer),
            int(SkillId.MediumArmor),
            int(SkillId.HeavyArmor),
            int(SkillId.BluntWeapon),
            int(SkillId.Axe),
            int(SkillId.Spear),
            int(SkillId.Athletics),
            int(SkillId.Enchant),
        ]
        cldt = struct.pack(
            "<3i10i2I",
            int(AttributeId.Strength),
            int(AttributeId.Endurance),
            int(Specialization.Combat),
            *skills,
            0,
            int(ServiceFlags.WEAPONS) if hasattr(ServiceFlags, "WEAPONS") else 0,
        )
        body = (
            _string_sub(b"NAME", "warrior")
            + _string_sub(b"FNAM", "Warrior")
            + _sub(b"CLDT", cldt)
            + _string_sub(b"DESC", "Fighters.")
        )
        original = _record(b"CLAS", body)
        (klass,) = read_plugin(original)
        assert isinstance(klass, Class)
        assert klass.data.attribute1 is AttributeId.Strength
        assert klass.data.specialization is Specialization.Combat
        assert [int(s) for s in klass.data.skills] == skills
        assert klass.description == "Fighters."
        assert write_plugin([klass]) == original

    def test_unexpected_tag_and_deletion(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: CLAS"):
            read_plugin(
                _record(b"CLAS", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )
        cldt = struct.pack("<3i10i2I", 0, 0, 0, *([0] * 10), 0, 0)
        body = (
            _string_sub(b"NAME", "gone") + _sub(b"CLDT", cldt) + b"DELE" + struct.pack("<II", 4, 0)
        )
        original = _record(b"CLAS", body, flags=int(ObjectFlags.DELETED))
        (klass,) = read_plugin(original)
        assert ObjectFlags.DELETED in klass.flags
        assert write_plugin([klass]) == original


class TestBirthsign:
    def test_spells_are_fixed_width_and_round_trip(self) -> None:
        body = (
            _string_sub(b"NAME", "sign_warrior")
            + _string_sub(b"FNAM", "The Warrior")
            + _string_sub(b"TNAM", "tx_warrior.dds")
            + _string_sub(b"DESC", "Combat sign.")
            + _sub(b"NPCS", b"bound_battle_axe".ljust(32, b"\x00"))
            + _sub(b"NPCS", b"berserk".ljust(32, b"\x00"))
        )
        original = _record(b"BSGN", body)
        (sign,) = read_plugin(original)
        assert isinstance(sign, Birthsign)
        assert sign.texture == "tx_warrior.dds"
        assert sign.spells == ["bound_battle_axe", "berserk"]
        assert write_plugin([sign]) == original

    def test_unexpected_tag_is_refused(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: BSGN"):
            read_plugin(
                _record(b"BSGN", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )

    def test_deleted_round_trips(self) -> None:
        body = _string_sub(b"NAME", "gone") + b"DELE" + struct.pack("<II", 4, 0)
        original = _record(b"BSGN", body, flags=int(ObjectFlags.DELETED))
        (sign,) = read_plugin(original)
        assert ObjectFlags.DELETED in sign.flags
        assert write_plugin([sign]) == original


class TestEnchanting:
    def test_round_trips_with_effects(self) -> None:
        endt = struct.pack("<IIII", int(EnchantType.CastOnce), 50, 100, 0)
        body = (
            _string_sub(b"NAME", "ench_fire")
            + _sub(b"ENDT", endt)
            + _sub(b"ENAM", _effect(int(EffectId2.FireDamage), 10))
        )
        original = _record(b"ENCH", body)
        (ench,) = read_plugin(original)
        assert isinstance(ench, Enchanting)
        assert ench.data.enchant_type is EnchantType.CastOnce
        assert ench.data.max_charge == 100
        assert len(ench.effects) == 1
        assert ench.effects[0].magic_effect is EffectId2.FireDamage
        assert write_plugin([ench]) == original

    def test_unexpected_tag_and_deletion(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: ENCH"):
            read_plugin(
                _record(b"ENCH", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )
        endt = struct.pack("<IIII", 0, 0, 0, 0)
        body = (
            _string_sub(b"NAME", "gone") + _sub(b"ENDT", endt) + b"DELE" + struct.pack("<II", 4, 0)
        )
        original = _record(b"ENCH", body, flags=int(ObjectFlags.DELETED))
        (ench,) = read_plugin(original)
        assert ObjectFlags.DELETED in ench.flags
        assert write_plugin([ench]) == original


class TestSpell:
    def test_round_trips_with_two_effects(self) -> None:
        spdt = struct.pack("<III", int(SpellType.Spell), 25, int(SpellFlags.AUTO_CALCULATE))
        body = (
            _string_sub(b"NAME", "fireball")
            + _string_sub(b"FNAM", "Fireball")
            + _sub(b"SPDT", spdt)
            + _sub(b"ENAM", _effect(int(EffectId2.FireDamage), 20))
            + _sub(b"ENAM", _effect(int(EffectId2.FireDamage), 5))
        )
        original = _record(b"SPEL", body)
        (spell,) = read_plugin(original)
        assert isinstance(spell, Spell)
        assert spell.data.spell_type is SpellType.Spell
        assert spell.data.cost == 25
        assert len(spell.effects) == 2
        assert write_plugin([spell]) == original

    def test_unexpected_tag_and_deletion(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: SPEL"):
            read_plugin(
                _record(b"SPEL", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )
        spdt = struct.pack("<III", 0, 0, 0)
        body = (
            _string_sub(b"NAME", "gone") + _sub(b"SPDT", spdt) + b"DELE" + struct.pack("<II", 4, 0)
        )
        original = _record(b"SPEL", body, flags=int(ObjectFlags.DELETED))
        (spell,) = read_plugin(original)
        assert ObjectFlags.DELETED in spell.flags
        assert write_plugin([spell]) == original
