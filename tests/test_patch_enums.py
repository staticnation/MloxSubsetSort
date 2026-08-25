"""The enum-variant table and the dropdown-option builder for typed values.

The dialog itself needs a display; this pins the pure logic behind it -- which
variants a field offers, and how the current value, the known enums, and the
plugins' own values combine into one de-duplicated, sensibly ordered list.
"""

from __future__ import annotations

from wraithguard.patch.enums import enum_options, value_options


class TestEnumOptions:
    def test_a_known_field_returns_its_variants(self) -> None:
        assert "Spell" in enum_options("data.spell_type")

    def test_the_leaf_name_is_what_matches_not_the_full_path(self) -> None:
        assert enum_options("spell_type") == enum_options("data.spell_type")

    def test_an_unknown_field_returns_empty(self) -> None:
        assert enum_options("data.some_custom_field") == ()


class TestValueOptions:
    def test_the_current_value_comes_first(self) -> None:
        out = value_options("Power", present=True, observed=[], path="data.spell_type")
        assert out[0] == "Power"

    def test_known_variants_and_observed_values_are_included(self) -> None:
        out = value_options(
            "Spell", present=True, observed=["MyCustomType"], path="data.spell_type"
        )
        assert "Ability" in out  # from the enum table
        assert "MyCustomType" in out  # a value a plugin actually uses

    def test_duplicates_are_dropped_and_order_kept(self) -> None:
        # "Spell" is both the current value and the first enum variant.
        out = value_options("Spell", present=True, observed=["Spell"], path="data.spell_type")
        assert out.count("Spell") == 1
        assert out[0] == "Spell"

    def test_an_unknown_string_field_offers_only_the_observed_values(self) -> None:
        out = value_options("A", present=True, observed=["A", "B"], path="name")
        assert out == ["A", "B"]

    def test_a_non_string_current_value_is_not_offered(self) -> None:
        # A number field never gets a string dropdown from its own value.
        assert value_options(5, present=True, observed=[], path="data.health") == []
