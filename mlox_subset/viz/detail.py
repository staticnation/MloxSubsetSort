"""Per-cell detail: the decoded payloads the local views draw.

The explorer page embeds its data rather than fetching it, because the pages
must work offline and from ``file://``. That makes **size** the governing
constraint: a full ``VHGT`` grid is 4,225 values, so embedding every landscape
record in a real load order would produce a document no browser will open.

So detail is collected for a *bounded* set of cells, chosen by how much they
matter (the user's own mods first, then the busiest), and the page states how
many cells it covers. The summary layer -- counts, plugins, winners -- stays
complete for every cell; only the heavy decoded grids are capped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from mlox_subset.tes3fields.landscape import LandscapeDecodeError, decode_vertex_heights
from mlox_subset.tes3fields.pathgrid import PathGridDecodeError, _point_fields, decode_connections
from mlox_subset.viz.cache import DetailCacheProtocol
from mlox_subset.viz.geometry import Cell, parse_grid

#: How many cells get decoded detail in the overview page by default.
DEFAULT_DETAIL_LIMIT = 60

#: Vertices per side of a landscape grid.
LAND_SIDE = 65

#: Sampling interval for the **overview** page. This is the fix for a measured
#: failure, not a precaution: at full resolution a cell costs 4,225 floats per
#: plugin, so sixty two-plugin cells embed roughly 25 MB of JSON into one
#: document -- which froze the app while it was built and would not have opened
#: afterwards. A real cell map is already 5 MB on its own.
#:
#: The sampling is chosen to match the **display**, not to an arbitrary
#: fraction: a cell is drawn as an 11-pixel square on the world map, so stride
#: 8 -- a 9x9 grid, near enough one value per pixel -- is all the resolution
#: that view can physically show. Carrying 65x65 there was 52x the data for
#: nothing visible.
#:
#: Full resolution lives in the per-cell sidecar, loaded on click, because that
#: is where per-vertex questions are actually asked.
OVERVIEW_STRIDE = 8


def _decode_land(value: object, offset: object, stride: int = 1) -> dict[str, Any] | None:
    """Decode one plugin's landscape heights into a compact payload.

    Args:
        value: The ``vertex_heights.data`` field.
        offset: The ``vertex_heights.offset`` field.
        stride: Sample every ``stride``-th vertex. 1 keeps full resolution;
            :data:`OVERVIEW_STRIDE` shrinks it for the overview page. ``min``
            and ``max`` are taken from the **full** grid either way, so the
            reported height range does not drift with the sampling.

    Returns:
        ``{"heights": [...], "min": float, "max": float, "side": int}`` with
        heights row-major, or ``None`` if the field cannot be decoded.
    """
    if not isinstance(value, (str, bytes)) or not value:
        return None
    try:
        base = float(offset)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        base = 0.0
    try:
        grid = decode_vertex_heights(value, base)
    except LandscapeDecodeError:
        return None
    full = [round(height, 1) for row in grid for height in row]
    if not full:
        return None
    if stride > 1:
        sampled = [round(v, 1) for row in grid[::stride] for v in row[::stride]]
        side = len(grid[::stride])
    else:
        sampled, side = full, len(grid)
    # Range comes from the full grid: a sampled peak would under-report how
    # much a mod actually moved, which is the number the user reads.
    return {"heights": sampled, "min": min(full), "max": max(full), "side": side}


def _decode_pgrd(value: object, points: object) -> dict[str, Any] | None:
    """Decode one plugin's path grid into nodes and undirected edges.

    Args:
        value: The ``connections`` field.
        points: The ``points`` list.

    Returns:
        ``{"points": [[x, y, z], ...], "edges": [[a, b], ...]}``, or ``None``
        if the field cannot be decoded.
    """
    if not isinstance(value, (str, bytes)) or not value:
        return None
    coords: list[list[int]] = []
    counts: list[int] = []
    if isinstance(points, Sequence) and not isinstance(points, str):
        for point in points:
            where, count = _point_fields(point)
            coords.append(list(where or (0, 0, 0)))
            counts.append(count or 0)
    try:
        flat = decode_connections(value, sum(counts) if counts else None)
    except PathGridDecodeError:
        return None
    edges: set[tuple[int, int]] = set()
    cursor = 0
    for index, count in enumerate(counts):
        for target in flat[cursor : cursor + count]:
            # Stored from both ends; normalise so an edge counts once.
            edges.add((index, target) if index <= target else (target, index))
        cursor += count
    return {"points": coords, "edges": sorted([a, b] for a, b in edges)}


def _rank(conflict: Mapping[str, Any]) -> tuple[int, int]:
    """Sort key putting the user's own mods first, then the most contested.

    Args:
        conflict: One conflict record.

    Returns:
        A key for ascending sort.
    """
    return (0 if conflict.get("involves_subset") else 1, -len(conflict.get("plugins") or []))


#: What a caller must supply to look one conflict's fields up: in the app this
#: wraps ``diff_record_fields``. Typed as returning ``object`` because the
#: result is validated here rather than trusted.
FieldsLookup = Callable[[Mapping[str, Any]], object]


def _fields_or_none(
    fields_for: FieldsLookup, conflict: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """Call the field lookup, turning any failure into "no data".

    Lifted out of the loop rather than wrapped in a per-iteration ``try``:
    that reads better and keeps ``PERF203`` honest. The catch stays broad
    because ``fields_for`` reaches into tes3conv and third-party plugin
    records, and one unreadable record must cost one cell rather than the
    whole page.

    Args:
        fields_for: The caller's lookup callable.
        conflict: The conflict to look up.

    Returns:
        The per-plugin field mapping, or ``None`` if it could not be read.
    """
    try:
        per = fields_for(conflict)
    except Exception:  # noqa: BLE001 - see above; a bad record loses one cell
        return None
    return per if isinstance(per, Mapping) else None


def _parse_key(key: str) -> Cell | None:
    """Parse a ``"x,y"`` detail key back into a cell.

    Args:
        key: The detail dictionary key.

    Returns:
        The cell, or ``None`` if the key is not a coordinate pair.
    """
    parts = key.split(",")
    if len(parts) != 2:
        return None
    try:
        return Cell(int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def _decode_cell(
    conflict: Mapping[str, Any], fields_for: FieldsLookup, stride: int
) -> dict[str, Any] | None:
    """Decode one conflict's landscape and path grid.

    Args:
        conflict: The conflict record.
        fields_for: The field lookup.
        stride: Height sampling interval.

    Returns:
        ``{"land", "pgrd", "plugins"}`` for the cell, or ``None`` if nothing
        decoded.
    """
    per = _fields_or_none(fields_for, conflict)
    if per is None:
        return None
    entry: dict[str, Any] = {"land": {}, "pgrd": {}, "plugins": []}
    for plugin, fields in per.items():
        if not isinstance(fields, Mapping):
            continue
        land = _decode_land(
            fields.get("vertex_heights.data"), fields.get("vertex_heights.offset", 0.0), stride
        )
        if land is not None:
            entry["land"][plugin] = land
        pgrd = _decode_pgrd(fields.get("connections"), fields.get("points"))
        if pgrd is not None:
            entry["pgrd"][plugin] = pgrd
        if (land or pgrd) and plugin not in entry["plugins"]:
            entry["plugins"].append(plugin)
    if not entry["land"] and not entry["pgrd"]:
        return None
    return entry


def collect_detail(
    conflicts: Iterable[Mapping[str, Any]],
    fields_for: FieldsLookup,
    *,
    limit: int = DEFAULT_DETAIL_LIMIT,
    stride: int = OVERVIEW_STRIDE,
    cache: DetailCacheProtocol | None = None,
    signature_for: Callable[[Mapping[str, Any]], str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Decode landscape and path-grid payloads for the most important cells.

    Args:
        conflicts: Conflict dicts as ``detect_conflicts`` returns them.
        fields_for: Callable taking one conflict and returning
            ``{plugin: {flattened_field: value}}`` -- in the app this wraps
            ``diff_record_fields``. Anything it raises is treated as "no data
            for this cell" rather than propagating, since one unreadable
            record must not lose the whole page.
        limit: Maximum number of cells to decode.
        stride: Height sampling interval. Defaults to :data:`OVERVIEW_STRIDE`
            because the overview embeds every cell at once; pass 1 for a
            single-cell page, where full resolution is the point.
        cache: Optional persistent cache. When given with ``signature_for``, a
            cell whose plugins have not changed is loaded from the cache
            instead of decoded, which is the slow path. Injected rather than
            reached for, so this module stays free of the filesystem.
        signature_for: Callable giving a change signature for one conflict --
            in the app, the mtime/size of its plugins. Required for the cache
            to be used; without it every cell is decoded.

    Returns:
        ``{"x,y": {"land": {...}, "pgrd": {...}, "plugins": [...]}}``, keyed by
        cell so the page can look a cell up directly.
    """
    spatial = [c for c in conflicts if parse_grid(c.get("id")) is not None]
    spatial.sort(key=_rank)
    use_cache = cache is not None and signature_for is not None
    # The cache key folds in the resolution: the overview (sampled) and the
    # per-cell page (full) decode the same cell to different data, so they must
    # not share an entry.
    suffix = f"@{stride}"
    out: dict[str, dict[str, Any]] = {}
    for conflict in spatial:
        cell = parse_grid(conflict.get("id"))
        if cell is None:
            continue
        key = f"{cell.x},{cell.y}"
        if len(out) >= limit and key not in out:
            break
        entry: dict[str, Any] | None = None
        sig = ""
        if use_cache:
            assert cache is not None and signature_for is not None  # noqa: S101 - narrows for mypy
            sig = signature_for(conflict)
            entry = cache.get(key + suffix, sig)
        if entry is None:
            entry = _decode_cell(conflict, fields_for, stride)
            if entry is not None and use_cache:
                assert cache is not None  # noqa: S101 - narrows for mypy
                cache.put(key + suffix, sig, entry)
        if entry is not None:
            out[key] = entry
    return out


def _winner_heights(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """The winning plugin's land grid for a cell, for a neighbour seam.

    Args:
        entry: One cell's detail (``land``/``pgrd``/``plugins``).

    Returns:
        ``{"heights", "side"}`` for the last plugin in load order, or ``None``.
    """
    land = entry.get("land") or {}
    if not land:
        return None
    winner = list(land)[-1]
    grid = land[winner]
    return {"heights": grid["heights"], "side": grid.get("side")}


def cell_page_detail(detail: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Add neighbour seam strips to each cell's detail, for the cell pages.

    For every detailed cell it attaches the winning heights of any of its eight
    neighbours that are *also* in the detail set, tagged with the direction.
    The cell page draws the centre at full resolution and each neighbour as a
    border strip abutting it, so a mismatched edge shows as a step -- which is
    the whole reason to show neighbours at all.

    Neighbours outside the detail set are not included: getting a non-conflict
    cell's terrain would need a separate load-order-winner lookup, and the
    cells that matter most for a seam are the ones both mods are editing, which
    are conflicts by definition.

    Args:
        detail: Full-resolution detail from
            :func:`collect_detail` (``stride=1``).

    Returns:
        The same mapping with a ``"seams"`` list added to each cell.
    """
    from mlox_subset.viz.sidecar import neighbours

    out: dict[str, dict[str, Any]] = {}
    for key, entry in detail.items():
        cell = _parse_key(key)
        if cell is None:
            out[key] = dict(entry)
            continue
        seams = []
        for nb in neighbours(cell):
            nb_entry = detail.get(f"{nb.x},{nb.y}")
            if nb_entry is None:
                continue
            heights = _winner_heights(nb_entry)
            if heights is None:
                continue
            seams.append(
                {"dx": nb.x - cell.x, "dy": nb.y - cell.y, **heights},
            )
        out[key] = {**entry, "seams": seams}
    return out


def detail_cells(detail: Mapping[str, Any]) -> set[Cell]:
    """List the cells a detail map covers.

    Args:
        detail: The result of :func:`collect_detail`.

    Returns:
        Every cell with decoded detail.
    """
    return {cell for cell in (_parse_key(str(key)) for key in detail) if cell is not None}


#: Sampling for the **world** terrain view. A cell is drawn about 11 pixels
#: across there, so 11 samples per edge is one per pixel -- stride 6 over a
#: 65-vertex edge gives exactly that. Anything finer is data the view cannot
#: show.
#: Vertices per edge for the world view. This is aggressive LOD, on purpose: a
#: cell is about 11 pixels across at world zoom, so it cannot show more than a
#: handful of faces, and drawing 65x65 was ~100 quads per cell -- 50,000 across
#: a real landmass, which is what made the view crawl. At side 3 each cell is a
#: 2x2 quad patch (4 faces), ~1,900 for the whole map, and it draws instantly.
#:
#: The **edges** are what this sampling protects. It is edge-inclusive (it takes
#: vertex 0 AND vertex 64), because the interesting thing at world zoom is where
#: one cell's border height does not match its neighbour's -- a seam, a visible
#: cliff in game. A stride that stopped short of vertex 64 would drop exactly
#: the data that shows those. Interior shape is what gets sacrificed to LOD, not
#: the edges.
WORLD_SIDE = 3


def collect_world_terrain(
    conflicts: Iterable[Mapping[str, Any]],
    fields_for: FieldsLookup,
    *,
    limit: int = 4000,
) -> dict[str, Any]:
    """Decode a tiny height patch per cell, for one low-detail world surface.

    The world view draws every cell in *one* pass, not a file per cell:
    thousands of small fetches to build a single picture is the wrong shape.
    Each patch is deliberately minimal -- :data:`WORLD_SIDE` per edge -- because
    a cell is only ~11 pixels wide there. The client draws each cell as its own
    little patch abutting its neighbours, so a mismatch at a shared border shows
    as a step: that seam is the point of the view.

    Only the winning plugin's heights are taken. The world view answers "what
    does the terrain end up looking like, and where do cells not line up" --
    which plugin lost a given vertex is a per-cell question, asked on the
    per-cell page where both are loaded.

    Args:
        conflicts: Conflict dicts as ``detect_conflicts`` returns them.
        fields_for: Field lookup, as for :func:`collect_detail`.
        limit: Maximum cells to decode. Generous, because each is tiny.

    Returns:
        ``{"side": int, "cells": {"x,y": [heights...]}}``, heights row-major and
        edge-inclusive. Cells whose landscape cannot be read are simply absent,
        leaving a hole rather than an invented surface.
    """
    seen: set[str] = set()
    patches: dict[str, list[float]] = {}
    for conflict in conflicts:
        cell = parse_grid(conflict.get("id"))
        if cell is None:
            continue
        key = f"{cell.x},{cell.y}"
        if key in seen:
            continue
        seen.add(key)
        if len(patches) >= limit:
            break
        per = _fields_or_none(fields_for, conflict)
        if per is None:
            continue
        # Last plugin in load order wins, so its terrain is what the game draws.
        winner = str(conflict.get("winner") or "")
        fields = per.get(winner)
        if not isinstance(fields, Mapping):
            plugins = [p for p in per if isinstance(per.get(p), Mapping)]
            if not plugins:
                continue
            fields = per[plugins[-1]]
        patch = _world_patch(
            fields.get("vertex_heights.data"), fields.get("vertex_heights.offset", 0.0)
        )
        if patch is not None:
            patches[key] = patch
    return {"side": WORLD_SIDE, "cells": patches}


def _world_patch(value: object, offset: object) -> list[float] | None:
    """Decode one cell's heights to a tiny edge-inclusive patch for the world view.

    Distinct from :func:`_decode_land`'s ``stride`` sampling, which steps from
    vertex 0 and can stop short of the last edge (65 with stride 6 ends at 60).
    For seams that is the wrong sampling -- the border height at vertex 64 is
    exactly what must be kept. This picks :data:`WORLD_SIDE` positions spread
    from 0 to 64 inclusive, so both edges are always present.

    Args:
        value: The ``vertex_heights.data`` field.
        offset: The ``vertex_heights.offset`` field.

    Returns:
        A row-major ``WORLD_SIDE * WORLD_SIDE`` height list, or ``None``.
    """
    if not isinstance(value, (str, bytes)) or not value:
        return None
    try:
        base = float(offset)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        base = 0.0
    try:
        grid = decode_vertex_heights(value, base)
    except LandscapeDecodeError:
        return None
    if not grid:
        return None
    last = len(grid) - 1
    idx = [round(k * last / (WORLD_SIDE - 1)) for k in range(WORLD_SIDE)]
    return [round(grid[iy][ix], 1) for iy in idx for ix in idx]
