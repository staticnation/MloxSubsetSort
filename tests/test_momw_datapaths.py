"""Tests for ``wraithguard.momw_datapaths`` -- data-path-order.yml consumption.

The module parses MOMW's ``data-path-order.yml`` (``for_mod`` / ``extra_dirs`` /
``on_lists``), fuzzy-matches each ``for_mod`` to a folder under a mods root,
builds the ordered data paths (existence-checked), and reconciles that intended
order against a cfg's ``data=`` paths. Parsing is covered on both the PyYAML and
the dependency-free hand-parser paths; the matcher, builder and reconciler are
pure and driven with tmp folders.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wraithguard.momw_datapaths import (
    DataPathEntry,
    _score,
    build_data_paths,
    entries_for_list,
    managed_cfg_data_path_norms,
    normalize_mod_name,
    parse_data_path_order_yml,
    reconcile_list_against_cfg,
    reconcile_with_cfg,
    resolve_mod_dir,
)

_YML = """\
- for_mod: "Morrowind"
  extra_dirs:
    - "Data Files"
  on_lists:
    - "total-overhaul"
    - "expanded-vanilla"

- for_mod: "Arktwend - OpenMW port"
  extra_dirs: ["Arktwend", "Data Files"]
  on_lists:
    - "arktwend-enhanced-wip"

- for_mod: "TAO - The Arktwend Overhaul"
  on_lists:
    - "arktwend-enhanced-wip"
"""


def _write(tmp_path: Path, text: str = _YML) -> Path:
    path = tmp_path / "data-path-order.yml"
    path.write_text(text, encoding="utf-8")
    return path


class TestParse:
    def _check(self, entries: list[DataPathEntry]) -> None:
        by_mod = {e.for_mod: e for e in entries}
        assert by_mod["Morrowind"].extra_dirs == ["Data Files"]
        assert by_mod["Arktwend - OpenMW port"].extra_dirs == ["Arktwend", "Data Files"]
        assert by_mod["TAO - The Arktwend Overhaul"].extra_dirs == []  # no extra_dirs
        assert by_mod["Arktwend - OpenMW port"].on_lists == ["arktwend-enhanced-wip"]

    def test_pyyaml_path(self, tmp_path: Path) -> None:
        pytest.importorskip("yaml")
        self._check(parse_data_path_order_yml(_write(tmp_path)))

    def test_hand_parser_path(self, tmp_path: Path, monkeypatch) -> None:
        """With PyYAML hidden, the line parser reads the same shape."""
        monkeypatch.setitem(sys.modules, "yaml", None)
        self._check(parse_data_path_order_yml(_write(tmp_path)))

    def test_hand_parser_reads_inline_and_block_lists(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "yaml", None)
        entries = parse_data_path_order_yml(_write(tmp_path))
        # inline `[a, b]` (Arktwend) and block `- a` (Morrowind) both parse
        assert entries[1].extra_dirs == ["Arktwend", "Data Files"]
        assert entries[0].on_lists == ["total-overhaul", "expanded-vanilla"]

    def test_an_entry_without_a_for_mod_is_dropped(self, tmp_path: Path) -> None:
        pytest.importorskip("yaml")
        path = _write(tmp_path, "- extra_dirs: ['x']\n  on_lists: ['a']\n- for_mod: 'Real'\n")
        mods = [e.for_mod for e in parse_data_path_order_yml(path)]
        assert mods == ["Real"]

    def test_hand_parser_ignores_junk_before_first_entry_and_scalars(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Lines before the first entry, inline scalars, and empty items are skipped."""
        monkeypatch.setitem(sys.modules, "yaml", None)
        text = (
            "# a comment\n"
            "stray: line before any entry\n"
            "- for_mod: 'M'\n"
            "  note: ignored scalar\n"  # indent-2 scalar that isn't a list key
            "  extra_dirs: not-a-bracket-list\n"  # inline non-list -> nothing added
            "    - orphan\n"  # indent-4 item with no active list key -> ignored
            "  on_lists:\n"
            "    - 'l'\n"
            "    - ''\n"  # empty item -> skipped
        )
        entries = parse_data_path_order_yml(_write(tmp_path, text))
        assert len(entries) == 1
        assert entries[0].for_mod == "M"
        assert entries[0].extra_dirs == []  # the non-bracket inline value added nothing
        assert entries[0].on_lists == ["l"]

    def test_hand_parser_on_a_file_with_no_entries(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "yaml", None)
        assert (
            parse_data_path_order_yml(_write(tmp_path, "# just a comment\nnope: nothing\n")) == []
        )

    def test_the_real_shipped_file_parses(self) -> None:
        root = Path(__file__).resolve().parent.parent
        entries = parse_data_path_order_yml(root / "testdata" / "data-path-order.yml")
        assert len(entries) > 100
        assert all(e.for_mod for e in entries)


class TestEntriesForList:
    def test_it_selects_by_list_case_insensitively(self) -> None:
        entries = [
            DataPathEntry("A", on_lists=["total-overhaul"]),
            DataPathEntry("B", on_lists=["arktwend-enhanced-wip"]),
        ]
        assert [e.for_mod for e in entries_for_list(entries, "TOTAL-OVERHAUL")] == ["A"]

    def test_an_empty_on_lists_applies_everywhere(self) -> None:
        entries = [DataPathEntry("Everywhere", on_lists=[])]
        assert entries_for_list(entries, "any-list")[0].for_mod == "Everywhere"

    def test_an_empty_list_name_selects_nothing(self) -> None:
        assert entries_for_list([DataPathEntry("A", on_lists=["x"])], "") == []


class TestNormaliseAndScore:
    def test_normalise_collapses_punctuation_and_case(self) -> None:
        assert normalize_mod_name("TAO - The_Arktwend  Overhaul!") == "tao the arktwend overhaul"

    def test_exact_keys_score_one(self) -> None:
        assert _score("abc", "abc") == 1.0

    def test_a_token_subset_scores_high(self) -> None:
        # "tao" is a subset of the longer token stream
        assert _score("tao", "tao the arktwend overhaul") == 0.9

    def test_unrelated_names_score_low(self) -> None:
        assert _score("better sounds", "morrowind graphics") < 0.6

    def test_an_empty_name_scores_zero(self) -> None:
        assert _score("", "anything") == 0.0

    def test_a_name_matches_its_space_stripped_umo_form(self) -> None:
        # normalize keeps spaces; umo's unpacked folder has them removed
        assert (
            _score(
                normalize_mod_name("TAO - The Arktwend Overhaul"),
                normalize_mod_name("TAOTheArktwendOverhaul"),
            )
            == 1.0
        )


class TestResolveModDir:
    def test_it_matches_a_folder_by_fuzzy_name(self, tmp_path: Path) -> None:
        cands = [("TAO", tmp_path / "TAO"), ("Rocks", tmp_path / "Rocks")]
        path, score, ambiguous = resolve_mod_dir("TAO - The Arktwend Overhaul", cands)
        assert path == tmp_path / "TAO"
        assert score >= 0.6
        assert ambiguous is False

    def test_no_candidate_over_the_floor_is_no_match(self, tmp_path: Path) -> None:
        cands = [("Totally Unrelated Mod", tmp_path / "x")]
        path, _score_val, _amb = resolve_mod_dir("Arktwend OpenMW Port", cands)
        assert path is None

    def test_two_near_ties_are_flagged_ambiguous(self, tmp_path: Path) -> None:
        # Both are token-supersets of "Better Bodies", so both score 0.9 -- a tie
        # the caller should be asked about rather than a silent pick.
        cands = [("Better Bodies Male", tmp_path / "a"), ("Better Bodies Female", tmp_path / "b")]
        _path, _score_val, ambiguous = resolve_mod_dir("Better Bodies", cands)
        assert ambiguous is True

    def test_no_candidates_at_all(self, tmp_path: Path) -> None:
        assert resolve_mod_dir("Anything", []) == (None, 0.0, False)

    def test_it_matches_a_umo_style_space_stripped_folder(self, tmp_path: Path) -> None:
        # umo unpacks "Arktwend - OpenMW port" into a spaces/punctuation-free dir
        cands = [("ArktwendOpenMWport", tmp_path / "ArktwendOpenMWport")]
        path, score, _amb = resolve_mod_dir("Arktwend - OpenMW port", cands)
        assert path == tmp_path / "ArktwendOpenMWport"
        assert score == 1.0


class TestBuildDataPaths:
    def _mods(self, root: Path, layout: dict[str, list[str]]) -> Path:
        for folder, subs in layout.items():
            (root / folder).mkdir()
            for s in subs:
                (root / folder / s).mkdir()
        return root

    def test_it_builds_ordered_existing_paths(self, tmp_path: Path) -> None:
        root = self._mods(tmp_path, {"Arktwend OpenMW port": ["Arktwend", "Data Files"]})
        entries = [
            DataPathEntry(
                "Arktwend - OpenMW port",
                extra_dirs=["Arktwend", "Data Files"],
                on_lists=["arktwend-enhanced-wip"],
            )
        ]
        [res] = build_data_paths(entries, "arktwend-enhanced-wip", root)
        assert res.mod_dir == root / "Arktwend OpenMW port"
        assert res.paths == [
            root / "Arktwend OpenMW port" / "Arktwend",
            root / "Arktwend OpenMW port" / "Data Files",
        ]
        assert res.missing == []

    def test_a_missing_extra_dir_is_recorded_not_pathed(self, tmp_path: Path) -> None:
        root = self._mods(tmp_path, {"Some Mod": ["Data Files"]})
        entries = [DataPathEntry("Some Mod", extra_dirs=["Data Files", "Gone"], on_lists=["l"])]
        [res] = build_data_paths(entries, "l", root)
        assert res.paths == [root / "Some Mod" / "Data Files"]
        assert res.missing == ["Gone"]

    def test_no_extra_dirs_uses_the_mod_dir_itself(self, tmp_path: Path) -> None:
        root = self._mods(tmp_path, {"TAO": []})
        entries = [DataPathEntry("TAO - The Arktwend Overhaul", extra_dirs=[], on_lists=["l"])]
        [res] = build_data_paths(entries, "l", root)
        assert res.paths == [root / "TAO"]

    def test_an_unmatched_mod_yields_no_paths(self, tmp_path: Path) -> None:
        root = self._mods(tmp_path, {"Nothing Alike": []})
        entries = [DataPathEntry("Arktwend OpenMW Port", extra_dirs=["Data Files"], on_lists=["l"])]
        [res] = build_data_paths(entries, "l", root)
        assert res.mod_dir is None
        assert res.paths == []


class TestReconcile:
    def _resolved(self, tmp_path: Path, paths: list[str]):
        from wraithguard.momw_datapaths import ResolvedDataPaths

        entry = DataPathEntry("M", extra_dirs=[], on_lists=["l"])
        return [
            ResolvedDataPaths(
                entry=entry,
                mod_dir=tmp_path,
                score=1.0,
                ambiguous=False,
                paths=[Path(p) for p in paths],
            )
        ]

    def test_a_clean_match_reports_nothing(self, tmp_path: Path) -> None:
        resolved = self._resolved(tmp_path, ["C:/mods/A/Data Files", "C:/mods/B/Data Files"])
        cfg = ["C:/mods/A/Data Files", "C:/mods/B/Data Files"]
        assert reconcile_with_cfg(resolved, cfg) == []

    def test_an_intended_path_absent_from_cfg_is_reported(self, tmp_path: Path) -> None:
        resolved = self._resolved(tmp_path, ["C:/mods/A/Data Files"])
        findings = reconcile_with_cfg(resolved, ["C:/mods/Other/Data Files"])
        assert any("not in openmw.cfg" in f for f in findings)

    def test_a_reversed_order_is_reported(self, tmp_path: Path) -> None:
        resolved = self._resolved(tmp_path, ["C:/mods/A/Data Files", "C:/mods/B/Data Files"])
        cfg = ["C:/mods/B/Data Files", "C:/mods/A/Data Files"]  # swapped
        findings = reconcile_with_cfg(resolved, cfg)
        assert any("data-path order" in f.lower() for f in findings)


class TestReconcileListAgainstCfg:
    _ENTRIES = [
        DataPathEntry("Arktwend - OpenMW port", extra_dirs=["Data Files"], on_lists=["l"]),
        DataPathEntry("TAO - The Arktwend Overhaul", extra_dirs=[], on_lists=["l"]),
        DataPathEntry("Off List", extra_dirs=[], on_lists=["other"]),
    ]

    def test_all_present_in_order_is_clean(self) -> None:
        cfg = ["C:/mods/Arktwend OpenMW port/Data Files", "C:/mods/TAO/Data Files"]
        assert reconcile_list_against_cfg(self._ENTRIES, "l", cfg) == []

    def test_missing_mods_collapse_to_one_summary_line_by_default(self) -> None:
        cfg = ["C:/mods/Arktwend OpenMW port/Data Files"]  # TAO absent
        findings = reconcile_list_against_cfg(self._ENTRIES, "l", cfg)
        missing = [f for f in findings if f.startswith("[DATA PATH]")]
        # One summary line, not one per mod, and it names the count not the mod.
        assert len(missing) == 1
        assert "1 mod(s)" in missing[0]
        assert "TAO" not in missing[0]

    def test_missing_mods_are_listed_when_report_missing(self) -> None:
        cfg = ["C:/mods/Arktwend OpenMW port/Data Files"]  # TAO absent
        findings = reconcile_list_against_cfg(self._ENTRIES, "l", cfg, report_missing=True)
        assert any(f.startswith("[DATA PATH]") and "TAO" in f for f in findings)

    def test_a_reversed_cfg_order_is_reported(self) -> None:
        # TAO (2nd on the list) placed before Arktwend (1st) in the cfg
        cfg = ["C:/mods/TAO/Data Files", "C:/mods/Arktwend OpenMW port/Data Files"]
        findings = reconcile_list_against_cfg(self._ENTRIES, "l", cfg)
        assert any(f.startswith("[DATA PATH ORDER]") for f in findings)

    def test_an_empty_list_selects_nothing(self) -> None:
        assert reconcile_list_against_cfg(self._ENTRIES, "", ["C:/x/Data Files"]) == []

    def test_umo_space_stripped_folders_are_matched(self) -> None:
        """umo's unpacked dirs drop spaces/punctuation; they still reconcile."""
        cfg = [
            "E:/OpenMW/Mods/total-overhaul/ArktwendOpenMWport/Data Files",
            "E:/OpenMW/Mods/total-overhaul/TAOTheArktwendOverhaul",
        ]
        assert reconcile_list_against_cfg(self._ENTRIES, "l", cfg) == []

    def test_umo_category_nested_folders_match(self) -> None:
        """umo lays mods out as <list>/<Category>/<CompactModName>; the mod folder
        is the parent of the data path, so parts[-2:] + compact still matches."""
        entries = [DataPathEntry("On the Blink", extra_dirs=[], on_lists=["total-overhaul"])]
        cfg = ["E:/OpenMW/Mods/total-overhaul/Animation/OntheBlink"]
        assert reconcile_list_against_cfg(entries, "total-overhaul", cfg) == []

    def test_a_name_that_normalises_to_nothing_is_reported_missing(self) -> None:
        """A punctuation-only for_mod has no tokens to match, so it's just missing."""
        entries = [DataPathEntry("!!!", extra_dirs=[], on_lists=["l"])]
        findings = reconcile_list_against_cfg(
            entries, "l", ["C:/mods/Real/Data Files"], report_missing=True
        )
        assert findings and findings[0].startswith("[DATA PATH]")
        assert "!!!" in findings[0]


class TestManagedCfgDataPathNorms:
    _ENTRIES = [
        DataPathEntry("Arktwend - OpenMW port", extra_dirs=["Data Files"], on_lists=["l"]),
        DataPathEntry("TAO - The Arktwend Overhaul", extra_dirs=[], on_lists=["l"]),
        DataPathEntry("Off List", extra_dirs=[], on_lists=["other"]),
    ]

    def test_it_returns_the_normalized_managed_cfg_paths(self) -> None:
        cfg = ["C:/mods/Arktwend OpenMW port/Data Files", "C:/mods/TAO/Data Files"]
        managed = managed_cfg_data_path_norms(self._ENTRIES, "l", cfg)
        assert managed == {
            "c:/mods/arktwend openmw port/data files",
            "c:/mods/tao/data files",
        }

    def test_an_unmatched_list_entry_contributes_nothing(self) -> None:
        # TAO has no cfg path; only Arktwend is managed.
        cfg = ["C:/mods/Arktwend OpenMW port/Data Files"]
        managed = managed_cfg_data_path_norms(self._ENTRIES, "l", cfg)
        assert managed == {"c:/mods/arktwend openmw port/data files"}

    def test_a_cfg_path_off_the_list_is_not_managed(self) -> None:
        # A loose folder no list entry names is absent from the managed set --
        # that is exactly what marks it an orphan to the caller.
        cfg = ["C:/mods/TAO/Data Files", "C:/mods/SomeLooseMod"]
        managed = managed_cfg_data_path_norms(self._ENTRIES, "l", cfg)
        assert "c:/mods/someloosemod" not in managed
        assert "c:/mods/tao/data files" in managed

    def test_an_empty_list_manages_nothing(self) -> None:
        assert managed_cfg_data_path_norms(self._ENTRIES, "", ["C:/x/Data Files"]) == set()

    def test_umo_compact_folders_are_managed(self) -> None:
        cfg = ["E:/Mods/total-overhaul/TAOTheArktwendOverhaul"]
        managed = managed_cfg_data_path_norms(self._ENTRIES, "l", cfg)
        assert managed == {"e:/mods/total-overhaul/taothearktwendoverhaul"}
