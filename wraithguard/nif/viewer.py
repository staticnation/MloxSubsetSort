"""A self-contained 3D page for looking at one or two meshes.

Answers the question a triangle count cannot: *does the winner actually look
different?* Two meshes side by side, orbitable, is the shortest route from "this
conflict is flagged" to a decision.

**Why three.js is embedded as a classic script.** Modern three.js ships ESM
only, split across ``three.module.min.js`` and ``three.core.min.js``, and **ES
module scripts do not load from ``file://``** -- the origin is ``null`` and the
CORS check fails. These pages are written to disk and opened in a browser, so a
module build cannot work here regardless of how it is packaged. The CommonJS
build is a single self-contained file with no ``require()`` of its own, so it
runs as an ordinary script behind a three-line ``exports`` shim. That was
verified rather than assumed: the shim was exercised and used to build a real
``BufferGeometry`` with computed normals before any of this was written.

**Why the orbit controls are ours.** three.js ships ``OrbitControls.js``, but it
imports the bare specifier ``'three'``, which would drag ESM and an import map
back into a page that has just gone to some trouble to avoid both. Dragging to
rotate is forty lines; an import map that works from ``file://`` is not.

**Why the page is self-contained.** The existing visualisations are single files
a user can move, keep or send to someone. Referencing a sibling script would
break that, and would also behave differently in the in-app viewers
(``pywebview``, ``tkinterweb``) than in a browser. One file behaves the same
everywhere.

**Finding the three.js build itself, and ``ViewerError``, now live in**
:mod:`wraithguard.viz.library`. That code was never specific to a NIF -- it
locates and reads one vendored asset -- and the texture comparison's WebGL
wipe view needs the identical bytes. This module still owns everything that
*is* NIF-specific: the scene payload, the orbit controls, and the page
template below.
"""

from __future__ import annotations

import base64
import html
import json
import struct
import zlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Final, cast

from wraithguard.images import ImageError, browser_image
from wraithguard.logging_setup import get_logger
from wraithguard.viz import ViewerError, three_source
from wraithguard.viz.library import EXTRA_SLOTS_JS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from wraithguard.nif.geometry import Mesh, TreeNode
    from wraithguard.nif.textures import Resolved, TextureResolver

LOG = get_logger(__name__)

#: Colors for the two sides of a comparison: the overridden mesh and the one
#: that wins. Deliberately not red and green -- the point is to tell them apart,
#: not to say which is better, and roughly 1 in 12 men cannot separate those.
_COLOURS: Final[tuple[str, str]] = ("#6ba3ff", "#ffb86b")

__all__ = ["BlobSink", "ViewerError", "build_viewer_page", "inline_blob", "three_source"]


#: Turns one deflated blob into whatever the page should read it from: a
#: ``{"b64": ...}`` for a standalone file, or a ``{"url": ...}`` for a served
#: one. The page branches on which key is present, so the two modes share every
#: line of rendering code and cannot drift apart.
BlobSink = Callable[[bytes, str], dict[str, str]]


def inline_blob(blob: bytes, content_type: str = "") -> dict[str, str]:
    """Carry a blob inside the document.

    Args:
        blob: The bytes. Deflated for geometry, a PNG for a texture.
        content_type: The MIME type, used only when serving. Inline blobs that
            the page hands to an ``Image`` need it as a data URL prefix; the
            rest are read as raw bytes and do not.

    Returns:
        A base64 entry, or a data URL when one is needed.
    """
    encoded = base64.b64encode(blob).decode("ascii")
    if content_type.startswith("image/"):
        return {"url": f"data:{content_type};base64,{encoded}"}
    return {"b64": encoded}


def _packed(values: list[float] | list[int], fmt: str) -> bytes:
    """Pack numbers as a deflated, base64 binary blob.

    Measured on a 204k-triangle mesh, against writing the same numbers as JSON
    decimals:

    * JSON decimals -- 5.40 MB.
    * base64 typed arrays -- 4.91 MB. Almost no gain: base64 costs 33% and
      hands most of the binary saving straight back.
    * base64 of *deflated* typed arrays -- 1.86 MB, a third of the JSON.

    So the compression is doing the work, not the binary encoding, and it costs
    nothing on the page: browsers inflate this natively with
    ``DecompressionStream`` and no library.

    Args:
        values: The numbers to pack.
        fmt: A :mod:`struct` format character, ``f`` or ``I``.

    Returns:
        The deflated bytes. How they reach the page -- inline as base64, or
        over loopback as a fetch -- is the caller's decision, which is what
        lets one builder produce both a served page and a standalone file.
    """
    raw = struct.pack(f"<{len(values)}{fmt}", *values)
    return zlib.compress(raw, 6)


def texture_bytes(resolved: Resolved, resolver: TextureResolver) -> tuple[bytes, str] | None:
    """Turn a resolved texture into something a browser shows, or give up quietly.

    Read through the resolver rather than off the path directly, because most
    of the base game's textures are inside ``Morrowind.bsa`` and have no path
    at all.

    Args:
        resolved: The outcome of a texture lookup.
        resolver: The resolver that produced it, which knows how to fetch the
            bytes whether they are loose or archived.

    Returns:
        Bytes a browser can display and their MIME type, or ``None`` when
        nothing provides the texture or it is in a format the decoders do not
        handle. Both are ordinary in a mod collection, and a broken texture is
        a finding about the mod rather than a reason to fail the whole view.

        A PNG comes back as the bytes it arrived as -- browsers decode PNG
        better than anything here would, and re-encoding could only lose
        fidelity. Everything else is decoded and re-encoded as PNG.
    """
    if not resolved.found:
        return None
    raw = resolver.read(resolved)
    if raw is None:
        return None
    try:
        return browser_image(raw)
    except (ImageError, ValueError) as exc:
        LOG.debug("cannot decode %s: %s", resolved.reference, exc)
        return None


def _mesh_payload(
    meshes: list[Mesh],
    sink: BlobSink,
    resolver: TextureResolver | None = None,
    cache: dict[str, dict[str, str] | None] | None = None,
) -> list[dict[str, object]]:
    """Reduce meshes to the packed arrays the page needs.

    Positions are flattened before packing, because the page inflates straight
    into a ``Float32Array``: any nesting would only cost the browser a pass to
    undo.

    Args:
        meshes: World-space meshes.
        sink: Turns a blob into something the page can read -- base64 for a
            standalone file, a URL for a served one.
        resolver: Finds texture files across the data folders. Without one the
            meshes are sent untextured, which is a complete and useful view.
        cache: Decoded textures by reference, shared across sides. One texture
            is routinely used by many shapes and by both sides of a
            comparison; decoding a 2048px image once per shape would dominate
            the time to open a view.

    Returns:
        One entry per mesh, JSON-ready.
    """
    decoded: dict[str, dict[str, str] | None] = cache if cache is not None else {}
    payload: list[dict[str, object]] = []
    for mesh in meshes:
        if not mesh.triangles:
            continue
        positions: list[float] = []
        for vertex in mesh.vertices:
            positions.extend(vertex)
        indices: list[int] = []
        for triangle in mesh.triangles:
            indices.extend(triangle)
        uvs: list[float] = []
        # Only send UVs when there is one per vertex. A partial set would make
        # three.js index past the end of the attribute and draw nothing at all,
        # which is a worse outcome than an untextured mesh.
        if len(mesh.uvs) == len(mesh.vertices):
            for u, v in mesh.uvs:
                # NIF measures V downward and OpenGL upward, so an untouched
                # copy renders every texture upside down.
                uvs.extend((u, 1.0 - v))
        image: dict[str, str] | None = None
        extras: dict[str, dict[str, str]] = {}

        def resolve_slot(reference: str) -> dict[str, str] | None:
            """Resolve one texture-slot reference, sharing the page's cache.

            Every optional slot below -- glow, dark, decal, detail, gloss,
            bump -- is a real ``NiTexturingProperty`` entry rather than a
            filename guess, so each is resolved the same way the base texture
            is: through the folder order, not by pattern-matching a sibling
            file.

            Args:
                reference: The slot's normalised texture path, already
                    checked for truthiness by the caller.

            Returns:
                The decoded blob for the page, or ``None`` when it could not
                be read.
            """
            if reference not in decoded:
                found = resolver.resolve(reference)  # type: ignore[union-attr]
                shown = texture_bytes(found, resolver)  # type: ignore[arg-type]
                decoded[reference] = sink(*shown) if shown else None
            return decoded[reference]

        if resolver is not None and mesh.texture and uvs:
            image = resolve_slot(mesh.texture)
            # The mesh names only its diffuse texture; OpenMW finds the rest by
            # name. Offering them is the only way a normal or specular map in
            # a texture pack is ever visible here, since no NIF mentions one.
            for suffix, resolved in resolver.siblings(mesh.texture).items():
                key = f"{mesh.texture}{suffix}"
                if key not in decoded:
                    aux = texture_bytes(resolved, resolver)
                    decoded[key] = sink(*aux) if aux else None
                # Bound to a name rather than subscripted twice: a subscript is
                # re-evaluated, so the None check does not narrow the second
                # one -- which is a real hole, not a typing nicety, if the
                # cache is ever mutated between the two.
                found_aux = decoded[key]
                if found_aux is not None:
                    extras[suffix] = found_aux
        glow = resolve_slot(mesh.glow) if resolver is not None and mesh.glow and uvs else None
        dark = resolve_slot(mesh.dark) if resolver is not None and mesh.dark and uvs else None
        decals = (
            [found for found in (resolve_slot(path) for path in mesh.decals) if found]
            if resolver is not None and mesh.decals and uvs
            else []
        )
        detail = resolve_slot(mesh.detail) if resolver is not None and mesh.detail and uvs else None
        gloss = resolve_slot(mesh.gloss) if resolver is not None and mesh.gloss and uvs else None
        bump = resolve_slot(mesh.bump) if resolver is not None and mesh.bump and uvs else None
        payload.append(
            {
                "name": mesh.name,
                "texture": mesh.texture,
                "collision": mesh.collision,
                "image": image,
                "glow": glow,
                "dark": dark,
                # A list, in slot order, because that is paint order: decals
                # composite over one another and the last one declared is the
                # one on top. Unresolvable ones are dropped rather than sent
                # as holes, so the order that reaches the shader is the order
                # of the decals that actually exist.
                "decals": decals,
                # dark, detail, gloss and decal are all drawn through the
                # onBeforeCompile shader hook (attachExtraSlots, in the JS
                # below) rather than a MeshPhongMaterial property -- Phong has
                # exactly one multiply-the-surface slot and one
                # modulate-specular slot, not four, and specularMap is
                # already spoken for by an OpenMW-style _spec map.
                "detail": detail,
                "gloss": gloss,
                # bump is sent unconditionally too -- what it means depends on
                # which convention drew the file, and only the caller (via the
                # "Bump as normal (MGE)" control) decides whether to use it.
                "bump": bump,
                "extras": extras,
                "positions": sink(_packed(positions, "f"), ""),
                "indices": sink(_packed(indices, "I"), ""),
                "uvs": sink(_packed(uvs, "f"), "") if uvs else None,
                # Per-vertex colors as a packed float run, like every other
                # attribute, rather than JSON numbers: a 30,000-vertex shape
                # is 120,000 floats, and spelling those out as text is larger
                # than the mesh.
                "colors": (
                    sink(_packed([c for rgba in mesh.vertex_colors for c in rgba[:3]], "f"), "")
                    if mesh.vertex_colors
                    else None
                ),
                # The material the *file* describes. Until now the viewer's
                # alpha controls applied one global guess -- a 0.5 cutoff on
                # everything -- because nothing carried the real values. These
                # let each shape use its own, and turn those controls from a
                # guess into an override of a known default.
                "diffuse": list(mesh.diffuse) if mesh.diffuse else None,
                "emissive": list(mesh.emissive) if mesh.emissive else None,
                "opacity": mesh.opacity,
                "alphaBlend": mesh.alpha_blend,
                "alphaTest": mesh.alpha_test,
                "alphaThreshold": mesh.alpha_threshold,
                "vertexCount": len(mesh.vertices),
                "triangleCount": len(mesh.triangles),
            }
        )
    return payload


#: The single-blob texture slots on a mesh payload (``decals`` is a list and
#: ``extras`` a map, handled separately).
_TEXTURE_SLOTS = ("image", "glow", "dark", "detail", "gloss", "bump")


def _hoist_textures(scenes: list[dict[str, object]]) -> list[dict[str, str]]:
    """Collapse texture blobs shared across meshes into one indexed table.

    A decoded texture is a single dict object reused -- by identity -- for every
    mesh that references it (see the cache in :func:`_mesh_payload`). Serialising
    the mesh payloads as they stand writes that texture's bytes once *per mesh*,
    which is fine for a single NIF but ruinous for a cell: hundreds of meshes
    share a handful of wall and crate textures, and the inline copies balloon the
    page to gigabytes. This walks every texture slot, replaces each blob with an
    integer index into a de-duplicated table (keyed by object identity), and
    returns the table for the page to rehydrate from -- so each unique texture is
    carried exactly once no matter how many meshes draw it.

    Args:
        scenes: The scene payloads, mutated in place: every texture slot becomes
            an ``int`` index, or is left ``None``.

    Returns:
        The unique texture blobs, in index order.
    """
    table: list[dict[str, str]] = []
    by_id: dict[int, int] = {}

    def index_of(blob: dict[str, str]) -> int:
        """The blob's index in the table, appending it on first sight."""
        found = by_id.get(id(blob))
        if found is None:
            found = len(table)
            by_id[id(blob)] = found
            table.append(blob)
        return found

    for scene in scenes:
        meshes = cast("list[dict[str, object]]", scene.get("meshes") or [])
        for mesh in meshes:
            for slot in _TEXTURE_SLOTS:
                blob = mesh.get(slot)
                if blob is not None:
                    mesh[slot] = index_of(cast("dict[str, str]", blob))
            decals = cast("list[dict[str, str]]", mesh.get("decals") or [])
            mesh["decals"] = [index_of(blob) for blob in decals]
            extras = cast("dict[str, dict[str, str]]", mesh.get("extras") or {})
            mesh["extras"] = {suffix: index_of(blob) for suffix, blob in extras.items()}
    return table


def _tree_payload(nodes: list[TreeNode]) -> list[dict[str, object]]:
    """Reduce a block tree to JSON the page can render.

    Args:
        nodes: Roots from :func:`~wraithguard.nif.geometry.block_tree`.

    Returns:
        Nested entries.
    """
    return [
        {
            "index": node.index,
            "type": node.type_name,
            "name": node.name,
            "note": node.note,
            "children": _tree_payload(node.children),
        }
        for node in nodes
    ]


def build_viewer_page(
    sides: list[tuple[str, list[Mesh]]],
    title: str = "Mesh viewer",
    *,
    sink: BlobSink | None = None,
    library_url: str = "",
    trees: list[list[TreeNode]] | None = None,
    resolver: TextureResolver | None = None,
    edit: Mapping[str, object] | None = None,
) -> str:
    """Build an HTML page showing one or more meshes.

    Two shapes from one template. With no arguments beyond the meshes it
    produces a **standalone file**: three.js and every byte of geometry inline,
    portable, and multi-megabyte. Given a ``sink`` and a ``library_url`` it
    produces a **served page** of a few kilobytes that fetches both.

    The difference is confined to how bytes arrive. The rendering code is
    identical in both, which is the point: a fallback that shares no code with
    the primary path is a second implementation waiting to rot.

    Args:
        sides: ``(label, meshes)`` pairs. One side shows a single mesh; two
            shows a comparison, each in its own color and its own viewport.
        title: The page title.
        sink: How geometry reaches the page. Defaults to inlining it.
        library_url: Where to fetch three.js. Empty means inline it.
        resolver: Finds texture files across the data folders. Omitted, the
            meshes render in a flat color, which stays a complete view rather
            than a degraded one.
        trees: Block hierarchies, one per side. Optional because the geometry
            view stands on its own; supplied, it fills the structure pane with
            what a render cannot show -- collision nodes, controllers, and
            every block that never draws.
        edit: When given, turns the page into an editor. A mapping of
            ``{"url": <apply endpoint>, "filename": <download name>, "blocks":
            {index: {"type": ..., "fields": [FieldView-shaped dicts]}}}`` -- the
            inspector reads it to show each block's fields, and POSTs edits to
            ``url``. Omitted (the default), the page is the read-only viewer
            exactly as before, carrying none of the editor code.

    Returns:
        The whole HTML document.

    Raises:
        ViewerError: If three.js is needed and missing.
    """
    blob_sink = sink or inline_blob
    library = "" if library_url else three_source()
    shared_textures: dict[str, dict[str, str] | None] = {}
    scenes: list[dict[str, object]] = [
        {
            "label": label,
            "color": _COLOURS[index % len(_COLOURS)],
            "meshes": _mesh_payload(meshes, blob_sink, resolver, shared_textures),
            "tree": _tree_payload(trees[index]) if trees and index < len(trees) else [],
        }
        for index, (label, meshes) in enumerate(sides)
    ]
    empty = all(not scene["meshes"] for scene in scenes)
    if empty:
        LOG.info("viewer built with no geometry: %s", title)
    # Shared textures are carried once in a side table and referenced by index;
    # without this a cell's meshes -- hundreds sharing a few wall textures --
    # would each inline their own copy and the page would reach gigabytes.
    textures = _hoist_textures(scenes)
    # json.dumps escapes nothing HTML-significant by default, and the payload
    # carries mod-authored names. "</script>" inside a string would end the
    # element early, so the sequence is broken up rather than trusted.
    data = json.dumps(scenes, separators=(",", ":")).replace("</", "<\\/")
    textures_data = json.dumps(textures, separators=(",", ":")).replace("</", "<\\/")
    # The CommonJS build needs its two globals to exist *before* it runs and
    # the namespace pulled back out *after*, whether it arrives inline or over
    # the wire. Serving it without the shim was a real bug: the file ran
    # against an undefined ``exports`` and the page reported "THREE is not
    # defined". Classic scripts execute in order, so three tags do for the
    # served case exactly what one does for the inline one.
    prologue = "<script>var module = {exports:{}}, exports = module.exports;</script>"
    epilogue = "<script>var THREE = module.exports;</script>"
    if library_url:
        middle = f'<script src="{html.escape(library_url, quote=True)}"></script>'
    else:
        middle = f"<script>\n{library}\n</script>"
    library_block = f"{prologue}\n{middle}\n{epilogue}"
    if edit:
        edit_data = json.dumps(edit, separators=(",", ":")).replace("</", "<\\/")
        editor_block = _EDITOR_JS.replace("__EDIT_DATA__", edit_data)
    else:
        editor_block = ""
    return (
        _PAGE.replace("__TITLE__", html.escape(title))
        .replace("__LIBRARY_BLOCK__", library_block)
        .replace("__TEXTURES__", textures_data)
        .replace("__DATA__", data)
        .replace("__EMPTY__", "true" if empty else "false")
        .replace("__EXTRA_SLOTS__", EXTRA_SLOTS_JS)
        .replace("__EDIT_JS__", editor_block)
    )


#: The page. Written as one template rather than assembled from fragments: it
#: is read far more often than it is edited, and a reader needs to see the
#: whole document to judge it.
# A **raw** string, and it has to be. The template is verbatim HTML, CSS and
# JavaScript, so a backslash in it belongs to the language being emitted rather
# than to Python. Without the `r`, the eleven `\n` sequences in the shader
# assembly below are turned into real newlines *by Python* and land inside
# JavaScript string literals, which is a syntax error that takes the whole
# page down with it -- the viewer renders nothing and the console blames a line
# that looks fine in the source.
#
# That is exactly how this broke: the shader code was the first thing in the
# template to need an escape, so the missing `r` had been harmless until then.
_PAGE: Final[str] = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
 :root{--ink:#e6e6e6;--dim:#9aa0aa;--line:#333945;--panel:#20242c}
 html,body{margin:0;height:100%;background:#15171c;color:var(--ink);
   font:13px/1.5 "Segoe UI",system-ui,sans-serif;display:flex;flex-direction:column}
 .panel{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center;
   margin:0;padding:8px 10px;border-bottom:1px solid var(--line);
   background:var(--panel);font-size:12.5px}
 .panel .ctl{display:flex;align-items:center;gap:5px}
 .panel .ctl.off{opacity:.45}
 .panel label{color:var(--dim);cursor:pointer}
 .panel .swatch{width:10px;height:10px;border-radius:2px;display:inline-block}
 .panel button{background:#2c313a;color:#d7dae0;border:1px solid var(--line);
   border-radius:4px;padding:4px 9px;cursor:pointer;font:inherit}
 .panel button:hover{background:#3d4450}
 .panel .spacer{flex:1}
 .panel input[type=range]{width:78px;accent-color:#6f8fb8;cursor:pointer}
 #body{flex:1;display:flex;min-height:0}
 #tree{width:300px;min-width:160px;max-width:50%;overflow:auto;padding:8px 10px;
   border-right:1px solid var(--line);background:#181b21;font-size:12px;
   font-family:Consolas,"Cascadia Mono",monospace;white-space:nowrap;resize:horizontal}
 #tree ul{list-style:none;margin:0;padding-left:14px}
 #tree>ul{padding-left:0}
 #tree li{line-height:1.55}
 #tree .ty{color:#9ecbff}
 #tree .nm{color:var(--ink)}
 #tree .no{color:var(--dim)}
 #tree .ix{color:#5b6270;margin-right:5px}
 #tree h4{margin:10px 0 3px;font:600 11px/1.4 "Segoe UI",system-ui,sans-serif;
   color:var(--dim);text-transform:uppercase;letter-spacing:.05em}
 #tree h4:first-child{margin-top:0}
 #tree ul.shapes{padding-left:0}
 #tree ul.shapes li{display:flex;align-items:baseline;gap:5px;white-space:normal}
 #tree ul.shapes input{margin:0;flex:none;accent-color:#6f8fb8;cursor:pointer}
 #tree .shapename{cursor:pointer;text-decoration:underline dotted transparent}
 #tree .shapename:hover{text-decoration-color:#9ecbff;color:#fff}
 #stage{flex:1;position:relative;min-height:0}
 canvas{display:block}
 #stats{position:absolute;bottom:8px;left:10px;opacity:.75;pointer-events:none}
 #hint{position:absolute;bottom:8px;right:10px;opacity:.55;pointer-events:none}
 #none{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
   text-align:center;padding:2rem;opacity:.8}
 #tree li[data-block-index]{cursor:pointer}
 #tree li.sel{background:#094771;box-shadow:inset 3px 0 0 #6ba3ff}
 #inspector{display:none;flex-direction:column;width:360px;min-width:220px;max-width:50%;
   overflow:auto;border-left:1px solid var(--line);background:var(--panel);
   font-family:Consolas,"Cascadia Mono",monospace;font-size:12px;resize:horizontal}
 #inspector .insp-head{padding:11px 12px;border-bottom:1px solid var(--line)}
 #inspector .insp-head .nm{color:#9ecbff;font-weight:600}
 #inspector .insp-head .sub{color:var(--dim);font-size:11px;margin-top:2px}
 #inspector .insp-empty{padding:14px 12px;color:var(--dim)}
 #inspector .sect .sh{padding:8px 12px;color:var(--dim);font-size:11px;letter-spacing:1px;
   font-weight:600;border-bottom:1px solid var(--line);
   font-family:"Segoe UI",system-ui,sans-serif}
 #inspector .sect .body{padding:4px 12px 10px}
 #inspector .frow{padding:6px 0;border-bottom:1px solid #262b33}
 #inspector .frow:last-child{border-bottom:0}
 #inspector .lab{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:4px}
 #inspector .fname{color:var(--ink)}
 #inspector .frow.ro .fname{color:var(--dim)}
 #inspector .kind{color:var(--dim);font-size:10px;border:1px solid var(--line);border-radius:4px;padding:0 5px}
 #inspector input.f{width:100%;background:#2c313a;border:1px solid var(--line);border-radius:5px;
   color:var(--ink);padding:5px 8px;font:inherit}
 #inspector input.f:focus{outline:none;border-color:#6ba3ff}
 #inspector .roval{color:var(--dim)}
 #inspector .frow.dirty input.f{border-color:#c9a227}
 .panel .save-btn{border-color:#3f6a99;color:#9ecbff}
 .panel .save-status{color:var(--dim);font-size:12px}
</style></head><body>
<div class="panel" id="controls"></div>
<div id="body">
  <div id="tree"></div>
  <div id="stage">
    <div id="stats"></div>
    <div id="hint">drag to orbit &middot; shift/right-drag to pan &middot; wheel to zoom</div>
  </div>
  <div id="inspector"></div>
</div>
__LIBRARY_BLOCK__
<script>
(function () {
  var scenes = __DATA__;
  // Textures are carried once in a side table and referenced by index (see
  // _hoist_textures); put each blob back on the mesh so the render code below
  // reads m.image/m.glow/... exactly as before. A null slot stays null; an
  // index of 0 is a real entry, so the test is against null, not truthiness.
  var textures = __TEXTURES__;
  function tex(i) { return (i === null || i === undefined) ? null : textures[i]; }
  scenes.forEach(function (spec) {
    (spec.meshes || []).forEach(function (m) {
      m.image = tex(m.image); m.glow = tex(m.glow); m.dark = tex(m.dark);
      m.detail = tex(m.detail); m.gloss = tex(m.gloss); m.bump = tex(m.bump);
      m.decals = (m.decals || []).map(tex);
      if (m.extras) {
        var rebuilt = {};
        Object.keys(m.extras).forEach(function (s) { rebuilt[s] = tex(m.extras[s]); });
        m.extras = rebuilt;
      }
    });
  });
  var stage = document.getElementById("stage");
  var controls = document.getElementById("controls");
  if (__EMPTY__) {
    stage.innerHTML = '<div id="none">No geometry could be read from this mesh.' +
      '<br>That is a limit of the reader, not a statement about the file.</div>';
    return;
  }

  function inflateBytes(bytes) {
    if (typeof DecompressionStream === "undefined") {
      return Promise.reject(new Error("this browser has no DecompressionStream"));
    }
    var stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
    return new Response(stream).arrayBuffer();
  }

  // A blob is either carried in the document or fetched from the loopback
  // server. Everything after this point is identical either way.
  function load(ref) {
    if (ref.url) {
      return fetch(ref.url).then(function (r) {
        if (!r.ok) throw new Error("could not fetch geometry (" + r.status + ")");
        return r.arrayBuffer();
      }).then(function (buf) { return inflateBytes(new Uint8Array(buf)); });
    }
    var bin = atob(ref.b64), bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return inflateBytes(bytes);
  }

  function unpack(spec) {
    return Promise.all(spec.meshes.map(function (m) {
      var jobs = [load(m.positions), load(m.indices)];
      jobs.push(m.uvs ? load(m.uvs) : Promise.resolve(null));
      jobs.push(m.colors ? load(m.colors) : Promise.resolve(null));
      return Promise.all(jobs).then(function (bufs) {
        m.positions = new Float32Array(bufs[0]);
        m.indices = new Uint32Array(bufs[1]);
        m.uvs = bufs[2] ? new Float32Array(bufs[2]) : null;
        m.colors = bufs[3] ? new Float32Array(bufs[3]) : null;
        return m;
      });
    }));
  }

  Promise.all(scenes.map(unpack)).then(build).catch(function (err) {
    stage.innerHTML = '<div id="none">Could not decode the geometry in this page.' +
      '<br>' + String(err && err.message ? err.message : err) + '</div>';
  });

  function build() {
    var renderer = new THREE.WebGLRenderer({antialias: true});
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    stage.appendChild(renderer.domElement);
    var scene = new THREE.Scene();
    scene.background = new THREE.Color(0x15171c);
    var camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200000);

    // One viewport, one camera, one set of geometry groups toggled on and off.
    // Separate panes each framed their own mesh independently, which is the
    // one thing a comparison must not do: two objects at different scales look
    // identical when each is fitted to its own viewport.
    var pivot = new THREE.Group();
    var groups = [];
    scene.add(pivot);

    var textured = true;
    // Whether anything in this view has an OpenMW auxiliary normal map, which
    // decides whether the control for them is worth offering at all.
    var anyNormalMaps = false;
    // Same reasoning, for the specular maps OpenMW finds beside the diffuse
    // texture by name.
    var anySpecularMaps = false;
    // And for glow: a real NIF texture slot rather than a filename guess, but
    // still worth gating the control on, since most meshes have none.
    var anyGlowMaps = false;
    // Same reasoning as glow -- a real slot, just a rarer one.
    var anyDarkMaps = false;
    var anyDetailMaps = false;
    var anyGlossMaps = false;
    var anyDecalMaps = false;
    // Whether any mesh has a bump-slot texture at all -- not whether it is
    // currently being drawn as anything, since what it means is a choice
    // the "Bump as normal (MGE)" checkbox makes, not this flag.
    var anyBumpMaps = false;
    // Whether anything is collision-only geometry -- physics shapes a
    // RootCollisionNode carries that the game never draws. Same reasoning:
    // no point offering a toggle that would always be a no-op.
    var anyCollision = false;

    // Injects dark, detail, gloss and decal straight into MeshPhongMaterial's
    // own fragment shader, string-patched at the two chunks that already do
    // the equivalent work for the base map and the specular map. Kept to
    // three.js's built-in Phong lighting rather than a shader written from
    // scratch -- the risk in a hand-rolled lighting model is getting the
    // lighting wrong, and nothing here needs to touch it, only what feeds it.
    //
    // Checked live, not baked in at attach time: a material only recompiles
    // when something sets needsUpdate, so every checkbox below that toggles
    // one of these sets it on the meshes it affects, and the next compile
    // reads whatever is checked *then*. Whether a given layer is even a
    // candidate is decided once, up front (only mount the ones the mesh
    // actually has); whether it is currently drawn is decided every compile.
__EXTRA_SLOTS__
    // Which layers the *mesh viewer* draws: one checkbox per slot. Passed in
    // rather than read inside the shared helper, because the texture
    // comparison has entirely different controls over the same slots.
    function wantsSlot(slot) {
      var box = {detail: typeof detailBox !== "undefined" ? detailBox : null,
                 dark: typeof darkBox !== "undefined" ? darkBox : null,
                 gloss: typeof glossBox !== "undefined" ? glossBox : null,
                 decal: typeof decalBox !== "undefined" ? decalBox : null}[slot];
      return !!(box && box.checked);
    }

    scenes.forEach(function (spec, index) {
      // The inner group carries the Z-up to Y-up rotation; the outer pivot
      // carries the centring. They cannot be the same object: three.js
      // composes T*R*S, so a position set from a centre measured before the
      // rotation leaves the mesh at R*v - centre rather than R*(v - centre).
      var group = new THREE.Group();
      group.userData.shapes = [];
      spec.meshes.forEach(function (m) {
        var g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(m.positions, 3));
        g.setIndex(new THREE.BufferAttribute(m.indices, 1));
        if (m.uvs) g.setAttribute("uv", new THREE.BufferAttribute(m.uvs, 2));
        // Vertex colors are three floats each, already 0-1 in the file. They
        // are only attached when the count matches, which geometry.py has
        // already enforced -- a short set makes three.js index past the end of
        // the attribute and draw nothing at all.
        if (m.colors) g.setAttribute("color", new THREE.BufferAttribute(m.colors, 3));
        // The files carry normals, but not always, and a mesh with none
        // renders flat black. Computing them is cheap and always right.
        g.computeVertexNormals();
        var material = new THREE.MeshPhongMaterial({
          color: spec.color, side: THREE.DoubleSide
        });
        // What the file itself says about this shape's material. Held on the
        // mesh rather than applied blindly, because every one of these is
        // something a control below can override -- and a control that
        // overrides a *known* value is far more useful than one that
        // overrides a guess, which is what these were before the reader
        // carried them.
        var fromFile = {
          diffuse: m.diffuse ? new THREE.Color(m.diffuse[0], m.diffuse[1], m.diffuse[2]) : null,
          emissive: m.emissive
            ? new THREE.Color(m.emissive[0], m.emissive[1], m.emissive[2]) : null,
          opacity: typeof m.opacity === "number" ? m.opacity : 1.0,
          blend: !!m.alphaBlend,
          test: !!m.alphaTest,
          // A threshold of zero with testing on discards nothing, which is
          // indistinguishable from testing being off. Morrowind's own default
          // reference is what the file stores; fall back only when it is
          // absent entirely.
          threshold: typeof m.alphaThreshold === "number" ? m.alphaThreshold : 0.5
        };
        material.userData.fromFile = fromFile;
        if (m.colors) material.vertexColors = true;
        // Emissive is the material's own glow color, and it combines with the
        // glow *map* by multiplication -- so setting it here is correct
        // whether or not a glow texture also arrives.
        if (fromFile.emissive) material.emissive = fromFile.emissive;
        if (fromFile.blend) { material.transparent = true; material.opacity = fromFile.opacity; }
        if (fromFile.test) material.alphaTest = fromFile.threshold;
        if (m.image && m.uvs && textured) {
          var image = new Image();
          var tex = new THREE.Texture(image);
          tex.colorSpace = THREE.SRGBColorSpace;
          tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
          // The image arrives after the material is built, so the texture has
          // to be marked dirty and a frame drawn once it lands. Without the
          // redraw the mesh stays flat-colored until something else happens
          // to trigger one, which looks exactly like a failure to load.
          image.onload = function () { tex.needsUpdate = true; draw(); };
          image.src = m.image.url;
          material.map = tex;
          // Tinting a texture with the side color would make the two
          // providers look different for a reason that is not in the file.
          material.color = new THREE.Color(0xffffff);
        }
        // An OpenMW-style normal map, found beside the diffuse one by name.
        // Not color: it is loaded in linear space, because treating a field
        // of vectors as sRGB bends every one of them.
        var extras = m.extras || {};
        var normalSource = extras["_nh"] || extras["_n"] || null;
        var normalTex = null;
        if (normalSource && m.uvs && textured) {
          var nImage = new Image();
          normalTex = new THREE.Texture(nImage);
          normalTex.wrapS = normalTex.wrapT = THREE.RepeatWrapping;
          nImage.onload = function () { normalTex.needsUpdate = true; draw(); };
          nImage.src = normalSource.url;
        }
        // The bump slot, read as tangent-space normals -- the MGE-XE/NifSkope
        // convention, not vanilla's (which ignores the slot outright) and not
        // necessarily OpenMW's. Loaded regardless of which convention is
        // in force; the "Bump as normal (MGE)" checkbox decides whether it
        // is ever attached to a material, and prefers an OpenMW-style
        // sibling when a mesh happens to carry both rather than fight over
        // which wins.
        var bumpTex = null;
        if (m.bump && m.uvs && textured) {
          var buImage = new Image();
          bumpTex = new THREE.Texture(buImage);
          bumpTex.wrapS = bumpTex.wrapT = THREE.RepeatWrapping;
          buImage.onload = function () { bumpTex.needsUpdate = true; draw(); };
          buImage.src = m.bump.url;
        }
        // Same OpenMW naming convention, for a specular map. Also linear:
        // it modulates highlight strength, not a color to be seen directly.
        var specSource = extras["_spec"] || extras["_diffusespec"] || null;
        var specTex = null;
        if (specSource && m.uvs && textured) {
          var sImage = new Image();
          specTex = new THREE.Texture(sImage);
          sImage.onload = function () { specTex.needsUpdate = true; draw(); };
          sImage.src = specSource.url;
          // MeshPhongMaterial's default specular color is a dim 0x111111,
          // dim enough that a specular map barely shows against it. The
          // brighter value belongs *with* the map and is applied by the
          // control, not here.
          //
          // Setting it at construction was the earlier approach and made the
          // control read backwards: with the box unchecked the shape still
          // got the bright highlight, unmodulated across its whole surface,
          // which looks like specular is on. Ticking the box then attached
          // the map and *darkened* it wherever the map was dark -- so "on"
          // looked duller than "off". The logic was right and the appearance
          // was inverted, which is the harder kind to spot.
        }
        // The glow slot, unlike the two above, is not a filename guess -- the
        // shape names it directly, and it is what Morrowind's own renderer
        // (not just OpenMW) uses for self-illumination: lit windows, lava,
        // glowing eyes. sRGB, like the diffuse map: it is a color being
        // added to the surface, not a vector or a scalar mask.
        var glowTex = null;
        if (m.glow && m.uvs && textured) {
          var gImage = new Image();
          glowTex = new THREE.Texture(gImage);
          glowTex.colorSpace = THREE.SRGBColorSpace;
          glowTex.wrapS = glowTex.wrapT = THREE.RepeatWrapping;
          gImage.onload = function () { glowTex.needsUpdate = true; draw(); };
          gImage.src = m.glow.url;
        }
        // The dark and detail slots both multiply into the base color --
        // Morrowind applies detail first, then dark -- and gloss modulates
        // specular strength by a mask rather than supplying a specular
        // color. None of the three is a color meant to be seen on its own,
        // so none is sRGB, the same reasoning as the normal and specular
        // maps above. A decal is different again: a layer stamped on top,
        // meant to be seen, so it stays sRGB like the base texture.
        //
        // MeshPhongMaterial has exactly one slot shaped like "multiply the
        // surface by a texture" (aoMap) and exactly one shaped like
        // "modulate specular by a texture" (specularMap) -- and specularMap
        // is already spoken for by an OpenMW-style _spec sibling. Cramming
        // dark and detail into the one multiply slot would mean whichever
        // assigned second silently overwrote the first. Rather than pick a
        // loser, all four slots below are drawn through a shared
        // onBeforeCompile hook (attachExtraSlots, defined once outside this
        // loop) that injects them straight into Phong's own fragment shader
        // -- real per-mesh layers, not a shared property fighting over who
        // gets to hold it.
        var detailTex = null;
        if (m.detail && m.uvs && textured) {
          var deImage = new Image();
          detailTex = new THREE.Texture(deImage);
          detailTex.wrapS = detailTex.wrapT = THREE.RepeatWrapping;
          deImage.onload = function () { detailTex.needsUpdate = true; draw(); };
          deImage.src = m.detail.url;
        }
        var darkTex = null;
        if (m.dark && m.uvs && textured) {
          var dImage = new Image();
          darkTex = new THREE.Texture(dImage);
          darkTex.wrapS = darkTex.wrapT = THREE.RepeatWrapping;
          dImage.onload = function () { darkTex.needsUpdate = true; draw(); };
          dImage.src = m.dark.url;
        }
        var glossTex = null;
        if (m.gloss && m.uvs && textured) {
          var glImage = new Image();
          glossTex = new THREE.Texture(glImage);
          glossTex.wrapS = glossTex.wrapT = THREE.RepeatWrapping;
          glImage.onload = function () { glossTex.needsUpdate = true; draw(); };
          glImage.src = m.gloss.url;
        }
        // Every decal the shape declares, in slot order. Kept as an array
        // rather than one texture because slot order *is* paint order: they
        // composite over one another, and the last declared is the one on top.
        var decalTexes = [];
        if (m.decals && m.decals.length && m.uvs && textured) {
          m.decals.forEach(function (slot) {
            var declImage = new Image();
            var tex = new THREE.Texture(declImage);
            tex.colorSpace = THREE.SRGBColorSpace;
            tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
            declImage.onload = function () { tex.needsUpdate = true; draw(); };
            declImage.src = slot.url;
            decalTexes.push(tex);
          });
        }
        var drawn = new THREE.Mesh(g, material);
        drawn.userData.map = material.map || null;
        drawn.userData.normalMap = normalTex;
        drawn.userData.bumpMap = bumpTex;
        drawn.userData.specularMap = specTex;
        drawn.userData.glowMap = glowTex;
        drawn.userData.darkMap = darkTex;
        drawn.userData.detailMap = detailTex;
        drawn.userData.glossMap = glossTex;
        drawn.userData.decalMaps = decalTexes;
        drawn.userData.tint = spec.color;
        drawn.userData.collision = !!m.collision;
        attachExtraSlots(material, drawn, wantsSlot);
        if (normalTex) anyNormalMaps = true;
        if (bumpTex) anyBumpMaps = true;
        if (specTex) anySpecularMaps = true;
        if (glowTex) anyGlowMaps = true;
        if (darkTex) anyDarkMaps = true;
        if (detailTex) anyDetailMaps = true;
        if (glossTex) anyGlossMaps = true;
        if (decalTexes.length) anyDecalMaps = true;
        if (m.collision) anyCollision = true;
        // Kept alongside the object so the shape list below can reach both:
        // the three.js mesh to hide, and the payload to describe. Matching
        // them up later by name would be guesswork -- shape names repeat
        // freely within one file, and several vanilla meshes have none.
        group.userData.shapes.push({object: drawn, spec: m});
        group.add(drawn);
      });
      group.rotation.x = -Math.PI / 2;
      group.visible = index === 0;
      pivot.add(group);
      groups.push(group);
    });

    pivot.updateMatrixWorld(true);
    // Framed over *every* provider, visible or not, so toggling never moves
    // the camera. A comparison where the view shifts as you switch is a
    // comparison of two different pictures.
    var box = new THREE.Box3();
    groups.forEach(function (g) { box.expandByObject(g); });
    var centre = box.getCenter(new THREE.Vector3());
    var radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-3);
    if (!isFinite(radius) || box.isEmpty()) { centre.set(0, 0, 0); radius = 1; }
    pivot.position.copy(centre).negate();
    pivot.updateMatrixWorld(true);

    // Lighting is not decoration here. A normal map changes nothing at all
    // under flat ambient light -- the whole point of one is how it catches a
    // light that moves -- so these are the controls that make a normal map
    // comparison possible.
    var ambient = new THREE.AmbientLight(0xffffff, 0.55);
    scene.add(ambient);
    var key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(1, 1.4, 1);
    scene.add(key);
    var fill = new THREE.DirectionalLight(0xffffff, 0.5);
    fill.position.set(-1, -0.6, -0.8);
    scene.add(fill);

    var lightState = {ambient: 0.55, key: 1.6, angle: 0.0, headlamp: false};

    function placeLights() {
      ambient.intensity = lightState.ambient;
      key.intensity = lightState.key;
      fill.intensity = lightState.key * 0.31;
      if (lightState.headlamp) {
        // From the camera's own position, so a surface is always lit from
        // wherever you are looking. Useful for reading a normal map's detail,
        // and deliberately *not* the default: it means moving the camera
        // changes the lighting, so two providers can never be compared under
        // identical light while it is on.
        key.position.copy(camera.position);
        fill.position.copy(camera.position).negate();
        return;
      }
      var a = lightState.angle;
      key.position.set(Math.sin(a), 1.4, Math.cos(a));
      fill.position.set(-Math.sin(a), -0.6, -Math.cos(a));
    }

    var treeBox = document.getElementById("tree");

    function renderTree(nodes) {
      var ul = document.createElement("ul");
      nodes.forEach(function (node) {
        var li = document.createElement("li");
        li.setAttribute("data-block-index", node.index);
        var index = document.createElement("span");
        index.className = "ix"; index.textContent = node.index;
        var type = document.createElement("span");
        type.className = "ty"; type.textContent = node.type;
        li.appendChild(index); li.appendChild(type);
        if (node.name) {
          var nm = document.createElement("span");
          nm.className = "nm"; nm.textContent = " " + node.name;
          li.appendChild(nm);
        }
        if (node.note) {
          var no = document.createElement("span");
          no.className = "no"; no.textContent = "  " + node.note;
          li.appendChild(no);
        }
        if (node.children && node.children.length) li.appendChild(renderTree(node.children));
        ul.appendChild(li);
      });
      return ul;
    }

    // What the *file* says this shape is made of, as a sentence rather than
    // as controls. A mesh routinely has twenty shapes, and a checkbox per
    // material property per shape would be a hundred controls answering a
    // question nobody asks -- while "which shape wants a cutout" is answered
    // perfectly well by reading it.
    function summaryOf(m) {
      var parts = [];
      parts.push(m.triangleCount + " tri");
      if (m.collision) parts.push("collision");
      if (!m.uvs) parts.push("no UVs");
      if (m.colors) parts.push("vertex colors");
      if (m.alphaBlend) parts.push("blend @ " + (m.opacity !== undefined
        ? Math.round(m.opacity * 100) + "%" : "?"));
      // A cutout's reference matters -- 0 discards nothing, which is the same
      // as the flag being off -- so it is shown rather than just named.
      if (m.alphaTest) parts.push("cutout @ " + Math.round((m.alphaThreshold || 0) * 255));
      var maps = [];
      if (m.image) maps.push("base");
      if (m.glow) maps.push("glow");
      if (m.dark) maps.push("dark");
      if (m.detail) maps.push("detail");
      if (m.gloss) maps.push("gloss");
      if (m.decals && m.decals.length) {
        maps.push(m.decals.length > 1 ? "decal x" + m.decals.length : "decal");
      }
      if (m.bump) maps.push("bump");
      if (maps.length) parts.push(maps.join("+"));
      return parts.join(", ");
    }

    // Per-shape visibility. Isolating one shape is the control that earns its
    // place: when two providers' meshes differ it is almost always in one
    // sub-shape, and with everything drawn at once you can see *that*
    // something moved without seeing *what*.
    function renderShapes(group, spec) {
      var ul = document.createElement("ul");
      ul.className = "shapes";
      group.userData.shapes.forEach(function (entry, i) {
        var li = document.createElement("li");
        var box = document.createElement("input");
        box.type = "checkbox";
        box.checked = entry.object.visible;
        box.addEventListener("change", function () {
          entry.object.visible = box.checked;
          draw();
        });
        var name = document.createElement("span");
        name.className = "nm shapename";
        name.textContent = entry.spec.name || "(unnamed " + i + ")";
        name.title = "click to isolate; click again to restore";
        // Solo, and solo again to restore. A dedicated "show all" button
        // would be a second control for something the first one can say.
        name.addEventListener("click", function () {
          var soloed = group.userData.shapes.every(function (o) {
            return o === entry ? o.object.visible : !o.object.visible;
          });
          group.userData.shapes.forEach(function (o) {
            o.object.visible = soloed || o === entry;
          });
          refreshTree();
          draw();
        });
        var note = document.createElement("span");
        note.className = "no";
        note.textContent = "  " + summaryOf(entry.spec);
        li.appendChild(box); li.appendChild(name); li.appendChild(note);
        ul.appendChild(li);
      });
      return ul;
    }

    function refreshTree() {
      treeBox.textContent = "";
      var any = false;
      scenes.forEach(function (spec, i) {
        if (!groups[i].visible) return;
        var shapes = groups[i].userData.shapes || [];
        if (!shapes.length && (!spec.tree || !spec.tree.length)) return;
        any = true;
        var heading = document.createElement("h4");
        heading.textContent = spec.label;
        heading.style.color = spec.color;
        treeBox.appendChild(heading);
        if (shapes.length) {
          var sub = document.createElement("h4");
          sub.textContent = "shapes (" + shapes.length + ")";
          sub.style.opacity = ".7";
          treeBox.appendChild(sub);
          treeBox.appendChild(renderShapes(groups[i], spec));
        }
        if (spec.tree && spec.tree.length) {
          var blocks = document.createElement("h4");
          blocks.textContent = "blocks";
          blocks.style.opacity = ".7";
          treeBox.appendChild(blocks);
          treeBox.appendChild(renderTree(spec.tree));
        }
      });
      if (!any) {
        var note = document.createElement("div");
        note.className = "no";
        note.textContent = "no structure to show";
        treeBox.appendChild(note);
      }
    }

    var statsBox = document.getElementById("stats");
    function refresh() {
      var shapes = 0, tris = 0, verts = 0, shown = 0;
      scenes.forEach(function (spec, i) {
        if (!groups[i].visible) return;
        shown++;
        spec.meshes.forEach(function (m) {
          shapes++; tris += m.triangleCount; verts += m.vertexCount;
        });
      });
      statsBox.textContent = shown
        ? shapes + " shape(s), " + tris + " triangles, " + verts + " vertices"
        : "nothing shown";
      Array.prototype.forEach.call(controls.querySelectorAll(".ctl"), function (el, i) {
        if (i < groups.length) el.className = "ctl" + (groups[i].visible ? "" : " off");
      });
      refreshTree();
      draw();
    }

    scenes.forEach(function (spec, index) {
      var id = "side" + index;
      var span = document.createElement("span");
      span.className = "ctl" + (index === 0 ? "" : " off");
      var box2 = document.createElement("input");
      box2.type = "checkbox"; box2.id = id; box2.checked = index === 0;
      var label = document.createElement("label");
      label.htmlFor = id;
      var swatch = document.createElement("span");
      swatch.className = "swatch"; swatch.style.background = spec.color;
      label.appendChild(swatch);
      label.appendChild(document.createTextNode(" " + spec.label));
      span.appendChild(box2); span.appendChild(label);
      controls.appendChild(span);
      box2.addEventListener("change", function () {
        groups[index].visible = box2.checked;
        refresh();
      });
    });

    var textureBox = document.createElement("input");
    textureBox.type = "checkbox"; textureBox.id = "textured"; textureBox.checked = true;
    var textureCtl = document.createElement("span");
    textureCtl.className = "ctl";
    var textureLabel = document.createElement("label");
    textureLabel.htmlFor = "textured";
    textureLabel.textContent = "Textures";
    textureCtl.appendChild(textureBox); textureCtl.appendChild(textureLabel);
    controls.appendChild(textureCtl);
    textureBox.addEventListener("change", function () {
      // Flat color is often the better comparison: two versions of a mesh
      // wearing the same texture differ in shape, and the texture hides it.
      scene.traverse(function (o) {
        if (!o.isMesh || !o.userData.map) return;
        o.material.map = textureBox.checked ? o.userData.map : null;
        o.material.color = new THREE.Color(textureBox.checked ? 0xffffff : o.userData.tint);
        o.material.needsUpdate = true;
      });
      textureCtl.className = "ctl" + (textureBox.checked ? "" : " off");
      draw();
    });

    var alphaBox = document.createElement("input");
    alphaBox.type = "checkbox"; alphaBox.id = "alphacut";
    var alphaCtl = document.createElement("span");
    alphaCtl.className = "ctl off";
    var alphaLabel = document.createElement("label");
    alphaLabel.htmlFor = "alphacut";
    alphaLabel.textContent = "Alpha cutout";
    alphaCtl.appendChild(alphaBox); alphaCtl.appendChild(alphaLabel);
    controls.appendChild(alphaCtl);
    alphaBox.addEventListener("change", function () {
      // A cutout texture (grass, a fence, a leaf) has a quad behind it that
      // otherwise renders fully opaque wherever the mesh exists, regardless
      // of what the alpha channel says -- Phong's default ignores it
      // entirely. alphaTest discards those fragments outright rather than
      // blending them, which is what a cutout wants: blending needs the
      // triangles sorted back-to-front to look right, and two overlapping
      // cutout quads sorted wrong show through each other. A hard cutoff has
      // no ordering to get wrong.
      //
      // Checked forces a cutout on every shape; unchecked returns each to
      // whatever its own NiAlphaProperty asked for, at that shape's own
      // reference value. Before the reader carried those, "unchecked" meant
      // "off everywhere" -- which silently overrode files that had asked for
      // a cutout and got none.
      scene.traverse(function (o) {
        if (!o.isMesh || !o.userData.map) return;
        var own = o.material.userData.fromFile;
        o.material.alphaTest = alphaBox.checked
          ? 0.5
          : (own && own.test ? own.threshold : 0);
        o.material.needsUpdate = true;
      });
      alphaCtl.className = "ctl" + (alphaBox.checked ? "" : " off");
      draw();
    });

    var blendBox = document.createElement("input");
    blendBox.type = "checkbox"; blendBox.id = "alphablend";
    var blendCtl = document.createElement("span");
    blendCtl.className = "ctl off";
    var blendLabel = document.createElement("label");
    blendLabel.htmlFor = "alphablend";
    blendLabel.textContent = "Alpha blend";
    blendCtl.appendChild(blendBox); blendCtl.appendChild(blendLabel);
    controls.appendChild(blendCtl);
    blendBox.addEventListener("change", function () {
      // Alpha cutout is a mask, either in or out -- right for grass and
      // fences, wrong for genuine translucency like glass, water or a ghost,
      // where alpha sits meaningfully between 0 and 1 and a 0.5 cutoff would
      // just round it to fully-opaque-or-fully-gone. This blends it properly
      // instead. The trade this time is the one alphaTest was chosen to
      // avoid above: three.js sorts whole objects back-to-front by distance
      // to the camera, but never the triangles within a single one, so a
      // mesh with self-overlapping transparent geometry -- a complex glass
      // shape folded back on itself -- can still show the wrong surface on
      // top. Independent of the cutout checkbox; a mesh can want both at
      // once (a cutout leaf with softened edges), so neither toggle turns
      // the other off.
      //
      // As with the cutout: checked forces blending everywhere, unchecked
      // returns each shape to what its own file asked for -- including the
      // material's own alpha value, which is *how* transparent as distinct
      // from *whether* it blends at all.
      scene.traverse(function (o) {
        if (!o.isMesh || !o.userData.map) return;
        var own = o.material.userData.fromFile;
        var blend = blendBox.checked || !!(own && own.blend);
        o.material.transparent = blend;
        o.material.opacity = blend && own ? own.opacity : 1.0;
        o.material.needsUpdate = true;
      });
      blendCtl.className = "ctl" + (blendBox.checked ? "" : " off");
      draw();
    });

    function addSlider(id, label, min, max, value, step, onInput) {
      var ctl = document.createElement("span");
      ctl.className = "ctl";
      var text = document.createElement("label");
      text.htmlFor = id; text.textContent = label;
      var range = document.createElement("input");
      range.type = "range"; range.id = id;
      range.min = min; range.max = max; range.step = step; range.value = value;
      range.addEventListener("input", function () {
        onInput(parseFloat(range.value));
        placeLights();
        draw();
      });
      ctl.appendChild(text); ctl.appendChild(range);
      controls.appendChild(ctl);
      return range;
    }

    addSlider("lightkey", "Light", 0, 4, lightState.key, 0.05,
      function (v) { lightState.key = v; });
    addSlider("lightamb", "Ambient", 0, 2, lightState.ambient, 0.05,
      function (v) { lightState.ambient = v; });
    var angleRange = addSlider("lightang", "Angle", 0, 6.2832, lightState.angle, 0.02,
      function (v) { lightState.angle = v; });

    var lampBox = document.createElement("input");
    lampBox.type = "checkbox"; lampBox.id = "headlamp";
    var lampCtl = document.createElement("span");
    lampCtl.className = "ctl off";
    var lampLabel = document.createElement("label");
    lampLabel.htmlFor = "headlamp";
    lampLabel.textContent = "Follow camera";
    lampCtl.appendChild(lampBox); lampCtl.appendChild(lampLabel);
    controls.appendChild(lampCtl);
    lampBox.addEventListener("change", function () {
      lightState.headlamp = lampBox.checked;
      // The fixed-angle slider means nothing while the light tracks the
      // camera, so it is disabled rather than left to look operative.
      angleRange.disabled = lampBox.checked;
      lampCtl.className = "ctl" + (lampBox.checked ? "" : " off");
      placeLights();
      draw();
    });

    // Only offered when the collection actually ships one. A permanently
    // dead control implies the feature is broken rather than unused.
    if (anyNormalMaps) {
      var normalBox = document.createElement("input");
      normalBox.type = "checkbox"; normalBox.id = "normals";
      var normalCtl = document.createElement("span");
      normalCtl.className = "ctl off";
      var normalLabel = document.createElement("label");
      normalLabel.htmlFor = "normals";
      normalLabel.textContent = "Normal maps";
      normalCtl.appendChild(normalBox); normalCtl.appendChild(normalLabel);
      controls.appendChild(normalCtl);
      normalBox.addEventListener("change", function () {
        scene.traverse(function (o) {
          if (!o.isMesh || !o.userData.normalMap) return;
          o.material.normalMap = normalBox.checked ? o.userData.normalMap : null;
          o.material.needsUpdate = true;
        });
        normalCtl.className = "ctl" + (normalBox.checked ? "" : " off");
        draw();
      });
    }

    if (anySpecularMaps) {
      var specBox = document.createElement("input");
      specBox.type = "checkbox"; specBox.id = "specular";
      var specCtl = document.createElement("span");
      specCtl.className = "ctl off";
      var specLabel = document.createElement("label");
      specLabel.htmlFor = "specular";
      specLabel.textContent = "Specular maps";
      specCtl.appendChild(specBox); specCtl.appendChild(specLabel);
      controls.appendChild(specCtl);
      specBox.addEventListener("change", function () {
        scene.traverse(function (o) {
          if (!o.isMesh || !o.userData.specularMap) return;
          o.material.specularMap = specBox.checked ? o.userData.specularMap : null;
          // The highlight color moves with the map. A specular map modulates
          // the specular color, so a bright color with no map is a highlight
          // over the whole surface -- the state that made this control look
          // inverted. 0x111111 is three.js's own default, i.e. what the
          // material would have had if no specular map had ever been found.
          o.material.specular = new THREE.Color(specBox.checked ? 0x808080 : 0x111111);
          o.material.needsUpdate = true;
        });
        specCtl.className = "ctl" + (specBox.checked ? "" : " off");
        draw();
      });
    }

    if (anyGlowMaps) {
      var glowBox = document.createElement("input");
      glowBox.type = "checkbox"; glowBox.id = "glow";
      var glowCtl = document.createElement("span");
      glowCtl.className = "ctl off";
      var glowLabel = document.createElement("label");
      glowLabel.htmlFor = "glow";
      glowLabel.textContent = "Glow maps";
      glowCtl.appendChild(glowBox); glowCtl.appendChild(glowLabel);
      controls.appendChild(glowCtl);
      glowBox.addEventListener("change", function () {
        // Off by default like every other optional map here, so the
        // starting view is the plain textured mesh and every extra is
        // something you switch on rather than something you notice you
        // have to switch off.
        scene.traverse(function (o) {
          if (!o.isMesh || !o.userData.glowMap) return;
          o.material.emissiveMap = glowBox.checked ? o.userData.glowMap : null;
          o.material.emissive = glowBox.checked ? new THREE.Color(0xffffff) : new THREE.Color(0x000000);
          o.material.needsUpdate = true;
        });
        glowCtl.className = "ctl" + (glowBox.checked ? "" : " off");
        draw();
      });
    }

    if (anyDarkMaps) {
      var darkBox = document.createElement("input");
      darkBox.type = "checkbox"; darkBox.id = "dark";
      var darkCtl = document.createElement("span");
      darkCtl.className = "ctl off";
      var darkLabel = document.createElement("label");
      darkLabel.htmlFor = "dark";
      darkLabel.textContent = "Dark maps";
      darkCtl.appendChild(darkBox); darkCtl.appendChild(darkLabel);
      controls.appendChild(darkCtl);
      darkBox.addEventListener("change", function () {
        // Nothing here sets a material property directly -- attachExtraSlots
        // reads darkBox.checked itself the next time the shader compiles,
        // which needsUpdate is what triggers.
        scene.traverse(function (o) {
          if (o.isMesh && o.userData.darkMap) o.material.needsUpdate = true;
        });
        darkCtl.className = "ctl" + (darkBox.checked ? "" : " off");
        draw();
      });
    }

    if (anyDetailMaps) {
      var detailBox = document.createElement("input");
      detailBox.type = "checkbox"; detailBox.id = "detail";
      var detailCtl = document.createElement("span");
      detailCtl.className = "ctl off";
      var detailLabel = document.createElement("label");
      detailLabel.htmlFor = "detail";
      detailLabel.textContent = "Detail maps";
      detailCtl.appendChild(detailBox); detailCtl.appendChild(detailLabel);
      controls.appendChild(detailCtl);
      detailBox.addEventListener("change", function () {
        scene.traverse(function (o) {
          if (o.isMesh && o.userData.detailMap) o.material.needsUpdate = true;
        });
        detailCtl.className = "ctl" + (detailBox.checked ? "" : " off");
        draw();
      });
    }

    if (anyGlossMaps) {
      var glossBox = document.createElement("input");
      glossBox.type = "checkbox"; glossBox.id = "gloss";
      var glossCtl = document.createElement("span");
      glossCtl.className = "ctl off";
      var glossLabel = document.createElement("label");
      glossLabel.htmlFor = "gloss";
      glossLabel.textContent = "Gloss maps";
      glossCtl.appendChild(glossBox); glossCtl.appendChild(glossLabel);
      controls.appendChild(glossCtl);
      glossBox.addEventListener("change", function () {
        scene.traverse(function (o) {
          if (o.isMesh && o.userData.glossMap) o.material.needsUpdate = true;
        });
        glossCtl.className = "ctl" + (glossBox.checked ? "" : " off");
        draw();
      });
    }

    if (anyDecalMaps) {
      var decalBox = document.createElement("input");
      decalBox.type = "checkbox"; decalBox.id = "decal";
      var decalCtl = document.createElement("span");
      decalCtl.className = "ctl off";
      var decalLabel = document.createElement("label");
      decalLabel.htmlFor = "decal";
      decalLabel.textContent = "Decal maps";
      decalCtl.appendChild(decalBox); decalCtl.appendChild(decalLabel);
      controls.appendChild(decalCtl);
      decalBox.addEventListener("change", function () {
        scene.traverse(function (o) {
          if (o.isMesh && o.userData.decalMaps && o.userData.decalMaps.length) {
            o.material.needsUpdate = true;
          }
        });
        decalCtl.className = "ctl" + (decalBox.checked ? "" : " off");
        draw();
      });
    }

    if (anyBumpMaps) {
      var bumpBox = document.createElement("input");
      bumpBox.type = "checkbox"; bumpBox.id = "bump";
      var bumpCtl = document.createElement("span");
      bumpCtl.className = "ctl off";
      var bumpLabel = document.createElement("label");
      bumpLabel.htmlFor = "bump";
      bumpLabel.textContent = "Bump as normal (MGE)";
      bumpCtl.appendChild(bumpBox); bumpCtl.appendChild(bumpLabel);
      controls.appendChild(bumpCtl);
      bumpBox.addEventListener("change", function () {
        // Unlike the four above, this is a real MeshPhongMaterial property
        // (normalMap), not a shader injection -- three.js already supports
        // it correctly, including the tangent-space lighting math, so there
        // is nothing to patch. An OpenMW-style _n/_nh sibling still wins
        // when a mesh happens to carry both, rather than the two fighting
        // over which is current: MGE-converted content and OpenMW-authored
        // normal maps are different eras of the same idea, not a pair meant
        // to be layered.
        scene.traverse(function (o) {
          if (!o.isMesh || !o.userData.bumpMap) return;
          if (o.userData.normalMap) return;
          o.material.normalMap = bumpBox.checked ? o.userData.bumpMap : null;
          o.material.needsUpdate = true;
        });
        bumpCtl.className = "ctl" + (bumpBox.checked ? "" : " off");
        draw();
      });
    }

    // Only offered when the collection actually has collision geometry, for
    // the same reason as the normal-map control above.
    if (anyCollision) {
      var collisionBox = document.createElement("input");
      collisionBox.type = "checkbox"; collisionBox.id = "collision"; collisionBox.checked = true;
      var collisionCtl = document.createElement("span");
      collisionCtl.className = "ctl";
      var collisionLabel = document.createElement("label");
      collisionLabel.htmlFor = "collision";
      collisionLabel.textContent = "Collision shapes";
      collisionCtl.appendChild(collisionBox); collisionCtl.appendChild(collisionLabel);
      controls.appendChild(collisionCtl);
      collisionBox.addEventListener("change", function () {
        // Collision geometry is never drawn in game, so hiding it here only
        // turns off its render -- the mesh itself stays in the scene graph.
        // That is deliberate: the bounding box was measured once at load
        // from every mesh regardless of visibility, and the stats count
        // every mesh in a visible side the same way, so hiding a collision
        // shape does not shrink the framing or the numbers, only what is
        // drawn -- it stays present, just invisible.
        scene.traverse(function (o) {
          if (!o.isMesh || !o.userData.collision) return;
          o.visible = collisionBox.checked;
        });
        collisionCtl.className = "ctl" + (collisionBox.checked ? "" : " off");
        draw();
      });
    }

    var spacer = document.createElement("span");
    spacer.className = "spacer";
    controls.appendChild(spacer);
    [["Show all", function () { groups.forEach(function (g) { g.visible = true; }); }],
     ["Reset view", function () {
       yaw = 0.6; pitch = 0.5; distance = radius * 3; target.set(0, 0, 0);
     }]
    ].forEach(function (pair) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = pair[0];
      button.addEventListener("click", function () {
        pair[1]();
        Array.prototype.forEach.call(controls.querySelectorAll("input"), function (cb, i) {
          if (i < groups.length) cb.checked = groups[i].visible;
        });
        place(); refresh();
      });
      controls.appendChild(button);
    });

    var yaw = 0.6, pitch = 0.5, distance = radius * 3;
    // What the camera orbits and looks at. Panning moves this rather than the
    // camera directly, so zoom and orbit keep working exactly as before --
    // they are still defined relative to a point, just one that is no longer
    // pinned to the mesh's centre.
    var target = new THREE.Vector3(0, 0, 0);
    function place() {
      camera.position.set(
        target.x + distance * Math.cos(pitch) * Math.sin(yaw),
        target.y + distance * Math.sin(pitch),
        target.z + distance * Math.cos(pitch) * Math.cos(yaw));
      camera.lookAt(target);
      // In headlamp mode the light rides the camera, so it has to move here
      // rather than only when a slider changes.
      placeLights();
    }
    function draw() { renderer.render(scene, camera); }
    function resize() {
      var w = stage.clientWidth, h = stage.clientHeight;
      renderer.setSize(w, h, false);
      camera.aspect = w / Math.max(h, 1);
      camera.updateProjectionMatrix();
    }
    var dragging = false, dragMode = "orbit", lastX = 0, lastY = 0;
    // A right-drag must not also pop up the browser's context menu.
    renderer.domElement.addEventListener("contextmenu", function (e) { e.preventDefault(); });
    renderer.domElement.addEventListener("mousedown", function (e) {
      dragging = true; lastX = e.clientX; lastY = e.clientY;
      dragMode = (e.button === 2 || e.shiftKey) ? "pan" : "orbit";
    });
    window.addEventListener("mouseup", function () { dragging = false; });
    window.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      var dx = e.clientX - lastX, dy = e.clientY - lastY;
      if (dragMode === "pan") {
        // Move the point the camera looks at across the camera's own
        // right/up axes rather than the world's, so panning "up" always
        // means up on screen whatever angle the mesh is being viewed from.
        // Scaled by distance so a drag covers the same apparent screen
        // distance whether zoomed in on a buckle or zoomed out on a whole
        // building -- a fixed world-space step would crawl at one zoom and
        // fly past the mesh at another.
        var panScale = distance * 0.0015;
        var rightAxis = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0);
        var upAxis = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 1);
        target.addScaledVector(rightAxis, -dx * panScale);
        target.addScaledVector(upAxis, dy * panScale);
      } else {
        yaw -= dx * 0.01;
        pitch += dy * 0.01;
        // Stop short of the poles: at exactly +/-90 degrees the look-at up
        // vector is parallel to the view and the image flips.
        pitch = Math.max(-1.55, Math.min(1.55, pitch));
      }
      lastX = e.clientX; lastY = e.clientY;
      place(); draw();
    });
    renderer.domElement.addEventListener("wheel", function (e) {
      e.preventDefault();
      distance *= (e.deltaY > 0) ? 1.1 : 0.9;
      distance = Math.max(radius * 0.05, Math.min(radius * 60, distance));
      place(); draw();
    }, {passive: false});
    window.addEventListener("resize", function () { resize(); draw(); });
    resize(); place(); refresh();
  }
})();
</script>
__EDIT_JS__
</body></html>
"""


#: The editor overlay, injected only for an editable view. Kept out of
#: :data:`_PAGE` and run as its own script so the large, read-only viewer is not
#: touched by it: it works off the DOM the viewer already built (the block tree's
#: ``li[data-block-index]`` rows) and a single ``EDIT`` payload, so a
#: non-editable page carries none of this and behaves exactly as before. It
#: collects field edits, POSTs them to the server's apply handler, and saves the
#: returned file. A **raw** string for the same reason :data:`_PAGE` is.
_EDITOR_JS: Final[str] = r"""<script>
(function () {
  var EDIT = __EDIT_DATA__;
  if (!EDIT || !EDIT.url) return;
  var insp = document.getElementById("inspector");
  var treeBox = document.getElementById("tree");
  var controls = document.getElementById("controls");
  if (!insp || !treeBox || !controls) return;
  insp.style.display = "flex";
  var pending = {};

  var save = document.createElement("button");
  save.className = "save-btn"; save.textContent = "Save edited .nif";
  var status = document.createElement("span"); status.className = "save-status";
  controls.appendChild(save); controls.appendChild(status);
  save.addEventListener("click", doSave);

  treeBox.addEventListener("click", function (e) {
    var li = e.target;
    while (li && li !== treeBox && !li.hasAttribute("data-block-index")) li = li.parentNode;
    if (!li || li === treeBox) return;
    select(parseInt(li.getAttribute("data-block-index"), 10), li);
  });

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c];
    });
  }
  function refreshStatus() {
    var n = Object.keys(pending).length;
    status.textContent = n ? (n + " edit" + (n > 1 ? "s" : "") + " pending") : "";
  }
  function select(idx, li) {
    Array.prototype.forEach.call(treeBox.querySelectorAll("li.sel"), function (x) {
      x.classList.remove("sel");
    });
    if (li) li.classList.add("sel");
    render(idx);
  }
  function widget(f) {
    if (!f.editable) return '<span class="roval">' + esc(f.value === null ? "—" : f.value) + "</span>";
    if (f.kind === "bool32") {
      return '<input type="checkbox" data-field="' + esc(f.name) + '" data-kind="bool32"' +
        (f.value === true ? " checked" : "") + ">";
    }
    return '<input class="f" type="text" data-field="' + esc(f.name) + '" data-kind="' +
      esc(f.kind) + '" value="' + esc(f.value === null ? "" : f.value) + '">';
  }
  function rows(fields) {
    return fields.map(function (f) {
      return '<div class="frow ' + (f.editable ? "" : "ro") + '"><div class="lab"><span class="fname">' +
        esc(f.name) + '</span><span class="kind">' + esc(f.kind) + "</span></div>" + widget(f) + "</div>";
    }).join("");
  }
  function render(idx) {
    var b = EDIT.blocks[idx];
    if (!b) { insp.innerHTML = '<div class="insp-empty">This block has no editable fields.</div>'; return; }
    var ed = b.fields.filter(function (f) { return f.editable; });
    var ro = b.fields.filter(function (f) { return !f.editable; });
    insp.innerHTML =
      '<div class="insp-head"><div class="nm">' + esc(b.type) + '</div><div class="sub">block ' +
      idx + " &middot; " + b.fields.length + " fields</div></div>" +
      '<div class="sect"><div class="sh">PROPERTIES &middot; EDITABLE (' + ed.length + ')</div><div class="body">' +
      (rows(ed) || '<div class="insp-empty">No directly-editable fields.</div>') + "</div></div>" +
      '<div class="sect"><div class="sh">STRUCTURE &middot; READ-ONLY (' + ro.length + ')</div><div class="body">' +
      rows(ro) + "</div></div>";
    Array.prototype.forEach.call(insp.querySelectorAll("[data-field]"), function (el) {
      el.addEventListener("change", function () { onEdit(idx, el); });
    });
  }
  function coerce(el) {
    var k = el.getAttribute("data-kind");
    if (k === "bool32") return el.checked;
    if (k === "f32") return parseFloat(el.value);
    if (k === "u8" || k === "u16" || k === "u32" || k === "i32" || k === "link") return parseInt(el.value, 10);
    return el.value;
  }
  function onEdit(idx, el) {
    var name = el.getAttribute("data-field");
    pending[idx + ":" + name] = {op: "set_field", block: idx, name: name, value: coerce(el)};
    var frow = el.parentNode;
    while (frow && frow.className && frow.className.indexOf("frow") < 0) frow = frow.parentNode;
    if (frow && frow.classList) frow.classList.add("dirty");
    refreshStatus();
  }
  function doSave() {
    var edits = Object.keys(pending).map(function (k) { return pending[k]; });
    if (!edits.length) { status.textContent = "no edits to save"; return; }
    status.textContent = "saving…";
    fetch(EDIT.url, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({edits: edits})
    }).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error(t || ("HTTP " + r.status)); });
      return r.blob();
    }).then(function (blob) {
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = EDIT.filename || "edited.nif";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
      pending = {};
      Array.prototype.forEach.call(insp.querySelectorAll(".frow.dirty"), function (x) {
        x.classList.remove("dirty");
      });
      status.textContent = "saved ✓";
    }).catch(function (err) { status.textContent = "error: " + err.message; });
  }
})();
</script>"""
