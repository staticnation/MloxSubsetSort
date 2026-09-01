"""Tests for :mod:`wraithguard.land.emit` and texture compaction.

These cover the only code in the toolkit that produces a file the game loads,
so the emphasis is on the failures that would *not* raise: a texture grid
written without its flag, a shared table emitted uncompacted, a header with no
masters.
"""

from __future__ import annotations

import pytest

from wraithguard.land.emit import (
    FLAG_TEXTURES,
    EmitError,
    attach_texture_indices,
    build_header,
    build_landscape_record,
    build_plugin,
    build_texture_records,
    encode_field,
    pack_texture_indices,
    sink_underwater_land,
    world_map_from_heights,
)
from wraithguard.land.textures import KnownTextures, compact_textures, vtex_of
from wraithguard.tes3fields.landscape import decode_texture_indices, decode_world_map

MASTERS = [("Morrowind.esm", 79837557)]


def heights(value: float = 0.0) -> list[list[float]]:
    """A flat 65x65 height grid."""
    return [[value] * 65 for _ in range(65)]


def textures(value: int = 0) -> list[list[int]]:
    """A uniform 16x16 texture grid."""
    return [[value] * 16 for _ in range(16)]


class TestPackingSaturates:
    """An out-of-range merged value clamps to its field's byte, never raises.

    ``struct.pack("b", ...)`` and ``bytes()`` raise on anything outside their
    range, and struct.error is not caught in service._build_records -- so one
    overflowing vertex would abort the whole merge. This is the i8 overflow the
    OpenMW fork hit on large load orders; it saturates for the same reason.
    """

    def test_world_map_saturates(self) -> None:
        """A world-map value past the int8 range clamps, not crashes."""
        from wraithguard.land.emit import pack_world_map
        from wraithguard.tes3fields.landscape import decode_world_map

        packed = pack_world_map([[300] * 9 for _ in range(9)])
        assert all(v == 127 for row in decode_world_map(packed) for v in row)

    def test_vertex_colors_saturate(self) -> None:
        """A colour channel past 0-255 clamps rather than raising."""
        from wraithguard.land.emit import pack_vertex_colors

        packed = pack_vertex_colors([[(300, -5, 128)] * 65 for _ in range(65)])
        assert packed[0] == 255
        assert packed[1] == 0
        assert packed[2] == 128

    def test_vertex_normals_saturate(self) -> None:
        """A carried normal past the int8 range clamps rather than raising."""
        from wraithguard.land.heights import pack_vertex_normals

        packed = pack_vertex_normals([[(200, -200, 5)] * 65 for _ in range(65)])
        assert packed[0] == 127
        assert packed[1] - 256 == -128  # unsigned byte view of -128
        assert packed[2] == 5


class TestEncodeField:
    """Payloads must be zstd-compressed, because tes3's reader assumes it."""

    def test_a_payload_encodes_to_base64_text(self) -> None:
        """The field is a string in the JSON, not bytes."""
        assert isinstance(encode_field(b"hello"), str)

    def test_the_frame_is_zstd(self) -> None:
        """tes3 runs decode_all unconditionally; plain base64 is rejected."""
        import base64

        raw = base64.b64decode(encode_field(b"hello" * 100))
        assert raw.startswith(b"\x28\xb5\x2f\xfd")


class TestHeader:
    """The masters list is what makes references resolve."""

    def test_masters_are_required(self) -> None:
        """A plugin with no masters loads and is wrong, so this refuses."""
        with pytest.raises(EmitError, match="must declare its masters"):
            build_header([], num_objects=1)

    def test_a_master_with_no_size_is_refused(self) -> None:
        """The engine matches on name *and* size; a guess would be silent."""
        with pytest.raises(EmitError, match="no usable size"):
            build_header([("Morrowind.esm", 0)], num_objects=1)

    def test_the_record_count_is_carried(self) -> None:
        """num_objects has to match what follows."""
        header = build_header(MASTERS, num_objects=7)
        assert header["num_objects"] == 7
        assert header["file_type"] == "Esp"


class TestLandscapeRecord:
    """One merged cell."""

    def test_an_empty_record_is_refused(self) -> None:
        """A LAND with no layers would replace real terrain with nothing."""
        with pytest.raises(EmitError, match="no layers"):
            build_landscape_record((0, 0))

    def test_heights_declare_heights_and_normals(self) -> None:
        """The format stores the two under one flag."""
        record, _ = build_landscape_record((1, 2), heights=heights())
        assert record["landscape_flags"] == "USES_VERTEX_HEIGHTS_AND_NORMALS"
        assert record["grid"] == [1, 2]

    def test_normals_are_recomputed_from_heights(self) -> None:
        """Terrain lit from stale normals looks like a rendering bug."""
        record, _ = build_landscape_record((0, 0), heights=heights())
        assert "vertex_normals" in record

    def test_every_grid_is_present_even_when_unused(self) -> None:
        """tes3 declares all five as plain fields, not Options, so its reader
        requires each one. Omitting world_map_data is rejected outright with
        "missing field `world_map_data`" -- found by an actual write."""
        record, _ = build_landscape_record((0, 0), heights=heights())
        for field in (
            "vertex_heights",
            "vertex_normals",
            "world_map_data",
            "vertex_colors",
            "texture_indices",
        ):
            assert field in record

    def test_an_unused_grid_is_written_but_not_declared(self) -> None:
        """A zero grid the flags do not mention is inert, not a black cell."""
        record, _ = build_landscape_record((0, 0), heights=heights())
        assert "USES_VERTEX_COLORS" not in record["landscape_flags"]
        assert FLAG_TEXTURES not in record["landscape_flags"]

    def test_no_world_map_flag_is_ever_emitted(self) -> None:
        """LandscapeFlags has three bits and none of them is a world map.

        An invented ``USES_WORLD_MAP_DATA`` is rejected with "unrecognized
        named flag", which is how this was established.
        """
        record, _ = build_landscape_record((0, 0), heights=heights())
        assert "WORLD_MAP" not in record["landscape_flags"]

    def test_missing_world_map_is_derived_from_heights(self) -> None:
        """A cell with heights but no carried WNAM must not paint at sea level.

        This is the brown-cells-over-water bug: an underwater cell whose source
        edited heights without shipping a world map used to emit a zero WNAM,
        which the world map renders as brown coast. The map is now derived from
        the merged heights, so below-sea-level terrain reads as water.
        """
        record, _ = build_landscape_record((0, 0), heights=heights(-1500.0))
        grid = decode_world_map(record["world_map_data"]["data"])
        flat = [v for row in grid for v in row]
        assert all(v < 0 for v in flat)

    def test_an_all_zero_carried_world_map_is_derived_from_heights(self) -> None:
        """A carried WNAM of all zeros is the sentinel, not a real map.

        This is the brown-square bug as it actually reaches emit: the world map
        is *present* but all zeros -- a source plugin that edited heights
        without regenerating its map exports a zero grid, and the merge carries
        the zeros. Zero is sea level (brown coast), so an underwater cell paints
        as land over ocean. An all-zero carried map is therefore re-derived from
        the heights exactly as an absent one is; only a non-zero carried map is
        trusted (see the preservation test below).
        """
        zero_map = [[0] * 9 for _ in range(9)]
        record, _ = build_landscape_record((0, 0), heights=heights(-1500.0), world_map=zero_map)
        flat = [v for row in decode_world_map(record["world_map_data"]["data"]) for v in row]
        assert all(v < 0 for v in flat)

    def test_a_carried_map_over_dry_land_is_preserved(self) -> None:
        """Above sea level, a real carried WNAM is left exactly as it is.

        Derivation is only a fallback for an absent or all-zero map, and the
        water-safe correction only touches vertices whose terrain is underwater.
        Over dry land (heights above zero) a carried map passes through untouched.
        """
        carried = [[42] * 9 for _ in range(9)]
        record, _ = build_landscape_record((0, 0), heights=heights(1500.0), world_map=carried)
        assert decode_world_map(record["world_map_data"]["data"]) == carried

    def test_underwater_land_in_a_carried_map_is_sunk_to_water(self) -> None:
        """A carried map that paints land below sea level is the brown square.

        Wherever a deep-water master meets a merged cell, the carried map can
        show land (>= 0) over terrain that is underwater. Every such vertex is
        forced to water (-1); a genuine deep-water value (-128) is left alone,
        because the correction only ever pushes toward water.
        """
        carried = [[0] * 9 for _ in range(9)]
        carried[8][8] = -128  # an intentional deep-water vertex
        record, _ = build_landscape_record((0, 0), heights=heights(-1500.0), world_map=carried)
        grid = decode_world_map(record["world_map_data"]["data"])
        flat = [v for row in grid for v in row]
        assert all(v < 0 for v in flat), "an underwater vertex still paints land"
        assert grid[8][8] == -128, "an intentional deep-water value was disturbed"

    def test_world_map_from_heights_matches_the_engine_downsample(self) -> None:
        """WNAM samples every eighth vertex and divides by 128."""
        # Vertex row v has height v * 128, so its map value is exactly v.
        grid = [[float(v * 128) for _ in range(65)] for v in range(65)]
        derived = world_map_from_heights(grid)
        # Map row gy samples vertex gy*8, whose value is gy*8.
        assert derived[0] == [0] * 9
        assert derived[1] == [8] * 9
        assert derived[8] == [min(127, 64)] * 9

    def test_world_map_from_heights_clamps_to_int8(self) -> None:
        """Deep water past the int8 floor saturates rather than wrapping."""
        derived = world_map_from_heights(heights(-99999.0))
        assert all(v == -128 for row in derived for v in row)

    def test_sink_underwater_land_only_pushes_toward_water(self) -> None:
        """The rule is one-directional: land over water sinks, nothing rises.

        A shallow-underwater vertex whose downsample rounds up to zero, and a
        carried land value over water, both go to -1. A real above-water value
        and an already-deep-water value are both left exactly as they are.
        """
        # gy=0 sampled at height -50 (underwater; round(-50/128) == 0 -> land),
        # gy=1 at +900 (dry land), gy=2 at -20000 (deep water).
        rows = [[0.0] * 65 for _ in range(65)]
        for x in range(65):
            rows[0][x] = -50.0
            rows[8][x] = 900.0
            rows[16][x] = -20000.0
        world_map = [[0] * 9 for _ in range(9)]
        world_map[1] = [7] * 9  # a real above-water map value
        world_map[2] = [-128] * 9  # already deep water
        sunk = sink_underwater_land(world_map, rows)
        assert sunk[0] == [-1] * 9, "underwater land was not sunk"
        assert sunk[1] == [7] * 9, "dry-land value was disturbed"
        assert sunk[2] == [-128] * 9, "deep-water value was disturbed"


class TestAttachTextures:
    """The seam between the two passes."""

    def test_the_flag_is_added_with_the_payload(self) -> None:
        """Writing 512 bytes without declaring them is read as no texture data."""
        record, _ = build_landscape_record((0, 0), heights=heights())
        attach_texture_indices(record, textures(3))
        assert FLAG_TEXTURES in record["landscape_flags"]
        assert "texture_indices" in record

    def test_existing_flags_survive(self) -> None:
        """Attaching textures must not drop the heights declaration."""
        record, _ = build_landscape_record((0, 0), heights=heights())
        attach_texture_indices(record, textures(3))
        assert "USES_VERTEX_HEIGHTS_AND_NORMALS" in record["landscape_flags"]

    def test_the_flag_is_not_duplicated(self) -> None:
        """Attaching twice should not produce a doubled flag string."""
        record, _ = build_landscape_record((0, 0), heights=heights())
        attach_texture_indices(record, textures(3))
        attach_texture_indices(record, textures(4))
        assert record["landscape_flags"].count(FLAG_TEXTURES) == 1

    def test_the_grid_round_trips(self) -> None:
        """The 4x4 swizzle has to be undone on write and redone on read."""
        record, _ = build_landscape_record((0, 0), heights=heights())
        grid = [[(y * 16 + x) % 40 for x in range(16)] for y in range(16)]
        attach_texture_indices(record, grid)
        assert decode_texture_indices(record["texture_indices"]["data"]) == grid

    def test_a_wrong_shape_is_refused(self) -> None:
        """A short grid would misplace every index after the truncation."""
        record, _ = build_landscape_record((0, 0), heights=heights())
        with pytest.raises(EmitError):
            attach_texture_indices(record, [[0] * 16])


class TestPackTextures:
    """VTEX is stored as sixteen 4x4 blocks, not row-major."""

    def test_payload_size(self) -> None:
        """16 * 16 uint16."""
        assert len(pack_texture_indices(textures())) == 512

    def test_packing_inverts_decoding(self) -> None:
        """Round-tripping a non-uniform grid is the only real check."""
        grid = [[(x * 7 + y * 13) % 64 for x in range(16)] for y in range(16)]
        assert decode_texture_indices(pack_texture_indices(grid)) == grid


class TestCompaction:
    """Only ship LTEX records the merged terrain actually references."""

    def _table(self, count: int) -> KnownTextures:
        """A shared table of ``count`` textures."""
        known = KnownTextures()
        known.observe(
            "a.esp",
            [
                {"type": "LandscapeTexture", "id": f"T{i}", "index": i, "file_name": f"t{i}.tga"}
                for i in range(count)
            ],
        )
        return known

    def test_unused_textures_are_dropped(self) -> None:
        """The point: a big table and a small merge should emit a small table."""
        known = self._table(50)
        _, kept, _ = compact_textures(known, {0, vtex_of(4), vtex_of(9)})
        assert [t.identifier for t in kept] == ["T4", "T9"]

    def test_survivors_are_renumbered_contiguously(self) -> None:
        """Gaps would leave indices nothing defines."""
        known = self._table(50)
        _, kept, _ = compact_textures(known, {0, vtex_of(4), vtex_of(9)})
        assert [t.index for t in kept] == [0, 1]

    def test_the_mapping_moves_grids_to_the_new_numbering(self) -> None:
        """A grid rewritten by this mapping must index the emitted records."""
        known = self._table(50)
        mapping, kept, _ = compact_textures(known, {0, vtex_of(4), vtex_of(9)})
        assert mapping[vtex_of(4)] == vtex_of(kept[0].index)
        assert mapping[vtex_of(9)] == vtex_of(kept[1].index)

    def test_no_texture_is_always_retained(self) -> None:
        """Unpainted terrain is a real value, and must stay translatable."""
        mapping, _, _ = compact_textures(self._table(5), {0})
        assert mapping[0] == 0

    def test_an_index_with_no_record_is_left_alone_when_opted_out(self) -> None:
        """With substitution off, a missing master's index passes through."""
        mapping, kept, unresolved = compact_textures(
            self._table(3), {0, vtex_of(99)}, substitute_unknown=False
        )
        assert vtex_of(99) not in mapping
        assert kept == []
        assert unresolved == [vtex_of(99)]

    def test_file_names_survive_compaction(self) -> None:
        """The emitted LTEX still has to name a texture file."""
        _, kept, _ = compact_textures(self._table(5), {0, vtex_of(2)})
        assert kept[0].file_name == "t2.tga"


class TestTextureRecords:
    """Emitting the compacted table."""

    def test_one_record_per_texture(self) -> None:
        """In index order, which is what the grids assume."""
        known = KnownTextures()
        known.observe("a.esp", [{"type": "LandscapeTexture", "id": "A", "index": 0}])
        _, kept, _ = compact_textures(known, {0, vtex_of(0)})
        records = build_texture_records(kept)
        assert [r["type"] for r in records] == ["LandscapeTexture"]
        assert records[0]["id"] == "A"

    def test_an_empty_table_emits_nothing(self) -> None:
        """A merge that paints with nothing needs no LTEX records."""
        assert build_texture_records([]) == []


class TestPlugin:
    """Assembling the document tes3conv converts."""

    def test_the_header_leads_and_counts_the_rest(self) -> None:
        """num_objects must match, or the plugin is malformed."""
        record, _ = build_landscape_record((0, 0), heights=heights())
        document = build_plugin([record], MASTERS)
        assert document[0]["type"] == "Header"
        assert document[0]["num_objects"] == 1
        assert len(document) == 2


class TestEmitEdgeBranches:
    """Shape guards, empty-layer fills, and the zstd backend fallback."""

    def test_pack_world_map_rejects_a_wrong_shape(self) -> None:
        """The world map must be exactly 9x9."""
        from wraithguard.land.emit import pack_world_map

        with pytest.raises(EmitError, match="world map must be"):
            pack_world_map([[0] * 9 for _ in range(8)])

    def test_pack_vertex_colors_rejects_a_wrong_shape(self) -> None:
        """Vertex colours must be exactly 65x65."""
        from wraithguard.land.emit import pack_vertex_colors

        with pytest.raises(EmitError, match="vertex colors must be"):
            pack_vertex_colors([[(0, 0, 0)] * 65 for _ in range(64)])

    def test_a_textures_only_record_zero_fills_the_other_layers(self) -> None:
        """With only textures supplied, the height/normal/colour/map fields are blanked."""
        record, _ = build_landscape_record((0, 0), textures=textures())
        # Heights, normals, world map and colours are all present but zero-filled.
        for field in ("vertex_heights", "vertex_normals", "world_map_data", "vertex_colors"):
            assert field in record
        assert record["texture_indices"]["data"]

    def test_compress_falls_back_to_the_zstandard_backend(self, monkeypatch) -> None:
        """When the stdlib zstd is unavailable, the third-party backend is used."""
        import sys

        from wraithguard.land.emit import _compress

        # Force `from compression import zstd` to raise ImportError so the
        # function takes its pre-3.14 path.
        monkeypatch.setitem(sys.modules, "compression", None)
        out = _compress(b"merged landscape bytes " * 20)
        assert isinstance(out, bytes)
        assert out  # a real zstd frame from the zstandard backend

    def test_compress_uses_the_stdlib_backend_when_present(self, monkeypatch) -> None:
        """On Python 3.14+ the ``compression.zstd`` stdlib module is preferred."""
        import sys
        import types

        from wraithguard.land.emit import _compress

        # Emulate the 3.14 standard library so the pre-3.14 fallback is not taken.
        compression = types.ModuleType("compression")
        zstd = types.ModuleType("compression.zstd")
        zstd.compress = lambda raw: b"STDLIB:" + raw  # type: ignore[attr-defined]
        compression.zstd = zstd  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "compression", compression)
        monkeypatch.setitem(sys.modules, "compression.zstd", zstd)

        assert _compress(b"payload") == b"STDLIB:payload"
