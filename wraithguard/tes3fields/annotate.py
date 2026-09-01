"""Explaining a diffed field in the terms the file format uses.

The field-diff window lists tes3conv's own key names -- ``vertex_heights``,
``landscape_flags``, ``num_objects``. They are readable, but they do not say
what the field *is* in the file: how wide it should be, whether the game
requires it, or which subrecord it came out of. The schema knows all three, so
this module joins the two vocabularies.

**Where the join is uncertain, there is no join.** tes3conv's names are its own
invention and nothing states the correspondence, so the mapping below is written
out by hand and only for the record types whose JSON this project has actually
read. A key with no entry is simply not annotated -- an unlabelled field costs a
reader nothing, while a confidently *wrong* label ("this is the 12-byte NPC
data") would send them looking in the wrong place.

Struct decoding is deliberately absent: tes3conv already expands struct
subrecords into JSON objects, so re-deriving them from bytes here would add a
second, worse answer to a question already answered. The blobs that arrive
*unexpanded* -- compressed vertex data, path-grid edges -- are handled by
:mod:`wraithguard.tes3fields.landscape` and :mod:`~.pathgrid`, which know their
compression as well as their layout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from wraithguard.tes3fields.naming import record_for, subrecord_for

if TYPE_CHECKING:
    from wraithguard.tes3fields.schema_types import Record

#: Keys that mean the same thing in every record that has them. ``id`` is the
#: ``NAME`` subrecord in every record type that carries one, which is the one
#: naming convention the format holds to throughout.
_COMMON_KEYS: Final[dict[str, str]] = {
    "id": "NAME",
    "name": "FNAM",
    "script": "SCRI",
    "mesh": "MODL",
    "model": "MODL",
    "icon": "ITEX",
}

#: tes3conv key -> subrecord tag, per record type. Only types whose JSON output
#: this project has read are listed; see the module docstring on why the gaps
#: are left as gaps.
_KEYS_BY_TYPE: Final[dict[str, dict[str, str]]] = {
    "Landscape": {
        "grid": "INTV",
        "landscape_flags": "DATA",
        "vertex_normals": "VNML",
        "vertex_heights": "VHGT",
        "world_map_data": "WNAM",
        "vertex_colors": "VCLR",
        "texture_indices": "VTEX",
    },
    "PathGrid": {
        "data": "DATA",
        "cell": "NAME",
        "points": "PGRP",
        "connections": "PGRC",
    },
    "Cell": {
        "name": "NAME",
        "data": "DATA",
        "region": "RGNN",
        "references": "FRMR",
        "map_color": "NAM5",
        "water_height": "WHGT",
        "atmosphere_data": "AMBI",
    },
    "LandscapeTexture": {
        "id": "NAME",
        "index": "INTV",
        "file_name": "DATA",
    },
    "Header": {
        "version": "HEDR",
        "file_type": "HEDR",
        "author": "HEDR",
        "description": "HEDR",
        "num_objects": "HEDR",
        "masters": "MAST",
    },
}


def tag_for_key(record_type: str, key: str) -> str | None:
    """Find the subrecord a tes3conv field came from.

    Args:
        record_type: tes3conv's ``"type"`` value, e.g. ``"Landscape"``.
        key: The flattened field name, e.g. ``"vertex_heights.data"``. Only the
            part before the first dot is looked up: the sub-keys tes3conv
            invents inside a subrecord (``.data``, ``.offset``) belong to its
            JSON shape, not to the file format.

    Returns:
        The four-character tag, or ``None`` when the correspondence is not
        recorded for this record type.
    """
    head = key.split(".", 1)[0]
    by_type = _KEYS_BY_TYPE.get(record_type, {})
    return by_type.get(head) or _COMMON_KEYS.get(head)


def field_note(record_type: str, key: str) -> str | None:
    """Describe a diffed field in the file format's own terms.

    Args:
        record_type: tes3conv's ``"type"`` value.
        key: The flattened field name.

    Returns:
        A phrase for the detail window's header, e.g. ``"VHGT - Height Data
        (struct, 4,232 bytes, optional)"``, or ``None`` when the field cannot
        be tied to a documented subrecord.
    """
    tag = tag_for_key(record_type, key)
    if tag is None:
        return None
    sub = subrecord_for(record_type, tag)
    return sub.describe() if sub is not None else None


def _member_lines(record: Record) -> list[str]:
    """Render one record's subrecords, with struct layouts indented beneath.

    Args:
        record: The documented record.

    Returns:
        One line per subrecord, plus one per struct member.
    """
    lines: list[str] = []
    for sub in record.fields:
        lines.append(f"  {sub.describe()}")
        if sub.variants:
            lines.extend(f"      {variant}" for variant in sub.variants)
            lines.append("      (layout depends on a flag; not decoded here)")
        lines.extend(f"      {member.describe()}" for member in sub.members)
        if sub.repeat > 1:
            lines.append(f"      x{sub.repeat}, filling {sub.fixed_size} bytes")
    return lines


def layout_text(record_type: str) -> str | None:
    """Render the documented layout of a record type as plain text.

    Written for a reference pane beside a diff: it answers "what *should* be in
    this record", which is the question a diff of two versions of it keeps
    raising.

    Args:
        record_type: tes3conv's ``"type"`` value, or a raw four-character tag.

    Returns:
        The layout, or ``None`` if the reference does not document this type.
    """
    record = record_for(record_type)
    if record is None:
        return None
    lines = [f"{record.name} record", ""]
    if record.description:  # pragma: no cover - every documented record carries a description
        lines += [record.description, ""]
    lines.append("Subrecords (+ required, - optional, * may repeat):")
    lines += _member_lines(record)
    lines += [
        "",
        "Source: UESP's Morrowind Mod File Format pages. Sizes are as documented;",
        "a subrecord with no layout shown is one the reference states in prose.",
    ]
    return "\n".join(lines)
