"""declared_groundcover: collecting grass-plugin declarations from three
optional, additive sources. And _read_subset_inputs: the branches of the
subset-reading stage not already pinned indirectly through compute_plan in
test_compute_plan_scans.py -- specifically its two SystemExit guards and the
"found but not sorted" notes printed when data paths turn up without
--sort-data-paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import wraithguard_toolkit as core

if TYPE_CHECKING:
    from pathlib import Path


def _cfg_and_rules(tmp_path: Path) -> tuple[Path, Path]:
    cfg = tmp_path / "openmw.cfg"
    cfg.write_text('data="Data Files"\ncontent=Morrowind.esm\n', encoding="utf-8")
    rules = tmp_path / "mlox_base.txt"
    rules.write_text("", encoding="utf-8")
    return cfg, rules


def _args(tmp_path: Path, *extra: str):
    cfg, rules = _cfg_and_rules(tmp_path)
    return core.build_arg_parser().parse_args(["--cfg", str(cfg), "--rules", str(rules), *extra])


class TestDeclaredGroundcover:
    def test_a_command_line_declaration_is_returned(self, tmp_path: Path) -> None:
        args = _args(tmp_path, "--groundcover", "Vurt_Grass.esp")
        assert core.declared_groundcover(args) == ["Vurt_Grass.esp"]

    def test_multiple_command_line_declarations_keep_their_order(self, tmp_path: Path) -> None:
        args = _args(tmp_path, "--groundcover", "B_Grass.esp", "A_Grass.esp")
        assert core.declared_groundcover(args) == ["B_Grass.esp", "A_Grass.esp"]

    def test_a_plain_text_subset_files_groundcover_line_is_included(self, tmp_path: Path) -> None:
        subset_file = tmp_path / "subset.txt"
        subset_file.write_text("MyMod.esp\ngroundcover=Vurt_Grass.esp\n", encoding="utf-8")
        args = _args(tmp_path, "--subset-file", str(subset_file))
        assert core.declared_groundcover(args) == ["Vurt_Grass.esp"]

    def test_a_toml_subset_files_groundcover_key_is_included(self, tmp_path: Path) -> None:
        subset_file = tmp_path / "subset.toml"
        subset_file.write_text(
            'subset = ["MyMod.esp"]\ngroundcover = ["Vurt_Grass.esp"]\n', encoding="utf-8"
        )
        args = _args(tmp_path, "--subset-file", str(subset_file))
        assert core.declared_groundcover(args) == ["Vurt_Grass.esp"]

    def test_in_memory_subset_lines_are_read_the_same_way_as_a_file(self, tmp_path: Path) -> None:
        args = _args(tmp_path)
        args.subset_lines = ["groundcover=Vurt_Grass.esp"]
        assert core.declared_groundcover(args) == ["Vurt_Grass.esp"]

    def test_declarations_from_every_source_are_combined_and_deduped(self, tmp_path: Path) -> None:
        subset_file = tmp_path / "subset.txt"
        subset_file.write_text("groundcover=CLI_Grass.esp\n", encoding="utf-8")
        args = _args(tmp_path, "--groundcover", "CLI_Grass.esp", "--subset-file", str(subset_file))
        args.subset_lines = ["groundcover=Mem_Grass.esp"]

        result = core.declared_groundcover(args)

        assert result == ["CLI_Grass.esp", "Mem_Grass.esp"]  # CLI's copy wins the dedupe

    def test_no_declarations_anywhere_returns_an_empty_list(self, tmp_path: Path) -> None:
        args = _args(tmp_path)
        assert core.declared_groundcover(args) == []

    def test_an_unreadable_subset_file_does_not_crash_this_check(self, tmp_path: Path) -> None:
        # The subset reader itself reports this properly elsewhere; this
        # function just shouldn't blow up gathering groundcover names.
        args = _args(tmp_path, "--subset-file", str(tmp_path / "does-not-exist.txt"))
        assert core.declared_groundcover(args) == []

    def test_a_malformed_toml_groundcover_section_is_silently_ignored(self, tmp_path: Path) -> None:
        """The subset reader reports the malformed TOML properly; this just must not also crash."""
        subset_file = tmp_path / "subset.toml"
        subset_file.write_text("not [ valid toml", encoding="utf-8")
        args = _args(tmp_path, "--subset-file", str(subset_file))
        assert core.declared_groundcover(args) == []

    def test_a_toml_groundcover_key_parses_via_tomli_without_the_real_package(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fourth copy of the same tomllib->tomli ladder, in this function specifically."""
        import sys
        import types

        import tomllib as _real_toml

        monkeypatch.setitem(sys.modules, "tomllib", None)
        monkeypatch.setitem(sys.modules, "tomli", types.SimpleNamespace(loads=_real_toml.loads))
        subset_file = tmp_path / "subset.toml"
        subset_file.write_text('groundcover = ["Vurt_Grass.esp"]\n', encoding="utf-8")
        args = _args(tmp_path, "--subset-file", str(subset_file))

        assert core.declared_groundcover(args) == ["Vurt_Grass.esp"]


class TestReadSubsetInputsGuards:
    def test_scan_dir_without_subset_file_exits_with_a_clear_message(self, tmp_path: Path) -> None:
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        args = _args(tmp_path, "--scan-dir", str(mods_dir))

        with pytest.raises(SystemExit, match="requires --subset-file"):
            core._read_subset_inputs(args)

    def test_scan_dir_with_subset_file_runs_the_scan(self, tmp_path: Path) -> None:
        """The success path this guard exists to gate: a real scan actually runs."""
        mods_dir = tmp_path / "mods"
        (mods_dir / "MyMod").mkdir(parents=True)
        (mods_dir / "MyMod" / "MyMod.esp").write_bytes(b"\x00")
        subset_file = tmp_path / "scanned.txt"
        args = _args(tmp_path, "--scan-dir", str(mods_dir), "--subset-file", str(subset_file))

        core._read_subset_inputs(args)

        assert subset_file.is_file()
        assert "MyMod.esp" in subset_file.read_text(encoding="utf-8")

    def test_no_input_source_at_all_exits_with_a_clear_message(self, tmp_path: Path) -> None:
        args = _args(tmp_path)

        with pytest.raises(SystemExit, match="Provide --customizations"):
            core._read_subset_inputs(args)

    def test_a_subset_file_with_only_unusable_lines_exits_nothing_to_do(
        self, tmp_path: Path
    ) -> None:
        subset_file = tmp_path / "subset.txt"
        subset_file.write_text("just_a_bare_word_no_extension_no_slash\n", encoding="utf-8")
        args = _args(tmp_path, "--subset-file", str(subset_file))

        with pytest.raises(SystemExit, match="No subset plugins or data paths found"):
            core._read_subset_inputs(args)


class TestReadSubsetInputsDataPathNotes:
    def test_a_subset_file_data_path_without_sort_flag_is_noted_not_included(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        subset_file = tmp_path / "subset.txt"
        subset_file.write_text("MyMod.esp\nmods/MyModFolder\n", encoding="utf-8")
        args = _args(tmp_path, "--subset-file", str(subset_file))

        _subset, data_inserts, *_rest = core._read_subset_inputs(args)

        assert data_inserts == []
        assert "found but not sorted" in capsys.readouterr().out

    def test_the_same_data_path_with_sort_flag_is_included_and_not_noted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        subset_file = tmp_path / "subset.txt"
        subset_file.write_text("MyMod.esp\nmods/MyModFolder\n", encoding="utf-8")
        args = _args(tmp_path, "--subset-file", str(subset_file), "--sort-data-paths")

        _subset, data_inserts, *_rest = core._read_subset_inputs(args)

        assert any(d["value"] == "mods/MyModFolder" for d in data_inserts)
        assert "found but not sorted" not in capsys.readouterr().out

    def test_a_single_in_memory_data_path_uses_the_singular_note(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = _args(tmp_path)
        args.subset_lines = ["MyMod.esp", "mods/MyModFolder"]

        core._read_subset_inputs(args)

        out = capsys.readouterr().out
        assert "1 path" in out and "1 paths" not in out

    def test_a_customizations_data_path_without_sort_flag_is_noted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        toml = tmp_path / "customizations.toml"
        toml.write_text(
            "[[Customizations]]\n\n"
            "[[Customizations.insert]]\n"
            'insert = "mods/MyModFolder"\n'
            'after = "Morrowind.esm"\n',
            encoding="utf-8",
        )
        args = _args(tmp_path, "--customizations", str(toml))

        _subset, data_inserts, *_rest = core._read_subset_inputs(args)

        assert data_inserts == []
        assert "found but not sorted" in capsys.readouterr().out

    def test_emit_toml_parses_via_tomli_without_the_real_package(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fifth copy of the same tomllib->tomli ladder, gated behind --emit-toml."""
        import sys
        import types

        import tomllib as _real_toml

        monkeypatch.setitem(sys.modules, "tomllib", None)
        monkeypatch.setitem(sys.modules, "tomli", types.SimpleNamespace(loads=_real_toml.loads))
        toml = tmp_path / "customizations2.toml"
        toml.write_text(
            '[[Customizations]]\n\n[[Customizations.insert]]\ninsert = "MyMod.esp"\n',
            encoding="utf-8",
        )
        args = _args(
            tmp_path, "--customizations", str(toml), "--emit-toml", str(tmp_path / "out.toml")
        )

        result = core._read_subset_inputs(args)

        assert result[4] == {"Customizations": [{"insert": [{"insert": "MyMod.esp"}]}]}


class TestReadSubsetInputsOrigins:
    def test_subset_file_plugins_are_tagged_with_the_files_name(self, tmp_path: Path) -> None:
        subset_file = tmp_path / "subset.txt"
        subset_file.write_text("MyMod.esp\n", encoding="utf-8")
        args = _args(tmp_path, "--subset-file", str(subset_file))

        *_rest, subset_origins = core._read_subset_inputs(args)

        assert "subset.txt" in subset_origins["mymod.esp"]

    def test_customizations_plugins_are_tagged_with_the_list_name(self, tmp_path: Path) -> None:
        toml = tmp_path / "customizations.toml"
        toml.write_text(
            "[[Customizations]]\n"
            'listName = "MyList"\n\n'
            "[[Customizations.insert]]\n"
            'insert = "MyMod.esp"\n',
            encoding="utf-8",
        )
        args = _args(tmp_path, "--customizations", str(toml))

        *_rest, subset_origins = core._read_subset_inputs(args)

        assert subset_origins["mymod.esp"] == "customizations.toml -> 'MyList'"
