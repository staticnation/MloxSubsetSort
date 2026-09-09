"""``_data_path_order_warnings``: the read-only data-path reconcile inside
compute_plan, against a cached MOMW list order. Driven directly rather than
through a full run.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import wraithguard_toolkit as core

if TYPE_CHECKING:
    from pathlib import Path

# A cache of relative data-path tails (as fetched from the MOMW API).
_CACHE = "# list: arktwend-enhanced-wip\nArktwend/ArktwendOpenMWport/Data Files\nArktwend/TAO\n"


def _args(cache: Path | None, verbose: int = 0) -> types.SimpleNamespace:
    return types.SimpleNamespace(data_paths_cache=cache, verbose=verbose)


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "data-paths.txt"
    p.write_text(_CACHE, encoding="utf-8")
    return p


def test_no_cache_is_silent(tmp_path: Path, capsys) -> None:
    core._data_path_order_warnings(_args(None), ["data=C:/x/Data Files"])
    assert capsys.readouterr().out == ""


def test_all_present_reports_no_warnings(tmp_path: Path, capsys) -> None:
    data_order = [
        "data=E:/Mods/total-overhaul/Arktwend/ArktwendOpenMWport/Data Files",
        "data=E:/Mods/total-overhaul/Arktwend/TAO",
    ]
    core._data_path_order_warnings(_args(_write(tmp_path)), data_order)
    assert "No data path warnings" in capsys.readouterr().out


def test_missing_paths_summarize_by_default(tmp_path: Path, capsys) -> None:
    data_order = [
        "data=E:/Mods/total-overhaul/Arktwend/ArktwendOpenMWport/Data Files"
    ]  # TAO absent
    core._data_path_order_warnings(_args(_write(tmp_path)), data_order)
    out = capsys.readouterr().out
    assert "DATA PATH WARNING" in out
    # Collapsed to a count, so the per-path noise (the tail) is not printed.
    assert "1 of the list's data= path(s)" in out and "TAO" not in out


def test_missing_paths_listed_with_verbose(tmp_path: Path, capsys) -> None:
    data_order = [
        "data=E:/Mods/total-overhaul/Arktwend/ArktwendOpenMWport/Data Files"
    ]  # TAO absent
    core._data_path_order_warnings(_args(_write(tmp_path), verbose=1), data_order)
    out = capsys.readouterr().out
    assert "[DATA PATH]" in out and "TAO" in out


def test_an_unreadable_cache_is_one_warning_not_a_crash(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "gone.txt"  # never created
    core._data_path_order_warnings(_args(missing), ["data=C:/x"])
    assert "could not read" in capsys.readouterr().out
