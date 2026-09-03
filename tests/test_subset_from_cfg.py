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
        *, subset_from_cfg: bool = True, sort_data_paths: bool = False, groundcover=frozenset()
    ):
        args = types.SimpleNamespace(
            subset_from_cfg=subset_from_cfg, sort_data_paths=sort_data_paths
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
            "data=C:/Mods/Loose",
        ]
        subset = core._pull_cfg_orphans(
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

    def test_orphan_data_is_never_pulled_even_with_sort_data_paths(self) -> None:
        # A data= path already sits in the cfg's own data= order; pulling it back
        # as an insert repositioned the whole VFS and marked every path touched.
        # So orphan data is left alone -- neither recorded nor positioned --
        # whether or not --sort-data-paths is set.
        without = self._run(sort_data_paths=False)
        assert without["data_inserts"] == []
        assert without["raw_toml_data_inserts"] == [{"value": "C:/Mods/Declared"}]

        with_flag = self._run(sort_data_paths=True)
        assert with_flag["data_inserts"] == []
        assert with_flag["raw_toml_data_inserts"] == [{"value": "C:/Mods/Declared"}]

    def test_a_groundcover_plugin_is_never_pulled_into_content(self) -> None:
        out = self._run(groundcover=frozenset({"loose2.esp"}))
        assert out["subset"] == ["Loose1.esp"]

    def test_the_flag_off_is_a_no_op(self) -> None:
        out = self._run(subset_from_cfg=False)
        assert out["subset"] == []
        assert out["data_inserts"] == []
        assert out["raw_toml_data_inserts"] == [{"value": "C:/Mods/Declared"}]

    def test_no_orphans_at_all_prints_a_clean_bill(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Every content= plugin is already curated or declared -- nothing left to pull."""
        args = types.SimpleNamespace(subset_from_cfg=True, sort_data_paths=False)
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
