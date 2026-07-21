#!/usr/bin/env python3
"""
mlox_subset_sort_gui.py

A small drag-and-drop GUI front-end for mlox_subset_sort.py. It doesn't
reimplement any sorting/warning logic itself -- it just builds the same
arguments the CLI would take and calls into mlox_subset_sort directly, and
streams that call's normal console output into a log panel (colorizing
warnings/errors as it goes) instead of you needing to remember flag names
and read a terminal.

Workflow is two steps, matching mlox_subset_sort's compute_plan()/
write_plan() split:
  1. Sort -- runs mlox, evaluates warnings, and populates a "Plugin Load
     Order" list you can drag rows up/down in to manually override anything
     mlox got wrong (or just prefer differently) before committing to it.
  2. Export -- writes openmw.cfg and/or the corrected customizations.toml
     using whatever order the list is in at that point (mlox's own order,
     if you never touched it).

Also here (all optional, all wired straight into the same core functions):
  - Scan... -- generate the subset by walking a mods folder (folds in the old
    mod_scan.py). With "Create subset text document" on it writes a .txt you
    pick; off, it keeps the result in memory just for this session.
  - a list-name field and a plugin-order.yml field -- when both are set, MOMW's
    curated list for that name is told apart from your custom additions.
  - hamburger-style grips on the pane dividers (drag to resize the panels).

Cross-platform: pure Python + tkinter, runs on Windows, Linux and macOS.

Requirements:
    pip install tkinterdnd2 --break-system-packages   (optional -- drag & drop)
    - tkinter ships with the python.org installers on Windows/macOS.
    - On Linux install it via your package manager, e.g.
      Debian/Ubuntu: sudo apt install python3-tk
    - PyYAML is optional (a built-in parser is used if it's absent).

If tkinterdnd2 isn't installed, the GUI still runs -- you just lose
drag-and-drop of files from your OS (dragging rows within the app's own
lists to reorder them doesn't need tkinterdnd2, so that always works) and
have to use the "Browse..." buttons instead for file inputs.

Run it with:
    python3 mlox_subset_sort_gui.py

This file must sit next to mlox_subset_sort.py (it imports it directly
rather than shelling out, so results, exceptions, etc. all stay in-process).
"""

# PEP 563: annotations are strings, so a hint may name a type that is
# only imported for type checking, and no annotation costs import time.
from __future__ import annotations

import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
import types
import webbrowser
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import ClassVar

# Compiled-script disassembly for the field-diff window. Optional: if the
# package is missing the diff view still works, it just shows the raw base64
# blob it always did.
try:
    from mlox_subset.mwscript import (
        listing_for_bytecode_field,
        variables_text_for_field,
    )
except ImportError:  # pragma: no cover - only when mlox_subset/ is absent
    listing_for_bytecode_field = None
    variables_text_for_field = None

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except ImportError:
    sys.exit(
        "tkinter isn't available in this Python install.\n"
        "On Debian/Ubuntu: sudo apt install python3-tk\n"
        "On Windows/Mac's python.org installers, tkinter is included by default."
    )

# Inline HTML rendering for the cell map is optional. tkinterweb is PREFERRED --
# its HtmlFrame.load_file reads the map from disk (bounded memory) and renders the
# SVG grid; tkhtmlview only takes an in-memory string and can't draw SVG, so it's
# a last resort. Without either, the map is written to a file and opened in a
# browser. (pip install tkinterweb  -- recommended for the in-app window.)
HTMLViewer = None
try:
    from tkinterweb import HtmlFrame as HTMLViewer  # supports load_file + SVG
except Exception:  # noqa: BLE001
    # optional 3rd-party import; a broken install must not kill startup
    try:
        from tkhtmlview import HTMLScrolledText as HTMLViewer
    except Exception:  # noqa: BLE001
        # optional 3rd-party import; a broken install must not kill startup
        HTMLViewer = None

# pywebview is the BEST in-app option: it hosts the OS webview (Edge WebView2 /
# WebKit), so it renders the SVG map + tabs exactly like a browser. It's launched
# in a separate process (webview.start() wants the main thread), so it doesn't
# fight tkinter's mainloop. Detected here; used first if present.
try:
    import webview as _webview_probe  # real import: reliable under PyInstaller, unlike find_spec

    HAVE_PYWEBVIEW = True
    del _webview_probe
except Exception:  # noqa: BLE001
    # optional 3rd-party import; a broken install must not kill startup
    HAVE_PYWEBVIEW = False


_APP_DIR = None
_TRACE_REQUEST = None  # set by main() from --trace; None = use env var / off


def app_base_dir():
    """A writable folder for everything the app persists (settings, trace log,
    cell_map.html, the tes3conv_json spool). This MUST NOT be derived from
    __file__: under PyInstaller / auto-py-to-exe, __file__ lives in a temp
    extraction dir that's wiped on exit (onefile) or a read-only install dir.
    Prefer the folder next to the .exe (frozen) or next to the script (source);
    if that isn't writable, fall back to a per-user data dir. Cached."""
    global _APP_DIR
    if _APP_DIR is not None:
        return _APP_DIR
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent  # next to the built .exe
    else:
        base = Path(__file__).resolve().parent
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


# Drag-and-drop is optional -- degrade gracefully to Browse-only if missing.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAVE_DND = True
except ImportError:
    HAVE_DND = False

# abspath/dirname deliberately, not Path.resolve(): resolve() follows
# symlinks, and this file is routinely run through one (MO2 junctions, a
# symlinked tools folder). Resolving would put the WRONG directory on
# sys.path and the engine import below would fail.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # noqa: PTH100, PTH120
try:
    import mlox_subset_sort as core
except ImportError as e:
    sys.exit(f"Couldn't import mlox_subset_sort.py -- make sure it's in the same folder.\n({e})")

# The gettext marker. Imported after the sys.path fix-up above, which is what
# makes the package findable; hence the E402. Every user-facing literal in this
# file is wrapped in _() so `tools/make_pot.py` can extract it -- with no
# catalogue installed this returns the English string unchanged.
from mlox_subset import _  # noqa: E402

# ---------------------------------------------------------------------------
# chrome palette -- the app-wide window/widget colours (as opposed to the
# syntax-highlighting THEME_PRESETS below). Historically a hardcoded dark
# constant; it is now the *active* chrome palette: set_active_chrome() (below,
# with the theme code) rewrites these values in place from the selected
# syntax theme, so every `DARK[...]` read site -- the ttk.Style setup, the
# plain-widget styler, and per-widget construction -- picks up the theme.
# The literal values here are the built-in dark defaults, kept as the
# fallback and as the hand-tuned chrome for the "Dark (default)" preset.
# ---------------------------------------------------------------------------

DARK = {
    "bg": "#1e1e1e",  # window/frame background
    "bg2": "#252526",  # slightly-raised panel background
    "field_bg": "#2d2d30",  # entry/listbox/text background
    "border": "#3f3f46",
    "fg": "#e6e6e6",  # normal text
    "fg_dim": "#9a9a9a",  # secondary/status text
    "select": "#094771",  # selection highlight
    "btn_bg": "#3a3a3d",
    "btn_bg_active": "#4a4a4e",
    "accent": "#3794ff",
    "log_bg": "#141414",  # console-style text areas (log, previews, detail panes)
}


# ttk base themes that actually honour style.configure(background=...). The
# Windows-native themes (vista/xpnative/winnative) and aqua draw widgets with
# the OS renderer and silently ignore our colour options, so a chrome/theme
# change would appear not to take -- notably in a PyInstaller *onefile* build,
# where clam's Tcl file can fail to extract, theme_use("clam") then raises, and
# without this we'd silently stay on vista. These four are defined in ttk's
# Tcl library; we pick the first that's actually registered.
_COLOR_CAPABLE_TTK_THEMES = ("clam", "alt", "default", "classic")


def _select_color_capable_theme(style):
    """Switch ``style`` to a theme that respects our colour options.

    Returns the theme name now in use. Traced (not swallowed) so a frozen
    build that lands on a native, colour-ignoring theme is diagnosable from
    the log rather than presenting as "the colours just don't change".
    """
    try:
        available = set(style.theme_names())
    except tk.TclError:
        available = set()
    for name in _COLOR_CAPABLE_TTK_THEMES:
        if name in available:
            try:
                style.theme_use(name)
                core.trace(f"[theme] ttk base theme: using {name!r}")
                return name
            except tk.TclError:
                continue
    # nothing colour-capable is registered -- almost always a frozen build that
    # didn't bundle ttk's Tcl theme files. Report loudly; colours on ttk
    # widgets (buttons/frames/tabs) will not apply until the build includes them.
    try:
        active = style.theme_use()
    except tk.TclError:
        active = "?"
    core.trace(
        f"[theme] WARNING: no colour-capable ttk theme available "
        f"(have {sorted(available)}); staying on {active!r} -- ttk widget "
        f"colours will NOT apply. If this is a frozen .exe, the Tcl/tk ttk "
        f"theme files were not bundled."
    )
    return active


def apply_dark_theme(root):
    root.configure(bg=DARK["bg"])
    style = ttk.Style(root)
    _select_color_capable_theme(style)

    style.configure(
        ".",
        background=DARK["bg"],
        foreground=DARK["fg"],
        fieldbackground=DARK["field_bg"],
        bordercolor=DARK["border"],
        darkcolor=DARK["bg"],
        lightcolor=DARK["bg"],
    )
    style.configure("TFrame", background=DARK["bg"])
    style.configure("TLabel", background=DARK["bg"], foreground=DARK["fg"])
    style.configure(
        "TLabelframe", background=DARK["bg"], foreground=DARK["fg"], bordercolor=DARK["border"]
    )
    style.configure("TLabelframe.Label", background=DARK["bg"], foreground=DARK["fg"])
    style.configure("TCheckbutton", background=DARK["bg"], foreground=DARK["fg"])
    style.map(
        "TCheckbutton",
        background=[("active", DARK["bg"])],
        foreground=[("disabled", DARK["fg_dim"])],
    )
    # radiobuttons need the same treatment or the focused/active one renders
    # with a white background on Windows' default theme
    style.configure("TRadiobutton", background=DARK["bg"], foreground=DARK["fg"])
    style.map(
        "TRadiobutton",
        background=[("active", DARK["bg"]), ("focus", DARK["bg"]), ("selected", DARK["bg"])],
        foreground=[("disabled", DARK["fg_dim"])],
    )
    style.configure(
        "TEntry",
        fieldbackground=DARK["field_bg"],
        foreground=DARK["fg"],
        insertcolor=DARK["fg"],
        bordercolor=DARK["border"],
    )
    style.map("TEntry", fieldbackground=[("readonly", DARK["field_bg"])])
    # the closed combobox field. Note: this does NOT reach the dropdown list
    # itself -- that's a separate plain tk::Listbox the combobox pops up, and
    # ttk::Style can't touch it; it's themed below via the option database.
    style.configure(
        "TCombobox",
        fieldbackground=DARK["field_bg"],
        background=DARK["btn_bg"],
        foreground=DARK["fg"],
        arrowcolor=DARK["fg"],
        bordercolor=DARK["border"],
        selectbackground=DARK["field_bg"],
        selectforeground=DARK["fg"],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", DARK["field_bg"]), ("disabled", DARK["bg2"])],
        foreground=[("readonly", DARK["fg"]), ("disabled", DARK["fg_dim"])],
        background=[("active", DARK["btn_bg_active"]), ("readonly", DARK["btn_bg"])],
        arrowcolor=[("disabled", DARK["fg_dim"])],
    )
    # the dropdown list's background/foreground/selection -- ttk::combobox's
    # popdown is a raw Listbox, so this has to go through Tk's option
    # database (by widget-class pattern) rather than ttk::Style.
    root.option_add("*TCombobox*Listbox.background", DARK["field_bg"])
    root.option_add("*TCombobox*Listbox.foreground", DARK["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", DARK["select"])
    root.option_add("*TCombobox*Listbox.selectForeground", DARK["fg"])
    root.option_add("*TCombobox*Listbox.font", ("TkDefaultFont",))
    style.configure(
        "Conf.Treeview",
        background=DARK["field_bg"],
        fieldbackground=DARK["field_bg"],
        foreground=DARK["fg"],
        bordercolor=DARK["border"],
        rowheight=22,
    )
    style.map(
        "Conf.Treeview",
        background=[("selected", DARK["select"])],
        foreground=[("selected", DARK["fg"])],
    )
    style.configure(
        "Conf.Treeview.Heading", background=DARK["btn_bg"], foreground=DARK["fg"], relief="flat"
    )
    style.map("Conf.Treeview.Heading", background=[("active", DARK["btn_bg_active"])])
    style.configure("TNotebook", background=DARK["bg"], borderwidth=0, bordercolor=DARK["border"])
    style.configure(
        "TNotebook.Tab",
        background=DARK["btn_bg"],
        foreground=DARK["fg"],
        padding=(12, 4),
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", DARK["select"]), ("active", DARK["btn_bg_active"])],
        foreground=[("selected", DARK["fg"])],
    )
    style.configure(
        "TButton",
        background=DARK["btn_bg"],
        foreground=DARK["fg"],
        bordercolor=DARK["border"],
        focuscolor=DARK["bg"],
    )
    style.map(
        "TButton",
        background=[("active", DARK["btn_bg_active"]), ("disabled", DARK["bg2"])],
        foreground=[("disabled", DARK["fg_dim"])],
    )
    style.configure(
        "TScrollbar",
        background=DARK["btn_bg"],
        troughcolor=DARK["bg2"],
        bordercolor=DARK["border"],
        arrowcolor=DARK["fg"],
    )
    style.map("TScrollbar", background=[("active", DARK["btn_bg_active"])])
    return style


def style_plain_widget(widget, chrome=None):
    """For non-ttk widgets (tk.Listbox, scrolledtext.ScrolledText) that ttk
    theming doesn't reach. Applied option-by-option since the exact set of
    supported options differs between Listbox and Text (e.g. Listbox has no
    insertbackground). Reads the *active* chrome palette (``DARK``) at call
    time unless an explicit chrome mapping is passed, so it serves both
    construction and the runtime re-apply walk."""
    chrome = DARK if chrome is None else chrome
    options = {
        "background": chrome["field_bg"],
        "foreground": chrome["fg"],
        "insertbackground": chrome["fg"],
        "selectbackground": chrome["select"],
        "selectforeground": chrome["fg"],
        "highlightbackground": chrome["border"],
        "highlightcolor": chrome["accent"],
        "highlightthickness": 1,
        "relief": "flat",
        "borderwidth": 0,
    }
    for opt, val in options.items():
        try:
            widget.configure(**{opt: val})
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# syntax highlighting themes -- shared by two places: the Log panel (the
# console-style output from Sort/Export/Lint/etc.) and the field-diff JSON
# viewer (Check Conflicts -> double-click a field), which also renders
# embedded HTML-ish markup (Morrowind book text uses tags like <DIV ALIGN=,
# <FONT COLOR=, <BR>). One theme picker (next to the log panel) drives both.
#
# Each theme has two layers:
#   - 6 log roles (required): section/warn/error/ok/inserted/dim, plus
#     background/foreground/select for the text widget itself.
#   - 7 JSON/HTML token roles (optional -- fall back to the log roles via
#     _json_syntax_colors if a theme doesn't define them): key/string/number/
#     keyword/punct/tag/attr.
#
# Built-in presets use each scheme's well-known palette. Users can also
# import their own via "Import Theme..." next to the log panel, in either of
# two file formats:
#   1. native JSON -- the 9 required fields above (as hex colors), plus
#      optionally any of the 7 token-role fields.
#   2. a base16 scheme file (.yaml/.yml/.json with base00..base0F keys) --
#      the format used by base16 scheme repos such as chriskempson/base16 and
#      atelierbram/syntax-highlighting. These get remapped onto all 13 roles
#      using the standard base16 semantic convention (see _theme_from_base16
#      below). PyYAML is NOT required -- a small parser below handles the
#      flat "key: value" shape these scheme files use.
# ---------------------------------------------------------------------------

THEME_PRESETS = {
    "Dark (default)": {
        "background": "#141414",
        "foreground": "#e6e6e6",
        "select": "#094771",
        "section": "#5eb3ff",
        "warn": "#ffb454",
        "error": "#ff5c5c",
        "ok": "#5fd97f",
        "inserted": "#7ee0a0",
        "dim": "#8a8a8a",
        "key": "#9cdcfe",
        "string": "#ce9178",
        "number": "#b5cea8",
        "keyword": "#569cd6",
        "punct": "#8a8a8a",
        "tag": "#569cd6",
        "attr": "#9cdcfe",
        # chrome: the original hardcoded palette, so the default look is
        # byte-for-byte unchanged from before themes drove the chrome
        "chrome": {
            "bg": "#1e1e1e",
            "bg2": "#252526",
            "field_bg": "#2d2d30",
            "border": "#3f3f46",
            "fg": "#e6e6e6",
            "fg_dim": "#9a9a9a",
            "select": "#094771",
            "btn_bg": "#3a3a3d",
            "btn_bg_active": "#4a4a4e",
            "accent": "#3794ff",
        },
    },
    "Dracula": {
        "background": "#282a36",
        "foreground": "#f8f8f2",
        "select": "#44475a",
        "section": "#bd93f9",
        "warn": "#f1fa8c",
        "error": "#ff5555",
        "ok": "#50fa7b",
        "inserted": "#8be9fd",
        "dim": "#6272a4",
        "key": "#8be9fd",
        "string": "#50fa7b",
        "number": "#bd93f9",
        "keyword": "#ff79c6",
        "punct": "#6272a4",
        "tag": "#ff79c6",
        "attr": "#8be9fd",
        # chrome from the published Dracula UI palette (darker sidebar
        # #21222c, current-line #44475a, comment #6272a4)
        "chrome": {
            "bg": "#282a36",
            "bg2": "#21222c",
            "field_bg": "#343746",
            "border": "#44475a",
            "fg": "#f8f8f2",
            "fg_dim": "#6272a4",
            "select": "#44475a",
            "btn_bg": "#44475a",
            "btn_bg_active": "#6272a4",
            "accent": "#bd93f9",
        },
    },
    "Monokai": {
        "background": "#272822",
        "foreground": "#f8f8f2",
        "select": "#49483e",
        "section": "#66d9ef",
        "warn": "#e6db74",
        "error": "#f92672",
        "ok": "#a6e22e",
        "inserted": "#66d9ef",
        "dim": "#75715e",
        "key": "#66d9ef",
        "string": "#e6db74",
        "number": "#ae81ff",
        "keyword": "#f92672",
        "punct": "#75715e",
        "tag": "#f92672",
        "attr": "#a6e22e",
        # chrome from Monokai's editor palette (line-highlight #3e3d32,
        # selection #49483e, comment #75715e)
        "chrome": {
            "bg": "#272822",
            "bg2": "#1e1f1c",
            "field_bg": "#3e3d32",
            "border": "#49483e",
            "fg": "#f8f8f2",
            "fg_dim": "#75715e",
            "select": "#49483e",
            "btn_bg": "#49483e",
            "btn_bg_active": "#75715e",
            "accent": "#66d9ef",
        },
    },
    "Atom One Dark": {
        "background": "#282c34",
        "foreground": "#abb2bf",
        "select": "#3e4451",
        "section": "#61afef",
        "warn": "#e5c07b",
        "error": "#e06c75",
        "ok": "#98c379",
        "inserted": "#56b6c2",
        "dim": "#5c6370",
        "key": "#61afef",
        "string": "#98c379",
        "number": "#d19a66",
        "keyword": "#c678dd",
        "punct": "#5c6370",
        "tag": "#e06c75",
        "attr": "#d19a66",
        # chrome from Atom One Dark's UI palette (gutter #21252b,
        # cursor-line #2c313a, selection #3e4451)
        "chrome": {
            "bg": "#282c34",
            "bg2": "#21252b",
            "field_bg": "#2c313a",
            "border": "#3e4451",
            "fg": "#abb2bf",
            "fg_dim": "#5c6370",
            "select": "#3e4451",
            "btn_bg": "#3e4451",
            "btn_bg_active": "#4b5263",
            "accent": "#61afef",
        },
    },
    "Gruvbox Dark": {
        "background": "#282828",
        "foreground": "#ebdbb2",
        "select": "#3c3836",
        "section": "#83a598",
        "warn": "#fabd2f",
        "error": "#fb4934",
        "ok": "#b8bb26",
        "inserted": "#8ec07c",
        "dim": "#928374",
        "key": "#83a598",
        "string": "#b8bb26",
        "number": "#fe8019",
        "keyword": "#d3869b",
        "punct": "#928374",
        "tag": "#fb4934",
        "attr": "#fe8019",
        # chrome from the gruvbox dark palette (bg0_h #1d2021, bg1 #3c3836,
        # bg2 #504945, bg3 #665c54, gray #928374)
        "chrome": {
            "bg": "#282828",
            "bg2": "#1d2021",
            "field_bg": "#3c3836",
            "border": "#504945",
            "fg": "#ebdbb2",
            "fg_dim": "#928374",
            "select": "#3c3836",
            "btn_bg": "#504945",
            "btn_bg_active": "#665c54",
            "accent": "#83a598",
        },
    },
}

# native-format field -> accepted aliases, for a bit of tolerance in
# hand-written / hand-edited import files
_THEME_FIELD_ALIASES = {
    "background": ("background", "bg"),
    "foreground": ("foreground", "fg"),
    "select": ("select", "selection", "selectbackground"),
    "section": ("section", "header", "info"),
    "warn": ("warn", "warning"),
    "error": ("error", "err"),
    "ok": ("ok", "success", "good"),
    "inserted": ("inserted", "insert", "added"),
    "dim": ("dim", "muted", "comment"),
}

_THEME_REQUIRED = (
    "background",
    "foreground",
    "select",
    "section",
    "warn",
    "error",
    "ok",
    "inserted",
    "dim",
)

# optional JSON/HTML syntax-highlighting token roles -- used by the field-diff
# viewer. A theme missing these still works fine (see _json_syntax_colors,
# which falls back to the required roles above).
_THEME_OPTIONAL_FIELD_ALIASES = {
    "key": ("key", "property", "propertyname"),
    "string": ("string", "str"),
    "number": ("number", "num", "constant"),
    "keyword": ("keyword", "boolean"),
    "punct": ("punct", "punctuation", "bracket"),
    "tag": ("tag", "htmltag", "tagname"),
    "attr": ("attr", "attribute", "htmlattr"),
}


def _normalize_hex(value):
    v = str(value).strip()
    if not v:
        raise ValueError("empty color value")
    if not v.startswith("#"):
        v = "#" + v
    if len(v) == 4:  # shorthand #abc -> #aabbcc
        v = "#" + "".join(c * 2 for c in v[1:])
    import re as _re

    if not _re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        raise ValueError(f"{value!r} isn't a valid hex color (expected e.g. #282a36)")
    return v.lower()


def _parse_flat_kv_text(text):
    """A tiny parser for the flat 'key: value' shape base16 scheme YAML files
    use (scheme/author/base00..base0F, one per line, values plain or quoted).
    Deliberately not a general YAML parser -- just enough to read these
    single-level scheme files without requiring PyYAML to be installed."""
    data = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "---", "%")):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().strip('"').strip("'")
        v = v.strip().strip('"').strip("'")
        if v in ("", "|", ">"):
            continue  # skip block-scalar / nested-mapping starts, not used by base16 scheme files
        data[k] = v
    return data


def _theme_from_base16(data):
    """Standard base16 semantic role mapping: base00=bg, base05=fg,
    base02=selection, base08=red/error (also: variables/XML tags),
    base0A=yellow/warn, base0B=green/ok (also: strings), base0C=cyan/inserted
    (also: support/escapes), base0D=blue/section (also: functions/attribute
    IDs -- used here for JSON keys), base03=comment/dim (also: punctuation),
    base09=orange (integers/booleans/constants AND xml attributes -- used
    here for both 'number' and 'attr', per the base16 spec's own slot
    reuse), base0E=purple (keywords)."""
    need = (
        "base00",
        "base02",
        "base03",
        "base05",
        "base08",
        "base0A",
        "base0B",
        "base0C",
        "base0D",
    )
    if not all(k in data for k in need):
        return None
    theme = {
        "background": _normalize_hex(data["base00"]),
        "foreground": _normalize_hex(data["base05"]),
        "select": _normalize_hex(data["base02"]),
        "section": _normalize_hex(data["base0D"]),
        "warn": _normalize_hex(data["base0A"]),
        "error": _normalize_hex(data["base08"]),
        "ok": _normalize_hex(data["base0B"]),
        "inserted": _normalize_hex(data["base0C"]),
        "dim": _normalize_hex(data["base03"]),
        "key": _normalize_hex(data["base0D"]),
        "string": _normalize_hex(data["base0B"]),
        "punct": _normalize_hex(data["base03"]),
        "tag": _normalize_hex(data["base08"]),
    }
    if "base09" in data:
        theme["number"] = theme["attr"] = _normalize_hex(data["base09"])
    if "base0E" in data:
        theme["keyword"] = _normalize_hex(data["base0E"])
    # chrome from base16's standard UI-role slots when the scheme has them
    # (base01 = lighter background / status bars, base02 = selection,
    # base03 = comments, base04 = dark foreground / status text). Absent
    # those slots, chrome_from_theme() derives the chrome instead.
    if "base01" in data and "base04" in data:
        lighter = _normalize_hex(data["base01"])
        selection = theme["select"]
        theme["chrome"] = {
            "bg": theme["background"],
            "bg2": lighter,
            "field_bg": lighter,
            "border": selection,
            "fg": theme["foreground"],
            "fg_dim": _normalize_hex(data["base04"]),
            "select": selection,
            "btn_bg": selection,
            "btn_bg_active": theme["dim"],
            "accent": theme["section"],
        }
    return theme


def _theme_from_native(data):
    out = {}
    for field, aliases in _THEME_FIELD_ALIASES.items():
        for alias in aliases:
            if alias in data:
                out[field] = _normalize_hex(data[alias])
                break
    missing = [f for f in _THEME_REQUIRED if f not in out]
    if missing:
        return None, missing
    for field, aliases in _THEME_OPTIONAL_FIELD_ALIASES.items():
        for alias in aliases:
            if alias in data:
                try:
                    out[field] = _normalize_hex(data[alias])
                except ValueError:  # _normalize_hex's only raise
                    pass
                break
    # optional explicit chrome (window/button) colours -- any subset of the
    # 10 chrome keys; anything missing or invalid is derived instead
    raw_chrome = data.get("chrome")
    if isinstance(raw_chrome, dict):
        chrome = {}
        for key in _CHROME_KEYS:
            if key in raw_chrome:
                try:
                    chrome[key] = _normalize_hex(raw_chrome[key])
                except ValueError:  # _normalize_hex's only raise
                    pass
        if chrome:
            out["chrome"] = chrome
    return out, []


def parse_theme_file(path):
    """Reads a theme file (.json, .yaml/.yml, or extensionless) and returns
    (name, theme_dict). Raises ValueError with a human-readable reason on any
    format problem. Tries, in order: JSON parse then native-field mapping or
    base16 mapping; falling back to a flat key:value parse for non-JSON
    (e.g. base16 .yaml scheme files) with the same two mappings."""
    import json as _json

    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    name = p.stem.replace("_", " ").replace("-", " ").strip().title() or "Imported Theme"

    data = None
    try:
        parsed = _json.loads(text)
        if isinstance(parsed, dict):
            data = parsed
    except ValueError:  # JSONDecodeError subclasses ValueError
        data = None
    if data is None:
        data = _parse_flat_kv_text(text)
    if not data:
        raise ValueError("Couldn't find any 'key: value' pairs in that file.")

    for key in ("name", "scheme"):
        if isinstance(data.get(key), str) and data[key].strip():
            name = data[key].strip()
            break

    theme, missing = _theme_from_native(data)
    if theme is not None:
        return name, theme

    b16 = _theme_from_base16(data)
    if b16 is not None:
        return name, b16

    raise ValueError(
        "Not a recognized theme file. Expected either the native format "
        "(background/foreground/select/section/warn/error/ok/inserted/dim, "
        "as hex colors) or a base16 scheme (base00..base0F).\n\n"
        f"Missing native fields: {', '.join(missing) if missing else '(all)'}"
    )


def _json_syntax_colors(theme):
    """The 7 JSON/HTML token-role colors for a theme, falling back to its
    required log-panel roles for any that are missing (so an older/plainer
    imported theme -- or one hand-written without the optional fields --
    still gets a coherent, if less differentiated, set of colors here)."""
    return {
        "key": theme.get("key", theme["section"]),
        "string": theme.get("string", theme["ok"]),
        "number": theme.get("number", theme["warn"]),
        "keyword": theme.get("keyword", theme["error"]),
        "punct": theme.get("punct", theme["dim"]),
        "tag": theme.get("tag", theme["error"]),
        "attr": theme.get("attr", theme["warn"]),
    }


# ---------------------------------------------------------------------------
# theme -> chrome bridge (task #43). A syntax theme carries 9 required text
# roles but no window/button colours, so the 11 chrome keys in DARK are
# produced from a theme in two layers:
#   1. derived: fg/fg_dim/select/accent map straight onto foreground/dim/
#      select/section; the five background-family keys (bg, bg2, field_bg,
#      btn_bg, btn_bg_active) and border are computed by shifting the theme's
#      `background` toward white (dark themes) or black (light themes) by
#      fixed fractions, chosen to reproduce the built-in dark palette's
#      spacing. This is the fallback and covers every imported theme.
#   2. hand-tuned: a theme may carry an optional "chrome" dict giving any of
#      the 10 keys explicitly. The built-in presets do (using each scheme's
#      published UI colours), base16 imports get one from base00..base04's
#      standard UI-role semantics, and native-JSON imports may supply one.
# ---------------------------------------------------------------------------

# fraction of the way from `background` toward white/black for each derived
# chrome key; values reproduce DARK's spacing from the default background
_CHROME_SHIFTS = {
    "bg": 0.04,
    "bg2": 0.07,
    "field_bg": 0.10,
    "btn_bg": 0.15,
    "border": 0.17,
    "btn_bg_active": 0.21,
}

_CHROME_KEYS = (
    "bg",
    "bg2",
    "field_bg",
    "border",
    "fg",
    "fg_dim",
    "select",
    "btn_bg",
    "btn_bg_active",
    "accent",
    "log_bg",
)


def _mix_hex(color: str, target: str, fraction: float) -> str:
    """Blend a ``#rrggbb`` color toward ``target`` by ``fraction`` (0..1)."""
    mixed = (
        round(a + (b - a) * fraction)
        for a, b in zip(
            (int(color[i : i + 2], 16) for i in (1, 3, 5)),
            (int(target[i : i + 2], 16) for i in (1, 3, 5)),
        )
    )
    return "#" + "".join(f"{c:02x}" for c in mixed)


def _is_light_color(color: str) -> bool:
    """True if a ``#rrggbb`` color is perceptually light (ITU-R 601 luma)."""
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) > 127


def chrome_from_theme(theme: dict) -> dict[str, str]:
    """The 11 chrome (window/widget) colours for a syntax theme.

    Derives the background-family keys from ``background`` (lightening on
    dark themes, darkening on light ones) and maps the text roles directly,
    then applies any explicit overrides from the theme's optional "chrome"
    dict. Invalid override values are ignored rather than fatal, since
    hand-edited log_themes.json entries reach here unvalidated.
    """
    base = theme["background"]
    target = "#000000" if _is_light_color(base) else "#ffffff"
    chrome = {key: _mix_hex(base, target, frac) for key, frac in _CHROME_SHIFTS.items()}
    chrome["fg"] = theme["foreground"]
    chrome["fg_dim"] = theme["dim"]
    chrome["select"] = theme["select"]
    chrome["accent"] = theme["section"]
    chrome["log_bg"] = base  # console-style text areas match the log exactly
    overrides = theme.get("chrome")
    if isinstance(overrides, dict):
        for key in _CHROME_KEYS:
            if key in overrides:
                try:
                    chrome[key] = _normalize_hex(overrides[key])
                except (ValueError, TypeError):
                    pass
    return chrome


# the syntax theme the chrome palette was last derived from. The runtime
# re-apply walk needs the *theme* (not just the derived chrome) to recolour
# the syntax-highlight tags in any open field-diff viewer.
_ACTIVE_THEME: dict = THEME_PRESETS["Dark (default)"]


def set_active_chrome(theme: dict) -> None:
    """Point the active chrome palette (``DARK``) at ``theme``, in place.

    Mutating in place is deliberate: the ~106 ``DARK[...]`` read sites then
    see the new colours without touching any of them. Widgets built after
    this pick the palette up automatically; *live* widgets are recoloured by
    ``restyle_widget_tree`` (called from ``App._reapply_chrome``), which also
    reads the remembered ``_ACTIVE_THEME`` for syntax-tag colours.
    """
    global _ACTIVE_THEME
    _ACTIVE_THEME = theme
    DARK.update(chrome_from_theme(theme))


def _restyle_syntax_tags(widget) -> None:
    """Re-colour a Text widget's field-diff syntax tags, if it has any.

    The diff viewer's token tags (json_key/json_string/html_tag/...) are
    configured once when the window opens; without this, a theme switch
    left an open viewer's tokens in the old theme's colours even though its
    chrome and background followed the new one. ``style_json_syntax_tags``
    is written as a (re-)configure, so calling it again is all it takes.
    """
    try:
        if "json_string" not in widget.tag_names():
            return  # not a syntax-highlighted pane (log/preview/detail)
        style_json_syntax_tags(widget, _json_syntax_colors(_ACTIVE_THEME))
    except tk.TclError:
        pass


def _restyle_combobox_popdown(widget) -> None:
    """Recolour a combobox's dropdown list, if it has been created.

    The dropdown is a plain ``tk::Listbox`` that only reads the option
    database when it is first built, so a live one must be reconfigured
    directly through Tk (there is no ttk.Style route to it).
    """
    try:
        popdown = str(widget.tk.call("ttk::combobox::PopdownWindow", widget))
    except tk.TclError:
        return
    listbox_path = popdown + ".f.l"
    for opt, val in (
        ("-background", DARK["field_bg"]),
        ("-foreground", DARK["fg"]),
        ("-selectbackground", DARK["select"]),
        ("-selectforeground", DARK["fg"]),
    ):
        try:
            widget.tk.call(listbox_path, "configure", opt, val)
        except tk.TclError:
            pass


def _configure_each(widget, options: dict) -> None:
    """Apply options one at a time, skipping any the widget doesn't support.

    Same pattern (and same reason) as style_plain_widget: one unsupported
    option -- or a widget destroyed mid-loop -- must not blank the rest.
    """
    for opt, val in options.items():
        try:
            widget.configure(**{opt: val})
        except tk.TclError:
            pass


def _restyle_plain_live(w) -> bool:
    """Re-apply the active chrome to one live widget; True if it was handled.

    ttk widgets return False -- the re-configured ttk.Style already reaches
    them -- except Combobox, whose already-built dropdown needs direct help.
    """
    handled = True
    if isinstance(w, ttk.Combobox):
        _restyle_combobox_popdown(w)
    elif isinstance(w, ttk.Widget):
        handled = False
    elif isinstance(w, (tk.Tk, tk.Toplevel)):
        _configure_each(w, {"bg": DARK["bg"]})
    elif isinstance(w, tk.Text):
        # every plain Text/ScrolledText in this app is a console-style pane;
        # the log panel itself is immediately re-coloured again (to the same
        # value) by _apply_log_theme after the walk
        style_plain_widget(w)
        _configure_each(w, {"background": DARK["log_bg"]})
        _restyle_syntax_tags(w)
    elif isinstance(w, tk.Listbox):
        style_plain_widget(w)
    elif isinstance(w, tk.Canvas):
        # the pane-divider grips: repaint the canvas and its drawn lines
        _configure_each(w, {"bg": DARK["btn_bg"], "highlightbackground": DARK["border"]})
        try:
            for item in w.find_all():
                w.itemconfigure(item, fill=DARK["fg_dim"])
        except tk.TclError:
            pass
    elif isinstance(w, tk.Scrollbar):
        # the scrollbar ScrolledText builds for itself is plain tk
        _configure_each(
            w,
            {
                "background": DARK["btn_bg"],
                "troughcolor": DARK["bg2"],
                "activebackground": DARK["btn_bg_active"],
                "highlightbackground": DARK["bg"],
            },
        )
    elif isinstance(w, tk.Label):
        # tooltip label
        _configure_each(w, {"background": DARK["field_bg"], "foreground": DARK["fg"]})
    elif isinstance(w, tk.Frame):
        # e.g. the frame ScrolledText wraps itself in
        _configure_each(w, {"bg": DARK["bg"]})
    else:
        handled = False
    return handled


def restyle_widget_tree(widget) -> int:
    """Recursively re-apply the active chrome to a live widget tree.

    Returns the number of widgets restyled (traced, for the smoke test).
    Toplevels created with ``root`` as master are children of ``root`` in
    Tk's hierarchy, so one walk from the main window reaches every open
    window. Widgets are created and destroyed dynamically, so both the
    existence check and every configure tolerate ``TclError``.
    """
    try:
        if not widget.winfo_exists():
            return 0
    except tk.TclError:
        return 0
    count = 1 if _restyle_plain_live(widget) else 0
    try:
        children = widget.winfo_children()
    except tk.TclError:
        return count
    for child in children:
        count += restyle_widget_tree(child)
    return count


# JSON token regex: strings (used for both keys and values -- disambiguated
# by what follows), numbers, true/false/null, and the structural punctuation.
# Whitespace and anything else is left untagged (plain foreground).
_JSON_TOKEN_RE = re.compile(
    r'(?P<string>"(?:\\.|[^"\\])*")'
    r"|(?P<number>-?\d+\.?\d*(?:[eE][+-]?\d+)?)"
    r"|(?P<keyword>\btrue\b|\bfalse\b|\bnull\b)"
    r"|(?P<punct>[{}\[\]:,])"
)

# a loose HTML-ish tag matcher for markup embedded *inside* JSON string
# values -- Morrowind book/dialogue text uses tags like <DIV ALIGN="left">,
# <FONT COLOR="FFFFFF">, <BR>, <P>. Not a real HTML parser (doesn't need to
# be); just enough to color tag names, attribute names, and attribute values
# distinctly from the surrounding string text.
_HTML_TAG_RE = re.compile(
    r"</?\s*([a-zA-Z][\w:-]*)"
    r'((?:\s+[a-zA-Z_:][\w:.-]*(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+))?)*)'
    r"\s*/?>"
)
_HTML_ATTR_RE = re.compile(r'([a-zA-Z_:][\w:.-]*)(\s*=\s*)("[^"]*"|\'[^\']*\'|[^\s>]+)?')


def _tag_embedded_html(text_widget, text, base, idx):
    """Tags any HTML-ish markup found in `text` (a slice starting at absolute
    character offset `base` within whatever was inserted into text_widget)
    with html_tag/html_attr/html_value/html_punct. `idx(absolute_pos)` must
    return a Tk Text index. Shared by highlight_json_with_html (HTML nested
    inside a JSON string token) and highlight_plain_text_with_html (HTML in
    a field shown as its own raw string, no surrounding JSON quoting)."""
    for tm in _HTML_TAG_RE.finditer(text):
        name_s, name_e = tm.span(1)
        attr_s, attr_e = tm.span(2)
        text_widget.tag_add("html_punct", idx(base + tm.start()), idx(base + name_s))
        text_widget.tag_add("html_tag", idx(base + name_s), idx(base + name_e))
        if attr_e > attr_s:
            blob = text[attr_s:attr_e]
            for am in _HTML_ATTR_RE.finditer(blob):
                an_s, an_e = am.span(1)
                text_widget.tag_add(
                    "html_attr", idx(base + attr_s + an_s), idx(base + attr_s + an_e)
                )
                if am.group(3):
                    av_s, av_e = am.span(3)
                    text_widget.tag_add(
                        "html_value", idx(base + attr_s + av_s), idx(base + attr_s + av_e)
                    )
        text_widget.tag_add("html_punct", idx(base + attr_e), idx(base + tm.end()))


def highlight_json_with_html(text_widget, text, colors):
    """Tags `text` (already inserted into `text_widget`, a normal-state Text
    widget) as JSON, with any HTML-ish markup inside string values further
    broken out and colored. `colors` is a _json_syntax_colors(...) dict.
    Tag *styles* (tag_configure) must already be set on text_widget by the
    caller -- this only calls tag_add."""

    def idx(pos):
        return text_widget.index(f"1.0 + {pos} chars")

    for m in _JSON_TOKEN_RE.finditer(text):
        kind = m.lastgroup
        s, e = m.start(), m.end()
        if kind == "string":
            j = e
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            is_key = j < len(text) and text[j] == ":"
            text_widget.tag_add("json_key" if is_key else "json_string", idx(s), idx(e))
            if is_key:
                continue
            _tag_embedded_html(text_widget, text[s:e], s, idx)
        elif kind == "number":
            text_widget.tag_add("json_number", idx(s), idx(e))
        elif kind == "keyword":
            text_widget.tag_add("json_keyword", idx(s), idx(e))
        elif kind == "punct":
            text_widget.tag_add("json_punct", idx(s), idx(e))

    # html_* spans sit inside json_string spans -- make sure they win
    for t in ("html_punct", "html_tag", "html_attr", "html_value"):
        try:
            text_widget.tag_raise(t)
        except tk.TclError:
            pass


def highlight_plain_text_with_html(text_widget, text, colors):
    """For a field that's a plain string being shown as its own raw content
    (see _show_field_detail) -- NOT run through json.dumps, so there's no
    surrounding quotes and no \\" / \\\\ / \\n escaping to fight through.
    That's the whole point: json.dumps-ing a book-text field just to display
    it turns every embedded quote in '<FONT COLOR=\"000000\">' into visual
    noise for no benefit, since nothing here is being re-parsed as JSON.
    Colors the whole span with the theme's string color, then layers embedded
    HTML-ish markup (if any) on top, exactly like a JSON string value would
    get inside highlight_json_with_html."""

    def idx(pos):
        return text_widget.index(f"1.0 + {pos} chars")

    text_widget.tag_add("json_string", idx(0), idx(len(text)))
    _tag_embedded_html(text_widget, text, 0, idx)
    for t in ("html_punct", "html_tag", "html_attr", "html_value"):
        try:
            text_widget.tag_raise(t)
        except tk.TclError:
            pass


def style_json_syntax_tags(text_widget, colors):
    """(Re-)configures the tag_configure styles used by highlight_json_with_html
    and highlight_plain_text_with_html. Call once per Text widget before/after
    inserting -- tag_add doesn't need the style to exist yet, but nothing
    will be visible until this runs."""
    text_widget.tag_configure("json_key", foreground=colors["key"])
    text_widget.tag_configure("json_string", foreground=colors["string"])
    text_widget.tag_configure("json_number", foreground=colors["number"])
    text_widget.tag_configure("json_keyword", foreground=colors["keyword"])
    text_widget.tag_configure("json_punct", foreground=colors["punct"])
    text_widget.tag_configure(
        "html_tag", foreground=colors["tag"], font=("TkFixedFont", 10, "bold")
    )
    text_widget.tag_configure("html_attr", foreground=colors["attr"])
    text_widget.tag_configure("html_value", foreground=colors["string"])
    text_widget.tag_configure("html_punct", foreground=colors["punct"])


# ---------------------------------------------------------------------------
# a small hover tooltip -- delayed popup, dark-themed to match the rest of
# the app. Works on any widget (ttk or plain tk).
# ---------------------------------------------------------------------------


class Tooltip:
    def __init__(self, widget, text, delay=450, wraplength=320):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self.tip_window = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text):
        self.text = text

    def _schedule(self, event=None):
        self._unschedule()
        try:
            self._after_id = self.widget.after(self.delay, self._show)
        except tk.TclError:
            pass

    def _unschedule(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self):
        if self.tip_window or not self.text:
            return
        try:
            wx = self.widget.winfo_rootx()
            wy = self.widget.winfo_rooty()
            wh = self.widget.winfo_height()
        except tk.TclError:
            return
        tw = tk.Toplevel(self.widget)
        self.tip_window = tw
        tw.wm_overrideredirect(True)
        try:
            tw.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(
            tw,
            text=self.text,
            justify="left",
            background=DARK["field_bg"],
            foreground=DARK["fg"],
            relief="solid",
            borderwidth=1,
            wraplength=self.wraplength,
            font=("TkDefaultFont", 9),
            padx=6,
            pady=4,
        ).pack()
        # Position AFTER the label exists so we know the real size, then clamp
        # to the screen so a tooltip on a right-edge widget (fullscreen) isn't
        # cut off. Preferred spot is below-left of the widget; flip/slide back
        # onto the screen when it would overflow.
        try:
            tw.update_idletasks()
            tw_w, tw_h = tw.winfo_reqwidth(), tw.winfo_reqheight()
            sw, sh = tw.winfo_screenwidth(), tw.winfo_screenheight()
            margin = 8
            x = wx + 14
            if x + tw_w > sw - margin:
                x = sw - margin - tw_w  # slide left to fit
            x = max(margin, x)
            y = wy + wh + 6
            if y + tw_h > sh - margin:
                y = wy - tw_h - 6  # not enough room below -> above
            y = max(margin, y)
        except tk.TclError:
            x, y = wx + 14, wy + wh + 6
        tw.wm_geometry(f"+{x}+{y}")

    def _hide(self, event=None):
        self._unschedule()
        if self.tip_window is not None:
            try:
                self.tip_window.destroy()
            except tk.TclError:
                pass
            self.tip_window = None


def add_tooltip(widget, text):
    return Tooltip(widget, text)


# ---------------------------------------------------------------------------
# a stdout/stderr-compatible stream that pushes chunks into a thread-safe
# queue instead of writing to a real terminal, so the worker thread can
# write freely and the UI thread can drain it on its own schedule
# ---------------------------------------------------------------------------


class QueueWriter(io.TextIOBase):
    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(s)
        return len(s)

    def flush(self):
        pass


# ---------------------------------------------------------------------------
# small reusable "path field": label + entry + Browse button, optionally
# a drag-and-drop target
# ---------------------------------------------------------------------------


class PathField:
    def __init__(
        self,
        parent,
        label,
        row,
        var,
        browse_kind="open",
        filetypes=(("All files", "*.*"),),
        on_drop_extra=None,
        tooltip=None,
        extra_button=None,
    ):
        """browse_kind: 'open', 'save', or 'dir'.
        extra_button: optional (text, command, tooltip) for a button placed to
        the right of Browse (e.g. a 'Scan...' action on the subset-file row)."""
        self.var = var
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        self.entry = entry

        def browse():
            if browse_kind == "save":
                path = filedialog.asksaveasfilename(filetypes=filetypes, defaultextension=".toml")
            elif browse_kind == "dir":
                path = filedialog.askdirectory()
            else:
                path = filedialog.askopenfilename(filetypes=filetypes)
            if path:
                var.set(path)

        self.extra_btn = None
        if extra_button:
            # keep everything inside column 2 (a small button bar) so rows that
            # span columns 0-2 below still line up -- no stray 4th column
            btnbar = ttk.Frame(parent)
            btnbar.grid(row=row, column=2, padx=(8, 0), pady=4, sticky="e")
            browse_btn = ttk.Button(btnbar, text=_("Browse..."), command=browse)
            browse_btn.pack(side="left")
            ex_text, ex_cmd = extra_button[0], extra_button[1]
            ex_tip = extra_button[2] if len(extra_button) > 2 else None
            self.extra_btn = ttk.Button(btnbar, text=ex_text, command=ex_cmd)
            self.extra_btn.pack(side="left", padx=(6, 0))
            if ex_tip:
                add_tooltip(self.extra_btn, ex_tip)
        else:
            browse_btn = ttk.Button(parent, text=_("Browse..."), command=browse)
            browse_btn.grid(row=row, column=2, padx=(8, 0), pady=4)
        self.browse_btn = browse_btn

        if tooltip:
            add_tooltip(label_widget, tooltip)
            add_tooltip(entry, tooltip)
            add_tooltip(browse_btn, tooltip)

        if HAVE_DND:
            entry.drop_target_register(DND_FILES)

            def on_drop(event):
                paths = parent.tk.splitlist(event.data)
                if paths:
                    var.set(paths[0])
                if on_drop_extra:
                    on_drop_extra(paths)

            entry.dnd_bind("<<Drop>>", on_drop)

    def set_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.entry.configure(state=state)
        self.browse_btn.configure(state=state)


# ---------------------------------------------------------------------------
# a Listbox you can reorder by clicking and dragging items up/down with the
# mouse, on top of Listbox's normal behavior (selection, scrolling, etc).
# This is separate from tkinterdnd2 drag & drop, which is for dragging files
# in *from the OS* -- reordering items already in the list needs nothing
# but plain tkinter mouse events, so it works even without tkinterdnd2.
# ---------------------------------------------------------------------------


class DragReorderListbox(tk.Listbox):
    def __init__(self, *args, on_reorder=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_reorder = on_reorder
        self._drag_block = None  # list of (contiguous) indices being dragged
        self._moved = False
        self.bind("<Button-1>", self._on_press, add="+")
        self.bind("<B1-Motion>", self._on_motion, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")

    def _on_press(self, event):
        idx = self.nearest(event.y)
        self._moved = False
        if not (0 <= idx < self.size()):
            self._drag_block = None
            return None
        # This widget-level binding runs BEFORE Listbox's own class binding, so
        # curselection() here is still the PRE-click selection. If the pressed
        # row is part of a contiguous multi-selection, drag the whole block and
        # return "break" to stop the default handler from collapsing it.
        sel = list(self.curselection())
        contiguous = bool(sel) and sel == list(range(sel[0], sel[-1] + 1))
        if len(sel) > 1 and contiguous and idx in sel:
            self._drag_block = sel
            return "break"
        self._drag_block = [idx]
        return None  # let Listbox's own click handling run

    def _on_motion(self, event):
        if not self._drag_block:
            return
        target = self.nearest(event.y)
        if not (0 <= target < self.size()):
            return
        if target < self._drag_block[0]:
            self._shift(-1)
        elif target > self._drag_block[-1]:
            self._shift(1)

    def _shift(self, direction):
        block, size = self._drag_block, self.size()
        if (direction < 0 and block[0] <= 0) or (direction > 0 and block[-1] >= size - 1):
            return
        order = block if direction < 0 else list(reversed(block))
        for i in order:
            t = self.get(i)
            self.delete(i)
            self.insert(i + direction, t)
        self._drag_block = [i + direction for i in block]
        self.selection_clear(0, "end")
        for i in self._drag_block:
            self.selection_set(i)
        self.see(self._drag_block[0] if direction < 0 else self._drag_block[-1])
        self._moved = True

    def _on_release(self, event):
        if self._moved and self.on_reorder:
            trace_first_fire("listbox drag-reorder -> on_reorder")
            core.trace(f"[smoke] drag-reorder committed: {self.size()} row(s) now listed")
            self.on_reorder()
        self._drag_block = None
        self._moved = False


def _app_version() -> str:
    """The running build's version string, or ``?`` if it can't be determined."""
    try:
        from mlox_subset import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        # a version stamp must never be the thing that stops the app starting
        return "?"


def _build_stamp() -> str:
    """Identifies *which* build is running: frozen exe vs source, and its mtime.

    Exists because a stale .exe presents exactly like a code bug -- new source
    on disk, old behaviour on screen, and nothing in the log to tell them
    apart. Comparing this timestamp against the source tree settles it.
    """
    from datetime import datetime as _dt

    frozen = bool(getattr(sys, "frozen", False))
    target = Path(sys.executable) if frozen else Path(__file__)
    try:
        mtime = _dt.fromtimestamp(target.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        mtime = "?"
    return f"frozen={frozen} built={mtime} path={target.name}"


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
    core.trace(f"[smoke] callback fired for the first time: {label}")


# ---------------------------------------------------------------------------
# rule-file list: an ordered listbox (priority = order, last = highest,
# matching mlox_subset_sort's own --rules semantics) with add/remove/reorder
# controls and its own drop target
# ---------------------------------------------------------------------------


class RuleFilesPanel:
    def __init__(self, parent, row, on_new_rule=None, on_sources=None, get_rules_url=None):
        self._get_rules_url = get_rules_url
        frame = ttk.LabelFrame(
            parent,
            text=_("Rule files (priority = order below, last = highest -- drag rows to reorder)"),
        )
        frame.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=(8, 4))
        frame.columnconfigure(0, weight=1)

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)  # let the listbox grow down to the buttons' bottom

        # single-select: dragging a multi-selection to a new spot is
        # ambiguous (which item does the cursor "carry"?), so keep it to one
        # row at a time -- Move Up/Down below still work with a single row too
        self.listbox = DragReorderListbox(
            list_frame, height=5, selectmode="browse", activestyle="dotbox", exportselection=False
        )
        style_plain_widget(self.listbox)
        attach_typeahead(self.listbox)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        add_tooltip(
            self.listbox,
            "mlox rule files (mlox_base.txt, mlox_user.txt, ...), applied in this order.\n"
            "Later files can override/extend earlier ones -- put mlox_base.txt first and "
            "your own mlox_user.txt last. Drag rows to reorder, or use the buttons.",
        )
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)

        # side column: just the list-management buttons (add / remove /
        # reorder). The rule ACTIONS (New Rule / Update Rules / Sources) go in
        # a horizontal row under the list so the side column stays short and
        # doesn't create a tall dead-space next to a small list.
        btns = ttk.Frame(frame)
        btns.grid(row=0, column=1, sticky="n", padx=(0, 8), pady=8)
        add_btn = ttk.Button(btns, text=_("Add File(s)..."), command=self.add_files)
        add_btn.pack(fill="x", pady=2)
        add_tooltip(add_btn, _("Browse for one or more mlox rule .txt files to add to the list."))
        remove_btn = ttk.Button(btns, text=_("Remove Selected"), command=self.remove_selected)
        remove_btn.pack(fill="x", pady=2)
        add_tooltip(
            remove_btn,
            _("Remove the selected rule file from the list (doesn't delete anything on disk)."),
        )
        up_btn = ttk.Button(btns, text=_("Move Up"), command=lambda: self.move(-1))
        up_btn.pack(fill="x", pady=2)
        add_tooltip(up_btn, _("Move the selected rule file earlier (lower priority)."))
        down_btn = ttk.Button(btns, text=_("Move Down"), command=lambda: self.move(1))
        down_btn.pack(fill="x", pady=2)
        add_tooltip(down_btn, _("Move the selected rule file later (higher priority)."))

        actions = ttk.Frame(frame)
        actions.grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(3, 6))
        if on_new_rule is not None:
            new_btn = ttk.Button(actions, text=_("New Rule..."), command=on_new_rule)
            new_btn.pack(side="left", padx=(0, 6))
            add_tooltip(
                new_btn,
                "Write your own mlox [Order]/[NearStart]/[NearEnd] rule without knowing "
                "the syntax: pick plugins (or grab the selected rows from the plugin "
                "panel), preview the rule, and append it to a personal rules file that "
                "loads LAST so it wins conflicts. This is how rules for modern mods get "
                "made -- consider contributing good ones upstream.",
            )
        upd_btn = ttk.Button(actions, text=_("Update Rules..."), command=self._update_rules)
        upd_btn.pack(side="left", padx=(0, 6))
        add_tooltip(
            upd_btn,
            "Download the CURRENT mlox_base.txt / mlox_user.txt from the actively "
            f"maintained rules repo (github.com/{core.RULES_REPO} -- the same source "
            "plox uses, and mlox 1.1+ auto-updates from) over the matching files in "
            "this list. The old files are kept as timestamped .bak copies. Files "
            "with other names (your personal rules) are never touched. Source URL "
            "configurable via Sources...",
        )
        if on_sources is not None:
            src_btn = ttk.Button(actions, text=_("Sources..."), command=on_sources)
            src_btn.pack(side="left", padx=(0, 6))
            add_tooltip(
                src_btn,
                "Configure WHERE 'Update Rules...' and the plugin-order.yml "
                "'Update...' button download from -- point them at a fork or "
                "mirror if upstream moves. Blank fields use the built-in defaults.",
            )

        if HAVE_DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self._on_drop)
        else:
            ttk.Label(
                actions,
                text=_("(install tkinterdnd2 to drag files in from your file manager)"),
                foreground=DARK["fg_dim"],
            ).pack(side="left", padx=(12, 0))

    def _update_rules(self):
        paths = self.get_paths()
        managed = [p for p in paths if Path(p).name.lower() in ("mlox_base.txt", "mlox_user.txt")]
        if not managed:
            messagebox.showinfo(
                _("Update rules"), _("Add mlox_base.txt and/or mlox_user.txt to the list first.")
            )
            return
        ages = core.rule_file_ages(managed)
        age_txt = "\n".join(
            f"  {n}: {'age unknown' if d is None else f'~{d} day(s) old'}" for n, d in ages
        )
        if not messagebox.askyesno(
            _("Update rules"),
            f"Download the current rules from github.com/{core.RULES_REPO} over these "
            f"files?\n\n{age_txt}\n\nTimestamped .bak copies of the old files are kept.",
        ):
            return

        custom = (self._get_rules_url() if self._get_rules_url else "") or None

        def work():
            try:
                report = core.update_rule_files(managed, url_template=custom)
            except Exception as e:  # noqa: BLE001
                # worker thread: must report into the dialog, never vanish silently
                report = [f"FAILED: {e}"]
            self.listbox.after(0, lambda: messagebox.showinfo(_("Update rules"), "\n".join(report)))

        threading.Thread(target=work, daemon=True).start()

    def _on_drop(self, event):
        for p in self.listbox.tk.splitlist(event.data):
            self.listbox.insert("end", p)

    def add_files(self):
        paths = filedialog.askopenfilenames(
            filetypes=(("mlox rule files", "*.txt"), ("All files", "*.*"))
        )
        for p in paths:
            self.listbox.insert("end", p)

    def remove_selected(self):
        for idx in reversed(self.listbox.curselection()):
            self.listbox.delete(idx)

    def move(self, direction):
        sel = list(self.listbox.curselection())
        if not sel:
            return
        indices = sel if direction < 0 else list(reversed(sel))
        for idx in indices:
            new_idx = idx + direction
            if 0 <= new_idx < self.listbox.size():
                text = self.listbox.get(idx)
                self.listbox.delete(idx)
                self.listbox.insert(new_idx, text)
                self.listbox.selection_set(new_idx)

    def get_paths(self):
        return [Path(p) for p in self.listbox.get(0, "end")]


# ---------------------------------------------------------------------------
# generic draggable-list panel: a titled list with Move Up/Down + Reset,
# used for both the plugin load order and the data= path order. Items
# matching highlighted_items (case-insensitive) get a highlighted background
# so it's obvious what a sort actually touched vs. what was already correct.
# Dragging rows here never re-runs anything -- it's a manual override of a
# computed order, applied at Export time.
# ---------------------------------------------------------------------------


def attach_typeahead(listbox, strip=None, feedback=None):
    """Windows-Explorer-style type-to-jump for a Listbox: type letters to jump
    to the first row whose name starts with what you typed (falls back to a
    substring match), press one letter repeatedly to cycle through its
    matches, Backspace edits, Esc clears. The buffer resets after a short
    pause. `strip` maps display text back to the real name; `feedback` (if
    given) is called with the current buffer for a UI hint."""
    import time as _time

    st = {"buf": "", "at": 0.0, "after": None}
    strip = strip or (lambda s: s)

    def _feedback():
        if feedback:
            try:
                feedback(st["buf"])
            except Exception:  # noqa: BLE001
                # caller-supplied feedback callback into Tk; purely cosmetic
                pass

    def _clear(_e=None):
        st["buf"] = ""
        _feedback()

    def _schedule_reset():
        if st["after"] is not None:
            try:
                listbox.after_cancel(st["after"])
            except tk.TclError:  # after_cancel on a stale/expired id
                pass
        st["after"] = listbox.after(1200, _clear)

    def _jump(idx):
        listbox.selection_clear(0, "end")
        listbox.selection_set(idx)
        listbox.activate(idx)
        listbox.see(idx)
        listbox.event_generate("<<ListboxSelect>>")

    def _on_key(e):
        ks = e.keysym
        if ks == "Escape":
            _clear()
            return "break"
        if ks == "BackSpace":
            if st["buf"]:
                st["buf"] = st["buf"][:-1]
                st["at"] = _time.time()
                _feedback()
                _schedule_reset()
                return "break"
            return None
        ch = e.char
        if not ch or not ch.isprintable() or (e.state & 0x0004):  # ignore Ctrl-chords
            return None
        now = _time.time()
        if now - st["at"] > 1.2:
            st["buf"] = ""
        # leaving single-key cycling: start a fresh buffer with the new key
        if len(st["buf"]) > 1 and set(st["buf"]) == {st["buf"][0]} and ch.lower() != st["buf"][0]:
            st["buf"] = ""
        st["at"] = now
        cl = ch.lower()
        items = [strip(listbox.get(i)).lower() for i in range(listbox.size())]
        if st["buf"] and set(st["buf"]) == {cl}:
            # same key again: cycle through rows starting with that letter
            st["buf"] += cl
            cur = listbox.curselection()
            start = (cur[0] + 1) if cur else 0
            for i in list(range(start, len(items))) + list(range(start)):
                if items[i].startswith(cl):
                    _jump(i)
                    break
        else:
            st["buf"] += cl
            buf = st["buf"]
            hit = next((i for i, s in enumerate(items) if s.startswith(buf)), None)
            if hit is None:
                hit = next((i for i, s in enumerate(items) if buf in s), None)
            if hit is not None:
                _jump(hit)
        _feedback()
        _schedule_reset()
        return "break"

    listbox.bind("<KeyPress>", _on_key, add="+")


class ReorderPanel:
    # "touched by this sort" row highlight. Deliberately a warm amber rather
    # than a blue -- blue was both low-contrast against the dark field bg and
    # easily confused with the blue selection highlight (#094771). Amber on
    # near-black reads clearly and never collides with the selection color.
    HIGHLIGHT: ClassVar[dict[str, str]] = {"background": "#8a0808", "foreground": "#ffe8c2"}

    # NORMAL/DISABLED are methods rather than ClassVars: reading DARK at class
    # definition time would freeze the startup palette, and a runtime theme
    # switch would then restyle rows with stale colours.
    @staticmethod
    def _row_normal() -> dict[str, str]:
        return {"background": DARK["field_bg"], "foreground": DARK["fg"]}

    @staticmethod
    def _row_disabled() -> dict[str, str]:
        # dimmer than fg_dim: mixed toward the field background (under the
        # default theme this lands within 1/255 of the old hardcoded #6a6a6a)
        return {
            "background": DARK["field_bg"],
            "foreground": _mix_hex(DARK["fg_dim"], DARK["field_bg"], 0.44),
        }

    # rows with an active problem (e.g. missing/mis-ordered master): vivid
    # purple with white text -- the only strong hue not already meaning
    # something in this app (red = touched by this sort, gold = your mods on
    # the cell map, navy = selection, grey = disabled, orange = log warnings),
    # and it pops against all of them
    ERROR: ClassVar[dict[str, str]] = {"background": "#8e24aa", "foreground": "#ffffff"}
    DISABLE_PREFIX = "✗ "  # "X " marker shown on opted-out rows

    def __init__(self, parent, title, reset_label="Reset to Computed Order", listbox_tooltip=None):
        frame = ttk.LabelFrame(parent, text=title)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.frame = frame

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        # extended selection so several rows can be opted out at once
        # (Ctrl/Cmd-click and Shift-click to multi-select); dragging still
        # reorders the single row under the cursor
        self.listbox = DragReorderListbox(
            list_frame,
            height=8,
            selectmode="extended",
            activestyle="dotbox",
            exportselection=False,
            on_reorder=self._restyle,
        )
        style_plain_widget(self.listbox)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        self.listbox.bind("<Double-Button-1>", self._on_double, add="+")
        # type-to-jump: click the list, then just start typing a plugin name;
        # the panel title shows what you've typed
        attach_typeahead(
            self.listbox,
            strip=self._strip,
            feedback=lambda buf, _f=frame, _t=title: _f.configure(
                text=(_t + (f"   [find: {buf}]" if buf else ""))
            ),
        )
        if listbox_tooltip:
            add_tooltip(self.listbox, listbox_tooltip)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)

        btns = ttk.Frame(frame)
        btns.grid(row=0, column=1, sticky="n", padx=(0, 8), pady=8)
        up_btn = ttk.Button(btns, text=_("Move Up"), command=lambda: self.move(-1))
        up_btn.pack(fill="x", pady=2)
        add_tooltip(
            up_btn,
            "Move the selected row(s) one position earlier. Works with a multi-"
            "selection; you can also drag a contiguous block up with the mouse.",
        )
        down_btn = ttk.Button(btns, text=_("Move Down"), command=lambda: self.move(1))
        down_btn.pack(fill="x", pady=2)
        add_tooltip(
            down_btn,
            "Move the selected row(s) one position later. Works with a multi-"
            "selection; you can also drag a contiguous block down with the mouse.",
        )
        toggle_btn = ttk.Button(btns, text=_("Disable / Enable"), command=self.toggle_selected)
        toggle_btn.pack(fill="x", pady=(10, 2))
        add_tooltip(
            toggle_btn,
            "Opt the selected row in or out of the load order. Disabled rows are dimmed and "
            "marked, and are left out of Export: a custom item is simply not inserted, and an "
            "item already in your openmw.cfg gets a removeContent/removeData entry in the "
            "emitted TOML so it's durably removed. Double-click a row to toggle it too.",
        )
        reset_btn = ttk.Button(btns, text=reset_label, command=self.reset)
        reset_btn.pack(fill="x", pady=(2, 2))
        add_tooltip(
            reset_btn,
            "Discard any manual dragging and restore the order from the last Sort "
            "(your disable/enable choices are kept).",
        )

        self._original_order = []
        self._highlight_lower = set()
        self._error_lower = set()  # rows flagged with an active problem (bright red)
        self._disabled = set()  # real item texts the user has opted out

    def load(self, items, highlighted_items=(), disabled_items=()):
        """Called after a successful Sort -- populates the list and remembers
        it (for Reset), which items render highlighted, and which are disabled.
        disabled_items lets a re-Sort carry the previous opt-outs forward for
        any item still present."""
        self._original_order = list(items)
        self._highlight_lower = {str(x).lower() for x in highlighted_items}
        self._error_lower = set()
        present = set(items)
        self._disabled = {str(d) for d in disabled_items if str(d) in present}
        self._refill(self._original_order)

    def set_errors(self, items):
        """Flag rows with an active problem (e.g. a missing master) -- they
        render bright red until the next load()."""
        self._error_lower = {str(x).lower() for x in (items or ())}
        self._restyle()

    def reset(self):
        self._refill(self._original_order)

    def _display(self, real):
        return self.DISABLE_PREFIX + real if real in self._disabled else real

    def _strip(self, display):
        if display.startswith(self.DISABLE_PREFIX):
            return display[len(self.DISABLE_PREFIX) :]
        return display

    def _refill(self, items):
        self.listbox.delete(0, "end")
        for real in items:
            self.listbox.insert("end", self._display(real))
        self._restyle()

    def _restyle(self):
        trace_first_fire("plugin list restyle")
        """Apply per-row colours: disabled = dim, else problem rows = bright
        red, else highlighted, else normal. Explicit on every row so
        toggling/dragging stays consistent."""
        for i, disp in enumerate(self.listbox.get(0, "end")):
            real = self._strip(disp)
            if real in self._disabled:
                self.listbox.itemconfig(i, **self._row_disabled())
            elif real.lower() in self._error_lower:
                self.listbox.itemconfig(i, **self.ERROR)
            elif real.lower() in self._highlight_lower:
                self.listbox.itemconfig(i, **self.HIGHLIGHT)
            else:
                self.listbox.itemconfig(i, **self._row_normal())

    def _on_double(self, _event):
        self.toggle_selected()
        return "break"

    def toggle_selected(self):
        """Opt the selected row(s) in/out. With several rows selected: if any is
        currently enabled, disable them all; otherwise enable them all (so a bulk
        click has one predictable outcome). A single row just flips."""
        sel = list(self.listbox.curselection())
        if not sel:
            return
        reals = [self._strip(self.listbox.get(i)) for i in sel]
        disable = any(r not in self._disabled for r in reals)
        for i, real in zip(sel, reals):
            if disable:
                self._disabled.add(real)
            else:
                self._disabled.discard(real)
            self.listbox.delete(i)
            self.listbox.insert(i, self._display(real))  # replace in place, index unchanged
        self._restyle()
        for i in sel:
            self.listbox.selection_set(i)
        self.listbox.see(sel[0])

    def move(self, direction):
        """Move all selected rows one step up (direction<0) or down (>0),
        together, preserving their order and selection. Blocked if the leading
        selected row is already at the edge."""
        sel = sorted(self.listbox.curselection())
        if not sel:
            return
        size = self.listbox.size()
        if direction < 0:
            if sel[0] <= 0:
                return
            for idx in sel:  # ascending, so each swaps up cleanly
                t = self.listbox.get(idx)
                self.listbox.delete(idx)
                self.listbox.insert(idx - 1, t)
            new_sel = [i - 1 for i in sel]
        else:
            if sel[-1] >= size - 1:
                return
            for idx in reversed(sel):  # descending for a down-move
                t = self.listbox.get(idx)
                self.listbox.delete(idx)
                self.listbox.insert(idx + 1, t)
            new_sel = [i + 1 for i in sel]
        self._restyle()
        self.listbox.selection_clear(0, "end")
        for i in new_sel:
            self.listbox.selection_set(i)
        self.listbox.see(new_sel[0] if direction < 0 else new_sel[-1])

    def get_order(self):
        """All rows, in current order, real text (opt-out marker stripped)."""
        return [self._strip(x) for x in self.listbox.get(0, "end")]

    def get_enabled(self):
        """Only the rows that are still enabled, in order."""
        return [r for r in self.get_order() if r not in self._disabled]

    def get_disabled(self):
        return set(self._disabled)

    def has_order(self):
        return self.listbox.size() > 0


class PluginOrderPanel(ReorderPanel):
    def __init__(self, parent):
        super().__init__(
            parent,
            title="Plugin load order -- drag to override mlox (red = touched by this sort, "
            "purple = master problem)",
            reset_label="Reset to mlox Order",
            listbox_tooltip="The content= load order mlox computed, after '1. Sort'. Drag rows to "
            "manually override it before Exporting -- red rows are the ones "
            "mlox actually inserted or moved; PURPLE rows have a missing or "
            "mis-ordered master (see the MASTER CHECK section in the log); "
            "unhighlighted rows were already in openmw.cfg and left where they "
            "were. Select row(s) and click Disable/Enable (or double-click) to "
            "opt them out of the load order.",
        )


class DataPathOrderPanel(ReorderPanel):
    """Same idea as PluginOrderPanel but for data= folder paths. Only
    populated when a Sort was run with 'Sort data= paths too' checked --
    otherwise stays empty, since there's nothing computed to show or
    override (see App._sort_finished)."""

    def __init__(self, parent):
        super().__init__(
            parent,
            title=_("Data path order -- drag to adjust (highlighted = your custom paths)"),
            reset_label="Reset to Computed Order",
            listbox_tooltip="The data= folder order, populated after '1. Sort' if 'Sort data= paths "
            "too' is checked. Drag rows to manually adjust before Exporting -- "
            "highlighted rows are the custom data paths this sort manages (newly "
            "inserted, OR already in openmw.cfg from a prior configurator run); "
            "unhighlighted rows are base mod-list paths left as-is. Select row(s) and "
            "click Disable/Enable (or double-click) to opt them out. Stays empty if "
            "data-path sorting was off.",
        )


# ---------------------------------------------------------------------------
# main application
# ---------------------------------------------------------------------------


class App:
    # Colors for the log panel's tags now come from the selected syntax
    # highlighting theme (THEME_PRESETS / imported custom themes) rather than
    # a fixed palette -- see _log_tag_style, _apply_log_theme. section/error
    # stay bold across every theme since that's the thing you most need to
    # be able to spot at a glance; everything else is a plain-weight color.

    @staticmethod
    def _log_tag_style(theme):
        return {
            "section": {"foreground": theme["section"], "font": ("TkFixedFont", 10, "bold")},
            "warn": {"foreground": theme["warn"]},
            "error": {"foreground": theme["error"], "font": ("TkFixedFont", 10, "bold")},
            "ok": {"foreground": theme["ok"]},
            "inserted": {
                "foreground": theme["inserted"]
            },  # a plugin/path this sort inserted or moved
            "dim": {"foreground": theme["dim"]},
        }

    def __init__(self, root):
        self.root = root
        root.title("MLOX Subset Sort")
        root.geometry("1320x820")
        root.minsize(1000, 620)

        self.log_queue = queue.Queue()
        self.worker_running = False
        self._log_group_tag = None
        self._current_plan = None
        self._scanned_subset_lines = None  # in-memory scan result (when not saved to a file)
        self._tes3conv_override = None  # user-set path to tes3conv (for field-level diffs)
        self._tes3cmd_override = None  # user-set path to tes3cmd (frontend window)
        self._conf_session = None
        self._conf_paths = {}
        self._session = None  # reused disk-backed Tes3ConvSession
        self._keep_json = False

        # Announce the running build in the Log panel on EVERY start, with no
        # flag required. "Am I actually running the build I just made?" should
        # be answerable by looking at the window, not by finding a trace file
        # -- which for a frozen .exe lives next to the .exe, not next to the
        # source, and is therefore easy to collect a stale copy of.
        self.log_queue.put(f"MLOX Subset Sort {_app_version()} -- {_build_stamp()}\n")

        # Trace is OFF by default. It's turned on by the --trace flag (set by main()
        # into _TRACE_REQUEST) or the MLOX_SUBSET_TRACE env var -- either can name a
        # log file; otherwise mlox_subset_sort_trace.log is written next to the app.
        # Enabled BEFORE theming and widget construction, deliberately: the theme
        # startup path calls trace_first_fire(), whose once-per-session labels
        # would otherwise be consumed while tracing was still off -- and then a
        # later theme switch would emit no [smoke] line at all, which reads as
        # exactly the dead-callback failure the markers exist to expose.
        try:
            req = (
                _TRACE_REQUEST
                if _TRACE_REQUEST is not None
                else os.environ.get("MLOX_SUBSET_TRACE")
            )
            if req:
                if isinstance(req, str) and req.lower() not in ("1", "true", "yes", "on", ""):
                    path = req
                else:
                    path = app_base_dir() / "mlox_subset_sort_trace.log"
                core.set_trace_file(path)
                core.trace("GUI started")
                # Build stamp, first thing after the header. A frozen .exe is
                # easy to rebuild-and-forget, and a stale one is otherwise
                # indistinguishable from a code bug: you get the old behaviour
                # with the new source sitting right there. Version + the source
                # file's mtime pin exactly which build is running.
                core.trace(f"build: version={_app_version()} {_build_stamp()}")
                core.trace(
                    f"viewers: frozen={bool(getattr(sys, 'frozen', False))} "
                    f"pywebview={HAVE_PYWEBVIEW} "
                    f"HTMLViewer={HTMLViewer.__module__ + '.' + HTMLViewer.__name__ if HTMLViewer else None} "
                    f"load_file={HTMLViewer is not None and hasattr(HTMLViewer, 'load_file')}"
                )
                # absolute path: for a frozen .exe this is next to the .exe,
                # which is NOT where the source tree's copy lives
                self.log_queue.put(f"[trace] writing debug trace to: {Path(path).resolve()}\n")
        except Exception:  # noqa: BLE001
            # enabling tracing must never be the thing that breaks startup
            pass

        self._custom_log_themes = {}  # name -> theme dict, imported by the user
        self._load_custom_log_themes()
        self.log_theme_var = tk.StringVar(value="Dark (default)")
        # the saved theme must drive the chrome palette *before* the ttk.Style
        # is configured and any widget is built, or the whole GUI would come up
        # in the default dark chrome and only the log would match the theme.
        # _load_settings() can't run this early (it sets widget variables), so
        # just the theme name is read ahead of it; _load_settings() re-reads it
        # later to the same value.
        saved_theme = self._saved_log_theme_name()
        if saved_theme is not None and saved_theme in self._theme_names():
            self.log_theme_var.set(saved_theme)
        set_active_chrome(
            self._resolve_theme(self.log_theme_var.get()) or THEME_PRESETS["Dark (default)"]
        )
        self.style = apply_dark_theme(root)

        self._build_widgets()
        self._load_settings()
        self._apply_log_theme(self.log_theme_var.get(), announce=False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._poll_log_queue)

    # -- settings persistence ----------------------------------------------

    def _settings_file(self):
        return app_base_dir() / "mlox_subset_sort_settings.json"

    def _saved_log_theme_name(self):
        """Just the saved theme name, readable before any widget exists."""
        try:
            d = json.loads(self._settings_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):  # unreadable file / bad JSON
            return None
        name = d.get("log_theme")
        return name if isinstance(name, str) else None

    def _gather_settings(self):
        return {
            "cfg": self.cfg_var.get(),
            "customizations": self.customizations_var.get(),
            "subset_file": self.subset_file_var.get(),
            "emit_toml": self.emit_toml_var.get(),
            "list_name": self.list_name_var.get(),
            "plugin_order_yml": self.plugin_order_yml_var.get(),
            "tes3conv": self._tes3conv_override or "",
            "exclude": self.exclude_var.get(),
            "tes3cmd": self._tes3cmd_override or "",
            "plugin_order_url": self.plugin_order_url_var.get(),
            "rules_url_template": self.rules_url_var.get(),
            "rules": [str(p) for p in self.rules_panel.get_paths()],
            "write_toml_inplace": self.write_toml_inplace_var.get(),
            "dry_run": self.dry_run_var.get(),
            "write_cfg": self.write_cfg_var.get(),
            "sort_data_paths": self.sort_data_paths_var.get(),
            "no_backup": self.no_backup_var.get(),
            "no_predicate_warnings": self.no_predicate_warnings_var.get(),
            "create_subset_doc": self.create_subset_doc_var.get(),
            "keep_json": self.keep_json_var.get(),
            "log_theme": self.log_theme_var.get(),
        }

    def _load_settings(self):
        import json

        try:
            d = json.loads(self._settings_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):  # unreadable file / bad JSON
            return
        setters = {
            "cfg": self.cfg_var,
            "customizations": self.customizations_var,
            "subset_file": self.subset_file_var,
            "emit_toml": self.emit_toml_var,
            "list_name": self.list_name_var,
            "plugin_order_yml": self.plugin_order_yml_var,
            "exclude": self.exclude_var,
            "plugin_order_url": self.plugin_order_url_var,
            "rules_url_template": self.rules_url_var,
        }
        for k, var in setters.items():
            if isinstance(d.get(k), str):
                var.set(d[k])
        for k, var in (
            ("write_toml_inplace", self.write_toml_inplace_var),
            ("dry_run", self.dry_run_var),
            ("write_cfg", self.write_cfg_var),
            ("sort_data_paths", self.sort_data_paths_var),
            ("no_backup", self.no_backup_var),
            ("no_predicate_warnings", self.no_predicate_warnings_var),
            ("create_subset_doc", self.create_subset_doc_var),
            ("keep_json", self.keep_json_var),
        ):
            if isinstance(d.get(k), bool):
                var.set(d[k])
        if d.get("tes3conv"):
            self._tes3conv_override = d["tes3conv"]
        if d.get("tes3cmd"):
            self._tes3cmd_override = d["tes3cmd"]
        for p in d.get("rules") or []:
            try:
                self.rules_panel.listbox.insert("end", p)
            except tk.TclError:  # insert into a destroyed listbox
                pass
        if isinstance(d.get("log_theme"), str) and d["log_theme"] in self._theme_names():
            self.log_theme_var.set(d["log_theme"])
        self._on_toggle_inplace()

    def _save_settings(self):
        import json

        try:
            self._settings_file().write_text(
                json.dumps(self._gather_settings(), indent=2), encoding="utf-8"
            )
        except (OSError, TypeError, ValueError):  # unwritable path / unserialisable value
            pass

    def _on_close(self):
        self._save_settings()
        s = getattr(self, "_session", None)
        if s is not None:
            try:
                s.cleanup()  # removes the temp JSON dump (no-op if 'keep' was set)
            except Exception:  # noqa: BLE001
                # close path: an escape here would stop the window being destroyed
                pass
        self.root.destroy()

    # -- layout ------------------------------------------------------------

    def _paned(self, parent, orient):
        """A tk.PanedWindow (not ttk -- ttk's has no visible grip) styled to
        match the dark theme. The default square handle is turned off; a
        hamburger-style grip is overlaid instead by _attach_hamburger_grip()."""
        return tk.PanedWindow(
            parent,
            orient=orient,
            sashwidth=8,
            sashrelief="flat",
            showhandle=False,
            bg=DARK["bg"],
            bd=0,
            background=DARK["border"],
            sashpad=0,
        )

    def _attach_hamburger_grip(self, paned, orient):
        """Overlay a hamburger-style (three-line) draggable grip centered on the
        single sash of a two-pane PanedWindow. Cross-platform: cursor names are
        tried in order and any failure is ignored, and if the sash geometry
        can't be read the grip just hides itself -- the sash stays draggable
        either way, so this is purely a nicer-looking handle, never load-bearing."""
        horizontal = orient == "horizontal"  # horizontal paned -> vertical sash
        long_px, thick_px = 34, 12
        w = thick_px if horizontal else long_px
        h = long_px if horizontal else thick_px
        grip = tk.Canvas(
            paned,
            width=w,
            height=h,
            bg=DARK["btn_bg"],
            highlightthickness=1,
            highlightbackground=DARK["border"],
            bd=0,
            takefocus=0,
        )
        if horizontal:  # three vertical lines (drag left/right)
            for x in (w // 2 - 3, w // 2, w // 2 + 3):
                grip.create_line(x, 5, x, h - 5, fill=DARK["fg_dim"])
        else:  # three horizontal lines (drag up/down)
            for y in (h // 2 - 3, h // 2, h // 2 + 3):
                grip.create_line(5, y, w - 5, y, fill=DARK["fg_dim"])
        for cur in (
            ("sb_h_double_arrow" if horizontal else "sb_v_double_arrow"),
            "fleur",
            "hand2",
            "",
        ):
            try:
                grip.configure(cursor=cur)
                break
            except tk.TclError:
                continue

        def reposition(_event=None):
            try:
                x, y = paned.sash_coord(0)
                half = int(paned.cget("sashwidth")) // 2
            except (tk.TclError, IndexError, TypeError, ValueError):
                grip.place_forget()
                return
            if horizontal:
                grip.place(x=x + half, rely=0.5, anchor="center")
            else:
                grip.place(relx=0.5, y=y + half, anchor="center")

        def on_drag(event):
            try:
                if horizontal:
                    paned.sash_place(0, max(1, event.x_root - paned.winfo_rootx()), 1)
                else:
                    paned.sash_place(0, 1, max(1, event.y_root - paned.winfo_rooty()))
            except tk.TclError:
                pass
            reposition()

        def reposition_soon(_event=None):
            # On a resize (esp. maximize/restore/fullscreen) the <Configure>
            # event fires before the PanedWindow has moved its sash, so reading
            # sash_coord() right now returns the OLD position and the grip lands
            # in the wrong place until you nudge it. Defer to the idle pass so we
            # read the sash position AFTER layout settles. A second delayed pass
            # catches window managers that relayout in more than one step.
            paned.after_idle(reposition)
            paned.after(60, reposition)

        grip.bind("<B1-Motion>", on_drag)
        paned.bind("<Configure>", reposition_soon, add="+")
        paned.bind("<B1-Motion>", lambda e: reposition(), add="+")  # follow a direct sash drag
        paned.bind("<ButtonRelease-1>", lambda e: reposition(), add="+")
        paned.after(200, reposition)

    def _build_widgets(self):
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)

        # main split: sorting panels on the left, everything else on the right
        main_pane = self._paned(outer, "horizontal")
        main_pane.pack(fill="both", expand=True)

        # LEFT: two stacked, independently resizable draggable-order panels
        left_pane = self._paned(main_pane, "vertical")
        main_pane.add(left_pane, minsize=280, width=380, stretch="always")

        plugin_frame = ttk.Frame(left_pane, padding=(0, 0, 6, 0))
        self.order_panel = PluginOrderPanel(plugin_frame)
        left_pane.add(plugin_frame, minsize=120, stretch="always")

        data_frame = ttk.Frame(left_pane, padding=(0, 6, 6, 0))
        self.data_order_panel = DataPathOrderPanel(data_frame)
        left_pane.add(data_frame, minsize=120, stretch="always")

        # RIGHT: the rest of the program (inputs/options/actions on top, log
        # below), also independently resizable against each other
        right_pane = self._paned(main_pane, "vertical")
        main_pane.add(right_pane, minsize=420, width=760, stretch="always")

        controls_frame = ttk.Frame(right_pane, padding=(6, 0, 0, 0))
        self._build_controls(controls_frame)
        right_pane.add(controls_frame, minsize=360)

        log_container = ttk.Frame(right_pane, padding=(6, 6, 0, 0))
        self._build_log(log_container)
        right_pane.add(log_container, minsize=120, stretch="always")

        # overlay hamburger-style drag grips on each sash (purely cosmetic; the
        # sashes themselves stay draggable if the overlay can't render)
        self._attach_hamburger_grip(main_pane, "horizontal")
        self._attach_hamburger_grip(left_pane, "vertical")
        self._attach_hamburger_grip(right_pane, "vertical")

    def _build_controls(self, top):
        top.columnconfigure(1, weight=1)

        if not HAVE_DND:
            note = ttk.Label(
                top,
                foreground=DARK["fg_dim"],
                text=_(
                    "Drag & drop is disabled (tkinterdnd2 not installed) -- use the Browse buttons below."
                ),
            )
            note.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
            start_row = 1
        else:
            start_row = 0

        self.cfg_var = tk.StringVar()
        self.plugin_order_url_var = tk.StringVar()  # blank = built-in candidates
        self.rules_url_var = tk.StringVar()  # blank = built-in template
        self.customizations_var = tk.StringVar()
        self.subset_file_var = tk.StringVar()
        self.emit_toml_var = tk.StringVar()
        self.write_toml_inplace_var = tk.BooleanVar(value=False)
        self.list_name_var = tk.StringVar()
        self.plugin_order_yml_var = tk.StringVar()

        PathField(
            top,
            "openmw.cfg:",
            start_row,
            self.cfg_var,
            filetypes=(("openmw.cfg", "*.cfg"), ("All files", "*.*")),
            tooltip="Required. The openmw.cfg to read the current content= and data= order "
            "from, and (if 'Write openmw.cfg directly' is checked) to patch.",
        )
        PathField(
            top,
            "customizations.toml:",
            start_row + 1,
            self.customizations_var,
            filetypes=(("TOML files", "*.toml"), ("All files", "*.*")),
            tooltip="A momw-configurator/umo customizations TOML to pull the plugin/data-path "
            "subset from automatically. Optional if you provide a subset file instead -- "
            "provide both and they're combined.",
        )
        PathField(
            top,
            "subset file (optional):",
            start_row + 2,
            self.subset_file_var,
            filetypes=(("Text/TOML", "*.txt *.toml"), ("All files", "*.*")),
            tooltip="A plain text file (one plugin filename or data folder path per line, "
            "'#' comments allowed) or a minimal TOML with subset=[...]/data=[...]. "
            "Combined with --emit-toml, this alone is enough to generate a brand new "
            "customizations.toml with no existing one required.",
            extra_button=(
                "Scan...",
                self.on_scan_mods,
                "Scan a mods folder to build the subset: every folder that contains an "
                "asset subfolder (meshes/textures/...) or a plugin becomes a data path "
                "(plus its plugins), then that branch isn't descended further. Whether "
                "the result is saved to a .txt (and loaded here) or just kept in memory "
                "for this session is set by the 'Create subset text document' option.",
            ),
        )
        self.emit_toml_field = PathField(
            top,
            "emit corrected TOML to:",
            start_row + 3,
            self.emit_toml_var,
            browse_kind="save",
            filetypes=(("TOML files", "*.toml"), ("All files", "*.*")),
            tooltip="Where to write a corrected customizations.toml (sorted insert blocks, "
            "re-anchored). Disabled when 'write directly back' below is checked.",
        )

        # listName for the emitted TOML. momw-configurator REQUIRES this -- it
        # names the curated mod list the customizations apply to. Left blank,
        # the source customizations.toml's own listName is kept; when generating
        # from a subset file alone it would otherwise fall back to the useless
        # placeholder "generated", so setting this is recommended in that case.
        list_name_label = ttk.Label(top, text=_("list name (optional):"))
        list_name_label.grid(row=start_row + 4, column=0, sticky="w", padx=(0, 8), pady=4)
        list_name_entry = ttk.Entry(top, textvariable=self.list_name_var)
        list_name_entry.grid(row=start_row + 4, column=1, sticky="ew", pady=4)
        list_name_tip = (
            "The momw-configurator listName written into the emitted "
            "momw-customizations.toml, e.g. 'total-overhaul' -- the curated mod list "
            "these customizations apply to. Overrides the listName from the "
            "customizations.toml above if both are set. Leave blank to keep that file's "
            "own listName; when generating from a subset file alone, set this so the "
            "output isn't stuck with the placeholder 'generated'."
        )
        add_tooltip(list_name_label, list_name_tip)
        add_tooltip(list_name_entry, list_name_tip)

        PathField(
            top,
            "plugin-order.yml (optional):",
            start_row + 5,
            self.plugin_order_yml_var,
            filetypes=(("YAML files", "*.yml *.yaml"), ("All files", "*.*")),
            tooltip="MOMW's plugin-order.yml (source of truth for which plugins belong to which "
            "curated list). With the list name above set, curated plugins for that list "
            "are excluded from the sort (never reordered) so only your custom additions "
            "are touched, and read-only warnings are emitted: redundant, orphan, "
            "needs-cleaning, and a base-order drift check. PyYAML used if installed, "
            "else a built-in parser.",
            extra_button=(
                "Update...",
                self.on_update_plugin_order_yml,
                "Download the current plugin-order.yml from MOMW over this file. "
                "The download is fully validated (must parse as plugin-order data "
                "with hundreds of entries) before anything is written, and the old "
                "file is kept as a timestamped .bak. Set $MLOX_PLUGIN_ORDER_URL "
                "to use a mirror.",
            ),
        )

        inplace_chk = ttk.Checkbutton(
            top,
            text="Write directly back to customizations.toml (overwrite in place; "
            "a .bak-<timestamp> copy is made first unless backups are disabled below)",
            variable=self.write_toml_inplace_var,
            command=self._on_toggle_inplace,
        )
        inplace_chk.grid(row=start_row + 6, column=0, columnspan=3, sticky="w", pady=(0, 4))
        add_tooltip(
            inplace_chk,
            "Instead of writing to a separate file above, overwrite the customizations.toml "
            "given above in place. A timestamped backup is made first (unless disabled), and "
            "you'll get a confirmation prompt before it actually happens.",
        )

        self.rules_panel = RuleFilesPanel(
            top,
            start_row + 7,
            on_new_rule=self.on_rule_maker,
            on_sources=self.on_sources,
            get_rules_url=lambda: self.rules_url_var.get().strip(),
        )

        # options
        opts = ttk.LabelFrame(top, text=_("Options"))
        opts.grid(row=start_row + 8, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        for i in range(3):
            opts.columnconfigure(i, weight=1)

        self.write_cfg_var = tk.BooleanVar(value=False)
        self.sort_data_paths_var = tk.BooleanVar(value=False)
        self.no_backup_var = tk.BooleanVar(value=False)
        self.no_predicate_warnings_var = tk.BooleanVar(value=False)
        self.dry_run_var = tk.BooleanVar(value=True)
        self.create_subset_doc_var = tk.BooleanVar(value=True)
        self.exclude_var = tk.StringVar()
        self.keep_json_var = tk.BooleanVar(value=False)

        dry_chk = ttk.Checkbutton(
            opts, text=_("Dry run (preview only, don't write files)"), variable=self.dry_run_var
        )
        dry_chk.grid(row=0, column=0, sticky="w", padx=8, pady=4)
        add_tooltip(
            dry_chk,
            "When checked, Export shows exactly what it would write without "
            "touching any files. Uncheck when you're ready to actually save.",
        )

        write_cfg_chk = ttk.Checkbutton(
            opts, text=_("Write openmw.cfg directly"), variable=self.write_cfg_var
        )
        write_cfg_chk.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        add_tooltip(
            write_cfg_chk,
            "Patch the content=/data= lines in openmw.cfg in place on Export. "
            "A .bak-<timestamp> copy is made first unless backups are disabled.",
        )

        sort_data_chk = ttk.Checkbutton(
            opts, text=_("Sort data= paths too"), variable=self.sort_data_paths_var
        )
        sort_data_chk.grid(row=0, column=2, sticky="w", padx=8, pady=4)
        add_tooltip(
            sort_data_chk,
            "mlox has no concept of data= folder order -- this positions new data= paths "
            "using an explicit after/before anchor if you wrote one, or by scanning the "
            "folder for plugins and anchoring next to their neighbor in the sorted content= "
            "order. Off by default so a plugin-only run can't surprise-reorder data= too. "
            "Also required for the data path order panel to populate.",
        )

        no_backup_chk = ttk.Checkbutton(
            opts, text=_("Skip .bak backup of openmw.cfg"), variable=self.no_backup_var
        )
        no_backup_chk.grid(row=1, column=0, sticky="w", padx=8, pady=4)
        add_tooltip(
            no_backup_chk,
            "Skip making a timestamped backup before overwriting openmw.cfg "
            "and/or an in-place customizations.toml. Not recommended.",
        )

        no_warn_chk = ttk.Checkbutton(
            opts,
            text=_("Skip mlox Conflict/Requires/Note warnings"),
            variable=self.no_predicate_warnings_var,
        )
        no_warn_chk.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        add_tooltip(
            no_warn_chk,
            "Skip evaluating [Conflict]/[Requires]/[Note] rules against the sorted plugin "
            "list. This is purely informational and read-only either way -- it never changes "
            "the computed order or what gets written, only whether warnings get printed.",
        )

        create_doc_chk = ttk.Checkbutton(
            opts,
            text=_("Create subset text document (on Scan)"),
            variable=self.create_subset_doc_var,
        )
        create_doc_chk.grid(row=1, column=2, sticky="w", padx=8, pady=4)

        excl_lbl = ttk.Label(opts, text=_("Exclude from conflict / cell scans:"))
        excl_lbl.grid(row=2, column=0, sticky="w", padx=8, pady=4)
        excl_entry = ttk.Entry(opts, textvariable=self.exclude_var)
        excl_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=8, pady=4)
        excl_tip = (
            "Comma-separated name patterns (glob: * ?, case-insensitive) to skip in the "
            "Conflict/Cell-map/Resource scans -- e.g. 's3lightfixes*, *delta*, *groundcover*, "
            "*grass*'. Handy for 'touches-everything' mods (light fixes, grass/ground "
            "generators, delta/merged patches) that swamp the results. Saved with your settings."
        )
        add_tooltip(excl_lbl, excl_tip)
        add_tooltip(excl_entry, excl_tip)
        keep_json_chk = ttk.Checkbutton(
            opts,
            text=_("Keep tes3conv JSON dump"),
            variable=self.keep_json_var,
            command=self._on_keep_json_toggle,
        )
        keep_json_chk.grid(row=3, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        add_tooltip(
            keep_json_chk,
            "tes3conv conversions are written to disk (not held in RAM) and read per-plugin, "
            "so big load orders don't blow up memory. They always go to a 'tes3conv_json' "
            "folder next to the app and are reused within a run -- so Check Conflicts then Cell "
            "Map won't re-run tes3conv (a plugin is only re-converted if it changed). This box "
            "just decides what happens on exit: checked = keep that folder (reused next launch "
            "too); unchecked = delete it when you close the app.",
        )
        add_tooltip(
            create_doc_chk,
            "Controls what 'Scan...' does with its result. Checked: write the scanned list "
            "to a .txt subset file you choose, and load it (the file stays on disk for reuse). "
            "Unchecked: keep the scanned list in memory just for this session and feed it "
            "straight to the sort -- nothing is written to disk.",
        )

        # action area: Sort computes the plan (never writes anything) and
        # populates the order panels on the left; Export writes using whatever
        # order those panels are currently showing. Two compact rows of
        # left-aligned buttons -- primary + read-only analysis on top, tools
        # below -- with the status label trailing on the first row.
        action_area = ttk.Frame(top)
        action_area.grid(row=start_row + 9, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        row1 = ttk.Frame(action_area)
        row1.pack(fill="x")
        row2 = ttk.Frame(action_area)
        row2.pack(fill="x", pady=(4, 0))

        def _btn(bar, text, cmd, tip, state="normal", pad=(0, 6)):
            b = ttk.Button(bar, text=text, command=cmd, state=state)
            b.pack(side="left", padx=pad)
            add_tooltip(b, tip)
            return b

        self.sort_button = _btn(
            row1,
            "1. Sort",
            self.on_sort,
            "Run mlox and populate the plugin/data order panels on the left. Never writes "
            "any files -- this is always safe to run.",
            pad=(0, 12),
        )
        self.export_button = _btn(
            row1,
            "2. Export",
            self.on_export,
            "Write openmw.cfg and/or the customizations.toml, using whatever order the "
            "panels on the left are currently showing (mlox's own order, unless you dragged "
            "rows). Rows you disabled are left out -- new customs aren't inserted, and items "
            "already in your cfg get a removeContent/removeData. Respects 'Dry run'. "
            "Disabled until a Sort succeeds.",
            state="disabled",
            pad=(0, 18),
        )
        self.conflicts_button = _btn(
            row1,
            "Check Conflicts",
            self.on_check_conflicts,
            "Scan the sorted, enabled plugins for TES3 record-level conflicts -- where two or "
            "more plugins edit the same record (the last in the load order wins), like "
            "TES3View. Prints a report in the log and opens a conflicts window; point that "
            "window at a tes3conv binary for a field-by-field diff of each conflicting record "
            "-- including compiled scripts, which are disassembled rather than shown as raw "
            "base64. Read-only; needs the plugin files reachable via your cfg's data= folders. "
            "Runs after a Sort.",
            state="disabled",
        )
        self.cellmap_button = _btn(
            row1,
            "Cell Map",
            self.on_cell_map,
            "Build a 'modmapper'-style cell map from the sorted, enabled plugins: an "
            "exterior-cell SVG heatmap (brighter = more mods; click a cell to jump to its "
            "list entry) plus exterior/interior cell lists, showing which mods touch which "
            "cells (your custom ones get a gold outline). A 'Focus on mod' dropdown dims "
            "everything one mod doesn't touch and lists its co-editors. The map is written "
            "to cell_map.html "
            "and shown in an in-app window if pywebview or tkinterweb is installed, otherwise "
            "in your browser. Read-only.",
            state="disabled",
        )
        self.resource_button = _btn(
            row1,
            "Resource Conflicts",
            self.on_resource_conflicts,
            "Scan the data= folders for loose-file (VFS) conflicts: the same relative path "
            "(meshes/textures/scripts/...) provided by two or more mod folders. In OpenMW the "
            "LATER data folder wins, so reorder the data-path panel to change the winner "
            "(like MO2's Data conflicts). Read-only; can be slow on a big install.",
            state="disabled",
        )

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(row1, textvariable=self.status_var, foreground=DARK["fg_dim"], anchor="e").pack(
            side="right", padx=(12, 2)
        )

        self.lint_button = _btn(
            row2,
            "Lint",
            self.on_lint,
            "tes3lint-style checks over the sorted, enabled plugins (native, "
            "VFS-aware): evil GMSTs (name AND value must match the known 72), "
            "the interior fog-density-0 'black void' bug, new interior cells with "
            "no pathgrid anywhere in the load order, expansion-function use without the "
            "expansion mastered, omwaddon/omwscripts twin mismatches, and customs with "
            "blank author/description. Read-only; reads every plugin file, so it can "
            "take a little while on a big install.",
            state="disabled",
        )
        self.tes3cmd_button = _btn(
            row2,
            "tes3cmd",
            self.on_tes3cmd_window,
            "Frontend for tes3cmd (distributed with the MOMW Tools Pack): clean plugins "
            "(staged with their masters so tes3cmd sees the full VFS), resync master "
            "sizes in-app ([MASTER SIZE] notes), or view headers. Uses the compiled "
            "tes3cmd.exe; the pure-perl script also works if perl is installed. "
            "Modifying commands keep backups. (No multipatch: OpenMW setups use "
            "delta-plugin for merged lists.)",
        )
        self.savecheck_button = _btn(
            row2,
            "Save Check",
            self.on_save_check,
            "Pick an OpenMW .omwsave and verify every content file it depends on is "
            "still in the (sorted, enabled) load order -- OpenMW refuses to load a "
            "save whose plugins are missing. Read-only.",
        )
        self.backups_button = _btn(
            row2,
            "Backups",
            self.on_backups,
            "List every backup left behind by this tool, tes3cmd and the Configurator "
            "(.preclean.bak, .masterfix.bak, name~1.esp, timestamped .bak / .backup "
            "copies) across the data folders, with restore/delete.",
        )

    def _build_log(self, log_container):
        log_container.columnconfigure(0, weight=1)
        log_container.rowconfigure(0, weight=1)

        log_frame = ttk.LabelFrame(log_container, text=_("Log"))
        log_frame.grid(row=0, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            wrap="word",
            font=("TkFixedFont", 10),
            state="disabled",
            background=DARK["log_bg"],
            foreground=DARK["fg"],
            insertbackground=DARK["fg"],
            selectbackground=DARK["select"],
            relief="flat",
            borderwidth=0,
            highlightbackground=DARK["border"],
            highlightthickness=1,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        add_tooltip(
            self.log_text,
            "Full output from the last Sort and/or Export. Colour key: green = a plugin/path "
            "this sort inserted or moved, orange = a heads-up (mlox warning, or a rule your "
            "curated cfg order overrode), blue = a section header, bright red = an error worth "
            "checking. Plain text = frozen base rows left untouched. Colors follow the "
            "syntax highlighting theme picked below.",
        )
        # tags get their real colors from _apply_log_theme once the theme is
        # known (see __init__) -- configure with placeholders now so nothing
        # is left unconfigured if something logs before that call
        for tag, cfg in self._log_tag_style(THEME_PRESETS["Dark (default)"]).items():
            self.log_text.tag_configure(tag, **cfg)

        log_btns = ttk.Frame(log_frame)
        log_btns.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        clear_btn = ttk.Button(log_btns, text=_("Clear Log"), command=self.clear_log)
        clear_btn.pack(side="left")
        add_tooltip(
            clear_btn, _("Clear the log panel. Doesn't affect the order panels or any files.")
        )
        save_btn = ttk.Button(log_btns, text=_("Save Log As..."), command=self.save_log)
        save_btn.pack(side="left", padx=(8, 0))
        add_tooltip(save_btn, _("Save the current log contents to a text file."))

        ttk.Label(log_btns, text=_("Theme:")).pack(side="left", padx=(16, 4))
        self.theme_combo = ttk.Combobox(
            log_btns,
            textvariable=self.log_theme_var,
            state="readonly",
            width=18,
            values=self._theme_names(),
        )
        self.theme_combo.pack(side="left")
        self.theme_combo.bind(
            "<<ComboboxSelected>>", lambda e: self._apply_log_theme(self.log_theme_var.get())
        )
        add_tooltip(
            self.theme_combo,
            "Colour theme for the whole GUI: window/button colours, syntax "
            "highlighting here in the Log panel, and the field-diff JSON viewer "
            "(Check Conflicts -> double-click a field). Switching re-themes "
            "everything immediately, including any windows already open. Built-in: "
            "Dracula, Monokai, Atom One Dark, Gruvbox Dark, plus anything you've "
            "imported.",
        )
        import_theme_btn = ttk.Button(
            log_btns, text=_("Import Theme..."), command=self._import_log_theme
        )
        import_theme_btn.pack(side="left", padx=(8, 0))
        add_tooltip(
            import_theme_btn,
            "Import a custom theme from a JSON file (background/foreground/select/"
            "section/warn/error/ok/inserted/dim as hex colors, plus an optional "
            '"chrome" object for explicit window/button colours) or a base16 scheme '
            "file (.yaml/.yml/.json with base00..base0F -- e.g. from the atelierbram/"
            "syntax-highlighting or chriskempson/base16 scheme repos). Window colours "
            "not given explicitly are derived from the background. Imported themes are "
            "saved and appear in the dropdown from then on.",
        )

    # -- log panel syntax highlighting themes --------------------------------

    def _custom_themes_file(self):
        return app_base_dir() / "log_themes.json"

    def _load_custom_log_themes(self):
        try:
            raw = json.loads(self._custom_themes_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):  # unreadable file / bad JSON
            return
        if not isinstance(raw, dict):
            return
        for name, theme in raw.items():
            try:
                for field in _THEME_REQUIRED:
                    theme[field] = _normalize_hex(theme[field])
                self._custom_log_themes[str(name)] = theme
            except (KeyError, TypeError, ValueError):  # missing key / non-dict entry / bad hex
                continue  # skip a corrupted entry rather than lose the rest

    def _save_custom_log_themes(self):
        try:
            self._custom_themes_file().write_text(
                json.dumps(self._custom_log_themes, indent=2), encoding="utf-8"
            )
        except (OSError, TypeError, ValueError):  # unwritable path / unserialisable value
            pass

    def _theme_names(self):
        return list(THEME_PRESETS.keys()) + sorted(self._custom_log_themes.keys())

    def _resolve_theme(self, name):
        return THEME_PRESETS.get(name) or self._custom_log_themes.get(name)

    def _apply_log_theme(self, name, announce=True):
        theme = self._resolve_theme(name)
        if theme is None:
            name = "Dark (default)"
            theme = THEME_PRESETS[name]
            self.log_theme_var.set(name)
        # keep the chrome palette in step with the syntax theme, then re-theme
        # every live widget (the main window and all open Toplevels)
        set_active_chrome(theme)
        trace_first_fire("theme -> chrome palette update (set_active_chrome)")
        # per-switch (not just first-fire): theme changes are rare and
        # user-initiated, and the name identifies *which* palette is active
        core.trace(f"[theme] chrome palette now follows: {name}")
        # never let a re-apply problem (e.g. a platform/build-specific Tk quirk)
        # stop the log theme itself from applying below
        try:
            self._reapply_chrome()
        except Exception:  # noqa: BLE001
            # diagnostic guard: must not raise onward
            core.trace("[theme] re-apply pass failed:\n" + traceback.format_exc())
        log_text = getattr(self, "log_text", None)
        if log_text is None or not log_text.winfo_exists():
            return
        log_text.configure(
            background=theme["background"],
            foreground=theme["foreground"],
            insertbackground=theme["foreground"],
            selectbackground=theme["select"],
        )
        for tag, cfg in self._log_tag_style(theme).items():
            log_text.tag_configure(tag, **cfg)
        if announce:
            self.status_var.set(f"Log syntax highlighting: {name}")

    def _reapply_chrome(self):
        """Re-theme the live GUI after the chrome palette changed.

        Three passes, because three different mechanisms own the colours:
        1. apply_dark_theme() re-configures the (live) ttk.Style and option
           database, which instantly restyles every ttk widget everywhere.
        2. restyle_widget_tree() walks the real widget tree from the main
           window -- open Toplevels are its children, so dialogs, the tes3cmd
           window etc. are reached -- fixing the plain-tk widgets that
           ttk.Style can't touch.
        3. The reorder panels' per-row itemconfig colours are re-applied.
        """
        apply_dark_theme(self.root)
        count = restyle_widget_tree(self.root)
        for panel in (getattr(self, "order_panel", None), getattr(self, "data_order_panel", None)):
            if panel is not None:
                try:
                    panel._restyle()
                except tk.TclError:
                    pass
        trace_first_fire("theme runtime re-apply walk (restyle_widget_tree)")
        core.trace(f"[theme] re-applied chrome to {count} plain-tk widgets (ttk covered by Style)")

    def _import_log_theme(self):
        path = filedialog.askopenfilename(
            title=_("Import syntax highlighting theme"),
            filetypes=(("Theme files", "*.json *.yaml *.yml"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            name, theme = parse_theme_file(path)
        except (OSError, ValueError) as e:
            # parse_theme_file's documented contract, plus read errors
            messagebox.showerror(_("Import failed"), f"Couldn't import that theme:\n\n{e}")
            return
        base_name, existing = name, self._theme_names()
        n = 2
        while name in existing:
            name = f"{base_name} ({n})"
            n += 1
        self._custom_log_themes[name] = theme
        self._save_custom_log_themes()
        self.theme_combo.configure(values=self._theme_names())
        self.log_theme_var.set(name)
        self._apply_log_theme(name, announce=False)
        self.status_var.set(f"Imported and applied theme: {name}")
        messagebox.showinfo(
            _("Theme imported"), f'Imported "{name}" and set it as the active log theme.'
        )

    # -- log handling --------------------------------------------------------

    def _tag_for_line(self, line: str) -> str:
        stripped = line.strip()
        if stripped.startswith("=" * 5) or (
            stripped
            and stripped == stripped.upper()
            and any(c.isalpha() for c in stripped)
            and len(stripped) < 70
            and not stripped.startswith("[")
        ):
            return "section"
        # [CONFLICT] and an internal drift warning are active problems worth
        # flagging in bright red; [REQUIRES]/generic WARNING:/NOTE: are
        # milder heads-ups and stay orange
        if "[CONFLICT]" in line or "INTERNAL WARNING" in line:
            return "error"
        if "MISMATCH:" in line or "PREVIEW ABORTED" in line:
            return "error"
        if "VERIFIED:" in line:
            return "ok"
        # a missing or out-of-order master breaks the game at launch -- red
        if "[MISSING MASTER]" in line or "[MASTER ORDER]" in line or "[MISSING SAVE DEP]" in line:
            return "error"
        if any(k in line for k in ("Traceback", "ERROR", "Error:")):
            return "error"
        if any(
            k in line
            for k in (
                "[REQUIRES]",
                "[NOTE]",
                "WARNING:",
                "NOTE:",
                "[MASTER SIZE]",
                "[FOGBUG]",
                "[EVLGMST]",
                "[NO PATHGRID]",
                "[HEADER]",
                "[EXP-DEP]",
                "[TWIN]",
                "[STALE]",
                "[REDUNDANT]",
                "[ORPHAN]",
                "[NEEDS CLEANING]",
                "[LIST ORDER]",
                # skipped-rule summary (mlox order overridden by the curated cfg)
                "ordering rule(s) not applied",
                "mlox wanted",
            )
        ):
            return "warn"
        if line.startswith("* ["):  # conflict report line involving your custom mods
            return "warn"
        if "<-- inserted" in line:  # 'content=X  <-- inserted/moved' / 'data=...  <-- inserted'
            return "inserted"
        if any(k in line for k in ("Wrote ", "written:    yes")):
            return "ok"
        if line.startswith("---"):
            return "dim"
        return ""

    def _append_log(self, text: str):
        self.log_text.configure(state="normal")
        for line in text.splitlines(keepends=True):
            stripped = line.strip("\n")
            if not stripped:
                self._log_group_tag = None
                self.log_text.insert("end", line)
                continue
            if line.startswith("    ") and self._log_group_tag:
                # indented continuation line (e.g. "Needed by:", "Caused by:")
                # -- inherit the color of the warning it belongs to
                tag = self._log_group_tag
            else:
                tag = self._tag_for_line(line)
                if tag in ("warn", "error"):
                    self._log_group_tag = tag
            self.log_text.insert("end", line, (tag,) if tag else ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_log_queue(self):
        drained = []
        try:
            while True:
                drained.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        if drained:
            self._append_log("".join(drained))
        self.root.after(80, self._poll_log_queue)

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._log_group_tag = None

    def save_log(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".log", filetypes=(("Log files", "*.log"), ("All files", "*.*"))
        )
        if not path:
            return
        Path(path).write_text(self.log_text.get("1.0", "end"), encoding="utf-8")

    # -- run -----------------------------------------------------------------

    def _on_toggle_inplace(self):
        inplace = self.write_toml_inplace_var.get()
        self.emit_toml_field.set_enabled(not inplace)

    def _validate(self) -> types.SimpleNamespace | None:
        errors = []
        if not self.cfg_var.get().strip():
            errors.append("openmw.cfg path is required.")
        rule_paths = self.rules_panel.get_paths()
        if not rule_paths:
            errors.append("At least one mlox rule file is required.")
        customizations = self.customizations_var.get().strip()
        subset_file = self.subset_file_var.get().strip()
        # an in-memory scan (Scan with 'Create subset text document' off) counts
        # as input too, so don't demand a file/customizations in that case
        has_mem_scan = bool(self._scanned_subset_lines) and not subset_file
        if not customizations and not subset_file and not has_mem_scan:
            errors.append("Provide a customizations.toml, a subset file, or run Scan.")

        write_inplace = self.write_toml_inplace_var.get()
        if write_inplace:
            if not customizations:
                errors.append(
                    "'Write directly back to customizations.toml' requires a customizations.toml."
                )
            emit_toml = customizations  # overwrite the source file itself
        else:
            emit_toml = self.emit_toml_var.get().strip()

        if errors:
            messagebox.showerror(_("Missing input"), "\n".join(f"- {e}" for e in errors))
            return None

        return types.SimpleNamespace(
            cfg=Path(self.cfg_var.get().strip()),
            rules=rule_paths,
            customizations=Path(customizations) if customizations else None,
            subset=[],
            subset_file=Path(subset_file) if subset_file else None,
            dry_run=self.dry_run_var.get(),
            no_backup=self.no_backup_var.get(),
            emit_toml=Path(emit_toml) if emit_toml else None,
            write_cfg=self.write_cfg_var.get(),
            sort_data_paths=self.sort_data_paths_var.get(),
            no_predicate_warnings=self.no_predicate_warnings_var.get(),
            list_name=self.list_name_var.get().strip() or None,
            plugin_order_yml=(
                Path(self.plugin_order_yml_var.get().strip())
                if self.plugin_order_yml_var.get().strip()
                else None
            ),
            subset_lines=(self._scanned_subset_lines if has_mem_scan else None),
        )

    def on_scan_mods(self):
        """Scan a mods folder to build a subset. If 'Create subset text document'
        is checked, write it to a .txt you choose and load that file; otherwise
        keep the result in memory for this session only (no file written). Runs
        in a worker thread since a big tree can take a moment to walk."""
        if self.worker_running:
            return
        folder = filedialog.askdirectory(title=_("Select the mods folder to scan"))
        if not folder:
            return
        make_doc = self.create_subset_doc_var.get()
        out = None
        if make_doc:
            out = filedialog.asksaveasfilename(
                title=_("Save generated subset file as"),
                defaultextension=".txt",
                initialfile="mod_scan_results.txt",
                filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
            )
            if not out:
                return
        self.worker_running = True
        self.sort_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.status_var.set(_("Scanning mods folder..."))
        threading.Thread(target=self._scan_worker, args=(folder, out), daemon=True).start()

    def _scan_worker(self, folder, out):
        writer = QueueWriter(self.log_queue)
        written, mem_lines = None, None
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                lines, n_folders, n_plugins = core.scan_mod_directories(folder, out)
            if out:
                written = out
                status = (
                    f"Scan complete -- {n_folders} folder(s), {n_plugins} plugin(s). "
                    f"Subset file loaded."
                )
            else:
                mem_lines = lines
                status = (
                    f"Scan complete -- {n_folders} folder(s), {n_plugins} plugin(s). "
                    f"Held in memory (no file written)."
                )
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: scan failed:\n" + traceback.format_exc())
            status = "Scan failed -- see log."
        finally:
            self.root.after(0, self._scan_finished, written, mem_lines, status)

    def _scan_finished(self, written_path, mem_lines, status):
        self.worker_running = False
        self.sort_button.configure(state="normal")
        self.export_button.configure(state="normal" if self._current_plan else "disabled")
        if written_path:
            self.subset_file_var.set(written_path)
            self._scanned_subset_lines = None  # using the file now
        elif mem_lines is not None:
            self._scanned_subset_lines = mem_lines
            self.subset_file_var.set("")  # in-memory: no file path to show
        self.status_var.set(status)

    def on_sort(self):
        if self.worker_running:
            return
        args = self._validate()
        if args is None:
            return

        self.clear_log()
        self.export_button.configure(state="disabled")
        self._current_plan = None
        self.worker_running = True
        self.sort_button.configure(state="disabled")
        self.status_var.set(_("Sorting..."))

        thread = threading.Thread(target=self._sort_worker, args=(args,), daemon=True)
        thread.start()

    def _sort_worker(self, args):
        writer = QueueWriter(self.log_queue)
        plan = None
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                plan = core.compute_plan(args)
            n_warn = len(plan.get("predicate_warnings") or [])
            n_yml = len(plan.get("yml_warnings") or [])
            n_plugins = len(plan.get("final_order") or [])
            yml_bit = f", {n_yml} yml warning(s)" if n_yml else ""
            status = (
                f"Sorted {n_plugins} plugin(s), {n_warn} rule warning(s){yml_bit}. "
                f"Drag to adjust, then Export."
            )
        except SystemExit as e:
            writer.write(f"\nERROR: {e}\n")
            status = "Sort failed -- see log."
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: unexpected exception:\n" + traceback.format_exc())
            status = "Sort failed -- see log."
        finally:
            self.root.after(0, self._sort_finished, plan, status)

    def _cfg_snapshot(self):
        """(mtime_ns, size) of the cfg file, for drift detection."""
        try:
            st = Path(self.cfg_var.get().strip()).stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _sort_finished(self, plan, status):
        self.worker_running = False
        self.sort_button.configure(state="normal")
        self.status_var.set(status)
        self._current_plan = plan
        self._cfg_at_sort = self._cfg_snapshot()  # detect drift before Export
        # carry the previous opt-outs forward across a re-Sort
        prev_disabled_p = self.order_panel.get_disabled()
        prev_disabled_d = self.data_order_panel.get_disabled()
        final_order = (plan or {}).get("final_order") or []
        self.order_panel.load(
            final_order, (plan or {}).get("subset") or [], disabled_items=prev_disabled_p
        )
        # plugins with a missing / mis-ordered master render bright red
        self.order_panel.set_errors((plan or {}).get("master_problem_plugins") or [])

        data_result = (plan or {}).get("data_result") or []
        data_lines = [line for line, _, _ in data_result]
        # Highlight every data= path that's OURS -- both genuinely new inserts
        # AND ones that are already in openmw.cfg because a prior
        # momw-configurator run baked them in (e.g. SetBonus/SkillFramework
        # added via the customizations TOML). Gating on is_new alone would leave
        # those un-highlighted even though they're part of what this sort
        # manages; matching them against this run's data-path inputs mirrors how
        # the plugin panel highlights all subset plugins, not just brand-new ones.
        user_norms = {
            core.normalize_data_path(d["value"]) for d in ((plan or {}).get("data_inserts") or [])
        }
        user_norms.discard("")

        def _is_ours(line, is_new):
            if is_new:
                return True
            p = core.normalize_data_path(core.extract_data_path_value(line) or "")
            return bool(p) and p in user_norms

        highlight_lines = [line for line, is_new, _ in data_result if _is_ours(line, is_new)]
        self.data_order_panel.load(data_lines, highlight_lines, disabled_items=prev_disabled_d)

        self.export_button.configure(state="normal" if (final_order or data_lines) else "disabled")
        self.conflicts_button.configure(state="normal" if final_order else "disabled")
        self.cellmap_button.configure(state="normal" if final_order else "disabled")
        self.resource_button.configure(state="normal" if final_order else "disabled")
        self.lint_button.configure(state="normal" if final_order else "disabled")

    def on_export(self):
        if self.worker_running or not self._current_plan:
            return
        args = (
            self._validate()
        )  # re-read current write-related fields (write_cfg, emit_toml, dry_run, ...)
        if args is None:
            return

        # cfg drift watchdog: if openmw.cfg changed since the Sort (e.g. the
        # Configurator re-ran), everything on screen is based on stale contents
        snap_then = getattr(self, "_cfg_at_sort", None)
        if (
            snap_then is not None
            and self._cfg_snapshot() != snap_then
            and not messagebox.askyesno(
                _("openmw.cfg changed"),
                "openmw.cfg has changed on disk since you ran '1. Sort' (did "
                "momw-configurator re-run?).\n\nThe order on screen was computed "
                "against the OLD contents. It's safer to re-Sort first.\n\n"
                "Export anyway?",
            )
        ):
            return

        if self.write_toml_inplace_var.get() and not args.dry_run:
            backup_note = (
                "" if self.no_backup_var.get() else " (a .bak-<timestamp> copy will be made first)"
            )
            if not messagebox.askyesno(
                _("Overwrite customizations.toml?"),
                f"This will overwrite:\n{args.emit_toml}\n\nin place{backup_note}. Continue?",
            ):
                return

        # Export only the ENABLED rows; the opted-out ones are omitted (and, if
        # they already exist in the cfg, removed via removeContent/removeData).
        final_order = self.order_panel.get_enabled()
        data_order = self.data_order_panel.get_enabled()
        disabled_plugins = self.order_panel.get_disabled()
        disabled_data = self.data_order_panel.get_disabled()
        self.worker_running = True
        self.sort_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.status_var.set(_("Exporting..."))

        thread = threading.Thread(
            target=self._export_worker,
            args=(args, final_order, data_order, disabled_plugins, disabled_data),
            daemon=True,
        )
        thread.start()

    def _export_worker(self, args, final_order, data_order, disabled_plugins, disabled_data):
        writer = QueueWriter(self.log_queue)
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                result = core.write_plan(
                    args,
                    self._current_plan,
                    final_order=final_order or None,
                    data_order=data_order or None,
                    disabled_plugins=disabled_plugins,
                    disabled_data=disabled_data,
                )
            status = (
                f"Export done -- cfg written: {'yes' if result['wrote_cfg'] else 'no'}, "
                f"toml written: {'yes' if result['wrote_toml'] else 'no'}."
            )
        except SystemExit as e:
            writer.write(f"\nERROR: {e}\n")
            status = "Export failed -- see log."
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: unexpected exception:\n" + traceback.format_exc())
            status = "Export failed -- see log."
        finally:
            self.root.after(0, self._export_finished, status)

    def _export_finished(self, status):
        self.worker_running = False
        self.sort_button.configure(state="normal")
        self.export_button.configure(state="normal" if self._current_plan else "disabled")
        self.conflicts_button.configure(state="normal" if self._current_plan else "disabled")
        self.cellmap_button.configure(state="normal" if self._current_plan else "disabled")
        self.resource_button.configure(state="normal" if self._current_plan else "disabled")
        self.status_var.set(status)

    # -- conflict detection --------------------------------------------------

    def _apply_exclusions(self, order):
        """Drop plugins matching the user's exclude patterns (Options field);
        logs what was skipped."""
        import re as _re

        pats = [p for p in _re.split(r"[,\n]", self.exclude_var.get()) if p.strip()]
        kept, excl = core.filter_plugins(order, pats)
        if excl:
            self.log_queue.put(
                f"\n  Excluded {len(excl)} plugin(s) by your filter: "
                + ", ".join(excl[:12])
                + (" ..." if len(excl) > 12 else "")
                + "\n"
            )
        return kept

    def _tes3conv_json_dir(self):
        return app_base_dir() / "tes3conv_json"

    def _on_keep_json_toggle(self):
        """The JSON dump always lives in the same 'tes3conv_json' folder so it's
        reused within a run; this checkbox only decides whether it's KEPT on close
        or removed. Flipping it just updates the live session's keep flag."""
        keep = bool(self.keep_json_var.get())
        self._keep_json = keep
        s = getattr(self, "_session", None)
        if s is not None:
            s.keep = keep
        dest = self._tes3conv_json_dir()
        self.status_var.set(
            f"Keeping tes3conv JSON dump in: {dest}"
            if keep
            else f"tes3conv JSON dump ({dest}) will be removed on close."
        )

    def _get_session(self, conv):
        """Reuse ONE disk-backed tes3conv session across scans, ALWAYS dumping to the
        same 'tes3conv_json' folder -- so every plugin is converted at most once per
        run (Check Conflicts then Cell Map reuse the JSON, no re-running tes3conv).
        A cached JSON is re-used only if it's newer than its plugin (mtime check in
        core), so an edited plugin still re-converts. The 'Keep tes3conv JSON dump'
        option only controls whether that folder is removed on close. Called from a
        worker thread; self._keep_json is snapshotted on the main thread."""
        if not conv:
            return None
        keep = bool(getattr(self, "_keep_json", False))
        s = getattr(self, "_session", None)
        if s is not None and getattr(s, "exe", None) == conv:
            s.keep = keep  # same dump folder -> just track keep
            return s
        if s is not None:  # engine path changed -> retire the old one
            try:
                s.cleanup()
            except Exception:  # noqa: BLE001
                # retiring a replaced engine session; failure must not block the new one
                pass
        s = core.Tes3ConvSession(conv, dump_dir=str(self._tes3conv_json_dir()), keep=keep)
        self._session = s
        return s

    def on_check_conflicts(self):
        """Scan the current (sorted, enabled) plugins for TES3 record conflicts.
        Runs in a worker since parsing every plugin can take a moment."""
        if self.worker_running or not self._current_plan:
            return
        order = self._apply_exclusions(self.order_panel.get_enabled())
        if not order:
            return
        dirs = self._plan_scan_dirs()
        subset = self._current_plan.get("subset") or []
        self._keep_json = self.keep_json_var.get()
        self._conf_subset_lower = {str(s).lower() for s in subset}  # your custom mods
        self.worker_running = True
        self.sort_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.conflicts_button.configure(state="disabled")
        self.status_var.set(_("Scanning for conflicts..."))
        threading.Thread(
            target=self._conflicts_worker, args=(order, dirs, subset), daemon=True
        ).start()

    def _plan_scan_dirs(self):
        """All folders the scans should search for THIS run: the cfg's data=
        dirs plus the pending custom data paths (scan / customizations TOML)
        that aren't in the cfg yet -- so Check Conflicts / Cell Map / Resource
        Conflicts can see your custom mods BEFORE the cfg is written."""
        plan = self._current_plan or {}
        dirs = plan.get("scan_dirs")
        if dirs:
            return list(dirs)
        # fallback for an old plan dict: rebuild from its parts
        return core.all_scan_dirs(
            plan.get("data_order") or [],
            plan.get("raw_toml_data_inserts"),
            plan.get("data_inserts"),
        )

    def _conflicts_worker(self, order, dirs, subset):
        writer = QueueWriter(self.log_queue)
        conflicts, stats, session = [], {}, None
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                index = core.PluginFileIndex(dirs)
                cfg_dir = (
                    str(Path(self.cfg_var.get().strip()).parent)
                    if self.cfg_var.get().strip()
                    else None
                )
                conv = core.find_tes3conv(explicit=self._tes3conv_override, extra_dirs=[cfg_dir])
                session = self._get_session(conv)
                print("\n" + "=" * 70)
                print(_(" TES3 RECORD CONFLICTS (read-only)"))
                print("=" * 70)
                if session:
                    print(f"  Engine: tes3conv ({conv}) -- field-level diffs available.")
                else:
                    print(
                        "  Engine: built-in parser (record-level). Point the Conflicts window at "
                        "a tes3conv binary for field-level diffs."
                    )
                conflicts, stats = core.detect_conflicts(
                    order, index, subset_names=subset, session=session
                )
                print(core.format_conflict_report(conflicts, stats, limit=200))
            n_sub = sum(1 for c in conflicts if c.get("involves_subset"))
            status = (
                f"Conflicts: {stats.get('conflicts', 0)} record(s), "
                f"{n_sub} involving your mods. See the Conflicts window."
            )
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: conflict scan failed:\n" + traceback.format_exc())
            status = "Conflict scan failed -- see log."
        finally:
            self.root.after(0, self._conflicts_finished, conflicts, stats, session, status)

    def _conflicts_finished(self, conflicts, stats, session, status):
        self.worker_running = False
        self.sort_button.configure(state="normal")
        self.export_button.configure(state="normal" if self._current_plan else "disabled")
        self.conflicts_button.configure(state="normal" if self._current_plan else "disabled")
        self.cellmap_button.configure(state="normal" if self._current_plan else "disabled")
        self.resource_button.configure(state="normal" if self._current_plan else "disabled")
        self.lint_button.configure(state="normal" if self._current_plan else "disabled")
        self.status_var.set(status)
        self._conf_session = session
        self._conf_paths = (stats or {}).get("paths", {})
        self._show_conflict_window(conflicts, stats)

    # -- cell map (modmapper) ------------------------------------------------

    def on_cell_map(self):
        if self.worker_running or not self._current_plan:
            return
        order = self._apply_exclusions(self.order_panel.get_enabled())
        if not order:
            return
        dirs = self._plan_scan_dirs()
        subset = self._current_plan.get("subset") or []
        self._keep_json = self.keep_json_var.get()
        self.worker_running = True
        for b in (
            self.sort_button,
            self.export_button,
            self.conflicts_button,
            self.cellmap_button,
            self.resource_button,
        ):
            b.configure(state="disabled")
        self.status_var.set(_("Building cell map..."))
        threading.Thread(
            target=self._cellmap_worker, args=(order, dirs, subset), daemon=True
        ).start()

    def _cellmap_file(self):
        """Stable, user-findable, writable location for the generated map."""
        return app_base_dir() / "cell_map.html"

    def _cellmap_worker(self, order, dirs, subset):
        writer = QueueWriter(self.log_queue)
        path = None
        core.trace(f"cell map: start, {len(order)} plugin(s)")
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                index = core.PluginFileIndex(dirs)
                cfg_dir = (
                    str(Path(self.cfg_var.get().strip()).parent)
                    if self.cfg_var.get().strip()
                    else None
                )
                conv = core.find_tes3conv(explicit=self._tes3conv_override, extra_dirs=[cfg_dir])
                session = self._get_session(conv)
                print("\n" + "=" * 70)
                print(_(" CELL MAP"))
                print("=" * 70)
                print(f"  Engine: {'tes3conv' if conv else 'built-in parser'}")
                cov = core.build_cell_coverage(order, index, subset_names=subset, session=session)
                core.trace(
                    f"cell map: coverage built, {len(cov['exterior'])} ext, {len(cov['interior'])} int"
                )
                html = core.generate_cell_map_html(cov)
                core.trace(f"cell map: html built, {len(html)} bytes")
                # Write straight to disk and drop the string -- the map is viewed
                # FROM the file (browser / tkinterweb load_file), never rendered
                # from an in-memory 2MB+ string (that path OOM'd tkhtmlview).
                path = str(self._cellmap_file())
                try:
                    Path(path).write_text(html, encoding="utf-8")
                except OSError:
                    # script dir not writable (e.g. a read-only install) -> temp
                    import tempfile

                    fd, path = tempfile.mkstemp(prefix="cell_map_", suffix=".html")
                    os.close(fd)
                    Path(path).write_text(html, encoding="utf-8")
                del html
                core.trace(f"cell map: written to {path}")
                print(
                    f"  {len(cov['exterior'])} exterior + {len(cov['interior'])} interior cell(s) "
                    f"touched across {cov['scanned']} plugin(s)."
                )
                print(f"  Map written to: {path}")
            status = f"Cell map ready ({len(cov['exterior'])} exterior, {len(cov['interior'])} interior)."
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: cell map failed:\n" + traceback.format_exc())
            status = "Cell map failed -- see log."
        finally:
            self.root.after(0, self._cellmap_finished, path, status)

    def _cellmap_finished(self, path, status):
        self.worker_running = False
        self.sort_button.configure(state="normal")
        for b in (
            self.export_button,
            self.conflicts_button,
            self.cellmap_button,
            self.resource_button,
            self.lint_button,
        ):
            b.configure(state="normal" if self._current_plan else "disabled")
        self.status_var.set(status)
        if not path:
            return
        self._last_cell_file = path
        # Optional override: MLOX_MAP_VIEWER = pywebview | tkinterweb | browser.
        # Handy when pywebview is installed but its backend is broken (e.g. its
        # WebView2/pythonnet backend on a too-new Python) -- force tkinterweb/browser.
        force = (os.environ.get("MLOX_MAP_VIEWER") or "").strip().lower()
        can_tkweb = HTMLViewer is not None and hasattr(HTMLViewer, "load_file")
        if force == "browser":
            core.trace("cell map: viewer = browser (forced)")
            self._open_cell_map_browser()
            return
        if force == "tkinterweb" and can_tkweb:
            core.trace("cell map: viewer = tkinterweb (forced)")
            self._show_cell_map_window(path)
            return
        if force == "pywebview" and HAVE_PYWEBVIEW:
            core.trace("cell map: viewer = pywebview (forced)")
            self._open_cell_map_pywebview(path)
            return
        # Auto: prefer pywebview (real OS webview), then tkinterweb's load_file,
        # then the browser. tkhtmlview can't draw SVG, so it's never used here.
        if HAVE_PYWEBVIEW:
            core.trace("cell map: viewer = pywebview (embedded)")
            self._open_cell_map_pywebview(path)
        elif can_tkweb:
            core.trace("cell map: viewer = tkinterweb (in-app window)")
            self._show_cell_map_window(path)
        else:
            core.trace("cell map: viewer = browser (no pywebview/tkinterweb available)")
            self._open_cell_map_browser()
            self.status_var.set(
                status + "  (opened in browser — pip install pywebview " "for an in-app window)"
            )

    def _open_cell_map_pywebview(self, path):
        """Show the map in an embedded OS webview by re-invoking ourselves with
        --show-map in a SEPARATE process (webview.start() needs its own main
        thread). Frozen-safe: a built .exe re-runs the .exe; from source we re-run
        the script -- never 'python -c', which a frozen exe can't do."""
        ap = os.path.abspath(path)  # noqa: PTH100 - must not resolve symlinks
        # IMPORTANT: only CREATE_NO_WINDOW here (suppresses a console flash) -- do
        # NOT use the SW_HIDE startupinfo from _no_window_kwargs(): that STARTUPINFO
        # is inherited by the child's FIRST window, which would hide the WebView2
        # cell-map window itself (it spawns but never shows). That was the bug.
        nw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--show-map", ap]
            else:
                cmd = [sys.executable, os.path.abspath(__file__), "--show-map", ap]  # noqa: PTH100
            core.trace(f"cell map: launching pywebview child: {cmd}")
            subprocess.Popen(cmd, **nw)
        except (OSError, ValueError):  # Popen: missing exe or bad argv
            core.trace("cell map: pywebview child launch FAILED:\n" + traceback.format_exc())
            self._open_cell_map_browser()

    def _show_cell_map_window(self, path):
        win = getattr(self, "_cellmap_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()
        win = tk.Toplevel(self.root)
        self._cellmap_win = win
        win.title("Cell Map")
        win.configure(bg=DARK["bg"])
        win.geometry("1000x720")
        bar = ttk.Frame(win, padding=6)
        bar.pack(fill="x")
        ttk.Button(bar, text=_("Save HTML..."), command=self._save_cell_map).pack(side="left")
        ttk.Button(bar, text=_("Open in browser"), command=self._open_cell_map_browser).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(bar, text=_("Close"), command=win.destroy).pack(side="right")
        try:
            viewer = HTMLViewer(win)
            viewer.pack(fill="both", expand=True)
            viewer.load_file(path)  # reads from disk, not an in-memory string
        except Exception:  # noqa: BLE001
            # third-party HTML widget; falls back to the browser
            ttk.Label(
                win,
                foreground="#ffb454",
                padding=8,
                text=_(
                    "(inline render failed — use 'Open in browser' for the full map)",
                ),
            ).pack(anchor="w")

    def _open_cell_map_browser(self):
        p = getattr(self, "_last_cell_file", None)
        if not p or not Path(p).exists():
            return
        try:
            webbrowser.open(Path(p).resolve().as_uri())  # correct file URI on Win/Linux/macOS
        except (OSError, ValueError, webbrowser.Error):  # as_uri on a relative path / no browser
            pass

    def _save_cell_map(self):
        src = getattr(self, "_last_cell_file", None)
        if not src or not Path(src).exists():
            return
        out = filedialog.asksaveasfilename(
            title=_("Save cell map"),
            defaultextension=".html",
            initialfile="cell_map.html",
            filetypes=(("HTML files", "*.html"), ("All files", "*.*")),
        )
        if not out:
            return
        try:
            import shutil

            if os.path.abspath(out) != os.path.abspath(src):  # noqa: PTH100
                shutil.copyfile(src, out)
            self.status_var.set(f"Cell map saved: {out}")
        except OSError as e:
            messagebox.showerror(_("Save failed"), str(e))

    # -- resource (VFS) conflicts --------------------------------------------

    def on_resource_conflicts(self):
        if self.worker_running or not self._current_plan:
            return
        dirs = self._plan_scan_dirs()
        if not dirs:
            self.status_var.set(_("No data= folders to scan."))
            return
        subset_dirs = self._current_plan.get("custom_data_dirs") or core.pending_custom_dirs(
            self._current_plan.get("raw_toml_data_inserts"), self._current_plan.get("data_inserts")
        )
        self.worker_running = True
        for b in (
            self.sort_button,
            self.export_button,
            self.conflicts_button,
            self.cellmap_button,
            self.resource_button,
        ):
            b.configure(state="disabled")
        self.status_var.set(_("Scanning data folders for file conflicts..."))
        threading.Thread(
            target=self._resource_worker, args=(dirs, subset_dirs), daemon=True
        ).start()

    def _resource_worker(self, dirs, subset_dirs):
        writer = QueueWriter(self.log_queue)
        conflicts, stats = [], {}
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                print("\n" + "=" * 70)
                print(_(" DATA-PATH RESOURCE (VFS) CONFLICTS"))
                print("=" * 70)
                conflicts, stats = core.detect_resource_conflicts(dirs, subset_dirs=subset_dirs)
                print(core.format_resource_report(conflicts, stats, limit=200))
            status = f"Resource conflicts: {stats.get('conflicts', 0)} file(s). See the window."
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: resource scan failed:\n" + traceback.format_exc())
            status = "Resource scan failed -- see log."
        finally:
            self.root.after(0, self._resource_finished, conflicts, stats, status)

    def _resource_finished(self, conflicts, stats, status):
        self.worker_running = False
        self.sort_button.configure(state="normal")
        for b in (
            self.export_button,
            self.conflicts_button,
            self.cellmap_button,
            self.resource_button,
            self.lint_button,
        ):
            b.configure(state="normal" if self._current_plan else "disabled")
        self.status_var.set(status)
        self._show_resource_window(conflicts, stats)

    # -- download sources dialog ---------------------------------------------

    def on_sources(self):
        win = getattr(self, "_src_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            return
        win = tk.Toplevel(self.root)
        self._src_win = win
        win.title("Download sources")
        win.configure(bg=DARK["bg"])
        win.geometry("780x300")
        top = ttk.Frame(win, padding=12)
        top.pack(fill="both", expand=True)
        top.columnconfigure(1, weight=1)

        ttk.Label(
            top,
            text="If upstream moves or a new fork takes over, point the updaters at "
            "the new location here. Blank = built-in defaults. Downloads are "
            "always validated before anything is overwritten.",
            foreground=DARK["fg_dim"],
            wraplength=720,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Label(top, text=_("mlox rules URL template:")).grid(row=1, column=0, sticky="w")
        rent = ttk.Entry(top, textvariable=self.rules_url_var)
        rent.grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        add_tooltip(
            rent,
            "Where 'Update Rules...' downloads from. Must contain {name}, which "
            "is replaced with mlox_base.txt / mlox_user.txt per file.\n"
            f"Default: {core.RULES_URL_TEMPLATE}",
        )
        ttk.Label(top, text=f"default: {core.RULES_URL_TEMPLATE}", foreground=DARK["fg_dim"]).grid(
            row=2, column=1, sticky="w", padx=6
        )

        ttk.Label(top, text=_("plugin-order.yml URL:")).grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )
        pent = ttk.Entry(top, textvariable=self.plugin_order_url_var)
        pent.grid(row=3, column=1, sticky="ew", padx=6, pady=(10, 2))
        add_tooltip(
            pent,
            "Where the plugin-order.yml 'Update...' button downloads from. "
            "Blank tries the built-in candidates in order.\n"
            f"Default: {core.PLUGIN_ORDER_URLS[0]}",
        )
        ttk.Label(
            top, text=f"default: {core.PLUGIN_ORDER_URLS[0][:96]}...", foreground=DARK["fg_dim"]
        ).grid(row=4, column=1, sticky="w", padx=6)

        row = ttk.Frame(top)
        row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(16, 0))

        def _reset():
            self.rules_url_var.set("")
            self.plugin_order_url_var.set("")

        def _close():
            t = self.rules_url_var.get().strip()
            if t and "{name}" not in t:
                messagebox.showerror(
                    _("Download sources"),
                    "The rules URL template must contain {name} (it's replaced "
                    "with the rule filename).",
                    parent=win,
                )
                return
            self._save_settings()
            win.destroy()

        ttk.Button(row, text=_("Reset to defaults"), command=_reset).pack(side="left")
        ttk.Button(row, text=_("Save & Close"), command=_close).pack(side="right")

    # -- mlox user-rules maker -----------------------------------------------

    def _default_rules_file(self):
        """Prefill: an existing personal file already in the rules list, else
        'mlox_my_rules.txt' next to the first rule file (or the cfg)."""
        for p in self.rules_panel.get_paths():
            if Path(p).name.lower() not in ("mlox_base.txt", "mlox_user.txt"):
                return str(p)
        paths = self.rules_panel.get_paths()
        base = Path(paths[0]).parent if paths else Path(self._cfg_dir() or ".")
        return str(base / "mlox_my_rules.txt")

    def on_rule_maker(self):
        win = getattr(self, "_rm_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            return
        win = tk.Toplevel(self.root)
        self._rm_win = win
        win.title("New mlox rule")
        win.configure(bg=DARK["bg"])
        win.geometry("720x600")
        win.minsize(660, 560)
        top = ttk.Frame(win, padding=10)
        top.pack(fill="both", expand=True)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text=_("rules file:")).grid(row=0, column=0, sticky="w")
        self._rm_file_var = tk.StringVar(value=self._default_rules_file())
        fent = ttk.Entry(top, textvariable=self._rm_file_var)
        fent.grid(row=0, column=1, sticky="ew", padx=6)
        add_tooltip(
            fent,
            "Personal rules file the rule is appended to (created with a header "
            "if new). Use your OWN file, not mlox_base/mlox_user -- those get "
            "overwritten by 'Update Rules...'. It's auto-added to the rule-files "
            "list LAST, so your rules win conflicts.",
        )
        ttk.Button(top, text=_("Browse..."), command=self._rm_browse_file).grid(row=0, column=2)

        tf = ttk.LabelFrame(top, text=_("Rule type"))
        tf.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        self._rm_kind = tk.StringVar(value="order")
        for i, (v, lbl) in enumerate(
            (
                ("order", "[Order] -- the plugins below load in this order (first loads first)"),
                ("nearstart", "[NearStart] -- each plugin below is pulled toward the START"),
                ("nearend", "[NearEnd] -- each plugin below is pulled toward the END"),
            )
        ):
            ttk.Radiobutton(
                tf, text=lbl, value=v, variable=self._rm_kind, command=self._rm_refresh
            ).grid(row=i, column=0, sticky="w", padx=8, pady=1)

        pf = ttk.LabelFrame(top, text=_("Plugins (drag order matters for [Order])"))
        pf.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=4)
        top.rowconfigure(2, weight=1)
        pf.columnconfigure(0, weight=1)
        pf.rowconfigure(0, weight=1)
        self._rm_list = DragReorderListbox(
            pf,
            selectmode="extended",
            exportselection=False,
            activestyle="dotbox",
            height=5,
            on_reorder=self._rm_refresh,
        )
        style_plain_widget(self._rm_list)
        attach_typeahead(self._rm_list)
        self._rm_list.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        rsc = ttk.Scrollbar(pf, orient="vertical", command=self._rm_list.yview)
        rsc.grid(row=0, column=1, sticky="ns", pady=8)
        self._rm_list.configure(yscrollcommand=rsc.set)

        rbtns = ttk.Frame(pf)
        rbtns.grid(row=0, column=2, sticky="n", padx=8, pady=8)
        b = ttk.Button(rbtns, text=_("From plugin panel"), command=self._rm_add_from_panel)
        b.pack(fill="x", pady=2)
        add_tooltip(
            b,
            "Add the rows currently SELECTED in the main plugin-order panel, in "
            "their displayed order (Ctrl/Shift-click there to multi-select first).",
        )
        ttk.Button(rbtns, text=_("Remove"), command=self._rm_remove).pack(fill="x", pady=2)
        ttk.Button(
            rbtns,
            text=_("Clear"),
            command=lambda: (self._rm_list.delete(0, "end"), self._rm_refresh()),
        ).pack(fill="x", pady=2)
        af = ttk.Frame(pf)
        af.grid(row=1, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 8))
        af.columnconfigure(0, weight=1)
        self._rm_add_var = tk.StringVar()
        aent = ttk.Entry(af, textvariable=self._rm_add_var)
        aent.grid(row=0, column=0, sticky="ew")
        add_tooltip(
            aent,
            "Type a plugin name or mlox pattern (wildcards * ? and <VER> allowed; "
            "must end in a plugin extension) and press Enter or Add.",
        )
        aent.bind("<Return>", lambda e: self._rm_add_typed())
        ttk.Button(af, text=_("Add"), command=self._rm_add_typed).grid(row=0, column=1, padx=(6, 0))

        ttk.Label(top, text=_("comment (optional):")).grid(row=3, column=0, sticky="w", pady=(4, 0))
        self._rm_comment = tk.StringVar()
        cent = ttk.Entry(top, textvariable=self._rm_comment)
        cent.grid(row=3, column=1, columnspan=2, sticky="ew", padx=6, pady=(4, 0))
        cent.bind("<KeyRelease>", lambda e: self._rm_refresh())
        add_tooltip(
            cent,
            "Written above the rule as a ';;' comment. The mlox rule guidelines "
            "suggest citing your source, e.g. (Ref: the mod's readme) or "
            "(Ref: a forum URL ) -- surround URLs with spaces. Handy if you "
            "later contribute the rule upstream.",
        )

        vf = ttk.LabelFrame(top, text=_("Preview"))
        vf.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        self._rm_preview = tk.Text(
            vf,
            height=5,
            wrap="none",
            state="disabled",
            background=DARK["log_bg"],
            foreground=DARK["fg"],
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=DARK["border"],
        )
        self._rm_preview.pack(fill="x", padx=8, pady=8)

        row = ttk.Frame(top)
        row.grid(row=5, column=0, columnspan=3, sticky="ew")
        ttk.Button(row, text=_("Append Rule"), command=self._rm_append).pack(side="left")
        ttk.Button(row, text=_("Close"), command=win.destroy).pack(side="right")
        self._rm_refresh()

    def _rm_names(self):
        return list(self._rm_list.get(0, "end"))

    def _rm_add_from_panel(self):
        sel = sorted(self.order_panel.listbox.curselection())
        if not sel:
            self.status_var.set(_("Select row(s) in the plugin-order panel first."))
            return
        have = {n.lower() for n in self._rm_names()}
        for i in sel:
            name = self.order_panel._strip(self.order_panel.listbox.get(i))
            if name.lower() not in have:
                self._rm_list.insert("end", name)
        self._rm_refresh()

    def _rm_add_typed(self):
        v = self._rm_add_var.get().strip()
        if v and v.lower() not in {n.lower() for n in self._rm_names()}:
            self._rm_list.insert("end", v)
            self._rm_add_var.set("")
        self._rm_refresh()

    def _rm_remove(self):
        for i in reversed(self._rm_list.curselection()):
            self._rm_list.delete(i)
        self._rm_refresh()

    def _rm_preview_text(self):
        try:
            names = self._rm_names()
            if not names:
                return "(add plugins above)"
            # build without writing: validate via the same code path
            kw = self._rm_kind.get()
            titles = {"order": "Order", "nearstart": "NearStart", "nearend": "NearEnd"}
            for n in names:
                m = core._RE_ORDER_NAME.match(n)
                if any(c in n for c in "[];") or not m or m.group(0) != n:
                    return f"INVALID: {n!r} -- names must end in a plugin extension"
            if kw == "order" and len(names) < 2:
                return "(an [Order] rule needs at least two plugins)"
            parts = []
            c = self._rm_comment.get().strip()
            if c:
                parts += [f";; {line}" for line in c.splitlines()]
            parts.append(f"[{titles[kw]}]")
            parts += names
            return "\n".join(parts)
        except Exception as e:  # noqa: BLE001
            # live rule preview; any failure becomes preview text
            return f"error: {e}"

    def _rm_browse_file(self):
        """Ask for a personal rules file and remember the choice.

        A cancelled dialog returns an empty string, which must leave the
        current value alone rather than blanking it.
        """
        chosen = filedialog.asksaveasfilename(
            title=_("Personal rules file"),
            initialfile="mlox_my_rules.txt",
            defaultextension=".txt",
            filetypes=(("Rules", "*.txt"),),
        )
        trace_first_fire("rules-maker Browse...")
        core.trace(f"[smoke] rules-maker Browse: {'chose ' + chosen if chosen else 'cancelled'}")
        if chosen:
            self._rm_file_var.set(chosen)

    def _rm_refresh(self):
        trace_first_fire("rules-maker refresh (radio / reorder)")
        txt = self._rm_preview_text()
        self._rm_preview.configure(state="normal")
        self._rm_preview.delete("1.0", "end")
        self._rm_preview.insert("1.0", txt)
        self._rm_preview.configure(state="disabled")

    def _rm_append(self):
        path = self._rm_file_var.get().strip()
        if not path:
            messagebox.showerror(_("New rule"), _("Set a rules file first."), parent=self._rm_win)
            return
        if Path(path).name.lower() in ("mlox_base.txt", "mlox_user.txt"):
            messagebox.showerror(
                _("New rule"),
                "Don't append personal rules to mlox_base.txt / mlox_user.txt -- "
                "'Update Rules...' overwrites those. Pick your own file "
                "(e.g. mlox_my_rules.txt).",
                parent=self._rm_win,
            )
            return

        # Cycle pre-check: for an [Order] rule, warn if it fights the frozen
        # curated order (mlox would discard those edges as cycles, so the rule
        # silently wouldn't take effect). Advisory only -- the user may be
        # planning to install those mods, or know what they're doing.
        names = self._rm_names()
        if self._rm_kind.get() == "order" and self._current_plan:
            final = self._current_plan.get("final_order") or []
            subset_lower = {str(s).lower() for s in (self._current_plan.get("subset") or [])}
            curated = {
                str(n).lower()
                for n in (self._current_plan.get("base_order_names") or [])
                if str(n).lower() not in subset_lower
            }
            bad = core.order_rule_frozen_conflicts(names, final, curated)
            if bad:
                pairs = "\n".join(
                    f"  '{a}'  before  '{b}'  (your cfg has them the other way)" for a, b in bad
                )
                if not messagebox.askyesno(
                    _("Rule conflicts with the curated order"),
                    f"This [Order] rule contradicts the frozen curated (MOMW) order for:\n\n"
                    f"{pairs}\n\nmlox will DISCARD those orderings (it never reorders the "
                    f"curated list), so the rule won't take effect for them. Write it "
                    f"anyway?",
                    parent=self._rm_win,
                ):
                    return
        try:
            core.append_user_rule(path, self._rm_kind.get(), names, comment=self._rm_comment.get())
        except (ValueError, OSError) as e:
            messagebox.showerror(_("New rule"), str(e), parent=self._rm_win)
            return
        # make sure the file is in the rules list, LAST (= highest priority)
        if str(path).lower() not in {str(p).lower() for p in self.rules_panel.get_paths()}:
            self.rules_panel.listbox.insert("end", path)
        self._rm_list.delete(0, "end")
        self._rm_comment.set("")
        self._rm_refresh()
        self.status_var.set(f"Rule appended to {Path(path).name}. Re-run '1. Sort' to apply it.")

    # -- plugin-order.yml updater --------------------------------------------

    def on_update_plugin_order_yml(self):
        p = self.plugin_order_yml_var.get().strip()
        if not p:
            messagebox.showinfo(
                _("Update plugin-order.yml"),
                _(
                    "Set the plugin-order.yml path first (or Browse to where you "
                    "want it created)."
                ),
            )
            return
        ages = core.rule_file_ages([p])
        age = ages[0][1]
        age_txt = (
            "file doesn't exist yet -- it will be created"
            if age is None
            else f"your copy is ~{age} day(s) old"
        )
        if not messagebox.askyesno(
            _("Update plugin-order.yml"),
            f"Download the current plugin-order.yml from MOMW?\n\n{age_txt}.\n\n"
            f"The download is validated before anything is written; a timestamped "
            f".bak of the old file is kept.",
        ):
            return

        custom = self.plugin_order_url_var.get().strip()
        urls = [custom] if custom else None

        def work():
            try:
                report = core.update_plugin_order_yml(p, urls=urls)
            except Exception as e:  # noqa: BLE001
                # worker thread: must report into the dialog, never vanish silently
                report = [f"FAILED: {e}"]
            self.root.after(
                0,
                lambda: (
                    messagebox.showinfo(_("Update plugin-order.yml"), "\n".join(report)),
                    self.status_var.set(report[0] if report else ""),
                ),
            )

        self.status_var.set(_("Downloading plugin-order.yml..."))
        threading.Thread(target=work, daemon=True).start()

    # -- savegame dependency check -------------------------------------------

    def on_save_check(self):
        order = (
            self.order_panel.get_enabled()
            if self.order_panel.has_order()
            else list((self._current_plan or {}).get("base_order_names") or [])
        )
        if not order:
            self.status_var.set(_("Run '1. Sort' first so there's a load order to check against."))
            return
        start = Path.home() / "Documents" / "My Games" / "OpenMW"
        p = filedialog.askopenfilename(
            title=_("Choose an OpenMW save"),
            initialdir=str(start if start.is_dir() else (self._cfg_dir() or ".")),
            filetypes=(("OpenMW saves", "*.omwsave"), ("All files", "*.*")),
        )
        if not p:
            return
        files, missing, err = core.check_savegame_against_order(p, order)
        writer = QueueWriter(self.log_queue)
        writer.write("\n" + "=" * 70 + f"\n SAVEGAME CHECK: {Path(p).name}\n" + "=" * 70 + "\n")
        if err:
            writer.write(f"  ERROR: {err}\n")
            self.status_var.set(f"Save check failed: {err}")
            return
        writer.write(f"  Save depends on {len(files)} content file(s).\n")
        if missing:
            for m in missing:
                writer.write(
                    f"\n[MISSING SAVE DEP] '{m}' is required by this save but not in "
                    f"the current load order -- OpenMW will refuse to load it.\n"
                )
            self.status_var.set(f"Save check: {len(missing)} missing dependencies! See the log.")
            messagebox.showwarning(
                _("Save Check"),
                f"{Path(p).name} depends on {len(missing)} content file(s) that are NOT in "
                f"the current load order:\n\n  "
                + "\n  ".join(missing[:12])
                + ("\n  ..." if len(missing) > 12 else "")
                + "\n\nOpenMW will refuse to load this save. Re-enable those plugins (or don't "
                "export this order if you want to keep playing that character).",
            )
        else:
            writer.write("  All dependencies present -- this save is safe with this order.\n")
            self.status_var.set(f"Save check: all {len(files)} dependencies present.")
            messagebox.showinfo(
                _("Save Check"),
                f"{Path(p).name}: all {len(files)} content files it needs are in "
                f"the current load order. Safe to export.",
            )

    # -- backup manager ------------------------------------------------------

    def on_backups(self):
        if self.worker_running:
            return
        dirs = self._plan_scan_dirs()
        cfg = self.cfg_var.get().strip() or None
        if not dirs and not cfg:
            self.status_var.set(_("Set openmw.cfg (or run a Sort) so I know where to look."))
            return
        self.worker_running = True
        self.status_var.set(_("Scanning for backups..."))
        threading.Thread(target=self._backups_worker, args=(dirs, cfg), daemon=True).start()

    def _backups_worker(self, dirs, cfg):
        try:
            found = core.scan_backups(dirs, cfg_path=cfg)
            status = f"Found {len(found)} backup file(s)."
        except Exception as e:  # noqa: BLE001
            # worker top level: status line carries the failure
            found, status = [], f"Backup scan failed: {e}"
        finally:
            self.worker_running = False
        self.root.after(0, self._show_backups_window, found, status)

    def _show_backups_window(self, found, status):
        self.status_var.set(status)
        win = getattr(self, "_bk_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()
        win = tk.Toplevel(self.root)
        self._bk_win = win
        win.title("Backups")
        win.configure(bg=DARK["bg"])
        win.geometry("900x480")
        top = ttk.Frame(win, padding=10)
        top.pack(fill="both", expand=True)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)
        ttk.Label(
            top,
            text=f"{len(found)} backup file(s). Restore copies the backup over its "
            f"original; Delete removes the backup file itself.",
            foreground=DARK["fg_dim"],
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        lb = tk.Listbox(top, selectmode="extended", exportselection=False, activestyle="dotbox")
        style_plain_widget(lb)
        attach_typeahead(lb)
        lb.grid(row=1, column=0, sticky="nsew", pady=8)
        sc = ttk.Scrollbar(top, orient="vertical", command=lb.yview)
        sc.grid(row=1, column=1, sticky="ns", pady=8)
        lb.configure(yscrollcommand=sc.set)
        self._bk_rows = found
        for bpath, orig, kind in found:
            tag = "" if (orig and Path(orig).exists()) else "   [original missing]"
            lb.insert("end", f"[{kind}]  {bpath}{tag}")
        self._bk_list = lb

        btns = ttk.Frame(top)
        btns.grid(row=2, column=0, sticky="w")

        def _selected():
            return [self._bk_rows[i] for i in lb.curselection()]

        def _restore():
            sel = [r for r in _selected() if r[1]]
            if not sel:
                return
            if not messagebox.askyesno(
                _("Restore"),
                f"Copy {len(sel)} backup(s) over their " f"originals (overwriting them)?",
                parent=win,
            ):
                return
            import shutil as _sh

            ok = fail = 0
            for bpath, orig, _k in sel:
                try:
                    _sh.copy2(bpath, orig)
                    ok += 1
                except OSError:
                    fail += 1
            self.status_var.set(
                f"Restored {ok} backup(s){f', {fail} failed' if fail else ''}. "
                f"Re-run '1. Sort' to refresh checks."
            )

        def _delete():
            sel = _selected()
            if not sel:
                return
            if not messagebox.askyesno(
                _("Delete"), f"Permanently delete {len(sel)} backup " f"file(s)?", parent=win
            ):
                return
            ok = 0
            for bpath, _o, _k in sel:
                try:
                    Path(bpath).unlink()
                    ok += 1
                except OSError:
                    pass
            self.status_var.set(f"Deleted {ok} backup file(s).")
            win.destroy()
            self.on_backups()

        ttk.Button(btns, text=_("Restore Selected"), command=_restore).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btns, text=_("Delete Selected"), command=_delete).pack(side="left", padx=(0, 6))
        ttk.Button(
            btns, text=_("Refresh"), command=lambda: (win.destroy(), self.on_backups())
        ).pack(side="left")
        ttk.Button(btns, text=_("Close"), command=win.destroy).pack(side="right")

    # -- lint (tes3lint-style native checks) ---------------------------------

    def on_lint(self):
        if self.worker_running or not self._current_plan:
            return
        order = self._apply_exclusions(self.order_panel.get_enabled())
        if not order:
            return
        dirs = self._plan_scan_dirs()
        subset = self._current_plan.get("subset") or []
        self.worker_running = True
        for b in (
            self.sort_button,
            self.export_button,
            self.conflicts_button,
            self.cellmap_button,
            self.resource_button,
            self.lint_button,
        ):
            b.configure(state="disabled")
        self.status_var.set(_("Linting plugins..."))
        threading.Thread(target=self._lint_worker, args=(order, dirs, subset), daemon=True).start()

    def _lint_worker(self, order, dirs, subset):
        writer = QueueWriter(self.log_queue)
        stats = {}
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                print("\n" + "=" * 70)
                print(_(" LINT (tes3lint-style checks, native)"))
                print("=" * 70)
                index = core.PluginFileIndex(dirs)
                subset_origins = {str(s).lower(): "your mod" for s in subset}
                warnings, stats = core.lint_plugins(
                    order, index, subset_names=subset, origins=subset_origins
                )
                print(
                    f"  Scanned {stats.get('scanned', 0)} plugin(s); "
                    f"{stats.get('interior_cells', 0)} interior cell(s), "
                    f"{stats.get('pathgrids', 0)} interior pathgrid(s)."
                )
                if warnings:
                    for w in warnings:
                        print(f"\n{w}")
                else:
                    print(_("\n  No lint findings. Clean bill of health."))
            n = stats.get("warnings", 0)
            status = f"Lint: {n} finding(s). See the log." if n else "Lint: no findings."
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: lint failed:\n" + traceback.format_exc())
            status = "Lint failed -- see log."
        finally:
            self.root.after(0, self._lint_finished, status)

    def _lint_finished(self, status):
        self.worker_running = False
        self.sort_button.configure(state="normal")
        for b in (
            self.export_button,
            self.conflicts_button,
            self.cellmap_button,
            self.resource_button,
            self.lint_button,
        ):
            b.configure(state="normal" if self._current_plan else "disabled")
        self.status_var.set(status)

    # -- tes3cmd frontend ----------------------------------------------------

    T3_COMMANDS = (
        (
            "clean",
            "clean -- remove junk (dup records, junk cells, evil GMSTs); runs in a "
            "staged Data Files with the plugin's masters, so tes3cmd sees the full "
            "VFS; skipped if a master can't be found; backup kept",
        ),
        (
            "sync",
            "resync master sizes -- IN-APP, VFS-aware (fixes [MASTER SIZE] notes; "
            "tes3cmd's own sync writes empty sizes on OpenMW multi-folder setups)",
        ),
        ("header", "header -- show author / description / masters (read-only)"),
        # NO multipatch: it needs the ENTIRE load order in one flat Data Files
        # dir, which can't be faked safely for a multi-GB OpenMW setup -- and
        # OpenMW/MOMW users get merged leveled lists from delta-plugin instead.
    )
    T3_MODIFIES: ClassVar[set[str]] = {"clean", "sync"}
    # NEVER cleaned, no exceptions: cleaning the vanilla masters -- even a
    # careful GMST-preserving clean -- rewrites record bytes that other
    # content depends on byte-for-byte, and causes in-game failures.
    T3_NEVER_CLEAN: ClassVar[set[str]] = {"morrowind.esm", "tribunal.esm", "bloodmoon.esm"}

    def on_tes3cmd_window(self):
        win = getattr(self, "_t3_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            return
        win = tk.Toplevel(self.root)
        self._t3_win = win
        win.title("tes3cmd")
        win.configure(bg=DARK["bg"])
        win.geometry("760x520")
        top = ttk.Frame(win, padding=10)
        top.pack(fill="both", expand=True)
        top.columnconfigure(1, weight=1)

        # tes3cmd binary path (auto-detected; MOMW Tools Pack ships tes3cmd.exe)
        ttk.Label(top, text=_("tes3cmd:")).grid(row=0, column=0, sticky="w")
        self._t3_path_var = tk.StringVar(value=self._tes3cmd_override or "")
        ent = ttk.Entry(top, textvariable=self._t3_path_var)
        ent.grid(row=0, column=1, sticky="ew", padx=6)
        add_tooltip(
            ent,
            "Path to tes3cmd. Leave empty to auto-detect (PATH, next to this app, "
            "next to openmw.cfg). End users normally have the compiled tes3cmd.exe "
            "from the MOMW Tools Pack; the pure-perl script works too if perl is "
            "installed.",
        )

        def _browse():
            p = filedialog.askopenfilename(
                title=_("Locate tes3cmd"),
                filetypes=(("tes3cmd", "tes3cmd*"), ("Executables", "*.exe"), ("All files", "*.*")),
            )
            if p:
                self._t3_path_var.set(p)

        ttk.Button(top, text=_("Browse..."), command=_browse).grid(row=0, column=2, padx=(0, 0))

        detected = core.find_tes3cmd(explicit=None, extra_dirs=[self._cfg_dir()])
        note = (
            f"auto-detected: {detected}"
            if detected
            else "not found -- install the MOMW Tools Pack or Browse to it"
        )
        ttk.Label(top, text=note, foreground=DARK["fg_dim"]).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(2, 6)
        )

        # plugin list
        lf = ttk.LabelFrame(top, text=_("Plugins to run on"))
        lf.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(4, 6))
        top.rowconfigure(2, weight=1)
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)
        self._t3_list = tk.Listbox(
            lf, selectmode="extended", exportselection=False, activestyle="dotbox"
        )
        style_plain_widget(self._t3_list)
        attach_typeahead(self._t3_list)
        self._t3_list.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sc = ttk.Scrollbar(lf, orient="vertical", command=self._t3_list.yview)
        sc.grid(row=0, column=1, sticky="ns", pady=8)
        self._t3_list.configure(yscrollcommand=sc.set)
        self._t3_files = []  # full paths, parallel to listbox rows

        btns = ttk.Frame(lf)
        btns.grid(row=0, column=2, sticky="n", padx=8, pady=8)
        b1 = ttk.Button(btns, text=_("My mods (last sort)"), command=self._t3_add_from_plan)
        b1.pack(fill="x", pady=2)
        add_tooltip(
            b1,
            "Add every custom mod from the last Sort whose file could be located "
            "(curated plugins are the list's job, not yours).",
        )
        b1b = ttk.Button(btns, text=_("MOMW needs-cleaning"), command=self._t3_add_needs_cleaning)
        b1b.pack(fill="x", pady=2)
        add_tooltip(
            b1b,
            "Add every ACTIVE plugin that plugin-order.yml flags as "
            "needs_cleaning (MOMW's own 'clean this one' list), resolved across "
            "the data folders. Requires the plugin-order.yml path on the main tab.",
        )
        b2 = ttk.Button(btns, text=_("Add file(s)..."), command=self._t3_add_files)
        b2.pack(fill="x", pady=2)
        b3 = ttk.Button(btns, text=_("Remove selected"), command=self._t3_remove_selected)
        b3.pack(fill="x", pady=2)
        b4 = ttk.Button(btns, text=_("Clear"), command=lambda: self._t3_set_files([]))
        b4.pack(fill="x", pady=2)

        # command choice
        cf = ttk.LabelFrame(top, text=_("Command"))
        cf.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        self._t3_cmd_var = tk.StringVar(value="sync")
        for i, (val, label) in enumerate(self.T3_COMMANDS):
            ttk.Radiobutton(cf, text=label, value=val, variable=self._t3_cmd_var).grid(
                row=i, column=0, sticky="w", padx=8, pady=1
            )
        xf = ttk.Frame(cf)
        xf.grid(row=len(self.T3_COMMANDS), column=0, sticky="ew", padx=8, pady=(4, 6))
        ttk.Label(xf, text=_("extra arguments:")).pack(side="left")
        self._t3_extra_var = tk.StringVar()
        xent = ttk.Entry(xf, textvariable=self._t3_extra_var, width=48)
        xent.pack(side="left", padx=6, fill="x", expand=True)
        add_tooltip(
            xent,
            "Optional extra tes3cmd switches, e.g. --instances for clean, "
            "--author/--description for header. Passed through verbatim.",
        )

        row = ttk.Frame(top)
        row.grid(row=4, column=0, columnspan=3, sticky="ew")
        self._t3_run_btn = ttk.Button(row, text=_("Run"), command=self._t3_run)
        self._t3_run_btn.pack(side="left")
        add_tooltip(
            self._t3_run_btn,
            "Output streams to the main Log. Modifying commands ask "
            "for confirmation first; tes3cmd makes its own backups.",
        )
        ttk.Button(row, text=_("Close"), command=win.destroy).pack(side="right")

    def _cfg_dir(self):
        c = self.cfg_var.get().strip()
        return str(Path(c).parent) if c else None

    def _t3_set_files(self, paths):
        self._t3_files = list(paths)
        self._t3_list.delete(0, "end")
        for p in self._t3_files:
            self._t3_list.insert("end", f"{Path(p).name}    ({Path(p).parent})")

    def _t3_remove_selected(self):
        trace_first_fire("tes3cmd Remove selected")
        before = len(self._t3_files)
        keep = [
            p for i, p in enumerate(self._t3_files) if i not in set(self._t3_list.curselection())
        ]
        core.trace(f"[smoke] tes3cmd Remove selected: {before} -> {len(keep)} file(s)")
        self._t3_set_files(keep)

    def _t3_add_from_plan(self):
        plan = self._current_plan or {}
        subset = plan.get("subset") or []
        if not subset:
            self.status_var.set(_("Run '1. Sort' first so I know which mods are yours."))
            return
        index = core.PluginFileIndex(self._plan_scan_dirs())
        found, missing = [], 0
        have = {str(p).lower() for p in self._t3_files}
        for n in subset:
            if str(n).lower().endswith(".omwscripts"):
                continue  # not a TES3 file; nothing for tes3cmd to do
            p = index.find(n)
            if p is None:
                missing += 1
            elif str(p).lower() not in have:
                found.append(str(p))
        self._t3_set_files(self._t3_files + found)
        msg = f"tes3cmd: added {len(found)} of your mods"
        if missing:
            msg += f" ({missing} file(s) not found in the data folders)"
        self.status_var.set(msg + ".")

    def _t3_add_needs_cleaning(self):
        """Add active plugins that MOMW's plugin-order.yml flags needs_cleaning."""
        yml = self.plugin_order_yml_var.get().strip()
        if not yml:
            self.status_var.set(_("Set the plugin-order.yml path on the main tab first."))
            return
        try:
            entries = core.parse_plugin_order_yml(Path(yml))
            nc = core.needs_cleaning_set(entries)
        except Exception as e:  # noqa: BLE001
            # untrusted plugin-order.yml; status line carries the failure
            self.status_var.set(f"Couldn't read plugin-order.yml: {e}")
            return
        plan = self._current_plan or {}
        active = plan.get("final_order") or plan.get("base_order_names") or []
        if not active:
            self.status_var.set(_("Run '1. Sort' first so I know the active load order."))
            return
        index = core.PluginFileIndex(self._plan_scan_dirs())
        have = {str(p).lower() for p in self._t3_files}
        found, unfound = [], 0
        for n in active:
            if n.lower() not in nc or n.lower() in self.T3_NEVER_CLEAN:
                continue
            p = index.find(n)
            if p is None:
                unfound += 1
            elif str(p).lower() not in have:
                found.append(str(p))
        self._t3_set_files(self._t3_files + found)
        msg = f"tes3cmd: added {len(found)} needs-cleaning plugin(s)"
        if unfound:
            msg += f" ({unfound} not found in the data folders)"
        self.status_var.set(msg + ".")

    def _t3_add_files(self):
        ps = filedialog.askopenfilenames(
            title=_("Choose plugin file(s)"),
            filetypes=(("TES3 plugins", "*.esp *.esm *.omwaddon *.omwgame"), ("All files", "*.*")),
        )
        if ps:
            have = {str(p).lower() for p in self._t3_files}
            self._t3_set_files(self._t3_files + [p for p in ps if str(p).lower() not in have])

    def _t3_run(self):
        if self.worker_running:
            return
        cmd = self._t3_cmd_var.get()
        extra = self._t3_extra_var.get().split()
        files = list(self._t3_files)

        # "sync" runs entirely in-app (VFS-aware) -- tes3cmd not needed, and
        # its own --synchronize writes EMPTY master sizes when the masters
        # aren't in the plugin's folder (OpenMW multi-folder layouts).
        if cmd == "sync":
            if not files:
                messagebox.showinfo(
                    _("tes3cmd"), _("Add at least one plugin file first."), parent=self._t3_win
                )
                return
            if not messagebox.askyesno(
                _("Resync master sizes"),
                f"Rewrite the recorded master sizes in {len(files)} plugin file(s) to "
                f"match the installed masters?\n\nOnly the 8-byte size fields change; a "
                f"one-time .masterfix.bak copy of each file is kept.",
                parent=self._t3_win,
            ):
                return
            self.worker_running = True
            self._t3_run_btn.configure(state="disabled")
            self.sort_button.configure(state="disabled")
            self.status_var.set(_("Resyncing master sizes..."))
            threading.Thread(target=self._t3_sync_worker, args=(files,), daemon=True).start()
            return

        # A manually-entered path must WIN or fail loudly -- never silently
        # fall back to some other tes3cmd found on the system.
        explicit = self._t3_path_var.get().strip() or None
        if explicit:
            if not Path(explicit).is_file():
                messagebox.showerror(
                    _("tes3cmd"),
                    f"'{explicit}' does not exist. Fix the path " f"or clear it to auto-detect.",
                    parent=self._t3_win,
                )
                return
            exe = explicit
        else:
            exe = core.find_tes3cmd(extra_dirs=[self._cfg_dir()])
        if not exe:
            messagebox.showerror(
                _("tes3cmd"),
                _("tes3cmd not found. Browse to the compiled " "tes3cmd.exe (MOMW Tools Pack)."),
                parent=self._t3_win,
            )
            return
        argv, err = core.tes3cmd_invocation(exe)
        if err:
            messagebox.showerror(_("tes3cmd"), err, parent=self._t3_win)
            return
        self._tes3cmd_override = explicit  # persist a manual path in settings
        if not files:
            messagebox.showinfo(
                _("tes3cmd"), _("Add at least one plugin file first."), parent=self._t3_win
            )
            return
        if cmd == "clean":
            guarded = [f for f in files if Path(f).name.lower() in self.T3_NEVER_CLEAN]
            if guarded:
                files = [f for f in files if Path(f).name.lower() not in self.T3_NEVER_CLEAN]
                messagebox.showwarning(
                    _("tes3cmd"),
                    "Skipping the vanilla master(s):\n\n  "
                    + "\n  ".join(Path(f).name for f in guarded)
                    + "\n\nMorrowind.esm, Tribunal.esm and Bloodmoon.esm are never cleaned -- "
                    "even a careful GMST-preserving clean rewrites bytes that other content "
                    "depends on and causes in-game failures.",
                    parent=self._t3_win,
                )
                if not files:
                    return
            # masters before dependents: cleaning changes the master a
            # dependent is compared against, so sequence by the sorted load
            # order (fallback: masters first, then name)
            order = {
                n.lower(): i
                for i, n in enumerate((self._current_plan or {}).get("final_order") or [])
            }
            files.sort(
                key=lambda f: (
                    order.get(Path(f).name.lower(), 1 << 30),
                    not Path(f).name.lower().endswith((".esm", ".omwgame")),
                    Path(f).name.lower(),
                )
            )
            if not messagebox.askyesno(
                _("tes3cmd clean"),
                f"Clean {len(files)} plugin file(s)?\n\nEach is staged into a private "
                f"'Data Files' with its masters so tes3cmd sees the full VFS; plugins "
                f"whose masters can't be found are skipped. A one-time .preclean.bak "
                f"copy of each modified file is kept.",
                parent=self._t3_win,
            ):
                return
        self.worker_running = True
        self._t3_run_btn.configure(state="disabled")
        self.sort_button.configure(state="disabled")
        self.status_var.set(f"tes3cmd {cmd} running...")
        threading.Thread(
            target=self._t3_worker, args=(argv, cmd, extra, files), daemon=True
        ).start()

    def _t3_staging_dir(self):
        """Persistent staging root (next to the app, like the tes3conv dump):
        hardlinked/copied masters are reused across runs, so staging a plugin
        costs almost nothing after the first time."""
        return app_base_dir() / "tes3cmd_staging"

    def _t3_worker(self, argv, cmd, extra, files):
        import subprocess

        writer = QueueWriter(self.log_queue)
        ok = fail = skipped = changed = 0
        # clean: --replace makes tes3cmd overwrite its input (we work on a
        # staged COPY and copy back ourselves); --hide-backups keeps its own
        # backup clutter inside the disposable staging dir
        sub = {"clean": ["clean", "--replace", "--hide-backups"], "header": ["header"]}[cmd]

        def _run_t3(args, cwd):
            env = dict(os.environ)
            env["PWD"] = str(cwd)  # tes3cmd trusts $PWD over getcwd when set
            # check=False: the caller inspects returncode itself and reports it
            # in the log rather than raising.
            return subprocess.run(
                args,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=900,
                cwd=str(cwd),
                env=env,
                check=False,
                **core._no_window_kwargs(),
            )

        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                print("\n" + "=" * 70)
                print(
                    f" TES3CMD {' '.join(sub).upper()}"
                    + (" (staged, VFS-aware)" if cmd == "clean" else "")
                )
                print("=" * 70)
                print(f"  Engine: {' '.join(argv)}")
                index = None
                staging = None
                if cmd == "clean":
                    dirs = self._plan_scan_dirs()
                    dirs += [str(Path(f).parent) for f in files]
                    index = core.PluginFileIndex(list(dict.fromkeys(dirs)))
                    staging = self._t3_staging_dir()
                    print(f"  Staging dir: {staging}")
                for f in files:
                    name = Path(f).name
                    print(f"\n--- {' '.join(sub)}: {f}")
                    try:
                        if cmd == "header":
                            r = _run_t3(argv + sub + extra + [name], Path(f).parent)
                            print(
                                ((r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")).strip()
                                or "(no output)"
                            )
                            ok += 1 if r.returncode == 0 else 0
                            fail += 0 if r.returncode == 0 else 1
                            continue
                        # clean: stage plugin + masters, run there, copy back
                        staged, missing = core.stage_for_tes3cmd(staging, f, index)
                        if missing:
                            skipped += 1
                            print(
                                f"  SKIPPED: master(s) not found in any data folder: "
                                f"{', '.join(missing)} -- cleaning without the masters "
                                f"present gives wrong results."
                            )
                            continue
                        before = staged.read_bytes()
                        r = _run_t3(argv + sub + extra + [staged.name], staged.parent)
                        print(
                            ((r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")).strip()
                            or "(no output)"
                        )
                        if r.returncode != 0:
                            fail += 1
                            print(f"  (exit code {r.returncode}) -- original NOT touched")
                            continue
                        after = staged.read_bytes()
                        if after == before:
                            ok += 1
                            print(_("  no changes -- already clean"))
                            continue
                        # copy the cleaned result back over the original,
                        # keeping a one-time backup of the original
                        import shutil as _sh

                        bak = Path(f).with_name(name + ".preclean.bak")
                        if not bak.exists():
                            _sh.copy2(f, bak)
                        Path(f).write_bytes(after)
                        ok += 1
                        changed += 1
                        print(
                            f"  cleaned: {len(before)} -> {len(after)} bytes "
                            f"(backup: {bak.name})"
                        )
                    except Exception as e:  # noqa: BLE001
                        # per-file isolation: one unclean plugin must not abort the batch
                        fail += 1
                        print(f"  ERROR: {e}")
            if cmd == "clean":
                status = (
                    f"tes3cmd clean: {changed} cleaned, {ok - changed} already clean, "
                    f"{skipped} skipped (missing masters), {fail} failed."
                )
                if changed:
                    status += "  Re-run '1. Sort' to refresh checks."
            else:
                status = f"tes3cmd {cmd}: {ok} ok, {fail} failed. See the log."
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: tes3cmd run failed:\n" + traceback.format_exc())
            status = "tes3cmd run failed -- see log."
        finally:
            self.root.after(0, self._t3_finished, status)

    def _t3_sync_worker(self, files):
        """In-app master-size resync (see core.sync_plugin_master_sizes)."""
        writer = QueueWriter(self.log_queue)
        ok = fixed = fail = 0
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                print("\n" + "=" * 70)
                print(_(" MASTER-SIZE RESYNC (in-app, VFS-aware)"))
                print("=" * 70)
                dirs = self._plan_scan_dirs()
                extra = [str(Path(f).parent) for f in files]
                index = core.PluginFileIndex(list(dict.fromkeys(dirs + extra)))
                for f in files:
                    name = Path(f).name
                    updated, unresolved, err = core.sync_plugin_master_sizes(f, index)
                    if err:
                        fail += 1
                        print(f"  {name}: ERROR: {err}")
                        continue
                    ok += 1
                    if updated:
                        fixed += 1
                        for m, old, new in updated:
                            print(f"  {name}: '{m}' {old} -> {new}")
                    else:
                        print(f"  {name}: already in sync")
                    for m in unresolved:
                        print(
                            f"  {name}: WARNING: master '{m}' not found in any data "
                            f"folder -- its size field left untouched"
                        )
            status = (
                f"Resync: {fixed} plugin(s) updated, {ok - fixed} already in sync, "
                f"{fail} error(s)."
            )
            if fixed:
                status += "  Re-run '1. Sort' to refresh the master check."
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: resync failed:\n" + traceback.format_exc())
            status = "Resync failed -- see log."
        finally:
            self.root.after(0, self._t3_finished, status)

    def _t3_finished(self, status):
        self.worker_running = False
        self.sort_button.configure(state="normal")
        try:
            if self._t3_win and self._t3_win.winfo_exists():
                self._t3_run_btn.configure(state="normal")
        except tk.TclError:  # the tes3cmd window may already be gone
            pass
        self.status_var.set(status)

    def _show_resource_window(self, conflicts, stats):
        self._all_res = conflicts
        win = getattr(self, "_res_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()
        win = tk.Toplevel(self.root)
        self._res_win = win
        win.title("Data-path Resource Conflicts")
        win.configure(bg=DARK["bg"])
        win.geometry("900x560")
        top = ttk.Frame(win, padding=8)
        top.pack(fill="x")
        n_sub = sum(1 for c in conflicts if c.get("involves_subset"))
        ttk.Label(
            top,
            text=(
                f"{stats.get('conflicts', 0)} loose-file conflict(s) across "
                f"{stats.get('dirs', 0)} folder(s), {stats.get('files', 0)} file(s) — "
                f"{n_sub} involve your custom data paths (★). Later folder wins — reorder "
                f"the data-path panel to change it."
            ),
        ).pack(side="left")
        self._res_subset_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top,
            text=_("Only my paths"),
            variable=self._res_subset_only,
            command=self._refill_res_tree,
        ).pack(side="right")
        # tree (top) and the detail panel (bottom) live in a draggable vertical
        # split, so the detail box can be resized -- grab the grip to grow it.
        body = self._paned(win, "vertical")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        mid = ttk.Frame(body)
        cols = ("custom", "path", "count", "winner")
        tree = ttk.Treeview(
            mid, columns=cols, show="headings", selectmode="browse", style="Conf.Treeview"
        )
        for c, txt, w in (
            ("custom", "★", 34),
            ("path", "File", 520),
            ("count", "#", 50),
            ("winner", "Winner (loads last)", 280),
        ):
            tree.heading(c, text=txt)
            tree.column(c, width=w, anchor="w", stretch=(c in ("path", "winner")))
        vsb = ttk.Scrollbar(mid, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        tree.tag_configure("sub", foreground="#ff9b6b")
        self._res_tree = tree
        body.add(mid, minsize=120, stretch="always")

        detbox = ttk.Frame(body)
        detail = tk.Text(
            detbox,
            height=5,
            wrap="word",
            background=DARK["log_bg"],
            foreground=DARK["fg"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=DARK["border"],
        )
        detail.pack(fill="both", expand=True)
        detail.insert("1.0", "Select a file to see every folder that provides it, in load order.")
        detail.configure(state="disabled")
        body.add(detbox, minsize=70)
        self._attach_hamburger_grip(body, "vertical")

        def on_sel(_e=None):
            sel = tree.selection()
            if not sel:
                return
            c = self._res_shown[int(sel[0])]
            txt = (
                f"{c['path']}\n"
                + "\n".join(f"  {i + 1}. {p}" for i, p in enumerate(c["providers"]))
                + f"\nWins: {c['winner']}"
            )
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            detail.insert("1.0", txt)
            detail.configure(state="disabled")

        tree.bind("<<TreeviewSelect>>", on_sel)
        btns = ttk.Frame(win, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text=_("Save report (CSV)..."), command=self._save_resource_csv).pack(
            side="left"
        )
        ttk.Button(btns, text=_("Close"), command=win.destroy).pack(side="right")
        self._refill_res_tree()

    def _refill_res_tree(self):
        tree = getattr(self, "_res_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        only = self._res_subset_only.get()
        self._res_shown = [c for c in self._all_res if c.get("involves_subset") or not only]
        tree.delete(*tree.get_children())
        for i, c in enumerate(self._res_shown):
            star = "★" if c["involves_subset"] else ""
            tags = ("sub",) if c["involves_subset"] else ()
            tree.insert(
                "",
                "end",
                iid=str(i),
                tags=tags,
                values=(star, c["path"], len(c["providers"]), c["winner"]),
            )

    def _save_resource_csv(self):
        if not getattr(self, "_all_res", None):
            return
        path = filedialog.asksaveasfilename(
            title=_("Save resource conflicts"),
            defaultextension=".csv",
            initialfile="resource_conflicts.csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            core.write_resource_csv(path, self._all_res)
            self.status_var.set(f"Saved: {path}")
        except OSError as e:
            messagebox.showerror(_("Save failed"), str(e))

    def _show_conflict_window(self, conflicts, stats):
        self._all_conflicts = conflicts
        win = getattr(self, "_conflict_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()
        win = tk.Toplevel(self.root)
        self._conflict_win = win
        win.title("TES3 Record Conflicts")
        win.configure(bg=DARK["bg"])
        win.geometry("980x680")

        top = ttk.Frame(win, padding=8)
        top.pack(fill="x")
        n_sub = sum(1 for c in conflicts if c.get("involves_subset"))
        ttk.Label(
            top,
            text=(
                f"{stats.get('conflicts', 0)} conflicting record(s) across "
                f"{stats.get('scanned', 0)} plugin(s) — {n_sub} involve your custom mods "
                f"(★). Winner = last loaded."
            ),
        ).pack(side="left")
        self._conf_subset_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top,
            text=_("Only my mods"),
            variable=self._conf_subset_only,
            command=self._refill_conflict_tree,
        ).pack(side="right")

        engine = (stats or {}).get("engine", "builtin")
        bar = ttk.Frame(win, padding=(8, 0))
        bar.pack(fill="x")
        ttk.Label(
            bar,
            foreground=(DARK["fg_dim"] if engine == "tes3conv" else "#ffb454"),
            text=(
                "Field-level diffs: ON (tes3conv)."
                if engine == "tes3conv"
                else "Field-level diffs: OFF — record-level only. Set a tes3conv binary, then re-check."
            ),
        ).pack(side="left")
        ttk.Button(bar, text=_("Set tes3conv..."), command=self._set_tes3conv).pack(
            side="left", padx=(8, 0)
        )

        panes = tk.PanedWindow(
            win,
            orient="vertical",
            bg=DARK["bg"],
            bd=0,
            sashwidth=6,
            sashrelief="flat",
            background=DARK["border"],
        )
        panes.pack(fill="both", expand=True, padx=8, pady=6)

        # --- conflicts table ---
        topf = ttk.Frame(panes)
        cols = ("custom", "type", "id", "count", "winner")
        tree = ttk.Treeview(
            topf, columns=cols, show="headings", selectmode="browse", style="Conf.Treeview"
        )
        for c, txt, w in (
            ("custom", "★", 34),
            ("type", "Type", 90),
            ("id", "Record", 380),
            ("count", "#", 40),
            ("winner", "Winner (loads last)", 280),
        ):
            tree.heading(c, text=txt)
            tree.column(c, width=w, anchor="w", stretch=(c in ("id", "winner")))
        vsb = ttk.Scrollbar(topf, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        topf.rowconfigure(0, weight=1)
        topf.columnconfigure(0, weight=1)
        tree.tag_configure("sub", foreground="#ff9b6b")
        self._conf_tree = tree
        panes.add(topf, minsize=150, stretch="always")

        # --- field-level comparison (populated on record select) ---
        botf = ttk.Frame(panes)
        ttk.Label(
            botf,
            foreground=DARK["fg_dim"],
            text="Field comparison for the selected record — differing fields in red · "
            "★ = your custom mod · last column wins · double-click a field for the full "
            "value:",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(2, 2))
        ftree = ttk.Treeview(botf, show="headings", selectmode="browse", style="Conf.Treeview")
        fvsb = ttk.Scrollbar(botf, orient="vertical", command=ftree.yview)
        fhsb = ttk.Scrollbar(botf, orient="horizontal", command=ftree.xview)
        ftree.configure(yscrollcommand=fvsb.set, xscrollcommand=fhsb.set)
        ftree.grid(row=1, column=0, sticky="nsew")
        fvsb.grid(row=1, column=1, sticky="ns")
        fhsb.grid(row=2, column=0, sticky="ew")
        botf.rowconfigure(1, weight=1)
        botf.columnconfigure(0, weight=1)
        ftree.tag_configure("diff", foreground="#ff6b6b")
        ftree.bind("<Double-Button-1>", lambda _e: self._show_field_detail())
        add_tooltip(
            ftree,
            "Field-by-field diff of the selected record. Red = the plugins disagree; "
            "the last one in the load order wins.\n\n"
            "Double-click any row for the full value, one tab per plugin. Two fields "
            "are decoded rather than shown raw:\n"
            "  \u2022 bytecode -- disassembled to named script instructions, so a script "
            "edit reads as a change instead of a wall of base64. Spans the disassembler "
            "cannot decode are printed as offset/hex/ASCII rather than guessed at, and a "
            "'decoded: N%' header says how much was understood.\n"
            "  \u2022 variables -- the script's local variable names, in declaration order.",
        )
        self._conf_ftree = ftree
        panes.add(botf, minsize=120, stretch="always")

        tree.bind("<<TreeviewSelect>>", lambda _e: self._on_conflict_select())

        btns = ttk.Frame(win, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text=_("Save report (CSV)..."), command=self._save_conflicts_csv).pack(
            side="left"
        )
        if self._conf_session is not None:
            ttk.Button(
                btns, text=_("Dump tes3conv JSON..."), command=self._dump_conflict_json
            ).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text=_("Close"), command=win.destroy).pack(side="right")

        self._refill_conflict_tree()

    def _dump_conflict_json(self):
        """Write the (in-memory) tes3conv JSON for every scanned plugin to a
        folder you pick."""
        if self._conf_session is None or not self._conf_paths:
            return
        folder = filedialog.askdirectory(title=_("Dump tes3conv JSON to folder"))
        if not folder:
            return
        try:
            n = core.dump_tes3conv_json(
                self._conf_session, list(self._conf_paths.keys()), self._conf_paths, folder
            )
            self.status_var.set(f"Wrote {n} JSON file(s) to {folder}")
            if n:
                messagebox.showinfo(
                    _("JSON dumped"), f"Wrote {n} tes3conv JSON file(s) to:\n{folder}"
                )
            else:
                messagebox.showwarning(
                    _("Nothing written"),
                    "No JSON was written. The tes3conv session may have been cleared — "
                    "re-run Check Conflicts, then dump again.",
                )
        except Exception as e:  # noqa: BLE001
            # user-facing dump; any failure becomes an error dialog
            messagebox.showerror(_("Dump failed"), str(e))

    def _on_conflict_select(self):
        tree = getattr(self, "_conf_tree", None)
        sel = tree.selection() if tree else None
        if not sel:
            return
        self._populate_field_diff(self._shown_conflicts[int(sel[0])])

    def _is_custom(self, plugin):
        """True if this plugin is one of YOUR custom mods (in the scanned subset),
        as opposed to a curated-list plugin."""
        return str(plugin).lower() in getattr(self, "_conf_subset_lower", set())

    @staticmethod
    def _fmt_val(v):
        if v is None:
            return "—"
        if isinstance(v, list):
            # short lists of scalars (e.g. a grid [x, y]) show inline; long ones
            # or lists of objects (e.g. references) get a count + hint to expand
            if all(not isinstance(x, (dict, list)) for x in v):
                s = str(v)
                if len(s) <= 140:
                    return s
            return f"[{len(v)} item(s)]  — double-click to view"
        s = str(v)
        return s if len(s) <= 140 else s[:137] + "…  (double-click)"

    def _populate_field_diff(self, conflict):
        ftree = getattr(self, "_conf_ftree", None)
        if ftree is None or not ftree.winfo_exists():
            return
        plugins = conflict["plugins"]
        cols = ["field"] + [f"p{i}" for i in range(len(plugins))]
        ftree.configure(columns=cols)
        ftree.heading("field", text="Field")
        ftree.column("field", width=240, anchor="w", stretch=False)
        for i, p in enumerate(plugins):
            star = "★ " if self._is_custom(p) else ""  # ★ marks your custom mods
            suffix = "  (wins)" if i == len(plugins) - 1 else ""
            ftree.heading(f"p{i}", text=f"{star}{p}{suffix}")
            ftree.column(f"p{i}", width=210, anchor="w", stretch=True)
        ftree.delete(*ftree.get_children())
        if self._conf_session is None:
            ftree.insert(
                "",
                "end",
                values=["(set a tes3conv binary for field-level diffs)"] + [""] * len(plugins),
            )
            return
        try:
            keys, per, diff = core.diff_record_fields(
                self._conf_session, conflict, self._conf_paths
            )
        except Exception:  # noqa: BLE001
            # field diff is best-effort; degrades to '(field diff unavailable)'
            ftree.insert("", "end", values=["(field diff unavailable)"] + [""] * len(plugins))
            return
        self._conf_fdiff = {"plugins": plugins, "per": per}  # for the expand popup
        for k in keys:
            row = [k] + [self._fmt_val(per[p].get(k)) for p in plugins]
            ftree.insert("", "end", iid=k, values=row, tags=("diff",) if k in diff else ())
        if not keys:
            ftree.insert("", "end", values=["(no fields / identical)"] + [""] * len(plugins))

    @staticmethod
    def _disassemble_bytecode_field(value, source_text=None):
        """Disassembly text for a tes3conv 'bytecode' field, or None.

        Thin delegation: the logic lives in mlox_subset.mwscript so it can be
        unit-tested without a display. Returns None only when the package is
        unavailable, in which case the caller shows the raw value as before.
        """
        if listing_for_bytecode_field is None:
            return None
        return listing_for_bytecode_field(value, source_text)

    def _show_field_detail(self):
        """Popup with the full value of the selected field for each plugin --
        one tab per plugin, pretty-printed with JSON syntax highlighting (and,
        for text fields like book/dialogue content, the embedded HTML-ish
        markup broken out too). Uses whatever theme is picked next to the Log
        panel, so the two stay in sync. For long fields like 'references'
        that get truncated in the table."""
        ftree = getattr(self, "_conf_ftree", None)
        fd = getattr(self, "_conf_fdiff", None)
        if not ftree or not fd:
            return
        sel = ftree.selection()
        if not sel:
            return
        key = sel[0]
        plugins = fd["plugins"]
        per = fd["per"]
        theme = self._resolve_theme(self.log_theme_var.get()) or THEME_PRESETS["Dark (default)"]
        json_colors = _json_syntax_colors(theme)
        win = tk.Toplevel(self.root)
        win.title(f"Field: {key}")
        win.configure(bg=DARK["bg"])
        win.geometry("820x520")
        note = "last plugin wins · ★ orange = your custom mod"
        if key == "bytecode" and listing_for_bytecode_field is not None:
            note += " · shown disassembled; undecoded spans are printed as hex"
        elif key == "variables" and variables_text_for_field is not None:
            note += " · decoded to local variable names"
        ttk.Label(win, text=f"{key}   ({note})", padding=8).pack(anchor="w")
        bar = ttk.Frame(win, padding=(8, 0))
        bar.pack(fill="x")
        wrap_var = tk.BooleanVar(value=True)
        texts = []

        def _apply_wrap():
            w = "word" if wrap_var.get() else "none"
            for st in texts:
                st.configure(state="normal")
                st.configure(wrap=w)
                st.configure(state="disabled")

        ttk.Checkbutton(bar, text=_("Word wrap"), variable=wrap_var, command=_apply_wrap).pack(
            side="left"
        )
        ttk.Label(
            bar, text=f"Syntax highlighting: {self.log_theme_var.get()}", foreground=DARK["fg_dim"]
        ).pack(side="right")
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        custom_fg = "#ff9b6b"
        for i, p in enumerate(plugins):
            cust = self._is_custom(p)
            val = per[p].get(key, None)
            # A plain string field (book/dialogue text, mesh/icon/script
            # paths, ids, ...) is shown as its own raw content, not run
            # through json.dumps -- dumping it would wrap it in quotes and
            # JSON-escape every embedded " and \ (Morrowind book text is
            # full of pseudo-HTML like <FONT COLOR="000000">, which
            # json.dumps turns into <FONT COLOR=\"000000\">, all noise and
            # no benefit since nothing here gets re-parsed as JSON). Only
            # structured values (list/dict/number/etc.) still need JSON's
            # own formatting, so those still go through json.dumps.
            is_plain_string = isinstance(val, str)
            # A compiled-script field is base64, so showing it verbatim makes
            # every script edit look like a total rewrite. Disassemble instead.
            is_listing = False
            listing = None
            if is_plain_string and key == "bytecode":
                listing = self._disassemble_bytecode_field(val, per[p].get("text"))
            elif is_plain_string and key == "variables" and variables_text_for_field:
                # Same base64+zstd wrapping as bytecode; shown as names so the
                # diff says WHICH locals changed, not just that the blob did.
                listing = variables_text_for_field(val)
            if listing is not None:
                text, is_listing, is_plain_string = listing, True, False
            elif is_plain_string:
                text = val
            else:
                try:
                    text = json.dumps(val, indent=2, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    # default=str handles most; circular refs raise ValueError
                    text = repr(val)
            frame = ttk.Frame(nb)
            # colored per-plugin header inside the tab: orange = your custom mod
            ttk.Label(
                frame,
                text=(
                    ("★ " if cust else "")
                    + p
                    + ("   — your custom mod" if cust else "   — curated list")
                    + ("   ✓ wins" if i == len(plugins) - 1 else "")
                ),
                foreground=(custom_fg if cust else DARK["fg_dim"]),
                padding=(4, 4),
            ).pack(anchor="w")
            st = scrolledtext.ScrolledText(
                frame,
                wrap="word",
                font=("TkFixedFont", 10),
                background=theme["background"],
                foreground=theme["foreground"],
                insertbackground=theme["foreground"],
                selectbackground=theme["select"],
                relief="flat",
                highlightthickness=1,
                highlightbackground=DARK["border"],
            )
            st.pack(fill="both", expand=True)
            style_json_syntax_tags(st, json_colors)
            shown = text if val is not None else "(field not present in this plugin)"
            st.insert("1.0", shown)
            if val is not None and not is_listing:
                try:
                    if is_plain_string:
                        highlight_plain_text_with_html(st, text, json_colors)
                    else:
                        highlight_json_with_html(st, text, json_colors)
                except Exception:  # noqa: BLE001
                    # highlighting is cosmetic -- never let it block showing the value
                    pass  # highlighting is cosmetic -- never let it block showing the value
            st.configure(state="disabled")
            texts.append(st)
            label = (p[:22] + "…") if len(p) > 24 else p
            tab = ("★ " if cust else "") + label + (" ✓" if i == len(plugins) - 1 else "")
            nb.add(frame, text=tab)
        ttk.Button(win, text=_("Close"), command=win.destroy).pack(pady=(0, 8))

    def _set_tes3conv(self):
        p = filedialog.askopenfilename(
            title=_("Locate the tes3conv executable"), filetypes=(("All files", "*.*"),)
        )
        if not p:
            return
        self._tes3conv_override = p
        self.status_var.set(
            _("tes3conv set — click 'Check Conflicts' again to re-scan with field diffs.")
        )
        messagebox.showinfo(
            _("tes3conv set"),
            "tes3conv location saved.\n\nClick 'Check Conflicts' again to re-scan; the "
            "field comparison will then populate when you select a record.",
        )

    def _refill_conflict_tree(self):
        tree = getattr(self, "_conf_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        only = self._conf_subset_only.get()
        self._shown_conflicts = [
            c for c in self._all_conflicts if c.get("involves_subset") or not only
        ]
        tree.delete(*tree.get_children())
        for i, c in enumerate(self._shown_conflicts):
            star = "★" if c["involves_subset"] else ""
            tags = ("sub",) if c["involves_subset"] else ()
            tree.insert(
                "",
                "end",
                iid=str(i),
                tags=tags,
                values=(star, c["type"], c["id"], len(c["plugins"]), c["winner"]),
            )

    def _save_conflicts_csv(self):
        if not getattr(self, "_all_conflicts", None):
            return
        path = filedialog.asksaveasfilename(
            title=_("Save conflict report"),
            defaultextension=".csv",
            initialfile="tes3_conflicts.csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            core.write_conflict_csv(path, self._all_conflicts)
            self.status_var.set(f"Conflict report saved: {path}")
        except OSError as e:
            messagebox.showerror(_("Save failed"), str(e))


def _run_pywebview_window(path):
    """Open one cell-map file in an OS webview and block until closed. Invoked in a
    child process (see _open_cell_map_pywebview) so webview.start() owns its own
    main thread, cleanly, without disturbing the tkinter app. Always writes its
    outcome to cell_map_viewer.log next to the app so a failed backend (e.g.
    pywebview's WebView2/pythonnet backend on an unsupported Python) is visible
    instead of silently falling back to the browser."""
    try:
        logf = str(app_base_dir() / "cell_map_viewer.log")
    except (OSError, RuntimeError):  # app_base_dir may fall back to Path.home()
        logf = None

    def _log(msg):
        if not logf:
            return
        try:
            from datetime import datetime as _dt

            with Path(logf).open("a", encoding="utf-8") as fh:
                fh.write(f"{_dt.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
        except OSError:  # appending to the viewer log
            pass

    try:
        import webview

        _log(f"pywebview {getattr(webview, '__version__', '?')}: opening {path}")
        webview.create_window("Cell Map", Path(path).resolve().as_uri(), width=1050, height=760)
        webview.start()
        _log("pywebview: window closed cleanly")
    except Exception:  # noqa: BLE001
        # child-process main: logs the traceback, then falls back to the browser
        import traceback as _tb

        _log("pywebview FAILED -- falling back to browser:\n" + _tb.format_exc())
        try:
            webbrowser.open(Path(path).resolve().as_uri())
        except (OSError, ValueError, webbrowser.Error):  # as_uri on a relative path / no browser
            pass


def main():
    # Re-entry used by the pywebview viewer child process. Works whether we're run
    # from source (python gui.py --show-map X) or frozen (App.exe --show-map X),
    # because it never spawns "python -c" (a frozen exe is not a Python interpreter).
    if len(sys.argv) >= 3 and sys.argv[1] == "--show-map":
        _run_pywebview_window(sys.argv[2])
        return
    import argparse

    ap = argparse.ArgumentParser(description="MLOX Subset Sort (GUI)")
    ap.add_argument(
        "--trace",
        nargs="?",
        const=True,
        default=None,
        metavar="LOGFILE",
        help="Write a debug trace log for troubleshooting. Off by default. "
        "Pass --trace for the default log (mlox_subset_sort_trace.log next "
        "to the app), or --trace PATH to choose the file.",
    )
    args, _unknown = ap.parse_known_args()
    global _TRACE_REQUEST
    _TRACE_REQUEST = args.trace
    root = TkinterDnD.Tk() if HAVE_DND else tk.Tk()
    # theming happens inside App.__init__ (not here), because the saved theme
    # name has to be loaded first so the chrome comes up already themed
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
