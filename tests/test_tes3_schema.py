"""Tests for the TES3 format schema and the annotations built on it.

The schema is generated out of prose tables written by people, so the important
question is not "does it load" but "is what it says *true*". Two checks answer
that without any hand-maintained expected values:

* every struct layout that could be parsed must add up to the byte count the
  same table declares for it -- 56 independent agreements, each one a chance for
  the parser to have gone wrong and not taken;
* every record type the diff window can encounter must resolve to a documented
  record, or be a type the reference is known not to cover.

The generator itself is tested against small synthetic tables, so its edge cases
(notes between the header and the fields, the ``uint32 = Flags`` typo, arrays
whose element layout is documented once) are pinned by construction rather than
by the current contents of the export.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from wraithguard.tes3fields.annotate import field_note, layout_text, tag_for_key
from wraithguard.tes3fields.naming import TYPE_TO_TAG, record_for, subrecord_for
from wraithguard.tes3fields.schema import RECORDS
from wraithguard.tes3fields.schema_types import Member, Record, Subrecord

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from gen_tes3_schema import (
    _declared_bytes,
    _description,
    _quote,
    emit,
    member_size,
    parse_csv,
    parse_layout,
    parse_type,
)

#: ``12 bytes``, ``12,675 bytes`` -- a size the reference states plainly, as
#: opposed to a hedge like ``12 or 52 bytes``.
PLAIN_SIZE = re.compile(r"^([\d,]+) bytes?$")

#: Record types the export does not cover. Empty since ``BODY`` was added to it;
#: kept as a named set rather than an inline ``== set()`` so that a *new* gap
#: fails the test with something to read instead of joining the schema unnoticed.
KNOWN_GAPS: set[str] = set()


class TestSchemaIsSane:
    """Structural facts that hold across the whole generated table."""

    def test_records_were_generated(self) -> None:
        """An empty schema would make every consumer silently do nothing."""
        assert len(RECORDS) >= 40

    def test_every_record_has_fields(self) -> None:
        """A record with no subrecords is a section the parser lost."""
        assert all(record.fields for record in RECORDS.values())

    def test_tags_are_four_characters(self) -> None:
        """Subrecord tags are fixed-width in the file format."""
        for record in RECORDS.values():
            for sub in record.fields:
                assert 1 <= len(sub.name) <= 4, f"{record.name}.{sub.name}"

    def test_cardinality_is_a_known_marker(self) -> None:
        """Anything else means a column shifted during parsing."""
        for record in RECORDS.values():
            for sub in record.fields:
                assert sub.cardinality in {"+", "-", "*", ""}

    def test_the_records_the_tool_diffs_are_all_present(self) -> None:
        """These four are what the conflict scanner actually surfaces."""
        for tag in ("LAND", "PGRD", "CELL", "LTEX"):
            assert tag in RECORDS

    def test_shared_subpages_are_kept(self) -> None:
        """AI packages are documented once and included by two record types."""
        assert "AI Package Fields" in RECORDS


class TestLayoutsAddUp:
    """The strongest available check that the parse is right."""

    def test_every_parsed_layout_matches_its_declared_size(self) -> None:
        """A layout that does not add up is a layout that was mis-read.

        This caught four real defects: a note row that ended a table early, a
        first member eaten as a description, ``float`` not being recognised as
        ``float32``, and a ``=`` where the table meant ``-``.
        """
        checked = 0
        for record in RECORDS.values():
            for sub in record.fields:
                match = PLAIN_SIZE.match(sub.size)
                if not (sub.members and sub.element_size and match):
                    continue
                declared = int(match.group(1).replace(",", ""))
                assert sub.fixed_size == declared, f"{record.name}.{sub.name}"
                checked += 1
        assert checked >= 50, f"only {checked} layouts checked -- did parsing regress?"

    def test_repeating_layouts_are_marked_not_flattened(self) -> None:
        """LAND's normals are one 3-byte element, 4,225 times."""
        vnml = RECORDS["LAND"].by_name["VNML"]
        assert vnml.element_size == 3
        assert vnml.repeat == 4225
        assert vnml.fixed_size == 12675

    def test_variant_layouts_carry_no_members(self) -> None:
        """NPDT is 12 *or* 52 bytes; running the two together mis-reads NPCs."""
        npdt = RECORDS["NPC"].by_name["NPDT"]
        assert npdt.variants
        assert npdt.members == ()
        assert npdt.fixed_size == 0

    def test_a_known_struct_decodes_to_its_documented_members(self) -> None:
        """One spot check with values that can be read off the wiki page."""
        aldt = RECORDS["ALCH"].by_name["ALDT"]
        assert [m.name for m in aldt.members] == ["Weight", "Value", "Flags"]
        assert [m.type for m in aldt.members] == ["float32", "uint32", "uint32"]
        assert aldt.fixed_size == 12


class TestNaming:
    """Joining tes3conv's vocabulary to the reference's."""

    def test_every_mapped_type_resolves(self) -> None:
        """A mapping to a tag with no record is a mapping that does nothing."""
        unresolved = {t for t, tag in TYPE_TO_TAG.items() if tag not in RECORDS}
        assert unresolved == KNOWN_GAPS

    def test_the_awkward_names_are_mapped(self) -> None:
        """No string rule turns "LandscapeTexture" into "LTEX"."""
        assert TYPE_TO_TAG["LandscapeTexture"] == "LTEX"
        assert TYPE_TO_TAG["Header"] == "TES3"
        assert TYPE_TO_TAG["Npc"] == "NPC"

    def test_raw_tags_resolve_too(self) -> None:
        """Callers hold a tag as often as a type name."""
        assert record_for("LAND") is RECORDS["LAND"]
        assert record_for("Landscape") is RECORDS["LAND"]

    def test_unknown_type_is_none_not_an_error(self) -> None:
        """tes3conv reads types the reference does not document."""
        assert record_for("Nonesuch") is None
        assert subrecord_for("Nonesuch", "XXXX") is None

    def test_shared_subpage_fields_are_found_through_their_record(self) -> None:
        """A creature's AI_W is documented on the AI package page."""
        assert subrecord_for("Creature", "AI_W") is not None
        assert subrecord_for("Npc", "AI_T") is not None

    def test_lookup_is_case_insensitive_on_the_tag(self) -> None:
        """Callers pass whatever case their data had."""
        assert subrecord_for("Landscape", "vhgt") is not None


class TestAnnotation:
    """What the diff window puts on screen."""

    def test_a_known_field_is_described_in_format_terms(self) -> None:
        """The point of the whole exercise."""
        note = field_note("Landscape", "vertex_heights.data")
        assert note is not None
        assert note.startswith("VHGT")
        assert "4,232 bytes" in note

    def test_the_json_sub_key_is_ignored(self) -> None:
        """``.data`` and ``.offset`` are tes3conv's shape, not the format's."""
        assert tag_for_key("Landscape", "vertex_heights.data") == "VHGT"
        assert tag_for_key("Landscape", "vertex_heights") == "VHGT"

    def test_an_unknown_field_is_not_annotated(self) -> None:
        """A confidently wrong label is worse than no label."""
        assert field_note("Landscape", "invented_key") is None
        assert field_note("Nonesuch", "id") is None

    def test_common_keys_work_across_record_types(self) -> None:
        """``id`` is the NAME subrecord wherever it appears."""
        assert tag_for_key("Alchemy", "id") == "NAME"
        assert tag_for_key("Weapon", "id") == "NAME"

    def test_a_record_specific_key_beats_the_common_one(self) -> None:
        """A path grid's ``data`` is its DATA, not something generic."""
        assert tag_for_key("PathGrid", "data") == "DATA"

    def test_layout_text_lists_every_subrecord(self) -> None:
        """The reference view is only useful if it is complete."""
        text = layout_text("Landscape")
        assert text is not None
        for tag in ("INTV", "DATA", "VNML", "VHGT", "WNAM", "VCLR", "VTEX"):
            assert tag in text

    def test_layout_text_shows_struct_members(self) -> None:
        """ "What was it" is answered by the members, not the tag."""
        text = layout_text("PathGrid")
        assert text is not None
        assert "Grid X" in text
        assert "Connection count" in text

    def test_layout_text_flags_variant_layouts(self) -> None:
        """A reader must not take the 12-byte NPDT as the only one."""
        text = layout_text("Npc")
        assert text is not None
        assert "52-byte version" in text
        assert "layout depends on a flag" in text

    def test_layout_text_credits_its_source(self) -> None:
        """The tables are CC-BY-SA; attribution travels with the text."""
        text = layout_text("LAND")
        assert text is not None
        assert "UESP" in text

    def test_layout_text_is_none_for_an_undocumented_type(self) -> None:
        """So the button offering it can be hidden rather than open empty."""
        assert layout_text("Nonesuch") is None


class TestSchemaTypes:
    """The hand-written shapes, including the ones the export never exercises."""

    def test_member_counts_its_extents(self) -> None:
        """A 16x16 grid is 256 values."""
        assert Member("uint16", (16, 16), "Texture indices", 512).count == 256
        assert Member("uint8", (), "Flags", 1).count == 1

    def test_long_member_names_are_truncated_for_display(self) -> None:
        """The reference writes a paragraph where a name belongs."""
        member = Member("int8", (65, 65), "x" * 300, 4225)
        assert len(member.describe()) < 120
        assert member.describe().endswith("(int8[65][65], 4225 bytes)")
        assert len(member.name) == 300  # the full text is still there

    def test_variable_width_member_makes_the_layout_unwalkable(self) -> None:
        """Zero means "cannot tell", and must not be treated as zero bytes."""
        sub = Subrecord("X", members=(Member("uint16", (), "a", 2), Member("zstring", (), "b", 0)))
        assert sub.element_size == 0
        assert sub.fixed_size == 0

    def test_cardinality_helpers(self) -> None:
        """Required and repeatable drive what a reader should expect."""
        assert Subrecord("NAME", "+").required
        assert not Subrecord("FNAM", "-").required
        assert Subrecord("AI_A", "*").repeatable

    def test_describe_without_a_description(self) -> None:
        """Some fields have only a type; the summary must still read."""
        assert Subrecord("NAME", "+", "zstring").describe() == "NAME (zstring, required)"

    def test_duplicate_tags_keep_the_first(self) -> None:
        """CELL documents DATA twice; the cell's own must win over a reference's."""
        record = Record(
            "X",
            fields=(Subrecord("DATA", "+", "struct", "12 bytes"), Subrecord("DATA", "-", "struct")),
        )
        assert record.by_name["DATA"].size == "12 bytes"


class TestGenerator:
    """The parser's edge cases, pinned against synthetic tables."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("uint16", ("uint16", ())),
            ("uint8[3]", ("uint8", (3,))),
            ("uint16[16][16]", ("uint16", (16, 16))),
            ("char[32]", ("char", (32,))),
        ],
    )
    def test_type_parsing(self, text: str, expected: tuple[str, tuple[int, ...]]) -> None:
        """Extents multiply the width, so mis-reading one mis-sizes a struct.

        Args:
            text: The type as written.
            expected: Base type and extents.
        """
        assert parse_type(text) == expected

    def test_unknown_type_has_no_size(self) -> None:
        """``zstring`` is variable; claiming a width for it would be a guess."""
        assert member_size("zstring", ()) == 0

    def test_array_sizes_multiply(self) -> None:
        """65x65 int8 is 4,225 bytes, which is how VHGT is checked."""
        assert member_size("int8", (65, 65)) == 4225

    def test_first_line_is_a_description_unless_it_is_a_member(self) -> None:
        """CELL's DATA opens straight onto ``uint32 - Flags``."""
        assert _description("Alchemy data\nfloat32 - Weight") == "Alchemy data"
        assert _description("uint32 - Flags\nint32 - Grid X") == ""

    def test_a_leading_member_is_not_eaten_as_a_description(self) -> None:
        """The defect that made CELL's DATA come out 4 bytes short."""
        members, variants = parse_layout("uint32 - Flags\nint32 - Grid X\nint32 - Grid Y")
        assert [m[2] for m in members] == ["Flags", "Grid X", "Grid Y"]
        assert variants == []

    def test_bit_value_lines_are_not_members(self) -> None:
        """``0x01 = Interior`` documents a flag value, not a field."""
        members, _variants = parse_layout(
            "Cell data\nuint32 - Flags\n0x01 = Interior\n0x02 = Has Water\nint32 - Grid X"
        )
        assert [m[2] for m in members] == ["Flags", "Grid X"]

    def test_equals_is_accepted_as_a_separator(self) -> None:
        """CLAS has ``uint32 = Flags``, and dropping it cost four bytes."""
        members, _variants = parse_layout("Class data\nuint32 = Flags")
        assert [m[2] for m in members] == ["Flags"]

    def test_float_is_treated_as_float32(self) -> None:
        """CELL's AMBI says ``float``, and the member was being dropped."""
        members, _variants = parse_layout("Ambient light\nrgb - Color\nfloat - Fog density")
        assert [m[0] for m in members] == ["rgb", "float"]
        assert sum(member_size(t, d) for t, d, _n in members) == 8

    def test_variants_suppress_members(self) -> None:
        """Two layouts under one tag cannot be concatenated."""
        members, variants = parse_layout(
            "NPC data\n12-byte version (autocalc flag set)\nuint16 - Level\n"
            "52-byte version (autocalc flag clear)\nuint16 - Level"
        )
        assert members == []
        assert len(variants) == 2

    def test_prose_containing_a_dash_is_not_a_member(self) -> None:
        """The tables are written by people and read like it."""
        members, _variants = parse_layout("Some field\nSee the Skills page - for details")
        assert members == []

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("12 bytes", 12), ("12,675 bytes", 12675), ("12 or 52 bytes", 0), ("", 0)],
    )
    def test_declared_bytes_refuses_to_resolve_a_hedge(self, text: str, expected: int) -> None:
        """A conditional size must not be turned into a number here.

        Args:
            text: The declared size as written.
            expected: The count, or 0 when it is not a plain one.
        """
        assert _declared_bytes(text) == expected

    def test_an_unparseable_type_comes_back_unchanged_with_no_extents(self) -> None:
        """The table is prose written by people; a type regex miss must not raise."""
        assert parse_type("3uint16 weird") == ("3uint16 weird", ())

    def test_a_blank_line_inside_the_info_cell_is_skipped(self) -> None:
        members, _variants = parse_layout("Desc\nuint16 - Level\n\nint32 - Grid X")
        assert [m[2] for m in members] == ["Level", "Grid X"]

    def test_a_matching_line_with_an_unknown_undimensioned_type_is_skipped(self) -> None:
        """zstring/string are variable-width and deliberately outside _WIDTHS."""
        members, _variants = parse_layout("Desc\nzstring - Name\nuint16 - Level")
        assert [m[2] for m in members] == ["Level"]

    def test_quote_escapes_backslashes_and_quotes(self) -> None:
        assert _quote('He said "hi"\\now') == '"He said \\"hi\\"\\\\now"'


def _csv_text(rows: list[list[str]]) -> str:
    import csv
    import io

    buf = io.StringIO()
    # lineterminator="\n": write_text() below does its own platform newline
    # translation (\n -> \r\n on Windows); csv's own default "\r\n" would
    # double up into "\r\r\n" there and desynchronize the row count.
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


class TestParseCsv:
    """The CSV parser, against small synthetic exports shaped like UESP's."""

    def test_a_full_table_round_trips_into_one_field(self, tmp_path: Path) -> None:
        rows = [
            ["Morrowind Mod:Mod File Format/GLOB"],
            ["The UESPWiki - Your source for..."],
            ["Global variables"],
            ["C", "Field", "Type/Size", "Info"],
            ["1", "NAME", "zstring", "The variable's ID"],
            ["1", "FLTV", "float32 (4 bytes)", "float32 - Value"],
        ]
        path = tmp_path / "export.csv"
        path.write_text(_csv_text(rows), encoding="utf-8")

        sections = parse_csv(path)

        assert sections["GLOB"]["description"] == "Global variables"
        fields = sections["GLOB"]["fields"]
        assert [f["name"] for f in fields] == ["NAME", "FLTV"]
        assert fields[1]["members"][0][2] == "Value"

    def test_a_note_row_with_no_name_is_skipped_not_treated_as_the_end(
        self, tmp_path: Path
    ) -> None:
        rows = [
            ["Morrowind Mod:Mod File Format/AIDT"],
            ["AI data"],
            ["C", "Field", "Type/Size", "Info"],
            ["", "", "", "AI Packages - the following fields can appear in any order"],
            ["1", "NAME", "zstring", "The variable's ID"],
        ]
        path = tmp_path / "export.csv"
        path.write_text(_csv_text(rows), encoding="utf-8")

        sections = parse_csv(path)

        assert [f["name"] for f in sections["AIDT"]["fields"]] == ["NAME"]

    def test_a_second_table_with_non_tag_names_is_skipped(self, tmp_path: Path) -> None:
        """A row whose 'name' isn't a short upper-case tag is a different table entirely."""
        rows = [
            ["Morrowind Mod:Mod File Format/MGEF"],
            ["Magic effects"],
            ["C", "Field", "Type/Size", "Info"],
            ["1", "NAME", "zstring", "The variable's ID"],
            ["9", "Jump", "int32", "Not a subrecord row"],
        ]
        path = tmp_path / "export.csv"
        path.write_text(_csv_text(rows), encoding="utf-8")

        sections = parse_csv(path)

        assert [f["name"] for f in sections["MGEF"]["fields"]] == ["NAME"]

    def test_a_declared_array_size_is_reported_as_a_repeat(self, tmp_path: Path) -> None:
        """LAND's VNML: one element's layout, times a repeat inferred from the declared size."""
        rows = [
            ["Morrowind Mod:Mod File Format/LAND"],
            ["Landscape data"],
            ["C", "Field", "Type/Size", "Info"],
            [
                "1",
                "VNML",
                "struct (12 bytes)",
                "Normals\nint8 - X\nint8 - Y\nint8 - Z",
            ],
        ]
        path = tmp_path / "export.csv"
        path.write_text(_csv_text(rows), encoding="utf-8")

        sections = parse_csv(path)

        assert sections["LAND"]["fields"][0]["repeat"] == 4

    def test_a_description_row_shortly_after_the_uespwiki_boilerplate_is_used(
        self, tmp_path: Path
    ) -> None:
        rows = [
            ["Morrowind Mod:Mod File Format/GLOB"],
            ["The UESPWiki - Your source for..."],
            ["Global variables"],
            ["C", "Field", "Type/Size", "Info"],
        ]
        path = tmp_path / "export.csv"
        path.write_text(_csv_text(rows), encoding="utf-8")

        sections = parse_csv(path)

        assert sections["GLOB"]["description"] == "Global variables"

    def test_a_csv_with_no_sections_at_all_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "export.csv"
        path.write_text(_csv_text([["nothing", "useful", "here"]]), encoding="utf-8")

        with pytest.raises(ValueError, match="no sections found"):
            parse_csv(path)

    def test_no_usable_description_within_three_rows_leaves_it_blank(self, tmp_path: Path) -> None:
        """Only the next 3 rows are checked; nothing there means an empty description."""
        rows = [
            ["Morrowind Mod:Mod File Format/GLOB"],
            [""],
            ["The UESPWiki - Your source for..."],
            [""],
            ["C", "Field", "Type/Size", "Info"],
        ]
        path = tmp_path / "export.csv"
        path.write_text(_csv_text(rows), encoding="utf-8")

        sections = parse_csv(path)

        assert sections["GLOB"]["description"] == ""


class TestEmit:
    def test_a_field_with_members_repeat_and_variants_renders_all_three(self) -> None:
        sections = {
            "GLOB": {
                "description": "Global variables",
                "fields": [
                    {
                        "name": "FLTV",
                        "cardinality": "1",
                        "type": "float32",
                        "size": "4 bytes",
                        "description": "Value",
                        "members": [("float32", (), "Value")],
                        "variants": ["12-byte version"],
                        "repeat": 2,
                    },
                    {
                        "name": "NAME",
                        "cardinality": "1",
                        "type": "zstring",
                        "size": "",
                        "description": "ID",
                        "members": [],
                        "variants": [],
                        "repeat": 1,
                    },
                ],
            },
            "EMPTY": {"description": "Nothing parsed", "fields": []},
        }

        source = emit(sections)

        assert "GLOB" in source
        assert "EMPTY" not in source  # sections with no fields are dropped
        assert "Member(" in source
        assert "repeat=2" in source
        assert "variants=" in source


class TestMain:
    def test_wrong_argument_count_prints_usage_and_exits_2(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        import gen_tes3_schema

        assert gen_tes3_schema.main(["prog"]) == 2
        assert gen_tes3_schema.__doc__ in capsys.readouterr().out

    def test_a_real_run_writes_the_module_and_reports_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        import gen_tes3_schema

        rows = [
            ["Morrowind Mod:Mod File Format/GLOB"],
            ["Global variables"],
            ["C", "Field", "Type/Size", "Info"],
            ["1", "FLTV", "float32 (4 bytes)", "float32 - Value"],
        ]
        csv_path = tmp_path / "export.csv"
        csv_path.write_text(_csv_text(rows), encoding="utf-8")
        out_path = tmp_path / "schema_out.py"
        monkeypatch.setattr(gen_tes3_schema, "OUT", out_path)

        rc = gen_tes3_schema.main(["prog", str(csv_path)])

        assert rc == 0
        assert out_path.is_file()
        assert "GLOB" in out_path.read_text(encoding="utf-8")
        assert "wrote 1 records, 1 subrecords" in capsys.readouterr().out
