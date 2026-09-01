"""Tests for writing mlox rules to the guidelines' standards.

Two kinds of check, and the second is the one that matters most.

**Does it say what the guidelines say?** The guideline page gives worked
examples -- the Assassins Armory ``[Requires]`` rule, the glue-patch
``[Patch]``, the nested ``[ALL A.esp [ANY B1.esp B2.esp]]`` note. Those are
reproduced here, so "we follow the guidelines" is a thing the suite checks
rather than a thing the docstring claims.

**Does mlox get it back?** Every rendered rule is fed to this project's own
parser -- the one that reads ``mlox_base.txt`` -- and has to come back as the
rule that was written. A renderer that emits something valid-looking but
unparseable is the failure that matters, and no amount of string comparison
finds it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from wraithguard.rules import check_predicates, load_rule_blocks
from wraithguard.rules.authoring import (
    PRIORITY_MARKS,
    RULE_KINDS,
    Desc,
    Plugin,
    Problem,
    Rule,
    Size,
    Ver,
    all_of,
    any_of,
    errors,
    format_ref,
    not_,
    render_rule,
    validate,
)

#: One rule of every kind, used by the engine round-trip tests.
RULES: dict[str, Rule] = {
    "Order": Rule("Order", plugins=["A.esp", "B.esp"], ref="r"),
    "NearStart": Rule("NearStart", plugins=["Morrowind.esm"], ref="r"),
    "NearEnd": Rule("NearEnd", plugins=["Mashed Lists.esp"], ref="r"),
    "Note": Rule("Note", expressions=[Plugin("A.esp")], message="m", ref="r"),
    "Requires": Rule(
        "Requires", expressions=[Plugin("A.esp"), Plugin("B.esp")], message="m", ref="r"
    ),
    "Conflict": Rule(
        "Conflict", expressions=[Plugin("A.esp"), Plugin("B.esp")], message="m", ref="r"
    ),
    "Patch": Rule(
        "Patch",
        expressions=[Plugin("patch.esp"), all_of(Plugin("X.esp"), Plugin("Y.esp"))],
        message="m",
        ref="r",
    ),
}

#: A load order that makes each warning rule fire. [Requires] fires when the
#: dependant is present and its requirement is not; the others when their
#: expressions are all satisfied.
TRIGGERING: dict[str, list[str]] = {
    "Note": ["A.esp"],
    "Requires": ["A.esp"],
    "Conflict": ["A.esp", "B.esp"],
    "Patch": ["patch.esp"],
}


def ok(rule: Rule) -> bool:
    """Whether a rule has no fatal problems.

    Args:
        rule: The rule.

    Returns:
        ``True`` when validation found no errors.
    """
    return not errors(validate(rule))


class TestOrderingRules:
    """[Order], [NearStart] and [NearEnd]."""

    def test_order_renders_as_the_guidelines_show(self) -> None:
        """The canonical example, verbatim from the guideline page."""
        rule = Rule("Order", plugins=["Foo.esp", "Bar.esp"], ref="my own load order")

        assert render_rule(rule).splitlines()[:3] == ["[Order]", "Foo.esp", "Bar.esp"]

    def test_order_needs_two_plugins(self) -> None:
        """It states that one plugin precedes another; one name states nothing."""
        problems = validate(Rule("Order", plugins=["Only.esp"]))

        assert any("at least two plugins" in p.message for p in problems)

    def test_the_same_plugin_twice_is_a_cycle(self) -> None:
        """mlox discards cycles, so the rule would silently do nothing."""
        problems = validate(Rule("Order", plugins=["A.esp", "A.esp"]))

        assert any("listed twice" in p.message for p in problems)

    def test_nearstart_and_nearend_are_discouraged(self) -> None:
        """ "Abuse of the [NearEnd] rule is frowned upon" -- allowed, but warned."""
        for kind in ("NearStart", "NearEnd"):
            problems = validate(Rule(kind, plugins=["A.esp"], ref="x"))
            assert any("discouraged" in p.message for p in problems), kind
            assert ok(Rule(kind, plugins=["A.esp"], ref="x")), "should still be writable"

    def test_an_ordering_rule_carries_no_message(self) -> None:
        """Only the warning rules take one."""
        rule = Rule("Order", plugins=["A.esp", "B.esp"], message="ignored", ref="x")

        assert "ignored" not in render_rule(rule)


class TestWarningRules:
    """[Note], [Requires], [Conflict] and [Patch]."""

    def test_the_guidelines_requires_example(self) -> None:
        """Reproduced from the guideline page, structure and all."""
        rule = Rule(
            "Requires",
            expressions=[
                Plugin("Assassins Armory - Arrows.esp"),
                any_of(Plugin("AreaEffectArrows XB Edition.esp"), Plugin("AreaEffectArrows.esp")),
            ],
            message='"Assassins Armory - Arrows.esp" requires the Area Effect Arrows plugin',
            ref='"Assassin\'s Armory readme.doc"',
        )

        text = render_rule(rule)

        assert text.startswith("[Requires]\n")
        assert ' (Ref: "Assassin\'s Armory readme.doc")' in text
        assert "Assassins Armory - Arrows.esp" in text
        assert "[ANY AreaEffectArrows XB Edition.esp AreaEffectArrows.esp]" in text
        assert ok(rule)

    def test_the_guidelines_glue_patch_example(self) -> None:
        """A patch that glues two mods together."""
        rule = Rule(
            "Patch",
            expressions=[
                Plugin("glue-patch.esp"),
                all_of(Plugin("original-X.esp"), Plugin("original-Y.esp")),
            ],
            message="glue-patch.esp makes X and Y compatible",
            ref="the patch readme",
        )

        assert "[ALL original-X.esp original-Y.esp]" in render_rule(rule)
        assert ok(rule)

    def test_a_nested_note_expression(self) -> None:
        """``[ALL A.esp [ANY B1.esp B2.esp]]``, from the guidelines."""
        rule = Rule(
            "Note",
            expressions=[all_of(Plugin("A.esp"), any_of(Plugin("B1.esp"), Plugin("B2.esp")))],
            message="Whee!",
            ref="x",
        )

        assert "[ALL A.esp [ANY B1.esp B2.esp]]" in render_rule(rule)

    def test_conflict_needs_two_things_to_conflict(self) -> None:
        """It warns when any two expressions are true at once."""
        problems = validate(Rule("Conflict", expressions=[Plugin("A.esp")], ref="x"))

        assert any("at least two expressions" in p.message for p in problems)

    def test_requires_takes_exactly_two(self) -> None:
        """A dependant and what it depends on."""
        rule = Rule("Requires", expressions=[Plugin("A.esp")], ref="x")

        assert any("exactly two expressions" in p.message for p in validate(rule))

    def test_note_needs_at_least_one_expression(self) -> None:
        """A note about nothing prints for everyone."""
        assert any("at least one" in p.message for p in validate(Rule("Note", ref="x")))


class TestPatchCannotExpressNot:
    """The guidelines state this outright, and give the remedy."""

    RULE = Rule(
        "Patch",
        expressions=[
            Plugin("original-X-patch.esp"),
            all_of(Plugin("original-X.esp"), not_(Plugin("original-Y.esp"))),
        ],
        message="x",
        ref="y",
    )

    def test_a_not_inside_a_patch_is_an_error(self) -> None:
        """Emitting it would produce something mlox will not do."""
        assert any("does not recognise the NOT" in p.message for p in validate(self.RULE))
        assert not ok(self.RULE)

    def test_the_remedy_is_the_one_the_guidelines_give(self) -> None:
        """Being told what to do instead is the point of the check."""
        problem = next(p for p in validate(self.RULE) if "NOT" in p.message)

        assert "[Conflict]" in problem.remedy
        assert "[Requires]" in problem.remedy

    def test_not_is_fine_in_the_other_warning_rules(self) -> None:
        """Only [Patch] is restricted."""
        for kind in ("Note", "Conflict"):
            rule = Rule(
                kind,
                expressions=[Plugin("A.esp"), not_(Plugin("B.esp"))],
                message="m",
                ref="r",
            )
            assert ok(rule), kind


class TestPredicates:
    """[DESC], [SIZE] and [VER]."""

    def test_desc_renders_on_one_line(self) -> None:
        """mlox requires it; a wrapped predicate would not parse."""
        rendered = Desc("v. 1.1109", "Sris_Alchemy_BM.esp").render()

        assert rendered == "[DESC /v. 1.1109/ Sris_Alchemy_BM.esp]"
        assert "\n" not in rendered

    def test_a_negated_desc_uses_the_bang(self) -> None:
        """ "If the first slash is preceded by a bang" -- from the guidelines."""
        assert Desc("x", "A.esp", negated=True).render() == "[DESC !/x/ A.esp]"

    def test_an_uncompilable_desc_regex_is_an_error(self) -> None:
        """It is a Python regex, so it has to be one."""
        rule = Rule("Note", expressions=[Desc("(unclosed", "A.esp")], message="m", ref="r")

        assert any("does not compile" in p.message for p in validate(rule))

    def test_size_renders_with_and_without_the_bang(self) -> None:
        """Both forms are documented."""
        assert Size(2476, "moons_soulgems.esp").render() == "[SIZE 2476 moons_soulgems.esp]"
        assert Size(2476, "a.esp", negated=True).render() == "[SIZE !2476 a.esp]"

    def test_a_negative_size_is_an_error(self) -> None:
        """No file has one."""
        rule = Rule("Note", expressions=[Size(-1, "A.esp")], message="m", ref="r")

        assert any("zero or more" in p.message for p in validate(rule))

    @pytest.mark.parametrize("version", ["1.2.3a", "1.0", "1", "1a", "1_3a", "77g"])
    def test_every_documented_version_form_is_accepted(self, version: str) -> None:
        """The guidelines list these exact examples as valid.

        Args:
            version: The version string.
        """
        rule = Rule("Note", expressions=[Ver("<", version, "foo.esp")], message="m", ref="r")

        assert ok(rule), version

    def test_a_nonsense_version_is_refused(self) -> None:
        """Silently emitting it would produce a rule that never fires."""
        rule = Rule("Note", expressions=[Ver("<", "banana", "foo.esp")], message="m", ref="r")

        assert any("not a version" in p.message for p in validate(rule))

    @pytest.mark.parametrize("operator", ["<", "=", ">"])
    def test_the_documented_operators_are_accepted(self, operator: str) -> None:
        """Args:
        operator: The comparison operator.
        """
        rule = Rule("Note", expressions=[Ver(operator, "1.0", "a.esp")], message="m", ref="r")

        assert ok(rule), operator

    def test_an_undocumented_operator_is_refused(self) -> None:
        """mlox knows three; anything else is a typo."""
        rule = Rule("Note", expressions=[Ver(">=", "1.0", "a.esp")], message="m", ref="r")

        assert any("not one of" in p.message for p in validate(rule))


class TestBooleanGroups:
    """ALL, ANY and NOT."""

    def test_not_takes_exactly_one_expression(self) -> None:
        """Negating two things at once is not a thing mlox expresses."""
        from wraithguard.rules.authoring import Group

        rule = Rule(
            "Note",
            expressions=[Group("NOT", (Plugin("A.esp"), Plugin("B.esp")))],
            message="m",
            ref="r",
        )

        assert any("exactly one expression" in p.message for p in validate(rule))

    def test_a_single_element_group_is_pointless(self) -> None:
        """Valid, but it says nothing the bare predicate did not."""
        rule = Rule("Note", expressions=[all_of(Plugin("A.esp"))], message="m", ref="r")
        problems = validate(rule)

        assert any("no effect" in p.message for p in problems)
        assert ok(rule), "a pointless group is still valid"

    def test_a_long_group_breaks_across_lines(self) -> None:
        """An unreadable one-liner helps nobody, and mlox accepts both."""
        group = any_of(*[Plugin(f"A Really Quite Long Plugin Name {n}.esp") for n in range(5)])
        rendered = group.render()

        assert "\n" in rendered
        assert rendered.startswith("[ANY ")
        assert rendered.endswith("]")


class TestCitations:
    """The ``(Ref:)`` convention."""

    def test_a_citation_is_rendered_into_the_message(self) -> None:
        """It belongs with the message, where a reader will see it."""
        rule = Rule("Note", expressions=[Plugin("A.esp")], message="m", ref="the readme")

        assert " (Ref: the readme)" in render_rule(rule)

    def test_a_url_citation_gets_the_required_whitespace(self) -> None:
        """Forums auto-link URLs and swallow the closing parenthesis otherwise.

        The guidelines call this out with a good/bad example, so the fix is
        applied rather than only complained about.
        """
        assert format_ref("http://www.uesp.net/wiki/Tes3Mod:Leveled_Lists").endswith(" )")

    def test_a_plain_citation_gets_no_extra_space(self) -> None:
        """The whitespace rule is about URLs, not about everything."""
        assert format_ref("Luthors Compass 1.0.zip/ReadMe.txt") == (
            "(Ref: Luthors Compass 1.0.zip/ReadMe.txt)"
        )

    def test_an_empty_citation_renders_nothing(self) -> None:
        """An empty "(Ref: )" is worse than none."""
        assert format_ref("   ") == ""

    def test_a_missing_citation_is_a_warning(self) -> None:
        """ "Whenever possible, rules should have a (Ref: xxx) comment"."""
        rule = Rule("Note", expressions=[Plugin("A.esp")], message="m")

        assert any("no (Ref:) citation" in p.message for p in validate(rule))
        assert ok(rule), "missing citation is a quality warning, not a parse error"


class TestMessagesAndHighlighting:
    """Message forms and the ! prefixes."""

    def test_a_short_message_goes_inline(self) -> None:
        """The form the guidelines show first."""
        rule = Rule("Note", expressions=[Plugin("A.esp")], message="Whee!")

        assert render_rule(rule).startswith("[Note Whee!]")

    def test_a_message_containing_a_bracket_uses_the_block_form(self) -> None:
        """ "the message may not contain a right-bracket" -- so it must not."""
        rule = Rule("Note", expressions=[Plugin("A.esp")], message="see [this] thing")
        text = render_rule(rule)

        assert text.startswith("[Note]\n")
        assert " see [this] thing" in text
        assert ok(rule)

    def test_block_message_lines_begin_with_whitespace(self) -> None:
        """mlox reads the indent as continuation; without it the rule breaks."""
        rule = Rule("Note", expressions=[Plugin("A.esp")], message="one\ntwo", ref="r")
        body = render_rule(rule).splitlines()

        assert body[0] == "[Note]"
        assert all(line.startswith(" ") for line in body[1:4])

    @pytest.mark.parametrize(("level", "mark"), sorted(PRIORITY_MARKS.items()))
    def test_priority_renders_its_documented_prefix(self, level: int, mark: str) -> None:
        """! blue, !! yellow, !!! red.

        Args:
            level: The priority level.
            mark: The prefix it should render.
        """
        rule = Rule("Note", expressions=[Plugin("A.esp")], message="m", priority=level, ref="r")
        text = render_rule(rule)

        assert (f" {mark} m" in text) or (not mark and " m" in text)

    def test_a_prefix_on_a_self_highlighting_rule_is_pointless(self) -> None:
        """[Requires] is already red; [Conflict] and [Patch] already yellow."""
        rule = Rule(
            "Requires",
            expressions=[Plugin("A.esp"), Plugin("B.esp")],
            message="m",
            priority=3,
            ref="r",
        )

        assert any("already highlighted" in p.message for p in validate(rule))


class TestSectionsAndComments:
    """The ``@Section`` and ``;`` conventions."""

    def test_a_section_heading_is_written_first(self) -> None:
        """ "sections, which begin with @ followed by the section name"."""
        rule = Rule("Order", plugins=["A.esp", "B.esp"], section="My Mod", ref="r")

        assert render_rule(rule).splitlines()[0] == "@My Mod"

    def test_comments_use_a_semicolon(self) -> None:
        """Comments are stripped by mlox before anything else."""
        rule = Rule("Order", plugins=["A.esp", "B.esp"], comment="why this exists", ref="r")

        assert "; why this exists" in render_rule(rule)


class TestFilenameExpansion:
    """``?``, ``*`` and ``<VER>``."""

    @pytest.mark.parametrize("name", ["plugin-?.esp", "plugin-*.esp", "plugin-<VER>.esp"])
    def test_expansion_patterns_are_accepted(self, name: str) -> None:
        """All three forms come straight from the guidelines' examples.

        Args:
            name: The pattern.
        """
        rule = Rule("Order", plugins=[name, "Other.esp"], ref="r")

        assert ok(rule), name

    def test_expansion_is_flagged_as_expensive(self) -> None:
        """ "it is suggested that filename expansions be used sparingly"."""
        rule = Rule("Order", plugins=["plugin-*.esp", "Other.esp"], ref="r")

        assert any("sparingly" in p.remedy for p in validate(rule))

    def test_a_name_with_no_extension_is_refused(self) -> None:
        """Nothing safe can be guessed from a bare word."""
        rule = Rule("Order", plugins=["not a plugin", "B.esp"], ref="r")

        assert any("does not look like a plugin" in p.message for p in validate(rule))


class TestEveryRenderedRuleWorksInTheEngine:
    """The check that matters: the rule must *do its job* when mlox reads it.

    Not "the parser recognised a label" -- that would pass for a rule that
    fires on the wrong plugins or carries a mangled message. Ordering rules are
    loaded with the same loader the sort uses and have to produce the chain;
    warning rules are evaluated against a load order and have to fire, with
    their message intact, and stay silent when they should not fire.
    """

    def test_an_order_rule_produces_its_chain(self, tmp_path: Path) -> None:
        """The sort reads these; a mis-rendered one silently orders nothing.

        Args:
            tmp_path: Pytest temporary directory.
        """
        path = tmp_path / "rules.txt"
        path.write_text(render_rule(RULES["Order"]) + "\n", encoding="utf-8")

        blocks, nearstart, nearend = load_rule_blocks([path])

        assert blocks == [(["A.esp", "B.esp"], 0)]
        assert nearstart == [] and nearend == []

    def test_nearstart_and_nearend_reach_their_own_lists(self, tmp_path: Path) -> None:
        """They are read separately from the ordering chains.

        Args:
            tmp_path: Pytest temporary directory.
        """
        path = tmp_path / "rules.txt"
        path.write_text(
            render_rule(RULES["NearStart"]) + "\n" + render_rule(RULES["NearEnd"]) + "\n",
            encoding="utf-8",
        )

        blocks, nearstart, nearend = load_rule_blocks([path])

        assert blocks == []
        assert nearstart == ["Morrowind.esm"]
        assert nearend == ["Mashed Lists.esp"]

    @pytest.mark.parametrize("kind", ["Note", "Requires", "Conflict", "Patch"])
    def test_a_warning_rule_fires_with_its_message(self, kind: str) -> None:
        """The message and the citation have to survive into the warning.

        Args:
            kind: The rule label under test.
        """
        warnings = check_predicates(render_rule(RULES[kind]), TRIGGERING[kind])

        assert warnings, f"[{kind}] did not fire for {TRIGGERING[kind]}"
        assert "(Ref: r)" in warnings[0], warnings

    def test_a_note_stays_silent_when_its_condition_is_unmet(self) -> None:
        """A rule that always fires is as useless as one that never does."""
        rule = Rule(
            "Note",
            expressions=[all_of(Plugin("A.esp"), any_of(Plugin("B1.esp"), Plugin("B2.esp")))],
            message="Whee!",
            ref="r",
        )

        assert check_predicates(render_rule(rule), ["A.esp", "B2.esp"])
        assert check_predicates(render_rule(rule), ["A.esp"]) == []

    def test_a_nested_expression_evaluates_correctly(self) -> None:
        """Nesting is where a renderer is most likely to produce garbage."""
        rule = Rule(
            "Note",
            expressions=[all_of(Plugin("A.esp"), not_(Plugin("B.esp")))],
            message="no B here",
            ref="r",
        )

        assert check_predicates(render_rule(rule), ["A.esp"])
        assert check_predicates(render_rule(rule), ["A.esp", "B.esp"]) == []

    def test_a_multi_line_block_message_arrives_whole(self) -> None:
        """The indent-continuation form is easy to get subtly wrong."""
        rule = Rule(
            "Conflict",
            expressions=[Plugin("A.esp"), Plugin("B.esp")],
            message="Do not use\nA.esp and B.esp together",
            ref="a forum post",
        )

        warnings = check_predicates(render_rule(rule), ["A.esp", "B.esp"])

        assert warnings
        assert "Do not use A.esp and B.esp together" in warnings[0]

    def test_every_rule_kind_is_exercised(self) -> None:
        """A new rule kind must not be able to skip this."""
        assert set(RULES) == set(RULE_KINDS)


class TestProblemReporting:
    """How problems reach the person."""

    def test_describe_includes_the_remedy(self) -> None:
        """A complaint with no remedy is half a message."""
        described = Problem("error", "something is wrong", "do this instead").describe()

        assert described == "error: something is wrong -- do this instead"

    def test_describe_omits_an_absent_remedy(self) -> None:
        """An invented suggestion would be worse than none."""
        assert Problem("warning", "just so you know").describe() == "warning: just so you know"

    def test_errors_filters_out_warnings(self) -> None:
        """The caller decides what is fatal; this is how it asks."""
        problems = [Problem("warning", "a"), Problem("error", "b")]

        assert [p.message for p in errors(problems)] == ["b"]


class TestNamesCannotCarryRuleSyntax:
    """A name mlox reads as syntax refers to a different plugin than the one typed.

    Found by the older ``append_user_rule`` tests when that function was made to
    delegate here: this validator accepted both of these, and the one it
    replaced did not. ``semi;colon.esp`` is the dangerous case -- ``;`` starts a
    comment, so mlox reads the name as ``semi`` and the rule silently applies to
    something else, or to nothing.
    """

    @pytest.mark.parametrize(
        "name", ["semi;colon.esp", "brackets[x].esp", "close]bracket.esp", "new\nline.esp"]
    )
    def test_structural_characters_are_refused(self, name: str) -> None:
        """Args:
        name: The malformed plugin name.
        """
        problems = errors(validate(Rule("Order", plugins=["Good.esp", name], ref="r")))

        assert problems, f"{name!r} was accepted"

    def test_the_message_says_why_rather_than_just_no(self) -> None:
        """ "Does not look like a plugin filename" is not obviously true here."""
        problems = errors(validate(Rule("Order", plugins=["Good.esp", "semi;colon.esp"], ref="r")))

        assert "rule syntax" in problems[0].message
        assert "comment" in problems[0].remedy

    def test_an_ordinary_name_is_unaffected(self) -> None:
        """The check must not be so broad it refuses real filenames."""
        assert not errors(
            validate(Rule("Order", plugins=["Bob's Armory (v2).esp", "B.esp"], ref="r"))
        )

    def test_the_same_check_applies_inside_expressions(self) -> None:
        """A warning rule's plugins go through a different code path."""
        rule = Rule("Note", expressions=[Plugin("semi;colon.esp")], message="m", ref="r")

        assert errors(validate(rule))


class TestValidatorEdges:
    """Paths the main tests do not reach, each a real way to be wrong.

    Added during the 3.1 audit after measuring which branches of the validator
    had never run. Coverage is not the point; every case here is something a
    person could actually type into the rule maker.
    """

    def test_an_unknown_rule_kind_is_refused(self) -> None:
        """A typo in a rule label produces a rule mlox ignores entirely."""
        problems = errors(validate(Rule("Ordering", plugins=["A.esp", "B.esp"], ref="r")))

        assert problems
        assert "not a rule mlox knows" in problems[0].message
        assert "Order" in problems[0].remedy

    def test_an_empty_nearstart_is_refused(self) -> None:
        """A position hint about no plugins hints at nothing."""
        problems = errors(validate(Rule("NearStart", ref="r")))

        assert any("at least one plugin" in p.message for p in problems)

    def test_a_priority_with_no_message_still_renders(self) -> None:
        """The mark alone is odd but must not produce broken text."""
        rule = Rule("Note", expressions=[Plugin("A.esp")], priority=2, ref="r")

        text = render_rule(rule)

        assert "!!" in text
        assert "[Note" in text

    def test_a_rule_with_no_message_and_no_citation_renders_bare(self) -> None:
        """The minimum a warning rule can be, and it must still parse."""
        rule = Rule("Note", expressions=[Plugin("A.esp")])

        text = render_rule(rule)

        assert text.splitlines()[0] == "[Note]"
        assert check_predicates(text, ["A.esp"])

    def test_a_version_predicate_renders_on_one_line(self) -> None:
        """mlox requires it, and the rendering path is separate from DESC/SIZE."""
        rendered = Ver("<", "1.2", "foo.esp").render()

        assert rendered == "[VER < 1.2 foo.esp]"
        assert "\n" not in rendered

    def test_an_empty_group_renders_without_crashing(self) -> None:
        """Half-built rules are the normal state of a live preview."""
        from wraithguard.rules.authoring import Group

        assert Group("ALL", ()).render() == "[ALL ]"


class TestAuditFindings:
    """Three defects found by probing the module with plausible typing.

    None were reachable from the tests that existed: each needed input a person
    would produce and a test author would not think to write.
    """

    def test_a_leading_space_does_not_swallow_a_plugin(self) -> None:
        """The worst of the three, because it is silent.

        mlox reads a line beginning with whitespace as message text. A space
        typed before a plugin name therefore removed it from the rule, and the
        rule still loaded -- just without that plugin. Confirmed against the
        real loader before the fix.
        """
        text = render_rule(Rule("Order", plugins=[" A.esp", "B.esp "], ref="r"))

        assert text == "[Order]\nA.esp\nB.esp"

    def test_a_leading_space_in_an_expression_is_stripped_too(self) -> None:
        """Warning rules render their plugins by a different path."""
        assert Plugin(" A.esp ").render() == "A.esp"

    def test_a_section_typed_with_its_at_sign_is_not_doubled(self) -> None:
        """The guidelines write sections as "@Name", so people type the @."""
        rule = Rule("Order", plugins=["A.esp", "B.esp"], section="@My Mod", ref="r")

        assert render_rule(rule).splitlines()[0] == "@My Mod"

    def test_a_section_without_the_at_sign_still_gets_one(self) -> None:
        """Both spellings have to arrive at the same place."""
        rule = Rule("Order", plugins=["A.esp", "B.esp"], section="My Mod", ref="r")

        assert render_rule(rule).splitlines()[0] == "@My Mod"

    def test_a_highlight_level_outside_the_documented_range_is_refused(self) -> None:
        """It silently rendered no mark, which is not what asking for one means."""
        rule = Rule("Note", expressions=[Plugin("A.esp")], message="m", priority=9, ref="r")
        problems = errors(validate(rule))

        assert problems
        assert "not one of [0, 1, 2, 3]" in problems[0].message

    @pytest.mark.parametrize("level", [0, 1, 2, 3])
    def test_every_documented_level_is_accepted(self, level: int) -> None:
        """The check must not refuse the levels the guidelines define.

        Args:
            level: The highlight level.
        """
        rule = Rule("Note", expressions=[Plugin("A.esp")], message="m", priority=level, ref="r")

        assert not errors(validate(rule))


class TestExpressionValidationEdges:
    """Corners of the per-expression checker."""

    def test_a_blank_name_is_not_a_plugin(self) -> None:
        """An empty candidate is rejected before any pattern is considered."""
        from wraithguard.rules.authoring import _looks_like_plugin

        assert _looks_like_plugin("   ") is False

    def test_an_empty_group_is_an_error(self) -> None:
        """An [ALL]/[ANY] with no operands cannot be evaluated."""
        rule = Rule("Note", expressions=[all_of()], message="m", ref="r")
        problems = errors(validate(rule))
        assert any("needs at least one expression" in p.message for p in problems)

    def test_a_valid_size_expression_passes_the_size_check(self) -> None:
        """A non-negative [SIZE] is accepted, exercising the pass-through branch."""
        rule = Rule("Note", expressions=[Size(0, "Foo.esp")], message="m", ref="r")
        assert not any("byte count" in p.message for p in validate(rule))
