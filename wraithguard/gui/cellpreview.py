"""The Cell Preview window: place a cell's objects in the 3D mesh viewer.

Mixed into ``App`` (like the tes3cmd and conflict windows). It resolves a cell
across the sorted load order -- every reference's winning object and world
transform -- bakes the placed meshes into cell space, and shows them through the
same viewer the single-mesh view uses, alongside an audit of what the load order
does to the cell (overrides, deletions, moves, missing meshes).

The heavy lifting is pure and lives in :mod:`wraithguard.scene`; this file is only
the Tk glue: pick a cell, run it off the UI thread, print the audit, open the
page. Meshes are textured through the same
:class:`~wraithguard.nif.textures.TextureResolver` the single-mesh view uses. A
prototype -- interior and exterior cells (exteriors show their statics; terrain,
water and adjacent-cell rings come later), and no editing.
"""

from __future__ import annotations

import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING, Any

import wraithguard_toolkit as core
from wraithguard.esp.io import EspError
from wraithguard.esp.plugin import read_header, read_plugin
from wraithguard.gui.widgets import QueueWriter
from wraithguard.i18n import gettext as _
from wraithguard.nif.geometry import world_meshes
from wraithguard.nif.textures import TextureResolver
from wraithguard.nif.viewer import build_viewer_page
from wraithguard.plugins import PluginFileIndex
from wraithguard.scene import CellKey, LoadedPlugin, preview_cell
from wraithguard.tracing import trace

if TYPE_CHECKING:
    import queue
    import tkinter as tk
    from collections.abc import Callable, Sequence

    from wraithguard.nif.geometry import Mesh

#: OpenMW's binary content files -- the ones made of TES3 records. A load order
#: also lists ``.omwscripts`` (a plain-text list of Lua scripts, not records),
#: which :func:`~wraithguard.esp.plugin.read_plugin` cannot and should not parse;
#: they hold no cell content, so they are skipped without a word.
_CONTENT_SUFFIXES = frozenset({".esm", ".esp", ".omwaddon", ".omwgame"})


class CellPreviewMixin:
    """The Cell Preview window and its worker (mixed into ``App``)."""

    if TYPE_CHECKING:
        # The host contract -- these live on ``App``. Declared, not silenced, so
        # mypy checks the half that is here against what the host must provide.
        root: tk.Tk
        log_queue: queue.Queue
        status_var: tk.StringVar
        worker_running: bool
        _current_plan: dict | None
        cellpreview_button: ttk.Button
        sort_button: ttk.Button
        export_button: ttk.Button
        conflicts_button: ttk.Button
        cellmap_button: ttk.Button
        resource_button: ttk.Button

        def _apply_exclusions(self, order: list[str]) -> list[str]: ...
        def _plan_scan_dirs(self) -> list[str]: ...
        def _read_mesh_anywhere(
            self, dirs: Sequence[Path], vfs_path: str
        ) -> Any: ...  # noqa: ANN401
        def _open_html_view(self, markup: str, stem: str, title: str = "") -> None: ...
        def _schedule_ui(
            self, delay_ms: int, func: Callable[..., Any], *args: Any  # noqa: ANN401
        ) -> None: ...

    def on_cell_preview(self) -> None:
        """Ask for a cell, then build its preview in a worker."""
        if self.worker_running or not self._current_plan:
            return
        chosen = self._ask_cell()
        if chosen is None:
            return
        key, label = chosen
        order = self._apply_exclusions(self.order_panel.get_enabled())  # type: ignore[attr-defined]
        if not order:
            return
        dirs = self._plan_scan_dirs()
        self.worker_running = True
        for button in (
            self.sort_button,
            self.export_button,
            self.conflicts_button,
            self.cellmap_button,
            self.cellpreview_button,
            self.resource_button,
        ):
            button.configure(state="disabled")
        self.status_var.set(_("Building cell preview..."))
        threading.Thread(
            target=self._cellpreview_worker, args=(order, dirs, key, label), daemon=True
        ).start()

    def _ask_cell(self) -> tuple[CellKey, str] | None:
        """Modal: pick an interior cell by name or an exterior cell by grid.

        Returns:
            ``(CellKey, label)`` or ``None`` if cancelled.
        """
        import tkinter as tk

        win = tk.Toplevel(self.root)
        win.title(_("Cell to preview"))
        win.transient(self.root)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        mode = tk.StringVar(value="interior")
        name_var = tk.StringVar()
        x_var = tk.StringVar(value="0")
        y_var = tk.StringVar(value="0")
        result: dict[str, tuple[CellKey, str] | None] = {"value": None}

        ttk.Radiobutton(frame, text=_("Interior cell"), value="interior", variable=mode).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(frame, text=_("Name:")).grid(row=1, column=0, sticky="e", padx=(16, 4), pady=2)
        ttk.Entry(frame, textvariable=name_var, width=34).grid(
            row=1, column=1, columnspan=2, sticky="w", pady=2
        )
        ttk.Radiobutton(frame, text=_("Exterior cell"), value="exterior", variable=mode).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )
        ttk.Label(frame, text=_("Grid X, Y:")).grid(row=3, column=0, sticky="e", padx=(16, 4))
        ttk.Entry(frame, textvariable=x_var, width=8).grid(row=3, column=1, sticky="w")
        ttk.Entry(frame, textvariable=y_var, width=8).grid(row=3, column=2, sticky="w")

        def accept() -> None:
            """Build the key from the fields and close."""
            if mode.get() == "interior":
                name = name_var.get().strip()
                if name:
                    result["value"] = (CellKey(interior=name.lower()), name)
            else:
                try:
                    grid = (int(x_var.get().strip()), int(y_var.get().strip()))
                except ValueError:
                    return
                result["value"] = (CellKey(grid=grid), f"Wilderness ({grid[0]}, {grid[1]})")
            win.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text=_("Preview"), command=accept).pack(side="left", padx=4)
        ttk.Button(buttons, text=_("Cancel"), command=win.destroy).pack(side="left")
        win.grab_set()
        self.root.wait_window(win)
        return result["value"]

    def _load_order_plugins(self, order: list[str], dirs: list[str]) -> list[LoadedPlugin]:
        """Parse every plugin in the order into a :class:`LoadedPlugin`.

        Args:
            order: Enabled plugin filenames, in load order.
            dirs: The data folders to resolve them in.

        Returns:
            One :class:`LoadedPlugin` per plugin that could be read.
        """
        plugins: list[LoadedPlugin] = []
        resolved = core.plugin_paths(order, PluginFileIndex(dirs))
        for name in order:
            path = resolved.get(name)
            if not path:
                continue
            if Path(name).suffix.lower() not in _CONTENT_SUFFIXES:
                continue  # .omwscripts and the like: not record files, no cells
            try:
                data = Path(path).read_bytes()
                records = read_plugin(data)
                masters = [master for master, _size in read_header(data).masters]
            except (OSError, ValueError, EspError) as exc:
                print(_("  skipped %(name)s: %(error)s") % {"name": name, "error": exc})
                continue
            plugins.append(LoadedPlugin(name=name, masters=masters, records=records))
        return plugins

    def _cellpreview_worker(
        self, order: list[str], dirs: list[str], key: CellKey, label: str
    ) -> None:
        """Off-thread: parse the order, resolve the cell, build and open the view."""
        writer = QueueWriter(self.log_queue)
        page: str | None = None
        trace(f"cell preview: start, {label!r}, {len(order)} plugin(s)")
        try:
            with redirect_stdout(writer.as_stream()), redirect_stderr(writer.as_stream()):
                print("\n" + "=" * 70)
                print(_(" CELL PREVIEW: %(label)s") % {"label": label})
                print("=" * 70)
                plugins = self._load_order_plugins(order, dirs)
                dir_paths = [Path(d) for d in dirs]

                def load_mesh(model: str) -> list[Mesh] | None:
                    """Resolve a model path across the data folders to its meshes."""
                    parsed = self._read_mesh_anywhere(dir_paths, f"meshes/{model}")
                    return world_meshes(parsed) if parsed is not None else None

                clock = time.perf_counter
                mark = clock()
                placements, audit, scene = preview_cell(plugins, key, load_mesh)
                print(f"  resolved cell in {clock() - mark:.1f}s")
                self._print_audit(audit, scene, len(placements))
                if not scene.meshes:
                    print(_("\n  Nothing to draw -- no placed meshes were found for this cell."))
                mark = clock()
                print(f"  indexing textures across {len(dir_paths)} folder(s)...")
                resolver = TextureResolver(dir_paths) if dir_paths else None
                print(f"  indexed textures in {clock() - mark:.1f}s")
                mark = clock()
                print("  assembling viewer page...")
                page = build_viewer_page(
                    [(label, scene.meshes)],
                    title=f"Cell preview: {label}",
                    resolver=resolver,
                )
                print(f"  built page in {clock() - mark:.1f}s ({len(page) / 1_048_576:.1f} MB)")
            status = _("Cell preview ready (%(drawn)d object(s) drawn).") % {"drawn": scene.drawn}
        except Exception:  # noqa: BLE001 -- worker top level reports into the log
            writer.write("\nERROR: cell preview failed:\n" + traceback.format_exc())
            status = "Cell preview failed -- see log."
            page = None
        self._schedule_ui(0, self._cellpreview_finished, page, label, status)

    @staticmethod
    def _print_audit(audit: Any, scene: Any, placement_count: int) -> None:  # noqa: ANN401
        """Print the cell audit as a stat block, mirroring the preview panel."""
        rows = [
            (_("references"), audit.references),
            (_("placed"), audit.placed),
            (_("no mesh record"), audit.no_mesh_record),
            (_("editor markers"), audit.editor_markers),
            (_("actors skipped"), audit.actors_skipped),
            (_("overridden by later"), audit.overridden_by_later),
            (_("deleted by later"), audit.deleted_by_later),
            (_("moved"), audit.moved),
            (_("meshes drawn"), scene.drawn),
            (_("meshes not found"), len(scene.missing_models)),
        ]
        for label, value in rows:
            print(f"  {label:.<24}{value:>6}")
        for missing in scene.missing_models[:20]:
            print(_("    missing mesh: %(model)s") % {"model": missing})

    def _cellpreview_finished(self, page: str | None, label: str, status: str) -> None:
        """Back on the UI thread: re-enable buttons and open the page."""
        self.worker_running = False
        self.sort_button.configure(state="normal")
        for button in (
            self.export_button,
            self.conflicts_button,
            self.cellmap_button,
            self.cellpreview_button,
            self.resource_button,
        ):
            button.configure(state="normal" if self._current_plan else "disabled")
        self.status_var.set(status)
        if page:
            self._open_html_view(page, "cell_preview", f"Cell preview: {label}")
