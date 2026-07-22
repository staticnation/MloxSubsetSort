"""Conflict windows: record/resource conflict scans, field diff, CSV export.

Split out of the ``App`` class in ``mlox_subset_sort_gui.py`` as a mixin
(CODE_REVIEW.md §16/§9.2, 3.0). Method bodies are verbatim; ``App`` inherits
this class, so ``self`` is the running ``App`` instance and every attribute
reference resolves exactly as it did when the methods lived there.
"""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
import traceback
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import TYPE_CHECKING, Any, Literal

import mlox_subset_sort as core
from mlox_subset.gui import app_base_dir
from mlox_subset.gui.theme import (
    DARK,
    THEME_PRESETS,
    _json_syntax_colors,
    highlight_json_with_html,
    highlight_plain_text_with_html,
    style_json_syntax_tags,
)
from mlox_subset.gui.widgets import QueueWriter, add_tooltip
from mlox_subset.i18n import gettext as _, ngettext
from mlox_subset.plugins import PluginFileIndex
from mlox_subset.tracing import trace
from mlox_subset.viz import sidecar

try:
    from mlox_subset.viz import assets as viz_assets, cache as viz_cache
except ImportError:  # pragma: no cover - only when viz/ is absent
    viz_assets = None  # type: ignore[assignment]
    viz_cache = None  # type: ignore[assignment]

# Compiled-script disassembly for the field-diff window. Optional, exactly as
# in the main module: without it the diff shows the raw base64 blob. Declared
# first so the ImportError fallback to None type-checks.
listing_for_bytecode_field: Callable[..., str] | None
variables_text_for_field: Callable[..., str] | None
try:
    from mlox_subset.mwscript import (
        listing_for_bytecode_field,
        variables_text_for_field,
    )
except ImportError:  # pragma: no cover - only when mwscript/ is absent
    listing_for_bytecode_field = None
    variables_text_for_field = None

# Landscape / path-grid field decoding. Optional on the same terms.
text_for_field: Callable[..., str | None] | None
describe_field: Callable[[str], str | None] | None
try:
    from mlox_subset.tes3fields import describe_field, text_for_field
except ImportError:  # pragma: no cover - only when tes3fields/ is absent
    text_for_field = None
    describe_field = None

# The HTML visualisations. Optional on the same terms again: without the
# package the windows lose their "Visualise" buttons and nothing else changes.
build_cell_pages: Callable[..., dict] | None
build_conflict_map: Callable[..., str] | None
build_explorer: Callable[..., str] | None
build_height_delta: Callable[..., str] | None
build_pathgrid_graph: Callable[..., str] | None
build_terrain_3d: Callable[..., str] | None
collect_detail: Callable[..., dict] | None
try:
    from mlox_subset.viz import (
        build_cell_pages,
        build_conflict_map,
        build_explorer,
        build_height_delta,
        build_pathgrid_graph,
        build_terrain_3d,
        collect_detail,
    )
except ImportError:  # pragma: no cover - only when viz/ is absent
    build_cell_pages = None
    build_conflict_map = None
    build_explorer = None
    build_height_delta = None
    build_pathgrid_graph = None
    build_terrain_3d = None
    collect_detail = None


def _as_float(value: object) -> float:
    """Coerce a field value to a float, defaulting to zero.

    Field values come from scanned third-party plugins, so a height offset can
    legitimately be missing, null, or a string. None of those is worth an
    exception when the consequence is a uniformly shifted surface.

    Args:
        value: The raw field value.

    Returns:
        The value as a float, or ``0.0`` if it cannot be read as one.
    """
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


class ConflictWindowsMixin:
    """The conflict/resource windows and their workers (mixed into ``App``)."""

    if TYPE_CHECKING:
        # The host contract -- see the equivalent block in gui/t3.py for why
        # this is declared rather than silenced.
        root: tk.Misc
        log_queue: queue.Queue
        status_var: tk.StringVar
        cfg_var: tk.StringVar
        log_theme_var: tk.StringVar
        keep_json_var: tk.BooleanVar
        sort_button: ttk.Button
        export_button: ttk.Button
        conflicts_button: ttk.Button
        cellmap_button: ttk.Button
        resource_button: ttk.Button
        lint_button: ttk.Button
        order_panel: Any
        _current_plan: dict | None
        worker_running: bool
        _res_shown: list
        _tes3conv_override: str | None

        def _apply_exclusions(self, names: list[str]) -> list[str]: ...
        def _attach_hamburger_grip(self, widget: tk.Misc, orient: str) -> None: ...
        def _disassemble_bytecode_field(
            self, value: str, source_text: str | None
        ) -> str | None: ...
        def _get_session(self, conv: str | None) -> core.Tes3ConvSession | None: ...
        def _is_custom(self, name: str) -> bool: ...
        def _paned(self, parent: tk.Misc, orient: str) -> tk.PanedWindow: ...
        def _plan_scan_dirs(self) -> list[str]: ...
        def _populate_field_diff(self, conflict: dict) -> None: ...
        def _refill_res_tree(self) -> None: ...
        def _resolve_theme(self, name: str) -> dict | None: ...
        def _set_tes3conv(self) -> None: ...

    def on_check_conflicts(self) -> None:
        """Scan the current (sorted, enabled) plugins for TES3 record conflicts.

        Runs in a worker since parsing every plugin can take a moment.
        """
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

    def _conflicts_worker(self, order: list[str], dirs: list[str], subset: list[str]) -> None:
        writer = QueueWriter(self.log_queue)
        conflicts: list[dict] = []
        stats: dict = {}
        session = None
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
                print(_(" TES3 RECORD CONFLICTS (read-only)"))
                print("=" * 70)
                if session:
                    print(
                        _("  Engine: tes3conv (%(path)s) -- field-level diffs available.")
                        % {"path": conv}
                    )
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
            status = _(
                "Conflicts: %(count)d record(s), %(involved)d involving your mods. "
                "See the Conflicts window."
            ) % {"count": stats.get("conflicts", 0), "involved": n_sub}
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: conflict scan failed:\n" + traceback.format_exc())
            status = "Conflict scan failed -- see log."
        finally:
            self.root.after(0, self._conflicts_finished, conflicts, stats, session, status)

    def _conflicts_finished(
        self,
        conflicts: list[dict],
        stats: dict,
        session: core.Tes3ConvSession | None,
        status: str,
    ) -> None:
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

    def on_resource_conflicts(self) -> None:
        """Scan the data folders for loose-file (VFS) conflicts, in a worker."""
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

    def _resource_worker(self, dirs: list[str], subset_dirs: list[str]) -> None:
        writer = QueueWriter(self.log_queue)
        conflicts: list[dict] = []
        stats: dict = {}
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                print("\n" + "=" * 70)
                print(_(" DATA-PATH RESOURCE (VFS) CONFLICTS"))
                print("=" * 70)
                conflicts, stats = core.detect_resource_conflicts(dirs, subset_dirs=subset_dirs)
                print(core.format_resource_report(conflicts, stats, limit=200))
            status = _("Resource conflicts: %(count)d file(s). See the window.") % {
                "count": stats.get("conflicts", 0)
            }
        except Exception:  # noqa: BLE001
            # worker top level: reports the traceback into the log panel
            writer.write("\nERROR: resource scan failed:\n" + traceback.format_exc())
            status = "Resource scan failed -- see log."
        finally:
            self.root.after(0, self._resource_finished, conflicts, stats, status)

    def _resource_finished(self, conflicts: list[dict], stats: dict, status: str) -> None:
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

    def _show_resource_window(self, conflicts: list[dict], stats: dict) -> None:
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
            text=_(
                "%(conflicts)d loose-file conflict(s) across "
                "%(dirs)d folder(s), %(files)d file(s) — "
                "%(involved)d involve your custom data paths (★). Later folder wins — "
                "reorder the data-path panel to change it."
            )
            % {
                "conflicts": stats.get("conflicts", 0),
                "dirs": stats.get("dirs", 0),
                "files": stats.get("files", 0),
                "involved": n_sub,
            },
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

        def on_sel(_e: object = None) -> None:
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

    def _save_resource_csv(self) -> None:
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
            self.status_var.set(_("Saved: %(path)s") % {"path": path})
        except OSError as e:
            messagebox.showerror(_("Save failed"), str(e))

    def _show_conflict_window(self, conflicts: list[dict], stats: dict) -> None:
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
            text=_(
                "%(conflicts)d conflicting record(s) across "
                "%(scanned)d plugin(s) — %(involved)d involve your custom mods "
                "(★). Winner = last loaded."
            )
            % {
                "conflicts": stats.get("conflicts", 0),
                "scanned": stats.get("scanned", 0),
                "involved": n_sub,
            },
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
            text=_(
                "Field comparison for the selected record — differing fields in red · "
                "★ = your custom mod · last column wins · double-click a field for the full "
                "value:"
            ),
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
            _(
                "Field-by-field diff of the selected record. Red = the plugins disagree; "
                "the last one in the load order wins.\n\n"
                "Double-click any row for the full value, one tab per plugin. Fields "
                "stored as binary are decoded rather than shown raw, so an edit reads "
                "as a change instead of a wall of base64:\n"
                "  \u2022 bytecode -- disassembled to named script instructions. Spans the "
                "disassembler cannot decode are printed as offset/hex/ASCII rather than "
                "guessed at, and a 'decoded: N%' header says how much was understood.\n"
                "  \u2022 variables -- the script's local variable names, in declaration order.\n"
                "  \u2022 landscape (vertex heights, normals, colours, textures, world map) "
                "-- decoded to one terrain row per line, so the diff shows which rows of "
                "the cell moved. Heights are reconstructed to absolute world units.\n"
                "  \u2022 path grid connections -- decoded to a per-point adjacency list "
                "(point -> its neighbours), so you can see which nodes were rewired."
            ),
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
        if build_explorer is not None:
            map_button = ttk.Button(
                btns, text=_("Conflict explorer..."), command=self._show_conflict_map
            )
            map_button.pack(side="left", padx=(8, 0))
            add_tooltip(
                map_button,
                _(
                    "Open the conflict explorer: a world map of WHERE your mods collide, "
                    "filterable by mod, plus exterior and interior lists. Click any cell "
                    "to see that cell's terrain surface, height difference and navigation "
                    "grid.\n\n"
                    "This is not the cell map: that shows which mods TOUCH which cells. "
                    "This shows which mods EDIT the land record and path grid there, and "
                    "how those edits conflict."
                ),
            )
        ttk.Button(btns, text=_("Close"), command=win.destroy).pack(side="right")

        self._refill_conflict_tree()

    def _detail_cache(self) -> tuple[Any, Callable[[Mapping[str, Any]], str] | None]:
        """Build the mtime cache and per-conflict signature function.

        The cache persists decoded cell JSON under the app directory, so the
        slow tes3conv decode is skipped for cells whose plugins have not
        changed since the last run -- which is what "help with load times"
        means here. The signature is the mtime and size of the conflict's
        plugins, so a re-saved plugin transparently invalidates just its cells.

        Returns:
            ``(cache, signature_for)``, or ``(None, None)`` if the viz cache is
            unavailable.
        """
        if viz_cache is None:
            return None, None
        cache = viz_cache.DetailCache(app_base_dir() / ".viz_cache")
        paths = dict(getattr(self, "_conf_paths", {}) or {})

        def signature_for(conflict: Mapping[str, Any]) -> str:
            """Change signature for one conflict's contributing plugins."""
            return viz_cache.plugin_signature(conflict.get("plugins") or [], paths)

        return cache, signature_for

    def _open_html_view(self, markup: str, stem: str, title: str = "") -> None:
        """Write a generated page beside the app and show it in-app.

        The visualisations are plain files rather than embedded widgets so the
        rendering code stays free of Tk -- which is what lets the hermetic
        suite test it at all -- but they are *displayed* through the same
        viewer chain as the cell map (pywebview, then tkinterweb, then the
        browser), not flung straight into a browser.

        Args:
            markup: The complete HTML document.
            stem: Filename stem; a timestamp is appended so successive views do
                not overwrite each other mid-comparison.
            title: Window title for the in-app viewer.
        """
        from datetime import datetime

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # noqa: DTZ005 - local clock is correct
        path = app_base_dir() / f"{stem}_{stamp}.html"
        self._last_written_view = str(path)
        try:
            path.write_text(markup, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(
                _("Could not write the page"),
                _("Writing %(path)s failed: %(error)s") % {"path": path, "error": exc},
            )
            return
        # Prefer the in-app viewer, exactly as the cell map does.
        opener = getattr(self, "open_html_in_app", None)
        try:
            if callable(opener):
                opener(path, title or stem)
            else:  # pragma: no cover - only if the mixin is used outside App
                webbrowser.open(path.as_uri())
        except Exception as exc:  # noqa: BLE001 - a viewer failure is non-fatal
            # The file exists either way, so tell the user where it is rather
            # than losing the work to a viewer that would not launch.
            messagebox.showinfo(
                _("Saved, but could not open a viewer"),
                _("The page was written to %(path)s (%(error)s)") % {"path": path, "error": exc},
            )

    def _show_conflict_map(self) -> None:
        """Build the conflict explorer off the main thread, then show it.

        Threaded because it is genuinely slow: decoding landscape and path-grid
        payloads means a tes3conv field lookup per cell. Doing that inline froze
        the window with no indication of why -- the first version of this did
        exactly that, and looked like a hang rather than work in progress.
        """
        conflicts = getattr(self, "_all_conflicts", None)
        if not conflicts or build_explorer is None or self.worker_running:
            return
        self.worker_running = True
        self.status_var.set(_("Building the conflict explorer..."))
        threading.Thread(target=self._explorer_worker, args=(list(conflicts),), daemon=True).start()

    def _explorer_worker(self, conflicts: list[dict]) -> None:
        """Decode, render and write the explorer, then hand back to the UI.

        Args:
            conflicts: The conflict list to render.
        """
        written: str | None = None
        error = ""
        cells = 0
        if build_explorer is None:  # pragma: no cover - guarded by the caller too
            self.root.after(0, lambda: self._explorer_done(None, "viz is unavailable", 0))
            return
        try:
            detail: dict = {}
            if collect_detail is not None and self._conf_session is not None:

                def fields_for(conflict: Mapping[str, Any]) -> dict:
                    """Look one conflict's fields up through tes3conv."""
                    _keys, per, _diff = core.diff_record_fields(
                        self._conf_session, conflict, self._conf_paths
                    )
                    return per

                cache, sig_for = self._detail_cache()
                # Only the sampled overview here (bounded to ~60 cells) so the
                # explorer opens promptly. Full-resolution cell pages are the
                # expensive part and are written in the background afterwards --
                # the world 3D terrain is not decoded at all (held back).
                detail = collect_detail(conflicts, fields_for, cache=cache, signature_for=sig_for)
            cells = len(detail)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # noqa: DTZ005 - local clock
            path = app_base_dir() / f"conflict_explorer_{stamp}.html"
            data_dir = path.stem + sidecar.DATA_SUFFIX
            markup = build_explorer(
                conflicts,
                detail=detail,
                cell_map_href="cell_map.html",
                data_dir=data_dir,
                embed_detail=False,
            )
            path.write_text(markup, encoding="utf-8")
            sidecar.write_sidecars(path, {"detail": detail})
            if viz_assets is not None:
                # Shared JS/CSS as files, so the pages are small and debuggable.
                viz_assets.write_assets(app_base_dir() / data_dir)
            written = str(path)
            if detail and build_cell_pages is not None:
                threading.Thread(
                    target=self._fill_cell_pages,
                    args=(list(conflicts), str(path), data_dir, len(detail)),
                    daemon=True,
                ).start()
        except Exception as exc:  # noqa: BLE001 - a view must never kill the scan
            error = str(exc)
        self.root.after(0, lambda: self._explorer_done(written, error, cells))

    def _fill_cell_pages(
        self, conflicts: list[dict], page_path: str, data_dir: str, limit: int
    ) -> None:
        """Decode full-resolution cells and write their pages, in the background.

        Runs after the explorer is already open, so its cost is never on the
        path to seeing it. Best-effort: any failure is swallowed, because the
        pages enrich the explorer rather than being required by it, and the
        client degrades to the sampled view for a cell whose page is not ready.

        Args:
            conflicts: The conflict rows.
            page_path: The explorer HTML path (for sibling filenames).
            data_dir: The sidecar folder name.
            limit: How many cells the overview covered, so the two agree.
        """
        if self._conf_session is None or collect_detail is None or build_cell_pages is None:
            return
        try:

            def fields_for(conflict: Mapping[str, Any]) -> dict:
                """Look one conflict's fields up through tes3conv."""
                _keys, per, _diff = core.diff_record_fields(
                    self._conf_session, conflict, self._conf_paths
                )
                return per

            cache, sig_for = self._detail_cache()
            full = collect_detail(
                conflicts, fields_for, limit=limit, stride=1, cache=cache, signature_for=sig_for
            )
            path = Path(page_path)
            pages = build_cell_pages(full, explorer_href=path.name, cell_map_href="cell_map.html")
            sidecar.write_sidecars(path, per_cell=full, cell_pages=pages)
            trace(f"conflict explorer: {len(pages)} full-resolution cell page(s) written")
        except Exception:  # noqa: BLE001 - enrichment only; the explorer is already open
            trace("conflict explorer: cell-page fill FAILED:\n" + traceback.format_exc())

    def _explorer_done(self, path: str | None, error: str, cells: int) -> None:
        """Open the built explorer, or report why it could not be built.

        Args:
            path: The written page, or ``None`` on failure.
            error: The failure message, if any.
            cells: How many cells got local detail.
        """
        self.worker_running = False
        if path is None:
            self.status_var.set(_("The conflict explorer could not be built."))
            messagebox.showerror(
                _("Could not build the conflict explorer"), _("%(error)s") % {"error": error}
            )
            return
        self._last_explorer_file = path
        self.status_var.set(
            _("Conflict explorer: %(cells)d cell(s) with local detail.") % {"cells": cells}
        )
        opener = getattr(self, "open_html_in_app", None)
        if callable(opener):
            opener(Path(path), _("Conflict explorer"))

    def _visualise_field(self, key: str, plugins: Sequence[str], per: Mapping[str, Any]) -> None:
        """Open the right visualisation for the selected field.

        Which view makes sense is a property of the field, so the button is
        contextual rather than a menu of mostly-inapplicable options.

        Args:
            key: The flattened field name the user selected.
            plugins: The plugins that touch this record, in load order.
            per: Field values per plugin.
        """
        winner = plugins[-1] if plugins else ""
        loser = plugins[-2] if len(plugins) > 1 else ""
        cell = str(getattr(self, "_conf_record_label", "") or "")

        def value(plugin: str, field: str, default: object = "") -> Any:  # noqa: ANN401
            """Read one field for one plugin, tolerating a missing record.

            Returns ``Any`` because a flattened tes3conv record holds whatever
            the field is -- base64 text for the grids, a list for ``points``, a
            float for the offset. Narrowing here would just move the cast.

            Args:
                plugin: The plugin to read from.
                field: The flattened field name.
                default: Returned when the plugin has no such field.

            Returns:
                The field value, or ``default``.
            """
            return (per.get(plugin) or {}).get(field, default)

        try:
            if key == "connections" and build_pathgrid_graph is not None:
                markup = build_pathgrid_graph(
                    value(winner, "connections"),
                    value(winner, "points", None),
                    winner_name=winner,
                    loser_value=value(loser, "connections", None) or None,
                    loser_points=value(loser, "points", None),
                    loser_name=loser,
                    cell_label=cell,
                )
                self._open_html_view(markup, "pathgrid")
                return
            if key == "vertex_heights.data" and loser and build_height_delta is not None:
                markup = build_height_delta(
                    value(winner, key),
                    value(loser, key),
                    winner_name=winner,
                    loser_name=loser,
                    winner_offset=_as_float(value(winner, "vertex_heights.offset", 0.0)),
                    loser_offset=_as_float(value(loser, "vertex_heights.offset", 0.0)),
                    cell_label=cell,
                )
                self._open_html_view(markup, "height_delta")
                return
            if key == "vertex_heights.data" and build_terrain_3d is not None:
                # Only one plugin has the field, so there is nothing to
                # subtract -- show the surface itself instead of refusing.
                markup = build_terrain_3d(
                    {
                        p: (value(p, key), _as_float(value(p, "vertex_heights.offset", 0.0)))
                        for p in plugins
                        if value(p, key)
                    },
                    cell_label=cell,
                )
                self._open_html_view(markup, "terrain")
        except Exception as exc:  # noqa: BLE001 - a bad record must not kill the window
            messagebox.showerror(_("Could not build the view"), _("%(error)s") % {"error": exc})

    def _show_terrain_3d(self, plugins: Sequence[str], per: Mapping[str, Any]) -> None:
        """Open the 3D surface for every plugin that has terrain here.

        Args:
            plugins: The plugins that touch this record, in load order.
            per: Field values per plugin.
        """
        if build_terrain_3d is None:
            return
        surfaces = {
            p: (
                (per.get(p) or {}).get("vertex_heights.data", ""),
                _as_float((per.get(p) or {}).get("vertex_heights.offset", 0.0)),
            )
            for p in plugins
            if (per.get(p) or {}).get("vertex_heights.data")
        }
        try:
            markup = build_terrain_3d(
                surfaces, cell_label=str(getattr(self, "_conf_record_label", "") or "")
            )
        except Exception as exc:  # noqa: BLE001 - a bad record must not kill the window
            messagebox.showerror(_("Could not build the view"), _("%(error)s") % {"error": exc})
            return
        self._open_html_view(markup, "terrain")

    def _add_field_view_buttons(
        self, bar: ttk.Frame, key: str, plugins: Sequence[str], per: Mapping[str, Any]
    ) -> None:
        """Add the visualisation buttons that apply to this field.

        Contextual by design: only the views that can say something about this
        field appear, so the bar never offers an action that would open an
        empty page.

        Args:
            bar: The detail window's button row.
            key: The flattened field name being shown.
            plugins: The plugins that touch this record, in load order.
            per: Field values per plugin.
        """
        has_terrain = sum(1 for p in plugins if (per.get(p) or {}).get("vertex_heights.data"))

        if key == "connections" and build_pathgrid_graph is not None:
            button = ttk.Button(
                bar,
                text=_("Show graph..."),
                command=lambda: self._visualise_field(key, plugins, per),
            )
            button.pack(side="left", padx=(12, 0))
            add_tooltip(
                button,
                _(
                    "Draw the path grid as a navigation graph, with the edges this "
                    "plugin added in green and the ones it removed in red.\n\n"
                    "A mod that only REMOVES edges has probably rebuilt its path grid "
                    "by accident -- that breaks NPC movement and nothing else reports it."
                ),
            )
            return

        if key == "vertex_heights.data" and build_height_delta is not None and len(plugins) > 1:
            button = ttk.Button(
                bar,
                text=_("Show difference..."),
                command=lambda: self._visualise_field(key, plugins, per),
            )
            button.pack(side="left", padx=(12, 0))
            add_tooltip(
                button,
                _(
                    "Decode both versions to absolute heights and subtract them: red "
                    "where the winner raised the ground, blue where it lowered it.\n\n"
                    "Comparing the raw values above is misleading. Heights are stored "
                    "as cumulative deltas, so moving ONE vertex changes every byte "
                    "after it and two nearly-identical cells look completely different."
                ),
            )

        if has_terrain and build_terrain_3d is not None:
            button = ttk.Button(
                bar,
                text=_("Show in 3D..."),
                command=lambda: self._show_terrain_3d(plugins, per),
            )
            button.pack(side="left", padx=(8, 0))
            add_tooltip(
                button,
                _(
                    "Draw the cell's terrain as a surface you can rotate, with each "
                    "plugin's version switchable in place. Drag to turn it.\n\n"
                    "Shading follows slope rather than height, which reads as terrain."
                ),
            )

    def _dump_conflict_json(self) -> None:
        """Write the tes3conv JSON for every scanned plugin to a chosen folder."""
        if self._conf_session is None or not self._conf_paths:
            return
        folder = filedialog.askdirectory(title=_("Dump tes3conv JSON to folder"))
        if not folder:
            return
        try:
            n = core.dump_tes3conv_json(
                self._conf_session, list(self._conf_paths.keys()), self._conf_paths, folder
            )
            self.status_var.set(
                ngettext(
                    "Wrote %(count)d JSON file to %(folder)s",
                    "Wrote %(count)d JSON files to %(folder)s",
                    n,
                )
                % {"count": n, "folder": folder}
            )
            if n:
                messagebox.showinfo(
                    _("JSON dumped"),
                    ngettext(
                        "Wrote %(count)d tes3conv JSON file to:\n%(folder)s",
                        "Wrote %(count)d tes3conv JSON files to:\n%(folder)s",
                        n,
                    )
                    % {"count": n, "folder": folder},
                )
            else:
                messagebox.showwarning(
                    _("Nothing written"),
                    _(
                        "No JSON was written. The tes3conv session may have been cleared — "
                        "re-run Check Conflicts, then dump again."
                    ),
                )
        except Exception as e:  # noqa: BLE001
            # user-facing dump; any failure becomes an error dialog
            messagebox.showerror(_("Dump failed"), str(e))

    def _on_conflict_select(self) -> None:
        tree = getattr(self, "_conf_tree", None)
        sel = tree.selection() if tree else None
        if not sel:
            return
        self._populate_field_diff(self._shown_conflicts[int(sel[0])])

    def _show_field_detail(self) -> None:
        """Pop up the full value of the selected field for each plugin.

        One tab per plugin, pretty-printed with JSON syntax highlighting (and,
        for text fields like book/dialogue content, the embedded HTML-ish
        markup broken out too). Uses whatever theme is picked next to the Log
        panel, so the two stay in sync. For long fields like 'references'
        that get truncated in the table.
        """
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
        win.geometry("1040x680")
        note = "last plugin wins · ★ orange = your custom mod"
        if key == "bytecode" and listing_for_bytecode_field is not None:
            note += " · shown disassembled; undecoded spans are printed as hex"
        elif key == "variables" and variables_text_for_field is not None:
            note += " · decoded to local variable names"
        elif describe_field is not None and (described := describe_field(key)):
            note += f" · {described}"
        ttk.Label(win, text=f"{key}   ({note})", padding=8).pack(anchor="w")
        bar = ttk.Frame(win, padding=(8, 0))
        bar.pack(fill="x")
        wrap_var = tk.BooleanVar(value=True)
        texts: list[tk.Text] = []

        def _apply_wrap() -> None:
            w: Literal["word", "none"] = "word" if wrap_var.get() else "none"
            for st in texts:
                st.configure(state="normal")
                st.configure(wrap=w)
                st.configure(state="disabled")

        ttk.Checkbutton(bar, text=_("Word wrap"), variable=wrap_var, command=_apply_wrap).pack(
            side="left"
        )
        self._add_field_view_buttons(bar, key, plugins, per)
        ttk.Label(
            bar,
            text=_("Syntax highlighting: %(theme)s") % {"theme": self.log_theme_var.get()},
            foreground=DARK["fg_dim"],
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
            elif is_plain_string and text_for_field is not None:
                # Landscape grids and path-grid edges are base64 too, and just
                # as unreadable: one moved vertex changes the whole string.
                # The whole record is passed because some of these fields only
                # mean something beside a sibling -- heights need their offset,
                # edges need their points.
                listing = text_for_field(key, val, per[p])
            if listing is not None:
                text, is_listing, is_plain_string = listing, True, False
            elif is_plain_string:
                text = str(val)
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

    def _refill_conflict_tree(self) -> None:
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

    def _save_conflicts_csv(self) -> None:
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
            self.status_var.set(_("Conflict report saved: %(path)s") % {"path": path})
        except OSError as e:
            messagebox.showerror(_("Save failed"), str(e))
