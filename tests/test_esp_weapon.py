"""The ESP library's byte layer and its first record, the weapon.

These pin two things at once: the framing foundation every record will share --
the sixteen-byte record header, the tag/size subrecords, the null-terminated
strings -- and that :class:`~wraithguard.esp.Weapon` reads and writes ``WEAP``
faithfully. A record built by hand, framed the way ``tests/test_land_native.py``
frames one (the on-disk layout, independent of this library), is read back to
equal fields; and any plugin's bytes, read and written again, come back byte for
byte -- the property the whole port is verified by.
"""

from __future__ import annotations

import struct

import pytest

from wraithguard.esp import (
    EspError,
    Reader,
    UnknownRecord,
    Weapon,
    Writer,
    read_plugin,
    write_plugin,
)
from wraithguard.esp.enums import WeaponType
from wraithguard.esp.flags import ObjectFlags, WeaponFlags


def _string_sub(tag: bytes, text: str) -> bytes:
    """A string subrecord as the format stores it: tag, u32 length, bytes+null."""
    body = text.encode("cp1252") + b"\x00"
    return tag + struct.pack("<I", len(body)) + body


def _wpdt(data: bytes) -> bytes:
    """A ``WPDT`` subrecord: tag, u32 size (32), the 32-byte block."""
    return b"WPDT" + struct.pack("<I", len(data)) + data


def _weapon_record(subrecords: bytes, flags: int = 0) -> bytes:
    """Frame weapon subrecords as an on-disk record: 16-byte header then body.

    Header is tag, body-size, a zero padding word, and the object flags; the
    body-size counts only the subrecords, not the flags word -- exactly the
    layout the library must read.
    """
    return (
        b"WEAP"
        + struct.pack("<I", len(subrecords))
        + struct.pack("<I", 0)
        + struct.pack("<I", flags)
        + subrecords
    )


def _sample_wpdt() -> bytes:
    """A filled 32-byte ``WPDT`` block with distinct values in every field."""
    return struct.pack(
        "<fIHHffHBBBBBBI",
        3.5,  # weight
        250,  # value
        int(WeaponType.AxeTwoHand),  # weapon_type (u16)
        180,  # health
        1.25,  # speed
        1.5,  # reach
        7,  # enchantment
        10,  # chop_min
        20,  # chop_max
        11,  # slash_min
        21,  # slash_max
        12,  # thrust_min
        22,  # thrust_max
        int(WeaponFlags.SILVER),  # flags
    )


class TestByteLayer:
    def test_string_round_trips_with_null_terminator(self) -> None:
        writer = Writer()
        writer.string("Daedric Dagger")
        raw = writer.getvalue()
        # length prefix includes the terminator
        assert struct.unpack_from("<I", raw)[0] == len("Daedric Dagger") + 1
        assert Reader(raw).string() == "Daedric Dagger"

    def test_empty_string_is_length_one_and_a_null(self) -> None:
        writer = Writer()
        writer.string("")
        assert writer.getvalue() == struct.pack("<I", 1) + b"\x00"
        assert Reader(writer.getvalue()).string() == ""

    def test_reading_past_the_end_raises(self) -> None:
        with pytest.raises(EspError, match="past end"):
            Reader(b"\x01\x02").u32()

    def test_bound_stops_a_reader_at_the_slice(self) -> None:
        reader = Reader(b"AAAA" + b"BBBB")
        inner = reader.bound(4)
        assert inner.raw(4) == b"AAAA"
        assert inner.at_end
        assert reader.raw(4) == b"BBBB"


class TestWeaponRecord:
    def test_reads_fields_and_data(self) -> None:
        body = (
            _string_sub(b"NAME", "daedric_dagger")
            + _string_sub(b"MODL", "w\\daedric_dagger.nif")
            + _string_sub(b"FNAM", "Daedric Dagger")
            + _wpdt(_sample_wpdt())
            + _string_sub(b"SCRI", "someScript")
            + _string_sub(b"ITEX", "w\\tx_daedric_dagger.dds")
            + _string_sub(b"ENAM", "someEnchant")
        )
        (weapon,) = read_plugin(_weapon_record(body))
        assert isinstance(weapon, Weapon)
        assert weapon.id == "daedric_dagger"
        assert weapon.mesh == "w\\daedric_dagger.nif"
        assert weapon.name == "Daedric Dagger"
        assert weapon.script == "someScript"
        assert weapon.icon == "w\\tx_daedric_dagger.dds"
        assert weapon.enchanting == "someEnchant"
        assert weapon.data.weight == pytest.approx(3.5)
        assert weapon.data.value == 250
        assert weapon.data.weapon_type is WeaponType.AxeTwoHand
        assert weapon.data.health == 180
        assert weapon.data.flags is WeaponFlags.SILVER

    def test_round_trips_byte_for_byte(self) -> None:
        body = (
            _string_sub(b"NAME", "daedric_dagger")
            + _string_sub(b"MODL", "w\\daedric_dagger.nif")
            + _string_sub(b"FNAM", "Daedric Dagger")
            + _wpdt(_sample_wpdt())
            + _string_sub(b"SCRI", "someScript")
            + _string_sub(b"ITEX", "w\\tx_daedric_dagger.dds")
            + _string_sub(b"ENAM", "someEnchant")
        )
        original = _weapon_record(body)
        assert write_plugin(read_plugin(original)) == original

    def test_empty_optional_strings_are_omitted_on_save(self) -> None:
        # Only NAME and WPDT: mesh/name/script/icon/enchanting empty.
        body = _string_sub(b"NAME", "rusty_spoon") + _wpdt(_sample_wpdt())
        original = _weapon_record(body)
        (weapon,) = read_plugin(original)
        assert weapon.mesh == "" and weapon.name == ""
        assert write_plugin([weapon]) == original

    def test_wrong_wpdt_size_is_refused(self) -> None:
        bad = _string_sub(b"NAME", "x") + b"WPDT" + struct.pack("<I", 16) + b"\x00" * 16
        with pytest.raises(EspError, match="WPDT size"):
            read_plugin(_weapon_record(bad))

    def test_deleted_flag_and_dele_subrecord_round_trip(self) -> None:
        body = (
            _string_sub(b"NAME", "gone")
            + _wpdt(_sample_wpdt())
            + b"DELE"
            + struct.pack("<I", 4)
            + struct.pack("<I", 0)
        )
        original = _weapon_record(body, flags=int(ObjectFlags.DELETED))
        (weapon,) = read_plugin(original)
        assert ObjectFlags.DELETED in weapon.flags
        assert write_plugin([weapon]) == original

    def test_unexpected_subrecord_tag_is_refused(self) -> None:
        body = _string_sub(b"NAME", "x") + _wpdt(_sample_wpdt()) + b"ZZZZ" + struct.pack("<I", 0)
        with pytest.raises(EspError, match="Unexpected Tag: WEAP"):
            read_plugin(_weapon_record(body))

    def test_unknown_enum_value_coerces_to_default(self) -> None:
        data = bytearray(_sample_wpdt())
        struct.pack_into("<H", data, 8, 999)  # weapon_type out of range
        body = _string_sub(b"NAME", "weird") + _wpdt(bytes(data))
        (weapon,) = read_plugin(_weapon_record(body))
        assert weapon.data.weapon_type is WeaponType.ShortBladeOneHand


class TestUnknownRecord:
    def test_unknown_tag_is_preserved_verbatim(self) -> None:
        # A LUAL record (OpenMW Lua config) tes3conv refuses; here it survives.
        subrecords = _string_sub(b"NAME", "some_id") + b"LUAL" + struct.pack("<I", 3) + b"abc"
        record_bytes = (
            b"LUAL"
            + struct.pack("<I", len(subrecords))
            + struct.pack("<I", 0)
            + struct.pack("<I", 0)
            + subrecords
        )
        (record,) = read_plugin(record_bytes)
        assert isinstance(record, UnknownRecord)
        assert record.wire_tag == b"LUAL"
        assert write_plugin([record]) == record_bytes
