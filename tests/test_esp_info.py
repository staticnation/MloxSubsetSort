"""The ``INFO`` record -- a dialogue response, the last record to port.

Exercises the response text (stored without a null terminator), the linked-list
ids, a quest-state marker, and the filters -- each an ``SCVR`` condition whose id
length is folded into its size word, whose index is an ASCII digit, and whose
value is a float or an integer told apart by the following subrecord.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import DialogueInfo, read_plugin, write_plugin
from wraithguard.esp.enums import (
    DialogueType,
    FilterComparison,
    FilterFunction,
    FilterType,
    Sex,
)
from wraithguard.esp.flags import ObjectFlags


def _sub(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack("<I", len(body)) + body


def _string_sub(tag: bytes, text: str) -> bytes:
    return _sub(tag, text.encode("cp1252") + b"\x00")


def _name(text: str) -> bytes:
    # The response text is stored with no null terminator.
    body = text.encode("cp1252")
    return b"NAME" + struct.pack("<I", len(body)) + body


def _record(tag: bytes, subrecords: bytes, flags: int = 0) -> bytes:
    return (
        tag
        + struct.pack("<I", len(subrecords))
        + struct.pack("<I", 0)
        + struct.pack("<I", flags)
        + subrecords
    )


def _data() -> bytes:
    return _sub(
        b"DATA",
        struct.pack("<iibBb", int(DialogueType.Topic), 50, 2, int(Sex.Female), -1) + b"\x00",
    )


def _scvr(index: int, id_text: str) -> bytes:
    body = struct.pack(
        "<BBHB",
        index + 0x30,
        int(FilterType.Function),
        int(FilterFunction.HealthPercent),
        int(FilterComparison.Equal),
    ) + id_text.encode("cp1252")
    return _sub(b"SCVR", body)


class TestDialogueInfo:
    def test_full_response_round_trips(self) -> None:
        body = (
            _string_sub(b"INAM", "info_id_001")
            + _string_sub(b"PNAM", "")
            + _string_sub(b"NNAM", "info_id_002")
            + _data()
            + _string_sub(b"ONAM", "caius_cosades")
            + _string_sub(b"RNAM", "Imperial")
            + _string_sub(b"CNAM", "Spymaster")
            + _string_sub(b"FNAM", "Blades")
            + _string_sub(b"ANAM", "Balmora")
            + _string_sub(b"DNAM", "Imperial Legion")
            + _string_sub(b"SNAM", "vo\\caius.mp3")
            + _name("You've found me at last.")
            + _scvr(0, "pcHealth")
            + _sub(b"FLTV", struct.pack("<f", 75.0))
            + _scvr(1, "Blades")
            + _sub(b"INTV", struct.pack("<i", 2))
            + _string_sub(b"BNAM", "Journal A1_1 10")
        )
        original = _record(b"INFO", body)
        (info,) = read_plugin(original)
        assert isinstance(info, DialogueInfo)
        assert info.id == "info_id_001"
        assert info.next_id == "info_id_002"
        assert info.data.dialogue_type is DialogueType.Topic
        assert info.data.speaker_sex is Sex.Female
        assert info.data.player_rank == -1
        assert info.speaker_id == "caius_cosades"
        assert info.speaker_cell == "Balmora"
        assert info.text == "You've found me at last."
        assert info.script_text == "Journal A1_1 10"
        assert len(info.filters) == 2
        assert info.filters[0].index == 0
        assert info.filters[0].id == "pcHealth"
        assert info.filters[0].value == pytest.approx(75.0)
        assert info.filters[1].index == 1
        assert info.filters[1].value == 2
        assert write_plugin([info]) == original

    def test_journal_with_quest_state_round_trips(self) -> None:
        body = (
            _string_sub(b"INAM", "j1")
            + _string_sub(b"PNAM", "")
            + _string_sub(b"NNAM", "")
            + _data()
            + _name("The spymaster asked me to find him.")
            + _sub(b"QSTN", struct.pack("<B", 1))
        )
        original = _record(b"INFO", body)
        (info,) = read_plugin(original)
        assert info.quest_state == "name"
        assert write_plugin([info]) == original

    def test_finished_and_restart_quest_states(self) -> None:
        for tag, state in ((b"QSTF", "finished"), (b"QSTR", "restart")):
            body = (
                _string_sub(b"INAM", "j")
                + _string_sub(b"PNAM", "")
                + _string_sub(b"NNAM", "")
                + _data()
                + _sub(tag, struct.pack("<B", 1))
            )
            original = _record(b"INFO", body)
            (info,) = read_plugin(original)
            assert info.quest_state == state
            assert write_plugin([info]) == original

    def test_filter_value_without_scvr_is_refused(self) -> None:
        from wraithguard.esp import EspError

        body = (
            _string_sub(b"INAM", "x")
            + _string_sub(b"PNAM", "")
            + _string_sub(b"NNAM", "")
            + _data()
            + _sub(b"FLTV", struct.pack("<f", 1.0))
        )
        with pytest.raises(EspError, match="filter value without"):
            read_plugin(_record(b"INFO", body))

    def test_invalid_filter_index_is_refused(self) -> None:
        from wraithguard.esp import EspError

        body = (
            _string_sub(b"INAM", "x")
            + _string_sub(b"PNAM", "")
            + _string_sub(b"NNAM", "")
            + _data()
            + _sub(b"SCVR", struct.pack("<BBHB", 0x10, 0, 0, 0) + b"id")  # index byte < '0'
        )
        with pytest.raises(EspError, match="invalid filter index"):
            read_plugin(_record(b"INFO", body))

    def test_unencodable_filter_id_is_refused(self) -> None:
        from wraithguard.esp import EspError, Writer
        from wraithguard.esp.records.dialogueinfo import Filter

        with pytest.raises(EspError, match="unencodable filter id"):
            Filter(id="☃").save(Writer())  # snowman, not in Windows-1252

    def test_unexpected_tag_and_deletion(self) -> None:
        from wraithguard.esp import EspError

        with pytest.raises(EspError, match="Unexpected Tag: INFO"):
            read_plugin(
                _record(b"INFO", _string_sub(b"INAM", "x") + b"ZZZZ" + struct.pack("<I", 0))
            )
        body = (
            _string_sub(b"INAM", "gone")
            + _string_sub(b"PNAM", "")
            + _string_sub(b"NNAM", "")
            + _data()
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"INFO", body, flags=int(ObjectFlags.DELETED))
        (info,) = read_plugin(original)
        assert ObjectFlags.DELETED in info.flags
        assert write_plugin([info]) == original
