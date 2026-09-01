"""Right-to-left (RTL) *appliers* for Tk widgets.

The direction *logic* -- which way is forward, and the anchor/side/sticky flips
that follow -- lives in :mod:`wraithguard.rtl`, which has no Tk dependency and
is unit-tested without a display. This module is the Tk half: the three
functions that actually mutate widget state so an Arabic interface reads
right-to-left. It imports :mod:`tkinter` lazily (like the rest of this package)
and is covered by the headless smoke run rather than the hermetic suite.

The pure helpers are re-exported here so a single ``from wraithguard.gui import
rtl`` gives call sites both the primitives and the appliers.

Tk does not reorder the glyphs of a bidirectional line (no full Unicode BiDi),
so mixed Arabic/Latin runs still lay out by the terminal's own rules; what these
appliers fix is *alignment and widget geometry*, which is the bulk of what reads
wrong. Full glyph-level BiDi would need a different text engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from wraithguard.rtl import (
    RTL_LANGUAGES,
    active_is_rtl,
    end_anchor,
    end_side,
    flip_anchor,
    flip_side,
    flip_sticky,
    is_rtl,
    start_anchor,
    start_side,
    text_justify,
)
from wraithguard.tracing import trace

if TYPE_CHECKING:
    import tkinter as tk
    from tkinter import ttk

# Re-export the pure primitives so callers reach everything through this module.
__all__ = [
    "RTL_LANGUAGES",
    "active_is_rtl",
    "apply_rtl_defaults",
    "apply_rtl_to_treeview",
    "end_anchor",
    "end_side",
    "flip_anchor",
    "flip_side",
    "flip_sticky",
    "is_rtl",
    "mirror_text_widget",
    "start_anchor",
    "start_side",
    "text_justify",
]


def apply_rtl_defaults(root: tk.Misc) -> bool:
    """Right-align labels, entries and buttons app-wide when the UI is RTL.

    Sets Tk option-database defaults and ttk style options on ``root`` so that
    widgets created afterwards read from the right without every construction
    site opting in. Covers the classes where alignment is both wrong-by-default
    in RTL and safe to flip wholesale: labels, entries, messages and buttons.

    Does nothing (returning ``False``) when the active language is left-to-right,
    so it is called unconditionally from the theme setup.

    Args:
        root: The application root (or any widget on the target interpreter).

    Returns:
        ``True`` if RTL defaults were applied, ``False`` if the language is LTR.
    """
    if not active_is_rtl():
        return False

    import tkinter as tk
    from tkinter import ttk

    # Option-database defaults reach plain (non-ttk) widgets and any ttk widget
    # whose alignment is not a style option -- crucially tk.Label/tk.Entry and
    # the ttk.Entry text field. Set by widget-class pattern, lowest priority, so
    # an explicit per-widget option still wins.
    for pattern, value in (
        ("*Label.justify", "right"),
        ("*Label.anchor", "e"),
        ("*Entry.justify", "right"),
        ("*Message.justify", "right"),
        ("*Message.anchor", "e"),
    ):
        try:
            root.option_add(pattern, value)
        except tk.TclError:
            # A malformed pattern must never stop the window from opening; the
            # rest of the defaults still apply. Traced so it is diagnosable.
            trace(f"rtl: option_add({pattern!r}) failed")

    # ttk widgets take alignment from the style, not the option database. Anchor
    # the label/button classes to the east so their text and any image sit on
    # the reading-start edge.
    style = ttk.Style(root)
    for name in ("TLabel", "TLabelframe.Label", "TButton", "Toolbutton"):
        try:
            style.configure(name, anchor="e")
        except tk.TclError:
            trace(f"rtl: style.configure({name!r}) failed")
    return True


def apply_rtl_to_treeview(tree: ttk.Treeview) -> bool:
    """Mirror a Treeview's column and heading alignment for RTL reading.

    Flips the east/west anchor of every data column (and the tree column
    ``#0``) and of each heading, so a table laid out left-to-right reads
    right-to-left: text columns move flush-right, and a numeric column anchored
    east stays on the reading-end edge by moving west. Centre-anchored columns
    are left alone.

    A no-op returning ``False`` in LTR, so table builders call it
    unconditionally after configuring their columns.

    Args:
        tree: A configured :class:`ttk.Treeview`.

    Returns:
        ``True`` if the tree was mirrored, ``False`` in LTR.
    """
    if not active_is_rtl():
        return False

    import tkinter as tk

    columns = ("#0", *tree["columns"])
    for col in columns:
        # The data cells (column) and the header (heading) carry independent
        # anchors, so both are mirrored. Kept as two explicit calls rather than
        # a loop over the two methods: their overloads differ and a shared
        # callable erases the return type the stubs give each.
        try:
            column_anchor = str(tree.column(col, "anchor"))
        except tk.TclError:
            column_anchor = ""
        flipped = flip_anchor(column_anchor, rtl=True)
        if column_anchor and flipped != column_anchor:
            try:
                tree.column(col, anchor=cast("Any", flipped))
            except tk.TclError:
                trace(f"rtl: could not mirror {col!r} column anchor")

        try:
            heading_anchor = str(tree.heading(col, "anchor"))
        except tk.TclError:
            heading_anchor = ""
        flipped = flip_anchor(heading_anchor, rtl=True)
        if heading_anchor and flipped != heading_anchor:
            try:
                tree.heading(col, anchor=cast("Any", flipped))
            except tk.TclError:
                trace(f"rtl: could not mirror {col!r} heading anchor")
    return True


def mirror_text_widget(text: tk.Text) -> bool:
    """Keep a prose ``tk.Text`` right-justified while the UI is RTL.

    Applies right justification to the whole buffer and, via a ``<<Modified>>``
    binding, re-applies it whenever the text changes -- so detail panes that are
    cleared and rewritten (record inspectors, journal bodies) stay aligned
    without the caller re-justifying after every update.

    A no-op returning ``False`` in LTR.

    Args:
        text: The prose widget to keep right-justified.

    Returns:
        ``True`` if RTL justification was installed, ``False`` in LTR.
    """
    if not active_is_rtl():
        return False

    import tkinter as tk

    tag = "wg_rtl"
    try:
        text.tag_configure(tag, justify="right")
    except tk.TclError:
        return False

    def _reapply(_event: object = None) -> None:
        """Re-cover the whole buffer with the right-justify tag after an edit."""
        try:
            # Cover the whole buffer, then clear the modified flag so this
            # binding does not re-fire on its own tag edit (which does not set
            # the flag, but resetting keeps the next real edit detectable).
            text.tag_add(tag, "1.0", "end")
            text.edit_modified(False)
        except tk.TclError:
            pass  # widget torn down mid-update; cosmetic, never fatal

    _reapply()
    text.bind("<<Modified>>", _reapply, add="+")
    return True
