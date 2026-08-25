"""Armor and clothing -- records carrying a list of biped-object groups.

The biped-object group is the interesting part: an ``INDX`` slot index then up
to two body-part meshes read by look-ahead (``BNAM`` male, ``CNAM`` female),
either, both or neither present. A slot with one mesh, one with both, and one
with none must all round-trip byte-for-byte, and the reader must not run a slot's
look-ahead into the next subrecord.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import Armor, BipedObject, Clothing, read_plugin, write_plugin
from wraithguard.esp.enums import ArmorType, BipedObjectType, ClothingType
from wraithguard.esp.flags import ObjectFlags


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


def _indx(slot: int) -> bytes:
    """An INDX subrecord: one byte of biped-object type."""
    return _sub(b"INDX", struct.pack("<B", slot))


class TestArmor:
    def test_cuirass_with_two_slot_meshes_round_trips(self) -> None:
        aodt = struct.pack("<IfIIII", int(ArmorType.Cuirass), 20.0, 500, 400, 10, 30)
        slot1 = (
            _indx(int(BipedObjectType.Chest))
            + _string_sub(b"BNAM", "a\\male_cuirass.nif")
            + _string_sub(b"CNAM", "a\\female_cuirass.nif")
        )
        slot2 = _indx(int(BipedObjectType.RightPauldron)) + _string_sub(b"BNAM", "a\\male_rp.nif")
        body = (
            _string_sub(b"NAME", "iron_cuirass")
            + _string_sub(b"MODL", "a\\cuirass.nif")
            + _string_sub(b"FNAM", "Iron Cuirass")
            + _string_sub(b"SCRI", "cuirassScript")
            + _sub(b"AODT", aodt)
            + _string_sub(b"ITEX", "t\\cuirass.dds")
            + slot1
            + slot2
            + _string_sub(b"ENAM", "cuirass_ench")
        )
        original = _record(b"ARMO", body)
        (armor,) = read_plugin(original)
        assert isinstance(armor, Armor)
        assert armor.data.armor_type is ArmorType.Cuirass
        assert armor.data.armor_rating == 30
        assert len(armor.biped_objects) == 2
        assert armor.biped_objects[0].biped_object_type is BipedObjectType.Chest
        assert armor.biped_objects[0].male_bodypart == "a\\male_cuirass.nif"
        assert armor.biped_objects[0].female_bodypart == "a\\female_cuirass.nif"
        assert armor.biped_objects[1].male_bodypart == "a\\male_rp.nif"
        assert armor.biped_objects[1].female_bodypart == ""
        assert armor.enchanting == "cuirass_ench"
        assert write_plugin([armor]) == original

    def test_female_only_slot_round_trips(self) -> None:
        aodt = struct.pack("<IfIIII", int(ArmorType.Helmet), 2.0, 50, 100, 0, 5)
        slot = _indx(int(BipedObjectType.Head)) + _string_sub(b"CNAM", "a\\female_helm.nif")
        body = _string_sub(b"NAME", "helm") + _sub(b"AODT", aodt) + slot
        original = _record(b"ARMO", body)
        (armor,) = read_plugin(original)
        assert armor.biped_objects[0].male_bodypart == ""
        assert armor.biped_objects[0].female_bodypart == "a\\female_helm.nif"
        assert write_plugin([armor]) == original

    def test_slot_with_no_meshes_round_trips(self) -> None:
        aodt = struct.pack("<IfIIII", int(ArmorType.Boots), 5.0, 40, 80, 0, 8)
        body = (
            _string_sub(b"NAME", "boots")
            + _sub(b"AODT", aodt)
            + _indx(int(BipedObjectType.RightFoot))
        )
        original = _record(b"ARMO", body)
        (armor,) = read_plugin(original)
        assert armor.biped_objects[0].male_bodypart == ""
        assert armor.biped_objects[0].female_bodypart == ""
        assert write_plugin([armor]) == original

    def test_unexpected_tag_is_refused(self) -> None:
        from wraithguard.esp import EspError

        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: ARMO"):
            read_plugin(_record(b"ARMO", body))

    def test_deleted_armor_round_trips(self) -> None:
        aodt = struct.pack("<IfIIII", int(ArmorType.Shield), 8.0, 90, 120, 0, 12)
        body = (
            _string_sub(b"NAME", "gone") + _sub(b"AODT", aodt) + b"DELE" + struct.pack("<II", 4, 0)
        )
        original = _record(b"ARMO", body, flags=int(ObjectFlags.DELETED))
        (armor,) = read_plugin(original)
        assert ObjectFlags.DELETED in armor.flags
        assert write_plugin([armor]) == original


class TestClothing:
    def test_shirt_with_slots_round_trips(self) -> None:
        ctdt = struct.pack("<IfHH", int(ClothingType.Shirt), 1.0, 15, 0)
        slot = (
            _indx(int(BipedObjectType.Chest))
            + _string_sub(b"BNAM", "c\\male_shirt.nif")
            + _string_sub(b"CNAM", "c\\female_shirt.nif")
        )
        body = (
            _string_sub(b"NAME", "common_shirt")
            + _string_sub(b"MODL", "c\\shirt.nif")
            + _string_sub(b"FNAM", "Common Shirt")
            + _sub(b"CTDT", ctdt)
            + _string_sub(b"SCRI", "shirtScript")
            + _string_sub(b"ITEX", "t\\shirt.dds")
            + slot
            + _string_sub(b"ENAM", "shirt_ench")
        )
        original = _record(b"CLOT", body)
        (clothing,) = read_plugin(original)
        assert isinstance(clothing, Clothing)
        assert clothing.data.clothing_type is ClothingType.Shirt
        assert clothing.data.value == 15
        assert clothing.script == "shirtScript"
        assert clothing.enchanting == "shirt_ench"
        assert clothing.biped_objects[0].female_bodypart == "c\\female_shirt.nif"
        assert write_plugin([clothing]) == original

    def test_unexpected_tag_is_refused(self) -> None:
        from wraithguard.esp import EspError

        body = _string_sub(b"NAME", "x") + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: CLOT"):
            read_plugin(_record(b"CLOT", body))

    def test_deleted_clothing_round_trips(self) -> None:
        ctdt = struct.pack("<IfHH", int(ClothingType.Ring), 0.0, 0, 0)
        body = (
            _string_sub(b"NAME", "gone") + _sub(b"CTDT", ctdt) + b"DELE" + struct.pack("<II", 4, 0)
        )
        original = _record(b"CLOT", body, flags=int(ObjectFlags.DELETED))
        (clothing,) = read_plugin(original)
        assert ObjectFlags.DELETED in clothing.flags
        assert write_plugin([clothing]) == original


class TestBipedObjectDefaults:
    def test_default_is_the_crate_default(self) -> None:
        assert BipedObject().biped_object_type is BipedObjectType.default()
