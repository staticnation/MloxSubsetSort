"""Structural coverage of the BC7 decoder's per-mode branches.

Pixel correctness across the mode and partition tables is established by
``tools/check_bc7.py`` against an independent decoder; these tests only walk the
code paths -- each mode's parity style, subset count, the mode-4 index selector
and rotation, and the size guards -- with minimal blocks whose mode bit selects
the path. The assertions check that a full 16-pixel block comes back, not its
exact colours.
"""

from __future__ import annotations

import pytest

from wraithguard.images.bc7 import (
    BLOCK_BYTES,
    _anchors,
    _unquantise,
    decode_block,
    decode_surface,
)
from wraithguard.images.image import ImageError


def _block(low_byte: int) -> bytes:
    """A 16-byte BC7 block whose only set bits are in the low byte."""
    return bytes([low_byte]) + bytes(BLOCK_BYTES - 1)


def test_unquantise_replicates_bits_below_a_full_byte() -> None:
    """A sub-8-bit value is bit-replicated up to a full byte."""
    assert _unquantise(0xFF, 8) == 0xFF  # already 8 bits, returned as-is
    assert _unquantise(0b11111, 5) == 0xFF  # 5 ones replicate to all ones
    assert _unquantise(0, 5) == 0


def test_anchors_for_two_and_three_subsets() -> None:
    """The anchor helper returns one index per subset."""
    assert _anchors(1, 0) == (0,)
    assert len(_anchors(2, 0)) == 2
    assert len(_anchors(3, 0)) == 3


class TestModePaths:
    """Each mode's decode path, selected by the block's mode bit."""

    def test_mode_0_three_subsets_and_endpoint_parity(self) -> None:
        """Mode 0 has three subsets, per-endpoint P-bits, and no alpha plane."""
        assert len(decode_block(_block(0x01))) == 16 * 4

    def test_mode_1_two_subsets_and_shared_parity(self) -> None:
        """Mode 1 has two subsets and one shared P-bit per pair."""
        assert len(decode_block(_block(0x02))) == 16 * 4

    def test_mode_4_selector_zero_keeps_the_natural_index_order(self) -> None:
        """Mode 4 with the index selector clear uses the first set for colour."""
        assert len(decode_block(_block(0x10))) == 16 * 4

    def test_mode_4_selector_set_swaps_the_index_sets(self) -> None:
        """With the selector bit set, the two index sets swap roles."""
        # bit4 = mode 4; bit7 = the selector, read after the two rotation bits.
        assert len(decode_block(_block(0x90))) == 16 * 4

    def test_mode_4_rotation_swaps_a_colour_channel_with_alpha(self) -> None:
        """A non-zero rotation swaps one colour channel with the alpha channel."""
        # bit4 = mode 4; bit5 = the low rotation bit -> rotation 1 (red<->alpha).
        assert len(decode_block(_block(0x30))) == 16 * 4

    def test_mode_4_rotation_two_and_three(self) -> None:
        """Rotations 2 and 3 swap green and blue with alpha respectively."""
        assert len(decode_block(_block(0x50))) == 16 * 4  # bit6 set -> rotation 2
        assert len(decode_block(_block(0x70))) == 16 * 4  # bits 5,6 set -> rotation 3

    def test_modes_with_an_alpha_plane_read_it(self) -> None:
        """Modes 5, 6 and 7 carry an alpha plane that is actually sampled."""
        for mode_bit in (0x20, 0x40, 0x80):  # modes 5, 6, 7
            assert len(decode_block(_block(mode_bit))) == 16 * 4

    def test_the_all_zero_reserved_mode_is_a_transparent_hole(self) -> None:
        """Eight zero mode bits are reserved and decode to transparent black."""
        pixels = decode_block(bytes(BLOCK_BYTES))
        assert len(pixels) == 16 * 4
        assert pixels[:4] == bytes([0, 0, 0, 0])


def test_a_block_of_the_wrong_size_is_refused() -> None:
    """Only a full 16-byte block can be decoded."""
    with pytest.raises(ImageError, match="BC7 block"):
        decode_block(b"\x01\x02\x03")


def test_a_surface_that_ends_early_is_refused() -> None:
    """A surface too short for its declared dimensions is a truncation."""
    with pytest.raises(ImageError, match="ends early"):
        decode_surface(bytes(BLOCK_BYTES // 2), 4, 4)  # half a block for a 4x4


def test_a_full_surface_decodes_every_pixel() -> None:
    """A complete 4x4 (one-block) surface fills all sixteen RGBA pixels."""
    surface = decode_surface(_block(0x01), 4, 4)
    assert len(surface) == 4 * 4 * 4
