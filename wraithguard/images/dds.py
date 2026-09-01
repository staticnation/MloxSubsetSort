"""Decoding DDS textures to plain RGBA, with no third-party dependency.

A texture conflict raises the same question a mesh conflict does -- *does the
winner actually look different?* -- and answering it needs pixels. Every Python
library that decodes DDS is either a large dependency, a wrapper around a C
library that complicates a onefile build, or licensed incompatibly with this
project. ``pydds`` is the closest fit and is GPLv3, which would relicense
everything here. The formats are arithmetic, so they are decoded here.

**What is handled.** BC1 through BC5 and BC7, plus uncompressed surfaces
described by channel masks. That covers Morrowind's own textures (BC1, BC3 and
the occasional uncompressed surface), the normal maps that current mods ship
(BC5), the single-channel height and gloss masks that come with them (BC4),
and the BC7 that OpenMW-era replacers use. BC6H is HDR, has no use in this
game, and is refused by name rather than guessed at.

**Two eras of header.** The original pixel format identifies compression with
a four-character code. Anything defined after Direct3D 10 -- BC7 included --
sets that code to ``DX10`` and puts the real format in a twenty-byte extension
header after it. Both are read; a file that says ``DX10`` and then stops is a
truncation, not a format this decoder lacks, and it says so.

**Normal maps are not color.** BC5 stores two channels because a tangent-space
normal's third can be recomputed, and this reconstructs it -- see
:func:`_decode_two_channel`. Morrowind and OpenMW both use the **DirectX**
convention, so the green channel is *not* flipped on load. Flipping it, which
tooling written against the OpenGL convention does by default, would make every
normal map in the collection compare as different from an identical copy of
itself.

**The block formats are arithmetic, not expression.** A DXT1 block is two
16-bit colors and sixteen 2-bit indices; the decode is the interpolation the
format defines. That is a fact about the format, derived here from the public
description and checked against real files, in the same way and for the same
licensing reasons as ``wraithguard.nif`` -- see ``NIF_PROVENANCE.md``.
"""

from __future__ import annotations

import math
import struct
from typing import Final

from wraithguard.images import bc7
from wraithguard.images.image import Image, ImageError
from wraithguard.logging_setup import get_logger

LOG = get_logger(__name__)

#: The four bytes every DDS file starts with.
MAGIC: Final[bytes] = b"DDS "

#: Header length in bytes, excluding the magic. The field is in the file too
#: and is checked rather than trusted.
_HEADER_SIZE: Final[int] = 124

#: Length of the Direct3D 10 extension header that follows a ``DX10`` code.
_DX10_SIZE: Final[int] = 20

#: Flag in ``pixel_format.flags`` meaning "the FourCC field is meaningful".
_DDPF_FOURCC: Final[int] = 0x4

#: Flag meaning the surface stores uncompressed RGB.
_DDPF_RGB: Final[int] = 0x40

#: Flag meaning an uncompressed surface carries alpha.
_DDPF_ALPHAPIXELS: Final[int] = 0x1

#: Flag meaning the surface is a single luminance channel.
_DDPF_LUMINANCE: Final[int] = 0x20000

#: The code that says "the real format is in the extension header".
_DX10: Final[bytes] = b"DX10"

#: Bytes per compressed block, by FourCC. DXT1 and the single-channel BC4 pack
#: eight; everything else carries a second eight-byte half.
_BLOCK_BYTES: Final[dict[bytes, int]] = {
    b"DXT1": 8,
    b"DXT2": 16,
    b"DXT3": 16,
    b"DXT4": 16,
    b"DXT5": 16,
    b"ATI1": 8,
    b"BC4U": 8,
    b"BC4S": 8,
    b"ATI2": 16,
    b"BC5U": 16,
    b"BC5S": 16,
}

#: FourCCs that mean "one channel, stored as a DXT5-style interpolated block".
_ONE_CHANNEL: Final[frozenset[bytes]] = frozenset({b"ATI1", b"BC4U", b"BC4S"})

#: FourCCs that mean "two channels, stored as a pair of those blocks".
_TWO_CHANNEL: Final[frozenset[bytes]] = frozenset({b"ATI2", b"BC5U", b"BC5S"})

#: DXGI format numbers mapped onto the older code that means the same layout.
#: Each block format has typeless, unorm and sRGB spellings that differ only in
#: how a GPU samples them, not in how the bits decode.
_DXGI_TO_FOURCC: Final[dict[int, bytes]] = {
    70: b"DXT1", 71: b"DXT1", 72: b"DXT1",
    73: b"DXT3", 74: b"DXT3", 75: b"DXT3",
    76: b"DXT5", 77: b"DXT5", 78: b"DXT5",
    79: b"ATI1", 80: b"ATI1", 81: b"ATI1",
    82: b"ATI2", 83: b"ATI2", 84: b"ATI2",
    97: _DX10, 98: _DX10, 99: _DX10,
}  # fmt: skip

#: DXGI numbers this decoder deliberately refuses, with why. Saying "BC6H is
#: HDR and this game has no use for it" is a better answer than "unsupported
#: format 95", because it tells the user not to wait for it.
_DXGI_REFUSED: Final[dict[int, str]] = {
    95: "BC6H is an HDR format with no use in this game",
    96: "BC6H is an HDR format with no use in this game",
}

#: A guard on dimensions read from the file, so a corrupt header cannot ask for
#: a multi-gigabyte allocation.
_MAX_DIMENSION: Final[int] = 1 << 15

#: A guard on the *total* pixel count, which is the one that matters. Bounding
#: each dimension alone is not enough: 32768 x 32768 passes that check and then
#: asks for 4.3 GB of RGBA before a single byte of texture data is read. The
#: largest texture in any Morrowind setup is a few thousand pixels square, so
#: 64 megapixels is far above anything real and far below anything dangerous.
_MAX_PIXELS: Final[int] = 64 << 20


class DdsError(ImageError):
    """Raised when a DDS texture cannot be decoded.

    Kept as its own type, rather than folded into :class:`ImageError`, so that
    a caller walking a folder can tell "this DDS is broken" from "this is not
    a DDS at all".
    """


def _expand_565(value: int) -> tuple[int, int, int]:
    """Expand a 16-bit 5:6:5 color to 8 bits per channel.

    The low bits are replicated from the high ones rather than zero-filled, so
    that full-scale input maps to full-scale output. Shifting alone would make
    white decode as 248, 252, 248 and give every decoded texture a faint cast.

    Args:
        value: The packed color.

    Returns:
        Red, green and blue, each 0-255.
    """
    r = (value >> 11) & 0x1F
    g = (value >> 5) & 0x3F
    b = value & 0x1F
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _color_table(c0: int, c1: int, *, punchthrough: bool) -> list[bytes]:
    """Build the four-entry color table for one block.

    Args:
        c0: First endpoint, packed 5:6:5.
        c1: Second endpoint, packed 5:6:5.
        punchthrough: Whether the three-color mode is available. DXT1 selects
            it by endpoint order; DXT3 and DXT5 never use it, because their
            alpha lives in its own block.

    Returns:
        Four RGBA entries, each already packed as four bytes so the decode loop
        can assign them into the output without rebuilding them per pixel.
    """
    r0, g0, b0 = _expand_565(c0)
    r1, g1, b1 = _expand_565(c1)
    table = [bytes((r0, g0, b0, 255)), bytes((r1, g1, b1, 255))]
    if punchthrough and c0 <= c1:
        # Three colors and a transparent slot: the midpoint, then a hole.
        table.append(bytes(((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255)))
        table.append(b"\x00\x00\x00\x00")
    else:
        table.append(bytes(((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255)))
        table.append(bytes(((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255)))
    return table


def _alpha_table(a0: int, a1: int) -> list[int]:
    """Build the eight-entry interpolation table of a DXT5-style block.

    The same block layout carries alpha in DXT5, the single channel of BC4 and
    each of the two channels of BC5, which is why this is shared rather than
    named for alpha alone.

    Args:
        a0: First endpoint.
        a1: Second endpoint.

    Returns:
        Eight values, 0-255.
    """
    if a0 > a1:
        return [a0, a1, *[((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]]
    # Six interpolated values plus explicit minimum and maximum slots.
    return [a0, a1, *[((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)], 0, 255]


def _decode_blocks(data: bytes, width: int, height: int, fourcc: bytes) -> bytearray:
    """Decode a whole DXT-family surface.

    Args:
        data: The surface bytes.
        width: Surface width.
        height: Surface height.
        fourcc: The compression tag.

    Returns:
        RGBA pixels.

    Raises:
        DdsError: If the data runs out mid-surface.
    """
    stride = _BLOCK_BYTES[fourcc]
    has_alpha_block = stride == 16
    explicit_alpha = fourcc in (b"DXT2", b"DXT3")
    out = bytearray(width * height * 4)
    offset = 0
    for block_y in range(0, height, 4):
        for block_x in range(0, width, 4):
            if offset + stride > len(data):
                raise DdsError(
                    f"texture data ends early: wanted {stride} byte(s) at {offset}, "
                    f"file holds {len(data) - offset}"
                )
            block = data[offset : offset + stride]
            offset += stride
            alphas: list[int] | None = None
            if has_alpha_block:
                alphas = (
                    _explicit_alphas(block[:8])
                    if explicit_alpha
                    else _interpolated_alphas(block[:8])
                )
                block = block[8:]
            c0, c1, bits = struct.unpack_from("<HHI", block, 0)
            table = _color_table(c0, c1, punchthrough=not has_alpha_block)
            # Whole rows of four pixels are written in one slice assignment.
            # Per-pixel assignment decoded the corpus in 23 seconds; a 2048px
            # texture is a quarter of a million blocks, and the loop overhead
            # rather than the arithmetic was the cost.
            wide = block_x + 4 <= width
            for row in range(4):
                y = block_y + row
                if y >= height:
                    break
                shift = 8 * row
                quad = (bits >> shift) & 0xFF
                indices = (quad & 0x3, (quad >> 2) & 0x3, (quad >> 4) & 0x3, (quad >> 6) & 0x3)
                start = (y * width + block_x) * 4
                if alphas is None:
                    if wide:
                        out[start : start + 16] = b"".join(table[i] for i in indices)
                        continue
                    for col in range(min(4, width - block_x)):
                        out[start + col * 4 : start + col * 4 + 4] = table[indices[col]]
                    continue
                base = 4 * row
                for col in range(4 if wide else max(0, width - block_x)):
                    at = start + col * 4
                    out[at : at + 4] = table[indices[col]]
                    out[at + 3] = alphas[base + col]
    return out


def _decode_one_channel(data: bytes, width: int, height: int) -> bytearray:
    """Decode a BC4 surface: one channel, shown as grey.

    BC4 carries a single value per pixel -- a height field, a gloss mask, an
    ambient-occlusion map. There is no color in it to recover, so it is
    expanded to grey rather than dropped into the red channel alone, because
    grey is what the map means and what every tool that shows one displays.

    Args:
        data: The surface bytes.
        width: Surface width.
        height: Surface height.

    Returns:
        RGBA pixels, opaque.

    Raises:
        DdsError: If the data runs out mid-surface.
    """
    out = bytearray(width * height * 4)
    offset = 0
    for block_y in range(0, height, 4):
        for block_x in range(0, width, 4):
            if offset + 8 > len(data):
                raise DdsError(f"BC4 data ends early at offset {offset}")
            values = _interpolated_alphas(data[offset : offset + 8])
            offset += 8
            for row in range(min(4, height - block_y)):
                start = ((block_y + row) * width + block_x) * 4
                for col in range(min(4, width - block_x)):
                    level = values[row * 4 + col]
                    at = start + col * 4
                    out[at : at + 4] = bytes((level, level, level, 255))
    return out


def _decode_two_channel(data: bytes, width: int, height: int) -> bytearray:
    """Decode a BC5 surface: two channels, with the third reconstructed.

    BC5 is how a tangent-space normal map is stored. Only X and Y are kept,
    because a unit vector's Z follows from them and storing it would waste a
    third of the file. The reconstruction is that identity::

        z = sqrt(1 - x^2 - y^2)

    with each channel mapped from the stored 0-255 onto -1 to 1 and back.

    Reconstructing rather than leaving blue flat matters for what this tool is
    for. Two mods' normal maps get compared against *each other*, so both sides
    are normal maps and the comparison is meaningful -- but only if Z is
    present, since a decode that discarded it would call two genuinely
    different maps identical whenever they happened to share X and Y.

    The **DirectX** convention is used, matching both Morrowind and OpenMW:
    green is passed through untouched. Tooling written for the OpenGL
    convention flips it on load, and doing that here would report a texture as
    differing from a byte-identical copy of itself.

    Args:
        data: The surface bytes.
        width: Surface width.
        height: Surface height.

    Returns:
        RGBA pixels, opaque.

    Raises:
        DdsError: If the data runs out mid-surface.
    """
    out = bytearray(width * height * 4)
    offset = 0
    # Normal maps repeat values heavily -- a flat area is one normal over
    # thousands of pixels -- so the square root is worth caching. Uncached this
    # is the slowest decode here; cached it is comparable to BC3.
    blue_of: dict[int, int] = {}
    for block_y in range(0, height, 4):
        for block_x in range(0, width, 4):
            if offset + 16 > len(data):
                raise DdsError(f"BC5 data ends early at offset {offset}")
            reds = _interpolated_alphas(data[offset : offset + 8])
            greens = _interpolated_alphas(data[offset + 8 : offset + 16])
            offset += 16
            for row in range(min(4, height - block_y)):
                start = ((block_y + row) * width + block_x) * 4
                for col in range(min(4, width - block_x)):
                    index = row * 4 + col
                    red, green = reds[index], greens[index]
                    key = (red << 8) | green
                    blue = blue_of.get(key)
                    if blue is None:
                        x = red / 127.5 - 1.0
                        y = green / 127.5 - 1.0
                        z = math.sqrt(max(0.0, 1.0 - x * x - y * y))
                        blue = min(255, int((z + 1.0) * 127.5))
                        blue_of[key] = blue
                    at = start + col * 4
                    out[at : at + 4] = bytes((red, green, blue, 255))
    return out


def _explicit_alphas(block: bytes) -> list[int]:
    """Read a DXT3 alpha block: sixteen 4-bit values.

    Args:
        block: The eight alpha bytes.

    Returns:
        Sixteen alpha values, 0-255.
    """
    values: list[int] = []
    for byte in block:
        low, high = byte & 0xF, byte >> 4
        # Replicated, not shifted, for the same reason as the color channels:
        # 0xF must reach 255 or every decoded texture is slightly transparent.
        values.append(low * 17)
        values.append(high * 17)
    return values


def _interpolated_alphas(block: bytes) -> list[int]:
    """Read a DXT5-style block: two endpoints and sixteen 3-bit indices.

    Args:
        block: The eight bytes.

    Returns:
        Sixteen values, 0-255.
    """
    table = _alpha_table(block[0], block[1])
    packed = int.from_bytes(block[2:8], "little")
    return [table[(packed >> (3 * i)) & 0x7] for i in range(16)]


def _decode_uncompressed(
    data: bytes, width: int, height: int, bit_count: int, masks: tuple[int, int, int, int]
) -> bytearray:
    """Decode an uncompressed surface described by channel masks.

    Args:
        data: The surface bytes.
        width: Surface width.
        height: Surface height.
        bit_count: Bits per pixel; 8, 16, 24 and 32 are supported.
        masks: Red, green, blue and alpha bit masks.

    Returns:
        RGBA pixels.

    Raises:
        DdsError: If the depth is unsupported or the data is short.
    """
    if bit_count not in (8, 16, 24, 32):
        raise DdsError(f"unsupported uncompressed depth: {bit_count} bits per pixel")
    step = bit_count // 8
    needed = width * height * step
    if len(data) < needed:
        raise DdsError(f"texture data ends early: wanted {needed} byte(s), got {len(data)}")
    shifts = [(_lowest_bit(mask), mask) for mask in masks]
    out = bytearray(width * height * 4)
    for index in range(width * height):
        raw = int.from_bytes(data[index * step : index * step + step], "little")
        channels = []
        for position, (shift, mask) in enumerate(shifts):
            if not mask:
                # No alpha mask means an opaque surface, not a transparent one.
                channels.append(255 if position == 3 else 0)
                continue
            channels.append(_scale_to_byte((raw & mask) >> shift, mask >> shift))
        out[index * 4 : index * 4 + 4] = bytes(channels)
    return out


def _lowest_bit(mask: int) -> int:
    """Position of a mask's lowest set bit.

    Args:
        mask: The channel mask.

    Returns:
        The shift, or ``0`` for an empty mask.
    """
    return (mask & -mask).bit_length() - 1 if mask else 0


def _scale_to_byte(value: int, maximum: int) -> int:
    """Scale a channel of arbitrary width to 0-255.

    Args:
        value: The channel value.
        maximum: The largest it can be.

    Returns:
        The scaled value.
    """
    return 255 if maximum <= 0 else (value * 255) // maximum


def _resolve_dx10(data: bytes) -> tuple[bytes, int]:
    """Read the Direct3D 10 extension header and say what it means.

    Args:
        data: The whole file.

    Returns:
        The equivalent FourCC -- or :data:`_DX10` itself for BC7, which has no
        older spelling -- and the offset at which the surface begins.

    Raises:
        DdsError: If the header is missing or names a format not handled.
    """
    start = 4 + _HEADER_SIZE
    if len(data) < start + _DX10_SIZE:
        raise DdsError("file claims a DX10 extension header and then ends")
    dxgi = struct.unpack_from("<I", data, start)[0]
    if dxgi in _DXGI_REFUSED:
        raise DdsError(f"unsupported DXGI format {dxgi}: {_DXGI_REFUSED[dxgi]}")
    fourcc = _DXGI_TO_FOURCC.get(dxgi)
    if fourcc is None:
        raise DdsError(f"unsupported DXGI format {dxgi}")
    return fourcc, start + _DX10_SIZE


def read_dds(data: bytes) -> Image:
    """Decode a DDS texture.

    Args:
        data: The whole file.

    Returns:
        The top-level surface. Mipmaps are ignored: a conflict is judged on the
        image itself, and decoding chains nobody looks at wastes the time this
        module exists to save.

    Raises:
        DdsError: If the file is not a DDS, is truncated, or uses a format this
            decoder does not handle. Never a :class:`struct.error`: these files
            arrive from mod archives and must fail as a finding.
    """
    if len(data) < 4 + _HEADER_SIZE or not data.startswith(MAGIC):
        raise DdsError("not a DDS file: missing the 'DDS ' magic")
    try:
        size, _flags, height, width = struct.unpack_from("<IIII", data, 4)
        if size != _HEADER_SIZE:
            raise DdsError(f"DDS header claims {size} bytes, expected {_HEADER_SIZE}")
        if not 0 < width <= _MAX_DIMENSION or not 0 < height <= _MAX_DIMENSION:
            raise DdsError(f"implausible dimensions {width}x{height}")
        if width * height > _MAX_PIXELS:
            raise DdsError(f"implausible size: {width}x{height} is {width * height} pixel(s)")
        pf_flags, fourcc, bit_count, r_mask, g_mask, b_mask, a_mask = struct.unpack_from(
            "<I4sIIIII", data, 4 + 76
        )
        start = 4 + _HEADER_SIZE
        if pf_flags & _DDPF_FOURCC and fourcc == _DX10:
            fourcc, start = _resolve_dx10(data)
        surface = data[start:]

        if pf_flags & _DDPF_FOURCC:
            pixels, label = _decode_compressed(surface, width, height, fourcc)
        elif pf_flags & (_DDPF_RGB | _DDPF_ALPHAPIXELS | _DDPF_LUMINANCE):
            if pf_flags & _DDPF_LUMINANCE and not r_mask:
                # A luminance surface with no explicit mask is grey at full
                # depth. Treating it as an empty red channel would decode the
                # whole image to black without failing.
                r_mask = g_mask = b_mask = (1 << bit_count) - 1
            pixels = _decode_uncompressed(
                surface, width, height, bit_count, (r_mask, g_mask, b_mask, a_mask)
            )
            label = f"{bit_count}-bit uncompressed"
        else:
            raise DdsError(f"unsupported DDS pixel format flags {pf_flags:#010x}")
    except struct.error as exc:  # pragma: no cover - the length guard above means every
        # unpack_from here has the bytes it needs; kept as a defensive net (see the
        # docstring: these mod-archive files must fail as a finding, never a struct.error).
        raise DdsError(f"DDS header is truncated: {exc}") from exc
    LOG.debug("decoded %dx%d %s", width, height, label)
    return Image(width, height, bytes(pixels))


def _decode_compressed(
    surface: bytes, width: int, height: int, fourcc: bytes
) -> tuple[bytearray, str]:
    """Dispatch a block-compressed surface to the decoder for its format.

    Args:
        surface: The surface bytes.
        width: Surface width.
        height: Surface height.
        fourcc: The compression tag, already resolved through any DX10 header.

    Returns:
        The RGBA pixels and a name for the log line.

    Raises:
        DdsError: If the format is not one this decoder handles.
    """
    if fourcc == _DX10:
        return bc7.decode_surface(surface, width, height), "BC7"
    if fourcc in _TWO_CHANNEL:
        return _decode_two_channel(surface, width, height), "BC5"
    if fourcc in _ONE_CHANNEL:
        return _decode_one_channel(surface, width, height), "BC4"
    if fourcc in _BLOCK_BYTES:
        return _decode_blocks(surface, width, height, fourcc), fourcc.decode("ascii", "replace")
    readable = fourcc.decode("ascii", "replace").strip("\x00")
    raise DdsError(f"unsupported texture compression {readable!r}")
