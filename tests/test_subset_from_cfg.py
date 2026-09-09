"""Pulling unmanaged (orphan) entries out of openmw.cfg as a subset source.

Three layers: the pure classifier ``orphan_cfg_entries`` (and its
``is_base_data_path`` helper), the ``_pull_cfg_orphans`` wiring stage that
appends the orphans to the subset and records their data paths, and the
``--subset-from-cfg`` flag threaded all the way through ``compute_plan``.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import wraithguard_toolkit as core
from wraithguard.configurator import curated_covers, is_base_data_path, orphan_cfg_entries

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class TestCuratedCovers:
    def test_a_plain_list_plugin_is_covered(self) -> None:
        assert curated_covers("Foo.esp", {"foo.esp"}, set())

    def test_the_umo_clean_download_of_a_needs_cleaning_plugin_is_covered(self) -> None:
        # yml names Foo.esp (needs_cleaning); the file on disk is Clean_Foo.esp.
        assert curated_covers("Clean_Foo.ESP", {"foo.esp"}, {"foo.esp"})

    def test_a_clean_download_whose_base_is_not_needs_cleaning_is_not_covered(self) -> None:
        assert not curated_covers("Clean_Foo.ESP", {"foo.esp"}, set())

    def test_a_yml_entry_already_prefixed_is_covered_by_exact_match(self) -> None:
        # Some yml entries carry the clean_ prefix in file_name themselves.
        name = "clean_argonian full helms lore integrated.esp"
        assert curated_covers("Clean_Argonian Full Helms Lore Integrated.ESP", {name}, {name})

    def test_only_the_first_clean_prefix_is_stripped_for_stacked_names(self) -> None:
        # An outside tool stacked clean_ onto an already-clean list name; one
        # strip lands on the clean_ base, which is on the list and needs-cleaning.
        base = "clean_foo.esp"
        assert curated_covers("Clean_Clean_Foo.ESP", {base}, {base})

    def test_a_custom_plugin_that_merely_starts_with_clean_is_not_covered(self) -> None:
        assert not curated_covers("Clean_MyMod.esp", {"foo.esp"}, {"foo.esp"})

    def test_the_clean_download_is_not_pulled_as_an_orphan(self) -> None:
        plugins, _data = orphan_cfg_entries(
            ["Clean_Foo.ESP", "Loose.esp"],
            [],
            curated_lower={"foo.esp"},
            needs_cleaning_lower={"foo.esp"},
            declared_plugins_lower=set(),
            declared_data_norms=set(),
        )
        assert plugins == ["Loose.esp"]


class TestIsBaseDataPath:
    def test_the_vanilla_install_folder_is_recognised(self) -> None:
        assert is_base_data_path("C:/Games/Morrowind/Data Files")

    def test_it_is_case_and_quote_and_slash_insensitive(self) -> None:
        assert is_base_data_path('"C:\\Games\\Morrowind\\DATA FILES\\"')

    def test_a_mod_folder_is_not_the_base_install(self) -> None:
        assert not is_base_data_path("C:/Mods/BetterBodies")


class TestOrphanCfgEntries:
    def test_orphans_are_kept_in_cfg_order(self) -> None:
        plugins, _data = orphan_cfg_entries(
            ["B_Orphan.esp", "A_Orphan.esp"],
            [],
            curated_lower=set(),
            needs_cleaning_lower=set(),
            declared_plugins_lower=set(),
            declared_data_norms=set(),
        )
        assert plugins == ["B_Orphan.esp", "A_Orphan.esp"]

    def test_base_masters_are_never_orphans(self) -> None:
        plugins, _data = orphan_cfg_entries(
            ["Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm", "Mine.esp"],
            [],
            curated_lower=set(),
            needs_cleaning_lower=set(),
            declared_plugins_lower=set(),
            declared_data_norms=set(),
        )
        assert plugins == ["Mine.esp"]

    def test_curated_and_declared_plugins_are_excluded(self) -> None:
        plugins, _data = orphan_cfg_entries(
            ["Curated.esp", "Declared.esp", "Loose.esp"],
            [],
            curated_lower={"curated.esp"},
            needs_cleaning_lower=set(),
            declared_plugins_lower={"declared.esp"},
            declared_data_norms=set(),
        )
        assert plugins == ["Loose.esp"]

    def test_data_paths_drop_the_declared_and_the_base_install(self) -> None:
        _plugins, data = orphan_cfg_entries(
            [],
            [
                'data="C:/Games/Morrowind/Data Files"',
                "data=C:/Mods/Declared",
                "data=C:/Mods/Loose",
            ],
            curated_lower=set(),
            needs_cleaning_lower=set(),
            declared_plugins_lower=set(),
            declared_data_norms={"c:/mods/declared"},
        )
        assert data == ["C:/Mods/Loose"]


class TestPullCfgOrphans:
    @staticmethod
    def _run(
        *,
        subset_from_cfg: bool = True,
        sort_data_paths: bool = False,
        groundcover=frozenset(),
        data_paths_cache=None,
    ):
        args = types.SimpleNamespace(
            subset_from_cfg=subset_from_cfg,
            sort_data_paths=sort_data_paths,
            data_paths_cache=data_paths_cache,
        )
        subset: list[str] = []
        data_inserts: list[dict] = []
        raw_toml_data_inserts: list[dict] = [{"value": "C:/Mods/Declared"}]
        original_content_values: dict[str, str] = {}
        subset_origins: dict[str, str] = {}
        base_order_names = ["Morrowind.esm", "Curated.esp", "Loose1.esp", "Loose2.esp"]
        data_order = [
            'data="C:/Games/Morrowind/Data Files"',
            "data=C:/Mods/Declared",
            "data=E:/Mods/TO/Category/Loose",
        ]
        subset, orphan_data = core._pull_cfg_orphans(
            args,
            subset,
            data_inserts,
            raw_toml_data_inserts,
            original_content_values,
            subset_origins,
            base_order_names,
            data_order,
            {"curated.esp"},
            set(),
            set(),
            groundcover,
        )
        return {
            "subset": subset,
            "orphan_data": orphan_data,
            "data_inserts": data_inserts,
            "raw_toml_data_inserts": raw_toml_data_inserts,
            "original_content_values": original_content_values,
            "subset_origins": subset_origins,
        }

    def test_orphan_plugins_are_appended_with_an_origin(self) -> None:
        out = self._run()
        assert out["subset"] == ["Loose1.esp", "Loose2.esp"]
        assert out["subset_origins"]["loose1.esp"] == "openmw.cfg (orphan)"
        assert out["original_content_values"]["Loose1.esp"] == "Loose1.esp"

    @staticmethod
    def _cache(tmp_path: Path, *tails: str) -> Path:
        p = tmp_path / "data-paths.txt"
        p.write_text("# list cache\n" + "\n".join(tails) + "\n", encoding="utf-8")
        return p

    def test_orphan_data_is_not_classified_without_a_cache(self) -> None:
        # Without a data paths cache there's no signal for which data= paths a
        # list manages, so none are surfaced (and none are ever re-inserted --
        # they already sit in the cfg's data= order).
        for flag in (False, True):
            out = self._run(sort_data_paths=flag)
            assert out["orphan_data"] == []
            assert out["data_inserts"] == []
            assert out["raw_toml_data_inserts"] == [{"value": "C:/Mods/Declared"}]

    def test_orphan_data_is_surfaced_with_a_cache(self, tmp_path: Path) -> None:
        # The cache manages Category/Loose (matches the cfg's E:/.../Category/Loose);
        # "Declared" is a customization. Neither is an orphan, so nothing surfaces.
        cache = self._cache(tmp_path, "Category/Loose")
        out = self._run(data_paths_cache=cache)
        assert out["orphan_data"] == []
        # data= paths are surfaced, never re-inserted.
        assert out["data_inserts"] == []

    def test_an_unmanaged_data_path_is_an_orphan(self, tmp_path: Path) -> None:
        # The cache manages nothing that matches the cfg, so the loose (undeclared,
        # non-base) data= path is surfaced as an orphan.
        cache = self._cache(tmp_path, "Category/SomethingElseEntirely")
        out = self._run(data_paths_cache=cache)
        # "Declared" is a customization; "Loose" is the sole unmanaged orphan.
        assert out["orphan_data"] == ["E:/Mods/TO/Category/Loose"]

    def test_a_groundcover_plugin_is_never_pulled_into_content(self) -> None:
        out = self._run(groundcover=frozenset({"loose2.esp"}))
        assert out["subset"] == ["Loose1.esp"]

    def test_the_flag_off_is_a_no_op(self) -> None:
        out = self._run(subset_from_cfg=False)
        assert out["subset"] == []
        assert out["orphan_data"] == []
        assert out["data_inserts"] == []
        assert out["raw_toml_data_inserts"] == [{"value": "C:/Mods/Declared"}]

    def test_no_orphans_at_all_prints_a_clean_bill(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Every content= plugin is already curated or declared -- nothing left to pull."""
        args = types.SimpleNamespace(
            subset_from_cfg=True,
            sort_data_paths=False,
            data_paths_cache=None,
        )
        core._pull_cfg_orphans(
            args,
            [],
            [],
            [],
            {},
            {},
            ["Morrowind.esm", "Curated.esp"],
            ['data="C:/Games/Morrowind/Data Files"'],
            {"curated.esp"},
            set(),
            set(),
            frozenset(),
        )

        assert "No unmanaged plugins found" in capsys.readouterr().out


class TestComputePlanIntegration:
    @staticmethod
    def _cfg_and_rules(tmp_path: Path) -> tuple[Path, Path]:
        cfg = tmp_path / "openmw.cfg"
        cfg.write_text(
            'data="C:/Games/Morrowind/Data Files"\n'
            "data=C:/Mods/Loose\n"
            "content=Morrowind.esm\n"
            "content=Loose1.esp\n"
            "content=Loose2.esp\n",
            encoding="utf-8",
        )
        rules = tmp_path / "mlox_base.txt"
        rules.write_text("", encoding="utf-8")
        return cfg, rules

    def test_the_flag_makes_the_cfg_orphans_the_subset(self, tmp_path: Path) -> None:
        cfg, rules = self._cfg_and_rules(tmp_path)
        args = core.build_arg_parser().parse_args(
            ["--cfg", str(cfg), "--rules", str(rules), "--subset-from-cfg"]
        )
        plan = core.compute_plan(args)
        # Morrowind.esm is a base master and excluded; the two loose plugins are
        # pulled, in cfg order, as the subset to sort.
        assert plan["subset"] == ["Loose1.esp", "Loose2.esp"]

    def test_without_the_flag_an_empty_subset_is_refused(self, tmp_path: Path) -> None:
        cfg, rules = self._cfg_and_rules(tmp_path)
        args = core.build_arg_parser().parse_args(["--cfg", str(cfg), "--rules", str(rules)])
        import pytest

        with pytest.raises(SystemExit):
            core.compute_plan(args)


class TestComputePlanCleanPrefix:
    def test_a_clean_prefixed_list_plugin_is_not_pulled(self, tmp_path: Path) -> None:
        cfg = tmp_path / "openmw.cfg"
        cfg.write_text(
            "content=Morrowind.esm\ncontent=Clean_A.esp\ncontent=Loose1.esp\n",
            encoding="utf-8",
        )
        yml = tmp_path / "plugin-order.yml"
        yml.write_text(
            "- file_name: A.esp\n"
            "  needs_cleaning: true\n"
            "  on_lists:\n"
            "    - total-overhaul\n",
            encoding="utf-8",
        )
        rules = tmp_path / "mlox_base.txt"
        rules.write_text("", encoding="utf-8")
        args = core.build_arg_parser().parse_args(
            [
                "--cfg",
                str(cfg),
                "--rules",
                str(rules),
                "--subset-from-cfg",
                "--plugin-order-yml",
                str(yml),
                "--list-name",
                "total-overhaul",
            ]
        )
        plan = core.compute_plan(args)
        # Clean_A.esp is the umo download of the list's needs-cleaning A.esp, so
        # it is managed, not an orphan; only the truly loose plugin is pulled.
        assert plan["subset"] == ["Loose1.esp"]
