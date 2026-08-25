"""Bulk field-merge enumeration: take one field from a source plugin, sweeping
every record it defines.

The heart of the bulk merge is pure -- given the records the scanner found and
the values the diff read, :func:`wraithguard.patch.bulk.bulk_field_choices`
decides which field takes to queue. These pin every rule that keeps the patch
minimal and correct, without a display.
"""

from __future__ import annotations

from wraithguard.patch import FieldChoice
from wraithguard.patch.bulk import (
    IDENTITY_FIELDS,
    BulkFieldDecision,
    BulkRecord,
    bulk_field_choices,
)


def _rec(
    record_type: str,
    key: str,
    plugins: tuple[str, ...],
    values: dict[str, dict[str, object]],
) -> BulkRecord:
    return BulkRecord(record_type=record_type, key=key, plugins=plugins, values=values)


class TestTakesFromSource:
    def test_takes_a_differing_field_from_the_source(self) -> None:
        """The base case: source defines the record, does not win, differs."""
        rec = _rec(
            "Weapon",
            "iron dagger",
            ("Base.esm", "Overhaul.esp"),
            {
                "Base.esm": {"data.weight": 5.0},
                "Overhaul.esp": {"data.weight": 9.0},
            },
        )
        out = bulk_field_choices([rec], "Base.esm", ["data.weight"])
        assert out == [
            BulkFieldDecision("Weapon", "iron dagger", FieldChoice("data.weight", "Base.esm"))
        ]

    def test_multiple_records_keep_record_then_field_order(self) -> None:
        """Decisions come out in records order, then field-paths order."""
        recs = [
            _rec(
                "Weapon",
                "a",
                ("Src.esp", "Win.esp"),
                {"Src.esp": {"x": 1, "y": 2}, "Win.esp": {"x": 9, "y": 9}},
            ),
            _rec(
                "Weapon",
                "b",
                ("Src.esp", "Win.esp"),
                {"Src.esp": {"x": 3, "y": 4}, "Win.esp": {"x": 9, "y": 9}},
            ),
        ]
        out = bulk_field_choices(recs, "Src.esp", ["x", "y"])
        assert [(d.key, d.choice.path) for d in out] == [
            ("a", "x"),
            ("a", "y"),
            ("b", "x"),
            ("b", "y"),
        ]
        assert all(d.choice.plugin == "Src.esp" for d in out)


class TestSkips:
    def test_skips_when_source_already_wins(self) -> None:
        """If the source loads last its value is live -- forcing it is a no-op."""
        rec = _rec(
            "Weapon",
            "a",
            ("Other.esp", "Src.esp"),
            {"Other.esp": {"x": 1}, "Src.esp": {"x": 2}},
        )
        assert bulk_field_choices([rec], "Src.esp", ["x"]) == []

    def test_skips_when_source_does_not_define_the_record(self) -> None:
        """No version to give -- nothing to take."""
        rec = _rec("Weapon", "a", ("A.esp", "B.esp"), {"A.esp": {"x": 1}, "B.esp": {"x": 2}})
        assert bulk_field_choices([rec], "Src.esp", ["x"]) == []

    def test_skips_when_values_already_match(self) -> None:
        """Equal to the winner already -- the load order yields it; no patch."""
        rec = _rec(
            "Weapon",
            "a",
            ("Src.esp", "Win.esp"),
            {"Src.esp": {"x": 7}, "Win.esp": {"x": 7}},
        )
        assert bulk_field_choices([rec], "Src.esp", ["x"]) == []

    def test_skips_when_the_source_lacks_the_field(self) -> None:
        """The source defines the record but not that field: nothing to give."""
        rec = _rec(
            "Weapon",
            "a",
            ("Src.esp", "Win.esp"),
            {"Src.esp": {"other": 1}, "Win.esp": {"x": 2}},
        )
        assert bulk_field_choices([rec], "Src.esp", ["x"]) == []

    def test_takes_a_field_the_winner_lacks_entirely(self) -> None:
        """Source has the field, winner does not: that is a real difference."""
        rec = _rec(
            "Weapon",
            "a",
            ("Src.esp", "Win.esp"),
            {"Src.esp": {"x": 5}, "Win.esp": {"other": 1}},
        )
        out = bulk_field_choices([rec], "Src.esp", ["x"])
        assert out == [BulkFieldDecision("Weapon", "a", FieldChoice("x", "Src.esp"))]

    def test_skips_records_with_no_plugins(self) -> None:
        """A degenerate record with an empty plugin list is ignored, not raised."""
        rec = _rec("Weapon", "a", (), {})
        assert bulk_field_choices([rec], "Src.esp", ["x"]) == []


class TestIdentityFields:
    def test_identity_fields_are_never_taken(self) -> None:
        """type/id/grid name the record; taking them would make a different one."""
        rec = _rec(
            "Weapon",
            "a",
            ("Src.esp", "Win.esp"),
            {
                "Src.esp": {"id": "SRC", "data.weight": 1.0},
                "Win.esp": {"id": "WIN", "data.weight": 2.0},
            },
        )
        out = bulk_field_choices([rec], "Src.esp", ["id", "data.weight"])
        assert [d.choice.path for d in out] == ["data.weight"]

    def test_only_identity_fields_requested_yields_nothing(self) -> None:
        rec = _rec("Cell", "0,0", ("Src.esp", "Win.esp"), {"Src.esp": {}, "Win.esp": {}})
        assert bulk_field_choices([rec], "Src.esp", ["data.grid", "type"]) == []

    def test_identity_set_mirrors_the_conflict_window(self) -> None:
        """Kept in step with the GUI's own guard and merge.IDENTITY."""
        expected = {"type", "id", "grid", "data.grid"}
        assert expected == IDENTITY_FIELDS


class TestListValues:
    def test_list_valued_fields_compare_by_equality(self) -> None:
        """Unhashable values (lists) still compare, so differing lists are taken."""
        rec = _rec(
            "Container",
            "crate",
            ("Src.esp", "Win.esp"),
            {"Src.esp": {"items": [["gold", 10]]}, "Win.esp": {"items": [["gold", 5]]}},
        )
        out = bulk_field_choices([rec], "Src.esp", ["items"])
        assert out == [BulkFieldDecision("Container", "crate", FieldChoice("items", "Src.esp"))]

    def test_equal_lists_are_skipped(self) -> None:
        rec = _rec(
            "Container",
            "crate",
            ("Src.esp", "Win.esp"),
            {"Src.esp": {"items": [["gold", 10]]}, "Win.esp": {"items": [["gold", 10]]}},
        )
        assert bulk_field_choices([rec], "Src.esp", ["items"]) == []
