"""Tests for ``wraithguard.momw_datapaths`` -- MOMW list data-path reconciliation.

The module reads a cached list of relative data-path *tails* (rendered by the MOMW
``/api/cfg-generator`` API, base directory stripped) and matches a user's
``openmw.cfg`` ``data=`` paths against them by compact suffix, to build the
"managed" set (for orphan detection) and report order drift. Matching must be
independent of the user's mod base directory and any umo per-list sub-folder, and
must handle paths that nest deeper than ``ModName/DataFiles`` -- the case the old
fuzzy matcher missed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wraithguard.momw_datapaths import (
    _compact,
    managed_cfg_data_path_norms,
    parse_data_paths_cache,
    reconcile_data_paths,
    relative_tail,
)

if TYPE_CHECKING:
    from pathlib import Path

# A list's tails, in canonical order. Includes a deep (Patches/<numbered>) path,
# the case the old last-two-components matcher missed.
_TAILS = [
    "ModdingResources/Morrowind/Data Files",
    "CitiesTowns/BeautifulCitiesofMorrowind/Patches/54 Area of effect Arrows",
    "TexturePacks/VurtsMorrowindVisualResurgence/OAAB",
    "Gameplay/ExpansionDelay",
]


def _cfg(*tails: str, base: str = "E:/OpenMW/Mods/total-overhaul") -> list[str]:
    """Simulate cfg data= values by prefixing tails with a user's own base."""
    return [f"{base}/{t}" for t in tails]


class TestCompactAndTail:
    def test_compact_drops_case_and_punctuation(self) -> None:
        assert _compact("TAO - The_Arktwend Overhaul!") == "taothearktwendoverhaul"

    def test_relative_tail_strips_the_placeholder_base(self) -> None:
        v = "C:\\games\\OpenMWMods\\CitiesTowns\\BeautifulCitiesofMorrowind\\Patches"
        assert relative_tail(v) == "CitiesTowns/BeautifulCitiesofMorrowind/Patches"

    def test_relative_tail_unquotes_and_forward_slashes(self) -> None:
        assert relative_tail('"C:/games/OpenMWMods/Tools/MOMWToolsPack"') == "Tools/MOMWToolsPack"

    def test_relative_tail_leaves_an_already_relative_value(self) -> None:
        assert relative_tail("SomeCategory/SomeMod/Data Files") == "SomeCategory/SomeMod/Data Files"


class TestParseCache:
    def test_it_reads_tails_and_skips_comments_and_blanks(self, tmp_path: Path) -> None:
        p = tmp_path / "cache.txt"
        p.write_text(
            "# list: total-overhaul\n\nCategory/Mod/Data Files\n  # a comment\nOther/Thing\n",
            encoding="utf-8",
        )
        assert parse_data_paths_cache(p) == ["Category/Mod/Data Files", "Other/Thing"]

    def test_backslashes_are_normalised(self, tmp_path: Path) -> None:
        p = tmp_path / "cache.txt"
        p.write_text("Category\\Mod\\Data Files\n", encoding="utf-8")
        assert parse_data_paths_cache(p) == ["Category/Mod/Data Files"]


class TestManagedSet:
    def test_matching_is_base_directory_independent(self) -> None:
        cfg = _cfg(*_TAILS, base="D:/whatever/place")
        managed = managed_cfg_data_path_norms(_TAILS, cfg)
        assert len(managed) == len(_TAILS)  # every tail matched under a foreign base

    def test_a_deep_nested_path_is_matched(self) -> None:
        # The old matcher looked only at the last two components and missed this.
        deep = _cfg("CitiesTowns/BeautifulCitiesofMorrowind/Patches/54 Area of effect Arrows")
        managed = managed_cfg_data_path_norms(_TAILS, deep)
        assert len(managed) == 1

    def test_an_unmanaged_path_is_not_matched(self) -> None:
        orphan = _cfg("PlayerHomes/MyHandInstalledShack/Data Files")
        assert managed_cfg_data_path_norms(_TAILS, orphan) == set()

    def test_a_umo_list_subfolder_prefix_still_matches(self) -> None:
        cfg = ["E:/Mods/total-overhaul/Gameplay/ExpansionDelay"]
        assert len(managed_cfg_data_path_norms(_TAILS, cfg)) == 1

    def test_no_tails_manages_nothing(self) -> None:
        assert managed_cfg_data_path_norms([], _cfg(*_TAILS)) == set()

    def test_a_slash_only_tail_is_skipped_not_crashed(self) -> None:
        # a tail with no real components is dropped when indexing, matches nothing
        assert managed_cfg_data_path_norms(["/"], ["E:/x/Something"]) == set()

    def test_a_slash_only_cfg_value_matches_nothing(self) -> None:
        assert managed_cfg_data_path_norms(["Category/Mod"], ["/"]) == set()


class TestReconcile:
    def test_all_present_and_in_order_is_clean(self) -> None:
        assert reconcile_data_paths(_TAILS, _cfg(*_TAILS)) == []

    def test_missing_paths_collapse_to_a_count(self) -> None:
        cfg = _cfg(_TAILS[0])  # only the first of four installed
        findings = reconcile_data_paths(_TAILS, cfg)
        assert len(findings) == 1
        assert "3 of the list's data= path(s) are not in" in findings[0]

    def test_report_missing_lists_each(self) -> None:
        cfg = _cfg(_TAILS[0])
        findings = reconcile_data_paths(_TAILS, cfg, report_missing=True)
        assert any("Gameplay/ExpansionDelay" in f for f in findings)
        assert all(f.startswith("[DATA PATH]") for f in findings)

    def test_out_of_order_paths_are_reported(self) -> None:
        # Present two managed paths in the reverse of the canonical order.
        cfg = _cfg(_TAILS[2], _TAILS[1])
        findings = reconcile_data_paths(_TAILS, cfg)
        assert any(f.startswith("[DATA PATH ORDER]") for f in findings)

    def test_no_tails_is_silent(self) -> None:
        assert reconcile_data_paths([], _cfg(*_TAILS)) == []

    def test_a_cfg_path_not_on_the_list_is_ignored(self) -> None:
        # an orphan cfg path (matches no tail) doesn't break the reconcile
        cfg = [*_cfg(*_TAILS), "E:/x/SomeHandInstalledMod/Data Files"]
        assert reconcile_data_paths(_TAILS, cfg) == []
