"""Container, region and faction -- lists, fixed strings and a large block.

The container pairs a count with a fixed 32-byte item id; the region's weather
block reads its own size (8 or 10) but always writes 10, and its sounds are fixed
32-byte ids with a chance byte; the faction packs ten rank requirements in one
240-byte block and lists reactions as ANAM/INTV sub-groups. All round-trip
byte-for-byte (with the region's documented 8->10 weather widening).
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import (
    Container,
    EspError,
    Faction,
    Region,
    read_plugin,
    write_plugin,
)
from wraithguard.esp.enums import AttributeId, SkillId
from wraithguard.esp.flags import ContainerFlags, FactionFlags, ObjectFlags


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


class TestContainer:
    def test_inventory_round_trips(self) -> None:
        npco1 = _sub(b"NPCO", struct.pack("<i", 5) + _fixed("gold_001", 32))
        npco2 = _sub(b"NPCO", struct.pack("<i", 1) + _fixed("iron_key", 32))
        body = (
            _string_sub(b"NAME", "chest_01")
            + _string_sub(b"MODL", "o\\chest.nif")
            + _string_sub(b"FNAM", "Chest")
            + _sub(b"CNDT", struct.pack("<f", 100.0))
            + _sub(b"FLAG", struct.pack("<I", int(ContainerFlags.ORGANIC)))
            + _string_sub(b"SCRI", "chestScript")
            + npco1
            + npco2
        )
        original = _record(b"CONT", body)
        (cont,) = read_plugin(original)
        assert isinstance(cont, Container)
        assert cont.encumbrance == pytest.approx(100.0)
        assert cont.inventory == [(5, "gold_001"), (1, "iron_key")]
        assert write_plugin([cont]) == original

    def test_unexpected_tag_and_deletion(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: CONT"):
            read_plugin(
                _record(b"CONT", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )
        body = (
            _string_sub(b"NAME", "gone")
            + _sub(b"CNDT", struct.pack("<f", 0.0))
            + _sub(b"FLAG", struct.pack("<I", 0))
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"CONT", body, flags=int(ObjectFlags.DELETED))
        (cont,) = read_plugin(original)
        assert ObjectFlags.DELETED in cont.flags
        assert write_plugin([cont]) == original


class TestRegion:
    def _weat10(self) -> bytes:
        return _sub(b"WEAT", struct.pack("<10B", 30, 20, 10, 15, 10, 5, 5, 0, 3, 2))

    def test_full_weather_and_sounds_round_trip(self) -> None:
        snam = _sub(b"SNAM", _fixed("ashstorm", 32) + struct.pack("<B", 50))
        body = (
            _string_sub(b"NAME", "ashlands_region")
            + _string_sub(b"FNAM", "Ashlands")
            + self._weat10()
            + _string_sub(b"BNAM", "ash_zombie")
            + _sub(b"CNAM", bytes([80, 60, 40, 0]))
            + snam
        )
        original = _record(b"REGN", body)
        (regn,) = read_plugin(original)
        assert isinstance(regn, Region)
        assert regn.weather_chances.clear == 30
        assert regn.weather_chances.blizzard == 2
        assert regn.map_color == bytes([80, 60, 40, 0])
        assert regn.sleep_creature == "ash_zombie"
        assert regn.sounds == [("ashstorm", 50)]
        assert write_plugin([regn]) == original

    def test_short_weather_reads_snow_as_zero_and_widens_on_save(self) -> None:
        # An 8-byte WEAT has no snow/blizzard; the crate reads them as 0 and
        # always writes the full 10-byte form.
        body = (
            _string_sub(b"NAME", "west_gash")
            + _sub(b"WEAT", struct.pack("<8B", 40, 30, 10, 10, 5, 5, 0, 0))
            + _sub(b"CNAM", bytes([0, 100, 0, 0]))
        )
        (regn,) = read_plugin(_record(b"REGN", body))
        assert regn.weather_chances.snow == 0
        assert regn.weather_chances.blizzard == 0
        # writing back produces the 10-byte weather form
        rewritten = write_plugin([regn])
        (again,) = read_plugin(rewritten)
        assert again.weather_chances.clear == 40

    def test_bad_weather_size_is_refused(self) -> None:
        body = _string_sub(b"NAME", "x") + _sub(b"WEAT", struct.pack("<5B", 1, 2, 3, 4, 5))
        with pytest.raises(EspError, match="WEAT size"):
            read_plugin(_record(b"REGN", body))

    def test_unexpected_tag_is_refused(self) -> None:
        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: REGN"):
            read_plugin(_record(b"REGN", body))

    def test_deleted_round_trips(self) -> None:
        body = (
            _string_sub(b"NAME", "gone")
            + self._weat10()
            + _sub(b"CNAM", bytes([0, 0, 0, 0]))
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"REGN", body, flags=int(ObjectFlags.DELETED))
        (regn,) = read_plugin(original)
        assert ObjectFlags.DELETED in regn.flags
        assert write_plugin([regn]) == original


def _fadt() -> bytes:
    out = struct.pack("<2i", int(AttributeId.Strength), int(AttributeId.Endurance))
    for _ in range(10):  # ten rank requirements, 20 bytes each
        out += struct.pack("<5i", 30, 30, 40, 40, 10)
    out += struct.pack("<7i", *[int(SkillId.LongBlade)] * 7)
    out += struct.pack("<I", int(FactionFlags.HIDDEN) if hasattr(FactionFlags, "HIDDEN") else 0)
    return out


class TestFaction:
    def test_round_trips_with_ranks_and_reactions(self) -> None:
        fadt = _fadt()
        assert len(fadt) == 240
        reaction1 = _string_sub(b"ANAM", "other_faction") + _sub(b"INTV", struct.pack("<i", -2))
        reaction2 = _string_sub(b"ANAM", "ally_faction") + _sub(b"INTV", struct.pack("<i", 3))
        body = (
            _string_sub(b"NAME", "fighters_guild")
            + _string_sub(b"FNAM", "Fighters Guild")
            + _sub(b"RNAM", _fixed("Associate", 32))
            + _sub(b"RNAM", _fixed("Journeyman", 32))
            + _sub(b"FADT", fadt)
            + reaction1
            + reaction2
        )
        original = _record(b"FACT", body)
        (fact,) = read_plugin(original)
        assert isinstance(fact, Faction)
        assert fact.rank_names == ["Associate", "Journeyman"]
        assert fact.data.favored_attributes[0] is AttributeId.Strength
        assert fact.data.requirements[0].reputation == 10
        assert len(fact.data.requirements) == 10
        assert [(r.faction, r.reaction) for r in fact.reactions] == [
            ("other_faction", -2),
            ("ally_faction", 3),
        ]
        assert write_plugin([fact]) == original

    def test_reaction_without_intv_is_refused(self) -> None:
        body = _string_sub(b"NAME", "f") + _sub(b"FADT", _fadt()) + _string_sub(b"ANAM", "x")
        body += b"DELE" + struct.pack("<II", 4, 0)
        with pytest.raises(EspError, match="expected INTV"):
            read_plugin(_record(b"FACT", body))

    def test_unexpected_tag_and_deletion(self) -> None:
        with pytest.raises(EspError, match="Unexpected Tag: FACT"):
            read_plugin(
                _record(b"FACT", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )
        body = (
            _string_sub(b"NAME", "gone")
            + _sub(b"FADT", _fadt())
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"FACT", body, flags=int(ObjectFlags.DELETED))
        (fact,) = read_plugin(original)
        assert ObjectFlags.DELETED in fact.flags
        assert write_plugin([fact]) == original
