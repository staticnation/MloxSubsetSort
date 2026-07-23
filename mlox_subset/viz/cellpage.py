"""A dedicated, full-resolution page for one cell.

The explorer's cell-detail tab is a preview: it shares the page with the map
and lists, and it decimates a large surface so dragging stays smooth. This is
the opposite -- one cell, its own page, **full resolution**, because that is
where per-vertex questions are actually answered.

It shows the same three views (terrain surface, height difference, navigation
graph) through the shared :data:`~mlox_subset.viz.draw_js.DRAW_JS`, so it can
never disagree with the preview about how a cell looks. The surface view also
draws the centre cell's neighbours as border strips, so a seam -- a border
height that does not match the next cell -- is visible as a step.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mlox_subset import _
from mlox_subset.viz import html as h
from mlox_subset.viz.detail import cell_page_detail
from mlox_subset.viz.draw_js import DRAW_JS

#: Styles specific to the cell page. The shared shell supplies the rest.
CELL_CSS = """
.tabs{margin:12px 0 8px}
.tabs button{background:#20242a;color:#ddd;border:1px solid #3a3a3a;
padding:6px 14px;margin-right:4px;cursor:pointer;font:inherit;border-radius:3px}
.tabs button.on{background:#8a3a12;color:#fff;border-color:#a4491a}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0}
.bar button{background:#20242a;color:#ddd;border:1px solid #3a3a3a;
padding:5px 10px;cursor:pointer;font:inherit;border-radius:3px}
.bar button.on{background:#8a3a12;color:#fff;border-color:#a4491a}
canvas{background:#12151a;border-radius:4px;max-width:100%;cursor:grab}
"""

#: The cell page's client. Draws one cell full resolution through VizDraw.
CELL_JS = r"""
(function(){
"use strict";
var D = window.__vizCell, T = D.labels;
var SUB = "surface", OVERLAY = 0, DIFF = true, yaw = 0.7, pitch = 0.55, drag = null;

function noteFor(sub, r){
  if (!r) return "";
  if (r.empty) return (sub === "nav") ? T.nonav : T.noland;
  if (r.needtwo) return T.needtwo;
  if (r.mismatch) return T.mismatch;
  if (sub === "nav") {
    return T.navof.replace("{plugin}", r.plugin).replace("{points}", r.points)
      .replace("{edges}", r.edges).replace("{added}", r.added).replace("{removed}", r.removed);
  }
  if (sub === "diff") {
    return T.diffof.replace("{winner}", r.winner).replace("{loser}", r.loser)
      .replace("{changed}", r.changed).replace("{total}", r.total).replace("{peak}", r.peak);
  }
  var s = T.surfaceof.replace("{plugin}", r.plugin).replace("{lo}", r.lo).replace("{hi}", r.hi);
  if (r.seams) s += " " + T.seams.replace("{n}", r.seams);
  return s;
}

function draw(){
  var cv = document.getElementById("cc");
  var cx = cv.getContext("2d");
  cx.clearRect(0, 0, cv.width, cv.height);
  var note = document.getElementById("note"), r;
  if (SUB === "nav") { r = window.VizDraw.nav(cv, cx, D.detail, {diff: DIFF}); }
  else if (SUB === "diff") { r = window.VizDraw.diff(cv, cx, D.detail, {diff: DIFF}); }
  else {
    r = window.VizDraw.surface(cv, cx, D.detail, {overlay: OVERLAY, yaw: yaw, pitch: pitch,
                                                  stride: 1, seams: D.detail.seams || []});
  }
  if (note) note.textContent = noteFor(SUB, r);
}

function setSub(s){
  SUB = s;
  ["surface", "diff", "nav"].forEach(function(k){
    var b = document.getElementById("s_" + k); if (b) b.className = (k === s) ? "on" : "";
  });
  draw();
}
window.cpSub = setSub;
window.cpDiff = function(){
  DIFF = !DIFF;
  var b = document.getElementById("dtog"); if (b) b.className = DIFF ? "on" : "";
  draw();
};

(function(){
  var wrap = document.getElementById("pick");
  (D.detail.plugins || []).forEach(function(name, i){
    var b = document.createElement("button");
    b.textContent = name; b.className = (i === 0) ? "on" : "";
    b.onclick = function(){
      OVERLAY = i;
      wrap.querySelectorAll("button").forEach(function(x, j){ x.className = (j === i) ? "on" : ""; });
      draw();
    };
    wrap.appendChild(b);
  });
})();

(function(){
  var cv = document.getElementById("cc");
  cv.addEventListener("mousedown", function(e){ drag = [e.clientX, e.clientY]; });
  window.addEventListener("mouseup", function(){ drag = null; });
  window.addEventListener("mousemove", function(e){
    if (!drag || SUB !== "surface") return;
    yaw += (e.clientX - drag[0]) * 0.01;
    pitch = Math.max(0.08, Math.min(1.5, pitch + (e.clientY - drag[1]) * 0.01));
    drag = [e.clientX, e.clientY]; draw();
  });
})();

draw();
})();
"""


def _labels() -> dict[str, str]:
    """The strings the cell-page client renders.

    Returns:
        Label key to translated text.
    """
    return {
        "surfaceof": _("{plugin} -- heights {lo} to {hi} units. Drag to rotate."),
        "seams": _("{n} neighbour(s) shown as border strips; a step means a seam."),
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
        "mismatch": _("The two records decode to different grid sizes and cannot be compared."),
    }


def build_cell_page(
    cell_label: str,
    detail: dict[str, Any],
    *,
    explorer_href: str = "",
    cell_map_href: str = "",
    assets_href: str = "",
) -> str:
    """Render a single cell's full-resolution page.

    Args:
        cell_label: The cell, e.g. ``"(43, -45)"``.
        detail: One cell's full-resolution detail -- ``land``/``pgrd``/
            ``plugins`` plus an optional ``seams`` list from
            :func:`~mlox_subset.viz.detail.cell_page_detail`.
        explorer_href: Link back to the explorer, if any.
        cell_map_href: Link to the cell map, if any.
        assets_href: Relative path to the shared ``assets/`` folder. When set,
            the shared CSS/JS are referenced as files (debuggable); when empty,
            they are inlined so the page stands alone (tests, one-offs).

    Returns:
        A complete HTML document. Self-contained when ``assets_href`` is empty;
        otherwise it needs the shared assets folder beside it.
    """
    payload = {"detail": detail, "labels": _labels()}
    links = []
    if explorer_href:
        links.append(f'<a href="{h.escape(explorer_href)}">{h.escape(_("Conflict explorer"))}</a>')
    if cell_map_href:
        links.append(f'<a href="{h.escape(cell_map_href)}">{h.escape(_("Cell map"))}</a>')
    link_bar = f'<div class="bar">{" &nbsp; ".join(links)}</div>' if links else ""

    if assets_href:
        a = h.escape(assets_href)
        head = (
            f'<link rel="stylesheet" href="{a}/cellpage.css">'
            f"<script>window.__vizCell={h.script_json(payload)};</script>"
            f'<script src="{a}/draw.js"></script>'
            f'<script src="{a}/cellpage.js"></script>'
        )
    else:
        head = (
            f"<style>{CELL_CSS}</style>"
            f"<script>window.__vizCell={h.script_json(payload)};</script>"
            f"<script>{DRAW_JS}</script>"
            f"<script>{CELL_JS}</script>"
        )

    body = f"""
{link_bar}
<div class="tabs">
  <button id="s_surface" class="on" onclick="cpSub('surface')">{h.escape(_("Terrain surface"))}</button>
  <button id="s_diff" onclick="cpSub('diff')">{h.escape(_("Terrain difference"))}</button>
  <button id="s_nav" onclick="cpSub('nav')">{h.escape(_("Nav grid"))}</button>
  <button id="dtog" class="on" onclick="cpDiff()">{h.escape(_("Highlight differences"))}</button>
</div>
<div class="bar" id="pick"></div>
<canvas id="cc" width="1400" height="900"></canvas>
<div id="note" class="sub"></div>
{head}
"""
    return h.page(
        _("Cell %(cell)s") % {"cell": cell_label},
        _("One cell at full resolution: terrain surface, height difference and navigation."),
        body,
    )


def build_cell_pages(
    full_detail: Mapping[str, dict[str, Any]],
    *,
    explorer_href: str = "",
    cell_map_href: str = "",
    assets_href: str = "../assets",
) -> dict[str, str]:
    """Build a full-resolution page for every detailed cell.

    Attaches neighbour seams once (via
    :func:`~mlox_subset.viz.detail.cell_page_detail`) and renders one page per
    cell, so the caller just writes the result out.

    Args:
        full_detail: Full-resolution detail from ``collect_detail(stride=1)``.
        explorer_href: Link back to the explorer, if any.
        cell_map_href: Link to the cell map, if any.
        assets_href: Path to the shared assets from a cell page. Defaults to
            ``"../assets"`` because pages are written under ``pages/`` and the
            assets sit beside it under ``assets/``. Pass ``""`` to inline.

    Returns:
        ``{"x,y": html}`` for each cell.
    """
    with_seams = cell_page_detail(full_detail)
    return {
        key: build_cell_page(
            f"({key.replace(',', ', ')})",
            entry,
            explorer_href=explorer_href,
            cell_map_href=cell_map_href,
            assets_href=assets_href,
        )
        for key, entry in with_seams.items()
    }
