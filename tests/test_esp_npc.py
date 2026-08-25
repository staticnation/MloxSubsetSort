"""The ``NPC_`` record -- stats, packed flags, inventory, spells and AI.

The NPC exercises the shared AI machinery for the first time: the AI data block,
each of the five AI package kinds (two of which trail an out-of-size cell name),
and travel destinations. It also has a two-form stats block and a flag word that
packs the NPC flags together with a blood type. All of it round-trips
byte-for-byte.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import Npc, NpcData, NpcStats, read_plugin, write_plugin
from wraithguard.esp.flags import NpcFlags, ObjectFlags, ServiceFlags


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


def _npdt_full() -> bytes:
    body = struct.pack("<h", 5)  # level
    body += bytes(range(8)) + bytes(range(27)) + b"\x00"  # attrs, skills, pad
    body += struct.pack("<3H", 100, 50, 120)  # health, magicka, fatigue
    body += struct.pack("<bbb", 50, 0, 0) + b"\x00"  # disposition, reputation, rank, pad
    body += struct.pack("<I", 250)  # gold
    return struct.pack("<I", 52) + body


def _npdt_auto() -> bytes:
    body = struct.pack("<h", 1)  # level
    body += struct.pack("<bbb", 40, 0, 0) + b"\x00\x00"  # disp, rep, rank, pad2
    body += b"\x00"  # shared pad
    body += struct.pack("<I", 0)  # gold
    return struct.pack("<I", 12) + body


def _aidt() -> bytes:
    return (
        struct.pack("<hbbb", 30, 30, 20, 90)
        + b"\x00\x00\x00"
        + struct.pack("<I", int(ServiceFlags(0)))
    )


class TestNpc:
    def test_full_npc_round_trips(self) -> None:
        flag_word = int(NpcFlags.FEMALE | NpcFlags.ESSENTIAL) | (3 << 10)  # blood type 3
        ai_t = struct.pack("<3fB", 100.0, 200.0, 50.0, 1) + b"\x00\x00\x00"
        ai_w = struct.pack("<HHB8Bb", 512, 5, 0, 1, 2, 3, 4, 5, 6, 7, 8, 0)
        ai_e = struct.pack("<3fH", 1.0, 2.0, 3.0, 10) + _fixed("player", 32) + b"\x02\x00"
        body = (
            _string_sub(b"NAME", "guard_01")
            + _string_sub(b"FNAM", "Guard")
            + _string_sub(b"RNAM", "Imperial")
            + _string_sub(b"CNAM", "Guard")
            + _string_sub(b"ANAM", "Imperial Legion")
            + _string_sub(b"BNAM", "b_head")
            + _string_sub(b"KNAM", "b_hair")
            + _string_sub(b"SCRI", "guardScript")
            + _sub(b"NPDT", _npdt_full()[4:])  # _sub adds the size; strip our own
            + _sub(b"FLAG", struct.pack("<I", flag_word))
            + _sub(b"NPCO", struct.pack("<i", 1) + _fixed("iron_cuirass", 32))
            + _sub(b"NPCS", _fixed("fireball", 32))
            + _sub(b"AIDT", _aidt())
            + _sub(b"DODT", struct.pack("<6f", 1, 2, 3, 0, 0, 0))
            + _string_sub(b"DNAM", "Balmora")
            + _sub(b"AI_T", ai_t)
            + _sub(b"AI_W", ai_w)
            + _sub(b"AI_E", ai_e)
            + _string_sub(b"CNDT", "Vivec")
        )
        original = _record(b"NPC_", body)
        (npc,) = read_plugin(original)
        assert isinstance(npc, Npc)
        assert npc.race == "Imperial"
        assert npc.npc_flags is NpcFlags.FEMALE | NpcFlags.ESSENTIAL
        assert npc.blood_type == 3
        assert npc.data.stats is not None
        assert npc.data.stats.health == 100
        assert npc.data.gold == 250
        assert npc.inventory == [(1, "iron_cuirass")]
        assert npc.spells == ["fireball"]
        assert npc.ai_data.fight == 30
        assert len(npc.ai_packages) == 3
        assert npc.ai_packages[2].cell == "Vivec"  # escort's trailing CNDT
        assert npc.travel_destinations[0].cell == "Balmora"  # DODT's trailing DNAM
        assert write_plugin([npc]) == original

    def test_follow_activate_and_empty_cells_round_trip(self) -> None:
        # Exercises AI_F, AI_A, an escort with no trailing cell, and a travel
        # destination with no DNAM, plus MODL.
        ai_f = struct.pack("<3fH", 0.0, 0.0, 0.0, 5) + _fixed("boss", 32) + b"\x01\x00"
        ai_e_nocell = struct.pack("<3fH", 0.0, 0.0, 0.0, 0) + _fixed("x", 32) + b"\x00\x00"
        ai_a = _fixed("lever_01", 32) + struct.pack("<B", 1)
        body = (
            _string_sub(b"NAME", "follower")
            + _string_sub(b"MODL", "b\\npc.nif")
            + _sub(b"NPDT", _npdt_auto()[4:])
            + _sub(b"FLAG", struct.pack("<I", 0))
            + _sub(b"AIDT", _aidt())
            + _sub(b"DODT", struct.pack("<6f", 0, 0, 0, 0, 0, 0))  # no DNAM
            + _sub(b"AI_F", ai_f)
            + _string_sub(b"CNDT", "Ald-ruhn")
            + _sub(b"AI_E", ai_e_nocell)  # no trailing CNDT
            + _sub(b"AI_A", ai_a)
        )
        original = _record(b"NPC_", body)
        (npc,) = read_plugin(original)
        assert npc.mesh == "b\\npc.nif"
        assert len(npc.ai_packages) == 3
        assert npc.ai_packages[0].cell == "Ald-ruhn"  # follow's CNDT
        assert npc.ai_packages[1].cell == ""  # escort with no cell
        assert npc.ai_packages[2].target == "lever_01"  # activate
        assert npc.travel_destinations[0].cell == ""  # DODT with no DNAM
        assert write_plugin([npc]) == original

    def test_autocalc_npc_round_trips(self) -> None:
        body = (
            _string_sub(b"NAME", "commoner")
            + _sub(b"NPDT", _npdt_auto()[4:])
            + _sub(b"FLAG", struct.pack("<I", int(NpcFlags.AUTO_CALCULATE)))
            + _sub(b"AIDT", _aidt())
        )
        original = _record(b"NPC_", body)
        (npc,) = read_plugin(original)
        assert npc.data.stats is None
        assert NpcFlags.AUTO_CALCULATE in npc.npc_flags
        assert write_plugin([npc]) == original

    def test_bad_npdt_size_is_refused(self) -> None:
        from wraithguard.esp import EspError

        body = _string_sub(b"NAME", "x") + _sub(b"NPDT", struct.pack("<h", 0))
        with pytest.raises(EspError, match="NPDT size"):
            read_plugin(_record(b"NPC_", body))

    def test_unexpected_tag_and_deletion(self) -> None:
        from wraithguard.esp import EspError

        with pytest.raises(EspError, match="Unexpected Tag: NPC_"):
            read_plugin(
                _record(b"NPC_", _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )
        body = (
            _string_sub(b"NAME", "gone")
            + _sub(b"NPDT", _npdt_auto()[4:])
            + _sub(b"FLAG", struct.pack("<I", 0))
            + _sub(b"AIDT", _aidt())
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"NPC_", body, flags=int(ObjectFlags.DELETED))
        (npc,) = read_plugin(original)
        assert ObjectFlags.DELETED in npc.flags
        assert write_plugin([npc]) == original


class TestNpcDataDefaults:
    def test_stats_default_absent(self) -> None:
        assert NpcData().stats is None
        assert NpcStats().health == 0
