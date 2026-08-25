"""Parsing a typed patch value into the type the field expects.

A defined value must come back as the same JSON type the field already is, or
tes3conv cannot rebuild the plugin from it. These pin each branch of
``parse_field_value`` -- the type it targets, and the messages it refuses with.
"""

from __future__ import annotations

import pytest

from wraithguard.patch import PatchError, parse_field_value, parse_typed_value


class TestScalarTypes:
    def test_an_int_field_parses_a_whole_number(self) -> None:
        assert parse_field_value("125", current=100, present=True) == 125

    def test_a_float_field_parses_a_decimal(self) -> None:
        assert parse_field_value("12.5", current=1.0, present=True) == 12.5

    def test_a_string_field_is_taken_verbatim_including_spaces(self) -> None:
        assert parse_field_value("  a name  ", current="old", present=True) == "  a name  "

    def test_a_bool_field_parses_true_and_false_words(self) -> None:
        assert parse_field_value("true", current=False, present=True) is True
        assert parse_field_value("No", current=True, present=True) is False

    def test_bool_is_checked_before_int(self) -> None:
        # bool is an int subclass; a bool field must not fall through to int().
        assert parse_field_value("1", current=True, present=True) is True


class TestContainers:
    def test_a_list_field_wants_a_json_array(self) -> None:
        assert parse_field_value("[1, 2, 3]", current=[0], present=True) == [1, 2, 3]

    def test_a_group_field_wants_a_json_object(self) -> None:
        assert parse_field_value('{"a": 1}', current={"a": 0}, present=True) == {"a": 1}

    def test_an_absent_field_takes_a_free_json_literal(self) -> None:
        assert parse_field_value('"anything"', current=None, present=False) == "anything"

    def test_a_null_field_takes_a_free_json_literal(self) -> None:
        assert parse_field_value("42", current=None, present=True) == 42


class TestRefusals:
    def test_a_non_number_for_an_int_field_is_refused(self) -> None:
        with pytest.raises(PatchError, match="whole number"):
            parse_field_value("big", current=1, present=True)

    def test_a_non_number_for_a_float_field_is_refused(self) -> None:
        with pytest.raises(PatchError, match="not a number"):
            parse_field_value("big", current=1.0, present=True)

    def test_a_non_boolean_word_is_refused(self) -> None:
        with pytest.raises(PatchError, match="true/false"):
            parse_field_value("maybe", current=True, present=True)

    def test_a_scalar_for_a_list_field_is_refused(self) -> None:
        with pytest.raises(PatchError, match="is a list"):
            parse_field_value("5", current=[1], present=True)

    def test_a_scalar_for_a_group_field_is_refused(self) -> None:
        with pytest.raises(PatchError, match="is a group"):
            parse_field_value("5", current={"a": 1}, present=True)

    def test_invalid_json_for_a_container_is_refused(self) -> None:
        with pytest.raises(PatchError, match="not valid JSON"):
            parse_field_value("[1, 2", current=[0], present=True)


class TestTypedByKind:
    """``parse_typed_value`` trusts the crate kind, not a sample value."""

    def test_a_bounded_int_parses_within_range(self) -> None:
        assert parse_typed_value("125", "int:0:65535") == 125

    def test_an_out_of_range_int_is_refused(self) -> None:
        with pytest.raises(PatchError, match="from 0 to 255"):
            parse_typed_value("300", "int:0:255")

    def test_a_non_number_for_an_int_kind_is_refused(self) -> None:
        with pytest.raises(PatchError, match="whole number"):
            parse_typed_value("big", "int:0:255")

    def test_a_float_kind_parses_a_decimal(self) -> None:
        assert parse_typed_value("12.5", "float") == 12.5

    def test_a_bool_kind_parses_words(self) -> None:
        assert parse_typed_value("true", "bool") is True
        assert parse_typed_value("off", "bool") is False

    def test_an_enum_kind_is_a_verbatim_string(self) -> None:
        assert parse_typed_value("ShortBladeOneHand", "enum") == "ShortBladeOneHand"

    def test_a_flags_kind_is_a_verbatim_string(self) -> None:
        assert parse_typed_value("SILVER | 0x8", "flags:WeaponFlags") == "SILVER | 0x8"

    def test_a_str_kind_keeps_spaces(self) -> None:
        assert parse_typed_value("  keep  ", "str") == "  keep  "

    def test_a_list_kind_wants_a_json_array(self) -> None:
        assert parse_typed_value("[1, 2]", "list") == [1, 2]

    def test_a_scalar_for_a_list_kind_is_refused(self) -> None:
        with pytest.raises(PatchError, match="is a list"):
            parse_typed_value("5", "list")

    def test_an_absent_bounded_int_still_enforces_type_and_range(self) -> None:
        # The point of kind-parsing: no current value, yet 3.5 is still refused.
        with pytest.raises(PatchError, match="whole number"):
            parse_typed_value("3.5", "int:0:65535")
