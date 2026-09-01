"""The path-grid connection decoder and its adjacency-list renderer.

``decode_connections`` (length-prefix detection) is exercised elsewhere; this
covers the point-shape probe ``_point_fields`` -- which has to cope with every
way tes3conv has written a point across versions -- and the ``render_connections``
paths that attribute edges to points, or explain why they cannot be.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.tes3fields.pathgrid import (
    PathGridDecodeError,
    _point_fields,
    decode_connections,
    render_connections,
)


def _connections(*edges: int) -> bytes:
    """A PGRC ``connections`` payload: a uint32 length prefix then the edges."""
    return struct.pack(f"<{len(edges) + 1}I", len(edges), *edges)


def test_point_fields_of_a_non_mapping_is_empty() -> None:
    """A point that is not a dict yields no location and no count."""
    assert _point_fields("not a dict") == (None, None)


def test_point_fields_reads_a_location_sequence() -> None:
    """A ``location`` triple is read as integer coordinates."""
    coords, count = _point_fields({"location": [1, 2, 3], "connection_count": 2})
    assert coords == (1, 2, 3)
    assert count == 2


def test_point_fields_reads_separate_x_y_z() -> None:
    """The older ``x``/``y``/``z`` shape is read too."""
    coords, _count = _point_fields({"x": 4, "y": 5, "z": 6, "connections": 1})
    assert coords == (4, 5, 6)


def test_point_fields_tolerates_a_non_numeric_location() -> None:
    """A location whose values are not numbers yields no coordinates, not a crash."""
    coords, _count = _point_fields({"location": ["a", "b", "c"], "connection_count": 1})
    assert coords is None


def test_point_fields_tolerates_non_numeric_x_y_z() -> None:
    """The same tolerance applies to the ``x``/``y``/``z`` shape."""
    coords, _count = _point_fields({"x": "no", "y": 5, "z": 6})
    assert coords is None


def test_point_fields_tolerates_a_non_numeric_count() -> None:
    """A connection count that is not a number is reported as unknown."""
    coords, count = _point_fields({"location": [1, 2, 3], "connection_count": "lots"})
    assert coords == (1, 2, 3)
    assert count is None


def test_render_connections_with_no_edges_says_so() -> None:
    """An empty grid renders a single explanatory line, not a header with no body."""
    text = render_connections(_connections())
    assert "no connections" in text


def test_render_connections_without_points_lists_edges_flat() -> None:
    """Without a points list the edges are listed flat, with a note on why."""
    text = render_connections(_connections(1, 2, 3), points=None)
    assert "flat" in text
    assert "not available" in text


def test_render_connections_attributes_edges_to_points() -> None:
    """With points, each point's own neighbours are sliced out in order."""
    points = [
        {"location": [0, 0, 0], "connection_count": 2},
        {"location": [1, 1, 1], "connection_count": 1},
    ]
    text = render_connections(_connections(1, 1, 0), points=points)
    assert "over 2 point(s)" in text
    assert "(0, 0, 0)" in text


def test_point_fields_with_no_location_at_all_is_empty() -> None:
    """A point carrying neither a location nor x/y/z yields no coordinates."""
    coords, count = _point_fields({"connection_count": 1})
    assert coords is None
    assert count == 1


def test_decode_connections_rejects_an_undecodable_value() -> None:
    """A value that is not valid encoded bytes is reported."""
    with pytest.raises(PathGridDecodeError):
        decode_connections("not!valid!base64!")


def test_decode_connections_rejects_a_non_uint32_length() -> None:
    """A field that is not a whole number of 4-byte edges is truncated."""
    with pytest.raises(PathGridDecodeError, match="whole number"):
        decode_connections(b"\x00\x00\x00\x00\x00")


def test_decode_connections_keeps_an_unprefixed_field_intact() -> None:
    """When the leading value is not a length prefix, no edge is stripped."""
    # edges[0] == 9 != len - 1 (== 1), so it is a real edge, not a prefix.
    assert decode_connections(struct.pack("<2I", 9, 9)) == [9, 9]


def test_render_connections_notes_when_points_overclaim_edges() -> None:
    """A point claiming more connections than the field holds is called out."""
    points = [{"location": [0, 0, 0], "connection_count": 5}]
    text = render_connections(_connections(1, 2), points=points)
    assert "more connections than the field holds" in text


def test_render_connections_flags_points_with_no_count() -> None:
    """A point whose count could not be read is called out, not silently skipped."""
    points = [
        {"location": [0, 0, 0], "connection_count": 1},
        {"location": [1, 1, 1]},  # no count
    ]
    text = render_connections(_connections(1, 0), points=points)
    assert "connection count unread" in text
