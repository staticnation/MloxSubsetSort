"""Every quest's journal stages, resolved and shown in order -- with what sets each one.

**Why this is not another `PluginViewMixin`.** That window's whole design is
built around one fact its own docstring states outright: judging a record
means reading it with tes3conv, "far too slow to do for a whole load order up
front" -- hence the lazy expansion, the background judging per opened group,
the batched inserts. None of that applies here. Resolving every quest and
scanning every script is a dict-and-sort and a token-stream scan over records
already in hand; it is the *reading* of those records that costs anything, and
that cost is paid exactly once, for the whole load order, when the window
opens -- not per group, not per click. So this window reads everything in one
background pass and fills the tree once, rather than building pluginview's
incremental machinery for a workload that does not have pluginview's shape.

**What the tree shows beyond the stage list.** Under each stage, a call site
that sets it (Stage 2) appears as a child row -- a standalone Script, another
stage's own result script, *or an ordinary NPC dialogue response*, whichever
plugin's surviving definition supplied it. That last source is not a minor
addition: most real quest progression happens through a regular topic
response whose result script happens to set the journal, not through the
journal-type topic's own entry, which is frequently empty. Missing it would
mean "Set by" was usually wrong about the one thing it exists to answer.
Selecting a stage or a call row shows its raw enclosing condition (Stage 3) in
the detail pane, verbatim source, never evaluated -- the same restraint the
disassembler already applies to compiled expressions, for the same reason: a
heuristic scan over arbitrary third-party mwscript cannot safely claim to know
what a condition means, only where it is. A stage's detail, and each of its
triggers', pulls in dialogue requirements (who has to be speaking, what
disposition or rank or filter has to hold) via the same ``condition_lines``
the field-diff viewer's own INFO rendering already uses -- the "Actor: ...
- If ..." lines its "Read as dialogue..." popup shows -- plus every other
notable thing that trigger's script does: item grants, disposition changes,
another script starting, whatever a mod actually does at that moment,
generically captured rather than matched against a curated list that would
always miss something. A "Set by"/"Also sets"/"Target stage" row that
resolves to a real stage is a questline link and can be double-clicked to
jump the tree there, including across quests.

**Why the detail pane is not a Treeview.** A ``ttk.Treeview`` cell is single
line, always -- there is no configuration that wraps long text inside one, so
a quest's full response text or a raw result script either gets truncated or
forces horizontal scrolling, both worse than actually wrapping it. The detail
pane is a plain scrollable frame of label pairs instead: a field name above,
its value below at ``wraplength``, keeping every field inspectable at
whatever width the pane actually has rather than a fixed column guess.

**What is reused.** The dark theme and reading plugins off the main thread via
``_schedule_ui`` are the same conventions the rest of the GUI already
follows -- there was no reason to invent different ones for this window
specifically.
"""

from __future__ import annotations

import itertools
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any, Final

from wraithguard.gui import rtl
from wraithguard.gui.theme import DARK, apply_titlebar_theme
from wraithguard.i18n import gettext as _
from wraithguard.logging_setup import get_logger
from wraithguard.patch.journal import Resolved, resolve_journals
from wraithguard.patch.journal_scripts import (
    Attachment,
    Effect,
    JournalCall,
    attach,
    calls_from_dialogue,
    calls_from_scripts,
    calls_from_stages,
    effects_from_dialogue,
    effects_from_scripts,
    effects_from_stages,
)
from wraithguard.tes3fields.dialogue import condition_lines

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

LOG: Final = get_logger(__name__)

#: Separator inside a stage row's tree id. A quest id cannot contain it, so a
#: split on it cannot be ambiguous. **Not** ``\x00``: Tcl's item ids are
#: null-terminated C strings under the hood, so an embedded NUL silently
#: truncates the id there rather than raising -- confirmed against this
#: environment's Tcl 8.6.14, where inserting ``f"{plugin}\x00{kind}"`` as a
#: child of a node already named ``plugin`` raises "Item {plugin} already
#: exists", because Tcl reads the child id back as just ``plugin``. Unit
#: Separator has no such special meaning to Tcl and round-trips cleanly.
SEP: Final = "\x1f"

#: The one placeholder node shown while the background read is in flight.
#: There is only ever one of these, unlike pluginview's per-group PENDING
#: markers, because this window fills in once rather than group by group.
LOADING: Final = "__loading__"

#: Prefix for a call-site child node's synthetic id. Not derived from quest or
#: stage ids at all (unlike a stage row's own SEP-joined id) -- a call site
#: has no natural composite key of its own, so :attr:`JournalViewMixin` tracks
#: it with a side dict instead, the same reason pluginview keeps
#: ``_plugin_rows`` alongside its own composite ids.
CALL_PREFIX: Final = "__call__"

#: Text colour for a stage that finishes its quest -- the one moment in a
#: quest's journal worth a glance drawing to it. Reuses the theme's existing
#: accent rather than adding a new hue to the palette.
FINISHED_TAG: Final = "journal-finished"

#: Text colour for a call-site child row, to read as a subordinate detail of
#: the stage above it rather than another stage.
CALL_TAG: Final = "journal-call"

#: Minimum wrap width for a detail-pane value label, used before the right
#: pane's first <Configure> event has actually reported a real width.
_MIN_WRAP: Final = 320


class JournalViewMixin:
    """A window showing every quest's journal stages, what sets each one, and why."""

    if TYPE_CHECKING:  # pragma: no cover - declarations for the host class
        root: tk.Tk
        status_var: tk.StringVar
        _conf_session: Any
        _conf_paths: dict[str, str]
        _conflict_win: tk.Toplevel | None
        order_panel: Any

        def _schedule_ui(
            self, delay_ms: int, func: Callable[..., Any], *args: Any  # noqa: ANN401
        ) -> None: ...
        def _ensure_conflict_session(self, conv: str | None = ...) -> bool: ...
        def _apply_exclusions(self, order: Sequence[str]) -> list[str]: ...

    # -- the window ------------------------------------------------------

    def show_journal_view(self) -> None:
        """Open the journal chains window, or raise it if already open.

        Needs a reading session up front, unlike the plugin view: there is no
        prior scan this can lean on for its tree structure, since a journal
        stage is not something Check Conflicts records as its own entry.
        """
        win = getattr(self, "_journal_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return

        if self._conf_session is None and not self._ensure_conflict_session():
            messagebox.showwarning(
                _("tes3conv needed"),
                _(
                    "Reading journal stages needs tes3conv, which does the binary "
                    "decoding.\n\nUse 'Set tes3conv...' in the Conflicts window to "
                    "point at it."
                ),
            )
            return

        order = self._apply_exclusions(self.order_panel.get_enabled())
        if not order:
            return

        win = tk.Toplevel(getattr(self, "_conflict_win", None) or self.root)
        self._journal_win = win
        apply_titlebar_theme(win)
        win.title(_("Journal chains"))
        win.configure(bg=DARK["bg"])
        win.geometry("1400x800")
        win.minsize(900, 500)

        ttk.Label(
            win,
            foreground=DARK["fg_dim"],
            padding=(8, 6, 8, 2),
            text=_(
                "Every quest's journal stages, sorted by journal index rather than "
                "file order, with the winning plugin's text, dialogue requirements, "
                "full result script, and everything else that script does -- item "
                "grants, disposition changes, other scripts starting, whatever a "
                "mod actually does at that stage. Read-only: nothing here writes."
            ),
        ).pack(fill="x")

        panes = ttk.PanedWindow(win, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(panes)
        nav = ttk.Treeview(left, show="tree headings", columns=("index",), style="Conf.Treeview")
        nav.heading("#0", text=_("Quest / stage / call"))
        nav.column("#0", width=380, stretch=True)
        nav.heading("index", text=_("Index"))
        nav.column("index", width=70, anchor="e", stretch=False)
        rtl.apply_rtl_to_treeview(nav)
        nav.tag_configure(FINISHED_TAG, foreground=DARK["accent"])
        nav.tag_configure(CALL_TAG, foreground=DARK["fg_dim"])
        nav_scroll = ttk.Scrollbar(left, orient="vertical", command=nav.yview)
        nav.configure(yscrollcommand=nav_scroll.set)
        nav.pack(side="left", fill="both", expand=True)
        nav_scroll.pack(side="right", fill="y")
        panes.add(left, weight=2)

        right = ttk.Frame(panes)
        canvas = tk.Canvas(right, bg=DARK["bg"], highlightthickness=0, bd=0)
        detail_scroll = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=detail_scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")

        body = ttk.Frame(canvas)
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_body_configure(_event: tk.Event[Any]) -> None:
            """Keep the scroll region matched to however tall the rows end up."""
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event: tk.Event[Any]) -> None:
            """Stretch the body to the canvas width and re-wrap future rows to match."""
            canvas.itemconfig(body_window, width=event.width)
            self._journal_wrap = max(event.width - 32, _MIN_WRAP)

        body.bind("<Configure>", _on_body_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event: tk.Event[Any]) -> None:
            """Windows/Mac deliver <MouseWheel> with a signed delta; scroll by it directly."""
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        def _on_wheel_up(_event: tk.Event[Any]) -> None:
            """X11 delivers the wheel as Button-4/5 instead of a MouseWheel delta."""
            canvas.yview_scroll(-1, "units")

        def _on_wheel_down(_event: tk.Event[Any]) -> None:
            """The X11 scroll-down half of the same wheel binding."""
            canvas.yview_scroll(1, "units")

        canvas.bind("<MouseWheel>", _on_mousewheel)
        canvas.bind("<Button-4>", _on_wheel_up)
        canvas.bind("<Button-5>", _on_wheel_down)

        panes.add(right, weight=3)

        self._journal_nav = nav
        self._journal_detail_canvas = canvas
        self._journal_detail_body = body
        self._journal_wrap = _MIN_WRAP
        self._journal_resolved: dict[str, list[Resolved]] = {}
        self._journal_incoming: dict[tuple[str, str], list[Attachment]] = {}
        self._journal_effects_by_owner: dict[tuple[str, str], list[Effect]] = {}
        self._journal_call_nodes: dict[str, Attachment] = {}
        self._stage_owner: dict[int, tuple[str, str]] = {}
        self._journal_info_quest: dict[str, str] = {}
        nav.bind("<<TreeviewSelect>>", lambda _event: self._on_journal_node())

        nav.insert("", "end", iid=LOADING, text=_("Reading the load order..."))

        # A left:right weight of 2:3 only governs how extra space is
        # *distributed* on resize; the initial split still needs an explicit
        # sash position, and that requires the window to actually have laid
        # out its widgets first.
        win.update_idletasks()
        panes.sashpos(0, 480)

        threading.Thread(target=self._journal_worker, args=(order,), daemon=True).start()

    def _journal_worker(self, order: Sequence[str]) -> None:
        """Read every plugin, resolve every quest, and scan every script, off the main thread.

        Args:
            order: The load order to read, exclusions already applied.
        """
        session = self._conf_session
        paths = self._conf_paths
        sources = {}
        for plugin in order:
            path = paths.get(plugin)
            if path:
                sources[plugin] = session.records(path)
        try:
            resolved = resolve_journals(sources, order)
            # Excluding a journal-type stage's own info_id keeps
            # calls_from_dialogue/effects_from_dialogue from double-reporting
            # what calls_from_stages/effects_from_stages already cover --
            # both sources will happily scan the same record otherwise, since
            # neither knows about the other's territory.
            journal_ids = {stage.info_id for stages in resolved.values() for stage in stages}
            calls = (
                calls_from_stages(resolved)
                + calls_from_scripts(sources, order)
                + calls_from_dialogue(sources, order, exclude_info_ids=journal_ids)
            )
            attachments = attach(calls, resolved)
            effects = (
                effects_from_stages(resolved)
                + effects_from_scripts(sources, order)
                + effects_from_dialogue(sources, order, exclude_info_ids=journal_ids)
            )
        except Exception:  # report it, don't crash the worker thread
            LOG.exception("resolving journal chains")
            self._schedule_ui(0, self._journal_failed)
            return
        self._schedule_ui(0, self._fill_journal_tree, resolved, attachments, effects)

    def _journal_failed(self) -> None:
        """Report a worker-thread failure without leaving the loading node stuck."""
        nav = getattr(self, "_journal_nav", None)
        if nav is not None and nav.exists(LOADING):
            nav.item(LOADING, text=_("Could not read the load order -- see the log."))

    def _fill_journal_tree(
        self,
        resolved: dict[str, list[Resolved]],
        attachments: list[Attachment],
        effects: list[Effect],
    ) -> None:
        """Populate the tree once the background read has finished.

        Args:
            resolved: Every quest's resolved stages, from
                :func:`~wraithguard.patch.journal.resolve_journals`.
            attachments: Every scanned call, linked to the stage it sets (if
                any), from :func:`~wraithguard.patch.journal_scripts.attach`.
            effects: Every scanned non-Journal statement, from every source
                (a stage's own script, a standalone Script, or an ordinary
                dialogue response) -- indexed here by owner so a trigger's
                detail can show what else it does without a second scan.
        """
        nav = self._journal_nav
        if nav.exists(LOADING):
            nav.delete(LOADING)
        self._journal_resolved = resolved

        effects_by_owner: dict[tuple[str, str], list[Effect]] = {}
        for effect in effects:
            effects_by_owner.setdefault((effect.owner_type, effect.owner_id), []).append(effect)
        self._journal_effects_by_owner = effects_by_owner

        # Two small reverse indices, built once here rather than recomputed on
        # every click: which quest owns a given stage object, and which
        # attachments target it. Keyed by id(stage) since Resolved is not
        # hashable -- these are the same object instances attach() itself
        # matched against, so identity comparison is exact, not approximate.
        stage_owner: dict[int, tuple[str, str]] = {
            id(stage): (quest, stage.info_id)
            for quest, stages in resolved.items()
            for stage in stages
        }
        self._stage_owner = stage_owner

        info_quest: dict[str, str] = {}
        for quest, stages in resolved.items():
            for stage in stages:
                info_quest.setdefault(stage.info_id, quest)
        self._journal_info_quest = info_quest

        incoming: dict[tuple[str, str], list[Attachment]] = {}
        for attachment in attachments:
            if attachment.stage is None:
                continue
            key = stage_owner.get(id(attachment.stage))
            if key is not None:
                incoming.setdefault(key, []).append(attachment)
        self._journal_incoming = incoming

        call_nodes: dict[str, Attachment] = {}
        call_counter = 0

        for quest in sorted(resolved):
            stages = resolved[quest]
            quest_node = nav.insert(
                "", "end", iid=quest, text=quest, values=(str(len(stages)),), open=False
            )
            for stage in stages:
                label = stage.text or _("(no text)")
                if stage.finished:
                    label = _("%(text)s (finished)") % {"text": label}
                stage_node = nav.insert(
                    quest_node,
                    "end",
                    iid=f"{quest}{SEP}{stage.info_id}",
                    text=label,
                    values=(str(stage.index),),
                    tags=(FINISHED_TAG,) if stage.finished else (),
                )
                for attachment in incoming.get((quest, stage.info_id), ()):
                    call_counter += 1
                    call_id = f"{CALL_PREFIX}{call_counter}"
                    nav.insert(
                        stage_node,
                        "end",
                        iid=call_id,
                        text=f"\u25c2 {self._source_label(attachment.call)}",
                        values=("",),
                        tags=(CALL_TAG,),
                    )
                    call_nodes[call_id] = attachment

        self._journal_call_nodes = call_nodes
        self.status_var.set(
            _("%(quests)d quest(s), %(stages)d stage(s), %(calls)d call site(s) found.")
            % {
                "quests": len(resolved),
                "stages": sum(len(stages) for stages in resolved.values()),
                "calls": len(attachments),
            }
        )

    # -- labelling ---------------------------------------------------------

    def _source_label(self, call: JournalCall) -> str:
        """A short label for where a call was found, for a tree row or detail value.

        Args:
            call: The call to describe.

        Returns:
            e.g. ``"A1_1_FargothRing: fargoth_1 (Tribunal.esm)"`` for an INFO,
            or ``"Script: FooScript (Patch.esp) [bytecode]"`` for a standalone
            one read from compiled data. The quest prefix on an INFO-owned
            call is a best-effort lookup, not a citation -- INFO ids are only
            guaranteed unique within their own topic, so an id reused across
            two different quests resolves to whichever this scan saw first.
        """
        if call.owner_type == "Script":
            base = f"Script: {call.owner_id}"
        else:
            owner_quest = self._journal_info_quest.get(call.owner_id, "")
            base = (
                f"{owner_quest}: {call.owner_id}" if owner_quest else f"Dialogue: {call.owner_id}"
            )
        plugin = f" ({call.plugin})" if call.plugin else ""
        tag = _(" [bytecode]") if call.from_bytecode else ""
        return f"{base}{plugin}{tag}"

    def _stage_label(self, stage: Resolved) -> str:
        """A short label for a stage as a jump target, e.g. in an "Also sets" row.

        Args:
            stage: The stage to describe.

        Returns:
            e.g. ``"A1_1_FargothRing [20]: I found Fargoth's ring."``
        """
        owner = self._stage_owner.get(id(stage))
        quest = owner[0] if owner else ""
        text = stage.text or _("(no text)")
        return f"{quest} [{stage.index}]: {text}" if quest else f"[{stage.index}]: {text}"

    def _node_for_stage(self, stage: Resolved) -> str | None:
        """The nav tree's node id for a resolved stage, for a double-click jump.

        Args:
            stage: The stage to find, by identity -- the same object
                :meth:`_fill_journal_tree` indexed, not merely an equal one.

        Returns:
            The node id, or ``None`` if this stage was never placed in the
            tree (should not happen for anything :func:`~wraithguard.patch.
            journal_scripts.attach` could have returned, but a jump target
            that quietly does nothing is safer than one that raises).
        """
        owner = self._stage_owner.get(id(stage))
        if owner is None:
            return None
        quest, info_id = owner
        return f"{quest}{SEP}{info_id}"

    def _node_for_call_owner(self, call: JournalCall) -> str | None:
        """The nav tree's node id for a call's own owning INFO stage.

        Args:
            call: The call to find the owner of.

        Returns:
            ``None`` for a Script-owned call -- a standalone script has no
            node of its own in this tree -- or when the owning quest was
            somehow never indexed.
        """
        if call.owner_type != "DialogueInfo":
            return None
        owner_quest = self._journal_info_quest.get(call.owner_id)
        if not owner_quest:
            return None
        return f"{owner_quest}{SEP}{call.owner_id}"

    @staticmethod
    def _effect_label(effect: Effect) -> str:
        """The line to show for one "Also happens" row.

        Args:
            effect: The effect to describe.

        Returns:
            The statement's own raw source line, verbatim -- already
            self-labelling as real mwscript (``AddItem "gold_001", 100``
            reads on its own), and not reformatted into a claim about what
            the arguments mean that this scan does not actually make.
        """
        return effect.raw

    def _add_effect_rows(self, effects: Sequence[Effect], *, indent: bool = False) -> None:
        """Show a script's other statements, grouped by shared condition.

        Consecutive statements sharing the exact same enclosing condition
        are one conditional block in the source -- showing that condition
        once, with every statement it covers listed under it, reads the way
        the script itself is laid out. The one-row-per-effect approach this
        replaces printed the same condition again before each individual
        statement inside it, so an ordinary if-block with several lines
        looked like the same condition had been duplicated by mistake
        rather than like one block with several things happening inside it.

        Args:
            effects: The statements to show, in source order -- grouping
                only merges genuinely consecutive runs, so two separate
                blocks that happen to reuse the same condition text later
                in the script are not folded together.
            indent: Whether these rows nest under an already-indented
                parent (a "Set by" trigger's own effects) rather than sit
                at the stage's own top level.
        """
        for conditions, group in itertools.groupby(effects, key=lambda e: e.conditions):
            if conditions:
                self._add_detail_row(_("if"), " \u2192 ".join(conditions), indent=indent)
            for effect in group:
                self._add_detail_row(_("Also happens"), self._effect_label(effect), indent=indent)

    def _add_call_condition_row(
        self,
        label: str,
        conditions: tuple[str, ...],
        effects: Sequence[Effect],
        *,
        indent: bool = False,
    ) -> None:
        """Show a call's own condition, unless a sibling effect group is about to show it anyway.

        A Journal call and the other statements in the same conditional
        block are independent scans (:func:`~wraithguard.patch.journal_scripts.
        calls_from_stages` and siblings never touch what
        :func:`~wraithguard.patch.journal_scripts.effects_from_stages` and
        siblings find, and vice versa), so nothing upstream already knows the
        two can name the exact same condition text. When they do -- Cast and
        Journal sitting in the same ``if`` block is the ordinary case, not an
        edge one -- showing the call's own condition here and then the same
        text again as an effect group's heading right after looked like one
        line duplicated by mistake, not two facts that happen to agree.

        Args:
            label: The row label to use when this condition is shown --
                ``"if"`` for a "Set by" trigger, ``"Condition"`` for a call's
                own detail view.
            conditions: The call's own conditions tuple.
            effects: The same owner's other statements, checked for a
                group sharing this exact tuple.
            indent: Whether the row nests under an already-indented parent.
        """
        if not conditions:
            return
        if any(effect.conditions == conditions for effect in effects):
            return
        self._add_detail_row(label, " \u2192 ".join(conditions), indent=indent)

    # -- the detail pane -------------------------------------------------

    def _clear_journal_detail(self) -> None:
        """Remove every row from the detail pane, ready for a fresh selection."""
        for child in self._journal_detail_body.winfo_children():
            child.destroy()

    def _add_detail_row(
        self, label: str, value: str, *, jump_target: str | None = None, indent: bool = False
    ) -> None:
        """Add one field/value row to the detail pane, wrapped to the pane's own width.

        Args:
            label: The field name.
            value: The field's value. Wrapped at the pane's current width
                (tracked live via the canvas's own <Configure> binding), never
                truncated -- long response text and full result scripts are
                exactly why this pane is a frame of labels and not a
                Treeview, which cannot wrap a cell at all.
            jump_target: A nav tree node id to jump to on double-click, or
                ``None`` for a row that names no navigable stage. Styled in
                the theme's accent colour so a jumpable row reads as one
                before it is even clicked.
            indent: Whether to indent this row slightly -- used for a
                condition line nested under the row it qualifies.
        """
        row = ttk.Frame(self._journal_detail_body)
        row.pack(fill="x", padx=(24 if indent else 8, 8), pady=(6, 0), anchor="w")

        ttk.Label(row, text=label, foreground=DARK["fg_dim"]).pack(anchor="w")
        value_label = ttk.Label(
            row,
            text=value,
            foreground=DARK["accent"] if jump_target else DARK["fg"],
            wraplength=self._journal_wrap,
            justify="left",
        )
        value_label.pack(anchor="w", fill="x")

        # A ttk.Label cannot be drag-selected, but the ids and response text in
        # this pane are exactly what a person wants to lift out and paste into
        # the Construction Set's search. A right-click "Copy" on the value gives
        # them that without turning every row into an editable widget.
        def _on_right_click(event: tk.Event[Any], copied: str = value) -> None:
            """Pop the value's copy menu -- `copied` is bound at row-build time."""
            self._show_journal_copy_menu(event, copied)

        value_label.bind("<Button-3>", _on_right_click)  # right-click on most platforms
        value_label.bind("<Button-2>", _on_right_click)  # right-click on macOS

        if jump_target is not None:
            value_label.configure(cursor="hand2")

            def _on_double_click(_event: tk.Event[Any], target: str = jump_target) -> None:
                """Jump to this row's target stage -- `target` is bound at row-build time."""
                self._jump_to_stage_node(target)

            value_label.bind("<Double-1>", _on_double_click)

    def _show_journal_copy_menu(self, event: tk.Event[Any], value: str) -> None:
        """Pop a one-item "Copy" menu for a detail-pane value at the click point.

        Args:
            event: The right-click event, for where to place the menu.
            value: The row's value text, put on the clipboard if "Copy" is chosen.
        """
        menu = tk.Menu(self._journal_detail_body, tearoff=0)
        menu.add_command(label=_("Copy"), command=lambda: self._copy_to_clipboard(value))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_to_clipboard(self, value: str) -> None:
        """Put ``value`` on the system clipboard.

        Args:
            value: The text to copy -- a stage id, quest id, or response text a
                person is lifting out to search for elsewhere.
        """
        widget = self._journal_detail_body
        widget.clipboard_clear()
        widget.clipboard_append(value)

    def _jump_to_stage_node(self, target: str) -> None:
        """Select a stage node in the nav tree and refresh the detail pane for it.

        Args:
            target: A nav tree node id, as built by :meth:`_node_for_stage` or
                :meth:`_node_for_call_owner`.
        """
        nav = self._journal_nav
        if not nav.exists(target):
            return
        parent = nav.parent(target)
        if parent:
            nav.item(parent, open=True)
        nav.see(target)
        nav.selection_set(target)
        nav.focus(target)
        self._on_journal_node()

    def _on_journal_node(self) -> None:
        """Show the selected row's detail: a stage, a call site, or nothing for a quest."""
        nav = self._journal_nav
        chosen = nav.selection()
        self._clear_journal_detail()
        if not chosen:
            return
        node = chosen[0]

        attachment = self._journal_call_nodes.get(node)
        if attachment is not None:
            self._fill_call_detail(attachment.call)
            return

        if SEP not in node:
            return  # a quest row
        quest, info_id = node.split(SEP, 1)
        stage = next(
            (s for s in self._journal_resolved.get(quest, ()) if s.info_id == info_id), None
        )
        if stage is not None:
            self._fill_journal_detail(stage, quest)

    def _fill_journal_detail(self, stage: Resolved, quest: str) -> None:
        """Fill the detail pane with one resolved stage: fields, requirements, script, and effects.

        Args:
            stage: The stage to show.
            quest: The quest it belongs to -- the caller already knows this
                from the tree id it was found under, so it is not re-derived
                here.
        """
        self._add_detail_row(_("Stage id"), stage.info_id)
        self._add_detail_row(_("Journal index"), str(stage.index))
        self._add_detail_row(_("Quest state"), stage.quest_state or _("(none)"))
        self._add_detail_row(_("Defined by"), ", ".join(stage.plugins) or _("(unknown)"))
        self._add_detail_row(_("Text"), stage.text or _("(no text)"))

        if stage.raw is not None:
            requirements = condition_lines(stage.raw)
            if requirements:
                self._add_detail_row(
                    _("Requirements"), "\n".join(f"\u2022 {line}" for line in requirements)
                )

        incoming = self._journal_incoming.get((quest, stage.info_id), ())
        if incoming:
            for attachment in incoming:
                call = attachment.call
                self._add_detail_row(
                    _("Set by"),
                    self._source_label(call),
                    jump_target=self._node_for_call_owner(call),
                )
                owner_effects = self._journal_effects_by_owner.get(
                    (call.owner_type, call.owner_id), ()
                )
                self._add_call_condition_row(_("if"), call.conditions, owner_effects, indent=True)
                # The trigger's own picture: the dialogue window reached
                # through "Read as dialogue..." shows this same
                # Actor/"- If ..."/Response/Result block per plugin, because
                # that IS what the trigger requires and does -- a journal
                # index rarely changes alone; the same response usually hands
                # over an item, changes disposition, or starts a script in
                # the same breath, and that content lives on the trigger's
                # own record, not on the journal-type stage being viewed.
                if call.owner_type == "DialogueInfo" and call.owner_raw is not None:
                    response = str(call.owner_raw.get("text") or "")
                    if response:
                        self._add_detail_row(_("Response"), response, indent=True)
                    requirements = condition_lines(call.owner_raw)
                    if requirements:
                        self._add_detail_row(
                            _("Requires"),
                            "\n".join(f"\u2022 {line}" for line in requirements),
                            indent=True,
                        )
                self._add_effect_rows(owner_effects, indent=True)
        else:
            self._add_detail_row(_("Set by"), _("(no script call found)"))

        if stage.script_text:
            own_effects = effects_from_stages({quest: [stage]})
            for attachment in attach(calls_from_stages({quest: [stage]}), self._journal_resolved):
                call = attachment.call
                if attachment.stage is not None:
                    self._add_detail_row(
                        _("Also sets"),
                        self._stage_label(attachment.stage),
                        jump_target=self._node_for_stage(attachment.stage),
                    )
                else:
                    self._add_detail_row(
                        _("Also sets"),
                        _("%(quest)s [%(index)d] -- no matching stage found")
                        % {"quest": call.quest, "index": call.index},
                    )
                self._add_call_condition_row(_("if"), call.conditions, own_effects, indent=True)

            self._add_effect_rows(own_effects)

            self._add_detail_row(_("Full script"), stage.script_text)

    def _fill_call_detail(self, call: JournalCall) -> None:
        """Fill the detail pane with one call site's own fields, requirements, and effects.

        Args:
            call: The call to show, from a selected call-site child row.
        """
        self._add_detail_row(_("Function"), call.function)
        self._add_detail_row(_("Sets quest"), call.quest)
        self._add_detail_row(_("Sets index"), str(call.index))
        self._add_detail_row(_("Found in"), f"{call.owner_type}: {call.owner_id}")
        self._add_detail_row(_("Plugin"), call.plugin or _("(unknown)"))

        if call.from_bytecode:
            self._add_detail_row(_("Source"), _("compiled bytecode -- no source text was shipped"))

        self._add_detail_row(
            _("Condition"),
            " \u2192 ".join(call.conditions) if call.conditions else _("(unconditional)"),
        )

        # Same "Read as dialogue..." picture as a stage's own Set-by rows
        # show: what this trigger requires and the response it gives, not
        # just the journal call buried inside it.
        if call.owner_type == "DialogueInfo" and call.owner_raw is not None:
            response = str(call.owner_raw.get("text") or "")
            if response:
                self._add_detail_row(_("Response"), response)
            requirements = condition_lines(call.owner_raw)
            if requirements:
                self._add_detail_row(
                    _("Requires"), "\n".join(f"\u2022 {line}" for line in requirements)
                )

        self._add_effect_rows(
            self._journal_effects_by_owner.get((call.owner_type, call.owner_id), ())
        )

        target = next(
            (
                stage
                for quest, stages in self._journal_resolved.items()
                for stage in stages
                if quest.lower() == call.quest.lower() and stage.index == call.index
            ),
            None,
        )
        if target is not None:
            self._add_detail_row(
                _("Target stage"),
                self._stage_label(target),
                jump_target=self._node_for_stage(target),
            )
        else:
            self._add_detail_row(_("Target stage"), _("(no matching stage found)"))


__all__ = ["JournalViewMixin"]
