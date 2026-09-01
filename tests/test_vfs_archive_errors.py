"""Resilience of the mesh VFS when an archive in the folder misbehaves.

``read_mesh_bytes`` walks the loose file, the loose index, then every ``.bsa``
in the folder. A single archive that raises while being read must be logged and
stepped over -- one corrupt ``.bsa`` cannot make a mesh that a later archive
holds unreadable, and when nothing holds it the failure is a plain ``OSError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from wraithguard.nif import vfs
from wraithguard.nif.bsa import BsaError

if TYPE_CHECKING:
    from pathlib import Path


class _RaisingArchive:
    """An archive whose read always fails, standing in for a corrupt .bsa."""

    def __init__(self, name: str) -> None:
        self.path = type("P", (), {"name": name})()

    def read(self, _wanted: str) -> bytes | None:
        raise BsaError("truncated central directory")


class _EmptyArchive:
    """An archive that simply does not hold the wanted mesh."""

    def __init__(self, name: str) -> None:
        self.path = type("P", (), {"name": name})()

    def read(self, _wanted: str) -> bytes | None:
        return None


class _HoldingArchive:
    """An archive that returns the mesh bytes."""

    def __init__(self, name: str, payload: bytes) -> None:
        self.path = type("P", (), {"name": name})()
        self._payload = payload

    def read(self, _wanted: str) -> bytes | None:
        return self._payload


def test_a_raising_archive_is_logged_and_skipped(tmp_path: Path, monkeypatch) -> None:
    """A BsaError from one archive does not abort the search; it moves on."""
    monkeypatch.setattr(vfs, "archives_in", lambda _folder: [_RaisingArchive("bad.bsa")])
    with pytest.raises(OSError, match="not in"):
        vfs.read_mesh_bytes(tmp_path, "meshes/x.nif")


def test_a_later_archive_can_still_supply_the_mesh(tmp_path: Path, monkeypatch) -> None:
    """After a raising archive is skipped, a good one still resolves the mesh."""
    archives = [_RaisingArchive("bad.bsa"), _HoldingArchive("good.bsa", b"NIFDATA")]
    monkeypatch.setattr(vfs, "archives_in", lambda _folder: archives)
    assert vfs.read_mesh_bytes(tmp_path, "meshes/x.nif") == b"NIFDATA"


def test_an_archive_that_lacks_the_mesh_is_passed_over(tmp_path: Path, monkeypatch) -> None:
    """An archive returning None contributes nothing and the search continues."""
    monkeypatch.setattr(vfs, "archives_in", lambda _folder: [_EmptyArchive("empty.bsa")])
    with pytest.raises(OSError, match="not in"):
        vfs.read_mesh_bytes(tmp_path, "meshes/x.nif")
