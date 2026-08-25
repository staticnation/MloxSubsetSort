"""GUI support package: theming, reusable widgets, smoke-test tracing.

Split out of ``wraithguard_toolkit_gui.py`` during the 3.0 reconciliation pass
(CODE_REVIEW.md §16/§9.2) the same way the engine was split: bodies moved
verbatim, imports adjusted, behaviour pinned by the existing checks. The main
GUI module re-imports every name, so nothing else changed.

Unlike the rest of ``wraithguard``, this subpackage imports :mod:`tkinter` at
module level and is therefore excluded from the hermetic suite's coverage and
from mypy gating (documented per-file in ``pyproject.toml``, like the other
relocation debt).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from wraithguard.tracing import trace

_APP_DIR = None


def app_base_dir() -> Path:
    """Return the writable folder for everything the app persists.

    Settings, the trace log, cell_map.html and the tes3conv_json spool all
    live here. This MUST NOT be derived from the GUI script's ``__file__``:
    under PyInstaller / auto-py-to-exe, ``__file__`` lives in a temp
    extraction dir that's wiped on exit (onefile) or a read-only install dir.
    Prefer the folder next to the .exe (frozen) or the app folder (source --
    the directory holding the ``wraithguard`` package, i.e. where
    wraithguard_toolkit_gui.py sits); if that isn't writable, fall back to a
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
        # this file is <app dir>/wraithguard/gui/__init__.py
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


#: Filenames the Help menu offers, in the order it offers them. Kept here rather
#: than in the GUI module so the lookup and the list of what can be looked up sit
#: together. The menu labels live at the call site instead, as literals: a
#: ``_(variable)`` is invisible to the string extractor, so a label held here
#: could never be translated.
HELP_DOCUMENTS: tuple[str, ...] = ("QUICKSTART.md", "README.md", "MLOX_RULES.md")


def doc_path(filename: str) -> Path | None:
    """Locate one of the project's Markdown documents.

    Three places, in order, because the answer differs per build:

    1. ``sys._MEIPASS`` -- a PyInstaller onefile build unpacks its bundled data
       to a temp directory. This is where the docs live in a shipped .exe, and
       it must be checked first: the folder beside the .exe may hold an *older*
       copy someone extracted by hand.
    2. Beside the executable or the app folder, via :func:`app_base_dir`.
    3. The source tree, for a checkout where the app folder resolved elsewhere.

    Args:
        filename: The document's filename, e.g. ``"README.md"``.

    Returns:
        The first readable match, or ``None`` when the document was not shipped
        with this build -- which the caller reports rather than crashing on,
        since missing help is a disappointment and not a failure.
    """
    bundled = getattr(sys, "_MEIPASS", None)
    candidates = [
        *([Path(bundled) / filename] if bundled else []),
        app_base_dir() / filename,
        Path(__file__).resolve().parent.parent.parent / filename,
    ]
    try:
        found = next((c for c in candidates if c.is_file()), None)
    except OSError:  # is_file() on an unreadable mount
        found = None
    if found is None:
        trace(f"help: {filename} not found; looked in {[str(c) for c in candidates]}")
    return found


def _ci_pattern(patterns: str) -> str:
    """Rewrite a space-separated glob pattern to be case-insensitive.

    Each alphabetic character becomes a ``[aA]`` class -- ``*.esp`` turns into
    ``*.[eE][sS][pP]`` -- which Tk's ``string match`` honours. Non-alphabetic
    characters (``*``, ``.``, digits) are left as they are.
    """
    return " ".join(
        "".join(f"[{c.lower()}{c.upper()}]" if c.isalpha() else c for c in token)
        for token in patterns.split()
    )


def case_insensitive_filetypes(
    filetypes: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    """Make ``filedialog`` extension filters case-insensitive on X11 (Linux).

    Tk's own file dialog -- the one used on Linux, including the Steam Deck --
    matches the ``filetypes`` extension globs case-sensitively, so a filter of
    ``*.esp`` hides ``Mod.ESP`` until the user switches to "All files". That
    bites hard on the Deck, where Morrowind plugins are routinely upper-case.

    Windows and macOS use their native dialogs, which are already
    case-insensitive AND do not understand ``[eE]``-style glob classes, so on
    those platforms the filters are returned unchanged. Only on X11 are they
    rewritten via :func:`_ci_pattern`.

    Idempotent: a pattern that already carries a ``[..]`` class is assumed
    rewritten and returned unchanged, so wrapping twice (a call site and a
    widget both wrapping, say) cannot double it into the malformed
    ``*.[[cC]]...`` that once made the ``openmw.cfg`` filter select nothing.

    Args:
        filetypes: A ``filedialog`` ``filetypes`` sequence of
            ``(label, patterns)`` pairs, where ``patterns`` is a
            space-separated glob string such as ``"*.esp *.esm"``.

    Returns:
        The filters, rewritten to be case-insensitive on X11 and untouched
        elsewhere.
    """
    if sys.platform in ("win32", "darwin"):
        return filetypes
    return tuple(
        (label, patterns if "[" in patterns else _ci_pattern(patterns))
        for label, patterns in filetypes
    )


# Drag-and-drop is optional -- the GUI degrades gracefully to Browse-only.
# Probed HERE, once, so the main module and the widgets share one answer.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAVE_DND = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    HAVE_DND = False


def dnd_ready(widget: object) -> bool:
    """Whether this interpreter can actually accept files dropped onto it.

    ``HAVE_DND`` answers a different question. It says the *Python package*
    imports; accepting a drop needs the **tkdnd Tcl package loaded into this
    interpreter**, which only happens when the root window was built with
    ``TkinterDnD.Tk()``. The two disagree whenever the root came from a plain
    ``tk.Tk()``, or tkdnd is half-installed, or a frozen build shipped the
    Python side without the Tcl side.

    Conflating them was a real failure, not a hypothetical one: a registration
    raising ``TclError`` aborted the whole window build, so the app would not
    start *at all* over a convenience feature -- and the traceback pointed at a
    path entry rather than at the missing package.

    Deliberately not cached. The answer belongs to an interpreter, and caching
    it in a module global would let one root's answer leak into another's.

    Args:
        widget: Any live widget, used to reach its interpreter.

    Returns:
        ``True`` when drag and drop will work.
    """
    if not HAVE_DND:
        return False
    # Imported here rather than at module scope: this package is imported by
    # the hermetic suite, which has no tkinter. Reaching this line means
    # tkinterdnd2 imported, which imports tkinter itself, so it cannot fail.
    import tkinter as tk

    try:
        widget.tk.eval("package present tkdnd")  # type: ignore[attr-defined]
    except tk.TclError:
        trace_first_fire("drag & drop unavailable: tkdnd is not loaded in this interpreter")
        return False
    return True


def register_drop_target(widget: object) -> bool:
    """Make a widget accept files dragged in from the file manager.

    Args:
        widget: The widget to register.

    Returns:
        ``True`` when the widget will accept drops; ``False`` when drag and
        drop is unavailable and the caller should offer Browse instead.
    """
    if not dnd_ready(widget):
        return False
    # Still guarded: the package being present does not prove this particular
    # widget class can carry a drop target, and no convenience feature is worth
    # refusing to open the window over.
    import tkinter as tk

    try:
        widget.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
    except tk.TclError:
        trace_first_fire("drop target registration refused by Tk")
        return False
    return True


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
