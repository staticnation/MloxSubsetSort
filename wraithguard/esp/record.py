"""The record framing that sits above the byte layer, and the record registry.

A plugin is a flat sequence of records; a record is a sixteen-byte header then a
body of subrecords. The header is four fields: the four-byte record ``tag``, a
``u32`` giving the body's size, a ``u32`` the format leaves as padding (always
written zero, as the crate does), and the record's :class:`ObjectFlags` as a
``u32``. The body that the size measures is the subrecords -- the flags word,
though it sits in the header slot, is written by the record itself, so the size
is exactly the subrecords and nothing else.

:func:`read_record` reads one header, hands the record a reader *bounded* to its
body so its subrecord loop stops at the record's end, and dispatches on the tag
to the class that knows that record. :func:`write_record` writes the header with
a placeholder size, lets the record write its subrecords, then fills the size in
now that it is known.

A tag with no class yet is not an error and not a loss: it becomes an
:class:`UnknownRecord` holding its bytes verbatim, so a plugin round-trips
whole while records are still being ported one at a time. This is the same
resilience :mod:`wraithguard.land.native` relies on -- a file is never refused
over a record it does not model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import ObjectFlags

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

#: Tag (four bytes) -> the record class that reads and writes it. Populated by
#: :func:`register`, which each record module applies to its class.
REGISTRY: dict[bytes, type[Record]] = {}


class Record(ABC):
    """One TES3 record: its object flags, and how it reads and writes its body.

    A subclass sets :attr:`TAG`, is (by convention) a dataclass of the record's
    fields, and implements :meth:`load`/:meth:`save` over the *subrecords* only
    -- the header, including the flags word, is the framing's job.
    """

    #: The record's four-byte tag, e.g. ``b"WEAP"``.
    TAG: ClassVar[bytes]

    #: The record's object flags, read from the header.
    flags: ObjectFlags

    @property
    def wire_tag(self) -> bytes:
        """The tag to write for this record (its class :attr:`TAG`)."""
        return self.TAG

    @classmethod
    @abstractmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Record:
        """Read this record's subrecords from ``reader`` (bounded to its body).

        Args:
            reader: A reader over exactly this record's body, positioned just
                after the flags word.
            flags: The object flags already read from the header.

        Returns:
            The parsed record.
        """

    @abstractmethod
    def save(self, writer: Writer) -> None:
        """Write this record's subrecords (not its header) to ``writer``."""


def register(cls: type[Record]) -> type[Record]:
    """Register a record class under its :attr:`~Record.TAG`. Use as a decorator."""
    REGISTRY[cls.TAG] = cls
    return cls


class UnknownRecord(Record):
    """A record whose tag has no class yet, kept as raw bytes so it round-trips.

    Its body (the subrecords after the flags word) is held verbatim and written
    back unchanged, so a plugin containing records not yet ported still reads and
    writes losslessly.
    """

    TAG = b"\x00\x00\x00\x00"

    def __init__(self, tag: bytes, flags: ObjectFlags, body: bytes) -> None:
        """Hold ``body`` (raw subrecord bytes) under ``tag`` with ``flags``."""
        self._tag = tag
        self.flags = flags
        self.body = body

    @property
    def wire_tag(self) -> bytes:
        """The original tag this record was read under."""
        return self._tag

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> UnknownRecord:
        """Not used -- unknown records are built directly by :func:`read_record`."""
        raise NotImplementedError

    def save(self, writer: Writer) -> None:
        """Write the held body bytes back verbatim."""
        writer.raw(self.body)

    def __repr__(self) -> str:
        """A short, tag-and-size summary."""
        return f"UnknownRecord({self._tag!r}, {len(self.body)} bytes)"


def read_record(reader: Reader) -> Record:
    """Read one record -- header then body -- from ``reader``.

    Args:
        reader: A reader positioned at a record header.

    Returns:
        The record: a registered class if the tag is known, else an
        :class:`UnknownRecord` holding its bytes.
    """
    tag = reader.tag()
    size = reader.u32()
    reader.u32()  # padding: the format's third header word, always zero
    body = reader.bound(4 + size)  # the flags word plus the subrecords
    flags = ObjectFlags(body.u32())
    cls = REGISTRY.get(tag)
    if cls is None:
        return UnknownRecord(tag, flags, body.raw(body.remaining))
    return cls.load(body, flags)


def write_record(writer: Writer, record: Record) -> None:
    """Write one record -- header then body -- to ``writer``.

    Args:
        writer: The destination.
        record: The record to write.
    """
    writer.tag(record.wire_tag)
    size_at = writer.mark()
    writer.u32(0)  # size placeholder, filled in below
    writer.u32(0)  # padding word
    body_at = writer.mark()
    writer.u32(int(record.flags))
    record.save(writer)
    writer.patch_u32(size_at, len(writer) - body_at - 4)  # less the flags word
