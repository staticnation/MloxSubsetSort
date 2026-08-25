"""Locating plugin files and reading their metadata -- the [VER]/[DESC] backing.

Exercises ``read_plugin_description`` (now backed by the in-process ESP header
reader), ``plugin_version``'s header path, and ``list_plugins_in_dir``'s guard
clauses, using real (if minimal) TES3 header records and real temp directories.
A header is one ``TES3`` record whose ``HEDR`` subrecord carries the
description, so :func:`_tes3` fabricates exactly that framing.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from wraithguard.plugins.metadata import (
    PluginFileIndex,
    list_plugins_in_dir,
    plugin_version,
    read_plugin_description,
)
from wraithguard.versions import format_version

if TYPE_CHECKING:
    from pathlib import Path

#: Size of the fixed ``HEDR`` block: version, file type, author, description,
#: object count -- the exact layout the ESP reader and the engine expect.
_HEDR_SIZE = 300

#: Byte offset of the description within the ``HEDR`` block: past the f32
#: version (4), the u32 file type (4), and the 32-byte author field.
_DESC_IN_HEDR = 40


def _tes3(tmp_path: Path, name: str, description: bytes) -> Path:
    """Write a minimal but *valid* TES3 plugin carrying the given description.

    One ``TES3`` record: the 16-byte record header, then a proper ``HEDR``
    subrecord with the description null-padded into its fixed field -- the real
    framing the header reader parses, not a description dropped at a raw offset.
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


class TestReadPluginDescription:
    """Reading the description field, tolerating anything that is not a plugin."""

    def test_a_missing_file_reads_as_empty(self, tmp_path: Path) -> None:
        """An unreadable path returns ``""`` rather than raising mid-scan."""
        assert read_plugin_description(tmp_path / "nope.esp") == ""

    def test_a_non_tes3_file_reads_as_empty(self, tmp_path: Path) -> None:
        """A file without the TES3 magic is not something this tool can read."""
        f = tmp_path / "notplugin.esp"
        f.write_bytes(b"NOPE" + b"\x00" * 400)
        assert read_plugin_description(f) == ""

    def test_a_too_short_file_reads_as_empty(self, tmp_path: Path) -> None:
        """A file too short for a complete header is rejected, not parsed."""
        f = tmp_path / "short.esp"
        f.write_bytes(b"TES3" + b"\x00" * 10)
        assert read_plugin_description(f) == ""

    def test_a_valid_header_yields_its_description(self, tmp_path: Path) -> None:
        """A well-formed header returns the null-terminated description text."""
        f = _tes3(tmp_path, "mod.esp", b"A tidy little mod")
        assert read_plugin_description(f) == "A tidy little mod"


class TestPluginVersion:
    """Header version beats filename version beats nothing."""

    def test_the_header_version_wins(self, tmp_path: Path) -> None:
        """A version stated in the header is authoritative over the filename."""
        _tes3(tmp_path, "Mod.esp", b"version 1.30")
        index = PluginFileIndex([tmp_path])
        assert plugin_version("Mod.esp", index) == format_version("1.30")

    def test_it_falls_back_to_the_filename(self, tmp_path: Path) -> None:
        """With no readable file, a version in the name is the next best guess."""
        assert plugin_version("Better Bodies 2.2.esp", None) == format_version("2.2")

    def test_an_unknowable_version_is_none(self) -> None:
        """Neither source yields one, so the answer is 'unknowable', not 'old'."""
        assert plugin_version("plain.esp", None) is None


class TestListPluginsInDir:
    """Listing plugins in a data folder, guarded against every bad path."""

    def test_an_empty_value_lists_nothing(self) -> None:
        """An empty ``data=`` value describes no folder."""
        assert list_plugins_in_dir("") == []

    def test_a_quotes_only_value_lists_nothing(self) -> None:
        """A value that is nothing but quotes strips to empty."""
        assert list_plugins_in_dir('""') == []

    def test_a_nonexistent_directory_lists_nothing(self, tmp_path: Path) -> None:
        """A folder that is not there yields an empty list, not an error."""
        assert list_plugins_in_dir(str(tmp_path / "nope")) == []

    def test_it_lists_plugins_sorted_ignoring_non_plugins(self, tmp_path: Path) -> None:
        """Only plugin extensions are returned, in sorted order."""
        (tmp_path / "B.esp").write_bytes(b"")
        (tmp_path / "A.esm").write_bytes(b"")
        (tmp_path / "notes.txt").write_bytes(b"")
        assert list_plugins_in_dir(str(tmp_path)) == ["A.esm", "B.esp"]

    def test_a_relative_path_resolves_against_base_dir(self, tmp_path: Path) -> None:
        """A relative ``data=`` value is joined onto the base directory."""
        sub = tmp_path / "mods"
        sub.mkdir()
        (sub / "X.esp").write_bytes(b"")
        assert list_plugins_in_dir("mods", base_dir=tmp_path) == ["X.esp"]
