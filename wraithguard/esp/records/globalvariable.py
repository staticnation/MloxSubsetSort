"""The ``GLOB`` record -- a global variable. A port of ``types/globalvariable.rs``.

A named global the scripting engine reads and writes. Its type -- float, long or
short -- is one byte in ``FNAM``; its value is *always* stored as an ``f32`` in a
following ``FLTV``, and interpreted through the type. So a ``long`` global holds
its integer as that integer's float form, and this mirrors the crate's handling:
a value read for a long or short is truncated to that integer type (the engine
does the same), and a ``NaN`` reads as zero -- as it must, since ``Morrowind.esm``
itself ships one (``ratskilled``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import GlobalType
from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.io import EspError
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    expect_size,
    put_string,
    read_dele,
    unexpected,
    write_dele,
)

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_I32 = (-(2**31), 2**31 - 1)
_I16 = (-(2**15), 2**15 - 1)


def _to_int(value: float, bounds: tuple[int, int]) -> int:
    """Cast a float to a bounded integer as the engine (and Rust ``as``) do.

    ``NaN`` becomes zero; a value beyond the type's range saturates to its
    nearest bound; otherwise it truncates toward zero.
    """
    if math.isnan(value):
        return 0
    low, high = bounds
    if value <= low:
        return low
    if value >= high:
        return high
    return int(value)


def _decode(global_type: GlobalType, raw: float) -> float | int:
    """Interpret the stored ``f32`` through the global's type."""
    if global_type is GlobalType.Long:
        return _to_int(raw, _I32)
    if global_type is GlobalType.Short:
        return _to_int(raw, _I16)
    return 0.0 if math.isnan(raw) else raw


@register
@dataclass
class GlobalVariable(Record):
    """A ``GLOB`` record: a typed global whose value is stored as an ``f32``."""

    TAG: ClassVar[bytes] = b"GLOB"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    global_type: GlobalType = field(default_factory=lambda: GlobalType.Float)
    value: float | int = 0.0

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> GlobalVariable:
        """Read the subrecords; ``FNAM`` (type) is always followed by ``FLTV``."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"NAME":
                self.id = reader.string()
            elif tag == b"FNAM":
                expect_size(reader, "GLOB", "FNAM", 1)
                self.global_type = GlobalType(reader.u8())
                fltv = reader.tag()
                if fltv != b"FLTV":
                    raise EspError(f"GLOB: expected FLTV after FNAM, got {fltv!r}")
                expect_size(reader, "GLOB", "FLTV", 4)
                self.value = _decode(self.global_type, reader.f32())
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("GLOB", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write ``NAME``, the type byte in ``FNAM``, and the value as an ``f32``."""
        put_string(writer, b"NAME", self.id)
        writer.tag(b"FNAM")
        writer.u32(1)
        writer.u8(int(self.global_type))
        writer.tag(b"FLTV")
        writer.u32(4)
        writer.f32(float(self.value))
        write_dele(writer, self.flags)
