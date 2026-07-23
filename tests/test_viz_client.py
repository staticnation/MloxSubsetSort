"""Execute the explorer's client-side code, not just assert it is present.

The rest of the suite checks that the generated HTML *contains* the right
things. That cannot catch a JavaScript error: a page whose script throws on
line one still contains every string the other tests look for, and the only
symptom is that nothing in the page responds. Since the GUI these views open
from has no automated coverage at all (``REMAINING_WORK.md`` §4), an untested
script would be the least-verified code in the project.

So this module runs the script under Node against a small DOM stand-in and
drives the real interactions -- switching modes, focusing a mod, selecting a
cell, drawing each local view. Node is optional; without it the module skips
rather than failing, and says why.

The stand-in is deliberately minimal. It is not trying to be a browser: it
implements exactly the DOM surface the script uses, so a script reaching for
something new fails loudly here instead of silently in the app.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from mlox_subset.viz.draw_js import DRAW_JS
from mlox_subset.viz.explorer_js import EXPLORER_JS

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")

#: A DOM stand-in covering exactly what the client touches. Elements record the
#: calls made against them so the test can assert on behaviour rather than on
#: the script merely not crashing.
_HARNESS = r"""
function El(id, tag){
  this.id = id; this.tagName = tag || "DIV";
  this.dataset = {}; this.style = {}; this.className = ""; this.textContent = "";
  this.children = []; this.disabled = false; this.value = "";
  this.innerText = "";
  var self = this;
  this.classList = {
    _s: {},
    add: function(c){ self.classList._s[c] = 1; },
    remove: function(c){ delete self.classList._s[c]; },
    contains: function(c){ return !!self.classList._s[c]; },
    toggle: function(c, on){ if (on) { self.classList._s[c] = 1; } else { delete self.classList._s[c]; } }
  };
  this.appendChild = function(c){ self.children.push(c); };
  // The client clears a container with `innerHTML = ""`, so mirror that in
  // the stand-in: otherwise rebuilding the picker would append duplicates and
  // the test would pass on a count the browser never produces.
  Object.defineProperty(this, "innerHTML", {
    get: function(){ return self._html || ""; },
    set: function(v){ self._html = v; if (v === "") { self.children = []; } }
  });
  this.addEventListener = function(){};
  this.scrollIntoView = function(){ self.scrolled = true; };
  this.getContext = function(){ return CTX; };
  this.width = 1000; this.height = 680;
}
var CALLS = {fill: 0, stroke: 0, arc: 0, rect: 0};
var CTX = {
  clearRect: function(){}, beginPath: function(){}, moveTo: function(){},
  lineTo: function(){}, closePath: function(){}, stroke: function(){ CALLS.stroke++; },
  fill: function(){ CALLS.fill++; }, fillRect: function(){ CALLS.rect++; },
  arc: function(){ CALLS.arc++; }, fillStyle: "", strokeStyle: "", lineWidth: 1
};
var ELS = {};
var RECTS = [];
function mkEl(id){ if (!ELS[id]) { ELS[id] = new El(id); } return ELS[id]; }
["tt","focus","focusinfo","detailhead","detailnote","plugpick","cellcanvas",
 "b0","b1","b2","b3","t0","t1","t2","t3","sub_surface","sub_diff","sub_nav",
 "difftog","xt","it","worldcanvas","worldnote","worldsvg","wm_density","wm_terrain"].forEach(mkEl);

global.document = {
  getElementById: function(id){ return ELS[id] || null; },
  createElement: function(tag){ return new El(null, tag.toUpperCase()); },
  addEventListener: function(){},
  querySelectorAll: function(sel){
    if (sel.indexOf("rect.cell") === 0) return RECTS;
    return [];
  }
};
global.window = {addEventListener: function(){}, innerWidth: 1600, innerHeight: 900};
// Capture lazily-injected sidecar <script> tags instead of loading them, so
// the test can deliver the payload itself and assert on the upgrade.
var INJECTED = null;
global.document.head = {appendChild: function(s){ INJECTED = s; }};
global.setTimeout = function(){};
// Two cells: one owned by the user's mod, one not.
function mkRect(mods, n){
  var r = new El(null, "rect");
  r.dataset.m = "|" + mods.join("|") + "|";
  r.dataset.n = String(n);
  r.querySelectorAll = function(){ return []; };
  return r;
}
RECTS.push(mkRect(["mine.esp", "a.esm"], 5));
RECTS.push(mkRect(["a.esm", "b.esm"], 2));
ELS.xt.querySelectorAll = function(){ return []; };
ELS.it.querySelectorAll = function(){ return []; };
"""


def _run(payload: dict, script: str) -> dict:
    """Run the client under Node with a payload, then a probe script.

    Args:
        payload: The ``window.__viz`` blob.
        script: JavaScript appended after the client loads, which must
            ``console.log`` a single JSON object as its last output line.

    Returns:
        The parsed probe result.

    Raises:
        AssertionError: If Node exits non-zero, which means the client threw.
    """
    source = "\n".join(
        [
            _HARNESS,
            f"global.window.__viz = {json.dumps(payload)};",
            "window.__viz = global.window.__viz;",
            DRAW_JS,
            EXPLORER_JS,
            script,
        ]
    )
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "client.js"
        path.write_text(source, encoding="utf-8")
        result = subprocess.run(  # noqa: S603 - argv is ours, no shell
            ["node", str(path)],  # noqa: S607 - resolved via PATH by design
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    assert result.returncode == 0, f"the client threw:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def _payload(**labels: str) -> dict:
    """Build a minimal payload with one detailed cell.

    Args:
        **labels: Label overrides.

    Returns:
        A ``window.__viz`` blob.
    """
    flat_low = [0.0] * (65 * 65)
    flat_high = list(flat_low)
    for i in range(65 * 10 + 20, 65 * 10 + 65):
        flat_high[i] = 40.0
    base = {
        "detail": {
            "4,7": {
                "land": {
                    "a.esm": {"heights": flat_low, "min": 0, "max": 0},
                    "mine.esp": {"heights": flat_high, "min": 0, "max": 40},
                },
                "pgrd": {
                    "a.esm": {
                        "points": [[0, 0, 0], [100, 0, 0], [100, 100, 0]],
                        "edges": [[0, 1], [1, 2], [0, 2]],
                    },
                    "mine.esp": {
                        "points": [[0, 0, 0], [100, 0, 0], [100, 100, 0]],
                        "edges": [[0, 1]],
                    },
                },
                "plugins": ["a.esm", "mine.esp"],
            }
        },
        "labels": {
            "focusinfo": "cells={cells} conflicts={conflicts} mods={mods} top={top}",
            "detailfor": "Cell {cell}",
            "nodetail": "no detail {cell}",
            "surfaceof": "{plugin} {lo} {hi}",
            "diffof": "{winner}|{loser}|{changed}|{total}|{peak}",
            "navof": "{plugin}|{points}|{edges}|{added}|{removed}",
            "noland": "noland",
            "nonav": "nonav",
            "needtwo": "needtwo",
            "mismatch": "MISMATCH",
            "noworld": "noworld",
            "worldof": "{cells}|{quads}|{lo}|{hi}",
        },
    }
    base["labels"].update(labels)
    return base


class TestClientLoads:
    def test_it_runs_without_throwing_and_exposes_its_api(self):
        """A script that throws on load leaves a page that looks fine and does nothing."""
        out = _run(
            _payload(),
            "console.log(JSON.stringify({"
            "show: typeof window.vizShow, focus: typeof window.vizFocus,"
            "select: typeof window.vizSelect, sub: typeof window.vizSub,"
            "filter: typeof window.vizFilter}));",
        )
        assert out == {
            "show": "function",
            "focus": "function",
            "select": "function",
            "sub": "function",
            "filter": "function",
        }

    def test_it_starts_on_the_map_with_detail_locked(self):
        """The cell-detail tab means nothing until a cell is chosen."""
        out = _run(
            _payload(),
            "console.log(JSON.stringify({t0: ELS.t0.className, b3: ELS.b3.disabled}));",
        )
        assert out["t0"] == "tab on"


class TestFocusFilter:
    def test_focusing_a_mod_dims_cells_it_does_not_touch(self):
        out = _run(
            _payload(),
            "window.vizFocus('mine.esp');"
            "console.log(JSON.stringify({"
            "kept: RECTS[0].classList.contains('dim'),"
            "dimmed: RECTS[1].classList.contains('dim'),"
            "info: ELS.focusinfo.textContent}));",
        )
        assert out["kept"] is False
        assert out["dimmed"] is True

    def test_the_focus_summary_counts_cells_and_conflicts(self):
        out = _run(
            _payload(),
            "window.vizFocus('mine.esp');console.log(JSON.stringify({i: ELS.focusinfo.textContent}));",
        )
        assert "cells=1" in out["i"]
        assert "conflicts=5" in out["i"]

    def test_clearing_focus_undims_everything(self):
        out = _run(
            _payload(),
            "window.vizFocus('mine.esp');window.vizFocus('');"
            "console.log(JSON.stringify({d0: RECTS[0].classList.contains('dim'),"
            "d1: RECTS[1].classList.contains('dim'), info: ELS.focusinfo.textContent}));",
        )
        assert out["d0"] is False and out["d1"] is False
        assert out["info"] == ""


class TestCellSelection:
    def test_selecting_a_detailed_cell_opens_the_local_view(self):
        out = _run(
            _payload(),
            "window.vizSelect('4,7',1);"
            "console.log(JSON.stringify({t3: ELS.t3.className, head: ELS.detailhead.textContent,"
            "picker: ELS.plugpick.children.length}));",
        )
        assert out["t3"] == "tab on"
        assert out["head"] == "Cell 4,7"
        assert out["picker"] == 2

    def test_selecting_an_undetailed_cell_falls_back_to_the_list(self):
        """A bounded detail budget must degrade to something useful."""
        out = _run(
            _payload(),
            "window.vizSelect('99,99',1);"
            "console.log(JSON.stringify({t1: ELS.t1.className, head: ELS.detailhead.textContent}));",
        )
        assert out["t1"] == "tab on"
        assert out["head"] == "no detail 99,99"


class TestLocalViews:
    def test_the_difference_view_reports_the_real_vertex_count(self):
        """45 vertices differ: columns 20..64 of row 10, as VHGT's cumulative
        encoding produces from a single changed delta."""
        out = _run(
            _payload(),
            "window.vizSelect('4,7',0);window.vizSub('diff');"
            "console.log(JSON.stringify({note: ELS.detailnote.textContent, rects: CALLS.rect}));",
        )
        winner, loser, changed, total, peak = out["note"].split("|")
        assert winner == "mine.esp" and loser == "a.esm"
        assert changed == "45" and total == "4225" and peak == "40"
        assert out["rects"] == 45

    def test_the_nav_view_counts_added_and_removed_edges(self):
        out = _run(
            _payload(),
            "window.vizSelect('4,7',0);window.vizSub('nav');"
            "console.log(JSON.stringify({note: ELS.detailnote.textContent}));",
        )
        plugin, points, edges, added, removed = out["note"].split("|")
        assert plugin == "mine.esp"
        assert points == "3" and edges == "1"
        assert added == "0" and removed == "2"

    def test_the_surface_view_draws_and_reports_its_range(self):
        out = _run(
            _payload(),
            "window.vizSelect('4,7',0);window.vizSub('surface');"
            "console.log(JSON.stringify({note: ELS.detailnote.textContent, fills: CALLS.fill}));",
        )
        assert out["note"].startswith("a.esm")
        assert out["fills"] > 100

    def test_switching_plugin_switches_the_surface(self):
        out = _run(
            _payload(),
            "window.vizSelect('4,7',0);window.vizSub('surface');"
            "ELS.plugpick.children[1].onclick();"
            "console.log(JSON.stringify({note: ELS.detailnote.textContent}));",
        )
        assert out["note"].startswith("mine.esp")

    def test_turning_off_diff_highlighting_draws_every_vertex(self):
        """With highlighting on, unchanged vertices are skipped."""
        out = _run(
            _payload(),
            "window.vizSelect('4,7',0);window.vizSub('diff');"
            "var on = CALLS.rect; CALLS.rect = 0;"
            "window.vizDiffToggle();"
            "console.log(JSON.stringify({on: on, off: CALLS.rect}));",
        )
        assert out["on"] == 45
        assert out["off"] == 4225

    def test_a_cell_with_one_plugin_says_there_is_nothing_to_subtract(self):
        payload = _payload()
        del payload["detail"]["4,7"]["land"]["a.esm"]
        out = _run(
            payload,
            "window.vizSelect('4,7',0);window.vizSub('diff');"
            "console.log(JSON.stringify({note: ELS.detailnote.textContent}));",
        )
        assert out["note"] == "needtwo"

    def test_a_cell_with_no_path_grid_says_so(self):
        payload = _payload()
        payload["detail"]["4,7"]["pgrd"] = {}
        out = _run(
            payload,
            "window.vizSelect('4,7',0);window.vizSub('nav');"
            "console.log(JSON.stringify({note: ELS.detailnote.textContent}));",
        )
        assert out["note"] == "nonav"


class TestSidecarLoading:
    """Full-resolution data arrives lazily, by script tag.

    ``fetch()`` is blocked against ``file://`` in every current browser, and
    these pages are opened from disk. A ``<script src>`` tag is not blocked,
    which is why the sidecars are JavaScript rather than JSON -- it is the only
    mechanism that gives real lazy loading with no server.
    """

    @staticmethod
    def _sampled(side: int, spike: int) -> dict:
        heights = [0.0] * (side * side)
        heights[spike] = 99.0
        return {"heights": heights, "min": 0, "max": 99, "side": side}

    def _payload_with_sidecar(self) -> dict:
        payload = _payload()
        payload["detail"] = {
            "4,7": {
                "land": {"a": self._sampled(9, 40), "b": self._sampled(9, 41)},
                "pgrd": {},
                "plugins": ["a", "b"],
            }
        }
        payload["dataDir"] = "d"
        return payload

    def test_opening_a_cell_requests_its_sidecar(self):
        out = _run(
            self._payload_with_sidecar(),
            'window.vizSelect("4,7",0);'
            "console.log(JSON.stringify({src: INJECTED ? INJECTED.src : null}));",
        )
        assert out["src"] == "d/cells/4_7.js"

    def test_the_sampled_grid_draws_before_the_sidecar_arrives(self):
        """The view must never be blank while data is in flight."""
        out = _run(
            self._payload_with_sidecar(),
            'window.vizSelect("4,7",0);window.vizSub("diff");'
            "console.log(JSON.stringify({note: ELS.detailnote.textContent}));",
        )
        assert out["note"].split("|")[3] == "81"

    def test_the_sidecar_upgrades_the_view_to_full_resolution(self):
        full_a = [0.0] * 4225
        full_b = list(full_a)
        full_b[2000] = 99.0
        payload = json.dumps(
            {
                "land": {
                    "a": {"heights": full_a, "min": 0, "max": 99, "side": 65},
                    "b": {"heights": full_b, "min": 0, "max": 99, "side": 65},
                },
                "pgrd": {},
                "plugins": ["a", "b"],
            }
        )
        out = _run(
            self._payload_with_sidecar(),
            'window.vizSelect("4,7",0);window.vizSub("diff");'
            "var sampled = ELS.detailnote.textContent;"
            f'window.__vizCellLoaded("4,7",{payload});'
            "console.log(JSON.stringify({sampled: sampled, full: ELS.detailnote.textContent}));",
        )
        assert out["sampled"].split("|")[3] == "81"
        assert out["full"].split("|")[3] == "4225"

    def test_a_missing_sidecar_leaves_the_sampled_view_working(self):
        """A cell beyond the detail budget must degrade, not break."""
        out = _run(
            self._payload_with_sidecar(),
            'window.vizSelect("4,7",0);window.vizSub("diff");'
            "INJECTED.onerror();"
            "console.log(JSON.stringify({note: ELS.detailnote.textContent}));",
        )
        assert out["note"].split("|")[3] == "81"

    def test_grids_of_different_sizes_are_refused_rather_than_compared(self):
        """Comparing a sampled grid against a full one would be nonsense."""
        payload = self._payload_with_sidecar()
        payload["detail"]["4,7"]["land"]["b"] = self._sampled(17, 40)
        out = _run(
            payload,
            'window.vizSelect("4,7",0);window.vizSub("diff");'
            "console.log(JSON.stringify({note: ELS.detailnote.textContent}));",
        )
        assert out["note"] == _payload()["labels"]["mismatch"]


class TestWorldTerrain:
    """The world view is aggressive LOD: a few faces per cell, edges honest.

    A cell is ~11 pixels wide at world zoom, so it cannot show more than a
    handful of faces -- drawing 65x65 was ~100 quads per cell and tens of
    thousands across a landmass, which crawled. At ``side`` 3 each cell is a
    2x2 patch (4 faces). Cells abut rather than share vertices, so a height
    mismatch at a shared border shows as a step -- the seam is the point.
    """

    SIDE = 3

    @staticmethod
    def _world(cells: dict[str, list[float]], side: int = 3) -> dict:
        return {"side": side, "cells": cells, "relief": 60}

    def _patch(self, value: float = 0.0) -> list[float]:
        return [value] * (self.SIDE * self.SIDE)

    def _run_world(self, world: dict, script: str) -> dict:
        return _run(
            _payload(),
            f"global.window.__vizWorld = {json.dumps(world)};"
            "window.__vizWorld = global.window.__vizWorld;" + script,
        )

    def test_each_cell_is_a_handful_of_faces_not_a_full_mesh(self):
        """LOD: (side-1)^2 faces per cell, so a landmass stays cheap to draw."""
        world = self._world({"0,0": self._patch(), "1,0": self._patch(50.0)})
        out = self._run_world(
            world,
            'window.vizWorldMode("terrain");' "console.log(JSON.stringify({fills: CALLS.fill}));",
        )
        # 2 cells x (3-1)^2 = 8 faces total -- not hundreds.
        assert out["fills"] == 2 * (self.SIDE - 1) ** 2

    def test_only_cells_with_data_are_drawn(self):
        """The map is sparse; a cell with no terrain is a hole, not invented land."""
        two = self._world({"0,0": self._patch(), "1,0": self._patch()})
        one = self._world({"0,0": self._patch()})
        a = self._run_world(
            two, 'window.vizWorldMode("terrain");console.log(JSON.stringify({f: CALLS.fill}));'
        )
        b = self._run_world(
            one, 'window.vizWorldMode("terrain");console.log(JSON.stringify({f: CALLS.fill}));'
        )
        assert a["f"] == 2 * b["f"]

    def test_a_seam_between_mismatched_cells_is_drawn_as_a_step(self):
        """Two neighbours disagreeing on their shared edge is the whole point.

        Because cells abut rather than share vertices, a border-height mismatch
        leaves the two patches at different heights where they meet -- a visible
        cliff. Both cells still draw their own faces; nothing is averaged away.
        """
        # Right cell sits 500 units higher at its whole edge.
        world = self._world({"0,0": self._patch(0.0), "1,0": self._patch(500.0)})
        out = self._run_world(
            world,
            'window.vizWorldMode("terrain");'
            "console.log(JSON.stringify({fills: CALLS.fill, note: ELS.worldnote.textContent}));",
        )
        assert out["fills"] == 2 * (self.SIDE - 1) ** 2
        # The height range spans both cells, so the step is real in the data.
        assert out["note"].split("|")[2] == "0"
        assert out["note"].split("|")[3] == "500"

    def test_it_says_so_when_there_is_no_terrain(self):
        out = self._run_world(
            self._world({}),
            'window.vizWorldMode("terrain");'
            "console.log(JSON.stringify({note: ELS.worldnote.textContent}));",
        )
        assert out["note"] == "noworld"

    def test_switching_back_to_density_restores_the_svg_map(self):
        out = self._run_world(
            self._world({"0,0": self._patch()}),
            'window.vizWorldMode("terrain");window.vizWorldMode("density");'
            "console.log(JSON.stringify({svg: ELS.worldsvg.style.display,"
            "cv: ELS.worldcanvas.style.display}));",
        )
        assert out["svg"] == "block"
        assert out["cv"] == "none"
