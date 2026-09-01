"""Tests for geometry retention and world-space composition.

Two things are being pinned down. That turning retention *on* adds data without
changing anything that was already there -- the scan path reads tens of
thousands of meshes and must not pay for a viewer feature. And that the
transform composition is actually composition, not addition, because the
difference only shows up once a parent is rotated and is invisible in the
identity-transform meshes that make up most of any corpus.
"""

from __future__ import annotations

import math
import struct

from wraithguard.nif.geometry import (
    Mesh,
    Transform,
    _triple,
    block_tree,
    bounds,
    find_roots,
    world_meshes,
)
from wraithguard.nif.reader import read_nif_bytes

HEADER = b"NetImmerse File Format, Version 4.0.0.2\n"


def text(value: str) -> bytes:
    """Length-prefixed, unterminated string.

    Args:
        value: The text.

    Returns:
        The encoded bytes.
    """
    raw = value.encode("cp1252")
    return struct.pack("<I", len(raw)) + raw


def nif(*blocks: tuple[str, bytes]) -> bytes:
    """Assemble a file from block bodies.

    Args:
        blocks: ``(type name, body)`` pairs in order.

    Returns:
        The file bytes.
    """
    out = [HEADER, struct.pack("<II", 0x04000002, len(blocks))]
    for type_name, body in blocks:
        out.append(text(type_name))
        out.append(body)
    return b"".join(out)


def av_body(
    name: str,
    *,
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation: tuple[float, ...] = (1, 0, 0, 0, 1, 0, 0, 0, 1),
    scale: float = 1.0,
    children: tuple[int, ...] = (),
    properties: tuple[int, ...] = (),
    tail: bytes = b"",
) -> bytes:
    """A scene-object body, optionally with children.

    Args:
        name: The object's name.
        translation: Its translation.
        rotation: Its 3x3 rotation, row major.
        scale: Its scale.
        children: Child block indices; makes this a node.
        properties: Property block indices.
        tail: Extra bytes after the node lists.

    Returns:
        The body bytes.
    """
    body = text(name) + struct.pack("<iiH", -1, -1, 0)
    body += struct.pack("<3f", *translation)
    body += struct.pack("<9f", *rotation)
    body += struct.pack("<f", scale)
    body += struct.pack("<3f", 0.0, 0.0, 0.0)
    body += struct.pack("<I", len(properties)) + b"".join(struct.pack("<i", p) for p in properties)
    body += struct.pack("<I", 0)
    if children or not tail:
        body += struct.pack("<I", len(children)) + b"".join(struct.pack("<i", c) for c in children)
        body += struct.pack("<I", 0)
    return body + tail


def shape_data(
    vertices: list[tuple[float, float, float]], triangles: list[tuple[int, int, int]]
) -> bytes:
    """A ``NiTriShapeData`` body carrying real geometry.

    Args:
        vertices: The vertex positions.
        triangles: Index triples.

    Returns:
        The body bytes.
    """
    body = struct.pack("<H", len(vertices)) + struct.pack("<I", 1)
    body += b"".join(struct.pack("<3f", *v) for v in vertices)
    body += struct.pack("<I", 0)
    body += struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
    body += struct.pack("<I", 0)
    body += struct.pack("<HI", 0, 0)
    body += struct.pack("<HI", len(triangles), len(triangles) * 3)
    body += b"".join(struct.pack("<3H", *t) for t in triangles)
    return body + struct.pack("<H", 0)


SQUARE = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)]
ONE_TRIANGLE = [(0, 1, 2)]


class TestRetentionAddsWithoutChanging:
    """Turning geometry on must not alter what was already reported."""

    def test_the_counts_stay_where_they_were(self) -> None:
        """A structure report goes on reading ``vertices``; a viewer reads ``vertices_xyz``."""
        data = nif(("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)))
        plain = read_nif_bytes(data).blocks[0].fields
        rich = read_nif_bytes(data, geometry=True).blocks[0].fields
        assert plain["vertices"] == rich["vertices"]
        assert plain["num_vertices"] == rich["num_vertices"]
        assert all(rich[key] == value for key, value in plain.items())

    def test_the_default_path_retains_nothing(self) -> None:
        """Otherwise every scan would carry geometry it never asked for."""
        fields = (
            read_nif_bytes(nif(("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE))))
            .blocks[0]
            .fields
        )
        assert not [key for key in fields if key.endswith(("_xyz", "_indices", "_links"))]

    def test_coordinates_come_back_as_written(self) -> None:
        """The whole point: real positions, not a byte count."""
        block = read_nif_bytes(
            nif(("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE))), geometry=True
        ).blocks[0]
        assert block.fields["vertices_xyz"] == SQUARE
        assert block.fields["triangles_indices"] == ONE_TRIANGLE


class TestTransformCompositionIsNotAddition:
    """The failure mode that identity-transform test data cannot reveal."""

    def test_a_rotated_parent_carries_its_child_offset(self) -> None:
        """Adding translations instead of composing puts children in the wrong place.

        A parent rotated 90 degrees about Z with a child offset along +X must
        put the child along +Y. Summing the offsets would leave it on +X, and
        every mesh with an unrotated parent -- almost all of them -- would still
        look right.
        """
        quarter_turn = Transform(rotation=((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)))
        child = Transform(translation=(2.0, 0.0, 0.0))
        combined = quarter_turn.then(child)
        x, y, z = combined.apply((0.0, 0.0, 0.0))
        assert math.isclose(x, 0.0, abs_tol=1e-6), (x, y, z)
        assert math.isclose(y, 2.0, abs_tol=1e-6), (x, y, z)

    def test_scale_multiplies_down_the_chain(self) -> None:
        """Two halvings make a quarter, not a half."""
        half = Transform(scale=0.5)
        combined = half.then(Transform(scale=0.5))
        assert math.isclose(combined.scale, 0.25)

    def test_identity_composes_to_identity(self) -> None:
        """A negative control, so the maths above is not merely busy."""
        combined = Transform().then(Transform())
        assert combined.apply((3.0, -4.0, 5.0)) == (3.0, -4.0, 5.0)


class TestWorldPlacement:
    """Shapes must land where their parents put them."""

    def test_a_shape_is_offset_by_its_parent_node(self) -> None:
        """The reason composition exists at all."""
        parsed = read_nif_bytes(
            nif(
                ("NiNode", av_body("root", translation=(10.0, 0.0, 0.0), children=(1,))),
                ("NiTriShape", av_body("shape", tail=struct.pack("<ii", 2, -1))),
                ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
            ),
            geometry=True,
        )
        meshes = world_meshes(parsed)
        assert len(meshes) == 1, parsed.stopped_reason
        assert meshes[0].vertices[0][0] == 10.0

    def test_roots_are_derived_not_assumed(self) -> None:
        """Block 0 is conventionally the root; convention is not evidence.

        Here block 0 is a *child* of block 1, so treating index 0 as the root
        would walk the tree from halfway down.
        """
        parsed = read_nif_bytes(
            nif(
                ("NiNode", av_body("child")),
                ("NiNode", av_body("parent", children=(0,))),
            ),
            geometry=True,
        )
        assert find_roots(parsed) == [1]

    def test_a_file_without_geometry_yields_no_meshes(self) -> None:
        """Honest emptiness: the data was never read, so there is none."""
        parsed = read_nif_bytes(
            nif(
                ("NiNode", av_body("root", children=(1,))),
                ("NiTriShape", av_body("shape", tail=struct.pack("<ii", 2, -1))),
                ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
            )
        )
        assert world_meshes(parsed) == []

    def test_a_cycle_does_not_hang(self) -> None:
        """Nothing in the format forbids a child link pointing back up.

        The files come from mod archives, so "no sane exporter would" is not a
        guarantee about the input.
        """
        parsed = read_nif_bytes(
            nif(
                ("NiNode", av_body("a", children=(1,))),
                ("NiNode", av_body("b", children=(0,))),
            ),
            geometry=True,
        )
        assert world_meshes(parsed) == []


class TestBounds:
    """A viewer needs to frame the thing before it can show it."""

    def test_bounds_span_every_vertex(self) -> None:
        """Across meshes, not just within one."""
        low, high = bounds(
            [
                Mesh("a", [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0)]),
                Mesh("b", [(-5.0, 0.0, 0.0)]),
            ]
        )
        assert low == (-5.0, 0.0, 0.0)
        assert high == (1.0, 2.0, 3.0)

    def test_empty_bounds_are_the_origin_not_an_error(self) -> None:
        """So a caller never has to special-case a mesh it could not read."""
        assert bounds([]) == ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


class TestBlockTree:
    """The structure pane's data: everything, including what never draws."""

    def _parsed(self):
        """A file with a shape, its data, and a collision node.

        Returns:
            The parsed file.
        """
        return read_nif_bytes(
            nif(
                ("NiNode", av_body("root", children=(1, 3))),
                ("NiTriShape", av_body("shape", tail=struct.pack("<ii", 2, -1))),
                ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
                ("RootCollisionNode", av_body("collide")),
            ),
            geometry=True,
        )

    def test_the_hierarchy_is_nested_under_its_root(self) -> None:
        """A flat list would lose the thing the pane exists to show."""
        roots = block_tree(self._parsed())
        assert len(roots) == 1
        assert roots[0].type_name == "NiNode"
        assert [child.type_name for child in roots[0].children] == [
            "NiTriShape",
            "RootCollisionNode",
        ]

    def test_blocks_that_never_draw_are_listed(self) -> None:
        """Collision is invisible in a render and decides whether you fall through it.

        This is the whole argument for the pane: the 3D view structurally
        cannot show it.
        """
        roots = block_tree(self._parsed())
        collision = [c for c in roots[0].children if c.type_name == "RootCollisionNode"]
        assert collision and collision[0].note == "collision"

    def test_data_blocks_carry_their_counts(self) -> None:
        """So a shape's weight is visible without selecting it."""
        roots = block_tree(self._parsed())
        shape = roots[0].children[0]
        data = [c for c in shape.children if c.type_name == "NiTriShapeData"]
        assert data and "3 verts" in data[0].note and "1 tris" in data[0].note

    def test_orphans_are_still_shown(self) -> None:
        """A block nothing references is exactly what a reader wants to know about."""
        parsed = read_nif_bytes(
            nif(
                ("NiNode", av_body("root")),
                ("NiZBufferProperty", text("orphan") + struct.pack("<iiH", -1, -1, 0)),
            ),
            geometry=True,
        )
        assert [n.type_name for n in block_tree(parsed)] == ["NiNode", "NiZBufferProperty"]

    def test_a_cycle_does_not_hang(self) -> None:
        """Nothing in the format forbids a child link pointing back up."""
        parsed = read_nif_bytes(
            nif(
                ("NiNode", av_body("a", children=(1,))),
                ("NiNode", av_body("b", children=(0,))),
            ),
            geometry=True,
        )
        roots = block_tree(parsed)
        assert roots, "a cycle should still produce a tree"


def material_body(
    diffuse: tuple[float, float, float] = (1.0, 1.0, 1.0),
    emissive: tuple[float, float, float] = (0.0, 0.0, 0.0),
    alpha: float = 1.0,
) -> bytes:
    """A ``NiMaterialProperty`` body.

    The field order is ambient, diffuse, specular, emissive, shine, alpha --
    confirmed against Greatness7's ``tes3``. Writing it here in that order is
    what makes the test meaningful: a reader that read diffuse where emissive
    lives would still return three floats and look plausible.

    Args:
        diffuse: The diffuse color.
        emissive: The emissive color.
        alpha: The material's own opacity.

    Returns:
        The body bytes.
    """
    body = text("mat") + struct.pack("<iiH", -1, -1, 0)
    body += struct.pack("<3f", 0.1, 0.1, 0.1)  # ambient
    body += struct.pack("<3f", *diffuse)
    body += struct.pack("<3f", 0.5, 0.5, 0.5)  # specular
    body += struct.pack("<3f", *emissive)
    body += struct.pack("<f", 10.0)  # shine
    return body + struct.pack("<f", alpha)


def alpha_body(flags: int, threshold: int) -> bytes:
    """A ``NiAlphaProperty`` body.

    Args:
        flags: The property flags, carrying blend and test bits.
        threshold: The alpha-test reference byte.

    Returns:
        The body bytes.
    """
    return text("alpha") + struct.pack("<iiH", -1, -1, flags) + struct.pack("<B", threshold)


def shape_with(properties: tuple[int, ...]) -> bytes:
    """A ``NiTriShape`` body pointing at property blocks and data block 1.

    Args:
        properties: Property block indices.

    Returns:
        The body bytes.
    """
    return av_body("s", properties=properties, tail=struct.pack("<ii", 1, -1))


class TestMaterialsComeFromTheFile:
    """What a shape is made of, not what a viewer guesses.

    Every layout here was confirmed field-for-field against Greatness7's
    ``tes3`` before being read. That matters more than usual: a material is
    four consecutive three-float colors, so reading the wrong one returns a
    perfectly well-formed color that is simply the wrong one, and nothing
    downstream can tell.
    """

    def test_diffuse_and_emissive_are_not_confused(self) -> None:
        """They sit two colors apart, with specular between them."""
        data = nif(
            ("NiTriShape", shape_with((2,))),
            ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
            (
                "NiMaterialProperty",
                material_body(diffuse=(1.0, 0.0, 0.0), emissive=(0.0, 0.0, 1.0)),
            ),
        )
        mesh = world_meshes(read_nif_bytes(data, geometry=True))[0]
        assert mesh.diffuse == (1.0, 0.0, 0.0)
        assert mesh.emissive == (0.0, 0.0, 1.0)

    def test_no_material_property_means_undescribed_not_black(self) -> None:
        """``None`` rather than ``(0, 0, 0)``.

        A caller that cannot tell "no material" from "black material" renders
        every unmaterialed shape as a silhouette, which is a plausible-looking
        result and completely wrong.
        """
        data = nif(
            ("NiTriShape", shape_with(())),
            ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
        )
        mesh = world_meshes(read_nif_bytes(data, geometry=True))[0]
        assert mesh.diffuse is None
        assert mesh.emissive is None
        assert mesh.opacity == 1.0

    def test_opacity_is_the_materials_own_alpha(self) -> None:
        """The last float in the property, after shine."""
        data = nif(
            ("NiTriShape", shape_with((2,))),
            ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
            ("NiMaterialProperty", material_body(alpha=0.25)),
        )
        mesh = world_meshes(read_nif_bytes(data, geometry=True))[0]
        assert math.isclose(mesh.opacity, 0.25, rel_tol=1e-6)


class TestAlphaIsTwoIndependentQuestions:
    """Blending and testing are separate bits, and conflating them is the bug.

    Foliage routinely sets testing without blending. Treating "has an alpha
    property" as "is translucent" makes every leaf in the game fade instead of
    cut out -- and a faded leaf looks like a rendering choice rather than an
    error, so nothing would report it.
    """

    def test_testing_without_blending(self) -> None:
        """The foliage case: bit 0x0200 set, bit 0x0001 clear."""
        data = nif(
            ("NiTriShape", shape_with((2,))),
            ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
            ("NiAlphaProperty", alpha_body(0x0200, 128)),
        )
        mesh = world_meshes(read_nif_bytes(data, geometry=True))[0]
        assert mesh.alpha_test
        assert not mesh.alpha_blend

    def test_blending_without_testing(self) -> None:
        """The glass case, and the negative control for the one above."""
        data = nif(
            ("NiTriShape", shape_with((2,))),
            ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
            ("NiAlphaProperty", alpha_body(0x0001, 0)),
        )
        mesh = world_meshes(read_nif_bytes(data, geometry=True))[0]
        assert mesh.alpha_blend
        assert not mesh.alpha_test

    def test_the_threshold_is_normalised_from_the_stored_byte(self) -> None:
        """The file stores 0-255; a renderer wants 0-1."""
        data = nif(
            ("NiTriShape", shape_with((2,))),
            ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
            ("NiAlphaProperty", alpha_body(0x0200, 255)),
        )
        mesh = world_meshes(read_nif_bytes(data, geometry=True))[0]
        assert math.isclose(mesh.alpha_threshold, 1.0, rel_tol=1e-6)

    def test_no_alpha_property_reads_as_opaque(self) -> None:
        """Nothing describing transparency means opaque, not unknown."""
        data = nif(
            ("NiTriShape", shape_with(())),
            ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
        )
        mesh = world_meshes(read_nif_bytes(data, geometry=True))[0]
        assert not mesh.alpha_blend
        assert not mesh.alpha_test


def shape_data_coloured(
    vertices: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
    colors: list[tuple[float, float, float, float]],
) -> bytes:
    """A ``NiTriShapeData`` body that actually carries vertex colors.

    The ordinary fixture writes ``has_vertex_colors = 0``, so it never
    exercised the colour path at all -- which is how the reader could hold a
    count where a caller expected a list without any test noticing.

    Args:
        vertices: The vertex positions.
        triangles: Index triples.
        colors: One RGBA per vertex, channels 0-1.

    Returns:
        The body bytes.
    """
    body = struct.pack("<H", len(vertices)) + struct.pack("<I", 1)
    body += b"".join(struct.pack("<3f", *v) for v in vertices)
    body += struct.pack("<I", 0)  # has_normals
    body += struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)  # centre + radius
    body += struct.pack("<I", 1)  # has_vertex_colors
    body += b"".join(struct.pack("<4f", *c) for c in colors)
    body += struct.pack("<HI", 0, 0)  # num_uv_sets, has_uv
    body += struct.pack("<HI", len(triangles), len(triangles) * 3)
    body += b"".join(struct.pack("<3H", *t) for t in triangles)
    return body + struct.pack("<H", 0)


class TestVertexColoursSurviveTheReader:
    """The field existed long before it held anything usable.

    ``color4_array`` was not in the reader's retained set, so
    ``fields["vertex_colors"]`` held the *count* the array gate produced. Any
    caller checking ``len(...) == len(vertices)`` compared against an integer,
    failed closed, and reported no colours -- with no error anywhere, because
    the field was present and held a number.
    """

    def test_the_decoded_companion_carries_the_colours(self) -> None:
        """``vertex_colors_rgba`` beside the count, as every other array does."""
        colours = [(1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0), (0.0, 0.0, 1.0, 0.5)]
        data = nif(("NiTriShapeData", shape_data_coloured(SQUARE, ONE_TRIANGLE, colours)))
        fields = read_nif_bytes(data, geometry=True).blocks[0].fields
        assert fields["vertex_colors_rgba"] == colours

    def test_the_count_still_lives_under_the_plain_name(self) -> None:
        """A structure report must not change behaviour when geometry is on."""
        colours = [(1.0, 1.0, 1.0, 1.0)] * 3
        data = nif(("NiTriShapeData", shape_data_coloured(SQUARE, ONE_TRIANGLE, colours)))
        plain = read_nif_bytes(data).blocks[0].fields
        rich = read_nif_bytes(data, geometry=True).blocks[0].fields
        assert plain["vertex_colors"] == rich["vertex_colors"]

    def test_a_mesh_exposes_them_one_per_vertex(self) -> None:
        """The whole point: a renderer can attach them as an attribute."""
        colours = [(1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0)]
        data = nif(
            ("NiTriShape", shape_with(())),
            ("NiTriShapeData", shape_data_coloured(SQUARE, ONE_TRIANGLE, colours)),
        )
        mesh = world_meshes(read_nif_bytes(data, geometry=True))[0]
        assert mesh.vertex_colors == colours

    def test_a_shape_without_them_reports_none(self) -> None:
        """A negative control, so the presence of colours means something."""
        data = nif(
            ("NiTriShape", shape_with(())),
            ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
        )
        assert world_meshes(read_nif_bytes(data, geometry=True))[0].vertex_colors == []


class TestTripleCoercion:
    """``_triple`` turns a retained vector into a colour, or nothing."""

    def test_a_three_element_sequence_becomes_floats(self) -> None:
        """A length-3 list/tuple coerces element-wise to floats."""
        assert _triple([1, 2, 3]) == (1.0, 2.0, 3.0)
        assert _triple((4, 5, 6)) == (4.0, 5.0, 6.0)

    def test_a_missing_or_wrong_shaped_value_is_none(self) -> None:
        """Anything that is not a 3-element sequence yields no colour."""
        assert _triple(None) is None
        assert _triple([1, 2]) is None
        assert _triple("nope") is None


class TestBlockTreeNotes:
    """The one-line summary each block row shows in the viewer's tree."""

    def _source_texture(self, path: str) -> bytes:
        return (
            text("tex")
            + struct.pack("<ii", -1, -1)  # extra, controller
            + struct.pack("<B", 1)  # use_external
            + text(path)  # the external filename
            + struct.pack("<III", 0, 0, 0)  # pixel layout, mipmaps, alpha
            + struct.pack("<B", 1)  # is static
        )

    def test_an_external_source_texture_names_its_file(self) -> None:
        """A NiSourceTexture row shows the texture it points at."""
        parsed = read_nif_bytes(
            nif(("NiSourceTexture", self._source_texture("textures/rock.dds"))), geometry=True
        )
        assert block_tree(parsed)[0].note == "textures/rock.dds"

    def test_a_skin_instance_reads_as_skinned(self) -> None:
        """A NiSkinInstance row is summarised as skinned geometry."""
        parsed = read_nif_bytes(
            nif(("NiSkinInstance", struct.pack("<iiI", -1, -1, 0))), geometry=True
        )
        assert block_tree(parsed)[0].note == "skinned"

    def test_a_controller_reads_as_animated(self) -> None:
        """Any *Controller row is summarised as animation."""
        body = (
            struct.pack("<i", -1)  # next_controller
            + struct.pack("<H", 0)  # flags
            + struct.pack("<4f", 0.0, 0.0, 0.0, 0.0)  # frequency, phase, start, stop
            + struct.pack("<i", -1)  # target
            + bytes(111)  # emitter parameters
            + struct.pack("<H", 0)  # num_particles
            + struct.pack("<H", 0)  # num_live_particles
            + bytes(13)  # unidentified tail
        )
        parsed = read_nif_bytes(nif(("NiParticleSystemController", body)), geometry=True)
        assert block_tree(parsed)[0].note == "animated"

    def test_a_shape_with_no_data_block_yields_no_mesh(self) -> None:
        """A NiTriShape whose data link is empty produces nothing to draw."""
        data = nif(("NiTriShape", av_body("s", tail=struct.pack("<ii", -1, -1))))
        assert world_meshes(read_nif_bytes(data, geometry=True)) == []

    def test_a_scene_deeper_than_the_limit_stops(self) -> None:
        """A chain longer than the depth cap is truncated with a warning, not a hang."""
        # 66 nodes each parenting the next -> past the 64-deep limit.
        blocks = [("NiNode", av_body(f"n{i}", children=(i + 1,))) for i in range(65)]
        blocks.append(("NiNode", av_body("leaf")))
        parsed = read_nif_bytes(nif(*blocks), geometry=True)
        assert parsed.complete
        # Traversal must return rather than recurse without bound.
        assert world_meshes(parsed) == []

    def test_block_tree_skips_invalid_child_links(self) -> None:
        """A child index that is negative or points at nothing adds no subtree."""
        parsed = read_nif_bytes(
            nif(("NiNode", av_body("r", children=(99, 99)))), geometry=True  # nonexistent
        )
        roots = block_tree(parsed)
        assert roots[0].type_name == "NiNode"
        assert roots[0].children == []  # the missing children were skipped

    def test_block_tree_skips_a_negative_child_index(self) -> None:
        """A child link of -1 (no child) is passed over rather than followed."""
        parsed = read_nif_bytes(nif(("NiNode", av_body("r", children=(-1,)))), geometry=True)
        assert block_tree(parsed)[0].children == []

    def test_block_tree_skips_an_attachment_link_to_nothing(self) -> None:
        """A data link pointing at a missing block adds no subtree."""
        # A shape whose data link is block 99, which does not exist.
        parsed = read_nif_bytes(
            nif(("NiTriShape", av_body("s", tail=struct.pack("<ii", 99, -1)))), geometry=True
        )
        assert block_tree(parsed)[0].children == []

    def test_world_meshes_survives_a_cycle_and_a_negative_child(self) -> None:
        """Traversal stops on a revisited block and passes over a -1 child link."""
        parsed = read_nif_bytes(
            nif(
                ("NiNode", av_body("root", children=(1, -1))),  # a root; the -1 child is skipped
                ("NiNode", av_body("a", children=(2,))),
                ("NiNode", av_body("b", children=(1,))),  # b -> a -> b, a cycle below the root
            ),
            geometry=True,
        )
        # No geometry, and crucially it returns rather than recursing forever.
        assert world_meshes(parsed) == []


def _external_source(path: str) -> bytes:
    return (
        text("tex")
        + struct.pack("<ii", -1, -1)
        + struct.pack("<B", 1)
        + text(path)
        + struct.pack("<III", 0, 0, 0)
        + struct.pack("<B", 1)
    )


class TestReaderFieldKinds:
    """Exotic subrecord shapes and the malformed-header stop."""

    def test_a_match_group_array_is_read(self) -> None:
        """A NiTriShapeData carrying a match group parses without desync."""
        body = (
            struct.pack("<H", 1)  # num_vertices
            + struct.pack("<I", 1)  # has_vertices
            + struct.pack("<3f", 0.0, 0.0, 0.0)  # one vertex
            + struct.pack("<I", 0)  # has_normals
            + struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)  # center + radius
            + struct.pack("<I", 0)  # has_vertex_colors
            + struct.pack("<HI", 0, 0)  # num_uv_sets, has_uv
            + struct.pack("<HI", 0, 0)  # num_triangles, num_triangle_points
            + struct.pack("<H", 1)  # one match group
            + struct.pack("<H", 1)  # with one member
            + struct.pack("<H", 0)  # member index
        )
        parsed = read_nif_bytes(nif(("NiTriShapeData", body)), geometry=True)
        assert parsed.complete
        assert parsed.blocks[0].type_name == "NiTriShapeData"

    def test_an_implausible_block_type_length_is_a_malformed_stop(self) -> None:
        """A block-type string claiming an absurd length is a malformed stop."""
        # 0xFFFFFFFF bytes is not a plausible type name -- flagged as malformed.
        data = HEADER + struct.pack("<II", 0x04000002, 1) + struct.pack("<I", 0xFFFFFFFF)
        parsed = read_nif_bytes(data)
        assert parsed.stopped_malformed
        assert not parsed.complete

    def _reads_cleanly(self, type_name: str, body: bytes) -> None:
        parsed = read_nif_bytes(nif((type_name, body)), geometry=True)
        assert parsed.complete, f"{type_name} did not parse"
        assert parsed.blocks[0].type_name == type_name

    def test_a_visibility_key_array_reads(self) -> None:
        """NiVisData carries a run of visibility keys."""
        self._reads_cleanly("NiVisData", struct.pack("<I", 0))

    def test_a_vector_key_group_reads(self) -> None:
        """NiPosData carries a vector key group."""
        self._reads_cleanly("NiPosData", struct.pack("<I", 0))

    def test_a_colour_key_group_reads(self) -> None:
        """NiColorData carries a colour key group."""
        self._reads_cleanly("NiColorData", struct.pack("<I", 0))

    def test_a_strip_array_reads(self) -> None:
        """NiTriStripsData ends in a triangle-strip array."""
        body = (
            struct.pack("<H", 0)  # num_vertices
            + struct.pack("<I", 0)  # has_vertices
            + struct.pack("<I", 0)  # has_normals
            + struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)  # center + radius
            + struct.pack("<I", 0)  # has_vertex_colors
            + struct.pack("<HI", 0, 0)  # num_uv_sets, has_uv
            + struct.pack("<H", 0)  # num_triangles
            + struct.pack("<H", 0)  # strip count
        )
        self._reads_cleanly("NiTriStripsData", body)

    def test_a_skin_partition_with_a_triangle_list_reads(self) -> None:
        """NiSkinPartition's one partition carries a triangle list."""
        body = (
            struct.pack("<I", 1)  # one partition
            + struct.pack("<5H", 0, 1, 0, 0, 0)  # verts, tris=1, bones, strips, per-vertex
            + bytes(6)  # one triangle
            + struct.pack("<B", 0)  # no bone palette
        )
        self._reads_cleanly("NiSkinPartition", body)

    def test_a_float_array_reads(self) -> None:
        """NiVertWeightsExtraData carries a per-vertex float array."""
        self._reads_cleanly(
            "NiVertWeightsExtraData",
            struct.pack("<i", -1) + struct.pack("<I", 0) + struct.pack("<H", 0),
        )

    def test_a_text_key_array_reads(self) -> None:
        """NiTextKeyExtraData carries a text-key array."""
        self._reads_cleanly(
            "NiTextKeyExtraData",
            struct.pack("<i", -1) + struct.pack("<I", 0) + struct.pack("<I", 0),
        )

    def test_a_skin_partition_with_strips_and_a_palette_reads(self) -> None:
        """The strip-list and bone-palette branches of a partition are walked."""
        body = (
            struct.pack("<I", 1)  # one partition
            + struct.pack("<5H", 0, 0, 0, 1, 0)  # tris=0 but one strip length
            + struct.pack("<H", 0)  # the single strip length
            + struct.pack("<B", 1)  # has a bone palette
        )
        self._reads_cleanly("NiSkinPartition", body)

    def test_a_keyframe_manager_sequence_with_an_external_file_reads(self) -> None:
        """NiKeyframeManager holds sequences, one naming an external keyframe file."""
        body = (
            struct.pack("<i", -1)  # next_controller
            + struct.pack("<H", 0)  # flags
            + struct.pack("<4f", 0.0, 0.0, 0.0, 0.0)  # frequency, phase, start, stop
            + struct.pack("<i", -1)  # target
            + struct.pack("<I", 1)  # one sequence
            + text("seq")
            + struct.pack("<B", 1)  # has external keyframe file
            + text("anim.kf")
            + struct.pack("<I", 1)  # one controller pair
            + text("pair")
            + struct.pack("<i", -1)  # the pair's controller link
        )
        self._reads_cleanly("NiKeyframeManager", body)


class TestTextureSlots:
    """Reading the base and decal texture references off a shape."""

    def test_the_base_texture_is_read_from_the_texturing_property(self) -> None:
        """A shape's base slot resolves to the NiSourceTexture it links."""
        texprop = (
            text("tp")
            + struct.pack("<ii", -1, -1)
            + struct.pack("<H", 0)  # flags
            + struct.pack("<I", 2)  # apply mode
            + struct.pack("<I", 1)  # texture_count
            + struct.pack("<I", 1)  # base slot present
            + struct.pack("<i", 3)  # -> NiSourceTexture at block 3
            + bytes(18)  # slot descriptor
        )
        data = nif(
            ("NiTriShape", shape_with((2,))),
            ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
            ("NiTexturingProperty", texprop),
            ("NiSourceTexture", _external_source("textures/base.dds")),
        )
        mesh = world_meshes(read_nif_bytes(data, geometry=True))[0]
        assert mesh.texture == "textures/base.dds"

    def test_an_embedded_source_texture_leaves_the_slot_empty(self) -> None:
        """A base slot whose source has no external filename yields no texture."""
        embedded = (
            text("tex")
            + struct.pack("<ii", -1, -1)  # extra, controller
            + struct.pack("<B", 0)  # use_external = 0 (internal pixels)
            + struct.pack("<B", 0)  # internal texture flag
            + struct.pack("<i", -1)  # internal pixel-data link
            + struct.pack("<III", 0, 0, 0)
            + struct.pack("<B", 1)
        )
        texprop = (
            text("tp")
            + struct.pack("<ii", -1, -1)
            + struct.pack("<H", 0)
            + struct.pack("<I", 2)
            + struct.pack("<I", 1)  # texture_count
            + struct.pack("<I", 1)  # base slot present
            + struct.pack("<i", 3)  # -> the embedded NiSourceTexture at block 3
            + bytes(18)
        )
        data = nif(
            ("NiTriShape", shape_with((2,))),
            ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
            ("NiTexturingProperty", texprop),
            ("NiSourceTexture", embedded),
        )
        mesh = world_meshes(read_nif_bytes(data, geometry=True))[0]
        assert mesh.texture == ""  # embedded pixels have no name to offer

    def test_a_decal_slot_is_collected(self) -> None:
        """A decal in slot 6 is walked and its texture path returned."""
        empty_slots = struct.pack("<I", 0) * 6  # base..bump all absent
        texprop = (
            text("tp")
            + struct.pack("<ii", -1, -1)
            + struct.pack("<H", 0)
            + struct.pack("<I", 2)
            + struct.pack("<I", 7)  # seven slots: index 6 is decal_0
            + empty_slots
            + struct.pack("<I", 1)  # decal_0 present
            + struct.pack("<i", 3)  # -> NiSourceTexture at block 3
            + bytes(18)
        )
        data = nif(
            ("NiTriShape", shape_with((2,))),
            ("NiTriShapeData", shape_data(SQUARE, ONE_TRIANGLE)),
            ("NiTexturingProperty", texprop),
            ("NiSourceTexture", _external_source("textures/decal.dds")),
        )
        mesh = world_meshes(read_nif_bytes(data, geometry=True))[0]
        assert "textures/decal.dds" in mesh.decals
