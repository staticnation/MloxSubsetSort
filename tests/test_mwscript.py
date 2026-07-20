"""Tests for compiled-script (SCDT) reading and disassembly.

The governing rule for this module is *never invent*: a disassembler that
emits a plausible-but-wrong instruction is worse than one that admits it
cannot decode a span, because the output is meant to be read as evidence when
diffing two versions of a script. Most tests below check that boundary rather
than checking that decoding succeeds.
"""

from __future__ import annotations

import base64
import io
import struct
import sys

import pytest

from mlox_subset.mwscript import (
    BytecodeDecodeError,
    Instruction,
    RawBytes,
    decode_bytecode_field,
    decode_variables_field,
    disassemble,
    format_listing,
    listing_for_bytecode_field,
    read_script_records,
    variables_text_for_field,
)
from mlox_subset.mwscript.opcodes import BY_NAME, FUNCTIONS, INTERNAL
from mlox_subset.mwscript.tes3conv import ZSTD_MAGIC


def bytecode(*chunks: bytes, declared: int | None = None) -> bytes:
    """Build a SCDT payload: 4-byte length prefix followed by ``chunks``."""
    body = b"".join(chunks)
    return struct.pack("<I", declared if declared is not None else len(body)) + body


def op(name: str) -> bytes:
    """The little-endian opcode bytes for a named function."""
    return struct.pack("<H", BY_NAME[name.lower()])


class TestOpcodeTable:
    """The table is generated from MWEdit's Functions.dat (MIT)."""

    def test_table_is_populated(self):
        assert len(FUNCTIONS) > 500

    @pytest.mark.parametrize(
        ("name", "opcode"),
        [("Journal", 0x10CC), ("Disable", 0x10DB), ("AddItem", 0x10D4), ("Show", 0x010D)],
    )
    def test_known_opcodes(self, name, opcode):
        """These were also derived independently from a corpus of real
        scripts by correlating opcodes against source keywords."""
        assert BY_NAME[name.lower()] == opcode

    def test_name_lookup_matches_the_forward_table(self):
        for opcode, (name, _params) in FUNCTIONS.items():
            assert BY_NAME[name.lower()] == opcode

    def test_else_and_endif_are_not_function_opcodes(self):
        """Guards against a widely-circulated but fabricated table that
        claimed 0x010D=ELSE and 0x010E=ENDIF. 0x010D is really `Show`, and
        neither value appeared in 428 real scripts as those keywords."""
        assert FUNCTIONS[0x010D][0] == "Show"
        assert "else" not in BY_NAME
        assert "endif" not in BY_NAME


class TestLengthPrefix:
    def test_declared_length_is_read(self):
        listing = disassemble(bytecode(op("Disable"), declared=2))
        assert listing.declared_length == 2

    def test_absent_prefix_can_be_disabled(self):
        listing = disassemble(op("Disable"), has_length_prefix=False)
        assert listing.declared_length is None
        assert [i.name for i in listing.instructions] == ["Disable"]


class TestDecoding:
    def test_decodes_a_no_argument_instruction(self):
        listing = disassemble(bytecode(op("Disable")))
        assert [i.name for i in listing.instructions] == ["Disable"]

    def test_decodes_a_sequence_in_order(self):
        listing = disassemble(bytecode(op("Disable"), op("Enable"), op("Show")))
        assert [i.name for i in listing.instructions] == ["Disable", "Enable", "Show"]

    def test_omitted_optional_argument_still_decodes(self):
        """`Activate` takes one optional id; absent, it is still an
        instruction rather than a failed decode."""
        listing = disassemble(bytecode(op("Activate")))
        assert [i.name for i in listing.instructions] == ["Activate"]

    def test_offsets_account_for_the_prefix(self):
        listing = disassemble(bytecode(op("Disable"), op("Enable")))
        assert [i.offset for i in listing.instructions] == [4, 6]

    def test_unknown_bytes_become_a_raw_span(self):
        listing = disassemble(bytecode(b"\x00\x00\x00\x00"))
        assert listing.instructions == []
        assert [type(i) for i in listing.items] == [RawBytes]

    def test_raw_spans_are_coalesced(self):
        listing = disassemble(bytecode(b"\x00\x00\x00\x00", op("Disable")))
        assert [type(i) for i in listing.items] == [RawBytes, Instruction]

    def test_resynchronises_after_undecodable_data(self):
        """The key property: unknown data must not desync the walker."""
        listing = disassemble(bytecode(op("Disable"), b" == 1 some expression ", op("Enable")))
        assert [i.name for i in listing.instructions] == ["Disable", "Enable"]


class TestNeverInvents:
    def test_absurd_float_operand_is_refused(self):
        """Expression bytes read as a float give wild magnitudes; that means
        the bytes were not an instruction at all."""
        float_fn = next((o for o, (_n, p) in FUNCTIONS.items() if p and p[0] & 0x8), None)
        if float_fn is None:  # pragma: no cover - table always has one
            pytest.skip("no float-taking function in the table")
        payload = bytecode(struct.pack("<H", float_fn) + struct.pack("<f", 1.8e22))
        assert disassemble(payload).instructions == []

    def test_mangled_identifier_is_refused(self):
        """A misread length byte makes the 'string' run into neighbouring
        expression data; such operands must be rejected, not shown."""
        id_fn = next((o for o, (_n, p) in FUNCTIONS.items() if p and p[0] & 0x20), None)
        if id_fn is None:  # pragma: no cover
            pytest.skip("no id-taking function in the table")
        bad = b'\x0cname" == 1 x'
        assert disassemble(bytecode(struct.pack("<H", id_fn) + bad)).instructions == []

    def test_source_hint_suppresses_coincidental_matches(self):
        """An opcode value occurring inside expression data must not be
        decoded when the source proves the script never calls it."""
        payload = bytecode(op("Disable"), op("Enable"))
        without = disassemble(payload)
        with_hint = disassemble(payload, source_text="begin x\nDisable\nend")
        assert {i.name for i in without.instructions} == {"Disable", "Enable"}
        assert [i.name for i in with_hint.instructions] == ["Disable"]


class TestInternalOpcodes:
    """Compiler-emitted opcodes (jumps, pushes, local access).

    These carry no source-level name -- `_SetReference` is what the compiler
    emits for a `->` call, not something anyone writes. The source-text hint
    must therefore never filter them out, or every hinted disassembly would
    lose its control flow.
    """

    def test_internal_opcodes_are_present(self):
        assert INTERNAL
        assert all(FUNCTIONS[opcode][0].startswith("_") for opcode in INTERNAL)

    def test_internal_opcodes_survive_the_source_hint(self):
        """Regression: the hint filter used to drop these, because their
        names appear in no script's source text."""
        set_reference = BY_NAME["_setreference"]
        payload = bytecode(struct.pack("<H", set_reference) + b"\x06player")
        listing = disassemble(payload, source_text="begin x\nend")
        assert [i.name for i in listing.instructions] == ["_SetReference"]
        assert listing.instructions[0].operands == ("player",)

    def test_named_functions_are_still_filtered(self):
        """The hint must keep working for ordinary functions."""
        listing = disassemble(bytecode(op("Enable")), source_text="begin x\nend")
        assert listing.instructions == []


class TestListingReporting:
    def test_decoded_ratio_is_honest(self):
        all_known = disassemble(bytecode(op("Disable"), op("Enable")))
        none_known = disassemble(bytecode(b"\x00" * 8))
        assert all_known.decoded_ratio == 1.0
        assert none_known.decoded_ratio == 0.0

    def test_empty_input_does_not_divide_by_zero(self):
        assert disassemble(b"").decoded_ratio == 0.0

    def test_format_listing_shows_instructions_and_hex(self):
        text = format_listing(disassemble(bytecode(op("Disable"), b"\x00\x01\x02")))
        assert "Disable" in text
        assert "declared bytecode length" in text
        assert "|" in text  # the ascii gutter of the hex dump


class TestMalformedInput:
    @pytest.mark.parametrize(
        "data",
        [b"", b"\x01", b"\x00\x00\x00", b"\xff" * 3, struct.pack("<I", 9999), b"\x00" * 64],
    )
    def test_never_raises(self, data):
        listing = disassemble(data)
        assert isinstance(listing.items, list)

    def test_truncated_operand_does_not_raise(self):
        id_fn = next((o for o, (_n, p) in FUNCTIONS.items() if p and p[0] & 0x20), None)
        if id_fn is None:  # pragma: no cover
            pytest.skip("no id-taking function")
        listing = disassemble(bytecode(struct.pack("<H", id_fn) + b"\x40ab"))
        assert listing.instructions == []


class TestScriptRecordReader:
    """Reads SCDT straight from the plugin, avoiding tes3conv's zstd layer."""

    def _plugin(self, tmp_path, name="test_script", code=b"", text=b"", variables=b""):
        def sub(tag: bytes, payload: bytes) -> bytes:
            return tag + struct.pack("<I", len(payload)) + payload

        schd = name.encode().ljust(32, b"\x00") + struct.pack("<5I", 1, 2, 3, len(code), 0)
        body = sub(b"SCHD", schd)
        if variables:
            body += sub(b"SCVR", variables)
        if code:
            body += sub(b"SCDT", code)
        if text:
            body += sub(b"SCTX", text)
        record = b"SCPT" + struct.pack("<III", len(body), 0, 0) + body
        header = b"TES3" + struct.pack("<III", 0, 0, 0)
        path = tmp_path / "plugin.esp"
        path.write_bytes(header + record)
        return path

    def test_reads_name_counts_and_bytecode(self, tmp_path):
        path = self._plugin(tmp_path, code=op("Disable"), text=b"begin x\nDisable\nend")
        (script,) = read_script_records(path)
        assert script.name == "test_script"
        assert (script.num_shorts, script.num_longs, script.num_floats) == (1, 2, 3)
        assert script.bytecode == op("Disable")
        assert "Disable" in script.text

    def test_reads_local_variable_names(self, tmp_path):
        path = self._plugin(tmp_path, code=op("Disable"), variables=b"status\x00timer\x00")
        (script,) = read_script_records(path)
        assert script.variables == ["status", "timer"]

    def test_bytecode_feeds_straight_into_the_disassembler(self, tmp_path):
        path = self._plugin(tmp_path, code=op("Disable"), text=b"begin x\nDisable\nend")
        (script,) = read_script_records(path)
        listing = disassemble(script.bytecode, has_length_prefix=False, source_text=script.text)
        assert [i.name for i in listing.instructions] == ["Disable"]

    @pytest.mark.parametrize("data", [b"", b"TES3", b"NOPE" + b"\x00" * 32, b"TES3" + b"\xff" * 8])
    def test_malformed_plugin_yields_no_scripts(self, tmp_path, data):
        path = tmp_path / "broken.esp"
        path.write_bytes(data)
        assert read_script_records(path) == []

    def test_missing_file_yields_no_scripts(self, tmp_path):
        assert read_script_records(tmp_path / "absent.esp") == []


class TestTes3convBytecodeField:
    """Decoding the ``bytecode`` field as tes3conv writes it into JSON."""

    def test_plain_base64_round_trips(self):
        payload = bytecode(op("Disable"))
        assert decode_bytecode_field(base64.b64encode(payload).decode()) == payload

    def test_bytes_input_is_passed_through(self):
        assert decode_bytecode_field(b"\x01\x02") == b"\x01\x02"

    def test_zstd_frame_is_detected_by_magic_not_assumed(self):
        """Both tes3conv generations must decode, so compression is sniffed
        rather than presumed."""
        zstd = pytest.importorskip("zstandard")
        payload = bytecode(op("Disable"), op("Enable"))
        frame = zstd.ZstdCompressor().compress(payload)
        assert frame.startswith(ZSTD_MAGIC)
        assert decode_bytecode_field(base64.b64encode(frame).decode()) == payload

    def test_invalid_base64_raises_a_clear_error(self):
        with pytest.raises(BytecodeDecodeError, match="base64"):
            decode_bytecode_field("not!valid!base64!")

    def test_corrupt_zstd_frame_raises_rather_than_returning_junk(self):
        pytest.importorskip("zstandard")
        junk = base64.b64encode(ZSTD_MAGIC + b"\x00" * 16).decode()
        with pytest.raises(BytecodeDecodeError):
            decode_bytecode_field(junk)

    def test_missing_zstd_backend_explains_the_alternative(self, monkeypatch):
        """With no backend the message must name a way forward, because the
        plugin file itself stores SCDT uncompressed."""
        monkeypatch.setitem(sys.modules, "compression.zstd", None)
        monkeypatch.setitem(sys.modules, "zstandard", None)
        with pytest.raises(BytecodeDecodeError) as excinfo:
            decode_bytecode_field(base64.b64encode(ZSTD_MAGIC + b"\x00" * 8).decode())
        assert "plugin file" in str(excinfo.value)

    def test_decoded_bytecode_feeds_the_disassembler(self):
        payload = bytecode(op("Disable"))
        raw = decode_bytecode_field(base64.b64encode(payload).decode())
        listing = disassemble(raw, source_text="begin x\nDisable\nend")
        assert [i.name for i in listing.instructions] == ["Disable"]

    def test_streaming_frame_without_content_size_still_decodes(self):
        """tes3conv may write frames with no size in the header; the strict
        zstd API rejects those, so the decoder must not use it."""
        zstd = pytest.importorskip("zstandard")
        payload = bytecode(op("Disable"))
        buf = io.BytesIO()
        with zstd.ZstdCompressor().stream_writer(buf, closefd=False) as writer:
            writer.write(payload)
        assert decode_bytecode_field(base64.b64encode(buf.getvalue()).decode()) == payload


class TestListingForBytecodeField:
    """The entry point the GUI diff window calls.

    Its contract is that it never raises: a field-detail window that dies on
    one malformed record is worse than one that explains the problem in place.
    """

    def test_renders_a_listing_from_a_base64_field(self):
        field = base64.b64encode(bytecode(op("Disable"))).decode()
        assert "Disable" in listing_for_bytecode_field(field, "begin x\nDisable\nend")

    def test_source_text_is_forwarded_to_the_filter(self):
        """Passing the source must suppress functions the script never calls."""
        field = base64.b64encode(bytecode(op("Disable"), op("Enable"))).decode()
        assert "Enable" not in listing_for_bytecode_field(field, "begin x\nDisable\nend")
        assert "Enable" in listing_for_bytecode_field(field)

    @pytest.mark.parametrize(
        "field",
        ["", "not!base64!", "AAAA", "////", base64.b64encode(b"\x00" * 4).decode()],
    )
    def test_never_raises_on_junk(self, field):
        assert isinstance(listing_for_bytecode_field(field), str)

    def test_undecodable_field_explains_itself_as_a_comment(self):
        out = listing_for_bytecode_field("not!base64!")
        assert out.startswith(";")
        assert "base64" in out

    def test_missing_zstd_backend_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "compression.zstd", None)
        monkeypatch.setitem(sys.modules, "zstandard", None)
        out = listing_for_bytecode_field(base64.b64encode(ZSTD_MAGIC + b"\x00" * 8).decode())
        assert out.startswith(";")
        assert "plugin file" in out


class TestVariablesField:
    """The ``variables`` field is SCVR under the same wrapping as bytecode."""

    @staticmethod
    def _field(*names: str, prefix: bool = True) -> str:
        body = b"".join(n.encode() + b"\x00" for n in names)
        raw = (struct.pack("<I", len(body)) if prefix else b"") + body
        return base64.b64encode(raw).decode()

    def test_decodes_names_in_declaration_order(self):
        field = self._field("rent", "rentDay", "rentMonth", "setup", "cleanup")
        assert decode_variables_field(field) == [
            "rent",
            "rentDay",
            "rentMonth",
            "setup",
            "cleanup",
        ]

    def test_four_byte_length_prefix_is_skipped(self):
        """Regression: without skipping it, the prefix's trailing bytes
        survive the NUL split as a junk leading 'name' -- which is exactly
        what a first attempt produced on all 118 corpus scripts."""
        names = decode_variables_field(self._field("status", "timer"))
        assert names == ["status", "timer"]
        assert len(names) == 2  # not 3

    def test_prefix_value_equals_the_body_length(self):
        """The property the corpus confirmed 120/120 times."""
        names = ("status", "bellrang", "rising")
        raw = decode_bytecode_field(self._field(*names))
        assert struct.unpack_from("<I", raw, 0)[0] == len(raw) - 4

    def test_empty_field_yields_no_names(self):
        assert decode_variables_field(base64.b64encode(struct.pack("<I", 0)).decode()) == []

    def test_text_renderer_lists_one_name_per_line(self):
        out = variables_text_for_field(self._field("status", "timer"))
        assert out.splitlines()[1:] == ["status", "timer"]
        assert out.startswith("; 2 local variable")

    def test_text_renderer_reports_no_variables(self):
        out = variables_text_for_field(base64.b64encode(struct.pack("<I", 0)).decode())
        assert "no local variables" in out

    @pytest.mark.parametrize("field", ["", "not!base64!", "AAAA", "//"])
    def test_text_renderer_never_raises(self, field):
        assert isinstance(variables_text_for_field(field), str)
