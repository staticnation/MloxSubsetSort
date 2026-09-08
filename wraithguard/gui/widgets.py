"""Reusable GUI widgets: tooltip, queue writer, path field, drag-list, typeahead.

Moved verbatim from ``wraithguard_toolkit_gui.py`` (see the package docstring).
"""

from __future__ import annotations

import io
import tkinter as tk
from tkinter import filedialog, font as tkfont, ttk
from typing import TYPE_CHECKING, Any, cast

from wraithguard.gui import (
    case_insensitive_filetypes,
    register_drop_target,
    rtl,
    trace_first_fire,
)
from wraithguard.gui.theme import DARK
from wraithguard.i18n import gettext as _
from wraithguard.tracing import trace

if TYPE_CHECKING:
    import queue
    from collections.abc import Callable, Sequence
    from typing import TextIO

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

    def _schedule(self, event: tk.Event | None = None) -> None:
        """Start the hover delay, replacing any delay already running.

        Args:
            event: The Tk event, unused -- present because this is bound.
        """
        self._unschedule()
        try:
            self._after_id = self.widget.after(self.delay, self._show)
        except tk.TclError:
            pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal

    def _unschedule(self) -> None:
        """Cancel a pending hover delay, if there is one."""
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass  # the widget can vanish mid-operation (window closed); cosmetic, never fatal
            self._after_id = None

    def _show(self) -> None:
        """Put the tooltip on screen beside its widget.

        Does nothing when one is already up or there is no text, so a repeated
        Enter event cannot leave a second window behind with no way to close it.
        """
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
            justify=rtl.text_justify(),
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
        """Take the tooltip down and cancel any pending one.

        Args:
            event: The Tk event, unused -- present because this is bound.
        """
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


def group_separator(parent: tk.Misc, *, pad: int = 10) -> ttk.Separator:
    """Pack a thin vertical divider between two groups of buttons in a row.

    Button rows read as one long strip until their logical groups are set
    apart; this is the divider that does it, packed ``side="left"`` like the
    buttons it sits between so the groups fall out left to right. A little more
    horizontal padding than the gap between buttons within a group is what makes
    the grouping legible.

    Args:
        parent: The button row (a ``side="left"`` packing context).
        pad: Horizontal padding on each side of the divider.

    Returns:
        The separator, already packed.
    """
    sep = ttk.Separator(parent, orient="vertical")
    sep.pack(side="left", fill="y", padx=pad, pady=2)
    return sep


class ToggleSwitch(tk.Canvas):
    """A pill on/off toggle drawn on a canvas -- our own switch, no library.

    A drop-in for a boolean ``ttk.Checkbutton``: the same
    ``(parent, text=, variable=, command=)`` shape, packs/grids like any widget,
    takes an :func:`add_tooltip`, and re-themes on a live theme switch via
    :meth:`refresh_theme` (the theme walk finds it by the ``_is_toggle_switch``
    marker, exactly as it finds pane-divider grips). Drawing it ourselves keeps
    the CTk-style look under our own code and colours.
    """

    _TRACK_W = 34
    _TRACK_H = 18
    _PAD = 3
    _GAP = 8

    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str = "",
        variable: tk.BooleanVar | None = None,
        command: Callable[[], None] | None = None,
        state: str = "normal",
        bg: str | None = None,
    ) -> None:
        """Build the switch.

        Args:
            parent: The container to place it in.
            text: The label drawn to the right of the pill.
            variable: The boolean the switch reflects and drives; a private one
                is made when omitted.
            command: Called after a user toggle (not on a programmatic set).
            state: ``"normal"`` or ``"disabled"`` (dimmed, ignores clicks).
            bg: Canvas fill; defaults to the chrome background so it blends.
        """
        self._var = variable if variable is not None else tk.BooleanVar(value=False)
        self._command = command
        self._text = text
        self._state = state
        self._font = tkfont.nametofont("TkDefaultFont")
        fill = bg or DARK["bg"]
        text_w = self._font.measure(text) if text else 0
        height = max(self._TRACK_H + 2 * self._PAD, self._font.metrics("linespace") + 4)
        width = self._PAD + self._TRACK_W + ((self._GAP + text_w) if text else 0) + self._PAD
        super().__init__(
            parent,
            width=width,
            height=height,
            highlightthickness=0,
            borderwidth=0,
            bg=fill,
            takefocus=1,
            cursor="" if state == "disabled" else "hand2",
        )
        #: marks this canvas for the theme restyle walk (see _restyle_plain_live)
        self._is_toggle_switch = True
        for sequence in ("<Button-1>", "<Return>", "<space>"):
            self.bind(sequence, self._on_click)
        self._var.trace_add("write", lambda *_: self._redraw())
        self._redraw()

    def get(self) -> bool:
        """The current on/off value."""
        return bool(self._var.get())

    def set_state(self, state: str) -> None:
        """Enable (``"normal"``) or disable (``"disabled"``) the switch."""
        self._state = state
        self.configure(cursor="" if state == "disabled" else "hand2")
        self._redraw()

    def refresh_theme(self) -> None:
        """Repaint with the active palette after a live theme switch."""
        self.configure(bg=DARK["bg"])
        self._redraw()

    def _on_click(self, _event: tk.Event | None = None) -> None:
        """Toggle on a click/keypress, unless disabled; then fire the command."""
        if self._state == "disabled":
            return
        self._var.set(not self._var.get())  # trace -> _redraw
        if self._command is not None:
            self._command()

    def _redraw(self) -> None:
        """Draw the pill, knob and label for the current value and state."""
        try:
            self.delete("all")
        except tk.TclError:
            return  # a shared variable's trace can fire after we're destroyed
        on = bool(self._var.get())
        disabled = self._state == "disabled"
        pad, tw, th = self._PAD, self._TRACK_W, self._TRACK_H
        y0 = (int(self["height"]) - th) // 2
        y1 = y0 + th
        x0, x1 = pad, pad + tw
        r = th // 2
        if disabled:
            track = DARK["bg2"]
        elif on:
            track = DARK["accent"]
        else:
            track = DARK["bg2"]
        # pill = two round caps + the rectangle between them, no outline
        self.create_oval(x0, y0, x0 + th, y1, fill=track, outline=track)
        self.create_oval(x1 - th, y0, x1, y1, fill=track, outline=track)
        self.create_rectangle(x0 + r, y0, x1 - r, y1, fill=track, outline=track)
        cy = (y0 + y1) // 2
        knob = DARK["fg_dim"] if disabled else "#e8eaed"
        k = r - 2
        cx = (x1 - r) if on else (x0 + r)
        self.create_oval(cx - k, cy - k, cx + k, cy + k, fill=knob, outline=knob)
        if self._text:
            fg = DARK["fg_dim"] if disabled else DARK["fg"]
            self.create_text(
                x1 + self._GAP, cy, text=self._text, anchor="w", fill=fg, font=self._font
            )


class RadioButton(tk.Canvas):
    """A ring-style radio drawn on a canvas -- our own, no library.

    A drop-in for ``ttk.Radiobutton``: the same
    ``(parent, text=, value=, variable=, command=)`` shape, where a set of them
    share one ``variable`` and each is selected when the variable equals its
    ``value``. Selecting one draws an accent ring with a filled centre dot; the
    rest show a plain outline ring -- the CTk look, in our palette. Re-themes on
    a live theme switch via :meth:`refresh_theme` (found by the
    ``_is_radio_button`` marker, like the pill switch and the pane grips).
    """

    _DIAM = 15
    _PAD = 3
    _GAP = 8

    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str = "",
        value: object = None,
        variable: tk.Variable | None = None,
        command: Callable[[], None] | None = None,
        state: str = "normal",
        bg: str | None = None,
    ) -> None:
        """Build one radio in a group.

        Args:
            parent: The container to place it in.
            text: The label drawn to the right of the ring.
            value: The value this radio writes to ``variable`` when picked, and
                the value it is "selected" for.
            variable: The shared group variable (``StringVar``/``IntVar``); a
                private one is made when omitted.
            command: Called after a user pick (not on a programmatic change).
            state: ``"normal"`` or ``"disabled"`` (dimmed, ignores clicks).
            bg: Canvas fill; defaults to the chrome background so it blends.
        """
        self._value = value
        self._variable: tk.Variable = variable if variable is not None else tk.StringVar()
        self._command = command
        self._text = text
        self._state = state
        self._font = tkfont.nametofont("TkDefaultFont")
        fill = bg or DARK["bg"]
        text_w = self._font.measure(text) if text else 0
        height = max(self._DIAM + 2 * self._PAD, self._font.metrics("linespace") + 4)
        width = self._PAD + self._DIAM + ((self._GAP + text_w) if text else 0) + self._PAD
        super().__init__(
            parent,
            width=width,
            height=height,
            highlightthickness=0,
            borderwidth=0,
            bg=fill,
            takefocus=1,
            cursor="" if state == "disabled" else "hand2",
        )
        #: marks this canvas for the theme restyle walk (see _restyle_plain_live)
        self._is_radio_button = True
        for sequence in ("<Button-1>", "<Return>", "<space>"):
            self.bind(sequence, self._on_click)
        self._variable.trace_add("write", lambda *_: self._redraw())
        self._redraw()

    def selected(self) -> bool:
        """Whether this radio's value is the one currently chosen."""
        return self._variable.get() == self._value

    def set_state(self, state: str) -> None:
        """Enable (``"normal"``) or disable (``"disabled"``) the radio."""
        self._state = state
        self.configure(cursor="" if state == "disabled" else "hand2")
        self._redraw()

    def refresh_theme(self) -> None:
        """Repaint with the active palette after a live theme switch."""
        self.configure(bg=DARK["bg"])
        self._redraw()

    def _on_click(self, _event: tk.Event | None = None) -> None:
        """Pick this radio (unless disabled), then fire the command."""
        if self._state == "disabled":
            return
        self._variable.set(self._value)  # traces -> every group member redraws
        if self._command is not None:
            self._command()

    def _redraw(self) -> None:
        """Draw the ring, centre dot (when picked) and label."""
        try:
            self.delete("all")
        except tk.TclError:
            return  # the shared variable's trace can fire after we're destroyed
        on = self.selected()
        disabled = self._state == "disabled"
        pad, d = self._PAD, self._DIAM
        y0 = (int(self["height"]) - d) // 2
        x0 = pad
        if disabled:
            ring = DARK["fg_dim"]
        elif on:
            ring = DARK["accent"]
        else:
            ring = DARK["border"]
        # the outline ring -- unfilled, so the panel shows through its centre
        self.create_oval(x0, y0, x0 + d, y0 + d, outline=ring, width=2, fill=DARK["bg"])
        if on:
            dot = DARK["fg_dim"] if disabled else DARK["accent"]
            cx, cy = x0 + d // 2, y0 + d // 2
            k = d // 2 - 4
            self.create_oval(cx - k, cy - k, cx + k, cy + k, fill=dot, outline=dot)
        if self._text:
            fg = DARK["fg_dim"] if disabled else DARK["fg"]
            self.create_text(
                x0 + d + self._GAP,
                y0 + d // 2,
                text=self._text,
                anchor="w",
                fill=fg,
                font=self._font,
            )


# ---------------------------------------------------------------------------
# scrollable containers -- for a form or toolbar row that can outgrow a small
# window. Plain ttk has no scrollable frame, so both wrap a plain tk.Canvas
# (which does) around an inner ttk.Frame the caller builds real content into.
# Mirrors the pattern the main window already uses for its controls panel.
# ---------------------------------------------------------------------------


def make_scrollable_y(parent: tk.Misc, *, bg: str) -> tuple[ttk.Frame, tk.Canvas, ttk.Frame]:
    """Build a vertically scrollable container for a tall form.

    The caller packs/grids the returned ``container`` into place and builds
    real content into the returned inner frame with ordinary ``grid``/
    ``pack`` calls, exactly as if it were the direct child of ``parent``. The
    canvas tracks the inner frame's height as its scroll region and matches
    its own width to the container's, so content reflows on resize instead
    of being clipped. The mouse wheel scrolls it while the pointer is over
    it, and only then -- it doesn't hijack the wheel for the rest of the app.

    Args:
        parent: Widget to build the scrollable container into.
        bg: Canvas background, matching the surrounding chrome so the strip
            of canvas outside the inner frame (before it's stretched to fit)
            never reads as a mismatched patch of color.

    Returns:
        ``(container, canvas, inner_frame)`` -- build content into
        ``inner_frame``.
    """
    container = ttk.Frame(parent)
    container.columnconfigure(0, weight=1)
    container.rowconfigure(0, weight=1)
    canvas = tk.Canvas(container, bg=bg, bd=0, highlightthickness=0)
    scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set, yscrollincrement=24)
    scroll.grid(row=0, column=1, sticky="ns")
    canvas.grid(row=0, column=0, sticky="nsew")

    inner = ttk.Frame(canvas)
    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(window_id, width=e.width))

    def _wheel(event: tk.Event) -> None:
        """Scroll the canvas one step per wheel notch.

        Args:
            event: The wheel event -- ``num`` 4/5 on X11 (no ``delta``),
                ``delta`` elsewhere (Windows/Mac).
        """
        if event.num == 4:
            canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            canvas.yview_scroll(1, "units")
        else:
            canvas.yview_scroll(-1 * (event.delta // 120), "units")

    def _bind_wheel(_event: object = None) -> None:
        """Hook the wheel to this canvas while the pointer is over it.

        Args:
            _event: The Tk event, unused -- present because this is bound.
        """
        canvas.bind_all("<MouseWheel>", _wheel)
        canvas.bind_all("<Button-4>", _wheel)
        canvas.bind_all("<Button-5>", _wheel)

    def _unbind_wheel(_event: object = None) -> None:
        """Release the wheel binding once the pointer leaves the canvas.

        Args:
            _event: The Tk event, unused -- present because this is bound.
        """
        canvas.unbind_all("<MouseWheel>")
        canvas.unbind_all("<Button-4>")
        canvas.unbind_all("<Button-5>")

    canvas.bind("<Enter>", _bind_wheel)
    canvas.bind("<Leave>", _unbind_wheel)
    return container, canvas, inner


def make_scrollable_x(parent: tk.Misc, *, bg: str) -> tuple[ttk.Frame, tk.Canvas, ttk.Frame]:
    """Build a horizontally scrollable single-row strip.

    For a toolbar or option row that can hold more than a narrow window can
    show at once. Packed children that run off the edge of a plain
    ``ttk.Frame`` just become unreachable -- pack never wraps -- so this
    puts them on a canvas with a horizontal scrollbar instead. The caller
    packs/grids the returned ``container`` and builds content into the
    inner frame with ordinary ``pack(side="left"/"right", ...)``. Unlike
    :func:`make_scrollable_y`, the canvas's *height* tracks its content
    (a single row should never be taller than the row itself).

    The inner frame is stretched to at least the canvas's own visible
    width, so ``side="right"`` children still pin to the actual visible
    right edge when everything fits -- same as an ordinary packed row.
    It's only ever allowed to grow *wider* than that, never narrower: if it
    were simply clamped to the canvas's width, pack would fall back to
    squeezing/clipping whatever doesn't fit, which is the exact bug this
    helper exists to avoid. Growing past the visible width is what puts the
    rest within a scroll instead.

    Args:
        parent: Widget to build the scrollable strip into.
        bg: Canvas background, matching the surrounding chrome.

    Returns:
        ``(container, canvas, inner_frame)`` -- build content into
        ``inner_frame``.
    """
    container = ttk.Frame(parent)
    canvas = tk.Canvas(container, bg=bg, bd=0, highlightthickness=0)
    scroll = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
    canvas.configure(xscrollcommand=scroll.set, xscrollincrement=24)
    canvas.pack(fill="x", expand=True)
    scroll.pack(fill="x")

    inner = ttk.Frame(canvas)
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _resize(_event: object = None) -> None:
        """Refit the scroll region/height, and the row's width, to content.

        Args:
            _event: The Tk event, unused -- present because this is bound.
        """
        # Without this, a Configure firing mid-layout (e.g. while several
        # buttons are still being packed in one at a time) can read
        # winfo_reqheight/reqwidth before Tk has finished computing them for
        # the row as a whole, locking in a size that's off in both directions
        # -- the strip then renders visibly cropped (top and bottom, not
        # just the horizontal edge) until something else happens to trigger
        # another Configure to correct it.
        canvas.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        # content-driven height: grows/shrinks with what's actually packed
        # into the row, never with the (irrelevant, here) canvas width.
        canvas.configure(height=inner.winfo_reqheight())
        # never narrower than the canvas's own visible width (so right-side
        # children stay pinned to the visible edge when there's room), but
        # never narrower than the content needs either (so nothing clips) --
        # see the docstring above for why both bounds matter.
        canvas.itemconfig(window_id, width=max(canvas.winfo_width(), inner.winfo_reqwidth()))

    inner.bind("<Configure>", _resize)
    canvas.bind("<Configure>", _resize)
    # content is added to `inner` after this function returns, so an initial
    # pass now would just measure an empty frame -- defer one more pass to
    # after the caller has finished building the row and Tk has processed
    # the resulting geometry queue, so the very first paint is already
    # correctly sized instead of waiting for an unrelated Configure to fix it.
    container.after_idle(_resize)

    def _wheel(event: tk.Event) -> None:
        """Scroll the canvas sideways one step per wheel notch.

        Args:
            event: The wheel event -- ``num`` 4/5 on X11 (no ``delta``),
                ``delta`` elsewhere (Windows/Mac).
        """
        if event.num == 4:
            canvas.xview_scroll(-1, "units")
        elif event.num == 5:
            canvas.xview_scroll(1, "units")
        else:
            canvas.xview_scroll(-1 * (event.delta // 120), "units")

    def _bind_wheel(_event: object = None) -> None:
        """Hook Shift+wheel to this canvas while the pointer is over it.

        Args:
            _event: The Tk event, unused -- present because this is bound.
        """
        # Shift+wheel is the conventional horizontal-scroll gesture, and
        # deliberately distinct from make_scrollable_y's plain wheel -- a
        # strip like this routinely sits right above a normal vertically
        # scrolling tree, and both grabbing the same gesture would fight.
        canvas.bind_all("<Shift-MouseWheel>", _wheel)
        canvas.bind_all("<Shift-Button-4>", _wheel)
        canvas.bind_all("<Shift-Button-5>", _wheel)

    def _unbind_wheel(_event: object = None) -> None:
        """Release the Shift+wheel binding once the pointer leaves the canvas.

        Args:
            _event: The Tk event, unused -- present because this is bound.
        """
        canvas.unbind_all("<Shift-MouseWheel>")
        canvas.unbind_all("<Shift-Button-4>")
        canvas.unbind_all("<Shift-Button-5>")

    canvas.bind("<Enter>", _bind_wheel)
    canvas.bind("<Leave>", _unbind_wheel)
    return container, canvas, inner


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

    def as_stream(self) -> TextIO:
        """Return ``self`` typed as a ``TextIO`` for ``contextlib.redirect_*``.

        ``QueueWriter`` subclasses ``io.TextIOBase`` and implements the
        ``write``/``flush`` a redirect target uses, so it is a valid stdout or
        stderr replacement at runtime. typeshed, however, does not model
        ``io.TextIOBase`` as ``typing.IO[str]``, so ``redirect_stdout`` and
        ``redirect_stderr`` reject it on their ``_T_io`` bound. This states the
        relationship the stubs omit; it has no runtime effect.

        Returns:
            ``self``, unchanged, annotated as ``TextIO``.
        """
        return cast("TextIO", self)


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
        extra_button: tuple | Sequence[tuple] | None = None,
    ) -> None:
        """Build the row inside ``parent`` at grid ``row``.

        browse_kind: 'open', 'save', or 'dir'.
        extra_button: optional (text, command, tooltip) for a button placed to
        the right of Browse (e.g. a 'Scan...' action on the subset-file row),
        or a sequence of such tuples for more than one.
        """
        self.var = var
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        self.entry = entry

        ci_filetypes = case_insensitive_filetypes(filetypes)

        def browse() -> None:
            """Ask for a path with the dialog this field was configured for."""
            if browse_kind == "save":
                path = filedialog.asksaveasfilename(
                    filetypes=ci_filetypes, defaultextension=".toml"
                )
            elif browse_kind == "dir":
                path = filedialog.askdirectory()
            else:
                path = filedialog.askopenfilename(filetypes=ci_filetypes)
            if path:
                var.set(path)

        self.extra_btn = None
        self.extra_btns: list[ttk.Button] = []
        if extra_button:
            # One (text, cmd, tip) tuple, or a sequence of them -- text is
            # always a str and a spec is never one, so checking the first
            # element tells the two shapes apart unambiguously.
            specs = [extra_button] if isinstance(extra_button[0], str) else list(extra_button)
            # keep everything inside column 2 (a small button bar) so rows that
            # span columns 0-2 below still line up -- no stray 4th column
            btnbar = ttk.Frame(parent)
            btnbar.grid(row=row, column=2, padx=(8, 0), pady=4, sticky="e")
            browse_btn = ttk.Button(btnbar, text=_("Browse..."), command=browse)
            browse_btn.pack(side="left")
            for spec in specs:
                # `specs` is a union of a bare tuple and a sequence of tuples, so
                # each element widens to `Any | tuple`; narrow it to a plain tuple
                # of fields (text, command, optional tooltip) before unpacking.
                fields = cast("tuple[Any, ...]", spec)
                ex_text, ex_cmd = fields[0], fields[1]
                ex_tip = fields[2] if len(fields) > 2 else None
                btn = ttk.Button(btnbar, text=ex_text, command=ex_cmd)
                btn.pack(side="left", padx=(6, 0))
                if ex_tip:
                    add_tooltip(btn, ex_tip)
                self.extra_btns.append(btn)
            self.extra_btn = self.extra_btns[0]  # back-compat: the single-button case
        else:
            browse_btn = ttk.Button(parent, text=_("Browse..."), command=browse)
            browse_btn.grid(row=row, column=2, padx=(8, 0), pady=4)
        self.browse_btn = browse_btn

        if tooltip:
            add_tooltip(label_widget, tooltip)
            add_tooltip(entry, tooltip)
            add_tooltip(browse_btn, tooltip)

        if register_drop_target(entry):
            # Any: tkinterdnd2 synthesises its own event object, which has no
            # published type -- only a `.data` string this reads.
            def on_drop(event: Any) -> None:  # noqa: ANN401
                """Fill the field from a dropped path.

                Args:
                    event: The tkinterdnd2 drop event; ``data`` is a Tk list of
                        paths, of which the first is taken.
                """
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
        self._press_idx: int | None = None  # row a block-grab press landed on
        self.bind("<Button-1>", self._on_press, add="+")
        self.bind("<B1-Motion>", self._on_motion, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")

    def _on_press(self, event: tk.Event) -> str | None:
        """Begin a drag, remembering which rows were grabbed.

        Args:
            event: The button-press event; its ``y`` picks the row.

        Returns:
            ``"break"`` to stop Tk's own selection handling when the press
            starts a drag, otherwise ``None`` to let it through.
        """
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
            self._press_idx = idx
            return "break"
        self._drag_block = [idx]
        return None  # let Listbox's own click handling run

    def _on_motion(self, event: tk.Event) -> None:
        """Move the grabbed rows to follow the pointer.

        Args:
            event: The motion event; its ``y`` names the row moved onto.
        """
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
        """Move the grabbed block one row up or down.

        Args:
            direction: ``-1`` for up, ``1`` for down. Movement that would run
                off either end is ignored rather than clamped, so a drag past
                the edge does not silently reorder anything.
        """
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
        """Finish a drag and notify the owner if anything actually moved.

        Args:
            event: The button-release event, unused beyond the binding.
        """
        if self._moved and self.on_reorder:
            trace_first_fire("listbox drag-reorder -> on_reorder")
            trace(f"[smoke] drag-reorder committed: {self.size()} row(s) now listed")
            self.on_reorder()
        elif not self._moved and self._press_idx is not None:
            # A press that grabbed a multi-row block but never moved is a plain
            # click, so collapse the selection to the clicked row -- the standard
            # click-in-selection behaviour the drag "break" had suppressed.
            # Without this, once every row is selected (Ctrl+A / <<SelectAll>>
            # makes one contiguous block) every click reads as a drag-grab and
            # the selection can never be reduced. Clicking any row now escapes it.
            self.selection_clear(0, "end")
            self.selection_set(self._press_idx)
            self.activate(self._press_idx)
            self.event_generate("<<ListboxSelect>>")
        self._drag_block = None
        self._moved = False
        self._press_idx = None


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
        """Show the current type-ahead buffer, if the caller wanted it shown."""
        if feedback:
            try:
                feedback(buf)
            except Exception:  # noqa: BLE001
                # caller-supplied feedback callback into Tk; purely cosmetic
                pass

    def _clear(_e: tk.Event | None = None) -> None:
        """Forget what has been typed so far.

        Args:
            _e: The Tk event, unused -- present because this is bound.
        """
        nonlocal buf
        buf = ""
        _feedback()

    def _schedule_reset() -> None:
        """Restart the idle timer that forgets the type-ahead buffer.

        Type-ahead has to expire, or a search begun a minute ago silently
        prefixes the next keystroke and the list jumps somewhere unexplained.
        """
        nonlocal after_id
        if after_id is not None:
            try:
                listbox.after_cancel(after_id)
            except tk.TclError:  # after_cancel on a stale/expired id
                pass
        after_id = listbox.after(1200, _clear)

    def _jump(idx: int) -> None:
        """Select one row and scroll it into view.

        Args:
            idx: The row to move to.
        """
        listbox.selection_clear(0, "end")
        listbox.selection_set(idx)
        listbox.activate(idx)
        listbox.see(idx)
        listbox.event_generate("<<ListboxSelect>>")

    def _on_key(e: tk.Event) -> str | None:
        """Fold one keystroke into the type-ahead search.

        Args:
            e: The key event.

        Returns:
            ``"break"`` when the key was consumed as part of a search, so Tk's
            own single-character jump does not also fire, otherwise ``None``.
        """
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
