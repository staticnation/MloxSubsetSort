"""Defensive branches in plugin metadata reading and directory listing.

Plugin files and ``data=`` paths come from the internet and from hand-edited
config: an unreadable directory, a header the strict reader refuses, a path that
cannot even be interpreted. None of these may abort a scan, so each degrades to
an empty result or the byte-scan fallback. Those degradations are pinned here.
"""

from __future__ import annotations

import struct
from pathlib import Path

from wraithguard.plugins.metadata import (
    PluginFileIndex,
    list_plugins_in_dir,
    read_plugin_description,
)


def test_index_skips_a_directory_that_cannot_be_listed(tmp_path: Path, monkeypatch) -> None:
    """An OSError while listing a data directory is swallowed, not fatal."""
    real_iterdir = Path.iterdir

    def refuse(self: Path):
        if self == tmp_path:
            raise OSError("unreadable mount")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", refuse)
    index = PluginFileIndex([tmp_path])
    assert index.find("anything.esp") is None


def test_description_falls_back_to_the_byte_scan(tmp_path: Path) -> None:
    """A header the strict reader refuses still yields its description by scan.

    The file has the ``TES3`` magic and a description at the documented offset,
    but a body the ESP header parser rejects -- so the parse returns ``None``
    and the fixed-offset scan supplies the text.
    """
    block = bytearray(400)
    block[0:4] = b"TES3"
    struct.pack_into("<I", block, 4, 300)  # a body size the parser cannot honour
    desc = b"A test plugin description"
    block[64 : 64 + len(desc)] = desc
    block[64 + len(desc)] = 0  # null terminator
    plugin = tmp_path / "broken.esp"
    plugin.write_bytes(bytes(block))

    assert read_plugin_description(plugin) == "A test plugin description"


def test_a_non_tes3_file_has_no_description(tmp_path: Path) -> None:
    """A file without the magic is not a plugin this tool can read."""
    plugin = tmp_path / "notaplugin.esp"
    plugin.write_bytes(b"NOPE" + bytes(400))
    assert read_plugin_description(plugin) == ""


def test_list_plugins_returns_empty_for_an_uninterpretable_path(tmp_path, monkeypatch) -> None:
    """A path that cannot even be interpreted yields nothing rather than raising."""

    def refuse(_self: Path) -> bool:
        raise ValueError("embedded null byte")

    # A path the platform refuses to classify (e.g. an unsubstituted MO2
    # variable) surfaces while building the candidate list and is swallowed.
    monkeypatch.setattr(Path, "is_absolute", refuse)
    assert list_plugins_in_dir("rel", base_dir=tmp_path) == []


def test_list_plugins_skips_a_directory_that_raises_on_listing(tmp_path: Path, monkeypatch) -> None:
    """A directory that raises while being listed contributes nothing."""

    def refuse(_self: Path):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "iterdir", refuse)
    assert list_plugins_in_dir(str(tmp_path)) == []


def test_list_plugins_finds_the_plugin_files(tmp_path: Path) -> None:
    """The ordinary case: plugin files in the folder are returned, sorted."""
    (tmp_path / "b.esp").write_bytes(b"")
    (tmp_path / "a.esm").write_bytes(b"")
    (tmp_path / "notes.txt").write_bytes(b"")
    assert list_plugins_in_dir(str(tmp_path)) == ["a.esm", "b.esp"]
