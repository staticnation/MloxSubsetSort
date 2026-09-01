"""Rendering DIAL/INFO records as readable dialogue.

The tes3conv shapes here were confirmed by round-tripping real records through
the tes3conv binary (JSON -> .esp -> JSON), so the field names, the enum-name
strings and the adjacently-tagged ``value`` (``{"type": "Integer", ...}``) are
exactly what the tool sees in practice.
"""

from __future__ import annotations

import pytest

from wraithguard.tes3fields.dialogue import (
    DIAL_TYPE,
    INFO_TYPE,
    describe_dialogue,
    describe_filter,
    describe_info,
    describe_record,
    is_dialogue_record,
    script_tokens,
)


def _filter(
    ftype: str, *, function: str = "", comparison: str, ident: str = "", value: int
) -> dict:
    """One filter in tes3conv form, with an adjacently-tagged integer value."""
    return {
        "index": 0,
        "filter_type": ftype,
        "function": function,
        "comparison": comparison,
        "id": ident,
        "value": {"type": "Integer", "data": value},
    }


class TestIsDialogueRecord:
    """Recognising the two dialogue record types."""

    def test_info_and_dial_are_dialogue(self) -> None:
        """Both tes3conv dialogue type tags are recognised."""
        assert is_dialogue_record({"type": INFO_TYPE})
        assert is_dialogue_record({"type": DIAL_TYPE})

    def test_other_records_are_not(self) -> None:
        """A non-dialogue record, or a non-dict, is rejected."""
        assert not is_dialogue_record({"type": "Cell"})
        assert not is_dialogue_record("nope")  # type: ignore[arg-type]


class TestDescribeFilter:
    """One SCVR filter -> one English clause."""

    def test_a_function_filter_names_the_function(self) -> None:
        """A Function filter uses the tes3-crate name, prettified, and operator."""
        clause = describe_filter(
            _filter("Function", function="PcAxe", comparison="GreaterEqual", value=1)
        )
        assert clause == "PC Axe >= 1"

    def test_function_names_follow_the_crate_not_mwde(self) -> None:
        """Names are the tes3 crate's own (prettified), not MWDE's phrasing."""
        clause = describe_filter(
            _filter("Function", function="ReactionLow", comparison="Greater", value=0)
        )
        assert clause == "Reaction Low > 0"

    def test_player_gender_spells_out_the_value(self) -> None:
        """PcSex reads its 0/1 operand as male/female."""
        female = describe_filter(_filter("Function", function="PcSex", comparison="Equal", value=1))
        assert female is not None
        assert "female" in female

    def test_a_global_reads_the_variable_name(self) -> None:
        """A Global filter compares the named global, not a function."""
        clause = describe_filter(
            _filter("Global", comparison="Greater", ident="GameHour", value=18)
        )
        assert clause == "global GameHour > 18"

    def test_an_item_filter_reads_player_inventory(self) -> None:
        """An Item filter is phrased as a player-inventory count."""
        clause = describe_filter(_filter("Item", comparison="Greater", ident="gold_001", value=100))
        assert clause == "player inventory gold_001 > 100"

    def test_not_faction_collapses_to_a_membership_sentence(self) -> None:
        """The == 0 boolean form of NotFaction reads as 'is not a member'."""
        clause = describe_filter(
            _filter("NotFaction", comparison="Equal", ident="Thieves Guild", value=0)
        )
        assert clause == "NPC is not a member of faction Thieves Guild"

    def test_not_faction_true_form_reads_as_member(self) -> None:
        """The == 1 boolean form reads as 'is a member'."""
        clause = describe_filter(_filter("NotFaction", comparison="Equal", ident="Legion", value=1))
        assert clause == "NPC is a member of faction Legion"

    def test_dead_zero_reads_as_not_dead(self) -> None:
        """Dead == 0 is the common 'is not dead' condition."""
        clause = describe_filter(_filter("Dead", comparison="Equal", ident="Fargoth", value=0))
        assert clause == "NPC Fargoth is not dead"

    def test_an_unused_filter_is_skipped(self) -> None:
        """A None-type filter carries no condition."""
        assert describe_filter(_filter("None", comparison="Equal", value=0)) is None


class TestDescribeInfo:
    """A whole INFO record -> speaker line, conditions, response, result."""

    def test_a_spoken_line_reads_top_to_bottom(self) -> None:
        """Speaker context, disposition, gender and a function filter, then text."""
        record = {
            "type": INFO_TYPE,
            "id": "info1",
            "data": {
                "dialogue_type": "Topic",
                "disposition": 50,
                "speaker_rank": -1,
                "speaker_sex": "Female",
                "player_rank": -1,
            },
            "speaker_id": "Fargoth",
            "speaker_race": "",
            "speaker_class": "",
            "speaker_faction": "",
            "speaker_cell": "",
            "player_faction": "",
            "sound_path": "",
            "text": "Hello, friend.",
            "filters": [_filter("Function", function="PcAxe", comparison="GreaterEqual", value=1)],
            "script_text": "",
        }
        out = describe_info(record)
        assert out.splitlines()[0] == "Actor: Fargoth"
        assert "- If disposition is at least 50" in out
        assert "- If NPC gender is female" in out
        assert "- If PC Axe >= 1" in out
        assert 'Response: "Hello, friend."' in out

    def test_a_journal_entry_reads_as_a_quest_index(self) -> None:
        """A Journal record's disposition is a quest index, not a disposition."""
        record = {
            "type": INFO_TYPE,
            "id": "j1",
            "data": {
                "dialogue_type": "Journal",
                "disposition": 10,
                "speaker_rank": -1,
                "speaker_sex": "Any",
                "player_rank": -1,
            },
            "text": "I met Fargoth.",
            "quest_state": "Name",
            "filters": [],
            "script_text": "",
        }
        out = describe_info(record)
        assert out.splitlines()[0] == "<Journal>"
        assert "quest index is 10 (Name)" in out

    def test_no_speaker_filters_reads_as_anyone(self) -> None:
        """A spoken line with no speaker filters is said by <Anyone>."""
        record = {
            "type": INFO_TYPE,
            "id": "x",
            "data": {"dialogue_type": "Greeting", "disposition": 0, "speaker_sex": "Any"},
            "text": "Hello.",
            "filters": [],
        }
        assert describe_info(record).splitlines()[0] == "<Anyone>"

    def test_a_result_script_is_shown(self) -> None:
        """A non-empty result script is appended."""
        record = {
            "type": INFO_TYPE,
            "id": "x",
            "data": {"dialogue_type": "Topic", "speaker_sex": "Any"},
            "text": "Done.",
            "filters": [],
            "script_text": 'Journal "Background" 10',
        }
        assert 'Result: Journal "Background" 10' in describe_info(record)

    def test_a_non_info_record_renders_empty(self) -> None:
        """describe_info is a no-op for anything that is not an INFO record."""
        assert describe_info({"type": "Cell", "id": "x"}) == ""


class TestDescribeDialogueAndRecord:
    """The DIAL header and the type-dispatching front door."""

    def test_a_dial_shows_its_kind_and_name(self) -> None:
        """A DIAL topic renders as its kind and quoted name."""
        assert describe_dialogue(
            {"type": DIAL_TYPE, "id": "Background", "dialogue_type": "Topic"}
        ) == ('Topic "Background"')

    def test_describe_record_dispatches_on_type(self) -> None:
        """describe_record routes INFO and DIAL, and ignores the rest."""
        assert describe_record({"type": DIAL_TYPE, "id": "Greeting 0", "dialogue_type": "Greeting"})
        assert describe_record({"type": "Cell", "id": "x"}) == ""


class TestScriptTokens:
    """Tokenising a result script for syntax highlighting."""

    def test_tokens_reproduce_the_input(self) -> None:
        """Concatenating every token's text gives back the exact source."""
        src = 'Journal "Background" 10 ; note\nif ( x >= 1 )'
        tokens = script_tokens(src)
        assert "".join(text for _kind, text in tokens) == src

    def test_keywords_strings_numbers_and_comments_are_classified(self) -> None:
        """The token kinds line up with the syntax-colour tags."""
        tokens = script_tokens("set x to 5 ; go")
        kinds = {text: kind for kind, text in tokens}
        assert kinds["set"] == "keyword"
        assert kinds["to"] == "keyword"
        assert kinds["5"] == "number"
        assert kinds["; go"] == "comment"

    def test_a_quoted_string_is_one_token(self) -> None:
        """A string literal is not split on its inner spaces."""
        tokens = script_tokens('"Tel Aruhn"')
        assert ("string", '"Tel Aruhn"') in tokens

    def test_empty_source_yields_no_tokens(self) -> None:
        """An empty script tokenises to nothing."""
        assert script_tokens("") == []


class TestDescribeFilterVariableTypes:
    """Each variable/identity filter type reads as its own English clause."""

    @pytest.mark.parametrize(
        ("ftype", "comparison", "ident", "value", "expected"),
        [
            ("Global", "Equal", "gv", 1, "global gv = 1"),
            ("Local", "Equal", "lv", 1, "local lv = 1"),
            ("Journal", "GreaterEqual", "q", 10, "quest q >= 10"),
            ("Item", "GreaterEqual", "gold_001", 100, "player inventory gold_001 >= 100"),
            ("Dead", "Equal", "npc", 0, "NPC npc is not dead"),
            ("Dead", "Greater", "npc", 2, "NPC death count for npc > 2"),
            ("NotLocal", "Equal", "lv", 1, "not local lv = 1"),
            ("NotId", "Equal", "foo", 0, "NPC is not foo"),
            ("NotFaction", "Equal", "bar", 1, "NPC is a member of faction bar"),
            ("NotId", "Greater", "baz", 5, "NotId baz > 5"),
            ("Weird_New_Type", "Equal", "x", 1, "Weird_New_Type x = 1"),
        ],
    )
    def test_variable_filter_reads_as_a_clause(
        self, ftype: str, comparison: str, ident: str, value: int, expected: str
    ) -> None:
        """The described clause matches the type's own wording."""
        clause = describe_filter(_filter(ftype, comparison=comparison, ident=ident, value=value))
        assert clause == expected

    def test_a_function_name_without_camel_case_is_kept_verbatim(self) -> None:
        """A function name the CamelCase splitter cannot read is shown as-is."""
        clause = describe_filter(_filter("Function", function="___", comparison="Equal", value=1))
        assert clause is not None
        assert "___" in clause

    def test_an_overridden_function_name_uses_its_pretty_form(self) -> None:
        """A name in the override table renders with its curated spelling."""
        clause = describe_filter(
            _filter("Function", function="PcCorprus", comparison="Equal", value=1)
        )
        assert clause is not None
        assert "PC Corprus" in clause

    def test_a_bare_numeric_value_is_read_directly(self) -> None:
        """A value that is a plain number, not an adjacently-tagged object, works."""
        flt = {
            "index": 0,
            "filter_type": "Function",
            "function": "PcClothingModifier",
            "comparison": "Greater",
            "id": "",
            "value": 5,  # bare number, not {"type": ..., "data": ...}
        }
        clause = describe_filter(flt)
        assert clause is not None
        assert "5" in clause

    def test_a_whole_float_value_drops_its_trailing_zero(self) -> None:
        """A float like 3.0 renders as 3, not 3.0."""
        flt = {
            "index": 0,
            "filter_type": "Function",
            "function": "PcClothingModifier",
            "comparison": "Greater",
            "id": "",
            "value": {"type": "Float", "data": 3.0},
        }
        clause = describe_filter(flt)
        assert clause is not None
        assert "3.0" not in clause
        assert "3" in clause


class TestConditionLines:
    """The packed-DATA conditions of an INFO record."""

    def test_faction_rank_zero_reads_as_not_a_member(self) -> None:
        record = {"type": INFO_TYPE, "player_faction": "Legion", "data": {"player_rank": 0}}
        out = describe_info(record)
        assert "not a member of faction Legion" in out

    def test_a_positive_faction_rank_reads_as_at_least(self) -> None:
        record = {"type": INFO_TYPE, "player_faction": "Legion", "data": {"player_rank": 3}}
        assert "rank in faction Legion is at least 3" in describe_info(record)

    def test_a_faction_with_no_rank_reads_as_a_member(self) -> None:
        record = {"type": INFO_TYPE, "player_faction": "Legion", "data": {}}
        assert "is a member of faction Legion" in describe_info(record)

    def test_a_male_speaker_condition(self) -> None:
        record = {"type": INFO_TYPE, "data": {"speaker_sex": "Male"}}
        assert "NPC gender is male" in describe_info(record)

    def test_a_female_speaker_condition(self) -> None:
        record = {"type": INFO_TYPE, "data": {"speaker_sex": "Female"}}
        assert "NPC gender is female" in describe_info(record)

    def test_a_positive_speaker_rank_condition(self) -> None:
        record = {"type": INFO_TYPE, "data": {"speaker_rank": 2}}
        assert "NPC rank is at least 2" in describe_info(record)

    def test_non_dict_and_empty_filters_are_skipped(self) -> None:
        """A stray non-dict filter and one that yields no clause are both ignored."""
        record = {
            "type": INFO_TYPE,
            "data": {},
            "filters": [
                "not a dict",  # skipped: not a mapping
                _filter("None", comparison="Equal", value=0),  # yields no clause
                _filter("Item", comparison="Greater", ident="gold_001", value=100),
            ],
        }
        out = describe_info(record)
        assert "gold_001" in out  # only the real filter contributed a line


class TestDescribeDispatch:
    """The convenience front doors."""

    def test_describe_dialogue_ignores_a_non_dial_record(self) -> None:
        assert describe_dialogue({"type": INFO_TYPE}) == ""

    def test_describe_record_routes_info_and_dial_and_other(self) -> None:
        assert describe_record({"type": INFO_TYPE, "text": "hi"}).startswith("<")
        assert describe_record({"type": DIAL_TYPE, "id": "Background"}).startswith("Dialogue")
        assert describe_record({"type": "Static", "id": "x"}) == ""
