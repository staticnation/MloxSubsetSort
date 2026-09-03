"""End-to-end checks and pure-helper tests.

When a real ``openmw.cfg`` and mlox rule files are available, the integration
tests run against them, so the tool's central promise -- the curated order is
never disturbed, and the sort is deterministic -- is verified against reality
rather than only synthetic fixtures.

Sample inputs live in ``testdata/`` (copies of a real setup, not live files).
The lookup order lets the suite run elsewhere too, and skips cleanly when no
data is available:

* ``$MLOX_TEST_DATA_DIR``, if set;
* ``testdata/`` inside the project;
* the project directory and its parent, for a checkout kept inside a larger
  modding workspace.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path

import pytest

from wraithguard.configurator import (
    cfg_line_value,
    configurator_remove_matches,
    extract_data_path_value,
    normalize_data_path,
)
from wraithguard.rules import load_rule_blocks, pattern_has_meta
from wraithguard.sort import build_and_sort, expand_pattern, is_master_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_FILES = ("openmw.cfg", "mlox_base.txt", "mlox_user.txt")


def _find_data_dir() -> Path | None:
    """Locate a directory holding a usable set of real input files.

    Returns:
        The first candidate directory containing every file in
        :data:`REQUIRED_FILES`, or ``None`` when no candidate qualifies.
    """
    candidates = []
    from_env = os.environ.get("MLOX_TEST_DATA_DIR")
    if from_env:
        candidates.append(Path(from_env))
    candidates += [PROJECT_ROOT / "testdata", PROJECT_ROOT, PROJECT_ROOT.parent]
    for candidate in candidates:
        if all((candidate / name).is_file() for name in REQUIRED_FILES):
            return candidate
    return None


DATA_DIR = _find_data_dir()
CFG = (DATA_DIR / "openmw.cfg") if DATA_DIR else PROJECT_ROOT / "openmw.cfg"
RULES = (
    [DATA_DIR / "mlox_base.txt", DATA_DIR / "mlox_user.txt"]
    if DATA_DIR
    else [PROJECT_ROOT / "mlox_base.txt", PROJECT_ROOT / "mlox_user.txt"]
)

real_data = pytest.mark.skipif(
    DATA_DIR is None,
    reason=(
        "no real openmw.cfg + mlox rule files found " "(set MLOX_TEST_DATA_DIR to point at them)"
    ),
)


def _silently(func, *args, **kwargs):
    """Run ``func`` with stdout suppressed -- the engine prints progress."""
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


class TestPathHelpers:
    def test_normalize_data_path_is_separator_insensitive(self):
        assert normalize_data_path(r"E:\Mods\Foo") == normalize_data_path("E:/Mods/Foo")

    def test_extract_data_path_value_strips_quotes(self):
        assert extract_data_path_value('data="E:/Mods/Foo"') == "E:/Mods/Foo"

    def test_cfg_line_value_unquotes(self):
        assert cfg_line_value('data="E:/x"') == "E:/x"
        assert cfg_line_value("content=A.esp") == "A.esp"
        assert cfg_line_value("no-equals-here") is None

    def test_configurator_remove_matches_mirrors_upstream(self):
        # plain names: whole-line substring match (upstream's quirk)
        assert configurator_remove_matches("B.esp", "content=NotB.esp")
        # path-like values: exact / suffix match on the value only
        assert configurator_remove_matches("SomeMod/00 Core", 'data="E:/M/SomeMod/00 Core"')
        assert not configurator_remove_matches("Mod/00 Core", 'data="E:/M/OtherMod/00 Core"')

    def test_all_scan_dirs_dedupes_and_orders(self, core):
        dirs = core.all_scan_dirs(
            ['data="/cfg/a"', 'data="/cfg/b"'],
            [{"value": "/pending/x"}],
            [{"value": "/pending/y"}, {"value": "/CFG/A"}],
        )
        assert dirs == ["/cfg/a", "/cfg/b", "/pending/y", "/pending/x"]

    def test_pattern_has_meta(self):
        assert pattern_has_meta("Wares*.esp")
        assert pattern_has_meta("Mod <VER>.esp")
        assert not pattern_has_meta("Plain.esp")

    def test_is_master_file(self, core):
        assert is_master_file("X.esm") and is_master_file("X.omwgame")
        assert not is_master_file("X.esp")


class TestExpandPattern:
    def test_exact_match_is_case_insensitive(self):
        assert expand_pattern("a.esp", ["A.esp", "B.esp"]) == ["A.esp"]

    def test_wildcard_expands_to_all_matches(self):
        pool = ["Wares-base.esm", "Wares_extra.esp", "Other.esp"]
        assert expand_pattern("Wares*", pool) == ["Wares-base.esm", "Wares_extra.esp"]

    def test_unmatched_pattern_yields_nothing(self):
        assert expand_pattern("Nope*.esp", ["A.esp"]) == []


def _sort_real(core):
    """Read the real cfg + rules and sort them, quietly."""

    def run():
        _lines, _cp, content_order, _dp, _do = core.read_cfg(CFG)
        base = [name for name, _ in content_order]
        rules, nearstart, nearend = load_rule_blocks(RULES)
        result = build_and_sort(base, [], rules, {}, nearstart=nearstart, nearend=nearend)
        return base, result, rules

    return _silently(run)


@real_data
class TestRealLoadOrder:
    def test_curated_order_is_preserved_exactly(self, core):
        base, result, _rules = _sort_real(core)
        assert result == base

    def test_every_plugin_is_placed_once(self, core):
        base, result, _rules = _sort_real(core)
        assert len(result) == len(base) == len(set(result))

    def test_rule_files_parse_into_many_blocks(self, core):
        _base, _result, rules = _sort_real(core)
        assert len(rules) > 1000, "the real rule base should yield thousands of blocks"

    def test_sort_is_deterministic_across_runs(self, core):
        _b1, first, _r1 = _sort_real(core)
        _b2, second, _r2 = _sort_real(core)
        assert first == second


class TestGroundcoverOnRealData:
    """The groundcover hold-back, against a real 687-plugin openmw.cfg.

    The synthetic cases in ``test_hardening.py`` prove the rule; this proves it
    survives contact with a real setup, which contains the one case a filename
    heuristic would get wrong.
    """

    def test_a_blank_groundcover_line_is_skipped(self, core) -> None:
        """``groundcover=`` with nothing after it (a stray blank line) names no plugin."""
        assert core.read_groundcover_names(["groundcover=", "groundcover=  "]) == []

    def test_the_real_cfg_declares_grass_separately(self, core) -> None:
        """A real MOMW-style setup has both kinds of line, in quantity.

        Args:
            core: The engine module.
        """
        lines, _cp, content, _dp, _data = core.read_cfg(CFG)
        groundcover = core.read_groundcover_names(lines)

        assert len(content) > 500, "the sample cfg is not the real one"
        assert len(groundcover) > 10, "the sample cfg has no groundcover lines to test against"

    def test_a_plugin_named_groundcover_is_not_treated_as_grass(self, core) -> None:
        """``deleted_groundcover.omwaddon`` is content, and its name says grass.

        This is why the rule is "what the cfg declares" and not "what the file
        is called": a ``*groundcover*`` pattern would hold this one back and
        silently drop a plugin the user wants loaded.

        Args:
            core: The engine module.
        """
        lines, _cp, content, _dp, _data = core.read_cfg(CFG)
        names = [name for name, _raw in content]
        groundcover = core.read_groundcover_names(lines)

        assert "deleted_groundcover.omwaddon" in names
        assert "deleted_groundcover.omwaddon" not in groundcover

        kept, held = core.hold_back_groundcover(["deleted_groundcover.omwaddon"], groundcover)
        assert kept == ["deleted_groundcover.omwaddon"]
        assert held == []

    def test_no_plugin_is_declared_both_ways(self, core) -> None:
        """The state the fix prevents, absent from a healthy cfg.

        Args:
            core: The engine module.
        """
        lines, _cp, content, _dp, _data = core.read_cfg(CFG)
        both = {name.lower() for name, _raw in content} & {
            name.lower() for name in core.read_groundcover_names(lines)
        }

        assert not both, f"declared as both content and groundcover: {sorted(both)}"

    def test_every_declared_grass_plugin_would_be_held_back(self, core) -> None:
        """If a scan swept them all into the subset, none would reach content=.

        Args:
            core: The engine module.
        """
        lines, *_rest = core.read_cfg(CFG)
        groundcover = core.read_groundcover_names(lines)

        kept, held = core.hold_back_groundcover([*groundcover, "MyNewQuest.esp"], groundcover)

        assert kept == ["MyNewQuest.esp"]
        assert held == groundcover
