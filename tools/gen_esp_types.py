"""Generate ``wraithguard/esp/enums.py`` and ``flags.py`` from the tes3 crate.

The Python ESP records serialize an enum field as its ``#[repr]`` integer and a
flags field as its backing integer's bits -- exactly as the crate's ``LoadSave``
derive does. For that the port needs every enum's variants *and their repr
width*, and every ``bitflags!`` set's members *and their backing width*, spelled
and valued as the crate declares them. Hand-copying ~50 enums and ~20 flag sets
would drift; this reads them straight from ``libs/esp/src/types/enums.rs`` and
``flags.rs`` instead, the same way ``gen_tes3_enums`` and ``gen_tes3_fieldtypes``
read the crate.

Enums become ``IntEnum`` subclasses that coerce an unrecognised wire value to
the crate's ``#[default]`` variant (its ``unwrap_or_default``); flags become
``IntFlag`` sets, which keep bits no named flag covers through a read and write
untouched (its ``from_bits_retain``). Each records its wire width so a record
reads the right number of bytes.

Needs a ``tes3-main`` checkout beside the repo (or ``$TES3_CRATE``).

Run: ``python tools/gen_esp_types.py``.
"""

from __future__ import annotations

import keyword
import os
import re
from pathlib import Path


def _ident(name: str) -> str:
    """A crate variant name made safe as a Python identifier.

    Only Python keywords collide -- ``None``/``True``/``False`` are valid Rust
    variant names -- and each gets a trailing underscore. The binary format keys
    on an enum's *value*, not its name, so this rename is invisible on the wire.
    """
    return f"{name}_" if keyword.iskeyword(name) else name


#: ``#[repr(u16)]`` above an enum -- its wire width.
_REPR = re.compile(r"#\[repr\((\w+)\)\]")
#: ``pub enum Name {``.
_ENUM_HEAD = re.compile(r"^pub enum (\w+)")
#: ``    Variant = <value>,`` -- value is decimal, hex, negative, or ``b'f'``.
_VARIANT = re.compile(r"^\s+(\w+)\s*=\s*([^,]+),")
#: ``pub struct Name: u32 {`` inside a ``bitflags!``.
_FLAG_HEAD = re.compile(r"pub struct (\w+):\s*(\w+)\s*\{")
#: ``    const NAME = 0x2;``.
_FLAG_CONST = re.compile(r"^\s+const (\w+)\s*=\s*([^;]+);")


def _find_crate(root: Path) -> Path | None:
    """The crate's ``libs/esp/src/types`` beside the repo or via ``$TES3_CRATE``."""
    override = os.environ.get("TES3_CRATE")
    for base in ([Path(override)] if override else []) + [root.parent / "tes3-main"]:
        types = base / "libs" / "esp" / "src" / "types"
        if types.is_dir():
            return types
    return None


def _int(token: str) -> int:
    """Parse a Rust discriminant: decimal, ``0x..`` hex, negative, or ``b'c'``."""
    text = token.strip()
    byte = re.fullmatch(r"b'(.)'", text)
    if byte:
        return ord(byte.group(1))
    return int(text, 0)


def _read_enums(text: str) -> list[tuple[str, str, str, list[tuple[str, int]]]]:
    """Every ``#[repr]`` enum: ``(name, repr, default_variant, [(variant, value)])``."""
    out: list[tuple[str, str, str, list[tuple[str, int]]]] = []
    lines = text.splitlines()
    repr_type: str | None = None
    name: str | None = None
    default = ""
    variants: list[tuple[str, int]] = []
    pending_default = False
    for line in lines:
        rep = _REPR.search(line)
        if rep and name is None:
            repr_type = rep.group(1)
            continue
        head = _ENUM_HEAD.match(line)
        if head and repr_type is not None:
            name, variants, default, pending_default = head.group(1), [], "", False
            continue
        if name is None:
            continue
        if "#[default]" in line:
            pending_default = True
            continue
        var = _VARIANT.match(line)
        if var:
            variant = var.group(1)
            variants.append((variant, _int(var.group(2))))
            if pending_default:
                default = variant
                pending_default = False
            continue
        if line.startswith("}"):
            if variants:
                out.append((name, repr_type or "u32", default or variants[0][0], variants))
            name, repr_type = None, None
    return out


def _read_flags(text: str) -> list[tuple[str, str, list[tuple[str, int]]]]:
    """Every ``bitflags!`` set: ``(name, backing, [(flag, bit)])``."""
    out: list[tuple[str, str, list[tuple[str, int]]]] = []
    name: str | None = None
    backing = "u32"
    consts: list[tuple[str, int]] = []
    for line in text.splitlines():
        head = _FLAG_HEAD.search(line)
        if head and "bitflags" not in line:
            name, backing, consts = head.group(1), head.group(2), []
            continue
        if name is None:
            continue
        const = _FLAG_CONST.match(line)
        if const:
            consts.append((const.group(1), _int(const.group(2))))
        elif line.strip() == "}":
            if consts:
                out.append((name, backing, consts))
            name = None
    return out


#: Rust int type -> byte width, for the recorded wire width.
_WIDTH = {"i8": 1, "u8": 1, "i16": 2, "u16": 2, "i32": 4, "u32": 4, "i64": 8, "u64": 8}


def _render_enums(enums: list[tuple[str, str, str, list[tuple[str, int]]]]) -> str:
    """Render ``enums.py``."""
    head = [
        '"""TES3 enums by name -- GENERATED from the tes3 crate, do not edit.',
        "",
        "See ``tools/gen_esp_types.py``. Each is an ``IntEnum`` whose members are the",
        "crate's variants at their ``#[repr]`` values; an unrecognised wire value coerces",
        "to the ``#[default]`` variant, as the crate's ``unwrap_or_default`` does. ``WIDTH``",
        "is the repr's byte width, so a record reads the right number of bytes.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import enum",
        "from typing import ClassVar, Final, TypeVar",
        "",
        '_E = TypeVar("_E", bound="EspEnum")',
        "",
        "",
        "class EspEnum(enum.IntEnum):",
        '    """An enum that coerces an unknown wire value to its default variant."""',
        "",
        '    __default_name__: ClassVar[str] = ""',
        "",
        "    @classmethod",
        "    def _missing_(cls, value: object) -> EspEnum:",
        '        """Any value the crate does not define reads back as the default."""',
        "        return cls[cls.__default_name__]",
        "",
        "    @classmethod",
        "    def default(cls: type[_E]) -> _E:",
        '        """The crate\'s ``#[default]`` variant, for seeding a field."""',
        "        return cls[cls.__default_name__]",
        "",
    ]
    body: list[str] = []
    widths: list[str] = []
    for name, _repr_type, default, variants in sorted(enums):
        body.append("")
        body.append(f"class {name}(EspEnum):")
        body.append(f'    __default_name__ = "{_ident(default)}"')
        body.append("")
        for variant, value in variants:
            body.append(f"    {_ident(variant)} = {value}")
    widths.append("")
    widths.append("#: Each enum's wire width in bytes (its repr).")
    widths.append("WIDTH: Final[dict[str, int]] = {")
    for name, repr_type, _default, _variants in sorted(enums):
        widths.append(f'    "{name}": {_WIDTH[repr_type]},')
    widths.append("}")
    return "\n".join(head + body + [""] + widths) + "\n"


def _render_flags(flags: list[tuple[str, str, list[tuple[str, int]]]]) -> str:
    """Render ``flags.py``."""
    head = [
        '"""TES3 bitflags by name -- GENERATED from the tes3 crate, do not edit.',
        "",
        "See ``tools/gen_esp_types.py``. Each is an ``IntFlag``, which keeps a bit no named",
        "flag covers through a read and write untouched -- the crate's ``from_bits_retain``",
        "-- on every supported Python. ``WIDTH`` is the backing integer's byte width.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import enum",
        "from typing import Final",
    ]
    body: list[str] = []
    for name, _backing, consts in sorted(flags):
        body.append("")
        body.append("")
        body.append(f"class {name}(enum.IntFlag):")
        for flag, bit in consts:
            body.append(f"    {flag} = {hex(bit)}")
    widths = ["", "", "#: Each flag set's wire width in bytes (its backing integer)."]
    widths.append("WIDTH: Final[dict[str, int]] = {")
    for name, backing, _consts in sorted(flags):
        widths.append(f'    "{name}": {_WIDTH[backing]},')
    widths.append("}")
    return "\n".join(head + body + widths) + "\n"


def main() -> None:
    """Read the crate and write the enum and flags modules."""
    root = Path(__file__).resolve().parent.parent
    types = _find_crate(root)
    if types is None:
        raise SystemExit("no tes3-main crate found beside the repo or via $TES3_CRATE")
    enums = _read_enums((types / "enums.rs").read_text(encoding="utf-8"))
    flags = _read_flags((types / "flags.rs").read_text(encoding="utf-8"))
    out = root / "wraithguard" / "esp"
    (out / "enums.py").write_text(_render_enums(enums), encoding="utf-8")
    (out / "flags.py").write_text(_render_flags(flags), encoding="utf-8")
    print(f"wrote enums.py ({len(enums)} enums), flags.py ({len(flags)} flag sets)")


if __name__ == "__main__":
    main()
