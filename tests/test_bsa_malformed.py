"""Malformed-archive guards in :mod:`wraithguard.nif.bsa`.

The archive reader treats a ``.bsa`` as untrusted third-party data: every way a
header can lie or a file can be truncated is caught and turned into a
:class:`~wraithguard.nif.bsa.BsaError`, so one bad archive in a data folder is
skipped rather than crashing the scan. The happy path and the "corrupt archive
does not hide a good one" case are covered in ``test_mesh_from_archive``; this
file drives each individual guard with a hand-built bad header.
"""

from __future__ import annotations

import io
import struct
from typing import TYPE_CHECKING

import pytest

from wraithguard.nif.bsa import (
    _MAX_FILES,
    _TES3_VERSION,
    BsaArchive,
    BsaError,
    _read_exact,
    _read_name,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, data: bytes) -> Path:
    """Write raw bytes to ``path`` and return it."""
    path.write_bytes(data)
    return path


def test_a_file_too_short_for_a_header_is_refused(tmp_path: Path) -> None:
    """Fewer than twelve bytes cannot be an archive header."""
    archive = _write(tmp_path / "short.bsa", b"\x00\x01\x02")
    with pytest.raises(BsaError, match="too short"):
        BsaArchive(archive)


def test_a_post_morrowind_bsa_is_refused(tmp_path: Path) -> None:
    """The later ``BSA\\0`` magic is a different format we do not read."""
    archive = _write(tmp_path / "later.bsa", b"BSA\x00" + b"\x00" * 8)
    with pytest.raises(BsaError, match="post-Morrowind"):
        BsaArchive(archive)


def test_a_wrong_version_is_refused(tmp_path: Path) -> None:
    """Only Morrowind's version word is accepted."""
    archive = _write(tmp_path / "ver.bsa", struct.pack("<III", 0x999, 0, 0))
    with pytest.raises(BsaError, match="version"):
        BsaArchive(archive)


def test_an_absurd_file_count_is_refused(tmp_path: Path) -> None:
    """A header claiming more files than any real archive holds is rejected."""
    archive = _write(tmp_path / "many.bsa", struct.pack("<III", _TES3_VERSION, 0, _MAX_FILES + 1))
    with pytest.raises(BsaError, match="claims"):
        BsaArchive(archive)


def test_an_inconsistent_hash_offset_is_refused(tmp_path: Path) -> None:
    """A hash offset that leaves no room for the name table is inconsistent."""
    # version ok, count=1, hash_offset=0 -> names_length = 0 - 12 < 0. The
    # records (8 bytes) and name offsets (4 bytes) must still be readable first.
    header = struct.pack("<III", _TES3_VERSION, 0, 1)
    body = b"\x00" * 8 + b"\x00" * 4
    archive = _write(tmp_path / "bad.bsa", header + body)
    with pytest.raises(BsaError, match="inconsistent"):
        BsaArchive(archive)


def test_a_truncated_table_is_refused(tmp_path: Path) -> None:
    """A header promising a table the file does not contain is truncated."""
    # count=1 needs 8 bytes of records, but the file ends right after the header.
    archive = _write(tmp_path / "trunc.bsa", struct.pack("<III", _TES3_VERSION, 100, 1))
    with pytest.raises(BsaError, match="ends inside"):
        BsaArchive(archive)


def test_read_name_out_of_range_is_empty() -> None:
    """A name offset past the end of the table yields no name, not an error."""
    assert _read_name(b"abc\x00", 99) == ""


def test_read_name_of_an_immediate_terminator_is_empty() -> None:
    """An offset pointing straight at a NUL is an empty (skipped) name."""
    assert _read_name(b"\x00abc", 0) == ""


def test_read_exact_raises_on_a_short_read() -> None:
    """Asking for more bytes than remain is a truncation error."""
    with pytest.raises(BsaError, match="ends inside"):
        _read_exact(io.BytesIO(b"ab"), 10, "file records")
