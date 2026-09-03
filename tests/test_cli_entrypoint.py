"""The CLI entry point: run_from_args and main.

Neither had any coverage at all -- every other test calls compute_plan and
write_plan directly, or exercises pieces of the pipeline in isolation. This
pins the thin wiring on top: that run_from_args is exactly compute_plan +
write_plan back to back with a summary dict, and that main() wires logging
verbosity and an optional trace file before handing off to it.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

import wraithguard_toolkit as wt

if TYPE_CHECKING:
    import pytest


class TestRunFromArgs:
    """compute_plan() + write_plan() back to back, summarised for callers."""

    def test_the_summary_dict_is_assembled_from_both_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every field in the summary traces back to compute_plan or write_plan, not re-derived."""
        plan = {
            "final_order": ["a.esp", "b.esp"],
            "predicate_warnings": ["a warning"],
            "data_result": ["a data line"],
        }
        result = {"wrote_cfg": True, "wrote_toml": False}
        seen_args: list[object] = []
        seen_plan: list[object] = []

        def fake_compute_plan(args: object) -> dict:
            seen_args.append(args)
            return plan

        def fake_write_plan(args: object, given_plan: dict) -> dict:
            seen_plan.append(given_plan)
            return result

        monkeypatch.setattr(wt, "compute_plan", fake_compute_plan)
        monkeypatch.setattr(wt, "write_plan", fake_write_plan)

        args = argparse.Namespace()
        summary = wt.run_from_args(args)

        assert seen_args == [args]
        assert seen_plan == [plan]
        assert summary == {
            "final_order": ["a.esp", "b.esp"],
            "predicate_warnings": ["a warning"],
            "data_result": ["a data line"],
            "wrote_cfg": True,
            "wrote_toml": False,
        }


class TestMain:
    """Argument parsing, logging, and optional tracing, ahead of the real work."""

    def _stub(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[object]]:
        """Replace every side effect main() can trigger with a recorder."""
        calls: dict[str, list[object]] = {
            "setup_logging": [],
            "set_trace_file": [],
            "trace": [],
            "run_from_args": [],
        }

        def fake_setup_logging(**kw: object) -> None:
            calls["setup_logging"].append(kw)

        def fake_set_trace_file(path: object) -> None:
            calls["set_trace_file"].append(path)

        def fake_trace(msg: object) -> None:
            calls["trace"].append(msg)

        def fake_run_from_args(args: object) -> None:
            calls["run_from_args"].append(args)

        monkeypatch.setattr(wt, "setup_logging", fake_setup_logging)
        monkeypatch.setattr(wt, "set_trace_file", fake_set_trace_file)
        monkeypatch.setattr(wt, "trace", fake_trace)
        monkeypatch.setattr(wt, "run_from_args", fake_run_from_args)
        return calls

    def test_no_flags_skips_tracing_and_uses_default_verbosity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The common case: no -v, no --trace."""
        calls = self._stub(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["wraithguard", "--cfg", "x.cfg", "--rules", "x.txt"])

        wt.main()

        assert calls["setup_logging"] == [{"verbosity": 0}]
        assert calls["set_trace_file"] == []
        assert calls["trace"] == []
        assert len(calls["run_from_args"]) == 1

    def test_verbose_flags_raise_the_verbosity_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """-vv is counted, not just detected."""
        calls = self._stub(monkeypatch)
        monkeypatch.setattr(
            sys, "argv", ["wraithguard", "--cfg", "x.cfg", "--rules", "x.txt", "-vv"]
        )

        wt.main()

        assert calls["setup_logging"] == [{"verbosity": 2}]

    def test_a_trace_flag_with_a_path_opens_that_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--trace PATH traces to exactly the path given."""
        calls = self._stub(monkeypatch)
        monkeypatch.setattr(
            sys,
            "argv",
            ["wraithguard", "--cfg", "x.cfg", "--rules", "x.txt", "--trace", "custom.log"],
        )

        wt.main()

        assert calls["set_trace_file"] == ["custom.log"]
        assert calls["trace"] == ["CLI started"]

    def test_a_bare_trace_flag_uses_the_default_file_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--trace with no path is a boolean switch, not a missing argument."""
        calls = self._stub(monkeypatch)
        monkeypatch.setattr(
            sys, "argv", ["wraithguard", "--cfg", "x.cfg", "--rules", "x.txt", "--trace"]
        )

        wt.main()

        assert calls["set_trace_file"] == ["wraithguard_toolkit_trace.log"]
