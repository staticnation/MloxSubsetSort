"""Tests for ``tools/gen_opcodes.py``, the script-opcode table generator.

The disassembler needs every function's opcode and operand shape. This reads
MWEdit's ``Functions.dat`` (names + flag words) and, where they add opcodes the
base table lacks, MWSE's ``customfunctions.dat`` (symbolic operand types), keeps
existing entries on any disagreement, applies a tiny hand-checked correction set,
and folds in the compiler-internal opcodes no table lists. The two dialect
parsers, the merge, and the literal emitter are pure; ``main`` is run against
hand-written fake tables with the real generated module saved and restored.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import gen_opcodes
from tools.gen_opcodes import (
    CORPUS_DERIVED,
    CORRECTIONS,
    _frozenset_literal,
    main,
    merge_custom,
    parse_custom_functions,
    parse_functions_dat,
)

_FUNCTIONS_DAT = """\
Function = Activate
    Options = 0x8
    Opcode = 0x1017
    Param1 = 0x820, "player"
End
Function = GetDistance
    Opcode = 0x1000
    Param2 = 0x4, "x"
    Param1 = 0x20, "id"
End
Function = Broken
    Opcode = nothex
End
"""

_CUSTOM_DAT = """\
# a leading comment
function
    Name = XAddItem
    Options = MWSE | AllowGlobal
    Param1 = Long | String
    Opcode = 0x3c28
end
function
    Name = XBadOpcode
    Opcode = zzz
end
function
    Name = ActivateRenamed
    Param1 = Long
    Opcode = 0x1017
end
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestParseFunctionsDat:
    def test_a_complete_block_is_parsed(self, tmp_path: Path) -> None:
        table = parse_functions_dat(_write(tmp_path, "Functions.dat", _FUNCTIONS_DAT))
        assert table[0x1017] == ("Activate", (0x820,))

    def test_params_are_ordered_by_number_not_file_order(self, tmp_path: Path) -> None:
        table = parse_functions_dat(_write(tmp_path, "Functions.dat", _FUNCTIONS_DAT))
        assert table[0x1000] == ("GetDistance", (0x20, 0x4))  # Param1 then Param2

    def test_a_block_with_a_malformed_opcode_is_dropped(self, tmp_path: Path) -> None:
        table = parse_functions_dat(_write(tmp_path, "Functions.dat", _FUNCTIONS_DAT))
        assert all(name != "Broken" for name, _p in table.values())

    def test_a_block_without_an_end_is_flushed_at_eof(self, tmp_path: Path) -> None:
        text = "Function = Last\n    Opcode = 0x2000\n"  # no End line
        table = parse_functions_dat(_write(tmp_path, "F.dat", text))
        assert table[0x2000] == ("Last", ())


class TestParseCustomFunctions:
    def test_symbolic_params_are_translated_to_flag_bits(self, tmp_path: Path) -> None:
        table = parse_custom_functions(_write(tmp_path, "custom.dat", _CUSTOM_DAT))
        assert table[0x3C28] == ("XAddItem", (0x4 | 0x10,))  # Long | String

    def test_a_block_with_a_bad_opcode_is_dropped(self, tmp_path: Path) -> None:
        table = parse_custom_functions(_write(tmp_path, "custom.dat", _CUSTOM_DAT))
        assert all(name != "XBadOpcode" for name, _p in table.values())

    def test_an_unrecognised_param_type_contributes_no_bits(self, tmp_path: Path) -> None:
        text = "function\n    Name = XWeird\n    Param1 = Nonsense\n    Opcode = 0x9000\nend\n"
        table = parse_custom_functions(_write(tmp_path, "c.dat", text))
        assert table[0x9000] == ("XWeird", (0,))

    def test_a_key_with_an_empty_value_is_ignored(self, tmp_path: Path) -> None:
        """A ``Key =`` line carrying no value is skipped, not treated as data."""
        text = "function\n    Name = XThing\n    Options =\n    Opcode = 0x9100\nend\n"
        table = parse_custom_functions(_write(tmp_path, "c.dat", text))
        assert table[0x9100] == ("XThing", ())


class TestMergeCustom:
    def test_a_new_opcode_is_added(self) -> None:
        table = {0x1: ("A", ())}
        added, notes = merge_custom(table, {0x2: ("B", (0x4,))})
        assert added == {0x2}
        assert table[0x2] == ("B", (0x4,))
        assert notes == []

    def test_an_existing_opcode_wins_a_name_disagreement(self) -> None:
        table = {0x1: ("Activate", (0x820,))}
        added, notes = merge_custom(table, {0x1: ("ActivateX", (0x820,))})
        assert added == set()
        assert table[0x1] == ("Activate", (0x820,))
        assert notes and "name Activate kept" in notes[0]

    def test_an_existing_opcode_wins_an_operand_disagreement(self) -> None:
        table = {0x1: ("Activate", (0x820,))}
        _added, notes = merge_custom(table, {0x1: ("Activate", (0x4,))})
        assert notes and "operands" in notes[0]

    def test_an_identical_custom_entry_is_neither_added_nor_noted(self) -> None:
        """When the custom table agrees exactly, there is nothing to add or report."""
        table = {0x1: ("Activate", (0x820,))}
        added, notes = merge_custom(table, {0x1: ("Activate", (0x820,))})
        assert added == set()
        assert notes == []


class TestFrozensetLiteral:
    def test_an_empty_set_reads_as_frozenset_not_a_dict(self) -> None:
        assert _frozenset_literal("INTERNAL", set()) == [
            "INTERNAL: Final[frozenset[int]] = frozenset()"
        ]

    def test_a_populated_set_lists_its_members_sorted(self) -> None:
        lines = _frozenset_literal("EXTENDED", {0x20, 0x10})
        joined = "\n".join(lines)
        assert "0x0010, 0x0020" in joined


class TestMain:
    def _protect_out(self) -> bytes:
        # Raw bytes: the tool writes LF, but the checked-in file may be CRLF,
        # and a text-mode restore would silently reflow it and dirty the tree.
        return gen_opcodes.OUT.read_bytes()

    def test_wrong_argument_count_prints_usage(self, capsys) -> None:
        assert main(["gen_opcodes.py"]) == 2  # no input file
        assert capsys.readouterr().out  # the docstring was printed

    def test_an_empty_functions_table_fails(self, tmp_path: Path, capsys) -> None:
        empty = _write(tmp_path, "Functions.dat", "nothing useful here\n")
        assert main(["gen_opcodes.py", str(empty)]) == 1
        assert "no functions parsed" in capsys.readouterr().err

    def test_a_functions_only_run_writes_the_module(self, tmp_path: Path, capsys) -> None:
        src = _write(tmp_path, "Functions.dat", _FUNCTIONS_DAT)
        original = self._protect_out()
        try:
            assert main(["gen_opcodes.py", str(src)]) == 0
            written = gen_opcodes.OUT.read_text(encoding="utf-8")
        finally:
            gen_opcodes.OUT.write_bytes(original)
        assert "FUNCTIONS" in written
        assert '"Activate"' in written
        for opcode in CORPUS_DERIVED:  # the internal opcodes were folded in
            assert f"0x{opcode:04X}" in written
        assert "wrote" in capsys.readouterr().out

    def test_a_custom_table_with_no_valid_blocks_fails(self, tmp_path: Path, capsys) -> None:
        src = _write(tmp_path, "Functions.dat", _FUNCTIONS_DAT)
        bad_custom = _write(tmp_path, "custom.dat", "# only a comment\n")
        original = self._protect_out()
        try:
            assert main(["gen_opcodes.py", str(src), str(bad_custom)]) == 1
        finally:
            gen_opcodes.OUT.write_bytes(original)
        assert "no functions parsed from the custom table" in capsys.readouterr().err

    def test_a_full_run_merges_custom_and_applies_corrections(
        self, tmp_path: Path, capsys
    ) -> None:
        # Include a 0x3C33 block that disagrees with CORRECTIONS so the
        # correction note fires, and a custom table that adds an opcode.
        funcs = _FUNCTIONS_DAT + 'Function = XFileWriteFloat\n    Opcode = 0x3C33\n    Param1 = 0x8, "v"\nEnd\n'
        src = _write(tmp_path, "Functions.dat", funcs)
        custom = _write(tmp_path, "custom.dat", _CUSTOM_DAT)
        original = self._protect_out()
        try:
            assert main(["gen_opcodes.py", str(src), str(custom)]) == 0
            written = gen_opcodes.OUT.read_text(encoding="utf-8")
        finally:
            gen_opcodes.OUT.write_bytes(original)
        err = capsys.readouterr().err
        assert "corrected: 0x3C33" in err  # the correction was applied and reported
        assert '0x3C28: ("XAddItem"' in written  # the custom opcode was merged in
        # CORRECTIONS won: the shipped entry, not the fake one, is in the output.
        corrected_name, _params = CORRECTIONS[0x3C33]
        assert f'0x3C33: ("{corrected_name}"' in written
