"""Tests for user-rule authoring (``append_user_rule`` and its cycle guard).

Per the mlox rule guidelines, a rule that would cause a cycle is *discarded*
at sort time -- so the authoring side's job is to refuse malformed rules
outright and warn about ones that would be silently dropped.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def rules_file(tmp_path):
    return tmp_path / "mlox_my_rules.txt"


class TestValidation:
    def test_creates_the_file_with_a_header(self, core, rules_file):
        core.append_user_rule(rules_file, "order", ["Base.esp", "Patch.esp"])
        text = rules_file.read_text(encoding="utf-8")
        assert "Personal mlox rules" in text
        assert "[Order]\nBase.esp\nPatch.esp" in text

    def test_round_trips_through_the_real_parser(self, core, rules_file, capsys):
        """A rule that writes must be a rule that loads."""
        core.append_user_rule(rules_file, "order", ["Base.esp", "Patch.omwaddon"])
        assert core.parse_mlox_file(rules_file) == [("order", ["Base.esp", "Patch.omwaddon"])]

    def test_comment_is_written_as_a_mlox_comment(self, core, rules_file, capsys):
        core.append_user_rule(rules_file, "order", ["A.esp", "B.esp"], comment="(Ref: the readme)")
        assert ";; (Ref: the readme)" in rules_file.read_text(encoding="utf-8")
        # comments must not leak into the parsed names
        assert core.parse_mlox_file(rules_file) == [("order", ["A.esp", "B.esp"])]

    def test_appending_keeps_earlier_rules(self, core, rules_file, capsys):
        core.append_user_rule(rules_file, "order", ["A.esp", "B.esp"])
        core.append_user_rule(rules_file, "nearend", ["Merged*.esp"])
        assert core.parse_mlox_file(rules_file) == [
            ("order", ["A.esp", "B.esp"]),
            ("nearend", ["Merged*.esp"]),
        ]

    def test_duplicate_name_is_rejected(self, core, rules_file):
        """A plugin ordered against itself is a self-cycle -- always a mistake."""
        with pytest.raises(ValueError, match="more than once"):
            core.append_user_rule(rules_file, "order", ["A.esp", "A.esp"])
        assert not rules_file.exists()

    def test_duplicate_detection_is_case_insensitive(self, core, rules_file):
        with pytest.raises(ValueError, match="more than once"):
            core.append_user_rule(rules_file, "order", ["A.esp", "B.esp", "a.ESP"])

    def test_order_rule_needs_two_names(self, core, rules_file):
        with pytest.raises(ValueError, match="at least two"):
            core.append_user_rule(rules_file, "order", ["Only.esp"])

    @pytest.mark.parametrize("bad", ["no_extension", "brackets[x].esp", "semi;colon.esp"])
    def test_malformed_names_are_rejected(self, core, rules_file, bad):
        with pytest.raises(ValueError):
            core.append_user_rule(rules_file, "order", ["Good.esp", bad])
        assert not rules_file.exists()

    def test_unknown_rule_type_is_rejected(self, core, rules_file):
        with pytest.raises(ValueError, match="unsupported rule type"):
            core.append_user_rule(rules_file, "note", ["A.esp", "B.esp"])

    def test_a_rejected_rule_never_touches_an_existing_file(self, core, rules_file, capsys):
        core.append_user_rule(rules_file, "order", ["A.esp", "B.esp"])
        before = rules_file.read_bytes()
        with pytest.raises(ValueError):
            core.append_user_rule(rules_file, "order", ["X.esp", "X.esp"])
        assert rules_file.read_bytes() == before

    @pytest.mark.parametrize("name", ["Wares*.esp", "plugin-?.esp", "Mod <VER>.esp"])
    def test_wildcard_patterns_are_accepted(self, core, rules_file, name, capsys):
        core.append_user_rule(rules_file, "order", [name, "Other.esp"])
        assert core.parse_mlox_file(rules_file)[0][1][0] == name


class TestFrozenOrderConflictDetection:
    """Advisory pre-check: warn when a rule would be discarded as a cycle."""

    FINAL = ["Morrowind.esm", "A.esp", "B.esp", "MyMod.esp"]
    CURATED = {"morrowind.esm", "a.esp", "b.esp"}

    def test_detects_a_rule_contradicting_the_curated_order(self, core):
        conflicts = core.order_rule_frozen_conflicts(["B.esp", "A.esp"], self.FINAL, self.CURATED)
        assert conflicts == [("B.esp", "A.esp")]

    def test_rule_agreeing_with_the_curated_order_is_fine(self, core):
        assert core.order_rule_frozen_conflicts(["A.esp", "B.esp"], self.FINAL, self.CURATED) == []

    def test_custom_plugins_can_move_freely(self, core):
        """A custom mod is not frozen, so ordering it anywhere is legitimate."""
        assert (
            core.order_rule_frozen_conflicts(["MyMod.esp", "A.esp"], self.FINAL, self.CURATED) == []
        )

    def test_wildcards_are_skipped(self, core):
        """A pattern does not resolve to one position, so it cannot be judged."""
        assert core.order_rule_frozen_conflicts(["B*.esp", "A.esp"], self.FINAL, self.CURATED) == []

    def test_unknown_plugins_are_skipped(self, core):
        assert (
            core.order_rule_frozen_conflicts(["Ghost.esp", "A.esp"], self.FINAL, self.CURATED) == []
        )

    def test_a_flagged_rule_really_is_discarded_by_the_sort(self, core, capsys):
        """Ties the advisory warning to actual engine behaviour."""
        base = ["Morrowind.esm", "A.esp", "B.esp"]
        curated = {n.lower() for n in base}

        conflicts = core.order_rule_frozen_conflicts(["B.esp", "A.esp"], base, curated)
        assert conflicts, "pre-check should flag this rule"

        result = core.build_and_sort(base, [], [(["B.esp", "A.esp"], 0)], {})
        assert result == base, "the sort must discard it, exactly as warned"
