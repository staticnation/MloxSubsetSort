"""Tests for the momw-configurator simulation and the TOML emitter.

``simulate_configurator_apply`` is a deliberate re-implementation of the real
Go tool's ``cfg/custom.go``. These tests pin the quirks we must match --
substring anchor matching, the multi-match abort, silent multi-removal, and
the asymmetric stacking of same-anchor inserts -- because "faithful to
upstream" is the whole point of that function.
"""

from __future__ import annotations

import pytest

CFG = ["content=A.esp", "content=B.esp", "content=C.esp", 'data="E:/Mods/Base"']


def content_lines(lines):
    return [line.split("=", 1)[1] for line in lines if line.startswith("content=")]


class TestInsertSemantics:
    def test_chained_inserts_land_in_order(self, core):
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
        lines, errs, _ = core.simulate_configurator_apply(CFG, toml)
        assert not errs
        assert content_lines(lines) == ["A.esp", "B.esp", "X.esp", "Y.esp", "C.esp"]

    def test_same_anchor_after_stacks_in_reverse(self, core):
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
        lines, _, _ = core.simulate_configurator_apply(CFG, toml)
        assert content_lines(lines) == ["A.esp", "B.esp", "Q.esp", "P.esp", "C.esp"]

    def test_same_anchor_before_keeps_file_order(self, core):
        toml = """
[[Customizations]]
[[Customizations.insert]]
insert = 'P.esp'
before = 'C.esp'
[[Customizations.insert]]
insert = 'Q.esp'
before = 'C.esp'
"""
        lines, _, _ = core.simulate_configurator_apply(CFG, toml)
        assert content_lines(lines) == ["A.esp", "B.esp", "P.esp", "Q.esp", "C.esp"]

    def test_insert_block_expands_sequentially(self, core):
        toml = """
[[Customizations]]
[[Customizations.insert]]
insertBlock = '''
M1.esp
M2.esp
'''
after = 'A.esp'
"""
        lines, errs, _ = core.simulate_configurator_apply(CFG, toml)
        assert not errs
        assert content_lines(lines)[:3] == ["A.esp", "M1.esp", "M2.esp"]

    def test_missing_anchor_is_reported(self, core):
        toml = """
[[Customizations]]
[[Customizations.insert]]
insert = 'X.esp'
after = 'NoSuchPlugin.esp'
"""
        lines, errs, _ = core.simulate_configurator_apply(CFG, toml)
        assert lines is not None  # not fatal, just skipped
        assert any("not present" in e for e in errs)


class TestAmbiguityIsFatal:
    def test_ambiguous_anchor_aborts_like_upstream(self, core):
        """Upstream returns a nil cfg on >1 match; we must abort too."""
        cfg = [*CFG, "content=NotB.esp"]
        toml = """
[[Customizations]]
[[Customizations.insert]]
insert = 'X.esp'
after = 'B.esp'
"""
        lines, errs, _ = core.simulate_configurator_apply(cfg, toml)
        assert lines is None
        assert any("FATAL" in e for e in errs)

    def test_ambiguity_error_names_the_colliding_lines(self, core):
        """The message must be self-diagnosing, not just repeat the anchor."""
        cfg = [*CFG, "content=NotB.esp"]
        toml = """
[[Customizations]]
[[Customizations.insert]]
insert = 'X.esp'
after = 'B.esp'
"""
        _, errs, _ = core.simulate_configurator_apply(cfg, toml)
        joined = " ".join(errs)
        assert "content=B.esp" in joined and "content=NotB.esp" in joined


class TestRemovalSemantics:
    def test_removal_deletes_every_substring_match_silently(self, core):
        """Upstream has no multi-match guard on removals -- a nested name
        removes both plugins with no error. This is why the emitter warns."""
        cfg = ["content=B.esp", "content=NotB.esp", "content=C.esp"]
        toml = "[[Customizations]]\nremoveContent = ['B.esp']\n"
        lines, errs, _ = core.simulate_configurator_apply(cfg, toml)
        assert not errs
        assert content_lines(lines) == ["C.esp"]

    def test_path_like_removal_matches_on_value_not_substring(self, core):
        cfg = ['data="E:/Mods/SomeMod/00 Core"', 'data="E:/Mods/OtherMod/00 Core"']
        toml = "[[Customizations]]\nremoveData = ['SomeMod/00 Core']\n"
        lines, _, _ = core.simulate_configurator_apply(cfg, toml)
        assert lines == ['data="E:/Mods/OtherMod/00 Core"']


class TestAppendRouting:
    def test_groundcover_and_other_lines_go_to_their_sections(self, core):
        toml = """
[[Customizations]]
[[Customizations.append]]
append = 'groundcover=gc.esp'
[[Customizations.append]]
append = 'fallback=Weather_x,1'
"""
        lines, errs, _ = core.simulate_configurator_apply(CFG, toml)
        assert not errs
        assert "groundcover=gc.esp" in lines
        assert "# GROUNDCOVER FILES #" in lines
        assert lines[-1] == "fallback=Weather_x,1"
        assert "# APPENDED LINES #" in lines


class TestRoundTrip:
    def test_emitted_toml_reproduces_the_sorted_order(self, core):
        """The end-to-end promise: what we emit, applied by the Configurator,
        must reproduce exactly what we sorted."""
        base = ["Morrowind.esm", "A.esp", "B.esp", "C.esp"]
        subset = ["B Patch.esp", "Standalone.esp"]
        masters = {
            "b patch.esp": ["Morrowind.esm", "B.esp"],
            "standalone.esp": ["Morrowind.esm"],
        }
        anchors: dict = {}
        final = core.build_and_sort(base, subset, [], masters, anchor_out=anchors)
        toml = core.generate_customizations_toml(
            {},
            final,
            {s.lower() for s in subset},
            {s: s for s in subset},
            custom_anchors=anchors,
        )
        ok, report = core.preview_configurator_result(
            [f"content={n}" for n in base], toml, final, subset
        )
        assert ok, report

    def test_round_trip_detects_a_corrupted_anchor(self, core):
        base = ["Morrowind.esm", "A.esp", "B.esp", "C.esp"]
        subset = ["B Patch.esp"]
        masters = {"b patch.esp": ["Morrowind.esm", "B.esp"]}
        anchors: dict = {}
        final = core.build_and_sort(base, subset, [], masters, anchor_out=anchors)
        toml = core.generate_customizations_toml(
            {}, final, {"b patch.esp"}, {"B Patch.esp": "B Patch.esp"}, custom_anchors=anchors
        ).replace("after = 'B.esp'", "after = 'A.esp'")
        ok, report = core.preview_configurator_result(
            [f"content={n}" for n in base], toml, final, subset
        )
        assert not ok
        assert any("MISMATCH" in line for line in report)


class TestEmitterHygiene:
    def test_remove_arrays_are_multiline(self, core, capsys):
        """MOMW's own docs use one entry per line; a 25-entry single line is
        unreadable."""
        toml = core.generate_customizations_toml(
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

    def test_ambiguous_emitted_anchor_is_warned_about(self, core, capsys):
        """Nested plugin names really occur (X.omwscripts inside
        X.omwscripts.esp) and would break the Configurator run."""
        core.generate_customizations_toml(
            {},
            ["Morrowind.esm", "Incantation.omwscripts", "Incantation.omwscripts.esp", "Mine.esp"],
            {"mine.esp"},
            {"Mine.esp": "Mine.esp"},
            remove_content=["Incantation.omwscripts"],
        )
        out = capsys.readouterr().out
        assert "matches 2 openmw.cfg lines" in out

    def test_inserts_are_annotated_with_their_real_constraint(self, core):
        base = ["Morrowind.esm", "A.esp", "B.esp"]
        subset = ["B Patch.esp", "Loose.esp"]
        masters = {"b patch.esp": ["Morrowind.esm", "B.esp"], "loose.esp": ["Morrowind.esm"]}
        anchors: dict = {}
        final = core.build_and_sort(base, subset, [], masters, anchor_out=anchors)
        toml = core.generate_customizations_toml(
            {},
            final,
            {s.lower() for s in subset},
            {s: s for s in subset},
            custom_anchors=anchors,
        )
        assert "# constraint: must load after 'B.esp'" in toml
        assert "# no ordering constraint -- positional only" in toml

    def test_emitted_toml_is_valid_and_reparses(self, core):
        try:  # 3.11+ stdlib, else the tomli backport the engine also accepts
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - depends on interpreter
            tomllib = pytest.importorskip("tomli", reason="needs tomllib or tomli")
        toml = core.generate_customizations_toml(
            {"Customizations": [{"listName": "x", "removeContent": ["A.esp"]}]},
            ["Morrowind.esm", "Mine.esp"],
            {"mine.esp"},
            {"Mine.esp": "Mine.esp"},
        )
        parsed = tomllib.loads(toml)
        assert parsed["Customizations"][0]["listName"] == "x"
        assert parsed["Customizations"][0]["removeContent"] == ["A.esp"]
