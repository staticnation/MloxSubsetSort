"""Generate ``wraithguard/patch/enum_data.py`` from real tes3conv output.

The patch "Define value" dialog offers a dropdown of a string field's known enum
variants. Two sources are combined so the result is both correctly *named* and
completely *listed*:

* the vanilla masters' tes3conv JSON (under ``tes3conv_json/``) says which JSON
  *field names* hold enum values, and what those values are spelled like -- but
  only the ones vanilla happens to use;
* the ``tes3`` crate's ``enums.rs`` (a ``tes3-main`` checkout beside the repo, or
  ``$TES3_CRATE``) gives each enum's *complete* variant list, including ones no
  vanilla record uses (``SpellType::Curse``, and so on).

Each harvested field is matched to the smallest crate enum whose variants are a
superset of its vanilla values, and that enum's full list is written. Without
the crate, the vanilla values stand on their own -- so this still runs in CI.

Run: ``python tools/gen_tes3_enums.py`` (needs the three vanilla dumps and
``ijson``). Best-effort by design, like the record schema: the dialog's combobox
is editable and also offers the conflict's own values, so a missed or over-eager
field is never a dead end.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import ijson

#: The vanilla dumps to read; together they use every core enum value.
_SOURCES = ("Morrowind.json", "Tribunal.json", "Bloodmoon.json")

#: A single enum variant, as the ``tes3`` crate serialises them: PascalCase, no
#: spaces, separators or extensions.
_VARIANT = re.compile(r"^[A-Z][A-Za-z0-9]*$")

#: Leaf field names that are identity, a value-type tag, a free string, or a
#: reference to another record -- never a single-choice enum, whatever their
#: values happen to look like in the vanilla data.
_DENY = frozenset(
    {
        "type",  # record type (identity) and the value-tag {"type": "Float"}
        "id",
        "name",
        "mesh",
        "icon",
        "model",
        "script",
        "class",
        "race",
        "faction",
        "sound",
        "region",
        "cell",
        "birthsign",
        "creature",
        "spell",
        "enchanting",
        "key",
        "trap",
        "owner",
        "global",
    }
)

#: A field must have at least this many, and at most this many, distinct values
#: to count as an enum: fewer is not worth a dropdown, more is an id space.
_MIN, _MAX = 2, 48


def _walk(obj: object, seen: dict[str, set[str]]) -> None:
    """Collect every string leaf, keyed by its field (leaf) name.

    Args:
        obj: A decoded record, or any nested part of one.
        seen: Field name -> the set of string values met for it. Updated.
    """
    if isinstance(obj, dict):
        for name, value in obj.items():
            if isinstance(value, str):
                seen[name].add(value)
            else:
                _walk(value, seen)
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, seen)


def _harvest(root: Path) -> dict[str, set[str]]:
    """Read the vanilla dumps and gather every string field's value set.

    Args:
        root: The ``tes3conv_json`` directory.

    Returns:
        Field name -> the set of string values seen across the masters.
    """
    seen: dict[str, set[str]] = defaultdict(set)
    for name in _SOURCES:
        path = root / name
        if not path.exists():
            raise SystemExit(f"missing {path} -- run tes3conv on the vanilla masters first")
        with path.open("rb") as handle:
            for record in ijson.items(handle, "item"):
                _walk(record, seen)
    return seen


#: One ``pub enum Name {`` header in the crate.
_ENUM_HEAD = re.compile(r"^pub enum (\w+)")
#: A plain unit variant line, e.g. ``    ShortBladeOneHand = 0,``. Variants that
#: carry data (``Variant(..)`` / ``Variant {..}``) do not serialise as a string
#: and are skipped by the trailing-char check in :func:`_crate_enums`.
_VARIANT_LINE = re.compile(r"^\s{4}([A-Z]\w*)\s*(=|,|$)")


def _crate_enums(esp_src: Path) -> list[tuple[str, frozenset[str]]]:
    """Read every unit enum's full variant set from the ``tes3`` esp crate.

    Args:
        esp_src: The crate's ``libs/esp/src`` directory.

    Returns:
        ``(enum name, variants)`` for each enum with at least two unit variants
        -- the authoritative, complete lists, including variants no vanilla
        record uses.
    """
    out: list[tuple[str, frozenset[str]]] = []
    for path in sorted(esp_src.rglob("*.rs")):
        name: str | None = None
        variants: set[str] = set()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            head = _ENUM_HEAD.match(line)
            if head:
                name = head.group(1)
                variants = set()
                continue
            if name is None:
                continue
            if line.startswith("}"):
                if len(variants) >= _MIN:
                    out.append((name, frozenset(variants)))
                name = None
                continue
            match = _VARIANT_LINE.match(line)
            if match:
                variants.add(match.group(1))
    return out


def _enums(
    seen: dict[str, set[str]], crate: list[tuple[str, frozenset[str]]]
) -> dict[str, tuple[str, ...]]:
    """Keep the fields whose values look like an enum; complete them from the crate.

    A field qualifies when its vanilla values form a small closed set of
    PascalCase names. When the crate is available, the field is matched to the
    smallest crate enum whose variants are a superset of those values, and that
    enum's *full* list is used -- so a variant no vanilla record uses (e.g.
    ``SpellType::Curse``) is still offered. Without the crate, or with no match,
    the vanilla values stand on their own.

    Args:
        seen: Every field's string value set, from :func:`_harvest`.
        crate: The crate's enums, from :func:`_crate_enums` (empty if absent).

    Returns:
        Field name -> its variants, sorted, for the fields that qualify.
    """
    out: dict[str, tuple[str, ...]] = {}
    for name, values in seen.items():
        if name in _DENY or not _MIN <= len(values) <= _MAX:
            continue
        if not all(_VARIANT.match(value) for value in values):
            continue
        supersets = [variants for _enum, variants in crate if values <= variants]
        chosen = min(supersets, key=len) if supersets else values
        out[name] = tuple(sorted(chosen))
    return dict(sorted(out.items()))


def _render(enums: dict[str, tuple[str, ...]]) -> str:
    """Render the generated data module's source text.

    Args:
        enums: The qualifying enum fields and their variants.

    Returns:
        The full text of ``wraithguard/patch/enum_data.py``.
    """
    lines = [
        '"""TES3 enum variants by field name -- GENERATED, do not edit by hand.',
        "",
        "See ``tools/gen_tes3_enums.py``. Harvested from tes3conv's output for the",
        "vanilla masters, so the spellings are the crate's own. Best-effort: a field",
        "here is one whose vanilla values form a small closed set of PascalCase names.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Final",
        "",
        f"#: {len(enums)} enum fields, keyed by leaf field name.",
        "FIELD_ENUMS: Final[dict[str, tuple[str, ...]]] = {",
    ]
    for name, variants in enums.items():
        rendered = ", ".join(repr(v) for v in variants)
        lines.append(f"    {name!r}: ({rendered}),")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _find_crate(root: Path) -> Path | None:
    """Locate the ``tes3`` crate's esp source, if it is present.

    Args:
        root: The repository root.

    Returns:
        Its ``libs/esp/src`` directory -- from ``$TES3_CRATE`` or a ``tes3-main``
        checkout beside the repo -- or ``None`` to fall back to harvest-only.
    """
    import os

    override = os.environ.get("TES3_CRATE")
    candidates = [Path(override)] if override else []
    candidates.append(root.parent / "tes3-main")
    for base in candidates:
        esp_src = base / "libs" / "esp" / "src"
        if esp_src.is_dir():
            return esp_src
    return None


def main() -> None:
    """Harvest the vanilla dumps, complete against the crate, write the module."""
    root = Path(__file__).resolve().parent.parent
    esp_src = _find_crate(root)
    crate = _crate_enums(esp_src) if esp_src else []
    enums = _enums(_harvest(root / "tes3conv_json"), crate)
    target = root / "wraithguard" / "patch" / "enum_data.py"
    target.write_text(_render(enums), encoding="utf-8")
    source = f"crate at {esp_src}" if esp_src else "vanilla dumps only (no crate found)"
    print(f"wrote {target} -- {len(enums)} enum field(s); variants from {source}")


if __name__ == "__main__":
    main()
