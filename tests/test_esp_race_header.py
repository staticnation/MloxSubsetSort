"""The race record and the plugin header -- and a whole-plugin round trip.

The race packs seven skill bonuses and ten attribute/size pairs in one block.
The header is the first record of every plugin: its version, type, author and
description (fixed-width), record count, and master list. The final test reads a
small but complete plugin -- header plus content records -- and writes it back
byte-for-byte, which is the property the whole library exists to guarantee.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import (
    EspError,
    Header,
    Race,
    Weapon,
    read_plugin,
    write_plugin,
)
from wraithguard.esp.enums import FileType, SkillId, WeaponType
from wraithguard.esp.flags import ObjectFlags, RaceFlags


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


def _fixed(text: str, size: int) -> bytes:
    return text.encode("cp1252").ljust(size, b"\x00")


def _radt() -> bytes:
    out = b""
    for _ in range(7):  # seven (skill, bonus) pairs
        out += struct.pack("<ii", int(SkillId.LongBlade), 5)
    for _ in range(8):  # eight attributes, male/female
        out += struct.pack("<ii", 40, 40)
    out += struct.pack("<4f", 1.0, 0.9, 1.0, 0.9)  # height then weight
    out += struct.pack("<I", int(RaceFlags.PLAYABLE) if hasattr(RaceFlags, "PLAYABLE") else 0)
    return out


class TestRace:
    def test_round_trips_with_bonuses_and_spells(self) -> None:
        radt = _radt()
        assert len(radt) == 140
        body = (
            _string_sub(b"NAME", "dark elf")
            + _string_sub(b"FNAM", "Dark Elf")
            + _sub(b"RADT", radt)
            + _sub(b"NPCS", _fixed("ancestor_guardian", 32))
            + _string_sub(b"DESC", "Dunmer of Morrowind.")
        )
        original = _record(b"RACE", body)
        (race,) = read_plugin(original)
        assert isinstance(race, Race)
        assert race.data.skill_bonuses[0][0] is SkillId.LongBlade
        assert race.data.skill_bonuses[0][1] == 5
        assert race.data.strength == (40, 40)
        assert race.data.height[0] == pytest.approx(1.0)
        assert race.spells == ["ancestor_guardian"]
        assert write_plugin([race]) == original

    def test_unexpected_tag_and_deletion(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: RACE"):
            read_plugin(
                _record(b"RACE", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )
        body = (
            _string_sub(b"NAME", "gone")
            + _sub(b"RADT", _radt())
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"RACE", body, flags=int(ObjectFlags.DELETED))
        (race,) = read_plugin(original)
        assert ObjectFlags.DELETED in race.flags
        assert write_plugin([race]) == original


def _hedr(version: float, file_type: int, author: str, desc: str, num: int) -> bytes:
    return (
        struct.pack("<f", version)
        + struct.pack("<I", file_type)
        + _fixed(author, 32)
        + _fixed(desc, 256)
        + struct.pack("<I", num)
    )


class TestHeader:
    def test_round_trips_with_masters(self) -> None:
        body = (
            _sub(b"HEDR", _hedr(1.3, int(FileType.Esp), "Bethesda", "A plugin.", 2))
            + _string_sub(b"MAST", "Morrowind.esm")
            + _sub(b"DATA", struct.pack("<Q", 79837557))
            + _string_sub(b"MAST", "Tribunal.esm")
            + _sub(b"DATA", struct.pack("<Q", 4565686))
        )
        original = _record(b"TES3", body)
        (header,) = read_plugin(original)
        assert isinstance(header, Header)
        assert header.version == pytest.approx(1.3)
        assert header.file_type is FileType.Esp
        assert header.author == "Bethesda"
        assert header.description == "A plugin."
        assert header.num_objects == 2
        assert header.masters == [("Morrowind.esm", 79837557), ("Tribunal.esm", 4565686)]
        assert write_plugin([header]) == original

    def test_master_without_data_is_refused(self) -> None:
        body = _sub(b"HEDR", _hedr(1.3, 0, "", "", 0)) + _string_sub(b"MAST", "x")
        body += b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="expected DATA"):
            read_plugin(_record(b"TES3", body))

    def test_unexpected_tag_is_refused(self) -> None:
        body = _sub(b"HEDR", _hedr(1.3, 0, "", "", 0)) + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: TES3"):
            read_plugin(_record(b"TES3", body))


class TestWholePlugin:
    def test_header_and_records_round_trip_together(self) -> None:
        header = _record(
            b"TES3",
            _sub(b"HEDR", _hedr(1.3, int(FileType.Esp), "me", "test", 1)),
        )
        wpdt = struct.pack(
            "<fIHHffHBBBBBBI",
            3.0,
            100,
            int(WeaponType.ShortBladeOneHand),
            50,
            1.0,
            1.0,
            0,
            5,
            6,
            5,
            6,
            5,
            6,
            0,
        )
        weapon = _record(
            b"WEAP",
            _string_sub(b"NAME", "test_dagger") + _sub(b"WPDT", wpdt),
        )
        plugin = header + weapon
        records = read_plugin(plugin)
        assert isinstance(records[0], Header)
        assert isinstance(records[1], Weapon)
        assert write_plugin(records) == plugin
