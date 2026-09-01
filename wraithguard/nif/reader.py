"""Walking a Morrowind NIF file block by block.

The shape of the problem, and why the code looks like this: **a NIF block has no
length field**. Blocks are written back to back, each preceded only by its type
string, so the only way to find block *n+1* is to have parsed block *n* exactly.
There is no resynchronisation and no skipping. One wrong field width does not
raise -- it shifts every subsequent read, and the damage usually surfaces much
later as a type string that is not a type string.

Two consequences run through this module:

* **Unknown blocks stop the read.** Guessing a length, or scanning forward for
  the next plausible type name, would produce a report that looks complete and
  is not. :class:`NifFile` therefore records how many blocks were read and why
  it stopped, and a caller can say "12 of 47 blocks" rather than implying 47.
* **Every read is bounds-checked.** The files come from the internet by way of
  a mod archive; a truncated or hostile one must produce
  :class:`NifParseError`, never an exception from :mod:`struct` and never a
  silent short read.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from wraithguard.nif.blocks import FIXED_WIDTHS, block_layout

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from wraithguard.nif.blocks import Field

#: The only NIF version Morrowind ships. Encoded as the file writes it: the
#: four version components packed one per byte, most significant first.
NIF_VERSION_MORROWIND: int = 0x04000002

#: Versions this reader accepts. ``4.0.0.0`` is the same format: the header
#: differs and the block layouts do not.
#:
#: Established by measurement, not by report. 40 of these files were taken from
#: a mod collection, their version word alone rewritten to ``4.0.0.2``, and
#: every one then parsed *identically* to the layout-free scan -- 40 identical,
#: 0 diverged, 0 stopped early. Had any layout differed the scan would have
#: named the block where, so this is the same evidence any other layout claim
#: in this reader rests on.
#:
#: Refusing them was costing 45 meshes in one mod collection for no benefit,
#: since the refusal was based on the version word rather than on anything the
#: reader could not read.
ACCEPTED_VERSIONS: Final[frozenset[int]] = frozenset({0x04000000, NIF_VERSION_MORROWIND})

#: The header line, up to but not including its newline. Checked as a prefix
#: rather than for equality: the version follows in the same line and is read
#: from the binary field below it, which is the authoritative copy.
_HEADER_PREFIX = b"NetImmerse File Format"

#: A ceiling on any count read from the file before it is used to size a read.
#: A corrupt length field is otherwise an instruction to allocate whatever
#: number happened to be at that offset.
_MAX_COUNT = 1 << 24

#: Bytes following the bounding box's *type* word, by that type.
#:
#: The box is not a fixed size, which cost a wrong fix before it was
#: understood. Type 1 carries a translation, a 3x3 rotation and an extents
#: triple -- visible as a literal identity matrix and extents of 5.0, 5.0, 5.0
#: in ``SpAr.NIF``, with the child list starting exactly 64 bytes past the
#: flag. Type 0 carries 16 bytes and nothing recognisable.
#:
#: The rule was found by separating the two populations rather than by
#: averaging them: across every block in the corpus that sets the flag *and
#: parses*, the type word is 1 in all 27; in the meshes that would not parse it
#: is 0 in every one. A single width cannot be right for both, and picking
#: either alone breaks the other set.
#: Type 0 is a sphere: a centre and a radius. Type 1 is a box: a centre, a 3x3
#: axis matrix and an extents triple.
#:
#: **Confirmed against an independent implementation on 28 July 2026.**
#: ``Greatness7/tes3`` (MIT) declares ``BoundType`` as ``Sphere = 0, Box = 1``,
#: with ``NiBound`` as centre plus radius (16 bytes) and ``NiBoxBV`` as centre,
#: 3x3 axis and extents (60 bytes). Both widths and both type numbers match
#: what was derived here from bytes alone. See ``NIF_PROVENANCE.md``.
_BOUNDING_BOX_TAILS: Final[dict[int, int]] = {0: 16, 1: 12 + 36 + 12}

#: The union type, which is not a width at all -- it is a count followed by
#: that many bounding volumes, each with its own type word. That is why it
#: cannot live in the table above and needs :func:`_read_bounding_volume`.
_BOUND_UNION: Final[int] = 4

#: A ceiling on how deep unions may nest. The format permits a union to contain
#: unions, so a corrupt or hostile file could otherwise recurse until the
#: interpreter's stack gives out. Real files nest one level at most.
_MAX_BOUND_DEPTH: Final[int] = 8


class NifParseError(Exception):
    """Raised when a file is not a readable Morrowind NIF."""


class NifMalformedError(NifParseError):
    """The file itself is malformed, as opposed to a gap in this reader.

    Raised by the data-plausibility guards -- an implausible array count, a
    bounding volume nested absurdly deep -- which fire on a value that no valid
    Morrowind NIF holds. It is a subclass of :class:`NifParseError`, so callers
    that only care whether reading stopped need not distinguish it; the point of
    the separate type is that a survey can tell "this mod's mesh is broken" (even
    Greatness7's own reader refuses it) from "a block layout here is wrong",
    which are opposite conclusions that ``stopped_reason`` alone conflates.
    """


def _read_bounding_volume(cursor: _Cursor, name: str, depth: int = 0) -> None:
    """Read one bounding volume, which may contain others.

    A volume is a type word followed by data whose shape that word decides. Two
    of the types are fixed widths and live in :data:`_BOUNDING_BOX_TAILS`. The
    union type is different in kind: it holds a *count* and then that many
    further volumes, each with its own type word, so it cannot be expressed as
    a width and is read here instead.

    Args:
        cursor: Positioned at the type word.
        name: The field name, for error messages.
        depth: How many unions deep this call already is.

    Raises:
        NifParseError: If the type is not one this reader knows, or unions nest
            past :data:`_MAX_BOUND_DEPTH`.
    """
    (bound_type,) = cursor.unpack("<I", f"{name} type")
    kind = int(bound_type)
    if kind == _BOUND_UNION:
        if depth >= _MAX_BOUND_DEPTH:
            raise NifMalformedError(
                f"{name}: bounding volumes nested more than {_MAX_BOUND_DEPTH} deep"
            )
        count = cursor.count(f"{name} union count")
        for _ in range(count):
            _read_bounding_volume(cursor, name, depth + 1)
        return
    tail = _BOUNDING_BOX_TAILS.get(kind)
    if tail is None:
        # Refused rather than guessed. An unrecognised type means an unknown
        # width, and a wrong width here does not fail here -- it desynchronises
        # the rest of the file and surfaces much later as something that looks
        # like a different bug entirely.
        #
        # Capsule (2), Lozenge (3) and Halfspace (5) are named by the format
        # and are refused deliberately: no file in either corpus has ever
        # carried one, so there is nothing to derive a width from, and the
        # independent implementation checked against declines them too.
        raise NifParseError(f"{name}: unknown bounding box type {bound_type}")
    cursor.take(tail, name)


@dataclass(frozen=True, slots=True)
class Block:
    """One parsed block.

    Attributes:
        index: Position in the file's block list, which is what links point at.
        type_name: The type string written before the block.
        fields: Field name to value, in the layout's order.
        offset: Where the block's body starts in the file.
        size: How many bytes the layout consumed. Recorded because a layout
            that is wrong by a few bytes is diagnosed by comparing this against
            where the *next* type string actually begins, and that comparison
            is impossible after the fact without it.
        raw: The block's body bytes verbatim, populated only when the file was
            read with ``retain=True`` (for :func:`write_nif`); empty otherwise,
            so the default scan pays nothing to keep them.
    """

    index: int
    type_name: str
    fields: dict[str, Any]
    offset: int = 0
    size: int = 0
    raw: bytes = b""

    def link(self, name: str) -> int:
        """Read a link field, normalising "absent" to ``-1``.

        Args:
            name: The field name.

        Returns:
            The target block index, or ``-1``.
        """
        value = self.fields.get(name, -1)
        return value if isinstance(value, int) else -1


@dataclass(frozen=True, slots=True)
class NifFile:
    """What could be read out of one file.

    Attributes:
        version: The version word from the header.
        block_count: How many blocks the header declares.
        blocks: The blocks actually parsed, in file order.
        stopped_at: The type string that could not be parsed, or ``None`` when
            the whole file was read.
        stopped_reason: Why reading stopped, for a report to quote verbatim.
        stopped_unknown: ``True`` when the block's *type* was not in the layout
            table, ``False`` when a known type failed to parse. Two very
            different findings that look identical from ``stopped_at`` alone: a
            missing type is a gap to fill, a known type that failed is a bug in
            its layout. Conflating them in a survey once sent a real
            investigation after the wrong thing.
        stopped_malformed: ``True`` when reading stopped on a data-plausibility
            guard (:class:`NifMalformedError`) -- the file is broken, not the
            layout. A third distinct finding: not a gap to fill and not a bug to
            fix, but a mesh even the reference reader refuses.
        header: The file's preamble bytes -- the version line through the block
            count -- kept only when read with ``retain=True``, so that
            :func:`write_nif` can reproduce the header exactly.
        footer: The bytes after the last block -- the root-object list -- kept on
            the same ``retain=True`` terms. The block walk stops at the declared
            block count and never reads these, so :func:`write_nif` must carry
            them across itself.
    """

    version: int
    block_count: int
    blocks: list[Block] = field(default_factory=list)
    stopped_at: str | None = None
    stopped_reason: str = ""
    stopped_unknown: bool = False
    stopped_malformed: bool = False
    header: bytes = b""
    footer: bytes = b""

    @property
    def complete(self) -> bool:
        """Whether every declared block was parsed."""
        return self.stopped_at is None and len(self.blocks) == self.block_count


class _Cursor:
    """A bounds-checked read head over one file's bytes.

    Every accessor raises :class:`NifParseError` rather than returning short
    data. That is the difference between a truncated file being reported as
    truncated and it being reported as a mesh with no triangles.
    """

    def __init__(self, data: bytes) -> None:
        """Wrap a buffer.

        Args:
            data: The whole file.
        """
        self.data = data
        self.pos = 0

    def take(self, count: int, what: str) -> bytes:
        """Consume a fixed number of bytes.

        Args:
            count: How many.
            what: What is being read, for the error message.

        Returns:
            The bytes.

        Raises:
            NifParseError: If the buffer does not hold that many.
        """
        if count < 0 or self.pos + count > len(self.data):
            raise NifParseError(
                f"{what}: wanted {count} byte(s) at offset {self.pos}, "
                f"file holds {len(self.data) - self.pos}"
            )
        chunk = self.data[self.pos : self.pos + count]
        self.pos += count
        return chunk

    def unpack(self, fmt: str, what: str) -> tuple[Any, ...]:
        """Consume and decode a struct.

        Args:
            fmt: A little-endian struct format.
            what: What is being read, for the error message.

        Returns:
            The decoded values.

        Raises:
            NifParseError: If the buffer is too short.
        """
        return struct.unpack(fmt, self.take(struct.calcsize(fmt), what))

    def count(self, what: str) -> int:
        """Read a length field and refuse an implausible one.

        Args:
            what: What is being counted, for the error message.

        Returns:
            The count.

        Raises:
            NifMalformedError: If the value exceeds :data:`_MAX_COUNT`, which no
                valid Morrowind NIF produces -- the file is broken, not the
                layout.
        """
        (value,) = self.unpack("<I", what)
        if value > _MAX_COUNT:
            raise NifMalformedError(f"{what}: implausible count {value} at offset {self.pos - 4}")
        return int(value)

    def string(self, what: str) -> str:
        """Read a length-prefixed, unterminated byte string.

        Args:
            what: What is being read, for the error message.

        Returns:
            The text. Decoded as cp1252 with replacement: these are Windows-era
            asset paths, and a stray byte must not lose the rest of the name.
        """
        length = self.count(f"{what} length")
        return self.take(length, what).decode("cp1252", errors="replace")


#: What a NIF type name can look like: ASCII, identifier-shaped, and bounded.
#: Anything else read where a type name belongs is not an unknown block, it is
#: a desynchronised cursor, and the two need different responses.
_TYPE_NAME: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,63}")


def _printable(text: str, limit: int = 32) -> str:
    """Render possibly-binary text safely for a message.

    A desynchronised read produces arbitrary bytes, and interpolating those
    into an error string puts raw binary through the logs and the terminal.
    This has happened: a stop reason in a survey report carried an embedded
    NUL and a run of high bytes straight to stdout.

    Args:
        text: The decoded bytes, which may be anything at all.
        limit: How many characters to keep.

    Returns:
        A quoted, escaped, length-bounded rendering.
    """
    clipped = text[:limit]
    escaped = clipped.encode("unicode_escape").decode("ascii")
    suffix = "..." if len(text) > limit else ""
    return f'"{escaped}{suffix}" ({len(text)} chars)'


def read_nif_bytes(data: bytes, *, geometry: bool = False, retain: bool = False) -> NifFile:
    """Parse a NIF from memory.

    Args:
        data: The whole file.
        geometry: Keep vertices, triangles, UVs and link lists as well as their
            counts. Off by default: a scan over a mod folder reads tens of
            thousands of meshes and needs none of it, while one mesh in a
            viewer needs all of it.
        retain: Keep each block's body bytes and the file header, so
            :func:`write_nif` can reproduce the file exactly. Off by default,
            for the same reason as ``geometry``: the scan does not need them.

    Returns:
        What could be read, including where it stopped.

    Raises:
        NifParseError: If the header is not a Morrowind NIF header. A bad
            *header* is fatal because nothing after it can be trusted; a bad
            *block* is recorded and reading stops there.
    """
    cursor = _Cursor(data)
    newline = data.find(b"\n")
    if not data.startswith(_HEADER_PREFIX) or newline < 0:
        raise NifParseError("not a NIF file: missing the NetImmerse header line")
    cursor.pos = newline + 1
    (version,) = cursor.unpack("<I", "version")
    if version not in ACCEPTED_VERSIONS:
        accepted = ", ".join(f"{known:#010x}" for known in sorted(ACCEPTED_VERSIONS))
        raise NifParseError(
            f"NIF version {version:#010x} is not one Morrowind ships ({accepted}); "
            f"this reader is deliberately limited to the versions the game loads"
        )
    block_count = cursor.count("block count")
    header = data[: cursor.pos] if retain else b""

    blocks: list[Block] = []
    for index in range(block_count):
        try:
            type_name = cursor.string(f"block {index} type")
        except NifMalformedError as exc:
            return NifFile(version, block_count, blocks, "", str(exc), stopped_malformed=True)
        except NifParseError as exc:
            return NifFile(version, block_count, blocks, "", str(exc))
        if not _TYPE_NAME.fullmatch(type_name):
            # Not a type name at all. The length prefix that produced it was
            # read at the wrong offset, which means alignment was lost inside
            # an *earlier* block -- a layout bug here, not a missing type
            # there. Reporting it as "unknown type" blamed the wrong thing and
            # inflated the missing-type count with files that are really
            # layout failures.
            return NifFile(
                version,
                block_count,
                blocks,
                "",
                f"lost alignment before block {index}: expected a type name, "
                f"read {_printable(type_name)}. A field width earlier in the "
                f"file is wrong.",
            )
        layout = block_layout(type_name)
        if layout is None:
            return NifFile(
                version,
                block_count,
                blocks,
                type_name,
                f"block {index} is a {type_name}, which this reader does not know. "
                f"NIF blocks carry no length, so there is nothing to skip by.",
                stopped_unknown=True,
            )
        start = cursor.pos
        try:
            fields = _read_block(cursor, layout, geometry=geometry)
        except NifMalformedError as exc:
            return NifFile(
                version, block_count, blocks, type_name, str(exc), stopped_malformed=True
            )
        except NifParseError as exc:
            return NifFile(version, block_count, blocks, type_name, str(exc))
        raw = data[start : cursor.pos] if retain else b""
        blocks.append(Block(index, type_name, fields, start, cursor.pos - start, raw))
    footer = data[cursor.pos :] if retain else b""
    return NifFile(version, block_count, blocks, header=header, footer=footer)


def read_nif(path: str | Path, *, geometry: bool = False, retain: bool = False) -> NifFile:
    """Parse a NIF from disk.

    Args:
        path: The file to read.
        geometry: Keep the bulk data as well as its counts; see
            :func:`read_nif_bytes`.
        retain: Keep the bytes :func:`write_nif` needs; see
            :func:`read_nif_bytes`.

    Returns:
        What could be read.

    Raises:
        NifParseError: If the file cannot be read or is not a Morrowind NIF.
    """
    from pathlib import Path as _Path

    try:
        data = _Path(path).read_bytes()
    except OSError as exc:
        raise NifParseError(f"cannot read {path}: {exc}") from exc
    return read_nif_bytes(data, geometry=geometry, retain=retain)


def write_nif(nif_file: NifFile) -> bytes:
    """Serialise a NIF back to bytes, the inverse of :func:`read_nif_bytes`.

    The reader is a structure walker: for the bulk arrays it keeps element
    *counts*, not the elements, so a field-by-field writer could not reproduce
    them. What it *can* keep, on request, is each block's body verbatim and the
    file header (``read_nif_bytes(..., retain=True)``); this reassembles those
    into the framing -- the header, then each block's type string followed by its
    body. The result is byte-for-byte identical to the input, which is the
    strongest statement of read/write parity available: every file the reader
    understands, it can write back unchanged, and a caller that replaces a
    block's :attr:`Block.raw` writes a modified file with every other block and
    all the framing intact.

    Args:
        nif_file: A file read with ``retain=True``. Its :attr:`NifFile.header`
            and every :attr:`Block.raw` must be populated.

    Returns:
        The encoded file bytes.

    Raises:
        NifParseError: If the file was not read with ``retain=True`` (its header
            is empty), so there is nothing to write back; or if reading stopped
            early, since a partial block list cannot be reassembled faithfully.
    """
    if nif_file.stopped_at is not None:
        raise NifParseError(
            f"write_nif cannot reassemble a file whose read stopped early "
            f"({nif_file.stopped_reason or 'incomplete'})"
        )
    if not nif_file.header:
        raise NifParseError("write_nif needs a file read with retain=True (its header is empty)")
    parts = [nif_file.header]
    for block in nif_file.blocks:
        name = block.type_name.encode("cp1252")
        parts.append(struct.pack("<I", len(name)))
        parts.append(name)
        parts.append(block.raw)
    parts.append(nif_file.footer)
    return b"".join(parts)


def field_spans(type_name: str, raw: bytes) -> dict[str, tuple[int, int]]:
    """Where each of a block's fields sits within its body bytes.

    Walks ``raw`` exactly as the reader would -- same layout, same field
    readers -- but records, instead of the values, each field's ``(offset,
    length)`` relative to the start of the block. That is what an editor needs
    to splice one field without re-encoding the block: a block carries no
    internal length, so the only way to find field *k* is to parse fields
    ``0..k`` and see where they land, which is precisely this walk.

    A gated-off field (its ``has_*`` flag was false, so it occupies no bytes)
    is recorded with a length of ``0`` rather than omitted, so a caller can
    tell "absent" from "missing name".

    Args:
        type_name: The block's type string, to look up its layout.
        raw: The block's body bytes, as :attr:`Block.raw` keeps them.

    Returns:
        Field name to ``(offset, length)`` within ``raw``.

    Raises:
        NifParseError: If ``type_name`` has no known layout, or ``raw`` is too
            short for it -- the same failure the reader would raise on the same
            bytes, not a silent partial answer.
    """
    layout = block_layout(type_name)
    if layout is None:
        raise NifParseError(f"no layout for block type {type_name!r}; cannot locate its fields")
    cursor = _Cursor(raw)
    seen: dict[str, Any] = {}
    spans: dict[str, tuple[int, int]] = {}
    for entry in layout:
        name, kind = entry[0], entry[1]
        gate = entry[2] if len(entry) > 2 else None
        start = cursor.pos
        seen[name] = _read_field(cursor, kind, name, seen, gate)
        spans[name] = (start, cursor.pos - start)
    return spans


def _read_block(
    cursor: _Cursor, layout: Sequence[Field], *, geometry: bool = False
) -> dict[str, Any]:
    """Read one block's fields in layout order.

    Args:
        cursor: The read head, positioned at the block's first byte.
        layout: The block's field list.
        geometry: Also decode and keep the bulk data, for a caller that needs
            coordinates rather than counts. See :func:`_decode_retained`.

    Returns:
        Field name to value.

    Raises:
        NifParseError: If a field runs past the end of the file.
    """
    fields: dict[str, Any] = {}
    for entry in layout:
        name, kind = entry[0], entry[1]
        gate = entry[2] if len(entry) > 2 else None
        # The span a field occupies is the only extra thing retention needs.
        # Recording it here rather than teaching every reader to return its
        # data keeps the default path *byte-identical* -- the scan runs over
        # tens of thousands of meshes and must not pay for a viewer feature.
        start = cursor.pos
        fields[name] = _read_field(cursor, kind, name, fields, gate)
        if geometry:
            extra = _decode_retained(kind, cursor.data, start, cursor.pos)
            if extra is not None:
                fields[f"{name}{_RETAINED_SUFFIX[kind]}"] = extra
    return fields


#: Suffix for the decoded companion of each retained kind. The count stays
#: under the original name, so nothing that already reads these blocks changes
#: behaviour when geometry is on -- a viewer asks for ``vertices_xyz`` while a
#: structure report goes on asking for ``vertices``.
_RETAINED_SUFFIX: Final[dict[str, str]] = {
    "vec3_array": "_xyz",
    "uv_array": "_uv",
    "triangle_array": "_indices",
    "ref_list": "_links",
    "vector3": "_xyz",
    "matrix33": "_m3",
    # Without this entry ``vertex_colors`` holds only the *count* the array
    # gate produced, and a caller that reasonably expects colours gets an
    # integer. Nothing announced that: the field existed, held a number, and
    # every length check against it quietly failed closed to "no colours".
    "color4_array": "_rgba",
}


def _decode_retained(kind: str, data: bytes, start: int, end: int) -> Any:  # noqa: ANN401
    """Decode the bytes a field just consumed, for the geometry path.

    Every kind here is decodable from its own span alone, which is what makes
    this a separate function rather than a change to each reader.

    Args:
        kind: The field kind.
        data: The whole file.
        start: Where the field began.
        end: Where it ended.

    Returns:
        The decoded value, or ``None`` when this kind carries nothing a viewer
        needs. An empty span -- an optional array whose flag was clear --
        returns an empty list rather than ``None``, because "present and empty"
        and "absent" are different answers.
    """
    if kind not in _RETAINED_SUFFIX:
        return None
    span = end - start
    if kind == "ref_list":
        # The span covers the u32 count and then the links themselves.
        count = (span - 4) // 4
        return list(struct.unpack_from(f"<{count}i", data, start + 4)) if count else []
    if kind == "vec3_array":
        return _triples(data, start, span // 12)
    if kind == "vector3":
        return _triples(data, start, 1)[0]
    if kind == "matrix33":
        return [list(row) for row in _triples(data, start, 3)]
    if kind == "uv_array":
        pairs = span // 8
        flat = struct.unpack_from(f"<{pairs * 2}f", data, start) if pairs else ()
        return [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]
    if kind == "color4_array":
        # Four floats per vertex, 0-1 rather than 0-255. Confirmed against
        # tes3, which types the same field ``Vec<ColorA>`` with ``ColorA`` an
        # alias for a four-component float vector.
        colours = span // 16
        flat = struct.unpack_from(f"<{colours * 4}f", data, start) if colours else ()
        return [tuple(flat[i : i + 4]) for i in range(0, len(flat), 4)]
    triangles = span // 6
    flat = struct.unpack_from(f"<{triangles * 3}H", data, start) if triangles else ()
    return [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat), 3)]


def _triples(data: bytes, start: int, count: int) -> list[tuple[float, float, float]]:
    """Decode a run of 3-float vectors.

    Args:
        data: The whole file.
        start: Where the run begins.
        count: How many vectors.

    Returns:
        The vectors.
    """
    if not count:
        return []
    flat = struct.unpack_from(f"<{count * 3}f", data, start)
    return [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat), 3)]


def _read_field(
    cursor: _Cursor, kind: str, name: str, seen: dict[str, Any], gate: str | None = None
) -> Any:  # noqa: ANN401
    """Read one field.

    Args:
        cursor: The read head.
        kind: The field kind from the layout.
        name: The field name, for error messages.
        seen: Fields already read from this block, which the length- and
            flag-dependent kinds consult.
        gate: The boolean field that decides whether this one is present at
            all. ``None`` for unconditional fields.

    Returns:
        The value. Bulk arrays return their element *count* rather than the
        elements: a structure report needs "how many vertices", never the
        vertices, and materialising a 60,000-float list per shape to discard it
        is the difference between scanning a mod folder and waiting for one.

    Raises:
        NifParseError: If the field runs past the end of the file.
    """
    width = FIXED_WIDTHS.get(kind)
    if width is not None:
        return _read_fixed(cursor, kind, name, width)
    if kind.startswith("skip:"):
        # A run of known length whose fields have not been identified. Naming
        # the bytes we cannot read would be inventing a layout; skipping a
        # measured span is honest and keeps the block's width correct, which
        # is the only thing the rest of the file depends on.
        span = int(kind.partition(":")[2])
        cursor.take(span, name)
        return span
    if kind == "string":
        return cursor.string(name)
    if kind == "ref_list":
        count = cursor.count(f"{name} count")
        cursor.take(count * 4, name)
        return count
    return _read_compound(cursor, kind, name, seen, gate)


def _read_fixed(cursor: _Cursor, kind: str, name: str, width: int) -> Any:  # noqa: ANN401
    """Read a field with a known width.

    Args:
        cursor: The read head.
        kind: The field kind.
        name: The field name, for error messages.
        width: The field's byte width.

    Returns:
        An int for the integer kinds, a float for ``f32``, a bool for
        ``bool32``, and the raw bytes for the fixed float runs -- which nothing
        in a structure report reads, so decoding them would be wasted work.
    """
    raw = cursor.take(width, name)
    if kind in {"u8", "u16", "u32", "i32", "link", "bool32"}:
        fmt = {"u8": "<B", "u16": "<H", "u32": "<I", "i32": "<i", "link": "<i", "bool32": "<I"}[
            kind
        ]
        (value,) = struct.unpack(fmt, raw)
        return bool(value) if kind == "bool32" else int(value)
    if kind == "f32":
        (value,) = struct.unpack("<f", raw)
        return float(value)
    return raw


def _read_compound(
    cursor: _Cursor, kind: str, name: str, seen: dict[str, Any], gate: str | None
) -> Any:  # noqa: ANN401
    """Read a field whose size depends on something already read.

    Args:
        cursor: The read head.
        kind: The field kind.
        name: The field name, for error messages.
        seen: Fields already read from this block.
        gate: The boolean field deciding whether this one is present.

    Returns:
        A count, a list of strings, or ``None`` for a field that was absent.

    Raises:
        NifParseError: If the kind is unknown, which is a bug in the layout
            table rather than a problem with the file, or if the field runs
            past the end.
    """
    vertices = int(seen.get("num_vertices", 0) or 0)
    if kind == "bounding_box":
        # Present only when the flag immediately before it says so, which is
        # true of almost no mesh -- 2 of 80,197 in one mod collection, and none
        # at all in vanilla. That rarity is why this was wrong for so long
        # without a single file noticing: the branch was essentially dead.
        #
        # The width depends on the type word that follows the flag; see
        # :data:`_BOUNDING_BOX_TAILS`.
        if not seen.get("has_bounding_box"):
            return None
        _read_bounding_volume(cursor, name)
        return True
    if kind == "vec3_array":
        return _optional_run(cursor, name, seen, gate, vertices * 12)
    if kind == "color4_array":
        return _optional_run(cursor, name, seen, gate, vertices * 16)
    if kind == "strip_array":
        # A strip count, one 16-bit length per strip, then that many indices
        # *in total*. The final run's size is the sum of the lengths just
        # read, which is not a number stored anywhere in the file -- so this
        # cannot be expressed as a gated run and needs its own branch.
        strips = int(cursor.unpack("<H", f"{name} strip count")[0])
        if strips > _MAX_COUNT:  # pragma: no branch - a u16 strip count maxes at 65535,
            # which is far below _MAX_COUNT (16M), so this cap can never trip.
            raise NifParseError(f"{name}: implausible strip count {strips}")  # pragma: no cover
        lengths = cursor.unpack(f"<{strips}H", f"{name} strip lengths") if strips else ()
        cursor.take(sum(lengths) * 2, name)
        return strips
    if kind == "lod_level_array":
        levels = cursor.count(f"{name} count")
        cursor.take(levels * 8, name)
        return levels
    if kind == "mipmap_array":
        levels = int(seen.get("num_mipmaps", 0) or 0)
        cursor.take(max(0, levels) * 12, name)
        return levels
    if kind == "byte_run":
        span = cursor.count(f"{name} length")
        cursor.take(span, name)
        return span
    if kind == "quat_array":
        return _optional_run(cursor, name, seen, gate, vertices * 16)
    if kind == "opt_float_array":
        return _optional_run(cursor, name, seen, gate, vertices * 4)
    if kind == "uv_array":
        sets = int(seen.get("num_uv_sets", 0) or 0)
        return _optional_run(cursor, name, seen, gate, sets * vertices * 8)
    if kind == "triangle_array":
        count = int(seen.get("num_triangles", 0) or 0)
        cursor.take(count * 6, name)
        return count
    if kind == "match_group_array":
        groups = cursor.unpack("<H", f"{name} count")[0]
        for _ in range(int(groups)):
            members = cursor.unpack("<H", f"{name} member count")[0]
            cursor.take(int(members) * 2, name)
        return int(groups)
    if kind == "float_array":
        cursor.take(vertices * 4, name)
        return vertices
    if kind == "text_key_array":
        count = cursor.count(f"{name} count")
        return [(cursor.unpack("<f", name)[0], cursor.string(name)) for _ in range(count)]
    if kind == "keyframe_data":
        return _read_keyframe_data(cursor)
    if kind == "vis_key_array":
        count = cursor.count(f"{name} count")
        cursor.take(count * 5, name)  # float time + byte visibility
        return count
    if kind == "float_key_group":
        return _read_key_group(cursor, name, _FLOAT_KEY_WIDTHS)
    if kind == "color_key_group":
        return _read_key_group(cursor, name, _COLOR_KEY_WIDTHS)
    if kind == "vector_key_group":
        return _read_key_group(cursor, name, _VECTOR_KEY_WIDTHS)
    if kind == "particle_array":
        return _read_particles(cursor, seen)
    if kind == "morph_array":
        return _read_morphs(cursor, seen)
    if kind == "skin_bone_array":
        return _read_skin_bones(cursor, seen)
    if kind == "texture_slots":
        return _read_texture_slots(cursor, seen)
    if kind == "source_texture_body":
        return _read_source_texture_body(cursor, seen)
    if kind == "palette_array":
        return _read_palette(cursor, name)
    if kind == "connectivity_flags":
        return _read_connectivity_flags(cursor, name, seen)
    if kind == "skin_partition_array":
        return _read_skin_partitions(cursor, name)
    if kind == "sequence_array":
        return _read_sequences(cursor, name)
    # Every kind used by BLOCK_LAYOUTS has a branch above; this guards a layout
    # table naming a kind the reader does not model (a code bug), so it is dead.
    raise NifParseError(f"layout error: field {name!r} has unknown kind {kind!r}")  # pragma: no cover


def _optional_run(
    cursor: _Cursor, name: str, seen: dict[str, Any], gate: str | None, size: int
) -> int | None:
    """Skip a bulk array that is present only when its flag is set.

    Args:
        cursor: The read head.
        name: The field name, for error messages.
        seen: Fields already read.
        gate: The boolean field that decides whether the array is there.
        size: The array's size in bytes when present.

    Returns:
        The array's size in bytes when present, otherwise ``None``.

    Raises:
        NifParseError: If the layout gave no gate. A bulk array with no flag is
            a layout bug, and guessing one is how normals came to be read
            whenever vertices were present.
    """
    if gate is None:
        raise NifParseError(f"layout error: field {name!r} is optional but names no gate")
    if not seen.get(gate):
        return None
    cursor.take(size, name)
    return size


#: Bytes per animation key, by interpolation mode. Keys are ``(time, value)``
#: with the mode deciding what else rides along: quadratic carries forward and
#: backward tangents, TBC carries tension, bias and continuity.
_FLOAT_KEY_WIDTHS: Final[dict[int, int]] = {1: 8, 2: 16, 3: 20, 5: 8}
_VECTOR_KEY_WIDTHS: Final[dict[int, int]] = {1: 16, 2: 40, 3: 28, 5: 16}
_QUAT_KEY_WIDTHS: Final[dict[int, int]] = {1: 20, 2: 20, 3: 32, 5: 20}

#: Bytes per color key, by the same rule as the tables above applied to a
#: four-float color: linear is time plus value, quadratic adds forward and
#: backward tangents, TBC adds three floats. Only mode 1 appears anywhere in
#: the corpus -- every NiColorData fixture is 8 bytes of head plus exactly 20
#: per key -- so the other three are the rule extended, not observations, and
#: an unknown mode still raises rather than guessing a width.
_COLOR_KEY_WIDTHS: Final[dict[int, int]] = {1: 20, 2: 52, 3: 32, 5: 20}


def _read_key_group(cursor: _Cursor, name: str, widths: dict[int, int]) -> int:
    """Read a count-then-interpolation-then-keys group.

    Args:
        cursor: The read head.
        name: The field name, for error messages.
        widths: Bytes per key, by interpolation mode.

    Returns:
        The key count.

    Raises:
        NifParseError: If the interpolation mode is not one with a known key
            width, which would otherwise desynchronise everything after it.
    """
    count = cursor.count(f"{name} key count")
    if not count:
        # The interpolation word is only written when there are keys to
        # interpolate, which is the sort of detail that costs a whole file.
        return 0
    (mode,) = cursor.unpack("<I", f"{name} interpolation")
    width = widths.get(int(mode))
    if width is None:
        raise NifParseError(f"{name}: unknown interpolation mode {mode}")
    cursor.take(count * width, name)
    return count


def _read_keyframe_data(cursor: _Cursor) -> dict[str, int]:
    """Read a ``NiKeyframeData`` body.

    Rotation is the awkward one: mode 4 means the rotation is stored as three
    separate float key groups (one per axis) rather than as quaternion keys, so
    the branch changes not just the key width but the shape of what follows.

    Args:
        cursor: The read head.

    Returns:
        Key counts for rotation, translation and scale.

    Raises:
        NifParseError: If a rotation mode has no known key width.
    """
    rotations = cursor.count("rotation key count")
    if rotations:
        (mode,) = cursor.unpack("<I", "rotation type")
        if int(mode) == 4:
            # A float sits between the rotation type and the three axis groups
            # on this NIF version. Omitting it read the float's bytes as the X
            # group's key count -- zero, so the group returned immediately --
            # and every group after that was one field out of step. Confirmed
            # against NifCorpus/.../Animated_RayTraicing2.NIF, where including
            # it lands the walk exactly on the next type string.
            cursor.unpack("<f", "xyz rotation unknown float")
            for axis in ("x", "y", "z"):
                _read_key_group(cursor, f"{axis} rotation", _FLOAT_KEY_WIDTHS)
        else:
            width = _QUAT_KEY_WIDTHS.get(int(mode))
            if width is None:
                raise NifParseError(f"rotation: unknown interpolation mode {mode}")
            cursor.take(rotations * width, "rotation keys")
    translations = _read_key_group(cursor, "translations", _VECTOR_KEY_WIDTHS)
    scales = _read_key_group(cursor, "scales", _FLOAT_KEY_WIDTHS)
    return {"rotation_keys": rotations, "translation_keys": translations, "scale_keys": scales}


#: The texture slots a Morrowind ``NiTexturingProperty`` can carry, in the order
#: they are written. Named because the base slot is the one a structure report
#: cares about -- "which texture does this mesh actually ask for".
_TEXTURE_SLOTS: tuple[str, ...] = (
    "base",
    "dark",
    "detail",
    "gloss",
    "glow",
    "bump",
    "decal_0",
)


#: Bytes each live particle occupies in a ``NiParticleSystemController``.
#: Established by reconciliation rather than assumption: across 51 fixtures
#: with declared counts of 150, 400, 600, 629 and 1000, every block body is
#: exactly 154 bytes of head plus ``count * 40``. Five distinct counts fitting
#: exactly leaves no room for the size to be a coincidence, and it also shows
#: there is nothing after the array -- a trailing field would offset every one.
_PARTICLE_RECORD: Final[int] = 40


def _read_particles(cursor: _Cursor, seen: dict[str, Any]) -> int:
    """Read the particle array of a ``NiParticleSystemController``.

    The array is sized by the *declared* count, not the live one. Both numbers
    are in the block -- the second is smaller in every observed file -- and
    using the live count would under-read every emitter that is not currently
    saturated, which is most of them.

    Args:
        cursor: The read head.
        seen: Fields already read, for the declared particle count.

    Returns:
        How many particle records were consumed.

    Raises:
        NifParseError: If the file ends inside the array.
    """
    declared = int(seen.get("num_particles", 0) or 0)
    count = max(0, declared)
    cursor.take(count * _PARTICLE_RECORD, "particles")
    return count


def _read_morphs(cursor: _Cursor, seen: dict[str, Any]) -> list[int]:
    """Read the morph targets of a ``NiMorphData``.

    Each target is an animation key group followed by a **complete** vertex
    set -- the mesh's whole geometry in that pose, not a sparse delta -- which
    is why these blocks are large and why the vertex count is declared once at
    the top rather than per target.

    **The key group here is not the generic one.** Every other key group in
    this format omits the interpolation word when the key count is zero;
    ``NiMorphData`` writes it regardless. That was not assumed: both readings
    were run against all 26 fixtures, and "always written" lands exactly on 26
    of them while "only when there are keys" lands on 3 -- the 3 being files
    where every target happens to have keys, so the two agree. Reusing
    :func:`_read_key_group` here would desynchronise any mesh with a keyless
    morph target, which is most of them.

    Args:
        cursor: The read head.
        seen: Fields already read, for the morph and vertex counts.

    Returns:
        The key count of each morph target, in order.

    Raises:
        NifParseError: If an interpolation mode is unknown or the file ends
            inside the table.
    """
    targets = int(seen.get("num_morphs", 0) or 0)
    vertices = int(seen.get("num_vertices", 0) or 0)
    counts: list[int] = []
    for index in range(max(0, targets)):
        keys = int(cursor.count(f"morph {index} key count"))
        (mode,) = cursor.unpack("<I", f"morph {index} interpolation")
        width = _FLOAT_KEY_WIDTHS.get(int(mode))
        if keys:
            if width is None:
                raise NifParseError(f"morph {index}: unknown interpolation mode {mode}")
            cursor.take(keys * width, f"morph {index} keys")
        cursor.take(vertices * 12, f"morph {index} vertices")
        counts.append(keys)
    return counts


#: Bytes of fixed data per skinned bone, before its per-vertex weights: a 3x3
#: rotation, a translation, a scale, a bounding-sphere centre and radius.
_SKIN_BONE_FIXED: Final[int] = 36 + 12 + 4 + 12 + 4

#: Bytes per vertex weight: a ``u16`` vertex index and an ``f32`` weight.
_SKIN_WEIGHT: Final[int] = 6


def _read_skin_bones(cursor: _Cursor, seen: dict[str, Any]) -> list[int]:
    """Read the per-bone weight table of a ``NiSkinData``.

    Each bone carries its own transform and bounding sphere, then a ``u16``
    count of the vertices it influences, then that many index-and-weight pairs.
    Only the counts are kept: a structure report answers "how much of this mesh
    does this bone move", never "by how much", so retaining several hundred
    weights per bone would be memory spent to no purpose.

    The shape was derived by reconciling block lengths rather than assumed. In
    three fixtures with 3, 2 and 6 bones the block bodies are 438, 1436 and
    1716 bytes; a fixed 60-byte head plus 70 bytes per bone leaves 168, 1236
    and 1236 bytes, each an exact multiple of the six-byte weight pair.

    Args:
        cursor: The read head.
        seen: Fields already read, for the bone count.

    Returns:
        The number of weighted vertices for each bone, in order.

    Raises:
        NifParseError: If the file ends inside the table.
    """
    bones = int(seen.get("bone_count", 0) or 0)
    counts: list[int] = []
    for index in range(max(0, bones)):
        cursor.take(_SKIN_BONE_FIXED, f"bone {index} transform")
        weighted = int(cursor.unpack("<H", f"bone {index} vertex count")[0])
        cursor.take(weighted * _SKIN_WEIGHT, f"bone {index} weights")
        counts.append(weighted)
    return counts


def _read_palette(cursor: _Cursor, name: str) -> int:
    """Read a ``NiPalette``'s colour table: a count, then that many RGBA quads.

    Args:
        cursor: The read head, at the palette count.
        name: The field name, for error messages.

    Returns:
        The number of colours.
    """
    count = cursor.count(f"{name} count")
    cursor.take(count * 4, name)
    return count


def _read_connectivity_flags(cursor: _Cursor, name: str, seen: dict[str, Any]) -> int:
    """Read a ``NiLinesData``'s connectivity flags: one byte per vertex.

    Sized by the vertices actually present: the reference reader counts
    ``len(vertices)``, which is ``num_vertices`` only when ``has_vertices`` was
    set and zero otherwise -- so a data block that declared a count but carried
    no vertex array consumes nothing here.

    Args:
        cursor: The read head.
        name: The field name, for error messages.
        seen: Fields already read, for ``num_vertices`` and ``has_vertices``.

    Returns:
        The number of flag bytes (one per present vertex).
    """
    vertices = max(0, int(seen.get("num_vertices", 0) or 0)) if seen.get("has_vertices") else 0
    cursor.take(vertices, name)
    return vertices


def _read_skin_partitions(cursor: _Cursor, name: str) -> int:
    """Read a ``NiSkinPartition``: a count, then that many partitions.

    Each partition is a five-``u16`` header giving the sizes of the arrays that
    follow -- bones, a vertex map, per-vertex weights, then either a triangle
    list or triangle strips -- and an optional bone palette. Only the partition
    count is kept; a structure report needs no more.

    Args:
        cursor: The read head, at the partition count.
        name: The field name, for error messages.

    Returns:
        The number of partitions.
    """
    count = cursor.count(f"{name} count")
    for index in range(count):
        num_vertices = int(cursor.unpack("<H", f"partition {index} vertices")[0])
        num_triangles = int(cursor.unpack("<H", f"partition {index} triangles")[0])
        num_bones = int(cursor.unpack("<H", f"partition {index} bones")[0])
        num_strip_lengths = int(cursor.unpack("<H", f"partition {index} strip lengths")[0])
        per_vertex = int(cursor.unpack("<H", f"partition {index} bones per vertex")[0])
        cursor.take(num_bones * 2, f"partition {index} bone list")
        cursor.take(num_vertices * 2, f"partition {index} vertex map")
        cursor.take(per_vertex * num_vertices * 4, f"partition {index} weights")
        if num_triangles:
            cursor.take(num_triangles * 6, f"partition {index} triangles")
        elif num_strip_lengths:
            lengths = cursor.unpack(f"<{num_strip_lengths}H", f"partition {index} strip lengths")
            cursor.take(sum(lengths) * 2, f"partition {index} strips")
        has_palette = int(cursor.unpack("<B", f"partition {index} has palette")[0])
        if has_palette:
            cursor.take(per_vertex * num_vertices, f"partition {index} bone palette")
    return count


def _read_sequences(cursor: _Cursor, name: str) -> int:
    """Read a ``NiKeyframeManager``'s sequences: a count, then that many.

    Each sequence is a name, then either an external keyframe filename or an
    inline int-and-link pair, then a count of ``(name, controller)`` pairs.

    Args:
        cursor: The read head, at the sequence count.
        name: The field name, for error messages.

    Returns:
        The number of sequences.
    """
    count = cursor.count(f"{name} count")
    for index in range(count):
        cursor.string(f"sequence {index} name")
        has_external = int(cursor.unpack("<B", f"sequence {index} has external kf")[0])
        if has_external:
            cursor.string(f"sequence {index} keyframe file")
        else:
            cursor.take(8, f"sequence {index} inline int and link")  # i32 + link
        pairs = cursor.count(f"sequence {index} pair count")
        for _ in range(pairs):
            cursor.string(f"sequence {index} pair name")
            cursor.take(4, f"sequence {index} pair controller")  # i32
    return count


def _read_texture_slots(cursor: _Cursor, seen: dict[str, Any]) -> dict[str, int]:
    """Read the texture slot table.

    Args:
        cursor: The read head.
        seen: Fields already read, for the declared slot count.

    Returns:
        Slot name to the ``NiSourceTexture`` block it links to. Absent slots are
        omitted rather than recorded as ``-1``, so a caller can ask "which slots
        does this use" by looking at the keys.
    """
    declared = int(seen.get("texture_count", 0) or 0)
    used: dict[str, int] = {}
    for index in range(max(0, declared)):
        slot = _slot_name(index)
        has_slot = cursor.unpack("<I", f"has {slot} texture")[0]
        if not has_slot:
            continue
        (link,) = cursor.unpack("<i", f"{slot} texture source")
        # clamp, filter, uv set, then the two PS2 fields and one more short.
        cursor.take(4 * 3 + 2 * 3, f"{slot} texture descriptor")
        if slot == "bump":
            # The bump slot carries a luma scale and offset plus a 2x2 matrix.
            #
            # Confirmed against the Morrowind NIF Notes' field walk of
            # NiTexturingProperty, which lists exactly these after the bump
            # descriptor: Luma Scale, Luma Offset, then Matrix 00-11. Two
            # floats and four is the 24 bytes taken here, and the field walk
            # also confirms the *slot order* above -- base, dark, detail,
            # gloss, glow, bump, then decals from index 6.
            cursor.take(4 * 2 + 4 * 4, "bump texture extras")
        used[slot] = int(link)
    return used


def _slot_name(index: int) -> str:
    """Name a texture slot by its position in the table.

    ``texture_count`` is a *slot* count, not a cap of seven. Meshes using more
    than one decal declare more slots, and the extra ones are further decals
    written in the same 26-byte shape with no count or padding between them.

    That was found by arithmetic rather than by assumption. On
    ``7decals.NIF`` the count is 13, the reader stopped 156 bytes short of the
    next block, and 156 is exactly six more slots -- so the seven named slots
    plus six unnamed ones account for the block with nothing left over.
    Capping the loop at ``len(_TEXTURE_SLOTS)`` silently truncated the block
    and desynchronised every file with a second decal.

    Args:
        index: The slot's position, from zero.

    Returns:
        The slot's name. Positions past the named table continue the decal
        numbering, so slot 7 is ``decal_1``.
    """
    if index < len(_TEXTURE_SLOTS):
        return _TEXTURE_SLOTS[index]
    return f"decal_{index - len(_TEXTURE_SLOTS) + 1}"


def _read_source_texture_body(cursor: _Cursor, seen: dict[str, Any]) -> str:
    """Read the half of ``NiSourceTexture`` that depends on ``use_external``.

    Args:
        cursor: The read head.
        seen: Fields already read.

    Returns:
        The referenced filename for an external texture, or ``""`` when the
        pixels are embedded in the file.
    """
    if seen.get("use_external"):
        return cursor.string("texture file name")
    cursor.take(1, "internal texture flag")
    cursor.unpack("<i", "internal pixel data")
    return ""
