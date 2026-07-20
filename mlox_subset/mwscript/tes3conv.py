"""Decode the ``bytecode`` field as tes3conv writes it into JSON.

tes3conv stores a script's compiled ``SCDT`` base64-encoded, and (in recent
versions) zstd-compressed underneath that. Zstd only entered the standard
library in Python 3.14, so decoding is attempted through whichever backend is
present and degrades to a clear message when none is.

The alternative -- reading ``SCDT`` straight out of the plugin -- needs no
compression support at all and is implemented in
:mod:`~mlox_subset.mwscript.script_record`. Prefer it when the plugin file is
to hand; this module exists for the conflict view, which has only the JSON.
"""

from __future__ import annotations

import base64
import binascii
from typing import Final

from mlox_subset.mwscript.disassembler import disassemble, format_listing

#: Leading bytes of a zstd frame (magic number 0xFD2FB528, little-endian).
ZSTD_MAGIC: Final = b"\x28\xb5\x2f\xfd"


class BytecodeDecodeError(Exception):
    """Raised when a ``bytecode`` field cannot be turned back into bytes."""


def _decompress_zstd(data: bytes) -> bytes:
    """Decompress a zstd frame using whatever backend is installed.

    A corrupt frame must not be allowed to look like an empty script. Every
    lenient zstd entry point (``stream_reader().read()``, ``decompressobj()``)
    returns ``b""`` for a malformed frame instead of raising, while the strict
    ``decompress()`` rejects *valid* streaming frames because they carry no
    content size in the header. Neither behaviour alone is usable here, so the
    lenient API is paired with an explicit emptiness check: a real ``SCDT``
    always contains at least its own length prefix, so nothing is a genuine
    empty result.

    Args:
        data: A complete zstd frame.

    Returns:
        The decompressed bytes.

    Raises:
        BytecodeDecodeError: If no zstd backend is available, or the frame is
            corrupt, truncated, or decompresses to nothing.
    """
    try:  # Python 3.14+ carries zstd in the standard library.
        from compression import zstd
    except ImportError:
        backend_error: type[Exception] | None = None
        decompress = None
    else:
        backend_error, decompress = zstd.ZstdError, zstd.decompress

    if decompress is None:
        try:  # The third-party binding, on older interpreters.
            import zstandard
        except ImportError as exc:
            raise BytecodeDecodeError(
                "this bytecode is zstd-compressed, which needs either Python "
                "3.14+ or the 'zstandard' package (pip install zstandard). "
                "Alternatively point the tool at the plugin file, which stores "
                "SCDT uncompressed."
            ) from exc
        backend_error = zstandard.ZstdError

        def decompress(frame: bytes) -> bytes:
            """Decompress via decompressobj, which accepts unsized frames."""
            return zstandard.ZstdDecompressor().decompressobj().decompress(frame)

    try:
        result = decompress(data)
    except backend_error as exc:  # type: ignore[misc]
        raise BytecodeDecodeError(f"corrupt zstd frame: {exc}") from exc

    if not result:
        raise BytecodeDecodeError(
            "zstd frame decompressed to nothing -- it is corrupt or truncated. "
            "Reporting this rather than showing an empty script, which would "
            "read as 'this plugin's script is blank'."
        )
    return result


def decode_bytecode_field(value: str | bytes) -> bytes:
    """Turn a tes3conv ``bytecode`` field into raw ``SCDT`` bytes.

    Handles both the compressed and uncompressed forms: the zstd frame is
    detected by its magic number rather than assumed, so output from either
    tes3conv generation decodes.

    Args:
        value: The field's value -- base64 text, or bytes already decoded.

    Returns:
        The raw ``SCDT`` payload, ready for
        :func:`~mlox_subset.mwscript.disassembler.disassemble`.

    Raises:
        BytecodeDecodeError: If the value is not valid base64, or is a zstd
            frame that cannot be decompressed here.
    """
    if isinstance(value, str):
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BytecodeDecodeError(f"not valid base64: {exc}") from exc
    else:
        raw = bytes(value)

    if raw.startswith(ZSTD_MAGIC):
        return _decompress_zstd(raw)
    return raw


def decode_variables_field(value: str | bytes) -> list[str]:
    """Decode a tes3conv ``variables`` field into local variable names.

    The field is the script's ``SCVR`` block under the same base64 (+zstd)
    wrapping as ``bytecode``, and it carries **a 4-byte little-endian length
    prefix** before the NUL-separated names. Skipping that prefix matters: its
    trailing bytes otherwise survive the split as a junk leading "name",
    yielding one variable too many. Verified against 120 real script records --
    with the prefix stripped, the body length equals both the prefix value and
    the header's ``variables_length``, and the name count equals
    ``num_shorts + num_longs + num_floats``, in every case.

    Args:
        value: The field's value, as tes3conv wrote it.

    Returns:
        The declared local variable names, in declaration order. Empty if the
        field holds no names.

    Raises:
        BytecodeDecodeError: If the value cannot be decoded to bytes.
    """
    raw = decode_bytecode_field(value)
    body = raw[4:] if len(raw) >= 4 else b""
    return [name.decode("latin-1", "replace") for name in body.split(b"\x00") if name]


def variables_text_for_field(value: str | bytes) -> str:
    """Render a ``variables`` field as one name per line, for the diff view.

    Never raises, for the same reason as :func:`listing_for_bytecode_field`: a
    field-detail window must not die on one malformed record.

    Args:
        value: The field's value, as tes3conv wrote it.

    Returns:
        The variable names one per line, or a ``;``-prefixed explanation.
    """
    try:
        names = decode_variables_field(value)
    except BytecodeDecodeError as exc:
        return f"; could not decode this variables field:\n;   {exc}"
    except Exception as exc:
        return f"; unexpected error decoding this variables field: {exc!r}"
    if not names:
        return "; (no local variables declared)"
    header = f"; {len(names)} local variable(s), in declaration order"
    return "\n".join([header, *names])


def listing_for_bytecode_field(value: str | bytes, source_text: str | None = None) -> str:
    """Render a tes3conv ``bytecode`` field as a readable disassembly.

    Diffing two versions of a compiled script is otherwise hopeless: the raw
    field is base64, so any change at all looks like a total rewrite. This
    turns it into named instructions that can be compared line by line.

    This function **never raises**. A diff view that dies on one malformed
    field is worse than one that explains the problem in place, so failures
    come back as a comment line in the listing itself.

    Args:
        value: The field's value, as tes3conv wrote it.
        source_text: The record's ``text`` field (the script's source), when
            available. Supplying it suppresses false positives: an opcode value
            that happens to occur inside embedded expression data is only
            decoded if the script really calls that function.

    Returns:
        The formatted listing, or a ``;``-prefixed explanation of why one could
        not be produced.
    """
    try:
        raw = decode_bytecode_field(value)
    except BytecodeDecodeError as exc:
        return f"; could not decode this bytecode field:\n;   {exc}"
    try:
        return format_listing(disassemble(raw, source_text=source_text))
    except Exception as exc:
        return f"; unexpected error disassembling this bytecode: {exc!r}"
