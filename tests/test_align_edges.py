"""Defensive corners of the field-alignment helpers.

The happy paths are in ``test_patch_align``; these pin the identity fallbacks
for a non-string id, the duplicate-entry handling in ``align``, and the
``_merged_order`` weave when its inputs carry repeats (which the callers dedupe,
but the function must survive).
"""

from __future__ import annotations

from wraithguard.patch.align import _merged_order, align, identity, label_for


class TestIdentityFallbacks:
    def test_a_dict_with_a_non_string_id_falls_back_to_content(self) -> None:
        """An id that is not a usable string is ignored; the whole entry keys it."""
        key = identity("whatever", {"id": 123})
        assert key == identity("whatever", {"id": 123})  # stable
        assert key != identity("whatever", {"id": 124})  # content-sensitive

    def test_label_for_a_dict_with_an_empty_id_uses_content(self) -> None:
        """An empty id is not a label; the entry's content stands in."""
        label = label_for("whatever", {"id": ""})
        assert isinstance(label, str)
        assert label


class TestMergedOrderWithRepeats:
    def test_a_repeated_new_key_is_placed_once(self) -> None:
        """A new key appearing twice in one list is not placed twice."""
        # X is new (not in base), so it leads; the second X is skipped.
        assert _merged_order(["A"], ["X", "X"]) == ["X", "A"]

    def test_a_duplicated_base_tail_entry_is_placed_once(self) -> None:
        """A base list carrying a duplicate places each key a single time."""
        assert _merged_order(["A", "A"], []) == ["A"]

    def test_a_duplicate_before_an_anchor_is_skipped_during_catch_up(self) -> None:
        """Catching up to an anchor steps over a base entry already placed."""
        assert _merged_order(["A", "B", "A", "C"], ["C"]) == ["A", "B", "C"]


def test_align_counts_a_duplicated_entry_once() -> None:
    """Two entries in one plugin sharing an identity collapse to one row."""
    per = {"A.esp": [{"id": "x", "v": 1}, {"id": "x", "v": 2}]}
    rows = align("whatever", per, ["A.esp"])
    assert len(rows) == 1  # the duplicate did not add a second row
