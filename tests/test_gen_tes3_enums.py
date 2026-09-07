"""Tests for ``tools/gen_tes3_enums.py``, the enum-dropdown data generator.

The patch dialog offers a dropdown of a string field's known enum variants,
harvested from the vanilla tes3conv dumps for correct spelling and completed
from the ``tes3`` crate so variants no vanilla record uses are still listed. The
harvest walk, the crate parser, the qualify-and-complete step and the renderer
are pure; a couple of tiny fake dumps and a fake ``enums.rs`` drive them, and
``main`` runs against those with the real generated module saved and restored.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("ijson")

from tools.gen_tes3_enums import (
    _crate_enums,
    _enums,
    _find_crate,
    _harvest,
    _render,
    _walk,
    main,
)

_ROOT = Path(__file__).resolve().parent.parent
_SOURCES = ("Morrowind.json", "Tribunal.json", "Bloodmoon.json")

_FAKE_ENUMS_RS = """\
pub enum WeaponType {
    ShortBladeOneHand = 0,
    LongBladeTwoClose = 1,
    MarksmanBow,
    MarksmanThrown,
}

pub enum WithData {
    Plain,
    Simple,
    Carries(u32),
}

pub enum TooSmall {
    Only,
}
"""


def _write_dumps(root: Path, records: list[dict]) -> Path:
    """Write the three vanilla dumps (identical content) under ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    for name in _SOURCES:
        (root / name).write_text(json.dumps(records), encoding="utf-8")
    return root


def _write_crate(root: Path, text: str = _FAKE_ENUMS_RS) -> Path:
    src = root / "libs" / "esp" / "src"
    src.mkdir(parents=True)
    (src / "enums.rs").write_text(text, encoding="utf-8")
    return src


class TestWalk:
    def test_string_leaves_are_grouped_by_field_name(self) -> None:
        from collections import defaultdict

        seen: dict[str, set[str]] = defaultdict(set)
        _walk({"a": "X", "nested": {"b": "Y"}, "list": [{"a": "Z"}]}, seen)
        assert seen["a"] == {"X", "Z"}
        assert seen["b"] == {"Y"}

    def test_non_string_leaves_are_ignored(self) -> None:
        from collections import defaultdict

        seen: dict[str, set[str]] = defaultdict(set)
        _walk({"n": 5, "flag": True, "none": None}, seen)
        assert seen == {}


class TestHarvest:
    def test_it_reads_the_dumps_and_gathers_field_values(self, tmp_path: Path) -> None:
        _write_dumps(
            tmp_path, [{"weapon_type": "ShortBladeOneHand"}, {"weapon_type": "MarksmanBow"}]
        )
        seen = _harvest(tmp_path)
        assert seen["weapon_type"] == {"ShortBladeOneHand", "MarksmanBow"}

    def test_a_missing_dump_is_fatal(self, tmp_path: Path) -> None:
        (tmp_path / "Morrowind.json").write_text("[]", encoding="utf-8")  # only one of three
        with pytest.raises(SystemExit, match="missing"):
            _harvest(tmp_path)


class TestCrateEnums:
    def test_it_reads_unit_enums_and_skips_data_and_tiny_ones(self, tmp_path: Path) -> None:
        src = _write_crate(tmp_path)
        enums = dict(_crate_enums(src))
        assert enums["WeaponType"] == frozenset(
            {"ShortBladeOneHand", "LongBladeTwoClose", "MarksmanBow", "MarksmanThrown"}
        )
        assert enums["WithData"] == frozenset({"Plain", "Simple"})  # data variant skipped
        assert "TooSmall" not in enums  # a single-variant enum is below the minimum


class TestEnums:
    def test_a_field_is_completed_from_the_matching_crate_enum(self) -> None:
        """A qualifying field gets the crate enum's full list, extras included."""
        seen = {"weapon_type": {"ShortBladeOneHand", "MarksmanBow"}}
        crate = [
            (
                "WeaponType",
                frozenset({"ShortBladeOneHand", "LongBladeTwoClose", "MarksmanBow"}),
            )
        ]
        out = _enums(seen, crate)
        assert out["weapon_type"] == ("LongBladeTwoClose", "MarksmanBow", "ShortBladeOneHand")

    def test_without_a_crate_match_the_vanilla_values_stand_alone(self) -> None:
        seen = {"weapon_type": {"MarksmanBow", "ShortBladeOneHand"}}
        out = _enums(seen, [])  # no crate
        assert out["weapon_type"] == ("MarksmanBow", "ShortBladeOneHand")

    def test_a_denied_field_name_is_never_an_enum(self) -> None:
        assert _enums({"type": {"Float", "Long"}}, []) == {}

    def test_non_pascalcase_values_disqualify_a_field(self) -> None:
        assert _enums({"note": {"a value", "another value"}}, []) == {}

    def test_a_single_valued_field_is_below_the_minimum(self) -> None:
        assert _enums({"weapon_type": {"OnlyOne"}}, []) == {}


class TestRender:
    def test_it_renders_the_generated_module(self) -> None:
        text = _render({"weapon_type": ("Bow", "ShortBlade")})
        assert "GENERATED, do not edit by hand" in text
        assert "FIELD_ENUMS" in text
        assert "'weapon_type': ('Bow', 'ShortBlade')" in text


class TestFindCrate:
    def test_the_env_override_wins(self, tmp_path: Path, monkeypatch) -> None:
        src = _write_crate(tmp_path)
        monkeypatch.setenv("TES3_CRATE", str(tmp_path))
        assert _find_crate(tmp_path / "elsewhere") == src

    def test_no_crate_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("TES3_CRATE", raising=False)
        assert _find_crate(tmp_path / "repo") is None


class TestMain:
    def _run_main_protected(self, monkeypatch) -> str:
        """Run main, returning what it wrote, with the real module restored after."""
        target = _ROOT / "wraithguard" / "patch" / "enum_data.py"
        original = target.read_bytes()  # raw bytes: never reflow CRLF on restore
        try:
            main()
            return target.read_text(encoding="utf-8")
        finally:
            target.write_bytes(original)

    def test_it_writes_the_enum_module_with_a_crate(
        self, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        import tools.gen_tes3_enums as gen

        src = _write_crate(tmp_path / "crate")
        monkeypatch.setattr(gen, "_find_crate", lambda _root: src)
        monkeypatch.setattr(
            gen, "_harvest", lambda _root: {"weapon_type": {"ShortBladeOneHand", "MarksmanBow"}}
        )
        written = self._run_main_protected(monkeypatch)
        assert "FIELD_ENUMS" in written
        assert "weapon_type" in written
        out = capsys.readouterr().out
        assert "wrote" in out and "variants from crate" in out

    def test_it_runs_without_a_crate(self, tmp_path: Path, capsys, monkeypatch) -> None:
        import tools.gen_tes3_enums as gen

        monkeypatch.setattr(gen, "_find_crate", lambda _root: None)
        monkeypatch.setattr(
            gen, "_harvest", lambda _root: {"weapon_type": {"ShortBladeOneHand", "MarksmanBow"}}
        )
        written = self._run_main_protected(monkeypatch)
        assert "weapon_type" in written
        assert "no crate found" in capsys.readouterr().out
