"""The ``SCPT`` record -- a script. A port of ``types/script.rs``.

A Morrowind script as it is stored: a fixed-width name and a header of variable
counts and block lengths (``SCHD``), the packed local-variable name table
(``SCVR``), the compiled bytecode (``SCDT``), and the original source text
(``SCTX``). The variable table and bytecode are opaque byte blocks here -- the
library preserves them exactly; decoding them is
:mod:`wraithguard.mwscript`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import (
    expect_size,
    put_opt_string,
    read_dele,
    unexpected,
    write_dele,
)

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_SCHD_SIZE = 52
_NAME_SIZE = 32


@dataclass
class ScriptHeader:
    """The ``SCHD`` counts: locals by type, and the bytecode/variable lengths."""

    num_shorts: int = 0
    num_longs: int = 0
    num_floats: int = 0
    bytecode_length: int = 0
    variables_length: int = 0

    @classmethod
    def load(cls, reader: Reader) -> ScriptHeader:
        """Read the five u32 counts in order."""
        return cls(
            num_shorts=reader.u32(),
            num_longs=reader.u32(),
            num_floats=reader.u32(),
            bytecode_length=reader.u32(),
            variables_length=reader.u32(),
        )

    def save(self, writer: Writer) -> None:
        """Write the five u32 counts in order (kept verbatim, not recomputed)."""
        writer.u32(self.num_shorts)
        writer.u32(self.num_longs)
        writer.u32(self.num_floats)
        writer.u32(self.bytecode_length)
        writer.u32(self.variables_length)


@register
@dataclass
class Script(Record):
    """A ``SCPT`` record."""

    TAG: ClassVar[bytes] = b"SCPT"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    id: str = ""
    header: ScriptHeader = field(default_factory=ScriptHeader)
    variables: bytes = b""
    bytecode: bytes = b""
    text: str = ""

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Script:
        """Read the script's subrecords; the id is fixed-width inside ``SCHD``."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"SCHD":
                expect_size(reader, "SCPT", "SCHD", _SCHD_SIZE)
                self.id = reader.string_of(_NAME_SIZE)
                self.header = ScriptHeader.load(reader)
            elif tag == b"SCVR":
                self.variables = reader.raw(reader.u32())
            elif tag == b"SCDT":
                self.bytecode = reader.raw(reader.u32())
            elif tag == b"SCTX":
                self.text = reader.string()
            elif tag == b"DELE":
                self.flags = read_dele(reader, self.flags)
            else:
                raise unexpected("SCPT", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the subrecords in the crate's order; empty blocks omitted."""
        writer.tag(b"SCHD")
        writer.u32(_SCHD_SIZE)
        writer.fixed_string(self.id, _NAME_SIZE)
        self.header.save(writer)
        if self.variables:
            writer.tag(b"SCVR")
            writer.u32(len(self.variables))
            writer.raw(self.variables)
        if self.bytecode:
            writer.tag(b"SCDT")
            writer.u32(len(self.bytecode))
            writer.raw(self.bytecode)
        put_opt_string(writer, b"SCTX", self.text)
        write_dele(writer, self.flags)
