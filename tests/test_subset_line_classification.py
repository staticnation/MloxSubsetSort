"""extract_groundcover_declarations, _classify_subset_entry, and
_strip_line_comment: the plain-text subset-file line-classification helpers.

All three are pure functions over strings -- no filesystem, no fixtures
needed -- and none had coverage before this file. _strip_line_comment in
particular protects a real regression noted in its own docstring: a filename
like ``FMI_#NotAllDunmer.ESP`` must survive, since a naive split on '#' once
truncated it to ``FMI_`` and dropped it as unclassifiable.
"""

from __future__ import annotations

from typing import Any

import wraithguard_toolkit as core


class TestExtractGroundcoverDeclarations:
    def test_a_groundcover_line_is_returned(self) -> None:
        assert core.extract_groundcover_declarations(["groundcover=Vurt_Grass.esp"]) == [
            "Vurt_Grass.esp"
        ]

    def test_matching_is_case_insensitive_on_the_keyword(self) -> None:
        assert core.extract_groundcover_declarations(["GROUNDCOVER = Vurt_Grass.esp"]) == [
            "Vurt_Grass.esp"
        ]

    def test_a_path_value_is_reduced_to_its_bare_filename(self) -> None:
        result = core.extract_groundcover_declarations(["groundcover=Mods/Vurt/Vurt_Grass.esp"])
        assert result == ["Vurt_Grass.esp"]

    def test_declarations_are_deduped_case_insensitively_keeping_the_first(self) -> None:
        result = core.extract_groundcover_declarations(
            ["groundcover=Vurt_Grass.esp", "groundcover=VURT_GRASS.ESP"]
        )
        assert result == ["Vurt_Grass.esp"]

    def test_multiple_distinct_declarations_are_all_returned_in_order(self) -> None:
        result = core.extract_groundcover_declarations(
            ["groundcover=B_Grass.esp", "groundcover=A_Grass.esp"]
        )
        assert result == ["B_Grass.esp", "A_Grass.esp"]

    def test_a_non_plugin_value_is_skipped(self) -> None:
        assert core.extract_groundcover_declarations(["groundcover=not_a_plugin"]) == []

    def test_a_line_that_is_not_a_groundcover_declaration_is_ignored(self) -> None:
        assert core.extract_groundcover_declarations(["MyMod.esp", "# a comment"]) == []

    def test_a_value_that_is_all_whitespace_names_nothing(self) -> None:
        """The regex can still match on pure whitespace; stripped, there is no name at all."""
        assert core.extract_groundcover_declarations(["groundcover=   "]) == []

    def test_an_empty_input_returns_an_empty_list(self) -> None:
        assert core.extract_groundcover_declarations([]) == []


class TestClassifySubsetEntry:
    def _classify(self, raw: str) -> tuple[list[str], list[dict[str, Any]]]:
        plugins: list[str] = []
        data_inserts: list[dict[str, Any]] = []
        core._classify_subset_entry(raw, plugins, data_inserts, "test-source")
        return plugins, data_inserts

    def test_a_plugin_filename_is_classified_as_a_plugin(self) -> None:
        plugins, data_inserts = self._classify("MyMod.esp")
        assert plugins == ["MyMod.esp"]
        assert data_inserts == []

    def test_a_slash_path_is_classified_as_a_data_insert(self) -> None:
        plugins, data_inserts = self._classify("Mods/MyMod")
        assert plugins == []
        assert data_inserts == [{"value": "Mods/MyMod", "after": None, "before": None}]

    def test_a_backslash_path_is_also_classified_as_a_data_insert(self) -> None:
        _plugins, data_inserts = self._classify("Mods\\MyMod")
        assert data_inserts == [{"value": "Mods\\MyMod", "after": None, "before": None}]

    def test_a_bare_word_with_neither_is_classified_as_neither(self) -> None:
        plugins, data_inserts = self._classify("just_a_word")
        assert plugins == []
        assert data_inserts == []

    def test_a_blank_entry_is_silently_ignored(self) -> None:
        plugins, data_inserts = self._classify("   ")
        assert plugins == []
        assert data_inserts == []

    def test_a_groundcover_declaration_is_not_also_classified_as_a_plugin(self) -> None:
        # extract_groundcover_declarations owns this line; it must not also
        # land in the ordinary plugin list.
        plugins, data_inserts = self._classify("groundcover=Vurt_Grass.esp")
        assert plugins == []
        assert data_inserts == []


class TestStripLineComment:
    def test_a_line_starting_with_hash_becomes_empty(self) -> None:
        assert core._strip_line_comment("# a full-line comment") == ""

    def test_leading_whitespace_before_the_hash_still_counts_as_starting(self) -> None:
        assert core._strip_line_comment("   # indented comment") == ""

    def test_a_hash_preceded_by_whitespace_truncates_the_line(self) -> None:
        assert core._strip_line_comment("MyMod.esp  # trailing note") == "MyMod.esp "

    def test_a_hash_with_no_preceding_space_is_left_alone(self) -> None:
        # The real-world case this guards: a filename that legitimately
        # contains '#' must not be truncated.
        assert core._strip_line_comment("FMI_#NotAllDunmer.ESP") == "FMI_#NotAllDunmer.ESP"

    def test_a_line_with_no_hash_at_all_is_unchanged(self) -> None:
        assert core._strip_line_comment("MyMod.esp") == "MyMod.esp"


class TestExtractSubsetFromSubsetFileTomlFallback:
    def test_a_toml_subset_file_parses_via_tomli_without_the_real_package(
        self, tmp_path, monkeypatch
    ) -> None:
        """Mirrors the same tomllib->tomli ladder pinned in test_configurator_apply.py.

        This is a *different* function with its own copy of the same
        try/except -- not exercised there -- so this sandbox (Python 3.11+,
        no tomli installed) never proves this one's fallback line runs either.
        """
        import sys
        import types

        import pytest

        _real_toml = pytest.importorskip("tomllib")

        monkeypatch.setitem(sys.modules, "tomllib", None)
        monkeypatch.setitem(sys.modules, "tomli", types.SimpleNamespace(loads=_real_toml.loads))
        subset_file = tmp_path / "subset.toml"
        subset_file.write_text('subset = ["MyMod.esp"]\n', encoding="utf-8")

        plugins, _data_inserts = core.extract_subset_from_subset_file(subset_file)

        assert plugins == ["MyMod.esp"]
