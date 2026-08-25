"""The ``read_header`` convenience and its production wiring.

``wraithguard.esp.read_header`` reads just the first record of a plugin -- the
``TES3`` header -- and is what ``read_plugin_masters`` /
``read_plugin_masters_with_sizes`` (the sort engine's dependency edges and the
missing-master check) and ``read_plugin_description`` (the mlox ``[VER]``/
``[DESC]`` predicates) now read through. These pin the header read itself, that
it ignores everything after the header, and that the byte-scan fallback agrees
with it on a well-formed file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import rec, sub, tes3_header, write_plugin, zstr

import wraithguard_toolkit as core
from wraithguard.esp import EspError, Header, read_header
from wraithguard.plugins.metadata import read_plugin_description

if TYPE_CHECKING:
    from pathlib import Path


class TestReadHeader:
    def test_reads_version_author_description_and_masters(self) -> None:
        data = tes3_header(
            masters=("Morrowind.esm", "Tribunal.esm"),
            sizes=(79837557, 4234906),
            author="Someone",
            description="A tidy little mod",
        )
        header = read_header(data)
        assert isinstance(header, Header)
        assert header.author == "Someone"
        assert header.description == "A tidy little mod"
        assert header.masters == [("Morrowind.esm", 79837557), ("Tribunal.esm", 4234906)]

    def test_only_the_first_record_is_read(self) -> None:
        """A trailing record -- even a bogus one -- is never touched."""
        data = tes3_header(masters=("Morrowind.esm",)) + rec("STAT", sub("NAME", zstr("torch")))
        header = read_header(data)
        assert header.masters == [("Morrowind.esm", 0)]

    def test_survives_a_record_tes3conv_would_refuse(self) -> None:
        """The header parses even when a later record is an unknown tag."""
        data = tes3_header() + rec("LUAL", sub("NAME", zstr("script")))
        assert read_header(data).description == "fixture"

    def test_a_non_header_first_record_is_refused(self) -> None:
        with pytest.raises(EspError):
            read_header(rec("STAT", sub("NAME", zstr("torch"))))

    def test_truncated_bytes_raise_rather_than_guess(self) -> None:
        with pytest.raises(EspError):
            read_header(b"TES3\x08\x00\x00\x00")  # header claims a body it does not have


class TestProductionWiring:
    """The header reader, as the master/description helpers consume it."""

    def test_masters_read_through_the_esp_header(self, tmp_path: Path) -> None:
        path = write_plugin(tmp_path / "Mine.esp", masters=("Morrowind.esm", "Tribunal.esm"))
        assert core.read_plugin_masters(path) == ["Morrowind.esm", "Tribunal.esm"]

    def test_masters_with_sizes_read_through_the_esp_header(self, tmp_path: Path) -> None:
        path = write_plugin(tmp_path / "Mine.esp", masters=("Morrowind.esm",), sizes=(500,))
        assert core.read_plugin_masters_with_sizes(path) == [("Morrowind.esm", 500)]

    def test_the_esp_path_and_the_byte_scan_agree_on_a_normal_file(self, tmp_path: Path) -> None:
        """On a well-formed header the strict reader and the fallback match."""
        path = write_plugin(
            tmp_path / "Mine.esp",
            masters=("Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm"),
            sizes=(1, 2, 3),
        )
        assert core.read_plugin_masters(path) == core._read_plugin_masters_scan(path)
        assert core.read_plugin_masters_with_sizes(
            path
        ) == core._read_plugin_masters_with_sizes_scan(path)

    def test_description_reads_through_the_esp_header(self, tmp_path: Path) -> None:
        path = tmp_path / "Described.esp"
        path.write_bytes(tes3_header(description="A tidy little mod"))
        assert read_plugin_description(path) == "A tidy little mod"

    def test_cp1252_high_bytes_decode_correctly(self, tmp_path: Path) -> None:
        """A smart quote (0x92) is a real character in cp1252, not a control byte
        -- the correctness win over the old latin-1 fixed-offset scan."""
        path = tmp_path / "Fancy.esp"
        path.write_bytes(tes3_header(description="it\x92s tidy"))
        assert read_plugin_description(path) == "it\u2019s tidy"
