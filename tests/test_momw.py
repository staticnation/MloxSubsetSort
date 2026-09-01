"""Reading MOMW's ``plugin-order.yml``, via PyYAML and via the fallback parser.

The hand-rolled fallback in ``wraithguard.momw`` only runs when PyYAML is absent,
so with PyYAML installed it was never exercised. These parse the same small file
both ways -- forcing the ``ImportError`` for the fallback -- and pin the
curated-list selection, the cleaning set, and the base-order drift check the sort
depends on.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import pytest

from wraithguard.momw import (
    base_order_matches_yml,
    curated_for_list,
    needs_cleaning_set,
    parse_plugin_order_yml,
)

if TYPE_CHECKING:
    from pathlib import Path

_YML = """\
# comment, ignored
- not-a-mapping
- for_mod: orphan with no filename
- file_name: A.esp
  for_mod: Mod A
  needs_cleaning: true
  on_lists:
    - total-overhaul
    - i-heart-vanilla
- file_name: B.esp
  on_lists:
    - total-overhaul
  depends:
    - file_name: nested-should-be-ignored.esp
"""


def _write(tmp_path: Path) -> Path:
    """Write the sample yml and return its path."""
    path = tmp_path / "plugin-order.yml"
    path.write_text(_YML, encoding="utf-8")
    return path


class TestParsing:
    """Both parsers must agree on this file, skipping junk and nested blocks."""

    @staticmethod
    def _check(entries: list[dict[str, Any]]) -> None:
        """The two well-formed entries, whichever parser produced them."""
        assert [e["file_name"] for e in entries] == ["A.esp", "B.esp"]
        first, second = entries
        assert first["for_mod"] == "Mod A"
        assert first["needs_cleaning"] is True
        assert first["on_lists"] == ["total-overhaul", "i-heart-vanilla"]
        assert second["for_mod"] is None
        assert second["needs_cleaning"] is False
        assert second["on_lists"] == ["total-overhaul"]

    def test_the_pyyaml_path(self, tmp_path: Path) -> None:
        """With PyYAML installed, the robust loader is used."""
        pytest.importorskip("yaml")
        self._check(parse_plugin_order_yml(_write(tmp_path)))

    def test_the_fallback_parser_when_pyyaml_is_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Forcing the ImportError exercises the stdlib-only line parser.

        It must agree with PyYAML on this file and ignore the nested ``depends``
        items (their ``- file_name:`` is not a top-level plugin entry).
        """
        monkeypatch.setitem(sys.modules, "yaml", None)
        self._check(parse_plugin_order_yml(_write(tmp_path)))

    def test_the_fallback_ignores_indented_lines_before_any_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stray indented line with no open entry is skipped, yielding nothing.

        With no top-level ``- `` item ever seen, the parser holds no entry to
        fold the line into and finishes with an empty result.
        """
        monkeypatch.setitem(sys.modules, "yaml", None)
        path = tmp_path / "plugin-order.yml"
        path.write_text("  file_name: orphan.esp\n  for_mod: nothing\n", encoding="utf-8")
        assert parse_plugin_order_yml(path) == []

    def test_the_fallback_ignores_unknown_keys_and_blank_list_items(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unrecognised key and an empty ``on_lists`` item are both skipped."""
        monkeypatch.setitem(sys.modules, "yaml", None)
        path = tmp_path / "plugin-order.yml"
        path.write_text(
            '- file_name: a.esp\n  unknown_key: ignored\n  on_lists:\n    - ""\n    - real-list\n',
            encoding="utf-8",
        )
        (entry,) = parse_plugin_order_yml(path)
        assert entry["file_name"] == "a.esp"
        assert entry["on_lists"] == ["real-list"]  # the blank item was dropped


class TestCuratedForList:
    """Selecting one curated list's plugins, in order, case-insensitively."""

    def test_it_selects_a_list_case_insensitively(self, tmp_path: Path) -> None:
        """A different-cased list name still matches; order is file order."""
        found, order = curated_for_list(parse_plugin_order_yml(_write(tmp_path)), "TOTAL-OVERHAUL")
        assert found == {"a.esp", "b.esp"}
        assert order == ["A.esp", "B.esp"]

    def test_an_empty_list_name_selects_nothing(self, tmp_path: Path) -> None:
        """No list named means no curated set -- everything is the user's own."""
        assert curated_for_list(parse_plugin_order_yml(_write(tmp_path)), "") == (set(), [])

    def test_an_unknown_list_selects_nothing(self, tmp_path: Path) -> None:
        """A list no plugin declares yields an empty selection, not an error."""
        assert curated_for_list(parse_plugin_order_yml(_write(tmp_path)), "no-such") == (set(), [])


class TestNeedsCleaning:
    """The set MOMW flags for a tes3cmd clean."""

    def test_only_flagged_plugins_appear(self, tmp_path: Path) -> None:
        """Lowercased, and only where the entry sets needs_cleaning."""
        assert needs_cleaning_set(parse_plugin_order_yml(_write(tmp_path))) == {"a.esp"}


class TestBaseOrderMatchesYml:
    """A read-only drift check of the cfg's curated order against the yml's."""

    def test_a_consistent_order_warns_about_nothing(self) -> None:
        """The user's own mods interleaved between curated ones are ignored."""
        warnings = base_order_matches_yml(["A.esp", "mine.esp", "B.esp"], ["A.esp", "B.esp"])
        assert warnings == []

    def test_a_drifted_order_is_reported_once(self) -> None:
        """A curated plugin out of canonical order raises one clear warning."""
        warnings = base_order_matches_yml(["B.esp", "A.esp"], ["A.esp", "B.esp"])
        assert len(warnings) == 1
        assert "[LIST ORDER]" in warnings[0]
