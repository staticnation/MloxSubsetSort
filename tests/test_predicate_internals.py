"""Internal branches of the mlox predicate evaluator.

The round-trip conflict/requires/patch/note behaviour is covered in
``test_rule_parser`` and ``test_predicate_eval``. This pins the smaller helpers
and the block-parsing corners those never happen to reach: an unrecognised
function token, the plugin-attribution helper, the ``DESC`` group form, and the
``check_predicates`` guards for empty logic and short blocks.
"""

from __future__ import annotations

from wraithguard.rules import check_predicates
from wraithguard.rules.predicates import (
    _eval_func_token,
    _func_token_matches,
    evaluate_node,
    get_triggered_plugins,
)


class TestFunctionTokens:
    """The atomic ``[VER]``/``[SIZE]``/``[DESC]``/``[MWSE-LUA]`` forms."""

    def test_an_unrecognised_token_does_not_hold(self) -> None:
        """A bracketed form this tool does not model evaluates to False."""
        assert _eval_func_token("[NONSENSE Foo.esp]", {"foo.esp"}, None) is False

    def test_mwse_lua_never_holds_under_openmw(self) -> None:
        """MWSE-Lua content cannot exist under OpenMW, so the token is False."""
        assert _eval_func_token("[MWSE-LUA whatever]", {"foo.esp"}, None) is False

    def test_func_token_matches_names_the_targeted_plugins(self) -> None:
        """The attribution helper returns the plugins a token's pattern names."""
        matched = _func_token_matches("[SIZE 123 Foo.esp]", {"foo.esp", "bar.esp"})
        assert matched == {"foo.esp"}

    def test_func_token_matches_is_empty_for_an_unmodelled_token(self) -> None:
        """A token matching no known form attributes to nothing."""
        assert _func_token_matches("[NONSENSE Foo.esp]", {"foo.esp"}) == set()


class TestEvaluateNodeForms:
    """Node shapes the ordering path does not exercise."""

    def test_a_desc_group_evaluates_its_final_operand(self) -> None:
        """A ``DESC`` group carries a message; its truth is its last operand."""
        assert evaluate_node(["DESC", "some message", "a.esp"], {"a.esp"}) is True
        assert evaluate_node(["DESC", "some message", "a.esp"], {"b.esp"}) is False

    def test_a_message_string_carries_no_truth(self) -> None:
        """A ``/regex/`` message string is neither true nor a plugin match."""
        assert evaluate_node("/just a message/", {"a.esp"}) is True  # sl-delimited: no-op True


class TestGetTriggeredPlugins:
    """Attribution across the node shapes."""

    def test_a_function_token_attributes_through_the_helper(self) -> None:
        """A bare function-token node is attributed via the token matcher."""
        assert get_triggered_plugins("[SIZE 1 Foo.esp]", {"foo.esp"}) == {"foo.esp"}

    def test_a_message_string_attributes_to_nothing(self) -> None:
        """A ``/message/`` string matches no plugin."""
        assert get_triggered_plugins("/a message/", {"foo.esp"}) == set()

    def test_a_group_attributes_its_operands(self) -> None:
        """A list node collects the plugins its operands name."""
        found = get_triggered_plugins(["ALL", "a.esp", "b.esp"], {"a.esp", "b.esp"})
        assert found == {"a.esp", "b.esp"}

    def test_an_empty_node_attributes_to_nothing(self) -> None:
        """A node that is neither a string nor a non-empty list yields nothing."""
        assert get_triggered_plugins([], {"a.esp"}) == set()


class TestCheckPredicateBlockParsing:
    """Guards in the block splitter and the per-keyword dispatch."""

    def test_a_block_with_only_a_message_fires_nothing(self) -> None:
        """A note whose logic is empty parses to no AST and is skipped."""
        rule = "[Note]\n a message with no logic operands\n"
        assert check_predicates(rule, ["anything.esp"]) == []

    def test_a_header_argument_becomes_the_message(self) -> None:
        """Text on the keyword line itself is used as the warning message."""
        rule = "[Note this is the header message]\nfoo.esp\n"
        warnings = check_predicates(rule, ["foo.esp"])
        assert len(warnings) == 1
        assert "this is the header message" in warnings[0]

    def test_a_patch_with_a_single_operand_is_skipped(self) -> None:
        """A [Patch] needs both halves; one operand cannot form a dependency."""
        rule = "[Patch]\nlonely-patch.esp\n"
        assert check_predicates(rule, ["lonely-patch.esp"]) == []

    def test_a_note_that_does_not_all_hold_is_silent(self) -> None:
        """A note fires only when every listed operand is active."""
        rule = "[Note both must be present]\n[ALL a.esp b.esp]\n"
        assert check_predicates(rule, ["a.esp"]) == []

    def test_a_conflict_of_message_operands_warns_without_attribution(self) -> None:
        """Two truthy operands that name no plugin still fire, sans "Caused by".

        A ``/message/`` operand is always true but matches no plugin, so the
        conflict is reported with no attribution line.
        """
        rule = "[Conflict a headline]\n/firstmessage/\n/secondmessage/\n"
        warnings = check_predicates(rule, ["anything.esp"])
        assert len(warnings) == 1
        assert "Caused by" not in warnings[0]

    def test_an_unpatched_original_without_attribution(self) -> None:
        """A missing patch over a message-only original omits the "Unpatched" line."""
        rule = "[Patch a headline]\nAbsentPatch.esp\n/originalmessage/\n"
        warnings = check_predicates(rule, ["something.esp"])
        assert len(warnings) == 1
        assert "Missing patch" in warnings[0]
        assert "Unpatched" not in warnings[0]
