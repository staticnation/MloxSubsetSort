"""The ``CREA`` creature record -- NPC-like, with a 96-byte block and a scale.

Reuses the shared AI machinery, so the focus here is what differs: the fixed
creature data block, the sound in ``CNAM``, and the optional ``XSCL`` scale that
the crate clamps to 0.5..2.0 and drops when it is the default 1.0.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import Creature, read_plugin, write_plugin
from wraithguard.esp.enums import CreatureType
from wraithguard.esp.flags import CreatureFlags, ObjectFlags, ServiceFlags


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


def _npdt() -> bytes:
    # type, level, 8 attrs, health/magicka/fatigue, soul, combat/magic/stealth,
    # attack1/2/3 (pairs), gold -- 24 u32 = 96 bytes.
    values = [int(CreatureType.Undead), 10]  # type, level
    values += list(range(1, 9))  # attributes
    values += [80, 40, 90, 5]  # health, magicka, fatigue, soul
    values += [50, 30, 20]  # combat, magic, stealth
    values += [1, 5, 2, 6, 3, 7]  # attack1/2/3 min/max
    values += [15]  # gold
    return struct.pack(f"<{len(values)}I", *values)


def _aidt() -> bytes:
    return (
        struct.pack("<hbbb", 0, 80, 20, 100)
        + b"\x00\x00\x00"
        + struct.pack("<I", int(ServiceFlags(0)))
    )


class TestCreature:
    def test_full_creature_round_trips(self) -> None:
        assert len(_npdt()) == 96
        flag_word = int(CreatureFlags.BIPED | CreatureFlags.RESPAWN) | (1 << 10)
        body = (
            _string_sub(b"NAME", "rat")
            + _string_sub(b"MODL", "r\\rat.nif")
            + _string_sub(b"CNAM", "rat_sound")
            + _string_sub(b"FNAM", "Rat")
            + _sub(b"NPDT", _npdt())
            + _sub(b"FLAG", struct.pack("<I", flag_word))
            + _sub(b"XSCL", struct.pack("<f", 1.5))
            + _sub(b"NPCO", struct.pack("<i", 1) + b"gold_001".ljust(32, b"\x00"))
            + _sub(b"AIDT", _aidt())
            + _sub(b"AI_W", struct.pack("<HHB8Bb", 256, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1))
        )
        original = _record(b"CREA", body)
        (crea,) = read_plugin(original)
        assert isinstance(crea, Creature)
        assert crea.sound == "rat_sound"
        assert crea.data.creature_type is CreatureType.Undead
        assert crea.data.health == 80
        assert crea.data.attack1 == (1, 5)
        assert crea.data.gold == 15
        assert crea.scale == pytest.approx(1.5)
        assert crea.creature_flags is CreatureFlags.BIPED | CreatureFlags.RESPAWN
        assert crea.blood_type == 1
        assert len(crea.ai_packages) == 1
        assert write_plugin([crea]) == original

    def test_script_spells_and_travel_round_trip(self) -> None:
        body = (
            _string_sub(b"NAME", "summon")
            + _string_sub(b"SCRI", "summonScript")
            + _sub(b"NPDT", _npdt())
            + _sub(b"FLAG", struct.pack("<I", 0))
            + _sub(b"NPCS", b"frost_bite".ljust(32, b"\x00"))
            + _sub(b"AIDT", _aidt())
            + _sub(b"DODT", struct.pack("<6f", 1, 2, 3, 0, 0, 0))
            + _string_sub(b"DNAM", "Mournhold")
        )
        original = _record(b"CREA", body)
        (crea,) = read_plugin(original)
        assert crea.script == "summonScript"
        assert crea.spells == ["frost_bite"]
        assert crea.travel_destinations[0].cell == "Mournhold"
        assert write_plugin([crea]) == original

    def test_no_scale_round_trips(self) -> None:
        body = (
            _string_sub(b"NAME", "scamp")
            + _sub(b"NPDT", _npdt())
            + _sub(b"FLAG", struct.pack("<I", 0))
            + _sub(b"AIDT", _aidt())
        )
        original = _record(b"CREA", body)
        (crea,) = read_plugin(original)
        assert crea.scale is None
        assert write_plugin([crea]) == original

    def test_default_scale_is_dropped_on_save(self) -> None:
        # An XSCL of exactly 1.0 is the default; the crate omits it on write.
        body = (
            _string_sub(b"NAME", "x")
            + _sub(b"NPDT", _npdt())
            + _sub(b"FLAG", struct.pack("<I", 0))
            + _sub(b"XSCL", struct.pack("<f", 1.0))
            + _sub(b"AIDT", _aidt())
        )
        (crea,) = read_plugin(_record(b"CREA", body))
        assert crea.scale == pytest.approx(1.0)
        rewritten = write_plugin([crea])
        assert b"XSCL" not in rewritten

    def test_unexpected_tag_and_deletion(self) -> None:
        from wraithguard.esp import EspError

        with pytest.raises(EspError, match="Unexpected Tag: CREA"):
            read_plugin(
                _record(b"CREA", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )
        body = (
            _string_sub(b"NAME", "gone")
            + _sub(b"NPDT", _npdt())
            + _sub(b"FLAG", struct.pack("<I", 0))
            + _sub(b"AIDT", _aidt())
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"CREA", body, flags=int(ObjectFlags.DELETED))
        (crea,) = read_plugin(original)
        assert ObjectFlags.DELETED in crea.flags
        assert write_plugin([crea]) == original
