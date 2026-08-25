"""The item-shaped records: static, activator, door, and the four data items.

These share one shape -- string subrecords and, for most, a fixed data block --
so one parametrised round-trip covers them: a hand-built record read back to the
right fields, and written out byte-for-byte. Field order in each data block, and
the save order of each record's subrecords, are exactly the crate's; a swapped
pair would break the byte-for-byte check here.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import (
    Activator,
    Apparatus,
    Door,
    Lockpick,
    Probe,
    RepairItem,
    Static,
    read_plugin,
    write_plugin,
)
from wraithguard.esp.enums import ApparatusType
from wraithguard.esp.flags import ObjectFlags


def _sub(tag: bytes, body: bytes) -> bytes:
    """A subrecord: tag, u32 size, body."""
    return tag + struct.pack("<I", len(body)) + body


def _string_sub(tag: bytes, text: str) -> bytes:
    """A string subrecord, null-terminated as the format stores it."""
    return _sub(tag, text.encode("cp1252") + b"\x00")


def _record(tag: bytes, subrecords: bytes, flags: int = 0) -> bytes:
    """Frame subrecords as an on-disk record with a 16-byte header."""
    return (
        tag
        + struct.pack("<I", len(subrecords))
        + struct.pack("<I", 0)
        + struct.pack("<I", flags)
        + subrecords
    )


class TestStatic:
    def test_reads_and_round_trips(self) -> None:
        body = _string_sub(b"NAME", "in_door_01") + _string_sub(b"MODL", "i\\in_door_01.nif")
        original = _record(b"STAT", body)
        (static,) = read_plugin(original)
        assert isinstance(static, Static)
        assert static.id == "in_door_01"
        assert static.mesh == "i\\in_door_01.nif"
        assert write_plugin([static]) == original

    def test_missing_mesh_is_omitted(self) -> None:
        original = _record(b"STAT", _string_sub(b"NAME", "marker"))
        (static,) = read_plugin(original)
        assert static.mesh == ""
        assert write_plugin([static]) == original


class TestActivatorAndDoor:
    def test_activator_round_trips(self) -> None:
        body = (
            _string_sub(b"NAME", "act_id")
            + _string_sub(b"MODL", "m\\act.nif")
            + _string_sub(b"FNAM", "Activator")
            + _string_sub(b"SCRI", "actScript")
        )
        original = _record(b"ACTI", body)
        (act,) = read_plugin(original)
        assert isinstance(act, Activator)
        assert (act.id, act.name, act.script, act.mesh) == (
            "act_id",
            "Activator",
            "actScript",
            "m\\act.nif",
        )
        assert write_plugin([act]) == original

    def test_door_carries_both_sounds(self) -> None:
        body = (
            _string_sub(b"NAME", "door_id")
            + _string_sub(b"MODL", "m\\door.nif")
            + _string_sub(b"FNAM", "Door")
            + _string_sub(b"SCRI", "doorScript")
            + _string_sub(b"SNAM", "open.wav")
            + _string_sub(b"ANAM", "close.wav")
        )
        original = _record(b"DOOR", body)
        (door,) = read_plugin(original)
        assert isinstance(door, Door)
        assert door.open_sound == "open.wav"
        assert door.close_sound == "close.wav"
        assert write_plugin([door]) == original


class TestDataItems:
    def test_apparatus_reads_type_and_numbers(self) -> None:
        block = struct.pack("<IffI", int(ApparatusType.Calcinator), 1.5, 2.0, 30)
        body = (
            _string_sub(b"NAME", "app_id")
            + _string_sub(b"MODL", "m\\app.nif")
            + _string_sub(b"FNAM", "Calcinator")
            + _string_sub(b"SCRI", "s")
            + _sub(b"AADT", block)
            + _string_sub(b"ITEX", "t\\app.dds")
        )
        original = _record(b"APPA", body)
        (appa,) = read_plugin(original)
        assert isinstance(appa, Apparatus)
        assert appa.data.apparatus_type is ApparatusType.Calcinator
        assert appa.data.quality == pytest.approx(1.5)
        assert appa.data.value == 30
        assert write_plugin([appa]) == original

    @pytest.mark.parametrize(
        ("tag", "data_tag", "cls", "block"),
        [
            (b"LOCK", b"LKDT", Lockpick, struct.pack("<fIfI", 1.0, 20, 0.5, 25)),
            (b"PROB", b"PBDT", Probe, struct.pack("<fIfI", 1.0, 20, 0.5, 25)),
            (b"REPA", b"RIDT", RepairItem, struct.pack("<fIIf", 1.0, 20, 25, 0.5)),
        ],
    )
    def test_tool_items_round_trip(
        self, tag: bytes, data_tag: bytes, cls: type, block: bytes
    ) -> None:
        body = (
            _string_sub(b"NAME", "tool_id")
            + _string_sub(b"MODL", "m\\tool.nif")
            + _string_sub(b"FNAM", "Tool")
            + _sub(data_tag, block)
            + _string_sub(b"SCRI", "s")
            + _string_sub(b"ITEX", "t\\tool.dds")
        )
        original = _record(tag, body)
        (item,) = read_plugin(original)
        assert isinstance(item, cls)
        assert item.data.weight == pytest.approx(1.0)
        assert item.data.value == 20
        assert item.data.quality == pytest.approx(0.5)
        assert item.data.uses == 25
        assert write_plugin([item]) == original

    def test_wrong_data_size_is_refused(self) -> None:
        from wraithguard.esp import EspError

        bad = _string_sub(b"NAME", "x") + b"LKDT" + struct.pack("<I", 8) + b"\x00" * 8
        with pytest.raises(EspError, match="LKDT size"):
            read_plugin(_record(b"LOCK", bad))

    @pytest.mark.parametrize(
        ("tag", "prefix"),
        [
            (b"STAT", b""),
            (b"ACTI", b""),
            (b"DOOR", b""),
            (b"APPA", b""),
            (b"LOCK", b""),
            (b"PROB", b""),
            (b"REPA", b""),
        ],
    )
    def test_each_record_refuses_an_unexpected_tag(self, tag: bytes, prefix: bytes) -> None:
        from wraithguard.esp import EspError

        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0) + prefix
        with pytest.raises(EspError, match="Unexpected Tag"):
            read_plugin(_record(tag, body))

    #: A minimal valid body per record tag: NAME, plus the fixed block records
    #: require (before DELE), matching each record's save order.
    _BODIES = {
        b"STAT": _string_sub(b"NAME", "gone"),
        b"ACTI": _string_sub(b"NAME", "gone"),
        b"DOOR": _string_sub(b"NAME", "gone"),
        b"APPA": _string_sub(b"NAME", "gone") + _sub(b"AADT", struct.pack("<IffI", 0, 0.0, 0.0, 0)),
        b"LOCK": _string_sub(b"NAME", "gone")
        + _sub(b"LKDT", struct.pack("<fIfI", 1.0, 20, 0.5, 25)),
        b"PROB": _string_sub(b"NAME", "gone")
        + _sub(b"PBDT", struct.pack("<fIfI", 1.0, 20, 0.5, 25)),
        b"REPA": _string_sub(b"NAME", "gone")
        + _sub(b"RIDT", struct.pack("<fIIf", 1.0, 20, 25, 0.5)),
    }

    @pytest.mark.parametrize("tag", list(_BODIES))
    def test_deleted_record_round_trips(self, tag: bytes) -> None:
        body = self._BODIES[tag] + b"DELE" + struct.pack("<I", 4) + struct.pack("<I", 0)
        original = _record(tag, body, flags=int(ObjectFlags.DELETED))
        (record,) = read_plugin(original)
        assert ObjectFlags.DELETED in record.flags
        assert write_plugin([record]) == original
