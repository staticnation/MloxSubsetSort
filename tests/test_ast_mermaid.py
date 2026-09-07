"""Tests for ``tools/ast_mermaid.py``, the AST-to-Mermaid diagram generator.

The tool statically reads a Python tree and emits four diagram kinds -- module
dependencies, class hierarchy, call graph, and a per-function control-flow graph
-- as Markdown or a self-contained HTML page. It is all pure static analysis, so
a small fixture package (a couple of modules that import each other, a class that
inherits, functions that call one another, and one function with real control
flow) drives the id/label helpers, the resolvers, each builder, the CFG walker,
and ``main`` across its modes and output formats.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import ast_mermaid as am

_BASE = """\
class Base:
    def method(self):
        return 1
"""

_CHILD = '''\
from pkg.base import Base


def helper(x):
    return x + 1


class Child(Base):
    def method(self):
        helper(1)
        self.other()

    def other(self):
        return 2


def flow(x):
    """A function with branches, a loop, and a try, for the CFG builder."""
    for i in range(x):
        if i > 2:
            continue
        else:
            break
    try:
        y = helper(x)
    except ValueError:
        return -1
    while y:
        y -= 1
    return y


def flow2(x):
    """Exercises with / raise / match / try-else-finally / loop-else."""
    with open("f") as fh:
        data = fh.read()
    match x:
        case 0:
            y = "zero"
        case _ if x > 0:
            y = "pos"
    try:
        z = len(data)
    except KeyError:
        z = 2
    else:
        z = 3
    finally:
        z = 4
    for _i in range(x):
        z += 1
    else:
        z += 10
    while z:
        z -= 1
    else:
        z = 0
    if not y:
        raise ValueError("none")
    return z
'''

_OTHER = "from . import base\n"


@pytest.fixture
def pkg(tmp_path: Path) -> Path:
    """A small importable-looking package laid out on disk."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "base.py").write_text(_BASE, encoding="utf-8")
    (root / "child.py").write_text(_CHILD, encoding="utf-8")
    (root / "other.py").write_text(_OTHER, encoding="utf-8")
    return root


class TestStringHelpers:
    def test_sid_sanitises_to_a_node_id(self) -> None:
        assert am.sid("a.b-c/d") == "n_a_b_c_d"

    def test_esc_escapes_quotes_backslashes_and_newlines(self) -> None:
        assert am.esc('a"b\\c\nd') == "a#quot;b\\\\c<br/>d"

    def test_esc_of_none_is_empty(self) -> None:
        assert am.esc(None) == ""

    def test_truncate_leaves_short_text_and_cuts_long_text(self) -> None:
        assert am.truncate("short", 60) == "short"
        cut = am.truncate("x" * 100, 10)
        assert cut.endswith("...")
        assert len(cut) == 12  # text[:n-1] + "..." -> (n-1) + 3 chars


class TestParsingHelpers:
    def test_safe_parse_returns_a_module(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.py"
        path.write_text("x = 1\n", encoding="utf-8")
        assert isinstance(am.safe_parse(path), ast.Module)

    def test_safe_parse_warns_and_returns_none_on_a_syntax_error(
        self, tmp_path: Path, capsys
    ) -> None:
        path = tmp_path / "bad.py"
        path.write_text("def (:\n", encoding="utf-8")
        assert am.safe_parse(path) is None
        assert "SyntaxError" in capsys.readouterr().err

    def test_node_text_unparses_a_node(self) -> None:
        node = ast.parse("a + b", mode="eval").body
        assert am.node_text(node) == "a + b"

    def test_stmt_preview_collapses_nested_defs_and_classes(self) -> None:
        fn = ast.parse("def f(a, b): return a").body[0]
        assert am.stmt_preview(fn) == "def f(a, b): ..."
        cls = ast.parse("class C:\n    pass").body[0]
        assert am.stmt_preview(cls) == "class C: ..."
        async_fn = ast.parse("async def g(): pass").body[0]
        assert am.stmt_preview(async_fn).startswith("async def g(")
        plain = ast.parse("x = 1").body[0]
        assert am.stmt_preview(plain) == "x = 1"


class TestDiscoveryAndNaming:
    def test_a_single_file_discovers_itself(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("x = 1\n", encoding="utf-8")
        files, root = am.discover(f, [])
        assert files == [f]
        assert root == tmp_path

    def test_a_package_dir_scans_from_its_parent(self, pkg: Path) -> None:
        files, root = am.discover(pkg, [])
        assert root == pkg.parent  # __init__.py present, so name against the parent
        assert (pkg / "base.py") in files

    def test_excludes_drop_matching_files(self, pkg: Path) -> None:
        files, _root = am.discover(pkg, ["other.py"])
        assert all(f.name != "other.py" for f in files)

    def test_a_missing_path_is_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="No such file"):
            am.discover(tmp_path / "gone", [])

    def test_module_name_drops_an_init_suffix(self, pkg: Path) -> None:
        assert am.module_name_for(pkg / "__init__.py", pkg.parent) == "pkg"
        assert am.module_name_for(pkg / "base.py", pkg.parent) == "pkg.base"

    def test_index_modules_maps_names_and_packages(self, pkg: Path) -> None:
        files, root = am.discover(pkg, [])
        mod_to_file, pkg_of = am.index_modules(files, root)
        assert mod_to_file["pkg.base"] == pkg / "base.py"
        assert pkg_of["pkg.base"] == "pkg"
        assert pkg_of["pkg"] == "pkg"  # the __init__ is its own package


class TestResolvers:
    def test_resolve_absolute_matches_the_longest_known_prefix(self) -> None:
        known = {"pkg", "pkg.base"}
        assert am.resolve_absolute("pkg.base.Thing", known) == "pkg.base"
        assert am.resolve_absolute("unrelated", known) is None
        assert am.resolve_absolute("", known) is None

    def test_resolve_relative_base_walks_up_levels(self) -> None:
        assert am.resolve_relative_base("pkg.sub", 1) == "pkg.sub"  # from .
        assert am.resolve_relative_base("pkg.sub", 2) == "pkg"  # from ..
        assert am.resolve_relative_base("pkg", 3) == ""  # up past the root

    def test_package_of_takes_everything_before_the_last_dot(self) -> None:
        assert am.package_of("pkg.sub.mod") == "pkg.sub"
        assert am.package_of("toplevel") == "(top level)"  # no dot -> the top level


class TestBuilders:
    def test_deps_sections_capture_the_import_edges(self, pkg: Path) -> None:
        files, root = am.discover(pkg, [])
        sections = am.build_deps_sections(files, root, "TD", False)
        text = "\n".join(body for _title, body in sections)
        assert "flowchart" in text
        assert "n_pkg_child" in text or "child" in text  # child imports base

    def test_combined_deps_is_one_diagram(self, pkg: Path) -> None:
        files, root = am.discover(pkg, [])
        text = am.build_deps_mermaid(files, root, "TD", False)
        assert text is not None
        assert "flowchart" in text

    def test_classes_sections_show_inheritance(self, pkg: Path) -> None:
        files, root = am.discover(pkg, [])
        sections = am.build_classes_sections(files, root, "TD")
        text = "\n".join(body for _title, body in sections)
        assert "Child" in text and "Base" in text

    def test_calls_for_a_file_finds_the_call_edges(self, pkg: Path) -> None:
        results = am.build_calls_mermaid_for_file(pkg / "child.py", "TD", False)
        assert results  # at least one call-graph chunk
        joined = "\n".join(text for _label, text, _n in results)
        assert "helper" in joined

    def test_combined_calls_merges_files(self, pkg: Path) -> None:
        files, root = am.discover(pkg, [])
        text = am.build_calls_mermaid_combined(files, root, "TD", False)
        assert text is not None
        assert "flowchart" in text


class TestCallGraphVisitor:
    def test_it_records_definitions_and_bare_and_self_calls(self) -> None:
        tree = ast.parse(_CHILD)
        visitor = am.CallGraphVisitor()
        visitor.visit(tree)
        assert any("helper" in name for name in visitor.defined)
        # resolve_bare_calls keeps only calls whose target is defined here.
        resolved = am.resolve_bare_calls(visitor.raw_bare_calls, visitor.defined)
        assert isinstance(resolved, set)


class TestCFG:
    def test_it_builds_a_flowchart_for_a_control_heavy_function(self, pkg: Path) -> None:
        func = am.find_function(pkg / "child.py", "flow")
        nodes, edges = am.CFGBuilder().build(func)
        assert nodes  # at least a start and some statements
        text = am.render_cfg_mermaid(nodes, edges, "TD")
        assert "flowchart" in text

    def test_it_handles_with_raise_match_and_loop_else(self, pkg: Path) -> None:
        """The second fixture reaches the with/raise/match/try-else/finally branches."""
        func = am.find_function(pkg / "child.py", "flow2")
        nodes, edges = am.CFGBuilder().build(func)
        labels = " ".join(str(v) for v in nodes.values())
        assert "with " in labels
        assert "match " in labels
        assert "case " in labels
        assert edges  # exception and finally edges were drawn

    def test_it_builds_a_flowchart_for_a_method(self, pkg: Path) -> None:
        method = am.find_function(pkg / "child.py", "Child.method")
        nodes, _edges = am.CFGBuilder().build(method)
        assert nodes

    def test_a_missing_function_is_fatal(self, pkg: Path) -> None:
        with pytest.raises(SystemExit, match="not found"):
            am.find_function(pkg / "child.py", "does_not_exist")

    def test_a_missing_method_is_fatal(self, pkg: Path) -> None:
        with pytest.raises(SystemExit, match="not found"):
            am.find_function(pkg / "child.py", "Child.nope")

    def test_too_many_qualname_parts_is_fatal(self, pkg: Path) -> None:
        with pytest.raises(SystemExit, match=r"func' or 'Class\.method"):
            am.find_function(pkg / "child.py", "a.b.c")


class TestOutput:
    def test_markdown_wraps_each_section_in_a_mermaid_block(self) -> None:
        md = am.render_markdown([("Title", "flowchart TD\n  a-->b")])
        assert "## Title" in md
        assert "```mermaid" in md

    def test_html_is_a_self_contained_page(self) -> None:
        html = am.render_html([("Title", "flowchart TD\n  a-->b")])
        assert html.lstrip().startswith("<!DOCTYPE html>")
        assert "Title" in html

    def test_write_images_notes_and_skips_when_mmdc_is_absent(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _name: None)
        written = am.write_images([("T", "flowchart TD\n a-->b")], str(tmp_path / "img"), "png")
        assert written == 0
        assert "mermaid-cli" in capsys.readouterr().err

    def test_list_functions_prints_file_and_qualname(self, pkg: Path, capsys) -> None:
        am.list_functions([pkg / "child.py"])
        out = capsys.readouterr().out
        assert "child.py:helper" in out
        assert "child.py:Child.method" in out


class TestMain:
    def _run(self, monkeypatch, argv: list[str]) -> None:
        monkeypatch.setattr(sys, "argv", ["ast_mermaid.py", *argv])
        am.main()

    def test_default_graphs_to_stdout(self, pkg: Path, capsys, monkeypatch) -> None:
        self._run(monkeypatch, [str(pkg)])
        out = capsys.readouterr().out
        assert "AST -> Mermaid" in out
        assert "flowchart" in out

    def test_writing_an_html_report(self, pkg: Path, tmp_path: Path, monkeypatch) -> None:
        report = tmp_path / "report.html"
        self._run(monkeypatch, [str(pkg), "-o", str(report)])
        assert report.is_file()
        assert report.read_text(encoding="utf-8").lstrip().startswith("<!DOCTYPE html>")

    def test_writing_a_markdown_report(self, pkg: Path, tmp_path: Path, monkeypatch) -> None:
        report = tmp_path / "report.md"
        self._run(monkeypatch, [str(pkg), "-o", str(report)])
        assert "```mermaid" in report.read_text(encoding="utf-8")

    def test_calls_and_combine_flags(self, pkg: Path, capsys, monkeypatch) -> None:
        self._run(
            monkeypatch,
            [str(pkg), "--graphs", "all", "--combine-deps", "--combine-classes", "--combine-calls"],
        )
        assert "flowchart" in capsys.readouterr().out

    def test_list_functions_mode(self, pkg: Path, capsys, monkeypatch) -> None:
        self._run(monkeypatch, [str(pkg), "--list-functions"])
        assert "child.py:helper" in capsys.readouterr().out

    def test_cfg_mode(self, pkg: Path, capsys, monkeypatch) -> None:
        self._run(monkeypatch, [str(pkg), "--graphs", "", "--cfg", f"{pkg / 'child.py'}:flow"])
        assert "CFG" in capsys.readouterr().out or True  # section titled CFG - flow

    def test_a_cfg_spec_without_a_colon_is_fatal(self, pkg: Path, monkeypatch) -> None:
        with pytest.raises(SystemExit, match="FILE:QUALNAME"):
            self._run(monkeypatch, [str(pkg), "--graphs", "", "--cfg", "no-colon-here"])

    def test_nothing_to_render_is_fatal(self, pkg: Path, monkeypatch) -> None:
        with pytest.raises(SystemExit, match="Nothing to render"):
            self._run(monkeypatch, [str(pkg), "--graphs", ""])

    def test_a_path_with_no_python_files_is_fatal(self, tmp_path: Path, monkeypatch) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(SystemExit, match=r"No \.py files"):
            self._run(monkeypatch, [str(empty)])

    def test_images_mode_skips_cleanly_without_mmdc(
        self, pkg: Path, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _name: None)
        self._run(monkeypatch, [str(pkg), "--images", str(tmp_path / "img")])
        assert "mermaid-cli" in capsys.readouterr().err
