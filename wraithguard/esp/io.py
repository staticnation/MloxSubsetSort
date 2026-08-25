r"""The binary layer every record is built on: reading and writing the wire.

This is the Python of the `tes3` crate's ``bytes_io``. The rules it encodes are
the format's, and they are small:

* **Primitives** are little-endian, no padding: ``u8``/``i8`` .. ``u64``/``i64``,
  ``f32``/``f64``, each its own width.
* **Tags** are four raw bytes (``b"NAME"``), read and written unchanged.
* **Strings** are a ``u32`` length then that many Windows-1252 bytes.
  On the wire they are null-terminated: the length *includes* the terminator, so
  ``"NAME"`` is written as length 5 and ``NAME\0``. An empty string is length 1
  and a lone ``\0``. On read the bytes are decoded and truncated at the first
  ``\0`` -- the inverse. (A string that itself contains a ``\0`` is written with
  no added terminator and a length up to that byte, matching the crate.)
* **Sequences** (``Vec<T>``) are a ``u32`` count then that many items. Fixed
  arrays (``[T; N]``) are just ``N`` items, no count.

The record framing that sits on top -- the sixteen-byte record header and the
tag/size subrecords -- lives in :mod:`wraithguard.esp.records`, because it is
about records, not bytes. Here we only read and write values.

Both classes fail loudly. A read past the end, or a byte a Windows-1252 string
cannot hold, raises :class:`EspError` rather than returning a short or wrong
value -- the same stance the NIF reader and the native landscape reader take,
for the same reason: the files come from the internet, and a quiet wrong read
corrupts everything downstream of it.
"""

from __future__ import annotations

import struct
from typing import Final

#: The format's text encoding. Morrowind predates Unicode; every string in a
#: plugin is a byte in this code page.
_ENCODING: Final = "cp1252"

#: Pre-built little-endian struct codecs, by the name records ask for.
_LE: Final[dict[str, struct.Struct]] = {
    "i8": struct.Struct("<b"),
    "u8": struct.Struct("<B"),
    "i16": struct.Struct("<h"),
    "u16": struct.Struct("<H"),
    "i32": struct.Struct("<i"),
    "u32": struct.Struct("<I"),
    "i64": struct.Struct("<q"),
    "u64": struct.Struct("<Q"),
    "f32": struct.Struct("<f"),
    "f64": struct.Struct("<d"),
}


class EspError(Exception):
    """A plugin's bytes did not hold what the format requires."""


class Reader:
    """A cursor over a plugin's bytes, reading the format's values in order.

    A reader is positioned; each call advances it. :meth:`bound` hands out a
    reader over a fixed slice -- how a record is read without its subrecord loop
    running past its end into the next record.
    """

    __slots__ = ("data", "end", "pos")

    def __init__(self, data: bytes, pos: int = 0, end: int | None = None) -> None:
        """Wrap ``data`` from ``pos`` to ``end`` (default the whole buffer)."""
        self.data = data
        self.pos = pos
        self.end = len(data) if end is None else end

    @property
    def remaining(self) -> int:
        """Bytes left before the end of this reader's range."""
        return self.end - self.pos

    @property
    def at_end(self) -> bool:
        """Whether the cursor has reached the end of its range."""
        return self.pos >= self.end

    def _take(self, n: int) -> int:
        """Advance by ``n`` bytes, returning the start offset, or raise."""
        start = self.pos
        if start + n > self.end:
            raise EspError(f"read past end: wanted {n} at {start}, end {self.end}")
        self.pos = start + n
        return start

    def raw(self, n: int) -> bytes:
        """The next ``n`` bytes, verbatim."""
        start = self._take(n)
        return self.data[start : start + n]

    def _num(self, kind: str) -> int | float:
        """Read one little-endian number of ``kind``, advancing past it."""
        codec = _LE[kind]
        start = self._take(codec.size)
        return codec.unpack_from(self.data, start)[0]

    def i8(self) -> int:
        """A signed byte."""
        return int(self._num("i8"))

    def u8(self) -> int:
        """An unsigned byte."""
        return int(self._num("u8"))

    def i16(self) -> int:
        """A signed 16-bit little-endian integer."""
        return int(self._num("i16"))

    def u16(self) -> int:
        """An unsigned 16-bit little-endian integer."""
        return int(self._num("u16"))

    def i32(self) -> int:
        """A signed 32-bit little-endian integer."""
        return int(self._num("i32"))

    def u32(self) -> int:
        """An unsigned 32-bit little-endian integer."""
        return int(self._num("u32"))

    def i64(self) -> int:
        """A signed 64-bit little-endian integer."""
        return int(self._num("i64"))

    def u64(self) -> int:
        """An unsigned 64-bit little-endian integer."""
        return int(self._num("u64"))

    def f32(self) -> float:
        """A 32-bit little-endian float."""
        return float(self._num("f32"))

    def f64(self) -> float:
        """A 64-bit little-endian float."""
        return float(self._num("f64"))

    def tag(self) -> bytes:
        """The next four bytes, a record or subrecord tag."""
        return self.raw(4)

    def string(self) -> str:
        r"""A length-prefixed, null-truncated Windows-1252 string.

        Reads a ``u32`` length then that many bytes, drops everything at and
        after the first ``\0``, and decodes the rest.
        """
        length = self.u32()
        return self.string_of(length)

    def string_of(self, length: int) -> str:
        """A null-truncated Windows-1252 string of exactly ``length`` bytes.

        For a fixed-width name field whose size is known from elsewhere rather
        than a length prefix.
        """
        if length == 0:
            return ""
        raw = self.raw(length)
        nul = raw.find(0)
        if nul != -1:
            raw = raw[:nul]
        try:
            return raw.decode(_ENCODING)
        except UnicodeDecodeError as exc:
            raise EspError(f"undecodable string at {self.pos - length}: {exc}") from exc

    def try_tag(self, expected: bytes) -> bool:
        """Consume the next tag only if it equals ``expected``; else leave it.

        For a subrecord that may or may not follow -- a look-ahead the reader
        can back out of. Reads nothing (and returns ``False``) at the end of the
        range, so an optional trailing subrecord is simply absent, not an error.
        """
        if self.remaining < 4:
            return False
        if self.data[self.pos : self.pos + 4] == expected:
            self.pos += 4
            return True
        return False

    def bound(self, length: int) -> Reader:
        """A reader over the next ``length`` bytes; advances this one past them.

        A record's body is read through one of these so its subrecord loop stops
        at the record's end instead of reading on into the next record.
        """
        start = self._take(length)
        return Reader(self.data, start, start + length)

    def skip(self, n: int) -> None:
        """Advance ``n`` bytes without reading them."""
        self._take(n)


class Writer:
    """A growing buffer that writes the format's values in order.

    The inverse of :class:`Reader`. :meth:`getvalue` returns the bytes written.
    :meth:`mark`/:meth:`patch_u32` let a record write a placeholder size and fill
    it in once the body length is known.
    """

    __slots__ = ("buf",)

    def __init__(self) -> None:
        """An empty writer."""
        self.buf = bytearray()

    def getvalue(self) -> bytes:
        """Everything written so far, as immutable bytes."""
        return bytes(self.buf)

    def __len__(self) -> int:
        """The number of bytes written so far."""
        return len(self.buf)

    def raw(self, data: bytes) -> None:
        """Append bytes verbatim."""
        self.buf += data

    def _num(self, kind: str, value: int | float) -> None:
        """Append one little-endian number of ``kind``."""
        self.buf += _LE[kind].pack(value)

    def i8(self, value: int) -> None:
        """Write a signed byte."""
        self._num("i8", value)

    def u8(self, value: int) -> None:
        """Write an unsigned byte."""
        self._num("u8", value)

    def i16(self, value: int) -> None:
        """Write a signed 16-bit little-endian integer."""
        self._num("i16", value)

    def u16(self, value: int) -> None:
        """Write an unsigned 16-bit little-endian integer."""
        self._num("u16", value)

    def i32(self, value: int) -> None:
        """Write a signed 32-bit little-endian integer."""
        self._num("i32", value)

    def u32(self, value: int) -> None:
        """Write an unsigned 32-bit little-endian integer."""
        self._num("u32", value)

    def i64(self, value: int) -> None:
        """Write a signed 64-bit little-endian integer."""
        self._num("i64", value)

    def u64(self, value: int) -> None:
        """Write an unsigned 64-bit little-endian integer."""
        self._num("u64", value)

    def f32(self, value: float) -> None:
        """Write a 32-bit little-endian float."""
        self._num("f32", value)

    def f64(self, value: float) -> None:
        """Write a 64-bit little-endian float."""
        self._num("f64", value)

    def tag(self, value: bytes) -> None:
        """Write a four-byte tag."""
        if len(value) != 4:
            raise EspError(f"tag must be four bytes, got {value!r}")
        self.buf += value

    def string(self, value: str) -> None:
        r"""Write a length-prefixed, null-terminated Windows-1252 string.

        Mirrors the crate: an empty string is length 1 and a lone ``\0``; a
        string with no embedded ``\0`` is its bytes plus a terminator, length one
        more than the text; a string that contains a ``\0`` is written up to that
        byte with no terminator added.
        """
        if value == "":
            self.u32(1)
            self.u8(0)
            return
        try:
            data = value.encode(_ENCODING)
        except UnicodeEncodeError as exc:
            raise EspError(f"unencodable string {value!r}: {exc}") from exc
        nul = data.find(0)
        if nul != -1:
            self.u32(nul)
            self.raw(data[:nul])
        else:
            self.u32(len(data) + 1)
            self.raw(data)
            self.u8(0)

    def string_no_terminator(self, value: str) -> None:
        r"""Write a length-prefixed Windows-1252 string with no null terminator.

        A few fields (a dialogue response's text) are stored without the trailing
        ``\0`` -- the engine counts the length toward a hard limit and the
        terminator would push a maximum-length string over it. Read back the same
        way as any other string.
        """
        try:
            data = value.encode(_ENCODING)
        except UnicodeEncodeError as exc:
            raise EspError(f"unencodable string {value!r}: {exc}") from exc
        self.u32(len(data))
        self.raw(data)

    def fixed_string(self, value: str, length: int) -> None:
        """Write a Windows-1252 string in exactly ``length`` bytes, null-padded.

        For a fixed-width name field (the crate's ``FixedString<N>``): the text is
        encoded, truncated to ``length`` if longer, and zero-padded to fill it, so
        the field is always the same size regardless of the string.
        """
        try:
            data = value.encode(_ENCODING)[:length]
        except UnicodeEncodeError as exc:
            raise EspError(f"unencodable string {value!r}: {exc}") from exc
        self.buf += data + b"\x00" * (length - len(data))

    def mark(self) -> int:
        """The current offset, to :meth:`patch_u32` a size into later."""
        return len(self.buf)

    def patch_u32(self, offset: int, value: int) -> None:
        """Overwrite the ``u32`` at ``offset`` -- a size known only afterwards."""
        _LE["u32"].pack_into(self.buf, offset, value)
