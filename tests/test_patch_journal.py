"""Resolving a quest's journal stages the way the engine actually reads them.

A journal stage's identity is its INFO id, same as an ordinary dialogue
response's -- last-plugin-wins-by-id override applies unchanged. What differs
is what "order" means: a stage has no prev/next chain to walk, only a numeric
journal index, so the resolved chain is a sort, not a rebuilt linked list.
"""

from __future__ import annotations

from typing import Any, Final

from wraithguard.patch.journal import (
    Resolved,
    Stage,
    resolve_journals,
    resolve_quest,
    stages_by_quest,
)


def _info(
    info_id: str,
    index: int = 0,
    text: str = "",
    quest_state: str = "",
    flags: str = "",
) -> dict[str, Any]:
    """A minimal DialogueInfo record shaped like tes3conv's JSON."""
    return {
        "type": "DialogueInfo",
        "id": info_id,
        "text": text,
        "quest_state": quest_state,
        "flags": flags,
        "data": {"disposition": index},
    }


#: A two-stage quest, as one plugin would define it start to finish.
FARGOTH_QUEST: Final[list[dict[str, Any]]] = [
    {"type": "Dialogue", "id": "A1_1_FargothRing", "dialogue_type": "Journal"},
    _info("fargoth_1", 10, "Fargoth asked me to find his ring."),
    _info("fargoth_2", 20, "I found Fargoth's ring.", quest_state="Finished"),
]

#: An ordinary topic, to prove it is left for dialogue.responses_by_topic.
GREETING_TOPIC: Final[list[dict[str, Any]]] = [
    {"type": "Dialogue", "id": "Greeting 0", "dialogue_type": "Greeting"},
    _info("greet_1", 0, "Hello there."),
]


class TestStagesByQuest:
    def test_a_journal_topics_infos_are_grouped_under_its_id(self) -> None:
        got = stages_by_quest(FARGOTH_QUEST, "Morrowind.esm")
        assert [stage.info_id for stage in got["A1_1_FargothRing"]] == ["fargoth_1", "fargoth_2"]

    def test_a_non_journal_topics_infos_are_left_out(self) -> None:
        """Ordinary dialogue is responses_by_topic's job, not this one's."""
        assert stages_by_quest(GREETING_TOPIC) == {}

    def test_an_info_before_any_topic_is_dropped(self) -> None:
        """An orphan in the source is the source's problem, same call
        responses_by_topic makes."""
        records = [_info("orphan", 10)]
        assert stages_by_quest(records) == {}

    def test_the_index_comes_from_the_disposition_field(self) -> None:
        got = stages_by_quest(FARGOTH_QUEST)
        assert got["A1_1_FargothRing"][0].index == 10

    def test_a_missing_disposition_defaults_to_zero(self) -> None:
        records = [
            {"type": "Dialogue", "id": "Q", "dialogue_type": "Journal"},
            {"type": "DialogueInfo", "id": "stage", "data": {}},
        ]
        assert stages_by_quest(records)["Q"][0].index == 0

    def test_the_deleted_flag_is_read(self) -> None:
        records = [
            {"type": "Dialogue", "id": "Q", "dialogue_type": "Journal"},
            _info("stage", 10, flags="DELETED"),
        ]
        assert stages_by_quest(records)["Q"][0].deleted is True

    def test_the_plugin_name_is_stamped_on_every_stage(self) -> None:
        got = stages_by_quest(FARGOTH_QUEST, "Tribunal.esm")
        assert all(stage.plugin == "Tribunal.esm" for stage in got["A1_1_FargothRing"])


class TestResolvingOneQuest:
    def test_stages_come_out_sorted_by_index_not_file_order(self) -> None:
        stages = [Stage("b", index=20), Stage("a", index=10)]
        assert [r.info_id for r in resolve_quest(stages)] == ["a", "b"]

    def test_two_stages_sharing_an_index_are_both_kept(self) -> None:
        """Sharing a number is not automatically a conflict -- only reusing
        the same id is an edit of the same stage."""
        stages = [Stage("a", index=10), Stage("b", index=10)]
        got = resolve_quest(stages)
        assert [r.info_id for r in got] == ["a", "b"]  # stable tie-break: file order

    def test_a_later_plugin_reusing_the_id_overrides_it_wholesale(self) -> None:
        stages = [
            Stage("a", index=10, text="Original.", plugin="Morrowind.esm"),
            Stage("a", index=15, text="Patched.", plugin="Patch.esp"),
        ]
        got = resolve_quest(stages)
        assert len(got) == 1
        assert got[0].index == 15
        assert got[0].text == "Patched."

    def test_an_override_records_every_contributing_plugin(self) -> None:
        stages = [
            Stage("a", index=10, plugin="Morrowind.esm"),
            Stage("a", index=10, plugin="Patch.esp"),
        ]
        assert resolve_quest(stages)[0].plugins == ["Morrowind.esm", "Patch.esp"]

    def test_a_deletion_removes_the_stage(self) -> None:
        stages = [
            Stage("a", index=10, plugin="Morrowind.esm"),
            Stage("a", plugin="Patch.esp", deleted=True),
        ]
        assert resolve_quest(stages) == []

    def test_a_deletion_of_something_never_placed_is_a_harmless_tombstone(self) -> None:
        assert resolve_quest([Stage("a", deleted=True)]) == []

    def test_re_adding_a_deleted_id_is_a_fresh_insertion(self) -> None:
        """Same call topic_order makes: a later plugin re-adding the id after
        a deletion is not treated as overriding the deletion's own (blank)
        fields."""
        stages = [
            Stage("a", index=10, text="Original.", plugin="Morrowind.esm"),
            Stage("a", plugin="Remover.esp", deleted=True),
            Stage("a", index=99, text="Reinstated.", plugin="Restorer.esp"),
        ]
        got = resolve_quest(stages)
        assert len(got) == 1
        assert got[0].plugins == ["Restorer.esp"]  # not ["Morrowind.esm", "Restorer.esp"]

    def test_finished_reflects_the_quest_state(self) -> None:
        assert resolve_quest([Stage("a", quest_state="Finished")])[0].finished is True
        assert resolve_quest([Stage("a", quest_state="Restart")])[0].finished is False
        assert resolve_quest([Stage("a")])[0].finished is False

    def test_an_override_with_no_plugin_name_does_not_append_a_blank_entry(self) -> None:
        stages = [
            Stage("a", index=10, plugin="Morrowind.esm"),
            Stage("a", index=15, plugin=""),
        ]
        assert resolve_quest(stages)[0].plugins == ["Morrowind.esm"]

    def test_an_empty_quest_resolves_to_nothing(self) -> None:
        assert resolve_quest([]) == []


class TestResolvingAWholeLoadOrder:
    SOURCES: Final[dict[str, list[dict[str, Any]]]] = {"Morrowind.esm": FARGOTH_QUEST}
    ORDER: Final[list[str]] = ["Morrowind.esm"]

    def test_a_quest_is_resolved_across_its_defining_plugin(self) -> None:
        got = resolve_journals(self.SOURCES, self.ORDER)
        assert [r.info_id for r in got["A1_1_FargothRing"]] == ["fargoth_1", "fargoth_2"]

    def test_a_plugin_missing_from_sources_is_skipped_quietly(self) -> None:
        """Same call dialogue.shifts makes for a plugin the caller named but
        never actually read."""
        got = resolve_journals(self.SOURCES, ["Morrowind.esm", "NotScanned.esp"])
        assert "A1_1_FargothRing" in got

    def test_a_quest_left_with_no_surviving_stages_is_left_out_entirely(self) -> None:
        patch = [
            {"type": "Dialogue", "id": "A1_1_FargothRing", "dialogue_type": "Journal"},
            _info("fargoth_1", flags="DELETED"),
            _info("fargoth_2", flags="DELETED"),
        ]
        sources = {"Morrowind.esm": FARGOTH_QUEST, "Patch.esp": patch}
        got = resolve_journals(sources, ["Morrowind.esm", "Patch.esp"])
        assert "A1_1_FargothRing" not in got

    def test_a_second_plugin_adding_a_later_stage_extends_the_chain(self) -> None:
        addon = [
            {"type": "Dialogue", "id": "A1_1_FargothRing", "dialogue_type": "Journal"},
            _info("fargoth_3", 30, "I gave the ring back."),
        ]
        sources = {"Morrowind.esm": FARGOTH_QUEST, "Addon.esp": addon}
        got = resolve_journals(sources, ["Morrowind.esm", "Addon.esp"])
        assert [r.info_id for r in got["A1_1_FargothRing"]] == [
            "fargoth_1",
            "fargoth_2",
            "fargoth_3",
        ]

    def test_load_order_governs_which_definition_survives(self) -> None:
        earlier = [
            {"type": "Dialogue", "id": "Q", "dialogue_type": "Journal"},
            _info("stage", 10, "Earlier text."),
        ]
        later = [
            {"type": "Dialogue", "id": "Q", "dialogue_type": "Journal"},
            _info("stage", 10, "Later text."),
        ]
        sources = {"A.esp": earlier, "B.esp": later}
        got = resolve_journals(sources, ["A.esp", "B.esp"])
        assert got["Q"][0].text == "Later text."

        reversed_got = resolve_journals(sources, ["B.esp", "A.esp"])
        assert reversed_got["Q"][0].text == "Earlier text."


class TestResolvedFinished:
    def test_finished_is_true_only_for_the_finished_marker(self) -> None:
        assert Resolved("a", quest_state="Finished").finished is True
        assert Resolved("a", quest_state="Name").finished is False
        assert Resolved("a").finished is False
