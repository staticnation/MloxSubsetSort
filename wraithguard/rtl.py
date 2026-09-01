"""Text-direction logic for right-to-left languages.

Arabic (and Hebrew, Persian, Urdu, ...) read right-to-left, so a UI laid out for
a left-to-right language reads backwards to them. Turning the interface around
has two halves: *deciding* which way is forward, and *applying* that to Tk
widgets. This module is the first half -- pure functions with no Tk dependency,
so they import and unit-test without a display. The Tk appliers that consume
them live in :mod:`wraithguard.gui.rtl`, alongside the rest of the GUI's
display-only code.

Layout code reads these helpers instead of hardcoding ``"w"``/``"left"``:
:func:`start_anchor` and friends resolve to the reading-start edge in either
direction, and the ``flip_*`` helpers mirror an existing anchor/side/sticky. All
default to the active language via :func:`active_is_rtl`, and every one is a
no-op in a left-to-right language, so call sites stay unconditional -- wiring
them in costs the left-to-right languages nothing.
"""

from __future__ import annotations

from typing import Literal

from wraithguard.i18n import get_language

#: Base language tags that read right-to-left. Matched against the part before
#: any region/script suffix (``ar_EG`` -> ``ar``), so regional variants are
#: covered without listing each one. Kurdish Sorani (``ckb``) and Dhivehi
#: (``dv``) are included for completeness even though the app ships neither yet;
#: the cost of an unused entry is nil and it documents intent.
RTL_LANGUAGES: frozenset[str] = frozenset(
    {"ar", "he", "fa", "ur", "ps", "sd", "ug", "yi", "dv", "ckb", "nqo"}
)

# Translation table swapping the 'e'/'w' code points, used by the flip helpers.
# Built once; ``str.translate`` with it is a single pass and allocation-free for
# strings that contain neither character.
_EW_SWAP = str.maketrans("ewEW", "weWE")


def _base_tag(language: str | None) -> str:
    """Reduce a language tag to its lowercase base subtag.

    ``"ar_EG.UTF-8"`` and ``"ar-EG"`` both become ``"ar"``; ``None`` becomes
    ``""``. This mirrors how :mod:`gettext` and the catalogue loader normalise
    tags, so the RTL decision agrees with which catalogue actually loaded.
    """
    if not language:
        return ""
    # Split on the same separators the locale machinery uses: region (``_`` or
    # ``-``) and encoding (``.``). The first field is the language proper.
    return language.replace("-", "_").split("_", 1)[0].split(".", 1)[0].lower()


def is_rtl(language: str | None) -> bool:
    """Whether ``language`` is written right-to-left.

    Args:
        language: A language tag such as ``"ar"``, ``"ar_EG"`` or ``None``.

    Returns:
        ``True`` for a right-to-left language, ``False`` otherwise (including
        for ``None`` or an unrecognised tag).
    """
    return _base_tag(language) in RTL_LANGUAGES


def active_is_rtl() -> bool:
    """Whether the currently active UI language reads right-to-left."""
    return is_rtl(get_language())


def _resolved(rtl: bool | None) -> bool:
    """Return ``rtl`` if given, else the active language's direction.

    Every public helper takes an optional ``rtl`` so tests can force a
    direction without touching global state, while call sites can omit it and
    follow the active language.
    """
    return active_is_rtl() if rtl is None else rtl


def start_anchor(rtl: bool | None = None) -> str:
    """The anchor for the reading-*start* edge: ``"e"`` in RTL, else ``"w"``.

    Use where content should hug the edge a reader begins from -- most labels,
    list rows and left-aligned cells.
    """
    return "e" if _resolved(rtl) else "w"


def end_anchor(rtl: bool | None = None) -> str:
    """The anchor for the reading-*end* edge: ``"w"`` in RTL, else ``"e"``.

    Use for content that trails to the far edge -- a right-aligned number
    column stays end-aligned by flipping to the left in RTL.
    """
    return "w" if _resolved(rtl) else "e"


def start_side(rtl: bool | None = None) -> str:
    """The :func:`pack` side for the reading-start edge (``right``/``left``)."""
    return "right" if _resolved(rtl) else "left"


def end_side(rtl: bool | None = None) -> str:
    """The :func:`pack` side for the reading-end edge (``left``/``right``)."""
    return "left" if _resolved(rtl) else "right"


def text_justify(rtl: bool | None = None) -> Literal["left", "right"]:
    """Text justification for the active direction (``right``/``left``)."""
    return "right" if _resolved(rtl) else "left"


def flip_anchor(anchor: str, rtl: bool | None = None) -> str:
    """Mirror the east/west component of a Tk anchor when in RTL.

    ``"w"`` <-> ``"e"``, ``"nw"`` <-> ``"ne"``, and so on; the north/south
    component and ``"center"`` are unchanged. A no-op in LTR, so it is safe to
    wrap any anchor unconditionally.

    Args:
        anchor: A Tk anchor such as ``"w"``, ``"ne"`` or ``"center"``.
        rtl: Force a direction; defaults to the active language.

    Returns:
        The mirrored anchor (or ``anchor`` unchanged in LTR).
    """
    if not _resolved(rtl):
        return anchor
    # "center"/"centre" carry no direction, and a blind e/w swap would mangle
    # their letters ("center" -> "cwntwr"). The compass anchors (n/s/e/w and
    # their pairs) are the only ones with a side to mirror.
    if anchor.lower() in ("center", "centre"):
        return anchor
    return anchor.translate(_EW_SWAP)


def flip_side(side: str, rtl: bool | None = None) -> str:
    """Mirror a :func:`pack` side (``left`` <-> ``right``) when in RTL.

    ``"top"`` and ``"bottom"`` are unchanged. A no-op in LTR.
    """
    if not _resolved(rtl):
        return side
    return {"left": "right", "right": "left"}.get(side, side)


def flip_sticky(sticky: str, rtl: bool | None = None) -> str:
    """Mirror the east/west directions in a grid ``sticky`` string in RTL.

    ``"w"`` <-> ``"e"`` (so a cell that hugged the left edge hugs the right);
    ``"ew"`` stretches both ways and is unaffected. A no-op in LTR.
    """
    if not _resolved(rtl):
        return sticky
    return sticky.translate(_EW_SWAP)
