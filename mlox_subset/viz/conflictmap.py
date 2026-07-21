"""The world conflict map: where your mods actually collide.

**An alternative map, not a change to the existing one.** ``cell_map.html``
answers "which mods touch which cells" -- coverage -- and it stays exactly as
it is, SVG and all. This page answers a different question over the same world
grid: "which cells have records that *conflict*, and who wins there". Two mods
can touch the same cell happily; the interesting cells are the ones where the
same record is defined twice.

Keeping them as two maps rather than one map with extra marks is deliberate.
Coverage is much the larger set, and painting collisions on top of it would
invite reading a busy cell as a broken one. They cross-link instead.

Drawn as a sparse SVG -- one ``<rect>`` per cell that has conflicts, placed
absolutely -- for the same reason the cell map is: a dense grid over Morrowind
plus Tamriel Rebuilt is millions of cells, almost all of them empty, and
emitting them all would produce a file no browser will open.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from mlox_subset import _, ngettext
from mlox_subset.viz import html as h
from mlox_subset.viz.geometry import Cell, CellConflicts, bounds, group_by_cell, parse_grid
from mlox_subset.viz.palette import MINE, legend_stops, saturation_point, severity

#: Pixel size of one cell in the rendered map.
_CELL_PX = 9

#: Cap on rows in the "worst cells" table, so a pathological load order does
#: not produce a hundred-thousand-row page.
_TOP_N = 40


def _svg_grid(cells: Mapping[Cell, CellConflicts], worst: int) -> str:
    """Draw the conflict grid as absolute-positioned SVG rectangles.

    Args:
        cells: Aggregated conflicts per cell.
        worst: The highest conflict count, saturating the colour ramp.

    Returns:
        The ``<svg>`` markup, or an empty-state note when there is nothing to
        draw.
    """
    box = bounds(cells)
    if box is None:
        return f'<div class="empty">{h.escape(_("No exterior cells have conflicts."))}</div>'
    min_x, min_y, max_x, max_y = box
    width = (max_x - min_x + 1) * _CELL_PX
    height = (max_y - min_y + 1) * _CELL_PX
    parts = [
        f'<svg class="grid" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img">'
    ]
    for cell, info in sorted(cells.items()):
        # SVG y grows downward, the world's grid y grows north: flip, or the
        # map comes out upside down against every other Morrowind map.
        px = (cell.x - min_x) * _CELL_PX
        py = (max_y - cell.y) * _CELL_PX
        colour = severity(info.total, worst)
        klass = ' class="mine"' if info.mine else ""
        label = _("cell (%(x)d, %(y)d): %(count)d conflict(s)") % {
            "x": cell.x,
            "y": cell.y,
            "count": info.total,
        }
        parts.append(
            f'<rect x="{px}" y="{py}" width="{_CELL_PX}" height="{_CELL_PX}" '
            f'fill="{colour}"{klass}><title>{h.escape(label)}</title></rect>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _worst_table(cells: Mapping[Cell, CellConflicts]) -> str:
    """Tabulate the cells with the most conflicts.

    Args:
        cells: Aggregated conflicts per cell.

    Returns:
        The table markup.
    """
    ranked = sorted(cells.values(), key=lambda c: (-c.total, c.cell))
    rows = []
    for info in ranked[:_TOP_N]:
        top_winner = max(info.winners.items(), key=lambda kv: kv[1])[0] if info.winners else ""
        kinds = ", ".join(f"{k} x{v}" for k, v in sorted(info.types.items(), key=lambda kv: -kv[1]))
        rows.append(
            [
                f"({info.cell.x}, {info.cell.y})",
                info.total,
                info.mine,
                kinds,
                top_winner,
            ]
        )
    return h.table(
        [
            _("Cell"),
            _("Conflicts"),
            _("Yours"),
            _("Record types"),
            _("Usually wins"),
        ],
        rows,
        numeric={1, 2},
    )


def _type_meaning() -> dict[str, str]:
    """What each spatially-keyed record type governs.

    A function rather than a module constant so the strings are marked *at the
    call*: ``_(variable)`` extracts nothing, so a lookup table of bare strings
    translated later would silently never appear in the ``.pot`` and could
    never be translated.

    Returns:
        Record type name to a one-line description of what it controls.
    """
    return {
        "Landscape": _("terrain shape, textures and vertex colours"),
        "PathGrid": _("NPC navigation -- broken edges strand NPCs, and nothing else reports it"),
        "Cell": _("the cell's own record: name, water level, region, ambient light"),
    }


def _type_table(cells: Mapping[Cell, CellConflicts]) -> str:
    """Break the conflicts down by what kind of record is being edited.

    A count of "conflicts in this cell" does not say whether two mods reshaped
    the same hillside or merely both placed a barrel. Landscape and path-grid
    conflicts are the ones with consequences you cannot see in a list, so the
    breakdown leads with them.

    Args:
        cells: Aggregated conflicts per cell.

    Returns:
        The table markup.
    """
    totals: dict[str, int] = {}
    places: dict[str, int] = {}
    for info in cells.values():
        for rectype, count in info.types.items():
            totals[rectype] = totals.get(rectype, 0) + count
            places[rectype] = places.get(rectype, 0) + 1
    meaning = _type_meaning()
    rows = [
        [rectype, count, places[rectype], meaning.get(rectype, "")]
        for rectype, count in sorted(totals.items(), key=lambda kv: -kv[1])
    ]
    return h.table(
        [_("Record type"), _("Conflicts"), _("Cells"), _("What it governs")],
        rows,
        numeric={1, 2},
    )


def build_conflict_map(
    conflicts: Sequence[Mapping[str, Any]],
    *,
    title: str = "",
    cell_map_href: str = "cell_map.html",
) -> str:
    """Render the world conflict map as a self-contained HTML page.

    Args:
        conflicts: Conflict dicts as ``detect_conflicts`` returns them.
        title: Optional page title; a sensible default is used when empty.
        cell_map_href: Where the coverage map lives, for the cross-link. Pass
            an empty string to omit it.

    Returns:
        A complete HTML document. Never raises on odd input -- records with
        unusable ids are simply not spatial and are counted as such.
    """
    cells = group_by_cell(conflicts)
    # Saturate at a high percentile rather than the maximum: one cell touched
    # by forty plugins would otherwise rescale every ordinary cell to green.
    worst = saturation_point([c.total for c in cells.values()])
    spatial = sum(c.total for c in cells.values())
    mine = sum(c.mine for c in cells.values())
    non_spatial = len(conflicts) - spatial

    stops = [
        (colour, ngettext("%(n)d conflict", "%(n)d conflicts", count) % {"n": count})
        for count, colour in legend_stops(worst)
    ]
    body = [
        h.summary(
            {
                _("Cells with conflicts"): len(cells),
                _("Spatial conflicts"): spatial,
                _("Involving your mods"): mine,
                _("Non-spatial (objects, dialogue, interiors)"): max(0, non_spatial),
            }
        ),
        h.card(
            _("Conflict density by cell"),
            _svg_grid(cells, worst)
            + h.legend(
                [*stops, (MINE, _("outlined = involves your mods"))],
                _(
                    "North is up. Hover a cell for its count. Colour saturates at the "
                    "95th percentile, so a few extreme cells do not flatten the rest."
                ),
            ),
        ),
        h.card(_("What is being edited"), _type_table(cells)),
        h.card(_("Worst cells"), _worst_table(cells)),
        _cell_map_link(cell_map_href),
    ]
    return h.page(
        title or _("Conflict map"),
        _(
            "Cells where the same record is defined by more than one plugin. "
            "Coverage (which mods touch which cells) is a different question -- "
            "see the cell map."
        ),
        "".join(body),
    )


def _cell_map_link(href: str) -> str:
    """Render the cross-link back to the coverage map.

    The link points *this* way only. The cell map is deliberately left
    untouched: it is an established view with its own SVG, and coverage is a
    different question from collision, so this is a parallel map that
    references it rather than a layer painted over it.

    Args:
        href: Relative path to the cell map, or empty to omit the link.

    Returns:
        The card markup, or an empty string when there is nothing to link to.
    """
    if not href:
        return ""
    return h.card(
        _("See also"),
        f'<a href="{h.escape(href)}">{h.escape(_("Cell map (coverage)"))}</a>'
        f'<div class="legend"><span>'
        f"{h.escape(_('That map shows which mods TOUCH each cell. Touching is not colliding: most shared cells here are fine, and this page is the subset that is not.'))}"
        "</span></div>",
    )


def cells_with_conflicts(conflicts: Iterable[Mapping[str, Any]]) -> set[tuple[int, int]]:
    """List the exterior cells that have any conflicting record.

    Used to cross-link the cell map: a coverage cell that also appears here can
    be marked so the two pages point at each other.

    Args:
        conflicts: Conflict dicts as ``detect_conflicts`` returns them.

    Returns:
        ``(x, y)`` pairs, plain tuples so callers need not import
        :class:`~mlox_subset.viz.geometry.Cell`.
    """
    out: set[tuple[int, int]] = set()
    for conflict in conflicts:
        cell = parse_grid(conflict.get("id"))
        if cell is not None:
            out.add((cell.x, cell.y))
    return out
