"""The book, misc item and light records -- data blocks with enums and a colour.

Book carries a signed skill id (``SkillId.None`` is -1, and must survive as -1,
not wrap to a huge unsigned value); the light carries a four-byte RGBA colour
that must round-trip verbatim. Each is read to the right fields and written back
byte-for-byte.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import Book, Light, MiscItem, read_plugin, write_plugin
from wraithguard.esp.enums import BookType, SkillId
from wraithguard.esp.flags import LightFlags, MiscItemFlags, ObjectFlags


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


class TestBook:
    def test_scroll_with_skill_and_text_round_trips(self) -> None:
        block = struct.pack("<fIiiI", 0.2, 50, int(BookType.Scroll), int(SkillId.Alchemy), 0)
        body = (
            _string_sub(b"NAME", "scroll_id")
            + _string_sub(b"MODL", "m\\scroll.nif")
            + _string_sub(b"FNAM", "Scroll of Fire")
            + _sub(b"BKDT", block)
            + _string_sub(b"SCRI", "scrollScript")
            + _string_sub(b"ITEX", "t\\scroll.dds")
            + _string_sub(b"TEXT", "<DIV>burn</DIV>")
            + _string_sub(b"ENAM", "fire_ench")
        )
        original = _record(b"BOOK", body)
        (book,) = read_plugin(original)
        assert isinstance(book, Book)
        assert book.data.book_type is BookType.Scroll
        assert book.data.skill is SkillId.Alchemy
        assert book.text == "<DIV>burn</DIV>"
        assert book.enchanting == "fire_ench"
        assert write_plugin([book]) == original

    def test_a_none_skill_stays_negative_one(self) -> None:
        block = struct.pack("<fIiiI", 1.0, 10, int(BookType.Book), int(SkillId.None_), 0)
        body = _string_sub(b"NAME", "plain") + _sub(b"BKDT", block)
        (book,) = read_plugin(_record(b"BOOK", body))
        assert book.data.skill is SkillId.None_
        assert int(book.data.skill) == -1
        assert write_plugin([book]) == _record(b"BOOK", body)


class TestMiscItem:
    def test_key_flag_round_trips(self) -> None:
        block = struct.pack("<fII", 0.0, 1, int(MiscItemFlags.KEY))
        body = _string_sub(b"NAME", "key_id") + _sub(b"MCDT", block)
        original = _record(b"MISC", body)
        (misc,) = read_plugin(original)
        assert isinstance(misc, MiscItem)
        assert MiscItemFlags.KEY in misc.data.flags
        assert write_plugin([misc]) == original

    def test_all_optional_fields_round_trip(self) -> None:
        block = struct.pack("<fII", 1.0, 5, 0)
        body = (
            _string_sub(b"NAME", "misc_id")
            + _string_sub(b"MODL", "m\\misc.nif")
            + _string_sub(b"FNAM", "Trinket")
            + _sub(b"MCDT", block)
            + _string_sub(b"SCRI", "miscScript")
            + _string_sub(b"ITEX", "t\\misc.dds")
        )
        original = _record(b"MISC", body)
        (misc,) = read_plugin(original)
        assert (misc.name, misc.script, misc.icon) == ("Trinket", "miscScript", "t\\misc.dds")
        assert write_plugin([misc]) == original


class TestGuardsAndDeletion:
    @pytest.mark.parametrize("tag", [b"BOOK", b"MISC", b"LIGH"])
    def test_unexpected_tag_is_refused(self, tag: bytes) -> None:
        from wraithguard.esp import EspError

        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag"):
            read_plugin(_record(tag, body))

    def test_deleted_book_round_trips(self) -> None:
        block = struct.pack("<fIiiI", 1.0, 10, int(BookType.Book), int(SkillId.None_), 0)
        body = (
            _string_sub(b"NAME", "gone") + _sub(b"BKDT", block) + b"DELE" + struct.pack("<II", 4, 0)
        )
        original = _record(b"BOOK", body, flags=int(ObjectFlags.DELETED))
        (book,) = read_plugin(original)
        assert ObjectFlags.DELETED in book.flags
        assert write_plugin([book]) == original

    def test_deleted_misc_round_trips(self) -> None:
        body = (
            _string_sub(b"NAME", "gone")
            + _sub(b"MCDT", struct.pack("<fII", 0.0, 0, 0))
            + b"DELE"
            + struct.pack("<II", 4, 0)
        )
        original = _record(b"MISC", body, flags=int(ObjectFlags.DELETED))
        (misc,) = read_plugin(original)
        assert ObjectFlags.DELETED in misc.flags
        assert write_plugin([misc]) == original


class TestLight:
    def test_colour_and_flags_round_trip(self) -> None:
        block = struct.pack("<fIiI", 2.0, 15, 300, 256) + bytes([255, 200, 100, 0])
        block += struct.pack("<I", int(LightFlags.DYNAMIC | LightFlags.FLICKER))
        body = (
            _string_sub(b"NAME", "torch_id")
            + _string_sub(b"MODL", "m\\torch.nif")
            + _string_sub(b"FNAM", "Torch")
            + _string_sub(b"ITEX", "t\\torch.dds")
            + _sub(b"LHDT", block)
            + _string_sub(b"SCRI", "torchScript")
            + _string_sub(b"SNAM", "torch.wav")
        )
        original = _record(b"LIGH", body)
        (light,) = read_plugin(original)
        assert isinstance(light, Light)
        assert light.data.color == bytes([255, 200, 100, 0])
        assert light.data.radius == 256
        assert LightFlags.DYNAMIC in light.data.flags
        assert light.sound == "torch.wav"
        assert write_plugin([light]) == original

    def test_deleted_light_round_trips(self) -> None:
        block = struct.pack("<fIiI", 0.0, 0, 0, 0) + b"\x00\x00\x00\x00" + struct.pack("<I", 0)
        body = (
            _string_sub(b"NAME", "gone") + _sub(b"LHDT", block) + b"DELE" + struct.pack("<II", 4, 0)
        )
        original = _record(b"LIGH", body, flags=int(ObjectFlags.DELETED))
        (light,) = read_plugin(original)
        assert ObjectFlags.DELETED in light.flags
        assert write_plugin([light]) == original
