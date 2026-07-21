"""Reusable GUI widgets: tooltip, queue writer, path field, drag-list, typeahead.

Moved verbatim from ``mlox_subset_sort_gui.py`` (see the package docstring).
"""

from __future__ import annotations

import io
import queue
import tkinter as tk
from collections.abc import Callable
from tkinter import filedialog, ttk
from typing import Any

from mlox_subset.gui import DND_FILES, HAVE_DND, trace_first_fire
from mlox_subset.gui.theme import DARK
from mlox_subset.i18n import gettext as _
from mlox_subset.tracing import trace

# ---------------------------------------------------------------------------
# a small hover tooltip -- delayed popup, dark-themed to match the rest of
# the app. Works on any widget (ttk or plain tk).
# ---------------------------------------------------------------------------


class Tooltip:
    """A delayed hover tooltip, dark-themed to match the active chrome."""

    def __init__(self, widget: tk.Misc, text: str, delay: int = 450, wraplength: int = 320) -> None:
        """Attach the tooltip to ``widget``, showing after ``delay`` ms."""
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self.tip_window: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        """Replace the tooltip text shown on the next hover."""
        self.text = text

    def _schedule(self, event: tk.Event | None = None) -> None:
        self._unschedule()
        try:
            self._after_id = self.widget.after(self.delay, self._show)
        except tk.TclError:
            pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal

    def _unschedule(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal
            self._after_id = None

    def _show(self) -> None:
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
            pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal
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

    def _hide(self, event: tk.Event | None = None) -> None:
        self._unschedule()
        if self.tip_window is not None:
            try:
                self.tip_window.destroy()
            except tk.TclError:
                pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal
            self.tip_window = None


def add_tooltip(widget: tk.Misc, text: str) -> Tooltip:
    """Attach a :class:`Tooltip` with the default delay and wrap width."""
    return Tooltip(widget, text)


# ---------------------------------------------------------------------------
# a stdout/stderr-compatible stream that pushes chunks into a thread-safe
# queue instead of writing to a real terminal, so the worker thread can
# write freely and the UI thread can drain it on its own schedule
# ---------------------------------------------------------------------------


class QueueWriter(io.TextIOBase):
    """A write-only text stream that pushes chunks into a thread-safe queue.

    Lets a worker thread print freely while the UI thread drains the queue on
    its own schedule.
    """

    def __init__(self, q: queue.Queue) -> None:
        """Wrap ``q``; every write() becomes a put()."""
        self.q = q

    def write(self, s: str) -> int:
        """Queue ``s`` (if non-empty) and report it written."""
        if s:
            self.q.put(s)
        return len(s)

    def flush(self) -> None:
        """No-op: every write is already visible to the consumer."""


# ---------------------------------------------------------------------------
# small reusable "path field": label + entry + Browse button, optionally
# a drag-and-drop target
# ---------------------------------------------------------------------------


class PathField:
    """A labelled path row: label + entry + Browse button, optionally a DnD target."""

    def __init__(
        self,
        parent: tk.Misc,
        label: str,
        row: int,
        var: tk.StringVar,
        browse_kind: str = "open",
        filetypes: tuple = (("All files", "*.*"),),
        on_drop_extra: Callable[[tuple[str, ...]], None] | None = None,
        tooltip: str | None = None,
        extra_button: tuple | None = None,
    ) -> None:
        """Build the row inside ``parent`` at grid ``row``.

        browse_kind: 'open', 'save', or 'dir'.
        extra_button: optional (text, command, tooltip) for a button placed to
        the right of Browse (e.g. a 'Scan...' action on the subset-file row).
        """
        self.var = var
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        self.entry = entry

        def browse() -> None:
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
            # tkinterdnd2 monkey-patches these onto the widget at runtime.
            entry.drop_target_register(DND_FILES)  # type: ignore[attr-defined]

            # Any: tkinterdnd2 synthesises its own event object, which has no
            # published type -- only a `.data` string this reads.
            def on_drop(event: Any) -> None:  # noqa: ANN401
                paths = parent.tk.splitlist(event.data)
                if paths:
                    var.set(paths[0])
                if on_drop_extra:
                    on_drop_extra(paths)

            entry.dnd_bind("<<Drop>>", on_drop)  # type: ignore[attr-defined]

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the entry and its Browse button together."""
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
    """A Listbox whose rows can be reordered by dragging them up or down.

    Independent of tkinterdnd2 (which is for dragging files in from the OS):
    reordering rows already in the list needs nothing but plain tkinter mouse
    events, so it works even when the optional dependency is absent.
    """

    def __init__(
        self,
        *args: Any,  # noqa: ANN401  (passed straight to tk.Listbox)
        on_reorder: Callable[[], None] | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        """Build the listbox; ``on_reorder`` is called after a committed drag."""
        super().__init__(*args, **kwargs)
        self.on_reorder = on_reorder
        self._drag_block: list[int] | None = None  # contiguous indices being dragged
        self._moved = False
        self.bind("<Button-1>", self._on_press, add="+")
        self.bind("<B1-Motion>", self._on_motion, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")

    def _on_press(self, event: tk.Event) -> str | None:
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

    def _on_motion(self, event: tk.Event) -> None:
        if not self._drag_block:
            return
        target = self.nearest(event.y)
        if not (0 <= target < self.size()):
            return
        if target < self._drag_block[0]:
            self._shift(-1)
        elif target > self._drag_block[-1]:
            self._shift(1)

    def _shift(self, direction: int) -> None:
        block, size = self._drag_block, self.size()
        if not block:
            return
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

    def _on_release(self, event: tk.Event) -> None:
        if self._moved and self.on_reorder:
            trace_first_fire("listbox drag-reorder -> on_reorder")
            trace(f"[smoke] drag-reorder committed: {self.size()} row(s) now listed")
            self.on_reorder()
        self._drag_block = None
        self._moved = False


# ---------------------------------------------------------------------------
# generic draggable-list panel: a titled list with Move Up/Down + Reset,
# used for both the plugin load order and the data= path order. Items
# matching highlighted_items (case-insensitive) get a highlighted background
# so it's obvious what a sort actually touched vs. what was already correct.
# Dragging rows here never re-runs anything -- it's a manual override of a
# computed order, applied at Export time.
# ---------------------------------------------------------------------------


def attach_typeahead(
    listbox: tk.Listbox,
    strip: Callable[[str], str] | None = None,
    feedback: Callable[[str], None] | None = None,
) -> None:
    """Add Windows-Explorer-style type-to-jump to a Listbox.

    Type letters to jump to the first row whose name starts with what you
    typed (falling back to a substring match); press one letter repeatedly to
    cycle through its matches; Backspace edits, Esc clears. The buffer resets
    after a short pause.

    Args:
        listbox: The list to attach the key bindings to.
        strip: Maps display text back to the real name, when the rows carry
            decoration. Defaults to identity.
        feedback: Called with the current buffer for a UI hint, if given.

    """
    import time as _time

    # Explicit: the three values have different types, so an inferred
    # dict[str, object] makes every use below an error.
    buf = ""
    last_at = 0.0
    after_id: str | None = None
    strip_fn: Callable[[str], str] = strip or (lambda s: s)

    def _feedback() -> None:
        if feedback:
            try:
                feedback(buf)
            except Exception:  # noqa: BLE001
                # caller-supplied feedback callback into Tk; purely cosmetic
                pass

    def _clear(_e: tk.Event | None = None) -> None:
        nonlocal buf
        buf = ""
        _feedback()

    def _schedule_reset() -> None:
        nonlocal after_id
        if after_id is not None:
            try:
                listbox.after_cancel(after_id)
            except tk.TclError:  # after_cancel on a stale/expired id
                pass
        after_id = listbox.after(1200, _clear)

    def _jump(idx: int) -> None:
        listbox.selection_clear(0, "end")
        listbox.selection_set(idx)
        listbox.activate(idx)
        listbox.see(idx)
        listbox.event_generate("<<ListboxSelect>>")

    def _on_key(e: tk.Event) -> str | None:
        nonlocal buf, last_at
        ks = e.keysym
        if ks == "Escape":
            _clear()
            return "break"
        if ks == "BackSpace":
            if buf:
                buf = buf[:-1]
                last_at = _time.time()
                _feedback()
                _schedule_reset()
                return "break"
            return None
        ch = e.char
        if not ch or not ch.isprintable() or (int(e.state) & 0x0004):  # ignore Ctrl-chords
            return None
        now = _time.time()
        if now - last_at > 1.2:
            buf = ""
        # leaving single-key cycling: start a fresh buffer with the new key
        if len(buf) > 1 and set(buf) == {buf[0]} and ch.lower() != buf[0]:
            buf = ""
        last_at = now
        cl = ch.lower()
        items = [strip_fn(listbox.get(i)).lower() for i in range(listbox.size())]
        if buf and set(buf) == {cl}:
            # same key again: cycle through rows starting with that letter
            buf += cl
            cur = listbox.curselection()
            start = (cur[0] + 1) if cur else 0
            for i in list(range(start, len(items))) + list(range(start)):
                if items[i].startswith(cl):
                    _jump(i)
                    break
        else:
            buf += cl
            hit = next((i for i, s in enumerate(items) if s.startswith(buf)), None)
            if hit is None:
                hit = next((i for i, s in enumerate(items) if buf in s), None)
            if hit is not None:
                _jump(hit)
        _feedback()
        _schedule_reset()
        return "break"

    listbox.bind("<KeyPress>", _on_key, add="+")
