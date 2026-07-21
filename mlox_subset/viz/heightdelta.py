"""Terrain height differences between two plugins' versions of a cell.

This exists because of a specific, measured failure of the text diff: ``VHGT``
is stored as *doubly-cumulative deltas*, so changing one vertex changes every
byte after it. Two landscape records differing by a single nudged vertex
produce entirely different base64, and the diff window reports them as
completely different. That is not a display quirk -- it actively misleads,
because "completely different" and "one vertex moved 8 units" call for opposite
decisions about load order.

Decoding to absolute heights and subtracting gives the honest answer: a 65x65
grid of signed deltas in world units, rendered as a divergence map. Red is
raised, blue is lowered, and the summary states the largest movement in units
so the picture is anchored to a number.
"""

from __future__ import annotations

from collections.abc import Sequence

from mlox_subset import _
from mlox_subset.tes3fields.landscape import LandscapeDecodeError, decode_vertex_heights
from mlox_subset.viz import html as h
from mlox_subset.viz.palette import divergence

#: Pixel size of one vertex in the rendered grid. 65 x 9 = 585px, which fits a
#: normal window without scaling and keeps individual vertices clickable.
_VERTEX_PX = 9

#: Deltas below this many world units are treated as noise for the "changed
#: vertices" count. A unit is tiny -- the player is ~128 units tall -- so a
#: sub-unit difference is not a change anyone can see.
_NOISE_FLOOR = 1.0


class HeightDeltaError(Exception):
    """Raised when a height comparison cannot be rendered."""


def _grid_svg(deltas: Sequence[Sequence[float]], scale: float) -> str:
    """Draw the delta grid as SVG.

    Args:
        deltas: Row-major signed height deltas, south edge first.
        scale: Magnitude that saturates the colour ramp.

    Returns:
        The ``<svg>`` markup.
    """
    size = len(deltas)
    span = size * _VERTEX_PX
    parts = [
        f'<svg class="grid" viewBox="0 0 {span} {span}" '
        f'width="{span}" height="{span}" role="img">'
    ]
    for row_index, row in enumerate(deltas):
        # Row 0 is the SOUTH edge (the stored data runs bottom-up), so flip for
        # display -- north at the top, matching every other map in the tool.
        y = (size - 1 - row_index) * _VERTEX_PX
        for col_index, value in enumerate(row):
            if abs(value) < _NOISE_FLOOR:
                continue  # leave unchanged vertices as background
            x = col_index * _VERTEX_PX
            parts.append(
                f'<rect x="{x}" y="{y}" width="{_VERTEX_PX}" height="{_VERTEX_PX}" '
                f'fill="{divergence(value, scale)}"><title>'
                f"{h.escape(_('vertex (%(col)d, %(row)d): %(delta)+.0f units') % {'col': col_index, 'row': row_index, 'delta': value})}"
                "</title></rect>"
            )
    parts.append("</svg>")
    return "".join(parts)


def _subtract(
    winner: Sequence[Sequence[float]], loser: Sequence[Sequence[float]]
) -> list[list[float]]:
    """Compute winner-minus-loser over two equally-shaped height grids.

    Args:
        winner: The plugin that wins the conflict.
        loser: The plugin it overrides.

    Returns:
        Signed deltas, positive where the winner is higher.

    Raises:
        HeightDeltaError: If the grids are not the same shape.
    """
    if len(winner) != len(loser) or any(len(a) != len(b) for a, b in zip(winner, loser)):
        raise HeightDeltaError(
            "the two landscape records decode to different grid sizes, so their "
            "heights cannot be compared vertex by vertex"
        )
    return [[float(a) - float(b) for a, b in zip(wr, lr)] for wr, lr in zip(winner, loser)]


def build_height_delta(
    winner_value: str | bytes,
    loser_value: str | bytes,
    *,
    winner_name: str,
    loser_name: str,
    winner_offset: float = 0.0,
    loser_offset: float = 0.0,
    cell_label: str = "",
) -> str:
    """Render the terrain difference between two plugins' landscape records.

    Args:
        winner_value: The winning plugin's ``vertex_heights.data``.
        loser_value: The losing plugin's ``vertex_heights.data``.
        winner_name: Winning plugin filename, for labelling.
        loser_name: Losing plugin filename, for labelling.
        winner_offset: The winner's ``vertex_heights.offset``, needed because
            ``VHGT`` heights are relative to it.
        loser_offset: The loser's ``vertex_heights.offset``.
        cell_label: Optional cell description, e.g. ``"(43, -45)"``.

    Returns:
        A complete HTML document.

    Raises:
        HeightDeltaError: If either field cannot be decoded, or the two decode
            to different shapes.
    """
    try:
        winner = decode_vertex_heights(winner_value, winner_offset)
        loser = decode_vertex_heights(loser_value, loser_offset)
    except LandscapeDecodeError as exc:
        raise HeightDeltaError(str(exc)) from exc

    deltas = _subtract(winner, loser)
    flat = [value for row in deltas for value in row]
    changed = [value for value in flat if abs(value) >= _NOISE_FLOOR]
    peak = max((abs(value) for value in flat), default=0.0)
    scale = peak if peak > 0 else 1.0

    if changed:
        raised = sum(1 for value in changed if value > 0)
        verdict = _(
            "%(changed)d of %(total)d vertices differ. Largest movement %(peak).0f units "
            "(%(up)d raised, %(down)d lowered)."
        ) % {
            "changed": len(changed),
            "total": len(flat),
            "peak": peak,
            "up": raised,
            "down": len(changed) - raised,
        }
    else:
        verdict = _(
            "The terrain is identical. These records differ in some other field -- "
            "textures, colours or the world map -- not in height."
        )

    body = [
        h.summary(
            {
                _("Cell"): cell_label or _("(unknown)"),
                _("Winner"): winner_name,
                _("Overrides"): loser_name,
            }
        ),
        h.card(
            _("Height difference"),
            f'<div class="empty">{h.escape(verdict)}</div>'
            + _grid_svg(deltas, scale)
            + h.legend(
                [
                    (divergence(scale, scale), _("winner is higher")),
                    (divergence(-scale, scale), _("winner is lower")),
                ],
                _(
                    "North is up. Unchanged vertices are left blank. Colour saturates "
                    "at the largest movement in this cell, so shades are comparable "
                    "within a page but not between pages."
                ),
            ),
        ),
    ]
    return h.page(
        _("Terrain difference"),
        _(
            "Absolute heights, decoded and subtracted. Comparing the raw fields is "
            "misleading: heights are stored as cumulative deltas, so moving one "
            "vertex changes every byte after it."
        ),
        "".join(body),
    )
