"""Display-backed tests for the Tk-touching RTL appliers.

The pure direction logic is covered hermetically in ``tests/test_rtl.py``; this
module checks the three helpers that actually mutate Tk state -- option-database
defaults, Treeview column anchors, and prose-widget justification -- against a
real (virtual) root. Like ``tests/test_gui_smoke.py`` it **skips** rather than
fails when Tk or a display is missing, so the hermetic suite is unaffected and
CI runs it under ``xvfb``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from wraithguard import set_language
from wraithguard.gui import rtl

if TYPE_CHECKING:
    from collections.abc import Iterator

tkinter = pytest.importorskip("tkinter", reason="Tk is not installed")


@pytest.fixture
def root() -> Iterator[object]:
    """A withdrawn Tk root, skipped when no display is available."""
    try:
        r = tkinter.Tk()
    except tkinter.TclError as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"no display available: {exc}")
    r.withdraw()
    try:
        yield r
    finally:
        r.destroy()
        set_language("en")


def test_apply_rtl_defaults_sets_option_db_when_arabic(root: object) -> None:
    set_language("ar")
    assert rtl.apply_rtl_defaults(root) is True
    # Widgets created after the call inherit the right-aligned defaults.
    label = tkinter.Label(root, text="مرحبا")
    entry = tkinter.Entry(root)
    assert str(label.cget("justify")) == "right"
    assert str(label.cget("anchor")) == "e"
    assert str(entry.cget("justify")) == "right"


def test_apply_rtl_defaults_is_noop_in_ltr(root: object) -> None:
    set_language("en")
    assert rtl.apply_rtl_defaults(root) is False
    label = tkinter.Label(root, text="hello")
    # Tk's own default label justify is "center"; the point is we did not force
    # it to "right".
    assert str(label.cget("justify")) != "right"


def test_apply_rtl_to_treeview_flips_column_and_heading(root: object) -> None:
    from tkinter import ttk

    set_language("ar")
    tree = ttk.Treeview(root, columns=("a", "b"), show="tree headings")
    tree.column("a", anchor="w")  # a text column: hugs the left in LTR
    tree.column("b", anchor="e")  # a numeric column: end-aligned right in LTR
    tree.heading("a", text="A", anchor="w")

    assert rtl.apply_rtl_to_treeview(tree) is True
    # Text column moves flush-right; numeric column keeps end-alignment by
    # moving left. The heading mirrors with its column.
    assert str(tree.column("a", "anchor")) == "e"
    assert str(tree.column("b", "anchor")) == "w"
    assert str(tree.heading("a", "anchor")) == "e"


def test_apply_rtl_to_treeview_is_noop_in_ltr(root: object) -> None:
    from tkinter import ttk

    set_language("en")
    tree = ttk.Treeview(root, columns=("a",), show="headings")
    tree.column("a", anchor="w")
    assert rtl.apply_rtl_to_treeview(tree) is False
    assert str(tree.column("a", "anchor")) == "w"


def test_mirror_text_widget_justifies_and_survives_rewrite(root: object) -> None:
    set_language("ar")
    text = tkinter.Text(root)
    text.insert("1.0", "نص عربي")
    assert rtl.mirror_text_widget(text) is True
    # The whole buffer carries the right-justify tag...
    assert "wg_rtl" in text.tag_names("1.0")
    # ...and it re-applies after the pane is cleared and rewritten, which is how
    # the detail panes update. The re-apply rides the <<Modified>> virtual
    # event, which is delivered through the event queue -- so pump it with
    # update() (not update_idletasks(), which runs only idle tasks and would
    # leave the event undelivered, exactly as a live mainloop would not).
    text.delete("1.0", "end")
    text.insert("1.0", "نص جديد")
    text.update()
    ranges = text.tag_ranges("wg_rtl")
    assert ranges  # the tag was re-added over the new content
    assert str(text.tag_cget("wg_rtl", "justify")) == "right"


def test_mirror_text_widget_is_noop_in_ltr(root: object) -> None:
    set_language("en")
    text = tkinter.Text(root)
    text.insert("1.0", "plain")
    assert rtl.mirror_text_widget(text) is False
    assert "wg_rtl" not in text.tag_names()
