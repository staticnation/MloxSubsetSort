"""Tests for ``tools/gen_tes3_fieldtypes.py``, the field-type code generator.

The patch "Define value" dialog chooses its widget and validation from each
field's declared crate type, so this generator reads the tes3 record structs and
emits, per record and flattened path, a *kind* (``float``, ``int:min:max``,
``enum``, ``flags:Name``, ``list``, ...). The Rust parsing, type classification,
struct flattening and rendering are pure -- a small hand-written fake crate drives
each -- and ``main`` is run against that fake via ``$TES3_CRATE`` with the real
generated module saved and restored so the checkout is never disturbed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.gen_tes3_fieldtypes import (
    _find_crate,
    _kind,
    _read,
    _render,
    _unwrap,
    _walk,
    main,
)

_ROOT = Path(__file__).resolve().parent.parent

_FAKE_CRATE = """\
#[tag("WEAP")]
Weapon(Weapon),

#[tag("EMPT")]
Empty(EmptyRec),

pub struct Weapon {
    // a comment line inside a struct is neither a field nor a close brace
    pub health: u16,
    pub weight: f32,
    pub weapon_type: WeaponType,
    pub flags: ObjectFlags,
    pub name: String,
    pub enabled: bool,
    pub extras: Vec<u8>,
    pub data: WeaponData,
    pub mystery: SomethingWeird<u8, u8>,
}

pub struct WeaponData {
    pub value: i32,
}

pub struct EmptyRec {
    pub mystery: SomethingWeird<u8, u8>,
}

pub enum WeaponType {
    ShortBlade,
    LongBlade,
}

bitflags! {
    pub struct ObjectFlags: u32 {

        const MODIFIED = 0x1;
        const DELETED = 0x2;
    }
}

bitflags! {
    pub struct EmptyFlags: u32 {
    }
}
"""


def _write_crate(root: Path, text: str = _FAKE_CRATE) -> Path:
    """Lay out a minimal ``libs/esp/src`` crate holding ``text``."""
    src = root / "libs" / "esp" / "src"
    src.mkdir(parents=True)
    (src / "records.rs").write_text(text, encoding="utf-8")
    return src


class TestUnwrap:
    def test_a_plain_type_is_unchanged(self) -> None:
        assert _unwrap("u16") == "u16"

    def test_an_option_is_stripped(self) -> None:
        assert _unwrap("Option<u16>") == "u16"

    def test_nested_wrappers_are_stripped_to_the_core(self) -> None:
        assert _unwrap("Box<Option<f32>>") == "f32"


class TestKind:
    _ENUMS = {"WeaponType"}
    _FLAGS = {"ObjectFlags": ("MODIFIED", "DELETED")}

    def _k(self, type_str: str) -> str | None:
        return _kind(type_str, self._ENUMS, self._FLAGS)

    def test_a_vec_is_a_list(self) -> None:
        assert self._k("Vec<u8>") == "list"

    def test_an_array_is_a_list(self) -> None:
        assert self._k("[u8; 4]") == "list"

    def test_a_float_is_a_float(self) -> None:
        assert self._k("f32") == "float"

    def test_an_integer_carries_its_range(self) -> None:
        assert self._k("u16") == "int:0:65535"
        assert self._k("i8") == "int:-128:127"

    def test_a_bool_and_a_string(self) -> None:
        assert self._k("bool") == "bool"
        assert self._k("String") == "str"

    def test_an_enum_and_a_flag_set(self) -> None:
        assert self._k("WeaponType") == "enum"
        assert self._k("ObjectFlags") == "flags:ObjectFlags"

    def test_a_plain_named_type_is_a_group(self) -> None:
        assert self._k("WeaponData") == "GROUP"

    def test_an_unrecognisable_type_is_none(self) -> None:
        assert self._k("Weird<u8, u8>") is None  # generic with args, not a plain word


class TestRead:
    def test_it_parses_records_structs_enums_and_flags(self, tmp_path: Path) -> None:
        src = _write_crate(tmp_path)
        structs, enums, flags, records = _read(src)
        assert records["Weapon"] == "Weapon"
        assert records["Empty"] == "EmptyRec"
        assert "Weapon" in structs
        assert ("health", "u16") in structs["Weapon"]
        assert "WeaponType" in enums
        assert flags["ObjectFlags"] == ("MODIFIED", "DELETED")
        assert "EmptyFlags" not in flags  # a bitflags set with no consts is dropped


class TestWalk:
    def test_it_flattens_fields_and_recurses_into_groups(self, tmp_path: Path) -> None:
        structs, enums, flags, _ = _read(_write_crate(tmp_path))
        out: dict[str, str] = {}
        _walk("Weapon", "", structs, enums, flags, out, frozenset({"Weapon"}))
        assert out["health"] == "int:0:65535"
        assert out["weight"] == "float"
        assert out["weapon_type"] == "enum"
        assert out["flags"] == "flags:ObjectFlags"
        assert out["data.value"] == "int:-2147483648:2147483647"  # recursed into WeaponData
        assert "mystery" not in out  # unrecognisable type dropped

    def test_a_self_referential_struct_does_not_recurse_forever(self) -> None:
        structs = {"Node": [("child", "Node"), ("id", "u8")]}
        out: dict[str, str] = {}
        _walk("Node", "", structs, set(), {}, out, frozenset({"Node"}))
        assert out == {"id": "int:0:255"}  # child skipped as an already-seen group


class TestRender:
    def test_it_renders_a_generated_data_module(self) -> None:
        records = {"WEAP": {"health": "int:0:65535"}}
        flags = {"ObjectFlags": ("MODIFIED", "DELETED")}
        text = _render(records, flags)
        assert "GENERATED, do not edit by hand" in text
        assert "RECORD_FIELDS" in text
        assert "'health': 'int:0:65535'" in text
        assert "FLAG_VARIANTS" in text
        assert "'ObjectFlags'" in text


class TestFindCrate:
    def test_the_env_override_is_honoured(self, tmp_path: Path, monkeypatch) -> None:
        src = _write_crate(tmp_path)
        monkeypatch.setenv("TES3_CRATE", str(tmp_path))
        assert _find_crate(tmp_path / "anywhere") == src

    def test_no_crate_anywhere_is_none(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("TES3_CRATE", raising=False)
        # A root whose parent holds no tes3-main checkout.
        assert _find_crate(tmp_path / "repo") is None


class TestMain:
    def test_it_writes_the_field_types_module(self, tmp_path: Path, capsys, monkeypatch) -> None:
        """main reads the (faked) crate and rewrites the generated module.

        The real generated file is saved and restored so running the generator
        in a test never leaves the checkout modified.
        """
        target = _ROOT / "wraithguard" / "patch" / "field_types.py"
        original = target.read_bytes()  # raw bytes: never reflow CRLF on restore
        _write_crate(tmp_path)
        monkeypatch.setenv("TES3_CRATE", str(tmp_path))
        try:
            main()
            written = target.read_text(encoding="utf-8")
        finally:
            target.write_bytes(original)  # restore the checkout byte-for-byte
        assert "RECORD_FIELDS" in written
        assert "'Weapon'" in written  # our fake record made it through
        assert "'Empty'" not in written  # a record with no resolvable fields is omitted
        assert "wrote" in capsys.readouterr().out

    def test_a_missing_crate_is_fatal(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("TES3_CRATE", str(tmp_path / "not-a-crate"))
        # Also block the beside-the-repo fallback by pointing the override at a
        # bare path; _find_crate then returns None and main bails out.
        import tools.gen_tes3_fieldtypes as gen

        monkeypatch.setattr(gen, "_find_crate", lambda _root: None)
        with pytest.raises(SystemExit, match="no tes3-main crate"):
            main()
