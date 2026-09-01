"""The Patch Builder: review and change a patch before it is written.

**Why this is a window and not a counter.** Queuing a record is a decision, and
decisions get revisited: you pick a winner, look at three more conflicts,
realise the first one should have taken one field from somewhere else, and want
to change it. With only a count on a button there is nowhere to do that -- the
only way to correct a mistake is to write the patch and start again.

So the queue is a thing you can see and edit. It stays open beside the conflict
list, updates as you add to it, and nothing is written until you say so.

**Everything here is still additive.** The queue holds *decisions*, not data;
no mod file is opened for writing at any point, and the output is one new
plugin that loads last. Deleting it restores the previous behaviour exactly.

The state lives here rather than in the conflicts window because it outlives
it: closing the conflict list should not silently discard a queue you have been
building.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any, Final

from wraithguard.gui import case_insensitive_filetypes, rtl
from wraithguard.gui.theme import DARK, apply_titlebar_theme
from wraithguard.gui.widgets import add_tooltip
from wraithguard.i18n import gettext as _
from wraithguard.logging_setup import get_logger
from wraithguard.patch.merge import FieldValue
from wraithguard.patch.queue import PatchQueue, base_from_conflicts
from wraithguard.patch.service import DEFAULT_NAME, PatchServiceError, build_record_patch

if TYPE_CHECKING:
    from collections.abc import Sequence

    from wraithguard.patch import Choice, Merge, Selection

LOG: Final = get_logger(__name__)

#: How a queued record is described in the tree.
WHOLE: Final = "whole record"
MERGED: Final = "merged"


class PatchBuilderMixin:
    """The queue of patch decisions, and the window that edits it."""

    if TYPE_CHECKING:  # pragma: no cover - declarations for the host class
        # tk.Tk, not tk.Misc: App is built on a real toplevel and these
        # windows call transient()/title()/geometry() on it, which live on Wm
        # and not on Misc. Declaring the weaker type here type-checked fine and
        # hid those calls from mypy entirely.
        root: tk.Tk
        _conflict_win: tk.Toplevel | None
        _conf_session: Any
        _conf_paths: dict[str, str]
        _conf_scan_args: tuple
        _shown_conflicts: list[dict]
        order_panel: Any

        def _ensure_conflict_session(self, conv: str | None = ...) -> bool: ...
        def _apply_exclusions(self, order: Sequence[str]) -> list[str]: ...

    # -- state ---------------------------------------------------------
    #
    # The rules live in wraithguard.patch.queue, which imports no widgets and
    # is tested without a display. These are the handles the window needs.

    def patch_queue(self) -> PatchQueue:
        """The queued decisions.

        Returns:
            The queue, created on first use. Lazily attached because this is a
            mixin with no constructor of its own.
        """
        queue = getattr(self, "_patch_queue", None)
        if queue is None:
            queue = PatchQueue()
            self._patch_queue = queue
        return queue

    def patch_selections(self) -> list[Selection]:
        """Records queued to be taken whole.

        Returns:
            The list, in the order chosen.
        """
        return self.patch_queue().selections

    def patch_merges(self) -> dict[tuple[str, str], list[Choice]]:
        """Field decisions queued (from a plugin or literal), keyed by ``(type, key)``.

        Returns:
            The mapping.
        """
        return self.patch_queue().fields

    def patch_count(self) -> int:
        """How many records the patch would carry.

        Returns:
            Whole-record choices plus field-level merges.
        """
        return len(self.patch_queue())

    def queue_whole_record(self, selection: Selection) -> None:
        """Queue a record to be taken whole, and redraw.

        Args:
            selection: The record and the plugin whose version wins.
        """
        self.patch_queue().add_whole(selection)
        self.refresh_patch_views()

    def queue_field(self, record_type: str, key: str, choice: Choice) -> None:
        """Queue one field of a record, and redraw.

        Args:
            record_type: The record's type.
            key: Its identifying key.
            choice: The field, taken from a plugin (``FieldChoice``) or given a
                typed value (``FieldValue``).
        """
        self.patch_queue().add_field(record_type, key, choice)
        self.refresh_patch_views()

    def _merge_base(self, record_type: str, key: str) -> str:
        """Which plugin supplies the fields a merge does not choose.

        Args:
            record_type: The record's type.
            key: Its identifying key.

        Returns:
            The plugin that currently wins, or an empty string when the record
            is no longer in the scan.
        """
        return base_from_conflicts(getattr(self, "_shown_conflicts", []) or [], record_type, key)

    def _merges_as_objects(self) -> list[Merge]:
        """The queued field choices, as the service takes them.

        Returns:
            One :class:`~wraithguard.patch.Merge` per record.
        """
        return self.patch_queue().merges(self._merge_base)

    # -- the window ----------------------------------------------------

    def show_patch_builder(self) -> None:
        """Open the Patch Builder, or raise it if it is already open."""
        win = getattr(self, "_patch_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return

        parent = getattr(self, "_conflict_win", None)
        if parent is None or not parent.winfo_exists():
            parent = self.root
        win = tk.Toplevel(parent)
        self._patch_win = win
        apply_titlebar_theme(win)
        win.title(_("Patch Builder"))
        win.configure(bg=DARK["bg"])
        win.geometry("760x460")

        ttk.Label(
            win,
            foreground=DARK["fg_dim"],
            padding=(8, 6, 8, 2),
            text=_(
                "Records queued for the patch. Nothing is written until you press "
                "Write. Your mods are never modified."
            ),
        ).pack(fill="x")

        frame = ttk.Frame(win, padding=8)
        frame.pack(fill="both", expand=True)
        cols = ("what", "mode", "source")
        tree = ttk.Treeview(frame, columns=cols, show="tree headings", style="Conf.Treeview")
        tree.heading("#0", text=_("Record"))
        tree.column("#0", width=300, stretch=True)
        for name, title, width in (
            ("what", _("Field"), 180),
            ("mode", _("How"), 110),
            ("source", _("From"), 200),
        ):
            tree.heading(name, text=title)
            tree.column(name, width=width, anchor="w", stretch=(name == "source"))
        rtl.apply_rtl_to_treeview(tree)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self._patch_tree = tree

        self._patch_summary = ttk.Label(win, foreground=DARK["fg_dim"], padding=(8, 0))
        self._patch_summary.pack(fill="x")

        row = ttk.Frame(win, padding=8)
        row.pack(fill="x")
        remove = ttk.Button(row, text=_("Remove"), command=self._remove_patch_entry)
        remove.pack(side="left")
        add_tooltip(
            remove,
            _(
                "Drop the selected record, or just the selected field. Removing a "
                "record's last field drops the record too."
            ),
        )
        ttk.Button(row, text=_("Clear all"), command=self._clear_patch).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(row, text=_("Close"), command=win.destroy).pack(side="right")
        self._patch_write = ttk.Button(row, text=_("Write patch..."), command=self.write_patch)
        self._patch_write.pack(side="right", padx=(0, 8))

        self.refresh_patch_views()

    def refresh_patch_views(self) -> None:
        """Redraw the queue wherever it is shown.

        Safe to call whether or not the window is open, so the callers that
        change the queue do not have to know.
        """
        button = getattr(self, "_patch_button", None)
        if button is not None and button.winfo_exists():
            count = self.patch_count()
            button.configure(
                text=(
                    _("Patch Builder... (%(count)d)") % {"count": count}
                    if count
                    else _("Patch Builder...")
                )
            )

        tree = getattr(self, "_patch_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        tree.delete(*tree.get_children())

        for selection in self.patch_selections():
            tree.insert(
                "",
                "end",
                iid=f"whole::{selection.record_type}::{selection.key}",
                text=f"{selection.record_type}  {selection.key}",
                values=("", WHOLE, selection.plugin),
            )

        for (record_type, key), choices in self.patch_merges().items():
            base = self._merge_base(record_type, key)
            parent = tree.insert(
                "",
                "end",
                iid=f"merge::{record_type}::{key}",
                text=f"{record_type}  {key}",
                values=("", MERGED, _("base: %(base)s") % {"base": base or _("unknown")}),
                open=True,
            )
            for choice in choices:
                if isinstance(choice, FieldValue):
                    verb, source = _("set to"), str(choice.value)
                else:
                    verb, source = _("from"), choice.plugin
                tree.insert(
                    parent,
                    "end",
                    iid=f"field::{record_type}::{key}::{choice.path}",
                    text="",
                    values=(choice.path, verb, source),
                )

        summary = getattr(self, "_patch_summary", None)
        if summary is not None and summary.winfo_exists():
            count = self.patch_count()
            summary.configure(
                text=(
                    _("%(count)d record(s) queued.") % {"count": count}
                    if count
                    else _("Nothing queued yet. Add records from the conflict list.")
                )
            )
        write = getattr(self, "_patch_write", None)
        if write is not None and write.winfo_exists():
            write.configure(state="normal" if self.patch_count() else "disabled")

    def _remove_patch_entry(self) -> None:
        """Drop whatever is selected: a whole record, a merge, or one field."""
        tree = getattr(self, "_patch_tree", None)
        selected = tree.selection() if tree else ()
        if not selected:
            return
        for iid in selected:
            parts = iid.split("::")
            kind = parts[0]
            if kind in ("whole", "merge") and len(parts) == 3:
                self.patch_queue().remove_record(parts[1], parts[2])
            elif kind == "field" and len(parts) == 4:
                self.patch_queue().remove_field(parts[1], parts[2], parts[3])
        self.refresh_patch_views()

    def _clear_patch(self) -> None:
        """Empty the queue, after asking."""
        if not self.patch_count():
            return
        if not messagebox.askokcancel(
            _("Clear the patch?"),
            _("This drops every queued record. Nothing has been written, so nothing is lost."),
        ):
            return
        self.patch_queue().clear()
        self.refresh_patch_views()

    # -- writing -------------------------------------------------------

    def write_patch(self) -> None:
        """Confirm, then write the queue as one new plugin -- or onto an existing one.

        A patch does not have to be finished in one sitting. When the chosen
        path already exists, this offers Append as well as Replace: Append
        reads that file's own records back in and carries them into the new
        build alongside whatever is queued today, so a record chosen last
        week is not lost just because this session never mentions it again.
        """
        if not self.patch_count() or self._conf_session is None:
            return
        # Refresh both the session and the plugin -> path map against the
        # current load order before measuring anything. Neither is done
        # automatically just because a patch is queued: a plugin enabled (or
        # a master newly required) after the session was last established --
        # by opening this window, or by Check Conflicts -- would otherwise
        # leave self._conf_paths silently missing it, and _plugin_sizes()
        # reads that map directly rather than re-resolving paths itself.
        if not self._ensure_conflict_session():
            messagebox.showerror(
                _("tes3conv not found"),
                _("Cannot measure master sizes without tes3conv. Set its path and try again."),
            )
            return
        order = self._apply_exclusions(self.order_panel.get_enabled())
        target = self._ask_patch_path()
        if target is None:
            return

        merges = self._merges_as_objects()
        unknown = [entry for entry in merges if not entry.base_plugin]
        if unknown:
            messagebox.showerror(
                _("Rescan needed"),
                _(
                    "%(what)s is no longer in the scan, so there is no way to tell "
                    "which plugin its unchosen fields should come from. Rescan and "
                    "queue it again."
                )
                % {"what": f"{unknown[0].record_type} {unknown[0].key}"},
            )
            return

        carried: list[Any] = []
        if target.exists():
            append = messagebox.askyesnocancel(
                _("Append or replace?"),
                _(
                    "%(name)s already exists. This session has %(count)d record(s) "
                    "queued.\n\n"
                    "Append keeps everything already written and adds these on top "
                    "-- the way to build one patch across several sessions.\n\n"
                    "Replace throws away what is already there and writes only "
                    "this session's records.\n\n"
                    "Your mods are NOT modified either way, and deleting the patch "
                    "restores your previous behaviour completely. Load it LAST, "
                    "and back up your saves."
                )
                % {"name": target.name, "count": self.patch_count()},
            )
            if append is None:
                return
            if append:
                carried = self._conf_session.records(target)
                if not carried:
                    messagebox.showerror(
                        _("Could not read the existing patch"),
                        _(
                            "%(name)s could not be decoded, so there is nothing to "
                            "append to. Run Write again and choose Replace, or "
                            "check the file opens in another tool first."
                        )
                        % {"name": target.name},
                    )
                    return
        elif not messagebox.askokcancel(
            _("Write patch?"),
            _(
                "%(count)d record(s) will be written to:\n%(path)s\n\n"
                "It carries whole records chosen by you, and loads last. Your mods "
                "are NOT modified, and deleting the patch restores your previous "
                "behaviour completely.\n\n"
                "Load it LAST, and back up your saves."
            )
            % {"count": self.patch_count(), "path": target},
        ):
            return

        try:
            wanted = {entry.plugin for entry in self.patch_selections()}
            for entry in merges:
                wanted |= entry.plugins
            records = {
                name: self._conf_session.records(self._conf_paths.get(name, "")) for name in wanted
            }
            sizes = self._plugin_sizes(order)
            result = build_record_patch(
                self.patch_selections(),
                records,
                order,
                sizes,
                self._conf_session.exe,
                target,
                merges=merges,
                carried=carried,
                report=LOG.info,
            )
        except PatchServiceError as exc:
            messagebox.showerror(_("Patch failed"), str(exc))
            return

        self.patch_queue().clear()
        self.refresh_patch_views()
        carried_note = (
            _(" (%(carried)d carried forward from before)") % {"carried": result.carried}
            if result.carried
            else ""
        )
        messagebox.showinfo(
            _("Patch written"),
            _(
                "%(records)d record(s) written to:\n%(path)s%(carried)s\n\n"
                "Declares %(masters)d master(s). Add it to your load order LAST."
            )
            % {
                "records": result.records,
                "path": result.output,
                "carried": carried_note,
                "masters": len(result.masters),
            },
        )

    def _plugin_sizes(self, order: Sequence[str]) -> dict[str, int]:
        """Measure every plugin in the order, for the patch header.

        Args:
            order: The load order.

        Returns:
            Name to size in bytes, skipping anything unmeasurable -- the
            service names a missing one, which is more use than failing here
            without saying which.
        """
        sizes: dict[str, int] = {}
        for name in order:
            path = self._conf_paths.get(name)
            if not path:
                continue
            try:
                sizes[name] = Path(path).stat().st_size
            except OSError:
                continue
        return sizes

    def _ask_patch_path(self) -> Path | None:
        """Ask where the patch should be written.

        Returns:
            The file, or ``None`` if the dialog was dismissed.
        """
        picked = filedialog.asksaveasfilename(
            title=_("Write the patch as"),
            initialfile=DEFAULT_NAME,
            defaultextension=".esp",
            filetypes=case_insensitive_filetypes(
                ((_("Morrowind plugin"), "*.esp"), (_("All files"), "*.*"))
            ),
        )
        return Path(picked) if picked else None
