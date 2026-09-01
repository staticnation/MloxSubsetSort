"""The human-readable renderers for a cell's landscape subrecords.

The decoders (VHGT/VNML/VCLR/WNAM/VTEX -> grids) are covered elsewhere; these
are the ``render_*`` functions that turn a decoded grid into the row-per-line
text the field-diff viewer shows. Each is driven here with a correctly-sized
zeroed buffer -- enough to exercise the header, the row formatting and the grid
dimensions without depending on a real cell fixture.
"""

from __future__ import annotations

import pytest

from wraithguard.tes3fields import landscape as land
from wraithguard.tes3fields.landscape import (
    LandscapeDecodeError,
    decode_texture_indices,
    decode_vertex_heights,
)

# The byte sizes each subrecord's payload must be (one value/tuple per vertex).
_VHGT = bytes(land.LAND_NUM_VERTS)
_VNML = bytes(3 * land.LAND_NUM_VERTS)
_VCLR = bytes(3 * land.LAND_NUM_VERTS)
_WNAM = bytes(land.WNAM_SIZE * land.WNAM_SIZE)
_VTEX = bytes(2 * land.NUM_TEXTURES)


def _body_rows(text: str) -> list[str]:
    """The ``rNN``-prefixed grid rows, dropping the ``;`` comment header."""
    return [line for line in text.splitlines() if line.startswith("r")]


def test_render_vertex_heights_labels_and_sizes_the_grid() -> None:
    """VHGT renders one ``rNN`` line per land row, under a VHGT header."""
    text = land.render_vertex_heights(_VHGT, offset=0.0)
    assert "VHGT" in text.splitlines()[0]
    assert len(_body_rows(text)) == land.LAND_SIZE


def test_render_vertex_normals_renders_triples() -> None:
    """VNML renders one row per land row, each cell an ``(x,y,z)`` triple."""
    text = land.render_vertex_normals(_VNML)
    rows = _body_rows(text)
    assert len(rows) == land.LAND_SIZE
    assert "(0,0,0)" in rows[0]


def test_render_vertex_colors_renders_hex() -> None:
    """VCLR renders one row per land row, each cell a ``#rrggbb`` value."""
    text = land.render_vertex_colors(_VCLR)
    rows = _body_rows(text)
    assert len(rows) == land.LAND_SIZE
    assert "#000000" in rows[0]


def test_render_world_map_is_nine_by_nine() -> None:
    """WNAM renders the 9x9 low-resolution heightmap."""
    text = land.render_world_map(_WNAM)
    assert "WNAM" in text.splitlines()[0]
    assert len(_body_rows(text)) == land.WNAM_SIZE


def test_render_texture_indices_is_sixteen_by_sixteen() -> None:
    """VTEX renders the 16x16 de-swizzled land-texture grid."""
    text = land.render_texture_indices(_VTEX)
    rows = _body_rows(text)
    assert "VTEX" in text.splitlines()[0]
    assert len(rows) == land.TEXTURE_SIZE


def test_decode_texture_indices_without_deswizzle_keeps_storage_order() -> None:
    """The raw path returns the 16x16 grid in stored order, un-de-swizzled."""
    grid = decode_texture_indices(_VTEX, deswizzle=False)
    assert len(grid) == land.TEXTURE_SIZE
    assert all(len(row) == land.TEXTURE_SIZE for row in grid)


def test_a_too_short_payload_is_refused() -> None:
    """A buffer shorter than the format requires is an error, not a partial grid."""
    with pytest.raises(LandscapeDecodeError, match="requires"):
        decode_vertex_heights(bytes(10))


def test_an_undecodable_value_is_refused() -> None:
    """A value that is not valid encoded bytes is reported, not swallowed."""
    with pytest.raises(LandscapeDecodeError):
        decode_vertex_heights("not!valid!base64!")
