"""Theming: chrome palette, syntax themes, live restyling, HTML highlighting.

Moved verbatim from ``mlox_subset_sort_gui.py`` (see the package docstring).
``DARK`` is the *active* chrome palette mutated in place by
:func:`set_active_chrome`; every ``DARK[...]`` read site follows the selected
theme through it.
"""

from __future__ import annotations

import re
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk

from mlox_subset.tracing import trace

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


def _select_color_capable_theme(style: ttk.Style) -> str:
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
                trace(f"[theme] ttk base theme: using {name!r}")
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
    trace(
        f"[theme] WARNING: no colour-capable ttk theme available "
        f"(have {sorted(available)}); staying on {active!r} -- ttk widget "
        f"colours will NOT apply. If this is a frozen .exe, the Tcl/tk ttk "
        f"theme files were not bundled."
    )
    return active


def apply_dark_theme(root: tk.Tk) -> ttk.Style:
    """Apply the active chrome palette to every ttk widget class on ``root``.

    Returns:
        The configured :class:`ttk.Style`, for callers that keep a handle.
    """
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


def style_plain_widget(widget: tk.Misc, chrome: dict | None = None) -> None:
    """Colour a non-ttk widget that ttk theming cannot reach.

    For tk.Listbox, scrolledtext.ScrolledText and friends. Applied
    option-by-option since the exact set of
    supported options differs between Listbox and Text (e.g. Listbox has no
    insertbackground). Reads the *active* chrome palette (``DARK``) at call
    time unless an explicit chrome mapping is passed, so it serves both
    construction and the runtime re-apply walk.
    """
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
            pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal


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
    # -- added in the 3.0 theme-pack pass. Palettes follow each scheme's
    # published colours; chrome uses the scheme's own UI slots where the
    # upstream defines them, otherwise sensible neighbours from the palette.
    # NOT added, because the picker already covers them: "Dracula Official"
    # (present as "Dracula") and "One Dark Pro" (the One Dark palette,
    # present as "Atom One Dark"). "Monokai Pro" IS distinct from classic
    # Monokai (different background and accent set), so it is here.
    "Monokai Pro": {
        "background": "#2d2a2e",
        "foreground": "#fcfcfa",
        "select": "#403e41",
        "section": "#78dce8",
        "warn": "#ffd866",
        "error": "#ff6188",
        "ok": "#a9dc76",
        "inserted": "#78dce8",
        "dim": "#727072",
        "key": "#78dce8",
        "string": "#ffd866",
        "number": "#ab9df2",
        "keyword": "#ff6188",
        "punct": "#727072",
        "tag": "#ff6188",
        "attr": "#a9dc76",
        "chrome": {
            "bg": "#2d2a2e",
            "bg2": "#221f22",
            "field_bg": "#403e41",
            "border": "#5b595c",
            "fg": "#fcfcfa",
            "fg_dim": "#727072",
            "select": "#403e41",
            "btn_bg": "#403e41",
            "btn_bg_active": "#5b595c",
            "accent": "#ffd866",
        },
    },
    "Tokyo Night": {
        "background": "#1a1b26",
        "foreground": "#c0caf5",
        "select": "#33467c",
        "section": "#7aa2f7",
        "warn": "#e0af68",
        "error": "#f7768e",
        "ok": "#9ece6a",
        "inserted": "#73daca",
        "dim": "#565f89",
        "key": "#7dcfff",
        "string": "#9ece6a",
        "number": "#ff9e64",
        "keyword": "#bb9af7",
        "punct": "#565f89",
        "tag": "#f7768e",
        "attr": "#e0af68",
        "chrome": {
            "bg": "#1a1b26",
            "bg2": "#16161e",
            "field_bg": "#24283b",
            "border": "#3b4261",
            "fg": "#c0caf5",
            "fg_dim": "#565f89",
            "select": "#33467c",
            "btn_bg": "#3b4261",
            "btn_bg_active": "#565f89",
            "accent": "#7aa2f7",
        },
    },
    "Night Owl": {
        "background": "#011627",
        "foreground": "#d6deeb",
        "select": "#1d3b53",
        "section": "#82aaff",
        "warn": "#ecc48d",
        "error": "#ef5350",
        "ok": "#addb67",
        "inserted": "#7fdbca",
        "dim": "#637777",
        "key": "#82aaff",
        "string": "#ecc48d",
        "number": "#f78c6c",
        "keyword": "#c792ea",
        "punct": "#637777",
        "tag": "#caece6",
        "attr": "#f78c6c",
        "chrome": {
            "bg": "#011627",
            "bg2": "#010e1a",
            "field_bg": "#0b2942",
            "border": "#122d42",
            "fg": "#d6deeb",
            "fg_dim": "#637777",
            "select": "#1d3b53",
            "btn_bg": "#122d42",
            "btn_bg_active": "#1d3b53",
            "accent": "#82aaff",
        },
    },
    "Nord": {
        "background": "#2e3440",
        "foreground": "#d8dee9",
        "select": "#434c5e",
        "section": "#88c0d0",
        "warn": "#ebcb8b",
        "error": "#bf616a",
        "ok": "#a3be8c",
        "inserted": "#8fbcbb",
        "dim": "#7b88a1",
        "key": "#88c0d0",
        "string": "#a3be8c",
        "number": "#b48ead",
        "keyword": "#81a1c1",
        "punct": "#7b88a1",
        "tag": "#81a1c1",
        "attr": "#d08770",
        "chrome": {
            "bg": "#2e3440",
            "bg2": "#272c36",
            "field_bg": "#3b4252",
            "border": "#4c566a",
            "fg": "#d8dee9",
            "fg_dim": "#7b88a1",
            "select": "#434c5e",
            "btn_bg": "#434c5e",
            "btn_bg_active": "#4c566a",
            "accent": "#88c0d0",
        },
    },
    "Shades of Purple": {
        "background": "#2d2b55",
        "foreground": "#e3dfff",
        "select": "#423f6f",
        "section": "#9effff",
        "warn": "#fad000",
        "error": "#ec3a37",
        "ok": "#3ad900",
        "inserted": "#a5ff90",
        "dim": "#a599e9",
        "key": "#9effff",
        "string": "#a5ff90",
        "number": "#ff628c",
        "keyword": "#ff9d00",
        "punct": "#a599e9",
        "tag": "#ff9d00",
        "attr": "#fad000",
        "chrome": {
            "bg": "#2d2b55",
            "bg2": "#1e1e3f",
            "field_bg": "#28284e",
            "border": "#43417a",
            "fg": "#e3dfff",
            "fg_dim": "#a599e9",
            "select": "#423f6f",
            "btn_bg": "#43417a",
            "btn_bg_active": "#5c5a9e",
            "accent": "#fad000",
        },
    },
    "GitHub Dark": {
        "background": "#0d1117",
        "foreground": "#e6edf3",
        "select": "#264f78",
        "section": "#58a6ff",
        "warn": "#d29922",
        "error": "#f85149",
        "ok": "#3fb950",
        "inserted": "#7ee787",
        "dim": "#8b949e",
        "key": "#79c0ff",
        "string": "#a5d6ff",
        "number": "#79c0ff",
        "keyword": "#ff7b72",
        "punct": "#8b949e",
        "tag": "#7ee787",
        "attr": "#d2a8ff",
        "chrome": {
            "bg": "#0d1117",
            "bg2": "#010409",
            "field_bg": "#161b22",
            "border": "#30363d",
            "fg": "#e6edf3",
            "fg_dim": "#8b949e",
            "select": "#264f78",
            "btn_bg": "#21262d",
            "btn_bg_active": "#30363d",
            "accent": "#58a6ff",
        },
    },
    "Catppuccin Mocha": {
        "background": "#1e1e2e",
        "foreground": "#cdd6f4",
        "select": "#45475a",
        "section": "#89b4fa",
        "warn": "#f9e2af",
        "error": "#f38ba8",
        "ok": "#a6e3a1",
        "inserted": "#94e2d5",
        "dim": "#6c7086",
        "key": "#89dceb",
        "string": "#a6e3a1",
        "number": "#fab387",
        "keyword": "#cba6f7",
        "punct": "#6c7086",
        "tag": "#cba6f7",
        "attr": "#f9e2af",
        "chrome": {
            "bg": "#1e1e2e",
            "bg2": "#181825",
            "field_bg": "#313244",
            "border": "#45475a",
            "fg": "#cdd6f4",
            "fg_dim": "#6c7086",
            "select": "#45475a",
            "btn_bg": "#313244",
            "btn_bg_active": "#45475a",
            "accent": "#89b4fa",
        },
    },
    "Ayu Dark": {
        "background": "#0a0e14",
        "foreground": "#b3b1ad",
        "select": "#273747",
        "section": "#39bae6",
        "warn": "#ffb454",
        "error": "#f07178",
        "ok": "#c2d94c",
        "inserted": "#95e6cb",
        "dim": "#626a73",
        "key": "#59c2ff",
        "string": "#c2d94c",
        "number": "#e6b450",
        "keyword": "#ff8f40",
        "punct": "#626a73",
        "tag": "#39bae6",
        "attr": "#ffb454",
        "chrome": {
            "bg": "#0a0e14",
            "bg2": "#0d1017",
            "field_bg": "#131721",
            "border": "#253340",
            "fg": "#b3b1ad",
            "fg_dim": "#626a73",
            "select": "#273747",
            "btn_bg": "#1b2733",
            "btn_bg_active": "#253340",
            "accent": "#e6b450",
        },
    },
    "Cobalt2": {
        "background": "#193549",
        "foreground": "#ffffff",
        "select": "#0050a4",
        "section": "#9effff",
        "warn": "#ffc600",
        "error": "#ff628c",
        "ok": "#3ad900",
        "inserted": "#80ffbb",
        "dim": "#0088ff",
        "key": "#9effff",
        "string": "#3ad900",
        "number": "#ff628c",
        "keyword": "#ff9d00",
        "punct": "#8ba7bf",
        "tag": "#ff9d00",
        "attr": "#ffc600",
        "chrome": {
            "bg": "#193549",
            "bg2": "#122738",
            "field_bg": "#1f4662",
            "border": "#234e6d",
            "fg": "#ffffff",
            "fg_dim": "#8ba7bf",
            "select": "#0050a4",
            "btn_bg": "#234e6d",
            "btn_bg_active": "#2f6591",
            "accent": "#ffc600",
        },
    },
    "SynthWave '84": {
        "background": "#262335",
        "foreground": "#f2f2f2",
        "select": "#463465",
        "section": "#36f9f6",
        "warn": "#fede5d",
        "error": "#fe4450",
        "ok": "#72f1b8",
        "inserted": "#ff7edb",
        "dim": "#848bbd",
        "key": "#36f9f6",
        "string": "#ff8b39",
        "number": "#f97e72",
        "keyword": "#fede5d",
        "punct": "#848bbd",
        "tag": "#72f1b8",
        "attr": "#fede5d",
        "chrome": {
            "bg": "#262335",
            "bg2": "#241b2f",
            "field_bg": "#34294f",
            "border": "#495495",
            "fg": "#f2f2f2",
            "fg_dim": "#848bbd",
            "select": "#463465",
            "btn_bg": "#3b3559",
            "btn_bg_active": "#495495",
            "accent": "#ff7edb",
        },
    },
    "Winter is Coming": {
        "background": "#011627",
        "foreground": "#c5e4fd",
        "select": "#103362",
        "section": "#00bff9",
        "warn": "#ffe6a6",
        "error": "#ef5350",
        "ok": "#8dec95",
        "inserted": "#bcf0c0",
        "dim": "#5f7e97",
        "key": "#87aff4",
        "string": "#bcf0c0",
        "number": "#f78c6c",
        "keyword": "#00bff9",
        "punct": "#5f7e97",
        "tag": "#00bff9",
        "attr": "#ffe6a6",
        "chrome": {
            "bg": "#011627",
            "bg2": "#010f1c",
            "field_bg": "#0b2942",
            "border": "#122d42",
            "fg": "#c5e4fd",
            "fg_dim": "#5f7e97",
            "select": "#103362",
            "btn_bg": "#122d42",
            "btn_bg_active": "#1d3b53",
            "accent": "#00bff9",
        },
    },
    "Material Dark": {
        "background": "#212121",
        "foreground": "#eeffff",
        "select": "#404040",
        "section": "#82aaff",
        "warn": "#ffcb6b",
        "error": "#f07178",
        "ok": "#c3e88d",
        "inserted": "#89ddff",
        "dim": "#545454",
        "key": "#82aaff",
        "string": "#c3e88d",
        "number": "#f78c6c",
        "keyword": "#c792ea",
        "punct": "#545454",
        "tag": "#f07178",
        "attr": "#ffcb6b",
        "chrome": {
            "bg": "#212121",
            "bg2": "#1a1a1a",
            "field_bg": "#292929",
            "border": "#2b2b2b",
            "fg": "#eeffff",
            "fg_dim": "#616161",
            "select": "#404040",
            "btn_bg": "#2b2b2b",
            "btn_bg_active": "#404040",
            "accent": "#009688",
        },
    },
    "Bluloco Dark": {
        "background": "#282c34",
        "foreground": "#abb2bf",
        "select": "#3e4451",
        "section": "#10b1fe",
        "warn": "#f9c859",
        "error": "#ff6480",
        "ok": "#3fc56b",
        "inserted": "#4ec9b0",
        "dim": "#636d83",
        "key": "#3691ff",
        "string": "#f9c859",
        "number": "#ff78f8",
        "keyword": "#10b1fe",
        "punct": "#636d83",
        "tag": "#ff6480",
        "attr": "#ff936a",
        "chrome": {
            "bg": "#282c34",
            "bg2": "#21252b",
            "field_bg": "#2f333d",
            "border": "#3e4451",
            "fg": "#abb2bf",
            "fg_dim": "#636d83",
            "select": "#3e4451",
            "btn_bg": "#3e4451",
            "btn_bg_active": "#4b5263",
            "accent": "#10b1fe",
        },
    },
    "Palenight": {
        "background": "#292d3e",
        "foreground": "#a6accd",
        "select": "#3e4460",
        "section": "#82aaff",
        "warn": "#ffcb6b",
        "error": "#f07178",
        "ok": "#c3e88d",
        "inserted": "#89ddff",
        "dim": "#676e95",
        "key": "#82aaff",
        "string": "#c3e88d",
        "number": "#f78c6c",
        "keyword": "#c792ea",
        "punct": "#676e95",
        "tag": "#f07178",
        "attr": "#ffcb6b",
        "chrome": {
            "bg": "#292d3e",
            "bg2": "#1b1e2b",
            "field_bg": "#333748",
            "border": "#3c435e",
            "fg": "#a6accd",
            "fg_dim": "#676e95",
            "select": "#3e4460",
            "btn_bg": "#3c435e",
            "btn_bg_active": "#4e5579",
            "accent": "#82aaff",
        },
    },
    "Poimandres": {
        "background": "#1b1e28",
        "foreground": "#e4f0fb",
        "select": "#303340",
        "section": "#add7ff",
        "warn": "#fffac2",
        "error": "#d0679d",
        "ok": "#5de4c7",
        "inserted": "#89ddff",
        "dim": "#767c9d",
        "key": "#add7ff",
        "string": "#5de4c7",
        "number": "#91b4d5",
        "keyword": "#91b4d5",
        "punct": "#767c9d",
        "tag": "#5de4c7",
        "attr": "#add7ff",
        "chrome": {
            "bg": "#1b1e28",
            "bg2": "#171922",
            "field_bg": "#252b37",
            "border": "#303340",
            "fg": "#e4f0fb",
            "fg_dim": "#767c9d",
            "select": "#303340",
            "btn_bg": "#303340",
            "btn_bg_active": "#3f4452",
            "accent": "#5de4c7",
        },
    },
    "Noctis": {
        "background": "#052529",
        "foreground": "#b2cacd",
        "select": "#0e6671",
        "section": "#49ace9",
        "warn": "#ffc180",
        "error": "#e66533",
        "ok": "#49e9a6",
        "inserted": "#49d6e9",
        "dim": "#5b858b",
        "key": "#49ace9",
        "string": "#49e9a6",
        "number": "#7060eb",
        "keyword": "#df769b",
        "punct": "#5b858b",
        "tag": "#df769b",
        "attr": "#ffc180",
        "chrome": {
            "bg": "#052529",
            "bg2": "#041d20",
            "field_bg": "#06373d",
            "border": "#0d4d55",
            "fg": "#b2cacd",
            "fg_dim": "#5b858b",
            "select": "#0e6671",
            "btn_bg": "#0d4d55",
            "btn_bg_active": "#0e6671",
            "accent": "#49ace9",
        },
    },
    "Panda": {
        "background": "#292a2b",
        "foreground": "#e6e6e6",
        "select": "#3e4142",
        "section": "#45a9f9",
        "warn": "#ffb86c",
        "error": "#ff2c6d",
        "ok": "#19f9d8",
        "inserted": "#6fc1ff",
        "dim": "#676b79",
        "key": "#6fc1ff",
        "string": "#19f9d8",
        "number": "#ffb86c",
        "keyword": "#ff75b5",
        "punct": "#676b79",
        "tag": "#ff75b5",
        "attr": "#ffb86c",
        "chrome": {
            "bg": "#292a2b",
            "bg2": "#222223",
            "field_bg": "#31353a",
            "border": "#3e4142",
            "fg": "#e6e6e6",
            "fg_dim": "#676b79",
            "select": "#3e4142",
            "btn_bg": "#3e4142",
            "btn_bg_active": "#52585c",
            "accent": "#19f9d8",
        },
    },
    "City Lights": {
        "background": "#1d252c",
        "foreground": "#b7c5d3",
        "select": "#28323b",
        "section": "#5ec4ff",
        "warn": "#ebbf83",
        "error": "#e27e8d",
        "ok": "#8bd49c",
        "inserted": "#70e1e8",
        "dim": "#4f6875",
        "key": "#70e1e8",
        "string": "#539afc",
        "number": "#e27e8d",
        "keyword": "#5ec4ff",
        "punct": "#718ca1",
        "tag": "#5ec4ff",
        "attr": "#ebbf83",
        "chrome": {
            "bg": "#1d252c",
            "bg2": "#171d23",
            "field_bg": "#242e38",
            "border": "#333f4a",
            "fg": "#b7c5d3",
            "fg_dim": "#718ca1",
            "select": "#28323b",
            "btn_bg": "#333f4a",
            "btn_bg_active": "#41505e",
            "accent": "#5ec4ff",
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


def _normalize_hex(value: str) -> str:
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


def _parse_flat_kv_text(text: str) -> dict[str, str]:
    """Parse the flat 'key: value' shape base16 scheme YAML files use.

    One ``scheme``/``author``/``base00``..``base0F`` per line, values plain
    or quoted. Deliberately not a general YAML parser -- just enough to read these
    single-level scheme files without requiring PyYAML to be installed.
    """
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


def _theme_from_base16(data: dict) -> dict | None:
    """Map a base16 scheme onto this project's theme roles.

    The standard semantic mapping: base00=bg, base05=fg,
    base02=selection, base08=red/error (also: variables/XML tags),
    base0A=yellow/warn, base0B=green/ok (also: strings), base0C=cyan/inserted
    (also: support/escapes), base0D=blue/section (also: functions/attribute
    IDs -- used here for JSON keys), base03=comment/dim (also: punctuation),
    base09=orange (integers/booleans/constants AND xml attributes -- used
    here for both 'number' and 'attr', per the base16 spec's own slot
    reuse), base0E=purple (keywords).
    """
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
    theme: dict[str, object] = {
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


def _theme_from_native(data: dict) -> tuple[dict | None, list[str]]:
    out: dict[str, object] = {}
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


def parse_theme_file(path: str | Path) -> tuple[str, dict]:
    """Read a theme file (.json, .yaml/.yml, or extensionless).

    Returns ``(name, theme_dict)``, and raises ValueError with a
    human-readable reason on any format problem. Tries, in order: JSON parse then native-field mapping or
    base16 mapping; falling back to a flat key:value parse for non-JSON
    (e.g. base16 .yaml scheme files) with the same two mappings.
    """
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


def _json_syntax_colors(theme: dict) -> dict[str, str]:
    """Return the 7 JSON/HTML token-role colors for a theme.

    Falls back to the theme's required log-panel roles for any that are
    missing (so an older/plainer
    imported theme -- or one hand-written without the optional fields --
    still gets a coherent, if less differentiated, set of colors here).
    """
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
    """Report whether a ``#rrggbb`` color is perceptually light (ITU-R 601 luma)."""
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) > 127


def chrome_from_theme(theme: dict) -> dict[str, str]:
    """Derive the 11 chrome (window/widget) colours for a syntax theme.

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
                    pass  # malformed colour value in an imported theme -- fall back silently
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


def _restyle_syntax_tags(widget: tk.Text) -> None:
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
        pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal


def _restyle_combobox_popdown(widget: tk.Misc) -> None:
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
            pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal


def _configure_each(widget: tk.Misc, options: dict) -> None:
    """Apply options one at a time, skipping any the widget doesn't support.

    Same pattern (and same reason) as style_plain_widget: one unsupported
    option -- or a widget destroyed mid-loop -- must not blank the rest.
    """
    for opt, val in options.items():
        try:
            widget.configure(**{opt: val})
        except tk.TclError:
            pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal


def _restyle_plain_live(w: tk.Misc) -> bool:
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
            pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal
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


def restyle_widget_tree(widget: tk.Misc) -> int:
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


def _tag_embedded_html(
    text_widget: tk.Text, text: str, base: int, idx: Callable[[int], str]
) -> None:
    """Tag any HTML-ish markup found in ``text``.

    ``text`` is a slice starting at absolute character offset ``base`` within
    whatever was inserted into text_widget; markup is tagged
    html_tag/html_attr/html_value/html_punct. ``idx(absolute_pos)`` must
    return a Tk Text index. Shared by highlight_json_with_html (HTML nested
    inside a JSON string token) and highlight_plain_text_with_html (HTML in
    a field shown as its own raw string, no surrounding JSON quoting).
    """
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


def highlight_json_with_html(text_widget: tk.Text, text: str, colors: dict) -> None:
    """Tag already-inserted ``text`` as JSON, breaking out embedded HTML.

    ``text_widget`` must be a normal-state Text widget holding ``text``; any
    HTML-ish markup inside string values is further broken out and coloured.
    ``colors`` is a _json_syntax_colors(...) dict.
    Tag *styles* (tag_configure) must already be set on text_widget by the
    caller -- this only calls tag_add.
    """

    def idx(pos: int) -> str:
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
            pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal


def highlight_plain_text_with_html(text_widget: tk.Text, text: str, colors: dict) -> None:
    r"""Tag a plain-string field shown as its own raw content.

    See _show_field_detail: this is NOT run through json.dumps, so there's no
    surrounding quotes and no \\" / \\\\ / \\n escaping to fight through.
    That's the whole point: json.dumps-ing a book-text field just to display
    it turns every embedded quote in '<FONT COLOR=\"000000\">' into visual
    noise for no benefit, since nothing here is being re-parsed as JSON.
    Colors the whole span with the theme's string color, then layers embedded
    HTML-ish markup (if any) on top, exactly like a JSON string value would
    get inside highlight_json_with_html.
    """

    def idx(pos: int) -> str:
        return text_widget.index(f"1.0 + {pos} chars")

    text_widget.tag_add("json_string", idx(0), idx(len(text)))
    _tag_embedded_html(text_widget, text, 0, idx)
    for t in ("html_punct", "html_tag", "html_attr", "html_value"):
        try:
            text_widget.tag_raise(t)
        except tk.TclError:
            pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal


def style_json_syntax_tags(text_widget: tk.Text, colors: dict) -> None:
    """(Re-)configure the tag styles the two highlighters paint with.

    Used by highlight_json_with_html and highlight_plain_text_with_html.
    Call once per Text widget before/after inserting -- tag_add doesn't need the style to exist yet, but nothing
    will be visible until this runs.
    """
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
