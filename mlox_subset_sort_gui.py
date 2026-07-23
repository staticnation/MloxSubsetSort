#!/usr/bin/env python3
"""mlox_subset_sort_gui.py.

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

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import traceback
import types
import webbrowser
from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, ClassVar

# Compiled-script disassembly for the field-diff window. Optional: if the
# package is missing the diff view still works, it just shows the raw base64
# blob it always did.
listing_for_bytecode_field: Callable[..., str] | None
variables_text_for_field: Callable[..., str] | None
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
HTMLViewer: Any = None
try:
    from tkinterweb import HtmlFrame as _HtmlFrame

    HTMLViewer = _HtmlFrame  # supports load_file + SVG
except Exception:  # noqa: BLE001
    # optional 3rd-party import; a broken install must not kill startup
    try:
        from tkhtmlview import HTMLScrolledText as _HTMLScrolledText

        HTMLViewer = _HTMLScrolledText
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


_TRACE_REQUEST = None  # set by main() from --trace; None = use env var / off

# app_base_dir() moved to mlox_subset/gui/__init__.py in the 3.0 split; it is
# re-imported below (after the sys.path fix-up) so every call site here and in
# the mixins resolves the same cached answer.

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
from mlox_subset import _, ngettext  # noqa: E402
from mlox_subset.configurator import (  # noqa: E402
    extract_data_path_value,
    normalize_data_path,
)

# ---------------------------------------------------------------------------
# GUI support package (split out per CODE_REVIEW.md §16/§9.2, 3.0). Bodies
# moved verbatim; every name is re-imported here so the rest of this module --
# and the smoke-test instructions that reference these names -- are unchanged.
# Imported after the sys.path fix-up, like the gettext marker above.
# ---------------------------------------------------------------------------
from mlox_subset.gui import (  # noqa: E402
    DND_FILES,
    HAVE_DND,
    TkinterDnD,
    app_base_dir,
    trace_first_fire,
)
from mlox_subset.gui.conflicts import ConflictWindowsMixin  # noqa: E402
from mlox_subset.gui.t3 import Tes3cmdMixin  # noqa: E402
from mlox_subset.gui.theme import (  # noqa: E402
    _THEME_REQUIRED,
    DARK,
    THEME_PRESETS,
    _mix_hex,
    _normalize_hex,
    apply_dark_theme,
    parse_theme_file,
    restyle_widget_tree,
    set_active_chrome,
    style_plain_widget,
)
from mlox_subset.gui.widgets import (  # noqa: E402
    DragReorderListbox,
    PathField,
    QueueWriter,
    add_tooltip,
    attach_typeahead,
)
from mlox_subset.net import (  # noqa: E402
    PLUGIN_ORDER_URLS,
    RULES_URL_TEMPLATE,
    rule_file_ages,
    update_plugin_order_yml,
    update_rule_files,
)
from mlox_subset.plugins import PluginFileIndex  # noqa: E402
from mlox_subset.rules import ORDER_NAME_RE  # noqa: E402

# The conflict explorer, built beside the cell map. Optional like every other
# extra: without it the cell map renders exactly as it always has.
build_cell_pages: Callable[..., dict] | None
build_explorer: Callable[..., str] | None
collect_detail: Callable[..., dict] | None
collect_world_terrain: Callable[..., dict] | None
try:
    from mlox_subset.viz import (
        build_explorer,
        collect_detail,
        collect_world_terrain,
        sidecar as viz_sidecar,
    )
except ImportError:  # pragma: no cover - only when viz/ is absent
    build_cell_pages = None
    build_explorer = None
    collect_detail = None
    collect_world_terrain = None
    viz_assets = None
    viz_sidecar = None  # type: ignore[assignment]
from mlox_subset.tracing import set_trace_file, trace  # noqa: E402


def _app_version() -> str:
    """Return the running build's version string, or ``?`` if unknown."""
    try:
        from mlox_subset import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        # a version stamp must never be the thing that stops the app starting
        return "?"


def _build_stamp() -> str:
    """Identify which build is running: frozen exe vs source, plus its mtime.

    Exists because a stale .exe presents exactly like a code bug -- new source
    on disk, old behaviour on screen, and nothing in the log to tell them
    apart. Comparing this timestamp against the source tree settles it.
    """
    from datetime import datetime as _dt

    frozen = bool(getattr(sys, "frozen", False))
    target = Path(sys.executable) if frozen else Path(__file__)
    try:
        # Local clock: the build stamp is compared by eye against the user's
        # own file timestamps in Explorer.
        mtime = _dt.fromtimestamp(target.stat().st_mtime).strftime(  # noqa: DTZ006
            "%Y-%m-%d %H:%M:%S"
        )
    except OSError:
        mtime = "?"
    return f"frozen={frozen} built={mtime} path={target.name}"


# ---------------------------------------------------------------------------
# rule-file list: an ordered listbox (priority = order, last = highest,
# matching mlox_subset_sort's own --rules semantics) with add/remove/reorder
# controls and its own drop target
# ---------------------------------------------------------------------------


class RuleFilesPanel:
    """The mlox rule-file list: add, remove, reorder, and update from upstream."""

    def __init__(
        self,
        parent: tk.Misc,
        row: int,
        on_new_rule: Callable[[], None] | None = None,
        on_sources: Callable[[], None] | None = None,
        get_rules_url: Callable[[], str] | None = None,
    ) -> None:
        """Build the rule-file panel inside ``parent`` at grid ``row``."""
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
            _(
                "mlox rule files (mlox_base.txt, mlox_user.txt, ...), applied in this order.\n"
                "Later files can override/extend earlier ones -- put mlox_base.txt first and "
                "your own mlox_user.txt last. Drag rows to reorder, or use the buttons."
            ),
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
                _(
                    "Write your own mlox [Order]/[NearStart]/[NearEnd] rule without knowing "
                    "the syntax: pick plugins (or grab the selected rows from the plugin "
                    "panel), preview the rule, and append it to a personal rules file that "
                    "loads LAST so it wins conflicts. This is how rules for modern mods get "
                    "made -- consider contributing good ones upstream."
                ),
            )
        upd_btn = ttk.Button(actions, text=_("Update Rules..."), command=self._update_rules)
        upd_btn.pack(side="left", padx=(0, 6))
        add_tooltip(
            upd_btn,
            _(
                "Download the CURRENT mlox_base.txt / mlox_user.txt from the actively "
                "maintained rules repo (github.com/%(repo)s -- the same source "
                "plox uses, and mlox 1.1+ auto-updates from) over the matching files in "
                "this list. The old files are kept as timestamped .bak copies. Files "
                "with other names (your personal rules) are never touched. Source URL "
                "configurable via Sources..."
            )
            % {"repo": core.RULES_REPO},
        )
        if on_sources is not None:
            src_btn = ttk.Button(actions, text=_("Sources..."), command=on_sources)
            src_btn.pack(side="left", padx=(0, 6))
            add_tooltip(
                src_btn,
                _(
                    "Configure WHERE 'Update Rules...' and the plugin-order.yml "
                    "'Update...' button download from -- point them at a fork or "
                    "mirror if upstream moves. Blank fields use the built-in defaults."
                ),
            )

        if HAVE_DND:
            # tkinterdnd2 adds these to the widget at runtime.
            self.listbox.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self.listbox.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
        else:
            ttk.Label(
                actions,
                text=_("(install tkinterdnd2 to drag files in from your file manager)"),
                foreground=DARK["fg_dim"],
            ).pack(side="left", padx=(12, 0))

    def _update_rules(self) -> None:
        paths = self.get_paths()
        managed = [p for p in paths if Path(p).name.lower() in ("mlox_base.txt", "mlox_user.txt")]
        if not managed:
            messagebox.showinfo(
                _("Update rules"), _("Add mlox_base.txt and/or mlox_user.txt to the list first.")
            )
            return
        ages = rule_file_ages(managed)
        age_txt = "\n".join(
            f"  {n}: {'age unknown' if d is None else f'~{d} day(s) old'}" for n, d in ages
        )
        if not messagebox.askyesno(
            _("Update rules"),
            _(
                "Download the current rules from github.com/%(repo)s over these "
                "files?\n\n%(age)s\n\nTimestamped .bak copies of the old files are kept."
            )
            % {"repo": core.RULES_REPO, "age": age_txt},
        ):
            return

        custom = (self._get_rules_url() if self._get_rules_url else "") or None

        def work() -> None:
            try:
                report = update_rule_files(managed, url_template=custom)
            except Exception as e:  # noqa: BLE001
                # worker thread: must report into the dialog, never vanish silently
                report = [f"FAILED: {e}"]
            self.listbox.after(0, lambda: messagebox.showinfo(_("Update rules"), "\n".join(report)))

        threading.Thread(target=work, daemon=True).start()

    # Any: tkinterdnd2 synthesises its own event object, which has no published
    # type -- only the `.data` string this reads.
    def _on_drop(self, event: Any) -> None:  # noqa: ANN401
        for p in self.listbox.tk.splitlist(event.data):
            self.listbox.insert("end", p)

    def add_files(self) -> None:
        """Prompt for rule files and append them to the list."""
        paths = filedialog.askopenfilenames(
            filetypes=(("mlox rule files", "*.txt"), ("All files", "*.*"))
        )
        for p in paths:
            self.listbox.insert("end", p)

    def remove_selected(self) -> None:
        """Remove the selected rule files from the list."""
        for idx in reversed(self.listbox.curselection()):
            self.listbox.delete(idx)

    def move(self, direction: int) -> None:
        """Move the selection one row in ``direction`` (-1 up, +1 down)."""
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

    def get_paths(self) -> list[Path]:
        """Return the rule files, in priority order."""
        return [Path(p) for p in self.listbox.get(0, "end")]


class ReorderPanel:
    """A titled drag-reorderable list with Move Up/Down, Reset and disable support."""

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

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        reset_label: str = "Reset to Computed Order",
        listbox_tooltip: str | None = None,
    ) -> None:
        """Build a titled reorderable panel inside ``parent``."""
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
            feedback=lambda buf, _f=frame, _t=title: _f.configure(  # type: ignore[misc]
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
            _(
                "Move the selected row(s) one position earlier. Works with a multi-"
                "selection; you can also drag a contiguous block up with the mouse."
            ),
        )
        down_btn = ttk.Button(btns, text=_("Move Down"), command=lambda: self.move(1))
        down_btn.pack(fill="x", pady=2)
        add_tooltip(
            down_btn,
            _(
                "Move the selected row(s) one position later. Works with a multi-"
                "selection; you can also drag a contiguous block down with the mouse."
            ),
        )
        toggle_btn = ttk.Button(btns, text=_("Disable / Enable"), command=self.toggle_selected)
        toggle_btn.pack(fill="x", pady=(10, 2))
        add_tooltip(
            toggle_btn,
            _(
                "Opt the selected row in or out of the load order. Disabled rows are dimmed and "
                "marked, and are left out of Export: a custom item is simply not inserted, and an "
                "item already in your openmw.cfg gets a removeContent/removeData entry in the "
                "emitted TOML so it's durably removed. Double-click a row to toggle it too."
            ),
        )
        reset_btn = ttk.Button(btns, text=reset_label, command=self.reset)
        reset_btn.pack(fill="x", pady=(2, 2))
        add_tooltip(
            reset_btn,
            _(
                "Discard any manual dragging and restore the order from the last Sort "
                "(your disable/enable choices are kept)."
            ),
        )

        self._original_order: list[str] = []
        self._highlight_lower: set[str] = set()
        # rows flagged with an active problem (bright red)
        self._error_lower: set[str] = set()
        self._disabled: set[str] = set()  # real item texts the user has opted out

    def load(
        self,
        items: Sequence[str],
        highlighted_items: Collection[str] = (),
        disabled_items: Collection[str] = (),
    ) -> None:
        """Populate the list after a successful Sort.

        Remembers
        it (for Reset), which items render highlighted, and which are disabled.
        disabled_items lets a re-Sort carry the previous opt-outs forward for
        any item still present.
        """
        self._original_order = list(items)
        self._highlight_lower = {str(x).lower() for x in highlighted_items}
        self._error_lower = set()
        present = set(items)
        self._disabled = {str(d) for d in disabled_items if str(d) in present}
        self._refill(self._original_order)

    def set_errors(self, items: Collection[str]) -> None:
        """Flag rows with an active problem (e.g.

        a missing master) -- they render bright red until the next load().
        """
        self._error_lower = {str(x).lower() for x in (items or ())}
        self._restyle()

    def reset(self) -> None:
        """Restore the computed order, discarding manual edits."""
        self._refill(self._original_order)

    def _display(self, real: str) -> str:
        return self.DISABLE_PREFIX + real if real in self._disabled else real

    def _strip(self, display: str) -> str:
        if display.startswith(self.DISABLE_PREFIX):
            return display[len(self.DISABLE_PREFIX) :]
        return display

    def _refill(self, items: Sequence[str]) -> None:
        self.listbox.delete(0, "end")
        for real in items:
            self.listbox.insert("end", self._display(real))
        self._restyle()

    def _restyle(self) -> None:
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

    def _on_double(self, _event: tk.Event) -> str:
        self.toggle_selected()
        return "break"

    def toggle_selected(self) -> None:
        """Opt the selected row(s) in/out.

        With several rows selected: if any is currently enabled, disable them all;
        otherwise enable them all (so a bulk click has one predictable outcome). A
        single row just flips.
        """
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

    def move(self, direction: int) -> None:
        """Move all selected rows one step up or down.

        Direction < 0 is up, > 0 is down;
        together, preserving their order and selection. Blocked if the leading
        selected row is already at the edge.
        """
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

    def get_order(self) -> list[str]:
        """All rows, in current order, real text (opt-out marker stripped)."""
        return [self._strip(x) for x in self.listbox.get(0, "end")]

    def get_enabled(self) -> list[str]:
        """Only the rows that are still enabled, in order."""
        return [r for r in self.get_order() if r not in self._disabled]

    def get_disabled(self) -> set[str]:
        """Return the rows the user opted out of."""
        return set(self._disabled)

    def has_order(self) -> bool:
        """Report whether the panel currently holds an order."""
        return self.listbox.size() > 0


class PluginOrderPanel(ReorderPanel):
    """The plugin load-order panel."""

    def __init__(self, parent: tk.Misc) -> None:
        """Build the panel inside ``parent``."""
        super().__init__(
            parent,
            title=_(
                "Plugin load order -- drag to override mlox (red = touched by this sort, "
                "purple = master problem)"
            ),
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
    """Same idea as PluginOrderPanel but for data= folder paths.

    Only populated when a Sort was run with 'Sort data= paths too' checked -- otherwise
    stays empty, since there's nothing computed to show or override (see
    App._sort_finished).
    """

    def __init__(self, parent: tk.Misc) -> None:
        """Build the panel inside ``parent``."""
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


class App(Tes3cmdMixin, ConflictWindowsMixin):
    """The main application window."""

    # Colors for the log panel's tags now come from the selected syntax
    # highlighting theme (THEME_PRESETS / imported custom themes) rather than
    # a fixed palette -- see _log_tag_style, _apply_log_theme. section/error
    # stay bold across every theme since that's the thing you most need to
    # be able to spot at a glance; everything else is a plain-weight color.

    @staticmethod
    def _log_tag_style(theme: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
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

    def __init__(self, root: tk.Tk) -> None:
        """Build the whole application UI on ``root``."""
        self.root = root
        root.title("MLOX Subset Sort")
        root.geometry("1320x820")
        root.minsize(1000, 620)

        self.log_queue = queue.Queue()
        self.worker_running = False
        self._log_group_tag: str | None = None
        self._current_plan = None
        # in-memory scan result (when not saved to a file)
        self._scanned_subset_lines: list[str] | None = None
        self._tes3conv_override = None  # user-set path to tes3conv (for field-level diffs)
        self._tes3cmd_override = None  # user-set path to tes3cmd (frontend window)
        self._conf_session = None
        self._conf_paths = {}
        # reused disk-backed Tes3ConvSession
        self._session: core.Tes3ConvSession | None = None
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
                    path: str | Path = req
                else:
                    path = app_base_dir() / "mlox_subset_sort_trace.log"
                set_trace_file(path)
                trace("GUI started")
                # Build stamp, first thing after the header. A frozen .exe is
                # easy to rebuild-and-forget, and a stale one is otherwise
                # indistinguishable from a code bug: you get the old behaviour
                # with the new source sitting right there. Version + the source
                # file's mtime pin exactly which build is running.
                trace(f"build: version={_app_version()} {_build_stamp()}")
                trace(
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

        self._custom_log_themes: dict[str, dict] = {}  # name -> theme dict, imported by the user
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

    def _settings_file(self) -> Path:
        return app_base_dir() / "mlox_subset_sort_settings.json"

    def _saved_log_theme_name(self) -> str | None:
        """Just the saved theme name, readable before any widget exists."""
        try:
            d = json.loads(self._settings_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):  # unreadable file / bad JSON
            return None
        name = d.get("log_theme")
        return name if isinstance(name, str) else None

    def _gather_settings(self) -> dict[str, Any]:
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

    def _load_settings(self) -> None:
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
        for k, bvar in (  # BooleanVars; `var` above was the StringVar loop
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
                bvar.set(d[k])
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

    def _save_settings(self) -> None:
        import json

        try:
            self._settings_file().write_text(
                json.dumps(self._gather_settings(), indent=2), encoding="utf-8"
            )
        except (OSError, TypeError, ValueError):  # unwritable path / unserialisable value
            pass

    def _on_close(self) -> None:
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

    def _paned(self, parent: tk.Misc, orient: str) -> tk.PanedWindow:
        """Build a themed tk.PanedWindow with a draggable grip.

        Plain tk, not ttk (ttk's has no visible grip), styled to
        match the dark theme. The default square handle is turned off; a
        hamburger-style grip is overlaid instead by _attach_hamburger_grip().
        """
        return tk.PanedWindow(
            parent,
            orient=orient,  # type: ignore[arg-type]
            sashwidth=8,
            sashrelief="flat",
            showhandle=False,
            bg=DARK["bg"],
            bd=0,
            background=DARK["border"],
            sashpad=0,
        )

    def _attach_hamburger_grip(self, paned: tk.Misc, orient: str) -> None:
        """Overlay a hamburger-style (three-line) draggable grip on a sash.

        Centred on the
        single sash of a two-pane PanedWindow. Cross-platform: cursor names are
        tried in order and any failure is ignored, and if the sash geometry
        can't be read the grip just hides itself -- the sash stays draggable
        either way, so this is purely a nicer-looking handle, never load-bearing.
        """
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

        def reposition(_event: tk.Event | None = None) -> None:
            try:
                x, y = paned.sash_coord(0)  # type: ignore[attr-defined]
                half = int(paned.cget("sashwidth")) // 2
            except (tk.TclError, IndexError, TypeError, ValueError):
                grip.place_forget()
                return
            if horizontal:
                grip.place(x=x + half, rely=0.5, anchor="center")
            else:
                grip.place(relx=0.5, y=y + half, anchor="center")

        def on_drag(event: tk.Event) -> None:
            try:
                if horizontal:
                    paned.sash_place(  # type: ignore[attr-defined]
                        0, max(1, event.x_root - paned.winfo_rootx()), 1
                    )
                else:
                    paned.sash_place(  # type: ignore[attr-defined]
                        0, 1, max(1, event.y_root - paned.winfo_rooty())
                    )
            except tk.TclError:
                pass
            reposition()

        def reposition_soon(_event: tk.Event | None = None) -> None:
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

    def _build_widgets(self) -> None:
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

    def _build_controls(self, top: tk.Misc) -> None:
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
            tooltip=_(
                "Required. The openmw.cfg to read the current content= and data= order "
                "from, and (if 'Write openmw.cfg directly' is checked) to patch."
            ),
        )
        PathField(
            top,
            "customizations.toml:",
            start_row + 1,
            self.customizations_var,
            filetypes=(("TOML files", "*.toml"), ("All files", "*.*")),
            tooltip=_(
                "A momw-configurator/umo customizations TOML to pull the plugin/data-path "
                "subset from automatically. Optional if you provide a subset file instead -- "
                "provide both and they're combined."
            ),
        )
        PathField(
            top,
            "subset file (optional):",
            start_row + 2,
            self.subset_file_var,
            filetypes=(("Text/TOML", "*.txt *.toml"), ("All files", "*.*")),
            tooltip=_(
                "A plain text file (one plugin filename or data folder path per line, "
                "'#' comments allowed) or a minimal TOML with subset=[...]/data=[...]. "
                "Combined with --emit-toml, this alone is enough to generate a brand new "
                "customizations.toml with no existing one required."
            ),
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
            tooltip=_(
                "Where to write a corrected customizations.toml (sorted insert blocks, "
                "re-anchored). Disabled when 'write directly back' below is checked."
            ),
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
            tooltip=_(
                "MOMW's plugin-order.yml (source of truth for which plugins belong to which "
                "curated list). With the list name above set, curated plugins for that list "
                "are excluded from the sort (never reordered) so only your custom additions "
                "are touched, and read-only warnings are emitted: redundant, orphan, "
                "needs-cleaning, and a base-order drift check. PyYAML used if installed, "
                "else a built-in parser."
            ),
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
            text=_(
                "Write directly back to customizations.toml (overwrite in place; "
                "a .bak-<timestamp> copy is made first unless backups are disabled below)"
            ),
            variable=self.write_toml_inplace_var,
            command=self._on_toggle_inplace,
        )
        inplace_chk.grid(row=start_row + 6, column=0, columnspan=3, sticky="w", pady=(0, 4))
        add_tooltip(
            inplace_chk,
            _(
                "Instead of writing to a separate file above, overwrite the customizations.toml "
                "given above in place. A timestamped backup is made first (unless disabled), and "
                "you'll get a confirmation prompt before it actually happens."
            ),
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
            _(
                "When checked, Export shows exactly what it would write without "
                "touching any files. Uncheck when you're ready to actually save."
            ),
        )

        write_cfg_chk = ttk.Checkbutton(
            opts, text=_("Write openmw.cfg directly"), variable=self.write_cfg_var
        )
        write_cfg_chk.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        add_tooltip(
            write_cfg_chk,
            _(
                "Patch the content=/data= lines in openmw.cfg in place on Export. "
                "A .bak-<timestamp> copy is made first unless backups are disabled."
            ),
        )

        sort_data_chk = ttk.Checkbutton(
            opts, text=_("Sort data= paths too"), variable=self.sort_data_paths_var
        )
        sort_data_chk.grid(row=0, column=2, sticky="w", padx=8, pady=4)
        add_tooltip(
            sort_data_chk,
            _(
                "mlox has no concept of data= folder order -- this positions new data= paths "
                "using an explicit after/before anchor if you wrote one, or by scanning the "
                "folder for plugins and anchoring next to their neighbor in the sorted content= "
                "order. Off by default so a plugin-only run can't surprise-reorder data= too. "
                "Also required for the data path order panel to populate."
            ),
        )

        no_backup_chk = ttk.Checkbutton(
            opts, text=_("Skip .bak backup of openmw.cfg"), variable=self.no_backup_var
        )
        no_backup_chk.grid(row=1, column=0, sticky="w", padx=8, pady=4)
        add_tooltip(
            no_backup_chk,
            _(
                "Skip making a timestamped backup before overwriting openmw.cfg "
                "and/or an in-place customizations.toml. Not recommended."
            ),
        )

        no_warn_chk = ttk.Checkbutton(
            opts,
            text=_("Skip mlox Conflict/Requires/Note warnings"),
            variable=self.no_predicate_warnings_var,
        )
        no_warn_chk.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        add_tooltip(
            no_warn_chk,
            _(
                "Skip evaluating [Conflict]/[Requires]/[Note] rules against the sorted plugin "
                "list. This is purely informational and read-only either way -- it never changes "
                "the computed order or what gets written, only whether warnings get printed."
            ),
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
            _(
                "tes3conv conversions are written to disk (not held in RAM) and read per-plugin, "
                "so big load orders don't blow up memory. They always go to a 'tes3conv_json' "
                "folder next to the app and are reused within a run -- so Check Conflicts then Cell "
                "Map won't re-run tes3conv (a plugin is only re-converted if it changed). This box "
                "just decides what happens on exit: checked = keep that folder (reused next launch "
                "too); unchecked = delete it when you close the app."
            ),
        )
        add_tooltip(
            create_doc_chk,
            _(
                "Controls what 'Scan...' does with its result. Checked: write the scanned list "
                "to a .txt subset file you choose, and load it (the file stays on disk for reuse). "
                "Unchecked: keep the scanned list in memory just for this session and feed it "
                "straight to the sort -- nothing is written to disk."
            ),
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

        def _btn(
            bar: tk.Misc,
            text: str,
            cmd: Callable[[], None],
            tip: str,
            state: str = "normal",
            pad: tuple[int, int] = (0, 6),
        ) -> ttk.Button:
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

    def _build_log(self, log_container: tk.Misc) -> None:
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
            _(
                "Full output from the last Sort and/or Export. Colour key: green = a plugin/path "
                "this sort inserted or moved, orange = a heads-up (mlox warning, or a rule your "
                "curated cfg order overrode), blue = a section header, bright red = an error worth "
                "checking. Plain text = frozen base rows left untouched. Colors follow the "
                "syntax highlighting theme picked below."
            ),
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
            _(
                "Colour theme for the whole GUI: window/button colours, syntax "
                "highlighting here in the Log panel, and the field-diff JSON viewer "
                "(Check Conflicts -> double-click a field). Switching re-themes "
                "everything immediately, including any windows already open. Built-in: "
                "Dracula, Monokai, Atom One Dark, Gruvbox Dark, plus anything you've "
                "imported."
            ),
        )
        import_theme_btn = ttk.Button(
            log_btns, text=_("Import Theme..."), command=self._import_log_theme
        )
        import_theme_btn.pack(side="left", padx=(8, 0))
        add_tooltip(
            import_theme_btn,
            _(
                "Import a custom theme from a JSON file (background/foreground/select/"
                "section/warn/error/ok/inserted/dim as hex colors, plus an optional "
                '"chrome" object for explicit window/button colours) or a base16 scheme '
                "file (.yaml/.yml/.json with base00..base0F -- e.g. from the atelierbram/"
                "syntax-highlighting or chriskempson/base16 scheme repos). Window colours "
                "not given explicitly are derived from the background. Imported themes are "
                "saved and appear in the dropdown from then on."
            ),
        )

    # -- log panel syntax highlighting themes --------------------------------

    def _custom_themes_file(self) -> Path:
        return app_base_dir() / "log_themes.json"

    def _load_custom_log_themes(self) -> None:
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

    def _save_custom_log_themes(self) -> None:
        try:
            self._custom_themes_file().write_text(
                json.dumps(self._custom_log_themes, indent=2), encoding="utf-8"
            )
        except (OSError, TypeError, ValueError):  # unwritable path / unserialisable value
            pass

    def _theme_names(self) -> list[str]:
        return list(THEME_PRESETS.keys()) + sorted(self._custom_log_themes.keys())

    def _resolve_theme(self, name: str) -> dict | None:
        return THEME_PRESETS.get(name) or self._custom_log_themes.get(name)

    def _apply_log_theme(self, name: str, announce: bool = True) -> None:
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
        trace(f"[theme] chrome palette now follows: {name}")
        # never let a re-apply problem (e.g. a platform/build-specific Tk quirk)
        # stop the log theme itself from applying below
        try:
            self._reapply_chrome()
        except Exception:  # noqa: BLE001
            # diagnostic guard: must not raise onward
            trace("[theme] re-apply pass failed:\n" + traceback.format_exc())
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
            self.status_var.set(_("Log syntax highlighting: %(name)s") % {"name": name})

    def _reapply_chrome(self) -> None:
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
        apply_dark_theme(self.root)  # type: ignore[arg-type]
        count = restyle_widget_tree(self.root)
        for panel in (getattr(self, "order_panel", None), getattr(self, "data_order_panel", None)):
            if panel is not None:
                try:
                    panel._restyle()
                except tk.TclError:
                    pass
        trace_first_fire("theme runtime re-apply walk (restyle_widget_tree)")
        trace(f"[theme] re-applied chrome to {count} plain-tk widgets (ttk covered by Style)")

    def _import_log_theme(self) -> None:
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
            messagebox.showerror(
                _("Import failed"), _("Couldn't import that theme:\n\n%(error)s") % {"error": e}
            )
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
        self.status_var.set(_("Imported and applied theme: %(name)s") % {"name": name})
        messagebox.showinfo(
            _("Theme imported"),
            _('Imported "%(name)s" and set it as the active log theme.') % {"name": name},
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

    def _append_log(self, text: str) -> None:
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

    def _poll_log_queue(self) -> None:
        drained = []
        try:
            while True:
                drained.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        if drained:
            self._append_log("".join(drained))
        self.root.after(80, self._poll_log_queue)

    def clear_log(self) -> None:
        """Empty the log panel."""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._log_group_tag = None

    def save_log(self) -> None:
        """Write the log panel's contents to a file the user picks."""
        path = filedialog.asksaveasfilename(
            defaultextension=".log", filetypes=(("Log files", "*.log"), ("All files", "*.*"))
        )
        if not path:
            return
        Path(path).write_text(self.log_text.get("1.0", "end"), encoding="utf-8")

    # -- run -----------------------------------------------------------------

    def _on_toggle_inplace(self) -> None:
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

    def on_scan_mods(self) -> None:
        """Scan a mods folder to build a subset.

        If 'Create subset text document' is checked, write it to a .txt you choose and
        load that file; otherwise keep the result in memory for this session only (no
        file written). Runs in a worker thread since a big tree can take a moment to
        walk.
        """
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

    def _scan_worker(self, folder: str, out: str | None) -> None:
        writer = QueueWriter(self.log_queue)
        written, mem_lines = None, None
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                lines, n_folders, n_plugins = core.scan_mod_directories(folder, out)
            if out:
                written = out
                status = _(
                    "Scan complete -- %(folders)d folder(s), %(plugins)d plugin(s). "
                    "Subset file loaded."
                ) % {"folders": n_folders, "plugins": n_plugins}
            else:
                mem_lines = lines
                status = _(
                    "Scan complete -- %(folders)d folder(s), %(plugins)d plugin(s). "
                    "Held in memory (no file written)."
                ) % {"folders": n_folders, "plugins": n_plugins}
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: scan failed:\n" + traceback.format_exc())
            status = "Scan failed -- see log."
        finally:
            self.root.after(0, self._scan_finished, written, mem_lines, status)

    def _scan_finished(
        self, written_path: str | None, mem_lines: list[str] | None, status: str
    ) -> None:
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

    def on_sort(self) -> None:
        """Run step 1: compute the plan, in a worker."""
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

    def _sort_worker(self, args: argparse.Namespace) -> None:
        writer = QueueWriter(self.log_queue)
        plan = None
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                plan = core.compute_plan(args)
            n_warn = len(plan.get("predicate_warnings") or [])
            n_yml = len(plan.get("yml_warnings") or [])
            n_plugins = len(plan.get("final_order") or [])
            yml_bit = (_(", %(count)d yml warning(s)") % {"count": n_yml}) if n_yml else ""
            status = _(
                "Sorted %(plugins)d plugin(s), %(warnings)d rule warning(s)%(yml)s. "
                "Drag to adjust, then Export."
            ) % {"plugins": n_plugins, "warnings": n_warn, "yml": yml_bit}
        except SystemExit as e:
            writer.write(f"\nERROR: {e}\n")
            status = "Sort failed -- see log."
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: unexpected exception:\n" + traceback.format_exc())
            status = "Sort failed -- see log."
        finally:
            self.root.after(0, self._sort_finished, plan, status)

    def _cfg_snapshot(self) -> tuple[int, int] | None:
        """(mtime_ns, size) of the cfg file, for drift detection."""
        try:
            st = Path(self.cfg_var.get().strip()).stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _sort_finished(self, plan: dict | None, status: str) -> None:
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
            normalize_data_path(d["value"]) for d in ((plan or {}).get("data_inserts") or [])
        }
        user_norms.discard("")

        def _is_ours(line: str, is_new: bool) -> bool:
            if is_new:
                return True
            p = normalize_data_path(extract_data_path_value(line) or "")
            return bool(p) and p in user_norms

        highlight_lines = [line for line, is_new, _ in data_result if _is_ours(line, is_new)]
        self.data_order_panel.load(data_lines, highlight_lines, disabled_items=prev_disabled_d)

        self.export_button.configure(state="normal" if (final_order or data_lines) else "disabled")
        self.conflicts_button.configure(state="normal" if final_order else "disabled")
        self.cellmap_button.configure(state="normal" if final_order else "disabled")
        self.resource_button.configure(state="normal" if final_order else "disabled")
        self.lint_button.configure(state="normal" if final_order else "disabled")

    def on_export(self) -> None:
        """Run step 2: write the plan out, in a worker."""
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
                _(
                    "openmw.cfg has changed on disk since you ran '1. Sort' (did "
                    "momw-configurator re-run?).\n\nThe order on screen was computed "
                    "against the OLD contents. It's safer to re-Sort first.\n\n"
                    "Export anyway?"
                ),
            )
        ):
            return

        if self.write_toml_inplace_var.get() and not args.dry_run:
            backup_note = (
                "" if self.no_backup_var.get() else " (a .bak-<timestamp> copy will be made first)"
            )
            if not messagebox.askyesno(
                _("Overwrite customizations.toml?"),
                _("This will overwrite:\n%(path)s\n\nin place%(note)s. Continue?")
                % {"path": args.emit_toml, "note": backup_note},
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

    def _export_worker(
        self,
        args: argparse.Namespace,
        final_order: list[str],
        data_order: list[str],
        disabled_plugins: Collection[str],
        disabled_data: Collection[str],
    ) -> None:
        writer = QueueWriter(self.log_queue)
        # on_export() only starts this worker once a plan exists; state it so
        # the contract is checked rather than assumed.
        assert self._current_plan is not None  # noqa: S101
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
            status = _("Export done -- cfg written: %(cfg)s, toml written: %(toml)s.") % {
                "cfg": _("yes") if result["wrote_cfg"] else _("no"),
                "toml": _("yes") if result["wrote_toml"] else _("no"),
            }
        except SystemExit as e:
            writer.write(f"\nERROR: {e}\n")
            status = "Export failed -- see log."
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: unexpected exception:\n" + traceback.format_exc())
            status = "Export failed -- see log."
        finally:
            self.root.after(0, self._export_finished, status)

    def _export_finished(self, status: str) -> None:
        self.worker_running = False
        self.sort_button.configure(state="normal")
        self.export_button.configure(state="normal" if self._current_plan else "disabled")
        self.conflicts_button.configure(state="normal" if self._current_plan else "disabled")
        self.cellmap_button.configure(state="normal" if self._current_plan else "disabled")
        self.resource_button.configure(state="normal" if self._current_plan else "disabled")
        self.status_var.set(status)

    # -- conflict detection --------------------------------------------------

    def _apply_exclusions(self, order: Sequence[str]) -> list[str]:
        """Drop plugins matching the user's exclude patterns.

        From the Options field;
        logs what was skipped.
        """
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

    def _on_keep_json_toggle(self) -> None:
        """Return the folder the tes3conv JSON dump lives in.

        Always the same 'tes3conv_json' folder so it's
        reused within a run; this checkbox only decides whether it's KEPT on close
        or removed. Flipping it just updates the live session's keep flag.
        """
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

    def _get_session(self, conv: str | None) -> core.Tes3ConvSession | None:
        """Return the shared disk-backed tes3conv session, creating it if needed.

        Reused across scans, always dumping to the
        same 'tes3conv_json' folder -- so every plugin is converted at most once per
        run (Check Conflicts then Cell Map reuse the JSON, no re-running tes3conv).
        A cached JSON is re-used only if it's newer than its plugin (mtime check in
        core), so an edited plugin still re-converts. The 'Keep tes3conv JSON dump'
        option only controls whether that folder is removed on close. Called from a
        worker thread; self._keep_json is snapshotted on the main thread.
        """
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

    def _plan_scan_dirs(self) -> list[str]:
        """Return every folder the scans should search for this run.

        The cfg's data=
        dirs plus the pending custom data paths (scan / customizations TOML)
        that aren't in the cfg yet -- so Check Conflicts / Cell Map / Resource
        Conflicts can see your custom mods BEFORE the cfg is written.
        """
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

    # -- cell map (modmapper) ------------------------------------------------

    def on_cell_map(self) -> None:
        """Build the cell-coverage map, in a worker."""
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

    def _cellmap_file(self) -> Path:
        """Stable, user-findable, writable location for the generated map."""
        return app_base_dir() / "cell_map.html"

    def _cellmap_worker(self, order: list[str], dirs: list[str], subset: list[str]) -> None:
        writer = QueueWriter(self.log_queue)
        path = None
        trace(f"cell map: start, {len(order)} plugin(s)")
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                index = PluginFileIndex(dirs)
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
                print(
                    _("  Engine: %(engine)s")
                    % {"engine": "tes3conv" if conv else _("built-in parser")}
                )
                cov = core.build_cell_coverage(order, index, subset_names=subset, session=session)
                trace(
                    f"cell map: coverage built, {len(cov['exterior'])} ext, {len(cov['interior'])} int"
                )
                # Build the explorer alongside the map when a conflict scan
                # has run, so the cell map's "Conflicts" button is there the
                # first time rather than only after the explorer was opened
                # by hand. Requiring that order was a real usability bug: the
                # button silently did not exist and nothing said why.
                href = self._ensure_explorer_for_cell_map(cov)
                html = core.generate_cell_map_html(cov, explorer_href=href)
                trace(f"cell map: html built, {len(html)} bytes")
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
                trace(f"cell map: written to {path}")
                print(
                    _(
                        "  %(exterior)d exterior + %(interior)d interior cell(s) "
                        "touched across %(scanned)d plugin(s)."
                    )
                    % {
                        "exterior": len(cov["exterior"]),
                        "interior": len(cov["interior"]),
                        "scanned": cov["scanned"],
                    }
                )
                print(_("  Map written to: %(path)s") % {"path": path})
            status = _("Cell map ready (%(exterior)d exterior, %(interior)d interior).") % {
                "exterior": len(cov["exterior"]),
                "interior": len(cov["interior"]),
            }
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: cell map failed:\n" + traceback.format_exc())
            status = "Cell map failed -- see log."
        finally:
            self.root.after(0, self._cellmap_finished, path, status)

    def _cellmap_finished(self, path: str | None, status: str) -> None:
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
            trace("cell map: viewer = browser (forced)")
            self._open_cell_map_browser()
            return
        if force == "tkinterweb" and can_tkweb:
            trace("cell map: viewer = tkinterweb (forced)")
            self._show_cell_map_window(path)
            return
        if force == "pywebview" and HAVE_PYWEBVIEW:
            trace("cell map: viewer = pywebview (forced)")
            self._open_cell_map_pywebview(path)
            return
        # Auto: prefer pywebview (real OS webview), then tkinterweb's load_file,
        # then the browser. tkhtmlview can't draw SVG, so it's never used here.
        if HAVE_PYWEBVIEW:
            trace("cell map: viewer = pywebview (embedded)")
            self._open_cell_map_pywebview(path)
        elif can_tkweb:
            trace("cell map: viewer = tkinterweb (in-app window)")
            self._show_cell_map_window(path)
        else:
            trace("cell map: viewer = browser (no pywebview/tkinterweb available)")
            self._open_cell_map_browser()
            self.status_var.set(
                status + "  (opened in browser — pip install pywebview " "for an in-app window)"
            )

    @staticmethod
    def _coverage_to_conflicts(coverage: Mapping[str, Any]) -> list[dict]:
        """Turn cell coverage into conflict-shaped rows to populate the explorer.

        The cell map already knows which cells more than one mod touches, and
        the explorer's map and lists only need exactly that shape. So when no
        record scan has run, the explorer is populated from coverage rather than
        left empty -- which is what a user sees on first open, and an empty page
        beside a busy cell map reads as broken.

        This is coverage, **not** record-level conflict, and the page says so:
        two mods touching a cell is not the same as their records colliding.
        The overlap is the honest superset -- every record conflict is in here,
        alongside cells that merely share space -- and the terrain/nav/field
        detail that needs tes3conv is added by 'Check Conflicts'.

        Args:
            coverage: The result of ``build_cell_coverage``.

        Returns:
            One row per cell touched by two or more mods.
        """
        subl = {s.lower() for s in coverage.get("subset_lower", set())}
        rows: list[dict] = []
        for (gx, gy), mods in coverage.get("exterior", {}).items():
            if len(mods) < 2:
                continue
            rows.append(
                {
                    "type": "Cell (coverage)",
                    "id": f"({gx}, {gy})",
                    "plugins": list(mods),
                    "winner": mods[-1],
                    "involves_subset": any(m.lower() in subl for m in mods),
                }
            )
        for name, mods in coverage.get("interior", {}).items():
            if len(mods) < 2:
                continue
            rows.append(
                {
                    "type": "Cell (coverage)",
                    "id": str(name),
                    "plugins": list(mods),
                    "winner": mods[-1],
                    "involves_subset": any(m.lower() in subl for m in mods),
                }
            )
        return rows

    def _ensure_explorer_for_cell_map(self, coverage: Mapping[str, Any] | None = None) -> str:
        """Write a conflict explorer next to the cell map, and return its name.

        **Always writes a populated one.** Two earlier bugs came from this
        method being conditional: first the button was missing without a prior
        scan, then the button was there but the page was empty. Both had the
        same cause -- the explorer read only the record-scan results, which the
        cell map does not produce. It now falls back to the coverage the cell
        map *did* compute, so the map and lists are populated on first open.

        The distinction is preserved, not erased: coverage-derived rows are
        marked as such and the page states that record-level detail (which
        records actually conflict, terrain, navigation) needs 'Check Conflicts'.
        A record scan, once run, supersedes the coverage rows entirely.

        Runs on the cell-map worker thread, so it costs nothing interactively.

        Args:
            coverage: The cell coverage already built for the map, used as the
                fallback data source when no record scan has run.

        Returns:
            The explorer's filename for a relative link, or ``""`` only if the
            viz package is unavailable or writing failed.
        """
        if build_explorer is None or viz_sidecar is None:
            return ""
        conflicts = list(getattr(self, "_all_conflicts", None) or [])
        coverage_only = not conflicts
        if coverage_only and coverage is not None:
            conflicts = self._coverage_to_conflicts(coverage)
        try:
            detail: dict = {}
            session = getattr(self, "_conf_session", None)
            # Sampled detail only, and bounded (60 cells) -- this is the map's
            # in-page preview. The world 3D terrain is NOT decoded here: it is
            # held back (its toggle is off), and decoding thousands of cells for
            # it was what froze the cell map and, because it ran before the
            # button was written, cost the button too. Full-resolution cell
            # pages are generated in the BACKGROUND below so the map and its
            # Conflicts button appear immediately.
            if not coverage_only and session is not None and collect_detail is not None:

                def fields_for(conflict: dict) -> dict:
                    """Look one conflict's fields up through tes3conv."""
                    _keys, per, _diff = core.diff_record_fields(
                        session, conflict, getattr(self, "_conf_paths", {})
                    )
                    return per

                cache, sig_for = self._detail_cache()
                detail = collect_detail(conflicts, fields_for, cache=cache, signature_for=sig_for)
            path = self._cellmap_file().with_name("conflict_explorer.html")
            data_dir = path.stem + viz_sidecar.DATA_SUFFIX
            path.write_text(
                build_explorer(
                    conflicts,
                    detail=detail,
                    cell_map_href=self._cellmap_file().name,
                    data_dir=data_dir,
                    embed_detail=False,
                    coverage_only=coverage_only,
                ),
                encoding="utf-8",
            )
            viz_sidecar.write_sidecars(path, {"detail": detail})
            if viz_assets is not None:
                viz_assets.write_assets(path.with_name(data_dir))
            self._last_explorer_file = str(path)
            trace(
                f"cell map: explorer written ({len(conflicts)} rows, "
                f"coverage_only={coverage_only}, {len(detail)} detailed)"
            )
            # Enrich with full-resolution per-cell pages off the critical path,
            # so the map is already on screen. Best-effort: if this thread is
            # slow or fails, the map, the lists and the button are untouched.
            if not coverage_only and detail and build_cell_pages is not None:
                threading.Thread(
                    target=self._fill_cell_pages,
                    args=(list(conflicts), str(path), data_dir, len(detail)),
                    daemon=True,
                ).start()
            if coverage_only:
                print(
                    _(
                        "  Conflict explorer: %(cells)d multi-mod cell(s) from coverage. "
                        "Run 'Check Conflicts' for record-level detail."
                    )
                    % {"cells": len(conflicts)}
                )
            else:
                print(_("  Conflict explorer ready; full-resolution cell pages filling in..."))
        except Exception:  # noqa: BLE001 - the cell map must render regardless
            trace("cell map: explorer build FAILED:\n" + traceback.format_exc())
            return ""
        return path.name

    def _open_cell_map_pywebview(self, path: str | Path) -> None:
        """Show the map in an embedded OS webview.

        Re-invokes this executable with
        --show-map in a SEPARATE process (webview.start() needs its own main
        thread). Frozen-safe: a built .exe re-runs the .exe; from source we re-run
        the script -- never 'python -c', which a frozen exe can't do.
        """
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
            trace(f"cell map: launching pywebview child: {cmd}")
            subprocess.Popen(cmd, **nw)  # type: ignore[call-overload]
        except (OSError, ValueError):  # Popen: missing exe or bad argv
            trace("cell map: pywebview child launch FAILED:\n" + traceback.format_exc())
            self._open_cell_map_browser()

    def open_html_in_app(self, path: str | Path, title: str) -> None:
        """Show a generated HTML page in-app, falling back the same way the map does.

        The chain is pywebview (a real OS webview, so canvas and SVG both work)
        -> tkinterweb -> the browser. It mirrors the cell map's chain
        deliberately: those pages are read in the same sitting, and a view that
        opened somewhere else would be its own surprise.

        The browser is the last resort rather than the default, which is the
        whole point -- but it is still a *real* resort, because tkinterweb
        cannot run the canvas the terrain and nav views draw on.

        Args:
            path: The written HTML file.
            title: Window title.
        """
        target = Path(path)
        self._last_view_file = str(target)
        can_tkweb = HTMLViewer is not None and hasattr(HTMLViewer, "load_file")
        if HAVE_PYWEBVIEW:
            trace(f"view: pywebview for {target.name}")
            self._open_cell_map_pywebview(target)
            return
        if can_tkweb:
            trace(f"view: tkinterweb for {target.name}")
            self._show_html_window(target, title)
            return
        trace(f"view: browser for {target.name} (no pywebview/tkinterweb)")
        self._open_file_in_browser(target)

    def _open_file_in_browser(self, path: str | Path) -> None:
        """Open any local file in the user's browser.

        Args:
            path: The file to open.
        """
        target = Path(path)
        if not target.exists():
            return
        try:
            webbrowser.open(target.resolve().as_uri())
        except (OSError, ValueError, webbrowser.Error):
            pass

    def _show_html_window(self, path: str | Path, title: str) -> None:
        """Render a page in an in-app tkinterweb window.

        Args:
            path: The HTML file.
            title: Window title.
        """
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=DARK["bg"])
        win.geometry("1280x860")
        bar = ttk.Frame(win, padding=6)
        bar.pack(fill="x")
        ttk.Button(
            bar,
            text=_("Open in browser"),
            command=lambda: self._open_file_in_browser(path),
        ).pack(side="left")
        ttk.Button(bar, text=_("Close"), command=win.destroy).pack(side="right")
        try:
            viewer = HTMLViewer(win)
            viewer.pack(fill="both", expand=True)
            viewer.load_file(str(path))
        except Exception:  # noqa: BLE001 - third-party widget; the browser still works
            ttk.Label(
                win,
                foreground="#ffb454",
                padding=8,
                text=_(
                    "(inline render failed -- use 'Open in browser'. The 3D and "
                    "nav views need a real browser engine.)"
                ),
            ).pack(anchor="w")

    def _show_cell_map_window(self, path: str | Path) -> None:
        win = getattr(self, "_cellmap_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()
        win = tk.Toplevel(self.root)
        self._cellmap_win = win
        win.title("Viz Window")
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

    def _open_cell_map_browser(self) -> None:
        p = getattr(self, "_last_cell_file", None)
        if not p or not Path(p).exists():
            return
        try:
            webbrowser.open(Path(p).resolve().as_uri())  # correct file URI on Win/Linux/macOS
        except (OSError, ValueError, webbrowser.Error):  # as_uri on a relative path / no browser
            pass

    def _save_cell_map(self) -> None:
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
            self.status_var.set(_("Cell map saved: %(path)s") % {"path": out})
        except OSError as e:
            messagebox.showerror(_("Save failed"), str(e))

    # -- resource (VFS) conflicts --------------------------------------------

    # -- download sources dialog ---------------------------------------------

    def on_sources(self) -> None:
        """Open the download-sources dialog."""
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
            text=_(
                "If upstream moves or a new fork takes over, point the updaters at "
                "the new location here. Blank = built-in defaults. Downloads are "
                "always validated before anything is overwritten."
            ),
            foreground=DARK["fg_dim"],
            wraplength=720,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Label(top, text=_("mlox rules URL template:")).grid(row=1, column=0, sticky="w")
        rent = ttk.Entry(top, textvariable=self.rules_url_var)
        rent.grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        add_tooltip(
            rent,
            # {name} is a literal token the user types into the URL template,
            # not a placeholder of THIS string -- hence %% -escaping is not
            # needed (this is %-formatted, and "{name}" contains no %).
            _(
                "Where 'Update Rules...' downloads from. Must contain {name}, which "
                "is replaced with mlox_base.txt / mlox_user.txt per file.\n"
                "Default: %(url)s"
            )
            % {"url": RULES_URL_TEMPLATE},
        )
        ttk.Label(
            top,
            text=_("default: %(url)s") % {"url": RULES_URL_TEMPLATE},
            foreground=DARK["fg_dim"],
        ).grid(row=2, column=1, sticky="w", padx=6)

        ttk.Label(top, text=_("plugin-order.yml URL:")).grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )
        pent = ttk.Entry(top, textvariable=self.plugin_order_url_var)
        pent.grid(row=3, column=1, sticky="ew", padx=6, pady=(10, 2))
        add_tooltip(
            pent,
            _(
                "Where the plugin-order.yml 'Update...' button downloads from. "
                "Blank tries the built-in candidates in order.\n"
                "Default: %(url)s"
            )
            % {"url": PLUGIN_ORDER_URLS[0]},
        )
        ttk.Label(
            top,
            text=_("default: %(url)s...") % {"url": PLUGIN_ORDER_URLS[0][:96]},
            foreground=DARK["fg_dim"],
        ).grid(row=4, column=1, sticky="w", padx=6)

        row = ttk.Frame(top)
        row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(16, 0))

        def _reset() -> None:
            self.rules_url_var.set("")
            self.plugin_order_url_var.set("")

        def _close() -> None:
            t = self.rules_url_var.get().strip()
            if t and "{name}" not in t:
                messagebox.showerror(
                    _("Download sources"),
                    _(
                        "The rules URL template must contain {name} (it's replaced "
                        "with the rule filename)."
                    ),
                    parent=win,
                )
                return
            self._save_settings()
            win.destroy()

        ttk.Button(row, text=_("Reset to defaults"), command=_reset).pack(side="left")
        ttk.Button(row, text=_("Save & Close"), command=_close).pack(side="right")

    # -- mlox user-rules maker -----------------------------------------------

    def _default_rules_file(self) -> str:
        """Return the rules file to prefill the maker with.

        An existing personal file already in the rules list, else
        'mlox_my_rules.txt' next to the first rule file (or the cfg).
        """
        for p in self.rules_panel.get_paths():
            if Path(p).name.lower() not in ("mlox_base.txt", "mlox_user.txt"):
                return str(p)
        paths = self.rules_panel.get_paths()
        base = Path(paths[0]).parent if paths else Path(self._cfg_dir() or ".")
        return str(base / "mlox_my_rules.txt")

    def on_rule_maker(self) -> None:
        """Open the mlox user-rules maker."""
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
            _(
                "Personal rules file the rule is appended to (created with a header "
                "if new). Use your OWN file, not mlox_base/mlox_user -- those get "
                "overwritten by 'Update Rules...'. It's auto-added to the rule-files "
                "list LAST, so your rules win conflicts."
            ),
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
            _(
                "Add the rows currently SELECTED in the main plugin-order panel, in "
                "their displayed order (Ctrl/Shift-click there to multi-select first)."
            ),
        )
        ttk.Button(rbtns, text=_("Remove"), command=self._rm_remove).pack(fill="x", pady=2)
        ttk.Button(
            rbtns,
            text=_("Clear"),
            command=lambda: (self._rm_list.delete(0, "end"), self._rm_refresh()),  # type: ignore[func-returns-value]
        ).pack(fill="x", pady=2)
        af = ttk.Frame(pf)
        af.grid(row=1, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 8))
        af.columnconfigure(0, weight=1)
        self._rm_add_var = tk.StringVar()
        aent = ttk.Entry(af, textvariable=self._rm_add_var)
        aent.grid(row=0, column=0, sticky="ew")
        add_tooltip(
            aent,
            _(
                "Type a plugin name or mlox pattern (wildcards * ? and <VER> allowed; "
                "must end in a plugin extension) and press Enter or Add."
            ),
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
            _(
                "Written above the rule as a ';;' comment. The mlox rule guidelines "
                "suggest citing your source, e.g. (Ref: the mod's readme) or "
                "(Ref: a forum URL ) -- surround URLs with spaces. Handy if you "
                "later contribute the rule upstream."
            ),
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

    def _rm_names(self) -> list[str]:
        return list(self._rm_list.get(0, "end"))

    def _rm_add_from_panel(self) -> None:
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

    def _rm_add_typed(self) -> None:
        v = self._rm_add_var.get().strip()
        if v and v.lower() not in {n.lower() for n in self._rm_names()}:
            self._rm_list.insert("end", v)
            self._rm_add_var.set("")
        self._rm_refresh()

    def _rm_remove(self) -> None:
        for i in reversed(self._rm_list.curselection()):
            self._rm_list.delete(i)
        self._rm_refresh()

    def _rm_preview_text(self) -> str:
        try:
            names = self._rm_names()
            if not names:
                return "(add plugins above)"
            # build without writing: validate via the same code path
            kw = self._rm_kind.get()
            titles = {"order": "Order", "nearstart": "NearStart", "nearend": "NearEnd"}
            for n in names:
                m = ORDER_NAME_RE.match(n)
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

    def _rm_browse_file(self) -> None:
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
        trace(f"[smoke] rules-maker Browse: {'chose ' + chosen if chosen else 'cancelled'}")
        if chosen:
            self._rm_file_var.set(chosen)

    def _rm_refresh(self) -> None:
        trace_first_fire("rules-maker refresh (radio / reorder)")
        txt = self._rm_preview_text()
        self._rm_preview.configure(state="normal")
        self._rm_preview.delete("1.0", "end")
        self._rm_preview.insert("1.0", txt)
        self._rm_preview.configure(state="disabled")

    def _rm_append(self) -> None:
        path = self._rm_file_var.get().strip()
        if not path:
            messagebox.showerror(_("New rule"), _("Set a rules file first."), parent=self._rm_win)
            return
        if Path(path).name.lower() in ("mlox_base.txt", "mlox_user.txt"):
            messagebox.showerror(
                _("New rule"),
                _(
                    "Don't append personal rules to mlox_base.txt / mlox_user.txt -- "
                    "'Update Rules...' overwrites those. Pick your own file "
                    "(e.g. mlox_my_rules.txt)."
                ),
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
                    _(
                        "This [Order] rule contradicts the frozen curated (MOMW) order "
                        "for:\n\n%(pairs)s\n\nmlox will DISCARD those orderings (it never "
                        "reorders the curated list), so the rule won't take effect for "
                        "them. Write it anyway?"
                    )
                    % {"pairs": pairs},
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
        self.status_var.set(
            _("Rule appended to %(file)s. Re-run '1. Sort' to apply it.")
            % {"file": Path(path).name}
        )

    # -- plugin-order.yml updater --------------------------------------------

    def on_update_plugin_order_yml(self) -> None:
        """Download a fresh plugin-order.yml, with confirmation."""
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
        ages = rule_file_ages([p])
        age = ages[0][1]
        age_txt = (
            "file doesn't exist yet -- it will be created"
            if age is None
            else f"your copy is ~{age} day(s) old"
        )
        if not messagebox.askyesno(
            _("Update plugin-order.yml"),
            _(
                "Download the current plugin-order.yml from MOMW?\n\n%(age)s.\n\n"
                "The download is validated before anything is written; a timestamped "
                ".bak of the old file is kept."
            )
            % {"age": age_txt},
        ):
            return

        custom = self.plugin_order_url_var.get().strip()
        urls = [custom] if custom else None

        def work() -> None:
            try:
                report = update_plugin_order_yml(p, urls=urls)
            except Exception as e:  # noqa: BLE001
                # worker thread: must report into the dialog, never vanish silently
                report = [f"FAILED: {e}"]
            self.root.after(
                0,
                lambda: (
                    messagebox.showinfo(_("Update plugin-order.yml"), "\n".join(report)),
                    self.status_var.set(report[0] if report else ""),  # type: ignore[func-returns-value]
                ),
            )

        self.status_var.set(_("Downloading plugin-order.yml..."))
        threading.Thread(target=work, daemon=True).start()

    # -- savegame dependency check -------------------------------------------

    def on_save_check(self) -> None:
        """Check a savegame's content files against the current order."""
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
            self.status_var.set(_("Save check failed: %(error)s") % {"error": err})
            return
        assert files is not None  # err is None here  # noqa: S101
        writer.write(f"  Save depends on {len(files)} content file(s).\n")
        if missing:
            for m in missing:
                writer.write(
                    f"\n[MISSING SAVE DEP] '{m}' is required by this save but not in "
                    f"the current load order -- OpenMW will refuse to load it.\n"
                )
            self.status_var.set(
                ngettext(
                    "Save check: %(count)d missing dependency! See the log.",
                    "Save check: %(count)d missing dependencies! See the log.",
                    len(missing),
                )
                % {"count": len(missing)}
            )
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
            self.status_var.set(
                _("Save check: all %(count)d dependencies present.") % {"count": len(files)}
            )
            messagebox.showinfo(
                _("Save Check"),
                _(
                    "%(file)s: all %(count)d content files it needs are in "
                    "the current load order. Safe to export."
                )
                % {"file": Path(p).name, "count": len(files)},
            )

    # -- backup manager ------------------------------------------------------

    def on_backups(self) -> None:
        """Scan for backup files and open the restore/delete window."""
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

    def _backups_worker(self, dirs: list[str], cfg: str | None) -> None:
        try:
            found = core.scan_backups(dirs, cfg_path=cfg)
            status = ngettext(
                "Found %(count)d backup file.", "Found %(count)d backup files.", len(found)
            ) % {"count": len(found)}
        except Exception as e:  # noqa: BLE001
            # worker top level: status line carries the failure
            found, status = [], _("Backup scan failed: %(error)s") % {"error": e}
        finally:
            self.worker_running = False
        self.root.after(0, self._show_backups_window, found, status)

    def _show_backups_window(self, found: Sequence[Any], status: str) -> None:
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
            text=ngettext(
                "%(count)d backup file. Restore copies the backup over its "
                "original; Delete removes the backup file itself.",
                "%(count)d backup files. Restore copies the backup over its "
                "original; Delete removes the backup file itself.",
                len(found),
            )
            % {"count": len(found)},
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

        def _selected() -> list[Any]:
            return [self._bk_rows[i] for i in lb.curselection()]

        def _restore() -> None:
            sel = [r for r in _selected() if r[1]]
            if not sel:
                return
            if not messagebox.askyesno(
                _("Restore"),
                ngettext(
                    "Copy %(count)d backup over its original (overwriting it)?",
                    "Copy %(count)d backups over their originals (overwriting them)?",
                    len(sel),
                )
                % {"count": len(sel)},
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
                ngettext(
                    "Restored %(count)d backup%(failed)s. Re-run '1. Sort' to refresh checks.",
                    "Restored %(count)d backups%(failed)s. Re-run '1. Sort' to refresh checks.",
                    ok,
                )
                % {
                    "count": ok,
                    "failed": (_(", %(fail)d failed") % {"fail": fail}) if fail else "",
                }
            )

        def _delete() -> None:
            sel = _selected()
            if not sel:
                return
            if not messagebox.askyesno(
                _("Delete"),
                ngettext(
                    "Permanently delete %(count)d backup file?",
                    "Permanently delete %(count)d backup files?",
                    len(sel),
                )
                % {"count": len(sel)},
                parent=win,
            ):
                return
            ok = 0
            for bpath, _o, _k in sel:
                try:
                    Path(bpath).unlink()
                    ok += 1
                except OSError:
                    pass
            self.status_var.set(
                ngettext(
                    "Deleted %(count)d backup file.",
                    "Deleted %(count)d backup files.",
                    ok,
                )
                % {"count": ok}
            )
            win.destroy()
            self.on_backups()

        ttk.Button(btns, text=_("Restore Selected"), command=_restore).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btns, text=_("Delete Selected"), command=_delete).pack(side="left", padx=(0, 6))
        ttk.Button(
            btns,
            text=_("Refresh"),
            command=lambda: (win.destroy(), self.on_backups()),  # type: ignore[func-returns-value]
        ).pack(side="left")
        ttk.Button(btns, text=_("Close"), command=win.destroy).pack(side="right")

    # -- lint (tes3lint-style native checks) ---------------------------------

    def on_lint(self) -> None:
        """Run the tes3lint-style checks, in a worker."""
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

    def _lint_worker(self, order: list[str], dirs: list[str], subset: list[str]) -> None:
        writer = QueueWriter(self.log_queue)
        stats: dict = {}
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                print("\n" + "=" * 70)
                print(_(" LINT (tes3lint-style checks, native)"))
                print("=" * 70)
                index = PluginFileIndex(dirs)
                subset_origins = {str(s).lower(): "your mod" for s in subset}
                warnings, stats = core.lint_plugins(
                    order, index, subset_names=subset, origins=subset_origins
                )
                print(
                    _(
                        "  Scanned %(scanned)d plugin(s); %(cells)d interior cell(s), "
                        "%(grids)d interior pathgrid(s)."
                    )
                    % {
                        "scanned": stats.get("scanned", 0),
                        "cells": stats.get("interior_cells", 0),
                        "grids": stats.get("pathgrids", 0),
                    }
                )
                if warnings:
                    for w in warnings:
                        print(f"\n{w}")
                else:
                    print(_("\n  No lint findings. Clean bill of health."))
            n = stats.get("warnings", 0)
            status = (
                ngettext(
                    "Lint: %(count)d finding. See the log.",
                    "Lint: %(count)d findings. See the log.",
                    n,
                )
                % {"count": n}
                if n
                else _("Lint: no findings.")
            )
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: lint failed:\n" + traceback.format_exc())
            status = "Lint failed -- see log."
        finally:
            self.root.after(0, self._lint_finished, status)

    def _lint_finished(self, status: str) -> None:
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

    def _cfg_dir(self) -> str | None:
        c = self.cfg_var.get().strip()
        return str(Path(c).parent) if c else None

    def _refill_res_tree(self) -> None:
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

    def _is_custom(self, plugin: str) -> bool:
        """Report whether this plugin is one of the user's custom mods.

        True when it is in the scanned subset,
        as opposed to a curated-list plugin.
        """
        return str(plugin).lower() in getattr(self, "_conf_subset_lower", set())

    @staticmethod
    # Any: formats an arbitrary decoded TES3 field value for display.
    def _fmt_val(v: Any) -> str:  # noqa: ANN401
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

    def _populate_field_diff(self, conflict: Mapping[str, Any]) -> None:
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
        # The record's own id doubles as its cell label for the visualisations
        # ("(43, -45)" for landscape, "Balmora (-3, -2)" for a path grid), so
        # the generated page can say which cell it is showing.
        self._conf_record_label = str(conflict.get("id") or "")
        for k in keys:
            row = [k] + [self._fmt_val(per[p].get(k)) for p in plugins]
            ftree.insert("", "end", iid=k, values=row, tags=("diff",) if k in diff else ())
        if not keys:
            ftree.insert("", "end", values=["(no fields / identical)"] + [""] * len(plugins))

    @staticmethod
    def _disassemble_bytecode_field(value: str, source_text: str | None = None) -> str | None:
        """Disassembly text for a tes3conv 'bytecode' field, or None.

        Thin delegation: the logic lives in mlox_subset.mwscript so it can be
        unit-tested without a display. Returns None only when the package is
        unavailable, in which case the caller shows the raw value as before.
        """
        if listing_for_bytecode_field is None:
            return None
        return listing_for_bytecode_field(value, source_text)


def _run_pywebview_window(path: str | Path) -> None:
    """Open one cell-map file in an OS webview and block until closed.

    Invoked in a child process (see _open_cell_map_pywebview) so webview.start() owns
    its own main thread, cleanly, without disturbing the tkinter app. Always writes its
    outcome to cell_map_viewer.log next to the app so a failed backend (e.g. pywebview's
    WebView2/pythonnet backend on an unsupported Python) is visible instead of silently
    falling back to the browser.
    """
    try:
        logf = str(app_base_dir() / "cell_map_viewer.log")
    except (OSError, RuntimeError):  # app_base_dir may fall back to Path.home()
        logf = None

    def _log(msg: str) -> None:
        if not logf:
            return
        try:
            from datetime import datetime as _dt

            with Path(logf).open("a", encoding="utf-8") as fh:
                # Local clock: read alongside the GUI's own log panel.
                fh.write(f"{_dt.now():%Y-%m-%d %H:%M:%S}  {msg}\n")  # noqa: DTZ005
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


def main() -> None:
    """Parse arguments and start the GUI."""
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
