"""The cell as a rotatable 3D surface.

A 65x65 grid of numbers describes terrain exactly and conveys its shape to
nobody. This renders the same data as a surface you can turn, which is the one
view that answers "is that a ridge or a trench" without counting.

**Why this is hand-rolled rather than built on a 3D library.** The generated
pages are self-contained by design -- no CDN, no external script -- because the
tool runs offline and ships as a PyInstaller binary, and a page that loses its
script tag when the network is down is worse than one that never had it.
Pulling in Three.js would either break that or add a bundled dependency to
every build for one view. A height *field* is a much smaller problem than
general 3D: the mesh is a regular grid, so quads can be sorted back-to-front
and painted with no depth buffer, no camera library and no shaders. That fits
in the page.

The projection is isometric with adjustable yaw and pitch. Faces are shaded by
surface slope, which reads as terrain far better than shading by height does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from mlox_subset import _
from mlox_subset.tes3fields.landscape import LandscapeDecodeError, decode_vertex_heights
from mlox_subset.viz import html as h


class Terrain3DError(Exception):
    """Raised when a terrain surface cannot be rendered."""


#: Drawn at reduced resolution: 65x65 is 4,096 quads per surface, which a 2D
#: canvas can paint but not at interactive frame rates while dragging. Sampling
#: every other vertex quarters the work and loses nothing visible at this size.
_STRIDE = 2

_SCRIPT = """
(function(){
const D=window.__terrain;
const cv=document.getElementById('surface'),cx=cv.getContext('2d');
let yaw=0.7,pitch=0.55,drag=null,which=0;
const btns=document.querySelectorAll('[data-surface]');
btns.forEach(function(b){b.addEventListener('click',function(){
  which=+b.dataset.surface;
  btns.forEach(function(o){o.className=(o===b)?'on':'';});draw();});});
cv.addEventListener('mousedown',function(e){drag=[e.clientX,e.clientY];});
window.addEventListener('mouseup',function(){drag=null;});
window.addEventListener('mousemove',function(e){
  if(!drag)return;
  yaw+=(e.clientX-drag[0])*0.01;
  pitch=Math.max(0.08,Math.min(1.5,pitch+(e.clientY-drag[1])*0.01));
  drag=[e.clientX,e.clientY];draw();});
function project(x,y,z,n,lo,span){
  const cxs=Math.cos(yaw),sxs=Math.sin(yaw);
  const u=(x-(n-1)/2),v=(y-(n-1)/2);
  const rx=u*cxs-v*sxs, ry=u*sxs+v*cxs;
  const h=((z-lo)/span)*D.relief;
  return [cv.width/2+rx*D.zoom, cv.height/2+ry*D.zoom*Math.sin(pitch)-h*Math.cos(pitch)*D.zoom];
}
function draw(){
  const g=D.surfaces[which].grid,n=g.length;
  let lo=Infinity,hi=-Infinity;
  for(const r of g)for(const z of r){if(z<lo)lo=z;if(z>hi)hi=z;}
  const span=(hi-lo)||1;
  cx.clearRect(0,0,cv.width,cv.height);
  const quads=[];
  for(let y=0;y<n-1;y++)for(let x=0;x<n-1;x++){
    const zs=[g[y][x],g[y][x+1],g[y+1][x+1],g[y+1][x]];
    const pts=[project(x,y,zs[0],n,lo,span),project(x+1,y,zs[1],n,lo,span),
               project(x+1,y+1,zs[2],n,lo,span),project(x,y+1,zs[3],n,lo,span)];
    // Depth key: painter's algorithm. A regular grid never self-intersects, so
    // sorting by mean screen depth is exact here, not an approximation.
    quads.push({pts:pts,d:pts[0][1]+pts[1][1]+pts[2][1]+pts[3][1],
                slope:Math.abs(zs[0]-zs[2]),z:(zs[0]+zs[2])/2});
  }
  quads.sort(function(a,b){return a.d-b.d;});
  const maxs=quads.reduce(function(m,q){return Math.max(m,q.slope);},1);
  for(const q of quads){
    const t=(q.z-lo)/span, s=1-Math.min(1,q.slope/maxs)*0.55;
    const r=Math.round((60+150*t)*s),gg=Math.round((75+140*t)*s),b=Math.round((85+110*t)*s);
    cx.fillStyle='rgb('+r+','+gg+','+b+')';
    cx.beginPath();cx.moveTo(q.pts[0][0],q.pts[0][1]);
    for(let i=1;i<4;i++)cx.lineTo(q.pts[i][0],q.pts[i][1]);
    cx.closePath();cx.fill();
  }
  document.getElementById('range').textContent=
    D.labels.range.replace('%(lo)s',Math.round(lo)).replace('%(hi)s',Math.round(hi));
}
draw();
})();
"""


def _sample(grid: Sequence[Sequence[float]], stride: int) -> list[list[float]]:
    """Reduce a height grid by taking every ``stride``-th vertex.

    Args:
        grid: The full-resolution grid.
        stride: Sampling interval; 1 returns the grid unchanged.

    Returns:
        The reduced grid, always keeping at least a 2x2 surface so the
        renderer has something to draw.
    """
    if stride <= 1:
        return [list(row) for row in grid]
    out = [[float(v) for v in row[::stride]] for row in grid[::stride]]
    return out if len(out) >= 2 else [list(row) for row in grid]


def build_terrain_3d(
    surfaces: Mapping[str, tuple[str | bytes, float]],
    *,
    cell_label: str = "",
) -> str:
    """Render one or more plugins' terrain as a rotatable 3D surface.

    Args:
        surfaces: Plugin filename to ``(vertex_heights.data, offset)``. Give
            more than one to make them switchable in place, which is what makes
            a difference in shape obvious.
        cell_label: Optional cell description.

    Returns:
        A complete HTML document.

    Raises:
        Terrain3DError: If no surface could be decoded.
    """
    decoded: list[dict[str, object]] = []
    failures: list[str] = []
    for name, (value, offset) in surfaces.items():
        try:
            grid = decode_vertex_heights(value, offset)
        except LandscapeDecodeError as exc:
            failures.append(f"{name}: {exc}")
            continue
        decoded.append({"name": name, "grid": _sample(grid, _STRIDE)})

    if not decoded:
        detail = "; ".join(failures) if failures else "no landscape data was supplied"
        raise Terrain3DError(f"no terrain could be decoded ({detail})")

    payload = {
        "surfaces": decoded,
        "zoom": 8.0,
        "relief": 110.0,
        "labels": {"range": _("Height range: %(lo)s to %(hi)s units")},
    }
    buttons = "".join(
        f'<button data-surface="{index}" class="{"on" if index == 0 else ""}">'
        f"{h.escape(surface['name'])}</button>"
        for index, surface in enumerate(decoded)
    )
    note = (
        _("Drag to rotate. Shading follows slope, which reads as terrain better than height does.")
        if len(decoded) < 2
        else _(
            "Drag to rotate; switch plugins to see the same cell as each one leaves it. "
            "Shading follows slope."
        )
    )
    warning = (
        f'<div class="empty">{h.escape(_("Some records could not be decoded: %(detail)s") % {"detail": "; ".join(failures)})}</div>'
        if failures
        else ""
    )

    body = (
        h.summary({_("Cell"): cell_label or _("(unknown)"), _("Surfaces"): len(decoded)})
        + h.card(
            _("Terrain surface"),
            warning
            + f'<div class="tabs">{buttons}</div>'
            + '<canvas id="surface" width="1100" height="740"></canvas>'
            + f'<div class="legend"><span id="range"></span><span>{h.escape(note)}</span></div>',
        )
        + "<style>.tabs{margin-bottom:10px}.tabs button{background:#2c313a;color:#d7dae0;"
        "border:1px solid #333945;border-radius:4px;padding:5px 10px;margin-right:6px;"
        "cursor:pointer;font:inherit}.tabs button.on{background:#3d4450;border-color:#7cc5ff;"
        "color:#fff}canvas{background:#1a1d23;border-radius:4px;max-width:100%;cursor:grab}</style>"
        + f"<script>window.__terrain={h.script_json(payload)};</script><script>{_SCRIPT}</script>"
    )
    return h.page(
        _("Terrain surface"),
        _("The same 65x65 height grid the text view lists, as a shape you can turn."),
        body,
    )
