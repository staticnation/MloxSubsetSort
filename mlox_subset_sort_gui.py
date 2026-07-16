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

import io
import os
import queue
import subprocess
import sys
import threading
import traceback
import types
import webbrowser
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
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
    from tkinterweb import HtmlFrame as HTMLViewer   # supports load_file + SVG
except Exception:
    try:
        from tkhtmlview import HTMLScrolledText as HTMLViewer
    except Exception:
        HTMLViewer = None

# pywebview is the BEST in-app option: it hosts the OS webview (Edge WebView2 /
# WebKit), so it renders the SVG map + tabs exactly like a browser. It's launched
# in a separate process (webview.start() wants the main thread), so it doesn't
# fight tkinter's mainloop. Detected here; used first if present.
try:
    import webview as _webview_probe   # real import: reliable under PyInstaller, unlike find_spec
    HAVE_PYWEBVIEW = True
    del _webview_probe
except Exception:
    HAVE_PYWEBVIEW = False


_APP_DIR = None
_TRACE_REQUEST = None   # set by main() from --trace; None = use env var / off


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
        base = Path(sys.executable).resolve().parent      # next to the built .exe
    else:
        base = Path(__file__).resolve().parent
    try:
        probe = base / ".mlox_write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except Exception:
        if os.name == "nt":
            root = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        elif sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support"
        else:
            root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        base = root / "MloxSubsetSort"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            base = Path.home()
    _APP_DIR = base
    return base

# Drag-and-drop is optional -- degrade gracefully to Browse-only if missing.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAVE_DND = True
except ImportError:
    HAVE_DND = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import mlox_subset_sort as core
except ImportError as e:
    sys.exit(f"Couldn't import mlox_subset_sort.py -- make sure it's in the same folder.\n({e})")


# ---------------------------------------------------------------------------
# dark theme palette -- applied once at startup to the ttk.Style, and passed
# directly to the couple of plain (non-ttk) widgets that ttk doesn't theme
# (tk.Listbox, the ScrolledText log panel).
# ---------------------------------------------------------------------------

DARK = {
    "bg":        "#1e1e1e",  # window/frame background
    "bg2":       "#252526",  # slightly-raised panel background
    "field_bg":  "#2d2d30",  # entry/listbox/text background
    "border":    "#3f3f46",
    "fg":        "#e6e6e6",  # normal text
    "fg_dim":    "#9a9a9a",  # secondary/status text
    "select":    "#094771",  # selection highlight
    "btn_bg":    "#3a3a3d",
    "btn_bg_active": "#4a4a4e",
    "accent":    "#3794ff",
}


def apply_dark_theme(root):
    root.configure(bg=DARK["bg"])
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(".", background=DARK["bg"], foreground=DARK["fg"],
                     fieldbackground=DARK["field_bg"], bordercolor=DARK["border"],
                     darkcolor=DARK["bg"], lightcolor=DARK["bg"])
    style.configure("TFrame", background=DARK["bg"])
    style.configure("TLabel", background=DARK["bg"], foreground=DARK["fg"])
    style.configure("TLabelframe", background=DARK["bg"], foreground=DARK["fg"],
                     bordercolor=DARK["border"])
    style.configure("TLabelframe.Label", background=DARK["bg"], foreground=DARK["fg"])
    style.configure("TCheckbutton", background=DARK["bg"], foreground=DARK["fg"])
    style.map("TCheckbutton", background=[("active", DARK["bg"])],
              foreground=[("disabled", DARK["fg_dim"])])
    style.configure("TEntry", fieldbackground=DARK["field_bg"], foreground=DARK["fg"],
                     insertcolor=DARK["fg"], bordercolor=DARK["border"])
    style.map("TEntry", fieldbackground=[("readonly", DARK["field_bg"])])
    style.configure("Conf.Treeview", background=DARK["field_bg"], fieldbackground=DARK["field_bg"],
                     foreground=DARK["fg"], bordercolor=DARK["border"], rowheight=22)
    style.map("Conf.Treeview", background=[("selected", DARK["select"])],
              foreground=[("selected", DARK["fg"])])
    style.configure("Conf.Treeview.Heading", background=DARK["btn_bg"], foreground=DARK["fg"],
                     relief="flat")
    style.map("Conf.Treeview.Heading", background=[("active", DARK["btn_bg_active"])])
    style.configure("TNotebook", background=DARK["bg"], borderwidth=0, bordercolor=DARK["border"])
    style.configure("TNotebook.Tab", background=DARK["btn_bg"], foreground=DARK["fg"],
                     padding=(12, 4), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", DARK["select"]), ("active", DARK["btn_bg_active"])],
              foreground=[("selected", "#ffffff")])
    style.configure("TButton", background=DARK["btn_bg"], foreground=DARK["fg"],
                     bordercolor=DARK["border"], focuscolor=DARK["bg"])
    style.map("TButton", background=[("active", DARK["btn_bg_active"]), ("disabled", DARK["bg2"])],
              foreground=[("disabled", DARK["fg_dim"])])
    style.configure("TScrollbar", background=DARK["btn_bg"], troughcolor=DARK["bg2"],
                     bordercolor=DARK["border"], arrowcolor=DARK["fg"])
    style.map("TScrollbar", background=[("active", DARK["btn_bg_active"])])
    return style


def style_plain_widget(widget):
    """For non-ttk widgets (tk.Listbox, scrolledtext.ScrolledText) that ttk
    theming doesn't reach. Applied option-by-option since the exact set of
    supported options differs between Listbox and Text (e.g. Listbox has no
    insertbackground)."""
    options = {
        "background": DARK["field_bg"], "foreground": DARK["fg"],
        "insertbackground": DARK["fg"], "selectbackground": DARK["select"],
        "selectforeground": DARK["fg"], "highlightbackground": DARK["border"],
        "highlightcolor": DARK["accent"], "highlightthickness": 1,
        "relief": "flat", "borderwidth": 0,
    }
    for opt, val in options.items():
        try:
            widget.configure(**{opt: val})
        except tk.TclError:
            pass


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
            x = self.widget.winfo_rootx() + 14
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except tk.TclError:
            return
        tw = tk.Toplevel(self.widget)
        self.tip_window = tw
        tw.wm_overrideredirect(True)
        try:
            tw.attributes("-topmost", True)
        except tk.TclError:
            pass
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tw, text=self.text, justify="left", background="#2d2d30", foreground=DARK["fg"],
            relief="solid", borderwidth=1, wraplength=self.wraplength,
            font=("TkDefaultFont", 9), padx=6, pady=4,
        ).pack()

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
    def __init__(self, parent, label, row, var, browse_kind="open",
                 filetypes=(("All files", "*.*"),), on_drop_extra=None, tooltip=None,
                 extra_button=None):
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
            browse_btn = ttk.Button(btnbar, text="Browse...", command=browse)
            browse_btn.pack(side="left")
            ex_text, ex_cmd = extra_button[0], extra_button[1]
            ex_tip = extra_button[2] if len(extra_button) > 2 else None
            self.extra_btn = ttk.Button(btnbar, text=ex_text, command=ex_cmd)
            self.extra_btn.pack(side="left", padx=(6, 0))
            if ex_tip:
                add_tooltip(self.extra_btn, ex_tip)
        else:
            browse_btn = ttk.Button(parent, text="Browse...", command=browse)
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
        self._drag_block = None   # list of (contiguous) indices being dragged
        self._moved = False
        self.bind("<Button-1>", self._on_press, add="+")
        self.bind("<B1-Motion>", self._on_motion, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")

    def _on_press(self, event):
        idx = self.nearest(event.y)
        self._moved = False
        if not (0 <= idx < self.size()):
            self._drag_block = None
            return
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
            self.on_reorder()
        self._drag_block = None
        self._moved = False


# ---------------------------------------------------------------------------
# rule-file list: an ordered listbox (priority = order, last = highest,
# matching mlox_subset_sort's own --rules semantics) with add/remove/reorder
# controls and its own drop target
# ---------------------------------------------------------------------------

class RuleFilesPanel:
    def __init__(self, parent, row):
        frame = ttk.LabelFrame(
            parent, text="Rule files (priority = order below, last = highest -- drag rows to reorder)")
        frame.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=(8, 4))
        frame.columnconfigure(0, weight=1)

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        # single-select: dragging a multi-selection to a new spot is
        # ambiguous (which item does the cursor "carry"?), so keep it to one
        # row at a time -- Move Up/Down below still work with a single row too
        self.listbox = DragReorderListbox(list_frame, height=5, selectmode="browse",
                                           activestyle="dotbox", exportselection=False)
        style_plain_widget(self.listbox)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        add_tooltip(self.listbox,
                     "mlox rule files (mlox_base.txt, mlox_user.txt, ...), applied in this order.\n"
                     "Later files can override/extend earlier ones -- put mlox_base.txt first and "
                     "your own mlox_user.txt last. Drag rows to reorder, or use the buttons.")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)

        btns = ttk.Frame(frame)
        btns.grid(row=0, column=1, sticky="n", padx=(0, 8), pady=8)
        add_btn = ttk.Button(btns, text="Add File(s)...", command=self.add_files)
        add_btn.pack(fill="x", pady=2)
        add_tooltip(add_btn, "Browse for one or more mlox rule .txt files to add to the list.")
        remove_btn = ttk.Button(btns, text="Remove Selected", command=self.remove_selected)
        remove_btn.pack(fill="x", pady=2)
        add_tooltip(remove_btn, "Remove the selected rule file from the list (doesn't delete anything on disk).")
        up_btn = ttk.Button(btns, text="Move Up", command=lambda: self.move(-1))
        up_btn.pack(fill="x", pady=2)
        add_tooltip(up_btn, "Move the selected rule file earlier (lower priority).")
        down_btn = ttk.Button(btns, text="Move Down", command=lambda: self.move(1))
        down_btn.pack(fill="x", pady=2)
        add_tooltip(down_btn, "Move the selected rule file later (higher priority).")

        if HAVE_DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self._on_drop)
        else:
            ttk.Label(frame, text="(install tkinterdnd2 to drag files in from your file manager)",
                      foreground=DARK["fg_dim"]).grid(row=1, column=0, columnspan=2, sticky="w", padx=8)

    def _on_drop(self, event):
        for p in self.listbox.tk.splitlist(event.data):
            self.listbox.insert("end", p)

    def add_files(self):
        paths = filedialog.askopenfilenames(filetypes=(("mlox rule files", "*.txt"), ("All files", "*.*")))
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

class ReorderPanel:
    # "touched by this sort" row highlight. Deliberately a warm amber rather
    # than a blue -- blue was both low-contrast against the dark field bg and
    # easily confused with the blue selection highlight (#094771). Amber on
    # near-black reads clearly and never collides with the selection color.
    HIGHLIGHT = {"background": "#8a0808", "foreground": "#ffe8c2"}
    NORMAL = {"background": DARK["field_bg"], "foreground": DARK["fg"]}
    DISABLED = {"background": DARK["field_bg"], "foreground": "#6a6a6a"}
    DISABLE_PREFIX = "✗ "   # "X " marker shown on opted-out rows

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
        self.listbox = DragReorderListbox(list_frame, height=8, selectmode="extended",
                                           activestyle="dotbox", exportselection=False,
                                           on_reorder=lambda: self._restyle())
        style_plain_widget(self.listbox)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        self.listbox.bind("<Double-Button-1>", self._on_double, add="+")
        if listbox_tooltip:
            add_tooltip(self.listbox, listbox_tooltip)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)

        btns = ttk.Frame(frame)
        btns.grid(row=0, column=1, sticky="n", padx=(0, 8), pady=8)
        up_btn = ttk.Button(btns, text="Move Up", command=lambda: self.move(-1))
        up_btn.pack(fill="x", pady=2)
        add_tooltip(up_btn, "Move the selected row(s) one position earlier. Works with a multi-"
                            "selection; you can also drag a contiguous block up with the mouse.")
        down_btn = ttk.Button(btns, text="Move Down", command=lambda: self.move(1))
        down_btn.pack(fill="x", pady=2)
        add_tooltip(down_btn, "Move the selected row(s) one position later. Works with a multi-"
                              "selection; you can also drag a contiguous block down with the mouse.")
        toggle_btn = ttk.Button(btns, text="Disable / Enable", command=self.toggle_selected)
        toggle_btn.pack(fill="x", pady=(10, 2))
        add_tooltip(toggle_btn,
                     "Opt the selected row in or out of the load order. Disabled rows are dimmed and "
                     "marked, and are left out of Export: a custom item is simply not inserted, and an "
                     "item already in your openmw.cfg gets a removeContent/removeData entry in the "
                     "emitted TOML so it's durably removed. Double-click a row to toggle it too.")
        reset_btn = ttk.Button(btns, text=reset_label, command=self.reset)
        reset_btn.pack(fill="x", pady=(2, 2))
        add_tooltip(reset_btn, "Discard any manual dragging and restore the order from the last Sort "
                               "(your disable/enable choices are kept).")

        self._original_order = []
        self._highlight_lower = set()
        self._disabled = set()   # real item texts the user has opted out

    def load(self, items, highlighted_items=(), disabled_items=()):
        """Called after a successful Sort -- populates the list and remembers
        it (for Reset), which items render highlighted, and which are disabled.
        disabled_items lets a re-Sort carry the previous opt-outs forward for
        any item still present."""
        self._original_order = list(items)
        self._highlight_lower = {str(x).lower() for x in highlighted_items}
        present = set(items)
        self._disabled = {str(d) for d in disabled_items if str(d) in present}
        self._refill(self._original_order)

    def reset(self):
        self._refill(self._original_order)

    def _display(self, real):
        return self.DISABLE_PREFIX + real if real in self._disabled else real

    def _strip(self, display):
        if display.startswith(self.DISABLE_PREFIX):
            return display[len(self.DISABLE_PREFIX):]
        return display

    def _refill(self, items):
        self.listbox.delete(0, "end")
        for real in items:
            self.listbox.insert("end", self._display(real))
        self._restyle()

    def _restyle(self):
        """Apply per-row colours: disabled = dim, else highlighted = amber, else
        normal. Explicit on every row so toggling/dragging stays consistent."""
        for i, disp in enumerate(self.listbox.get(0, "end")):
            real = self._strip(disp)
            if real in self._disabled:
                self.listbox.itemconfig(i, **self.DISABLED)
            elif real.lower() in self._highlight_lower:
                self.listbox.itemconfig(i, **self.HIGHLIGHT)
            else:
                self.listbox.itemconfig(i, **self.NORMAL)

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
            for idx in sel:                       # ascending, so each swaps up cleanly
                t = self.listbox.get(idx)
                self.listbox.delete(idx)
                self.listbox.insert(idx - 1, t)
            new_sel = [i - 1 for i in sel]
        else:
            if sel[-1] >= size - 1:
                return
            for idx in reversed(sel):             # descending for a down-move
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
            title="Plugin load order -- drag to override mlox (highlighted = touched by this sort)",
            reset_label="Reset to mlox Order",
            listbox_tooltip="The content= load order mlox computed, after '1. Sort'. Drag rows to "
                             "manually override it before Exporting -- highlighted rows are the ones "
                             "mlox actually inserted or moved; unhighlighted rows were already in "
                             "openmw.cfg and left where they were. Select row(s) and click "
                             "Disable/Enable (or double-click) to opt them out of the load order.",
        )


class DataPathOrderPanel(ReorderPanel):
    """Same idea as PluginOrderPanel but for data= folder paths. Only
    populated when a Sort was run with 'Sort data= paths too' checked --
    otherwise stays empty, since there's nothing computed to show or
    override (see App._sort_finished)."""
    def __init__(self, parent):
        super().__init__(
            parent,
            title="Data path order -- drag to adjust (highlighted = your custom paths)",
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
    # tuned for readability on a dark (#1e1e1e-ish) log background --
    # errors/conflicts get an extra-bright, bold red since that's the
    # thing you most need to be able to spot at a glance
    LOG_TAGS = {
        "section": {"foreground": "#5eb3ff", "font": ("TkFixedFont", 10, "bold")},
        "warn":    {"foreground": "#ffb454"},
        "error":   {"foreground": "#ff5c5c", "font": ("TkFixedFont", 10, "bold")},
        "ok":      {"foreground": "#5fd97f"},
        "inserted": {"foreground": "#7ee0a0"},   # a plugin/path this sort inserted or moved
        "dim":     {"foreground": "#8a8a8a"},
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
        self._tes3conv_override = None     # user-set path to tes3conv (for field-level diffs)
        self._conf_session = None
        self._conf_paths = {}
        self._session = None               # reused disk-backed Tes3ConvSession
        self._keep_json = False

        self._build_widgets()
        self._load_settings()
        # Trace is OFF by default. It's turned on by the --trace flag (set by main()
        # into _TRACE_REQUEST) or the MLOX_SUBSET_TRACE env var -- either can name a
        # log file; otherwise mlox_subset_sort_trace.log is written next to the app.
        try:
            req = _TRACE_REQUEST if _TRACE_REQUEST is not None else os.environ.get("MLOX_SUBSET_TRACE")
            if req:
                if isinstance(req, str) and req.lower() not in ("1", "true", "yes", "on", ""):
                    path = req
                else:
                    path = app_base_dir() / "mlox_subset_sort_trace.log"
                core.set_trace_file(path)
                core.trace("GUI started")
                core.trace(f"viewers: frozen={bool(getattr(sys, 'frozen', False))} "
                           f"pywebview={HAVE_PYWEBVIEW} "
                           f"HTMLViewer={HTMLViewer.__module__ + '.' + HTMLViewer.__name__ if HTMLViewer else None} "
                           f"load_file={HTMLViewer is not None and hasattr(HTMLViewer, 'load_file')}")
                self.log_queue.put(f"[trace] writing debug trace to: {path}\n")
        except Exception:
            pass
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._poll_log_queue)

    # -- settings persistence ----------------------------------------------

    def _settings_file(self):
        return app_base_dir() / "mlox_subset_sort_settings.json"

    def _gather_settings(self):
        return {
            "cfg": self.cfg_var.get(), "customizations": self.customizations_var.get(),
            "subset_file": self.subset_file_var.get(), "emit_toml": self.emit_toml_var.get(),
            "list_name": self.list_name_var.get(), "plugin_order_yml": self.plugin_order_yml_var.get(),
            "tes3conv": self._tes3conv_override or "", "exclude": self.exclude_var.get(),
            "rules": [str(p) for p in self.rules_panel.get_paths()],
            "write_toml_inplace": self.write_toml_inplace_var.get(),
            "dry_run": self.dry_run_var.get(), "write_cfg": self.write_cfg_var.get(),
            "sort_data_paths": self.sort_data_paths_var.get(), "no_backup": self.no_backup_var.get(),
            "no_predicate_warnings": self.no_predicate_warnings_var.get(),
            "create_subset_doc": self.create_subset_doc_var.get(),
            "keep_json": self.keep_json_var.get(),
        }

    def _load_settings(self):
        import json
        try:
            d = json.loads(self._settings_file().read_text(encoding="utf-8"))
        except Exception:
            return
        setters = {
            "cfg": self.cfg_var, "customizations": self.customizations_var,
            "subset_file": self.subset_file_var, "emit_toml": self.emit_toml_var,
            "list_name": self.list_name_var, "plugin_order_yml": self.plugin_order_yml_var,
            "exclude": self.exclude_var,
        }
        for k, var in setters.items():
            if isinstance(d.get(k), str):
                var.set(d[k])
        for k, var in (("write_toml_inplace", self.write_toml_inplace_var),
                       ("dry_run", self.dry_run_var), ("write_cfg", self.write_cfg_var),
                       ("sort_data_paths", self.sort_data_paths_var), ("no_backup", self.no_backup_var),
                       ("no_predicate_warnings", self.no_predicate_warnings_var),
                       ("create_subset_doc", self.create_subset_doc_var),
                       ("keep_json", self.keep_json_var)):
            if isinstance(d.get(k), bool):
                var.set(d[k])
        if d.get("tes3conv"):
            self._tes3conv_override = d["tes3conv"]
        for p in (d.get("rules") or []):
            try:
                self.rules_panel.listbox.insert("end", p)
            except Exception:
                pass
        self._on_toggle_inplace()

    def _save_settings(self):
        import json
        try:
            self._settings_file().write_text(json.dumps(self._gather_settings(), indent=2), encoding="utf-8")
        except Exception:
            pass

    def _on_close(self):
        self._save_settings()
        s = getattr(self, "_session", None)
        if s is not None:
            try:
                s.cleanup()   # removes the temp JSON dump (no-op if 'keep' was set)
            except Exception:
                pass
        self.root.destroy()

    # -- layout ------------------------------------------------------------

    def _paned(self, parent, orient):
        """A tk.PanedWindow (not ttk -- ttk's has no visible grip) styled to
        match the dark theme. The default square handle is turned off; a
        hamburger-style grip is overlaid instead by _attach_hamburger_grip()."""
        return tk.PanedWindow(
            parent, orient=orient, sashwidth=8, sashrelief="flat", showhandle=False,
            bg=DARK["bg"], bd=0, background=DARK["border"], sashpad=0,
        )

    def _attach_hamburger_grip(self, paned, orient):
        """Overlay a hamburger-style (three-line) draggable grip centered on the
        single sash of a two-pane PanedWindow. Cross-platform: cursor names are
        tried in order and any failure is ignored, and if the sash geometry
        can't be read the grip just hides itself -- the sash stays draggable
        either way, so this is purely a nicer-looking handle, never load-bearing."""
        horizontal = (orient == "horizontal")   # horizontal paned -> vertical sash
        long_px, thick_px = 34, 12
        w = thick_px if horizontal else long_px
        h = long_px if horizontal else thick_px
        grip = tk.Canvas(paned, width=w, height=h, bg=DARK["btn_bg"],
                         highlightthickness=1, highlightbackground=DARK["border"], bd=0,
                         takefocus=0)
        if horizontal:   # three vertical lines (drag left/right)
            for x in (w // 2 - 3, w // 2, w // 2 + 3):
                grip.create_line(x, 5, x, h - 5, fill=DARK["fg_dim"])
        else:            # three horizontal lines (drag up/down)
            for y in (h // 2 - 3, h // 2, h // 2 + 3):
                grip.create_line(5, y, w - 5, y, fill=DARK["fg_dim"])
        for cur in (("sb_h_double_arrow" if horizontal else "sb_v_double_arrow"), "fleur", "hand2", ""):
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
        paned.bind("<B1-Motion>", lambda e: reposition(), add="+")     # follow a direct sash drag
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
                top, foreground=DARK["fg_dim"],
                text="Drag & drop is disabled (tkinterdnd2 not installed) -- use the Browse buttons below."
            )
            note.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
            start_row = 1
        else:
            start_row = 0

        self.cfg_var = tk.StringVar()
        self.customizations_var = tk.StringVar()
        self.subset_file_var = tk.StringVar()
        self.emit_toml_var = tk.StringVar()
        self.write_toml_inplace_var = tk.BooleanVar(value=False)
        self.list_name_var = tk.StringVar()
        self.plugin_order_yml_var = tk.StringVar()

        PathField(top, "openmw.cfg:", start_row, self.cfg_var,
                  filetypes=(("openmw.cfg", "*.cfg"), ("All files", "*.*")),
                  tooltip="Required. The openmw.cfg to read the current content= and data= order "
                          "from, and (if 'Write openmw.cfg directly' is checked) to patch.")
        PathField(top, "customizations.toml:", start_row + 1, self.customizations_var,
                  filetypes=(("TOML files", "*.toml"), ("All files", "*.*")),
                  tooltip="A momw-configurator/umo customizations TOML to pull the plugin/data-path "
                          "subset from automatically. Optional if you provide a subset file instead -- "
                          "provide both and they're combined.")
        PathField(top, "subset file (optional):", start_row + 2, self.subset_file_var,
                  filetypes=(("Text/TOML", "*.txt *.toml"), ("All files", "*.*")),
                  tooltip="A plain text file (one plugin filename or data folder path per line, "
                          "'#' comments allowed) or a minimal TOML with subset=[...]/data=[...]. "
                          "Combined with --emit-toml, this alone is enough to generate a brand new "
                          "customizations.toml with no existing one required.",
                  extra_button=("Scan...", self.on_scan_mods,
                                "Scan a mods folder to build the subset: every folder that contains an "
                                "asset subfolder (meshes/textures/...) or a plugin becomes a data path "
                                "(plus its plugins), then that branch isn't descended further. Whether "
                                "the result is saved to a .txt (and loaded here) or just kept in memory "
                                "for this session is set by the 'Create subset text document' option."))
        self.emit_toml_field = PathField(
            top, "emit corrected TOML to:", start_row + 3, self.emit_toml_var,
            browse_kind="save", filetypes=(("TOML files", "*.toml"), ("All files", "*.*")),
            tooltip="Where to write a corrected customizations.toml (sorted insert blocks, "
                    "re-anchored). Disabled when 'write directly back' below is checked.")

        # listName for the emitted TOML. momw-configurator REQUIRES this -- it
        # names the curated mod list the customizations apply to. Left blank,
        # the source customizations.toml's own listName is kept; when generating
        # from a subset file alone it would otherwise fall back to the useless
        # placeholder "generated", so setting this is recommended in that case.
        list_name_label = ttk.Label(top, text="list name (optional):")
        list_name_label.grid(row=start_row + 4, column=0, sticky="w", padx=(0, 8), pady=4)
        list_name_entry = ttk.Entry(top, textvariable=self.list_name_var)
        list_name_entry.grid(row=start_row + 4, column=1, sticky="ew", pady=4)
        list_name_tip = ("The momw-configurator listName written into the emitted "
                         "momw-customizations.toml, e.g. 'total-overhaul' -- the curated mod list "
                         "these customizations apply to. Overrides the listName from the "
                         "customizations.toml above if both are set. Leave blank to keep that file's "
                         "own listName; when generating from a subset file alone, set this so the "
                         "output isn't stuck with the placeholder 'generated'.")
        add_tooltip(list_name_label, list_name_tip)
        add_tooltip(list_name_entry, list_name_tip)

        PathField(top, "plugin-order.yml (optional):", start_row + 5, self.plugin_order_yml_var,
                  filetypes=(("YAML files", "*.yml *.yaml"), ("All files", "*.*")),
                  tooltip="MOMW's plugin-order.yml (source of truth for which plugins belong to which "
                          "curated list). With the list name above set, curated plugins for that list "
                          "are excluded from the sort (never reordered) so only your custom additions "
                          "are touched, and read-only warnings are emitted: redundant, orphan, "
                          "needs-cleaning, and a base-order drift check. PyYAML used if installed, "
                          "else a built-in parser.")

        inplace_chk = ttk.Checkbutton(
            top, text="Write directly back to customizations.toml (overwrite in place; "
                       "a .bak-<timestamp> copy is made first unless backups are disabled below)",
            variable=self.write_toml_inplace_var, command=self._on_toggle_inplace,
        )
        inplace_chk.grid(row=start_row + 6, column=0, columnspan=3, sticky="w", pady=(0, 4))
        add_tooltip(inplace_chk,
                     "Instead of writing to a separate file above, overwrite the customizations.toml "
                     "given above in place. A timestamped backup is made first (unless disabled), and "
                     "you'll get a confirmation prompt before it actually happens.")

        self.rules_panel = RuleFilesPanel(top, start_row + 7)

        # options
        opts = ttk.LabelFrame(top, text="Options")
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

        dry_chk = ttk.Checkbutton(opts, text="Dry run (preview only, don't write files)",
                                    variable=self.dry_run_var)
        dry_chk.grid(row=0, column=0, sticky="w", padx=8, pady=4)
        add_tooltip(dry_chk, "When checked, Export shows exactly what it would write without "
                              "touching any files. Uncheck when you're ready to actually save.")

        write_cfg_chk = ttk.Checkbutton(opts, text="Write openmw.cfg directly",
                                          variable=self.write_cfg_var)
        write_cfg_chk.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        add_tooltip(write_cfg_chk, "Patch the content=/data= lines in openmw.cfg in place on Export. "
                                    "A .bak-<timestamp> copy is made first unless backups are disabled.")

        sort_data_chk = ttk.Checkbutton(opts, text="Sort data= paths too",
                                          variable=self.sort_data_paths_var)
        sort_data_chk.grid(row=0, column=2, sticky="w", padx=8, pady=4)
        add_tooltip(sort_data_chk,
                     "mlox has no concept of data= folder order -- this positions new data= paths "
                     "using an explicit after/before anchor if you wrote one, or by scanning the "
                     "folder for plugins and anchoring next to their neighbor in the sorted content= "
                     "order. Off by default so a plugin-only run can't surprise-reorder data= too. "
                     "Also required for the data path order panel to populate.")

        no_backup_chk = ttk.Checkbutton(opts, text="Skip .bak backup of openmw.cfg",
                                          variable=self.no_backup_var)
        no_backup_chk.grid(row=1, column=0, sticky="w", padx=8, pady=4)
        add_tooltip(no_backup_chk, "Skip making a timestamped backup before overwriting openmw.cfg "
                                    "and/or an in-place customizations.toml. Not recommended.")

        no_warn_chk = ttk.Checkbutton(opts, text="Skip mlox Conflict/Requires/Note warnings",
                                        variable=self.no_predicate_warnings_var)
        no_warn_chk.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        add_tooltip(no_warn_chk,
                     "Skip evaluating [Conflict]/[Requires]/[Note] rules against the sorted plugin "
                     "list. This is purely informational and read-only either way -- it never changes "
                     "the computed order or what gets written, only whether warnings get printed.")

        create_doc_chk = ttk.Checkbutton(opts, text="Create subset text document (on Scan)",
                                          variable=self.create_subset_doc_var)
        create_doc_chk.grid(row=1, column=2, sticky="w", padx=8, pady=4)

        excl_lbl = ttk.Label(opts, text="Exclude from conflict / cell scans:")
        excl_lbl.grid(row=2, column=0, sticky="w", padx=8, pady=4)
        excl_entry = ttk.Entry(opts, textvariable=self.exclude_var)
        excl_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=8, pady=4)
        excl_tip = ("Comma-separated name patterns (glob: * ?, case-insensitive) to skip in the "
                    "Conflict/Cell-map/Resource scans -- e.g. 's3lightfixes*, *delta*, *groundcover*, "
                    "*grass*'. Handy for 'touches-everything' mods (light fixes, grass/ground "
                    "generators, delta/merged patches) that swamp the results. Saved with your settings.")
        add_tooltip(excl_lbl, excl_tip)
        add_tooltip(excl_entry, excl_tip)
        keep_json_chk = ttk.Checkbutton(opts, text="Keep tes3conv JSON dump", variable=self.keep_json_var,
                                        command=self._on_keep_json_toggle)
        keep_json_chk.grid(row=3, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        add_tooltip(keep_json_chk,
                     "tes3conv conversions are written to disk (not held in RAM) and read per-plugin, "
                     "so big load orders don't blow up memory. They always go to a 'tes3conv_json' "
                     "folder next to the app and are reused within a run -- so Check Conflicts then Cell "
                     "Map won't re-run tes3conv (a plugin is only re-converted if it changed). This box "
                     "just decides what happens on exit: checked = keep that folder (reused next launch "
                     "too); unchecked = delete it when you close the app.")
        add_tooltip(create_doc_chk,
                     "Controls what 'Scan...' does with its result. Checked: write the scanned list "
                     "to a .txt subset file you choose, and load it (the file stays on disk for reuse). "
                     "Unchecked: keep the scanned list in memory just for this session and feed it "
                     "straight to the sort -- nothing is written to disk.")

        # action row: Sort computes the plan (never writes anything) and
        # populates the order panels on the left; Export writes using
        # whatever order those panels are currently showing (the computed
        # order, if they were never dragged) and stays disabled until a
        # Sort succeeds
        action_row = ttk.Frame(top)
        action_row.grid(row=start_row + 9, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        action_row.columnconfigure(5, weight=1)

        self.sort_button = ttk.Button(action_row, text="1. Sort", command=self.on_sort)
        self.sort_button.grid(row=0, column=0, sticky="w")
        add_tooltip(self.sort_button,
                     "Run mlox and populate the plugin/data order panels on the left. Never writes "
                     "any files -- this is always safe to run.")
        self.export_button = ttk.Button(action_row, text="2. Export", command=self.on_export, state="disabled")
        self.export_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        add_tooltip(self.export_button,
                     "Write openmw.cfg and/or the customizations.toml, using whatever order the "
                     "panels on the left are currently showing (mlox's own order, unless you dragged "
                     "rows). Rows you disabled are left out -- new customs aren't inserted, and items "
                     "already in your cfg get a removeContent/removeData. Respects 'Dry run'. "
                     "Disabled until a Sort succeeds.")
        self.conflicts_button = ttk.Button(action_row, text="Check Conflicts",
                                            command=self.on_check_conflicts, state="disabled")
        self.conflicts_button.grid(row=0, column=2, sticky="w", padx=(8, 0))
        add_tooltip(self.conflicts_button,
                     "Scan the sorted, enabled plugins for TES3 record-level conflicts -- where two or "
                     "more plugins edit the same record (the last in the load order wins), like "
                     "TES3View. Prints a report in the log and opens a conflicts window; point that "
                     "window at a tes3conv binary for a field-by-field diff of each conflicting record. "
                     "Read-only; needs the plugin files reachable via your cfg's data= folders. Runs "
                     "after a Sort.")
        self.cellmap_button = ttk.Button(action_row, text="Cell Map",
                                          command=self.on_cell_map, state="disabled")
        self.cellmap_button.grid(row=0, column=3, sticky="w", padx=(8, 0))
        add_tooltip(self.cellmap_button,
                     "Build a 'modmapper'-style cell map from the sorted, enabled plugins: an "
                     "exterior-cell SVG heatmap (brighter = more mods; click a cell to jump to its "
                     "list entry) plus exterior/interior cell lists, showing which mods touch which "
                     "cells (your custom ones get a gold outline). The map is written to cell_map.html "
                     "and shown in an in-app window if pywebview or tkinterweb is installed, otherwise "
                     "in your browser. Read-only.")
        self.resource_button = ttk.Button(action_row, text="Resource Conflicts",
                                           command=self.on_resource_conflicts, state="disabled")
        self.resource_button.grid(row=0, column=4, sticky="w", padx=(8, 0))
        add_tooltip(self.resource_button,
                     "Scan the data= folders for loose-file (VFS) conflicts: the same relative path "
                     "(meshes/textures/scripts/...) provided by two or more mod folders. In OpenMW the "
                     "LATER data folder wins, so reorder the data-path panel to change the winner "
                     "(like MO2's Data conflicts). Read-only; can be slow on a big install.")

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(action_row, textvariable=self.status_var, foreground=DARK["fg_dim"]).grid(row=0, column=5, sticky="e")

    def _build_log(self, log_container):
        log_container.columnconfigure(0, weight=1)
        log_container.rowconfigure(0, weight=1)

        log_frame = ttk.LabelFrame(log_container, text="Log")
        log_frame.grid(row=0, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(
            log_frame, wrap="word", font=("TkFixedFont", 10), state="disabled",
            background="#141414", foreground=DARK["fg"], insertbackground=DARK["fg"],
            selectbackground=DARK["select"], relief="flat", borderwidth=0,
            highlightbackground=DARK["border"], highlightthickness=1,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        add_tooltip(self.log_text,
                     "Full output from the last Sort and/or Export. Colour key: green = a plugin/path "
                     "this sort inserted or moved, orange = a heads-up (mlox warning, or a rule your "
                     "curated cfg order overrode), blue = a section header, bright red = an error worth "
                     "checking. Plain text = frozen base rows left untouched.")
        for tag, cfg in self.LOG_TAGS.items():
            self.log_text.tag_configure(tag, **cfg)

        log_btns = ttk.Frame(log_frame)
        log_btns.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        clear_btn = ttk.Button(log_btns, text="Clear Log", command=self.clear_log)
        clear_btn.pack(side="left")
        add_tooltip(clear_btn, "Clear the log panel. Doesn't affect the order panels or any files.")
        save_btn = ttk.Button(log_btns, text="Save Log As...", command=self.save_log)
        save_btn.pack(side="left", padx=(8, 0))
        add_tooltip(save_btn, "Save the current log contents to a text file.")

    # -- log handling --------------------------------------------------------

    def _tag_for_line(self, line: str) -> str:
        stripped = line.strip()
        if stripped.startswith("=" * 5) or (stripped and stripped == stripped.upper()
                                             and any(c.isalpha() for c in stripped) and len(stripped) < 70
                                             and not stripped.startswith("[")):
            return "section"
        # [CONFLICT] and an internal drift warning are active problems worth
        # flagging in bright red; [REQUIRES]/generic WARNING:/NOTE: are
        # milder heads-ups and stay orange
        if "[CONFLICT]" in line or "INTERNAL WARNING" in line:
            return "error"
        if any(k in line for k in ("Traceback", "ERROR", "Error:")):
            return "error"
        if any(k in line for k in ("[REQUIRES]", "[NOTE]", "WARNING:", "NOTE:",
                                    "[REDUNDANT]", "[ORPHAN]", "[NEEDS CLEANING]", "[LIST ORDER]",
                                    # skipped-rule summary (mlox order overridden by the curated cfg)
                                    "ordering rule(s) not applied", "mlox wanted")):
            return "warn"
        if line.startswith("* ["):      # conflict report line involving your custom mods
            return "warn"
        if "<-- inserted" in line:      # 'content=X  <-- inserted/moved' / 'data=...  <-- inserted'
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
        path = filedialog.asksaveasfilename(defaultextension=".log",
                                             filetypes=(("Log files", "*.log"), ("All files", "*.*")))
        if not path:
            return
        Path(path).write_text(self.log_text.get("1.0", "end"), encoding="utf-8")

    # -- run -----------------------------------------------------------------

    def _on_toggle_inplace(self):
        inplace = self.write_toml_inplace_var.get()
        self.emit_toml_field.set_enabled(not inplace)

    def _validate(self) -> "types.SimpleNamespace | None":
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
                errors.append("'Write directly back to customizations.toml' requires a customizations.toml.")
            emit_toml = customizations  # overwrite the source file itself
        else:
            emit_toml = self.emit_toml_var.get().strip()

        if errors:
            messagebox.showerror("Missing input", "\n".join(f"- {e}" for e in errors))
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
            plugin_order_yml=(Path(self.plugin_order_yml_var.get().strip())
                              if self.plugin_order_yml_var.get().strip() else None),
            subset_lines=(self._scanned_subset_lines if has_mem_scan else None),
        )

    def on_scan_mods(self):
        """Scan a mods folder to build a subset. If 'Create subset text document'
        is checked, write it to a .txt you choose and load that file; otherwise
        keep the result in memory for this session only (no file written). Runs
        in a worker thread since a big tree can take a moment to walk."""
        if self.worker_running:
            return
        folder = filedialog.askdirectory(title="Select the mods folder to scan")
        if not folder:
            return
        make_doc = self.create_subset_doc_var.get()
        out = None
        if make_doc:
            out = filedialog.asksaveasfilename(
                title="Save generated subset file as", defaultextension=".txt",
                initialfile="mod_scan_results.txt",
                filetypes=(("Text files", "*.txt"), ("All files", "*.*")))
            if not out:
                return
        self.worker_running = True
        self.sort_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.status_var.set("Scanning mods folder...")
        threading.Thread(target=self._scan_worker, args=(folder, out), daemon=True).start()

    def _scan_worker(self, folder, out):
        writer = QueueWriter(self.log_queue)
        written, mem_lines = None, None
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                lines, n_folders, n_plugins = core.scan_mod_directories(folder, out)
            if out:
                written = out
                status = (f"Scan complete -- {n_folders} folder(s), {n_plugins} plugin(s). "
                          f"Subset file loaded.")
            else:
                mem_lines = lines
                status = (f"Scan complete -- {n_folders} folder(s), {n_plugins} plugin(s). "
                          f"Held in memory (no file written).")
        except Exception:
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
        self.status_var.set("Sorting...")

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
            status = (f"Sorted {n_plugins} plugin(s), {n_warn} rule warning(s){yml_bit}. "
                      f"Drag to adjust, then Export.")
        except SystemExit as e:
            writer.write(f"\nERROR: {e}\n")
            status = "Sort failed -- see log."
        except Exception:
            writer.write("\nERROR: unexpected exception:\n" + traceback.format_exc())
            status = "Sort failed -- see log."
        finally:
            self.root.after(0, self._sort_finished, plan, status)

    def _sort_finished(self, plan, status):
        self.worker_running = False
        self.sort_button.configure(state="normal")
        self.status_var.set(status)
        self._current_plan = plan
        # carry the previous opt-outs forward across a re-Sort
        prev_disabled_p = self.order_panel.get_disabled()
        prev_disabled_d = self.data_order_panel.get_disabled()
        final_order = (plan or {}).get("final_order") or []
        self.order_panel.load(final_order, (plan or {}).get("subset") or [],
                              disabled_items=prev_disabled_p)

        data_result = (plan or {}).get("data_result") or []
        data_lines = [line for line, _, _ in data_result]
        # Highlight every data= path that's OURS -- both genuinely new inserts
        # AND ones that are already in openmw.cfg because a prior
        # momw-configurator run baked them in (e.g. SetBonus/SkillFramework
        # added via the customizations TOML). Gating on is_new alone would leave
        # those un-highlighted even though they're part of what this sort
        # manages; matching them against this run's data-path inputs mirrors how
        # the plugin panel highlights all subset plugins, not just brand-new ones.
        user_norms = {core.normalize_data_path(d["value"])
                      for d in ((plan or {}).get("data_inserts") or [])}
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

    def on_export(self):
        if self.worker_running or not self._current_plan:
            return
        args = self._validate()  # re-read current write-related fields (write_cfg, emit_toml, dry_run, ...)
        if args is None:
            return

        if self.write_toml_inplace_var.get() and not args.dry_run:
            backup_note = "" if self.no_backup_var.get() else " (a .bak-<timestamp> copy will be made first)"
            if not messagebox.askyesno(
                "Overwrite customizations.toml?",
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
        self.status_var.set("Exporting...")

        thread = threading.Thread(
            target=self._export_worker,
            args=(args, final_order, data_order, disabled_plugins, disabled_data), daemon=True)
        thread.start()

    def _export_worker(self, args, final_order, data_order, disabled_plugins, disabled_data):
        writer = QueueWriter(self.log_queue)
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                result = core.write_plan(args, self._current_plan,
                                          final_order=final_order or None,
                                          data_order=data_order or None,
                                          disabled_plugins=disabled_plugins,
                                          disabled_data=disabled_data)
            status = (f"Export done -- cfg written: {'yes' if result['wrote_cfg'] else 'no'}, "
                      f"toml written: {'yes' if result['wrote_toml'] else 'no'}.")
        except SystemExit as e:
            writer.write(f"\nERROR: {e}\n")
            status = "Export failed -- see log."
        except Exception:
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
            self.log_queue.put(f"\n  Excluded {len(excl)} plugin(s) by your filter: "
                               + ", ".join(excl[:12]) + (" ..." if len(excl) > 12 else "") + "\n")
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
        self.status_var.set((f"Keeping tes3conv JSON dump in: {dest}" if keep
                             else f"tes3conv JSON dump ({dest}) will be removed on close."))

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
            s.keep = keep                        # same dump folder -> just track keep
            return s
        if s is not None:                        # engine path changed -> retire the old one
            try:
                s.cleanup()
            except Exception:
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
        data_order = self._current_plan.get("data_order") or []
        subset = self._current_plan.get("subset") or []
        self._keep_json = self.keep_json_var.get()
        self._conf_subset_lower = {str(s).lower() for s in subset}   # your custom mods
        self.worker_running = True
        self.sort_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.conflicts_button.configure(state="disabled")
        self.status_var.set("Scanning for conflicts...")
        threading.Thread(target=self._conflicts_worker,
                         args=(order, data_order, subset), daemon=True).start()

    def _conflicts_worker(self, order, data_order, subset):
        writer = QueueWriter(self.log_queue)
        conflicts, stats, session = [], {}, None
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                dirs = [d for d in (core.extract_data_path_value(l) for l in data_order) if d]
                index = core.PluginFileIndex(dirs)
                cfg_dir = str(Path(self.cfg_var.get().strip()).parent) if self.cfg_var.get().strip() else None
                conv = core.find_tes3conv(explicit=self._tes3conv_override, extra_dirs=[cfg_dir])
                session = self._get_session(conv)
                print("\n" + "=" * 70)
                print(" TES3 RECORD CONFLICTS (read-only)")
                print("=" * 70)
                if session:
                    print(f"  Engine: tes3conv ({conv}) -- field-level diffs available.")
                else:
                    print("  Engine: built-in parser (record-level). Point the Conflicts window at "
                          "a tes3conv binary for field-level diffs.")
                conflicts, stats = core.detect_conflicts(order, index, subset_names=subset, session=session)
                print(core.format_conflict_report(conflicts, stats, limit=200))
            n_sub = sum(1 for c in conflicts if c.get("involves_subset"))
            status = (f"Conflicts: {stats.get('conflicts', 0)} record(s), "
                      f"{n_sub} involving your mods. See the Conflicts window.")
        except Exception:
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
        data_order = self._current_plan.get("data_order") or []
        subset = self._current_plan.get("subset") or []
        self._keep_json = self.keep_json_var.get()
        self.worker_running = True
        for b in (self.sort_button, self.export_button, self.conflicts_button,
                  self.cellmap_button, self.resource_button):
            b.configure(state="disabled")
        self.status_var.set("Building cell map...")
        threading.Thread(target=self._cellmap_worker, args=(order, data_order, subset), daemon=True).start()

    def _cellmap_file(self):
        """Stable, user-findable, writable location for the generated map."""
        return app_base_dir() / "cell_map.html"

    def _cellmap_worker(self, order, data_order, subset):
        writer = QueueWriter(self.log_queue)
        path = None
        core.trace(f"cell map: start, {len(order)} plugin(s)")
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                dirs = [d for d in (core.extract_data_path_value(l) for l in data_order) if d]
                index = core.PluginFileIndex(dirs)
                cfg_dir = str(Path(self.cfg_var.get().strip()).parent) if self.cfg_var.get().strip() else None
                conv = core.find_tes3conv(explicit=self._tes3conv_override, extra_dirs=[cfg_dir])
                session = self._get_session(conv)
                print("\n" + "=" * 70)
                print(" CELL MAP")
                print("=" * 70)
                print(f"  Engine: {'tes3conv' if conv else 'built-in parser'}")
                cov = core.build_cell_coverage(order, index, subset_names=subset, session=session)
                core.trace(f"cell map: coverage built, {len(cov['exterior'])} ext, {len(cov['interior'])} int")
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
                print(f"  {len(cov['exterior'])} exterior + {len(cov['interior'])} interior cell(s) "
                      f"touched across {cov['scanned']} plugin(s).")
                print(f"  Map written to: {path}")
            status = f"Cell map ready ({len(cov['exterior'])} exterior, {len(cov['interior'])} interior)."
        except Exception:
            writer.write("\nERROR: cell map failed:\n" + traceback.format_exc())
            status = "Cell map failed -- see log."
        finally:
            self.root.after(0, self._cellmap_finished, path, status)

    def _cellmap_finished(self, path, status):
        self.worker_running = False
        self.sort_button.configure(state="normal")
        for b in (self.export_button, self.conflicts_button, self.cellmap_button, self.resource_button):
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
            core.trace("cell map: viewer = browser (forced)"); self._open_cell_map_browser(); return
        if force == "tkinterweb" and can_tkweb:
            core.trace("cell map: viewer = tkinterweb (forced)"); self._show_cell_map_window(path); return
        if force == "pywebview" and HAVE_PYWEBVIEW:
            core.trace("cell map: viewer = pywebview (forced)"); self._open_cell_map_pywebview(path); return
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
            self.status_var.set(status + "  (opened in browser — pip install pywebview "
                                         "for an in-app window)")

    def _open_cell_map_pywebview(self, path):
        """Show the map in an embedded OS webview by re-invoking ourselves with
        --show-map in a SEPARATE process (webview.start() needs its own main
        thread). Frozen-safe: a built .exe re-runs the .exe; from source we re-run
        the script -- never 'python -c', which a frozen exe can't do."""
        ap = os.path.abspath(path)
        # IMPORTANT: only CREATE_NO_WINDOW here (suppresses a console flash) -- do
        # NOT use the SW_HIDE startupinfo from _no_window_kwargs(): that STARTUPINFO
        # is inherited by the child's FIRST window, which would hide the WebView2
        # cell-map window itself (it spawns but never shows). That was the bug.
        nw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--show-map", ap]
            else:
                cmd = [sys.executable, os.path.abspath(__file__), "--show-map", ap]
            core.trace(f"cell map: launching pywebview child: {cmd}")
            subprocess.Popen(cmd, **nw)
        except Exception:
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
        ttk.Button(bar, text="Save HTML...", command=self._save_cell_map).pack(side="left")
        ttk.Button(bar, text="Open in browser", command=self._open_cell_map_browser).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")
        try:
            viewer = HTMLViewer(win)
            viewer.pack(fill="both", expand=True)
            viewer.load_file(path)   # reads from disk, not an in-memory string
        except Exception:
            ttk.Label(win, foreground="#ffb454", padding=8,
                      text="(inline render failed — use 'Open in browser' for the full map)").pack(anchor="w")

    def _open_cell_map_browser(self):
        p = getattr(self, "_last_cell_file", None)
        if not p or not os.path.exists(p):
            return
        try:
            webbrowser.open(Path(p).resolve().as_uri())   # correct file URI on Win/Linux/macOS
        except Exception:
            pass

    def _save_cell_map(self):
        src = getattr(self, "_last_cell_file", None)
        if not src or not os.path.exists(src):
            return
        out = filedialog.asksaveasfilename(title="Save cell map", defaultextension=".html",
                                           initialfile="cell_map.html",
                                           filetypes=(("HTML files", "*.html"), ("All files", "*.*")))
        if not out:
            return
        try:
            import shutil
            if os.path.abspath(out) != os.path.abspath(src):
                shutil.copyfile(src, out)
            self.status_var.set(f"Cell map saved: {out}")
        except OSError as e:
            messagebox.showerror("Save failed", str(e))

    # -- resource (VFS) conflicts --------------------------------------------

    def on_resource_conflicts(self):
        if self.worker_running or not self._current_plan:
            return
        data_order = self._current_plan.get("data_order") or []
        dirs = [d for d in (core.extract_data_path_value(l) for l in data_order) if d]
        if not dirs:
            self.status_var.set("No data= folders to scan.")
            return
        subset_dirs = [d.get("value") for d in (self._current_plan.get("data_inserts") or [])]
        self.worker_running = True
        for b in (self.sort_button, self.export_button, self.conflicts_button,
                  self.cellmap_button, self.resource_button):
            b.configure(state="disabled")
        self.status_var.set("Scanning data folders for file conflicts...")
        threading.Thread(target=self._resource_worker, args=(dirs, subset_dirs), daemon=True).start()

    def _resource_worker(self, dirs, subset_dirs):
        writer = QueueWriter(self.log_queue)
        conflicts, stats = [], {}
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                print("\n" + "=" * 70)
                print(" DATA-PATH RESOURCE (VFS) CONFLICTS")
                print("=" * 70)
                conflicts, stats = core.detect_resource_conflicts(dirs, subset_dirs=subset_dirs)
                print(core.format_resource_report(conflicts, stats, limit=200))
            status = f"Resource conflicts: {stats.get('conflicts', 0)} file(s). See the window."
        except Exception:
            writer.write("\nERROR: resource scan failed:\n" + traceback.format_exc())
            status = "Resource scan failed -- see log."
        finally:
            self.root.after(0, self._resource_finished, conflicts, stats, status)

    def _resource_finished(self, conflicts, stats, status):
        self.worker_running = False
        self.sort_button.configure(state="normal")
        for b in (self.export_button, self.conflicts_button, self.cellmap_button, self.resource_button):
            b.configure(state="normal" if self._current_plan else "disabled")
        self.status_var.set(status)
        self._show_resource_window(conflicts, stats)

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
        ttk.Label(top, text=(f"{stats.get('conflicts', 0)} loose-file conflict(s) across "
                             f"{stats.get('dirs', 0)} folder(s), {stats.get('files', 0)} file(s) — "
                             f"{n_sub} involve your custom data paths (★). Later folder wins — reorder "
                             f"the data-path panel to change it.")).pack(side="left")
        self._res_subset_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Only my paths", variable=self._res_subset_only,
                        command=self._refill_res_tree).pack(side="right")
        # tree (top) and the detail panel (bottom) live in a draggable vertical
        # split, so the detail box can be resized -- grab the grip to grow it.
        body = self._paned(win, "vertical")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        mid = ttk.Frame(body)
        cols = ("custom", "path", "count", "winner")
        tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse", style="Conf.Treeview")
        for c, txt, w in (("custom", "★", 34), ("path", "File", 520), ("count", "#", 50),
                          ("winner", "Winner (loads last)", 280)):
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
        detail = tk.Text(detbox, height=5, wrap="word", background="#141414", foreground=DARK["fg"],
                         relief="flat", highlightthickness=1, highlightbackground=DARK["border"])
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
            txt = (f"{c['path']}\n"
                   + "\n".join(f"  {i + 1}. {p}" for i, p in enumerate(c["providers"]))
                   + f"\nWins: {c['winner']}")
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            detail.insert("1.0", txt)
            detail.configure(state="disabled")
        tree.bind("<<TreeviewSelect>>", on_sel)
        btns = ttk.Frame(win, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="Save report (CSV)...", command=self._save_resource_csv).pack(side="left")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")
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
            tree.insert("", "end", iid=str(i), tags=tags,
                        values=(star, c["path"], len(c["providers"]), c["winner"]))

    def _save_resource_csv(self):
        if not getattr(self, "_all_res", None):
            return
        path = filedialog.asksaveasfilename(title="Save resource conflicts", defaultextension=".csv",
                                            initialfile="resource_conflicts.csv",
                                            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")))
        if not path:
            return
        try:
            core.write_resource_csv(path, self._all_res)
            self.status_var.set(f"Saved: {path}")
        except OSError as e:
            messagebox.showerror("Save failed", str(e))

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
        ttk.Label(top, text=(f"{stats.get('conflicts', 0)} conflicting record(s) across "
                             f"{stats.get('scanned', 0)} plugin(s) — {n_sub} involve your custom mods "
                             f"(★). Winner = last loaded.")).pack(side="left")
        self._conf_subset_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Only my mods", variable=self._conf_subset_only,
                        command=self._refill_conflict_tree).pack(side="right")

        engine = (stats or {}).get("engine", "builtin")
        bar = ttk.Frame(win, padding=(8, 0))
        bar.pack(fill="x")
        ttk.Label(bar, foreground=(DARK["fg_dim"] if engine == "tes3conv" else "#ffb454"),
                  text=("Field-level diffs: ON (tes3conv)." if engine == "tes3conv"
                        else "Field-level diffs: OFF — record-level only. Set a tes3conv binary, then re-check.")
                  ).pack(side="left")
        ttk.Button(bar, text="Set tes3conv...", command=self._set_tes3conv).pack(side="left", padx=(8, 0))

        panes = tk.PanedWindow(win, orient="vertical", bg=DARK["bg"], bd=0, sashwidth=6,
                               sashrelief="flat", background=DARK["border"])
        panes.pack(fill="both", expand=True, padx=8, pady=6)

        # --- conflicts table ---
        topf = ttk.Frame(panes)
        cols = ("custom", "type", "id", "count", "winner")
        tree = ttk.Treeview(topf, columns=cols, show="headings", selectmode="browse", style="Conf.Treeview")
        for c, txt, w in (("custom", "★", 34), ("type", "Type", 90), ("id", "Record", 380),
                          ("count", "#", 40), ("winner", "Winner (loads last)", 280)):
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
        ttk.Label(botf, foreground=DARK["fg_dim"],
                  text="Field comparison for the selected record — differing fields in red · "
                       "★ = your custom mod · last column wins · double-click a field for the full "
                       "value:").grid(row=0, column=0, columnspan=2, sticky="w", pady=(2, 2))
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
        self._conf_ftree = ftree
        panes.add(botf, minsize=120, stretch="always")

        tree.bind("<<TreeviewSelect>>", lambda _e: self._on_conflict_select())

        btns = ttk.Frame(win, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="Save report (CSV)...", command=self._save_conflicts_csv).pack(side="left")
        if self._conf_session is not None:
            ttk.Button(btns, text="Dump tes3conv JSON...", command=self._dump_conflict_json).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")

        self._refill_conflict_tree()

    def _dump_conflict_json(self):
        """Write the (in-memory) tes3conv JSON for every scanned plugin to a
        folder you pick."""
        if self._conf_session is None or not self._conf_paths:
            return
        folder = filedialog.askdirectory(title="Dump tes3conv JSON to folder")
        if not folder:
            return
        try:
            n = core.dump_tes3conv_json(self._conf_session, list(self._conf_paths.keys()),
                                        self._conf_paths, folder)
            self.status_var.set(f"Wrote {n} JSON file(s) to {folder}")
            if n:
                messagebox.showinfo("JSON dumped", f"Wrote {n} tes3conv JSON file(s) to:\n{folder}")
            else:
                messagebox.showwarning("Nothing written",
                    "No JSON was written. The tes3conv session may have been cleared — "
                    "re-run Check Conflicts, then dump again.")
        except Exception as e:
            messagebox.showerror("Dump failed", str(e))

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
            star = "★ " if self._is_custom(p) else ""       # ★ marks your custom mods
            suffix = "  (wins)" if i == len(plugins) - 1 else ""
            ftree.heading(f"p{i}", text=f"{star}{p}{suffix}")
            ftree.column(f"p{i}", width=210, anchor="w", stretch=True)
        ftree.delete(*ftree.get_children())
        if self._conf_session is None:
            ftree.insert("", "end", values=["(set a tes3conv binary for field-level diffs)"] + [""] * len(plugins))
            return
        try:
            keys, per, diff = core.diff_record_fields(self._conf_session, conflict, self._conf_paths)
        except Exception:
            ftree.insert("", "end", values=["(field diff unavailable)"] + [""] * len(plugins))
            return
        self._conf_fdiff = {"plugins": plugins, "per": per}   # for the expand popup
        for k in keys:
            row = [k] + [self._fmt_val(per[p].get(k)) for p in plugins]
            ftree.insert("", "end", iid=k, values=row, tags=("diff",) if k in diff else ())
        if not keys:
            ftree.insert("", "end", values=["(no fields / identical)"] + [""] * len(plugins))

    def _show_field_detail(self):
        """Popup with the full value of the selected field for each plugin --
        one tab per plugin, pretty-printed. For long fields like 'references'
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
        win = tk.Toplevel(self.root)
        win.title(f"Field: {key}")
        win.configure(bg=DARK["bg"])
        win.geometry("820x520")
        ttk.Label(win, text=f"{key}   (last plugin wins · ★ orange = your custom mod)",
                  padding=8).pack(anchor="w")
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
        ttk.Checkbutton(bar, text="Word wrap", variable=wrap_var, command=_apply_wrap).pack(side="left")
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        import json as _json
        CUST = "#ff9b6b"
        for i, p in enumerate(plugins):
            cust = self._is_custom(p)
            val = per[p].get(key, None)
            try:
                text = _json.dumps(val, indent=2, ensure_ascii=False, default=str)
            except Exception:
                text = repr(val)
            frame = ttk.Frame(nb)
            # colored per-plugin header inside the tab: orange = your custom mod
            ttk.Label(frame, text=(("★ " if cust else "") + p
                                   + ("   — your custom mod" if cust else "   — curated list")
                                   + ("   ✓ wins" if i == len(plugins) - 1 else "")),
                      foreground=(CUST if cust else "#9a9a9a"), padding=(4, 4)).pack(anchor="w")
            st = scrolledtext.ScrolledText(frame, wrap="word", font=("TkFixedFont", 10),
                                           background="#141414", foreground=DARK["fg"],
                                           insertbackground=DARK["fg"], relief="flat",
                                           highlightthickness=1, highlightbackground=DARK["border"])
            st.pack(fill="both", expand=True)
            st.insert("1.0", text if val is not None else "(field not present in this plugin)")
            st.configure(state="disabled")
            texts.append(st)
            label = (p[:22] + "…") if len(p) > 24 else p
            tab = ("★ " if cust else "") + label + (" ✓" if i == len(plugins) - 1 else "")
            nb.add(frame, text=tab)
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 8))

    def _set_tes3conv(self):
        p = filedialog.askopenfilename(title="Locate the tes3conv executable",
                                       filetypes=(("All files", "*.*"),))
        if not p:
            return
        self._tes3conv_override = p
        self.status_var.set("tes3conv set — click 'Check Conflicts' again to re-scan with field diffs.")
        messagebox.showinfo("tes3conv set",
                            "tes3conv location saved.\n\nClick 'Check Conflicts' again to re-scan; the "
                            "field comparison will then populate when you select a record.")

    def _refill_conflict_tree(self):
        tree = getattr(self, "_conf_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        only = self._conf_subset_only.get()
        self._shown_conflicts = [c for c in self._all_conflicts
                                 if c.get("involves_subset") or not only]
        tree.delete(*tree.get_children())
        for i, c in enumerate(self._shown_conflicts):
            star = "★" if c["involves_subset"] else ""
            tags = ("sub",) if c["involves_subset"] else ()
            tree.insert("", "end", iid=str(i), tags=tags,
                        values=(star, c["type"], c["id"], len(c["plugins"]), c["winner"]))

    def _save_conflicts_csv(self):
        if not getattr(self, "_all_conflicts", None):
            return
        path = filedialog.asksaveasfilename(
            title="Save conflict report", defaultextension=".csv",
            initialfile="tes3_conflicts.csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")))
        if not path:
            return
        try:
            core.write_conflict_csv(path, self._all_conflicts)
            self.status_var.set(f"Conflict report saved: {path}")
        except OSError as e:
            messagebox.showerror("Save failed", str(e))


def _run_pywebview_window(path):
    """Open one cell-map file in an OS webview and block until closed. Invoked in a
    child process (see _open_cell_map_pywebview) so webview.start() owns its own
    main thread, cleanly, without disturbing the tkinter app. Always writes its
    outcome to cell_map_viewer.log next to the app so a failed backend (e.g.
    pywebview's WebView2/pythonnet backend on an unsupported Python) is visible
    instead of silently falling back to the browser."""
    try:
        logf = str(app_base_dir() / "cell_map_viewer.log")
    except Exception:
        logf = None

    def _log(msg):
        if not logf:
            return
        try:
            from datetime import datetime as _dt
            with open(logf, "a", encoding="utf-8") as fh:
                fh.write(f"{_dt.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
        except Exception:
            pass

    try:
        import webview
        _log(f"pywebview {getattr(webview, '__version__', '?')}: opening {path}")
        webview.create_window("Cell Map", Path(path).resolve().as_uri(), width=1050, height=760)
        webview.start()
        _log("pywebview: window closed cleanly")
    except Exception:
        import traceback as _tb
        _log("pywebview FAILED -- falling back to browser:\n" + _tb.format_exc())
        try:
            webbrowser.open(Path(path).resolve().as_uri())
        except Exception:
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
    ap.add_argument("--trace", nargs="?", const=True, default=None, metavar="LOGFILE",
                    help="Write a debug trace log for troubleshooting. Off by default. "
                         "Pass --trace for the default log (mlox_subset_sort_trace.log next "
                         "to the app), or --trace PATH to choose the file.")
    args, _unknown = ap.parse_known_args()
    global _TRACE_REQUEST
    _TRACE_REQUEST = args.trace
    root = TkinterDnD.Tk() if HAVE_DND else tk.Tk()
    apply_dark_theme(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
