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

**A chain of edits, not a star of comparisons against the winner.**
Morrowind's landscape records don't merge -- whichever plugin loads last for a
given record completely replaces what was there before. So the question that
matters at each step isn't "how does this differ from the winner" but "what
did *this* plugin change, relative to whatever the plugin before it left
behind". The Base tab is the earliest contributor -- usually the game's own
master file -- shown on its own absolute elevation scale, since there's
nothing before it to diff against; each later tab diffs that plugin against
the one immediately before it in load order. The page opens on the winner's
own step, since that's the change that's actually live in the game. Every
contributing plugin's grid is decoded once, server-side (only Python can read
``VHGT``), and embedded as JSON; a tab switch then recomputes the delta and
redraws entirely client-side, the same split :mod:`~wraithguard.viz.terrain3d`
and :mod:`~wraithguard.viz.pathgrid` use for their own switchers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wraithguard import _
from wraithguard.tes3fields.landscape import LandscapeDecodeError, decode_vertex_heights
from wraithguard.viz import html as h
from wraithguard.viz.palette import NEUTRAL, divergence

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: Pixel size of one vertex in the rendered grid. 65 x 9 = 585px, which fits a
#: normal window without scaling and keeps individual vertices clickable.
_VERTEX_PX = 9

#: Deltas below this many world units are treated as noise for the "changed
#: vertices" count. A unit is tiny -- the player is ~128 units tall -- so a
#: sub-unit difference is not a change anyone can see.
_NOISE_FLOOR = 1.0

#: Shared tab-strip styling, matching terrain3d's and pathgrid's switchers.
_TABS_CSS = (
    "<style>.tabs{margin-bottom:10px}.tabs button{background:#2c313a;color:#d7dae0;"
    "border:1px solid #333945;border-radius:4px;padding:5px 10px;margin-right:6px;"
    "cursor:pointer;font:inherit}.tabs button.on{background:#3d4450;border-color:#7cc5ff;"
    "color:#fff}</style>"
)

_SCRIPT = """
(function(){
const D=window.__heightdelta;
const group=document.getElementById('cells');
const NS='http://www.w3.org/2000/svg';
const btns=document.querySelectorAll('[data-step]');

function clamp(v){return Math.max(0,Math.min(1,v));}
function channel(v){return Math.round(clamp(v)*255).toString(16).padStart(2,'0');}
function hexColor(r,g,b){return '#'+channel(r)+channel(g)+channel(b);}
function divergence(value,scale){
  if(scale<=0)return D.neutral;
  let t=clamp(Math.abs(value)/scale);
  t=Math.pow(t,0.6);
  if(value>=0)return hexColor(0.17+0.78*t,0.19-0.08*t,0.23-0.15*t);
  return hexColor(0.17-0.13*t,0.19+0.35*t,0.23+0.72*t);
}
// Low-to-high elevation ramp for the Base step: there is no previous version
// to diff against, so color tracks absolute height within this one surface
// instead of a signed delta -- cool/low to warm/high, the usual
// topographic-map convention.
function elevation(value,lo,span){
  const t=span>0?clamp((value-lo)/span):0;
  const loC=[0.20,0.30,0.42],hiC=[0.64,0.52,0.30];
  return hexColor(
    loC[0]+(hiC[0]-loC[0])*t,
    loC[1]+(hiC[1]-loC[1])*t,
    loC[2]+(hiC[2]-loC[2])*t,
  );
}

function draw(step){
  group.textContent='';
  const name=D.chain[step];
  const current=D.surfaces[name];
  const size=current.length;
  const px=D.vertex_px;

  if(step<=0){
    let lo=Infinity,hi=-Infinity;
    for(const row of current)for(const v of row){if(v<lo)lo=v;if(v>hi)hi=v;}
    const span=hi-lo;
    for(let row=0;row<size;row++){
      const y=(size-1-row)*px;
      for(let col=0;col<size;col++){
        const value=current[row][col];
        const x=col*px;
        const rect=document.createElementNS(NS,'rect');
        rect.setAttribute('x',x);rect.setAttribute('y',y);
        rect.setAttribute('width',px);rect.setAttribute('height',px);
        rect.setAttribute('fill',elevation(value,lo,span));
        const title=document.createElementNS(NS,'title');
        title.textContent=D.labels.vertex_abs
          .replace('%(col)s',col).replace('%(row)s',row)
          .replace('%(height)s',Math.round(value));
        rect.appendChild(title);
        group.appendChild(rect);
      }
    }
    document.getElementById('val-step').textContent=D.labels.base;
    document.getElementById('verdict').textContent=D.labels.base_note
      .replace('%(lo)s',Math.round(lo)).replace('%(hi)s',Math.round(hi));
    return;
  }

  const prevName=D.chain[step-1];
  const other=D.surfaces[prevName];
  let peak=0,changed=0,raised=0;
  const total=size*size;
  const deltas=[];
  for(let row=0;row<size;row++){
    const drow=new Array(size);
    for(let col=0;col<size;col++){
      const value=current[row][col]-other[row][col];
      drow[col]=value;
      const a=Math.abs(value);
      if(a>peak)peak=a;
      if(a>=D.noise_floor){changed++;if(value>0)raised++;}
    }
    deltas.push(drow);
  }
  const scale=peak>0?peak:1;
  for(let row=0;row<size;row++){
    const y=(size-1-row)*px;
    for(let col=0;col<size;col++){
      const value=deltas[row][col];
      if(Math.abs(value)<D.noise_floor)continue;
      const x=col*px;
      const rect=document.createElementNS(NS,'rect');
      rect.setAttribute('x',x);rect.setAttribute('y',y);
      rect.setAttribute('width',px);rect.setAttribute('height',px);
      rect.setAttribute('fill',divergence(value,scale));
      const title=document.createElementNS(NS,'title');
      title.textContent=D.labels.vertex
        .replace('%(col)s',col).replace('%(row)s',row)
        .replace('%(delta)s',(value>=0?'+':'')+Math.round(value));
      rect.appendChild(title);
      group.appendChild(rect);
    }
  }
  document.getElementById('val-step').textContent=prevName+' \u2192 '+name;
  const verdict=document.getElementById('verdict');
  if(changed>0){
    verdict.textContent=D.labels.changed
      .replace('%(changed)s',changed).replace('%(total)s',total)
      .replace('%(peak)s',Math.round(peak))
      .replace('%(up)s',raised).replace('%(down)s',changed-raised);
  }else{
    verdict.textContent=D.labels.identical;
  }
}

btns.forEach(function(b){
  b.addEventListener('click',function(){
    btns.forEach(function(o){o.className='';});
    b.className='on';
    draw(+b.dataset.step);
  });
});
draw(D.default_step);
})();
"""


class HeightDeltaError(Exception):
    """Raised when a height comparison cannot be rendered."""


def _subtract(
    winner: Sequence[Sequence[float]], loser: Sequence[Sequence[float]]
) -> list[list[float]]:
    """Compute winner-minus-loser over two equally-shaped height grids.

    Kept as a small, independently testable primitive even though the page
    itself now recomputes deltas client-side (a tab switch has no server round
    trip to call this from); other tools in this codebase may still want a
    one-shot difference without going through HTML at all.

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
    surfaces: Mapping[str, tuple[str | bytes, float]],
    *,
    winner_name: str,
    cell_label: str = "",
) -> str:
    """Render terrain height as the chain of edits that produced it.

    Args:
        surfaces: Every contributing plugin's filename to its
            ``(vertex_heights.data, vertex_heights.offset)``. Must contain
            ``winner_name``, which should be last, and at least one other
            plugin whose grid decodes to the same shape.
        winner_name: The plugin whose step the page opens on; every step is
            diffed against the plugin immediately before it in load order.
        cell_label: Optional cell description, e.g. ``"(43, -45)"``.

    Returns:
        A complete HTML document.

    Raises:
        HeightDeltaError: If the winner's field cannot be decoded, or no other
            surface decodes to the same grid shape as the winner's.
    """
    winner_value, winner_offset = surfaces[winner_name]
    try:
        winner = decode_vertex_heights(winner_value, winner_offset)
    except LandscapeDecodeError as exc:
        raise HeightDeltaError(str(exc)) from exc

    chain: list[str] = []
    grids: dict[str, list[list[float]]] = {}
    for name, (value, offset) in surfaces.items():
        if name == winner_name:
            chain.append(name)
            grids[name] = winner
            continue
        try:
            grid = decode_vertex_heights(value, offset)
        except LandscapeDecodeError:
            continue
        if len(grid) != len(winner) or any(  # pragma: no cover - every VHGT decodes to 65x65
            len(a) != len(b) for a, b in zip(grid, winner)
        ):
            continue  # different grid size -- not comparable, quietly skipped
        chain.append(name)
        grids[name] = grid

    if len(chain) < 2:
        raise HeightDeltaError(
            "no other plugin's landscape record decoded to the same grid shape as "
            "the winner's, so no comparison can be drawn"
        )

    size = len(winner)
    span = size * _VERTEX_PX
    # Representative full-saturation swatches for the legend; the live map's
    # own saturation point moves with whichever step is selected, so an
    # exact-shade legend would be wrong the moment someone switches tabs.
    high = divergence(1.0, 1.0)
    low = divergence(-1.0, 1.0)

    tabs = "".join(
        f'<button data-step="{index}" class="{"on" if index == len(chain) - 1 else ""}">'
        + (
            h.escape(_("Base: %(plugin)s") % {"plugin": name})
            if index == 0
            else (
                h.escape(_("Winner: %(plugin)s") % {"plugin": name})
                if index == len(chain) - 1
                else h.escape(name)
            )
        )
        + "</button>"
        for index, name in enumerate(chain)
    )

    facts = (
        f'<span>{h.escape(_("Cell"))}: <b>{h.escape(cell_label or _("(unknown)"))}</b></span>'
        f'<span>{h.escape(_("Winner"))}: <b>{h.escape(winner_name)}</b></span>'
        f'<span>{h.escape(_("Step"))}: <b id="val-step"></b></span>'
    )

    body = [
        f'<div class="legend">{facts}</div>',
        h.card(
            _("Height difference"),
            f'<div class="tabs">{tabs}</div>'
            '<div class="empty" id="verdict"></div>'
            f'<svg id="grid" class="grid" viewBox="0 0 {span} {span}" '
            f'width="{span}" height="{span}" role="img"><g id="cells"></g></svg>'
            + h.legend(
                [
                    (high, _("higher than the previous step")),
                    (low, _("lower than the previous step")),
                ],
                _(
                    "North is up. Unchanged vertices are left blank on a diff step, and "
                    "color saturates at that step's largest movement, so shades are "
                    "comparable within a page but not between pages. The Base step has "
                    "nothing before it to diff against, so it shows that plugin's own "
                    "absolute elevation on a separate low-to-high scale instead."
                ),
            ),
        ),
        _TABS_CSS,
    ]

    payload = {
        "chain": chain,
        "surfaces": {name: grids[name] for name in chain},
        "default_step": len(chain) - 1,
        "neutral": NEUTRAL,
        "vertex_px": _VERTEX_PX,
        "noise_floor": _NOISE_FLOOR,
        "labels": {
            "base": _("Base version"),
            "vertex": _("vertex (%(col)s, %(row)s): %(delta)s units"),
            "vertex_abs": _("vertex (%(col)s, %(row)s): %(height)s units"),
            "base_note": _(
                "Absolute elevation, %(lo)s to %(hi)s units. Nothing loaded before "
                "it to compare against."
            ),
            "changed": _(
                "%(changed)s of %(total)s vertices differ from the previous step. "
                "Largest movement %(peak)s units (%(up)s raised, %(down)s lowered)."
            ),
            "identical": _(
                "The terrain is identical at this step. It differs from the previous "
                "step in some other field -- textures, colors or the world map -- "
                "not in height."
            ),
        },
    }
    body.append(
        f"<script>window.__heightdelta={h.script_json(payload)};</script><script>{_SCRIPT}</script>"
    )

    return h.page(
        _("Terrain difference"),
        _(
            "Absolute heights, decoded and subtracted. Comparing the raw fields is "
            "misleading: heights are stored as cumulative deltas, so moving one "
            "vertex changes every byte after it."
        ),
        "".join(body),
    )
