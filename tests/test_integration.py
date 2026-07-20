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
    def test_normalize_data_path_is_separator_insensitive(self, core):
        assert core.normalize_data_path(r"E:\Mods\Foo") == core.normalize_data_path("E:/Mods/Foo")

    def test_extract_data_path_value_strips_quotes(self, core):
        assert core.extract_data_path_value('data="E:/Mods/Foo"') == "E:/Mods/Foo"

    def test_cfg_line_value_unquotes(self, core):
        assert core.cfg_line_value('data="E:/x"') == "E:/x"
        assert core.cfg_line_value("content=A.esp") == "A.esp"
        assert core.cfg_line_value("no-equals-here") is None

    def test_configurator_remove_matches_mirrors_upstream(self, core):
        # plain names: whole-line substring match (upstream's quirk)
        assert core.configurator_remove_matches("B.esp", "content=NotB.esp")
        # path-like values: exact / suffix match on the value only
        assert core.configurator_remove_matches("SomeMod/00 Core", 'data="E:/M/SomeMod/00 Core"')
        assert not core.configurator_remove_matches("Mod/00 Core", 'data="E:/M/OtherMod/00 Core"')

    def test_all_scan_dirs_dedupes_and_orders(self, core):
        dirs = core.all_scan_dirs(
            ['data="/cfg/a"', 'data="/cfg/b"'],
            [{"value": "/pending/x"}],
            [{"value": "/pending/y"}, {"value": "/CFG/A"}],
        )
        assert dirs == ["/cfg/a", "/cfg/b", "/pending/y", "/pending/x"]

    def test_pattern_has_meta(self, core):
        assert core.pattern_has_meta("Wares*.esp")
        assert core.pattern_has_meta("Mod <VER>.esp")
        assert not core.pattern_has_meta("Plain.esp")

    def test_is_master_file(self, core):
        assert core._is_master_file("X.esm") and core._is_master_file("X.omwgame")
        assert not core._is_master_file("X.esp")


class TestExpandPattern:
    def test_exact_match_is_case_insensitive(self, core):
        assert core.expand_pattern("a.esp", ["A.esp", "B.esp"]) == ["A.esp"]

    def test_wildcard_expands_to_all_matches(self, core):
        pool = ["Wares-base.esm", "Wares_extra.esp", "Other.esp"]
        assert core.expand_pattern("Wares*", pool) == ["Wares-base.esm", "Wares_extra.esp"]

    def test_unmatched_pattern_yields_nothing(self, core):
        assert core.expand_pattern("Nope*.esp", ["A.esp"]) == []


def _sort_real(core):
    """Read the real cfg + rules and sort them, quietly."""

    def run():
        _lines, _cp, content_order, _dp, _do = core.read_cfg(CFG)
        base = [name for name, _ in content_order]
        rules, nearstart, nearend = core.load_rule_blocks(RULES)
        result = core.build_and_sort(base, [], rules, {}, nearstart=nearstart, nearend=nearend)
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
