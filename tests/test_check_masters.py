"""_check_masters: the always-on missing/out-of-order master report inside
compute_plan. Previously entirely untested -- these call it directly rather
than threading a full compute_plan run through it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from conftest import write_plugin

import wraithguard_toolkit as core

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class TestCheckMasters:
    def test_a_missing_master_is_warned_about(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        write_plugin(data_dir / "Mine.esp", masters=("Ghost.esm",), sizes=(0,))

        warnings, problems = core._check_masters(
            final_order=["Mine.esp"],
            base_order_names=["Mine.esp"],
            data_order=[f'data="{data_dir}"'],
            raw_toml_data_inserts=[],
            data_inserts=[],
            subset_origins={},
        )

        assert any("Ghost.esm" in w for w in warnings)
        assert "Mine.esp" in problems

    def test_an_out_of_order_master_is_warned_about(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Data Files"
        data_dir.mkdir()
        write_plugin(data_dir / "Morrowind.esm")
        write_plugin(data_dir / "Mine.esp", masters=("Morrowind.esm",), sizes=(0,))

        warnings, problems = core._check_masters(
            # Mine.esp loads before its own master:
            final_order=["Mine.esp", "Morrowind.esm"],
            base_order_names=["Mine.esp", "Morrowind.esm"],
            data_order=[f'data="{data_dir}"'],
            raw_toml_data_inserts=[],
            data_inserts=[],
            subset_origins={},
        )

        assert warnings
        assert "Mine.esp" in problems

    def test_a_check_failure_is_advisory_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole section is wrapped: a failure here must not take out the sort."""

        def _boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("simulated: a genuinely broken master check")

        monkeypatch.setattr(core, "check_missing_masters", _boom)

        warnings, problems = core._check_masters(
            final_order=["Mine.esp"],
            base_order_names=["Mine.esp"],
            data_order=[],
            raw_toml_data_inserts=[],
            data_inserts=[],
            subset_origins={},
        )

        assert warnings == []
        assert problems == []
