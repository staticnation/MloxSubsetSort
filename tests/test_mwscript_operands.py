"""Operand-decoder guards in the compiled-script disassembler.

``_read_operands`` is the point where the disassembler either produces an
operand or admits it cannot. The rule is *never invent*: every encoding it does
not model, and every operand that would run past the end of the data, must
return ``None`` so the caller falls back to an honest raw span. Those refusals
are pinned here directly on the helper, alongside the two plausibility checks
and the raw-span text rendering.
"""

from __future__ import annotations

import struct

from wraithguard.mwscript.disassembler import (
    FLAG_FLOAT,
    FLAG_ID,
    FLAG_LONG,
    FLAG_MANY,
    FLAG_OPTIONAL,
    FLAG_STRING,
    RawBytes,
    _plausible_float,
    _plausible_identifier,
    _read_operands,
)


class TestPlausibleFloat:
    """A wild magnitude means the bytes were never really a float."""

    def test_a_non_finite_value_is_refused(self) -> None:
        """Infinity and NaN cannot be script constants."""
        assert _plausible_float(float("inf")) is False
        assert _plausible_float(float("nan")) is False

    def test_an_absurd_magnitude_is_refused(self) -> None:
        """A value far outside the sane band is rejected."""
        assert _plausible_float(1e30) is False

    def test_zero_and_ordinary_values_pass(self) -> None:
        """Exact zero and everyday magnitudes are accepted."""
        assert _plausible_float(0.0) is True
        assert _plausible_float(3.5) is True


class TestPlausibleIdentifier:
    """A length-prefixed name has to look like a Morrowind identifier."""

    def test_empty_text_is_not_an_identifier(self) -> None:
        """A zero-length name is not evidence of a real operand."""
        assert _plausible_identifier("") is False

    def test_control_characters_are_refused(self) -> None:
        """A byte the length field mislabelled shows up as an unprintable."""
        assert _plausible_identifier("bad\x01name") is False

    def test_an_ordinary_name_is_accepted(self) -> None:
        """Alphanumerics and the allowed punctuation pass."""
        assert _plausible_identifier("Player_Var-1.esp") is True


class TestReadOperandsRefusals:
    """Every operand that cannot be substantiated returns None."""

    def test_a_truncated_fixed_width_operand_refuses(self) -> None:
        """A LONG needs four bytes; three is a refusal, not a guess."""
        assert _read_operands(b"\x01\x02\x03", 0, (FLAG_LONG,)) is None

    def test_a_wild_float_operand_refuses(self) -> None:
        """A four-byte span that decodes to nonsense is not a float."""
        data = struct.pack("<f", 1e30)
        assert _read_operands(data, 0, (FLAG_FLOAT,)) is None

    def test_a_name_with_no_length_byte_refuses(self) -> None:
        """A string operand with nothing left to read is refused."""
        assert _read_operands(b"", 0, (FLAG_STRING,)) is None

    def test_a_name_longer_than_the_data_refuses(self) -> None:
        """A length byte promising more than exists is refused."""
        assert _read_operands(b"\x05ab", 0, (FLAG_ID,)) is None

    def test_a_name_that_is_not_an_identifier_refuses(self) -> None:
        """A length byte that was not really a length is refused."""
        data = b"\x03\x01\x02\x03"  # length 3, then control bytes
        assert _read_operands(data, 0, (FLAG_STRING,)) is None

    def test_an_unmodelled_encoding_refuses(self) -> None:
        """A flag word this decoder does not model returns None."""
        assert _read_operands(b"\x00\x00\x00\x00", 0, (FLAG_MANY,)) is None


class TestReadOperandsHappyStops:
    """The non-refusal early exits: optional arguments that simply end."""

    def test_an_optional_with_no_bytes_left_stops(self) -> None:
        """An optional argument with no data was simply not supplied."""
        operands, pos = _read_operands(b"", 0, (FLAG_OPTIONAL,))
        assert operands == []
        assert pos == 0

    def test_an_optional_unmodelled_operand_stops_the_list(self) -> None:
        """With bytes present but no known encoding, an optional ends cleanly."""
        operands, _pos = _read_operands(b"\xff\xff", 0, (FLAG_OPTIONAL,))
        assert operands == []


class TestReadOperandsDecodes:
    """The successful fixed-width decode paths."""

    def test_a_long_operand_decodes_to_its_value(self) -> None:
        """A four-byte LONG yields the signed integer and advances four bytes."""
        operands, pos = _read_operands(struct.pack("<i", 42), 0, (FLAG_LONG,))
        assert operands == [42]
        assert pos == 4

    def test_a_sane_float_operand_decodes(self) -> None:
        """A plausible float is kept as an operand."""
        operands, pos = _read_operands(struct.pack("<f", 2.5), 0, (FLAG_FLOAT,))
        assert operands == [2.5]
        assert pos == 4


def test_a_recognised_opcode_with_bad_operands_extends_a_pending_span() -> None:
    """A junk byte then a real opcode whose operands fail stay one raw span.

    The first byte is not an opcode start, so it opens a pending span; the next
    two bytes are a valid opcode whose fixed-width operand runs off the end, so
    it too is refused and folds into the *same* span rather than starting a new
    one.
    """
    from wraithguard.mwscript.disassembler import disassemble

    data = struct.pack("<I", 3) + b"\x00" + struct.pack("<H", 0x126)  # 0x126 = MenuTest
    listing = disassemble(data)
    assert [type(i).__name__ for i in listing.items] == ["RawBytes"]
    span = listing.items[0]
    assert isinstance(span, RawBytes)
    assert span.data == b"\x00\x26\x01"


def test_raw_span_renders_printable_and_dots() -> None:
    """A raw span shows printable bytes and replaces the rest with dots."""
    span = RawBytes(0, b"Ab\x00\x7f!")
    assert span.text == "Ab..!"
