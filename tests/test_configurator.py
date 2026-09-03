"""Tests for the momw-configurator simulation and the TOML emitter.

``simulate_configurator_apply`` is a deliberate re-implementation of the real
Go tool's ``cfg/custom.go``. These tests pin the quirks we must match --
substring anchor matching, the multi-match abort, silent multi-removal, and
the asymmetric stacking of same-anchor inserts -- because "faithful to
upstream" is the whole point of that function.
"""

from __future__ import annotations

import pytest

from wraithguard.configurator import (
    generate_customizations_toml,
    preview_configurator_result,
    simulate_configurator_apply,
)
from wraithguard.sort import build_and_sort

CFG = ["content=A.esp", "content=B.esp", "content=C.esp", 'data="E:/Mods/Base"']


def content_lines(lines):
    return [line.split("=", 1)[1] for line in lines if line.startswith("content=")]


class TestInsertSemantics:
    def test_chained_inserts_land_in_order(self):
        toml = """
[[Customizations]]
listName = 'total-overhaul'
[[Customizations.insert]]
insert = 'X.esp'
after = 'B.esp'
[[Customizations.insert]]
insert = 'Y.esp'
after = 'X.esp'
"""
        lines, errs, _ = simulate_configurator_apply(CFG, toml)
        assert not errs
        assert content_lines(lines) == ["A.esp", "B.esp", "X.esp", "Y.esp", "C.esp"]

    def test_same_anchor_after_stacks_in_reverse(self):
        """Upstream computes target+1 for every insert, so equal-anchor
        ``after`` inserts end up reversed. Undocumented -- hence chained
        anchors in our emitter rather than relying on this."""
        toml = """
[[Customizations]]
[[Customizations.insert]]
insert = 'P.esp'
after = 'B.esp'
[[Customizations.insert]]
insert = 'Q.esp'
after = 'B.esp'
"""
        lines, _, _ = simulate_configurator_apply(CFG, toml)
        assert content_lines(lines) == ["A.esp", "B.esp", "Q.esp", "P.esp", "C.esp"]

    def test_same_anchor_before_keeps_file_order(self):
        toml = """
[[Customizations]]
[[Customizations.insert]]
insert = 'P.esp'
before = 'C.esp'
[[Customizations.insert]]
insert = 'Q.esp'
before = 'C.esp'
"""
        lines, _, _ = simulate_configurator_apply(CFG, toml)
        assert content_lines(lines) == ["A.esp", "B.esp", "P.esp", "Q.esp", "C.esp"]

    def test_insert_block_expands_sequentially(self):
        toml = """
[[Customizations]]
[[Customizations.insert]]
insertBlock = '''
M1.esp
M2.esp
'''
after = 'A.esp'
"""
        lines, errs, _ = simulate_configurator_apply(CFG, toml)
        assert not errs
        assert content_lines(lines)[:3] == ["A.esp", "M1.esp", "M2.esp"]

    def test_missing_anchor_is_reported(self):
        toml = """
[[Customizations]]
[[Customizations.insert]]
insert = 'X.esp'
after = 'NoSuchPlugin.esp'
"""
        lines, errs, _ = simulate_configurator_apply(CFG, toml)
        assert lines is not None  # not fatal, just skipped
        assert any("not present" in e for e in errs)


class TestAmbiguityIsFatal:
    def test_ambiguous_anchor_aborts_like_upstream(self):
        """Upstream returns a nil cfg on >1 match; we must abort too."""
        cfg = [*CFG, "content=NotB.esp"]
        toml = """
[[Customizations]]
[[Customizations.insert]]
insert = 'X.esp'
after = 'B.esp'
"""
        lines, errs, _ = simulate_configurator_apply(cfg, toml)
        assert lines is None
        assert any("FATAL" in e for e in errs)

    def test_ambiguity_error_names_the_colliding_lines(self):
        """The message must be self-diagnosing, not just repeat the anchor."""
        cfg = [*CFG, "content=NotB.esp"]
        toml = """
[[Customizations]]
[[Customizations.insert]]
insert = 'X.esp'
after = 'B.esp'
"""
        _, errs, _ = simulate_configurator_apply(cfg, toml)
        joined = " ".join(errs)
        assert "content=B.esp" in joined and "content=NotB.esp" in joined


class TestRemovalSemantics:
    def test_removal_deletes_every_substring_match_silently(self):
        """Upstream has no multi-match guard on removals -- a nested name
        removes both plugins with no error. This is why the emitter warns."""
        cfg = ["content=B.esp", "content=NotB.esp", "content=C.esp"]
        toml = "[[Customizations]]\nremoveContent = ['B.esp']\n"
        lines, errs, _ = simulate_configurator_apply(cfg, toml)
        assert not errs
        assert content_lines(lines) == ["C.esp"]

    def test_path_like_removal_matches_on_value_not_substring(self):
        cfg = ['data="E:/Mods/SomeMod/00 Core"', 'data="E:/Mods/OtherMod/00 Core"']
        toml = "[[Customizations]]\nremoveData = ['SomeMod/00 Core']\n"
        lines, _, _ = simulate_configurator_apply(cfg, toml)
        assert lines == ['data="E:/Mods/OtherMod/00 Core"']


class TestAppendRouting:
    def test_groundcover_and_other_lines_go_to_their_sections(self):
        toml = """
[[Customizations]]
[[Customizations.append]]
append = 'groundcover=gc.esp'
[[Customizations.append]]
append = 'fallback=Weather_x,1'
"""
        lines, errs, _ = simulate_configurator_apply(CFG, toml)
        assert not errs
        assert "groundcover=gc.esp" in lines
        assert "# GROUNDCOVER FILES #" in lines
        assert lines[-1] == "fallback=Weather_x,1"
        assert "# APPENDED LINES #" in lines


class TestEmitterVariety:
    """Anchor kinds, passthrough data inserts, and carried-over append blocks."""

    def test_before_nearstart_and_nearend_hints_are_annotated(self) -> None:
        """Each ordering-hint kind gets its own explanatory comment."""
        final = ["Morrowind.esm", "A.esp", "Before.esp", "Near1.esp", "Near2.esp"]
        subset = {"before.esp", "near1.esp", "near2.esp"}
        anchors = {
            "before.esp": ("before", "A.esp"),
            "near1.esp": ("nearstart", None),
            "near2.esp": ("nearend", None),
        }
        toml = generate_customizations_toml(
            {},
            final,
            subset,
            {"Before.esp": "Before.esp", "Near1.esp": "Near1.esp", "Near2.esp": "Near2.esp"},
            custom_anchors=anchors,
        )
        assert "must load before 'A.esp'" in toml
        assert "mlox [NearStart] hint" in toml
        assert "mlox [NearEnd] hint" in toml

    def test_raw_data_inserts_pass_a_before_anchor_through(self) -> None:
        """Without --sort-data-paths, a raw insert's ``before`` is written as-is."""
        toml = generate_customizations_toml(
            {},
            ["Morrowind.esm"],
            set(),
            {},
            raw_data_inserts=[{"value": "E:/Mods/New", "before": "E:/Mods/Base"}],
        )
        assert "before = 'E:/Mods/Base'" in toml

    def test_append_blocks_from_the_source_are_carried_over(self) -> None:
        """``append`` and ``appendBlock`` entries in the source TOML survive."""
        original = {
            "Customizations": [{"append": [{"append": "foo=bar"}, {"appendBlock": "a\nb"}]}]
        }
        toml = generate_customizations_toml(original, ["Morrowind.esm"], set(), {})
        assert "append = 'foo=bar'" in toml
        assert "appendBlock" in toml

    def test_case_insensitive_duplicate_removes_are_collapsed(self) -> None:
        """A remove list is de-duplicated ignoring case, keeping first order."""
        toml = generate_customizations_toml(
            {}, ["Morrowind.esm"], set(), {}, remove_content=["Foo.esp", "FOO.esp"]
        )
        assert toml.count("Foo.esp") == 1  # the case-variant duplicate was dropped

    def test_a_replace_block_missing_a_source_or_dest_still_emits(self) -> None:
        """Partial replace blocks emit only the halves they carry."""
        original = {
            "Customizations": [
                {"replace": [{"dest": "only-dest"}]},  # no source
                {"replace": [{"source": "only-source"}]},  # no dest
            ]
        }
        toml = generate_customizations_toml(original, ["Morrowind.esm"], set(), {})
        assert "dest = 'only-dest'" in toml
        assert "source = 'only-source'" in toml

    def test_an_unavoidably_ambiguous_data_anchor_still_emits_with_a_warning(self, capsys) -> None:
        """When even the full line cannot disambiguate, the anchor is emitted anyway.

        Two identical frozen ``data=`` lines make every candidate anchor match
        both, so no unique anchor exists in either direction or form; the natural
        anchor is written and the ambiguity is reported rather than dropping the
        insert.
        """
        drt = [
            ('data="E:/Dup"', False, "E:/Dup"),
            ('data="E:/Dup"', False, "E:/Dup"),  # identical -> full line also ambiguous
            ('data="E:/Mine"', True, "E:/Mine"),  # the user's own, inserted after
        ]
        toml = generate_customizations_toml(
            {}, ["Morrowind.esm"], set(), {}, data_result_tuples=drt, user_data_values=["E:/Mine"]
        )
        assert "after = 'E:/Dup'" in toml  # the natural anchor, emitted anyway
        assert "matches 2 openmw.cfg lines" in capsys.readouterr().out

    def test_an_unavoidably_ambiguous_content_anchor_still_emits(self, capsys) -> None:
        """A content insert whose only neighbour is a duplicated name emits anyway."""
        # The insert is at the very start, so it has only a *following* neighbour,
        # and that name is duplicated -- neither the bare name nor the whole line
        # is unique, so the content-side "before" fallback fires.
        final = ["Mine.esp", "Dup.esp", "Dup.esp"]
        toml = generate_customizations_toml({}, final, {"mine.esp"}, {"Mine.esp": "Mine.esp"})
        assert "Dup.esp" in toml
        assert "matches 2 openmw.cfg lines" in capsys.readouterr().out


class TestRoundTrip:
    def test_emitted_toml_reproduces_the_sorted_order(self):
        """The end-to-end promise: what we emit, applied by the Configurator,
        must reproduce exactly what we sorted."""
        base = ["Morrowind.esm", "A.esp", "B.esp", "C.esp"]
        subset = ["B Patch.esp", "Standalone.esp"]
        masters = {
            "b patch.esp": ["Morrowind.esm", "B.esp"],
            "standalone.esp": ["Morrowind.esm"],
        }
        anchors: dict = {}
        final = build_and_sort(base, subset, [], masters, anchor_out=anchors)
        toml = generate_customizations_toml(
            {},
            final,
            {s.lower() for s in subset},
            {s: s for s in subset},
            custom_anchors=anchors,
        )
        ok, report = preview_configurator_result(
            [f"content={n}" for n in base], toml, final, subset
        )
        assert ok, report

    def test_round_trip_detects_a_corrupted_anchor(self):
        base = ["Morrowind.esm", "A.esp", "B.esp", "C.esp"]
        subset = ["B Patch.esp"]
        masters = {"b patch.esp": ["Morrowind.esm", "B.esp"]}
        anchors: dict = {}
        final = build_and_sort(base, subset, [], masters, anchor_out=anchors)
        toml = generate_customizations_toml(
            {}, final, {"b patch.esp"}, {"B Patch.esp": "B Patch.esp"}, custom_anchors=anchors
        ).replace("after = 'B.esp'", "after = 'A.esp'")
        ok, report = preview_configurator_result(
            [f"content={n}" for n in base], toml, final, subset
        )
        assert not ok
        assert any("MISMATCH" in line for line in report)


class TestEmitterHygiene:
    def test_remove_arrays_are_multiline(self, capsys):
        """MOMW's own docs use one entry per line; a 25-entry single line is
        unreadable."""
        toml = generate_customizations_toml(
            {
                "Customizations": [
                    {"listName": "total-overhaul", "removeContent": ["A.ESP", "B.esp"]}
                ]
            },
            ["Morrowind.esm"],
            set(),
            {},
        )
        assert "removeContent = [\n  'A.ESP',\n  'B.esp',\n]" in toml

    def test_ambiguous_emitted_anchor_is_warned_about(self, capsys):
        """Nested plugin names really occur (X.omwscripts inside
        X.omwscripts.esp) and would break the Configurator run."""
        generate_customizations_toml(
            {},
            ["Morrowind.esm", "Incantation.omwscripts", "Incantation.omwscripts.esp", "Mine.esp"],
            {"mine.esp"},
            {"Mine.esp": "Mine.esp"},
            remove_content=["Incantation.omwscripts"],
        )
        out = capsys.readouterr().out
        assert "matches 2 openmw.cfg lines" in out

    def test_inserts_are_annotated_with_their_real_constraint(self):
        """Each annotation now names its plugin.

        A run of plugins shares one ``insertBlock``, so "must load after B.esp"
        on its own no longer says *which* plugin that constraint belongs to.
        The plugin name is prefixed instead.
        """
        base = ["Morrowind.esm", "A.esp", "B.esp"]
        subset = ["B Patch.esp", "Loose.esp"]
        masters = {"b patch.esp": ["Morrowind.esm", "B.esp"], "loose.esp": ["Morrowind.esm"]}
        anchors: dict = {}
        final = build_and_sort(base, subset, [], masters, anchor_out=anchors)
        toml = generate_customizations_toml(
            {},
            final,
            {s.lower() for s in subset},
            {s: s for s in subset},
            custom_anchors=anchors,
        )
        assert "# B Patch.esp: must load after 'B.esp'" in toml
        assert "# Loose.esp: no ordering constraint -- positional only" in toml

    def test_emitted_toml_is_valid_and_reparses(self):
        try:  # 3.11+ stdlib, else the tomli backport the engine also accepts
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - depends on interpreter
            tomllib = pytest.importorskip("tomli", reason="needs tomllib or tomli")
        toml = generate_customizations_toml(
            {"Customizations": [{"listName": "x", "removeContent": ["A.esp"]}]},
            ["Morrowind.esm", "Mine.esp"],
            {"mine.esp"},
            {"Mine.esp": "Mine.esp"},
        )
        parsed = tomllib.loads(toml)
        assert parsed["Customizations"][0]["listName"] == "x"
        assert parsed["Customizations"][0]["removeContent"] == ["A.esp"]


class TestDisableOnlyRemovesWhatWeDoNotOwn:
    """A removal is only needed for a plugin the curated list owns.

    momw-configurator rebuilds openmw.cfg from the curated list plus these
    customizations. A plugin the tool simply stops inserting is already absent
    from the result, so a ``removeContent`` for it is noise in a file people
    hand-edit. The old test was "is it currently in openmw.cfg", which catches
    the user's own mods the moment they have exported once -- reported as
    "disabling my own mod adds a disable block at the top of the TOML".
    """

    BASE = ["Curated.esp", "Mine.esp"]
    CURATED = {"curated.esp"}

    def test_a_curated_plugin_still_gets_a_removal(self, core):
        """It is in the list, so not inserting it would change nothing."""
        assert core.plugins_needing_removal(["Curated.esp"], self.CURATED, self.BASE) == [
            "Curated.esp"
        ]

    def test_the_users_own_mod_is_just_not_inserted(self, core):
        """The reported noise: no block for something we control."""
        assert core.plugins_needing_removal(["Mine.esp"], self.CURATED, self.BASE) == []

    def test_without_a_curated_list_the_old_behaviour_stands(self, core):
        """No plugin-order.yml means "unknown", not "nothing is curated".

        Guessing wrong in that direction would leave a plugin enabled that the
        user asked to disable, so presence in the cfg stays the fallback.
        """
        assert core.plugins_needing_removal(["Mine.esp"], set(), self.BASE) == ["Mine.esp"]

    def test_a_plugin_in_neither_needs_no_removal(self, core):
        """There is nothing to remove it from."""
        assert core.plugins_needing_removal(["NeverSeen.esp"], self.CURATED, self.BASE) == []

    def test_matching_is_case_insensitive(self, core):
        """Plugin names carry whatever case someone typed."""
        assert core.plugins_needing_removal(["CURATED.ESP"], self.CURATED, self.BASE) == [
            "CURATED.ESP"
        ]

    def test_the_result_is_sorted_and_deduplicated(self, core):
        """It goes into a file people read; duplicates would be confusing."""
        disabled = ["Curated.esp", "Curated.esp"]
        assert core.plugins_needing_removal(disabled, self.CURATED, self.BASE) == ["Curated.esp"]

    def test_nothing_disabled_means_no_removals(self, core):
        """The common case must not emit an empty block."""
        assert core.plugins_needing_removal([], self.CURATED, self.BASE) == []


class TestDisableCoversDataPathsToo:
    """The plugin rule, applied to folders.

    A mod is a data path *and* a plugin, so fixing only the plugin half left
    disabling a custom mod still emitting a ``removeData`` block for its folder
    -- the same noise the content fix removed.
    """

    OURS = {"c:/mods/mine"}
    IN_CFG = {"c:/mods/mine", "c:/mods/curated"}

    def test_a_path_we_insert_needs_no_removal(self, core):
        """We can simply stop inserting it."""
        assert (
            core.data_paths_needing_removal(['data="C:/mods/Mine"'], self.OURS, self.IN_CFG) == []
        )

    def test_a_curated_path_still_gets_one(self, core):
        """Nothing else will take it out of the rebuilt cfg."""
        result = core.data_paths_needing_removal(['data="C:/mods/Curated"'], self.OURS, self.IN_CFG)

        assert result == ["C:/mods/Curated"]

    def test_a_path_not_in_the_cfg_needs_nothing(self, core):
        """There is nothing to remove it from."""
        assert (
            core.data_paths_needing_removal(['data="C:/mods/Absent"'], self.OURS, self.IN_CFG) == []
        )

    def test_results_are_sorted_and_deduplicated(self, core):
        """It goes into a file people read."""
        lines = ['data="C:/mods/Curated"', 'data="C:/mods/Curated"']

        assert core.data_paths_needing_removal(lines, self.OURS, self.IN_CFG) == ["C:/mods/Curated"]

    def test_a_bare_path_without_the_data_prefix_is_accepted(self, core):
        """Callers pass raw cfg lines, but not always."""
        assert core.data_paths_needing_removal(["C:/mods/Curated"], self.OURS, self.IN_CFG) == [
            "C:/mods/Curated"
        ]


class TestUnsortedDataInsertPassthrough:
    """generate_customizations_toml's raw_data_inserts branch (--sort-data-paths
    NOT given -- e.g. a GUI drag-and-drop with 'Sort data= paths too' left
    unticked, its default state).

    A manually added folder has no anchor syntax of its own, so `after` and
    `before` are both None here as a matter of course, not an edge case.
    Previously that meant the emitted insert had no before/after and nothing
    -- not the console, not the TOML -- said so. Confirmed against
    simulate_configurator_apply below: an anchor-less insert is not fatal,
    but the real Configurator silently skips it, so the folder never reaches
    openmw.cfg even though its text is sitting right there in the TOML.
    """

    def _emit(self, raw_data_inserts):
        return generate_customizations_toml(
            original_data=None,
            final_content_order=[],
            subset_set=set(),
            original_content_values={},
            raw_data_inserts=raw_data_inserts,
        )

    def test_an_anchorless_insert_is_visibly_flagged(self):
        toml_text = self._emit([{"value": "C:/mods/Dropped", "after": None, "before": None}])
        assert "insert = 'C:/mods/Dropped'" in toml_text
        assert "# WARNING: no anchor for this path" in toml_text

    def test_an_anchored_insert_is_not_flagged(self):
        """The warning must not fire on the entries that were never broken."""
        toml_text = self._emit([{"value": "C:/mods/Fine", "after": "data=E:/Mods/Base"}])
        assert "after = 'data=E:/Mods/Base'" in toml_text
        assert "WARNING" not in toml_text

    def test_the_flagged_insert_is_the_one_the_real_configurator_skips(self):
        """Closes the loop: what the emitter warns about is exactly what
        simulate_configurator_apply (the faithful re-implementation) drops.
        """
        toml_text = self._emit([{"value": "C:/mods/Dropped", "after": None, "before": None}])
        lines, errs, _notes = simulate_configurator_apply(CFG, toml_text)
        assert lines is not None  # not fatal
        assert "C:/mods/Dropped" not in "\n".join(lines)
        assert any("needs insert/insertBlock plus after or before" in e for e in errs)
