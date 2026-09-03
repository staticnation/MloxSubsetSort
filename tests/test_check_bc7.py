"""Tests for ``tools/check_bc7.py``, the BC7 decoder cross-check.

BC7 is six hundred hand-transcribed table numbers; a single wrong one yields a
correct-looking image with a few wrong blocks, invisible to a test written by
the same hand. The tool defends against that by forcing every mode and partition
to be used and comparing against Pillow's unrelated decoder. Here the block
builder and DDS wrapper are checked structurally, a short real run proves the
two decoders agree, and the failure branches (no Pillow, the oracle refusing, a
byte mismatch) are driven with fakes so a regression in the *tool* is caught too.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import check_bc7
from tools.check_bc7 import DXGI_BC7_UNORM, dx10_dds, main, make_block


class TestDx10Dds:
    def test_it_produces_a_dx10_dds_container(self) -> None:
        blocks = b"\x00" * 16
        out = dx10_dds(blocks, 4, 4)
        assert out.startswith(b"DDS ")
        assert b"DX10" in out
        assert out.endswith(blocks)

    def test_the_header_records_the_surface_dimensions_and_format(self) -> None:
        out = dx10_dds(b"\x11" * 16, width=8, height=4)
        # height and width live at offsets 8 and 12 of the 124-byte header,
        # which begins right after the 4-byte "DDS " magic.
        height, width = struct.unpack_from("<II", out, 4 + 8)
        assert (width, height) == (8, 4)
        dxgi = struct.unpack_from("<I", out, 4 + 124)[0]  # first word of the DX10 extension
        assert dxgi == DXGI_BC7_UNORM


class TestMakeBlock:
    def test_a_block_is_sixteen_bytes(self) -> None:
        import random

        rng = random.Random(0)  # noqa: S311 -- test data, not a secret
        assert len(make_block(rng, mode=4, partition=0)) == 16

    def test_the_mode_bits_select_the_mode(self) -> None:
        import random

        # Mode 4 has no partition; its bits are "0000 1" -> value low bits = 1<<4.
        rng = random.Random(1)  # noqa: S311 -- test data, not a secret
        block = make_block(rng, mode=4, partition=0)
        value = int.from_bytes(block, "little")
        assert value & 0b11111 == 1 << 4  # a run of four zeros then the mode-select one

    def test_the_partition_is_packed_above_the_mode_bit(self) -> None:
        import random

        # Mode 0 uses a 4-bit partition field, sitting just above the 1-bit mode.
        rng = random.Random(2)  # noqa: S311 -- test data, not a secret
        block = make_block(rng, mode=0, partition=5)
        value = int.from_bytes(block, "little")
        assert value & 0b1 == 1  # mode 0 select bit
        assert (value >> 1) & 0b1111 == 5  # the partition number


class TestMain:
    def test_a_short_real_run_agrees_with_pillow(self, capsys) -> None:
        """One block per mode/partition, checked byte-for-byte against Pillow."""
        pytest.importorskip("PIL")
        assert main(["--trials", "1"]) == 0
        out = capsys.readouterr().out
        assert "matches an independent decoder" in out

    def test_without_pillow_it_exits_two(self, capsys, monkeypatch) -> None:
        """The check needs an independent decoder; absent one, it bows out cleanly."""
        monkeypatch.setitem(sys.modules, "PIL", None)  # force the import to fail
        assert main(["--trials", "1"]) == 2
        assert "Pillow is not installed" in capsys.readouterr().err

    def test_a_decoder_mismatch_is_reported_and_fails(self, capsys, monkeypatch) -> None:
        """If our decoder disagrees with Pillow, the tool names the mode and fails."""
        pytest.importorskip("PIL")

        def wrong_surface(_blocks: bytes, width: int, height: int) -> bytes:
            return b"\x00" * (width * height * 4)  # deliberately not Pillow's output

        monkeypatch.setattr(check_bc7.bc7, "decode_surface", wrong_surface)
        assert main(["--trials", "1"]) == 1
        out = capsys.readouterr().out
        assert "MISMATCH" in out
        assert "mismatches by mode" in out

    def test_an_oracle_that_refuses_a_block_is_counted_as_a_failure(
        self, capsys, monkeypatch
    ) -> None:
        """When Pillow itself cannot decode a block, that is recorded, not ignored."""
        from PIL import Image as PilImage

        def refuse(*_a: object, **_k: object) -> object:
            raise ValueError("nope")

        monkeypatch.setattr(PilImage, "open", refuse)
        assert main(["--trials", "1"]) == 1
        assert "oracle refused" in capsys.readouterr().out
