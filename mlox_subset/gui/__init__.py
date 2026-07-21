"""GUI support package: theming, reusable widgets, smoke-test tracing.

Split out of ``mlox_subset_sort_gui.py`` during the 3.0 reconciliation pass
(CODE_REVIEW.md §16/§9.2) the same way the engine was split: bodies moved
verbatim, imports adjusted, behaviour pinned by the existing checks. The main
GUI module re-imports every name, so nothing else changed.

Unlike the rest of ``mlox_subset``, this subpackage imports :mod:`tkinter` at
module level and is therefore excluded from the hermetic suite's coverage and
from mypy gating (documented per-file in ``pyproject.toml``, like the other
relocation debt).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mlox_subset.tracing import trace

_APP_DIR = None


def app_base_dir() -> Path:
    """Return the writable folder for everything the app persists.

    Settings, the trace log, cell_map.html and the tes3conv_json spool all
    live here. This MUST NOT be derived from the GUI script's ``__file__``:
    under PyInstaller / auto-py-to-exe, ``__file__`` lives in a temp
    extraction dir that's wiped on exit (onefile) or a read-only install dir.
    Prefer the folder next to the .exe (frozen) or the app folder (source --
    the directory holding the ``mlox_subset`` package, i.e. where
    mlox_subset_sort_gui.py sits); if that isn't writable, fall back to a
    per-user data dir. Cached.

    Returns:
        The base directory, created if the fallback path was needed.
    """
    global _APP_DIR
    if _APP_DIR is not None:
        return _APP_DIR
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent  # next to the built .exe
    else:
        # this file is <app dir>/mlox_subset/gui/__init__.py
        base = Path(__file__).resolve().parent.parent.parent
    try:
        probe = base / ".mlox_write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError:  # the probe is just write_text/unlink
        if os.name == "nt":
            root = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        elif sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support"
        else:
            root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        base = root / "MloxSubsetSort"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:  # mkdir on the per-user data dir
            base = Path.home()
    _APP_DIR = base
    return base


# Drag-and-drop is optional -- the GUI degrades gracefully to Browse-only.
# Probed HERE, once, so the main module and the widgets share one answer.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAVE_DND = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    HAVE_DND = False

_FIRED_ONCE: set[str] = set()


def trace_first_fire(label: str) -> None:
    """Record the first time a callback fires, once per session.

    Added for smoke-testing the GUI, which has no automated coverage. Several
    callbacks were rewritten from ``lambda: self.m()`` to ``self.m``; if such a
    rewrite were wrong the callback would simply never run, which is invisible
    on screen and silent in the log. One line per callback proves the binding
    is live without burying the trace under re-render noise.

    Args:
        label: Identifier for the callback, e.g. ``"listbox drag-reorder"``.
    """
    if label in _FIRED_ONCE:
        return
    _FIRED_ONCE.add(label)
    trace(f"[smoke] callback fired for the first time: {label}")
