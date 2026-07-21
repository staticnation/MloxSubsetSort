"""Render binary TES3 record fields as readable text for the diff window.

tes3conv writes several record fields as base64 (zstd-compressed underneath):
landscape grids, path-grid edges, compiled script bytecode. In a field-by-field
diff those are useless -- two versions of a cell that differ by one vertex look
completely different, because base64 of *nearly* identical bytes is *entirely*
different text.

This package turns them back into line-per-row text so the diff is meaningful.
Scripts are handled by :mod:`mlox_subset.mwscript`; the record types here are
landscape (``LAND``) and path grids (``PGRD``).

The public entry point is :func:`text_for_field`, which the field-detail window
calls for every field and which returns ``None`` for anything it does not
handle -- so adding a record type is a change in one dict, not in the GUI.

Every decoder is **total**: a malformed field comes back as a ``;``-prefixed
explanation rather than an exception, because a viewer that dies on one bad
record is worse than one that says what went wrong in place.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from mlox_subset.tes3fields.landscape import (
    LandscapeDecodeError,
    render_texture_indices,
    render_vertex_colours,
    render_vertex_heights,
    render_vertex_normals,
    render_world_map,
)
from mlox_subset.tes3fields.pathgrid import PathGridDecodeError, render_connections

__all__ = [
    "DECODABLE_FIELDS",
    "LandscapeDecodeError",
    "PathGridDecodeError",
    "describe_field",
    "text_for_field",
]


def _heights(value: str | bytes, record: Mapping[str, Any] | None) -> str:
    """Render VHGT, taking the base height from the sibling ``offset`` field."""
    offset = (record or {}).get("vertex_heights.offset", 0.0)
    try:
        base = float(offset)
    except (TypeError, ValueError):
        base = 0.0
    return render_vertex_heights(value, base)


def _connections(value: str | bytes, record: Mapping[str, Any] | None) -> str:
    """Render PGRC, slicing it with the sibling ``points`` field."""
    return render_connections(value, (record or {}).get("points"))


#: Flattened field name -> renderer. The key is the dotted path the field-diff
#: window uses (``flatten_dict``'s output), so it matches what the user clicked.
_RENDERERS: Final[dict[str, Callable[[str | bytes, Mapping[str, Any] | None], str]]] = {
    "vertex_heights.data": _heights,
    "vertex_normals.data": lambda v, _r: render_vertex_normals(v),
    "vertex_colors.data": lambda v, _r: render_vertex_colours(v),
    "world_map_data.data": lambda v, _r: render_world_map(v),
    "texture_indices.data": lambda v, _r: render_texture_indices(v),
    "connections": _connections,
}

#: One-line description per decodable field, shown in the detail window header.
_DESCRIPTIONS: Final[dict[str, str]] = {
    "vertex_heights.data": "decoded to absolute heights, one terrain row per line",
    "vertex_normals.data": "decoded to (x,y,z) normals, one terrain row per line",
    "vertex_colors.data": "decoded to #rrggbb, one terrain row per line",
    "world_map_data.data": "decoded to the 9x9 world-map heightmap",
    "texture_indices.data": "decoded to the 16x16 land-texture index grid",
    "connections": "decoded to a per-point adjacency list",
}

#: The field names this package can render. Public so callers can ask before
#: reading a potentially large value.
DECODABLE_FIELDS: Final[frozenset[str]] = frozenset(_RENDERERS)


def describe_field(key: str) -> str | None:
    """Return a short description of what decoding does to ``key``.

    Args:
        key: The flattened field name.

    Returns:
        A phrase for the detail window's header, or ``None`` if this package
        does not handle the field.
    """
    return _DESCRIPTIONS.get(key)


def text_for_field(
    key: str, value: str | bytes, record: Mapping[str, Any] | None = None
) -> str | None:
    """Render one binary record field as readable text.

    Never raises. A field that cannot be decoded comes back as a
    ``;``-prefixed explanation, so the detail window shows the problem in place
    instead of dying on one malformed record.

    Args:
        key: The flattened field name, e.g. ``"vertex_heights.data"``.
        value: The field's value, as tes3conv wrote it.
        record: The whole flattened record, when available. Some fields are
            only meaningful alongside a sibling -- heights need their
            ``offset``, path-grid edges need their ``points``.

    Returns:
        The rendered text, or ``None`` if this package does not handle ``key``
        (the caller should then fall back to its normal rendering).
    """
    render = _RENDERERS.get(key)
    if render is None:
        return None
    try:
        return render(value, record)
    except (LandscapeDecodeError, PathGridDecodeError) as exc:
        return f"; could not decode this {key} field:\n;   {exc}"
    except Exception as exc:  # noqa: BLE001 -- deliberate backstop, as in
        # mwscript.tes3conv: these decoders walk arbitrary bytes from any mod
        # file, so an unanticipated struct.error / IndexError / MemoryError
        # must become a comment line rather than take the window down.
        return f"; unexpected error decoding this {key} field: {exc!r}"
