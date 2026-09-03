"""Tests for ``tools/check_against_tes3.py``, the second-implementation cross-check.

The tool's value is finding block types no corpus file happens to contain -- a
gap invisible to any test written against the corpus. It does that by diffing
this reader's known types against Greatness7's ``tes3`` and, given a corpus list,
separating "missing and really used" from "missing but never seen". The Rust
struct scan, the corpus reader and the exit-code logic are all pure, so a fake
tes3 checkout of a couple of ``.rs`` files drives every branch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.check_against_tes3 import corpus_types, main, ours, tes3_types


def _fake_tes3(root: Path, structs: list[str]) -> Path:
    """Build a minimal tes3 checkout whose type files declare ``structs``."""
    types_dir = root / "libs" / "nif" / "src" / "types"
    types_dir.mkdir(parents=True)
    body = "\n\n".join(f"pub struct {name} {{\n    pub flags: u16,\n}}" for name in structs)
    (types_dir / "blocks.rs").write_text(body + "\n", encoding="utf-8")
    return root


class TestTes3Types:
    def test_it_reads_struct_declarations(self, tmp_path: Path) -> None:
        root = _fake_tes3(tmp_path, ["NiNode", "NiTriShape"])
        assert tes3_types(root) == {"NiNode", "NiTriShape"}

    def test_a_path_that_is_not_a_checkout_is_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="not a tes3 checkout"):
            tes3_types(tmp_path)


class TestOurs:
    def test_it_reports_the_readers_known_types(self) -> None:
        types = ours()
        assert isinstance(types, set)
        assert "NiNode" in types  # a type any NIF reader must know


class TestCorpusTypes:
    def test_names_are_read_one_per_line(self, tmp_path: Path) -> None:
        path = tmp_path / "corpus.txt"
        path.write_text("NiNode\nNiTriShape 4210\n# a comment\n\n", encoding="utf-8")
        assert corpus_types(path) == {"NiNode", "NiTriShape"}

    def test_an_absent_file_is_an_empty_set(self, tmp_path: Path) -> None:
        assert corpus_types(tmp_path / "gone.txt") == set()


class TestMain:
    def test_without_a_corpus_it_lists_the_difference_and_exits_zero(
        self, tmp_path: Path, capsys
    ) -> None:
        """No corpus means every difference is scope, not a proven gap: exit 0."""
        root = _fake_tes3(tmp_path, ["NiNode", "NiPhantomBlock"])
        assert main([str(root)]) == 0
        out = capsys.readouterr().out
        assert "NiPhantomBlock" in out  # a type tes3 has that we lack
        assert "Pass --corpus-types" in out

    def test_a_missing_type_the_corpus_uses_is_a_real_gap_and_fails(
        self, tmp_path: Path, capsys
    ) -> None:
        """A type we lack that a real file contains is the one unambiguous failure."""
        root = _fake_tes3(tmp_path, ["NiNode", "NiPhantomBlock"])
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("NiNode\nNiPhantomBlock\n", encoding="utf-8")
        assert main([str(root), "--corpus-types", str(corpus)]) == 1
        out = capsys.readouterr().out
        assert "real gaps" in out
        assert "NiPhantomBlock" in out

    def test_a_missing_type_absent_from_the_corpus_is_scope_not_a_gap(
        self, tmp_path: Path, capsys
    ) -> None:
        """A type we lack that no corpus file uses is scope: reported, exit 0."""
        root = _fake_tes3(tmp_path, ["NiNode", "NiPhantomBlock"])
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("NiNode\n", encoding="utf-8")  # phantom not present
        assert main([str(root), "--corpus-types", str(corpus)]) == 0
        out = capsys.readouterr().out
        assert "scope, not gaps" in out
        assert "none" in out  # no confirmed gaps

    def test_types_only_this_reader_names_are_reported_as_extra(
        self, tmp_path: Path, capsys
    ) -> None:
        """A type in this reader but not tes3 is surfaced as a possible misreading."""
        root = _fake_tes3(tmp_path, ["NiNode"])  # tes3 knows almost nothing here
        assert main([str(root)]) == 0
        out = capsys.readouterr().out
        assert "type(s) tes3 does not" in out  # our large table dwarfs the fake checkout

    def test_no_extra_section_when_tes3_knows_every_type_we_do(
        self, tmp_path: Path, capsys
    ) -> None:
        """If tes3 declares every type this reader has, the 'extra' section is skipped."""
        root = _fake_tes3(tmp_path, sorted(ours()))  # a superset of our types
        assert main([str(root)]) == 0
        out = capsys.readouterr().out
        assert "type(s) tes3 does not" not in out  # nothing is ours-only

    def test_a_corpus_with_no_confirmed_gaps_says_none(self, tmp_path: Path, capsys) -> None:
        """When tes3 has no types we lack, the confirmed-gap list prints 'none'."""
        root = _fake_tes3(tmp_path, ["NiNode"])  # a type we already have
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("NiNode\n", encoding="utf-8")
        assert main([str(root), "--corpus-types", str(corpus)]) == 0
        assert "real gaps:" in capsys.readouterr().out
