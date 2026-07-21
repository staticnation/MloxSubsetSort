"""Verify the LAND / PGRD field decoders used by the diff window.

Fixtures here are **built from the documented format**, with answers known by
construction: an all-ones height-delta grid must reconstruct to a known plane,
and UESP's own worked path-grid example must produce the adjacency it
documents. Synthetic input is used deliberately -- the answers are exact, and
no third-party mod data is committed to this repository.

The decoders were **separately validated against real plugins** in the
workspace, which is what settled the two questions synthetic data cannot
answer. Those findings are recorded in ``CODE_REVIEW.md`` §22:

* Every subrecord is exactly its documented size (VNML 12,675, VHGT 4,232,
  WNAM 81, VCLR 12,675, VTEX 512).
* Reconstructed heights bottom out at exactly **-2048**, the format's
  documented default-height sentinel, and stay in a plausible terrain range.
* Path grids: the sum of every point's connection count equals the edge count
  **exactly** (282 = 282 on the first record checked), confirming both the
  byte offset of that field and the slicing model.
* VTEX de-swizzling raises adjacent-cell agreement from 0.714 to 0.852 across
  **2,190** non-uniform cells, winning in **97%** individually.

Re-running that validation needs only a plugin with LAND/PGRD records; the
extraction is a dozen lines of ``struct.unpack_from`` over the record stream.
"""

from __future__ import annotations

import base64
import struct

import pytest

from mlox_subset.tes3fields import DECODABLE_FIELDS, describe_field, text_for_field
from mlox_subset.tes3fields.landscape import (
    HEIGHT_SCALE,
    LAND_NUM_VERTS,
    LAND_SIZE,
    LandscapeDecodeError,
    decode_texture_indices,
    decode_vertex_colours,
    decode_vertex_heights,
    decode_vertex_normals,
    decode_world_map,
)
from mlox_subset.tes3fields.pathgrid import PathGridDecodeError, decode_connections


def _b64(payload: bytes) -> str:
    """Encode a payload the way tes3conv writes an uncompressed field."""
    return base64.b64encode(payload).decode()


class TestVertexHeights:
    """The doubly-cumulative reconstruction, the easiest thing here to get wrong."""

    def test_all_ones_reconstructs_to_a_known_plane(self) -> None:
        # Every delta is 1, so vertex (row, col) sits at offset + (row+1) + col.
        heights = decode_vertex_heights(_b64(bytes([1]) * LAND_NUM_VERTS), 10.0)
        assert heights[0][0] == (10.0 + 1) * HEIGHT_SCALE
        assert heights[0][64] == (10.0 + 1 + 64) * HEIGHT_SCALE
        assert heights[1][0] == (10.0 + 2) * HEIGHT_SCALE
        assert heights[64][64] == (10.0 + 65 + 64) * HEIGHT_SCALE

    def test_a_flat_sum_would_not_produce_this(self) -> None:
        """Guard the specific wrong answer: one running total over all deltas.

        A flat cumulative sum would put the last vertex at ``offset + 4225``.
        The correct row/column accumulation puts it at ``offset + 65 + 64``.
        """
        heights = decode_vertex_heights(_b64(bytes([1]) * LAND_NUM_VERTS), 0.0)
        assert heights[64][64] != LAND_NUM_VERTS * HEIGHT_SCALE
        assert heights[64][64] == (65 + 64) * HEIGHT_SCALE

    def test_an_isolated_bump_stays_in_its_row(self) -> None:
        deltas = bytearray(LAND_NUM_VERTS)
        deltas[3 * LAND_SIZE + 10] = 5
        heights = decode_vertex_heights(_b64(bytes(deltas)), 0.0)
        assert heights[3][9] == 0
        assert heights[3][10] == 5 * HEIGHT_SCALE
        assert heights[3][64] == 5 * HEIGHT_SCALE  # carries along the row
        assert heights[4][10] == 0  # but not into the next row

    def test_offset_shifts_the_whole_grid(self) -> None:
        flat = _b64(bytes(LAND_NUM_VERTS))
        assert decode_vertex_heights(flat, 7.0)[0][0] == 7.0 * HEIGHT_SCALE
        assert decode_vertex_heights(flat, 0.0)[0][0] == 0.0

    def test_negative_deltas_are_signed(self) -> None:
        deltas = bytes([0xFF]) * LAND_NUM_VERTS  # -1 as int8
        assert decode_vertex_heights(_b64(deltas), 0.0)[0][0] == -1 * HEIGHT_SCALE


class TestOtherLandscapeGrids:
    """Shape and signedness of the remaining LAND blobs."""

    def test_normals_are_signed_triples(self) -> None:
        payload = bytes([1, 2, 0xFF]) * LAND_NUM_VERTS
        grid = decode_vertex_normals(_b64(payload))
        assert len(grid) == LAND_SIZE
        assert len(grid[0]) == LAND_SIZE
        assert grid[0][0] == (1, 2, -1)

    def test_colours_are_unsigned_triples(self) -> None:
        payload = bytes([0, 128, 255]) * LAND_NUM_VERTS
        assert decode_vertex_colours(_b64(payload))[0][0] == (0, 128, 255)

    def test_world_map_is_nine_by_nine_signed(self) -> None:
        grid = decode_world_map(_b64(bytes([0xFF]) * 81))
        assert len(grid) == 9
        assert len(grid[0]) == 9
        assert grid[0][0] == -1

    def test_texture_indices_are_uint16(self) -> None:
        grid = decode_texture_indices(_b64(struct.pack("<256H", *range(256))))
        assert len(grid) == 16
        assert all(len(row) == 16 for row in grid)

    def test_texture_deswizzle_places_each_block(self) -> None:
        """Stored index k belongs at (y1*4+y2, x1*4+x2) for k's base-4 digits.

        Verified against real plugins before it was written: de-swizzling
        raises adjacent-cell agreement from 0.714 to 0.852 across 2,190
        non-uniform landscape cells, winning in 97% individually.
        """
        grid = decode_texture_indices(_b64(struct.pack("<256H", *range(256))))
        # k=0 is the first cell of the first block -> (0, 0)
        assert grid[0][0] == 0
        # k=1..3 finish that block's first row -> (0, 1..3)
        assert grid[0][1] == 1
        # k=4 wraps to the second row *of the same 4x4 block* -> (1, 0)
        assert grid[1][0] == 4
        # k=16 starts the next block along -> (0, 4)
        assert grid[0][4] == 16
        # k=64 starts the next block row -> (4, 0)
        assert grid[4][0] == 64
        assert grid[15][15] == 255

    def test_texture_deswizzle_is_a_permutation(self) -> None:
        """Re-ordering must move values, never drop or duplicate them."""
        payload = _b64(struct.pack("<256H", *range(256)))
        flat = [v for row in decode_texture_indices(payload) for v in row]
        assert sorted(flat) == list(range(256))

    def test_texture_storage_order_is_available_unchanged(self) -> None:
        payload = _b64(struct.pack("<256H", *range(256)))
        stored = decode_texture_indices(payload, deswizzle=False)
        assert stored[0] == list(range(16))
        assert stored != decode_texture_indices(payload)


class TestPathGridConnections:
    """The flat edge array, and slicing it by each point's connection count."""

    def test_uesp_worked_example(self) -> None:
        """UESP documents Azura's Coast (12, 20): point 0 -> 1; point 1 -> 0,6,3,2."""
        edges = _b64(struct.pack("<5I", 1, 0, 6, 3, 2))
        points = [
            {"location": [100, 200, 30], "connection_count": 1},
            {"location": [140, 260, 30], "connection_count": 4},
        ]
        out = text_for_field("connections", edges, {"points": points})
        assert out is not None
        lines = out.splitlines()
        assert "(100, 200, 30) -> 1" in lines[1]
        assert "(140, 260, 30) -> 0, 6, 3, 2" in lines[2]

    def test_alternate_key_spellings_are_accepted(self) -> None:
        edges = _b64(struct.pack("<1I", 7))
        points = [{"x": 1, "y": 2, "z": 3, "connection_num": 1}]
        out = text_for_field("connections", edges, {"points": points})
        assert out is not None
        assert "(1, 2, 3) -> 7" in out

    def test_missing_points_degrades_and_says_so(self) -> None:
        out = text_for_field("connections", _b64(struct.pack("<2I", 4, 5)), {})
        assert out is not None
        assert "not available" in out
        assert "-> 4" in out

    def test_unaccounted_edges_are_reported_not_hidden(self) -> None:
        edges = _b64(struct.pack("<4I", 1, 2, 3, 4))
        points = [{"location": [0, 0, 0], "connection_count": 1}]
        out = text_for_field("connections", edges, {"points": points})
        assert out is not None
        assert "3 trailing edge(s) unaccounted for" in out

    def test_tes3conv_length_prefix_is_stripped(self) -> None:
        """tes3conv prefixes the payload with a uint32 count; the ESP does not.

        Found on a real record: the prefix survived as a leading "edge" of
        value 224 in a 62-point grid -- an index that cannot exist -- and
        shifted every later edge by one slot, attributing each point its
        neighbour's connections.
        """
        edges = _b64(struct.pack("<4I", 3, 7, 8, 9))  # leading 3 == the 3 that follow
        points = [{"location": [0, 0, 0], "connection_count": 3}]
        out = text_for_field("connections", edges, {"points": points})
        assert out is not None
        assert "3 edge(s)" in out.splitlines()[0]
        assert "-> 7, 8, 9" in out
        assert "unaccounted" not in out

    def test_a_genuine_leading_edge_is_not_mistaken_for_a_prefix(self) -> None:
        """Only strip when the leading value counts exactly what follows."""
        edges = _b64(struct.pack("<4I", 2, 7, 8, 9))  # 2 != 3 -> a real edge
        assert decode_connections(edges) == [2, 7, 8, 9]

    def test_the_points_total_confirms_the_prefix(self) -> None:
        """With the points to hand, their sum decides rather than the shape."""
        raw = _b64(struct.pack("<4I", 3, 7, 8, 9))
        assert decode_connections(raw, 3) == [7, 8, 9]  # prefix confirmed
        assert decode_connections(raw, 4) == [3, 7, 8, 9]  # 4 edges expected: keep it

    def test_length_not_a_multiple_of_four_is_rejected(self) -> None:
        with pytest.raises(PathGridDecodeError, match="whole number"):
            decode_connections(_b64(b"\x01\x02\x03"))


class TestTotality:
    """Malformed input must explain itself, never raise into the GUI."""

    @pytest.mark.parametrize("key", sorted(DECODABLE_FIELDS))
    def test_truncated_field_returns_a_comment(self, key: str) -> None:
        out = text_for_field(key, _b64(b"\x00\x01\x02\x03"))
        assert out is not None
        assert out.startswith(";")

    @pytest.mark.parametrize("key", sorted(DECODABLE_FIELDS))
    def test_garbage_returns_a_comment(self, key: str) -> None:
        out = text_for_field(key, "definitely not base64 !!!")
        assert out is not None
        assert out.startswith(";")

    def test_short_payload_raises_only_inside_the_decoder(self) -> None:
        with pytest.raises(LandscapeDecodeError, match="requires"):
            decode_vertex_heights(_b64(b"\x00\x01"))

    def test_unknown_field_is_declined_so_the_caller_falls_back(self) -> None:
        assert text_for_field("some_other_field", "value") is None
        assert describe_field("some_other_field") is None

    def test_every_decodable_field_has_a_description(self) -> None:
        assert {k for k in DECODABLE_FIELDS if describe_field(k)} == set(DECODABLE_FIELDS)
