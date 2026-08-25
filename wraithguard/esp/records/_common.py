"""Small helpers shared by the record ports, for the parts they all repeat.

Almost every record is a run of string subrecords with an optional fixed data
block, and every one ends the same way: a ``DELE`` subrecord marks a deletion,
optional strings are written only when non-empty, and an unrecognised tag is an
error naming the record. These are those repeated moves, one place, so each
record module reads as its own shape and not as boilerplate. They add nothing to
the format -- they are exactly what the crate's ``Load``/``Save`` do inline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.io import EspError

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer


class FixedData(Protocol):
    """A fixed-layout data block that can write itself (a ``WPDT``, ``AADT``, ...)."""

    def save(self, writer: Writer) -> None:
        """Write the block's fields in order."""


def read_dele(reader: Reader, flags: ObjectFlags) -> ObjectFlags:
    """Consume a ``DELE`` subrecord's body and return ``flags`` with DELETED set."""
    reader.skip(reader.u32())
    return flags | ObjectFlags.DELETED


def write_dele(writer: Writer, flags: ObjectFlags) -> None:
    """Write a ``DELE`` subrecord if ``flags`` is deleted, as the crate does."""
    if ObjectFlags.DELETED in flags:
        writer.tag(b"DELE")
        writer.u32(4)
        writer.u32(0)


def put_string(writer: Writer, tag: bytes, value: str) -> None:
    """Write a string subrecord unconditionally (for a required field like id)."""
    writer.tag(tag)
    writer.string(value)


def put_opt_string(writer: Writer, tag: bytes, value: str) -> None:
    """Write a string subrecord only when non-empty, as the crate omits empties."""
    if value:
        writer.tag(tag)
        writer.string(value)


def expect_size(reader: Reader, record_tag: str, sub_tag: str, expected: int) -> None:
    """Read a fixed data subrecord's size word and require it to be ``expected``.

    Mirrors the crate's ``stream.expect(<size>u32)`` before a fixed block: the
    size is part of the format and a wrong one means the file is malformed.
    """
    size = reader.u32()
    if size != expected:
        raise EspError(f"{record_tag}::{sub_tag} size {size}, expected {expected}")


def put_fixed(writer: Writer, tag: bytes, size: int, data: FixedData) -> None:
    """Write a fixed data subrecord: its tag, its size word, then the block."""
    writer.tag(tag)
    writer.u32(size)
    data.save(writer)


def put_fixed_string(writer: Writer, tag: bytes, value: str, size: int) -> None:
    """Write a subrecord holding a fixed-width string (the crate's ``FixedString``).

    The size word is the field's fixed width, and the string is written in exactly
    that many bytes, null-padded -- as list entries like a birthsign's spells are.
    Reading takes the length-prefixed string back and truncates at the padding's
    first null, so it round-trips.
    """
    writer.tag(tag)
    writer.u32(size)
    writer.fixed_string(value, size)


def unexpected(record_tag: str, tag: bytes) -> EspError:
    """The error the crate raises for a subrecord tag a record does not know."""
    return EspError(f"Unexpected Tag: {record_tag}::{tag!r}")
