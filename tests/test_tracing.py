"""The crash-survival trace logs.

``wraithguard.tracing`` keeps module-global handles so a heavy run's last steps
survive a crash. It is off until :func:`set_trace_file` is called; these exercise
the on and off paths, the separate sort trace, and the truncate-per-session rule.
An autouse fixture leaves tracing off after each test -- through the public API,
not by poking the module globals -- so no test leaks tracing into another.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from wraithguard.tracing import (
    SORT_TRACE_NAME,
    set_trace_file,
    sort_trace_begin,
    sort_trace_path,
    trace,
    trace_path,
    trace_sort,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_tracing() -> Iterator[None]:
    """Leave tracing off and the sort handle closed after every test."""
    yield
    set_trace_file(None)
    sort_trace_begin()  # tracing now off -> closes and clears the sort handle


class TestMainTrace:
    """Enabling, writing, disabling, and the truncate-per-session rule."""

    def test_off_by_default_writes_nothing(self) -> None:
        """With no file set, a trace call is a silent no-op."""
        set_trace_file(None)
        assert trace_path() is None
        trace("ignored")  # must not raise

    def test_enabling_truncates_and_writes_a_start_marker(self, tmp_path: Path) -> None:
        """set_trace_file opens the file and records that tracing began."""
        log = tmp_path / "trace.log"
        set_trace_file(log)
        assert trace_path() == str(log)
        assert "=== trace start ===" in log.read_text(encoding="utf-8")

    def test_a_line_is_timestamped_and_reaches_disk(self, tmp_path: Path) -> None:
        """Each message is written immediately, behind a timestamp."""
        log = tmp_path / "trace.log"
        set_trace_file(log)
        trace("cell map: start")
        body = log.read_text(encoding="utf-8")
        assert "cell map: start" in body
        assert body.strip().splitlines()[-1][:4].isdigit()  # YYYY-... prefix

    def test_re_enabling_truncates_the_previous_session(self, tmp_path: Path) -> None:
        """The log does not grow across runs -- each session starts clean."""
        log = tmp_path / "trace.log"
        set_trace_file(log)
        trace("from the first session")
        set_trace_file(log)
        body = log.read_text(encoding="utf-8")
        assert "from the first session" not in body
        assert "=== trace start ===" in body

    def test_an_unwritable_path_disables_rather_than_raises(self, tmp_path: Path) -> None:
        """A path under a missing directory becomes a safe no-op, not a crash."""
        missing = tmp_path / "nope" / "trace.log"
        set_trace_file(missing)  # open() fails; must not raise
        trace("still safe")  # must not raise
        assert not missing.exists()


class TestSortTrace:
    """The dedicated, per-sort trace file."""

    def test_it_opens_a_separate_file_and_points_the_main_log_at_it(self, tmp_path: Path) -> None:
        """A sort gets its own truncated file, discoverable from the main log."""
        log = tmp_path / "trace.log"
        set_trace_file(log)
        sort_trace_begin()
        sort_path = sort_trace_path()
        assert sort_path is not None
        assert sort_path.endswith(SORT_TRACE_NAME)
        assert Path(sort_path).exists()
        assert SORT_TRACE_NAME in log.read_text(encoding="utf-8")

    def test_lines_go_to_the_sort_file(self, tmp_path: Path) -> None:
        """trace_sort writes to the sort trace once it is open."""
        set_trace_file(tmp_path / "trace.log")
        sort_trace_begin()
        trace_sort("engine: step 1")
        sort_path = sort_trace_path()
        assert sort_path is not None
        assert "engine: step 1" in Path(sort_path).read_text(encoding="utf-8")

    def test_it_is_a_noop_when_tracing_is_off(self) -> None:
        """No main trace means no sort trace either."""
        set_trace_file(None)
        sort_trace_begin()
        assert sort_trace_path() is None
        trace_sort("ignored")  # must not raise


class TestTraceIoFailuresAreSwallowed:
    """A failing disk must lose trace lines, never break the run it traces."""

    def test_closing_a_broken_handle_is_ignored(self) -> None:
        """``_close`` swallows an OSError from a handle that will not close."""
        from wraithguard.tracing import _close

        class _Stubborn:
            def close(self) -> None:
                raise OSError("disk went away")

        _close(_Stubborn())  # must not raise

    def test_a_sort_trace_that_cannot_open_leaves_no_handle(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """If the sort trace cannot be opened, tracing carries on without it."""
        set_trace_file(tmp_path / "trace.log")

        real_open = Path.open

        def refuse(self: Path, *args: object, **kwargs: object):
            if self.name == SORT_TRACE_NAME:
                raise OSError("read-only filesystem")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", refuse)
        sort_trace_begin()  # must not raise
        trace_sort("ignored")  # no handle -> silent no-op

    def test_a_write_failure_on_the_sort_trace_is_swallowed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A write that raises mid-sort loses the line, not the sort."""
        import wraithguard.tracing as tracing_mod

        set_trace_file(tmp_path / "trace.log")
        sort_trace_begin()

        class _BrokenWriter:
            def write(self, _text: str) -> int:
                raise OSError("disk full")

            def flush(self) -> None:  # pragma: no cover - never reached after write raises
                pass

        monkeypatch.setattr(tracing_mod, "_sort_trace_handle", _BrokenWriter())
        trace_sort("engine: step")  # must not raise
