"""Tests for every texture decoder and the PNG encoder.

These decoders were checked against an independent implementation during
development, and that check is kept as ``tools/check_images.py`` and
``tools/check_bc7.py`` rather than living here: Pillow is not a dependency of
this project and the texture corpus is not in the repository. At the time of
writing BC7 agreed with Pillow on 19,380 random blocks covering all eight modes
and all 64 partitions, and every other block format agreed on 512 random blocks
each.

What is pinned *here* is different in kind: hand-built inputs whose correct
output can be worked out on paper, and the refusals that turn a broken mod file
into a finding rather than a traceback. A cross-check proves the decoders match
someone else's; these prove they do what this project needs and keep doing it.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from wraithguard.images import (
    BitmapError,
    DdsError,
    Image,
    ImageError,
    ImageFormat,
    TargaError,
    TextureRole,
    browser_image,
    classify,
    comparable,
    detect,
    encode_png,
    read_bmp,
    read_dds,
    read_image,
    read_tga,
)
from wraithguard.images.roles import role_from_name, role_from_osg, role_from_slot


def dds(
    fourcc: bytes,
    width: int,
    height: int,
    surface: bytes,
    *,
    pf_flags: int = 0x4,
    bit_count: int = 0,
    masks: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> bytes:
    """Assemble a DDS file around a surface.

    Args:
        fourcc: The compression tag, or four NULs for uncompressed.
        width: Surface width.
        height: Surface height.
        surface: The pixel or block data.
        pf_flags: Pixel-format flags.
        bit_count: Bits per pixel, for uncompressed surfaces.
        masks: Channel masks, for uncompressed surfaces.

    Returns:
        The whole file.
    """
    header = bytearray(124)
    struct.pack_into("<IIII", header, 0, 124, 0x1007, height, width)
    # The pixel-format block starts at header offset 72 and its own size field
    # occupies the first four bytes, so the flags are at 76 -- not 72. Getting
    # this wrong shifted every field by four and made eight tests fail against
    # a decoder that was correct.
    struct.pack_into("<II4sI", header, 72, 32, pf_flags, fourcc, bit_count)
    struct.pack_into("<IIII", header, 88, *masks)
    return b"DDS " + bytes(header) + surface


def dx10_dds(dxgi: int, width: int, height: int, surface: bytes) -> bytes:
    """Assemble a DDS file with a Direct3D 10 extension header.

    Args:
        dxgi: The DXGI format number.
        width: Surface width.
        height: Surface height.
        surface: The block data.

    Returns:
        The whole file.
    """
    header = bytearray(124)
    struct.pack_into("<IIII", header, 0, 124, 0x1007, height, width)
    struct.pack_into("<II4s", header, 72, 32, 0x4, b"DX10")
    return b"DDS " + bytes(header) + struct.pack("<IIIII", dxgi, 3, 0, 1, 0) + surface


def bc1_block(c0: int, c1: int, indices: int) -> bytes:
    """One DXT1 color block.

    Args:
        c0: First endpoint, packed 5:6:5.
        c1: Second endpoint, packed 5:6:5.
        indices: Sixteen 2-bit selectors.

    Returns:
        Eight bytes.
    """
    return struct.pack("<HHI", c0, c1, indices)


def tga(
    width: int,
    height: int,
    pixels: bytes,
    *,
    image_type: int = 2,
    depth: int = 32,
    descriptor: int = 0,
) -> bytes:
    """Assemble a Targa file.

    Args:
        width: Image width.
        height: Image height.
        pixels: The stored pixel data, blue first.
        image_type: The Targa type code.
        depth: Bits per pixel.
        descriptor: The descriptor byte, carrying the origin bits.

    Returns:
        The whole file.
    """
    header = struct.pack(
        "<BBBHHBHHHHBB", 0, 0, image_type, 0, 0, 0, 0, 0, width, height, depth, descriptor
    )
    return header + pixels


def bmp(width: int, height: int, rows: bytes, *, depth: int = 24, palette: bytes = b"") -> bytes:
    """Assemble a Windows bitmap with a 40-byte information header.

    Args:
        width: Image width.
        height: Image height; negative means the rows are stored top-down.
        rows: The padded row data.
        depth: Bits per pixel.
        palette: The color table, for paletted depths.

    Returns:
        The whole file.
    """
    offset = 14 + 40 + len(palette)
    info = struct.pack(
        "<IiiHHIIiiII", 40, width, height, 1, depth, 0, len(rows), 0, 0, len(palette) // 4, 0
    )
    return struct.pack("<2sIHHI", b"BM", offset + len(rows), 0, 0, offset) + info + palette + rows


WHITE_565 = 0xFFFF
BLACK_565 = 0x0000


class TestBc1:
    """DXT1: two endpoints, four colors, and a punchthrough mode."""

    def test_full_scale_white_decodes_to_255(self) -> None:
        """The channel expansion must replicate bits, not zero-fill them.

        Shifting alone decodes white as (248, 252, 248), which is invisible on
        one texture and shows up as a color cast across a whole comparison.
        """
        image = read_dds(dds(b"DXT1", 4, 4, bc1_block(WHITE_565, WHITE_565, 0)))
        assert image.pixel(0, 0) == (255, 255, 255, 255)

    def test_the_second_endpoint_is_selected_by_index_one(self) -> None:
        """Index 1 picks c1, which pins the bit order of the selector word."""
        image = read_dds(dds(b"DXT1", 4, 4, bc1_block(WHITE_565, BLACK_565, 0b01)))
        assert image.pixel(0, 0) == (0, 0, 0, 255)
        assert image.pixel(1, 0) == (255, 255, 255, 255)

    def test_punchthrough_makes_index_three_transparent(self) -> None:
        """When c0 <= c1 the fourth slot is a hole, not a color.

        This is the mode that carries cutout foliage, so getting it wrong makes
        leaves opaque black rather than absent.
        """
        image = read_dds(dds(b"DXT1", 4, 4, bc1_block(BLACK_565, WHITE_565, 0b11)))
        assert image.pixel(0, 0) == (0, 0, 0, 0)

    def test_the_opaque_mode_gives_a_third_not_a_hole(self) -> None:
        """A negative control: with c0 > c1 index 3 is a real color."""
        image = read_dds(dds(b"DXT1", 4, 4, bc1_block(WHITE_565, BLACK_565, 0b11)))
        red, _green, _blue, alpha = image.pixel(0, 0)
        assert alpha == 255
        assert 0 < red < 255


class TestBc2AndBc3Alpha:
    """The two alpha encodings, which is the whole difference between them."""

    def test_bc2_alpha_is_four_bits_a_pixel_scaled_to_full_range(self) -> None:
        """0xF must reach 255, or every texture is faintly transparent."""
        alpha = b"\xf0" + b"\x00" * 7
        surface = alpha + bc1_block(WHITE_565, WHITE_565, 0)
        image = read_dds(dds(b"DXT3", 4, 4, surface))
        assert image.pixel(0, 0)[3] == 0
        assert image.pixel(1, 0)[3] == 255

    def test_bc3_uses_its_endpoints_directly_for_the_first_two_indices(self) -> None:
        """Index 0 is a0 and index 1 is a1, whichever interpolation mode."""
        alpha = bytes((200, 40)) + bytes((0b01_000_000, 0, 0, 0, 0, 0))
        surface = alpha + bc1_block(WHITE_565, WHITE_565, 0)
        image = read_dds(dds(b"DXT5", 4, 4, surface))
        assert image.pixel(0, 0)[3] == 200
        assert image.pixel(2, 0)[3] == 40

    def test_bc3_six_value_mode_has_explicit_transparent_and_opaque_slots(self) -> None:
        """With a0 <= a1, indices 6 and 7 are 0 and 255 rather than interpolants."""
        alpha = bytes((40, 200)) + bytes((0b110, 0, 0, 0, 0, 0))
        surface = alpha + bc1_block(WHITE_565, WHITE_565, 0)
        image = read_dds(dds(b"DXT5", 4, 4, surface))
        assert image.pixel(0, 0)[3] == 0

    def test_bc3_never_uses_the_punchthrough_color_mode(self) -> None:
        """Its alpha lives in its own block, so index 3 is always a color.

        Sharing the DXT1 rule here would punch holes in every DXT5 texture
        whose endpoints happen to be ordered the other way.
        """
        surface = b"\xff" * 8 + bc1_block(BLACK_565, WHITE_565, 0b11)
        image = read_dds(dds(b"DXT5", 4, 4, surface))
        assert image.pixel(0, 0)[3] == 255


class TestBc4AndBc5:
    """The one- and two-channel formats, which mods use for masks and normals."""

    def test_bc4_expands_its_single_channel_to_grey(self) -> None:
        """There is no color in a height or gloss map to recover.

        Dropping the value into red alone would render every one of them as a
        red wash, which reads as a decode failure rather than a mask.
        """
        block = bytes((200, 40)) + bytes(6)
        image = read_dds(dds(b"ATI1", 4, 4, block))
        red, green, blue, alpha = image.pixel(0, 0)
        assert (red, green, blue) == (200, 200, 200)
        assert alpha == 255

    def test_bc4_truncated_before_a_block_is_refused(self) -> None:
        """A BC4 surface too short for its first block fails as a finding."""
        with pytest.raises(DdsError, match="BC4 data ends early"):
            read_dds(dds(b"ATI1", 4, 4, b"\x00\x00\x00\x00"))  # needs 8 bytes, has 4

    def test_bc5_truncated_before_a_block_is_refused(self) -> None:
        """A BC5 surface too short for its first 16-byte block fails as a finding."""
        with pytest.raises(DdsError, match="BC5 data ends early"):
            read_dds(dds(b"ATI2", 4, 4, b"\x00" * 8))  # needs 16 bytes, has 8

    def test_a_dx10_format_with_no_fourcc_mapping_is_refused(self) -> None:
        """A DXGI number that is neither refused nor mapped cannot be decoded."""
        with pytest.raises(DdsError, match="unsupported DXGI format 200"):
            read_dds(dx10_dds(200, 4, 4, bytes(16)))

    def test_a_dx10_header_that_is_truncated_is_a_finding_not_a_struct_error(self) -> None:
        """A DX10 tag with no extension header degrades to a DdsError."""
        with pytest.raises(DdsError):
            read_dds(dds(b"DX10", 4, 4, b""))  # no DX10 extension bytes follow

    def test_a_luminance_surface_without_a_mask_decodes_to_grey(self) -> None:
        """A luminance format with no explicit mask is treated as full-depth grey."""
        image = read_dds(
            dds(
                b"\x00\x00\x00\x00",
                2,
                2,
                bytes([128, 128, 128, 128]),
                pf_flags=0x20000,  # _DDPF_LUMINANCE
                bit_count=8,
                masks=(0, 0, 0, 0),
            )
        )
        red, green, blue, _alpha = image.pixel(0, 0)
        assert red == green == blue

    def test_bc5_reconstructs_blue_from_the_two_stored_channels(self) -> None:
        """A flat normal must come back pointing straight out of the surface.

        Leaving blue at zero decodes every flat surface as though it faced
        sideways, which tilts the lighting on everything in the game.
        """
        # Both channels at mid-scale: x and y are zero, so z is one.
        block = bytes((128, 128)) + bytes(6) + bytes((128, 128)) + bytes(6)
        image = read_dds(dds(b"ATI2", 4, 4, block))
        red, green, blue, _alpha = image.pixel(0, 0)
        assert (red, green) == (128, 128)
        assert blue >= 250

    def test_bc5_green_is_not_flipped(self) -> None:
        """Morrowind and OpenMW both use the DirectX convention.

        Tooling written for OpenGL flips green on load. Doing that here would
        make every normal map differ from a byte-identical copy of itself,
        which is precisely the comparison this project exists to make.
        """
        block = bytes((200, 200)) + bytes(6) + bytes((40, 40)) + bytes(6)
        image = read_dds(dds(b"ATI2", 4, 4, block))
        assert image.pixel(0, 0)[1] == 40

    def test_the_modern_spellings_decode_the_same_way(self) -> None:
        """BC4U and BC5U are ATI1 and ATI2 under later names."""
        block = bytes((200, 40)) + bytes(6)
        assert (
            read_dds(dds(b"BC4U", 4, 4, block)).pixels == read_dds(dds(b"ATI1", 4, 4, block)).pixels
        )


class TestBc7:
    """The eight-mode format, checked here only where paper reasoning applies.

    Correctness across the mode and partition tables is established by
    ``tools/check_bc7.py`` against an independent decoder, because six hundred
    transcribed table entries cannot be meaningfully spot-checked by hand.
    """

    def test_it_is_reached_through_the_dx10_header(self) -> None:
        """BC7 has no FourCC, so a header that does not parse means no BC7."""
        image = read_dds(dx10_dds(98, 4, 4, b"\x40" + bytes(15)))
        assert (image.width, image.height) == (4, 4)

    def test_the_reserved_pattern_decodes_to_a_hole_not_an_error(self) -> None:
        """All eight mode bits zero is reserved, and the format defines it.

        One bad block in a large texture should leave a hole, not fail the
        whole surface -- a mod with a single corrupt block is still worth
        looking at.
        """
        image = read_dds(dx10_dds(98, 4, 4, bytes(16)))
        assert image.pixel(0, 0) == (0, 0, 0, 0)

    def test_bc6h_is_refused_by_name(self) -> None:
        """An HDR format with no use in this game, so the message says so."""
        with pytest.raises(DdsError, match="HDR"):
            read_dds(dx10_dds(95, 4, 4, bytes(16)))

    def test_a_dx10_header_that_is_not_there_is_a_truncation(self) -> None:
        """Not an unsupported format: the distinction is what the user acts on."""
        header = bytearray(124)
        struct.pack_into("<IIII", header, 0, 124, 0x1007, 4, 4)
        struct.pack_into("<II4s", header, 72, 32, 0x4, b"DX10")
        with pytest.raises(DdsError, match="and then ends"):
            read_dds(b"DDS " + bytes(header))


class TestPartialBlocks:
    """Textures are not always a multiple of four pixels."""

    def test_a_surface_narrower_than_a_block_is_cropped_not_padded(self) -> None:
        """A 2x2 texture is one block with twelve pixels discarded."""
        image = read_dds(dds(b"DXT1", 2, 2, bc1_block(WHITE_565, WHITE_565, 0)))
        assert (image.width, image.height) == (2, 2)
        assert len(image.pixels) == 2 * 2 * 4

    def test_a_non_multiple_size_still_fills_every_pixel(self) -> None:
        """6x6 is four blocks, and no pixel may be left unwritten."""
        surface = bc1_block(WHITE_565, WHITE_565, 0) * 4
        image = read_dds(dds(b"DXT1", 6, 6, surface))
        assert all(image.pixel(x, y) == (255, 255, 255, 255) for x in range(6) for y in range(6))

    def test_bc7_also_crops_its_edge_blocks(self) -> None:
        """The same rule, on the decoder most likely to get it wrong."""
        image = read_dds(dx10_dds(98, 5, 5, (b"\x40" + bytes(15)) * 4))
        assert (image.width, image.height) == (5, 5)
        assert len(image.pixels) == 5 * 5 * 4


class TestTarga:
    """The format meshes name constantly and mods sometimes actually ship."""

    def test_channels_are_stored_blue_first(self) -> None:
        """Assuming RGB gives an image that looks right until the sky is orange."""
        image = read_tga(tga(1, 1, bytes((10, 20, 30, 40))))
        assert image.pixel(0, 0) == (30, 20, 10, 40)

    def test_the_origin_defaults_to_the_bottom_left(self) -> None:
        """Ignoring it returns the image mirrored, which tiles plausibly.

        That is the dangerous kind of wrong: a flipped tiling texture looks
        fine and compares as different from an identical file.
        """
        # Two rows: first stored is the bottom one.
        pixels = bytes((1, 1, 1, 255)) + bytes((2, 2, 2, 255))
        image = read_tga(tga(1, 2, pixels))
        assert image.pixel(0, 0)[0] == 2
        assert image.pixel(0, 1)[0] == 1

    def test_the_descriptor_bit_flips_it_back(self) -> None:
        """A negative control, so the origin handling means something."""
        pixels = bytes((1, 1, 1, 255)) + bytes((2, 2, 2, 255))
        image = read_tga(tga(1, 2, pixels, descriptor=0x20))
        assert image.pixel(0, 0)[0] == 1

    def test_a_run_packet_repeats_one_pixel(self) -> None:
        """The high bit selects a run, and the count is stored minus one."""
        packet = bytes((0x83,)) + bytes((9, 8, 7, 255))
        image = read_tga(tga(4, 1, packet, image_type=10))
        assert all(image.pixel(x, 0) == (7, 8, 9, 255) for x in range(4))

    def test_a_literal_packet_carries_distinct_pixels(self) -> None:
        """The other half of the encoding, and the half that desynchronises."""
        packet = bytes((0x01,)) + bytes((1, 0, 0, 255)) + bytes((2, 0, 0, 255))
        image = read_tga(tga(2, 1, packet, image_type=10))
        assert image.pixel(0, 0)[2] == 1
        assert image.pixel(1, 0)[2] == 2

    def test_a_packet_cut_off_mid_pixel_is_refused(self) -> None:
        """These arrive from mod archives and must fail as findings."""
        with pytest.raises(TargaError, match="truncated"):
            read_tga(tga(64, 64, bytes((0x81,)), image_type=10))

    def test_packets_that_stop_before_the_image_is_full_are_refused(self) -> None:
        """A different failure from a cut-off packet, and a different message.

        Here every packet is well formed and there are simply too few of them,
        which is what a partially written file looks like.
        """
        one_full_run = bytes((0xFF,)) + bytes((1, 2, 3, 255))
        with pytest.raises(TargaError, match="ends early"):
            read_tga(tga(64, 64, one_full_run, image_type=10))


class TestBitmap:
    """The 2002 toolchain's default, still present across the base game."""

    def test_rows_are_padded_to_four_bytes(self) -> None:
        """A one-pixel-wide 24-bit row occupies four bytes, not three.

        Assuming otherwise drifts one row at a time and produces the diagonal
        smear that is the classic symptom of this bug.
        """
        rows = bytes((10, 20, 30, 0)) + bytes((40, 50, 60, 0))
        image = read_bmp(bmp(1, 2, rows))
        assert image.pixel(0, 0) == (60, 50, 40, 255)
        assert image.pixel(0, 1) == (30, 20, 10, 255)

    def test_a_negative_height_means_the_rows_are_already_top_down(self) -> None:
        """The other row order, which is rarer and therefore less tested."""
        rows = bytes((10, 20, 30, 0)) + bytes((40, 50, 60, 0))
        image = read_bmp(bmp(1, -2, rows))
        assert image.pixel(0, 0) == (30, 20, 10, 255)

    def test_a_paletted_bitmap_resolves_its_indices(self) -> None:
        """8-bit bitmaps are what the older mods actually ship."""
        palette = bytes((1, 2, 3, 0)) + bytes((4, 5, 6, 0))
        image = read_bmp(bmp(2, 1, bytes((0, 1, 0, 0)), depth=8, palette=palette))
        assert image.pixel(0, 0) == (3, 2, 1, 255)
        assert image.pixel(1, 0) == (6, 5, 4, 255)

    def test_a_plain_32_bit_bitmap_is_opaque_not_invisible(self) -> None:
        """Its fourth byte is padding, and old writers leave it zero.

        Honouring it as alpha decodes the whole image to nothing, which looks
        exactly like a missing texture and would be reported as one.
        """
        image = read_bmp(bmp(1, 1, bytes((10, 20, 30, 0)), depth=32))
        assert image.pixel(0, 0) == (30, 20, 10, 255)

    def test_run_length_encoding_is_refused_by_name(self) -> None:
        """Rare enough not to implement, common enough to name in the message."""
        broken = bytearray(bmp(1, 1, bytes(4), depth=8, palette=bytes(4)))
        struct.pack_into("<I", broken, 14 + 16, 1)
        with pytest.raises(BitmapError, match="run-length"):
            read_bmp(bytes(broken))


class TestFormatDetection:
    """Dispatching on the extension would be wrong for much of the base game."""

    def test_each_format_is_recognised_by_its_own_bytes(self) -> None:
        """Every format here announces itself except Targa."""
        assert detect(dds(b"DXT1", 4, 4, bytes(8))) is ImageFormat.DDS
        assert detect(bmp(1, 1, bytes(4))) is ImageFormat.BMP
        assert detect(b"\x89PNG\r\n\x1a\n" + bytes(32)) is ImageFormat.PNG
        assert detect(b"II\x2a\x00" + bytes(32)) is ImageFormat.TIFF

    def test_a_targa_is_identified_by_elimination(self) -> None:
        """It predates magic numbers, so its header is judged for plausibility."""
        assert detect(tga(4, 4, bytes(64))) is ImageFormat.TGA

    def test_noise_is_not_mistaken_for_a_targa(self) -> None:
        """The fallback must be able to say no, or it catches everything."""
        assert detect(b"\xff" * 64) is ImageFormat.UNKNOWN

    def test_a_dds_carrying_a_tga_name_still_decodes(self) -> None:
        """The case this module exists for.

        Morrowind's engine falls back between extensions, so a mesh naming a
        ``.tga`` is routinely resolved to a DDS on disk. Dispatching by name
        would send it to the wrong decoder for a large share of the game.
        """
        image = read_image(dds(b"DXT1", 4, 4, bc1_block(WHITE_565, WHITE_565, 0)))
        assert image.pixel(0, 0) == (255, 255, 255, 255)

    def test_tiff_is_recognised_but_refused(self) -> None:
        """Naming it is more useful than calling it "not an image"."""
        with pytest.raises(ImageError, match="TIFF"):
            read_image(b"II\x2a\x00" + bytes(64))


class TestBrowserImage:
    """What the 3D viewer asks for, which is not always a decode."""

    def test_a_png_is_handed_back_untouched(self) -> None:
        """Re-encoding one could only cost time and lose fidelity."""
        original = encode_png(Image(2, 2, bytes(range(16))))
        payload, mime = browser_image(original)
        assert payload is original
        assert mime == "image/png"

    def test_everything_else_is_re_encoded_as_png(self) -> None:
        """A browser cannot read a DDS, so this is where the decoder earns out."""
        payload, mime = browser_image(dds(b"DXT1", 4, 4, bc1_block(WHITE_565, WHITE_565, 0)))
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        assert mime == "image/png"

    def test_a_bitmap_we_cannot_decode_still_reaches_the_browser(self) -> None:
        """Browsers read run-length bitmaps; showing one beats showing an error.

        Passthrough is the fallback rather than the first choice, because
        decoding first is what makes a genuinely corrupt file reportable.
        """
        broken = bytearray(bmp(1, 1, bytes(4), depth=8, palette=bytes(4)))
        struct.pack_into("<I", broken, 14 + 16, 1)
        payload, mime = browser_image(bytes(broken))
        assert mime == "image/bmp"
        assert payload == bytes(broken)


class TestTextureRoles:
    """Which channel of a material a texture is, so like is compared with like."""

    def test_the_vanilla_slots_carry_the_role(self) -> None:
        """For a NIF the slot is what the engine honours, not the file name."""
        assert role_from_slot("base") is TextureRole.DIFFUSE
        assert role_from_slot("glow") is TextureRole.GLOW
        assert role_from_slot("decal_2") is TextureRole.DECAL

    def test_the_bump_slot_is_not_called_a_normal_map(self) -> None:
        """Vanilla ignores it, and MGE puts normals in the environment slot.

        What a bump slot means depends on the toolchain that wrote the file,
        so recording it as its own role is the honest answer.
        """
        assert role_from_slot("bump") is TextureRole.BUMP

    def test_openmw_suffixes_are_matched_longest_first(self) -> None:
        """``_diffusespec`` also ends in ``spec``.

        Matching the shorter pattern first files every terrain layer as a
        plain specular map, which is wrong and entirely silent.
        """
        assert role_from_name("rock_diffusespec.dds") is TextureRole.DIFFUSE_SPECULAR
        assert role_from_name("rock_spec.dds") is TextureRole.SPECULAR
        assert role_from_name("rock_nh.dds") is TextureRole.NORMAL_HEIGHT
        assert role_from_name("rock_n.dds") is TextureRole.NORMAL

    def test_a_plain_name_has_no_opinion(self) -> None:
        """Most vanilla textures carry no suffix, and that is not "color"."""
        assert role_from_name("tx_rock_01.dds") is TextureRole.UNKNOWN

    def test_osg_units_name_the_role_outright(self) -> None:
        """The only one of the three conventions that is unambiguous."""
        assert role_from_osg("normalMap") is TextureRole.NORMAL
        assert role_from_osg("emissiveMap") is TextureRole.GLOW
        assert role_from_osg("envMap") is TextureRole.ENVIRONMENT

    def test_a_normal_suffix_beats_a_base_slot(self) -> None:
        """A pack adding an OpenMW map to a mesh written before the convention.

        Treating it as diffuse would show a field of normals as a photograph.
        """
        assert classify("rock_n.dds", slot="base") is TextureRole.NORMAL

    def test_an_osg_name_beats_everything(self) -> None:
        """It is a declaration; the others are conventions."""
        assert classify("rock_n.dds", slot="base", osg_name="specularMap") is TextureRole.SPECULAR

    def test_two_normal_maps_are_worth_comparing(self) -> None:
        """The case that motivated the whole module: both sides are normals."""
        assert comparable(TextureRole.NORMAL, TextureRole.NORMAL)
        assert comparable(TextureRole.NORMAL, TextureRole.BUMP)

    def test_a_normal_map_against_a_diffuse_map_is_not_a_conflict(self) -> None:
        """They are complementary channels of one material, not rivals."""
        assert not comparable(TextureRole.NORMAL, TextureRole.DIFFUSE)

    def test_no_opinion_never_blocks_a_comparison(self) -> None:
        """Otherwise the feature switches itself off for the whole base game."""
        assert comparable(TextureRole.UNKNOWN, TextureRole.DIFFUSE)

    def test_a_specular_map_is_color_not_a_mask(self) -> None:
        """Its RGB is specular color; only gloss is a single channel."""
        assert not TextureRole.SPECULAR.is_mask
        assert TextureRole.GLOSS.is_mask


class TestRefusalsAreFindings:
    """These files come from mod archives and must fail as findings."""

    def test_a_non_dds_is_refused_by_name(self) -> None:
        """Extensions lie."""
        with pytest.raises(DdsError, match="DDS "):
            read_dds(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)

    def test_an_unsupported_compression_is_named(self) -> None:
        """A tag we do not know must say which, rather than produce garbage."""
        with pytest.raises(DdsError, match="ETC2"):
            read_dds(dds(b"ETC2", 4, 4, b"\x00" * 16))

    def test_a_truncated_surface_is_refused(self) -> None:
        """Half a block is not half an image."""
        with pytest.raises(DdsError, match="ends early"):
            read_dds(dds(b"DXT1", 64, 64, b"\x00" * 8))

    def test_implausible_dimensions_are_refused(self) -> None:
        """A corrupt header must not become a multi-gigabyte allocation."""
        with pytest.raises(DdsError, match="implausible"):
            read_dds(dds(b"DXT1", 1 << 20, 1 << 20, b"\x00" * 8))

    def test_a_plausible_pair_of_dimensions_can_still_be_an_absurd_area(self) -> None:
        """Bounding each side is not the same as bounding the allocation.

        32768 x 32768 passes a per-dimension check and then asks for 4.3 GB of
        RGBA before any texture data is read. Found by auditing this module
        rather than by a failing file.
        """
        with pytest.raises(DdsError, match="implausible size"):
            read_dds(dds(b"DXT1", 1 << 15, 1 << 15, b"\x00" * 8))


class TestUncompressed:
    """Not every Morrowind texture is compressed."""

    def test_masks_place_the_channels(self) -> None:
        """The masks say where each channel lives, so they must be honoured."""
        # B, G, R, A in memory order, which is what these headers describe.
        surface = bytes((10, 20, 30, 40))
        image = read_dds(
            dds(
                b"\x00\x00\x00\x00",
                1,
                1,
                surface,
                pf_flags=0x41,
                bit_count=32,
                masks=(0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000),
            )
        )
        assert image.pixel(0, 0) == (30, 20, 10, 40)

    def test_no_alpha_mask_means_opaque_not_transparent(self) -> None:
        """A 24-bit surface has no alpha, and must not decode as invisible."""
        image = read_dds(
            dds(
                b"\x00\x00\x00\x00",
                1,
                1,
                bytes((10, 20, 30)),
                pf_flags=0x40,
                bit_count=24,
                masks=(0x00FF0000, 0x0000FF00, 0x000000FF, 0),
            )
        )
        assert image.pixel(0, 0)[3] == 255

    def test_a_16_bit_surface_is_decoded_through_its_masks(self) -> None:
        """These exist in the base game's interface art."""
        image = read_dds(
            dds(
                b"\x00\x00\x00\x00",
                1,
                1,
                struct.pack("<H", 0xF800),
                pf_flags=0x40,
                bit_count=16,
                masks=(0xF800, 0x07E0, 0x001F, 0),
            )
        )
        assert image.pixel(0, 0) == (255, 0, 0, 255)


class TestPng:
    """The encoder writes one thing, and must write it correctly."""

    def test_it_produces_a_parseable_rgba_png(self) -> None:
        """Signature, IHDR, and pixels that survive the round trip.

        Parsed with :mod:`zlib` rather than an image library, so the test has
        the same dependency footprint as the code it covers.
        """
        pixels = bytes(range(16))
        png = encode_png(Image(2, 2, pixels))
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert png[12:16] == b"IHDR"
        width, height, depth, color = struct.unpack_from(">IIBB", png, 16)
        assert (width, height, depth, color) == (2, 2, 8, 6)
        start = png.index(b"IDAT") + 4
        length = struct.unpack_from(">I", png, start - 8)[0]
        raw = zlib.decompress(png[start : start + length])
        # One filter byte per scanline, then the pixels unchanged.
        assert raw == b"\x00" + pixels[:8] + b"\x00" + pixels[8:]

    def test_every_chunk_carries_a_correct_crc(self) -> None:
        """A wrong CRC makes a file that some viewers open and others reject."""
        png = encode_png(Image(1, 1, b"\x01\x02\x03\x04"))
        offset = 8
        seen = []
        while offset < len(png):
            length = struct.unpack_from(">I", png, offset)[0]
            tag = png[offset + 4 : offset + 8]
            body = png[offset + 8 : offset + 8 + length]
            stored = struct.unpack_from(">I", png, offset + 8 + length)[0]
            assert stored == zlib.crc32(tag + body) & 0xFFFFFFFF, tag
            seen.append(tag)
            offset += 12 + length
        assert seen == [b"IHDR", b"IDAT", b"IEND"]


class TestTheImageTypeItself:
    """The one shape every decoder here produces."""

    def test_a_buffer_that_does_not_match_the_dimensions_is_refused(self) -> None:
        """A decoder that miscounts rows renders as diagonal garbage.

        That is far harder to diagnose from a screenshot than an exception is,
        so the type refuses to hold an inconsistent buffer at all.
        """
        with pytest.raises(ImageError, match="needs"):
            Image(4, 4, b"\x00" * 8)

    def test_reading_outside_the_image_raises(self) -> None:
        """A viewer asking for a pixel that is not there is a caller bug."""
        image = read_dds(dds(b"DXT1", 4, 4, bc1_block(WHITE_565, WHITE_565, 0)))
        with pytest.raises(IndexError, match="outside"):
            image.pixel(4, 0)


class TestRemainingRefusals:
    """The error branches an audit found untested.

    These are the paths that run on files from mod archives, so they are the
    ones that most need to fail as findings rather than as tracebacks -- and
    they were the least exercised part of the module.
    """

    def test_a_wrong_header_size_is_refused(self) -> None:
        """The field is in the file, so it is checked rather than trusted."""
        broken = bytearray(dds(b"DXT1", 4, 4, b"\x00" * 8))
        struct.pack_into("<I", broken, 4, 100)
        with pytest.raises(DdsError, match="header claims"):
            read_dds(bytes(broken))

    def test_an_unsupported_uncompressed_depth_is_refused(self) -> None:
        """4-bit surfaces are not something to guess at."""
        with pytest.raises(DdsError, match="depth"):
            read_dds(
                dds(
                    b"\x00\x00\x00\x00",
                    2,
                    2,
                    b"\x00" * 8,
                    pf_flags=0x40,
                    bit_count=4,
                    masks=(0xF, 0, 0, 0),
                )
            )

    def test_a_truncated_uncompressed_surface_is_refused(self) -> None:
        """Same guarantee as the compressed path, which was already tested."""
        with pytest.raises(DdsError, match="ends early"):
            read_dds(
                dds(
                    b"\x00\x00\x00\x00",
                    16,
                    16,
                    b"\x00" * 8,
                    pf_flags=0x41,
                    bit_count=32,
                    masks=(0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000),
                )
            )

    def test_a_pixel_format_that_is_neither_is_refused(self) -> None:
        """A surface that is neither compressed, RGB, nor luminance."""
        with pytest.raises(DdsError, match="pixel format"):
            read_dds(dds(b"\x00\x00\x00\x00", 2, 2, b"\x00" * 16, pf_flags=0x200))


class TestReaderFormatDispatch:
    """The format enum's capability flags, and read_image's dispatch/refusals."""

    def test_decodable_flags_the_formats_we_turn_into_pixels(self) -> None:
        """DDS/BMP/TGA decode here; PNG/TIFF/unknown do not."""
        assert ImageFormat.DDS.decodable
        assert not ImageFormat.PNG.decodable

    def test_browser_native_flags_what_a_browser_shows_untouched(self) -> None:
        """PNG and BMP are handed over as-is; DDS is not."""
        assert ImageFormat.PNG.browser_native
        assert not ImageFormat.DDS.browser_native

    def test_looks_like_tga_rejects_too_short_data(self) -> None:
        """A buffer too short to hold a TGA header is not a TGA."""
        from wraithguard.images.reader import _looks_like_tga

        assert _looks_like_tga(b"short") is False

    def test_read_image_refuses_png(self) -> None:
        """PNG decoding is intentionally not implemented."""
        with pytest.raises(ImageError, match="PNG"):
            read_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    def test_read_image_refuses_tiff(self) -> None:
        """TIFF is not a format the viewer decodes."""
        with pytest.raises(ImageError, match="TIFF"):
            read_image(b"II*\x00" + b"\x00" * 20)

    def test_read_image_refuses_an_unrecognised_format(self) -> None:
        """Bytes matching no known signature are refused, not guessed at."""
        with pytest.raises(ImageError, match="unrecognised"):
            read_image(b"not any known image header at all")

    def test_read_image_decodes_a_targa(self) -> None:
        """A real TGA is dispatched to the Targa decoder and returns pixels."""
        image = read_image(tga(1, 1, bytes((10, 20, 30, 40))))
        assert image.width == 1
        assert image.height == 1

    def test_looks_like_tga_accepts_a_real_header(self) -> None:
        """A well-formed TGA header passes the field checks, not just the footer."""
        from wraithguard.images.reader import _looks_like_tga

        assert _looks_like_tga(tga(1, 1, bytes((10, 20, 30, 40)))) is True

    def test_looks_like_tga_accepts_the_v2_footer(self) -> None:
        """A TGA 2.0 footer signature is recognised even without a valid header."""
        from wraithguard.images.reader import _TGA_FOOTER, _looks_like_tga

        assert _looks_like_tga(b"\x00" * 10 + _TGA_FOOTER + b"\x00") is True

    def test_browser_image_reraises_for_an_undisplayable_format(self) -> None:
        """An unrecognised, non-browser-native blob cannot be shown, so it raises."""
        from wraithguard.images import browser_image

        with pytest.raises(ImageError):
            browser_image(b"not any known image header at all")


class TestTextureRoleClassification:
    """The role properties and the ``classify`` precedence rules."""

    def test_is_color_excludes_normal_maps_and_masks(self) -> None:
        """A side-by-side colour view means something only for colour textures."""
        assert TextureRole.DIFFUSE.is_color
        assert not TextureRole.NORMAL.is_color

    def test_carries_height_is_the_normal_family_only(self) -> None:
        """Alpha holds parallax height only in the normal-map roles."""
        assert TextureRole.NORMAL.carries_height
        assert not TextureRole.DIFFUSE.carries_height

    def test_role_from_name_without_an_extension_is_unknown(self) -> None:
        """A bare stem with no recognised suffix carries no opinion."""
        assert role_from_name("tx_rock") is TextureRole.UNKNOWN

    def test_classify_falls_through_an_unrecognised_osg_unit(self) -> None:
        """An OSG unit name that states no role does not veto the name evidence."""
        assert classify("tx_n.dds", osg_name="mysteryunit") is TextureRole.NORMAL

    def test_classify_prefers_a_normal_suffix_over_a_base_slot(self) -> None:
        """A normal-map name in a diffuse slot is a normal map, not a photograph."""
        assert classify("tx_n.dds", slot="base") is TextureRole.NORMAL

    def test_classify_honours_a_non_diffuse_slot_over_a_bare_name(self) -> None:
        """A slot the engine reads wins over a name that carries no opinion."""
        assert classify("tx.dds", slot="glow") is TextureRole.GLOW
