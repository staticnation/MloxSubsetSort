"""Locating and reading the vendored three.js build.

``three_source`` finds the library in a source checkout; ``_first_readable``
is the fallback walker underneath it, and its two failure branches -- a
candidate that is not a file, and one that raises while being read -- are what
the happy-path viewer tests never reach.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wraithguard.viz.library import ViewerError, _first_readable, three_source


def test_three_source_reads_the_checked_in_build() -> None:
    """A source checkout has the library beside the module."""
    text = three_source()
    assert isinstance(text, str)
    assert text


def test_first_readable_returns_the_first_file(tmp_path: Path) -> None:
    """The first candidate that exists and reads is returned verbatim."""
    good = tmp_path / "b.txt"
    good.write_text("payload", encoding="utf-8")
    assert _first_readable([tmp_path / "missing.txt", good]) == "payload"


def test_first_readable_skips_a_non_file(tmp_path: Path) -> None:
    """A candidate that is not a file is passed over rather than read."""
    assert _first_readable([tmp_path / "does-not-exist"]) is None


def test_first_readable_survives_an_unreadable_candidate(tmp_path: Path, monkeypatch) -> None:
    """A candidate that raises on read is logged and skipped, not fatal."""
    victim = tmp_path / "locked.txt"
    victim.write_text("x", encoding="utf-8")

    def boom(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("locked")

    monkeypatch.setattr(Path, "read_text", boom)
    assert _first_readable([victim]) is None


def test_three_source_reports_a_missing_build(monkeypatch) -> None:
    """When nothing can be read, the disappointment is an explicit error."""
    monkeypatch.setattr("wraithguard.viz.library._first_readable", lambda _c: None)
    with pytest.raises(ViewerError, match="not shipped"):
        three_source()
