"""Tests for ``tools/gen_esp_types.py``, the ESP enum/flags code generator.

The native ESP reader keys records on enum *values* and bitflag *bits*, so this
generates ``wraithguard/esp/enums.py`` and ``flags.py`` straight from the tes3
crate: each enum an ``IntEnum`` at the crate's ``#[repr]`` values with the
``#[default]`` variant recorded, each bitflags set an ``IntFlag``, plus a wire
width per type. The discriminant parser, the Rust readers and the two renderers
are pure and driven with a small fake crate; ``main`` runs against it with the
two real generated modules saved and restored.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.gen_esp_types import (
    _find_crate,
    _ident,
    _int,
    _read_enums,
    _read_flags,
    _render_enums,
    _render_flags,
    main,
)

_ROOT = Path(__file__).resolve().parent.parent

_FAKE_ENUMS = """\
#[repr(u16)]
pub enum WeaponType {
    // the blade and marksman families
    #[default]
    ShortBladeOneHand = 0,
    LongBladeTwoClose = 1,
}

#[repr(i32)]
pub enum SpellType {
    Spell = 0,
    Ability = 1,
    Curse = -1,
}

#[repr(u8)]
pub enum Maybe {
    #[default]
    None = 0,
    Some = 1,
}

#[repr(u8)]
pub enum NoVariants {
}

pub enum NoRepr {
    A = 0,
    B = 1,
}
"""

_FAKE_FLAGS = """\
bitflags! {
    pub struct ObjectFlags: u32 {
        // record-level state bits
        const MODIFIED = 0x2;
        const DELETED = 0x20;
    }
}

bitflags! {
    pub struct EmptyFlags: u16 {
    }
}
"""


def _write_crate(root: Path) -> Path:
    types = root / "libs" / "esp" / "src" / "types"
    types.mkdir(parents=True)
    (types / "enums.rs").write_text(_FAKE_ENUMS, encoding="utf-8")
    (types / "flags.rs").write_text(_FAKE_FLAGS, encoding="utf-8")
    return types


class TestIdentAndInt:
    def test_a_keyword_variant_gets_a_trailing_underscore(self) -> None:
        assert _ident("None") == "None_"

    def test_a_normal_variant_is_unchanged(self) -> None:
        assert _ident("Spell") == "Spell"

    def test_int_parses_decimal_hex_negative_and_byte(self) -> None:
        assert _int("5") == 5
        assert _int("0x20") == 0x20
        assert _int("-1") == -1
        assert _int("b'A'") == 65


class TestReadEnums:
    def test_it_reads_repr_enums_with_defaults(self) -> None:
        enums = {
            name: (rep, default, variants)
            for name, rep, default, variants in _read_enums(_FAKE_ENUMS)
        }
        assert enums["WeaponType"][0] == "u16"
        assert enums["WeaponType"][1] == "ShortBladeOneHand"  # the #[default]
        assert ("Curse", -1) in enums["SpellType"][2]

    def test_an_enum_without_an_explicit_default_uses_its_first_variant(self) -> None:
        enums = {name: default for name, _rep, default, _v in _read_enums(_FAKE_ENUMS)}
        assert enums["SpellType"] == "Spell"  # no #[default], so first variant

    def test_an_enum_without_a_repr_is_ignored(self) -> None:
        names = {name for name, *_ in _read_enums(_FAKE_ENUMS)}
        assert "NoRepr" not in names

    def test_an_enum_with_no_variants_is_dropped(self) -> None:
        names = {name for name, *_ in _read_enums(_FAKE_ENUMS)}
        assert "NoVariants" not in names


class TestReadFlags:
    def test_it_reads_bitflags_with_backing_width(self) -> None:
        flags = {name: (backing, consts) for name, backing, consts in _read_flags(_FAKE_FLAGS)}
        assert flags["ObjectFlags"][0] == "u32"
        assert ("DELETED", 0x20) in flags["ObjectFlags"][1]

    def test_an_empty_flag_set_is_dropped(self) -> None:
        names = {name for name, *_ in _read_flags(_FAKE_FLAGS)}
        assert "EmptyFlags" not in names


class TestRender:
    def test_enums_render_with_default_members_and_widths(self) -> None:
        text = _render_enums(_read_enums(_FAKE_ENUMS))
        assert "class WeaponType(EspEnum):" in text
        assert '__default_name__ = "ShortBladeOneHand"' in text
        assert "None_ = 0" in text  # the keyword variant was renamed
        assert '"WeaponType": 2,' in text  # u16 wire width

    def test_flags_render_as_intflags_with_widths(self) -> None:
        text = _render_flags(_read_flags(_FAKE_FLAGS))
        assert "class ObjectFlags(enum.IntFlag):" in text
        assert "DELETED = 0x20" in text
        assert '"ObjectFlags": 4,' in text  # u32 wire width


class TestFindCrate:
    def test_the_env_override_is_used(self, tmp_path: Path, monkeypatch) -> None:
        types = _write_crate(tmp_path)
        monkeypatch.setenv("TES3_CRATE", str(tmp_path))
        assert _find_crate(tmp_path / "elsewhere") == types

    def test_no_crate_is_none(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("TES3_CRATE", raising=False)
        assert _find_crate(tmp_path / "repo") is None


class TestMain:
    def test_it_writes_both_modules(self, tmp_path: Path, capsys, monkeypatch) -> None:
        """main reads the crate and rewrites enums.py and flags.py, restored after."""
        enums_py = _ROOT / "wraithguard" / "esp" / "enums.py"
        flags_py = _ROOT / "wraithguard" / "esp" / "flags.py"
        # Save and restore as raw bytes: the tool writes LF, but the checked-in
        # files may be CRLF, and a text-mode restore would silently reflow them.
        saved = {p: p.read_bytes() for p in (enums_py, flags_py)}
        _write_crate(tmp_path)
        monkeypatch.setenv("TES3_CRATE", str(tmp_path))
        try:
            main()
            enums_written = enums_py.read_text(encoding="utf-8")
            flags_written = flags_py.read_text(encoding="utf-8")
        finally:
            for path, raw in saved.items():
                path.write_bytes(raw)
        assert "class WeaponType(EspEnum):" in enums_written
        assert "class ObjectFlags(enum.IntFlag):" in flags_written
        assert "wrote enums.py" in capsys.readouterr().out

    def test_a_missing_crate_is_fatal(self, monkeypatch) -> None:
        import tools.gen_esp_types as gen

        monkeypatch.setattr(gen, "_find_crate", lambda _root: None)
        with pytest.raises(SystemExit, match="no tes3-main crate"):
            main()
