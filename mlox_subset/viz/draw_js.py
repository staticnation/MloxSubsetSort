"""The three local-view drawing routines, as one shared source.

The terrain surface, the height difference and the navigation graph are drawn
in two places: inside the explorer's cell-detail tab, and on the dedicated
full-resolution single-cell pages. Keeping the drawing in one module constant
means those two can never disagree about how a cell looks -- the mistake the
whole ``viz`` split exists to prevent, applied to JavaScript.

Each function is **pure over its inputs**: it takes the canvas, the decoded
cell detail, and an options object, draws, and returns a small result object
of the numbers worth reporting. It renders no text and knows no labels, so the
caller owns wording and translation. That is what lets the same code serve a
tabbed panel and a standalone page without carrying either's chrome.
"""

from __future__ import annotations

from typing import Final

#: ``window.VizDraw`` -- ``surface``, ``diff`` and ``nav``. Each returns a
#: plain object the caller turns into a status line.
DRAW_JS: Final[str] = r"""
(function(){
"use strict";

function landNames(d){ return Object.keys((d && d.land) || {}); }

// Extract the region of a neighbour's grid that faces the centre cell. The
// centre sits at (0,0); a neighbour at (dx,dy) shows only its border strip --
// a half on an edge neighbour, a quarter on a corner -- because that is all
// that abuts the centre and all the seam needs. `frac` is how deep the strip
// reaches into the neighbour (0.5 = half).
function facingStrip(hs, n, dx, dy, frac){
  var cut = Math.max(1, Math.round(n * frac));
  // Columns kept: neighbour to the EAST (dx>0) shows its WEST columns, etc.
  var x0 = 0, x1 = n, y0 = 0, y1 = n;
  if (dx > 0) x1 = cut; else if (dx < 0) x0 = n - cut;
  if (dy > 0) y0 = n - cut; else if (dy < 0) y1 = cut;
  var out = [], ox = 0, oy = 0;
  for (var y = y0; y < y1; y++){ var row = []; for (var x = x0; x < x1; x++){ row.push(hs[y * n + x]); } out.push(row); }
  // World origin of this strip: it abuts the centre along the shared border.
  // Centre spans world [0, n-1]. East neighbour begins at world x = n-1.
  ox = (dx > 0) ? (n - 1) : (dx < 0) ? (x0 - (n - 1)) : x0;
  oy = (dy > 0) ? (y0 - (n - 1)) : (dy < 0) ? (n - 1) : y0;
  return {rows: out, ox: ox, oy: oy};
}

// Terrain surface, isometric, shaded by slope. `stride` decimates the mesh:
// 1 is full resolution (right for a single-cell page), higher thins it so a
// 65x65 grid stays smooth to drag. `opts.seams` is an array of neighbour grids
// {dx,dy,heights,side} drawn abutting the centre, so a border-height mismatch
// with a neighbour shows as a step. Returns {plugin, lo, hi} or {empty:true}.
function surface(cv, cx, d, opts){
  opts = opts || {};
  var names = landNames(d);
  if (!names.length) return {empty: true};
  var name = names[Math.min(opts.overlay || 0, names.length - 1)];
  var g = d.land[name], n = g.side || Math.round(Math.sqrt(g.heights.length)), h = g.heights;
  var yaw = (opts.yaw == null) ? 0.7 : opts.yaw, pitch = (opts.pitch == null) ? 0.55 : opts.pitch;
  var stride = opts.stride || 1;
  var zoom = opts.zoom || 7, relief = opts.relief || 90;

  // Assemble every patch to draw: the centre at world (0,0), plus any seam
  // strips at their abutting offsets. lo/hi span them all so shading is
  // consistent across the join.
  var frac = (opts.seamFrac == null) ? 0.5 : opts.seamFrac;
  var patches = [{ox: 0, oy: 0, w: n, hgt: n, get: function(x, y){ return h[y * n + x]; }, seam: false}];
  var lo = g.min, hi = g.max;
  (opts.seams || []).forEach(function(s){
    var sn = s.side || Math.round(Math.sqrt(s.heights.length));
    var strip = facingStrip(s.heights, sn, s.dx, s.dy, frac);
    var rows = strip.rows;
    for (var yy = 0; yy < rows.length; yy++){ for (var xx = 0; xx < rows[yy].length; xx++){
      var z = rows[yy][xx]; if (z < lo) lo = z; if (z > hi) hi = z; } }
    patches.push({ox: strip.ox, oy: strip.oy, w: rows[0] ? rows[0].length : 0, hgt: rows.length,
                  get: function(x, y){ return rows[y][x]; }, seam: true});
  });
  var span = (hi - lo) || 1;
  function proj(wx, wy, z){
    var c = Math.cos(yaw), s = Math.sin(yaw);
    var u = wx - (n - 1) / 2, v = wy - (n - 1) / 2;
    var rx = u * c - v * s, ry = u * s + v * c;
    var hh = ((z - lo) / span) * relief;
    return [cv.width / 2 + rx * zoom,
            cv.height / 2 + ry * zoom * Math.sin(pitch) - hh * Math.cos(pitch) * zoom / 6];
  }
  var quads = [];
  patches.forEach(function(pt){
    var st = pt.seam ? 1 : stride;
    for (var y = 0; y < pt.hgt - st; y += st){
      for (var x = 0; x < pt.w - st; x += st){
        var z0 = pt.get(x, y), z1 = pt.get(x + st, y);
        var z2 = pt.get(x + st, y + st), z3 = pt.get(x, y + st);
        var p = [proj(pt.ox + x, pt.oy + y, z0), proj(pt.ox + x + st, pt.oy + y, z1),
                 proj(pt.ox + x + st, pt.oy + y + st, z2), proj(pt.ox + x, pt.oy + y + st, z3)];
        quads.push({p: p, d: p[0][1] + p[1][1] + p[2][1] + p[3][1],
                    slope: Math.abs(z0 - z2), z: (z0 + z2) / 2, seam: pt.seam});
      }
    }
  });
  quads.sort(function(a, b){ return a.d - b.d; });
  var maxs = quads.reduce(function(m, q){ return Math.max(m, q.slope); }, 1);
  quads.forEach(function(q){
    var t = (q.z - lo) / span, sh = 1 - Math.min(1, q.slope / maxs) * 0.55;
    // Seam strips are drawn dimmer so the centre cell -- the one being looked
    // at -- stays visually dominant while its neighbours give context.
    var dim = q.seam ? 0.6 : 1;
    cx.fillStyle = "rgb(" + Math.round((60 + 150 * t) * sh * dim) + "," +
      Math.round((75 + 140 * t) * sh * dim) + "," + Math.round((85 + 110 * t) * sh * dim) + ")";
    cx.beginPath(); cx.moveTo(q.p[0][0], q.p[0][1]);
    for (var i = 1; i < 4; i++) cx.lineTo(q.p[i][0], q.p[i][1]);
    cx.closePath(); cx.fill();
  });
  return {plugin: name, lo: Math.round(g.min), hi: Math.round(g.max), seams: (opts.seams || []).length};
}

// Winner-minus-loser height delta: red raised, blue lowered. With opts.diff on,
// unchanged vertices are skipped. Returns the tallies, or {needtwo}/{mismatch}.
function diff(cv, cx, d, opts){
  opts = opts || {};
  var names = landNames(d);
  if (names.length < 2) return {needtwo: true};
  var ga = d.land[names[names.length - 1]], gb = d.land[names[names.length - 2]];
  var a = ga.heights, b = gb.heights;
  var n = ga.side || Math.round(Math.sqrt(a.length));
  if ((gb.side || 0) !== (ga.side || 0) || a.length !== b.length) return {mismatch: true};
  var peak = 0, changed = 0, deltas = new Array(n * n);
  for (var i = 0; i < n * n; i++){
    var v = (a[i] || 0) - (b[i] || 0);
    deltas[i] = v;
    if (Math.abs(v) >= 1) changed++;
    if (Math.abs(v) > peak) peak = Math.abs(v);
  }
  var scale = peak || 1, px = Math.max(1, Math.floor(Math.min(cv.width, cv.height) / n));
  var ox = (cv.width - px * n) / 2, oy = (cv.height - px * n) / 2;
  var hideUnchanged = (opts.diff !== false);
  for (var y = 0; y < n; y++){
    for (var x = 0; x < n; x++){
      var val = deltas[y * n + x];
      if (hideUnchanged && Math.abs(val) < 1) continue;
      var t = Math.pow(Math.min(1, Math.abs(val) / scale), 0.6);
      cx.fillStyle = val >= 0
        ? "rgb(" + Math.round((0.17 + 0.78 * t) * 255) + "," + Math.round((0.19 - 0.08 * t) * 255) + "," + Math.round((0.23 - 0.15 * t) * 255) + ")"
        : "rgb(" + Math.round((0.17 - 0.13 * t) * 255) + "," + Math.round((0.19 + 0.35 * t) * 255) + "," + Math.round((0.23 + 0.72 * t) * 255) + ")";
      // Row 0 is the SOUTH edge, so flip: north at the top.
      cx.fillRect(ox + x * px, oy + (n - 1 - y) * px, px, px);
    }
  }
  return {winner: names[names.length - 1], loser: names[names.length - 2],
          changed: changed, total: n * n, peak: Math.round(peak)};
}

// Navigation graph, top-down. With opts.diff on, edges the winner added are
// green and ones it removed red. Returns counts, or {empty:true}.
function nav(cv, cx, d, opts){
  opts = opts || {};
  var names = Object.keys((d && d.pgrd) || {});
  if (!names.length) return {empty: true};
  var win = d.pgrd[names[names.length - 1]];
  var los = names.length > 1 ? d.pgrd[names[names.length - 2]] : null;
  var pts = win.points || [];
  if (!pts.length) return {empty: true};
  var showDiff = (opts.diff !== false);
  var xs = pts.map(function(p){ return p[0]; }), ys = pts.map(function(p){ return p[1]; });
  var minx = Math.min.apply(null, xs), maxx = Math.max.apply(null, xs);
  var miny = Math.min.apply(null, ys), maxy = Math.max.apply(null, ys);
  var w = Math.max(1, maxx - minx), h = Math.max(1, maxy - miny);
  var pad = 30, sc = Math.min((cv.width - 2 * pad) / w, (cv.height - 2 * pad) / h);
  function P(i){ var p = pts[i]; return [pad + (p[0] - minx) * sc, pad + (maxy - p[1]) * sc]; }
  var before = {};
  if (los) (los.edges || []).forEach(function(e){ before[e[0] + "_" + e[1]] = 1; });
  var now = {};
  (win.edges || []).forEach(function(e){ now[e[0] + "_" + e[1]] = 1; });
  var added = 0, removed = 0;
  (win.edges || []).forEach(function(e){
    var isNew = los && !before[e[0] + "_" + e[1]];
    if (isNew) added++;
    if (e[0] >= pts.length || e[1] >= pts.length) return;
    var A = P(e[0]), B = P(e[1]);
    cx.strokeStyle = (showDiff && isNew) ? "#5cc45c" : "#5a6473";
    cx.lineWidth = (showDiff && isNew) ? 2 : 1;
    cx.beginPath(); cx.moveTo(A[0], A[1]); cx.lineTo(B[0], B[1]); cx.stroke();
  });
  if (los && showDiff){
    (los.edges || []).forEach(function(e){
      if (now[e[0] + "_" + e[1]]) return;
      removed++;
      if (e[0] >= pts.length || e[1] >= pts.length) return;
      var A = P(e[0]), B = P(e[1]);
      cx.strokeStyle = "#e05561"; cx.lineWidth = 2;
      cx.beginPath(); cx.moveTo(A[0], A[1]); cx.lineTo(B[0], B[1]); cx.stroke();
    });
  }
  cx.fillStyle = "#d7dae0";
  pts.forEach(function(_p, i){
    var A = P(i);
    cx.beginPath(); cx.arc(A[0], A[1], 2.6, 0, 6.2832); cx.fill();
  });
  return {plugin: names[names.length - 1], points: pts.length,
          edges: (win.edges || []).length, added: added, removed: removed};
}

window.VizDraw = {surface: surface, diff: diff, nav: nav};
})();
"""
