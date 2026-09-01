"""Convert ESP records to, and from, ``tes3conv``'s JSON schema.

``tes3conv`` is ``serde_json`` over the tes3 crate's record structs -- the same
structs :mod:`wraithguard.esp` mirrors. This module reproduces that JSON exactly
from the native records, so the conflict/patch/field pipeline (which was written
against ``tes3conv``'s output) can run on the built-in reader with no external
process, and ``tes3conv`` becomes optional rather than required.

The schema is the tes3 crate's ``#[esp_meta]`` serde derivation, verified
field-by-field against a real ``tes3conv`` binary. Its rules, in full:

* A record is internally tagged: ``{"type": <VariantName>, ...its fields}`` --
  the variant name is the record class's own name (``Npc``, ``GameSetting``).
* Fields serialize in struct order under their Rust snake_case name; the port's
  keyword-avoiding trailing underscore (``class_``) is dropped for the wire
  (``class``). ``Option`` fields are omitted entirely when ``None``.
* An enum serializes as its variant *name* string (``"HeavyArmor"``); a
  ``bitflags`` set as its set flag names joined by ``" | "`` (empty -> ``""``).
* A fixed array ``[u8; 4]`` (a colour, an attribute block) is a JSON array of
  numbers. A ``Vec``/``Box`` of a numeric primitive is base64 of the *zstd*
  compression of its bytes -- either bare (``Script.bytecode``) or, when the
  crate wraps it in a one-field struct, as ``{"data": <base64>}``
  (``Landscape`` terrain layers; ``vertex_heights`` also carries its ``offset``).
* A tuple ``(i32, String)`` (an inventory row, a master) is a JSON array.

A handful of fields the port groups differently from the crate are bridged by
name: the ``Landscape`` terrain layers and the ``Script``/``PathGrid`` blobs; the
adjacently-tagged values of a game setting, a global and a dialogue filter
(``{"type": "Integer", "data": ...}``); a class's ten minor/major skills; an
actor's AI packages (a five-variant tagged union); and the dialogue quest
markers. Everything else follows from the record's dataclass fields. Verified
value-for-value against ``tes3conv`` on the 115,380 records of a full mainland
ESM, across all 37 record types.
"""

from __future__ import annotations

import base64
import dataclasses
import enum
import types
import typing
from typing import TYPE_CHECKING, Any, get_args, get_origin

from wraithguard.esp.enums import GlobalType, SkillId
from wraithguard.esp.record import REGISTRY, Record

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Cache for :func:`_by_name`, filled on first use rather than at import: the
#: record classes register as their modules load, which can be after this one.
_BY_NAME: dict[str, type[Record]] = {}


def _by_name() -> dict[str, type[Record]]:
    """Record class name -> class, for :func:`record_from_json`.

    The JSON tags a record by its class name (the crate's variant); the registry
    keys on the four-byte tag. Built lazily and cached, so it reflects every
    record registered by the time a record is first decoded, regardless of the
    order the ``esp`` submodules imported in.

    Returns:
        The name-to-class map, rebuilt if new records have registered since.
    """
    if len(_BY_NAME) != len(REGISTRY):
        _BY_NAME.update({cls.__name__: cls for cls in REGISTRY.values()})
    return _BY_NAME


#: Bare ``Vec<T>`` fields, by record -> field -> element width. The crate's
#: ``Save`` for a ``Vec`` writes a ``u32`` element count then the packed
#: little-endian elements; the whole thing is zstd-compressed and base64'd into a
#: plain string. ``"u8"`` elements are our ``bytes`` fields; ``"u32"`` our lists.
_VEC_BLOBS: dict[str, dict[str, str]] = {
    "Script": {"bytecode": "u8", "variables": "u8"},
    "PathGrid": {"connections": "u32"},
}

#: Newtype-wrapped ``Box<[..]>`` blobs: ``{"data": base64(zstd(raw bytes))}``.
#: A ``Box`` is fixed-size, so -- unlike a ``Vec`` -- it has no count prefix.
_DATA_BLOBS: dict[str, frozenset[str]] = {
    "Landscape": frozenset(
        {"vertex_normals", "world_map_data", "vertex_colors", "texture_indices"},
    ),
}

#: Struct byte width of each ``Vec`` element format above.
_VEC_ELEM_SIZE: dict[str, int] = {"u8": 1, "u32": 4}

#: Sub-struct field the port spells differently from the crate: our name -> wire.
#: (The crate's ``CellData.flags`` clashes with the record's own ``flags``, so
#: the port disambiguates it; on the wire it is ``flags`` like the crate.)
_FIELD_RENAMES: dict[str, dict[str, str]] = {"CellData": {"cell_flags": "flags"}}

#: The crate splits a class's ten skills into named minor/major fields, in this
#: order; the port keeps them as one ``skills`` tuple. The two are bridged here.
_CLASS_SKILL_NAMES: tuple[str, ...] = (
    "minor1", "major1", "minor2", "major2", "minor3",
    "major3", "minor4", "major4", "minor5", "major5",
)  # fmt: skip

#: An AI package is a tagged union in the crate: ``{"type": <variant>, ...}``.
#: The port models each variant as its own class; this maps the two, both ways.
_AI_PACKAGE_TAG: dict[str, str] = {
    "AiTravelPackage": "Travel",
    "AiWanderPackage": "Wander",
    "AiEscortPackage": "Escort",
    "AiFollowPackage": "Follow",
    "AiActivatePackage": "Activate",
}

#: The crate spreads a wander package's eight idle weights over these fields; the
#: port keeps them as one ``idles`` tuple, in this order.
_WANDER_IDLES: tuple[str, ...] = (
    "idle2",
    "idle3",
    "idle4",
    "idle5",
    "idle6",
    "idle7",
    "idle8",
    "idle9",
)

#: DialogueInfo quest markers: the port's lowercase tag <-> the crate's enum name.
_QUEST_STATE_WIRE: dict[str, str] = {"name": "Name", "finished": "Finished", "restart": "Restart"}
_QUEST_STATE_PY: dict[str, str] = {v: k for k, v in _QUEST_STATE_WIRE.items()}


def _ai_package_to_json(obj: Any) -> dict[str, Any]:  # noqa: ANN401 - one AI-package variant
    """Serialise one AI package as its tagged union object.

    ``{"type": <variant>, ...fields}``, with a wander package's ``idles`` tuple
    spread back over the crate's ``idle2``..``idle9`` fields.

    Args:
        obj: One of the five ``Ai*Package`` dataclasses.

    Returns:
        The tagged JSON object.
    """
    name = type(obj).__name__
    out: dict[str, Any] = {"type": _AI_PACKAGE_TAG[name]}
    for field in dataclasses.fields(obj):
        value = getattr(obj, field.name)
        if name == "AiWanderPackage" and field.name == "idles":
            out.update(dict(zip(_WANDER_IDLES, value, strict=False)))
        else:
            out[_wire_name(field.name)] = _to_json_value(value)
    return out


def _ai_package_from_json(elem: dict[str, Any]) -> Any:  # noqa: ANN401 - builds one variant
    """Rebuild one AI package from its tagged union object.

    Args:
        elem: ``{"type": <variant>, ...fields}``.

    Returns:
        The matching ``Ai*Package`` dataclass instance.
    """
    from wraithguard.esp.records import _ai

    cls = getattr(_ai, next(k for k, v in _AI_PACKAGE_TAG.items() if v == elem.get("type")))
    if cls is _ai.AiWanderPackage:
        idles = tuple(elem[label] for label in _WANDER_IDLES if label in elem)
        fixed = {k: v for k, v in elem.items() if k not in _WANDER_IDLES and k != "type"}
        return cls(idles=idles, **fixed)
    return _struct_from_json(cls, elem)


def _gmst_tag(value: object) -> str:
    """The adjacent-enum tag tes3conv gives a game-setting value by its type."""
    if isinstance(value, bool):  # pragma: no cover - no boolean GMSTs, but bool < int
        raise EspJsonError("a game setting value cannot be a bool")
    if isinstance(value, int):
        return "Integer"
    if isinstance(value, float):
        return "Float"
    return "String"


def _pack_vec(value: bytes | list[int], elem: str) -> bytes:
    """Encode a ``Vec`` field as the crate's ``Save`` does: count then elements.

    Args:
        value: The field value -- ``bytes`` for ``u8`` elements, a list of ints
            for ``u32``.
        elem: The element format, ``"u8"`` or ``"u32"``.

    Returns:
        A ``u32`` little-endian count followed by the packed elements.
    """
    import struct

    if elem == "u8":
        body = bytes(value)
        return struct.pack("<I", len(body)) + body
    items = list(value)
    return struct.pack(f"<I{len(items)}I", len(items), *items)


def _unpack_vec(raw: bytes, elem: str) -> object:
    """Decode a ``Vec`` field packed by :func:`_pack_vec`.

    Args:
        raw: The decompressed bytes: a ``u32`` count then the elements.
        elem: The element format, ``"u8"`` or ``"u32"``.

    Returns:
        ``bytes`` for ``u8`` elements, or a list of ints for ``u32``.
    """
    import struct

    body = raw[4:]
    if elem == "u8":
        return body
    return list(struct.unpack(f"<{len(body) // _VEC_ELEM_SIZE['u32']}I", body))


class EspJsonError(ValueError):
    """A record could not be converted to or from the tes3conv JSON schema."""


def _zstd_available() -> bool:
    """Whether the optional ``zstandard`` extra is importable."""
    try:
        import zstandard  # noqa: F401
    except ImportError:
        return False
    return True


def _compress(raw: bytes) -> bytes:
    """Zstd-compress ``raw`` to match tes3conv, or return it unchanged.

    tes3conv compresses every ``Vec``/``Box`` blob with zstd before base64. With
    ``zstandard`` installed the output matches it; without, the bytes are left
    raw so the native path still round-trips against *itself* (the pipeline only
    ever compares records produced by one backend), just not byte-for-byte with
    tes3conv. :func:`_decompress` reverses whichever path was taken.

    Args:
        raw: The uncompressed field bytes.

    Returns:
        The zstd frame, or ``raw`` if ``zstandard`` is absent.
    """
    if not _zstd_available():
        return raw
    import zstandard

    return zstandard.ZstdCompressor().compress(raw)


def _decompress(data: bytes) -> bytes:
    """Reverse :func:`_compress`: decode a zstd frame, or pass raw bytes through.

    Args:
        data: Bytes from a base64 blob -- a zstd frame, or already raw.

    Returns:
        The uncompressed field bytes.
    """
    if data[:4] != b"\x28\xb5\x2f\xfd":  # not a zstd magic -> stored raw
        return data
    if not _zstd_available():
        raise EspJsonError("this blob is zstd-compressed but the zstandard extra is not installed")
    import io

    import zstandard

    # tes3conv's frames omit the embedded content size, so the one-shot
    # ``decompress`` (which needs it) fails; the streaming reader does not.
    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as reader:
        return reader.read()


def _b64zstd(raw: bytes) -> str:
    """Encode field bytes as tes3conv does: base64 of their zstd compression."""
    return base64.standard_b64encode(_compress(raw)).decode("ascii")


def _unb64zstd(text: str) -> bytes:
    """Decode a tes3conv base64 blob back to the raw field bytes."""
    return _decompress(base64.standard_b64decode(text))


def _flags_to_str(value: enum.IntFlag) -> str:
    """Render a ``bitflags`` set as tes3conv does: set names joined by `` | ``.

    Named bits appear in declaration order; any leftover bit with no name is
    appended as ``0x`` hex, matching the ``bitflags`` crate's ``Display``. An
    empty set is the empty string.

    Args:
        value: The flags value.

    Returns:
        The wire string, e.g. ``"MODIFIED | DELETED"`` or ``""``.
    """
    cls = type(value)
    parts: list[str] = [
        member.name
        for member in cls
        if member.name is not None and member.value and member in value
    ]
    named = 0
    for member in cls:
        named |= member.value
    leftover = int(value) & ~named
    if leftover:
        parts.append(f"0x{leftover:x}")
    return " | ".join(parts)


def _flags_from_str(cls: type[enum.IntFlag], text: str) -> enum.IntFlag:
    """Parse a `` | ``-joined flag string back into a flags value.

    Args:
        cls: The ``IntFlag`` subclass to build.
        text: The wire string from :func:`_flags_to_str`.

    Returns:
        The reconstructed flags, unknown ``0x`` bits preserved.
    """
    result = 0
    for token in (part.strip() for part in text.split("|")):
        if not token:
            continue
        if token.startswith("0x"):
            result |= int(token, 16)
        else:
            result |= cls[token].value
    return cls(result)


def _wire_name(field_name: str) -> str:
    """The JSON key for a dataclass field: its name without a keyword underscore."""
    return field_name[:-1] if field_name.endswith("_") else field_name


def _enum_name(value: enum.IntEnum) -> str:
    """The wire spelling of an enum variant: its name without a keyword underscore."""
    return _wire_name(value.name)


def _to_json_value(value: Any) -> Any:  # noqa: ANN401 - a JSON value is genuinely dynamic
    """Convert one field value to its JSON form, following the crate's rules.

    Bytes default to a numeric array (a fixed ``[u8; N]``); the two blob shapes
    that differ are handled by name in :func:`_struct_to_json`, not here.

    Args:
        value: A field value: scalar, str, bytes, enum, flags, dataclass, or a
            list/tuple of those.

    Returns:
        A JSON-serialisable value.
    """
    if isinstance(value, enum.IntFlag):
        return _flags_to_str(value)
    if isinstance(value, enum.IntEnum):
        return _enum_name(value)
    if isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, bytes | bytearray):
        return list(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _struct_to_json(value)
    if isinstance(value, list | tuple):
        return [_to_json_value(item) for item in value]
    raise EspJsonError(f"no JSON rule for a value of type {type(value).__name__}")


def _struct_to_json(obj: Any) -> dict[str, Any]:  # noqa: ANN401 - any record or sub-struct
    """Convert a record or sub-struct dataclass to a JSON object.

    Fields serialise in declaration order under their wire name; ``None`` fields
    are skipped; the landscape and script blob fields take their special shapes.

    Args:
        obj: A dataclass instance (a record or one of its sub-structs).

    Returns:
        The field mapping (without the ``"type"`` tag, which records add).
    """
    name = type(obj).__name__
    if name in _AI_PACKAGE_TAG:
        return _ai_package_to_json(obj)
    vec_blobs = _VEC_BLOBS.get(name, {})
    data_blobs = _DATA_BLOBS.get(name, frozenset())
    renames = _FIELD_RENAMES.get(name, {})
    out: dict[str, Any] = {}
    for field in dataclasses.fields(obj):
        fname = field.name
        if name == "Landscape" and fname == "vertex_heights_offset":
            continue  # folded into the vertex_heights object below
        if name == "GlobalVariable" and fname == "global_type":
            continue  # folded into the adjacently-tagged value object below
        value = getattr(obj, fname)
        if value is None:
            continue
        if name == "DialogueInfo" and fname == "quest_state":
            out["quest_state"] = _QUEST_STATE_WIRE.get(value, value)
        elif name == "GlobalVariable" and fname == "value":
            out["value"] = {"type": _enum_name(obj.global_type), "data": value}
        elif name in ("GameSetting", "Filter") and fname == "value":
            out["value"] = {"type": _gmst_tag(value), "data": value}
        elif name == "ClassData" and fname == "skills":
            for label, skill in zip(_CLASS_SKILL_NAMES, value, strict=False):
                out[label] = _enum_name(skill)
        elif fname in vec_blobs:
            out[renames.get(fname) or _wire_name(fname)] = _b64zstd(
                _pack_vec(value, vec_blobs[fname])
            )
        elif fname in data_blobs:
            out[renames.get(fname) or _wire_name(fname)] = {"data": _b64zstd(bytes(value))}
        elif name == "Landscape" and fname == "vertex_heights":
            out["vertex_heights"] = {
                "offset": obj.vertex_heights_offset,
                "data": _b64zstd(bytes(value)),
            }
        else:
            out[renames.get(fname) or _wire_name(fname)] = _to_json_value(value)
    return out


def record_to_json(record: Record) -> dict[str, Any]:
    """Convert one record to a tes3conv JSON object, tag first.

    Args:
        record: A parsed record from :mod:`wraithguard.esp`.

    Returns:
        ``{"type": <ClassName>, ...fields}`` matching tes3conv.
    """
    return {"type": type(record).__name__, **_struct_to_json(record)}


def plugin_to_json(records: Iterable[Record]) -> list[dict[str, Any]]:
    """Convert a whole plugin's records to the tes3conv JSON array.

    Args:
        records: The records, e.g. from :func:`wraithguard.esp.read_plugin`.

    Returns:
        A list of record objects, in the order given.
    """
    return [record_to_json(record) for record in records]


def _from_json_value(value: Any, annotation: Any) -> Any:  # noqa: ANN401 - driven by the annotation
    """Rebuild a field value from JSON, guided by its dataclass annotation.

    Args:
        value: The JSON value.
        annotation: The resolved field type (from ``typing.get_type_hints``).

    Returns:
        The reconstructed Python value.
    """
    origin = get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:  # X | None and Union[X, None]
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if value is None:
            return None
        return _from_json_value(value, args[0]) if args else value
    if isinstance(annotation, type) and issubclass(annotation, enum.IntFlag):
        return _flags_from_str(annotation, value)
    if isinstance(annotation, type) and issubclass(annotation, enum.IntEnum):
        return _enum_member(annotation, value)
    if annotation in (int, float, str, bool):
        return value
    if annotation in (bytes, bytearray):
        return _bytes_from_json(value)
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        return _struct_from_json(annotation, value)
    if origin in (list, typing.List):  # noqa: UP006 - get_origin can return either
        sub = get_args(annotation)
        elem = sub[0] if sub else Any
        return [_from_json_value(item, elem) for item in value]
    if origin in (tuple, typing.Tuple):  # noqa: UP006
        params = get_args(annotation)
        if len(params) == 2 and params[1] is Ellipsis:
            return tuple(_from_json_value(item, params[0]) for item in value)
        return tuple(
            _from_json_value(item, param) for item, param in zip(value, params, strict=False)
        )
    return value


def _bytes_from_json(value: Any) -> bytes:  # noqa: ANN401 - array, base64 string, or blob object
    """Rebuild a bytes field from any of its three JSON shapes.

    Args:
        value: A numeric array (fixed array), a base64 string (a bare blob), or
            a ``{"data": ...}`` object (a wrapped blob).

    Returns:
        The raw field bytes.
    """
    if isinstance(value, str):
        return _unb64zstd(value)
    if isinstance(value, dict):
        return _unb64zstd(value["data"])
    return bytes(value)


def _enum_member(cls: type[enum.IntEnum], name: str) -> enum.IntEnum:
    """Look up an enum variant by its wire name, restoring a keyword underscore.

    Args:
        cls: The ``IntEnum`` subclass.
        name: The wire variant name.

    Returns:
        The matching member.
    """
    try:
        return cls[name]
    except KeyError:
        return cls[f"{name}_"]


def _struct_from_json(cls: type, obj: dict[str, Any]) -> Any:  # noqa: ANN401 - builds any dataclass
    """Rebuild a record or sub-struct dataclass from its JSON object.

    Args:
        cls: The dataclass to build.
        obj: The JSON object (without the ``"type"`` tag).

    Returns:
        The constructed instance, its ``None`` and defaulted fields left default.
    """
    name = cls.__name__
    hints = typing.get_type_hints(cls)
    vec_blobs = _VEC_BLOBS.get(name, {})
    renames = _FIELD_RENAMES.get(name, {})
    kwargs: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        fname = field.name
        if name == "Landscape" and fname in ("vertex_heights", "vertex_heights_offset"):
            heights = obj.get("vertex_heights")
            if heights is not None:
                kwargs["vertex_heights_offset"] = heights.get("offset", 0.0)
                kwargs["vertex_heights"] = _unb64zstd(heights["data"])
            continue
        if name == "GlobalVariable" and fname == "global_type":
            tagged = obj.get("value")
            if tagged is not None:
                kwargs["global_type"] = _enum_member(GlobalType, tagged["type"])
            continue
        if name in ("GlobalVariable", "GameSetting", "Filter") and fname == "value":
            tagged = obj.get("value")
            if tagged is not None:
                kwargs["value"] = tagged["data"]
            continue
        if name == "ClassData" and fname == "skills":
            kwargs["skills"] = tuple(
                _enum_member(SkillId, obj[label]) for label in _CLASS_SKILL_NAMES if label in obj
            )
            continue
        if fname == "ai_packages" and name in ("Npc", "Creature"):
            kwargs["ai_packages"] = [_ai_package_from_json(e) for e in obj.get("ai_packages", [])]
            continue
        if name == "DialogueInfo" and fname == "quest_state":
            if "quest_state" in obj:
                kwargs["quest_state"] = _QUEST_STATE_PY.get(obj["quest_state"], obj["quest_state"])
            continue
        key = renames.get(fname) or _wire_name(fname)
        if key not in obj:
            continue
        if fname in vec_blobs:
            kwargs[fname] = _unpack_vec(_unb64zstd(obj[key]), vec_blobs[fname])
        else:
            kwargs[fname] = _from_json_value(obj[key], hints.get(fname, Any))
    return cls(**kwargs)


def record_from_json(obj: dict[str, Any]) -> Record:
    """Rebuild a record from a tes3conv JSON object.

    Args:
        obj: ``{"type": <ClassName>, ...fields}``.

    Returns:
        The parsed record.

    Raises:
        EspJsonError: If ``type`` is missing or names no known record.
    """
    name = obj.get("type")
    if not isinstance(name, str):
        raise EspJsonError("record JSON has no string 'type' tag")
    cls = _by_name().get(name)
    if cls is None:
        raise EspJsonError(f"unknown record type {name!r}")
    record = _struct_from_json(cls, obj)
    if not isinstance(record, Record):  # pragma: no cover - _BY_NAME holds only records
        raise EspJsonError(f"{name} did not build a record")
    return record


def plugin_from_json(objects: Iterable[dict[str, Any]]) -> list[Record]:
    """Rebuild a plugin's records from a tes3conv JSON array.

    Args:
        objects: The record objects.

    Returns:
        The parsed records, in the order given.
    """
    return [record_from_json(obj) for obj in objects]
