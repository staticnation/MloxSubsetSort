"""Tests for ``tools/check_images.py``, the decoder cross-check against Pillow.

The unit tests prove the decoders behave as this project expects; they cannot
prove the expectation is right, because test and code were written from the same
reading of the same spec. This tool guards that blind spot by comparing against
Pillow's separate implementation. Here the happy paths run for real against
Pillow (present in the sandbox), while the branches that only fire when a decoder
or the oracle misbehaves are driven with fakes -- a fake Pillow that refuses, and
a fake ``read_dds`` that returns wrong pixels -- so a regression in the *tool's*
own reporting is caught too.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import check_images
from tools.check_images import (
    check_block_formats,
    check_corpus,
    check_normal_reconstruction,
    check_uncompressed,
    compare,
    fourcc_dds,
    main,
)

pytest.importorskip("PIL")
from PIL import Image as PilImage


class _FakeImage:
    """Stand-in for a decoded image with a fixed pixel and RGBA buffer."""

    def __init__(self, pixel: tuple[int, int, int, int], count: int = 64) -> None:
        self._pixel = pixel
        self.pixels = bytes(pixel) * count

    def pixel(self, _x: int, _y: int) -> tuple[int, int, int, int]:
        return self._pixel


class TestFourccDds:
    def test_it_wraps_blocks_in_a_dds_container(self) -> None:
        out = fourcc_dds(b"DXT1", b"\x00" * 8, 4, 4)
        assert out.startswith(b"DDS ")
        assert b"DXT1" in out
        assert out.endswith(b"\x00" * 8)

    def test_the_header_carries_the_dimensions(self) -> None:
        out = fourcc_dds(b"DXT5", b"\x00" * 16, width=8, height=4)
        height, width = struct.unpack_from("<II", out, 4 + 8)
        assert (width, height) == (8, 4)


class TestCompare:
    def test_identical_buffers_do_not_differ(self) -> None:
        buf = bytes([10, 20, 30, 40] * 4)
        assert compare(buf, buf, "RGBA", "x") == 0

    def test_a_difference_is_counted_and_reported(self, capsys) -> None:
        ours = bytes([10, 20, 30, 40])
        theirs = bytes([10, 25, 30, 40])  # green differs by 5
        assert compare(ours, theirs, "RGBA", "green-test") == 1
        assert "worst channel gap 5" in capsys.readouterr().out

    def test_only_the_named_channels_are_compared(self) -> None:
        """A gap in an unlisted channel is ignored, which is the BC5 blue case."""
        ours = bytes([10, 20, 30, 40])
        theirs = bytes([10, 20, 99, 40])  # only blue differs
        assert compare(ours, theirs, "RG", "rg-only") == 0  # blue not in the mask


class TestCheckBlockFormats:
    def test_a_real_run_agrees_with_pillow(self, capsys) -> None:
        assert check_block_formats(None, PilImage) == 0
        assert "match on" in capsys.readouterr().out

    def test_an_oracle_that_refuses_counts_every_format_as_a_failure(self, capsys) -> None:
        class _RefusingPil:
            @staticmethod
            def open(_buf: object) -> object:
                raise ValueError("nope")

        failures = check_block_formats(None, _RefusingPil)
        assert failures == len(check_images.BLOCK_FORMATS)
        assert "oracle refused" in capsys.readouterr().out

    def test_a_decoder_mismatch_is_counted(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(
            check_images, "read_dds", lambda _data: _FakeImage((0, 0, 0, 0), count=4 * 512)
        )
        assert check_block_formats(None, PilImage) >= 1
        assert "differ" in capsys.readouterr().out


class TestNormalReconstruction:
    def test_a_real_run_reconstructs_flat_and_unit_normals(self, capsys) -> None:
        assert check_normal_reconstruction() == 0
        assert "flat normal decodes to" in capsys.readouterr().out

    def test_a_wrong_flat_normal_is_flagged(self, capsys, monkeypatch) -> None:
        # First read_dds (the flat block) returns a bad centre pixel; the second
        # (the random 8x8) returns clamped pixels with the right blue, so only
        # the flat check fails.
        calls = iter([_FakeImage((200, 200, 10, 255)), _FakeImage((255, 255, 127, 255))])
        monkeypatch.setattr(check_images, "read_dds", lambda _data: next(calls))
        assert check_normal_reconstruction() == 1
        assert "expected ~(128, 128, 255)" in capsys.readouterr().out

    def test_an_unrepresentable_normal_with_wrong_blue_is_flagged(
        self, capsys, monkeypatch
    ) -> None:
        # Flat block fine; random block has r=g=255 (x^2+y^2>1 -> clamped) but
        # blue 0 rather than ~127, which is the wrong-clamp failure.
        calls = iter([_FakeImage((128, 128, 254, 255)), _FakeImage((255, 255, 0, 255))])
        monkeypatch.setattr(check_images, "read_dds", lambda _data: next(calls))
        assert check_normal_reconstruction() >= 1  # one failure per clamped pixel
        assert "unrepresentable normal gave blue" in capsys.readouterr().out

    def test_a_non_unit_normal_is_flagged(self, capsys, monkeypatch) -> None:
        # Flat block fine; random block has r=g=128 (nx=ny=0) and blue 200, so
        # the vector is far from unit length: the reconstruction-error failure.
        calls = iter([_FakeImage((128, 128, 254, 255)), _FakeImage((128, 128, 200, 255))])
        monkeypatch.setattr(check_images, "read_dds", lambda _data: next(calls))
        assert check_normal_reconstruction() == 1
        assert "not unit vectors" in capsys.readouterr().out


class TestCheckUncompressed:
    def test_a_real_run_matches_pillow(self, capsys) -> None:
        assert check_uncompressed(PilImage) == 0
        assert "matches" in capsys.readouterr().out

    def test_a_variant_the_oracle_cannot_write_is_skipped(self, capsys) -> None:
        class _Pil:
            @staticmethod
            def frombytes(mode: str, size: tuple[int, int], data: bytes) -> object:
                return _Unwritable()

        class _Unwritable:
            def convert(self, _mode: str) -> _Unwritable:
                return self

            def save(self, _buf: object, _fmt: str, **_k: object) -> None:
                raise OSError("cannot write")

        assert check_uncompressed(_Pil) == 0  # all variants skipped, none failed
        assert "oracle cannot write it" in capsys.readouterr().out

    def test_a_variant_we_cannot_decode_is_a_failure(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(
            check_images,
            "read_image",
            lambda _data: (_ for _ in ()).throw(check_images.ImageError("no")),
        )
        assert check_uncompressed(PilImage) >= 1
        assert "could not decode it" in capsys.readouterr().out

    def test_a_variant_that_decodes_but_mismatches_is_a_failure(self, capsys, monkeypatch) -> None:
        """A variant that decodes cleanly but disagrees with Pillow is counted."""
        monkeypatch.setattr(
            check_images, "read_image", lambda _data: _FakeImage((0, 0, 0, 0), count=23 * 17)
        )
        assert check_uncompressed(PilImage) >= 1
        assert "differ" in capsys.readouterr().out


class TestCheckCorpus:
    def _write_tga(self, path: Path) -> None:
        img = PilImage.frombytes("RGBA", (4, 4), bytes(range(4 * 4 * 4)))
        img.save(path, "TGA")

    def test_an_empty_folder_is_reported(self, tmp_path: Path, capsys) -> None:
        assert check_corpus(tmp_path, PilImage) == 0
        assert "no textures under" in capsys.readouterr().out

    def test_a_real_file_matches(self, tmp_path: Path, capsys) -> None:
        self._write_tga(tmp_path / "a.tga")
        assert check_corpus(tmp_path, PilImage) == 0
        assert "matched" in capsys.readouterr().out

    def test_a_file_the_oracle_cannot_open_is_skipped(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "junk.dds").write_bytes(b"not a real dds file at all")
        assert check_corpus(tmp_path, PilImage) == 0  # skipped, not failed
        assert "the oracle skipped" in capsys.readouterr().out

    def test_a_file_we_cannot_decode_is_a_failure(self, tmp_path: Path, capsys, monkeypatch) -> None:
        self._write_tga(tmp_path / "a.tga")
        monkeypatch.setattr(
            check_images,
            "read_image",
            lambda _data: (_ for _ in ()).throw(check_images.ImageError("no")),
        )
        assert check_corpus(tmp_path, PilImage) >= 1
        assert "could not decode it" in capsys.readouterr().out

    def test_a_mismatch_against_the_oracle_is_counted(self, tmp_path: Path, capsys, monkeypatch) -> None:
        self._write_tga(tmp_path / "a.tga")
        monkeypatch.setattr(check_images, "read_image", lambda _data: _FakeImage((0, 0, 0, 0)))
        assert check_corpus(tmp_path, PilImage) >= 1
        assert "differed" in capsys.readouterr().out


class TestMain:
    def test_without_pillow_it_exits_two(self, capsys, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "PIL", None)
        assert main([]) == 2
        assert "Pillow is not installed" in capsys.readouterr().err

    def test_a_full_run_agrees_with_the_oracle(self, capsys) -> None:
        assert main([]) == 0
        assert "Every decoder agrees" in capsys.readouterr().out

    def test_a_run_with_a_corpus_folder_includes_it(self, tmp_path: Path, capsys) -> None:
        img = PilImage.frombytes("RGBA", (4, 4), bytes(range(4 * 4 * 4)))
        img.save(tmp_path / "a.tga", "TGA")
        assert main(["--corpus", str(tmp_path)]) == 0
        assert "texture(s)" in capsys.readouterr().out

    def test_a_run_where_a_decoder_disagrees_exits_one(self, capsys, monkeypatch) -> None:
        """A block decoder that disagrees with Pillow makes the whole run fail."""
        monkeypatch.setattr(
            check_images, "read_dds", lambda _data: _FakeImage((0, 0, 0, 0), count=4 * 512)
        )
        assert main([]) == 1
        assert "did not match an independent decoder" in capsys.readouterr().out
