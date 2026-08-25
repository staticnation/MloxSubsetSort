"""Leveled item/creature lists and the magic effect definition.

The leveled lists interleave id (``INAM``/``CNAM``) and level (``INTV``)
subrecords, counted by an ``INDX`` -- the pairing and the count must survive. The
magic effect packs a three-channel colour in its block and a long run of optional
strings. All round-trip byte-for-byte, and a stray level without an id is
refused.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import (
    EspError,
    LeveledCreature,
    LeveledItem,
    MagicEffect,
    read_plugin,
    write_plugin,
)
from wraithguard.esp.enums import EffectId, EffectSchool
from wraithguard.esp.flags import LeveledItemFlags, ObjectFlags


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


class TestLeveledItem:
    def test_pairs_round_trip_in_order(self) -> None:
        body = (
            _string_sub(b"NAME", "random_loot")
            + _sub(b"DATA", struct.pack("<I", int(LeveledItemFlags.CALCULATE_FROM_ALL_LEVELS)))
            + _sub(b"NNAM", struct.pack("<B", 25))
            + _sub(b"INDX", struct.pack("<I", 2))
            + _string_sub(b"INAM", "iron_dagger")
            + _sub(b"INTV", struct.pack("<H", 1))
            + _string_sub(b"INAM", "steel_dagger")
            + _sub(b"INTV", struct.pack("<H", 5))
        )
        original = _record(b"LEVI", body)
        (levi,) = read_plugin(original)
        assert isinstance(levi, LeveledItem)
        assert levi.chance_none == 25
        assert levi.items == [("iron_dagger", 1), ("steel_dagger", 5)]
        assert write_plugin([levi]) == original

    def test_empty_list_round_trips(self) -> None:
        body = (
            _string_sub(b"NAME", "empty")
            + _sub(b"DATA", struct.pack("<I", 0))
            + _sub(b"NNAM", struct.pack("<B", 0))
        )
        original = _record(b"LEVI", body)
        (levi,) = read_plugin(original)
        assert levi.items == []
        assert write_plugin([levi]) == original

    def test_level_without_item_is_refused(self) -> None:
        body = (
            _string_sub(b"NAME", "bad")
            + _sub(b"DATA", struct.pack("<I", 0))
            + _sub(b"NNAM", struct.pack("<B", 0))
            + _sub(b"INTV", struct.pack("<H", 3))
        )
        with pytest.raises(EspError, match="INTV level without"):
            read_plugin(_record(b"LEVI", body))

    def test_unexpected_tag_and_deletion(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: LEVI"):
            read_plugin(
                _record(b"LEVI", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )
        body = (
            _string_sub(b"NAME", "gone")
            + _sub(b"DATA", struct.pack("<I", 0))
            + _sub(b"NNAM", struct.pack("<B", 0))
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"LEVI", body, flags=int(ObjectFlags.DELETED))
        (levi,) = read_plugin(original)
        assert ObjectFlags.DELETED in levi.flags
        assert write_plugin([levi]) == original


class TestLeveledCreature:
    def test_pairs_round_trip(self) -> None:
        body = (
            _string_sub(b"NAME", "random_mob")
            + _sub(b"DATA", struct.pack("<I", 0))
            + _sub(b"NNAM", struct.pack("<B", 10))
            + _sub(b"INDX", struct.pack("<I", 1))
            + _string_sub(b"CNAM", "rat")
            + _sub(b"INTV", struct.pack("<H", 1))
        )
        original = _record(b"LEVC", body)
        (levc,) = read_plugin(original)
        assert isinstance(levc, LeveledCreature)
        assert levc.creatures == [("rat", 1)]
        assert write_plugin([levc]) == original

    def test_level_without_creature_is_refused(self) -> None:
        body = (
            _string_sub(b"NAME", "bad")
            + _sub(b"DATA", struct.pack("<I", 0))
            + _sub(b"NNAM", struct.pack("<B", 0))
            + _sub(b"INTV", struct.pack("<H", 3))
        )
        with pytest.raises(EspError, match="INTV level without"):
            read_plugin(_record(b"LEVC", body))

    def test_unexpected_tag_is_refused(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: LEVC"):
            read_plugin(
                _record(b"LEVC", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )

    def test_empty_and_deleted_round_trips(self) -> None:
        body = (
            _string_sub(b"NAME", "gone")
            + _sub(b"DATA", struct.pack("<I", 0))
            + _sub(b"NNAM", struct.pack("<B", 0))
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"LEVC", body, flags=int(ObjectFlags.DELETED))
        (levc,) = read_plugin(original)
        assert levc.creatures == []
        assert ObjectFlags.DELETED in levc.flags
        assert write_plugin([levc]) == original


class TestMagicEffect:
    def test_round_trips_with_colour_and_strings(self) -> None:
        medt = struct.pack(
            "<IfI3ifff",
            int(EffectSchool.Destruction),
            5.0,
            0,
            255,
            128,
            0,
            1.0,
            2.0,
            3.0,
        )
        body = (
            _sub(b"INDX", struct.pack("<i", int(EffectId.FireDamage)))
            + _sub(b"MEDT", medt)
            + _string_sub(b"ITEX", "s\\fire.dds")
            + _string_sub(b"PTEX", "vfx_fire.dds")
            + _string_sub(b"CSND", "fire_cast.wav")
            + _string_sub(b"DESC", "Burns the target.")
        )
        original = _record(b"MGEF", body)
        (mgef,) = read_plugin(original)
        assert isinstance(mgef, MagicEffect)
        assert mgef.effect_id is EffectId.FireDamage
        assert mgef.data.school is EffectSchool.Destruction
        assert mgef.data.color == (255, 128, 0)
        assert mgef.data.size_cap == pytest.approx(3.0)
        assert mgef.icon == "s\\fire.dds"
        assert mgef.cast_sound == "fire_cast.wav"
        assert write_plugin([mgef]) == original

    def test_all_optional_strings_and_deletion(self) -> None:
        medt = struct.pack("<IfI3ifff", 0, 0.0, 0, 0, 0, 0, 0.0, 0.0, 0.0)
        body = _sub(b"INDX", struct.pack("<i", 0)) + _sub(b"MEDT", medt)
        for tag in (b"ITEX", b"PTEX", b"BSND", b"CSND", b"HSND", b"ASND"):
            body += _string_sub(tag, "x")
        for tag in (b"CVFX", b"BVFX", b"HVFX", b"AVFX", b"DESC"):
            body += _string_sub(tag, "y")
        body += b"DELE" + struct.pack("<II", 4, 0)
        original = _record(b"MGEF", body, flags=int(ObjectFlags.DELETED))
        (mgef,) = read_plugin(original)
        assert mgef.area_visual == "y"
        assert mgef.hit_sound == "x"
        assert ObjectFlags.DELETED in mgef.flags
        assert write_plugin([mgef]) == original

    def test_unexpected_tag_is_refused(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: MGEF"):
            read_plugin(
                _record(
                    b"MGEF", _sub(b"INDX", struct.pack("<i", 0)) + b"ZZZZ" + struct.pack("<I", 0)
                )
            )
