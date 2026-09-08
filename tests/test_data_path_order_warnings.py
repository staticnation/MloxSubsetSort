"""``_data_path_order_warnings``: the read-only data-path-order.yml reconcile
inside compute_plan. Driven directly rather than through a full run.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import wraithguard_toolkit as core

if TYPE_CHECKING:
    from pathlib import Path

_YML = """\
- for_mod: "Arktwend - OpenMW port"
  extra_dirs:
    - "Data Files"
  on_lists:
    - "arktwend-enhanced-wip"

- for_mod: "TAO - The Arktwend Overhaul"
  on_lists:
    - "arktwend-enhanced-wip"
"""


def _args(yml: Path | None, verbose: int = 0) -> types.SimpleNamespace:
    return types.SimpleNamespace(data_path_order_yml=yml, verbose=verbose)


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "data-path-order.yml"
    p.write_text(_YML, encoding="utf-8")
    return p


def test_no_yml_is_silent(tmp_path: Path, capsys) -> None:
    core._data_path_order_warnings(_args(None), "arktwend-enhanced-wip", ["data=C:/x/Data Files"])
    assert capsys.readouterr().out == ""


def test_no_list_name_is_silent(tmp_path: Path, capsys) -> None:
    core._data_path_order_warnings(_args(_write(tmp_path)), None, ["data=C:/x/Data Files"])
    assert capsys.readouterr().out == ""


def test_all_present_reports_no_warnings(tmp_path: Path, capsys) -> None:
    data_order = [
        "data=C:/mods/Arktwend OpenMW port/Data Files",
        "data=C:/mods/TAO/Data Files",
    ]
    core._data_path_order_warnings(_args(_write(tmp_path)), "arktwend-enhanced-wip", data_order)
    assert "No data-path-order.yml warnings" in capsys.readouterr().out


def test_missing_mods_summarize_by_default(tmp_path: Path, capsys) -> None:
    data_order = ["data=C:/mods/Arktwend OpenMW port/Data Files"]  # TAO absent
    core._data_path_order_warnings(_args(_write(tmp_path)), "arktwend-enhanced-wip", data_order)
    out = capsys.readouterr().out
    assert "DATA-PATH-ORDER.YML WARNING" in out
    # Collapsed to a count, so the per-mod noise (the mod name) is not printed.
    assert "1 mod(s)" in out and "TAO" not in out


def test_missing_mods_listed_with_verbose(tmp_path: Path, capsys) -> None:
    data_order = ["data=C:/mods/Arktwend OpenMW port/Data Files"]  # TAO absent
    core._data_path_order_warnings(
        _args(_write(tmp_path), verbose=1), "arktwend-enhanced-wip", data_order
    )
    out = capsys.readouterr().out
    assert "[DATA PATH]" in out and "TAO" in out


def test_an_unreadable_yml_is_one_warning_not_a_crash(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "gone.yml"  # never created
    core._data_path_order_warnings(_args(missing), "arktwend-enhanced-wip", ["data=C:/x"])
    assert "could not read" in capsys.readouterr().out
