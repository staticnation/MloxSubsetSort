"""The cell map: which mods touch which cells.

Moved out of ``wraithguard_toolkit.py``, where it was one 216-line f-string that
was effectively uneditable -- every brace doubled, no way to test a part of it,
and 216 lines of presentation sitting in the middle of the sort engine (the
move is recorded in ``CODE_REVIEW.md`` §29). It is now assembled from small
functions that
each return a fragment, so a change to the legend cannot break the script and
each piece can be asserted on directly.

This answers **coverage**: how many mods touch each cell. That is a different
and much larger question than *conflict* -- two mods can edit the same cell
happily -- so the conflict map (:mod:`~wraithguard.viz.conflictmap`) is a
separate page built by a separate button. Neither links to nor depends on the
other, deliberately: an earlier version had the cell map generate the conflict
view to fill in a cross-link button, and when that generation was slow or
failed the cell map lost its button with no explanation.

The heatmap is a sparse SVG -- one ``<rect>`` per *touched* cell, positioned
absolutely -- because a dense grid over Morrowind plus Tamriel Rebuilt is
millions of cells, almost all empty. Ported from modmapper, fed by this tool's
own load order.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from wraithguard.viz.cellmap_js import CELLMAP_CSS, CELLMAP_JS
from wraithguard.viz.palette import coverage_heat, coverage_legend_stops

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: Largest exterior cell coordinate treated as real. Beyond this a plugin is
#: almost certainly corrupt, and plotting it would stretch the map to nothing.
CELL_GRID_LIMIT = 4096

#: A 12px square on a 13px pitch, leaving a 1px gutter.
CELL_MAP_CELL_PX = 12
CELL_MAP_STEP_PX = 13


def _escape(value: object) -> str:
    """Escape a value for HTML text or an attribute.

    Kept local rather than imported from :mod:`~wraithguard.viz.html` because
    this page has its own standalone styling and shell -- it predates the
    shared one and deliberately still looks like itself.

    Args:
        value: Anything; stringified first. Plugin names come from disk and are
            not trusted.

    Returns:
        The escaped string.
    """
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _anchor(gx: int, gy: int) -> str:
    """Build a DOM id for a cell's list row.

    Args:
        gx: Grid X.
        gy: Grid Y.

    Returns:
        An id with no characters needing escaping in a selector.
    """
    return f"e_{gx}_{gy}".replace("-", "m")


def _modattr(mods: Sequence[str]) -> str:
    """Render a cell's mod list as an exact-match filter token.

    ``|a.esp|b.esp|`` so a substring search for ``|name|`` can never partially
    match a longer plugin's filename.

    Args:
        mods: Plugin filenames touching this cell, any case.

    Returns:
        An attribute-safe token string.
    """
    return _escape("|" + "|".join(m.lower() for m in mods) + "|")


def _in_bounds(key: tuple[int, int]) -> bool:
    """Report whether a grid coordinate is plausible.

    An interior cell with a garbage grid field, or a mis-parse, can otherwise
    place a marker millions of cells away and flatten the whole map.

    Args:
        key: The ``(x, y)`` grid coordinate.

    Returns:
        ``True`` if both axes are within :data:`CELL_GRID_LIMIT`.
    """
    return -CELL_GRID_LIMIT <= key[0] <= CELL_GRID_LIMIT and (
        -CELL_GRID_LIMIT <= key[1] <= CELL_GRID_LIMIT
    )


def _focus_options(
    exterior: Mapping[tuple[int, int], Sequence[str]],
    interior: Mapping[str, Sequence[str]],
    subset_lower: set[str],
) -> str:
    """Build the "Focus on mod" dropdown options.

    The user's own mods sort first and carry a star, so the mods they care about
    are reachable without scrolling a 989-entry list.

    Args:
        exterior: Exterior cell to the mods touching it.
        interior: Interior cell name to the mods touching it.
        subset_lower: Lower-cased filenames of the user's own mods.

    Returns:
        The ``<option>`` markup.
    """
    all_mods: dict[str, str] = {}
    for mods in [*exterior.values(), *interior.values()]:
        for mod in mods:
            all_mods.setdefault(mod.lower(), mod)
    star = " ★"
    return "".join(
        f'<option value="{_escape(low)}">{_escape(all_mods[low])}'
        f"{star if low in subset_lower else ''}</option>"
        for low in sorted(all_mods, key=lambda x: (x not in subset_lower, x))
    )


def _svg_grid(
    exterior: Mapping[tuple[int, int], Sequence[str]],
    subset_lower: set[str],
    worst: int,
) -> str:
    """Draw the coverage heatmap as absolutely-placed SVG rectangles.

    Args:
        exterior: In-bounds exterior cells to their mods.
        subset_lower: Lower-cased filenames of the user's own mods.
        worst: The highest mod count on the map, saturating the color ramp.

    Returns:
        The scrollable wrapper and SVG, or an empty-state note.
    """
    if not exterior:
        return '<p class="sub">No exterior cells touched.</p>'
    xs = [k[0] for k in exterior]
    ys = [k[1] for k in exterior]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    width = (maxx - minx + 1) * CELL_MAP_STEP_PX
    height = (maxy - miny + 1) * CELL_MAP_STEP_PX
    rects = []
    for (gx, gy), mods in exterior.items():
        px = (gx - minx) * CELL_MAP_STEP_PX
        # North (max y) at the top, matching every other Morrowind map.
        py = (maxy - gy) * CELL_MAP_STEP_PX
        custom = any(m.lower() in subset_lower for m in mods)
        tip = f"({gx}, {gy}) - {len(mods)} mod(s): " + ", ".join(mods)
        stroke = ' stroke="#ffd24a" stroke-width="1.4"' if custom else ""
        rects.append(
            f'<rect x="{px}" y="{py}" width="{CELL_MAP_CELL_PX}" '
            f'height="{CELL_MAP_CELL_PX}" fill="{coverage_heat(len(mods), worst)}"'
            f'{stroke} class="cell" data-t="{_escape(tip)}" data-m="{_modattr(mods)}" '
            f"onclick=\"jump('{_anchor(gx, gy)}')\"></rect>"
        )
    svg = (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">' + "".join(rects) + "</svg>"
    )
    return f'<div class="mapwrap">{svg}</div>'


def _exterior_rows(
    exterior: Mapping[tuple[int, int], Sequence[str]], subset_lower: set[str]
) -> str:
    """Build the exterior-cell list rows, busiest first.

    Args:
        exterior: In-bounds exterior cells to their mods.
        subset_lower: Lower-cased filenames of the user's own mods.

    Returns:
        The ``<tr>`` markup, or an empty-state row.
    """
    rows = []
    for (gx, gy), mods in sorted(exterior.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        custom = any(m.lower() in subset_lower for m in mods)
        cls = ' class="cust"' if custom else ""
        rows.append(
            f'<tr id="{_anchor(gx, gy)}"{cls} data-m="{_modattr(mods)}">'
            f"<td>({gx}, {gy})</td><td>{len(mods)}</td>"
            f'<td>{_escape(", ".join(mods))}</td></tr>'
        )
    return "".join(rows) or "<tr><td colspan=3 class=sub>None.</td></tr>"


def _interior_rows(interior: Mapping[str, Sequence[str]], subset_lower: set[str]) -> str:
    """Build the interior-cell list rows, busiest first.

    Args:
        interior: Interior cell name to the mods touching it.
        subset_lower: Lower-cased filenames of the user's own mods.

    Returns:
        The ``<tr>`` markup, or an empty-state row.
    """
    rows = []
    for name, mods in sorted(interior.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())):
        custom = any(m.lower() in subset_lower for m in mods)
        cls = ' class="cust"' if custom else ""
        rows.append(
            f'<tr{cls} data-m="{_modattr(mods)}"><td>{_escape(name)}</td>'
            f"<td>{len(mods)}</td><td>{_escape(', '.join(mods))}</td></tr>"
        )
    return "".join(rows) or "<tr><td colspan=3 class=sub>None.</td></tr>"


def _legend(worst: int) -> str:
    """Build the mods-per-cell color legend.

    Generated from the same ramp the map uses, so the two can never disagree --
    the old hand-written legend listed five fixed swatches while the map had
    its own hard-coded five, and keeping them in step was manual.

    Args:
        worst: The highest mod count on the map.

    Returns:
        The legend markup.
    """
    swatches = "".join(
        f'<span style="background:{color};color:{"#fff" if dark else "#111"}">{label}</span>'
        for label, color, dark in coverage_legend_stops(worst)
    )
    return (
        f'<div class="legend">Mods per cell: {swatches}'
        " &nbsp;(north up; hover a cell for its mods, click it to jump to the list)</div>"
    )


def generate_cell_map_html(
    coverage: Mapping[str, Any],
    title: str = "Wraithguard Toolkit - Cell Map",
    generated_at: datetime | None = None,
) -> str:
    """Render the cell map as a self-contained HTML page.

    Three tabs: a color-coded exterior heatmap (one uniform square per touched
    cell, hotter where more mods overlap, click to jump to its list entry), an
    exterior-cell list and an interior-cell list. Cells the user's own mods
    touch get a gold outline and orange text.

    Args:
        coverage: The result of ``build_cell_coverage`` -- ``exterior``,
            ``interior``, ``scanned`` and ``subset_lower``.
        title: The page title.
        generated_at: When this map was built, stamped into the header so a map
            found on disk can be told from a fresh one. Defaults to now.

    Returns:
        A complete, self-contained HTML document -- no CDN, no external script,
        because the tool runs offline and ships frozen.
    """
    exterior_all = coverage["exterior"]
    interior = coverage["interior"]
    subset_lower = set(coverage.get("subset_lower", set()))
    stamped = generated_at or datetime.now()  # noqa: DTZ005 - local clock is what the user reads

    exterior = {k: v for k, v in exterior_all.items() if _in_bounds(k)}
    dropped = len(exterior_all) - len(exterior)
    worst = max((len(m) for m in exterior.values()), default=1)

    ext_conflicts = sum(1 for m in exterior.values() if len(m) > 1)
    int_conflicts = sum(1 for m in interior.values() if len(m) > 1)
    dropped_note = (
        f" {dropped} cell(s) had out-of-range coordinates and were dropped." if dropped else ""
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{_escape(title)}</title>
<style>{CELLMAP_CSS}</style></head><body>
<div id="tt"></div>
<h1>{_escape(title)}</h1>
<p class="sub">Scanned {coverage["scanned"]} plugin(s). Exterior: {len(exterior)} cell(s)
 touched ({ext_conflicts} by 2+ mods). Interior: {len(interior)} cell(s) touched
 ({int_conflicts} by 2+ mods). Cells your custom mods touch are highlighted
 (gold outline / orange text).{dropped_note}</p>
<p class="sub stamp">Generated {stamped.strftime("%Y-%m-%d %H:%M:%S")}</p>
{_legend(worst)}
<div class="focusbar">Focus on mod:
 <select id="focus" onchange="setFocus(this.value)"><option value="">- all mods -</option>{
    _focus_options(exterior_all, interior, subset_lower)
}</select>
 <button onclick="document.getElementById('focus').value='';setFocus('')">Clear</button>
 <div id="focusinfo" class="sub"></div></div>
<div class="tabs">
 <button id="b0" class="on" onclick="show(0)">Map</button>
 <button id="b1" onclick="show(1)">Exterior list ({len(exterior)})</button>
 <button id="b2" onclick="show(2)">Interior list ({len(interior)})</button>
</div>
<div id="t0" class="tab on">{_svg_grid(exterior, subset_lower, worst)}</div>
<div id="t1" class="tab"><input class="f"
  placeholder="Filter exterior cells / mods..." onkeyup="ff('xt')">
 <div class="listwrap"><table class="list" id="xt"><thead><tr><th>Cell (x, y)</th><th>#</th>
 <th>Mods (load order, last wins)</th></tr></thead>
 <tbody>{_exterior_rows(exterior, subset_lower)}</tbody></table></div></div>
<div id="t2" class="tab"><input class="f"
  placeholder="Filter interior cells / mods..." onkeyup="ff('it')">
 <div class="listwrap"><table class="list" id="it"><thead><tr><th>Cell</th><th>#</th>
 <th>Mods (load order, last wins)</th></tr></thead>
 <tbody>{_interior_rows(interior, subset_lower)}</tbody></table></div></div>
<script>{CELLMAP_JS}</script>
</body></html>
"""
