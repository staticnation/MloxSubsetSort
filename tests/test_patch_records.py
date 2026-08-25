"""Carrying a record into a patch without repointing every object in it.

A record patch works by the engine's own rule: last definition of a record
wins, whole. So a patch carries the chosen plugin's record verbatim and loads
last, and everything it does not carry still comes from the original mods. No
source file is opened for writing at any point.

The one thing that cannot be copied verbatim is a ``Cell``'s references.
``mast_index`` is a *position*, not a name -- ``0`` is the file being read and
``k >= 1`` is the ``k``-th master of that file. Moving the record into a patch
changes what position 0 means and renumbers everything after it. Copied
unchanged, every placed object in that cell comes to mean a different file, and
nothing reports it.

Measured on real plugins: ``Clean Solstheim_Castle_v1.1`` declares three
masters and uses indices 0-3, 11,972 references sitting at 0; ``Bloodmoon``
declares one master and all 26,473 of its references sit at 0.
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from wraithguard.patch import (
    GREETING,
    PatchError,
    Selection,
    carry_forward,
    collect,
    defining_plugins,
    dialogue_position_risk,
    index_map,
    master_names,
    needs_remapping,
    position_anchors,
    record_key,
    remap_references,
    required_masters,
    topic_kind,
)

#: A plugin shaped like the real one the indices were measured from.
CASTLE: Final[list[dict[str, Any]]] = [
    {
        "type": "Header",
        "masters": [
            ["Morrowind.esm", 79837557],
            ["Tribunal.esm", 4565686],
            ["Bloodmoon.esm", 9631798],
        ],
    },
    {
        "type": "Cell",
        "data": {"grid": [7, 22], "flags": "HAS_WATER"},
        "references": [
            {"mast_index": 0, "refr_index": 1, "id": "KO_ShipCaptain_DFell"},
            {"mast_index": 1, "refr_index": 2, "id": "light_com_candle_07_77"},
            {"mast_index": 3, "refr_index": 3, "id": "Ex_DE_ship"},
        ],
    },
    {"type": "Static", "id": "ex_common_house"},
]


class TestRecordIdentity:
    """Cells and landscapes have no id; everything else does."""

    def test_an_id_identifies_most_records(self) -> None:
        """The ordinary case."""
        assert record_key({"type": "Static", "id": "ex_common_house"}) == "ex_common_house"

    def test_a_cell_is_identified_by_its_grid(self) -> None:
        """An exterior cell has no id at all."""
        assert record_key(CASTLE[1]) == "(7, 22)"

    def test_a_top_level_grid_is_read_too(self) -> None:
        """Landscapes carry the grid directly rather than under ``data``."""
        assert record_key({"type": "Landscape", "grid": [-3, 12]}) == "(-3, 12)"

    def test_a_record_with_neither_has_no_key(self) -> None:
        """Better to have no key than a key that collides with another record."""
        assert record_key({"type": "Header"}) == ""


class TestTheMasterListIsRead:
    """The mapping depends on it, so reading it wrong is reading everything wrong."""

    def test_masters_come_back_in_order(self) -> None:
        """Order *is* the meaning: these are positions, not names."""
        assert master_names(CASTLE) == ["Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm"]

    def test_a_plugin_with_no_masters_is_fine(self) -> None:
        """Morrowind.esm itself depends on nothing."""
        assert master_names([{"type": "Header"}]) == []

    def test_no_header_is_not_a_crash(self) -> None:
        """A partial read should degrade, not explode."""
        assert master_names([{"type": "Static", "id": "x"}]) == []


class TestIndexMapping:
    """Zero means *this plugin*, and that is what moves."""

    def test_zero_becomes_the_source_plugin(self) -> None:
        """The source's own objects must point at the source."""
        patch = ["Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm", "Castle.esp"]
        mapping = index_map("Castle.esp", master_names(CASTLE), patch)
        assert mapping[0] == 4

    def test_each_master_moves_to_its_new_position(self) -> None:
        """The patch may declare them in a different order or with gaps."""
        patch = ["Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm", "Castle.esp"]
        mapping = index_map("Castle.esp", master_names(CASTLE), patch)
        assert (mapping[1], mapping[2], mapping[3]) == (1, 2, 3)

    def test_an_extra_master_in_the_patch_shifts_the_rest(self) -> None:
        """The case that makes copying verbatim wrong rather than merely untidy."""
        patch = ["Morrowind.esm", "Extra.esm", "Tribunal.esm", "Bloodmoon.esm", "Castle.esp"]
        mapping = index_map("Castle.esp", master_names(CASTLE), patch)
        assert mapping[2] == 3  # Tribunal moved from position 2 to 3
        assert mapping[0] == 5

    def test_case_does_not_matter(self) -> None:
        """Load orders and headers disagree about case constantly."""
        mapping = index_map("castle.ESP", ["MORROWIND.ESM"], ["Morrowind.esm", "Castle.esp"])
        assert mapping == {0: 2, 1: 1}

    def test_a_missing_master_is_refused(self) -> None:
        """Silently renumbering would repoint every reference that used it."""
        with pytest.raises(PatchError, match=r"not .*in the patch's master list"):
            index_map("Castle.esp", master_names(CASTLE), ["Morrowind.esm", "Castle.esp"])


class TestReferencesAreRewritten:
    """The whole point of the module."""

    def test_every_reference_is_renumbered(self) -> None:
        """0 -> the source's slot, and each master to its new position."""
        patch = ["Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm", "Castle.esp"]
        mapping = index_map("Castle.esp", master_names(CASTLE), patch)
        out = remap_references(CASTLE[1], mapping)
        assert [r["mast_index"] for r in out["references"]] == [4, 1, 3]

    def test_the_source_record_is_not_touched(self) -> None:
        """The viewer may still be showing it."""
        before = [r["mast_index"] for r in CASTLE[1]["references"]]
        remap_references(CASTLE[1], {0: 9, 1: 9, 3: 9})
        assert [r["mast_index"] for r in CASTLE[1]["references"]] == before

    def test_everything_else_survives(self) -> None:
        """A patch carries the record whole; only the indices may change."""
        mapping = {0: 4, 1: 1, 3: 3}
        out = remap_references(CASTLE[1], mapping)
        assert out["data"] == {"grid": [7, 22], "flags": "HAS_WATER"}
        assert [r["id"] for r in out["references"]] == [
            "KO_ShipCaptain_DFell",
            "light_com_candle_07_77",
            "Ex_DE_ship",
        ]

    def test_an_index_the_mapping_does_not_cover_is_refused(self) -> None:
        """The plugin is inconsistent; guessing would place objects at random."""
        with pytest.raises(PatchError, match="mast_index 7"):
            remap_references({"type": "Cell", "references": [{"mast_index": 7}]}, {0: 1})

    def test_a_record_without_references_passes_through(self) -> None:
        """Most record types have nothing to remap."""
        assert remap_references({"type": "Static", "id": "x"}, {0: 1}) == {
            "type": "Static",
            "id": "x",
        }

    def test_only_cells_are_treated_as_master_indexed(self) -> None:
        """Being conservative here would remap things that are not indices."""
        assert needs_remapping(CASTLE[1])
        assert not needs_remapping({"type": "Static", "id": "x"})
        assert not needs_remapping({"type": "Cell", "data": {"grid": [0, 0]}})


class TestCollecting:
    """Turning a selection into the records a patch will carry."""

    def _sources(self) -> dict[str, list[dict[str, Any]]]:
        """One source plugin, keyed by name."""
        return {"Castle.esp": CASTLE}

    def test_a_plain_record_is_taken_verbatim(self) -> None:
        """No difference is computed; the whole record is the patch."""
        got = collect(
            [Selection("Castle.esp", "Static", "ex_common_house")],
            self._sources(),
            ["Morrowind.esm", "Castle.esp"],
        )
        assert got == [{"type": "Static", "id": "ex_common_house"}]

    def test_a_cell_is_taken_with_its_references_remapped(self) -> None:
        """The case that needs the whole module."""
        patch = ["Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm", "Castle.esp"]
        got = collect([Selection("Castle.esp", "Cell", "(7, 22)")], self._sources(), patch)
        assert [r["mast_index"] for r in got[0]["references"]] == [4, 1, 3]

    def test_selection_order_is_kept(self) -> None:
        """The caller chose an order; a patch should not reshuffle it."""
        got = collect(
            [
                Selection("Castle.esp", "Static", "ex_common_house"),
                Selection("Castle.esp", "Cell", "(7, 22)"),
            ],
            self._sources(),
            ["Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm", "Castle.esp"],
        )
        assert [r["type"] for r in got] == ["Static", "Cell"]

    def test_an_unknown_plugin_is_refused(self) -> None:
        """Better than emitting a patch that quietly carries less than asked."""
        with pytest.raises(PatchError, match="no records were read"):
            collect([Selection("Gone.esp", "Static", "x")], self._sources(), [])

    def test_a_record_that_is_no_longer_there_is_refused(self) -> None:
        """The mod may have been updated between the scan and the patch."""
        with pytest.raises(PatchError, match="has no Static record"):
            collect(
                [Selection("Castle.esp", "Static", "vanished")],
                self._sources(),
                ["Morrowind.esm", "Castle.esp"],
            )


class TestRequiredMasters:
    """A patch that carries a plugin's record must declare that plugin."""

    def test_the_source_and_its_masters_are_all_declared(self) -> None:
        """Otherwise the patch describes edits to files that may not be loaded."""
        order = ["Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm", "Other.esp", "Castle.esp"]
        got = required_masters(
            [Selection("Castle.esp", "Cell", "(7, 22)")], {"Castle.esp": CASTLE}, order
        )
        assert got == ["Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm", "Castle.esp"]

    def test_load_order_decides_the_order(self) -> None:
        """These are positions; declaring them out of order changes meanings."""
        order = ["Bloodmoon.esm", "Morrowind.esm", "Tribunal.esm", "Castle.esp"]
        got = required_masters(
            [Selection("Castle.esp", "Cell", "(7, 22)")], {"Castle.esp": CASTLE}, order
        )
        assert got == order

    def test_a_contributor_missing_from_the_order_is_still_declared(self) -> None:
        """Omitting a real dependency is worse than declaring one out of order."""
        got = required_masters(
            [Selection("Castle.esp", "Static", "ex_common_house")],
            {"Castle.esp": CASTLE},
            ["Morrowind.esm"],
        )
        assert "Castle.esp" in got

    def test_extra_masters_are_declared_even_without_a_selection(self) -> None:
        """What carry_forward needs: a name required by nothing in `selections`."""
        got = required_masters([], {}, ["Morrowind.esm", "Tribunal.esm"], extra=["Tribunal.esm"])
        assert got == ["Tribunal.esm"]


class TestCarryingAnEarlierBuildForward:
    """Appending to a patch: reading its own previous build back in.

    Unlike :class:`TestCollecting`, the source here is not some other plugin --
    it is an earlier version of the very file about to be overwritten. That
    changes what index 0 means: the record already lives in the file being
    rewritten and stays there, so self-references need no remapping and the
    old file's name never has to appear in the new master list. Only indices
    into the *old header's own* masters move.
    """

    #: Shaped like a small earlier build: one master, one self-reference, one
    #: reference into that master, plus a record with nothing to remap.
    OLD_BUILD: Final[list[dict[str, Any]]] = [
        {"type": "Header", "masters": [["Tribunal.esm", 1]]},
        {
            "type": "Cell",
            "data": {"grid": [1, 1]},
            "references": [
                {"mast_index": 0, "refr_index": 1, "id": "patch_own_thing"},
                {"mast_index": 1, "refr_index": 2, "id": "from_tribunal"},
            ],
        },
        {"type": "GameSetting", "id": "sPatchedName", "value": "Whatever"},
    ]

    def test_the_header_is_dropped(self) -> None:
        """The new build makes its own; the old one is not a record to carry."""
        got = carry_forward(self.OLD_BUILD, ["Morrowind.esm", "Tribunal.esm"])
        assert "Header" not in [r["type"] for r in got]

    def test_a_self_reference_stays_zero(self) -> None:
        """The file's own objects are still the file's own objects."""
        got = carry_forward(self.OLD_BUILD, ["Morrowind.esm", "Tribunal.esm"])
        cell = next(r for r in got if r["type"] == "Cell")
        own = next(r for r in cell["references"] if r["id"] == "patch_own_thing")
        assert own["mast_index"] == 0

    def test_a_reference_into_an_old_master_moves_to_its_new_position(self) -> None:
        """Tribunal.esm sat at position 1 in the old header; it may not any more."""
        got = carry_forward(self.OLD_BUILD, ["Morrowind.esm", "Bloodmoon.esm", "Tribunal.esm"])
        cell = next(r for r in got if r["type"] == "Cell")
        moved = next(r for r in cell["references"] if r["id"] == "from_tribunal")
        assert moved["mast_index"] == 3

    def test_the_old_file_never_needs_to_be_in_the_new_master_list(self) -> None:
        """Declaring a patch as a master of its own replacement is circular."""
        # Neither "Wraithguard Patch.esp" nor any stand-in for the old file
        # itself appears here, and that is deliberate -- only what it depended
        # on does.
        got = carry_forward(self.OLD_BUILD, ["Morrowind.esm", "Tribunal.esm"])
        assert len(got) == len(self.OLD_BUILD) - 1  # everything but the header

    def test_a_record_with_no_references_passes_through_unchanged(self) -> None:
        """Nothing to remap, nothing to touch."""
        got = carry_forward(self.OLD_BUILD, ["Tribunal.esm"])
        setting = next(r for r in got if r["type"] == "GameSetting")
        assert setting == {"type": "GameSetting", "id": "sPatchedName", "value": "Whatever"}

    def test_a_missing_old_master_is_refused(self) -> None:
        """Silently dropping it would repoint every reference that used it."""
        with pytest.raises(PatchError, match=r"Tribunal\.esm"):
            carry_forward(self.OLD_BUILD, ["Morrowind.esm"])

    def test_a_record_this_session_re_decided_is_not_carried_forward(self) -> None:
        """Re-deciding replaces -- carrying the old answer too would leave the
        patch's own last-wins to pick between it and the new one.
        """
        got = carry_forward(
            self.OLD_BUILD, ["Tribunal.esm"], skip={("GameSetting", "sPatchedName")}
        )
        assert "GameSetting" not in [r["type"] for r in got]

    def test_records_not_skipped_are_unaffected_by_an_unrelated_skip(self) -> None:
        """Skip is keyed by type and id -- it must not drop everything."""
        got = carry_forward(self.OLD_BUILD, ["Tribunal.esm"], skip={("Static", "nothing_here")})
        assert len(got) == 2

    def test_relative_order_survives(self) -> None:
        """A dialogue response carried forward must still follow its topic --
        the old build already put it there, so this only has to not reshuffle.
        """
        old = [
            {"type": "Header", "masters": []},
            {"type": "Dialogue", "id": "Idle"},
            {"type": "DialogueInfo", "id": "idle_1", "prev_id": "", "next_id": ""},
        ]
        got = carry_forward(old, [])
        assert [r["type"] for r in got] == ["Dialogue", "DialogueInfo"]

    def test_an_empty_carry_forward_is_fine(self) -> None:
        """A fresh patch, or one with nothing worth keeping from before."""
        assert carry_forward([], ["Morrowind.esm"]) == []


class TestReplacingAChoice:
    """Choosing a winner twice for one record must replace, not accumulate.

    Two records with the same key in one patch would leave the patch's *own*
    last-wins to decide which the user gets -- so a second choice that looked
    like it had been applied might not have been. The window keeps its pending
    list on this rule; it is proved here because it is a data rule, not a
    drawing one.
    """

    def _pending(self) -> list[Selection]:
        """A pending list with one record already chosen."""
        return [Selection("A.esp", "Static", "rock")]

    def _add(self, pending: list[Selection], selection: Selection) -> list[Selection]:
        """Apply the window's replace-then-append rule."""
        pending[:] = [
            entry
            for entry in pending
            if not (entry.record_type == selection.record_type and entry.key == selection.key)
        ]
        pending.append(selection)
        return pending

    def test_a_second_choice_replaces_the_first(self) -> None:
        """One entry per record, carrying the latest choice."""
        pending = self._add(self._pending(), Selection("B.esp", "Static", "rock"))
        assert pending == [Selection("B.esp", "Static", "rock")]

    def test_a_different_record_is_added_alongside(self) -> None:
        """Only the same record is replaced."""
        pending = self._add(self._pending(), Selection("B.esp", "Static", "tree"))
        assert len(pending) == 2

    def test_the_same_key_in_another_type_is_a_different_record(self) -> None:
        """A Static and a Cell may legitimately share an id."""
        pending = self._add(self._pending(), Selection("B.esp", "Cell", "rock"))
        assert len(pending) == 2


class TestDialogueResponsesCarryTheirTopic:
    """A ``DialogueInfo`` is not a free-standing record.

    It carries no topic of its own: the engine attaches it to the last
    ``Dialogue`` it read, so a response's meaning comes from its position in
    the file. Carried into a patch alone, there is no topic for it to answer.

    Found in a patch a user actually built and uploaded. It contained one
    ``INFO`` and no ``DIAL`` -- the response *"Now, what was I going to do
    today?"*, voiced by ``Idl_IF005.mp3``, belonging to the ``Idle`` topic,
    with nothing in the file to say so. Verified on the source plugin: all 458
    of its responses follow a ``Dialogue``, none is orphaned.
    """

    TOPIC: Final[dict[str, Any]] = {"type": "Dialogue", "id": "Idle"}
    OTHER_TOPIC: Final[dict[str, Any]] = {"type": "Dialogue", "id": "Hello"}

    def _plugin(self) -> list[dict[str, Any]]:
        """A plugin shaped like a voice mod: topics, then their responses."""
        return [
            {"type": "Header", "masters": [["Morrowind.esm", 1]]},
            self.OTHER_TOPIC,
            {"type": "DialogueInfo", "id": "greeting_1"},
            self.TOPIC,
            {"type": "DialogueInfo", "id": "idle_1"},
            {"type": "DialogueInfo", "id": "idle_2"},
        ]

    def _sources(self) -> dict[str, list[dict[str, Any]]]:
        """That plugin, keyed by name."""
        return {"Voices.esp": self._plugin()}

    def test_the_topic_is_emitted_before_the_response(self) -> None:
        """Order is the whole mechanism; after would attach it to nothing."""
        got = collect(
            [Selection("Voices.esp", "DialogueInfo", "idle_1")],
            self._sources(),
            ["Morrowind.esm", "Voices.esp"],
        )
        assert [(r["type"], r["id"]) for r in got] == [
            ("Dialogue", "Idle"),
            ("DialogueInfo", "idle_1"),
        ]

    def test_the_right_topic_is_chosen(self) -> None:
        """The one *preceding* it, not the first or last in the plugin."""
        got = collect(
            [Selection("Voices.esp", "DialogueInfo", "greeting_1")],
            self._sources(),
            ["Morrowind.esm", "Voices.esp"],
        )
        assert got[0]["id"] == "Hello"

    def test_a_shared_topic_is_emitted_once(self) -> None:
        """Two responses to one topic do not need it twice."""
        got = collect(
            [
                Selection("Voices.esp", "DialogueInfo", "idle_1"),
                Selection("Voices.esp", "DialogueInfo", "idle_2"),
            ],
            self._sources(),
            ["Morrowind.esm", "Voices.esp"],
        )
        assert [r["type"] for r in got] == ["Dialogue", "DialogueInfo", "DialogueInfo"]

    def test_two_topics_each_get_their_own(self) -> None:
        """Responses to different topics need both, each before its own."""
        got = collect(
            [
                Selection("Voices.esp", "DialogueInfo", "greeting_1"),
                Selection("Voices.esp", "DialogueInfo", "idle_1"),
            ],
            self._sources(),
            ["Morrowind.esm", "Voices.esp"],
        )
        assert [r["id"] for r in got] == ["Hello", "greeting_1", "Idle", "idle_1"]

    def test_other_record_types_gain_nothing(self) -> None:
        """Only dialogue works this way; nothing else should sprout a parent."""
        sources = {"Voices.esp": [*self._plugin(), {"type": "Static", "id": "rock"}]}
        got = collect(
            [Selection("Voices.esp", "Static", "rock")],
            sources,
            ["Morrowind.esm", "Voices.esp"],
        )
        assert [r["type"] for r in got] == ["Static"]

    def test_a_response_with_no_topic_before_it_is_still_carried(self) -> None:
        """A malformed plugin should not lose the record the user chose."""
        sources = {
            "Odd.esp": [
                {"type": "Header", "masters": []},
                {"type": "DialogueInfo", "id": "orphan"},
            ]
        }
        got = collect([Selection("Odd.esp", "DialogueInfo", "orphan")], sources, ["Odd.esp"])
        assert [r["type"] for r in got] == ["DialogueInfo"]


class TestDialoguePositionIsReported:
    """Where a response falls in its topic is priority, and it is not promised.

    A ``DialogueInfo`` sits in a doubly-linked list -- ``prev_id`` /
    ``next_id`` -- and the engine uses the *first* matching response. Move one
    and an NPC says a different line.

    A patch keeps those links, but they routinely name responses in other
    files: 23 of 458 in one voice mod, and 2,791 of 6,573 -- 42% -- in Patch
    for Purists. Usually fine, because those files are still loaded. Not
    promisable, because another plugin may have rewritten the neighbourhood.
    So it is reported rather than refused: refusing every dialogue edit would
    make the feature useless, and saying nothing would hide the one thing worth
    testing in game.
    """

    def _info(self, key: str, prev: str = "", nxt: str = "") -> dict[str, Any]:
        """A response with the links that decide its position."""
        return {"type": "DialogueInfo", "id": key, "prev_id": prev, "next_id": nxt}

    def test_a_lone_response_is_reported(self) -> None:
        """Both neighbours live elsewhere."""
        carried = [self._info("b", prev="a", nxt="c")]
        notes = dialogue_position_risk(carried)
        assert len(notes) == 1
        assert "previous response" in notes[0]
        assert "next response" in notes[0]

    def test_carrying_the_neighbours_settles_it(self) -> None:
        """With the whole run carried, position is the patch's own business."""
        carried = [
            self._info("a", nxt="b"),
            self._info("b", prev="a", nxt="c"),
            self._info("c", prev="b"),
        ]
        assert dialogue_position_risk(carried) == []

    def test_only_the_loose_side_is_named(self) -> None:
        """Half-anchored is worth saying precisely."""
        carried = [self._info("a", nxt="b"), self._info("b", prev="a", nxt="elsewhere")]
        notes = dialogue_position_risk(carried)
        assert len(notes) == 1
        assert "next response" in notes[0]
        assert "previous response" not in notes[0]

    def test_a_response_at_the_end_of_a_topic_is_fine(self) -> None:
        """An empty link is not a link to somewhere else."""
        assert dialogue_position_risk([self._info("only")]) == []

    def test_other_record_types_are_not_reported(self) -> None:
        """Nothing else has a position to lose."""
        assert dialogue_position_risk([{"type": "Static", "id": "rock"}]) == []

    def test_every_note_is_said_once(self) -> None:
        """The service used to call this per source plugin, and the notes are a
        property of what is carried -- so three sources repeated each note three
        times. Pinned because a duplicated warning teaches people to skim.
        """
        carried = [self._info("b", prev="a", nxt="c")]
        sources = {"One.esp": carried, "Two.esp": [], "Three.esp": []}
        assert len(dialogue_position_risk(carried, sources)) == 1

    def test_a_neighbour_defined_once_is_named_as_its_holder(self) -> None:
        """Knowing which file holds the place is what makes the note testable."""
        carried = [self._info("b", prev="a")]
        sources = {"Anchors.esp": [self._info("a", nxt="b")]}
        assert "held in place by Anchors.esp" in dialogue_position_risk(carried, sources)[0]

    def test_a_contested_neighbour_is_called_out(self) -> None:
        """Two files defining one neighbour is the case that actually moves a
        line: whichever wins decides the position, and that is load order, not
        the patch.
        """
        carried = [self._info("b", prev="a")]
        sources = {"One.esp": [self._info("a")], "Two.esp": [self._info("a")]}
        note = dialogue_position_risk(carried, sources)[0]
        assert "defined by 2 files" in note
        assert "One.esp, Two.esp" in note

    def test_without_sources_the_note_still_says_something_true(self) -> None:
        """The argument is optional, so the fallback must not overclaim."""
        note = dialogue_position_risk([self._info("b", prev="a")])[0]
        assert "comes from another file" in note


class TestGreetingsAreCalledOutSeparately:
    """A greeting has no identifier to match on -- only its position.

    Greetings are numbered buckets, ``Greeting 0`` to ``Greeting 9``, read top
    down and matched on filters alone. Every instruction an author gives for
    them is a placement, and the guide's own worked examples say "place this at
    the top of the greetings" precisely because that is the mechanism. A
    greeting that moves down is a greeting that stops being said.
    """

    def _greeting(self, key: str, prev: str = "") -> list[dict[str, Any]]:
        """A greeting topic and one response in it."""
        return [
            {"type": "Dialogue", "id": "Greeting 5", "dialogue_type": "Greeting"},
            {"type": "DialogueInfo", "id": key, "prev_id": prev, "next_id": ""},
        ]

    def test_a_greeting_gets_the_sharper_wording(self) -> None:
        """The generic note understates it for this one kind of topic."""
        note = dialogue_position_risk(self._greeting("b", prev="a"))[0]
        assert "greeting" in note
        assert "stops being said" in note

    def test_an_ordinary_topic_keeps_the_generic_wording(self) -> None:
        """Overusing the strong wording would make it mean nothing."""
        carried: list[dict[str, Any]] = [
            {"type": "Dialogue", "id": "new shoes", "dialogue_type": "Topic"},
            {"type": "DialogueInfo", "id": "b", "prev_id": "a", "next_id": ""},
        ]
        note = dialogue_position_risk(carried)[0]
        assert "depends on those staying put" in note
        assert "stops being said" not in note

    def test_the_kind_is_read_from_a_nested_group_too(self) -> None:
        """tes3conv writes it flat; a nested ``data`` group is not assumed away."""
        assert topic_kind({"type": "Dialogue", "data": {"dialogue_type": "Greeting"}}) == GREETING

    def test_an_unknown_topic_is_not_guessed_at(self) -> None:
        """No kind means no claim about the kind."""
        assert topic_kind(None) == ""
        assert topic_kind({"type": "Dialogue", "id": "x"}) == ""


class TestPositionAnchorsAreFound:
    """The technique: drag the neighbours in, unedited, to fix a line's place.

    To insert a response between two existing ones the author brings those two
    into their plugin as well. Content untouched, only ``prev_id`` and
    ``next_id`` changed, so the plugin itself states where the new line goes.

    Measured over the 298 plugins in this corpus containing dialogue: 1,729
    responses are byte-identical to their master apart from those two links,
    and 1,711 of them -- 98% -- sit directly beside a response the same plugin
    added or edited. Tribunal and Bloodmoon do it 1,125 times between them.

    The hazard is that an anchor *looks* like an unedited record. ``tes3cmd``
    lists ``INFO`` among the types its clean command deletes when they duplicate
    a master, and its own manual warns the duplication is often deliberate.
    """

    def _info(self, key: str, prev: str = "", nxt: str = "") -> dict[str, Any]:
        """A response with the links that decide its position."""
        return {"type": "DialogueInfo", "id": key, "prev_id": prev, "next_id": nxt}

    def test_an_anchor_in_a_source_plugin_is_found(self) -> None:
        """The case the corpus says is 98% of them."""
        carried = [self._info("b", prev="a")]
        sources = {"Mod.esp": [self._info("a", nxt="b"), self._info("b", prev="a")]}
        assert position_anchors(carried, sources) == [("b", "a", "Mod.esp")]

    def test_both_sides_are_reported(self) -> None:
        """An inserted line has two neighbours, and the author moved both."""
        carried = [self._info("b", prev="a", nxt="c")]
        sources = {"Mod.esp": [self._info("a"), self._info("c")]}
        assert [entry[1] for entry in position_anchors(carried, sources)] == ["a", "c"]

    def test_a_carried_neighbour_is_not_an_anchor_to_report(self) -> None:
        """Carrying it makes the position the patch's own business."""
        carried = [self._info("a", nxt="b"), self._info("b", prev="a")]
        sources = {"Mod.esp": carried}
        assert position_anchors(carried, sources) == []

    def test_a_neighbour_no_source_defines_is_not_reported_here(self) -> None:
        """That is the other note's job; this one names a file or says nothing."""
        assert position_anchors([self._info("b", prev="a")], {"Mod.esp": []}) == []

    def test_every_holder_of_a_contested_anchor_is_named(self) -> None:
        """Two files holding one place is exactly what the user needs told."""
        carried = [self._info("b", prev="a")]
        sources = {"One.esp": [self._info("a")], "Two.esp": [self._info("a")]}
        assert [entry[2] for entry in position_anchors(carried, sources)] == ["One.esp", "Two.esp"]

    def test_which_plugins_define_a_response_is_reported_plainly(self) -> None:
        """The building block, kept public because the GUI needs it too."""
        sources = {"One.esp": [self._info("a")], "Two.esp": [self._info("a"), self._info("b")]}
        assert defining_plugins(sources) == {"a": ["One.esp", "Two.esp"], "b": ["Two.esp"]}
