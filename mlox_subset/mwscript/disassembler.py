"""Disassemble Morrowind compiled script bytecode.

The instruction stream is a sequence of little-endian ``uint16`` opcodes, most
of which take operands whose shapes are described by the parameter flags in
:mod:`~mlox_subset.mwscript.opcodes`. Expressions (the ``x == 1`` in an ``if``)
are *not* opcodes: Bethesda's compiler stores them as semi-textual data
embedded in the stream. A purely table-driven walker therefore cannot decode a
whole script, and one that pretends otherwise desynchronises and emits
nonsense.

This implementation is explicit about that boundary. It decodes what the table
covers, emits everything else as labelled raw spans, and resynchronises on the
next byte offset that plausibly begins a known instruction.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Final

from mlox_subset.mwscript.opcodes import FUNCTIONS, INTERNAL

#: Parameter flag bits, from MWEdit's ``script_defs.h``.
FLAG_BYTE: Final = 0x00000001
FLAG_SHORT: Final = 0x00000002
FLAG_LONG: Final = 0x00000004
FLAG_FLOAT: Final = 0x00000008
FLAG_STRING: Final = 0x00000010
FLAG_ID: Final = 0x00000020
FLAG_OPTIONAL: Final = 0x00000800
FLAG_MANY: Final = 0x00008000

#: Width in bytes of each fixed-size numeric operand.
_FIXED_WIDTHS: Final[dict[int, tuple[int, str]]] = {
    FLAG_BYTE: (1, "<b"),
    FLAG_SHORT: (2, "<h"),
    FLAG_LONG: (4, "<i"),
    FLAG_FLOAT: (4, "<f"),
}

#: Smallest and largest opcode in the table, used to reject noise quickly.
_MIN_OPCODE: Final = min(FUNCTIONS)
_MAX_OPCODE: Final = max(FUNCTIONS)


@dataclass(frozen=True, slots=True)
class Instruction:
    """One decoded instruction.

    Attributes:
        offset: Byte offset within the bytecode (after the length prefix).
        opcode: The 16-bit opcode.
        name: Function name from the opcode table.
        operands: Decoded operand values, in declaration order.
        size: Total encoded size in bytes, including the opcode.
    """

    offset: int
    opcode: int
    name: str
    operands: tuple[object, ...] = ()
    size: int = 2


@dataclass(frozen=True, slots=True)
class RawBytes:
    """A span the table could not account for.

    Most often an embedded expression. Reported verbatim rather than guessed
    at, so the reader can see exactly what was skipped.

    Attributes:
        offset: Byte offset within the bytecode.
        data: The raw bytes of the span.
    """

    offset: int
    data: bytes

    @property
    def size(self) -> int:
        """Length of the span in bytes."""
        return len(self.data)

    @property
    def text(self) -> str:
        """The span rendered as latin-1 text, unprintables as ``.``."""
        return "".join(chr(b) if 32 <= b < 127 else "." for b in self.data)


@dataclass(slots=True)
class Listing:
    """The result of disassembling one script.

    Attributes:
        declared_length: Length recorded in the 4-byte prefix, when present.
        items: Decoded instructions and raw spans, in stream order.
    """

    declared_length: int | None = None
    items: list[Instruction | RawBytes] = field(default_factory=list)

    @property
    def instructions(self) -> list[Instruction]:
        """Only the successfully decoded instructions."""
        return [item for item in self.items if isinstance(item, Instruction)]

    @property
    def decoded_ratio(self) -> float:
        """Fraction of bytes accounted for by decoded instructions.

        A useful confidence signal: a script that is mostly raw spans is
        either unusual or exposes a gap in the opcode table.
        """
        total = sum(item.size for item in self.items)
        if not total:
            return 0.0
        decoded = sum(i.size for i in self.items if isinstance(i, Instruction))
        return decoded / total


def _plausible_float(value: float) -> bool:
    """Whether ``value`` is a magnitude a real script would use.

    Expression bytes reinterpreted as an IEEE float produce wild magnitudes
    (``1.8e+22``). Game values are small, so an implausible one is strong
    evidence the bytes were never an instruction.

    Args:
        value: The decoded float.

    Returns:
        ``True`` if the value is finite and of a sane magnitude.
    """
    if not math.isfinite(value):
        return False
    return value == 0.0 or 1e-6 <= abs(value) <= 1e7


def _plausible_identifier(text: str) -> bool:
    """Whether ``text`` looks like a real object/spell/script identifier.

    Guards the length-prefixed string decode. If the byte taken as a length
    was actually something else, the "string" that follows runs into
    neighbouring expression data and comes out containing operators, quotes or
    control characters. Rejecting those keeps the disassembler honest: a raw
    span is a truthful "I don't know", a mangled operand is a lie.

    Args:
        text: Candidate identifier decoded from the stream.

    Returns:
        ``True`` if the text is consistent with a Morrowind identifier.
    """
    if not text:
        return False
    return all(ch.isalnum() or ch in "_- '." for ch in text)


def _read_operands(
    data: bytes, pos: int, params: tuple[int, ...]
) -> tuple[list[object], int] | None:
    """Decode one instruction's operands.

    Args:
        data: The bytecode.
        pos: Offset just past the opcode.
        params: Parameter flag words from the opcode table.

    Returns:
        ``(operands, new_pos)``, or ``None`` if the operands run past the end
        of the data or use an encoding this decoder does not handle.
    """
    operands: list[object] = []
    for flags in params:
        # An optional argument that has no bytes left simply was not supplied.
        # Stopping here is correct; failing the whole decode is not.
        if flags & FLAG_OPTIONAL and pos >= len(data):
            break
        for bit, (width, fmt) in _FIXED_WIDTHS.items():
            if flags & bit:
                if pos + width > len(data):
                    return None
                value = struct.unpack_from(fmt, data, pos)[0]
                if bit == FLAG_FLOAT and not _plausible_float(value):
                    # A wild magnitude means these bytes were not a float --
                    # i.e. this was not really an instruction. Refuse.
                    return None
                operands.append(value)
                pos += width
                break
        else:
            if flags & (FLAG_STRING | FLAG_ID):
                # Length-prefixed name: one byte of length, then the text.
                if pos >= len(data):
                    return None
                length = data[pos]
                if pos + 1 + length > len(data):
                    return None
                text = data[pos + 1 : pos + 1 + length].decode("latin-1")
                if not _plausible_identifier(text):
                    # The length byte was probably not a length -- refuse the
                    # decode rather than emit a mangled operand. The caller
                    # falls back to a raw span, which is honest.
                    return None
                operands.append(text)
                pos += 1 + length
            elif flags & FLAG_OPTIONAL:
                # Absent optional arguments simply stop the operand list.
                break
            else:
                # An encoding we do not model (expressions, variadics).
                return None
    return operands, pos


def disassemble(
    data: bytes,
    *,
    has_length_prefix: bool = True,
    source_text: str | None = None,
) -> Listing:
    """Disassemble compiled script bytecode.

    Args:
        data: Raw ``SCDT`` contents.
        has_length_prefix: Whether ``data`` begins with the 4-byte
            little-endian length field that ``SCDT`` carries. Verified present
            on every script in the reference corpus.
        source_text: The script's ``SCTX`` source, when available. Because
            expression data can coincidentally match an opcode value, passing
            the source restricts decoding to functions the script actually
            names -- which removes essentially all false positives. Both a
            ``SCPT`` record and the diff view have this to hand, so prefer
            passing it.

    Returns:
        A :class:`Listing` of instructions interleaved with the raw spans that
        could not be decoded.
    """
    allowed: set[str] | None = None
    if source_text is not None:
        lowered = source_text.lower()
        allowed = {
            name.lower()
            for _opcode, (name, _params) in FUNCTIONS.items()
            if name.lower() in lowered
        }

    listing = Listing()
    pos = 0
    if has_length_prefix and len(data) >= 4:
        listing.declared_length = struct.unpack_from("<I", data, 0)[0]
        pos = 4

    pending = bytearray()
    pending_at = pos

    def flush() -> None:
        """Emit any accumulated undecodable bytes as one span."""
        nonlocal pending, pending_at
        if pending:
            listing.items.append(RawBytes(pending_at, bytes(pending)))
            pending = bytearray()

    while pos < len(data):
        if pos + 2 > len(data):
            pending += data[pos:]
            pos = len(data)
            break

        opcode = struct.unpack_from("<H", data, pos)[0]
        entry = FUNCTIONS.get(opcode) if _MIN_OPCODE <= opcode <= _MAX_OPCODE else None
        if (
            entry is not None
            and allowed is not None
            and opcode not in INTERNAL  # internal opcodes never appear in source
            and entry[0].lower() not in allowed
        ):
            entry = None  # opcode value occurring inside expression data
        if entry is None:
            # Not a known instruction start: accumulate and resynchronise on
            # the next byte, rather than guessing a length and desyncing.
            if not pending:
                pending_at = pos
            pending.append(data[pos])
            pos += 1
            continue

        name, params = entry
        decoded = _read_operands(data, pos + 2, params)
        if decoded is None:
            if not pending:
                pending_at = pos
            pending.append(data[pos])
            pos += 1
            continue

        operands, new_pos = decoded
        flush()
        listing.items.append(
            Instruction(
                offset=pos,
                opcode=opcode,
                name=name,
                operands=tuple(operands),
                size=new_pos - pos,
            )
        )
        pos = new_pos

    flush()
    return listing


def format_listing(listing: Listing, *, width: int = 16) -> str:
    """Render a listing as human-readable text.

    Args:
        listing: The disassembly to render.
        width: Bytes per line when dumping raw spans.

    Returns:
        A multi-line string: one line per instruction, and an offset/hex/ASCII
        dump for each raw span.
    """
    lines: list[str] = []
    if listing.declared_length is not None:
        lines.append(f"; declared bytecode length: {listing.declared_length} bytes")
        lines.append(f"; decoded: {listing.decoded_ratio:.0%} of the stream")
    for item in listing.items:
        if isinstance(item, Instruction):
            args = ", ".join(
                f'"{value}"' if isinstance(value, str) else str(value) for value in item.operands
            )
            lines.append(f"{item.offset:04X}  {item.name}{f' {args}' if args else ''}")
        else:
            for start in range(0, len(item.data), width):
                chunk = item.data[start : start + width]
                ascii_text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                lines.append(
                    f"{item.offset + start:04X}  " f"{chunk.hex(' '):<{width * 3}}  |{ascii_text}|"
                )
    return "\n".join(lines)
