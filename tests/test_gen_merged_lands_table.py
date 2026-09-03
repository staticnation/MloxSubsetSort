"""Tests for ``tools/gen_merged_lands_table.py``, the port-coverage table generator.

The Merged Lands port claims function-by-function completeness, so this parses
the Rust source, checks every function against the hand-maintained ``COVERAGE``
map (failing on an uncovered function, a bad status, or a stale entry), and
renders the markdown table CI verifies. The scanner, checker and renderer are
pure; a small fake Rust tree drives the scan, a monkeypatched coverage map
isolates the checker and renderer, and ``main`` is exercised over its --check,
--out and failure paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import gen_merged_lands_table as gen
from tools.gen_merged_lands_table import Function, check, render, scan

_FAKE_RS = """\
fn free_function() {}

impl_from_macro!(Foo);

impl Foo {
    pub fn new() -> Self {}
    fn average(&self) {}
}

trait Bar {
    fn required(&self);
}
"""


def _write_src(root: Path, rel: str = "land/merge.rs", text: str = _FAKE_RS) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return root


class TestFunction:
    def test_key_joins_file_context_and_name(self) -> None:
        fn = Function("land/merge.rs", 3, "Foo", "new")
        assert fn.key == "land/merge.rs::Foo::new"

    def test_label_includes_the_context_when_present(self) -> None:
        assert Function("f.rs", 1, "Foo", "new").label == "`Foo::new`"

    def test_label_is_bare_for_a_free_function(self) -> None:
        assert Function("f.rs", 1, "", "helper").label == "`helper`"


class TestScan:
    def test_it_finds_functions_with_their_contexts(self, tmp_path: Path) -> None:
        found = scan(_write_src(tmp_path))
        by_name = {f.name: f for f in found}
        assert by_name["free_function"].context == ""
        assert by_name["new"].context == "Foo"
        assert by_name["average"].context == "Foo"
        assert by_name["required"].context == "trait Bar"

    def test_a_tree_with_no_rust_files_is_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match=r"no \.rs files"):
            scan(tmp_path)


class TestCheck:
    def test_a_fully_covered_set_has_no_problems(self, monkeypatch) -> None:
        monkeypatch.setattr(gen, "COVERAGE", {"f.rs::::a": ("ported", "here")})
        assert check([Function("f.rs", 1, "", "a")]) == []

    def test_an_uncovered_function_is_reported(self, monkeypatch) -> None:
        monkeypatch.setattr(gen, "COVERAGE", {})
        problems = check([Function("f.rs", 2, "", "a")])
        assert problems and problems[0].startswith("UNCOVERED")

    def test_a_bad_status_is_reported(self, monkeypatch) -> None:
        monkeypatch.setattr(gen, "COVERAGE", {"f.rs::::a": ("nonsense", "here")})
        problems = check([Function("f.rs", 1, "", "a")])
        assert problems and "BAD STATUS" in problems[0]

    def test_a_stale_entry_is_reported(self, monkeypatch) -> None:
        monkeypatch.setattr(gen, "COVERAGE", {"f.rs::::gone": ("ported", "here")})
        problems = check([])  # no functions, so the entry is stale
        assert problems and problems[0].startswith("STALE ENTRY")


class TestRender:
    def test_it_groups_by_top_directory_and_escapes_pipes(self, monkeypatch) -> None:
        coverage = {
            "land/merge.rs::::a": ("ported", "uses |lhs|/(|lhs|+|rhs|)"),
            "main.rs::::b": ("verified", "top level"),
        }
        monkeypatch.setattr(gen, "COVERAGE", coverage)
        functions = [
            Function("land/merge.rs", 1, "", "a"),
            Function("main.rs", 1, "", "b"),
        ]
        text = render(functions)
        assert "## `land/` — 1 functions" in text
        assert "## `main.rs` — 1 functions" in text
        assert r"\|lhs\|" in text  # the raw pipes were escaped
        assert "| `a` | `land/merge.rs` | ported |" in text


class TestMain:
    def test_check_succeeds_when_everything_is_covered(self, tmp_path, capsys, monkeypatch) -> None:
        functions = [Function("f.rs", 1, "", "a")]
        monkeypatch.setattr(gen, "scan", lambda _src: functions)
        monkeypatch.setattr(gen, "check", lambda _fns: [])
        monkeypatch.setattr(sys, "argv", ["gen", "--src", str(tmp_path), "--check"])
        assert gen.main() == 0
        assert "all accounted for" in capsys.readouterr().out

    def test_problems_make_it_fail(self, tmp_path, capsys, monkeypatch) -> None:
        monkeypatch.setattr(gen, "scan", lambda _src: [Function("f.rs", 1, "", "a")])
        monkeypatch.setattr(gen, "check", lambda _fns: ["UNCOVERED f.rs:1 f.rs::::a"])
        monkeypatch.setattr(sys, "argv", ["gen", "--src", str(tmp_path), "--check"])
        assert gen.main() == 1
        err = capsys.readouterr().err
        assert "UNCOVERED" in err
        assert "1 problem(s)" in err

    def test_out_writes_the_rendered_tables(self, tmp_path, capsys, monkeypatch) -> None:
        functions = [Function("land/merge.rs", 1, "", "a")]
        monkeypatch.setattr(gen, "scan", lambda _src: functions)
        monkeypatch.setattr(gen, "check", lambda _fns: [])
        monkeypatch.setattr(gen, "render", lambda _fns: "# rendered table\n")
        out = tmp_path / "tables.md"
        monkeypatch.setattr(sys, "argv", ["gen", "--src", str(tmp_path), "--out", str(out)])
        assert gen.main() == 0
        assert out.read_text(encoding="utf-8") == "# rendered table\n"
        assert "wrote" in capsys.readouterr().out

    def test_src_is_required(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "argv", ["gen"])  # no --src
        with pytest.raises(SystemExit):
            gen.main()
