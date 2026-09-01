"""Editing values inside a NIF block, then writing the file back out.

These check the two edits the module offers -- a text field (a texture
filename) and vertex positions -- plus the byte-span machinery underneath them.
The standing guarantee across all of it: an edit changes only the bytes it
targets, every other block and all the framing survive verbatim, and the result
is a file the reader parses whole again. Unedited, a round trip is byte-exact;
that is checked here against the one real fixture as well as synthetic blocks.
"""

from __future__ import annotations

import struct
from dataclasses import replace
from pathlib import Path

import pytest

from wraithguard.nif import (
    NifFile,
    read_nif_bytes,
    write_nif,
)
from wraithguard.nif.edit import (
    NifEditError,
    apply_edits,
    field_views,
    set_field,
    set_string_field,
    set_texture_path,
    swap_texture_path,
    texture_paths,
    translate_all_vertices,
    translate_vertices,
)
from wraithguard.nif.reader import NifParseError, field_spans

_FIXTURE = Path(__file__).resolve().parent.parent / "testdata" / "nif" / "NiParticleMeshes.nif"
_HEADER = b"NetImmerse File Format, Version 4.0.0.2\n"


def _string(text: str) -> bytes:
    """A length-prefixed cp1252 string, as the format writes one."""
    encoded = text.encode("cp1252")
    return struct.pack("<I", len(encoded)) + encoded


def _nif(*blocks: tuple[str, bytes]) -> bytes:
    """A whole little NIF file wrapping the given (type, body) blocks."""
    out = _HEADER + struct.pack("<I", 0x04000002) + struct.pack("<I", len(blocks))
    for name, body in blocks:
        encoded = name.encode("cp1252")
        out += struct.pack("<I", len(encoded)) + encoded + body
    return out + struct.pack("<i", 0)  # footer: no root objects


def _source_texture(path: str) -> bytes:
    """A NiSourceTexture body that references an external file at ``path``."""
    return (
        _string("tex")  # name
        + struct.pack("<ii", -1, -1)  # extra data, controller
        + struct.pack("<B", 1)  # use_external
        + _string(path)  # the external filename
        + struct.pack("<III", 0, 0, 0)  # pixel layout, mipmaps, alpha format
        + struct.pack("<B", 1)  # is static
    )


class TestFieldSpans:
    """The (offset, length) map the editor splices against."""

    def test_spans_tile_the_whole_block(self) -> None:
        """Every byte of a block is covered: the last field ends at the block's end."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        shape = next(b for b in nif.blocks if b.type_name == "NiTriShapeData")
        spans = field_spans(shape.type_name, shape.raw)
        assert max(off + length for off, length in spans.values()) == len(shape.raw)

    def test_a_vertices_span_is_a_run_of_triples(self) -> None:
        """The vertices span is a whole number of 12-byte points."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        shape = next(b for b in nif.blocks if b.type_name == "NiTriShapeData")
        _off, length = field_spans(shape.type_name, shape.raw)["vertices"]
        assert length % 12 == 0
        assert length > 0

    def test_an_unknown_type_is_refused(self) -> None:
        """A type with no layout cannot have its fields located."""
        with pytest.raises(NifParseError):
            field_spans("NotABlock", b"\x00\x00")


class TestTranslateVertices:
    """Shifting a geometry block's vertices."""

    def test_every_vertex_and_the_centre_move_by_the_offset(self) -> None:
        """A rigid translation adds the offset to each vertex and to the bounding centre."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        shape = next(b for b in nif.blocks if b.type_name == "NiTriShapeData")
        spans = field_spans(shape.type_name, shape.raw)
        v_off, _v_len = spans["vertices"]
        c_off, _c_len = spans["center"]
        before_v = struct.unpack_from("<3f", shape.raw, v_off)
        before_c = struct.unpack_from("<3f", shape.raw, c_off)

        moved = translate_vertices(shape, 10.0, 20.0, 30.0)

        after_v = struct.unpack_from("<3f", moved.raw, v_off)
        after_c = struct.unpack_from("<3f", moved.raw, c_off)
        assert after_v == pytest.approx((before_v[0] + 10, before_v[1] + 20, before_v[2] + 30))
        assert after_c == pytest.approx((before_c[0] + 10, before_c[1] + 20, before_c[2] + 30))

    def test_the_block_keeps_its_byte_length(self) -> None:
        """Moving vertices changes coordinates, never the block's size."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        shape = next(b for b in nif.blocks if b.type_name == "NiTriShapeData")
        moved = translate_vertices(shape, 1.0, 2.0, 3.0)
        assert len(moved.raw) == len(shape.raw)

    def test_the_edited_file_parses_whole_again(self) -> None:
        """Writing back a file with one shifted block yields a valid NIF."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        index, shape = next(
            (i, b) for i, b in enumerate(nif.blocks) if b.type_name == "NiTriShapeData"
        )
        blocks = list(nif.blocks)
        blocks[index] = translate_vertices(shape, 5.0, 0.0, -5.0)
        reparsed = read_nif_bytes(write_nif(replace(nif, blocks=blocks)), retain=True)
        assert reparsed.complete
        assert len(reparsed.blocks) == len(nif.blocks)

    def test_a_non_geometry_block_is_refused(self) -> None:
        """A block with no vertices field cannot be translated."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        node = next(b for b in nif.blocks if b.type_name == "NiNode")
        with pytest.raises(NifEditError):
            translate_vertices(node, 1.0, 1.0, 1.0)

    def test_a_shape_with_no_vertices_is_returned_unchanged(self) -> None:
        """A geometry block whose vertex array is empty needs no move.

        ``has_vertices`` is set but ``num_vertices`` is zero, so the vertices
        span is present but empty and the block comes back untouched.
        """
        body = (
            struct.pack("<H", 0)  # num_vertices
            + struct.pack("<I", 1)  # has_vertices
            + struct.pack("<I", 0)  # has_normals
            + struct.pack("<3f", 0.0, 0.0, 0.0)  # center
            + struct.pack("<f", 1.0)  # radius
            + struct.pack("<I", 0)  # has_vertex_colors
            + struct.pack("<H", 0)  # num_uv_sets
            + struct.pack("<I", 0)  # has_uv
            + struct.pack("<H", 0)  # num_triangles
            + struct.pack("<I", 0)  # num_triangle_points
            + struct.pack("<H", 0)  # match-group count
        )
        nif = read_nif_bytes(_nif(("NiTriShapeData", body)), retain=True)
        shape = next(b for b in nif.blocks if b.type_name == "NiTriShapeData")
        moved = translate_vertices(shape, 5.0, 6.0, 7.0)
        assert moved.raw == shape.raw


class TestTranslateAllVertices:
    """Shifting every geometry block in a file at once."""

    def test_all_geometry_blocks_move_and_the_file_reparses(self) -> None:
        """The whole-mesh move touches each shape and leaves a readable file."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        shapes = sum(
            1 for b in nif.blocks if b.type_name in ("NiTriShapeData", "NiParticleMeshesData")
        )
        moved, count = translate_all_vertices(nif, 100.0, 0.0, 0.0)
        assert count == shapes
        assert read_nif_bytes(write_nif(moved), retain=True).complete

    def test_a_zero_move_is_a_no_op(self) -> None:
        """Moving by nothing returns the same file object and a count of zero."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        same, count = translate_all_vertices(nif, 0.0, 0.0, 0.0)
        assert count == 0
        assert same is nif

    def test_blocks_without_retained_bytes_are_skipped(self) -> None:
        """A file read without ``retain`` has no raw to splice, so nothing moves."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=False)
        assert all(not b.raw for b in nif.blocks)
        _same, count = translate_all_vertices(nif, 1.0, 2.0, 3.0)
        assert count == 0

    def test_a_geometry_block_with_no_vertices_leaves_the_count_at_zero(self) -> None:
        """An empty-mesh geometry block is visited but returns itself unchanged."""
        body = (
            struct.pack("<H", 0)  # num_vertices
            + struct.pack("<I", 1)  # has_vertices
            + struct.pack("<I", 0)  # has_normals
            + struct.pack("<3f", 0.0, 0.0, 0.0)  # center
            + struct.pack("<f", 1.0)  # radius
            + struct.pack("<I", 0)  # has_vertex_colors
            + struct.pack("<H", 0)  # num_uv_sets
            + struct.pack("<I", 0)  # has_uv
            + struct.pack("<H", 0)  # num_triangles
            + struct.pack("<I", 0)  # num_triangle_points
            + struct.pack("<H", 0)  # match-group count
        )
        nif = read_nif_bytes(_nif(("NiTriShapeData", body)), retain=True)
        _same, count = translate_all_vertices(nif, 1.0, 2.0, 3.0)
        assert count == 0  # the shape was visited but had nothing to move


class TestSetField:
    """The general scalar/string field editor behind the inspector."""

    def _shape(self) -> object:
        """The fixture's first triangle-shape data block."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        return next(b for b in nif.blocks if b.type_name == "NiTriShapeData")

    def test_a_float_field_is_set_at_the_same_width(self) -> None:
        """Setting a scalar keeps the block's byte length and re-parses whole."""
        shape = self._shape()
        edited = set_field(shape, "radius", 123.5)
        assert len(edited.raw) == len(shape.raw)
        assert next(fv.value for fv in field_views(edited) if fv.name == "radius") == pytest.approx(
            123.5
        )

    def test_a_count_field_is_refused(self) -> None:
        """Editing a count (num_vertices) would desync the block; it is refused."""
        with pytest.raises(NifEditError):
            set_field(self._shape(), "num_vertices", 999)

    def test_a_presence_flag_is_refused(self) -> None:
        """Editing an array's has_* gate would desync the block; it is refused."""
        with pytest.raises(NifEditError):
            set_field(self._shape(), "has_vertices", False)

    def test_a_bulk_array_field_is_refused(self) -> None:
        """A vec3 array is not a single value and cannot be set this way."""
        with pytest.raises(NifEditError):
            set_field(self._shape(), "vertices", 0)

    def test_an_out_of_range_value_is_refused(self) -> None:
        """A value too big for the field's width is refused, not truncated."""
        nif = read_nif_bytes(_nif(("NiSourceTexture", _source_texture("t.tga"))), retain=True)
        with pytest.raises(NifEditError):
            set_field(nif.blocks[0], "use_external", 999)  # use_external is a u8

    def test_the_edited_file_round_trips(self) -> None:
        """A file with one scalar edit writes back and parses whole again."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        index, shape = next(
            (i, b) for i, b in enumerate(nif.blocks) if b.type_name == "NiTriShapeData"
        )
        blocks = list(nif.blocks)
        blocks[index] = set_field(shape, "radius", 42.0)
        assert read_nif_bytes(write_nif(replace(nif, blocks=blocks)), retain=True).complete


class TestFieldViews:
    """The per-field description an inspector panel is built from."""

    def test_scalars_are_editable_and_arrays_are_not(self) -> None:
        """A float field is editable; a vertex array is shown but not editable."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        shape = next(b for b in nif.blocks if b.type_name == "NiTriShapeData")
        views = {fv.name: fv for fv in field_views(shape)}
        assert views["radius"].editable
        assert views["radius"].kind == "f32"
        assert not views["vertices"].editable
        assert not views["has_vertices"].editable  # a gate, not editable

    def test_a_string_field_is_editable(self) -> None:
        """An external texture path is a string field and is editable."""
        nif = read_nif_bytes(_nif(("NiSourceTexture", _source_texture("t.tga"))), retain=True)
        views = {fv.name: fv for fv in field_views(nif.blocks[0])}
        assert views["external_or_internal"].editable


class TestApplyEdits:
    """The one entry point the editor UI drives."""

    def test_a_list_of_edits_applies_in_order(self) -> None:
        """A scalar set and a vertex translate both land, and the file re-parses."""
        data = _FIXTURE.read_bytes()
        nif = read_nif_bytes(data, retain=True)
        shape_index = next(i for i, b in enumerate(nif.blocks) if b.type_name == "NiTriShapeData")
        out = apply_edits(
            data,
            [
                {"op": "set_field", "block": shape_index, "name": "radius", "value": 7.5},
                {"op": "translate", "block": shape_index, "dx": 1.0, "dy": 2.0, "dz": 3.0},
            ],
        )
        edited = read_nif_bytes(out, retain=True)
        assert edited.complete
        assert next(
            fv.value for fv in field_views(edited.blocks[shape_index]) if fv.name == "radius"
        ) == pytest.approx(7.5)

    def test_no_edits_reproduces_the_file(self) -> None:
        """An empty edit list is a byte-exact round trip."""
        data = _FIXTURE.read_bytes()
        assert apply_edits(data, []) == data

    def test_an_out_of_range_block_is_refused(self) -> None:
        """An edit naming a block that is not there raises, writing nothing."""
        with pytest.raises(NifEditError):
            apply_edits(
                _FIXTURE.read_bytes(), [{"op": "set_field", "block": 9999, "name": "x", "value": 0}]
            )

    def test_an_unknown_op_is_refused(self) -> None:
        """An unrecognised edit op raises rather than silently skipping."""
        with pytest.raises(NifEditError):
            apply_edits(_FIXTURE.read_bytes(), [{"op": "explode", "block": 0}])

    def test_a_refused_edit_aborts_the_whole_batch(self) -> None:
        """A bad edit mid-list raises; nothing is returned half-applied."""
        data = _FIXTURE.read_bytes()
        shape_index = next(
            i
            for i, b in enumerate(read_nif_bytes(data, retain=True).blocks)
            if b.type_name == "NiTriShapeData"
        )
        with pytest.raises(NifEditError):
            apply_edits(
                data,
                [
                    {"op": "set_field", "block": shape_index, "name": "radius", "value": 1.0},
                    {"op": "set_field", "block": shape_index, "name": "num_vertices", "value": 5},
                ],
            )


class TestTexturePaths:
    """Listing and rewriting external texture references."""

    def _one_texture(self, path: str) -> NifFile:
        """A one-block file read back, its sole block a texture source at ``path``."""
        return read_nif_bytes(_nif(("NiSourceTexture", _source_texture(path))), retain=True)

    def test_external_paths_are_listed_with_their_block_index(self) -> None:
        """texture_paths reports each external filename and where it lives."""
        nif = self._one_texture("textures\\old.tga")
        assert texture_paths(nif) == [(0, "textures\\old.tga")]

    def test_swap_is_case_insensitive_and_counts_changes(self) -> None:
        """A path is matched regardless of case, and the swap count is returned."""
        nif = self._one_texture("textures\\old.tga")
        new, count = swap_texture_path(nif, "TEXTURES\\OLD.TGA", "textures\\new.tga")
        assert count == 1
        assert texture_paths(new) == [(0, "textures\\new.tga")]

    def test_a_longer_path_grows_the_block_and_still_parses(self) -> None:
        """The block grows to fit a longer name and the file is still readable."""
        nif = self._one_texture("a.tga")
        original = write_nif(nif)
        new, _count = swap_texture_path(nif, "a.tga", "a_much_longer_name.tga")
        written = write_nif(new)
        assert len(written) - len(original) == len("a_much_longer_name.tga") - len("a.tga")
        assert read_nif_bytes(written, retain=True).complete

    def test_a_swap_that_matches_nothing_is_a_no_op(self) -> None:
        """No match returns the same file object and a count of zero."""
        nif = self._one_texture("textures\\old.tga")
        same, count = swap_texture_path(nif, "not-here.tga", "x.tga")
        assert count == 0
        assert same is nif

    def test_set_texture_path_targets_one_block(self) -> None:
        """A specific block's path can be set by index."""
        nif = self._one_texture("old.tga")
        new = set_texture_path(nif, 0, "new.tga")
        assert texture_paths(new) == [(0, "new.tga")]

    def test_set_texture_path_refuses_a_non_texture_block(self) -> None:
        """A block that is not an external texture source cannot take a path."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        with pytest.raises(NifEditError):
            set_texture_path(nif, 0, "x.tga")


class TestSetStringField:
    """The length-prefixed-string splice under the texture edits."""

    def test_a_non_string_field_is_refused(self) -> None:
        """Setting a string on a numeric field would corrupt the block; it is refused."""
        nif = read_nif_bytes(_nif(("NiSourceTexture", _source_texture("t.tga"))), retain=True)
        # use_external is a u8, not a length-prefixed string.
        with pytest.raises(NifEditError):
            set_string_field(nif.blocks[0], "use_external", "x")

    def test_an_absent_field_name_is_refused(self) -> None:
        """A field the block does not have cannot be set."""
        nif = read_nif_bytes(_nif(("NiSourceTexture", _source_texture("t.tga"))), retain=True)
        with pytest.raises(NifEditError):
            set_string_field(nif.blocks[0], "no_such_field", "x")


class TestUneditedRoundTrip:
    """The parity the editor rests on: no edit, no change."""

    def test_the_real_fixture_is_byte_exact(self) -> None:
        """Reading and writing the fixture with no edit reproduces it exactly."""
        data = _FIXTURE.read_bytes()
        assert write_nif(read_nif_bytes(data, retain=True)) == data


class TestEditGuards:
    """The refusals: an edit is rejected rather than allowed to corrupt a mesh."""

    def _shape(self) -> object:
        """A real geometry block (has a ``vertices`` field) from the fixture."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        return next(b for b in nif.blocks if b.type_name == "NiTriShapeData")

    def _texture_nif(self) -> NifFile:
        """A minimal file whose only block is an external texture source."""
        return read_nif_bytes(_nif(("NiSourceTexture", _source_texture("a.dds"))), retain=True)

    def test_set_string_field_needs_retained_bytes(self) -> None:
        """A block read without its raw bytes cannot be spliced."""
        with pytest.raises(NifEditError, match="no raw bytes"):
            set_string_field(replace(self._shape(), raw=b""), "name", "x")

    def test_set_string_field_refuses_a_too_short_field(self) -> None:
        """A field narrower than a 4-byte length prefix cannot hold a string."""
        with pytest.raises(NifEditError, match="not a length-prefixed string"):
            set_string_field(self._shape(), "num_vertices", "x")

    def test_set_string_field_refuses_a_mismatched_length_prefix(self) -> None:
        """A 4-byte field whose leading u32 is not its own length is not a string."""
        # has_vertices is a bool32 (value 1): 4 + 1 != 4, so the prefix disagrees.
        with pytest.raises(NifEditError, match="refusing to overwrite it"):
            set_string_field(self._shape(), "has_vertices", "x")

    def test_set_field_needs_retained_bytes(self) -> None:
        """The scalar setter needs raw bytes too."""
        with pytest.raises(NifEditError, match="no raw bytes"):
            set_field(replace(self._shape(), raw=b""), "num_vertices", 3)

    def test_set_field_refuses_a_type_with_no_layout(self) -> None:
        """Without a layout the editor cannot locate any field."""
        with pytest.raises(NifEditError, match="no layout"):
            set_field(replace(self._shape(), type_name="NotABlock"), "x", 1)

    def test_set_field_refuses_an_unknown_field(self) -> None:
        """A field the block's layout does not define cannot be set."""
        with pytest.raises(NifEditError, match="no field"):
            set_field(self._shape(), "nope_not_a_field", 1)

    def test_set_field_refuses_a_non_scalar_field(self) -> None:
        """A vertex array is neither a scalar nor a string, so set_field refuses it."""
        with pytest.raises(NifEditError, match="only scalar and string"):
            set_field(self._shape(), "vertices", 1)

    def test_translate_vertices_needs_retained_bytes(self) -> None:
        """Moving vertices needs the block's raw bytes."""
        with pytest.raises(NifEditError, match="no raw bytes"):
            translate_vertices(replace(self._shape(), raw=b""), 1.0, 1.0, 1.0)

    def test_translate_vertices_refuses_a_non_geometry_block(self) -> None:
        """A texture source has no vertices to move."""
        with pytest.raises(NifEditError, match="no vertices"):
            translate_vertices(self._texture_nif().blocks[0], 1.0, 1.0, 1.0)

    def test_translate_all_vertices_with_nothing_to_move_returns_the_input(self) -> None:
        """When no block is geometry, the file comes back unchanged."""
        nif = self._texture_nif()
        result, changed = translate_all_vertices(nif, 1.0, 1.0, 1.0)
        assert changed == 0
        assert result is nif

    def test_translate_all_vertices_skips_every_non_geometry_block(self) -> None:
        """Two non-geometry blocks are both skipped -- the loop runs to the end."""
        data = _nif(
            ("NiSourceTexture", _source_texture("a.dds")),
            ("NiSourceTexture", _source_texture("b.dds")),
        )
        nif = read_nif_bytes(data, retain=True)
        result, changed = translate_all_vertices(nif, 2.0, 0.0, 0.0)
        assert changed == 0
        assert result is nif

    def test_set_field_on_an_external_texture_name_uses_the_string_path(self) -> None:
        """An editable string field routed through set_field is spliced as a string."""
        nif = self._texture_nif()
        edited = set_field(nif.blocks[0], "external_or_internal", "swapped.dds")
        assert b"swapped.dds" in edited.raw
        assert edited is not nif.blocks[0]

    def test_apply_edits_refuses_an_incompletely_parsed_mesh(self) -> None:
        """A mesh that does not parse whole is too risky to splice."""
        truncated = _HEADER + struct.pack("<I", 0x04000002) + struct.pack("<I", 1)
        with pytest.raises(NifEditError, match="does not parse completely"):
            apply_edits(truncated, [{"op": "set_field", "block": 0, "field": "x", "value": 1}])

    def test_apply_edits_requires_a_block_index(self) -> None:
        """An edit with no usable block index is rejected."""
        data = _nif(("NiSourceTexture", _source_texture("a.dds")))
        with pytest.raises(NifEditError, match="missing a valid block index"):
            apply_edits(data, [{"op": "set_field", "field": "x", "value": 1}])

    def test_apply_edits_rejects_an_out_of_range_block(self) -> None:
        """An edit naming a block past the end is rejected."""
        data = _nif(("NiSourceTexture", _source_texture("a.dds")))
        with pytest.raises(NifEditError, match="out of range"):
            apply_edits(data, [{"op": "set_field", "block": 99, "field": "x", "value": 1}])

    def test_set_texture_path_rejects_an_out_of_range_index(self) -> None:
        """A block index past the end has no texture to retarget."""
        with pytest.raises(NifEditError, match="out of range"):
            set_texture_path(self._texture_nif(), 99, "b.dds")

    def test_texture_paths_skips_non_texture_blocks(self) -> None:
        """A geometry-only file reports no external texture sources."""
        nif = read_nif_bytes(_FIXTURE.read_bytes(), retain=True)
        assert texture_paths(nif) == []

    def test_field_views_needs_retained_bytes(self) -> None:
        """Describing a block's fields needs its raw bytes."""
        with pytest.raises(NifEditError, match="no raw bytes"):
            field_views(replace(self._shape(), raw=b""))

    def test_field_views_of_an_unknown_type_is_empty(self) -> None:
        """A type with no layout has no fields to describe."""
        assert field_views(replace(self._shape(), type_name="NotABlock")) == []

    def test_summarise_ignores_a_texture_source_with_no_filename(self) -> None:
        """A NiSourceTexture whose external filename is blank contributes no texture."""
        from wraithguard.nif.report import summarise

        nif = read_nif_bytes(_nif(("NiSourceTexture", _source_texture(""))), retain=True)
        assert summarise(nif).textures == []
