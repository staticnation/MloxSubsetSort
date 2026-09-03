"""Direction-logic tests for :mod:`wraithguard.rtl`.

These cover the *pure* helpers -- language classification and the anchor/side/
sticky/justify flips -- which have no Tk dependency and so run in the hermetic
suite (and, unlike the GUI, are measured by coverage). The Tk-touching appliers
in :mod:`wraithguard.gui.rtl` are exercised under a real display by
``tests/test_rtl_tk.py``; here we only assert the decisions they are built on.
"""

from __future__ import annotations

import pytest

from wraithguard import rtl, set_language


@pytest.fixture(autouse=True)
def _restore_language():
    """Keep each test from leaking the active language into the next."""
    yield
    set_language("en")


@pytest.mark.parametrize(
    "tag",
    ["ar", "ar_EG", "ar-EG", "ar_EG.UTF-8", "he", "he_IL", "fa", "ur", "ps", "AR"],
)
def test_is_rtl_true_for_rtl_tags(tag: str) -> None:
    assert rtl.is_rtl(tag) is True


@pytest.mark.parametrize(
    "tag", ["en", "de", "pt_BR", "zh", "ja", "ru", "", None, "arabic", "en_AR"]
)
def test_is_rtl_false_for_ltr_or_unknown(tag: str | None) -> None:
    assert rtl.is_rtl(tag) is False


def test_active_is_rtl_follows_set_language() -> None:
    set_language("ar")
    assert rtl.active_is_rtl() is True
    set_language("en")
    assert rtl.active_is_rtl() is False


def test_start_and_end_anchor_flip() -> None:
    assert rtl.start_anchor(rtl=True) == "e"
    assert rtl.start_anchor(rtl=False) == "w"
    assert rtl.end_anchor(rtl=True) == "w"
    assert rtl.end_anchor(rtl=False) == "e"


def test_start_and_end_side_flip() -> None:
    assert rtl.start_side(rtl=True) == "right"
    assert rtl.start_side(rtl=False) == "left"
    assert rtl.end_side(rtl=True) == "left"
    assert rtl.end_side(rtl=False) == "right"


def test_text_justify() -> None:
    assert rtl.text_justify(rtl=True) == "right"
    assert rtl.text_justify(rtl=False) == "left"


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [("w", "e"), ("e", "w"), ("nw", "ne"), ("ne", "nw"), ("sw", "se"), ("se", "sw"), ("n", "n")],
)
def test_flip_anchor_mirrors_east_west(anchor: str, expected: str) -> None:
    assert rtl.flip_anchor(anchor, rtl=True) == expected


def test_flip_anchor_leaves_center_untouched() -> None:
    # A blind e/w swap would mangle the word's letters ("center" -> "cwntwr").
    assert rtl.flip_anchor("center", rtl=True) == "center"
    assert rtl.flip_anchor("centre", rtl=True) == "centre"


def test_flip_anchor_is_noop_in_ltr() -> None:
    for anchor in ("w", "e", "nw", "center"):
        assert rtl.flip_anchor(anchor, rtl=False) == anchor


def test_flip_side() -> None:
    assert rtl.flip_side("left", rtl=True) == "right"
    assert rtl.flip_side("right", rtl=True) == "left"
    assert rtl.flip_side("top", rtl=True) == "top"
    assert rtl.flip_side("bottom", rtl=True) == "bottom"
    assert rtl.flip_side("left", rtl=False) == "left"


def test_flip_sticky_mirrors_only_single_side() -> None:
    assert rtl.flip_sticky("w", rtl=True) == "e"
    assert rtl.flip_sticky("e", rtl=True) == "w"
    assert rtl.flip_sticky("nsw", rtl=True) == "nse"
    # A both-sides sticky is the same set either way; direction is irrelevant.
    assert set(rtl.flip_sticky("nsew", rtl=True)) == set("nsew")
    assert rtl.flip_sticky("ns", rtl=True) == "ns"


def test_flip_sticky_is_noop_in_ltr() -> None:
    assert rtl.flip_sticky("w", rtl=False) == "w"


def test_default_direction_follows_active_language() -> None:
    set_language("ar")
    assert rtl.start_anchor() == "e"
    assert rtl.text_justify() == "right"
    assert rtl.flip_anchor("w") == "e"
    set_language("en")
    assert rtl.start_anchor() == "w"
    assert rtl.text_justify() == "left"
    assert rtl.flip_anchor("w") == "w"
