"""The [VER]/[SIZE]/[DESC] predicate evaluators, which read real plugin files.

The differential suite drives these through whole rule files, but never with a
readable index pointing at files whose version/size/description are known, so the
match/negate/unreadable branches stay dark. These build tiny TES3 headers and
size-controlled files, index them, and exercise each comparison directly.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from wraithguard.plugins import PluginFileIndex
from wraithguard.rules.predicates import _eval_desc, _eval_size, _eval_ver
from wraithguard.versions import format_version

if TYPE_CHECKING:
    from pathlib import Path

#: The fixed ``HEDR`` block: version, file type, author, description, count.
_HEDR_SIZE = 300

#: Description offset within ``HEDR``: past the f32 version and u32 file type
#: (8 bytes) and the 32-byte author field.
_DESC_IN_HEDR = 40


def _tes3(tmp_path: Path, name: str, description: bytes) -> Path:
    """Write a minimal but valid TES3 plugin carrying the given description.

    A real ``TES3`` record with a proper ``HEDR`` subrecord -- the framing the
    in-process header reader parses -- rather than a description dropped at a
    raw file offset.
    """
    hedr = bytearray(_HEDR_SIZE)
    struct.pack_into("<f", hedr, 0, 1.3)  # format version
    struct.pack_into("<I", hedr, 4, 0)  # file type: esp
    hedr[_DESC_IN_HEDR : _DESC_IN_HEDR + len(description)] = description  # rest stays null
    body = b"HEDR" + struct.pack("<I", _HEDR_SIZE) + bytes(hedr)
    record = b"TES3" + struct.pack("<I", len(body)) + b"\x00" * 8 + body  # size, padding+flags
    path = tmp_path / name
    path.write_bytes(record)
    return path


class TestEvalVer:
    """[VER op version Plugin] against a header-stated version."""

    def test_equal_matches(self, tmp_path: Path) -> None:
        """An ``=`` comparison holds when the header version equals the rule's."""
        _tes3(tmp_path, "mod.esp", b"version 1.30")
        index = PluginFileIndex([tmp_path])
        assert _eval_ver("=", "1.30", "mod.esp", ["mod.esp"], index) is True

    def test_less_than_matches(self, tmp_path: Path) -> None:
        """A ``<`` comparison holds when the header version is lower."""
        _tes3(tmp_path, "mod.esp", b"version 1.30")
        index = PluginFileIndex([tmp_path])
        assert _eval_ver("<", "2.00", "mod.esp", ["mod.esp"], index) is True

    def test_greater_than_matches(self, tmp_path: Path) -> None:
        """A ``>`` comparison holds when the header version is higher."""
        _tes3(tmp_path, "mod.esp", b"version 1.30")
        index = PluginFileIndex([tmp_path])
        assert _eval_ver(">", "1.00", "mod.esp", ["mod.esp"], index) is True

    def test_an_unknowable_version_holds_only_for_equals(self, tmp_path: Path) -> None:
        """With no version anywhere, ``=`` is assumed to hold and others do not."""
        _tes3(tmp_path, "plain.esp", b"no version here")
        index = PluginFileIndex([tmp_path])
        assert _eval_ver("=", "1.0", "plain.esp", ["plain.esp"], index) is True
        assert _eval_ver("<", "1.0", "plain.esp", ["plain.esp"], index) is False

    def test_a_wrong_version_does_not_match(self, tmp_path: Path) -> None:
        """A known version that fails the comparison returns False."""
        _tes3(tmp_path, "mod.esp", b"version 1.30")
        index = PluginFileIndex([tmp_path])
        assert _eval_ver("=", "9.99", "mod.esp", ["mod.esp"], index) is False
        # sanity: format_version is what the evaluator compares against
        assert format_version("1.30") is not None


class TestEvalSize:
    """[SIZE bytes Plugin] against the file's real byte length."""

    def test_a_matching_size_fires(self, tmp_path: Path) -> None:
        """The predicate holds when the file is exactly the stated size."""
        (tmp_path / "sized.esp").write_bytes(b"\x00" * 500)
        index = PluginFileIndex([tmp_path])
        assert _eval_size("", 500, "sized.esp", ["sized.esp"], index) is True

    def test_a_negated_size_inverts(self, tmp_path: Path) -> None:
        """``!`` negates the comparison."""
        (tmp_path / "sized.esp").write_bytes(b"\x00" * 500)
        index = PluginFileIndex([tmp_path])
        assert _eval_size("!", 999, "sized.esp", ["sized.esp"], index) is True

    def test_an_unreadable_file_is_treated_as_unverifiable(self, tmp_path: Path) -> None:
        """A file present at index time but gone at stat time cannot be verified,
        so it errs on the side of firing -- mlox's 'cannot check' behaviour."""
        f = tmp_path / "sized.esp"
        f.write_bytes(b"\x00" * 500)
        index = PluginFileIndex([tmp_path])
        assert index.find("sized.esp") is not None  # force the index to cache the path
        f.unlink()  # now stat() will raise
        assert _eval_size("", 500, "sized.esp", ["sized.esp"], index) is True


class TestEvalDesc:
    """[DESC /regex/ Plugin] against the header description."""

    def test_a_matching_pattern_fires(self, tmp_path: Path) -> None:
        """The predicate holds when the regex matches the description."""
        _tes3(tmp_path, "mod.esp", b"A tidy little mod")
        index = PluginFileIndex([tmp_path])
        assert _eval_desc("", "tidy", "mod.esp", ["mod.esp"], index) is True

    def test_a_negated_pattern_inverts(self, tmp_path: Path) -> None:
        """``!`` inverts the match, so a present pattern makes it not fire."""
        _tes3(tmp_path, "mod.esp", b"A tidy little mod")
        index = PluginFileIndex([tmp_path])
        assert _eval_desc("!", "tidy", "mod.esp", ["mod.esp"], index) is False

    def test_an_invalid_regex_never_fires(self, tmp_path: Path) -> None:
        """A malformed pattern is treated as no match, not a crash."""
        _tes3(tmp_path, "mod.esp", b"A tidy little mod")
        index = PluginFileIndex([tmp_path])
        assert _eval_desc("", "[unclosed", "mod.esp", ["mod.esp"], index) is False
