"""Edge branches of the network updaters, without a live server.

``fetch_url_bytes`` and ``parse_plugin_order_yml`` are stubbed so the
validation gates, the up-to-date short-circuit, the cosmetic old-count
re-parse, and the file-age helper can be exercised deterministically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wraithguard.net import updaters
from wraithguard.net.updaters import rule_file_ages, update_plugin_order_yml

if TYPE_CHECKING:
    from pathlib import Path

_VALID = b"- file_name: a.esp\n  on_lists:\n    - total-overhaul\n"


def test_a_response_that_is_not_plugin_order_is_rejected(tmp_path: Path, monkeypatch) -> None:
    """A download missing the tell-tale keys is refused before touching disk."""
    monkeypatch.setattr(updaters, "fetch_url_bytes", lambda _u, timeout=45: b"just some html")
    target = tmp_path / "plugin-order.yml"
    report = update_plugin_order_yml(target, urls=["https://example/x"])
    assert not target.exists()
    assert any("doesn't look like" in line for line in report)


def test_an_already_current_file_is_left_untouched(tmp_path: Path, monkeypatch) -> None:
    """When the download matches the file on disk, nothing is rewritten."""
    monkeypatch.setattr(updaters, "fetch_url_bytes", lambda _u, timeout=45: _VALID)
    monkeypatch.setattr(updaters, "parse_plugin_order_yml", lambda _p: ["e"] * 150)
    target = tmp_path / "plugin-order.yml"
    target.write_bytes(_VALID)
    report = update_plugin_order_yml(target, urls=["https://example/x"])
    assert any("already up to date" in line for line in report)


def test_a_failed_old_reparse_does_not_break_the_update(tmp_path: Path, monkeypatch) -> None:
    """If counting the previous file's entries fails, the update still succeeds."""
    calls = {"n": 0}

    def flaky(_path: Path) -> list[str]:
        calls["n"] += 1
        if calls["n"] == 1:
            return ["e"] * 150  # the new download validates fine
        raise ValueError("cannot re-parse the old file")  # the cosmetic old count

    monkeypatch.setattr(updaters, "fetch_url_bytes", lambda _u, timeout=45: _VALID)
    monkeypatch.setattr(updaters, "parse_plugin_order_yml", flaky)
    target = tmp_path / "plugin-order.yml"
    target.write_bytes(b"- file_name: old.esp\n  on_lists:\n    - x\n")  # different old content
    report = update_plugin_order_yml(target, urls=["https://example/x"])
    assert any("updated from" in line for line in report)
    assert target.read_bytes() == _VALID  # the new data was written


def test_rule_file_ages_reports_ages_and_missing_files(tmp_path: Path) -> None:
    """A readable file gets an age in days; an unreadable one gets None."""
    present = tmp_path / "mlox_base.txt"
    present.write_text("data", encoding="utf-8")
    ages = rule_file_ages([present, tmp_path / "absent.txt"])
    by_name = dict(ages)
    assert by_name["mlox_base.txt"] is not None
    assert by_name["mlox_base.txt"] >= 0
    assert by_name["absent.txt"] is None
