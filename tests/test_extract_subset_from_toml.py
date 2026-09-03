"""extract_subset_from_toml: reading a momw-customizations.toml back apart.

test_hardening.py covers the one defensive branch (non-UTF-8 input). Every
other branch -- single vs. block inserts, data-folder paths, replace/append
routing, listName tracking, and the case-insensitive de-dupe that preserves
declaration order -- had no coverage before this file.

TOML fixtures use the real array-of-tables shape (``[[Customizations.insert]]``
etc.), matching exactly what generate_customizations_toml() itself emits --
confirmed by generating a sample and reading its shape back, rather than
guessing at the schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import wraithguard_toolkit as core

if TYPE_CHECKING:
    from pathlib import Path


def _toml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "customizations.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestBlankOrNonStringInsertValues:
    def test_an_empty_string_insert_value_names_nothing(self, tmp_path: Path) -> None:
        path = _toml(
            tmp_path,
            '[[Customizations]]\nlistName = "L"\n[[Customizations.insert]]\ninsert = ""\n',
        )
        subset, data_inserts, _replace, _listnames = core.extract_subset_from_toml(path)
        assert subset == []
        assert data_inserts == []

    def test_a_non_string_insert_value_is_ignored_not_raised(self, tmp_path: Path) -> None:
        """A TOML author's mistake (a number, a table) must not crash the read."""
        path = _toml(
            tmp_path,
            '[[Customizations]]\nlistName = "L"\n[[Customizations.insert]]\ninsert = 42\n',
        )
        subset, data_inserts, _replace, _listnames = core.extract_subset_from_toml(path)
        assert subset == []
        assert data_inserts == []


class TestNoTomlLibraryAvailable:
    def test_neither_tomllib_nor_tomli_exits_with_a_clear_message(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import sys

        monkeypatch.setitem(sys.modules, "tomllib", None)
        monkeypatch.setitem(sys.modules, "tomli", None)
        path = _toml(tmp_path, '[[Customizations]]\nlistName = "L"\n')

        with pytest.raises(SystemExit, match="Need Python 3"):
            core.extract_subset_from_toml(path)


class TestSingleInsert:
    def test_a_plugin_path_becomes_its_bare_filename(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n"
            'listName = "MyMod"\n\n'
            "[[Customizations.insert]]\n"
            'insert = "Mods/MyMod/MyMod.esp"\n',
        )

        subset, data_inserts, replace_dest, listnames = core.extract_subset_from_toml(doc)

        assert subset == ["MyMod.esp"]
        assert data_inserts == []
        assert replace_dest == set()
        assert listnames == {"MyMod.esp": "MyMod"}

    def test_after_and_before_are_ignored_for_a_plugin_insert(self, tmp_path: Path) -> None:
        # after/before only matter for data-path inserts; a plugin's position
        # comes from the mlox sort, not this anchor.
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n\n"
            "[[Customizations.insert]]\n"
            'insert = "MyMod.esp"\n'
            'after = "Morrowind.esm"\n',
        )

        subset, _data, _replace, _names = core.extract_subset_from_toml(doc)

        assert subset == ["MyMod.esp"]

    def test_a_data_folder_path_is_recorded_with_its_anchor(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n\n"
            "[[Customizations.insert]]\n"
            'insert = "Mods/MyMod"\n'
            'after = "Morrowind.esm"\n',
        )

        subset, data_inserts, _replace, _names = core.extract_subset_from_toml(doc)

        assert subset == []
        assert data_inserts == [{"value": "Mods/MyMod", "after": "Morrowind.esm", "before": None}]

    def test_a_bare_word_with_no_slash_or_plugin_extension_is_dropped(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            '[[Customizations]]\n\n[[Customizations.insert]]\ninsert = "just_a_word"\n',
        )

        subset, data_inserts, _replace, _names = core.extract_subset_from_toml(doc)

        assert subset == []
        assert data_inserts == []

    def test_backslash_paths_are_recognized_as_data_paths_too(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            '[[Customizations]]\n\n[[Customizations.insert]]\ninsert = "Mods\\\\MyMod"\n',
        )

        _subset, data_inserts, _replace, _names = core.extract_subset_from_toml(doc)

        assert data_inserts == [{"value": "Mods\\MyMod", "after": None, "before": None}]


class TestInsertBlock:
    def test_each_line_of_a_block_is_a_separate_entry(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n"
            'listName = "Bundle"\n\n'
            "[[Customizations.insert]]\n"
            'insertBlock = """\n'
            "One.esp\n"
            "Two.esp\n"
            '"""\n',
        )

        subset, _data, _replace, listnames = core.extract_subset_from_toml(doc)

        assert subset == ["One.esp", "Two.esp"]
        assert listnames == {"One.esp": "Bundle", "Two.esp": "Bundle"}

    def test_blank_lines_in_a_block_are_skipped(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n\n"
            "[[Customizations.insert]]\n"
            'insertBlock = """\n'
            "One.esp\n"
            "\n"
            "Two.esp\n"
            '"""\n',
        )

        subset, _data, _replace, _names = core.extract_subset_from_toml(doc)

        assert subset == ["One.esp", "Two.esp"]


class TestReplace:
    def test_replace_dest_lands_in_subset_and_replace_dest_names(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n\n"
            "[[Customizations.replace]]\n"
            'source = "Old.esp"\n'
            'dest = "New.esp"\n',
        )

        subset, _data, replace_dest, _names = core.extract_subset_from_toml(doc)

        assert subset == ["New.esp"]
        assert replace_dest == {"New.esp"}

    def test_replace_source_becomes_the_before_anchor(self, tmp_path: Path) -> None:
        # replace has no after/before of its own -- it's fed into the mlox
        # sort anchored at the position of what it replaces, purely to detect
        # drift; generate_customizations_toml skips it via replace_dest_names.
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n\n"
            "[[Customizations.replace]]\n"
            'source = "Old.esp"\n'
            'dest = "Mods/New"\n',
        )

        _subset, data_inserts, _replace, _names = core.extract_subset_from_toml(doc)

        assert data_inserts == [{"value": "Mods/New", "after": None, "before": "Old.esp"}]


class TestAppend:
    def test_a_content_line_in_an_append_block_is_picked_up(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n"
            'listName = "Appended"\n\n'
            "[[Customizations.append]]\n"
            'appendBlock = """\n'
            "content=Extra.esp\n"
            '"""\n',
        )

        subset, _data, _replace, listnames = core.extract_subset_from_toml(doc)

        assert subset == ["Extra.esp"]
        assert listnames == {"Extra.esp": "Appended"}

    def test_the_single_line_append_key_works_the_same_way(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n\n" "[[Customizations.append]]\n" 'append = "content=Extra.esp"\n',
        )

        subset, _data, _replace, _names = core.extract_subset_from_toml(doc)

        assert subset == ["Extra.esp"]

    def test_non_content_lines_in_an_append_block_are_ignored(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n\n"
            "[[Customizations.append]]\n"
            'appendBlock = """\n'
            "fallback-archive=Extra.bsa\n"
            '"""\n',
        )

        subset, _data, _replace, _names = core.extract_subset_from_toml(doc)

        assert subset == []


class TestDedupeAndOrdering:
    def test_a_plugin_listed_twice_case_insensitively_keeps_the_first(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n\n"
            "[[Customizations.insert]]\n"
            'insertBlock = """\n'
            "MyMod.esp\n"
            "MYMOD.ESP\n"
            "OtherMod.esp\n"
            '"""\n',
        )

        subset, _data, _replace, _names = core.extract_subset_from_toml(doc)

        assert subset == ["MyMod.esp", "OtherMod.esp"]

    def test_declaration_order_is_preserved_not_alphabetized(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n\n"
            "[[Customizations.insert]]\n"
            'insertBlock = """\n'
            "Zebra.esp\n"
            "Alpha.esp\n"
            "Mike.esp\n"
            '"""\n',
        )

        subset, _data, _replace, _names = core.extract_subset_from_toml(doc)

        assert subset == ["Zebra.esp", "Alpha.esp", "Mike.esp"]

    def test_multiple_customizations_blocks_are_all_processed(self, tmp_path: Path) -> None:
        doc = _toml(
            tmp_path,
            "[[Customizations]]\n"
            'listName = "First"\n\n'
            "[[Customizations.insert]]\n"
            'insert = "One.esp"\n\n'
            "[[Customizations]]\n"
            'listName = "Second"\n\n'
            "[[Customizations.insert]]\n"
            'insert = "Two.esp"\n',
        )

        subset, _data, _replace, listnames = core.extract_subset_from_toml(doc)

        assert subset == ["One.esp", "Two.esp"]
        assert listnames == {"One.esp": "First", "Two.esp": "Second"}
