"""Tests for the Morrowind NIF structure reader.

**What these tests do and do not establish.** The fixtures are synthetic NIFs
built byte by byte here, so they check the *walker* thoroughly: that a block's
fields are read in order and at the right widths, that an unknown block stops
the read instead of desynchronising it, and that truncation and hostile counts
produce an error rather than an exception from :mod:`struct`.

They do **not** establish that the layouts in
:mod:`~wraithguard.nif.blocks` match what Bethesda's exporter actually wrote.
The builder below and the reader share one description of the format, so a
layout that is wrong in the same way in both places passes every test here. That
is a real limit and it is stated rather than papered over: the layouts need
checking against real files, and the reader reports how far it got precisely so
that check has something to report. A wrong field width shows up on real data as
a block type that is not a block type, a few blocks downstream.
"""

from __future__ import annotations

import dataclasses
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from check_nif_layouts import load_census

from wraithguard.nif import (
    ACCEPTED_VERSIONS,
    NIF_VERSION_MORROWIND,
    NifMalformedError,
    NifParseError,
    Structure,
    compare,
    read_nif,
    read_nif_bytes,
    summarise,
    write_nif,
)
from wraithguard.nif.blocks import BLOCK_LAYOUTS
from wraithguard.nif.report import normalise_texture, texture_key
from wraithguard.nif.scan import first_divergence, scan_block_types

HEADER = b"NetImmerse File Format, Version 4.0.0.2\n"


def text(value: str) -> bytes:
    """Encode a length-prefixed, unterminated string as a NIF stores one.

    Args:
        value: The text.

    Returns:
        The encoded bytes.
    """
    raw = value.encode("cp1252")
    return struct.pack("<I", len(raw)) + raw


def nif(*blocks: tuple[str, bytes], version: int = NIF_VERSION_MORROWIND) -> bytes:
    """Assemble a whole file from block bodies.

    Args:
        blocks: ``(type name, body bytes)`` pairs in file order.
        version: The version word to write.

    Returns:
        The file bytes.
    """
    out = [HEADER, struct.pack("<I", version), struct.pack("<I", len(blocks))]
    for type_name, body in blocks:
        out.append(text(type_name))
        out.append(body)
    return b"".join(out)


def av_object(name: str, *, properties: int = 0, children: int = 0) -> bytes:
    """Build the shared scene-object preamble plus node tails.

    Args:
        name: The object's name.
        properties: How many property links to write.
        children: How many child links to write, ``-1`` if negative.

    Returns:
        The body bytes for a ``NiNode``.
    """
    body = [
        text(name),
        struct.pack("<i", -1),  # extra data
        struct.pack("<i", -1),  # controller
        struct.pack("<H", 0),  # flags
        b"\0" * 12,  # translation
        b"\0" * 36,  # rotation
        struct.pack("<f", 1.0),  # scale
        b"\0" * 12,  # velocity
        struct.pack("<I", properties),
        b"".join(struct.pack("<i", 9) for _ in range(properties)),
        struct.pack("<I", 0),  # has bounding box
    ]
    body.append(struct.pack("<I", children))
    body.append(b"".join(struct.pack("<i", 1) for _ in range(children)))
    body.append(struct.pack("<I", 0))  # effects
    return b"".join(body)


def tri_shape(name: str, *, data: int = 1) -> bytes:
    """Build a ``NiTriShape`` body.

    Args:
        name: The shape's name.
        data: The block index of its geometry data.

    Returns:
        The body bytes.
    """
    head = av_object(name)
    # av_object appended the node tail; rebuild without it.
    head = head[: -(4 + 4)]
    return head + struct.pack("<ii", data, -1)


def tri_shape_data(vertices: int, triangles: int) -> bytes:
    """Build a ``NiTriShapeData`` body with no optional arrays.

    Args:
        vertices: The vertex count to declare.
        triangles: The triangle count to declare.

    Returns:
        The body bytes.
    """
    return b"".join(
        [
            struct.pack("<H", vertices),
            struct.pack("<I", 0),  # has vertices
            struct.pack("<I", 0),  # has normals
            b"\0" * 12,  # center
            struct.pack("<f", 1.0),  # radius
            struct.pack("<I", 0),  # has vertex colors
            struct.pack("<H", 0),  # uv sets
            struct.pack("<I", 0),  # has uv
            struct.pack("<H", triangles),
            struct.pack("<I", triangles * 3),
            b"\0" * (triangles * 6),
            struct.pack("<H", 0),  # match groups
        ]
    )


class TestHeader:
    """A bad header is fatal; nothing after it can be trusted."""

    def test_a_minimal_file_parses(self) -> None:
        """The smallest legal file: a header and no blocks."""
        result = read_nif_bytes(nif())

        assert result.version == NIF_VERSION_MORROWIND
        assert result.block_count == 0
        assert result.complete

    def test_something_that_is_not_a_nif_is_refused(self) -> None:
        """A texture or a readme must not be parsed as geometry."""
        with pytest.raises(NifParseError, match="NetImmerse header"):
            read_nif_bytes(b"DDS |not a nif at all")

    def test_a_later_nif_version_is_refused_by_name(self) -> None:
        """Limiting the versions is a design choice, so it says so.

        A Skyrim mesh in a Morrowind data folder is a real thing to find, and
        naming the versions the reader does accept is a more useful answer than
        a desynchronised parse.
        """
        with pytest.raises(NifParseError, match="is not one Morrowind ships"):
            read_nif_bytes(nif(version=0x14020007))

    def test_both_versions_morrowind_ships_are_accepted(self) -> None:
        """4.0.0.0 differs from 4.0.0.2 in the header, not in the layouts.

        Measured rather than taken on report: 40 mod meshes at 4.0.0.0 had
        their version word alone rewritten and every one then parsed
        identically to the layout-free scan.
        """
        for version in sorted(ACCEPTED_VERSIONS):
            result = read_nif_bytes(nif(("NiNode", av_object("Root")), version=version))
            assert result.stopped_reason == "", hex(version)
            assert result.blocks[0].type_name == "NiNode"

    def test_a_header_with_no_newline_is_refused(self) -> None:
        """The version field sits after the line, so there is nowhere to start."""
        with pytest.raises(NifParseError, match="NetImmerse header"):
            read_nif_bytes(b"NetImmerse File Format, Version 4.0.0.2")


class TestBlockWalking:
    """Blocks carry no length, so walking them is the whole problem."""

    def test_a_node_is_read(self) -> None:
        """Name and links come back, and the walk lands cleanly at the end."""
        result = read_nif_bytes(nif(("NiNode", av_object("Root"))))

        assert result.complete
        assert result.blocks[0].type_name == "NiNode"
        assert result.blocks[0].fields["name"] == "Root"

    def test_consecutive_blocks_stay_in_step(self) -> None:
        """The real invariant: block *n+1* is only findable via block *n*.

        Three blocks of different shapes, so any width error in the first two
        lands the third somewhere that is not a type string.
        """
        data = nif(
            ("NiNode", av_object("Root", children=1)),
            ("NiTriShape", tri_shape("Shape", data=2)),
            ("NiTriShapeData", tri_shape_data(vertices=8, triangles=4)),
        )
        result = read_nif_bytes(data)

        assert result.complete, result.stopped_reason
        assert [b.type_name for b in result.blocks] == [
            "NiNode",
            "NiTriShape",
            "NiTriShapeData",
        ]
        assert result.blocks[2].fields["num_vertices"] == 8
        assert result.blocks[2].fields["triangles"] == 4

    def test_a_shape_links_to_its_data(self) -> None:
        """Which is how a report reaches the counts from the named shape."""
        data = nif(
            ("NiTriShape", tri_shape("Shape", data=1)),
            ("NiTriShapeData", tri_shape_data(vertices=3, triangles=1)),
        )
        result = read_nif_bytes(data)

        assert result.blocks[0].link("data") == 1

    def test_property_links_are_counted_not_kept(self) -> None:
        """A structure report wants how many, never which bytes."""
        result = read_nif_bytes(nif(("NiNode", av_object("Root", properties=3))))

        assert result.blocks[0].fields["properties"] == 3


class TestOptionalArraysUseTheirOwnFlag:
    """Found on real meshes, not here: 32 of 214 files failed to parse.

    ``vertices`` and ``normals`` are the same *kind* of field, and the walker
    originally derived the gating flag from the kind rather than from the
    field. Normals were therefore read whenever ``has_vertices`` was set, so
    any mesh carrying one without the other read a block-sized run of bytes
    that was not there and desynchronised everything after it.

    The synthetic fixtures could not have caught it: they wrote both flags
    together, which is the common case and the one that works. Each optional
    array now names its own gate in the layout table.
    """

    @staticmethod
    def geometry(*, has_vertices: bool, has_normals: bool, vertices: int = 4) -> bytes:
        """Build a ``NiTriShapeData`` with either bulk array independently set.

        Args:
            has_vertices: Whether to write the vertex array.
            has_normals: Whether to write the normal array.
            vertices: The vertex count to declare.

        Returns:
            The body bytes.
        """
        return b"".join(
            [
                struct.pack("<H", vertices),
                struct.pack("<I", int(has_vertices)),
                b"\0" * (vertices * 12 if has_vertices else 0),
                struct.pack("<I", int(has_normals)),
                b"\0" * (vertices * 12 if has_normals else 0),
                b"\0" * 12,
                struct.pack("<f", 1.0),
                struct.pack("<I", 0),
                struct.pack("<H", 0),
                struct.pack("<I", 0),
                struct.pack("<H", 0),
                struct.pack("<I", 0),
                struct.pack("<H", 0),
            ]
        )

    @pytest.mark.parametrize(
        ("has_vertices", "has_normals"),
        [(True, True), (True, False), (False, True), (False, False)],
    )
    def test_every_combination_stays_in_step(self, has_vertices: bool, has_normals: bool) -> None:
        """A following block proves the walk ended where the data ended.

        Args:
            has_vertices: Whether the fixture writes vertices.
            has_normals: Whether the fixture writes normals.
        """
        data = nif(
            ("NiTriShapeData", self.geometry(has_vertices=has_vertices, has_normals=has_normals)),
            ("NiNode", av_object("After")),
        )
        result = read_nif_bytes(data)

        assert result.complete, result.stopped_reason
        assert result.blocks[1].fields["name"] == "After"

    def test_an_optional_array_with_no_gate_is_a_layout_error(self) -> None:
        """Guessing a gate is what caused the original defect."""
        from wraithguard.nif.reader import NifParseError as Err, _Cursor, _read_field

        with pytest.raises(Err, match="names no gate"):
            _read_field(_Cursor(b"\0" * 64), "vec3_array", "normals", {"num_vertices": 1})


class TestBillboardNodeHasNoModeField:
    """Found on a real vanilla mesh, not here.

    ``NiBillboardNode`` was given a ``billboard_mode`` u16 on the assumption it
    matched later NIF versions. It does not: 4.0.0.2 has no such field, so the
    layout consumed two bytes too many and landed the reader *past* the next
    type string. The synthetic fixtures could not catch it -- the builder wrote
    the same two bytes the reader expected.
    """

    def test_a_billboard_node_is_shaped_exactly_like_a_node(self) -> None:
        """A following block proves the walk ended where the body ended."""
        data = nif(
            ("NiBillboardNode", av_object("Billboard")),
            ("NiTriShape", tri_shape("After", data=2)),
            ("NiTriShapeData", tri_shape_data(vertices=3, triangles=1)),
        )
        result = read_nif_bytes(data)

        assert result.complete, result.stopped_reason
        assert result.blocks[1].fields["name"] == "After"

    def test_its_body_is_the_same_size_as_a_plain_node(self) -> None:
        """The invariant stated as a measurement rather than as a field list."""
        body = av_object("Same")
        billboard = read_nif_bytes(nif(("NiBillboardNode", body))).blocks[0]
        plain = read_nif_bytes(nif(("NiNode", body))).blocks[0]

        assert billboard.size == plain.size


class TestAnimationBlocks:
    """Controllers are why 8 of those 214 files stopped."""

    @staticmethod
    def controller(data: int = 1) -> bytes:
        """Build a ``NiKeyframeController`` body.

        Args:
            data: The block index of its keyframe data.

        Returns:
            The body bytes.
        """
        return (
            struct.pack("<i", -1)
            + struct.pack("<H", 8)
            + struct.pack("<ffff", 1.0, 0.0, 0.0, 2.0)
            + struct.pack("<i", 0)
            + struct.pack("<i", data)
        )

    def test_a_keyframe_controller_is_read(self) -> None:
        """Its target and data links are what an animation report follows."""
        result = read_nif_bytes(nif(("NiKeyframeController", self.controller(data=3))))

        assert result.complete, result.stopped_reason
        assert result.blocks[0].link("data") == 3
        assert result.blocks[0].fields["stop_time"] == pytest.approx(2.0)

    def test_keyframe_data_counts_its_keys(self) -> None:
        """Linear quaternion rotations plus translations, no scales."""
        body = b"".join(
            [
                struct.pack("<I", 2),  # rotation keys
                struct.pack("<I", 1),  # linear
                b"\0" * (2 * 20),
                struct.pack("<I", 3),  # translation keys
                struct.pack("<I", 1),  # linear
                b"\0" * (3 * 16),
                struct.pack("<I", 0),  # no scale keys
            ]
        )
        result = read_nif_bytes(nif(("NiKeyframeData", body), ("NiNode", av_object("After"))))

        assert result.complete, result.stopped_reason
        counts = result.blocks[0].fields["keyframe_data"]
        assert counts == {"rotation_keys": 2, "translation_keys": 3, "scale_keys": 0}
        assert result.blocks[1].fields["name"] == "After"

    def test_an_empty_key_group_writes_no_interpolation_word(self) -> None:
        """The detail that would cost a whole file if assumed otherwise."""
        body = struct.pack("<I", 0) + struct.pack("<I", 0) + struct.pack("<I", 0)
        result = read_nif_bytes(nif(("NiKeyframeData", body), ("NiNode", av_object("After"))))

        assert result.complete, result.stopped_reason
        assert result.blocks[1].fields["name"] == "After"

    def test_xyz_rotations_are_three_float_groups(self) -> None:
        """Mode 4 changes the shape of what follows, not just the key width.

        Including the float between the rotation type and the axis groups,
        which a real corpus file proved is there: without it the float's bytes
        were read as the X group's key count -- zero, so the group returned at
        once -- and everything after was a field out of step.
        """
        body = b"".join(
            [
                struct.pack("<I", 1),  # rotation key count
                struct.pack("<I", 4),  # XYZ rotation
                struct.pack("<f", 0.0),  # the float that must be consumed
                *[struct.pack("<II", 1, 1) + b"\0" * 8 for _ in range(3)],
                struct.pack("<I", 0),  # translations
                struct.pack("<I", 0),  # scales
            ]
        )
        result = read_nif_bytes(nif(("NiKeyframeData", body), ("NiNode", av_object("After"))))

        assert result.complete, result.stopped_reason
        assert result.blocks[1].fields["name"] == "After"

    def test_an_unknown_rotation_mode_stops_rather_than_guessing(self) -> None:
        """An unknown key width desynchronises everything after it."""
        body = struct.pack("<I", 1) + struct.pack("<I", 99) + b"\0" * 32
        result = read_nif_bytes(nif(("NiKeyframeData", body)))

        assert not result.complete
        assert "unknown interpolation mode" in result.stopped_reason

    def test_an_unknown_mode_in_a_key_group_also_stops(self) -> None:
        """Rotation has its own branch, so the shared group needs its own test.

        A negative control caught this: making the shared reader fall back to a
        guessed width passed, because the only unknown-mode test in the suite
        exercised the rotation branch instead.
        """
        body = b"".join(
            [
                struct.pack("<I", 0),  # no rotation keys
                struct.pack("<I", 2),  # two translation keys
                struct.pack("<I", 77),  # ... in a mode nobody knows
                b"\0" * 32,
            ]
        )
        result = read_nif_bytes(nif(("NiKeyframeData", body)))

        assert not result.complete
        assert "unknown interpolation mode" in result.stopped_reason


class TestUnknownBlocksStopTheRead:
    """The design decision this module turns on."""

    def test_an_unknown_type_stops_and_says_so(self) -> None:
        """Not skipped: there is no length field to skip by."""
        data = nif(
            ("NiNode", av_object("Root")),
            (UNKNOWN_TYPE, b"\0" * 32),
            ("NiNode", av_object("Later")),
        )
        result = read_nif_bytes(data)

        assert not result.complete
        assert result.stopped_at == UNKNOWN_TYPE
        assert "carry no length" in result.stopped_reason

    def test_what_was_read_before_it_is_kept(self) -> None:
        """A partial answer is useful; a silently truncated one is not."""
        data = nif(("NiNode", av_object("Root")), (UNKNOWN_TYPE, b"\0" * 8))
        result = read_nif_bytes(data)

        assert len(result.blocks) == 1
        assert result.block_count == 2, "the declared total must still be reported"

    def test_a_missing_type_is_told_apart_from_a_broken_layout(self) -> None:
        """The distinction a survey of real files turns on.

        Both stop the read and both set ``stopped_at``, but they mean opposite
        things: a type with no layout is a gap to fill, while a *known* type
        that failed to parse is a bug in its own layout. Reporting them
        together once sent a real investigation after a block that was never
        missing at all.
        """
        missing = read_nif_bytes(nif((UNKNOWN_TYPE, b"")))
        # A known type given a body too short to satisfy its own layout.
        broken = read_nif_bytes(nif(("NiTriShapeData", b"\x04\x00")))

        assert missing.stopped_unknown is True
        assert missing.stopped_at == UNKNOWN_TYPE
        assert broken.stopped_unknown is False
        assert broken.stopped_at == "NiTriShapeData"

    def test_completeness_is_not_claimed_on_a_short_block_list(self) -> None:
        """Fewer blocks than declared is incomplete even with no error."""
        result = read_nif_bytes(nif((UNKNOWN_TYPE, b"")))

        assert not result.complete


class TestHostileInput:
    """These files arrive from the internet inside a mod archive."""

    def test_a_truncated_block_is_an_error_not_a_crash(self) -> None:
        """Never a struct.error, and never a short read treated as data."""
        data = nif(("NiNode", av_object("Root")))[:-10]
        result = read_nif_bytes(data)

        assert not result.complete
        assert "byte(s) at offset" in result.stopped_reason

    def test_an_implausible_count_is_refused_before_it_is_used(self) -> None:
        """A corrupt length is otherwise an instruction to allocate 4 GB."""
        body = text("Root") + struct.pack("<ii", -1, -1) + struct.pack("<H", 0)
        body += b"\0" * 12 + b"\0" * 36 + struct.pack("<f", 1.0) + b"\0" * 12
        body += struct.pack("<I", 0xFFFFFFF0)  # property count
        result = read_nif_bytes(nif(("NiNode", body)))

        assert not result.complete
        assert "implausible count" in result.stopped_reason

    def test_a_declared_block_count_beyond_the_file_ends_cleanly(self) -> None:
        """Claiming a thousand blocks in a hundred bytes must not hang."""
        data = HEADER + struct.pack("<II", NIF_VERSION_MORROWIND, 1000)
        result = read_nif_bytes(data)

        assert not result.complete
        assert result.blocks == []

    def test_a_string_length_past_the_end_is_refused(self) -> None:
        """The length prefix is attacker-controlled like any other field."""
        data = HEADER + struct.pack("<II", NIF_VERSION_MORROWIND, 1)
        data += struct.pack("<I", 0xFFFFFF)  # block type name length
        result = read_nif_bytes(data)

        assert not result.complete

    def test_reading_a_missing_file_is_a_parse_error(self, tmp_path) -> None:
        """One exception type out of this package, whatever went wrong.

        Args:
            tmp_path: Pytest's temp directory.
        """
        with pytest.raises(NifParseError, match="cannot read"):
            read_nif(tmp_path / "absent.nif")

    def test_reading_from_disk_matches_reading_from_memory(self, tmp_path) -> None:
        """The disk path must add nothing but the read.

        Args:
            tmp_path: Pytest's temp directory.
        """
        blob = nif(("NiNode", av_object("Root")))
        path = tmp_path / "m.nif"
        path.write_bytes(blob)

        assert read_nif(path).blocks[0].fields == read_nif_bytes(blob).blocks[0].fields


class TestTextureReferences:
    """ "Which texture does this mesh ask for" is a question about the file."""

    def test_an_external_source_texture_yields_its_filename(self) -> None:
        """The answer a resource-conflict report actually needs."""
        body = (
            text("")
            + struct.pack("<ii", -1, -1)
            + struct.pack("<B", 1)
            + text("textures\\tx_rock_01.dds")
            + struct.pack("<III", 0, 0, 0)
            + struct.pack("<B", 1)
        )
        result = read_nif_bytes(nif(("NiSourceTexture", body)))

        assert result.complete, result.stopped_reason
        assert result.blocks[0].fields["external_or_internal"] == "textures\\tx_rock_01.dds"

    def test_an_internal_texture_reports_no_filename(self) -> None:
        """Embedded pixels reference nothing on disk, so nothing is claimed."""
        body = (
            text("")
            + struct.pack("<ii", -1, -1)
            + struct.pack("<B", 0)
            + struct.pack("<B", 0)
            + struct.pack("<i", 4)
            + struct.pack("<III", 0, 0, 0)
            + struct.pack("<B", 1)
        )
        result = read_nif_bytes(nif(("NiSourceTexture", body)))

        assert result.complete, result.stopped_reason
        assert result.blocks[0].fields["external_or_internal"] == ""


class TestStructureReport:
    """The questions a resource conflict actually raises."""

    @staticmethod
    def mesh(
        *,
        shapes: tuple[tuple[str, int, int], ...] = (),
        textures: tuple[str, ...] = (),
        collision: bool = False,
        animation: bool = False,
    ) -> bytes:
        """Build a whole mesh with the features under test.

        Args:
            shapes: ``(name, vertices, triangles)`` per shape.
            textures: External texture paths to reference.
            collision: Whether to include a collision node.
            animation: Whether to include a keyframe controller.

        Returns:
            The file bytes.
        """
        blocks: list[tuple[str, bytes]] = [("NiNode", av_object("Root"))]
        for name, verts, tris in shapes:
            data_index = len(blocks) + 1
            blocks.append(("NiTriShape", tri_shape(name, data=data_index)))
            blocks.append(("NiTriShapeData", tri_shape_data(verts, tris)))
        blocks.extend(
            (
                "NiSourceTexture",
                text("")
                + struct.pack("<ii", -1, -1)
                + struct.pack("<B", 1)
                + text(path)
                + struct.pack("<III", 0, 0, 0)
                + struct.pack("<B", 1),
            )
            for path in textures
        )
        if collision:
            blocks.append(("RootCollisionNode", av_object("Collision")))
        if animation:
            blocks.append(
                (
                    "NiKeyframeController",
                    struct.pack("<i", -1)
                    + struct.pack("<H", 8)
                    + struct.pack("<ffff", 1.0, 0.0, 0.0, 1.0)
                    + struct.pack("<ii", 0, -1),
                )
            )
        return nif(*blocks)

    def structure(self, **kwargs: object) -> Structure:
        """Build a mesh and summarise it.

        Args:
            kwargs: Passed to :meth:`mesh`.

        Returns:
            The structure summary.
        """
        parsed = read_nif_bytes(self.mesh(**kwargs))  # type: ignore[arg-type]
        assert parsed.complete, parsed.stopped_reason
        return summarise(parsed)

    def test_shapes_carry_their_counts(self) -> None:
        """ "Is the winner a tenth the polys" is the first question asked."""
        got = self.structure(shapes=(("Body", 100, 60), ("Head", 40, 20)))

        assert [(s.name, s.vertices, s.triangles) for s in got.shapes] == [
            ("Body", 100, 60),
            ("Head", 40, 20),
        ]
        assert got.total_triangles == 80

    def test_texture_references_are_normalised(self) -> None:
        """Two spellings of one path are one texture, not two.

        Morrowind paths are case-insensitive and written with either slash, and
        mods are inconsistent about both.
        """
        got = self.structure(textures=("Textures\\TX_Rock_01.DDS", "textures/tx_rock_01.dds"))

        assert got.textures == ["textures/tx_rock_01.dds"]

    def test_collision_and_animation_are_detected(self) -> None:
        """Both are presence questions rather than flags on anything."""
        assert self.structure(collision=True).has_collision
        assert not self.structure().has_collision
        assert self.structure(animation=True).has_animation
        assert not self.structure().has_animation

    def test_a_partial_read_is_flagged(self) -> None:
        """An absence proves nothing when the file was not finished.

        This is the property that keeps the report honest: "no collision" and
        "no collision *seen so far*" are different claims, and only one of them
        is safe to act on.
        """
        parsed = read_nif_bytes(nif(("NiNode", av_object("Root")), (UNKNOWN_TYPE, b"")))

        got = summarise(parsed)

        assert got.partial
        assert not got.has_collision, "unproven, which is what `partial` is for"

    def test_a_shape_whose_data_was_not_reached_reports_zero_not_a_guess(self) -> None:
        """An unknown size is not an empty one, and the flag says which."""
        parsed = read_nif_bytes(nif(("NiTriShape", tri_shape("Orphan", data=99))))

        got = summarise(parsed)

        assert got.shapes[0].vertices == 0
        assert got.shapes[0].name == "Orphan"


class TestComparingTwoMeshes:
    """What changes when the winner replaces the loser."""

    @staticmethod
    def built(**kwargs: object) -> Structure:
        """Summarise a mesh built from the shared fixture.

        Args:
            kwargs: Passed to :meth:`TestStructureReport.mesh`.

        Returns:
            The structure summary.
        """
        return TestStructureReport().structure(**kwargs)

    def test_a_simplified_winner_shows_as_a_ratio(self) -> None:
        """The number that says "this is a downgrade" without adjectives."""
        loser = self.built(shapes=(("Body", 100, 100),))
        winner = self.built(shapes=(("Body", 10, 10),))

        assert compare(loser, winner).triangle_ratio == pytest.approx(0.1)

    def test_a_ratio_against_nothing_is_not_invented(self) -> None:
        """Dividing by zero triangles would report an infinite downgrade."""
        loser = self.built()
        winner = self.built(shapes=(("Body", 10, 10),))

        assert compare(loser, winner).triangle_ratio is None

    def test_lost_collision_is_reported(self) -> None:
        """Found in game by falling through the world, otherwise."""
        difference = compare(self.built(collision=True), self.built())

        assert difference.lost_collision

    def test_gained_collision_is_not_reported_as_a_loss(self) -> None:
        """Only losses are named; a report of every difference is unread."""
        difference = compare(self.built(), self.built(collision=True))

        assert not difference.lost_collision

    def test_lost_animation_is_reported(self) -> None:
        """A door whose winning mesh has no controllers stops moving."""
        assert compare(self.built(animation=True), self.built()).lost_animation

    def test_texture_references_are_split_both_ways(self) -> None:
        """A mesh asking for a texture nobody ships is the subtle breakage."""
        loser = self.built(textures=("textures/old.dds",))
        winner = self.built(textures=("textures/new.dds",))

        difference = compare(loser, winner)

        assert difference.added_textures == ["textures/new.dds"]
        assert difference.dropped_textures == ["textures/old.dds"]

    def test_a_partial_read_makes_the_comparison_unreliable(self) -> None:
        """Every absence in the result is unproven, so the flag travels with it."""
        partial = summarise(read_nif_bytes(nif((UNKNOWN_TYPE, b""))))

        assert compare(partial, self.built()).unreliable
        assert compare(self.built(), partial).unreliable
        assert not compare(self.built(), self.built()).unreliable


class TestCensusLoading:
    """The census is a reference file, so mis-reading it accuses the reader."""

    def test_two_records_sharing_a_line_are_both_read(self, tmp_path: Path) -> None:
        """A census without newlines between records must not lose either half.

        The real census has 17 such lines. Splitting on lines dropped *both*
        records each time -- silently, since a mangled record simply fails to
        parse -- which removed 34 files from the comparison and would have made
        any conclusion drawn from the totals wrong by exactly that much.
        """
        census = tmp_path / "census.txt"
        census.write_text(
            "a\\one.nif = {'NiNode': 1}b\\two.nif = {'NiNode': 2}\n",
            encoding="utf-8",
        )
        loaded = load_census(census)
        assert loaded == {"a/one.nif": {"NiNode": 1}, "b/two.nif": {"NiNode": 2}}

    def test_an_unreadable_census_is_reported_not_raised(self, tmp_path: Path) -> None:
        """A mistyped path is a message, not a traceback."""
        with pytest.raises(ValueError, match="cannot read the census"):
            load_census(tmp_path / "absent.txt")


#: A block type this reader will never implement, for the tests that need one.
#: Naming a *real* type here is a trap: two tests used ``NiPixelData`` as their
#: example of an unknown block and started failing the moment it was
#: implemented, which is the test asserting the state of the layout table
#: rather than the behaviour it meant to pin down.
UNKNOWN_TYPE = "NiDefinitelyNotARealBlock"


def property_body(name: str = "") -> bytes:
    """A minimal NiProperty body: the named-object preamble plus flags.

    Args:
        name: The block's name.

    Returns:
        The body bytes.
    """
    return text(name) + struct.pack("<iiH", -1, -1, 0)


def time_controller(data_link: int = -1) -> bytes:
    """The shared NiTimeController preamble plus a data link.

    Args:
        data_link: The block index the controller drives.

    Returns:
        The body bytes.
    """
    return struct.pack("<ihffffii", -1, 0, 1.0, 0.0, 0.0, 1.0, -1, data_link)


class TestDesynchronisationIsNotAMissingType:
    """Losing alignment and meeting an unknown block are different failures.

    They were reported as the same one, which mattered twice over: it blamed a
    missing type for what was a wrong field width, and it interpolated raw
    bytes into the message, so a survey printed binary to the terminal.
    """

    def test_a_bad_type_name_is_reported_as_lost_alignment(self) -> None:
        """Garbage where a type name belongs means a width is wrong earlier."""
        data = HEADER + struct.pack("<II", NIF_VERSION_MORROWIND, 1)
        data += struct.pack("<I", 6) + b"\x00\x01\xff\xfe\x02\x03"
        result = read_nif_bytes(data)
        assert "lost alignment" in result.stopped_reason
        assert not result.stopped_unknown, "a desync is not a missing block type"
        assert result.stopped_at == ""

    def test_the_message_never_carries_raw_bytes(self) -> None:
        """A stop reason goes to logs and terminals, so it must be printable."""
        data = HEADER + struct.pack("<II", NIF_VERSION_MORROWIND, 1)
        data += struct.pack("<I", 5) + b"\x00\x0bNi\xff"
        reason = read_nif_bytes(data).stopped_reason
        assert reason.isprintable(), f"unprintable characters in {reason!r}"
        assert "\\x00" in reason, "the bytes should be escaped, not dropped"

    def test_a_known_type_still_reports_a_missing_type(self) -> None:
        """The honest case must survive the new one."""
        result = read_nif_bytes(nif(("NiFooBarProperty", b"")))
        assert result.stopped_unknown
        assert result.stopped_at == "NiFooBarProperty"


class TestGeomMorpherControllerLength:
    """NiGeomMorpherController carries one byte the other controllers do not.

    Without it the reader stopped one byte early and read a type name of
    ``\\x00NiMorphData`` -- the correct name behind a leading NUL, which is
    exactly what being one byte short looks like.
    """

    def test_the_next_block_is_reached(self) -> None:
        """With the byte consumed, the following block parses normally."""
        body = time_controller(data_link=1) + b"\x00"
        result = read_nif_bytes(
            nif(("NiGeomMorpherController", body), ("NiZBufferProperty", property_body()))
        )
        assert result.stopped_reason == ""
        assert [b.type_name for b in result.blocks] == [
            "NiGeomMorpherController",
            "NiZBufferProperty",
        ]

    def test_dropping_the_byte_desynchronises(self) -> None:
        """A negative control: without the byte the read must fail, not pass.

        If this ever stops failing, the trailing byte has become optional and
        the layout above is no longer carrying its weight.
        """
        result = read_nif_bytes(
            nif(("NiZBufferProperty", property_body()), ("NiGeomMorpherController", b""))
        )
        assert result.stopped_reason != ""


class TestLayoutFreeScan:
    """The scan exists to disagree with the reader, so it must not share its code.

    Every assertion here is about the scan finding things the layout table does
    not contain. A scan that could only find implemented types would agree with
    the reader by construction and would be worth nothing as a cross-check.
    """

    def test_it_finds_a_type_the_reader_has_never_heard_of(self) -> None:
        """Discovery is the whole point: no layout, still found."""
        data = nif(("NiMadeUpThing", b"\x00" * 8), ("NiNode", b""))
        scanned = scan_block_types(data)
        assert "NiMadeUpThing" in scanned.type_names
        assert "NiMadeUpThing" not in BLOCK_LAYOUTS

    def test_a_block_name_is_not_mistaken_for_a_type(self) -> None:
        """Names are length-prefixed too, which is what broke the first version.

        Scanning on "u32 length then that many bytes" alone over-counted 522 of
        556 corpus files, because a node called ``Bip01`` matches it exactly.
        """
        body = text("Bip01") + struct.pack("<ii", -1, -1)
        scanned = scan_block_types(nif(("NiStringExtraData", body)))
        assert scanned.type_names == ["NiStringExtraData"]

    def test_it_disqualifies_itself_when_the_count_is_wrong(self) -> None:
        """A reference that cannot be checked must not present itself as one."""
        data = HEADER + struct.pack("<II", NIF_VERSION_MORROWIND, 9)
        data += text("NiNode")
        scanned = scan_block_types(data)
        assert scanned.found == 1
        assert scanned.declared == 9
        assert not scanned.reconciles

    def test_a_missing_header_yields_nothing_rather_than_a_guess(self) -> None:
        """Garbage in does not become a confident block list."""
        scanned = scan_block_types(b"this is not a nif at all")
        assert not scanned.header_ok
        assert scanned.type_names == []
        assert not scanned.reconciles

    def test_the_longer_name_wins_inside_one_run(self) -> None:
        """``NiTriShapeData`` must not be read as the ``NiTriShape`` at its front."""
        scanned = scan_block_types(nif(("NiTriShapeData", b"")))
        assert scanned.type_names == ["NiTriShapeData"]


class TestFirstDivergence:
    """Where two listings part company, not merely that they do."""

    def test_a_prefix_is_not_a_disagreement(self) -> None:
        """Stopping early is incompleteness; it must not read as an error."""
        assert first_divergence(["NiNode", "NiTriShape"], ["NiNode"]) is None

    def test_it_returns_the_first_differing_index(self) -> None:
        """The index is what makes the result actionable."""
        assert first_divergence(["NiNode", "NiTriShape"], ["NiNode", "NiAvoidThing"]) == 1


def texture_slot(link: int, *, bump: bool = False) -> bytes:
    """One present texture slot: the flag, the link and the descriptor.

    Args:
        link: The ``NiSourceTexture`` block index.
        bump: Whether this is the bump slot, which carries a luma scale and
            offset plus a 2x2 matrix on top of the usual descriptor.

    Returns:
        The 26 bytes a present slot occupies, or 50 for the bump slot.
    """
    slot = struct.pack("<Ii", 1, link) + struct.pack("<IIIhhh", 0, 0, 0, 0, 0, 0)
    return slot + struct.pack("<6f", *([0.0] * 6)) if bump else slot


def texture_slots(count: int, link: int) -> bytes:
    """A full run of present slots, with the bump slot written correctly.

    Args:
        count: How many slots to write.
        link: The block index every slot points at.

    Returns:
        The concatenated slots.
    """
    return b"".join(texture_slot(link, bump=index == 5) for index in range(count))


class TestTexturingPropertyDecals:
    """``texture_count`` counts slots; it is not capped at the seven named ones.

    Capping the loop truncated the block and desynchronised every mesh using a
    second decal -- 11 files in the corpus, and invisibly, since the reader
    stopped inside a block type it claims to support.
    """

    def test_more_slots_than_names_are_all_consumed(self) -> None:
        """Nine slots must be read, not seven, and the next block reached."""
        body = text("") + struct.pack("<iiH", -1, -1, 0)
        body += struct.pack("<II", 2, 9) + texture_slots(9, 1)
        result = read_nif_bytes(
            nif(("NiTexturingProperty", body), ("NiZBufferProperty", property_body()))
        )
        assert result.stopped_reason == ""
        assert [b.type_name for b in result.blocks] == [
            "NiTexturingProperty",
            "NiZBufferProperty",
        ]

    def test_slots_past_the_named_table_continue_the_decal_numbering(self) -> None:
        """Slot 7 is decal_1, so a caller can still say which slots are used."""
        body = text("") + struct.pack("<iiH", -1, -1, 0)
        body += struct.pack("<II", 2, 8) + texture_slots(8, 3)
        block = read_nif_bytes(nif(("NiTexturingProperty", body))).blocks[0]
        slots = block.fields["textures"]
        assert isinstance(slots, dict)
        assert "decal_1" in slots, slots
        assert slots["decal_1"] == 3

    def test_the_seven_named_slots_still_work(self) -> None:
        """A negative control: the ordinary case must not have been broken."""
        body = text("") + struct.pack("<iiH", -1, -1, 0)
        body += struct.pack("<II", 2, 7) + texture_slot(2) + struct.pack("<I", 0) * 6
        block = read_nif_bytes(nif(("NiTexturingProperty", body))).blocks[0]
        slots = block.fields["textures"]
        assert isinstance(slots, dict)
        assert slots == {"base": 2}


def skin_bone(weighted: int) -> bytes:
    """One bone's entry in a ``NiSkinData``.

    Args:
        weighted: How many vertices this bone influences.

    Returns:
        The transform and bounding sphere, the count, and the weight pairs.
    """
    fixed = struct.pack("<9f", *([0.0] * 9)) + struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
    fixed += struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
    return fixed + struct.pack("<H", weighted) + struct.pack("<Hf", 0, 1.0) * weighted


class TestSkinning:
    """The skin pair, which always co-occurs, so neither is useful alone."""

    def test_the_instance_reads_its_bone_list(self) -> None:
        """Two links then a counted list, which is all 36 bytes accounted for."""
        body = struct.pack("<9i", 41, 34, 6, 3, 2, 31, 32, 29, 28)
        block = read_nif_bytes(nif(("NiSkinInstance", body))).blocks[0]
        assert block.link("data") == 41
        assert block.link("skeleton_root") == 34
        assert block.fields["bones"] == 6

    def test_skin_data_consumes_every_bone_and_reaches_the_next_block(self) -> None:
        """The exact-landing test, which is what validates the layout at all."""
        body = struct.pack("<9f", *([0.0] * 9)) + struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
        body += struct.pack("<Ii", 3, -1)
        body += skin_bone(4) + skin_bone(0) + skin_bone(11)
        result = read_nif_bytes(nif(("NiSkinData", body), ("NiZBufferProperty", property_body())))
        assert result.stopped_reason == ""
        assert result.blocks[0].fields["bones"] == [4, 0, 11]
        assert result.blocks[1].type_name == "NiZBufferProperty"

    def test_a_bone_with_no_weights_still_costs_its_transform(self) -> None:
        """Zero weighted vertices is not zero bytes.

        Treating an empty bone as absent would desynchronise every rig that
        has one, and rigs that have one are common.
        """
        body = struct.pack("<9f", *([0.0] * 9)) + struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
        body += struct.pack("<Ii", 1, -1) + skin_bone(0)
        result = read_nif_bytes(nif(("NiSkinData", body), ("NiShadeProperty", property_body())))
        assert result.stopped_reason == ""
        assert result.blocks[0].fields["bones"] == [0]

    def test_a_truncated_weight_table_is_reported_not_ignored(self) -> None:
        """A negative control: claiming more bones than are present must fail."""
        body = struct.pack("<9f", *([0.0] * 9)) + struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
        body += struct.pack("<Ii", 9, -1) + skin_bone(1)
        assert read_nif_bytes(nif(("NiSkinData", body))).stopped_reason != ""


class TestMorphData:
    """Morph targets, whose key group is not the generic one."""

    def test_a_keyless_target_still_writes_its_interpolation_word(self) -> None:
        """The detail that decides whether every morphing mesh parses.

        Every other key group omits the interpolation word when there are no
        keys. This one does not. Both readings were run against all 26
        fixtures: "always written" landed exactly on 26, the alternative on 3.
        """
        body = struct.pack("<IIB", 2, 1, 1)
        body += struct.pack("<II", 0, 1) + struct.pack("<3f", 0.0, 0.0, 0.0)
        body += struct.pack("<II", 2, 1) + struct.pack("<f", 0.0) * 4
        body += struct.pack("<3f", 0.0, 0.0, 0.0)
        result = read_nif_bytes(nif(("NiMorphData", body), ("NiShadeProperty", property_body())))
        assert result.stopped_reason == ""
        assert result.blocks[0].fields["morphs"] == [0, 2]
        assert result.blocks[1].type_name == "NiShadeProperty"

    def test_every_target_carries_a_full_vertex_set(self) -> None:
        """Targets are whole poses, not sparse deltas, so each costs vertices."""
        body = struct.pack("<IIB", 3, 5, 1)
        body += (struct.pack("<II", 0, 1) + struct.pack("<3f", 0.0, 0.0, 0.0) * 5) * 3
        result = read_nif_bytes(nif(("NiMorphData", body), ("NiShadeProperty", property_body())))
        assert result.stopped_reason == ""
        assert result.blocks[0].fields["morphs"] == [0, 0, 0]

    def test_an_unknown_interpolation_mode_is_refused(self) -> None:
        """A negative control, on the branch that actually reads keys."""
        body = struct.pack("<IIB", 1, 0, 1) + struct.pack("<II", 4, 99)
        assert "interpolation" in read_nif_bytes(nif(("NiMorphData", body))).stopped_reason


class TestParticles:
    """Particle systems: geometry, a controller with an array, and modifiers."""

    def test_the_controller_sizes_its_array_by_the_declared_count(self) -> None:
        """Not by the live count, which is smaller in every observed file.

        Using the live count would under-read every emitter that is not
        currently saturated, which is most of them.
        """
        body = struct.pack("<ihffffi", -1, 8, 1.0, 0.0, 0.0, 1.0, -1)
        body += b"\x00" * 111 + struct.pack("<HH", 7, 3) + b"\x00" * 13
        body += b"\x00" * (7 * 40)
        result = read_nif_bytes(
            nif(("NiParticleSystemController", body), ("NiShadeProperty", property_body()))
        )
        assert result.stopped_reason == ""
        assert result.blocks[0].fields["particles"] == 7
        assert result.blocks[0].fields["num_live_particles"] == 3
        assert result.blocks[1].type_name == "NiShadeProperty"

    def test_rotations_are_sixteen_bytes_a_particle_behind_their_flag(self) -> None:
        """Two 1000-particle fixtures differ by exactly 16000 bytes.

        That difference is what identified the array; this pins it down so a
        future edit cannot quietly change the width.
        """

        def data(rotations: bool) -> bytes:
            body = struct.pack("<H", 4) + struct.pack("<I", 1) + b"\x00" * (4 * 12)
            body += struct.pack("<I", 0) + struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
            body += struct.pack("<I", 0) + struct.pack("<HI", 0, 0)
            body += struct.pack("<HfH", 4, 1.0, 4) + struct.pack("<I", 0)
            body += struct.pack("<I", int(rotations)) + (b"\x00" * (4 * 16) if rotations else b"")
            return body

        with_rot = len(data(True))
        without = len(data(False))
        assert with_rot - without == 4 * 16, "the flag itself is always present"
        for flag in (True, False):
            result = read_nif_bytes(
                nif(("NiRotatingParticlesData", data(flag)), ("NiShadeProperty", property_body()))
            )
            assert result.stopped_reason == "", flag
            assert result.blocks[1].type_name == "NiShadeProperty"

    def test_a_modifier_chain_keeps_its_links(self) -> None:
        """The chain links are the part a structure report can actually use."""
        body = struct.pack("<ii", 6, 4) + struct.pack("<ff", 1.0, 0.1)
        block = read_nif_bytes(nif(("NiParticleGrowFade", body))).blocks[0]
        assert block.link("next_modifier") == 6
        assert block.link("controller") == 4

    def test_color_keys_are_twenty_bytes_each(self) -> None:
        """A time and an RGBA, which is what every fixture reconciles to."""
        body = struct.pack("<II", 3, 1) + (struct.pack("<5f", *([0.0] * 5)) * 3)
        result = read_nif_bytes(nif(("NiColorData", body), ("NiShadeProperty", property_body())))
        assert result.stopped_reason == ""
        assert result.blocks[0].fields["keys"] == 3


class TestEffectsAndImages:
    """The long tail: texture effects, UV animation and embedded images."""

    def test_the_effect_counts_its_pointer_list(self) -> None:
        """Tail is 4 + 4*n + 91, which is what four observed shapes reconcile to.

        The counted entries hold values like 0x0b741950 -- exporter memory
        addresses, not block indices -- so they are stepped over rather than
        offered as links for a caller to follow.
        """
        for count in (0, 1, 4, 5):
            body = text("fx") + struct.pack("<iiH", -1, -1, 0)
            body += b"\0" * 12 + b"\0" * 36 + struct.pack("<f", 1.0) + b"\0" * 12
            body += struct.pack("<II", 0, 0) + struct.pack("<I", count)
            body += b"\x00" * (count * 4) + b"\x00" * 91
            result = read_nif_bytes(
                nif(("NiTextureEffect", body), ("NiShadeProperty", property_body()))
            )
            assert result.stopped_reason == "", count
            assert result.blocks[1].type_name == "NiShadeProperty", count

    def test_uv_data_is_four_key_groups(self) -> None:
        """Empty groups cost four bytes each, which is what makes 52 come out."""
        body = struct.pack("<II", 2, 2) + b"\x00" * 32 + struct.pack("<I", 0) * 3
        result = read_nif_bytes(nif(("NiUVData", body), ("NiShadeProperty", property_body())))
        assert result.stopped_reason == ""
        assert len(body) == 52, "the fixture should match the observed block length"
        assert result.blocks[0].fields["u_keys"] == 2
        assert result.blocks[0].fields["v_scale_keys"] == 0

    def test_pixel_data_reads_its_mipmaps_then_its_pixels(self) -> None:
        """One mipmap entry and a length-prefixed pixel run."""
        body = struct.pack("<6I", 0, 255, 65280, 16711680, 0, 24) + b"\x00" * 8
        body += struct.pack("<iII", -1, 1, 3) + struct.pack("<3I", 4, 4, 0)
        body += struct.pack("<I", 48) + b"\x00" * 48
        result = read_nif_bytes(nif(("NiPixelData", body), ("NiShadeProperty", property_body())))
        assert result.stopped_reason == ""
        assert result.blocks[0].fields["pixels"] == 48
        assert result.blocks[0].fields["mipmaps"] == 1


class TestUvSetsGate:
    """UV data follows the set *count*, not the ``has_uv`` flag.

    Three mod meshes carry ``num_uv_sets=1`` with ``has_uv=0`` and the UV data
    written anyway. Trusting the flag skipped it and desynchronised the rest of
    the block -- and, because the block still ended somewhere plausible, did so
    without raising.
    """

    def _data(self, *, sets: int, has_uv: int, uv_bytes: int) -> bytes:
        body = struct.pack("<H", 4) + struct.pack("<I", 1) + b"\x00" * (4 * 12)
        body += struct.pack("<I", 0)
        body += struct.pack("<4f", 0.0, 0.0, 0.0, 1.0) + struct.pack("<I", 0)
        body += struct.pack("<H", sets) + struct.pack("<I", has_uv)
        body += b"\x00" * uv_bytes
        body += struct.pack("<HI", 1, 3) + b"\x00" * 6 + struct.pack("<H", 0)
        return body

    def test_uv_data_is_read_when_the_count_is_set_but_the_flag_is_not(self) -> None:
        """The exact case the three mod meshes hit."""
        body = self._data(sets=1, has_uv=0, uv_bytes=4 * 1 * 8)
        result = read_nif_bytes(nif(("NiTriShapeData", body), ("NiShadeProperty", property_body())))
        assert result.stopped_reason == ""
        assert result.blocks[1].type_name == "NiShadeProperty"

    def test_no_uv_data_is_read_when_the_count_is_zero(self) -> None:
        """A negative control: the gate must still be able to say no.

        Without this, gating on the count could be satisfied by reading UV data
        unconditionally, which would break every mesh that genuinely has none.
        """
        body = self._data(sets=0, has_uv=0, uv_bytes=0)
        result = read_nif_bytes(nif(("NiTriShapeData", body), ("NiShadeProperty", property_body())))
        assert result.stopped_reason == ""
        assert result.blocks[1].type_name == "NiShadeProperty"


class TestSwitchAndLodNodes:
    """The two node types that dominate modded meshes and never appear in vanilla.

    Between them they accounted for 92% of everything that stopped early in a
    real load order, which is why measuring against vanilla alone had ranked
    them at zero.
    """

    def test_a_switch_node_is_a_node_plus_one_word(self) -> None:
        """Four fixtures, all exactly four bytes past the node's shape."""
        body = av_object("switch", children=0) + struct.pack("<I", 0)
        result = read_nif_bytes(nif(("NiSwitchNode", body), ("NiShadeProperty", property_body())))
        assert result.stopped_reason == ""
        assert result.blocks[1].type_name == "NiShadeProperty"

    def test_lod_levels_are_eight_bytes_each_behind_a_count(self) -> None:
        """Solved from two fixtures: 4 levels leave 52 bytes, 1 level leaves 28.

        ``16 + 4 + n*8`` accounts for both, and no other split does.
        """
        for levels in (1, 4):
            body = av_object("lod", children=0) + b"\x00" * 16
            body += struct.pack("<I", levels) + struct.pack("<2f", 0.0, 1.0) * levels
            result = read_nif_bytes(nif(("NiLODNode", body), ("NiShadeProperty", property_body())))
            assert result.stopped_reason == "", levels
            assert result.blocks[0].fields["lod_levels"] == levels
            assert result.blocks[1].type_name == "NiShadeProperty", levels

    def test_a_truncated_level_table_is_refused(self) -> None:
        """A negative control on the counted part."""
        body = av_object("lod", children=0) + b"\x00" * 16 + struct.pack("<I", 9)
        assert read_nif_bytes(nif(("NiLODNode", body))).stopped_reason != ""


class TestBoundingBoxIsTyped:
    """The box is not one size, which cost a wrong fix before it was understood.

    Solving it from the failing meshes alone gave 20 bytes, and 20 broke every
    file that 64 read correctly. The two populations had to be separated rather
    than averaged: the type word is 1 wherever the flag is set and the file
    parses, and 0 in every file that would not.
    """

    def _node_with_box(self, box_type: int, tail: bytes) -> bytes:
        body = text("boxed") + struct.pack("<iiH", -1, -1, 0)
        body += b"\0" * 12 + b"\0" * 36 + struct.pack("<f", 1.0) + b"\0" * 12
        body += struct.pack("<II", 0, 1) + struct.pack("<I", box_type) + tail
        return body + struct.pack("<II", 0, 0)

    def test_type_one_carries_a_full_transform(self) -> None:
        """A translation, a 3x3 rotation and an extents triple: 60 bytes."""
        data = nif(
            ("NiNode", self._node_with_box(1, b"\0" * 60)),
            ("NiShadeProperty", property_body()),
        )
        result = read_nif_bytes(data)
        assert result.stopped_reason == ""
        assert result.blocks[1].type_name == "NiShadeProperty"

    def test_type_zero_carries_sixteen_bytes(self) -> None:
        """The case that had three mod meshes failing."""
        data = nif(
            ("NiNode", self._node_with_box(0, b"\0" * 16)),
            ("NiShadeProperty", property_body()),
        )
        result = read_nif_bytes(data)
        assert result.stopped_reason == ""
        assert result.blocks[1].type_name == "NiShadeProperty"

    def test_an_unknown_box_type_is_refused_rather_than_guessed(self) -> None:
        """A negative control, and the honest response to an unknown width.

        Guessing would not fail here -- it would desynchronise the rest of the
        file and surface later as something that looks like a different bug.
        """
        data = nif(("NiNode", self._node_with_box(7, b"\0" * 16)))
        assert "bounding box type" in read_nif_bytes(data).stopped_reason

    def test_a_union_holds_a_count_and_then_more_volumes(self) -> None:
        """Type 4 is not a width, which is why a width table cannot hold it.

        A union carries a count followed by that many complete volumes, each
        with its own type word. Found by cross-checking against an independent
        implementation rather than by a failing file -- a union bound would
        have stopped this reader with "unknown bounding box type 4".
        """
        # Two volumes inside the union: one sphere, one box.
        inner = struct.pack("<I", 0) + b"\0" * 16 + struct.pack("<I", 1) + b"\0" * 60
        tail = struct.pack("<I", 2) + inner
        data = nif(
            ("NiNode", self._node_with_box(4, tail)),
            ("NiShadeProperty", property_body()),
        )
        result = read_nif_bytes(data)
        assert result.stopped_reason == ""
        assert result.blocks[1].type_name == "NiShadeProperty"

    def test_an_empty_union_is_legal(self) -> None:
        """A count of zero consumes nothing further, and must not hang."""
        data = nif(
            ("NiNode", self._node_with_box(4, struct.pack("<I", 0))),
            ("NiShadeProperty", property_body()),
        )
        assert read_nif_bytes(data).stopped_reason == ""

    def test_unions_cannot_nest_without_limit(self) -> None:
        """The format lets a union hold unions, so a file can ask for recursion.

        Real files nest one level at most. A corrupt one that nests forever
        must be refused rather than exhaust the interpreter's stack.
        """
        tail = b""
        for _ in range(12):
            tail = struct.pack("<II", 4, 1) + tail
        # Strip the leading type word: _node_with_box writes it.
        data = nif(("NiNode", self._node_with_box(4, tail[4:])))
        assert "nested" in read_nif_bytes(data).stopped_reason


class TestTextureIdentityIgnoresTheExtension:
    """A reference names a file by path and stem; the engine picks the format.

    Base-game meshes routinely say ``.bmp`` or ``.tga`` for files that only
    ever shipped as ``.dds``, and Morrowind loads them regardless. Comparing
    references verbatim therefore invents differences -- and it did: two
    versions of one mesh naming the same texture with different extensions
    were reported as one adding a texture and the other dropping it, on the
    line a user is most likely to act on.
    """

    def test_the_same_texture_named_differently_is_not_a_difference(self) -> None:
        """The exact case seen in the wild: darkbrotherhood_head.bmp vs .dds."""
        loser = Structure(textures=[normalise_texture("darkbrotherhood_head.bmp")])
        winner = Structure(textures=[normalise_texture("darkbrotherhood_head.dds")])
        difference = compare(loser, winner)
        assert difference.added_textures == []
        assert difference.dropped_textures == []

    def test_a_redundant_textures_prefix_is_not_a_difference(self) -> None:
        """Some exporters write it, some do not; it names the same file."""
        difference = compare(
            Structure(textures=[normalise_texture("tx_rock.tga")]),
            Structure(textures=[normalise_texture("textures/tx_rock.dds")]),
        )
        assert difference.added_textures == []

    def test_genuinely_different_textures_are_still_reported(self) -> None:
        """A negative control.

        Without this, the fix could be "never report a texture difference",
        which would pass every assertion above and destroy the finding.
        """
        difference = compare(
            Structure(textures=[normalise_texture("tx_a.dds")]),
            Structure(textures=[normalise_texture("tx_b.dds")]),
        )
        assert difference.added_textures == ["tx_b.dds"]
        assert difference.dropped_textures == ["tx_a.dds"]

    def test_the_report_still_shows_the_reference_as_written(self) -> None:
        """Identity is for comparing; a person wants to see what the mesh says."""
        difference = compare(
            Structure(textures=[normalise_texture("tx_a.tga")]),
            Structure(textures=[normalise_texture("tx_b.BMP")]),
        )
        assert difference.added_textures == ["tx_b.bmp"]

    def test_a_directory_with_a_dot_is_not_mistaken_for_an_extension(self) -> None:
        """``mod.v2/rock`` has no extension, and stripping one would corrupt it."""
        assert texture_key("mod.v2/rock") == "mod.v2/rock"
        assert texture_key("mod.v2/rock.dds") == "mod.v2/rock"


def _node_with_property_count(count: int) -> bytes:
    """A ``NiNode`` body whose property-link count is ``count``, links omitted.

    Used to drive the implausible-count guard: a real body would follow the
    count with that many links, but the guard fires on the count itself.

    Args:
        count: The value to write where the property-link count goes.

    Returns:
        The node body bytes, truncated right after the count.
    """
    return (
        text("n")
        + struct.pack("<ii", -1, -1)  # extra data, controller
        + struct.pack("<H", 0)  # flags
        + b"\0" * 12  # translation
        + b"\0" * 36  # rotation
        + struct.pack("<f", 1.0)  # scale
        + b"\0" * 12  # velocity
        + struct.pack("<I", count)  # property-link count
    )


class TestMalformedVersusLayout:
    """A broken file and a wrong layout are different findings, kept apart.

    The reader guards against a value no valid NIF holds -- an implausible array
    count -- and reports it as :class:`NifMalformedError` / ``stopped_malformed``,
    separate from a layout desync. This is the distinction that kept a real mod's
    corrupt ``NiBSParticleNode`` (which Greatness7's own reader also refuses) out
    of the "layout bug" tally, where it looked like a defect in this reader.
    """

    def test_malformed_error_is_a_parse_error(self) -> None:
        """Callers that only care that reading stopped need not distinguish it."""
        assert issubclass(NifMalformedError, NifParseError)

    def test_an_implausible_count_is_flagged_malformed(self) -> None:
        """A count no valid file holds stops with ``stopped_malformed`` set."""
        result = read_nif_bytes(nif(("NiNode", _node_with_property_count(0xFFFFFFFF))))
        assert result.stopped_malformed is True
        assert result.stopped_at == "NiNode"
        assert "implausible" in result.stopped_reason

    def test_a_plausible_node_is_not_flagged_malformed(self) -> None:
        """A well-formed node reads clean; the flag stays false."""
        result = read_nif_bytes(nif(("NiNode", av_object("root"))))
        assert result.complete
        assert result.stopped_malformed is False


class TestPortedBlocks:
    """The four blocks ported from Greatness7's ``es3`` reference reader.

    Each is checked by parsing it followed by a plain node: the node only reads
    back at the right offset if the block before it consumed exactly its bytes,
    so a two-block file that parses whole proves the new layout's width.
    """

    def _followed_by_node(self, type_name: str, body: bytes) -> None:
        """Parse ``body`` as ``type_name`` then a node, and require both.

        Args:
            type_name: The block type to place first.
            body: Its body bytes.
        """
        result = read_nif_bytes(nif((type_name, body), ("NiNode", av_object("after"))))
        assert result.complete, result.stopped_reason
        assert [block.type_name for block in result.blocks] == [type_name, "NiNode"]

    def test_palette(self) -> None:
        """A has-alpha byte, a count, then that many RGBA quads."""
        body = struct.pack("<B", 1) + struct.pack("<I", 2) + b"\x00" * (2 * 4)
        self._followed_by_node("NiPalette", body)

    def test_lines_data(self) -> None:
        """A geometry base with two vertices, then two connectivity bytes."""
        body = (
            struct.pack("<H", 2)  # num_vertices
            + struct.pack("<I", 1)  # has_vertices
            + b"\x00" * (2 * 3 * 4)  # two xyz vertices
            + struct.pack("<I", 0)  # has_normals
            + b"\x00" * 12  # center
            + struct.pack("<f", 1.0)  # radius
            + struct.pack("<I", 0)  # has_vertex_colors
            + struct.pack("<H", 0)  # num_uv_sets
            + struct.pack("<I", 0)  # has_uv
            + b"\x00" * 2  # one connectivity flag per vertex
        )
        self._followed_by_node("NiLinesData", body)

    def test_skin_partition(self) -> None:
        """One partition, every array empty, no bone palette."""
        partition = struct.pack("<5H", 0, 0, 0, 0, 0) + struct.pack("<B", 0)
        self._followed_by_node("NiSkinPartition", struct.pack("<I", 1) + partition)

    def test_renderer_specific_property(self) -> None:
        """A bare property: name, extra, controller, flags."""
        self._followed_by_node(
            "NiRendererSpecificProperty", text("") + struct.pack("<iiH", -1, -1, 0)
        )

    def test_texture_property(self) -> None:
        """A property plus one image link."""
        body = text("") + struct.pack("<iiH", -1, -1, 0) + struct.pack("<i", -1)
        self._followed_by_node("NiTextureProperty", body)

    def test_particle_meshes(self) -> None:
        """A geometry: the AV object, then data and skin-instance links."""
        # av_object() writes the node tail (children + effects); trim it and add
        # the two geometry links instead, exactly as a NiTriShape body is built.
        head = av_object("mesh")[: -(4 + 4)]
        self._followed_by_node("NiParticleMeshes", head + struct.pack("<ii", -1, -1))

    def test_mirrored_node(self) -> None:
        """A mirrored node is a plain node."""
        self._followed_by_node("BSMirroredNode", av_object("mirror"))

    def test_particle_mesh_modifier(self) -> None:
        """A particle modifier base, then an empty mesh reference list."""
        body = struct.pack("<ii", -1, -1) + struct.pack("<I", 0)  # next, controller, 0 meshes
        self._followed_by_node("NiParticleMeshModifier", body)

    def test_particle_meshes_data(self) -> None:
        """A rotating-particles body, then one trailing modifier link.

        The block is that base plus a single ``Ref``; a following node only
        aligns if the base and the 4-byte link together consumed exactly the
        block's bytes.
        """
        body = (
            struct.pack("<H", 1)  # num_vertices
            + struct.pack("<I", 1)  # has_vertices
            + b"\x00" * 12  # one xyz vertex
            + struct.pack("<I", 0)  # has_normals
            + b"\x00" * 12  # center
            + struct.pack("<f", 1.0)  # radius
            + struct.pack("<I", 0)  # has_vertex_colors
            + struct.pack("<H", 0)  # num_uv_sets
            + struct.pack("<I", 0)  # has_uv
            + struct.pack("<H", 1)  # num_particles
            + struct.pack("<f", 1.0)  # particle_radius
            + struct.pack("<H", 1)  # num_active
            + struct.pack("<I", 0)  # has_sizes
            + struct.pack("<I", 0)  # has_rotations
            + struct.pack("<i", -1)  # modifier link
        )
        self._followed_by_node("NiParticleMeshesData", body)

    def test_keyframe_manager(self) -> None:
        """A time controller, then one inline sequence with no pairs."""
        controller = (
            struct.pack("<i", -1)  # next
            + struct.pack("<H", 8)  # flags
            + struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)  # frequency, phase, start, stop
            + struct.pack("<i", -1)  # target
        )
        sequence = (
            text("seq")
            + struct.pack("<B", 0)  # not external
            + struct.pack("<ii", 0, -1)  # inline int + link
            + struct.pack("<I", 0)  # no name/controller pairs
        )
        self._followed_by_node("NiKeyframeManager", controller + struct.pack("<I", 1) + sequence)


class TestParticleMeshesFixture:
    """The one real file that exercises ``NiParticleMeshesData``.

    Unlike the synthetic fixtures, this is a whole authored mesh (a particle
    system over a mesh template, built in NifSkope). It is the only file in the
    reference corpus that carries a ``NiParticleMeshesData`` block, so it is
    kept here to pin that layout against a genuine file, not just a hand-built
    body: if the block's width regresses, the following blocks lose alignment
    and the whole-file parse and round trip below both fail.
    """

    _FIXTURE = Path(__file__).resolve().parent.parent / "testdata" / "nif" / "NiParticleMeshes.nif"

    def test_the_whole_file_parses_and_round_trips(self) -> None:
        """All 26 blocks parse with no stop, and the file writes back unchanged."""
        data = self._FIXTURE.read_bytes()
        result = read_nif_bytes(data, retain=True)
        assert result.complete, result.stopped_reason
        assert len(result.blocks) == result.block_count == 26
        assert write_nif(result) == data

    def test_the_data_block_links_its_mesh_node(self) -> None:
        """The block is present and its trailing modifier link resolves to a node."""
        result = read_nif_bytes(self._FIXTURE.read_bytes())
        data_block = next(b for b in result.blocks if b.type_name == "NiParticleMeshesData")
        target = data_block.fields["modifier"]
        assert result.blocks[target].type_name == "NiNode"


class TestWriter:
    """:func:`write_nif`, the inverse of the reader.

    The reader keeps element counts, not the elements, so the writer works from
    the block bodies and framing the ``retain=True`` read keeps. Its contract is
    a byte-exact round trip -- verified over the whole corpus during development,
    and pinned in miniature here -- plus the two refusals that keep it honest.
    """

    def _file(self) -> bytes:
        """A two-node file with a root-list footer, as a real NIF has.

        Returns:
            The file bytes.
        """
        return nif(("NiNode", av_object("root")), ("NiNode", av_object("child"))) + struct.pack(
            "<Ii", 1, 0
        )  # footer: one root, index 0

    def test_round_trips_byte_exact(self) -> None:
        """A retained read written back equals the original, footer and all."""
        data = self._file()
        result = read_nif_bytes(data, retain=True)
        assert result.complete
        assert write_nif(result) == data

    def test_a_read_without_retain_cannot_be_written(self) -> None:
        """Without the kept bytes there is nothing to reassemble."""
        result = read_nif_bytes(self._file())  # retain defaults off
        with pytest.raises(NifParseError):
            write_nif(result)

    def test_an_incomplete_read_cannot_be_written(self) -> None:
        """A file whose read stopped early would reassemble wrong; it refuses."""
        data = HEADER + struct.pack("<II", NIF_VERSION_MORROWIND, 3)  # claims 3 blocks, has none
        result = read_nif_bytes(data, retain=True)
        assert result.stopped_at is not None
        with pytest.raises(NifParseError):
            write_nif(result)

    def test_editing_one_block_leaves_the_rest_intact(self) -> None:
        """Replacing a block's body rewrites just it; the framing survives."""
        result = read_nif_bytes(self._file(), retain=True)
        result.blocks[0] = dataclasses.replace(result.blocks[0], raw=av_object("renamed"))
        rewritten = read_nif_bytes(write_nif(result), retain=True)
        assert rewritten.complete
        assert [block.type_name for block in rewritten.blocks] == ["NiNode", "NiNode"]
        assert rewritten.blocks[0].fields["name"] == "renamed"
