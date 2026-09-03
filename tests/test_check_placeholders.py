"""Tests for ``tools/check_placeholders.py``, the ``%(name)s`` key checker.

A mistyped placeholder key is a runtime ``KeyError`` nothing else catches, so
the tool turns ``_("...%(count)d...") % {"cont": n}`` into a lint failure. The
regex scanners, the marker/dict AST readers and the per-file checker are all
pure, so each is driven directly -- the positive cases (a real missing key is
caught) and, just as important, the conservative negatives (a dict built in a
variable is reported unverifiable rather than guessed at).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.check_placeholders import (
    _dict_keys,
    _iter_sources,
    _marker_strings,
    check_file,
    main,
    placeholder_keys,
    positional_placeholders,
)


def _binop(expr: str) -> ast.BinOp:
    """Parse a single ``call % dict`` expression to its BinOp node."""
    node = ast.parse(expr, mode="eval").body
    assert isinstance(node, ast.BinOp)
    return node


class TestPlaceholderKeys:
    def test_named_keys_are_collected(self) -> None:
        assert placeholder_keys("Loaded %(count)d of %(total)d") == {"count", "total"}

    def test_an_escaped_percent_is_not_a_placeholder(self) -> None:
        assert placeholder_keys("100%% done, %(n)s left") == {"n"}

    def test_a_string_with_no_placeholders_is_empty(self) -> None:
        assert placeholder_keys("nothing here") == set()


class TestPositionalPlaceholders:
    def test_bare_conversions_are_listed_in_order(self) -> None:
        assert positional_placeholders("%s of %d") == ["%s", "%d"]

    def test_escaped_percent_is_not_positional(self) -> None:
        assert positional_placeholders("100%% done") == []

    def test_a_named_conversion_is_not_positional(self) -> None:
        assert positional_placeholders("%(n)s") == []


class TestMarkerStrings:
    def test_a_gettext_call_yields_one_string(self) -> None:
        call = ast.parse('_("hello %(n)s")', mode="eval").body
        assert _marker_strings(call) == ["hello %(n)s"]

    def test_an_ngettext_call_yields_both_forms(self) -> None:
        call = ast.parse('ngettext("%(n)d file", "%(n)d files", n)', mode="eval").body
        assert _marker_strings(call) == ["%(n)d file", "%(n)d files"]

    def test_an_attribute_marker_is_recognised(self) -> None:
        call = ast.parse('gettext("x")', mode="eval").body  # bare name
        assert _marker_strings(call) == ["x"]
        attr = ast.parse('mod.gettext("y")', mode="eval").body  # attribute
        assert _marker_strings(attr) == ["y"]

    def test_a_non_marker_call_is_ignored(self) -> None:
        call = ast.parse('print("x")', mode="eval").body
        assert _marker_strings(call) is None

    def test_a_non_literal_gettext_argument_is_unreadable(self) -> None:
        call = ast.parse("_(variable)", mode="eval").body
        assert _marker_strings(call) is None

    def test_ngettext_with_too_few_args_is_unreadable(self) -> None:
        call = ast.parse('ngettext("only one")', mode="eval").body
        assert _marker_strings(call) is None

    def test_ngettext_with_a_non_literal_form_is_unreadable(self) -> None:
        call = ast.parse('ngettext("one", plural_var, n)', mode="eval").body
        assert _marker_strings(call) is None


class TestDictKeys:
    def test_literal_string_keys_are_returned(self) -> None:
        node = _binop('_("x") % {"a": 1, "b": 2}')
        assert _dict_keys(node.right) == {"a", "b"}

    def test_a_non_dict_operand_is_unverifiable(self) -> None:
        node = _binop('_("x") % values')
        assert _dict_keys(node.right) is None

    def test_a_computed_key_makes_the_dict_unverifiable(self) -> None:
        node = _binop('_("x") % {key: 1}')  # variable key
        assert _dict_keys(node.right) is None


class TestCheckFile:
    def _check(self, tmp_path: Path, source: str) -> list[str]:
        path = tmp_path / "mod.py"
        path.write_text(source, encoding="utf-8")
        return check_file(path, tmp_path)

    def test_a_consistent_file_reports_nothing(self, tmp_path: Path) -> None:
        assert self._check(tmp_path, '_("Loaded %(n)d") % {"n": 1}\n') == []

    def test_a_missing_key_is_reported(self, tmp_path: Path) -> None:
        out = self._check(tmp_path, '_("Loaded %(count)d") % {"cont": 1}\n')
        assert any("missing key 'count'" in line for line in out)
        assert any("unused key 'cont'" in line for line in out)

    def test_a_positional_placeholder_in_a_marked_string_is_reported(self, tmp_path: Path) -> None:
        out = self._check(tmp_path, '_("Loaded %s")\n')
        assert any("positional" in line for line in out)

    def test_a_non_dict_right_side_is_reported_unverifiable(self, tmp_path: Path) -> None:
        out = self._check(tmp_path, '_("Loaded %(n)d") % values\n')
        assert any("cannot verify" in line for line in out)

    def test_a_syntax_error_is_reported_not_raised(self, tmp_path: Path) -> None:
        out = self._check(tmp_path, "def (:\n")
        assert any("could not parse" in line for line in out)

    def test_an_ngettext_key_used_by_only_one_form_is_not_flagged(self, tmp_path: Path) -> None:
        """A count dropped by the plural form is still 'used'; no unused-key noise."""
        src = 'ngettext("%(n)d file", "many files", n) % {"n": 1}\n'
        assert self._check(tmp_path, src) == []


class TestIterSourcesAndMain:
    def test_a_directory_is_expanded_to_its_python_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "b.txt").write_text("", encoding="utf-8")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "c.py").write_text("", encoding="utf-8")
        found = _iter_sources([tmp_path])
        assert [p.name for p in found] == ["a.py"]  # .txt and __pycache__ skipped

    def test_main_reports_a_clean_specific_file(self, tmp_path: Path, capsys) -> None:
        path = tmp_path / "ok.py"
        path.write_text('_("Loaded %(n)d") % {"n": 1}\n', encoding="utf-8")
        assert main([str(path)]) == 0
        assert "placeholders ok" in capsys.readouterr().out

    def test_main_fails_on_a_file_with_a_mismatch(self, tmp_path: Path, capsys) -> None:
        path = tmp_path / "bad.py"
        path.write_text('_("Loaded %(count)d") % {"cont": 1}\n', encoding="utf-8")
        assert main([str(path)]) == 1
        assert "missing key" in capsys.readouterr().out

    def test_main_with_no_sources_found_fails(self, tmp_path: Path, capsys) -> None:
        assert main([str(tmp_path / "nothing.txt")]) == 1
        assert "no Python sources" in capsys.readouterr().err

    def test_main_scans_the_shipped_sources_by_default(self, capsys) -> None:
        """No arguments scans the real project; it should be clean and exit zero."""
        assert main([]) == 0
        assert "placeholders ok" in capsys.readouterr().out
