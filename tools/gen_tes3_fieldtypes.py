"""Generate ``wraithguard/patch/field_types.py`` from the tes3 crate.

The patch "Define value" dialog picks its input widget and validates by the
field's declared type. That type is the crate's own: a ``WeaponData.health`` is
a ``u16`` (0..65535), a ``weight`` an ``f32``, a ``weapon_type`` an enum, a
``flags`` a ``bitflags`` set. This reads the crate's record structs and produces,
per tes3conv record ``type`` and flattened field path, one of a small set of
*kinds* -- so the dialog can offer a spinbox with the right range, a checkbox
group for flags, a dropdown for an enum, a JSON box for a list, and so on,
instead of guessing from the field's current value.

Needs a ``tes3-main`` checkout beside the repo (or ``$TES3_CRATE``). Best-effort,
like the schema and the enum table: a field whose type could not be resolved is
simply left out, and the dialog falls back to its value-type heuristics -- never
a dead end.

Run: ``python tools/gen_tes3_fieldtypes.py``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: ``#[tag("WEAP")] Weapon(Weapon),`` -- the record ``type`` string maps to the
#: struct that defines it. The variant name is what tes3conv writes as ``type``.
_RECORD = re.compile(r'#\[tag\("[^"]+"\)\]\s*(\w+)\((\w+)\)')
#: ``pub struct Name {`` (plain structs; the bitflags ones are read separately).
_STRUCT_HEAD = re.compile(r"^pub struct (\w+)")
#: ``    pub field: Type,`` inside a struct.
_FIELD = re.compile(r"^\s+pub (\w+):\s*(.+?),?\s*$")
#: ``pub enum Name`` -- unit enum names, so a field of that type is a dropdown.
_ENUM_HEAD = re.compile(r"^pub enum (\w+)")
#: A ``bitflags!`` struct header and its ``const`` members.
_FLAG_HEAD = re.compile(r"pub struct (\w+):\s*\w+\s*\{")
_FLAG_CONST = re.compile(r"^\s+const (\w+)\s*=")

#: Integer Rust types -> (min, max), for the spinbox range and validation.
_INTS: dict[str, tuple[int, int]] = {
    "u8": (0, 255),
    "u16": (0, 65535),
    "u32": (0, 4294967295),
    "u64": (0, 18446744073709551615),
    "i8": (-128, 127),
    "i16": (-32768, 32767),
    "i32": (-2147483648, 2147483647),
    "i64": (-9223372036854775808, 9223372036854775807),
}
_WRAPPERS = ("Option", "Box")


def _find_crate(root: Path) -> Path | None:
    """The crate's ``libs/esp/src`` beside the repo or via ``$TES3_CRATE``."""
    override = os.environ.get("TES3_CRATE")
    for base in ([Path(override)] if override else []) + [root.parent / "tes3-main"]:
        src = base / "libs" / "esp" / "src"
        if src.is_dir():
            return src
    return None


def _read(
    src: Path,
) -> tuple[dict[str, list[tuple[str, str]]], set[str], dict[str, tuple[str, ...]], dict[str, str]]:
    """Parse structs, enum names, flag sets, and the record map from the crate.

    Returns:
        ``(structs, enum names, flag variants, record type -> struct)``.
    """
    structs: dict[str, list[tuple[str, str]]] = {}
    enums: set[str] = set()
    flags: dict[str, tuple[str, ...]] = {}
    records: dict[str, str] = {}
    for path in sorted(src.rglob("*.rs")):
        text = path.read_text(encoding="utf-8", errors="replace")
        records.update(_RECORD.findall(text))
        name: str | None = None
        fields: list[tuple[str, str]] = []
        flag_name: str | None = None
        flag_consts: list[str] = []
        for line in text.splitlines():
            enum = _ENUM_HEAD.match(line)
            if enum:
                enums.add(enum.group(1))
            fh = _FLAG_HEAD.search(line)
            if fh and "bitflags" not in line:
                flag_name, flag_consts = fh.group(1), []
            elif flag_name is not None:
                const = _FLAG_CONST.match(line)
                if const:
                    flag_consts.append(const.group(1))
                elif line.strip() == "}":
                    if flag_consts:
                        flags[flag_name] = tuple(flag_consts)
                    flag_name = None
            sh = _STRUCT_HEAD.match(line)
            if sh:
                name, fields = sh.group(1), []
            elif name is not None:
                if line.startswith("}"):
                    structs[name] = fields
                    name = None
                else:
                    fm = _FIELD.match(line)
                    if fm:
                        fields.append((fm.group(1), fm.group(2)))
    return structs, enums, flags, records


def _unwrap(type_str: str) -> str:
    """Strip ``Option<..>`` / ``Box<..>`` down to the inner type name."""
    current = type_str.strip()
    changed = True
    while changed:
        changed = False
        for wrapper in _WRAPPERS:
            if current.startswith(f"{wrapper}<") and current.endswith(">"):
                current = current[len(wrapper) + 1 : -1].strip()
                changed = True
    return current


def _kind(type_str: str, enums: set[str], flags: dict[str, tuple[str, ...]]) -> str | None:
    """Classify a Rust field type into a dialog *kind*, or ``None`` if unknown.

    A struct type returns ``"GROUP"`` -- the caller recurses into it.
    """
    inner = _unwrap(type_str)
    if inner.startswith(("Vec<", "[")):
        return "list"
    if inner in ("f32", "f64"):
        return "float"
    if inner in _INTS:
        low, high = _INTS[inner]
        return f"int:{low}:{high}"
    if inner == "bool":
        return "bool"
    if inner == "String":
        return "str"
    if inner in enums:
        return "enum"
    if inner in flags:
        return f"flags:{inner}"
    if re.fullmatch(r"\w+", inner):
        return "GROUP"  # a plain named type: assume a nested struct
    return None


def _walk(
    struct: str,
    prefix: str,
    structs: dict[str, list[tuple[str, str]]],
    enums: set[str],
    flags: dict[str, tuple[str, ...]],
    out: dict[str, str],
    seen: frozenset[str],
) -> None:
    """Flatten one struct's fields into ``path -> kind``, recursing into groups."""
    for field, type_str in structs.get(struct, ()):
        path = f"{prefix}{field}"
        kind = _kind(type_str, enums, flags)
        if kind == "GROUP":
            inner = _unwrap(type_str)
            if inner in structs and inner not in seen:
                _walk(inner, f"{path}.", structs, enums, flags, out, seen | {inner})
        elif kind is not None:
            out[path] = kind


def _render(records: dict[str, dict[str, str]], flags: dict[str, tuple[str, ...]]) -> str:
    """Render the generated data module."""
    lines = [
        '"""TES3 field types by record and path -- GENERATED, do not edit by hand.',
        "",
        "See ``tools/gen_tes3_fieldtypes.py``, which reads the tes3 crate. Each kind is",
        "one of: ``float``, ``int:<min>:<max>``, ``str``, ``bool``, ``enum``,",
        "``flags:<FlagsName>``, or ``list``.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Final",
        "",
        f"#: {len(records)} record types -> flattened field path -> kind.",
        "RECORD_FIELDS: Final[dict[str, dict[str, str]]] = {",
    ]
    for record, fields in sorted(records.items()):
        lines.append(f"    {record!r}: {{")
        for path, kind in sorted(fields.items()):
            lines.append(f"        {path!r}: {kind!r},")
        lines.append("    },")
    lines.append("}")
    lines.append("")
    lines.append(f"#: {len(flags)} bitflags sets -> their flag names.")
    lines.append("FLAG_VARIANTS: Final[dict[str, tuple[str, ...]]] = {")
    for name, consts in sorted(flags.items()):
        rendered = "".join(f"{c!r}, " for c in consts)
        lines.append(f"    {name!r}: ({rendered}),")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> None:
    """Read the crate and write the field-type data module."""
    root = Path(__file__).resolve().parent.parent
    src = _find_crate(root)
    if src is None:
        raise SystemExit("no tes3-main crate found beside the repo or via $TES3_CRATE")
    structs, enums, flags, records = _read(src)
    out: dict[str, dict[str, str]] = {}
    for record_type, struct in records.items():
        fields: dict[str, str] = {}
        _walk(struct, "", structs, enums, flags, fields, frozenset({struct}))
        if fields:
            out[record_type] = fields
    target = root / "wraithguard" / "patch" / "field_types.py"
    target.write_text(_render(out, flags), encoding="utf-8")
    print(f"wrote {target} -- {len(out)} records, {len(flags)} flag sets")


if __name__ == "__main__":
    main()
