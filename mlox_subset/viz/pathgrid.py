"""Path-grid navigation graphs, and what a mod did to them.

A ``PGRD`` record is a navigation mesh: points with world coordinates, and
edges between them that NPCs follow. It is also the record type most likely to
be broken silently -- a mod that edits a cell and rebuilds its path grid can
drop connections, and nothing complains until an NPC walks into a wall. The
resource folder's ``missing_pathgrids.pl`` exists precisely because this is a
known and under-diagnosed failure.

The text view already renders the adjacency list, which is readable but not
*comparable*: spotting that node 37 lost two edges means reading two columns of
numbers side by side. Drawn as a graph with added and removed edges coloured,
the same change is immediate.

Projection is a plain top-down ``(x, y)`` drop. Path grids are near-planar
within a cell and Z varies little, so an isometric view would add distortion
for no information -- Z is reported in the tooltip instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mlox_subset import _
from mlox_subset.tes3fields.pathgrid import PathGridDecodeError, _point_fields, decode_connections
from mlox_subset.viz import html as h

#: Drawing area in pixels, before margins.
_SPAN = 620

#: Margin so nodes on the boundary are not clipped by the viewBox.
_MARGIN = 24

_ADDED = "#5cc45c"
_REMOVED = "#e05561"
_KEPT = "#5a6473"
_NODE = "#d7dae0"


def _points_and_edges(
    value: str | bytes,
    points: Any,  # noqa: ANN401 - tes3conv's `points` JSON; shape has varied by version
) -> tuple[list[tuple[int, int, int]], set[tuple[int, int]]]:
    """Decode one plugin's path grid into positioned nodes and an edge set.

    Args:
        value: The record's ``connections`` field.
        points: The record's ``points`` list.

    Returns:
        ``(coordinates, edges)``. Edges are ``(source, target)`` index pairs,
        normalised so an undirected connection has one representation.

    Raises:
        PathGridDecodeError: If the connections field cannot be decoded.
    """
    coords: list[tuple[int, int, int]] = []
    counts: list[int] = []
    if isinstance(points, Sequence) and not isinstance(points, str):
        for point in points:
            where, count = _point_fields(point)
            coords.append(where or (0, 0, 0))
            counts.append(count or 0)
    expected = sum(counts) if counts else None
    edges_flat = decode_connections(value, expected)

    edges: set[tuple[int, int]] = set()
    cursor = 0
    for index, count in enumerate(counts):
        for target in edges_flat[cursor : cursor + count]:
            # Normalise: the grid stores each connection from both ends, so
            # without this every edge appears twice and "removed" counts double.
            edges.add((index, target) if index <= target else (target, index))
        cursor += count
    return coords, edges


def _project(
    coords: Sequence[tuple[int, int, int]],
) -> tuple[list[tuple[float, float]], float, float]:
    """Scale world coordinates into the drawing area.

    Args:
        coords: World ``(x, y, z)`` per node.

    Returns:
        ``(screen_positions, scale, unused)`` where scale is world units per
        pixel, for reporting the graph's real extent.
    """
    if not coords:
        return [], 1.0, 1.0
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    width = max(1, max(xs) - min(xs))
    height = max(1, max(ys) - min(ys))
    scale = _SPAN / max(width, height)
    return (
        [
            (
                _MARGIN + (c[0] - min(xs)) * scale,
                # World Y grows north; screen Y grows down.
                _MARGIN + (max(ys) - c[1]) * scale,
            )
            for c in coords
        ],
        scale,
        max(width, height),
    )


def build_pathgrid_graph(
    winner_value: str | bytes,
    winner_points: Any,  # noqa: ANN401 - see _points_and_edges
    *,
    winner_name: str,
    loser_value: str | bytes | None = None,
    loser_points: Any = None,  # noqa: ANN401 - see _points_and_edges
    loser_name: str = "",
    cell_label: str = "",
) -> str:
    """Render a path grid, optionally diffed against the record it overrides.

    Args:
        winner_value: The winning plugin's ``connections`` field.
        winner_points: The winning plugin's ``points`` list.
        winner_name: Winning plugin filename.
        loser_value: The overridden plugin's ``connections``, when comparing.
        loser_points: The overridden plugin's ``points``.
        loser_name: Overridden plugin filename.
        cell_label: Optional cell description.

    Returns:
        A complete HTML document. With no loser given it draws the grid alone.

    Raises:
        PathGridDecodeError: If the winner's connections cannot be decoded.
    """
    coords, edges = _points_and_edges(winner_value, winner_points)
    before: set[tuple[int, int]] = set()
    comparing = loser_value is not None
    if comparing:
        try:
            # NB: not `_, before = ...` -- `_` is the gettext marker in this
            # module and rebinding it here shadows it for the rest of the
            # function. That has now cost two debugging rounds in this codebase
            # (see the sort engine's `_rank`), so tests/test_standards.py
            # enforces it.
            _coords, before = _points_and_edges(loser_value or b"", loser_points)
        except PathGridDecodeError:
            # The overridden record being unreadable is not a reason to refuse
            # to draw the one that wins -- fall back to showing it alone.
            comparing = False
            before = set()

    positions, _scale, extent = _project(coords)
    added = edges - before if comparing else set()
    removed = before - edges if comparing else set()
    kept = edges & before if comparing else edges

    parts = [
        f'<svg class="grid" viewBox="0 0 {_SPAN + 2 * _MARGIN} {_SPAN + 2 * _MARGIN}" '
        f'width="{_SPAN + 2 * _MARGIN}" height="{_SPAN + 2 * _MARGIN}" role="img">'
    ]

    def draw(pairs: set[tuple[int, int]], colour: str, width: float) -> None:
        """Append one class of edges to the drawing.

        Args:
            pairs: The edges to draw.
            colour: Stroke colour.
            width: Stroke width.
        """
        for source, target in sorted(pairs):
            if source >= len(positions) or target >= len(positions):
                continue  # an edge naming a point the grid does not have
            x1, y1 = positions[source]
            x2, y2 = positions[target]
            parts.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="{colour}" stroke-width="{width}"/>'
            )

    # Draw unchanged first so added/removed sit on top of them.
    draw(kept, _KEPT, 1.0)
    draw(removed, _REMOVED, 2.0)
    draw(added, _ADDED, 2.0)

    for index, (x, y) in enumerate(positions):
        z = coords[index][2] if index < len(coords) else 0
        label = _("point %(index)d at (%(x)d, %(y)d, %(z)d)") % {
            "index": index,
            "x": coords[index][0],
            "y": coords[index][1],
            "z": z,
        }
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{_NODE}">'
            f"<title>{h.escape(label)}</title></circle>"
        )
    parts.append("</svg>")

    entries = [(_NODE, _("path point"))]
    if comparing:
        entries += [
            (_ADDED, _("edge added by the winner")),
            (_REMOVED, _("edge removed by the winner")),
            (_KEPT, _("unchanged")),
        ]
    else:
        entries.append((_KEPT, _("connection")))

    facts: dict[str, object] = {
        _("Cell"): cell_label or _("(unknown)"),
        _("Winner"): winner_name,
        _("Points"): len(coords),
        _("Connections"): len(edges),
        _("Extent"): _("%(units)d world units") % {"units": int(extent)},
    }
    if comparing:
        facts[_("Overrides")] = loser_name
        facts[_("Edges added")] = len(added)
        facts[_("Edges removed")] = len(removed)

    verdict = ""
    if comparing and not added and not removed:
        verdict = _(
            "The navigation graph is unchanged. These records differ in point "
            "positions or flags, not in connectivity."
        )
    elif comparing and removed and not added:
        verdict = _(
            "This mod only removes connections. That is the shape of an "
            "accidentally rebuilt path grid, and it is worth checking in game."
        )

    body = [
        h.summary(facts),
        h.card(
            _("Navigation graph"),
            (f'<div class="empty">{h.escape(verdict)}</div>' if verdict else "")
            + "".join(parts)
            + h.legend(entries, _("Top-down view, north up. Hover a point for its coordinates.")),
        ),
    ]
    return h.page(
        _("Path grid"),
        _("The navigation mesh NPCs follow, and what this plugin changed about it."),
        "".join(body),
    )
