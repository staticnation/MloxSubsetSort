"""Tests for ``tools/gen_tes3_schema.py``, the record/subrecord schema generator.

The schema drives the native ESP reader, and it is built from UESP's format
pages -- prose written by people -- so the generator is a best-effort parser:
type strings with array extents, struct member lines (with the ``=`` typo the
tables contain), variant headings, per-element array sizing, and the noise rows
that share the four-column shape. Every step is pure text-in/data-out, so a
hand-built CSV export exercises them; ``main`` runs against it with the real
generated module saved and restored.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import gen_tes3_schema
from tools.gen_tes3_schema import (
    _clean,
    _declared_bytes,
    _description,
    _quote,
    emit,
    main,
    member_size,
    parse_csv,
    parse_layout,
    parse_type,
)

_ROWS = [
    ["Morrowind Mod:Mod File Format/LAND"],
    ["The UESPWiki entry, skipped as a description"],
    ["Landscape data"],
    ["C", "Field", "Type/Size", "Info"],
    ["*", "NAME", "uint32", "Land flags"],
    ["", "", "", ""],  # a full-width note: no field name, skipped
    ["*", "Jump", "uint32", "a second table's row, not a subrecord tag"],
    ["1", "DATA", "struct (4 bytes)", "uint32 - Flags"],  # opens on a member line
    ["1", "VNML", "struct (12675 bytes)", "Normals\nint8 - X\nint8 - Y\nint8 - Z"],
    # blank line, a stray prose line (no dash), and a dash line whose left side
    # is not a type -- each of the parser's "skip this line" branches.
    [
        "1",
        "VHGT",
        "struct (5 bytes)",
        "Height data\n\nfloat32 - Offset\nint8 - Data\njust words\nnote - prose",
    ],
    ["1", "NPDT", "struct (12 or 52 bytes)", "NPC data\n12-byte version\n52-byte version"],
    ["Morrowind Mod:Mod File Format/EMPTY"],
    ["C", "Field", "Type/Size", "Info"],
    ["*", "Skip", "uint32", "not a tag, so this section ends up empty"],
    # A section whose next rows are blank: the description scan finds none.
    ["Morrowind Mod:Mod File Format/QUICK"],
    [],
    [],
    [],
    ["C", "Field", "Type/Size", "Info"],
    ["*", "QUIK", "uint32", "a field"],
]


def _write_csv(tmp_path: Path, rows: list[list[str]]) -> Path:
    path = tmp_path / "export.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return path


class TestSmallHelpers:
    def test_clean_normalises_wiki_whitespace_and_dashes(self) -> None:
        # NBSP, en dash and times sign, written as escapes to keep the source ASCII.
        assert _clean("a\xa0b\u2013c\u00d7d ") == "a b-cxd"

    def test_parse_type_splits_base_and_extents(self) -> None:
        assert parse_type("uint16[16][16]") == ("uint16", (16, 16))

    def test_parse_type_leaves_an_unparseable_type_whole(self) -> None:
        assert parse_type("weird type") == ("weird type", ())

    def test_member_size_multiplies_by_the_extents(self) -> None:
        assert member_size("uint16", (16, 16)) == 512

    def test_member_size_of_an_unknown_type_is_zero(self) -> None:
        assert member_size("zstring", ()) == 0

    def test_declared_bytes_reads_a_plain_count(self) -> None:
        assert _declared_bytes("12,675 bytes") == 12675

    def test_declared_bytes_refuses_a_hedged_size(self) -> None:
        assert _declared_bytes("12 or 52 bytes") == 0

    def test_quote_escapes_backslashes_and_quotes(self) -> None:
        assert _quote('a\\b"c') == '"a\\\\b\\"c"'

    def test_description_is_blank_when_the_first_line_is_a_member(self) -> None:
        assert _description("uint32 - Flags") == ""

    def test_description_is_the_first_line_otherwise(self) -> None:
        assert _description("Alchemy data\nuint32 - Value") == "Alchemy data"


class TestParseLayout:
    def test_it_reads_members_after_a_description_line(self) -> None:
        members, variants = parse_layout("Height data\nfloat32 - Offset\nint8 - Data")
        assert members == [("float32", (), "Offset"), ("int8", (), "Data")]
        assert variants == []

    def test_a_member_line_first_is_not_skipped(self) -> None:
        members, _ = parse_layout("uint32 - Flags")  # CELL's DATA opens on a member
        assert members == [("uint32", (), "Flags")]

    def test_prose_that_happens_to_contain_a_dash_is_ignored(self) -> None:
        members, _ = parse_layout("Info\nsee - the wiki")  # 'see' is not a type
        assert members == []

    def test_variant_headings_yield_no_members(self) -> None:
        members, variants = parse_layout("NPC data\n12-byte version\n52-byte version")
        assert members == []
        assert variants == ["12-byte version", "52-byte version"]


class TestParseCsv:
    def test_it_parses_sections_fields_and_descriptions(self, tmp_path: Path) -> None:
        sections = parse_csv(_write_csv(tmp_path, _ROWS))
        assert sections["LAND"]["description"] == "Landscape data"  # UESPWiki row skipped
        names = [f["name"] for f in sections["LAND"]["fields"]]
        assert names == ["NAME", "DATA", "VNML", "VHGT", "NPDT"]  # Jump and the note dropped

    def test_a_per_element_array_size_becomes_a_repeat(self, tmp_path: Path) -> None:
        sections = parse_csv(_write_csv(tmp_path, _ROWS))
        vnml = next(f for f in sections["LAND"]["fields"] if f["name"] == "VNML")
        assert vnml["repeat"] == 4225  # 12675 declared / 3 parsed

    def test_a_variant_field_carries_headings_and_no_members(self, tmp_path: Path) -> None:
        sections = parse_csv(_write_csv(tmp_path, _ROWS))
        npdt = next(f for f in sections["LAND"]["fields"] if f["name"] == "NPDT")
        assert npdt["members"] == []
        assert npdt["variants"] == ["12-byte version", "52-byte version"]

    def test_an_export_with_no_sections_is_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="no sections found"):
            parse_csv(_write_csv(tmp_path, [["just"], ["some"], ["junk"]]))


class TestEmit:
    def test_it_renders_records_and_skips_empty_sections(self, tmp_path: Path) -> None:
        sections = parse_csv(_write_csv(tmp_path, _ROWS))
        text = emit(sections)
        assert "GENERATED FILE" in text
        assert '"LAND": Record(' in text
        assert "Member(" in text
        assert "repeat=4225," in text
        assert 'variants=("12-byte version"' in text
        assert '"EMPTY": Record(' not in text  # the field-less section is dropped


class TestMain:
    def test_wrong_argument_count_prints_usage(self, capsys) -> None:
        assert main(["gen_tes3_schema.py"]) == 2
        assert capsys.readouterr().out  # docstring printed

    def test_it_writes_the_schema_module(self, tmp_path: Path, capsys) -> None:
        src = _write_csv(tmp_path, _ROWS)
        original = gen_tes3_schema.OUT.read_bytes()  # raw bytes: never reflow CRLF
        try:
            assert main(["gen_tes3_schema.py", str(src)]) == 0
            written = gen_tes3_schema.OUT.read_text(encoding="utf-8")
        finally:
            gen_tes3_schema.OUT.write_bytes(original)
        assert '"LAND": Record(' in written
        assert "wrote" in capsys.readouterr().out
