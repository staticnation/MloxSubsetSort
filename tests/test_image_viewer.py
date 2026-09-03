"""The HTML-building helpers behind the texture comparison page.

These pin the small branches the round-trip page tests do not reach: the
three.js fallback when the library is absent, the empty library block, the
per-verdict "why is there no difference image" text, and the role line.
"""

from __future__ import annotations

from wraithguard.images.compare import Comparison, Verdict
from wraithguard.images.roles import TextureRole
from wraithguard.images.viewer import (
    _inline_library,
    _library_block,
    _verdict_line,
    _why_no_difference,
)


def test_the_library_falls_back_to_empty_when_three_js_is_absent(monkeypatch) -> None:
    """A missing three.js build yields no script, not a crash."""
    from wraithguard.images import viewer
    from wraithguard.viz.library import ViewerError

    def missing() -> str:
        raise ViewerError("not shipped")

    monkeypatch.setattr(viewer, "three_source", missing)
    assert _inline_library() == ""


def test_an_empty_library_block_is_blank() -> None:
    """With neither a URL nor inline source there is nothing to embed."""
    assert _library_block("", "") == ""


class TestWhyNoDifference:
    """The explanation shown in place of an absent difference image."""

    def _cmp(self, verdict: Verdict, detail: str = "because") -> Comparison:
        return Comparison(verdict, detail)

    def test_a_not_comparable_pair_shows_its_detail(self) -> None:
        assert (
            _why_no_difference(self._cmp(Verdict.NOT_COMPARABLE, "roles differ")) == "roles differ"
        )

    def test_identical_images_say_the_difference_is_blank(self) -> None:
        out = _why_no_difference(self._cmp(Verdict.IDENTICAL))
        assert "identical" in out

    def test_an_undecodable_pair_shows_its_detail(self) -> None:
        assert (
            _why_no_difference(self._cmp(Verdict.UNDECODABLE, "cannot decode")) == "cannot decode"
        )


def test_the_verdict_line_names_mismatched_roles() -> None:
    """When the two images are classified differently, the line says so."""
    outcome = Comparison(
        Verdict.NOT_COMPARABLE,
        "a normal map against a diffuse",
        left_role=TextureRole.NORMAL,
        right_role=TextureRole.DIFFUSE,
    )
    line = _verdict_line(outcome)
    assert "roles:" in line
    assert "normal" in line
