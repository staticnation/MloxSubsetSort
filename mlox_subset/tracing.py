"""Lightweight trace logs for post-mortem debugging.

Distinct from :mod:`mlox_subset.logging_setup`, which handles the user-facing
levelled logging. This is a crash-survival tool: timestamped lines appended to
a file and flushed immediately, so that when a heavy operation runs out of
memory or hangs and the process dies, the last steps it managed are still on
disk. That is the whole design constraint -- buffering would defeat it.

Two files, deliberately:

* the **main trace**, covering cell-map and tes3conv work;
* the **sort trace**, a separate file truncated at the start of each sort.

The sort engine's play-by-play runs to thousands of lines and has nothing to do
with the other operations. Interleaving them makes both unreadable, so the sort
gets its own file and each sort starts clean -- a sort log stays small,
self-contained, and worth actually reading.

Tracing is off unless :func:`set_trace_file` is called.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import IO, Final

#: Filename of the sort trace, written next to the main trace file.
SORT_TRACE_NAME: Final = "mlox_subset_sort_sort_trace.log"

_trace_path: str | None = None
_trace_handle: IO[str] | None = None
_sort_trace_path: str | None = None
_sort_trace_handle: IO[str] | None = None


def _close(handle: IO[str] | None) -> None:
    """Close a handle, ignoring any failure.

    Args:
        handle: The file handle to close, or ``None``.
    """
    if handle is None:
        return
    try:
        handle.close()
    except OSError:
        pass  # already closed, or the disk went away -- nothing useful to do


def set_trace_file(path: str | Path | None) -> None:
    """Enable tracing to ``path``, or disable it.

    The file is truncated per session so the log does not grow without bound
    across runs.

    Args:
        path: Destination for the main trace, or ``None``/empty to turn
            tracing off.
    """
    global _trace_path, _trace_handle
    _close(_trace_handle)
    _trace_handle = None
    _trace_path = str(path) if path else None
    if _trace_path:
        try:
            _trace_handle = Path(_trace_path).open("w", encoding="utf-8")
        except OSError:
            _trace_handle = None
        trace("=== trace start ===")


def trace(message: str) -> None:
    """Append one timestamped line to the main trace.

    The handle is kept open between calls: reopening per call crawled once the
    sort engine began logging thousands of steps. Each line is still flushed,
    so a crash leaves everything written so far on disk.

    Args:
        message: The line to record. No-op when tracing is off.
    """
    global _trace_handle
    if not _trace_path:
        return
    try:
        if _trace_handle is None:
            _trace_handle = Path(_trace_path).open("a", encoding="utf-8")
        # Local clock: these lines are read next to the user's own log/GUI.
        _trace_handle.write(
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {message}\n"  # noqa: DTZ005
        )
        _trace_handle.flush()
    except OSError:
        pass  # tracing must never be the thing that breaks a run


def sort_trace_begin() -> None:
    """Open a fresh, truncated sort-trace file next to the main trace.

    Call once at the start of a sort. No-op unless tracing is enabled.
    """
    global _sort_trace_path, _sort_trace_handle
    _close(_sort_trace_handle)
    _sort_trace_handle = None
    if not _trace_path:
        _sort_trace_path = None
        return
    _sort_trace_path = str(Path(_trace_path).with_name(SORT_TRACE_NAME))
    try:
        _sort_trace_handle = Path(_sort_trace_path).open("w", encoding="utf-8")
    except OSError:
        _sort_trace_handle = None
    # Leave a pointer in the MAIN log so the sort trace is discoverable.
    trace(f"[sort] full sort play-by-play -> {_sort_trace_path}")


def trace_sort(message: str) -> None:
    """Append one line to the dedicated sort trace.

    Args:
        message: The line to record. No-op unless :func:`sort_trace_begin` has
            opened the sort trace.
    """
    if not _trace_path or _sort_trace_handle is None:
        return
    try:
        _sort_trace_handle.write(
            f"{datetime.now().strftime('%H:%M:%S')}  {message}\n"  # noqa: DTZ005
        )
        _sort_trace_handle.flush()
    except OSError:
        # Same rule as trace(): the diagnostic log must never be the thing that
        # breaks the run it exists to diagnose. A full disk loses trace lines,
        # not the sort.
        pass


def trace_path() -> str | None:
    """The active main-trace path, or ``None`` when tracing is off."""
    return _trace_path


def sort_trace_path() -> str | None:
    """The active sort-trace path, or ``None`` when no sort trace is open."""
    return _sort_trace_path
