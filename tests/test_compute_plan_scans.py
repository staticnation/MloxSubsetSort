"""compute_plan's less-common subset-input branches, plus the opt-in
--check-conflicts / --cell-map scan stage (_conflict_and_cellmap_scans).

All were previously untested: args.subset_lines (the GUI's "scan into
memory" path -- never a real CLI flag, set directly on the Namespace the
same way the GUI does), the --customizations TOML path threaded all the way
through compute_plan, --exclude filtering, --conflicts-out CSV writing, and
--cell-map's HTML output with real exterior/interior cell touches.

find_tes3conv is monkeypatched to None throughout so these run the same way
on any machine, real tes3conv on PATH or not -- the engine-selection branch
itself (builtin vs. tes3conv) is exercised directly in
test_conflict_detection.py instead.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import pytest
from conftest import interior_cell, rec, static_record, sub, write_plugin, zstr

import wraithguard_toolkit as core

if TYPE_CHECKING:
    from pathlib import Path


def _exterior_cell(gx: int, gy: int) -> bytes:
    """An exterior CELL record at grid (gx, gy) -- flags=0, no interior bit."""
    return rec("CELL", sub("NAME", zstr("")) + sub("DATA", struct.pack("<iii", 0, gx, gy)))


@pytest.fixture(autouse=True)
def _force_builtin_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these deterministic regardless of whether tes3conv is on PATH."""
    monkeypatch.setattr(core, "find_tes3conv", lambda *a, **k: None)


def _cfg_and_rules(tmp_path: Path, data_dir: Path, content: list[str]) -> tuple[Path, Path]:
    cfg = tmp_path / "openmw.cfg"
    lines = [f'data="{data_dir}"'] + [f"content={c}" for c in content]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rules = tmp_path / "mlox_base.txt"
    rules.write_text("", encoding="utf-8")
    return cfg, rules


def _args(cfg: Path, rules: Path, *extra: str):
    return core.build_arg_parser().parse_args(["--cfg", str(cfg), "--rules", str(rules), *extra])


def _args_with_empty_subset(tmp_path: Path, cfg: Path, rules: Path, *extra: str):
    """Like _args, but satisfies "give me *something* to sort".

    For tests targeting the conflict/cell-map scan stage, which runs over
    the whole active (base) list regardless of subset content -- a subset of
    exactly one harmless, unshared dummy plugin, written to the data dir but
    left out of the cfg's content= lines, so it never touches the fixtures
    the test actually cares about.
    """
    data_dir = cfg.parent / "Data Files"
    dummy = data_dir / "ZZZDummySubset.esp"
    if not dummy.exists():
        write_plugin(dummy)
    subset_file = tmp_path / "subset.txt"
    if not subset_file.exists():
        subset_file.write_text("ZZZDummySubset.esp\n", encoding="utf-8")
    return _args(cfg, rules, "--subset-file", str(subset_file), *extra)


class TestSubsetLines:
    """args.subset_lines: the GUI's in-memory 'scan into memory' path."""

    def test_a_plugin_line_is_picked_up_as_subset(self, tmp_path: Path) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        write_plugin(data / "Mine.esp", masters=("Morrowind.esm",), sizes=(0,))
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm"])
        args = _args(cfg, rules)
        args.subset_lines = ["Mine.esp"]

        plan = core.compute_plan(args)

        assert "Mine.esp" in plan["subset"]

    def test_a_data_path_line_is_only_kept_when_sort_data_paths_is_on(self, tmp_path: Path) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        mod_dir = tmp_path / "mods" / "MyMod"
        mod_dir.mkdir(parents=True)
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm"])

        without_sort = _args(cfg, rules)
        without_sort.subset_lines = [str(mod_dir)]
        plan_without = core.compute_plan(without_sort)
        assert plan_without["data_inserts"] == []

        with_sort = _args(cfg, rules, "--sort-data-paths")
        with_sort.subset_lines = [str(mod_dir)]
        plan_with = core.compute_plan(with_sort)
        assert any(d["value"] == str(mod_dir) for d in plan_with["data_inserts"])


class TestPredicateWarnings:
    def _plan(self, tmp_path: Path, rule_text: str, *extra: str) -> dict:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        write_plugin(data / "Mine.esp", masters=("Morrowind.esm",), sizes=(0,))
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm"])
        rules.write_text(rule_text, encoding="utf-8")
        args = _args(cfg, rules, *extra)
        args.subset_lines = ["Mine.esp"]
        return core.compute_plan(args)

    def test_a_triggered_note_is_reported(self, tmp_path: Path) -> None:
        plan = self._plan(tmp_path, "[Note a triggered note]\nMine.esp\n")
        assert len(plan["predicate_warnings"]) == 1
        assert "a triggered note" in plan["predicate_warnings"][0]

    def test_no_predicate_warnings_flag_skips_the_check_entirely(self, tmp_path: Path) -> None:
        """--no-predicate-warnings must not even run check_predicates."""
        plan = self._plan(
            tmp_path, "[Note a triggered note]\nMine.esp\n", "--no-predicate-warnings"
        )
        assert plan["predicate_warnings"] == []


class TestHeaderMasterReadIsAdvisoryOnly:
    def test_an_unexpected_read_failure_degrades_to_no_masters_known(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """read_plugin_masters is meant to never raise; this proves the wiring survives it anyway.

        The header read here is explicitly "advisory only" (see its own
        comment) precisely because it runs over arbitrary third-party binary
        files -- so the guard is proven by simulating a genuinely broken
        reader, not by trying to construct bytes that defeat a hardened
        parser no fixture here has managed to break.
        """
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        write_plugin(data / "Mine.esp", masters=("Morrowind.esm",), sizes=(0,))
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm"])
        args = _args(cfg, rules)
        args.subset_lines = ["Mine.esp"]

        def _boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("simulated: a genuinely broken plugin reader")

        monkeypatch.setattr(core, "read_plugin_masters", _boom)

        plan = core.compute_plan(args)  # must not raise

        assert plan["final_order"] is not None
        assert "Mine.esp" in plan["final_order"]

    def test_a_frozen_order_drift_prints_the_internal_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """build_and_sort's own contract (curated order is never touched) is pinned in
        test_sort_engine.py; this pins that compute_plan notices and reports a violation
        rather than silently shipping a corrupted order, by simulating one directly."""
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        write_plugin(data / "A.esp")
        write_plugin(data / "B.esp")
        write_plugin(data / "Mine.esp", masters=("Morrowind.esm",), sizes=(0,))
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm", "A.esp", "B.esp"])
        args = _args(cfg, rules)
        args.subset_lines = ["Mine.esp"]

        def _reversed_curated_order(
            base_order_names: list[str], subset: list[str], *_a: object, **_k: object
        ) -> list[str]:
            frozen = [n for n in base_order_names if n.lower() not in {s.lower() for s in subset}]
            return [*subset, *reversed(frozen)]

        monkeypatch.setattr(core, "build_and_sort", _reversed_curated_order)

        core.compute_plan(args)

        assert "INTERNAL WARNING: curated (frozen) order drifted" in capsys.readouterr().out


class TestCustomizationsPath:
    """--customizations threaded all the way through compute_plan."""

    def test_an_insert_from_the_toml_lands_in_the_final_subset(self, tmp_path: Path) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        write_plugin(data / "Mine.esp", masters=("Morrowind.esm",), sizes=(0,))
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm"])
        toml = tmp_path / "customizations.toml"
        toml.write_text(
            "[[Customizations]]\n"
            'listName = "Mine"\n\n'
            "[[Customizations.insert]]\n"
            'insert = "Mine.esp"\n'
            'after = "Morrowind.esm"\n',
            encoding="utf-8",
        )

        args = _args(cfg, rules, "--customizations", str(toml))
        plan = core.compute_plan(args)

        assert "Mine.esp" in plan["subset"]
        assert "Mine.esp" in plan["final_order"]

    def test_a_toml_data_path_insert_requires_sort_data_paths_too(self, tmp_path: Path) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm"])
        toml = tmp_path / "customizations.toml"
        toml.write_text(
            "[[Customizations]]\n\n"
            "[[Customizations.insert]]\n"
            'insert = "mods/MyMod"\n'
            'after = "Morrowind.esm"\n',
            encoding="utf-8",
        )

        args = _args(cfg, rules, "--customizations", str(toml), "--sort-data-paths")
        plan = core.compute_plan(args)

        assert any(d["value"] == "mods/MyMod" for d in plan["data_inserts"])


class TestCheckConflictsScan:
    """--check-conflicts, --exclude, and --conflicts-out via compute_plan."""

    def test_a_conflict_is_found_and_returned_on_the_plan(self, tmp_path: Path) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        write_plugin(
            data / "A.esp", extra=static_record("torch_01"), masters=("Morrowind.esm",), sizes=(0,)
        )
        write_plugin(
            data / "B.esp", extra=static_record("torch_01"), masters=("Morrowind.esm",), sizes=(0,)
        )
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm", "A.esp", "B.esp"])

        args = _args_with_empty_subset(tmp_path, cfg, rules, "--check-conflicts")
        plan = core.compute_plan(args)

        assert len(plan["conflicts"]) == 1
        assert plan["conflicts"][0]["id"] == "torch_01"

    def test_exclude_drops_a_plugin_from_the_scan_entirely(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        write_plugin(
            data / "A.esp", extra=static_record("torch_01"), masters=("Morrowind.esm",), sizes=(0,)
        )
        write_plugin(
            data / "B.esp", extra=static_record("torch_01"), masters=("Morrowind.esm",), sizes=(0,)
        )
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm", "A.esp", "B.esp"])

        args = _args_with_empty_subset(
            tmp_path, cfg, rules, "--check-conflicts", "--exclude", "B.esp"
        )
        plan = core.compute_plan(args)

        assert plan["conflicts"] == []
        assert "excluded 1 plugin" in capsys.readouterr().out

    def test_conflicts_out_writes_a_csv(self, tmp_path: Path) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        write_plugin(
            data / "A.esp", extra=static_record("torch_01"), masters=("Morrowind.esm",), sizes=(0,)
        )
        write_plugin(
            data / "B.esp", extra=static_record("torch_01"), masters=("Morrowind.esm",), sizes=(0,)
        )
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm", "A.esp", "B.esp"])
        out = tmp_path / "conflicts.csv"

        args = _args_with_empty_subset(
            tmp_path, cfg, rules, "--check-conflicts", "--conflicts-out", str(out)
        )
        core.compute_plan(args)

        assert out.exists()
        assert "torch_01" in out.read_text(encoding="utf-8")

    def test_conflicts_out_is_not_written_when_there_are_no_conflicts(self, tmp_path: Path) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm"])
        out = tmp_path / "conflicts.csv"

        args = _args_with_empty_subset(
            tmp_path, cfg, rules, "--check-conflicts", "--conflicts-out", str(out)
        )
        core.compute_plan(args)

        assert not out.exists()

    def test_conflicts_subset_only_still_returns_every_conflict_on_the_plan(
        self, tmp_path: Path
    ) -> None:
        # subset_only only filters the printed report; the plan's own
        # conflicts list is unfiltered, since the GUI re-filters it itself.
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        write_plugin(
            data / "A.esp", extra=static_record("torch_01"), masters=("Morrowind.esm",), sizes=(0,)
        )
        write_plugin(
            data / "B.esp", extra=static_record("torch_01"), masters=("Morrowind.esm",), sizes=(0,)
        )
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm", "A.esp", "B.esp"])

        args = _args_with_empty_subset(
            tmp_path, cfg, rules, "--check-conflicts", "--conflicts-subset-only"
        )
        plan = core.compute_plan(args)

        assert len(plan["conflicts"]) == 1

    def test_neither_flag_given_means_no_scan_runs_at_all(self, tmp_path: Path) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm"])

        args = _args_with_empty_subset(tmp_path, cfg, rules)
        plan = core.compute_plan(args)

        assert plan["conflicts"] == []


class TestCellMapScan:
    """--cell-map: real exterior/interior cell touches, written out as HTML."""

    def test_exterior_and_interior_touches_are_both_counted(self, tmp_path: Path) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        write_plugin(
            data / "A.esp",
            extra=_exterior_cell(3, -2) + interior_cell("Balmora, Guild", fog=0.5),
            masters=("Morrowind.esm",),
            sizes=(0,),
        )
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm", "A.esp"])
        out = tmp_path / "cellmap.html"

        args = _args_with_empty_subset(tmp_path, cfg, rules, "--cell-map", str(out))
        core.compute_plan(args)

        assert out.exists()
        html = out.read_text(encoding="utf-8")
        assert "<html" in html.lower()
        assert len(html) > 0

    def test_a_plugin_missing_from_the_index_is_unreadable_not_fatal(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm", "Ghost.esp"])
        out = tmp_path / "cellmap.html"

        args = _args_with_empty_subset(tmp_path, cfg, rules, "--cell-map", str(out))
        plan = core.compute_plan(args)  # must not raise

        assert out.exists()
        assert plan is not None

    def test_check_conflicts_and_cell_map_share_one_session_in_one_pass(
        self, tmp_path: Path
    ) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        write_plugin(
            data / "A.esp",
            extra=static_record("torch_01") + _exterior_cell(1, 1),
            masters=("Morrowind.esm",),
            sizes=(0,),
        )
        write_plugin(
            data / "B.esp",
            extra=static_record("torch_01"),
            masters=("Morrowind.esm",),
            sizes=(0,),
        )
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm", "A.esp", "B.esp"])
        out = tmp_path / "cellmap.html"

        args = _args_with_empty_subset(
            tmp_path, cfg, rules, "--check-conflicts", "--cell-map", str(out)
        )
        plan = core.compute_plan(args)

        assert len(plan["conflicts"]) == 1
        assert out.exists()

    def test_json_dump_dir_prints_where_it_kept_the_dump(self, tmp_path: Path) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm"])
        dump_dir = tmp_path / "dump"

        args = _args_with_empty_subset(
            tmp_path, cfg, rules, "--check-conflicts", "--json-dump-dir", str(dump_dir)
        )
        import io
        from contextlib import redirect_stdout

        out = io.StringIO()
        with redirect_stdout(out):
            core.compute_plan(args)

        assert "Keeping tes3conv JSON dump in" in out.getvalue()

    def test_a_conflicts_csv_write_failure_is_logged_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        write_plugin(
            data / "A.esp", extra=static_record("torch_01"), masters=("Morrowind.esm",), sizes=(0,)
        )
        write_plugin(
            data / "B.esp", extra=static_record("torch_01"), masters=("Morrowind.esm",), sizes=(0,)
        )
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm", "A.esp", "B.esp"])

        def _boom(*_a: object, **_k: object) -> None:
            raise OSError("simulated: disk full")

        monkeypatch.setattr(core, "write_conflict_csv", _boom)
        args = _args_with_empty_subset(
            tmp_path, cfg, rules, "--check-conflicts", "--conflicts-out", str(tmp_path / "out.csv")
        )

        core.compute_plan(args)  # must not raise

    def test_a_cell_map_write_failure_is_logged_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "Morrowind.esm")
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm"])

        def _boom(*_a: object, **_k: object) -> None:
            raise OSError("simulated: disk full")

        monkeypatch.setattr(core, "generate_cell_map_html", _boom)
        args = _args_with_empty_subset(
            tmp_path, cfg, rules, "--cell-map", str(tmp_path / "cellmap.html")
        )

        core.compute_plan(args)  # must not raise


class TestAllowEmptySort:
    """An empty sort returns the current order for editing (the GUI opt-out).

    Clicking Sort with nothing to sort used to abort with "nothing to do", so the
    order panel had nothing to load. With allow_empty_sort the run proceeds and
    hands back the cfg's own order (final_order stays None), which the GUI loads
    so the user can prune it and Export.
    """

    def test_empty_subset_with_the_flag_returns_the_base_order(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm", "Tribunal.esm", "MyMod.esp"])
        args = _args(cfg, rules)
        args.allow_empty_sort = True

        plan = core.compute_plan(args)  # must not raise

        assert plan["final_order"] is None
        assert plan["base_order_names"] == ["Morrowind.esm", "Tribunal.esm", "MyMod.esp"]
        assert "NOTHING TO SORT" in capsys.readouterr().out

    def test_without_the_flag_an_empty_run_is_still_refused(self, tmp_path: Path) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        cfg, rules = _cfg_and_rules(tmp_path, data, ["Morrowind.esm"])
        with pytest.raises(SystemExit, match="something to sort"):
            core.compute_plan(_args(cfg, rules))
