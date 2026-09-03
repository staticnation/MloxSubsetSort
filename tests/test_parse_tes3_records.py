"""parse_tes3_records / parse_omwscripts / parse_plugin_records: turning a
plugin or .omwscripts file into (record_type, record_id, deleted) triples.

This is what conflict/diff detection is built on, so getting each record
type's id derivation right matters: CELL keys on grid coords for exteriors
but on name for interiors (and the interior/exterior split itself comes from
a flag bit, not the record type), SCPT keys on its 32-byte header name
rather than its NAME field (it doesn't have one), and LAND keys on INTV grid
coords. LUAL is its own record type entirely (an OpenMW Lua-script-attach
list), not a normal id-bearing record, and its declarations have to line up
with what parse_omwscripts sees for the same script -- both normalize
backslashes, lowercase, and a leading slash the same way, which is worth
pinning in one place before it silently drifts between them. None of this
had coverage before this file.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from conftest import interior_cell, rec, script_record, static_record, sub, write_plugin, zstr

import wraithguard_toolkit as core

if TYPE_CHECKING:
    from pathlib import Path


def _exterior_cell(gx: int, gy: int, name: str = "") -> bytes:
    return rec("CELL", sub("NAME", zstr(name)) + sub("DATA", struct.pack("<iii", 0, gx, gy)))


def _land(gx: int, gy: int) -> bytes:
    return rec("LAND", sub("INTV", struct.pack("<ii", gx, gy)))


def _lual(*paths: str) -> bytes:
    return rec("LUAL", b"".join(sub("LUAS", zstr(p)) for p in paths))


def _info(response_id: str) -> bytes:
    return rec("INFO", sub("INAM", zstr(response_id)))


def _bare_cell(name: str) -> bytes:
    """A CELL record with only a NAME -- no DATA subrecord at all."""
    return rec("CELL", sub("NAME", zstr(name)))


class TestParseTes3Records:
    def test_a_simple_record_yields_its_type_id_and_not_deleted(self, tmp_path: Path) -> None:
        path = write_plugin(tmp_path / "Mine.esp", extra=static_record("torch_01"))

        assert list(core.parse_tes3_records(path)) == [("STAT", "torch_01", False)]

    def test_a_dele_subrecord_marks_the_record_deleted(self, tmp_path: Path) -> None:
        body = sub("NAME", zstr("torch_01")) + sub("MODL", zstr("x.nif")) + sub("DELE", b"")
        path = write_plugin(tmp_path / "Mine.esp", extra=rec("STAT", body))

        assert list(core.parse_tes3_records(path)) == [("STAT", "torch_01", True)]

    def test_an_exterior_cell_is_keyed_on_grid_coordinates(self, tmp_path: Path) -> None:
        path = write_plugin(tmp_path / "Mine.esp", extra=_exterior_cell(3, -2))

        assert list(core.parse_tes3_records(path)) == [("CELL", "Exterior (3, -2)", False)]

    def test_a_named_exterior_cell_includes_its_name_bracketed(self, tmp_path: Path) -> None:
        path = write_plugin(tmp_path / "Mine.esp", extra=_exterior_cell(1, 1, "Seyda Neen Docks"))

        _rectype, rid, _deleted = next(core.parse_tes3_records(path))
        assert rid == "Exterior (1, 1) [Seyda Neen Docks]"

    def test_an_interior_cell_is_keyed_on_its_name(self, tmp_path: Path) -> None:
        path = write_plugin(tmp_path / "Mine.esp", extra=interior_cell("Balmora, Guild", fog=0.5))

        assert list(core.parse_tes3_records(path)) == [("CELL", "Interior: Balmora, Guild", False)]

    def test_a_cell_with_no_data_subrecord_falls_back_to_treating_it_as_interior(
        self, tmp_path: Path
    ) -> None:
        """A CELL with no DATA at all (no flags to read) defaults to the interior phrasing."""
        path = write_plugin(tmp_path / "Mine.esp", extra=_bare_cell("Orphaned Cell"))

        assert list(core.parse_tes3_records(path)) == [("CELL", "Interior: Orphaned Cell", False)]

    def test_an_unexpected_nested_tes3_record_is_skipped_not_yielded(self, tmp_path: Path) -> None:
        """_TES3_SKIP_TYPES exists for exactly this: a stray TES3-tagged record mid-body."""
        stray = rec("TES3", b"unexpected")
        path = write_plugin(tmp_path / "Mine.esp", extra=stray + static_record("torch_01"))

        assert list(core.parse_tes3_records(path)) == [("STAT", "torch_01", False)]

    def test_a_script_is_keyed_on_its_header_name_not_a_name_subrecord(
        self, tmp_path: Path
    ) -> None:
        path = write_plugin(
            tmp_path / "Mine.esp", extra=script_record("MyQuestScript", "; do nothing")
        )

        assert list(core.parse_tes3_records(path)) == [("SCPT", "MyQuestScript", False)]

    def test_a_land_record_is_keyed_on_its_grid_coordinates(self, tmp_path: Path) -> None:
        path = write_plugin(tmp_path / "Mine.esp", extra=_land(5, -7))

        assert list(core.parse_tes3_records(path)) == [("LAND", "Land (5, -7)", False)]

    def test_a_dialogue_response_is_keyed_on_inam_when_there_is_no_name(
        self, tmp_path: Path
    ) -> None:
        path = write_plugin(tmp_path / "Mine.esp", extra=_info("response-id-123"))

        assert list(core.parse_tes3_records(path)) == [("INFO", "response-id-123", False)]

    def test_a_lual_record_yields_one_luascript_entry_per_luas_subrecord(
        self, tmp_path: Path
    ) -> None:
        path = write_plugin(
            tmp_path / "Mine.omwaddon", extra=_lual("Scripts/Foo.lua", "Scripts/Bar.lua")
        )

        assert list(core.parse_tes3_records(path)) == [
            ("LuaScript", "scripts/foo.lua", False),
            ("LuaScript", "scripts/bar.lua", False),
        ]

    def test_lual_paths_are_normalized_backslashes_case_and_leading_slash(
        self, tmp_path: Path
    ) -> None:
        path = write_plugin(tmp_path / "Mine.omwaddon", extra=_lual("\\Scripts\\FOO.LUA"))

        assert list(core.parse_tes3_records(path)) == [("LuaScript", "scripts/foo.lua", False)]

    def test_a_non_luas_subrecord_inside_lual_is_skipped(self, tmp_path: Path) -> None:
        """LUAL can carry other subrecord tags; only LUAS entries are script paths."""
        body = sub("XTRA", b"\x00\x00") + sub("LUAS", zstr("Scripts/Foo.lua"))
        path = write_plugin(tmp_path / "Mine.omwaddon", extra=rec("LUAL", body))

        assert list(core.parse_tes3_records(path)) == [("LuaScript", "scripts/foo.lua", False)]

    def test_an_empty_luas_path_yields_nothing_for_that_entry(self, tmp_path: Path) -> None:
        """A blank/NUL-only LUAS subrecord has no path worth yielding."""
        body = sub("LUAS", zstr("")) + sub("LUAS", zstr("Scripts/Foo.lua"))
        path = write_plugin(tmp_path / "Mine.omwaddon", extra=rec("LUAL", body))

        assert list(core.parse_tes3_records(path)) == [("LuaScript", "scripts/foo.lua", False)]

    def test_a_record_with_no_derivable_id_yields_nothing(self, tmp_path: Path) -> None:
        # An unrecognized record type with a subrecord this parser doesn't
        # key on at all -- there is simply nothing to report it as.
        path = write_plugin(tmp_path / "Mine.esp", extra=rec("XYZZ", sub("ZZZZ", b"\x01")))

        assert list(core.parse_tes3_records(path)) == []

    def test_multiple_records_are_all_yielded_in_file_order(self, tmp_path: Path) -> None:
        path = write_plugin(
            tmp_path / "Mine.esp",
            extra=static_record("First") + static_record("Second"),
        )

        assert [rid for _t, rid, _d in core.parse_tes3_records(path)] == ["First", "Second"]

    def test_a_non_tes3_file_yields_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "not-a-plugin.esp"
        path.write_bytes(b"this is not a TES3 file")

        assert list(core.parse_tes3_records(path)) == []

    def test_a_file_too_short_for_even_one_header_yields_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "truncated.esp"
        path.write_bytes(b"TES3\x00\x00")

        assert list(core.parse_tes3_records(path)) == []

    def test_a_record_truncated_mid_body_stops_without_raising(self, tmp_path: Path) -> None:
        good = static_record("First")
        # A second record whose header claims more body than actually follows.
        truncated_header = struct.pack("<4sIII", b"STAT", 999, 0, 0) + b"short"
        path = write_plugin(tmp_path / "Mine.esp", extra=good + truncated_header)

        assert list(core.parse_tes3_records(path)) == [("STAT", "First", False)]

    def test_a_missing_file_yields_nothing_rather_than_raising(self, tmp_path: Path) -> None:
        assert list(core.parse_tes3_records(tmp_path / "does-not-exist.esp")) == []


class TestParseOmwscripts:
    def test_a_normal_declaration_line_yields_a_luascript_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "my.omwscripts"
        path.write_text("PLAYER: scripts/foo.lua\n", encoding="utf-8")

        assert list(core.parse_omwscripts(path)) == [("LuaScript", "scripts/foo.lua", False)]

    def test_comments_and_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "my.omwscripts"
        path.write_text(
            "# a comment\n\nPLAYER: scripts/foo.lua\n# another comment\n",
            encoding="utf-8",
        )

        assert list(core.parse_omwscripts(path)) == [("LuaScript", "scripts/foo.lua", False)]

    def test_a_line_with_no_colon_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "my.omwscripts"
        path.write_text("this line has no colon at all\n", encoding="utf-8")

        assert list(core.parse_omwscripts(path)) == []

    def test_a_non_lua_target_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "my.omwscripts"
        path.write_text("PLAYER: readme.txt\n", encoding="utf-8")

        assert list(core.parse_omwscripts(path)) == []

    def test_quoted_paths_have_their_quotes_stripped(self, tmp_path: Path) -> None:
        path = tmp_path / "my.omwscripts"
        path.write_text('PLAYER: "scripts/foo.lua"\n', encoding="utf-8")

        assert list(core.parse_omwscripts(path)) == [("LuaScript", "scripts/foo.lua", False)]

    def test_backslashes_and_case_are_normalized_the_same_way_as_lual(self, tmp_path: Path) -> None:
        path = tmp_path / "my.omwscripts"
        path.write_text("PLAYER: \\Scripts\\FOO.LUA\n", encoding="utf-8")

        assert list(core.parse_omwscripts(path)) == [("LuaScript", "scripts/foo.lua", False)]

    def test_a_missing_file_yields_nothing_rather_than_raising(self, tmp_path: Path) -> None:
        assert list(core.parse_omwscripts(tmp_path / "does-not-exist.omwscripts")) == []


class TestParsePluginRecordsDispatch:
    def test_an_omwscripts_extension_dispatches_to_the_text_parser(self, tmp_path: Path) -> None:
        path = tmp_path / "my.omwscripts"
        path.write_text("PLAYER: scripts/foo.lua\n", encoding="utf-8")

        assert list(core.parse_plugin_records(path)) == [("LuaScript", "scripts/foo.lua", False)]

    def test_an_esp_extension_dispatches_to_the_binary_parser(self, tmp_path: Path) -> None:
        path = write_plugin(tmp_path / "Mine.esp", extra=static_record("torch_01"))

        assert list(core.parse_plugin_records(path)) == [("STAT", "torch_01", False)]

    def test_the_omwscripts_extension_match_is_case_insensitive(self, tmp_path: Path) -> None:
        path = tmp_path / "MY.OMWSCRIPTS"
        path.write_text("PLAYER: scripts/foo.lua\n", encoding="utf-8")

        assert list(core.parse_plugin_records(path)) == [("LuaScript", "scripts/foo.lua", False)]
