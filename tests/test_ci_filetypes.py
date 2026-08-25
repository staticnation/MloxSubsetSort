"""Case-insensitive ``filedialog`` filters for X11 (the Steam Deck bug).

Tk's own file dialog matches ``filetypes`` extension globs case-sensitively, so
``*.esp`` hides ``Mod.ESP`` on Linux until the user picks "All files" -- a real
snag on the Steam Deck, where Morrowind plugins are routinely upper-case. The
fix rewrites each glob to a ``[aA]``-class form, but only on X11: the native
Windows/macOS dialogs are already case-insensitive and do not understand those
classes.

These are pure string transforms, so they are tested here without tkinter.
"""

from __future__ import annotations

import fnmatch

import pytest

from wraithguard.gui import _ci_pattern, case_insensitive_filetypes


class TestCiPattern:
    """Turning a plain glob into a case-insensitive character-class glob."""

    def test_a_simple_extension_becomes_a_class_per_letter(self) -> None:
        """Every alphabetic character is wrapped in a lower/upper class."""
        assert _ci_pattern("*.esp") == "*.[eE][sS][pP]"

    def test_space_separated_patterns_are_each_transformed(self) -> None:
        """Multiple globs in one filter keep their spacing."""
        assert _ci_pattern("*.esp *.esm") == "*.[eE][sS][pP] *.[eE][sS][mM]"

    def test_non_letters_are_left_alone(self) -> None:
        """Stars, dots and digits pass through untouched."""
        assert _ci_pattern("*.*") == "*.*"
        assert _ci_pattern("tes3cmd*") == "[tT][eE][sS]3[cC][mM][dD]*"

    def test_the_result_matches_both_cases(self) -> None:
        """The whole point: the rewritten glob matches upper and lower case."""
        pat = _ci_pattern("*.esp")
        assert fnmatch.fnmatchcase("Mod.ESP", pat)
        assert fnmatch.fnmatchcase("Mod.esp", pat)
        assert fnmatch.fnmatchcase("Mod.Esp", pat)
        assert not fnmatch.fnmatchcase("Mod.esm", pat)


class TestCaseInsensitiveFiletypes:
    """The platform-aware wrapper around the filter list."""

    def test_on_x11_the_filters_are_rewritten(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Linux the extension globs become case-insensitive."""
        monkeypatch.setattr("sys.platform", "linux")
        out = case_insensitive_filetypes((("Plugins", "*.esp"), ("All files", "*.*")))
        assert out == (("Plugins", "*.[eE][sS][pP]"), ("All files", "*.*"))

    @pytest.mark.parametrize("platform", ["win32", "darwin"])
    def test_native_dialogs_are_left_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, platform: str
    ) -> None:
        """Windows/macOS use native case-insensitive dialogs that reject classes."""
        monkeypatch.setattr("sys.platform", platform)
        filters = (("Plugins", "*.esp *.esm"), ("All files", "*.*"))
        assert case_insensitive_filetypes(filters) == filters

    def test_a_list_of_filters_is_accepted_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Some call sites pass a list rather than a tuple; both work."""
        monkeypatch.setattr("sys.platform", "linux")
        out = case_insensitive_filetypes([("Plugin", "*.esp"), ("All", "*.*")])
        assert out == (("Plugin", "*.[eE][sS][pP]"), ("All", "*.*"))

    def test_wrapping_twice_does_not_double_the_pattern(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: a filter wrapped twice must equal one wrapped once.

        The reported bug was a filter wrapped in both ``__init__`` and the GUI,
        producing ``*.[[cC]][[fF]]...`` that matched nothing so the file picker
        could not select ``openmw.cfg`` at all.
        """
        monkeypatch.setattr("sys.platform", "linux")
        once = case_insensitive_filetypes((("openmw.cfg", "*.cfg"), ("All files", "*.*")))
        twice = case_insensitive_filetypes(once)
        assert once == (("openmw.cfg", "*.[cC][fF][gG]"), ("All files", "*.*"))
        assert twice == once  # idempotent -- no doubled brackets

    def test_the_openmw_cfg_filter_matches_any_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The exact filter from the screenshot must actually match the file."""
        monkeypatch.setattr("sys.platform", "linux")
        (_label, pattern), _all = case_insensitive_filetypes((("openmw.cfg", "*.cfg"), ("", "*")))
        assert fnmatch.fnmatchcase("openmw.cfg", pattern)
        assert fnmatch.fnmatchcase("OpenMW.CFG", pattern)
