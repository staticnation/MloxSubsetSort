"""Reading landscape without tes3conv's permission.

tes3conv decodes a plugin by *understanding* every record in it and refuses the
whole file on one it does not know. A real 27-master load order stopped at::

    could not read master the_Arcane_Academy_of_Venarius.esm:
    tes3conv exited 1: Error: Custom { kind: InvalidData,
                                       error: "Unexpected Tag: LUAL" }

``LUAL`` is OpenMW's Lua-script configuration record. It has no bearing on
terrain whatsoever, and it stopped a nine-hundred-mod merge before it started.

These tests cover the two things that fix it: a length-driven reader that skips
record types it has never heard of, and the record-key sidecars that say which
plugins are worth opening at all.
"""

from __future__ import annotations

import json
import os
import struct
from typing import TYPE_CHECKING, Final

import pytest

from wraithguard.land.diff import LandscapeLayers, is_deleted
from wraithguard.land.native import (
    KEYS_VERSION,
    NativeReadError,
    format_landscape_flags,
    has_landscape,
    landscape_in_sidecar,
    landscape_records_from_sidecar,
    read_landscape_records,
)
from wraithguard.tes3fields.landscape import LAND_SIZE

if TYPE_CHECKING:
    from pathlib import Path

#: Vertices in one cell.
VERTICES: Final = LAND_SIZE * LAND_SIZE


def record(tag: bytes, body: bytes, flags: int = 0) -> bytes:
    """Frame a record body with a TES3 record header.

    Args:
        tag: The four-byte record type.
        body: The already-assembled subrecords.
        flags: Object flags.

    Returns:
        The framed record.
    """
    return (
        tag + struct.pack("<I", len(body)) + struct.pack("<I", 0) + struct.pack("<I", flags) + body
    )


def sub(tag: bytes, payload: bytes) -> bytes:
    """Frame one subrecord.

    Args:
        tag: The four-byte subrecord type.
        payload: Its contents.

    Returns:
        The framed subrecord.
    """
    return tag + struct.pack("<I", len(payload)) + payload


def land(x: int, y: int, flags: int = 0x1, height: int = 0) -> bytes:
    """A minimal but valid ``LAND`` record.

    Args:
        x: Cell column.
        y: Cell row.
        flags: The ``DATA`` word.
        height: A constant first-delta, giving a flat cell.

    Returns:
        The framed record.
    """
    vhgt = struct.pack("<f", 0.0) + bytes([height]) + bytes(VERTICES - 1) + b"\x00\x00\x00"
    body = sub(b"INTV", struct.pack("<ii", x, y)) + sub(b"DATA", struct.pack("<I", flags))
    body += sub(b"VHGT", vhgt)
    return record(b"LAND", body)


def ltex(identifier: str, index: int, file_name: str) -> bytes:
    """A minimal ``LTEX`` record.

    Args:
        identifier: The texture's id.
        index: Its index in the plugin's own space.
        file_name: The texture file.

    Returns:
        The framed record.
    """
    body = sub(b"NAME", identifier.encode("latin-1") + b"\x00")
    body += sub(b"INTV", struct.pack("<I", index))
    body += sub(b"DATA", file_name.encode("latin-1") + b"\x00")
    return record(b"LTEX", body)


def _file(*records: bytes) -> bytes:
    """A whole plugin: the ``TES3`` header every real file starts with, then
    the records under test.

    The header is not decoration. :func:`read_landscape_records` refuses a file
    that does not begin ``TES3``, because an Oblivion or Skyrim plugin walked
    as a Morrowind one produces landscape out of nothing.

    Args:
        records: Already-framed records.

    Returns:
        The plugin bytes.
    """
    return record(b"TES3", sub(b"HEDR", bytes(300))) + b"".join(records)


class TestUnknownRecordsAreSkipped:
    """The whole point: a record type we have never heard of is not fatal."""

    def test_a_lual_record_does_not_stop_the_read(self, tmp_path: Path) -> None:
        """The exact record that stopped the user's load order."""
        plugin = tmp_path / "lua.esm"
        plugin.write_bytes(
            _file(record(b"LUAL", sub(b"NAME", b"whatever\x00") + bytes(64)), land(3, 4))
        )
        records = read_landscape_records(plugin)
        assert [r["type"] for r in records] == ["Landscape"]
        assert records[0]["grid"] == [3, 4]

    def test_an_unknown_record_between_two_land_records(self, tmp_path: Path) -> None:
        """Skipping must land exactly on the next record, not near it."""
        plugin = tmp_path / "x.esm"
        plugin.write_bytes(
            _file(
                land(0, 0),
                record(b"ZZZZ", bytes(1234)),
                land(1, 1),
                record(b"QQQQ", b""),
                land(2, 2),
            )
        )
        grids = [r["grid"] for r in read_landscape_records(plugin)]
        assert grids == [[0, 0], [1, 1], [2, 2]]

    def test_a_truncated_final_record_keeps_what_parsed(self, tmp_path: Path) -> None:
        """Plugins come from the internet; salvage rather than abandon."""
        plugin = tmp_path / "cut.esm"
        plugin.write_bytes(
            _file(land(0, 0)) + b"LAND" + struct.pack("<I", 9999) + bytes(8) + b"short"
        )
        assert [r["grid"] for r in read_landscape_records(plugin)] == [[0, 0]]

    def test_a_plugin_with_no_landscape_reads_as_nothing(self, tmp_path: Path) -> None:
        """No terrain is a valid answer, not an error.

        Distinct from an empty *file*, which is not a plugin at all and is
        refused -- see :class:`TestForeignFormatsAreRefused`. A real plugin
        that simply has no ``LAND`` records is the common case: 869 of 972 on
        a real load order.
        """
        plugin = tmp_path / "nothing.esm"
        plugin.write_bytes(_file(record(b"STAT", sub(b"NAME", b"rock\x00"))))
        assert read_landscape_records(plugin) == []


class TestRecordsMatchWhatTheMergeExpects:
    """Output has to be the shape ``LandscapeLayers.from_record`` already reads."""

    def test_a_landscape_record_decodes(self, tmp_path: Path) -> None:
        """Straight into the merge's own decoder, with no adapter."""
        plugin = tmp_path / "a.esm"
        plugin.write_bytes(_file(land(-7, 12, flags=0x1, height=2)))
        layers = LandscapeLayers.from_record(read_landscape_records(plugin)[0])
        assert layers.coords == (-7, 12)
        assert layers.heights is not None
        assert len(layers.heights) == VERTICES

    def test_a_texture_record_decodes(self, tmp_path: Path) -> None:
        """id, index and file name are what the shared table is built from."""
        plugin = tmp_path / "t.esm"
        plugin.write_bytes(_file(ltex("Rock_Coastal", 4, "Tx_rock_coastal.tga")))
        got = read_landscape_records(plugin)[0]
        assert got["type"] == "LandscapeTexture"
        assert got["id"] == "Rock_Coastal"
        assert got["index"] == 4
        assert got["file_name"] == "Tx_rock_coastal.tga"

    def test_a_deleted_record_is_marked(self, tmp_path: Path) -> None:
        """``diff.is_deleted`` must recognise what this writes."""
        plugin = tmp_path / "d.esm"
        plugin.write_bytes(_file(record(b"LTEX", sub(b"NAME", b"gone\x00"), flags=0x20)))
        assert is_deleted(read_landscape_records(plugin)[0])

    def test_a_land_record_without_coordinates_is_dropped(self, tmp_path: Path) -> None:
        """Nowhere to put it; keeping it would collide with cell (0, 0)."""
        plugin = tmp_path / "n.esm"
        plugin.write_bytes(_file(record(b"LAND", sub(b"DATA", struct.pack("<I", 1)))))
        assert read_landscape_records(plugin) == []

    def test_a_texture_without_an_id_is_dropped(self, tmp_path: Path) -> None:
        """An id is the only thing that matches two plugins' textures."""
        plugin = tmp_path / "u.esm"
        plugin.write_bytes(_file(record(b"LTEX", sub(b"INTV", struct.pack("<I", 3)))))
        assert read_landscape_records(plugin) == []


class TestLandscapeFlags:
    """Only the bits ``tes3`` names may be emitted."""

    def test_the_three_named_bits(self) -> None:
        """Spelled as tes3conv spells them, because the parser expects that."""
        assert format_landscape_flags(0x7) == (
            "USES_VERTEX_HEIGHTS_AND_NORMALS | USES_VERTEX_COLORS | USES_TEXTURES"
        )

    def test_the_unnamed_bit_is_dropped(self) -> None:
        """Real files carry ``0x8``; naming it produces a string tes3 rejects."""
        assert format_landscape_flags(0x9) == "USES_VERTEX_HEIGHTS_AND_NORMALS"

    def test_no_bits(self) -> None:
        """A record declaring nothing declares nothing."""
        assert format_landscape_flags(0) == ""


class TestTheSidecarDecidesWhatToOpen:
    """869 of 972 plugins have no terrain. Opening them is wasted work."""

    def _sidecar(
        self, folder: Path, stem: str, types: list[str], version: int = KEYS_VERSION
    ) -> None:
        """Write a record-key sidecar naming ``types``."""
        rows = [[name, "x", False] for name in types]
        (folder / f"{stem}.keys.json").write_text(
            json.dumps({"v": version, "d": rows}), encoding="utf-8"
        )

    def _plugin(self, folder: Path, stem: str) -> Path:
        """An empty plugin file, backdated so its sidecar looks current."""
        path = folder / f"{stem}.esm"
        path.write_bytes(b"")
        os.utime(path, (0, 0))
        return path

    def test_a_plugin_with_landscape_is_reported(self, tmp_path: Path) -> None:
        """The sidecar names the record types outright."""
        self._sidecar(tmp_path, "a", ["Static", "Landscape"])
        assert landscape_in_sidecar(self._plugin(tmp_path, "a"), tmp_path) is True

    def test_a_texture_alone_counts(self, tmp_path: Path) -> None:
        """An ``LTEX``-only plugin still contributes to the shared table."""
        self._sidecar(tmp_path, "b", ["LandscapeTexture"])
        assert landscape_in_sidecar(self._plugin(tmp_path, "b"), tmp_path) is True

    def test_a_plugin_without_landscape_is_reported(self, tmp_path: Path) -> None:
        """This is the answer that saves the work."""
        self._sidecar(tmp_path, "c", ["Static", "Npc", "Dialogue"])
        assert landscape_in_sidecar(self._plugin(tmp_path, "c"), tmp_path) is False

    def test_a_stale_sidecar_is_no_answer(self, tmp_path: Path) -> None:
        """The decisive one.

        The conflict scanner happily reuses a cache older than its plugin --
        for listing records that is a reasonable trade. Here it decides whether
        a plugin is *read at all*, so a plugin that has since gained terrain
        would have that terrain dropped from the merge silently.
        """
        self._sidecar(tmp_path, "d", ["Static"])
        os.utime(tmp_path / "d.keys.json", (0, 0))
        plugin = tmp_path / "d.esm"
        plugin.write_bytes(b"")
        # Set explicitly rather than relying on write order: a filesystem whose
        # timestamps are second-granular would make the two equal, and equal is
        # not stale.
        os.utime(plugin, (1_000_000, 1_000_000))
        assert landscape_in_sidecar(plugin, tmp_path) is None

    def test_a_missing_sidecar_is_no_answer(self, tmp_path: Path) -> None:
        """Most installs have never run a scan."""
        assert landscape_in_sidecar(self._plugin(tmp_path, "e"), tmp_path) is None

    def test_a_sidecar_from_another_schema_is_no_answer(self, tmp_path: Path) -> None:
        """A different version may not mean what this reader assumes."""
        self._sidecar(tmp_path, "f", ["Landscape"], version=KEYS_VERSION + 1)
        assert landscape_in_sidecar(self._plugin(tmp_path, "f"), tmp_path) is None

    def test_a_corrupt_sidecar_is_no_answer(self, tmp_path: Path) -> None:
        """A truncated cache must not be read as "no terrain here"."""
        (tmp_path / "g.keys.json").write_text("{not json", encoding="utf-8")
        assert landscape_in_sidecar(self._plugin(tmp_path, "g"), tmp_path) is None


class TestTheBytePrescan:
    """Without a sidecar, a cheap scan still avoids most conversions."""

    def test_a_plugin_with_land_is_found(self, tmp_path: Path) -> None:
        """The tag appears in the file, so the file is worth opening."""
        plugin = tmp_path / "a.esm"
        plugin.write_bytes(_file(land(0, 0)))
        assert has_landscape(plugin)

    def test_a_plugin_without_either_tag(self, tmp_path: Path) -> None:
        """Nothing to merge, and no subprocess spent finding that out."""
        plugin = tmp_path / "b.esm"
        plugin.write_bytes(_file(record(b"STAT", sub(b"NAME", b"rock\x00"))))
        assert not has_landscape(plugin)

    def test_a_tag_split_across_a_chunk_boundary(self, tmp_path: Path) -> None:
        """The overlap exists for exactly this; without it the tag is missed."""
        plugin = tmp_path / "c.esm"
        plugin.write_bytes(_file() + b"\x00" * ((1 << 20) - 2) + b"LAND" + b"\x00" * 16)
        assert has_landscape(plugin)

    def test_an_unreadable_file_is_assumed_to_have_terrain(self, tmp_path: Path) -> None:
        """An I/O error must surface from the real reader, with a real message."""
        assert has_landscape(tmp_path / "missing.esm")

    def test_the_sidecar_wins_when_it_answers(self, tmp_path: Path) -> None:
        """Exact beats heuristic: the bytes say yes, the sidecar says no."""
        plugin = tmp_path / "d.esm"
        plugin.write_bytes(_file(land(0, 0)))
        os.utime(plugin, (0, 0))
        (tmp_path / "d.keys.json").write_text(
            json.dumps({"v": KEYS_VERSION, "d": [["Static", "x", False]]}), encoding="utf-8"
        )
        assert has_landscape(plugin, tmp_path) is False

    def test_an_absent_sidecar_falls_back_to_reading_the_plugin(self, tmp_path: Path) -> None:
        """With a sidecar dir but no sidecar for this plugin, the bytes decide."""
        plugin = tmp_path / "d.esm"
        plugin.write_bytes(_file(land(0, 0)))  # really does have landscape
        # tmp_path holds no d.keys.json, so the sidecar answer is None and the
        # reader falls through to scanning the plugin itself.
        assert has_landscape(plugin, tmp_path) is True


class TestForeignFormatsAreRefused:
    """A Morrowind load order can contain an Oblivion or Skyrim plugin.

    An armour pack ported from Skyrim that shipped its source ``.esp``, say.
    OpenMW itself copes -- it dispatches on the header magic and hands ``TES4``
    files to a different reader entirely.

    This reader must refuse them, and the reason is the same tolerance that
    makes it useful: *skip anything you do not recognise, by its declared
    length*. TES4 record headers are 24 bytes where this expects 16, so walking
    one does not fail -- it succeeds, and invents landscape out of whatever the
    bytes happen to spell. Skyrim has ``LAND`` records of its own, so even the
    cheap pre-scan matches.

    Shipped consequence: a Skyrim ``.esp`` was read as terrain, its garbage
    cells entered the merge, and it was declared a contributing master. OpenMW
    then had to load it, recognised ``TES4``, and died with::

        ESM4::Reader::updateModIndices required dependency 'Skyrim.esm' not found
    """

    def _tes4(self, tmp_path: Path) -> Path:
        """A Skyrim-shaped plugin, with a LAND record and a Skyrim.esm master."""
        header = b"TES4" + struct.pack("<I", 0) + b"\x00" * 16
        land_record = b"LAND" + struct.pack("<I", 40) + b"\x00" * 16 + b"\x00" * 40
        path = tmp_path / "SkyrimArmor.esp"
        path.write_bytes(header + land_record + b"Skyrim.esm\x00")
        return path

    def test_the_prescan_does_not_claim_it(self, tmp_path: Path) -> None:
        """It has LAND records, so only the magic can tell them apart."""
        assert has_landscape(self._tes4(tmp_path)) is False

    def test_reading_it_is_refused(self, tmp_path: Path) -> None:
        """Refused, not tolerated -- tolerance is what invents the terrain."""
        with pytest.raises(NativeReadError, match="not a Morrowind plugin"):
            read_landscape_records(self._tes4(tmp_path))

    def test_the_message_names_what_it_found(self, tmp_path: Path) -> None:
        """'TES4' is the fact the user needs to go and find the file."""
        with pytest.raises(NativeReadError, match="TES4"):
            read_landscape_records(self._tes4(tmp_path))

    def test_an_empty_file_is_refused_too(self, tmp_path: Path) -> None:
        """No magic at all is not a TES3 plugin either."""
        path = tmp_path / "empty.esp"
        path.write_bytes(b"")
        with pytest.raises(NativeReadError):
            read_landscape_records(path)

    def test_a_real_tes3_plugin_still_reads(self, tmp_path: Path) -> None:
        """The guard must not cost us the case it was added to protect."""
        path = tmp_path / "ok.esp"
        path.write_bytes(record(b"TES3", sub(b"HEDR", bytes(300))) + land(1, 2))
        assert [r["grid"] for r in read_landscape_records(path)] == [[1, 2]]
        assert has_landscape(path) is True


class TestNativeSubrecordEdges:
    """Truncation, deletion markers, and read failure in the native reader."""

    def _write(self, tmp_path: Path, *records: bytes) -> Path:
        path = tmp_path / "plugin.esp"
        path.write_bytes(_file(*records))
        return path

    def test_a_subrecord_claiming_too_many_bytes_stops_the_walk(self, tmp_path: Path) -> None:
        """A subrecord length past the record end truncates the walk, not crashes."""
        body = sub(b"INTV", struct.pack("<ii", 0, 0)) + b"VHGT" + struct.pack("<I", 9999)
        path = self._write(tmp_path, record(b"LAND", body))
        grids = [r["grid"] for r in read_landscape_records(path)]
        assert grids == [[0, 0]]  # INTV parsed; the impossible VHGT was dropped

    def test_a_deleted_landscape_record_is_flagged(self, tmp_path: Path) -> None:
        """A ``DELE`` subrecord marks the landscape record deleted.

        DELE is placed before another subrecord so the walk continues past it.
        """
        body = (
            sub(b"INTV", struct.pack("<ii", 0, 0))
            + sub(b"DELE", bytes(4))
            + sub(b"DATA", struct.pack("<I", 0))  # a subrecord after DELE
        )
        path = self._write(tmp_path, record(b"LAND", body))
        (rec,) = read_landscape_records(path)
        assert rec["flags"] == "DELETED"

    def test_a_deleted_texture_record_is_flagged(self, tmp_path: Path) -> None:
        """A ``DELE`` subrecord marks a LandscapeTexture record deleted."""
        body = (
            sub(b"NAME", b"tex\x00")
            + sub(b"DELE", bytes(4))
            + sub(b"INTV", struct.pack("<I", 0))  # a subrecord after DELE
        )
        path = self._write(tmp_path, record(b"LTEX", body))
        texture = next(r for r in read_landscape_records(path) if r["type"] == "LandscapeTexture")
        assert texture["flags"] == "DELETED"

    def test_a_grid_subrecord_is_carried_as_raw_data(self, tmp_path: Path) -> None:
        """A VTEX/VCLR/etc grid subrecord is passed through as a raw data blob."""
        body = sub(b"INTV", struct.pack("<ii", 0, 0)) + sub(b"VTEX", b"\x01\x02\x03\x04")
        path = self._write(tmp_path, record(b"LAND", body))
        (rec,) = read_landscape_records(path)
        assert rec["texture_indices"]["data"] == b"\x01\x02\x03\x04"

    def test_an_unknown_landscape_subrecord_is_ignored(self, tmp_path: Path) -> None:
        """A subrecord tag the reader does not model is skipped, not fatal."""
        body = (
            sub(b"INTV", struct.pack("<ii", 0, 0))
            + sub(b"ZZZZ", b"unmodelled")  # matches no branch -> falls through
            + sub(b"DATA", struct.pack("<I", 0))
        )
        path = self._write(tmp_path, record(b"LAND", body))
        (rec,) = read_landscape_records(path)
        assert rec["grid"] == [0, 0]

    def test_an_unknown_texture_subrecord_is_ignored(self, tmp_path: Path) -> None:
        """An unmodelled subrecord in a LandscapeTexture record is skipped."""
        body = (
            sub(b"NAME", b"tex\x00")
            + sub(b"ZZZZ", b"unmodelled")  # matches no branch -> falls through
            + sub(b"INTV", struct.pack("<I", 0))
        )
        path = self._write(tmp_path, record(b"LTEX", body))
        assert any(r["type"] == "LandscapeTexture" for r in read_landscape_records(path))

    def test_a_path_that_cannot_be_read_is_a_native_read_error(self, tmp_path: Path) -> None:
        """Opening a directory as a plugin fails as a NativeReadError, not OSError."""
        directory = tmp_path / "a-directory.esp"
        directory.mkdir()
        with pytest.raises(NativeReadError, match="could not read"):
            read_landscape_records(directory)


class TestSidecarMalformed:
    """A sidecar whose ``d`` field is not a list is treated as no answer."""

    def _stale_proof(self, plugin: Path, side: Path) -> None:
        """Make the sidecar at least as new as the plugin so it is trusted."""
        now = side.stat().st_mtime
        os.utime(plugin, (now - 10, now - 10))

    def test_landscape_in_sidecar_rejects_a_non_list_body(self, tmp_path: Path) -> None:
        plugin = tmp_path / "P.esp"
        plugin.write_bytes(b"x")
        side = tmp_path / "P.keys.json"
        side.write_text(json.dumps({"v": KEYS_VERSION, "d": "not a list"}), encoding="utf-8")
        self._stale_proof(plugin, side)
        assert landscape_in_sidecar(plugin, tmp_path) is None

    def test_records_from_sidecar_rejects_a_non_list_body(self, tmp_path: Path) -> None:
        plugin = tmp_path / "P.esp"
        plugin.write_bytes(b"x")
        side = tmp_path / "P.land.json"
        side.write_text(json.dumps({"v": KEYS_VERSION, "d": {"not": "a list"}}), encoding="utf-8")
        self._stale_proof(plugin, side)
        assert landscape_records_from_sidecar(plugin, tmp_path) is None
