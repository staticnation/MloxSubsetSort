"""The ESP byte layer's primitives, in isolation from any record.

Every integer width and both floats must round-trip little-endian, tags must be
four bytes, and the size-patching a record relies on must land where it is told.
Small, exhaustive, and independent of the format's records.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp.io import EspError, Reader, Writer
from wraithguard.esp.record import UnknownRecord


class TestPrimitiveRoundTrips:
    @pytest.mark.parametrize(
        ("method", "value"),
        [
            ("i8", -128),
            ("u8", 255),
            ("i16", -32768),
            ("u16", 65535),
            ("i32", -2147483648),
            ("u32", 4294967295),
            ("i64", -(2**63)),
            ("u64", 2**64 - 1),
        ],
    )
    def test_integers(self, method: str, value: int) -> None:
        writer = Writer()
        getattr(writer, method)(value)
        assert getattr(Reader(writer.getvalue()), method)() == value

    @pytest.mark.parametrize("method", ["f32", "f64"])
    def test_floats(self, method: str) -> None:
        writer = Writer()
        getattr(writer, method)(1.5)
        assert getattr(Reader(writer.getvalue()), method)() == pytest.approx(1.5)

    def test_tag_must_be_four_bytes(self) -> None:
        with pytest.raises(EspError, match="four bytes"):
            Writer().tag(b"AB")

    def test_tag_round_trips(self) -> None:
        writer = Writer()
        writer.tag(b"WEAP")
        assert Reader(writer.getvalue()).tag() == b"WEAP"

    def test_undecodable_string_is_refused(self) -> None:
        # 0x81 has no Windows-1252 character.
        raw = struct.pack("<I", 2) + b"\x81\x00"
        with pytest.raises(EspError, match="undecodable"):
            Reader(raw).string()

    def test_string_of_fixed_width_truncates_at_null(self) -> None:
        assert Reader(b"hi\x00\x00\x00").string_of(5) == "hi"

    def test_string_of_zero_length_is_empty(self) -> None:
        assert Reader(b"").string_of(0) == ""

    def test_string_of_without_a_null_reads_all_bytes(self) -> None:
        assert Reader(b"abcde").string_of(5) == "abcde"

    def test_writer_string_with_embedded_null_stops_there(self) -> None:
        writer = Writer()
        writer.string("ab\x00cd")
        # length up to the null, no added terminator; reads back to the null
        assert Reader(writer.getvalue()).string() == "ab"

    def test_writer_string_rejects_unencodable_text(self) -> None:
        with pytest.raises(EspError, match="unencodable"):
            Writer().string("☃")  # snowman, not in Windows-1252

    def test_fixed_string_pads_to_width_and_round_trips(self) -> None:
        writer = Writer()
        writer.fixed_string("spell_id", 32)
        raw = writer.getvalue()
        assert len(raw) == 32
        # a length-prefixed read of the same bytes recovers the value
        assert Reader(struct.pack("<I", 32) + raw).string() == "spell_id"

    def test_fixed_string_rejects_unencodable_text(self) -> None:
        with pytest.raises(EspError, match="unencodable"):
            Writer().fixed_string("☃", 32)

    def test_string_no_terminator_writes_no_null(self) -> None:
        writer = Writer()
        writer.string_no_terminator("hi")
        assert writer.getvalue() == struct.pack("<I", 2) + b"hi"

    def test_string_no_terminator_rejects_unencodable_text(self) -> None:
        with pytest.raises(EspError, match="unencodable"):
            Writer().string_no_terminator("☃")

    def test_mark_and_patch_fill_a_size_in_after_the_fact(self) -> None:
        writer = Writer()
        at = writer.mark()
        writer.u32(0)
        writer.raw(b"abcd")
        writer.patch_u32(at, len(writer) - at - 4)
        assert struct.unpack_from("<I", writer.getvalue())[0] == 4

    def test_skip_advances_without_reading(self) -> None:
        reader = Reader(b"....XY")
        reader.skip(4)
        assert reader.raw(2) == b"XY"


class TestUnknownRecordHelpers:
    def test_repr_names_tag_and_size(self) -> None:
        record = UnknownRecord(b"LUAL", flags=None, body=b"abc")  # type: ignore[arg-type]
        assert "LUAL" in repr(record) and "3 bytes" in repr(record)

    def test_load_is_not_used_directly(self) -> None:
        with pytest.raises(NotImplementedError):
            UnknownRecord.load(Reader(b""), None)  # type: ignore[arg-type]
