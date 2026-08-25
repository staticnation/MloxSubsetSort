"""The crate-derived field-type lookup and the flag-string helpers.

The generated table (``field_types.py``) is data; this pins the small pure logic
the dialog leans on -- reading a field's kind, an int's range, a flags set's
names, and round-tripping a `` | ``-joined flag string including unknown tokens.
"""

from __future__ import annotations

from wraithguard.patch.fieldtypes import (
    field_kind,
    flag_options,
    flags_name,
    int_bounds,
    join_flags,
    split_flags,
)


class TestFieldKind:
    def test_a_u16_field_is_a_bounded_int(self) -> None:
        assert field_kind("Weapon", "data.health") == "int:0:65535"

    def test_a_float_field(self) -> None:
        assert field_kind("Weapon", "data.weight") == "float"

    def test_an_enum_field(self) -> None:
        assert field_kind("Weapon", "data.weapon_type") == "enum"

    def test_a_flags_field_names_its_set(self) -> None:
        assert field_kind("Weapon", "data.flags") == "flags:WeaponFlags"

    def test_an_unknown_record_or_path_is_none(self) -> None:
        assert field_kind("Weapon", "data.nope") is None
        assert field_kind("NotARecord", "data.health") is None


class TestKindHelpers:
    def test_int_bounds_reads_the_range(self) -> None:
        assert int_bounds("int:0:65535") == (0, 65535)

    def test_int_bounds_is_none_for_other_kinds(self) -> None:
        assert int_bounds("float") is None

    def test_flags_name_and_options(self) -> None:
        assert flags_name("flags:ObjectFlags") == "ObjectFlags"
        assert "BLOCKED" in flag_options("ObjectFlags")

    def test_flags_name_is_none_for_other_kinds(self) -> None:
        assert flags_name("enum") is None


class TestFlagStrings:
    def test_split_separates_known_from_unknown(self) -> None:
        enabled, unknown = split_flags("AUTO_CALCULATE | 0xfffe", ["AUTO_CALCULATE", "OTHER"])
        assert enabled == ["AUTO_CALCULATE"]
        assert unknown == ["0xfffe"]

    def test_split_of_empty_is_nothing(self) -> None:
        assert split_flags("", ["A"]) == ([], [])

    def test_join_composes_and_preserves_unknown(self) -> None:
        assert join_flags(["A", "B"], ["0x1"]) == "A | B | 0x1"

    def test_join_of_nothing_is_empty(self) -> None:
        assert join_flags([], []) == ""

    def test_round_trip(self) -> None:
        known = ["MODIFIED", "BLOCKED"]
        enabled, unknown = split_flags("BLOCKED | 0x8", known)
        assert join_flags(enabled, unknown) == "BLOCKED | 0x8"
