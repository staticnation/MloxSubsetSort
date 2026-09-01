"""Edge branches of the image comparator that the round-trip tests miss.

The happy paths live in ``test_image_compare``; this pins the decode-failure
returns, the too-large guard, the alpha-folding corner of the difference image,
and the small helpers.
"""

from __future__ import annotations

from wraithguard.images import compare as compare_mod
from wraithguard.images.compare import (
    Comparison,
    Verdict,
    compare_bytes,
    compare_images,
    difference_image,
    digest,
)
from wraithguard.images.image import Image, ImageError


def _solid(width: int, height: int, color: tuple[int, int, int, int]) -> Image:
    return Image(width, height, bytes(color) * (width * height))


class TestDecodeFailuresAreAVerdict:
    """A file that will not decode is reported, not raised through."""

    def test_the_first_being_undecodable_is_reported(self, monkeypatch) -> None:
        """When the left file cannot be read, the verdict names it."""

        def refuse(_data: bytes) -> Image:
            raise ImageError("nope")

        monkeypatch.setattr(compare_mod, "read_image", refuse)
        outcome = compare_bytes(b"aaaa", b"bbbb")
        assert outcome.verdict is Verdict.UNDECODABLE
        assert "first" in outcome.detail

    def test_the_second_being_undecodable_is_reported(self, monkeypatch) -> None:
        """When only the right file fails, the left's size is still carried."""
        good = _solid(2, 2, (1, 2, 3, 255))
        calls = {"n": 0}

        def once(_data: bytes) -> Image:
            calls["n"] += 1
            if calls["n"] == 1:
                return good
            raise ImageError("nope")

        monkeypatch.setattr(compare_mod, "read_image", once)
        outcome = compare_bytes(b"aaaa", b"bbbb")
        assert outcome.verdict is Verdict.UNDECODABLE
        assert "second" in outcome.detail
        assert outcome.left_size == (2, 2)

    def test_two_decodable_files_flow_into_the_pixel_comparison(self, monkeypatch) -> None:
        """When both decode, the byte entry point hands off to the pixel path."""
        left = _solid(2, 2, (0, 0, 0, 255))
        right = _solid(2, 2, (9, 9, 9, 255))
        monkeypatch.setattr(compare_mod, "read_image", lambda data: left if data == b"L" else right)
        outcome = compare_bytes(b"L", b"R")
        assert outcome.verdict is Verdict.DIFFERENT


def test_an_image_beyond_the_pixel_budget_is_not_walked(monkeypatch) -> None:
    """Past the cap, the comparator reports the size rather than scanning it."""
    monkeypatch.setattr(compare_mod, "_MAX_COMPARE", 1)
    big = _solid(2, 2, (0, 0, 0, 255))
    outcome = compare_images(big, _solid(2, 2, (1, 1, 1, 255)))
    assert outcome.verdict is Verdict.DIFFERENT
    assert "too large" in outcome.detail


def test_an_alpha_change_folds_into_the_visible_channels() -> None:
    """A small alpha gap under a large colour gap exercises both fold branches."""
    left = _solid(1, 1, (200, 0, 0, 250))
    right = _solid(1, 1, (0, 0, 0, 255))
    out = difference_image(left, right)
    # Red saturated by the colour gap; green raised by the folded alpha gap.
    assert out.pixels[0] == 255
    assert out.pixels[1] == 20
    assert out.pixels[3] == 255


def test_worth_showing_tracks_whether_the_pair_differs() -> None:
    """The convenience flag mirrors ``differs`` for the viewer's benefit."""
    same = Comparison(Verdict.IDENTICAL, "same")
    differ = Comparison(Verdict.DIFFERENT, "changed")
    assert same.worth_showing is False
    assert differ.worth_showing is True


def test_digest_is_a_short_stable_hex() -> None:
    """The digest is the first 16 hex characters of the SHA-256."""
    import hashlib

    assert digest(b"payload") == hashlib.sha256(b"payload").hexdigest()[:16]
