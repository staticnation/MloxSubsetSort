"""Gate and prove ``tools/check_placeholders.py`` (CODE_REVIEW.md §17).

Two jobs, deliberately separate:

1. **The gate**: the shipped sources must be placeholder-consistent. This is
   the pytest twin of the CI step, so a local ``pytest`` run catches a bad key
   without needing the workflow.
2. **The negative controls**: each finding class is proven against a
   deliberately broken file. A checker is only trustworthy once it has been
   watched failing -- a green result from a tool that cannot go red proves
   nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import check_placeholders  # noqa: E402  (needs the sys.path line above)


def _run_on(tmp_path: Path, source: str) -> list[str]:
    """Write ``source`` to a scratch file and return the checker's findings."""
    target = tmp_path / "sample.py"
    target.write_text(source, encoding="utf-8")
    return check_placeholders.check_file(target, tmp_path)


class TestShippedSourcesGate:
    """The real tree must pass -- this is the enforcement half."""

    def test_shipped_sources_are_placeholder_consistent(self, capsys) -> None:
        assert check_placeholders.main([]) == 0
        assert "placeholders ok" in capsys.readouterr().out


class TestNegativeControls:
    """Each finding class, proven on a file built to trigger it."""

    def test_missing_key_is_reported(self, tmp_path: Path) -> None:
        findings = _run_on(tmp_path, '_("Loaded %(count)d files") % {"cont": 3}\n')
        assert any("missing key 'count'" in f for f in findings)

    def test_unused_key_is_reported(self, tmp_path: Path) -> None:
        findings = _run_on(tmp_path, '_("Done %(x)s") % {"x": 1, "extra": 2}\n')
        assert any("unused key 'extra'" in f for f in findings)

    def test_positional_placeholder_is_reported(self, tmp_path: Path) -> None:
        findings = _run_on(tmp_path, '_("Loaded %s files")\n')
        assert any("positional '%s'" in f for f in findings)

    def test_non_literal_dict_is_unverifiable_not_ignored(self, tmp_path: Path) -> None:
        findings = _run_on(tmp_path, 'd = {"count": 3}\n_("Loaded %(count)d files") % d\n')
        assert any("cannot verify" in f for f in findings)

    def test_escaped_percent_is_not_positional(self, tmp_path: Path) -> None:
        findings = _run_on(tmp_path, '_("100%% done %(n)d") % {"n": 1}\n')
        assert findings == []

    def test_ngettext_checks_both_forms(self, tmp_path: Path) -> None:
        findings = _run_on(
            tmp_path,
            'ngettext("%(count)d file", "%(count)d files", n) % {"cuont": n}\n',
        )
        assert any("missing key 'count'" in f for f in findings)
        assert any("unused key 'cuont'" in f for f in findings)

    def test_ngettext_plural_may_drop_the_count(self, tmp_path: Path) -> None:
        """A key consumed by either form is used; some languages drop it."""
        findings = _run_on(
            tmp_path,
            'ngettext("one thing (%(count)d)", "many things", n) % {"count": n}\n',
        )
        assert findings == []

    def test_clean_conversion_passes(self, tmp_path: Path) -> None:
        findings = _run_on(
            tmp_path,
            '_("Loaded %(count)d entries from %(name)s") % {"count": 3, "name": "x"}\n',
        )
        assert findings == []


class TestUserFacingStringsAreMarked:
    """Every user-facing literal in the GUI reaches a translator.

    The 3.0 i18n pass marked report and status output but missed the tooltips
    and several dialog bodies -- 42 strings that looked marked because the
    *widgets around them* were. This asserts the property directly rather than
    trusting a one-time sweep.
    """

    #: Call names whose positional string arguments are shown to the user.
    USER_FACING_CALLS = frozenset(
        {"add_tooltip", "showinfo", "showerror", "showwarning", "askyesno"}
    )
    #: Keyword arguments that carry user-visible text.
    USER_FACING_KWARGS = frozenset({"text", "title", "message", "label", "tooltip"})

    def _gui_sources(self) -> list[Path]:
        return [
            PROJECT_ROOT / "mlox_subset_sort_gui.py",
            *(PROJECT_ROOT / "mlox_subset/gui").glob("*.py"),
        ]

    def test_no_unmarked_user_facing_literals(self) -> None:
        import ast

        offenders = []
        for path in self._gui_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                candidates = list(node.args) if name in self.USER_FACING_CALLS else []
                candidates += [
                    kw.value for kw in node.keywords if kw.arg in self.USER_FACING_KWARGS
                ]
                for arg in candidates:
                    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                        continue
                    text = arg.value.strip()
                    # Short symbols and pure identifiers are data, not prose.
                    if len(text) < 4 or (text.replace(" ", "").isalnum() and len(text) < 8):
                        continue
                    offenders.append(f"{path.name}:{arg.lineno}: {text[:50]!r}")
        assert (
            not offenders
        ), f"{len(offenders)} user-facing string(s) not wrapped in _(): {offenders[:8]}"


class TestPlaceholderParsing:
    """The regex layer, at the unit level."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("%(count)d files", {"count"}),
            ("%(a)s and %(b)s", {"a", "b"}),
            ("no placeholders", set()),
            ("100%% done", set()),
            ("%(padded)05d", {"padded"}),
        ],
    )
    def test_named_keys(self, text: str, expected: set[str]) -> None:
        assert check_placeholders.placeholder_keys(text) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("%s plain", ["%s"]),
            ("%03d padded", ["%03d"]),
            ("%(named)s only", []),
            ("100%% escaped", []),
        ],
    )
    def test_positional(self, text: str, expected: list[str]) -> None:
        assert check_placeholders.positional_placeholders(text) == expected
