"""Read ``SCPT`` records straight out of a plugin file.

tes3conv stores the bytecode base64-encoded *and* zstd-compressed, which would
make the disassembler depend on a zstd library (only in the standard library
from Python 3.14). The plugin file itself stores ``SCDT`` uncompressed, so
reading it natively avoids the dependency entirely and works whether or not
tes3conv is installed.

Layout of a ``SCPT`` record:
    ``SCHD``  52-byte header: 32-byte name, then five ``uint32`` counts.
    ``SCVR``  NUL-separated local variable names.
    ``SCDT``  compiled bytecode.
    ``SCTX``  source text.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

#: Minimum bytes needed for a TES3 record header (tag, size, header1, flags).
_RECORD_HEADER_SIZE: Final = 16

#: Minimum bytes needed for a subrecord header (tag, size).
_SUBRECORD_HEADER_SIZE: Final = 8

#: Size of the fixed portion of a ``SCHD`` subrecord.
_SCHD_SIZE: Final = 52


@dataclass(slots=True)
class ScriptRecord:
    """One script from a plugin.

    Attributes:
        name: The script's identifier.
        num_shorts: Declared local ``short`` variables.
        num_longs: Declared local ``long`` variables.
        num_floats: Declared local ``float`` variables.
        bytecode: Raw ``SCDT`` contents, ready for
            :func:`~mlox_subset.mwscript.disassembler.disassemble`.
        variables: Local variable names, in declaration order.
        text: The script's source, when the record carries it.
    """

    name: str = ""
    num_shorts: int = 0
    num_longs: int = 0
    num_floats: int = 0
    bytecode: bytes = b""
    variables: list[str] = field(default_factory=list)
    text: str = ""


def _iter_records(data: bytes) -> Iterator[tuple[bytes, bytes]]:
    """Yield ``(tag, body)`` for each top-level record in ``data``."""
    pos = 0
    while pos + _RECORD_HEADER_SIZE <= len(data):
        tag = data[pos : pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        body_start = pos + _RECORD_HEADER_SIZE
        yield tag, data[body_start : body_start + size]
        # A zero-size record would loop forever; always advance the header.
        pos = body_start + size


def _iter_subrecords(body: bytes) -> Iterator[tuple[bytes, bytes]]:
    """Yield ``(tag, payload)`` for each subrecord in a record body."""
    pos = 0
    while pos + _SUBRECORD_HEADER_SIZE <= len(body):
        tag = body[pos : pos + 4]
        (size,) = struct.unpack_from("<I", body, pos + 4)
        start = pos + _SUBRECORD_HEADER_SIZE
        yield tag, body[start : start + size]
        pos = start + size


def _decode_name(raw: bytes) -> str:
    """Decode a NUL-padded latin-1 name field."""
    return raw.split(b"\x00", 1)[0].decode("latin-1", "replace").strip()


def read_script_records(path: Path | str) -> list[ScriptRecord]:
    """Read every script in a plugin.

    Malformed input is tolerated rather than raised on: plugins come from the
    internet, and a truncated or corrupt file should yield whatever parsed
    cleanly instead of failing the whole operation.

    Args:
        path: A ``.esp``/``.esm``/``.omwaddon`` file.

    Returns:
        Every ``SCPT`` record found. Empty if the file is unreadable or is not
        a TES3 plugin.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    if len(data) < _RECORD_HEADER_SIZE or data[:4] != b"TES3":
        return []

    scripts: list[ScriptRecord] = []
    for tag, body in _iter_records(data):
        if tag != b"SCPT":
            continue
        script = ScriptRecord()
        for sub_tag, payload in _iter_subrecords(body):
            if sub_tag == b"SCHD" and len(payload) >= _SCHD_SIZE:
                script.name = _decode_name(payload[:32])
                counts = struct.unpack_from("<5I", payload, 32)
                script.num_shorts, script.num_longs, script.num_floats = counts[:3]
            elif sub_tag == b"SCVR":
                script.variables = [
                    name.decode("latin-1", "replace") for name in payload.split(b"\x00") if name
                ]
            elif sub_tag == b"SCDT":
                script.bytecode = payload
            elif sub_tag == b"SCTX":
                script.text = payload.decode("latin-1", "replace")
        if script.name or script.bytecode:
            scripts.append(script)
    return scripts
