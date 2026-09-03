"""Merging one record down out of several, field by field.

Carrying a whole record settles a conflict by picking a side. Sometimes neither
side is right -- one mod fixed the script, another retextured the mesh -- and
what you want is a record with both. That is merging down, as TES3Edit and
TES3 Conflict Solver do it.

The trap is the same one whole-record patching has, arriving by a different
door. ``mast_index`` is a position in *a particular plugin's* master list, so a
``references`` list taken from one plugin while the rest of the record comes
from another must be renumbered against **its own** plugin. Using the base
record's mapping repoints every object in the cell at a different file.
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from wraithguard.patch import PatchError
from wraithguard.patch.merge import (
    FieldChoice,
    FieldValue,
    Merge,
    describe,
    merge_record,
    set_at,
    value_at,
)

#: The plugin the reference indices were measured from.
CASTLE: Final[list[dict[str, Any]]] = [
    {
        "type": "Header",
        "masters": [["Morrowind.esm", 1], ["Tribunal.esm", 1], ["Bloodmoon.esm", 1]],
    },
    {
        "type": "Cell",
        "data": {"grid": [7, 22], "flags": "HAS_WATER"},
        "references": [
            {"mast_index": 0, "id": "castle_own_thing"},
            {"mast_index": 1, "id": "vanilla_thing"},
            {"mast_index": 3, "id": "bloodmoon_thing"},
        ],
    },
]

#: A second plugin editing the same cell, with a *different* master list -- the
#: whole reason the remap has to follow the source.
OTHER: Final[list[dict[str, Any]]] = [
    {"type": "Header", "masters": [["Morrowind.esm", 1], ["Bloodmoon.esm", 1]]},
    {
        "type": "Cell",
        "data": {"grid": [7, 22], "flags": "RESTING_IS_ILLEGAL"},
        "region": "Felsaad Coast",
        "references": [
            {"mast_index": 0, "id": "other_own_thing"},
            {"mast_index": 2, "id": "bloodmoon_thing"},
        ],
    },
]

#: What the patch declares. Order is meaning: these are positions.
PATCH: Final[list[str]] = [
    "Morrowind.esm",
    "Tribunal.esm",
    "Bloodmoon.esm",
    "Castle.esp",
    "Other.esp",
]

SOURCES: Final[dict[str, list[dict[str, Any]]]] = {"Castle.esp": CASTLE, "Other.esp": OTHER}


def merged(*choices: FieldChoice) -> dict[str, Any]:
    """Merge the shared cell with ``Castle.esp`` as the base."""
    return merge_record("Castle.esp", "Cell", "(7, 22)", choices, SOURCES, PATCH)


class TestReadingAndWritingPaths:
    """Dotted paths, as the diff panel labels them."""

    def test_a_nested_value_is_found(self) -> None:
        """``data.flags`` is how the panel names it."""
        assert value_at(CASTLE[1], "data.flags") == ("HAS_WATER", True)

    def test_absence_is_distinct_from_none(self) -> None:
        """``None`` is a legitimate field value; missing is not the same thing."""
        assert value_at(CASTLE[1], "region") == (None, False)
        assert value_at({"region": None}, "region") == (None, True)

    def test_a_path_through_a_non_dict_is_absent(self) -> None:
        """Not a crash: a malformed record should read as missing."""
        assert value_at({"data": 5}, "data.grid") == (None, False)

    def test_writing_creates_missing_groups(self) -> None:
        """A field the base lacks can still be merged in."""
        record: dict[str, Any] = {}
        set_at(record, "data.flags", "X")
        assert record == {"data": {"flags": "X"}}

    def test_writing_through_a_value_is_refused(self) -> None:
        """Replacing a value with a structure would corrupt the record."""
        with pytest.raises(PatchError, match="not a group of fields"):
            set_at({"data": 5}, "data.grid", [0, 0])


class TestMergingFields:
    """The point: a record neither plugin wrote, that both would recognise."""

    def test_a_chosen_field_comes_from_the_chosen_plugin(self) -> None:
        """The ordinary case."""
        assert merged(FieldChoice("data.flags", "Other.esp"))["data"]["flags"] == (
            "RESTING_IS_ILLEGAL"
        )

    def test_unchosen_fields_stay_with_the_base(self) -> None:
        """A merge is a set of departures, not a rewrite."""
        assert merged(FieldChoice("data.flags", "Other.esp"))["data"]["grid"] == [7, 22]

    def test_a_field_the_base_lacks_can_be_taken(self) -> None:
        """Castle has no region; Other does."""
        assert merged(FieldChoice("region", "Other.esp"))["region"] == "Felsaad Coast"

    def test_choosing_the_base_changes_nothing(self) -> None:
        """So a caller may pass every displayed field without filtering."""
        assert merged(FieldChoice("data.flags", "Castle.esp")) == merged()

    def test_the_sources_are_not_modified(self) -> None:
        """The diff panel may still be showing them."""
        merged(FieldChoice("references", "Other.esp"), FieldChoice("region", "Other.esp"))
        assert [r["mast_index"] for r in CASTLE[1]["references"]] == [0, 1, 3]
        assert [r["mast_index"] for r in OTHER[1]["references"]] == [0, 2]


class TestReferencesFollowTheirOwnSource:
    """The failure this module exists to prevent."""

    def test_the_base_s_references_use_the_base_s_mapping(self) -> None:
        """Castle: 0 -> its own slot 4, Morrowind 1 -> 1, Bloodmoon 3 -> 3."""
        assert [r["mast_index"] for r in merged()["references"]] == [4, 1, 3]

    def test_taken_references_use_the_other_plugin_s_mapping(self) -> None:
        """Other declares two masters, so its indices mean different files.

        Other: 0 is Other itself -> slot 5; 2 is *its* second master,
        Bloodmoon -> slot 3. Under Castle's mapping 0 would become 4, pointing
        at Castle.esp -- a file that has nothing to do with those objects.
        """
        out = merged(FieldChoice("references", "Other.esp"))
        assert [r["mast_index"] for r in out["references"]] == [5, 3]

    def test_the_wrong_mapping_would_be_visibly_different(self) -> None:
        """Pins the distinction, so a regression cannot look like a no-op."""
        base = [r["mast_index"] for r in merged()["references"]]
        taken = [
            r["mast_index"] for r in merged(FieldChoice("references", "Other.esp"))["references"]
        ]
        assert base[0] != taken[0]

    def test_one_plugins_mapping_is_built_only_once(self) -> None:
        """Two reference choices from the same plugin reuse its cached index map."""
        # Both choices point at Other.esp, so mapping_for('Other.esp') is asked
        # for twice; the second call must reuse what the first built.
        out = merged(FieldChoice("references", "Other.esp"), FieldChoice("references", "Other.esp"))
        assert [r["mast_index"] for r in out["references"]] == [5, 3]


class TestARecordWithoutReferences:
    """A merged record that has no reference list needs no remapping."""

    def test_a_reference_free_record_merges_without_remapping(self) -> None:
        sources = {
            "P.esp": [
                {"type": "Header", "masters": [["Morrowind.esm", 1]]},
                {"type": "GameSetting", "id": "sMyName", "value": "Base"},
            ],
            "Q.esp": [
                {"type": "Header", "masters": [["Morrowind.esm", 1]]},
                {"type": "GameSetting", "id": "sMyName", "value": "Other"},
            ],
        }
        out = merge_record(
            "P.esp",
            "GameSetting",
            "sMyName",
            (FieldChoice("value", "Q.esp"),),
            sources,
            ["Morrowind.esm", "P.esp", "Q.esp"],
        )
        assert out["value"] == "Other"
        assert "references" not in out


class TestWhatIsRefused:
    """Guessing here writes a record no author produced."""

    def test_identity_cannot_be_taken_from_elsewhere(self) -> None:
        """That makes a different record, not a merged one."""
        with pytest.raises(PatchError, match="says which record this is"):
            merged(FieldChoice("data.grid", "Other.esp"))

    def test_the_type_cannot_be_taken_either(self) -> None:
        """A Cell that claims to be a Static is not a merge."""
        with pytest.raises(PatchError, match="says which record this is"):
            merged(FieldChoice("type", "Other.esp"))

    def test_a_field_the_chosen_plugin_lacks_is_refused(self) -> None:
        """Ambiguous: delete the field, or a misread of the panel?"""
        with pytest.raises(PatchError, match="has no region"):
            merge_record(
                "Other.esp",
                "Cell",
                "(7, 22)",
                [FieldChoice("region", "Castle.esp")],
                SOURCES,
                PATCH,
            )

    def test_an_unknown_plugin_is_refused(self) -> None:
        """Better than a patch that quietly carries the base unchanged."""
        with pytest.raises(PatchError, match="no records were read"):
            merged(FieldChoice("data.flags", "Gone.esp"))

    def test_a_record_the_plugin_does_not_define_is_refused(self) -> None:
        """It may have been updated since the scan."""
        with pytest.raises(PatchError, match="has no Cell record"):
            merge_record("Castle.esp", "Cell", "(0, 0)", [], SOURCES, PATCH)


class TestDescribing:
    """A user should see what they are about to write, not a count."""

    def test_each_taken_field_is_named(self) -> None:
        """Two lines for two departures."""
        lines = describe(
            [FieldChoice("data.flags", "Other.esp"), FieldChoice("region", "Other.esp")],
            "Castle.esp",
        )
        assert lines == ["data.flags: from Other.esp", "region: from Other.esp"]

    def test_base_choices_are_not_listed(self) -> None:
        """They are not departures."""
        assert describe([FieldChoice("data.flags", "Castle.esp")], "Castle.esp") == [
            "nothing taken from elsewhere; this is Castle.esp's record whole"
        ]


class TestWholeAndMergedAreExclusive:
    """A record may be taken whole or merged, never both.

    Carrying it twice would leave the *patch's own* last-wins to decide which
    version the player gets -- so the choice the user made last in the window
    might not be the one that reaches the game. The window enforces this by
    dropping the other kind whenever one is chosen; the service refuses if a
    caller manages it anyway.
    """

    def test_the_service_refuses_both(self) -> None:
        """A caller that has not enforced it must not slip through."""
        from pathlib import Path

        from wraithguard.patch import Merge, Selection
        from wraithguard.patch.service import PatchServiceError, build_record_patch

        merge = Merge("Cell", "(7, 22)", "Castle.esp", (FieldChoice("data.flags", "Other.esp"),))
        with pytest.raises(PatchServiceError, match="both taken whole and merged"):
            build_record_patch(
                [Selection("Castle.esp", "Cell", "(7, 22)")],
                SOURCES,
                PATCH,
                dict.fromkeys(PATCH, 1),
                "tes3conv",
                Path("unused.esp"),
                merges=[merge],
                dry_run=True,
            )

    def test_a_merge_alone_is_fine(self) -> None:
        """The ordinary case: no whole-record choice for that record."""
        from pathlib import Path

        from wraithguard.patch import Merge
        from wraithguard.patch.service import build_record_patch

        merge = Merge("Cell", "(7, 22)", "Castle.esp", (FieldChoice("data.flags", "Other.esp"),))
        result = build_record_patch(
            [],
            SOURCES,
            PATCH,
            dict.fromkeys(PATCH, 1),
            "tes3conv",
            Path("unused.esp"),
            merges=[merge],
            dry_run=True,
        )
        assert result.records == 1

    def test_a_merge_declares_every_plugin_it_reads(self) -> None:
        """Both the base and each field's source must be masters.

        Without the field's source in the list, its references cannot be
        renumbered and the record would be refused at write time -- after the
        user had already chosen it.
        """
        from pathlib import Path

        from wraithguard.patch import Merge
        from wraithguard.patch.service import build_record_patch

        merge = Merge("Cell", "(7, 22)", "Castle.esp", (FieldChoice("references", "Other.esp"),))
        result = build_record_patch(
            [],
            SOURCES,
            PATCH,
            dict.fromkeys(PATCH, 1),
            "tes3conv",
            Path("unused.esp"),
            merges=[merge],
            dry_run=True,
        )
        assert "Castle.esp" in result.masters
        assert "Other.esp" in result.masters


class TestDefinedValues:
    """Typing a value directly -- a number or string no plugin in the conflict uses."""

    def test_a_typed_value_is_written_verbatim(self) -> None:
        """The literal lands exactly as given, over whatever the base had."""
        assert merged(FieldValue("data.flags", "MY_FLAG"))["data"]["flags"] == "MY_FLAG"

    def test_a_typed_value_can_fill_a_field_the_base_lacks(self) -> None:
        """Castle has no region; the user supplies one no plugin wrote."""
        assert merged(FieldValue("region", "Nowhere"))["region"] == "Nowhere"

    def test_a_literal_and_a_taken_field_coexist_in_one_merge(self) -> None:
        """Take one field from a plugin, type another, in the same record."""
        out = merged(FieldChoice("region", "Other.esp"), FieldValue("data.flags", "SET"))
        assert out["region"] == "Felsaad Coast"
        assert out["data"]["flags"] == "SET"

    def test_a_literal_reference_list_is_taken_as_typed_not_reindexed(self) -> None:
        """It belongs to no plugin's master list, so it is written as given."""
        literal = [{"mast_index": 0, "id": "typed_thing"}]
        out = merged(FieldValue("references", literal))
        assert out["references"] == literal

    def test_the_sources_are_not_modified_by_a_literal(self) -> None:
        """A deep copy is written, so editing the patch cannot reach the mods."""
        literal: list[dict[str, Any]] = [{"mast_index": 0, "id": "x"}]
        out = merged(FieldValue("references", literal))
        out["references"][0]["id"] = "changed"
        assert literal[0]["id"] == "x"

    def test_a_typed_identity_field_is_refused(self) -> None:
        """Retyping id/type/grid makes a different record, not a merged one."""
        with pytest.raises(PatchError, match="says which record this is"):
            merged(FieldValue("data.grid", [0, 0]))

    def test_describe_names_a_typed_value(self) -> None:
        """The builder shows what will be written, literals included."""
        assert describe([FieldValue("data.flags", 7)], "Castle.esp") == ["data.flags: set to 7"]

    def test_a_literal_adds_no_master_to_the_merge(self) -> None:
        """A typed value sources nothing, so it needs no plugin in the masters."""
        merge = Merge("Cell", "(7, 22)", "Castle.esp", (FieldValue("data.flags", "X"),))
        assert merge.plugins == {"Castle.esp"}
