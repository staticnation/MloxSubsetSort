"""_staleness_watchdog: the [STALE] advisory for generated merge artifacts
(delta-merged.omwaddon and friends) that predate an active plugin.

Read-only and purely cosmetic -- it never blocks a run, only prints a hint
that momw-configurator should be re-run. Had no coverage before this file.
Mtimes are set explicitly via os.utime with wide (100s+) deltas rather than
relying on write order, since filesystem mtime resolution isn't guaranteed
fine enough to separate two files written back to back.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from conftest import write_plugin

import wraithguard_toolkit as core

if TYPE_CHECKING:
    from pathlib import Path


def _touch(path: Path, *, offset_seconds: float) -> None:
    t = time.time() + offset_seconds
    import os

    os.utime(path, (t, t))


def _data_order(data_dir: Path) -> list[str]:
    return [f'data="{data_dir}"']


class TestNoStaleness:
    def test_no_artifact_present_is_a_silent_no_op(self, tmp_path: Path, capsys) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "A.esp")

        core._staleness_watchdog(["A.esp"], ["A.esp"], _data_order(data), [], [])

        assert capsys.readouterr().out == ""

    def test_an_artifact_not_in_the_active_list_is_ignored(self, tmp_path: Path, capsys) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "delta-merged.omwaddon")
        _touch(data / "delta-merged.omwaddon", offset_seconds=-3600)
        write_plugin(data / "A.esp")

        # delta-merged.omwaddon exists on disk but was never activated.
        core._staleness_watchdog(["A.esp"], ["A.esp"], _data_order(data), [], [])

        assert capsys.readouterr().out == ""

    def test_an_artifact_newer_than_everything_active_is_not_stale(
        self, tmp_path: Path, capsys
    ) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "A.esp")
        _touch(data / "A.esp", offset_seconds=-3600)
        write_plugin(data / "delta-merged.omwaddon")  # written after -> newer

        core._staleness_watchdog(
            ["A.esp", "delta-merged.omwaddon"],
            ["A.esp", "delta-merged.omwaddon"],
            _data_order(data),
            [],
            [],
        )

        assert capsys.readouterr().out == ""


class TestStaleness:
    def test_an_older_artifact_than_an_active_plugin_is_flagged(
        self, tmp_path: Path, capsys
    ) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "delta-merged.omwaddon")
        _touch(data / "delta-merged.omwaddon", offset_seconds=-3600)
        write_plugin(data / "A.esp")  # newer than the artifact

        core._staleness_watchdog(
            ["A.esp", "delta-merged.omwaddon"],
            ["A.esp", "delta-merged.omwaddon"],
            _data_order(data),
            [],
            [],
        )

        out = capsys.readouterr().out
        assert "[STALE]" in out
        assert "delta-merged.omwaddon" in out
        assert "A.esp" in out

    def test_final_order_none_falls_back_to_base_order_names(self, tmp_path: Path, capsys) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "delta-merged.omwaddon")
        _touch(data / "delta-merged.omwaddon", offset_seconds=-3600)
        write_plugin(data / "A.esp")

        core._staleness_watchdog(
            None,
            ["A.esp", "delta-merged.omwaddon"],
            _data_order(data),
            [],
            [],
        )

        assert "[STALE]" in capsys.readouterr().out

    def test_the_artifact_is_never_compared_against_itself(self, tmp_path: Path, capsys) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        # Only the artifact is active -- nothing else to be "newer" than it.
        write_plugin(data / "delta-merged.omwaddon")
        _touch(data / "delta-merged.omwaddon", offset_seconds=-3600)

        core._staleness_watchdog(
            ["delta-merged.omwaddon"], ["delta-merged.omwaddon"], _data_order(data), [], []
        )

        assert capsys.readouterr().out == ""

    def test_omwscripts_files_are_excluded_from_the_comparison(
        self, tmp_path: Path, capsys
    ) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "delta-merged.omwaddon")
        _touch(data / "delta-merged.omwaddon", offset_seconds=-3600)
        (data / "A.omwscripts").write_text("PLAYER_STARTUP: foo.lua\n", encoding="utf-8")

        core._staleness_watchdog(
            ["A.omwscripts", "delta-merged.omwaddon"],
            ["A.omwscripts", "delta-merged.omwaddon"],
            _data_order(data),
            [],
            [],
        )

        assert capsys.readouterr().out == ""

    def test_more_than_three_newer_plugins_truncates_the_example_list(
        self, tmp_path: Path, capsys
    ) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "delta-merged.omwaddon")
        _touch(data / "delta-merged.omwaddon", offset_seconds=-3600)
        names = ["A.esp", "B.esp", "C.esp", "D.esp"]
        for n in names:
            write_plugin(data / n)

        core._staleness_watchdog(
            [*names, "delta-merged.omwaddon"],
            [*names, "delta-merged.omwaddon"],
            _data_order(data),
            [],
            [],
        )

        out = capsys.readouterr().out
        assert "4 active plugins" in out
        assert ", ..." in out

    def test_the_watched_artifacts_are_the_three_documented_names(
        self, tmp_path: Path, capsys
    ) -> None:
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "S3LightFixes.esp")
        _touch(data / "S3LightFixes.esp", offset_seconds=-3600)
        write_plugin(data / "A.esp")

        core._staleness_watchdog(
            ["A.esp", "S3LightFixes.esp"],
            ["A.esp", "S3LightFixes.esp"],
            _data_order(data),
            [],
            [],
        )

        assert "S3LightFixes.esp" in capsys.readouterr().out

    def test_pending_data_inserts_are_searched_too(self, tmp_path: Path, capsys) -> None:
        # The artifact lives in a pending custom data folder that isn't in
        # the cfg's data= lines yet -- all_scan_dirs() is what makes it
        # reachable at all.
        cfg_data = tmp_path / "Data Files"
        cfg_data.mkdir()
        write_plugin(cfg_data / "A.esp")

        pending = tmp_path / "PendingMod"
        pending.mkdir()
        write_plugin(pending / "delta-merged.omwaddon")
        _touch(pending / "delta-merged.omwaddon", offset_seconds=-3600)

        core._staleness_watchdog(
            ["A.esp", "delta-merged.omwaddon"],
            ["A.esp", "delta-merged.omwaddon"],
            _data_order(cfg_data),
            [],
            [{"value": str(pending), "after": None, "before": None}],
        )

        assert "[STALE]" in capsys.readouterr().out


class TestFailureModesAreAdvisoryOnly:
    def test_a_candidate_whose_mtime_cannot_be_read_is_skipped(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """One candidate's stat() failing (e.g. deleted mid-run) must not stop the others."""
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "delta-merged.omwaddon")
        _touch(data / "delta-merged.omwaddon", offset_seconds=-3600)
        write_plugin(data / "A.esp")
        write_plugin(data / "B.esp")
        from pathlib import Path as RealPath

        original_stat = RealPath.stat
        calls: dict[str, int] = {}

        def flaky_stat(self: RealPath, *args, **kwargs):
            if self.name == "A.esp":
                calls["A.esp"] = calls.get("A.esp", 0) + 1
                if calls["A.esp"] > 1:  # let is_file() during indexing succeed; fail the real check
                    raise OSError("simulated: deleted mid-run")
            return original_stat(self, *args, **kwargs)

        monkeypatch.setattr(RealPath, "stat", flaky_stat)

        core._staleness_watchdog(
            ["A.esp", "B.esp", "delta-merged.omwaddon"],
            ["A.esp", "B.esp", "delta-merged.omwaddon"],
            _data_order(data),
            [],
            [],
        )

        out = capsys.readouterr().out
        assert "[STALE]" in out
        assert "B.esp" in out  # the one candidate that could still be compared

    def test_an_unexpected_failure_is_swallowed_not_raised(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Purely cosmetic: any failure anywhere in this check must not surface at all."""
        data = tmp_path / "Data Files"
        data.mkdir()
        write_plugin(data / "delta-merged.omwaddon")
        write_plugin(data / "A.esp")

        def _boom(*_a, **_k):
            raise RuntimeError("simulated: a genuinely broken index")

        monkeypatch.setattr(core, "PluginFileIndex", _boom)

        core._staleness_watchdog(
            ["A.esp", "delta-merged.omwaddon"],
            ["A.esp", "delta-merged.omwaddon"],
            _data_order(data),
            [],
            [],
        )  # must not raise

        assert capsys.readouterr().out == ""
