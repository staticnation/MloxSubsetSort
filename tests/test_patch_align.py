"""Lining up list entries by identity instead of by position.

The failure this prevents is the loud one. A mod inserts a single item near the
top of a leveled list; compared by index, every entry after it reports as
changed, and the one real edit is buried in a hundred false ones. That is
indistinguishable from a broken tool, which is why the flat view compares these
fields whole rather than badly.

What identifies an entry is per-field and it is never the index: a reference by
its object instance, a leveled entry by item *and* level, an inventory entry by
item id because the count is what gets edited.
"""

from __future__ import annotations

from typing import Any

from wraithguard.patch.align import (
    Row,
    align,
    alignable_fields,
    identity,
    label_for,
)
from wraithguard.patch.status import ABSENT, ConflictAll, ConflictThis

PLUGINS = ["Base.esm", "Mod.esp"]


def labels(rows: list[Row]) -> list[str]:
    """The rows as names, for readable assertions."""
    return [row.label for row in rows]


class TestWhatIdentifiesAnEntry:
    """Per field, and never the index."""

    def test_a_reference_is_its_object_instance(self) -> None:
        """Stable across plugins by design; the id is not, two can share one."""
        entry = {"mast_index": 1, "refr_index": 7294, "id": "apelles matius"}
        assert identity("references", entry) == "1|7294"

    def test_a_levelled_entry_includes_its_level(self) -> None:
        """One item at two levels is two entries, not one edited twice."""
        assert identity("items", ["iron sword", 1]) == "iron sword @ 1"
        assert identity("items", ["iron sword", 20]) != identity("items", ["iron sword", 1])

    def test_an_inventory_entry_is_just_the_item(self) -> None:
        """The count is the value being edited, so it cannot be the identity."""
        assert identity("inventory", [5, "gold_001"]) == "gold_001"
        assert identity("inventory", [99, "gold_001"]) == "gold_001"

    def test_a_reaction_is_the_faction_it_is_toward(self) -> None:
        """The number is what mods change."""
        assert identity("reactions", {"faction": "Ashlanders", "reaction": -1}) == "Ashlanders"

    def test_an_unknown_object_falls_back_to_its_id(self) -> None:
        """Most records carry one, and it is nearly always the right answer."""
        assert identity("whatever", {"id": "thing", "value": 3}) == "thing"

    def test_anything_else_falls_back_to_its_content(self) -> None:
        """Equal entries align, unequal ones do not. It can only fail to spot
        that two entries are the same thing, never claim they are when they
        are not.
        """
        assert identity("odd", [1, 2, 3]) == identity("odd", [1, 2, 3])
        assert identity("odd", [1, 2, 3]) != identity("odd", [1, 2, 4])

    def test_a_reference_reads_as_its_object_not_its_index(self) -> None:
        """Nobody recognises 1|7294."""
        entry = {"mast_index": 1, "refr_index": 7294, "id": "apelles matius"}
        assert label_for("references", entry) == "apelles matius"

    def test_a_bare_string_entry_is_its_own_identity(self) -> None:
        """A plain string list entry is already the key."""
        assert identity("scripts", "my_script") == "my_script"

    def test_an_unserialisable_entry_falls_back_to_repr(self) -> None:
        """A value JSON cannot encode still yields a stable key via repr."""
        key = identity("odd", frozenset({1, 2}))
        assert key == identity("odd", frozenset({1, 2}))
        assert "frozenset" in key


class TestTheInsertionThatBreaksOrdinalDiffs:
    """The case the module exists for."""

    def test_inserting_one_entry_leaves_the_rest_alone(self) -> None:
        """Compared by index this would report every later entry as changed."""
        per = {
            "Base.esm": [["a", 1], ["b", 1], ["c", 1]],
            "Mod.esp": [["a", 1], ["new", 1], ["b", 1], ["c", 1]],
        }
        rows = align("items", per, PLUGINS)
        changed = [row.label for row in rows if row.overall is not ConflictAll.NO_CONFLICT]
        assert changed == ["new @ 1"]

    def test_the_new_entry_appears_where_the_mod_put_it(self) -> None:
        """Appending it to the bottom would hide it where nobody looks."""
        per = {
            "Base.esm": [["a", 1], ["b", 1]],
            "Mod.esp": [["a", 1], ["new", 1], ["b", 1]],
        }
        assert labels(align("items", per, PLUGINS)) == ["a @ 1", "new @ 1", "b @ 1"]

    def test_an_added_entry_is_absent_from_the_plugins_without_it(self) -> None:
        """Added and removed have to be visible as such, not as "differs"."""
        per = {"Base.esm": [["a", 1]], "Mod.esp": [["a", 1], ["new", 1]]}
        rows = align("items", per, PLUGINS)
        assert rows[1].values[0] is ABSENT
        assert rows[1].present == (False, True)

    def test_a_removed_entry_is_reported_too(self) -> None:
        """A mod deleting a leveled entry is a real and easily missed edit."""
        per = {"Base.esm": [["a", 1], ["gone", 1]], "Mod.esp": [["a", 1]]}
        rows = align("items", per, PLUGINS)
        assert rows[1].present == (True, False)


class TestEditsWithinAnEntry:
    """Same entry, different content."""

    def test_a_changed_count_is_a_change_to_that_entry(self) -> None:
        """And to nothing else."""
        per = {"Base.esm": [[1, "gold_001"]], "Mod.esp": [[99, "gold_001"]]}
        rows = align("inventory", per, PLUGINS)
        assert rows[0].overall is ConflictAll.OVERRIDE_BENIGN
        assert rows[0].per_plugin == (ConflictThis.MASTER, ConflictThis.OVERRIDE_WINS)

    def test_a_moved_reference_shows_as_one_edited_object(self) -> None:
        """Not as every object after it having moved."""
        base: dict[str, Any] = {"mast_index": 0, "refr_index": 1, "id": "urn", "z": 0}
        per = {
            "Base.esm": [base, {"mast_index": 0, "refr_index": 2, "id": "pot"}],
            "Mod.esp": [{**base, "z": 50}, {"mast_index": 0, "refr_index": 2, "id": "pot"}],
        }
        rows = align("references", per, PLUGINS)
        assert [row.overall for row in rows] == [
            ConflictAll.OVERRIDE_BENIGN,
            ConflictAll.NO_CONFLICT,
        ]

    def test_a_discarded_edit_is_still_reported_as_one(self) -> None:
        """The two-axis judgement applies per entry, as it does per field."""
        per = {
            "Base.esm": [[1, "gold_001"]],
            "Mid.esp": [[50, "gold_001"]],
            "Last.esp": [[99, "gold_001"]],
        }
        rows = align("inventory", per, ["Base.esm", "Mid.esp", "Last.esp"])
        assert rows[0].overall is ConflictAll.CONFLICT
        assert rows[0].per_plugin[1] is ConflictThis.CONFLICT_LOSES


class TestWhatIsNotAligned:
    """Being careful about the difference between silence and emptiness."""

    def test_a_plugin_without_the_field_contributes_nothing(self) -> None:
        """Not an empty list: "did not say" and "said it is empty" differ."""
        per: dict[str, Any] = {"Base.esm": [["a", 1]], "Mod.esp": None}
        rows = align("items", per, PLUGINS)
        assert rows[0].values[1] is ABSENT

    def test_an_empty_list_everywhere_produces_no_rows(self) -> None:
        """Nothing to line up is not an error."""
        assert align("items", {"Base.esm": [], "Mod.esp": []}, PLUGINS) == []

    def test_only_list_fields_are_offered(self) -> None:
        """A scalar has no entries, so the aligned view has nothing to show."""
        per = {"Base.esm": {"name": "x", "items": [["a", 1]]}}
        assert alignable_fields(["name", "items"], per) == ["items"]

    def test_a_fixed_tuple_is_not_a_repeated_field(self) -> None:
        """A landscape's grid is ``[-27, 6]`` -- list syntax, but a coordinate.

        The first version offered it, and lining a coordinate up entry by entry
        is meaningless. Pinned because the mistake is easy to make again: the
        test for "is a list" is not the test for "holds entries".
        """
        per = {"Base.esm": {"grid": [-27, 6], "translation": [1.0, 2.0, 3.0]}}
        assert alignable_fields(["grid", "translation"], per) == []

    def test_a_list_of_objects_is_a_repeated_field(self) -> None:
        """Fixed tuples are never objects, so this is safe without a name list."""
        per = {"Base.esm": {"effects": [{"magic_effect": "Light"}]}}
        assert alignable_fields(["effects"], per) == ["effects"]

    def test_a_known_repeated_field_of_pairs_is_still_offered(self) -> None:
        """Leveled entries and inventory are pairs, not objects."""
        per = {"Base.esm": {"items": [["a", 1]], "inventory": [[1, "b"]]}}
        assert alignable_fields(["items", "inventory"], per) == ["items", "inventory"]

    def test_an_empty_list_is_not_offered(self) -> None:
        """Otherwise the view opens on a field with nothing in it."""
        per = {"Base.esm": {"items": []}}
        assert alignable_fields(["items"], per) == []

    def test_a_field_only_one_plugin_has_is_still_offered(self) -> None:
        """That is a mod adding a whole list, which is worth seeing."""
        per: dict[str, Any] = {"Base.esm": {}, "Mod.esp": {"spells": ["fireball"]}}
        assert alignable_fields(["spells"], per) == ["spells"]


class TestOrderingAcrossSeveralPlugins:
    """Three or more lists merged into one column of rows."""

    def test_each_plugin_s_additions_land_in_its_own_order(self) -> None:
        """Two mods inserting in different places both read correctly."""
        per = {
            "Base.esm": [["a", 1], ["d", 1]],
            "One.esp": [["a", 1], ["b", 1], ["d", 1]],
            "Two.esp": [["a", 1], ["c", 1], ["d", 1]],
        }
        rows = labels(align("items", per, ["Base.esm", "One.esp", "Two.esp"]))
        assert rows.index("a @ 1") < rows.index("b @ 1") < rows.index("d @ 1")
        assert rows.index("a @ 1") < rows.index("c @ 1") < rows.index("d @ 1")

    def test_every_entry_appears_exactly_once(self) -> None:
        """A duplicated row would double-count in any tally built on this."""
        per = {
            "Base.esm": [["a", 1], ["b", 1]],
            "One.esp": [["b", 1], ["a", 1]],
        }
        rows = labels(align("items", per, ["Base.esm", "One.esp"]))
        assert sorted(rows) == ["a @ 1", "b @ 1"]

    def test_one_status_per_plugin_per_row(self) -> None:
        """The panel indexes into these by column, so a short tuple mislabels."""
        per = {"Base.esm": [["a", 1]], "One.esp": [["a", 1]], "Two.esp": [["a", 1]]}
        rows = align("items", per, ["Base.esm", "One.esp", "Two.esp"])
        assert len(rows[0].per_plugin) == 3
        assert len(rows[0].values) == 3


class TestTheOrderMergeIsLinear:
    """Weaving each plugin's order in once, rather than inserting one at a time.

    The first version inserted each new key into a shared list and rebuilt a
    key-to-index map afterwards so the next insertion knew where to go: O(n)
    per entry, O(n^2) overall. Invisible on an inventory of nine items; on a
    cell's references, where one exterior cell carries thousands, it was 3.1
    seconds for 8,000 entries against 2 milliseconds for the merge -- same
    result, 1,392 times slower.

    These pin the behaviour the rewrite had to preserve, plus the size that
    made it worth rewriting.
    """

    def _refs(self, count: int, extra: str | None = None, at: int = 0) -> list[dict[str, Any]]:
        """A cell's worth of references, optionally with one inserted."""
        rows = [{"mast_index": 0, "refr_index": n, "id": f"obj{n}"} for n in range(count)]
        if extra is not None:
            rows.insert(at, {"mast_index": 0, "refr_index": 10**6, "id": extra})
        return rows

    def test_a_large_cell_aligns_in_reasonable_time(self) -> None:
        """8,000 references is an ordinary big exterior cell, not a pathological
        case. If this ever takes seconds again, the quadratic is back.
        """
        import time

        base = self._refs(8000)
        edited = self._refs(8000, extra="inserted", at=2600)
        start = time.perf_counter()
        rows = align("references", {"A.esm": base, "B.esp": edited}, ["A.esm", "B.esp"])
        assert time.perf_counter() - start < 5.0
        assert len(rows) == 8001

    def test_the_inserted_entry_is_the_only_one_flagged_at_scale(self) -> None:
        """The whole point of aligning, held at a size where it matters."""
        base = self._refs(2000)
        edited = self._refs(2000, extra="inserted", at=700)
        rows = align("references", {"A.esm": base, "B.esp": edited}, ["A.esm", "B.esp"])
        changed = [r.label for r in rows if r.overall is not ConflictAll.NO_CONFLICT]
        assert changed == ["inserted"]

    def test_shared_entries_keep_the_established_order(self) -> None:
        """When a later plugin lists the same entries in a different order, the
        order already built stands -- otherwise each plugin would reshuffle the
        rows under the previous one and nothing would hold still.
        """
        per = {
            "A.esm": [["a", 1], ["b", 1], ["c", 1]],
            "B.esp": [["c", 1], ["a", 1]],
        }
        assert labels(align("items", per, ["A.esm", "B.esp"])) == ["a @ 1", "b @ 1", "c @ 1"]

    def test_a_plugin_that_only_removes_entries_changes_no_order(self) -> None:
        """A removal is an absence, not a reordering."""
        per = {"A.esm": [["a", 1], ["b", 1], ["c", 1]], "B.esp": [["a", 1], ["c", 1]]}
        rows = align("items", per, ["A.esm", "B.esp"])
        assert labels(rows) == ["a @ 1", "b @ 1", "c @ 1"]
        assert rows[1].present == (True, False)

    def test_three_plugins_each_inserting_somewhere_different(self) -> None:
        """Every addition lands where its own plugin put it."""
        per = {
            "A.esm": [["a", 1], ["z", 1]],
            "B.esp": [["a", 1], ["b", 1], ["z", 1]],
            "C.esp": [["a", 1], ["c", 1], ["z", 1]],
        }
        got = labels(align("items", per, ["A.esm", "B.esp", "C.esp"]))
        assert got[0] == "a @ 1"
        assert got[-1] == "z @ 1"
        assert set(got) == {"a @ 1", "b @ 1", "c @ 1", "z @ 1"}
