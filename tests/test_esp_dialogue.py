"""The ``DIAL`` dialogue-topic record.

A topic is an id and a one-byte type. A live topic round-trips byte-for-byte; a
deleted topic's four-byte ``DATA`` (which some files carry) is skipped on read,
matching the crate.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import Dialogue, EspError, read_plugin, write_plugin
from wraithguard.esp.enums import DialogueType2
from wraithguard.esp.flags import ObjectFlags


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


class TestDialogue:
    def test_topic_round_trips(self) -> None:
        body = _string_sub(b"NAME", "Background") + _sub(
            b"DATA", struct.pack("<B", int(DialogueType2.Topic))
        )
        original = _record(b"DIAL", body)
        (dial,) = read_plugin(original)
        assert isinstance(dial, Dialogue)
        assert dial.id == "Background"
        assert dial.dialogue_type is DialogueType2.Topic
        assert write_plugin([dial]) == original

    def test_journal_type_round_trips(self) -> None:
        body = _string_sub(b"NAME", "A1_1_FindSpymaster") + _sub(
            b"DATA", struct.pack("<B", int(DialogueType2.Journal))
        )
        original = _record(b"DIAL", body)
        (dial,) = read_plugin(original)
        assert dial.dialogue_type is DialogueType2.Journal
        assert write_plugin([dial]) == original

    def test_deleted_topic_with_four_byte_data_is_skipped(self) -> None:
        body = (
            _string_sub(b"NAME", "gone")
            + _sub(b"DATA", struct.pack("<I", 0))  # 4-byte DATA, deleted convention
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        (dial,) = read_plugin(_record(b"DIAL", body, flags=int(ObjectFlags.DELETED)))
        assert ObjectFlags.DELETED in dial.flags
        assert dial.dialogue_type is DialogueType2.default()

    def test_unexpected_tag_is_refused(self) -> None:
        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: DIAL"):
            read_plugin(_record(b"DIAL", body))
