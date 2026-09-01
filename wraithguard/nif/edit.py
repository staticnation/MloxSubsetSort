"""Edit values inside a NIF block, then write the file back with :func:`write_nif`.

The reader keeps each block's body verbatim (:attr:`~wraithguard.nif.reader.Block.raw`)
and, on request, where every field sits inside it
(:func:`~wraithguard.nif.reader.field_spans`). That pair is all an editor needs:
to change one field, splice new bytes over that field's span and leave the rest
of the block -- and every other block, and the framing -- untouched. A NIF block
carries no internal length, so a field growing or shrinking (a longer texture
path, say) is not a problem the way it would be in a length-prefixed format:
the next field simply starts where this one now ends, and the reader finds it by
walking, exactly as it found it before.

This is deliberately *not* a general block encoder. It does not re-serialise a
block from parsed values -- the reader keeps counts, not the elements, so there
is nothing to re-serialise from. It edits the bytes in place, which is both
smaller and safer: an untouched field is copied through byte-for-byte, so the
only bytes that can change are the ones asked for.

Two kinds of edit are provided, the two asked for:

- **Text fields** (:func:`set_string_field`) -- a length-prefixed string, such
  as a :class:`NiSourceTexture` filename. The new value is re-length-prefixed;
  the block grows or shrinks to fit. Guarded so it refuses a field that is not
  actually a length-prefixed string, which is what stops a caller turning the
  non-external branch of a texture source into corruption.
- **Vertex positions** (:func:`translate_vertices`) -- every vertex in a
  geometry block shifted by the same offset, same byte length, and the block's
  bounding-sphere centre moved with them so the two stay consistent.

Higher up, :func:`texture_paths` lists a file's external texture references and
:func:`swap_texture_path` rewrites them, each returning a new
:class:`~wraithguard.nif.reader.NifFile` ready for :func:`write_nif`. Everything
here returns new frozen objects rather than mutating; the input file is left as
it was read.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from wraithguard.nif.blocks import block_layout
from wraithguard.nif.reader import (
    Block,
    NifFile,
    NifParseError,
    field_spans,
    read_nif_bytes,
    write_nif,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: The encoding the reader decodes strings with (Windows-era asset paths), used
#: here to re-encode an edited one so a round trip is exact.
_STRING_ENCODING = "cp1252"

#: Block types whose ``external_or_internal`` body is a texture filename when
#: ``use_external`` is set. Both share the flag-then-body shape.
_TEXTURE_SOURCE_TYPES = frozenset({"NiSourceTexture", "NiBltSource"})

#: ``struct`` formats for the fixed-width scalar kinds a field can be set to. A
#: value written back this way keeps the field's byte width, so nothing after it
#: in the block moves -- unlike a string, which is length-prefixed and may grow.
_SCALAR_FORMATS: dict[str, str] = {
    "u8": "<B",
    "u16": "<H",
    "u32": "<I",
    "i32": "<i",
    "link": "<i",
}

#: The scalar kinds :func:`set_field` can encode, and :func:`field_views` marks
#: editable. ``bool32`` and ``f32`` are handled specially (a bool is 0/1 in a
#: ``u32``; a float has its own pack), so they are named here but not in
#: :data:`_SCALAR_FORMATS`.
_EDITABLE_SCALAR_KINDS = frozenset({*_SCALAR_FORMATS, "bool32", "f32"})


class NifEditError(NifParseError):
    """An edit could not be applied to a block as asked.

    A subclass of :class:`~wraithguard.nif.reader.NifParseError` so a caller
    already handling NIF read failures catches edit failures too, but distinct
    so "I could not change this" is never mistaken for "I could not read this".
    """


def _encode_string(text: str) -> bytes:
    """A length-prefixed string in the reader's own format.

    Args:
        text: The value to encode.

    Returns:
        A little-endian ``u32`` length followed by the ``cp1252`` bytes,
        unterminated -- the exact shape
        :meth:`~wraithguard.nif.reader._Cursor.string` reads back.
    """
    encoded = text.encode(_STRING_ENCODING, errors="replace")
    return struct.pack("<I", len(encoded)) + encoded


def _splice(block: Block, offset: int, old_length: int, new_bytes: bytes) -> bytes:
    """The block's raw bytes with ``[offset:offset+old_length]`` replaced.

    Args:
        block: The block being edited.
        offset: Where the replaced span starts, within :attr:`Block.raw`.
        old_length: How many bytes the span currently occupies.
        new_bytes: What to put there instead.

    Returns:
        The new raw body.
    """
    return block.raw[:offset] + new_bytes + block.raw[offset + old_length :]


def set_string_field(block: Block, name: str, text: str) -> Block:
    """A copy of ``block`` with its length-prefixed string field ``name`` set to ``text``.

    The field grows or shrinks to fit the new value; because NIF blocks carry no
    internal length, that is safe -- the fields after it simply move.

    Args:
        block: The block to edit, read with ``retain=True`` so its
            :attr:`~wraithguard.nif.reader.Block.raw` is present.
        name: The field to set. It must currently hold a length-prefixed string
            -- e.g. ``"external_or_internal"`` on a :class:`NiSourceTexture`
            whose ``use_external`` flag is set.
        text: The new value.

    Returns:
        A new :class:`~wraithguard.nif.reader.Block`; the original is unchanged.

    Raises:
        NifEditError: If ``block`` has no raw bytes, ``name`` is not one of its
            fields, or that field is not a length-prefixed string (so splicing
            a string there would corrupt the block).
    """
    if not block.raw:
        raise NifEditError(f"{block.type_name}: no raw bytes to edit (read with retain=True)")
    spans = field_spans(block.type_name, block.raw)
    if name not in spans:
        raise NifEditError(f"{block.type_name} has no field {name!r} to set")
    offset, length = spans[name]
    _require_length_prefixed_string(block, name, offset, length)
    new_raw = _splice(block, offset, length, _encode_string(text))
    fields = {**block.fields, name: text}
    return replace(block, fields=fields, size=len(new_raw), raw=new_raw)


def _require_length_prefixed_string(block: Block, name: str, offset: int, length: int) -> None:
    """Refuse a field whose bytes are not a self-consistent length-prefixed string.

    A texture source's ``external_or_internal`` body is a string only when
    ``use_external`` is set; otherwise it is a flag and a link. Setting a string
    on the wrong one would write a plausible-looking but corrupt block, so this
    checks the bytes actually present rather than trusting the field name.

    Args:
        block: The block being edited.
        name: The field being set, for the message.
        offset: The field's start within :attr:`Block.raw`.
        length: The field's byte length.

    Raises:
        NifEditError: If the span is not exactly a ``u32`` length followed by
            that many bytes.
    """
    if length < 4:
        raise NifEditError(
            f"{block.type_name}.{name} is {length} bytes: not a length-prefixed string"
        )
    (declared,) = struct.unpack_from("<I", block.raw, offset)
    if 4 + declared != length:
        raise NifEditError(
            f"{block.type_name}.{name} is not a length-prefixed string "
            f"(declares {declared} bytes in a {length}-byte field); refusing to overwrite it"
        )


def _is_editable_string(block: Block, name: str, kind: str) -> bool:
    """Whether field ``name`` is an editable length-prefixed string.

    A plain ``string`` field always is. A texture source's
    ``external_or_internal`` body is one *only* when its ``use_external`` flag is
    set -- otherwise it is a flag and a link, not text -- so that case is
    recognised here rather than by the field name alone.

    Args:
        block: The block the field belongs to.
        name: The field's name.
        kind: The field's layout kind.

    Returns:
        ``True`` when :func:`set_string_field` can set this field as a string.
    """
    if kind == "string":
        return True
    return kind == "source_texture_body" and bool(block.fields.get("use_external"))


def _encode_scalar(kind: str, value: object) -> bytes:
    """A scalar field's new bytes, the same width the reader read it at.

    Args:
        kind: One of :data:`_EDITABLE_SCALAR_KINDS`.
        value: The new value. Coerced to ``int`` for the integer kinds and
            ``bool`` for ``bool32``, to ``float`` for ``f32``.

    Returns:
        The packed bytes.

    Raises:
        NifEditError: If ``value`` is out of the field's range or the wrong
            type for its kind.
    """
    try:
        if kind == "f32":
            return struct.pack("<f", float(value))  # type: ignore[arg-type]
        if kind == "bool32":
            return struct.pack("<I", 1 if value else 0)
        return struct.pack(_SCALAR_FORMATS[kind], int(value))  # type: ignore[call-overload]
    except (struct.error, ValueError, TypeError) as exc:
        raise NifEditError(f"{value!r} is not a valid {kind} value: {exc}") from exc


def _tiles_exactly(type_name: str, raw: bytes) -> bool:
    """Whether ``raw`` still parses as a whole ``type_name`` block, no bytes over or short.

    After a scalar edit the block keeps its byte length, but editing a field
    that decides how many bytes come after it -- a count, or an array's
    ``has_*`` flag -- makes the layout consume a different number of bytes than
    the block holds. Re-walking and checking the fields still tile the block
    exactly is what catches that: a size-determining edit no longer tiles, and
    is refused before it can desynchronise the file.

    Args:
        type_name: The block's type.
        raw: The candidate bytes after an edit.

    Returns:
        ``True`` when the layout walks ``raw`` end to end with nothing left
        over; ``False`` when it overruns, underruns, or fails to parse.
    """
    try:
        spans = field_spans(type_name, raw)
    except NifParseError:
        return False
    return bool(spans) and max(off + length for off, length in spans.values()) == len(raw)


def set_field(block: Block, name: str, value: object) -> Block:
    """A copy of ``block`` with scalar or string field ``name`` set to ``value``.

    The general field edit behind the inspector: any single-value field can be
    written back. Integer and float fields keep their byte width; a string field
    is re-length-prefixed and may grow or shrink. A field that decides the size
    of what follows it -- a count, or an array's ``has_*`` flag -- is refused,
    because changing it without changing the array would leave the block's
    declared shape disagreeing with its bytes (see :func:`_tiles_exactly`).

    Args:
        block: The block to edit, read with ``retain=True``.
        name: The field to set.
        value: The new value -- a ``str`` for a string field, an ``int``/``bool``
            for the integer kinds, a number for ``f32``.

    Returns:
        A new :class:`~wraithguard.nif.reader.Block`; the original is unchanged.

    Raises:
        NifEditError: If ``block`` has no raw bytes, ``name`` is not a field of
            it, the field is not a scalar or string (a bulk array or compound
            cannot be set this way), the value does not fit the field, or the
            edit would change how many bytes the block's layout consumes.
    """
    if not block.raw:
        raise NifEditError(f"{block.type_name}: no raw bytes to edit (read with retain=True)")
    layout = block_layout(block.type_name)
    if layout is None:
        raise NifEditError(f"no layout for block type {block.type_name!r}")
    kinds = {entry[0]: entry[1] for entry in layout}
    if name not in kinds:
        raise NifEditError(f"{block.type_name} has no field {name!r} to set")
    kind = kinds[name]
    if _is_editable_string(block, name, kind):
        return set_string_field(block, name, str(value))
    if kind not in _EDITABLE_SCALAR_KINDS:
        raise NifEditError(
            f"{block.type_name}.{name} is a {kind}; only scalar and string fields set"
        )
    offset, length = field_spans(block.type_name, block.raw)[name]
    new_bytes = _encode_scalar(kind, value)
    new_raw = _splice(block, offset, length, new_bytes)
    if not _tiles_exactly(block.type_name, new_raw):
        raise NifEditError(
            f"{block.type_name}.{name} decides the size of a later field "
            f"(it is a count or presence flag); it cannot be edited on its own"
        )
    coerced: object = bool(value) if kind == "bool32" else value
    return replace(block, fields={**block.fields, name: coerced}, size=len(new_raw), raw=new_raw)


def translate_vertices(block: Block, dx: float, dy: float, dz: float) -> Block:
    """A copy of ``block`` with every vertex, and the bounding centre, shifted by ``(dx, dy, dz)``.

    The vertex array keeps its byte length -- only the coordinates change -- and
    the geometry's bounding-sphere ``center`` is moved by the same offset so the
    sphere still encloses the mesh. The radius is unchanged: a rigid translation
    does not change it.

    Args:
        block: A geometry block (anything with a ``vertices`` field, e.g.
            :class:`NiTriShapeData`), read with ``retain=True``.
        dx: X offset added to every vertex.
        dy: Y offset.
        dz: Z offset.

    Returns:
        A new :class:`~wraithguard.nif.reader.Block`; the original is unchanged.
        A block whose vertices are absent (``has_vertices`` was false) is
        returned unchanged -- there is nothing to move.

    Raises:
        NifEditError: If ``block`` has no raw bytes or no ``vertices`` field at
            all (it is not a geometry block).
    """
    if not block.raw:
        raise NifEditError(f"{block.type_name}: no raw bytes to edit (read with retain=True)")
    spans = field_spans(block.type_name, block.raw)
    if "vertices" not in spans:
        raise NifEditError(f"{block.type_name} has no vertices to move")
    offset, length = spans["vertices"]
    if length == 0:
        return block
    raw = _offset_triples(block.raw, offset, length, (dx, dy, dz))
    centre = spans.get("center")
    # Every geometry layout that has a vertices field also has a 12-byte center
    # (a vector3), so this guard holds on all well-formed geometry blocks; it is
    # kept defensive against a malformed layout rather than as a live branch.
    if centre is not None and centre[1] == 12:  # pragma: no branch
        raw = _offset_triples(raw, centre[0], 12, (dx, dy, dz))
    return replace(block, size=len(raw), raw=raw)


def _offset_triples(
    raw: bytes, offset: int, length: int, delta: tuple[float, float, float]
) -> bytes:
    """``raw`` with the run of ``float`` triples at ``[offset:offset+length]`` shifted by ``delta``.

    Args:
        raw: The bytes to edit.
        offset: Start of the ``vec3`` run.
        length: Its byte length; a multiple of 12 (three ``float32`` per point).
        delta: The ``(x, y, z)`` offset added to every point.

    Returns:
        A new bytes object, same length as ``raw``.
    """
    count = length // 3 // 4
    values = list(struct.unpack_from(f"<{count * 3}f", raw, offset))
    for i in range(count):
        values[i * 3] += delta[0]
        values[i * 3 + 1] += delta[1]
        values[i * 3 + 2] += delta[2]
    packed = struct.pack(f"<{count * 3}f", *values)
    return raw[:offset] + packed + raw[offset + length :]


def translate_all_vertices(nif: NifFile, dx: float, dy: float, dz: float) -> tuple[NifFile, int]:
    """A copy of ``nif`` with every geometry block's vertices shifted by ``(dx, dy, dz)``.

    Applies :func:`translate_vertices` to each block that has vertices, leaving
    the rest untouched -- the whole-mesh move a caller usually wants, rather than
    picking one shape by index. Blocks without a ``vertices`` field (nodes,
    properties, controllers) are skipped, not errors.

    Args:
        nif: A file read with ``retain=True``.
        dx: X offset added to every vertex, in every shape.
        dy: Y offset.
        dz: Z offset.

    Returns:
        ``(new_file, count)`` -- the shifted file and how many blocks moved.
        ``count`` is ``0`` and the file is returned unchanged when no block has
        vertices to move, or when the offset is zero (a move by nothing is not a
        change, and rewriting would needlessly disturb any ``-0.0`` bytes).
    """
    if dx == 0.0 and dy == 0.0 and dz == 0.0:
        return nif, 0
    blocks = list(nif.blocks)
    changed = 0
    for index, block in enumerate(blocks):
        if not block.raw:
            continue
        try:
            moved = translate_vertices(block, dx, dy, dz)
        except NifEditError:
            continue  # not a geometry block -- nothing to move here
        if moved is not block:
            blocks[index] = moved
            changed += 1
    if changed == 0:
        return nif, 0
    return _with_blocks(nif, blocks), changed


def apply_edits(data: bytes, edits: Sequence[Mapping[str, object]]) -> bytes:
    """Apply a list of edits to a mesh's bytes and return the edited bytes.

    The one entry point the editor UI drives: it reads the mesh, applies each
    edit in order, and writes it back. Each edit is a small mapping naming a
    block and what to do to it:

    - ``{"op": "set_field", "block": i, "name": n, "value": v}`` -- set a scalar
      or string field, via :func:`set_field`.
    - ``{"op": "translate", "block": i, "dx": x, "dy": y, "dz": z}`` -- shift a
      geometry block's vertices, via :func:`translate_vertices`.

    Applied to a copy; ``data`` is not touched. Either every edit applies or the
    call raises and nothing is written, so a bad edit in the middle cannot leave
    a half-edited file.

    Args:
        data: The original mesh bytes.
        edits: The edits to apply, in order.

    Returns:
        The edited mesh bytes, ready to save.

    Raises:
        NifEditError: If the mesh does not parse whole, an edit names a block
            that is not there, an edit's ``op`` is unknown, or any single edit
            is refused (see :func:`set_field` and :func:`translate_vertices`).
    """
    nif = read_nif_bytes(data, retain=True)
    if not nif.complete:
        raise NifEditError("this mesh does not parse completely, so it cannot be edited safely")
    blocks = list(nif.blocks)
    for edit in edits:
        op = str(edit.get("op", "set_field"))
        try:
            index = int(edit["block"])  # type: ignore[call-overload]
        except (KeyError, TypeError, ValueError) as exc:
            raise NifEditError(f"edit is missing a valid block index: {edit!r}") from exc
        if not 0 <= index < len(blocks):
            raise NifEditError(f"edit names block {index}, which is out of range")
        if op == "set_field":
            blocks[index] = set_field(blocks[index], str(edit["name"]), edit["value"])
        elif op == "translate":
            blocks[index] = translate_vertices(
                blocks[index], float(edit["dx"]), float(edit["dy"]), float(edit["dz"])  # type: ignore[arg-type]
            )
        else:
            raise NifEditError(f"unknown edit op {op!r}")
    return write_nif(_with_blocks(nif, blocks))


def _with_blocks(nif: NifFile, blocks: Sequence[Block]) -> NifFile:
    """``nif`` with its block list replaced, everything else (header, footer) kept.

    Args:
        nif: The file read with ``retain=True``.
        blocks: The new block list, same length and order as the original.

    Returns:
        A new :class:`~wraithguard.nif.reader.NifFile`.
    """
    return replace(nif, blocks=list(blocks))


def texture_paths(nif: NifFile) -> list[tuple[int, str]]:
    """Every external texture filename the file references, with its block index.

    A :class:`NiSourceTexture` (or :class:`NiBltSource`) points at an external
    file only when its ``use_external`` flag is set; an embedded-pixels source
    has no path and is skipped. The block index is what
    :func:`set_texture_path` takes to rewrite a specific one.

    Args:
        nif: A file read with ``retain=True``.

    Returns:
        ``(block_index, filename)`` for each external texture source, in file
        order.
    """
    found: list[tuple[int, str]] = []
    for index, block in enumerate(nif.blocks):
        if block.type_name in _TEXTURE_SOURCE_TYPES and block.fields.get("use_external"):
            path = block.fields.get("external_or_internal")
            # When use_external is set on a fully-parsed block, the body is always
            # read as a filename string (see _read_source_texture_body), so this
            # only guards against an incompletely-parsed block.
            if isinstance(path, str):  # pragma: no branch
                found.append((index, path))
    return found


def set_texture_path(nif: NifFile, block_index: int, new_path: str) -> NifFile:
    """A copy of ``nif`` with one texture source's filename set to ``new_path``.

    Args:
        nif: A file read with ``retain=True``.
        block_index: The block to change, as :func:`texture_paths` reports it.
        new_path: The replacement filename.

    Returns:
        A new :class:`~wraithguard.nif.reader.NifFile`.

    Raises:
        NifEditError: If ``block_index`` is out of range, or the block there is
            not an external texture source.
    """
    if not 0 <= block_index < len(nif.blocks):
        raise NifEditError(f"block index {block_index} is out of range (0..{len(nif.blocks) - 1})")
    block = nif.blocks[block_index]
    if block.type_name not in _TEXTURE_SOURCE_TYPES or not block.fields.get("use_external"):
        raise NifEditError(
            f"block {block_index} ({block.type_name}) is not an external texture source"
        )
    blocks = list(nif.blocks)
    blocks[block_index] = set_string_field(block, "external_or_internal", new_path)
    return _with_blocks(nif, blocks)


def swap_texture_path(nif: NifFile, old_path: str, new_path: str) -> tuple[NifFile, int]:
    """A copy of ``nif`` with every external texture equal to ``old_path`` set to ``new_path``.

    Matching is case-insensitive on the whole stored string, since Windows asset
    paths are, and a swap that missed a reference only because it was cased
    differently would be a quiet failure.

    Args:
        nif: A file read with ``retain=True``.
        old_path: The filename to replace, matched case-insensitively.
        new_path: The replacement.

    Returns:
        ``(new_file, count)`` -- the rewritten file and how many references
        changed. ``count`` is ``0`` and the file is returned unchanged when
        nothing matched, so a caller can tell a no-op from a rewrite.
    """
    target = old_path.casefold()
    blocks = list(nif.blocks)
    changed = 0
    for index, path in texture_paths(nif):
        if path.casefold() == target:
            blocks[index] = set_string_field(blocks[index], "external_or_internal", new_path)
            changed += 1
    if changed == 0:
        return nif, 0
    return _with_blocks(nif, blocks), changed


@dataclass(frozen=True, slots=True)
class FieldView:
    """One field of a block, described for an editor to show and edit.

    Attributes:
        name: The field's name in its layout.
        kind: The field's layout kind (``"f32"``, ``"u16"``, ``"string"``,
            ``"vec3_array"``, ...), which an editor maps to a widget -- a number
            box, a text box, a toggle for ``bool32``.
        value: The parsed value for a scalar or string field; for a bulk array
            the element *count* the reader keeps, since the elements themselves
            are not held.
        editable: Whether :func:`set_field` will set this field. ``True`` for a
            scalar or string that does not gate a later field; ``False`` for a
            bulk array, a compound, or a count/presence flag another field's
            size depends on -- editing which on its own would break the block.
    """

    name: str
    kind: str
    value: object
    editable: bool


def field_views(block: Block) -> list[FieldView]:
    """Every field of ``block``, in layout order, described for an editor.

    The data behind an inspector panel: what each field is called, what kind it
    is (so the panel can pick a widget), its current value, and whether it can
    be edited. A field that gates a later one -- a count or an array's ``has_*``
    flag -- is reported as present but not editable, so the panel does not offer
    an edit that :func:`set_field` would only refuse.

    Args:
        block: A block read with ``retain=True``.

    Returns:
        One :class:`FieldView` per field, in the block's layout order. Empty if
        the block's type has no known layout.

    Raises:
        NifEditError: If the block has no raw bytes to describe.
    """
    if not block.raw:
        raise NifEditError(f"{block.type_name}: no raw bytes to describe (read with retain=True)")
    layout = block_layout(block.type_name)
    if layout is None:
        return []
    gates = {entry[2] for entry in layout if len(entry) > 2}
    views: list[FieldView] = []
    for entry in layout:
        name, kind = entry[0], entry[1]
        is_string = _is_editable_string(block, name, kind)
        editable = (kind in _EDITABLE_SCALAR_KINDS or is_string) and name not in gates
        views.append(FieldView(name, kind, block.fields.get(name), editable))
    return views


__all__ = [
    "FieldView",
    "NifEditError",
    "apply_edits",
    "field_views",
    "set_field",
    "set_string_field",
    "set_texture_path",
    "swap_texture_path",
    "texture_paths",
    "translate_all_vertices",
    "translate_vertices",
]
