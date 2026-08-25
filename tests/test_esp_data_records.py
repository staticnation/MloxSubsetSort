"""Game setting, skill, landscape texture and body part.

The game setting is a tagged value like the global -- string, float or integer,
told apart by its subrecord tag -- and here the Python type stands in for the
tag, so all three variants must round-trip to the right subrecord. The skill has
no id string (it is keyed by index), and the body part packs four one-byte
fields including a bool; both are pinned byte-for-byte.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import (
    Bodypart,
    EspError,
    GameSetting,
    LandscapeTexture,
    Skill,
    read_plugin,
    write_plugin,
)
from wraithguard.esp.enums import BodypartId, BodypartType, SkillId
from wraithguard.esp.flags import BodypartFlags, ObjectFlags


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


class TestGameSetting:
    def test_string_value_round_trips(self) -> None:
        body = _string_sub(b"NAME", "sMonthMorningstar") + _string_sub(b"STRV", "Morning Star")
        original = _record(b"GMST", body)
        (gmst,) = read_plugin(original)
        assert isinstance(gmst, GameSetting)
        assert gmst.value == "Morning Star"
        assert write_plugin([gmst]) == original

    def test_float_value_round_trips(self) -> None:
        body = _string_sub(b"NAME", "fEncumbranceStrMult") + _sub(b"FLTV", struct.pack("<f", 5.0))
        original = _record(b"GMST", body)
        (gmst,) = read_plugin(original)
        assert gmst.value == pytest.approx(5.0)
        assert isinstance(gmst.value, float)
        assert write_plugin([gmst]) == original

    def test_integer_value_round_trips(self) -> None:
        body = _string_sub(b"NAME", "iMonthsToRespawn") + _sub(b"INTV", struct.pack("<i", 4))
        original = _record(b"GMST", body)
        (gmst,) = read_plugin(original)
        assert gmst.value == 4
        assert isinstance(gmst.value, int)
        assert write_plugin([gmst]) == original

    def test_unexpected_tag_is_refused(self) -> None:
        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: GMST"):
            read_plugin(_record(b"GMST", body))


class TestSkill:
    def test_round_trips(self) -> None:
        skdt = struct.pack("<ii4f", 2, 0, 1.0, 2.0, 3.0, 4.0)
        body = (
            _sub(b"INDX", struct.pack("<i", int(SkillId.LongBlade)))
            + _sub(b"SKDT", skdt)
            + _string_sub(b"DESC", "Skill with long blades.")
        )
        original = _record(b"SKIL", body)
        (skill,) = read_plugin(original)
        assert isinstance(skill, Skill)
        assert skill.skill_id is SkillId.LongBlade
        assert skill.data.governing_attribute == 2
        assert skill.data.actions == pytest.approx((1.0, 2.0, 3.0, 4.0))
        assert skill.description == "Skill with long blades."
        assert write_plugin([skill]) == original

    def test_unexpected_tag_is_refused(self) -> None:
        body = _sub(b"INDX", struct.pack("<i", 0)) + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: SKIL"):
            read_plugin(_record(b"SKIL", body))

    def test_deleted_round_trips(self) -> None:
        skdt = struct.pack("<ii4f", 0, 0, 0.0, 0.0, 0.0, 0.0)
        body = _sub(b"INDX", struct.pack("<i", 0)) + _sub(b"SKDT", skdt)
        body += b"DELE" + struct.pack("<II", 4, 0)
        original = _record(b"SKIL", body, flags=int(ObjectFlags.DELETED))
        (skill,) = read_plugin(original)
        assert ObjectFlags.DELETED in skill.flags
        assert write_plugin([skill]) == original


class TestLandscapeTexture:
    def test_round_trips(self) -> None:
        body = (
            _string_sub(b"NAME", "_land_default")
            + _sub(b"INTV", struct.pack("<I", 0))
            + _string_sub(b"DATA", "tx_rock_01.tga")
        )
        original = _record(b"LTEX", body)
        (ltex,) = read_plugin(original)
        assert isinstance(ltex, LandscapeTexture)
        assert ltex.index == 0
        assert ltex.file_name == "tx_rock_01.tga"
        assert write_plugin([ltex]) == original

    def test_deleted_round_trips(self) -> None:
        body = _string_sub(b"NAME", "gone") + _sub(b"INTV", struct.pack("<I", 3))
        body += b"DELE" + struct.pack("<II", 4, 0)
        original = _record(b"LTEX", body, flags=int(ObjectFlags.DELETED))
        (ltex,) = read_plugin(original)
        assert ObjectFlags.DELETED in ltex.flags
        assert write_plugin([ltex]) == original

    def test_unexpected_tag_is_refused(self) -> None:
        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: LTEX"):
            read_plugin(_record(b"LTEX", body))


class TestBodypart:
    def test_round_trips_with_vampire_flag(self) -> None:
        bydt = struct.pack(
            "<BBBB",
            int(BodypartId.Head),
            1,
            int(BodypartFlags.FEMALE) if hasattr(BodypartFlags, "FEMALE") else 0,
            int(BodypartType.Skin),
        )
        body = (
            _string_sub(b"NAME", "b_n_dark elf_m_head")
            + _string_sub(b"MODL", "b\\head.nif")
            + _string_sub(b"FNAM", "Dark Elf")
            + _sub(b"BYDT", bydt)
        )
        original = _record(b"BODY", body)
        (part,) = read_plugin(original)
        assert isinstance(part, Bodypart)
        assert part.race == "Dark Elf"
        assert part.data.part is BodypartId.Head
        assert part.data.vampire is True
        assert part.data.bodypart_type is BodypartType.Skin
        assert write_plugin([part]) == original

    def test_non_vampire_and_deletion_round_trip(self) -> None:
        bydt = struct.pack("<BBBB", int(BodypartId.Hair), 0, 0, int(BodypartType.Skin))
        body = _string_sub(b"NAME", "gone") + _sub(b"BYDT", bydt)
        body += b"DELE" + struct.pack("<II", 4, 0)
        original = _record(b"BODY", body, flags=int(ObjectFlags.DELETED))
        (part,) = read_plugin(original)
        assert part.data.vampire is False
        assert ObjectFlags.DELETED in part.flags
        assert write_plugin([part]) == original

    def test_unexpected_tag_is_refused(self) -> None:
        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: BODY"):
            read_plugin(_record(b"BODY", body))
