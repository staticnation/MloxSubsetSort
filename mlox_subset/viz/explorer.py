"""The conflict explorer: one page, several views of the same conflicts.

This supersedes the standalone conflict map. It carries over the cell map's
interaction language on purpose -- tabbed views, a mod focus dropdown, list
filters, a scrollable map and instant delegated tooltips -- because the two
pages are read in the same sitting and switching idioms between them would be
its own kind of friction.

What it adds is **granularity**. The world map answers "which cells have
conflicts"; selecting a cell answers "and what do they actually look like
there", with the terrain surface, the height difference and the navigation
graph for that one cell. Reaching a cell works from either direction: click it
on the map, or click its row in the list.

Heavy per-cell payloads are bounded (see :mod:`mlox_subset.viz.detail`), so
cells without decoded detail still appear on the map and in the lists -- they
simply route to the list rather than the local view, and say so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from mlox_subset import _
from mlox_subset.viz import html as h
from mlox_subset.viz.detail import detail_cells
from mlox_subset.viz.draw_js import DRAW_JS
from mlox_subset.viz.explorer_js import EXPLORER_CSS, EXPLORER_JS
from mlox_subset.viz.geometry import Cell, CellConflicts, bounds, group_by_cell, is_interior
from mlox_subset.viz.palette import MINE, legend_stops, saturation_point, severity

#: Pixel geometry of the world map: a square on a slightly larger pitch, so
#: cells read as separate without needing a stroke between them.
_CELL_PX = 11
_STEP_PX = 12

#: The world-terrain (knitted 3D) toggle is **held back from release**. The
#: feature works but its rendering still needs another pass, and the rest of
#: the explorer ships without it. The button and canvas are commented out here
#: rather than deleted so re-enabling is a one-line change; the data collector
#: (``collect_world_terrain``) and the client's ``drawWorld`` stay in the tree,
#: tested, waiting. Set this to the toggle markup to bring it back.
_WORLD_TERRAIN_TOGGLE = ""


def _anchor(cell: Cell) -> str:
    """Build a DOM-safe row id for a cell.

    Args:
        cell: The cell.

    Returns:
        An id with no characters that would need escaping in a selector.
    """
    return f"r_{cell.x},{cell.y}".replace("-", "m").replace(",", "_")


def _mod_attr(plugins: Sequence[str]) -> str:
    """Build the ``|a.esp|b.esp|`` token list the focus filter matches on.

    Args:
        plugins: Plugin names.

    Returns:
        The escaped attribute value.
    """
    return h.escape("|" + "|".join(p.lower() for p in plugins) + "|")


def _world_svg(cells: Mapping[Cell, CellConflicts], worst: int, detailed: set[Cell]) -> str:
    """Draw the world conflict grid.

    Args:
        cells: Aggregated conflicts per cell.
        worst: Count that saturates the colour ramp.
        detailed: Cells that have decoded local detail, outlined so it is
            obvious which ones open a local view.

    Returns:
        The scrollable ``<div>`` wrapping the SVG.
    """
    box = bounds(cells)
    if box is None:
        return f'<div class="empty">{h.escape(_("No exterior cells have conflicts."))}</div>'
    min_x, min_y, max_x, max_y = box
    width = (max_x - min_x + 1) * _STEP_PX
    height = (max_y - min_y + 1) * _STEP_PX
    parts = [
        f'<div class="mapwrap"><svg width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
    ]
    for cell, info in sorted(cells.items()):
        px = (cell.x - min_x) * _STEP_PX
        # SVG y grows down, world y grows north: flip so north is up.
        py = (max_y - cell.y) * _STEP_PX
        kinds = ", ".join(f"{k} x{v}" for k, v in sorted(info.types.items(), key=lambda kv: -kv[1]))
        tip = _("(%(x)d, %(y)d) -- %(count)d conflict(s)\n%(kinds)s\nwinner: %(winner)s") % {
            "x": cell.x,
            "y": cell.y,
            "count": info.total,
            "kinds": kinds,
            "winner": (max(info.winners.items(), key=lambda kv: kv[1])[0] if info.winners else "?"),
        }
        if cell in detailed:
            tip += "\n" + _("click to open the local view")
        classes = "cell"
        if info.mine:
            classes += " mine"
        if cell in detailed:
            classes += " hasdetail"
        parts.append(
            f'<rect x="{px}" y="{py}" width="{_CELL_PX}" height="{_CELL_PX}" '
            f'fill="{severity(info.total, worst)}" class="{classes}" '
            f'data-t="{h.escape(tip)}" data-m="{_mod_attr(info.plugins)}" '
            f'data-n="{info.total}" '
            f"onclick=\"vizSelect('{cell.x},{cell.y}',1)\"></rect>"
        )
    parts.append("</svg></div>")
    return "".join(parts)


def _exterior_rows(cells: Mapping[Cell, CellConflicts], detailed: set[Cell]) -> str:
    """Build the exterior list rows.

    Args:
        cells: Aggregated conflicts per cell.
        detailed: Cells with decoded local detail.

    Returns:
        The ``<tr>`` markup.
    """
    rows = []
    for info in sorted(cells.values(), key=lambda c: (-c.total, c.cell)):
        cell = info.cell
        kinds = ", ".join(f"{k} x{v}" for k, v in sorted(info.types.items(), key=lambda kv: -kv[1]))
        winner = max(info.winners.items(), key=lambda kv: kv[1])[0] if info.winners else ""
        open_note = _("open") if cell in detailed else ""
        rows.append(
            f'<tr class="row" id="{_anchor(cell)}" data-m="{_mod_attr(info.plugins)}" '
            f"onclick=\"vizSelect('{cell.x},{cell.y}',0)\">"
            f"<td>({cell.x}, {cell.y})</td>"
            f'<td class="num">{info.total}</td>'
            f'<td class="num">{info.mine}</td>'
            f"<td>{h.escape(kinds)}</td>"
            f"<td>{h.escape(winner)}</td>"
            f'<td>{h.escape(", ".join(info.plugins))}</td>'
            f"<td>{h.escape(open_note)}</td></tr>"
        )
    return "".join(rows)


def _interior_rows(conflicts: Sequence[Mapping[str, Any]]) -> str:
    """Build the interior list rows.

    Interiors have no grid coordinates, so they cannot be mapped -- but they
    still conflict, and dropping them would quietly under-report. They get a
    list of their own instead.

    Args:
        conflicts: Every conflict.

    Returns:
        The ``<tr>`` markup.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for conflict in conflicts:
        name = conflict.get("id")
        if not is_interior(name):
            continue
        entry = grouped.setdefault(
            str(name), {"n": 0, "mine": 0, "types": {}, "plugins": [], "winner": ""}
        )
        entry["n"] += 1
        entry["mine"] += 1 if conflict.get("involves_subset") else 0
        rectype = str(conflict.get("type") or "?")
        entry["types"][rectype] = entry["types"].get(rectype, 0) + 1
        for plugin in conflict.get("plugins") or []:
            if plugin not in entry["plugins"]:
                entry["plugins"].append(plugin)
        entry["winner"] = conflict.get("winner") or entry["winner"]
    rows = []
    for name, entry in sorted(grouped.items(), key=lambda kv: (-kv[1]["n"], kv[0].lower())):
        kinds = ", ".join(
            f"{k} x{v}" for k, v in sorted(entry["types"].items(), key=lambda kv: -kv[1])
        )
        rows.append(
            f'<tr class="row" data-m="{_mod_attr(entry["plugins"])}">'
            f"<td>{h.escape(name)}</td>"
            f'<td class="num">{entry["n"]}</td>'
            f'<td class="num">{entry["mine"]}</td>'
            f"<td>{h.escape(kinds)}</td>"
            f'<td>{h.escape(entry["winner"])}</td>'
            f'<td>{h.escape(", ".join(entry["plugins"]))}</td></tr>'
        )
    return "".join(rows)


def _labels() -> dict[str, str]:
    """Every string the client script renders.

    Marked here rather than in the JavaScript so ``tools/make_pot.py`` can
    extract them: the extractor reads Python, and a string living only in a
    script constant would silently never be translatable.

    Returns:
        Label key to translated text, with ``{placeholder}`` slots the script
        substitutes.
    """
    return {
        "focusinfo": _(
            "Conflicts in {cells} cell(s), {conflicts} record(s) total. "
            "Shares those cells with {mods} other mod(s): {top}"
        ),
        "detailfor": _("Cell {cell}"),
        "nodetail": _(
            "Cell {cell} has no decoded detail on this page -- see the list for what conflicts there."
        ),
        "surfaceof": _("{plugin} -- heights {lo} to {hi} units. Drag to rotate."),
        "diffof": _(
            "{winner} over {loser}: {changed} of {total} vertices differ, "
            "largest movement {peak} units."
        ),
        "navof": _(
            "{plugin} -- {points} point(s), {edges} edge(s); {added} added, {removed} removed."
        ),
        "noland": _("No landscape data for this cell."),
        "nonav": _("No path grid for this cell."),
        "needtwo": _("Only one plugin has terrain here, so there is nothing to subtract."),
        "noworld": _(
            "No terrain was decoded for the world view. Run a conflict scan with "
            "tes3conv available, so landscape records can be read."
        ),
        "worldof": _(
            "{cells} cell(s) knitted, {quads} faces, heights {lo} to {hi} units. " "Drag to rotate."
        ),
        "mismatch": _(
            "These two landscape records decode to different grid sizes, so their "
            "heights cannot be compared vertex by vertex."
        ),
    }


def build_explorer(
    conflicts: Sequence[Mapping[str, Any]],
    *,
    detail: Mapping[str, Any] | None = None,
    cell_map_href: str = "cell_map.html",
    title: str = "",
    data_dir: str = "",
    embed_detail: bool = True,
    world: Mapping[str, Any] | None = None,
    coverage_only: bool = False,
) -> str:
    """Render the conflict explorer as one self-contained HTML page.

    Args:
        conflicts: Conflict dicts as ``detect_conflicts`` returns them.
        detail: Decoded per-cell payloads from
            :func:`~mlox_subset.viz.detail.collect_detail`. Cells absent from
            it still map and list; they just have no local view.
        cell_map_href: Where the coverage map lives, for the cross-link.
        title: Optional page title.
        data_dir: Relative path to the sidecar folder written by
            :func:`~mlox_subset.viz.sidecar.write_sidecars`. When given, the
            page loads its overview data from there and fetches
            full-resolution cells on demand, which is what keeps the document
            small enough to open.
        world: Knitted world terrain from
            :func:`~mlox_subset.viz.detail.collect_world_terrain`, embedded
            when small or loaded from the sidecar when not.
        embed_detail: Whether to inline the overview data. Left on for callers
            with no sidecar (and for the tests); turned off by the app, which
            writes sidecars instead.
        coverage_only: Whether the rows came from cell coverage rather than a
            record scan. Adds a banner and subtitle saying so, since coverage
            overlap and record conflict are deliberately different questions.

    Returns:
        A complete HTML document with no external dependencies.
    """
    detail = dict(detail or {})
    cells = group_by_cell(conflicts)
    if coverage_only:
        # Coverage rows are one-per-cell, so counting them would make every
        # cell equally hot and wash the map to a single colour. The meaningful
        # heat in coverage mode is how many mods touch a cell -- exactly what
        # the cell map shades by -- and that is already tracked as `plugins`.
        cells = {c: info._replace(total=len(info.plugins)) for c, info in cells.items()}
    worst = saturation_point([c.total for c in cells.values()])
    detailed = detail_cells(detail)

    every_mod: dict[str, str] = {}
    for info in cells.values():
        for plugin in info.plugins:
            every_mod.setdefault(plugin.lower(), plugin)
    options = "".join(
        f'<option value="{h.escape(low)}">{h.escape(name)}</option>'
        for low, name in sorted(every_mod.items(), key=lambda kv: kv[1].lower())
    )

    spatial = sum(c.total for c in cells.values())
    payload: dict[str, Any] = {
        "detail": dict(detail) if embed_detail else {},
        "labels": _labels(),
        "dataDir": data_dir,
    }
    # The overview sidecar is a plain <script> tag, not a fetch: `fetch()` is
    # blocked against file:// and these pages open from disk. world.js is not
    # referenced -- the 3D world terrain is held back, so requesting it would
    # only 404. Re-add it here alongside re-enabling the toggle.
    sidecar = f'<script src="{h.escape(data_dir)}/overview.js"></script>' if data_dir else ""
    # With a data folder, the shared CSS/JS are referenced as files under
    # assets/ so the page is small and debuggable; without one (tests,
    # one-offs) they are inlined so the page stands alone. This is the fix for
    # the inline blob being impossible to inspect or edit.
    if data_dir:
        a = h.escape(f"{data_dir}/assets")
        head = (
            f'<link rel="stylesheet" href="{a}/explorer.css">'
            f"<script>window.__viz={h.script_json(payload)};</script>"
            f'<script src="{a}/draw.js"></script>'
            f'<script src="{a}/explorer.js"></script>'
        )
    else:
        head = (
            f"<style>{EXPLORER_CSS}</style>"
            f"<script>window.__viz={h.script_json(payload)};</script>"
            f"<script>{DRAW_JS}</script>"
            f"<script>{EXPLORER_JS}</script>"
        )
    inline_world = (
        f"<script>window.__vizWorld={h.script_json(dict(world))};</script>"
        if world and not data_dir
        else ""
    )

    stops = [(colour, str(count)) for count, colour in legend_stops(worst)]
    ext_count, int_count = len(cells), _interior_rows(conflicts).count("<tr")

    # When the page was built from coverage (no record scan yet), say so plainly
    # at the top. Coverage overlap and record conflict are different questions,
    # and a page that quietly presented one as the other would undo the very
    # distinction these two maps exist to keep.
    banner = (
        f'<div class="banner">{h.escape(_("Showing cells that more than one mod touches (coverage). That is not the same as their records conflicting -- it is the superset. Run Check Conflicts for record-level detail: which records actually collide, plus the terrain, difference and navigation views."))}</div>'
        if coverage_only
        else ""
    )

    body = f"""
<div id="tt"></div>
{banner}
{h.summary({
    _("Cells with conflicts"): len(cells),
    _("Spatial conflicts"): spatial,
    _("Involving your mods"): sum(c.mine for c in cells.values()),
    _("Cells with local detail"): len(detailed),
})}
<div class="bar">
  <label for="focus">{h.escape(_("Focus on mod:"))}</label>
  <select id="focus" onchange="vizFocus(this.value)">
    <option value="">{h.escape(_("-- all mods --"))}</option>{options}
  </select>
  <button onclick="vizFocus('')">{h.escape(_("Clear"))}</button>
  <a href="{h.escape(cell_map_href)}">{h.escape(_("Cell map (coverage)"))}</a>
</div>
<div id="focusinfo" class="sub"></div>
<div class="tabs">
  <button id="b0" class="on" onclick="vizShow(0)">{h.escape(_("Conflict map"))}</button>
  <button id="b1" onclick="vizShow(1)">{h.escape(_("Exterior list"))} ({ext_count})</button>
  <button id="b2" onclick="vizShow(2)">{h.escape(_("Interior list"))} ({int_count})</button>
  <button id="b3" onclick="vizShow(3)" disabled>{h.escape(_("Cell detail"))}</button>
</div>
<div id="t0" class="tab on">
  {_WORLD_TERRAIN_TOGGLE}
  {_world_svg(cells, worst, detailed)}
  {h.legend(
      [*stops, (MINE, _("involves your mods")), ("#7cc5ff", _("has a local view"))],
      _("North is up. Scroll to pan. Click a cell for its local views."),
  )}
</div>
<div id="t1" class="tab">
  <div class="bar"><input placeholder="{h.escape(_("Filter cells / mods..."))}"
    onkeyup="vizFilter('xt', this.value)"></div>
  <div class="mapwrap"><table id="xt"><thead><tr>
    <th>{h.escape(_("Cell"))}</th><th>{h.escape(_("Conflicts"))}</th>
    <th>{h.escape(_("Yours"))}</th><th>{h.escape(_("Record types"))}</th>
    <th>{h.escape(_("Winner"))}</th><th>{h.escape(_("Plugins"))}</th><th></th>
  </tr></thead><tbody>{_exterior_rows(cells, detailed)}</tbody></table></div>
</div>
<div id="t2" class="tab">
  <div class="bar"><input placeholder="{h.escape(_("Filter interiors / mods..."))}"
    onkeyup="vizFilter('it', this.value)"></div>
  <div class="mapwrap"><table id="it"><thead><tr>
    <th>{h.escape(_("Cell"))}</th><th>{h.escape(_("Conflicts"))}</th>
    <th>{h.escape(_("Yours"))}</th><th>{h.escape(_("Record types"))}</th>
    <th>{h.escape(_("Winner"))}</th><th>{h.escape(_("Plugins"))}</th>
  </tr></thead><tbody>{_interior_rows(conflicts)}</tbody></table></div>
</div>
<div id="t3" class="tab">
  <div id="detailhead" class="sub"></div>
  <div class="bar">
    <button id="sub_surface" class="on" onclick="vizSub('surface')">{h.escape(_("Terrain surface"))}</button>
    <button id="sub_diff" onclick="vizSub('diff')">{h.escape(_("Terrain difference"))}</button>
    <button id="sub_nav" onclick="vizSub('nav')">{h.escape(_("Nav grid"))}</button>
    <button id="difftog" class="on" onclick="vizDiffToggle()">{h.escape(_("Highlight differences"))}</button>
    <a id="fullpage" href="#" style="display:none">{h.escape(_("Open full-resolution page"))}</a>
  </div>
  <div class="bar" id="plugpick"></div>
  <div id="detailwrap"><canvas id="cellcanvas" width="1000" height="680"></canvas></div>
  <div id="detailnote" class="sub"></div>
</div>
{head}
{inline_world}{sidecar}
"""
    return h.page(
        title or _("Conflict explorer"),
        (
            _(
                "Cells more than one mod touches, from coverage. Run Check Conflicts "
                "for record-level detail and the terrain, difference and nav views."
            )
            if coverage_only
            else _(
                "Where your mods collide, and what the collision looks like. "
                "The cell map answers a different question: which mods touch a cell at all."
            )
        ),
        body,
    )
