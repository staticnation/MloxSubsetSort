"""Simulating momw-configurator's apply step: replaces, removes, and the preview.

Covers the parts of ``wraithguard.configurator.apply`` a dry-run leans on but the
existing tests skip: ``remove*`` matching on value-less lines, the array-vs-string
guard, the whole ``replace`` block, ``insert`` anchor ambiguity, the ``listName``
filter, template notes, the tomllib/tomli fallbacks, and the round-trip preview's
strip-and-verify path. All pure: cfg lines and TOML text in, new lines out.
"""

from __future__ import annotations

import sys

import pytest

from wraithguard.configurator.apply import (
    configurator_remove_matches,
    customization_string_list,
    preview_configurator_result,
    simulate_configurator_apply,
)
from wraithguard.configurator.cfglines import normalize_data_path


class TestRemoveMatches:
    """``shouldRemoveLine`` semantics, including the value-less line."""

    def test_a_path_value_against_a_line_with_no_value_does_not_match(self) -> None:
        """A path-like removal needs a cfg value to compare; a line without one
        (no ``=``) simply does not match, rather than erroring."""
        assert configurator_remove_matches("mods/x", "a line without an equals sign") is False

    def test_a_relative_path_matches_by_suffix(self) -> None:
        """A relative path matches a line whose value ends with ``/value``."""
        assert configurator_remove_matches("Patch", 'data="C:/games/Patch"') is True

    def test_a_plain_substring_matches_the_whole_line(self) -> None:
        """A value with no slash is a whole-line substring test."""
        assert configurator_remove_matches("Bloodmoon.esm", "content=Bloodmoon.esm") is True


class TestCustomizationStringList:
    """A ``remove*`` value must be an array of strings, not a bare string."""

    def test_a_bare_string_is_rejected_silently_without_an_errors_list(self) -> None:
        """No errors list: the wrong-type value is dropped, no exception."""
        assert customization_string_list({"removeContent": "X.esp"}, "removeContent") == []

    def test_a_bare_string_is_reported_with_an_errors_list(self) -> None:
        """With an errors list, the rejection is explained to the user."""
        errs: list[str] = []
        assert customization_string_list({"removeContent": "X.esp"}, "removeContent", errs) == []
        assert errs and "array of strings" in errs[0]

    def test_a_non_string_entry_is_skipped(self) -> None:
        """A non-string item in the array is dropped; the strings survive."""
        assert customization_string_list({"removeContent": ["A.esp", 5]}, "removeContent") == [
            "A.esp"
        ]

    def test_a_non_string_entry_is_reported_with_an_errors_list(self) -> None:
        """With an errors list, the non-string item is named."""
        errs: list[str] = []
        out = customization_string_list({"removeContent": ["A.esp", 5]}, "removeContent", errs)
        assert out == ["A.esp"]
        assert errs and "not a string" in errs[0]

    def test_an_absent_key_is_an_empty_list(self) -> None:
        """A missing ``remove*`` key contributes nothing."""
        assert customization_string_list({}, "removeContent") == []


class TestSimulateReplace:
    """The ``replace`` block: single match rewrites, multiple matches skip."""

    def test_a_single_match_is_rewritten(self) -> None:
        """One matching line has its value replaced in place."""
        toml = '[[Customizations]]\n[[Customizations.replace]]\nsource = "A.esp"\ndest = "B.esp"\n'
        sim, errs, _ = simulate_configurator_apply(["content=A.esp"], toml)
        assert sim == ["content=B.esp"]
        assert errs == []

    def test_a_data_line_replacement_is_quoted(self) -> None:
        """A ``data=`` replacement gets a quoted value, matching the Go source."""
        toml = (
            "[[Customizations]]\n[[Customizations.replace]]\n"
            'source = "C:/mods/x"\ndest = "C:/mods/y"\n'
        )
        sim, _, _ = simulate_configurator_apply(["data=C:/mods/x"], toml)
        assert sim == ['data="C:/mods/y"']

    def test_more_than_one_match_is_skipped_with_detail(self) -> None:
        """An ambiguous replace source is reported and the entry left undone."""
        toml = '[[Customizations]]\n[[Customizations.replace]]\nsource = "A.esp"\ndest = "B.esp"\n'
        lines = [f"content=A.esp {i}" for i in range(6)]  # 6 matches -> "and N more"
        sim, errs, _ = simulate_configurator_apply(lines, toml)
        assert sim == lines  # unchanged
        assert errs and "more than one line" in errs[0]
        assert "more" in errs[0]

    def test_a_few_matches_are_listed_without_an_and_more_tail(self) -> None:
        """Two-to-five matches are all shown, so no ``... and N more`` is added."""
        toml = '[[Customizations]]\n[[Customizations.replace]]\nsource = "A.esp"\ndest = "B.esp"\n'
        lines = [f"content=A.esp {i}" for i in range(2)]  # only 2 matches
        sim, errs, _ = simulate_configurator_apply(lines, toml)
        assert sim == lines  # unchanged
        assert errs and "more than one line" in errs[0]
        assert "... and" not in errs[0]  # every match fit, so no truncation note

    def test_a_replace_source_matching_nothing_is_a_silent_no_op(self) -> None:
        """A source no line contains changes nothing and raises no error."""
        toml = '[[Customizations]]\n[[Customizations.replace]]\nsource = "Absent.esp"\ndest = "X"\n'
        sim, errs, _ = simulate_configurator_apply(["content=A.esp"], toml)
        assert sim == ["content=A.esp"]  # unchanged
        assert errs == []


class TestSimulateInsertAndFilters:
    """Insert ambiguity, the listName filter, and template notes."""

    def test_an_ambiguous_insert_anchor_is_fatal(self) -> None:
        """More than one anchor match abandons the cfg, as the real tool does."""
        toml = '[[Customizations]]\n[[Customizations.insert]]\nafter = "content=X"\ninsert = "Y"\n'
        lines = [f"content=X {i}" for i in range(6)]
        sim, errs, _ = simulate_configurator_apply(lines, toml)
        assert sim is None
        assert errs and "FATAL" in errs[0]

    def test_a_block_for_another_list_is_skipped(self) -> None:
        """With a listName filter, a block for a different list does nothing."""
        toml = (
            '[[Customizations]]\nlistName = "other"\n'
            '[[Customizations.replace]]\nsource = "A.esp"\ndest = "B.esp"\n'
        )
        sim, errs, _ = simulate_configurator_apply(["content=A.esp"], toml, list_name="mine")
        assert sim == ["content=A.esp"]
        assert errs == []

    def test_an_unexpanded_template_is_noted(self) -> None:
        """A value carrying a template/env var is flagged, not shown as literal."""
        toml = (
            "[[Customizations]]\n[[Customizations.insert]]\n"
            'after = "anchor"\ninsert = "$MODDIR/x"\n'
        )
        _, _, notes = simulate_configurator_apply(["anchor line"], toml)
        assert notes and "unexpanded" in notes[0]


class TestTomlFallbacks:
    """The tomllib -> tomli -> 'no toml' import ladder."""

    def test_it_falls_back_to_tomli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With tomllib hidden, both simulate and preview parse via tomli.

        Only meaningful where ``tomli`` is actually installed -- it is not a
        dependency on Python 3.11+, so this skips there rather than testing the
        no-parser path (which ``test_no_toml_library`` covers).
        """
        pytest.importorskip("tomli")
        monkeypatch.setitem(sys.modules, "tomllib", None)
        toml = '[[Customizations]]\n[[Customizations.replace]]\nsource = "A"\ndest = "B"\n'
        sim, _, _ = simulate_configurator_apply(["content=A"], toml)
        assert sim == ["content=B"]
        ok, _ = preview_configurator_result(["content=A"], toml, ["B"], [])
        assert ok is True

    def test_preview_falls_back_to_tomli_without_the_real_package(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """preview_configurator_result's own tomllib->tomli ladder, proven without tomli installed.

        test_it_falls_back_to_tomli above skips entirely where tomli is not
        actually installed (Python 3.11+ does not need it), which is exactly
        this sandbox -- so that test alone never proves this function's
        second import line runs. A fake module standing in for tomli, built
        on the real tomllib parser this interpreter already has, proves the
        fallback wiring without needing the package.
        """
        import types

        _real_toml = pytest.importorskip("tomllib")

        monkeypatch.setitem(sys.modules, "tomllib", None)
        monkeypatch.setitem(sys.modules, "tomli", types.SimpleNamespace(loads=_real_toml.loads))
        toml = '[[Customizations]]\n[[Customizations.replace]]\nsource = "A"\ndest = "B"\n'
        ok, _ = preview_configurator_result(["content=A"], toml, ["B"], [])
        assert ok is True

    def test_no_toml_library_skips_the_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With neither tomllib nor tomli, the preview is skipped with a note."""
        monkeypatch.setitem(sys.modules, "tomllib", None)
        monkeypatch.setitem(sys.modules, "tomli", None)
        sim, _, notes = simulate_configurator_apply(["content=A"], "[[Customizations]]\n")
        assert sim is None
        assert notes and "preview skipped" in notes[0]


class TestPreviewResult:
    """Stripping the run's own additions from the base, then verifying."""

    def test_it_strips_subset_content_and_user_data_then_verifies(self) -> None:
        """The user's own content= and data= lines are removed before the sim,
        so the base resembles the fresh curated cfg the Configurator sees."""
        plan = ["content=Mine.esp", "content=Base.esp", "data=C:/mymod"]
        ok, report = preview_configurator_result(
            plan,
            "",  # no customisations: base should reproduce the expected order
            ["Base.esp"],
            ["Mine.esp"],
            user_data_norms={normalize_data_path("C:/mymod")},
        )
        assert ok is True
        assert any("VERIFIED" in line for line in report)

    def test_an_aborted_simulation_reports_and_fails(self) -> None:
        """When the sim would abort (ambiguous anchor), the preview says so."""
        toml = '[[Customizations]]\n[[Customizations.insert]]\nafter = "X.esp"\ninsert = "Y"\n'
        ok, report = preview_configurator_result(
            ["content=X.esp", "content=X.esp"], toml, ["X.esp"], []
        )
        assert ok is False
        assert any("ABORTED" in line for line in report)
