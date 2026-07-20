"""Tests for the load-order sort engine (``build_and_sort``).

The invariants here are the tool's core promise: the curated MOMW order is
never reordered, only the user's own additions move, and conflicting rules are
discarded rather than forced (matching mlox's documented behaviour).
"""

from __future__ import annotations

BASE = ["Morrowind.esm", "Tribunal.esm", "A.esp", "B.esp", "C.esp", "D.esp"]
VANILLA = ["Morrowind.esm"]


def curated(order, subset):
    """The frozen (non-custom) plugins, in order."""
    lowered = {s.lower() for s in subset}
    return [n for n in order if n.lower() not in lowered]


class TestCuratedOrderIsFrozen:
    def test_curated_order_never_changes(self, core):
        subset = ["Zebra.esp", "Apple.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        result = core.build_and_sort(BASE, subset, [], masters)
        assert curated(result, subset) == BASE

    def test_rule_fighting_curated_order_is_discarded(self, core, capsys):
        """mlox: 'whenever we encounter a rule that would cause a cycle, it is
        discarded'. Here the rule contradicts the frozen order."""
        result = core.build_and_sort(BASE, [], [(["C.esp", "A.esp"], 0)], {})
        assert result == BASE

    def test_all_plugins_are_placed_exactly_once(self, core):
        subset = ["New1.esp", "New2.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        result = core.build_and_sort(BASE, subset, [], masters)
        assert sorted(result) == sorted(BASE + subset)
        assert len(result) == len(set(result))


class TestAnchoring:
    def test_patch_lands_after_the_plugin_it_masters(self, core):
        result = core.build_and_sort(
            BASE, ["B Patch.esp"], [], {"b patch.esp": ["Morrowind.esm", "B.esp"]}
        )
        assert result.index("B Patch.esp") == result.index("B.esp") + 1

    def test_custom_to_custom_chain_resolves_transitively(self, core):
        subset = ["MyMod.esp", "MyMod Patch.esp"]
        masters = {
            "mymod.esp": ["Morrowind.esm", "C.esp"],
            "mymod patch.esp": ["Morrowind.esm", "MyMod.esp"],
        }
        result = core.build_and_sort(BASE, subset, [], masters)
        assert result.index("MyMod.esp") == result.index("C.esp") + 1
        assert result.index("MyMod Patch.esp") == result.index("MyMod.esp") + 1

    def test_esm_only_dependency_is_not_a_position_signal(self, core):
        """Anchoring to shared resource ESMs would cluster everything at the
        front, so a mod that only depends on masters goes to the end."""
        base = ["Morrowind.esm", "OAAB_Data.esm", "A.esp", "B.esp"]
        result = core.build_and_sort(
            base, ["Standalone.esp"], [], {"standalone.esp": ["Morrowind.esm", "OAAB_Data.esm"]}
        )
        assert result[-1] == "Standalone.esp"

    def test_rule_edge_alone_anchors_a_custom(self, core):
        result = core.build_and_sort(BASE, ["RuleMod.esp"], [(["B.esp", "RuleMod.esp"], 0)], {})
        assert result.index("RuleMod.esp") == result.index("B.esp") + 1

    def test_before_constraint_does_not_drag_a_block(self, core):
        """Regression: a 'loads before X' custom used to keep its end position,
        stalling Kahn's at X and dumping every pending custom there at once."""
        subset = ["AAA_Standalone.esp", "Early.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        result = core.build_and_sort(BASE, subset, [(["Early.esp", "C.esp"], 0)], masters)
        assert result.index("Early.esp") == result.index("C.esp") - 1
        assert result[-1] == "AAA_Standalone.esp"

    def test_esm_first_tiebreak(self, core):
        subset = ["Cust.esm", "CustAddon.esp"]
        masters = {"cust.esm": VANILLA, "custaddon.esp": ["Cust.esm"]}
        result = core.build_and_sort(BASE, subset, [], masters)
        assert result.index("Cust.esm") < result.index("A.esp")


class TestDeclarationOrder:
    def test_unconstrained_mods_keep_declared_order(self, core):
        """Regression: the subset used to be alphabetised, so unconstrained
        mods landed A->Z instead of in the user's own order."""
        subset = ["Zebra.esp", "Apple.esp", "Middle.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        result = core.build_and_sort(BASE, subset, [], masters)
        assert result[-3:] == subset


class TestCyclesAndPriority:
    def test_user_rules_beat_base_rules(self, core, capsys):
        """mlox reads mlox_user.txt first so its rules win conflicts."""
        subset = ["X.esp", "Y.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        rules = [(["X.esp", "Y.esp"], 0), (["Y.esp", "X.esp"], 1)]
        result = core.build_and_sort(["Morrowind.esm"], subset, rules, masters)
        assert result.index("Y.esp") < result.index("X.esp")

    def test_direct_two_cycle_terminates(self, core, capsys):
        subset = ["P.esp", "Q.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        rules = [(["P.esp", "Q.esp"], 0), (["Q.esp", "P.esp"], 0)]
        result = core.build_and_sort(["Morrowind.esm"], subset, rules, masters)
        assert sorted(result) == sorted(["Morrowind.esm", *subset])

    def test_long_cycle_terminates(self, core, capsys):
        """A->B->C->A must not hang or drop plugins."""
        subset = ["A1.esp", "B1.esp", "C1.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        rules = [
            (["A1.esp", "B1.esp"], 0),
            (["B1.esp", "C1.esp"], 0),
            (["C1.esp", "A1.esp"], 0),
        ]
        result = core.build_and_sort(["Morrowind.esm"], subset, rules, masters)
        assert sorted(result) == sorted(["Morrowind.esm", *subset])


class TestDeterminism:
    def test_repeated_sorts_are_identical(self, core):
        """Set iteration order is randomised per process; the sort must not be."""
        subset = [f"Mod{i}.esp" for i in range(25)]
        masters = {s.lower(): VANILLA for s in subset}
        rules = [(["Mod3.esp", "Mod9.esp"], 0), (["Mod9.esp", "C.esp"], 0)]
        first = core.build_and_sort(BASE, subset, rules, masters)
        for _ in range(5):
            assert core.build_and_sort(BASE, subset, rules, masters) == first


class TestNearHints:
    def test_nearend_pulls_to_the_end_without_chaining(self, core):
        subset = ["N1.esp", "N2.esp"]
        masters = {s.lower(): VANILLA for s in subset}
        result = core.build_and_sort(BASE, subset, [], masters, nearend=["N1.esp", "N2.esp"])
        assert set(result[-2:]) == {"N1.esp", "N2.esp"}

    def test_nearstart_pulls_toward_the_front(self, core):
        result = core.build_and_sort(
            BASE, ["StartMe.esp"], [], {"startme.esp": VANILLA}, nearstart=["StartMe.esp"]
        )
        assert result.index("StartMe.esp") < result.index("A.esp")

    def test_nearend_accepts_wildcards(self, core):
        result = core.build_and_sort(
            BASE, ["Merged Stuff.esp"], [], {"merged stuff.esp": VANILLA}, nearend=["Merged*.esp"]
        )
        assert result[-1] == "Merged Stuff.esp"


class TestAnchorReporting:
    def test_anchor_out_reports_why_each_custom_moved(self, core):
        subset = ["B Patch.esp", "Loose.esp"]
        masters = {"b patch.esp": ["Morrowind.esm", "B.esp"], "loose.esp": VANILLA}
        anchors: dict = {}
        core.build_and_sort(BASE, subset, [], masters, anchor_out=anchors)
        assert anchors["b patch.esp"] == ("after", "B.esp")
        assert anchors["loose.esp"][0] == "none"


class TestCaseInsensitivity:
    def test_subset_entry_matching_cfg_casing_is_not_duplicated(self, core, capsys):
        """OpenMW's VFS is case-insensitive, so 'a.esp' and 'A.esp' are one file."""
        result = core.build_and_sort(BASE, ["a.esp"], [], {"a.esp": VANILLA})
        assert sorted(result) == sorted(BASE)
