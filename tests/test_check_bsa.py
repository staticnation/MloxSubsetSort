"""Tests for ``tools/check_bsa.py``, the real-archive sanity checker.

The tool exists because the BSA reader's own round-trip tests only prove the
reader agrees with the test writer -- a shared wrong understanding passes. This
checks extracted bytes against the magic each extension implies, which is the
one thing that catches a self-consistent index pointing at the wrong data. The
archives here are built by the same real-layout ``build_bsa`` the reader tests
use, so the checker runs against genuine parsed archives, not mocks.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_mesh_from_archive import build_bsa
from tools.check_bsa import main


def _dds(body: bytes = b"\x00" * 16) -> bytes:
    return b"DDS " + body


def _nif(body: bytes = b"\x00" * 16) -> bytes:
    return b"NetImmerse" + body


def test_a_clean_archive_passes(tmp_path, capsys) -> None:
    """Every sampled file starting with its extension's magic exits zero."""
    archive = tmp_path / "clean.bsa"
    build_bsa(archive, {"tex/a.dds": _dds(), "mesh/b.nif": _nif(), "notes/readme.txt": b"hi"})
    assert main([str(archive)]) == 0
    out = capsys.readouterr().out
    assert "3 file(s)" in out
    assert "magic its extension implies" in out


def test_a_file_that_cannot_be_opened_exits_two(tmp_path, capsys) -> None:
    """A path that is not a readable archive is a clean exit 2, not a crash."""
    not_bsa = tmp_path / "junk.bsa"
    not_bsa.write_bytes(b"\x00\x01\x02")  # too short for even a header
    assert main([str(not_bsa)]) == 2
    assert "could not open" in capsys.readouterr().err


def test_wrong_magic_is_caught_and_fails(tmp_path, capsys) -> None:
    """A .dds whose bytes do not start ``DDS `` means a wrong data offset."""
    archive = tmp_path / "wrong.bsa"
    build_bsa(archive, {"tex/bad.dds": b"XXXX not a dds"})
    assert main([str(archive)]) == 1
    out = capsys.readouterr().out
    assert "WRONG MAGIC" in out
    assert "1 with wrong magic" in out


def test_only_the_first_five_mismatches_are_listed(tmp_path, capsys) -> None:
    """Six wrong files still fail, but the report caps its itemised list at five."""
    archive = tmp_path / "many_wrong.bsa"
    build_bsa(archive, {f"tex/bad{i}.dds": b"nope" for i in range(6)})
    assert main([str(archive)]) == 1
    out = capsys.readouterr().out
    assert out.count("WRONG MAGIC") == 5  # the sixth is counted but not printed
    assert "6 with wrong magic" in out


def test_an_implausible_name_fails_even_with_right_magic(tmp_path, capsys) -> None:
    """A name carrying a control character signals a wrong name-table offset."""
    archive = tmp_path / "oddname.bsa"
    build_bsa(archive, {"tex/\x01bad.dds": _dds()})
    assert main([str(archive)]) == 1
    assert "not plausible paths: 1" in capsys.readouterr().out


def test_a_file_truncated_past_the_index_is_counted_unreadable(tmp_path, capsys) -> None:
    """A consistent index whose data runs past the file end fails as unreadable.

    The index parses (it lives at the front), so the archive opens; the read of
    the truncated payload is where it goes wrong -- exactly the split between a
    plausible index and wrong data the tool is built to surface.
    """
    archive = tmp_path / "cut.bsa"
    build_bsa(archive, {"tex/a.dds": _dds(b"\x00" * 32)})
    archive.write_bytes(archive.read_bytes()[:-20])  # lop off the tail of the data
    assert main([str(archive)]) == 1
    assert "1 unreadable" in capsys.readouterr().out


def test_the_sample_flag_limits_how_many_are_extracted(tmp_path, capsys) -> None:
    """``--sample`` bounds the number of files read regardless of archive size."""
    archive = tmp_path / "big.bsa"
    build_bsa(archive, {f"tex/a{i}.dds": _dds() for i in range(10)})
    assert main([str(archive), "--sample", "2"]) == 0
    assert "extracted 2 file(s)" in capsys.readouterr().out
