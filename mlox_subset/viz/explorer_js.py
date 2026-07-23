"""The explorer page's client-side code, kept out of the Python that builds it.

Separated from :mod:`mlox_subset.viz.explorer` deliberately. The lesson from
``generate_cell_map_html`` -- one 185-line f-string that ``REMAINING_WORK.md``
§5 calls effectively uneditable -- is that mixing a template with the code that
fills it makes both hard to change. Here the script is a plain module constant
with no interpolation at all: everything it needs arrives in one JSON blob, so
there are no ``{{``/``}}`` escapes and the JavaScript can be read as
JavaScript.

Every user-visible string it renders is passed in through that blob, already
translated, so the page stays i18n-correct without gettext in the browser.
"""

from __future__ import annotations

from typing import Final

#: Styles for the explorer. Extends the shared shell rather than replacing it.
EXPLORER_CSS: Final[str] = """
.tabs{margin:14px 0 6px}
.tabs button{background:#20242a;color:#ddd;border:1px solid #3a3a3a;
padding:6px 14px;margin-right:4px;cursor:pointer;font:inherit;border-radius:3px}
.tabs button.on{background:#8a3a12;color:#fff;border-color:#a4491a}
.tabs button:disabled{opacity:.4;cursor:default}
.tab{display:none}.tab.on{display:block}
.mapwrap{overflow:auto;max-height:72vh;border:1px solid #333945;background:#06111c;
display:block;max-width:100%;resize:vertical}
.mapwrap svg{display:block}
#tt{position:fixed;pointer-events:none;display:none;z-index:99;max-width:460px;
background:#000;color:#eee;border:1px solid #555;border-radius:3px;
padding:3px 7px;font-size:12px;white-space:pre-line}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0}
.bar select,.bar input{background:#1c1c22;color:#ddd;border:1px solid #3a3a3a;
padding:5px;font:inherit;border-radius:3px}
.bar input{width:300px}
.bar select{max-width:420px}
.bar button{background:#20242a;color:#ddd;border:1px solid #3a3a3a;
padding:5px 10px;cursor:pointer;font:inherit;border-radius:3px}
.bar button.on{background:#8a3a12;color:#fff;border-color:#a4491a}
rect.cell{cursor:pointer}
rect.cell:hover{stroke:#fff;stroke-width:1.4}
rect.cell.dim{opacity:.13}
rect.cell.hasdetail{stroke:#7cc5ff;stroke-width:1.1}
tr.row{cursor:pointer}
tr.row:hover td{background:#2a3038}
tr.hl td{background:#3a2a10}
tr.dimrow{display:none}
canvas{background:#12151a;border-radius:4px;max-width:100%}
#detailwrap{overflow:auto;max-height:72vh}
.pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11.5px;
background:#2c313a;color:#b9c0cc;margin-right:5px}
.pill.mine{background:#4a3423;color:#ff9d5c}
.banner{background:#2a2620;border:1px solid #5a4a2a;border-radius:6px;
padding:9px 12px;margin-bottom:12px;color:#e0c88a;font-size:12.5px}
"""

#: The whole client. Reads ``window.__viz`` and drives every mode.
EXPLORER_JS: Final[str] = r"""
(function(){
"use strict";
var D = window.__viz, T = D.labels;
// Overview data arrives in a sidecar script, not in the document: embedding it
// produced a page too large to open (see viz/sidecar.py). Absent, the map and
// lists still work -- only the local views go missing.
D.detail = (window.__vizOverview && window.__vizOverview.detail) || D.detail || {};
// Full-resolution cells load on click, by injecting a <script> tag. `fetch()`
// is blocked against file:// but a script tag is not, which is the whole
// reason this is a .js file rather than .json.
var FULL = {}, PENDING = {};
window.__vizCellLoaded = function(key, payload){
  FULL[key] = payload;
  var waiting = PENDING[key] || [];
  delete PENDING[key];
  waiting.forEach(function(fn){ fn(); });
};
var FOCUS = "", MODE = 0, SEL = null, SUB = "surface", OVERLAY = 0, DIFF = true;
var WORLDMODE = "density";
// The world terrain arrives knitted: one file holding an 11x11 patch per cell,
// not a file per cell. Thousands of requests to draw one picture is the wrong
// shape, and at this sampling the whole landmass costs less than a single cell
// does at full resolution.
//
// Read at draw time rather than captured at load: the sidecar is a separate
// <script> and there is no ordering guarantee worth relying on. Capturing it
// once meant a sidecar that arrived a moment late was ignored for good, and
// the view drew nothing with no error to explain it.
function worldData(){ return window.__vizWorld || null; }

// ---- tooltip -------------------------------------------------------------
// One delegated listener rather than per-element handlers: with thousands of
// rects, attaching individually is what makes a tooltip feel laggy. This is
// the same approach the cell map uses, and it shows with no delay at all.
var tt = document.getElementById("tt");
document.addEventListener("mouseover", function(e){
  var t = e.target;
  if (t && t.dataset && t.dataset.t) { tt.textContent = t.dataset.t; tt.style.display = "block"; }
});
document.addEventListener("mousemove", function(e){
  if (tt.style.display === "block") {
    var x = e.clientX + 14, y = e.clientY + 14;
    if (x + 470 > window.innerWidth) { x = e.clientX - 470; }
    if (y + 120 > window.innerHeight) { y = e.clientY - 90; }
    tt.style.left = x + "px"; tt.style.top = y + "px";
  }
});
document.addEventListener("mouseout", function(e){
  if (e.target && e.target.dataset && e.target.dataset.t) { tt.style.display = "none"; }
});

// ---- tabs ----------------------------------------------------------------
function show(n){
  MODE = n;
  for (var i = 0; i < 4; i++) {
    var tab = document.getElementById("t" + i), btn = document.getElementById("b" + i);
    if (tab) tab.className = (i === n) ? "tab on" : "tab";
    if (btn) btn.className = (i === n) ? "on" : "";
  }
  if (n === 3) drawDetail();
}
window.vizShow = show;

// ---- world terrain (knitted) ---------------------------------------------
var wyaw = 0.7, wpitch = 0.62, wdrag = null;
window.vizWorldMode = function(mode){
  WORLDMODE = mode;
  ["density", "terrain"].forEach(function(k){
    var b = document.getElementById("wm_" + k);
    if (b) b.className = (k === mode) ? "on" : "";
  });
  var svg = document.getElementById("worldsvg");
  var cv = document.getElementById("worldcanvas");
  if (svg) svg.style.display = (mode === "density") ? "block" : "none";
  if (cv) cv.style.display = (mode === "terrain") ? "block" : "none";
  if (mode === "terrain") drawWorld();
};

function drawWorld(){
  var cv = document.getElementById("worldcanvas");
  if (!cv) return;
  var cx = cv.getContext("2d");
  cx.clearRect(0, 0, cv.width, cv.height);
  var note = document.getElementById("worldnote");
  var WORLD = worldData();
  if (!WORLD || !WORLD.cells) { if (note) note.textContent = T.noworld; return; }
  var keys = Object.keys(WORLD.cells);
  if (!keys.length) { if (note) note.textContent = T.noworld; return; }
  var side = WORLD.side || 3;

  // Each cell is drawn as its own small patch at its true world extent, ABUTTING
  // its neighbours rather than sharing their vertices. That is what makes a
  // seam visible: if two adjacent cells disagree on their shared border height
  // (different mods won them), the patches meet at a step -- a cliff -- instead
  // of being averaged into one smooth vertex. Sharing vertices would hide the
  // exact thing the world view is for.
  var minx = 1e9, maxx = -1e9, miny = 1e9, maxy = -1e9, lo = 1e9, hi = -1e9;
  keys.forEach(function(k){
    var p = k.split(","), cxi = +p[0], cyi = +p[1];
    if (cxi < minx) minx = cxi; if (cxi > maxx) maxx = cxi;
    if (cyi < miny) miny = cyi; if (cyi > maxy) maxy = cyi;
    var hs = WORLD.cells[k];
    for (var i = 0; i < hs.length; i++){ if (hs[i] < lo) lo = hs[i]; if (hs[i] > hi) hi = hs[i]; }
  });
  var span = (hi - lo) || 1;
  // World units: one cell is `side-1` units wide here, so the whole map is
  // (cols)*(side-1) across. The projection centres and scales to fit.
  var cols = (maxx - minx + 1), rows = (maxy - miny + 1);
  var cellW = side - 1;
  var scale = Math.min(cv.width / (cols * cellW * 1.5), cv.height / (rows * cellW * 1.5)) * 1.4;
  var cyaw = Math.cos(wyaw), syaw = Math.sin(wyaw);
  var cxm = (cols * cellW) / 2, cym = (rows * cellW) / 2;

  function P(wx, wy, z){
    var u = wx - cxm, v = wy - cym;
    var rx = u * cyaw - v * syaw, ry = u * syaw + v * cyaw;
    var hh = ((z - lo) / span) * (WORLD.relief || 60);
    return [cv.width / 2 + rx * scale,
            cv.height / 2 + ry * scale * Math.sin(wpitch) - hh * scale * Math.cos(wpitch)];
  }

  var quads = [];
  keys.forEach(function(k){
    var p = k.split(","), cxi = +p[0], cyi = +p[1];
    var hs = WORLD.cells[k];
    // Cell origin in world units; north (max y) at the top means flipping y.
    var ox = (cxi - minx) * cellW, oy = (maxy - cyi) * cellW;
    for (var ly = 0; ly < side - 1; ly++){
      for (var lx = 0; lx < side - 1; lx++){
        // Row 0 of the stored patch is the SOUTH edge, so read rows bottom-up.
        var z0 = hs[(side - 1 - ly) * side + lx];
        var z1 = hs[(side - 1 - ly) * side + lx + 1];
        var z2 = hs[(side - 2 - ly) * side + lx + 1];
        var z3 = hs[(side - 2 - ly) * side + lx];
        var pts = [P(ox + lx, oy + ly, z0), P(ox + lx + 1, oy + ly, z1),
                   P(ox + lx + 1, oy + ly + 1, z2), P(ox + lx, oy + ly + 1, z3)];
        quads.push({p: pts, d: pts[0][1] + pts[1][1] + pts[2][1] + pts[3][1],
                    slope: Math.abs(z0 - z2), z: (z0 + z2) / 2});
      }
    }
  });
  quads.sort(function(a, b){ return a.d - b.d; });
  var maxs = quads.reduce(function(m, q){ return Math.max(m, q.slope); }, 1);
  quads.forEach(function(q){
    var t = (q.z - lo) / span, sh = 1 - Math.min(1, q.slope / maxs) * 0.55;
    cx.fillStyle = "rgb(" + Math.round((60 + 150 * t) * sh) + "," +
      Math.round((75 + 140 * t) * sh) + "," + Math.round((85 + 110 * t) * sh) + ")";
    cx.beginPath(); cx.moveTo(q.p[0][0], q.p[0][1]);
    for (var i = 1; i < 4; i++) cx.lineTo(q.p[i][0], q.p[i][1]);
    cx.closePath(); cx.fill();
  });
  if (note) note.textContent = T.worldof
    .replace("{cells}", keys.length).replace("{quads}", quads.length)
    .replace("{lo}", Math.round(lo)).replace("{hi}", Math.round(hi));
}

(function(){
  var cv = document.getElementById("worldcanvas");
  if (!cv) return;
  cv.addEventListener("mousedown", function(e){ wdrag = [e.clientX, e.clientY]; });
  window.addEventListener("mouseup", function(){ wdrag = null; });
  window.addEventListener("mousemove", function(e){
    if (!wdrag) return;
    wyaw += (e.clientX - wdrag[0]) * 0.01;
    wpitch = Math.max(0.08, Math.min(1.5, wpitch + (e.clientY - wdrag[1]) * 0.01));
    wdrag = [e.clientX, e.clientY];
    drawWorld();
  });
})();

// ---- focus / filter ------------------------------------------------------
function matches(el){
  if (!FOCUS) return true;
  return (el.dataset.m || "").indexOf("|" + FOCUS + "|") > -1;
}
function setFocus(v){
  FOCUS = (v || "").toLowerCase();
  var sel = document.getElementById("focus");
  if (sel && sel.value.toLowerCase() !== FOCUS) sel.value = v || "";
  document.querySelectorAll("rect.cell").forEach(function(r){
    r.classList.toggle("dim", !!FOCUS && !matches(r));
  });
  applyList("xt"); applyList("it");
  var info = document.getElementById("focusinfo");
  if (!info) return;
  if (!FOCUS) { info.textContent = ""; return; }
  var cells = 0, conflicts = 0, co = {};
  document.querySelectorAll("rect.cell").forEach(function(r){
    if (!matches(r)) return;
    cells++; conflicts += (+r.dataset.n || 0);
    (r.dataset.m || "").split("|").forEach(function(m){
      if (m && m !== FOCUS) co[m] = (co[m] || 0) + 1;
    });
  });
  var names = Object.keys(co).sort(function(a, b){ return co[b] - co[a]; });
  info.textContent = T.focusinfo
    .replace("{cells}", cells).replace("{conflicts}", conflicts)
    .replace("{mods}", names.length)
    .replace("{top}", names.slice(0, 12).map(function(n){ return n + " (" + co[n] + ")"; }).join(", "));
}
window.vizFocus = setFocus;

var Q = {xt: "", it: ""};
function applyList(id){
  var tbl = document.getElementById(id);
  if (!tbl) return;
  tbl.querySelectorAll("tbody tr").forEach(function(r){
    var okQ = !Q[id] || r.innerText.toLowerCase().indexOf(Q[id]) > -1;
    r.classList.toggle("dimrow", !(okQ && matches(r)));
  });
}
window.vizFilter = function(id, value){ Q[id] = (value || "").toLowerCase(); applyList(id); };

// ---- selecting a cell ----------------------------------------------------
function selectCell(key, fromMap){
  SEL = key;
  var d = D.detail[key];
  var btn = document.getElementById("b3");
  if (btn) btn.disabled = !d;
  var head = document.getElementById("detailhead");
  if (head) head.textContent = d ? T.detailfor.replace("{cell}", key) : T.nodetail.replace("{cell}", key);
  // Point the "full-resolution page" link at this cell's standalone page, if
  // one was written. Shown only when there is both detail and a data folder.
  var full = document.getElementById("fullpage");
  if (full) {
    if (d && D.dataDir) {
      full.href = D.dataDir + "/pages/" + key.replace(",", "_").replace(/-/g, "m") + ".html";
      full.style.display = "";
    } else {
      full.style.display = "none";
    }
  }
  buildPluginPicker(d);
  if (d) {
    show(3);
    // Ask for full resolution in the background; the sampled grid is already
    // on screen, so the upgrade is a redraw rather than a wait.
    requestFull(key, function(){
      if (SEL !== key) return;
      buildPluginPicker(currentDetail());
      drawDetail();
    });
  } else if (fromMap) { jumpToRow(key); }
}
window.vizSelect = selectCell;

function jumpToRow(key){
  show(1);
  var row = document.getElementById("r_" + key.replace(/-/g, "m").replace(",", "_"));
  if (!row) return;
  row.scrollIntoView({block: "center"});
  row.classList.add("hl");
  setTimeout(function(){ row.classList.remove("hl"); }, 2200);
}

function buildPluginPicker(d){
  var wrap = document.getElementById("plugpick");
  if (!wrap) return;
  wrap.innerHTML = "";
  if (!d) return;
  var names = d.plugins || [];
  names.forEach(function(name, i){
    var b = document.createElement("button");
    b.textContent = name;
    b.className = (i === OVERLAY) ? "on" : "";
    b.onclick = function(){ OVERLAY = i; buildPluginPicker(d); drawDetail(); };
    wrap.appendChild(b);
  });
}

// ---- local views ---------------------------------------------------------
// Prefer the full-resolution cell once it has loaded; fall back to the
// sampled overview so the view is never blank while a sidecar is in flight.
function currentDetail(){
  if (!SEL) return null;
  return FULL[SEL] || D.detail[SEL] || null;
}

function requestFull(key, done){
  if (FULL[key] || !D.dataDir) { done(); return; }
  if (PENDING[key]) { PENDING[key].push(done); return; }
  PENDING[key] = [done];
  var s = document.createElement("script");
  s.src = D.dataDir + "/cells/" + key.replace(",", "_").replace(/-/g, "m") + ".js";
  s.onerror = function(){
    // No sidecar for this cell: keep the sampled overview rather than failing.
    var waiting = PENDING[key] || [];
    delete PENDING[key];
    FULL[key] = null;
    waiting.forEach(function(fn){ fn(); });
  };
  document.head.appendChild(s);
}

function setSub(s){
  SUB = s;
  ["surface", "diff", "nav"].forEach(function(k){
    var b = document.getElementById("sub_" + k);
    if (b) b.className = (k === s) ? "on" : "";
  });
  drawDetail();
}
window.vizSub = setSub;
window.vizDiffToggle = function(){
  DIFF = !DIFF;
  var b = document.getElementById("difftog");
  if (b) b.className = DIFF ? "on" : "";
  drawDetail();
};

var yaw = 0.7, pitch = 0.55, drag = null;
function drawDetail(){
  var d = currentDetail();
  var cv = document.getElementById("cellcanvas");
  if (!cv || !d) return;
  var cx = cv.getContext("2d");
  cx.clearRect(0, 0, cv.width, cv.height);
  var note = document.getElementById("detailnote");
  // The three draws live in the shared VizDraw module, so this panel and the
  // standalone cell pages cannot disagree about how a cell looks. This panel
  // decimates a large surface (stride 2) for smooth dragging; the dedicated
  // page passes stride 1 for full resolution.
  var r;
  if (SUB === "nav") { r = window.VizDraw.nav(cv, cx, d, {diff: DIFF}); }
  else if (SUB === "diff") { r = window.VizDraw.diff(cv, cx, d, {diff: DIFF}); }
  else {
    var g0 = d.land[Object.keys(d.land || {})[0] || ""] || {};
    var n = g0.side || 0;
    r = window.VizDraw.surface(cv, cx, d, {overlay: OVERLAY, yaw: yaw, pitch: pitch,
                                           stride: (n > 40) ? 2 : 1});
  }
  if (note) note.textContent = noteFor(SUB, r);
}

// Turn a VizDraw result into a status line, using the page's own labels so
// wording and translation stay out of the shared drawing code.
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
  return T.surfaceof.replace("{plugin}", r.plugin).replace("{lo}", r.lo).replace("{hi}", r.hi);
}

// Rotating the surface. Only the 3D mode uses it; the others ignore the drag.
(function(){
  var cv = document.getElementById("cellcanvas");
  if (!cv) return;
  cv.addEventListener("mousedown", function(e){ drag = [e.clientX, e.clientY]; });
  window.addEventListener("mouseup", function(){ drag = null; });
  window.addEventListener("mousemove", function(e){
    if (!drag || SUB !== "surface") return;
    yaw += (e.clientX - drag[0]) * 0.01;
    pitch = Math.max(0.08, Math.min(1.5, pitch + (e.clientY - drag[1]) * 0.01));
    drag = [e.clientX, e.clientY];
    drawDetail();
  });
})();

show(0);
})();
"""
