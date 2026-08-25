"""Finding Journal/SetJournalIndex call sites, and linking them to the stages they set.

A heuristic scan, not a parser -- no notion of which if/else branch a call
sits in, and honest about that. What it does guarantee: only a call that
actually survives override resolution is reported, whether that override
happened in journal.py's Stage/Resolved model (an INFO's result script) or in
this module's own smaller version of the same rule (a standalone Script).
"""

from __future__ import annotations

import base64
import struct
from typing import Any, Final

from wraithguard.patch.journal import Resolved
from wraithguard.patch.journal_scripts import (
    Attachment,
    Effect,
    JournalCall,
    attach,
    calls_from_dialogue,
    calls_from_scripts,
    calls_from_stages,
    calls_in_text,
    calls_in_text_with_context,
    effects_from_dialogue,
    effects_from_scripts,
    effects_from_stages,
    statements_in_text_with_context,
)

# ---------------------------------------------------------------------------
# calls_in_text
# ---------------------------------------------------------------------------


class TestCallsInText:
    def test_a_journal_call_is_found(self) -> None:
        assert calls_in_text('Journal "A1_1_FargothRing", 10') == [
            ("Journal", "A1_1_FargothRing", 10)
        ]

    def test_a_setjournalindex_call_is_found(self) -> None:
        assert calls_in_text('SetJournalIndex "A1_1_FargothRing", 20') == [
            ("SetJournalIndex", "A1_1_FargothRing", 20)
        ]

    def test_function_name_matching_is_case_insensitive(self) -> None:
        """MWScript itself is not case-sensitive about function names."""
        assert calls_in_text('JOURNAL "Q", 5') == [("Journal", "Q", 5)]
        assert calls_in_text('journal "Q", 5') == [("Journal", "Q", 5)]
        assert calls_in_text('setjournalindex "Q", 5') == [("SetJournalIndex", "Q", 5)]

    def test_getjournalindex_is_a_read_and_is_ignored(self) -> None:
        """A query is not a place a stage gets set."""
        assert calls_in_text('if ( GetJournalIndex "Q" >= 10 )\nendif\n') == []

    def test_multiple_calls_in_one_script_are_all_found(self) -> None:
        script = 'Journal "Q", 10\nJournal "Q", 20\nSetJournalIndex "Other", 5\n'
        assert calls_in_text(script) == [
            ("Journal", "Q", 10),
            ("Journal", "Q", 20),
            ("SetJournalIndex", "Other", 5),
        ]

    def test_a_decimal_index_is_read_as_an_int(self) -> None:
        assert calls_in_text('Journal "Q", 10.0') == [("Journal", "Q", 10)]

    def test_a_negative_index_parses(self) -> None:
        assert calls_in_text('Journal "Q", -1') == [("Journal", "Q", -1)]

    def test_an_incomplete_call_with_no_string_is_skipped(self) -> None:
        assert calls_in_text("Journal 10\n") == []

    def test_an_incomplete_call_with_no_number_is_skipped(self) -> None:
        assert calls_in_text('Journal "Q"\n') == []

    def test_a_bare_function_name_at_end_of_script_is_skipped(self) -> None:
        assert calls_in_text("Journal") == []

    def test_a_call_embedded_in_a_real_multiline_script(self) -> None:
        script = (
            "Begin FargothScript\n"
            "short state\n\n"
            "if ( OnActivate == 1 )\n"
            '    Journal "A1_1_FargothRing", 10\n'
            "    set state to 1\n"
            "endif\n"
            "End FargothScript\n"
        )
        assert calls_in_text(script) == [("Journal", "A1_1_FargothRing", 10)]

    def test_no_calls_in_an_ordinary_script_is_an_empty_list(self) -> None:
        assert calls_in_text("if ( GetDisposition >= 50 )\n    set x to 1\nendif\n") == []

    def test_a_quest_id_with_spaces_is_read_correctly(self) -> None:
        assert calls_in_text('Journal "My Quest Name", 30') == [("Journal", "My Quest Name", 30)]


# ---------------------------------------------------------------------------
# statements_in_text_with_context -- generic "what else happens here"
# ---------------------------------------------------------------------------


class TestStatementsInTextWithContext:
    def test_a_plain_function_call_is_captured(self) -> None:
        got = statements_in_text_with_context('AddItem "gold_001", 100\n')
        assert got == [("AddItem", "", '"gold_001", 100', 'AddItem "gold_001", 100', ())]

    def test_a_target_qualified_call_splits_target_and_function(self) -> None:
        script = 'fargoth_ref->AddItem "gold_001", 100\n'
        got = statements_in_text_with_context(script)
        assert got[0][:3] == ("AddItem", "fargoth_ref", '"gold_001", 100')
        assert got[0][3] == 'fargoth_ref->AddItem "gold_001", 100'

    def test_a_set_statement_is_captured(self) -> None:
        got = statements_in_text_with_context("set x to 1\n")
        assert got == [("set", "", "x to 1", "set x to 1", ())]

    def test_startscript_is_captured_despite_being_a_keyword(self) -> None:
        got = statements_in_text_with_context('StartScript "WatcherScript"\n')
        assert got[0][0] == "StartScript"

    def test_journal_calls_are_excluded(self) -> None:
        assert statements_in_text_with_context('Journal "Q", 10\n') == []

    def test_setjournalindex_calls_are_excluded_case_insensitively(self) -> None:
        assert statements_in_text_with_context('setjournalindex "Q", 10\n') == []

    def test_control_flow_keywords_are_not_captured(self) -> None:
        script = (
            "if ( x == 1 )\n"
            "elseif ( x == 2 )\n"
            "else\n"
            "endif\n"
            "while ( x < 1 )\n"
            "endwhile\n"
        )
        assert statements_in_text_with_context(script) == []

    def test_local_declarations_are_not_captured(self) -> None:
        assert statements_in_text_with_context("short state\nfloat timer\nlong counter\n") == []

    def test_a_comment_only_line_is_skipped(self) -> None:
        assert statements_in_text_with_context("; just a note\n") == []

    def test_blank_lines_produce_no_entries(self) -> None:
        assert statements_in_text_with_context('\n\nAddItem "gold_001", 1\n\n\n') == [
            ("AddItem", "", '"gold_001", 1', 'AddItem "gold_001", 1', ())
        ]

    def test_multiple_statements_are_all_found_in_order(self) -> None:
        script = 'AddItem "gold_001", 100\nModDisposition 10\n'
        got = statements_in_text_with_context(script)
        assert [s[0] for s in got] == ["AddItem", "ModDisposition"]

    def test_a_statement_inside_an_if_carries_its_condition(self) -> None:
        script = 'if ( OnActivate == 1 )\n    AddItem "gold_001", 100\nendif\n'
        got = statements_in_text_with_context(script)
        assert got[0][4] == ("if ( OnActivate == 1 )",)

    def test_the_raw_line_is_reconstructed_verbatim(self) -> None:
        got = statements_in_text_with_context("   PayFine   \n")
        assert got[0][3] == "PayFine"

    def test_no_effects_means_an_empty_list_not_an_error(self) -> None:
        assert statements_in_text_with_context("") == []


# ---------------------------------------------------------------------------
# effects_from_stages / effects_from_scripts
# ---------------------------------------------------------------------------


class TestEffectsFromStages:
    def test_a_stage_with_no_script_text_contributes_nothing(self) -> None:
        assert effects_from_stages({"Q": [Resolved("s1", index=10)]}) == []

    def test_an_effects_owner_is_the_stage_that_defines_it(self) -> None:
        resolved = {
            "Q": [
                Resolved("s1", index=10, script_text='AddItem "gold_001", 100\n', plugins=["A.esp"])
            ]
        }
        got = effects_from_stages(resolved)
        assert got == [
            Effect(
                "AddItem",
                "",
                '"gold_001", 100',
                'AddItem "gold_001", 100',
                (),
                "DialogueInfo",
                "s1",
                "A.esp",
            )
        ]

    def test_the_plugin_is_the_stages_winning_plugin_not_the_first(self) -> None:
        resolved = {
            "Q": [
                Resolved(
                    "s1",
                    index=10,
                    script_text='AddItem "gold_001", 100\n',
                    plugins=["Morrowind.esm", "Patch.esp"],
                )
            ]
        }
        assert effects_from_stages(resolved)[0].plugin == "Patch.esp"

    def test_journal_calls_in_the_same_script_are_not_duplicated_as_effects(self) -> None:
        resolved = {
            "Q": [Resolved("s1", index=10, script_text='Journal "Q", 20\nAddItem "gold_001", 1\n')]
        }
        got = effects_from_stages(resolved)
        assert [e.function for e in got] == ["AddItem"]


class TestEffectsFromScripts:
    def test_a_calls_owner_is_its_own_script_id(self) -> None:
        sources = {"A.esp": [_script("FargothScript", text='AddItem "gold_001", 100\n')]}
        got = effects_from_scripts(sources, ["A.esp"])
        assert got[0].owner_type == "Script"
        assert got[0].owner_id == "FargothScript"
        assert got[0].plugin == "A.esp"

    def test_a_later_plugin_redefining_a_script_replaces_the_earlier_ones_effects(self) -> None:
        sources = {
            "A.esp": [_script("FooScript", text='AddItem "gold_001", 100\n')],
            "B.esp": [_script("FooScript", text="; nothing here\n")],
        }
        assert effects_from_scripts(sources, ["A.esp", "B.esp"]) == []

    def test_a_deleted_script_contributes_nothing(self) -> None:
        sources = {
            "A.esp": [_script("FooScript", text='AddItem "gold_001", 100\n')],
            "B.esp": [_script("FooScript", flags="DELETED")],
        }
        assert effects_from_scripts(sources, ["A.esp", "B.esp"]) == []

    def test_a_script_with_stripped_source_contributes_no_effects(self) -> None:
        """Unlike JournalCall, there is no bytecode fallback for generic effects."""
        sources = {
            "A.esp": [_script("Compiled", text="", bytecode=_bytecode_field(("Journal", "Q", 30)))]
        }
        assert effects_from_scripts(sources, ["A.esp"]) == []


# ---------------------------------------------------------------------------
# calls_in_text_with_context -- Stage 3's raw condition tracking
# ---------------------------------------------------------------------------


class TestCallsInTextWithContext:
    def test_a_call_with_no_enclosing_condition_has_an_empty_tuple(self) -> None:
        assert calls_in_text_with_context('Journal "Q", 10') == [("Journal", "Q", 10, ())]

    def test_a_call_inside_a_single_if_reports_that_condition_verbatim(self) -> None:
        script = 'if ( OnActivate == 1 )\n    Journal "Q", 10\nendif\n'
        got = calls_in_text_with_context(script)
        assert got == [("Journal", "Q", 10, ("if ( OnActivate == 1 )",))]

    def test_a_call_after_endif_has_no_condition(self) -> None:
        script = 'if ( OnActivate == 1 )\nendif\nJournal "Q", 10\n'
        got = calls_in_text_with_context(script)
        assert got == [("Journal", "Q", 10, ())]

    def test_nested_ifs_report_outermost_first(self) -> None:
        script = (
            "if ( OnActivate == 1 )\n"
            "    if ( GetDisposition >= 50 )\n"
            '        Journal "Q", 10\n'
            "    endif\n"
            "endif\n"
        )
        got = calls_in_text_with_context(script)
        assert got == [
            (
                "Journal",
                "Q",
                10,
                ("if ( OnActivate == 1 )", "if ( GetDisposition >= 50 )"),
            )
        ]

    def test_an_elseif_branch_reports_its_own_condition_not_the_original_ifs(self) -> None:
        script = (
            "if ( OnActivate == 1 )\n"
            "    set x to 1\n"
            "elseif ( OnActivate == 2 )\n"
            '    Journal "Q", 10\n'
            "endif\n"
        )
        got = calls_in_text_with_context(script)
        assert got == [("Journal", "Q", 10, ("elseif ( OnActivate == 2 )",))]

    def test_an_else_branch_is_reported_as_the_literal_word_else(self) -> None:
        """No claim about what it negates -- that would be evaluating, not scanning."""
        script = (
            "if ( OnActivate == 1 )\n" "    set x to 1\n" "else\n" '    Journal "Q", 10\n' "endif\n"
        )
        got = calls_in_text_with_context(script)
        assert got == [("Journal", "Q", 10, ("else",))]

    def test_a_while_loop_is_tracked_like_an_if(self) -> None:
        script = 'while ( x < 10 )\n    Journal "Q", 10\n    set x to ( x + 1 )\nendwhile\n'
        got = calls_in_text_with_context(script)
        assert got == [("Journal", "Q", 10, ("while ( x < 10 )",))]

    def test_a_condition_with_its_own_nested_parens_is_captured_whole(self) -> None:
        script = (
            "if ( ( GetDisposition >= 50 ) && ( GetHealth < 20 ) )\n"
            '    Journal "Q", 10\n'
            "endif\n"
        )
        got = calls_in_text_with_context(script)
        assert got[0][3] == ("if ( ( GetDisposition >= 50 ) && ( GetHealth < 20 ) )",)

    def test_two_sibling_ifs_do_not_leak_into_each_other(self) -> None:
        script = (
            "if ( a == 1 )\n"
            '    Journal "Q", 1\n'
            "endif\n"
            "if ( b == 1 )\n"
            '    Journal "Q", 2\n'
            "endif\n"
        )
        got = calls_in_text_with_context(script)
        assert got == [
            ("Journal", "Q", 1, ("if ( a == 1 )",)),
            ("Journal", "Q", 2, ("if ( b == 1 )",)),
        ]

    def test_leaving_a_nested_if_restores_the_outer_condition(self) -> None:
        script = (
            "if ( a == 1 )\n"
            "    if ( b == 1 )\n"
            "        set x to 1\n"
            "    endif\n"
            '    Journal "Q", 1\n'
            "endif\n"
        )
        got = calls_in_text_with_context(script)
        assert got == [("Journal", "Q", 1, ("if ( a == 1 )",))]

    def test_agrees_with_calls_in_text_on_function_quest_and_index(self) -> None:
        script = 'if ( x == 1 )\n    Journal "Q", 10\nendif\nSetJournalIndex "R", 5\n'
        plain = calls_in_text(script)
        contextual = [(f, q, ix) for f, q, ix, _ in calls_in_text_with_context(script)]
        assert plain == contextual

    def test_no_calls_means_an_empty_list_not_an_error(self) -> None:
        assert calls_in_text_with_context("if ( x == 1 )\nendif\n") == []


# ---------------------------------------------------------------------------
# calls_from_stages
# ---------------------------------------------------------------------------


class TestCallsFromStages:
    def test_a_stage_with_no_script_text_contributes_nothing(self) -> None:
        resolved = {"Q": [Resolved("stage1", index=10)]}
        assert calls_from_stages(resolved) == []

    def test_a_calls_owner_is_the_stage_that_defines_it(self) -> None:
        resolved = {
            "Q": [Resolved("stage1", index=10, script_text='Journal "Other", 5', plugins=["A.esp"])]
        }
        got = calls_from_stages(resolved)
        assert got == [JournalCall("Journal", "Other", 5, "DialogueInfo", "stage1", "A.esp")]

    def test_the_plugin_is_the_stages_winning_plugin_not_the_first(self) -> None:
        resolved = {
            "Q": [
                Resolved(
                    "stage1",
                    index=10,
                    script_text='Journal "Q", 20',
                    plugins=["Morrowind.esm", "Patch.esp"],
                )
            ]
        }
        assert calls_from_stages(resolved)[0].plugin == "Patch.esp"

    def test_calls_across_several_quests_and_stages_are_all_collected(self) -> None:
        resolved = {
            "Q1": [Resolved("s1", index=10, script_text='Journal "Q1", 20')],
            "Q2": [Resolved("s2", index=5, script_text='Journal "Q2", 15')],
        }
        got = calls_from_stages(resolved)
        assert {(c.quest, c.index) for c in got} == {("Q1", 20), ("Q2", 15)}

    def test_a_conditioned_call_carries_its_raw_condition(self) -> None:
        resolved = {
            "Q": [
                Resolved(
                    "s1",
                    index=10,
                    script_text='if ( OnActivate == 1 )\n    Journal "Q", 20\nendif\n',
                )
            ]
        }
        got = calls_from_stages(resolved)
        assert got[0].conditions == ("if ( OnActivate == 1 )",)

    def test_an_unconditioned_call_has_an_empty_conditions_tuple(self) -> None:
        resolved = {"Q": [Resolved("s1", index=10, script_text='Journal "Q", 20')]}
        assert calls_from_stages(resolved)[0].conditions == ()


# ---------------------------------------------------------------------------
# calls_from_scripts -- override resolution and the bytecode fallback
# ---------------------------------------------------------------------------


def _script(
    script_id: str, text: str = "", bytecode: Any = None, flags: str = ""
) -> dict[str, Any]:
    """A minimal standalone Script record shaped like tes3conv's JSON."""
    record: dict[str, Any] = {"type": "Script", "id": script_id, "text": text, "flags": flags}
    if bytecode is not None:
        record["bytecode"] = bytecode
    return record


#: Opcodes from wraithguard.mwscript.opcodes, duplicated here as plain
#: literals -- a test for the encoder should not import the very table the
#: production disassembler trusts, or a mistake in both would cancel out.
_OPCODE_JOURNAL: Final = 0x10CC
_OPCODE_SET_JOURNAL_INDEX: Final = 0x11AA


def _encode_scdt(*calls: tuple[str, str, int]) -> bytes:
    """Hand-encode a minimal SCDT byte stream for one or more journal calls.

    Mirrors exactly what the real compiler emits for ``Journal "id", N``:
    opcode, then a 1-byte-length-prefixed identifier, then a signed 16-bit
    short -- the shapes disassembler.py's own ``_read_operands`` expects for
    param flags ``(0x80020, 0x2)``.
    """
    opcodes = {"Journal": _OPCODE_JOURNAL, "SetJournalIndex": _OPCODE_SET_JOURNAL_INDEX}
    body = b""
    for function, quest, index in calls:
        qbytes = quest.encode("latin-1")
        body += struct.pack("<H", opcodes[function])
        body += bytes([len(qbytes)]) + qbytes
        body += struct.pack("<h", index)
    return struct.pack("<I", len(body)) + body


def _bytecode_field(*calls: tuple[str, str, int]) -> str:
    """Base64 text for a Script record's ``bytecode`` field, as tes3conv writes it."""
    return base64.b64encode(_encode_scdt(*calls)).decode("ascii")


class TestWinningScriptsAndCallsFromScripts:
    def test_a_calls_owner_is_its_own_script_id(self) -> None:
        sources = {"A.esp": [_script("FargothScript", text='Journal "Q", 10')]}
        got = calls_from_scripts(sources, ["A.esp"])
        assert len(got) == 1
        assert (got[0].function, got[0].quest, got[0].index) == ("Journal", "Q", 10)
        assert (got[0].owner_type, got[0].owner_id, got[0].plugin) == (
            "Script",
            "FargothScript",
            "A.esp",
        )

    def test_a_later_plugin_redefining_a_script_id_replaces_the_earlier_ones_calls(self) -> None:
        """The earlier version's call is dead code once overridden -- it must
        not be reported as something that still runs."""
        sources = {
            "A.esp": [_script("FooScript", text='Journal "Q", 10')],
            "B.esp": [_script("FooScript", text="; nothing journal-related here\n")],
        }
        got = calls_from_scripts(sources, ["A.esp", "B.esp"])
        assert got == []

    def test_a_deleted_script_contributes_nothing(self) -> None:
        sources = {
            "A.esp": [_script("FooScript", text='Journal "Q", 10')],
            "B.esp": [_script("FooScript", flags="DELETED")],
        }
        assert calls_from_scripts(sources, ["A.esp", "B.esp"]) == []

    def test_calls_from_multiple_distinct_scripts_are_all_found(self) -> None:
        sources = {
            "A.esp": [
                _script("Script1", text='Journal "Q1", 10'),
                _script("Script2", text='Journal "Q2", 20'),
            ]
        }
        got = calls_from_scripts(sources, ["A.esp"])
        assert {(c.owner_id, c.quest, c.index) for c in got} == {
            ("Script1", "Q1", 10),
            ("Script2", "Q2", 20),
        }

    def test_a_script_with_only_bytecode_falls_back_to_disassembly(self) -> None:
        sources = {
            "A.esp": [_script("Compiled", text="", bytecode=_bytecode_field(("Journal", "Q", 30)))]
        }
        got = calls_from_scripts(sources, ["A.esp"])
        assert len(got) == 1
        assert (got[0].function, got[0].quest, got[0].index) == ("Journal", "Q", 30)
        assert (got[0].owner_type, got[0].owner_id, got[0].plugin) == (
            "Script",
            "Compiled",
            "A.esp",
        )
        assert got[0].from_bytecode is True

    def test_a_script_with_real_source_never_touches_bytecode(self) -> None:
        """Source is the ground truth when present; the bytecode field is
        pure fallback, not a second, redundant scan."""
        # Deliberately mismatched bytecode -- if this were scanned too, the
        # test would see a second, wrong call.
        sources = {
            "A.esp": [
                _script(
                    "Both",
                    text='Journal "FromSource", 1',
                    bytecode=_bytecode_field(("Journal", "FromBytecode", 99)),
                )
            ]
        }
        got = calls_from_scripts(sources, ["A.esp"])
        assert len(got) == 1
        assert (got[0].function, got[0].quest, got[0].index) == ("Journal", "FromSource", 1)
        assert (got[0].owner_type, got[0].owner_id, got[0].plugin) == ("Script", "Both", "A.esp")
        assert got[0].from_bytecode is False

    def test_malformed_bytecode_yields_nothing_rather_than_raising(self) -> None:
        sources = {"A.esp": [_script("Broken", text="", bytecode="not valid base64 !!!")]}
        assert calls_from_scripts(sources, ["A.esp"]) == []

    def test_a_non_string_bytecode_field_yields_nothing_rather_than_raising(self) -> None:
        sources = {"A.esp": [_script("Weird", text="", bytecode=[1, 2, 3])]}
        assert calls_from_scripts(sources, ["A.esp"]) == []

    def test_setjournalindex_is_also_read_from_bytecode(self) -> None:
        sources = {
            "A.esp": [_script("C", text="", bytecode=_bytecode_field(("SetJournalIndex", "Q", 40)))]
        }
        got = calls_from_scripts(sources, ["A.esp"])
        assert got[0].function == "SetJournalIndex"
        assert got[0].index == 40

    def test_a_plugin_missing_from_sources_is_skipped_quietly(self) -> None:
        sources = {"A.esp": [_script("S", text='Journal "Q", 1')]}
        got = calls_from_scripts(sources, ["A.esp", "NotScanned.esp"])
        assert len(got) == 1

    def test_a_conditioned_call_from_a_standalone_script_carries_its_condition(self) -> None:
        sources = {"A.esp": [_script("Foo", text='if ( x == 1 )\n    Journal "Q", 10\nendif\n')]}
        got = calls_from_scripts(sources, ["A.esp"])
        assert got[0].conditions == ("if ( x == 1 )",)

    def test_a_bytecode_derived_call_has_no_condition_text(self) -> None:
        """Reconstructing block structure from compiled jumps is out of scope."""
        sources = {
            "A.esp": [_script("Compiled", text="", bytecode=_bytecode_field(("Journal", "Q", 30)))]
        }
        got = calls_from_scripts(sources, ["A.esp"])
        assert got[0].conditions == ()
        assert got[0].from_bytecode is True


# ---------------------------------------------------------------------------
# calls_from_dialogue / effects_from_dialogue -- ordinary NPC dialogue,
# the "dialogue is the other half" source neither calls_from_stages nor
# calls_from_scripts ever sees: a journal-type topic's own INFO is usually
# empty, and most journal-setting happens in a regular dialogue response.
# ---------------------------------------------------------------------------


def _dialogue_info(
    info_id: str, script_text: str = "", flags: str = "", dialogue_type: str = "Topic"
) -> dict[str, Any]:
    """A minimal ordinary (non-journal-topic) DialogueInfo record, tes3conv-shaped."""
    return {
        "type": "DialogueInfo",
        "id": info_id,
        "script_text": script_text,
        "flags": flags,
        "data": {"dialogue_type": dialogue_type},
    }


class TestWinningDialogueInfosAndCallsFromDialogue:
    def test_a_calls_owner_is_its_own_info_id(self) -> None:
        sources = {"A.esp": [_dialogue_info("cunius_ring", 'Journal "Q", 10')]}
        got = calls_from_dialogue(sources, ["A.esp"])
        assert len(got) == 1
        assert (got[0].function, got[0].quest, got[0].index) == ("Journal", "Q", 10)
        assert (got[0].owner_type, got[0].owner_id, got[0].plugin) == (
            "DialogueInfo",
            "cunius_ring",
            "A.esp",
        )

    def test_an_info_with_no_script_text_contributes_nothing(self) -> None:
        sources = {"A.esp": [_dialogue_info("greeting_5")]}
        assert calls_from_dialogue(sources, ["A.esp"]) == []

    def test_a_later_plugin_redefining_an_info_id_replaces_the_earlier_ones_calls(self) -> None:
        sources = {
            "A.esp": [_dialogue_info("cunius_ring", 'Journal "Q", 10')],
            "B.esp": [_dialogue_info("cunius_ring", 'Journal "Q", 20')],
        }
        got = calls_from_dialogue(sources, ["A.esp", "B.esp"])
        assert len(got) == 1
        assert (got[0].function, got[0].quest, got[0].index) == ("Journal", "Q", 20)
        assert (got[0].owner_type, got[0].owner_id, got[0].plugin) == (
            "DialogueInfo",
            "cunius_ring",
            "B.esp",
        )

    def test_a_deleted_info_contributes_nothing(self) -> None:
        sources = {
            "A.esp": [_dialogue_info("cunius_ring", 'Journal "Q", 10')],
            "B.esp": [_dialogue_info("cunius_ring", flags="DELETED")],
        }
        assert calls_from_dialogue(sources, ["A.esp", "B.esp"]) == []

    def test_calls_from_multiple_distinct_infos_are_all_found(self) -> None:
        sources = {
            "A.esp": [
                _dialogue_info("r1", 'Journal "Q", 10'),
                _dialogue_info("r2", 'Journal "Q", 20'),
            ]
        }
        got = calls_from_dialogue(sources, ["A.esp"])
        assert {c.owner_id for c in got} == {"r1", "r2"}

    def test_a_conditioned_call_from_dialogue_carries_its_condition(self) -> None:
        sources = {
            "A.esp": [
                _dialogue_info("cunius_ring", 'if ( PCVampire == 1 )\n    Journal "Q", 10\nendif\n')
            ]
        }
        got = calls_from_dialogue(sources, ["A.esp"])
        assert got[0].conditions == ("if ( PCVampire == 1 )",)

    def test_a_plugin_missing_from_sources_is_skipped_quietly(self) -> None:
        sources = {"A.esp": [_dialogue_info("cunius_ring", 'Journal "Q", 10')]}
        assert calls_from_dialogue(sources, ["A.esp", "Ghost.esp"]) != []  # does not raise

    def test_exclude_info_ids_skips_a_journal_type_entry_already_covered_elsewhere(self) -> None:
        """The double-counting guard: a journal-type stage's own INFO id, if
        it also carries a script, must not be reported both here and by
        calls_from_stages -- the caller passes every such id in."""
        sources = {"A.esp": [_dialogue_info("stage_10_entry", 'Journal "Q", 20')]}
        got = calls_from_dialogue(sources, ["A.esp"], exclude_info_ids={"stage_10_entry"})
        assert got == []

    def test_exclude_info_ids_leaves_everything_else_alone(self) -> None:
        sources = {
            "A.esp": [
                _dialogue_info("stage_10_entry", 'Journal "Q", 20'),
                _dialogue_info("cunius_ring", 'Journal "Q", 30'),
            ]
        }
        got = calls_from_dialogue(sources, ["A.esp"], exclude_info_ids={"stage_10_entry"})
        assert {c.owner_id for c in got} == {"cunius_ring"}


class TestEffectsFromDialogue:
    def test_a_non_journal_statement_from_dialogue_is_found(self) -> None:
        sources = {
            "A.esp": [_dialogue_info("cunius_ring", 'AddItem "gold_001", 100\nJournal "Q", 10\n')]
        }
        got = effects_from_dialogue(sources, ["A.esp"])
        assert len(got) == 1
        assert got[0].function == "AddItem"
        assert got[0].owner_type == "DialogueInfo"
        assert got[0].owner_id == "cunius_ring"

    def test_an_info_with_no_script_text_contributes_nothing(self) -> None:
        assert effects_from_dialogue({"A.esp": [_dialogue_info("greeting_5")]}, ["A.esp"]) == []

    def test_a_deleted_info_contributes_nothing(self) -> None:
        sources = {
            "A.esp": [_dialogue_info("cunius_ring", 'AddItem "gold_001", 100')],
            "B.esp": [_dialogue_info("cunius_ring", flags="DELETED")],
        }
        assert effects_from_dialogue(sources, ["A.esp", "B.esp"]) == []

    def test_exclude_info_ids_skips_a_journal_type_entry_already_covered_elsewhere(self) -> None:
        sources = {"A.esp": [_dialogue_info("stage_10_entry", 'AddItem "gold_001", 100')]}
        got = effects_from_dialogue(sources, ["A.esp"], exclude_info_ids={"stage_10_entry"})
        assert got == []


# ---------------------------------------------------------------------------
# attach
# ---------------------------------------------------------------------------


class TestAttach:
    def test_a_call_matching_its_own_quest_and_index_links_to_that_stage(self) -> None:
        stage = Resolved("s1", index=10)
        resolved = {"Q": [stage]}
        call = JournalCall("Journal", "Q", 10, "DialogueInfo", "s1", "A.esp")
        assert attach([call], resolved) == [Attachment(call, stage)]

    def test_matching_is_case_insensitive_on_the_quest_id(self) -> None:
        stage = Resolved("s1", index=10)
        resolved = {"A1_1_FargothRing": [stage]}
        call = JournalCall("Journal", "a1_1_fargothring", 10)
        assert attach([call], resolved)[0].stage is stage

    def test_a_call_to_a_different_quest_is_a_questline_link_not_a_mismatch(self) -> None:
        """A stage of one quest moving another quest's index along is a real
        case, not an error -- Balketh's own "links between quests"."""
        other_stage = Resolved("finish", index=100)
        resolved = {
            "QuestA": [Resolved("a1", index=10, script_text='Journal "QuestB", 100')],
            "QuestB": [other_stage],
        }
        calls = calls_from_stages(resolved)
        got = attach(calls, resolved)
        assert got == [Attachment(calls[0], other_stage)]
        assert got[0].call.owner_id == "a1"  # the call still belongs to QuestA's stage
        assert got[0].stage is not None
        assert got[0].stage.info_id == "finish"  # but it sets QuestB's

    def test_an_unmatched_call_links_to_none(self) -> None:
        resolved = {"Q": [Resolved("s1", index=10)]}
        call = JournalCall("Journal", "Q", 999, "DialogueInfo", "s1", "A.esp")
        assert attach([call], resolved) == [Attachment(call, None)]

    def test_an_unknown_quest_links_to_none(self) -> None:
        resolved = {"Q": [Resolved("s1", index=10)]}
        call = JournalCall("Journal", "NoSuchQuest", 10)
        assert attach([call], resolved) == [Attachment(call, None)]

    def test_results_are_returned_in_the_same_order_as_the_calls(self) -> None:
        resolved = {"Q": [Resolved("s1", index=1), Resolved("s2", index=2)]}
        calls = [
            JournalCall("Journal", "Q", 2),
            JournalCall("Journal", "Q", 1),
        ]
        got = attach(calls, resolved)
        assert [a.call.index for a in got] == [2, 1]

    def test_an_empty_call_list_is_fine(self) -> None:
        assert attach([], {"Q": [Resolved("s1", index=10)]}) == []
