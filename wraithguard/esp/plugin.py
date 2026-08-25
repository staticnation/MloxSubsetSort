"""Read a whole plugin into records, and write records back to a plugin.

A plugin file is nothing more than its records, one after another, to the end of
the file -- there is no table of contents and no trailing index. So reading one
is a loop over :func:`~wraithguard.esp.record.read_record` until the bytes run
out, and writing one is a loop over
:func:`~wraithguard.esp.record.write_record`. The first record is always the
``TES3`` header (its masters and record count); the rest are the content.

Records whose tag has no class yet come back as
:class:`~wraithguard.esp.record.UnknownRecord` and are written unchanged, so a
plugin that mixes ported and not-yet-ported records still round-trips exactly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wraithguard.esp.io import EspError, Reader, Writer
from wraithguard.esp.record import read_record, write_record

if TYPE_CHECKING:
    from collections.abc import Iterable

    from wraithguard.esp.record import Record
    from wraithguard.esp.records import Header


def read_header(data: bytes) -> Header:
    """Read just the plugin header -- the first, ``TES3`` record -- from ``data``.

    Every plugin opens with its ``TES3`` header (format version, author,
    description, and master list), and it is the one record a caller often wants
    on its own: the description for an ``[VER]``/``[DESC]`` rule, the masters for
    a dependency check. Reading it does not require reading the file: only the
    first record is parsed, so ``data`` need only contain that record's bytes,
    and -- because the rest of the file is never touched -- a plugin whose later
    records ``tes3conv`` would refuse (OpenMW's ``LUAL``, say) still yields its
    header here.

    Args:
        data: The plugin's bytes, or at least a prefix holding the whole first
            record (its 16-byte header plus the body its size declares).

    Returns:
        The parsed :class:`~wraithguard.esp.records.Header`.

    Raises:
        EspError: If the bytes are too short, or the first record is not a
            ``TES3`` header.
    """
    from wraithguard.esp.records import Header  # deferred: keeps import order simple

    record = read_record(Reader(data))
    if not isinstance(record, Header):
        raise EspError(f"expected a TES3 header record, got {record.wire_tag!r}")
    return record


def read_plugin(data: bytes) -> list[Record]:
    """Read every record in ``data``, in file order.

    Args:
        data: The bytes of a plugin file.

    Returns:
        The records, ready to inspect or edit and hand back to
        :func:`write_plugin`.
    """
    reader = Reader(data)
    records: list[Record] = []
    while not reader.at_end:
        records.append(read_record(reader))
    return records


def write_plugin(records: Iterable[Record]) -> bytes:
    """Write ``records`` back to plugin bytes, in the order given.

    Args:
        records: The records to write.

    Returns:
        The plugin file's bytes.
    """
    writer = Writer()
    for record in records:
        write_record(writer, record)
    return writer.getvalue()
