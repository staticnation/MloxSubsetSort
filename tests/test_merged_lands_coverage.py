"""The port's coverage claim has to be checkable, not asserted.

The *Function-by-function coverage* section of ``MERGED_LANDS.md`` says every
function in Merged Lands is accounted for. The first version of that document
said so while grouping
related functions onto one table row and reporting the *row* count as the
function count -- ``land/`` was labelled 37 functions when it has 64, and forty
functions were covered only by a heading that never named them.

So the claim is checked here rather than believed. When a copy of the Rust
source is present the whole map is verified against it; when it is not, the
document and the coverage map are still checked against each other, because
those two can rot without the source being anywhere nearby.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    from types import ModuleType

#: The repository root.
ROOT: Final = Path(__file__).resolve().parent.parent

#: The generator, which owns the coverage map.
GENERATOR: Final = ROOT / "tools" / "gen_merged_lands_table.py"

#: The document it produces -- the *Function-by-function coverage* section of
#: the consolidated ``MERGED_LANDS.md``.
DOC: Final = ROOT / "MERGED_LANDS.md"

#: Where a copy of the Rust source may be, relative to the repository root.
#: Absent on a clean checkout, which is why those tests skip rather than fail.
SOURCES: Final[tuple[Path, ...]] = (
    ROOT.parent / "merged_lands-main" / "src",
    ROOT / "merged_lands-main" / "src",
)

#: A generated table row: any first cell, then the source file it came from.
#: Matching on the file column rather than on pipe count keeps a legitimately
#: escaped pipe inside a cell from being read as a row boundary.
_ROW: Final = re.compile(r"^\| .+? \| `[A-Za-z0-9_/.]+\.rs` \| ")

#: Pipes that actually separate cells -- that is, not preceded by a backslash.
_UNESCAPED_PIPE: Final = re.compile(r"(?<!\\)\|")

#: How many functions Merged Lands has. Pinned so that a scan quietly finding
#: fewer -- a broken regex, a half-copied tree -- fails instead of passing.
EXPECTED_FUNCTIONS: Final = 191


def _generator() -> ModuleType:
    """Import the generator as a module.

    Returns:
        The imported module.

    Raises:
        AssertionError: If it cannot be imported.
    """
    spec = importlib.util.spec_from_file_location("gen_merged_lands_table", GENERATOR)
    assert spec is not None and spec.loader is not None, f"cannot load {GENERATOR}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _source() -> Path | None:
    """Find a copy of the Rust source, if one is present.

    Returns:
        The ``src`` directory, or ``None``.
    """
    return next((path for path in SOURCES if path.is_dir()), None)


class TestTheCoverageMapIsComplete:
    """Every function in the source has an entry, and every entry a function."""

    def test_the_generator_exists(self) -> None:
        """The document is generated; the generator is part of the claim."""
        assert GENERATOR.is_file()

    def test_no_function_is_uncovered(self) -> None:
        """A function with no entry is the failure this whole file exists for."""
        source = _source()
        if source is None:
            pytest.skip("no copy of merged_lands-main/src alongside the repository")
        module = _generator()
        problems = module.check(module.scan(source))
        assert not problems, "\n".join(problems)

    def test_the_function_count_is_what_we_claim(self) -> None:
        """191, pinned -- a scan that finds fewer has broken, not improved."""
        source = _source()
        if source is None:
            pytest.skip("no copy of merged_lands-main/src alongside the repository")
        module = _generator()
        assert len(module.scan(source)) == EXPECTED_FUNCTIONS

    def test_the_map_itself_has_the_right_size(self) -> None:
        """Checkable without the source: the map must have one entry per fn."""
        module = _generator()
        assert len(module.COVERAGE) == EXPECTED_FUNCTIONS

    def test_every_entry_has_a_known_status(self) -> None:
        """A typo in a status would silently weaken the claim it makes."""
        module = _generator()
        unknown = {
            key: status
            for key, (status, _where) in module.COVERAGE.items()
            if status not in module.STATUS
        }
        assert not unknown, unknown

    def test_every_entry_says_where(self) -> None:
        """ "Ported" with no destination is not an account of anything."""
        module = _generator()
        empty = [key for key, (_status, where) in module.COVERAGE.items() if not where.strip()]
        assert not empty, empty


class TestTheDocumentMatchesTheMap:
    """The published table is the map, not a stale copy of it."""

    def test_the_document_exists(self) -> None:
        """It is the thing the map is for."""
        assert DOC.is_file()

    def test_every_section_count_is_a_function_count(self) -> None:
        """The original defect: headings that counted rows, not functions."""
        module = _generator()
        text = DOC.read_text(encoding="utf-8")
        totals: dict[str, int] = {}
        for key in module.COVERAGE:
            group = key.split("/")[0] if "/" in key.split("::")[0] else "main.rs"
            totals[group] = totals.get(group, 0) + 1
        titles = {
            "land": "`land/`",
            "merge": "`merge/`",
            "repair": "`repair/`",
            "io": "`io/`",
            "main.rs": "`main.rs`",
        }
        for group, count in totals.items():
            heading = f"## {titles[group]} — {count} functions"
            assert heading in text, f"missing or wrong heading: {heading}"

    def test_the_table_has_a_row_for_every_function(self) -> None:
        """One row each, so the table can be read as the account it claims to be.

        Rows are matched on their *source file* column rather than by counting
        pipes: a cell may legitimately contain an escaped ``\\|``, as the entry
        for ``classify_conflict`` does when it writes out the weighting formula.
        """
        text = DOC.read_text(encoding="utf-8")
        rows = [line for line in text.splitlines() if _ROW.match(line)]
        assert len(rows) == EXPECTED_FUNCTIONS, f"{len(rows)} rows, expected {EXPECTED_FUNCTIONS}"

    def test_no_row_has_an_unescaped_pipe(self) -> None:
        """An unescaped pipe ends its cell and silently shears the row apart."""
        broken = [
            line
            for line in DOC.read_text(encoding="utf-8").splitlines()
            if _ROW.match(line) and _UNESCAPED_PIPE.findall(line) != ["|"] * 5
        ]
        assert not broken, broken

    def test_the_total_is_stated(self) -> None:
        """A reader should not have to add up the sections to check."""
        assert f"**all {EXPECTED_FUNCTIONS} of them**" in DOC.read_text(encoding="utf-8")


class TestTheReverseDirection:
    """Every module of ours is placed relative to Merged Lands.

    The coverage table proves *their* 191 functions all landed somewhere here.
    It says nothing about the other direction -- how much of this merge is code
    no reference implementation stands behind. That is now the larger risk: of
    158 functions in ``wraithguard/land``, only 83 trace to a Rust counterpart.

    Most of the remainder is Python plumbing, but some of it moves vertices the
    original never moves. Those modules are marked ``ours`` so that "faithful
    port" is never read as covering them.
    """

    def test_every_module_is_classified(self) -> None:
        """A new module must declare where it stands before it can ship."""
        module = _generator()
        present = {path.name for path in (ROOT / "wraithguard" / "land").glob("*.py")}
        assert present == set(module.MODULES), (
            f"unclassified: {sorted(present - set(module.MODULES))}; "
            f"stale: {sorted(set(module.MODULES) - present)}"
        )

    def test_every_classification_is_known(self) -> None:
        """Only three answers are meaningful here."""
        module = _generator()
        allowed = {"port", "ours", "ours-aux"}
        wrong = {name: role for name, (role, _why) in module.MODULES.items() if role not in allowed}
        assert not wrong, wrong

    def test_the_terrain_changing_additions_are_named(self) -> None:
        """These three are where our output can differ from Merged Lands'.

        Pinned by name because the honest claim about this port depends on the
        list being short and known. If a fourth appears, it is a deliberate
        decision that belongs in the documentation, not a quiet addition.
        """
        module = _generator()
        ours = {name for name, (role, _why) in module.MODULES.items() if role == "ours"}
        assert ours == {"seams.py", "slope.py", "curvature.py"}

    def test_every_classification_says_why(self) -> None:
        """A label with no reason is not an account of anything."""
        module = _generator()
        empty = [name for name, (_role, why) in module.MODULES.items() if not why.strip()]
        assert not empty, empty


class TestScan:
    """The Rust scanner, against small synthetic files -- not the real port."""

    def test_a_free_function_has_no_context(self, tmp_path: Path) -> None:
        (tmp_path / "main.rs").write_text("fn top_level() {}\n", encoding="utf-8")
        module = _generator()

        functions = module.scan(tmp_path)

        assert len(functions) == 1
        fn = functions[0]
        assert fn.file == "main.rs"
        assert fn.line == 1
        assert fn.context == ""
        assert fn.name == "top_level"
        assert fn.key == "main.rs::::top_level"
        assert fn.label == "`top_level`"

    def test_a_method_is_scoped_to_its_impl(self, tmp_path: Path) -> None:
        (tmp_path / "land.rs").write_text(
            "impl Landmass {\n    pub fn new() -> Self {}\n}\n", encoding="utf-8"
        )
        module = _generator()

        functions = module.scan(tmp_path)

        assert len(functions) == 1
        assert functions[0].context == "Landmass"
        assert functions[0].label == "`Landmass::new`"

    def test_a_trait_method_is_scoped_to_the_trait(self, tmp_path: Path) -> None:
        (tmp_path / "io.rs").write_text(
            "trait Reader {\n    fn read(&self) -> u8;\n}\n", encoding="utf-8"
        )
        module = _generator()

        functions = module.scan(tmp_path)

        assert functions[0].context == "trait Reader"

    def test_context_resets_between_files(self, tmp_path: Path) -> None:
        """An impl block in one file must not leak context into the next."""
        (tmp_path / "a.rs").write_text("impl Foo {\n    fn one() {}\n}\n", encoding="utf-8")
        (tmp_path / "b.rs").write_text("fn two() {}\n", encoding="utf-8")
        module = _generator()

        functions = module.scan(tmp_path)

        by_name = {f.name: f for f in functions}
        assert by_name["two"].context == ""

    def test_files_are_scanned_in_sorted_order(self, tmp_path: Path) -> None:
        (tmp_path / "b.rs").write_text("fn second() {}\n", encoding="utf-8")
        (tmp_path / "a.rs").write_text("fn first() {}\n", encoding="utf-8")
        module = _generator()

        functions = module.scan(tmp_path)

        assert [f.file for f in functions] == ["a.rs", "b.rs"]

    def test_no_rust_files_is_a_hard_stop(self, tmp_path: Path) -> None:
        module = _generator()
        with pytest.raises(SystemExit, match=r"no \.rs files"):
            module.scan(tmp_path)

    def test_a_nested_directory_is_included(self, tmp_path: Path) -> None:
        nested = tmp_path / "land"
        nested.mkdir()
        (nested / "cells.rs").write_text("fn merge_cells() {}\n", encoding="utf-8")
        module = _generator()

        functions = module.scan(tmp_path)

        assert functions[0].file == "land/cells.rs"

    def test_a_malformed_impl_line_is_not_treated_as_a_context_change(self, tmp_path: Path) -> None:
        """Starts with 'impl' but the regex needs a name after it; must not crash or match."""
        (tmp_path / "weird.rs").write_text("impl\nfn after() {}\n", encoding="utf-8")
        module = _generator()

        functions = module.scan(tmp_path)

        assert functions[0].context == ""


class TestCheck:
    """check() against a small synthetic COVERAGE map, not the real 191 entries."""

    def _fn(self, module, file: str = "main.rs", context: str = "", name: str = "foo"):
        return module.Function(file=file, line=1, context=context, name=name)

    def test_an_uncovered_function_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _generator()
        monkeypatch.setattr(module, "COVERAGE", {})
        fn = self._fn(module)

        problems = module.check([fn])

        assert any("UNCOVERED" in p and fn.key in p for p in problems)

    def test_a_bad_status_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _generator()
        fn = self._fn(module)
        monkeypatch.setattr(module, "COVERAGE", {fn.key: ("not-a-real-status", "somewhere")})

        problems = module.check([fn])

        assert any("BAD STATUS" in p for p in problems)

    def test_a_stale_entry_with_no_matching_function_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _generator()
        fn = self._fn(module)
        monkeypatch.setattr(
            module,
            "COVERAGE",
            {fn.key: ("ported", "here"), "ghost.rs::::gone": ("ported", "here")},
        )

        problems = module.check([fn])

        assert any("STALE ENTRY" in p and "ghost.rs" in p for p in problems)

    def test_a_fully_covered_list_has_no_problems(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _generator()
        fn = self._fn(module)
        monkeypatch.setattr(module, "COVERAGE", {fn.key: ("ported", "here")})

        assert module.check([fn]) == []


class TestRender:
    def test_a_covered_function_becomes_a_table_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _generator()
        fn = module.Function(file="land/cells.rs", line=3, context="", name="merge_cells")
        monkeypatch.setattr(module, "COVERAGE", {fn.key: ("ported", "wraithguard/land/cells.py")})

        text = module.render([fn])

        assert "## `land/` — 1 functions" in text
        assert "`merge_cells`" in text
        assert "wraithguard/land/cells.py" in text

    def test_a_pipe_in_the_location_is_escaped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _generator()
        fn = module.Function(file="merge/weight.rs", line=1, context="", name="classify")
        monkeypatch.setattr(
            module, "COVERAGE", {fn.key: ("ported", "weighting: |lhs|/(|lhs|+|rhs|)")}
        )

        text = module.render([fn])

        assert r"\|lhs\|" in text

    def test_a_group_with_nothing_scanned_still_gets_a_zero_heading(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _generator()
        fn = module.Function(file="main.rs", line=1, context="", name="entry")
        monkeypatch.setattr(module, "COVERAGE", {fn.key: ("ported", "cli.py")})

        text = module.render([fn])

        assert "## `land/` — 0 functions" in text


class TestMain:
    def test_check_mode_writes_nothing_and_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        module = _generator()
        (tmp_path / "main.rs").write_text("fn entry() {}\n", encoding="utf-8")
        monkeypatch.setattr(module, "COVERAGE", {"main.rs::::entry": ("ported", "cli.py")})
        monkeypatch.setattr(sys, "argv", ["gen", "--src", str(tmp_path), "--check"])

        rc = module.main()

        assert rc == 0
        assert "1 function(s), all accounted for" in capsys.readouterr().out

    def test_problems_are_printed_and_the_run_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        module = _generator()
        (tmp_path / "main.rs").write_text("fn entry() {}\n", encoding="utf-8")
        monkeypatch.setattr(module, "COVERAGE", {})
        monkeypatch.setattr(sys, "argv", ["gen", "--src", str(tmp_path), "--check"])

        rc = module.main()

        assert rc == 1
        assert "UNCOVERED" in capsys.readouterr().err

    def test_out_writes_the_rendered_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        module = _generator()
        (tmp_path / "main.rs").write_text("fn entry() {}\n", encoding="utf-8")
        monkeypatch.setattr(module, "COVERAGE", {"main.rs::::entry": ("ported", "cli.py")})
        out_path = tmp_path / "out.md"
        monkeypatch.setattr(sys, "argv", ["gen", "--src", str(tmp_path), "--out", str(out_path)])

        rc = module.main()

        assert rc == 0
        assert out_path.is_file()
        assert "entry" in out_path.read_text(encoding="utf-8")
        assert f"wrote {out_path}" in capsys.readouterr().out

    def test_no_out_and_no_check_still_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _generator()
        (tmp_path / "main.rs").write_text("fn entry() {}\n", encoding="utf-8")
        monkeypatch.setattr(module, "COVERAGE", {"main.rs::::entry": ("ported", "cli.py")})
        monkeypatch.setattr(sys, "argv", ["gen", "--src", str(tmp_path)])

        rc = module.main()

        assert rc == 0
