"""write_plan: the OUTPUT stage -- writing openmw.cfg and/or a corrected
customizations.toml from a plan compute_plan() already computed.

Built on real plans from compute_plan() (as test_compute_plan_scans.py
does) rather than a hand-constructed plan dict, since write_plan's own
branches -- write_cfg on/off, dry-run, emit_toml on/off, the groundcover
append, and the "manually adjusted" report lines -- are what's actually
untested; the plan's own shape is compute_plan's job to get right, already
covered elsewhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from conftest import write_plugin

import wraithguard_toolkit as core

if TYPE_CHECKING:
    from pathlib import Path


def _cfg_and_rules(tmp_path: Path, data_dir: Path, content: list[str]) -> tuple[Path, Path]:
    cfg = tmp_path / "openmw.cfg"
    lines = [f'data="{data_dir}"'] + [f"content={c}" for c in content]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rules = tmp_path / "mlox_base.txt"
    rules.write_text("", encoding="utf-8")
    return cfg, rules


def _plan(tmp_path: Path, *extra: str) -> tuple[dict, object]:
    """A real, computed plan: one master plus one subset plugin."""
    data_dir = tmp_path / "Data Files"
    data_dir.mkdir()
    write_plugin(data_dir / "Morrowind.esm")
    write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(0,))
    cfg, rules = _cfg_and_rules(tmp_path, data_dir, ["Morrowind.esm"])
    args = core.build_arg_parser().parse_args(
        ["--cfg", str(cfg), "--rules", str(rules), "--subset", "Mine.esp", *extra]
    )
    return core.compute_plan(args), args


class TestWriteCfgNoExistingAnchorLines:
    def test_new_lines_are_appended_when_no_content_lines_existed_at_all(
        self, tmp_path: Path
    ) -> None:
        """A segment with no positions (an anchor kind absent from the file) still lands."""
        cfg = tmp_path / "openmw.cfg"
        cfg.write_text("some=setting\n", encoding="utf-8")

        core.write_cfg(
            cfg,
            ["some=setting"],
            [((), ["content=New.esp"])],
            dry_run=False,
            no_backup=True,
        )

        text = cfg.read_text(encoding="utf-8")
        assert "some=setting" in text
        assert "content=New.esp" in text


class TestWriteCfgFlag:
    def test_without_write_cfg_the_file_is_left_untouched(self, tmp_path: Path, capsys) -> None:
        plan, args = _plan(tmp_path)
        before = args.cfg.read_text(encoding="utf-8")

        result = core.write_plan(args, plan)

        assert result["wrote_cfg"] is False
        assert args.cfg.read_text(encoding="utf-8") == before
        assert "left untouched" in capsys.readouterr().out

    def test_with_write_cfg_the_file_is_updated(self, tmp_path: Path) -> None:
        plan, args = _plan(tmp_path, "--write-cfg")

        result = core.write_plan(args, plan)

        assert result["wrote_cfg"] is True
        assert "content=Mine.esp" in args.cfg.read_text(encoding="utf-8")

    def test_write_cfg_with_dry_run_reports_but_does_not_write(self, tmp_path: Path) -> None:
        plan, args = _plan(tmp_path, "--write-cfg", "--dry-run")
        before = args.cfg.read_text(encoding="utf-8")

        result = core.write_plan(args, plan)

        assert result["wrote_cfg"] is False
        assert args.cfg.read_text(encoding="utf-8") == before


class TestEmitToml:
    def test_emit_toml_writes_a_file_and_is_reported(self, tmp_path: Path) -> None:
        out = tmp_path / "customizations.toml"
        plan, args = _plan(tmp_path, "--emit-toml", str(out))

        result = core.write_plan(args, plan)

        assert result["wrote_toml"] is True
        assert out.exists()
        assert "Mine.esp" in out.read_text(encoding="utf-8")

    def test_a_broken_preview_does_not_block_the_export(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """The preview is read-only advice; its own failure must never stop the real write."""

        def _boom(*_a, **_k):
            raise RuntimeError("simulated: a genuinely broken preview")

        monkeypatch.setattr(core, "preview_configurator_result", _boom)
        out = tmp_path / "customizations.toml"
        plan, args = _plan(tmp_path, "--emit-toml", str(out))

        result = core.write_plan(args, plan)

        assert result["wrote_toml"] is True
        assert out.exists()
        assert "WARNING: configurator preview failed" in capsys.readouterr().out

    def test_emit_toml_with_dry_run_does_not_write_the_file(self, tmp_path: Path) -> None:
        out = tmp_path / "customizations.toml"
        plan, args = _plan(tmp_path, "--emit-toml", str(out), "--dry-run")

        result = core.write_plan(args, plan)

        assert result["wrote_toml"] is False
        assert not out.exists()


class TestNothingWritten:
    def test_neither_flag_prints_the_preview_only_note(self, tmp_path: Path, capsys) -> None:
        plan, args = _plan(tmp_path)

        core.write_plan(args, plan)

        assert "nothing was written" in capsys.readouterr().out

    def test_either_flag_present_suppresses_the_preview_only_note(
        self, tmp_path: Path, capsys
    ) -> None:
        plan, args = _plan(tmp_path, "--write-cfg")

        core.write_plan(args, plan)

        assert "nothing was written" not in capsys.readouterr().out


class TestNewGroundcoverAppending:
    def test_a_new_groundcover_declaration_is_appended_once(self, tmp_path: Path) -> None:
        plan, args = _plan(tmp_path, "--write-cfg")
        plan["new_groundcover"] = ["Vurt_Grass.esp"]

        core.write_plan(args, plan)

        text = args.cfg.read_text(encoding="utf-8")
        assert text.count("groundcover=Vurt_Grass.esp") == 1

    def test_an_already_declared_groundcover_line_is_not_duplicated(self, tmp_path: Path) -> None:
        plan, args = _plan(tmp_path, "--write-cfg")
        plan["lines"] = [*plan["lines"], "groundcover=Vurt_Grass.esp"]
        plan["new_groundcover"] = ["Vurt_Grass.esp"]

        core.write_plan(args, plan)

        text = args.cfg.read_text(encoding="utf-8")
        assert text.count("groundcover=Vurt_Grass.esp") == 1


class TestManualReordering:
    def test_a_final_order_matching_the_plans_own_order_prints_no_adjustment_note(
        self, tmp_path: Path, capsys
    ) -> None:
        plan, args = _plan(tmp_path)

        core.write_plan(args, plan, final_order=list(plan["final_order"]))

        assert "manually adjusted" not in capsys.readouterr().out

    def test_a_different_final_order_is_reported_as_manually_adjusted(
        self, tmp_path: Path, capsys
    ) -> None:
        plan, args = _plan(tmp_path)
        reversed_order = list(reversed(plan["final_order"]))

        core.write_plan(args, plan, final_order=reversed_order)

        assert "manually adjusted" in capsys.readouterr().out


class TestOptedOutRemovals:
    def test_disabling_an_already_present_master_and_data_path_emits_removals(
        self, tmp_path: Path, capsys
    ) -> None:
        """Both plugins_needing_removal and data_paths_needing_removal, in one real plan."""
        plan, args = _plan(tmp_path)
        data_dir = tmp_path / "Data Files"

        core.write_plan(
            args,
            plan,
            disabled_plugins=["Morrowind.esm"],
            disabled_data=[f'data="{data_dir}"'],
        )

        out = capsys.readouterr().out
        assert "removeContent: Morrowind.esm" in out
        assert "removeData:" in out and str(data_dir) in out


class TestManualDataReordering:
    def test_a_different_data_order_is_reported_as_manually_adjusted(
        self, tmp_path: Path, capsys
    ) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        write_plugin(data_dir / "Morrowind.esm")
        mod_dir = tmp_path / "mods" / "MyMod"
        mod_dir.mkdir(parents=True)
        cfg, rules = _cfg_and_rules(tmp_path, data_dir, ["Morrowind.esm"])
        toml = tmp_path / "customizations.toml"
        toml.write_text(
            "[[Customizations]]\n\n"
            "[[Customizations.insert]]\n"
            f'insert = "{mod_dir.as_posix()}"\n'
            'after = "Morrowind.esm"\n',
            encoding="utf-8",
        )
        args = core.build_arg_parser().parse_args(
            [
                "--cfg",
                str(cfg),
                "--rules",
                str(rules),
                "--subset",
                "Morrowind.esm",
                "--customizations",
                str(toml),
                "--sort-data-paths",
            ]
        )
        plan = core.compute_plan(args)
        assert plan["data_result"] is not None
        reversed_data_order = list(reversed([line for line, _, _ in plan["data_result"]]))

        core.write_plan(args, plan, data_order=reversed_data_order)

        assert "data= order being exported (manually adjusted)" in capsys.readouterr().out


class TestSummaryCounts:
    def test_the_summary_reports_the_subset_size(self, tmp_path: Path, capsys) -> None:
        plan, args = _plan(tmp_path)

        core.write_plan(args, plan)

        assert "Plugins sorted:        1" in capsys.readouterr().out


class TestSubsetFromCfgCapturesOrphanData:
    """The point of --subset-from-cfg: unmanaged cfg data= paths land in the
    emitted TOML so a momw-configurator rebuild re-creates them, while paths the
    curated list manages are left out (the list re-adds those itself)."""

    @staticmethod
    def _setup(tmp_path: Path, *extra: str) -> tuple[dict, object, Path]:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        write_plugin(data_dir / "Morrowind.esm")
        write_plugin(data_dir / "Loose.esp", masters=("Morrowind.esm",), sizes=(0,))
        cfg = tmp_path / "openmw.cfg"
        cfg.write_text(
            f'data="{data_dir}"\n'
            "data=C:/Mods/CuratedMod\n"
            "data=C:/Mods/OrphanMod\n"
            "content=Morrowind.esm\n"
            "content=Loose.esp\n",
            encoding="utf-8",
        )
        rules = tmp_path / "mlox_base.txt"
        rules.write_text("", encoding="utf-8")
        cache = tmp_path / "data-paths.txt"
        # the list manages CuratedMod; OrphanMod is not in the cache
        cache.write_text("# list cache\nCuratedMod\n", encoding="utf-8")
        out = tmp_path / "customizations.toml"
        args = core.build_arg_parser().parse_args(
            [
                "--cfg",
                str(cfg),
                "--rules",
                str(rules),
                "--subset-from-cfg",
                "--data-paths-cache",
                str(cache),
                "--emit-toml",
                str(out),
                *extra,
            ]
        )
        return core.compute_plan(args), args, out

    @staticmethod
    def _insert_values(toml: str) -> list[str]:
        """The values on `insert = ...` lines (not `after`/`before` anchors)."""
        out = []
        for line in toml.splitlines():
            stripped = line.strip()
            if stripped.startswith("insert = "):
                out.append(stripped.split("=", 1)[1].strip().strip("'\""))
        return out

    def test_the_orphan_data_path_is_captured_but_the_curated_one_is_not(
        self, tmp_path: Path
    ) -> None:
        plan, args, out = self._setup(tmp_path)
        assert plan["orphan_data_paths"] == ["C:/Mods/OrphanMod"]

        core.write_plan(args, plan)

        toml = out.read_text(encoding="utf-8")
        inserts = self._insert_values(toml)
        # the orphan is inserted; the curated path is only an anchor, never inserted
        # (the list re-adds it on rebuild -- duplicating it would be wrong)
        assert "C:/Mods/OrphanMod" in inserts
        assert "C:/Mods/CuratedMod" not in inserts
        # the orphan plugin is captured too (content orphans already worked)
        assert "Loose.esp" in inserts

    def test_capture_also_works_with_sort_data_paths(self, tmp_path: Path) -> None:
        # The sorted path routes through data_result; the orphan must still land.
        plan, args, out = self._setup(tmp_path, "--sort-data-paths")

        core.write_plan(args, plan)

        inserts = self._insert_values(out.read_text(encoding="utf-8"))
        assert "C:/Mods/OrphanMod" in inserts
        assert "C:/Mods/CuratedMod" not in inserts
