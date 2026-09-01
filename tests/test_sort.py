"""Tests for the load-order sort engine (``build_and_sort``).

The invariants here are the tool's core promise: the curated MOMW order is
never reordered, only the user's own additions move, and conflicting rules are
discarded rather than forced (matching mlox's documented behaviour).
"""

from __future__ import annotations

from wraithguard.sort import build_and_sort

BASE = ["Morrowind.esm", "Tribunal.esm", "A.esp", "B.esp", "C.esp", "D.esp"]
VANILLA = ["Morrowind.esm"]


def curated(order, subset):
    """The frozen (non-custom) plugins, in order."""
    lowered = {s.lower() for s in subset}
    return [n for n in order if n.lower() not in lowered]


class TestCuratedOrderIsFrozen:
    def test_curated_order_never_changes(self):
        subset = ["Zebra.esp", "Apple.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        result = build_and_sort(BASE, subset, [], masters)
        assert curated(result, subset) == BASE

    def test_rule_fighting_curated_order_is_discarded(self, capsys):
        """mlox: 'whenever we encounter a rule that would cause a cycle, it is
        discarded'. Here the rule contradicts the frozen order."""
        result = build_and_sort(BASE, [], [(["C.esp", "A.esp"], 0)], {})
        assert result == BASE

    def test_all_plugins_are_placed_exactly_once(self):
        subset = ["New1.esp", "New2.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        result = build_and_sort(BASE, subset, [], masters)
        assert sorted(result) == sorted(BASE + subset)
        assert len(result) == len(set(result))


class TestAnchoring:
    def test_patch_lands_after_the_plugin_it_masters(self):
        result = build_and_sort(
            BASE, ["B Patch.esp"], [], {"b patch.esp": ["Morrowind.esm", "B.esp"]}
        )
        assert result.index("B Patch.esp") == result.index("B.esp") + 1

    def test_custom_to_custom_chain_resolves_transitively(self):
        subset = ["MyMod.esp", "MyMod Patch.esp"]
        masters = {
            "mymod.esp": ["Morrowind.esm", "C.esp"],
            "mymod patch.esp": ["Morrowind.esm", "MyMod.esp"],
        }
        result = build_and_sort(BASE, subset, [], masters)
        assert result.index("MyMod.esp") == result.index("C.esp") + 1
        assert result.index("MyMod Patch.esp") == result.index("MyMod.esp") + 1

    def test_esm_only_dependency_is_not_a_position_signal(self):
        """Anchoring to shared resource ESMs would cluster everything at the
        front, so a mod that only depends on masters goes to the end."""
        base = ["Morrowind.esm", "OAAB_Data.esm", "A.esp", "B.esp"]
        result = build_and_sort(
            base, ["Standalone.esp"], [], {"standalone.esp": ["Morrowind.esm", "OAAB_Data.esm"]}
        )
        assert result[-1] == "Standalone.esp"

    def test_rule_edge_alone_anchors_a_custom(self):
        result = build_and_sort(BASE, ["RuleMod.esp"], [(["B.esp", "RuleMod.esp"], 0)], {})
        assert result.index("RuleMod.esp") == result.index("B.esp") + 1

    def test_before_constraint_does_not_drag_a_block(self):
        """Regression: a 'loads before X' custom used to keep its end position,
        stalling Kahn's at X and dumping every pending custom there at once."""
        subset = ["AAA_Standalone.esp", "Early.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        result = build_and_sort(BASE, subset, [(["Early.esp", "C.esp"], 0)], masters)
        assert result.index("Early.esp") == result.index("C.esp") - 1
        assert result[-1] == "AAA_Standalone.esp"

    def test_esm_first_tiebreak(self):
        subset = ["Cust.esm", "CustAddon.esp"]
        masters = {"cust.esm": VANILLA, "custaddon.esp": ["Cust.esm"]}
        result = build_and_sort(BASE, subset, [], masters)
        assert result.index("Cust.esm") < result.index("A.esp")


class TestDeclarationOrder:
    def test_unconstrained_mods_keep_declared_order(self):
        """Regression: the subset used to be alphabetised, so unconstrained
        mods landed A->Z instead of in the user's own order."""
        subset = ["Zebra.esp", "Apple.esp", "Middle.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        result = build_and_sort(BASE, subset, [], masters)
        assert result[-3:] == subset


class TestCyclesAndPriority:
    def test_user_rules_beat_base_rules(self, capsys):
        """mlox reads mlox_user.txt first so its rules win conflicts."""
        subset = ["X.esp", "Y.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        rules = [(["X.esp", "Y.esp"], 0), (["Y.esp", "X.esp"], 1)]
        result = build_and_sort(["Morrowind.esm"], subset, rules, masters)
        assert result.index("Y.esp") < result.index("X.esp")

    def test_direct_two_cycle_terminates(self, capsys):
        subset = ["P.esp", "Q.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        rules = [(["P.esp", "Q.esp"], 0), (["Q.esp", "P.esp"], 0)]
        result = build_and_sort(["Morrowind.esm"], subset, rules, masters)
        assert sorted(result) == sorted(["Morrowind.esm", *subset])

    def test_long_cycle_terminates(self, capsys):
        """A->B->C->A must not hang or drop plugins."""
        subset = ["A1.esp", "B1.esp", "C1.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        rules = [
            (["A1.esp", "B1.esp"], 0),
            (["B1.esp", "C1.esp"], 0),
            (["C1.esp", "A1.esp"], 0),
        ]
        result = build_and_sort(["Morrowind.esm"], subset, rules, masters)
        assert sorted(result) == sorted(["Morrowind.esm", *subset])


class TestDeterminism:
    def test_repeated_sorts_are_identical(self):
        """Set iteration order is randomised per process; the sort must not be."""
        subset = [f"Mod{i}.esp" for i in range(25)]
        masters = {s.lower(): VANILLA for s in subset}
        rules = [(["Mod3.esp", "Mod9.esp"], 0), (["Mod9.esp", "C.esp"], 0)]
        first = build_and_sort(BASE, subset, rules, masters)
        for _ in range(5):
            assert build_and_sort(BASE, subset, rules, masters) == first


class TestNearHints:
    def test_nearend_pulls_to_the_end_without_chaining(self):
        subset = ["N1.esp", "N2.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        result = build_and_sort(BASE, subset, [], masters, nearend=["N1.esp", "N2.esp"])
        assert set(result[-2:]) == {"N1.esp", "N2.esp"}

    def test_nearstart_pulls_toward_the_front(self):
        result = build_and_sort(
            BASE, ["StartMe.esp"], [], {"startme.esp": VANILLA}, nearstart=["StartMe.esp"]
        )
        assert result.index("StartMe.esp") < result.index("A.esp")

    def test_nearend_accepts_wildcards(self):
        result = build_and_sort(
            BASE, ["Merged Stuff.esp"], [], {"merged stuff.esp": VANILLA}, nearend=["Merged*.esp"]
        )
        assert result[-1] == "Merged Stuff.esp"

    def test_nearend_keeps_the_listed_order(self):
        """A hand-written [NearEnd] means "end in *this* order", so the block's
        order wins over the plugins' existing positions. This is the base plugin
        pinned ahead of the addons built on top of it (none masters the other).
        """
        # Input order has the addons before the base plugin -- the wrong way.
        subset = ["Addon B.esp", "Addon A.esp", "Base.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        result = build_and_sort(
            BASE,
            subset,
            [],
            masters,
            nearend=["Base.esp", "Addon A.esp", "Addon B.esp"],
        )
        assert result[-3:] == ["Base.esp", "Addon A.esp", "Addon B.esp"]

    def test_nearend_listed_order_yields_to_a_real_edge(self):
        """The hint is a soft position; an [Order] rule is a hard edge and wins.
        Listing B before A near the end cannot override "A before B"."""
        subset = ["A.esp", "B.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        result = build_and_sort(
            BASE,
            subset,
            [(["A.esp", "B.esp"], 0)],
            masters,
            nearend=["B.esp", "A.esp"],
        )
        assert result.index("A.esp") < result.index("B.esp")


class TestAnchorReporting:
    def test_anchor_out_reports_why_each_custom_moved(self):
        subset = ["B Patch.esp", "Loose.esp"]
        masters = {"b patch.esp": ["Morrowind.esm", "B.esp"], "loose.esp": VANILLA}
        anchors: dict = {}
        build_and_sort(BASE, subset, [], masters, anchor_out=anchors)
        assert anchors["b patch.esp"] == ("after", "B.esp")
        assert anchors["loose.esp"][0] == "none"


class TestCaseInsensitivity:
    def test_subset_entry_matching_cfg_casing_is_not_duplicated(self, capsys):
        """OpenMW's VFS is case-insensitive, so 'a.esp' and 'A.esp' are one file."""
        result = build_and_sort(BASE, ["a.esp"], [], {"a.esp": VANILLA})
        assert sorted(result) == sorted(BASE)

    def test_two_differently_cased_duplicates_collapse_to_one(self, capsys):
        """Two subset spellings of one cfg plugin canonicalise and de-duplicate."""
        result = build_and_sort(["Morrowind.esm"], ["morrowind.esm", "MORROWIND.ESM"], [], {})
        assert result == ["Morrowind.esm"]


class TestRuleEdgeCorners:
    """Degenerate rule shapes the engine must tolerate without misbehaving."""

    def test_a_rule_naming_one_plugin_twice_adds_no_self_edge(self, capsys):
        """A rule with the same plugin on both sides is a no-op, not a self-loop."""
        result = build_and_sort(BASE, [], [(["A.esp", "A.esp"], 0)], {})
        assert result == BASE

    def test_a_repeated_conflicting_rule_is_reported_once(self, capsys):
        """Two identical rules that would each close the same cycle dedupe to one."""
        subset = ["S.esp"]
        masters = {"s.esp": VANILLA}
        rules = [
            (["S.esp", "A.esp"], 2),  # S before A -- added
            (["A.esp", "S.esp"], 1),  # A before S -- rejected, would cycle
            (["A.esp", "S.esp"], 0),  # the same rejected edge again
        ]
        build_and_sort(BASE, subset, rules, masters)
        out = capsys.readouterr().out
        # The single deduped "not applied" report, not one per repeated rule.
        assert out.count("not applied") == 1

    def test_the_after_anchor_keeps_the_furthest_predecessor(self, capsys):
        """With two predecessors, the later-but-nearer one is not a better anchor.

        Predecessors are visited alphabetically; here that order runs opposite to
        base position, so the second candidate is rejected as no improvement.
        """
        base = ["Morrowind.esm", "Zebra.esp", "Apple.esp"]
        masters = {"p.esp": ["Morrowind.esm", "Zebra.esp", "Apple.esp"]}
        result = build_and_sort(base, ["P.esp"], [], masters)
        assert result.index("P.esp") > result.index("Apple.esp")

    def test_the_before_anchor_keeps_the_earliest_successor(self, capsys):
        """Symmetric: a later successor that sits further along is no improvement."""
        base = ["Morrowind.esm", "Apple.esp", "Zebra.esp"]
        rules = [(["P.esp", "Apple.esp"], 0), (["P.esp", "Zebra.esp"], 0)]
        result = build_and_sort(base, ["P.esp"], rules, {"p.esp": VANILLA})
        assert result.index("P.esp") < result.index("Apple.esp")

    def test_a_custom_derived_from_another_custom_settles(self, capsys):
        """A custom mastering another custom resolves through the position fixpoint."""
        subset = ["P.esp", "Q.esp"]
        # C.esp precedes A.esp among P's masters so the *later* master gives a
        # lower position -- exercising the "not a better anchor" tie-break.
        masters = {
            "p.esp": ["Morrowind.esm", "C.esp", "A.esp"],
            "q.esp": ["Morrowind.esm", "P.esp"],
        }
        result = build_and_sort(BASE, subset, [], masters)
        assert result.index("P.esp") < result.index("Q.esp")
        assert sorted(result) == sorted(BASE + subset)


class TestNearAnchorsAreRecorded:
    def test_a_near_start_hint_is_written_to_anchor_out(self, capsys):
        """A custom plugin hinted near-start is recorded as such for the report."""
        anchors: dict = {}
        build_and_sort(
            BASE,
            ["Custom.esp"],
            [],
            {"custom.esp": VANILLA},
            nearstart=["Custom.esp"],
            anchor_out=anchors,
        )
        assert anchors["custom.esp"] == ("nearstart", None)

    def test_a_near_end_hint_is_written_to_anchor_out(self, capsys):
        """The symmetric near-end case is recorded too."""
        anchors: dict = {}
        build_and_sort(
            BASE,
            ["Custom.esp"],
            [],
            {"custom.esp": VANILLA},
            nearend=["Custom.esp"],
            anchor_out=anchors,
        )
        assert anchors["custom.esp"] == ("nearend", None)
