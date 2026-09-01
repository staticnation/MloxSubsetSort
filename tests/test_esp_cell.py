"""The ``CELL`` record -- header plus object references.

The hardest record: a header with several optional blocks, then references, each
framed by an ``FRMR`` packed index. This pins the reference sub-record (its many
optional fields and its ``DATA``/``DELE`` terminator), moved references
(``MVRF``/``CNDT``), the ``NAM0`` marker separating persistent from temporary
references, and a whole interior cell round-tripping byte-for-byte.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import Cell, Reference, read_plugin, write_plugin
from wraithguard.esp.flags import CellFlags, ObjectFlags


def _sub(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack("<I", len(body)) + body


def _string_sub(tag: bytes, text: str) -> bytes:
    return _sub(tag, text.encode("cp1252") + b"\x00")


def _record(tag: bytes, subrecords: bytes, flags: int = 0) -> bytes:
    return (
        tag
        + struct.pack("<I", len(subrecords))
        + struct.pack("<I", 0)
        + struct.pack("<I", flags)
        + subrecords
    )


def _pack(mast: int, refr: int) -> int:
    return refr | (mast << 24)


def _data(x: float, y: float, z: float) -> bytes:
    return _sub(b"DATA", struct.pack("<6f", x, y, z, 0.0, 0.0, 0.0))


def _ref(mast: int, refr: int, obj_id: str, extra: bytes = b"") -> bytes:
    return (
        _sub(b"FRMR", struct.pack("<I", _pack(mast, refr)))
        + _string_sub(b"NAME", obj_id)
        + extra
        + _data(1.0, 2.0, 3.0)
    )


class TestCell:
    def test_interior_cell_with_references_round_trips(self) -> None:
        # A persistent ref, then the NAM0 marker, then a temporary ref with owner.
        refs = (
            _ref(0, 1, "player_bed")  # persistent (temporary False -> persistent True)
            + _sub(b"NAM0", struct.pack("<I", 1))  # one temporary ref follows
            + _ref(0, 2, "com_chest_01", _string_sub(b"ANAM", "some_owner"))
        )
        body = (
            _string_sub(b"NAME", "Balmora, Guild of Fighters")
            + _sub(b"DATA", struct.pack("<Iii", int(CellFlags.IS_INTERIOR), 0, 0))
            + _sub(b"AMBI", struct.pack("<12sf", bytes(range(12)), 0.5))
            + refs
        )
        original = _record(b"CELL", body)
        (cell,) = read_plugin(original)
        assert isinstance(cell, Cell)
        assert cell.name == "Balmora, Guild of Fighters"
        assert CellFlags.IS_INTERIOR in cell.data.cell_flags
        assert cell.atmosphere_data is not None
        assert cell.atmosphere_data.fog_density == pytest.approx(0.5)
        assert len(cell.references) == 2
        assert cell.references[0].id == "player_bed"
        assert cell.references[0].persistent is True
        assert cell.references[1].id == "com_chest_01"
        assert cell.references[1].owner == "some_owner"
        assert cell.references[1].temporary is True
        assert cell.references[1].translation == pytest.approx((1.0, 2.0, 3.0))
        assert write_plugin([cell]) == original

    def test_exterior_cell_with_region_and_moved_ref_round_trips(self) -> None:
        moved = (
            _sub(b"MVRF", struct.pack("<I", _pack(0, 5)))
            + _sub(b"CNDT", struct.pack("<ii", -2, 3))
            + _ref(0, 5, "guar_pack")
        )
        body = (
            _string_sub(b"NAME", "")
            + _sub(b"DATA", struct.pack("<Iii", 0, -2, 3))
            + _string_sub(b"RGNN", "Ascadian Isles Region")
            + _sub(b"NAM5", bytes([10, 20, 30, 40]))
            + _sub(b"WHGT", struct.pack("<f", -14.0))
            + moved
        )
        original = _record(b"CELL", body)
        (cell,) = read_plugin(original)
        assert cell.data.grid == (-2, 3)
        assert cell.region == "Ascadian Isles Region"
        assert cell.map_color == bytes([10, 20, 30, 40])
        assert cell.water_height == pytest.approx(-14.0)
        assert cell.references[0].moved_cell == (-2, 3)
        assert cell.references[0].persistent is True  # moved refs are persistent
        assert write_plugin([cell]) == original

    def test_deleted_reference_round_trips(self) -> None:
        ref = _sub(b"FRMR", struct.pack("<I", _pack(0, 9))) + _string_sub(b"NAME", "gone_ref")
        ref += b"DELE" + struct.pack("<II", 4, 0)
        body = (
            _string_sub(b"NAME", "Cell")
            + _sub(b"DATA", struct.pack("<Iii", int(CellFlags.IS_INTERIOR), 0, 0))
            + _sub(b"NAM0", struct.pack("<I", 1))
            + ref
        )
        original = _record(b"CELL", body)
        (cell,) = read_plugin(original)
        assert cell.references[0].deleted is True
        assert write_plugin([cell]) == original

    def test_reference_with_every_optional_field_round_trips(self) -> None:
        # All of a reference's optional subrecords, in the crate's save order.
        dodt = struct.pack("<6f", 10, 20, 30, 0, 0, 0)
        ref = (
            _sub(b"FRMR", struct.pack("<I", _pack(0, 7)))
            + _string_sub(b"NAME", "chest")
            + _sub(b"UNAM", struct.pack("<B", 1))  # blocked
            + _sub(b"XSCL", struct.pack("<f", 1.5))  # scale
            + _string_sub(b"ANAM", "owner_npc")
            + _string_sub(b"BNAM", "owner_var")
            + _string_sub(b"CNAM", "owner_fac")
            + _sub(b"INDX", struct.pack("<I", 3))  # faction rank
            + _string_sub(b"XSOL", "golden_saint")
            + _sub(b"XCHG", struct.pack("<I", 200))  # charge
            + _sub(b"INTV", struct.pack("<i", 45))  # health left
            + _sub(b"NAM9", struct.pack("<I", 5))  # object count
            + _sub(b"DODT", dodt)
            + _string_sub(b"DNAM", "Seyda Neen")
            + _sub(b"FLTV", struct.pack("<i", 50))  # lock level
            + _string_sub(b"KNAM", "chest_key")
            + _string_sub(b"TNAM", "fire_trap")
            + _data(1.0, 2.0, 3.0)
        )
        body = (
            _string_sub(b"NAME", "Loot Cell")
            + _sub(b"DATA", struct.pack("<Iii", int(CellFlags.IS_INTERIOR), 0, 0))
            + ref
        )
        original = _record(b"CELL", body)
        (cell,) = read_plugin(original)
        r = cell.references[0]
        assert r.blocked == 1
        assert r.scale == pytest.approx(1.5)
        assert r.owner == "owner_npc"
        assert r.owner_global == "owner_var"
        assert r.owner_faction == "owner_fac"
        assert r.owner_faction_rank == 3
        assert r.soul == "golden_saint"
        assert r.charge_left == 200
        assert r.health_left == 45
        assert r.object_count == 5
        assert r.destination is not None and r.destination.cell == "Seyda Neen"
        assert r.lock_level == 50
        assert r.key == "chest_key"
        assert r.trap == "fire_trap"
        assert write_plugin([cell]) == original

    def test_old_format_int_water_height_reads_as_float(self) -> None:
        # An INTV water height (esp 1.2) reads as a float and is rewritten as WHGT.
        body = (
            _string_sub(b"NAME", "Old Cell")
            + _sub(b"DATA", struct.pack("<Iii", int(CellFlags.IS_INTERIOR), 0, 0))
            + _sub(b"INTV", struct.pack("<i", -100))
        )
        (cell,) = read_plugin(_record(b"CELL", body))
        assert cell.water_height == pytest.approx(-100.0)
        (again,) = read_plugin(write_plugin([cell]))
        assert again.water_height == pytest.approx(-100.0)

    def test_deleted_cell_round_trips(self) -> None:
        body = _string_sub(b"NAME", "gone") + _sub(
            b"DATA", struct.pack("<Iii", int(CellFlags.IS_INTERIOR), 0, 0)
        )
        body += b"DELE" + struct.pack("<II", 4, 0)
        original = _record(b"CELL", body, flags=int(ObjectFlags.DELETED))
        (cell,) = read_plugin(original)
        assert ObjectFlags.DELETED in cell.flags
        assert write_plugin([cell]) == original

    def test_empty_cell_round_trips(self) -> None:
        body = _string_sub(b"NAME", "Empty") + _sub(
            b"DATA", struct.pack("<Iii", int(CellFlags.IS_INTERIOR), 0, 0)
        )
        original = _record(b"CELL", body)
        (cell,) = read_plugin(original)
        assert cell.references == []
        assert write_plugin([cell]) == original

    def test_bad_mvrf_without_cndt_is_refused(self) -> None:
        from wraithguard.esp import EspError

        body = (
            _string_sub(b"NAME", "x")
            + _sub(b"DATA", struct.pack("<Iii", 0, 0, 0))
            + _sub(b"MVRF", struct.pack("<I", 0))
            + _string_sub(b"NAME", "zzz")
        )
        with pytest.raises(EspError, match="expected CNDT"):
            read_plugin(_record(b"CELL", body))

    def test_unexpected_tag_is_refused(self) -> None:
        from wraithguard.esp import EspError

        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: CELL"):
            read_plugin(_record(b"CELL", body))


class TestReferenceEdges:
    def test_a_bare_reference_is_not_persistent_when_temporary(self) -> None:
        ref = Reference(temporary=True)
        assert ref.persistent is False
        assert Reference().persistent is True  # not temporary -> persistent

    def test_a_reference_ending_the_record_reads_as_empty(self) -> None:
        """A bare ``FRMR`` at the very end leaves the reference with no subrecords.

        ``Reference.load`` sees the reader already at the end, so its subrecord
        loop never runs and it returns a defaulted, transform-sanitised record.
        """
        body = (
            _string_sub(b"NAME", "Cell")
            + _sub(b"DATA", struct.pack("<Iii", int(CellFlags.IS_INTERIOR), 0, 0))
            + _sub(b"FRMR", struct.pack("<I", _pack(0, 1)))
        )
        (cell,) = read_plugin(_record(b"CELL", body))
        assert len(cell.references) == 1
        assert cell.references[0].id == ""

    def test_unexpected_reference_tag_is_refused(self) -> None:
        from wraithguard.esp import EspError

        body = (
            _string_sub(b"NAME", "Cell")
            + _sub(b"DATA", struct.pack("<Iii", int(CellFlags.IS_INTERIOR), 0, 0))
            + _sub(b"FRMR", struct.pack("<I", _pack(0, 1)))
            + _string_sub(b"NAME", "ref")
            + b"ZZZZ"
            + struct.pack("<I", 0)
        )
        with pytest.raises(EspError, match="Unexpected Tag: REFR"):
            read_plugin(_record(b"CELL", body))

    def test_master_reference_writes_default_scale_and_count(self) -> None:
        # From a master (mast_index != 0) even a default scale/count is written.
        ref = (
            _sub(b"FRMR", struct.pack("<I", _pack(1, 4)))
            + _string_sub(b"NAME", "master_ref")
            + _sub(b"XSCL", struct.pack("<f", 1.0))
            + _sub(b"NAM9", struct.pack("<I", 1))
            + _data(0.0, 0.0, 0.0)
        )
        body = (
            _string_sub(b"NAME", "Cell")
            + _sub(b"DATA", struct.pack("<Iii", int(CellFlags.IS_INTERIOR), 0, 0))
            + ref
        )
        original = _record(b"CELL", body)
        (cell,) = read_plugin(original)
        assert cell.references[0].mast_index == 1
        assert cell.references[0].scale == pytest.approx(1.0)
        assert cell.references[0].object_count == 1
        assert write_plugin([cell]) == original

    def test_plugin_reference_omits_default_scale_and_count(self) -> None:
        from wraithguard.esp import Writer

        # A plugin (mast_index 0) reference drops a default scale and stack count.
        writer = Writer()
        Reference(id="x", scale=1.0, object_count=1, mast_index=0).save(writer)
        written = writer.getvalue()
        assert b"XSCL" not in written
        assert b"NAM9" not in written

    def test_non_finite_transforms_and_scale_are_sanitised(self) -> None:
        inf = struct.pack("<f", float("inf"))
        ref = (
            _sub(b"FRMR", struct.pack("<I", _pack(0, 1)))
            + _string_sub(b"NAME", "bad")
            + _sub(b"XSCL", inf)
            + _sub(b"DATA", inf + struct.pack("<5f", 0, 0, 0, 0, 0))
        )
        body = (
            _string_sub(b"NAME", "Cell")
            + _sub(b"DATA", struct.pack("<Iii", int(CellFlags.IS_INTERIOR), 0, 0))
            + ref
        )
        (cell,) = read_plugin(_record(b"CELL", body))
        assert cell.references[0].translation[0] == 0.0  # inf zeroed
        assert cell.references[0].scale is None  # non-finite scale dropped
