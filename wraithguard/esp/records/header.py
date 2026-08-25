"""The ``TES3`` record -- the plugin header. A port of ``types/header.rs``.

Every plugin opens with this: its format version, whether it is a master, a
plugin or a save, the author and description shown in the launcher, the number of
records that follow, and the masters it depends on. The author and description
are fixed-width fields (32 and 256 bytes); each master is a ``MAST`` name paired
with a ``DATA`` giving that master file's size, which the engine checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from wraithguard.esp.enums import FileType
from wraithguard.esp.flags import ObjectFlags
from wraithguard.esp.io import EspError
from wraithguard.esp.record import Record, register
from wraithguard.esp.records._common import expect_size, unexpected

if TYPE_CHECKING:
    from wraithguard.esp.io import Reader, Writer

_HEDR_SIZE = 300
_AUTHOR_SIZE = 32
_DESC_SIZE = 256
_MASTER_DATA_SIZE = 8
#: The format version Morrowind's own files carry.
_DEFAULT_VERSION = 1.3


@register
@dataclass
class Header(Record):
    """A ``TES3`` record: the plugin header and its master list."""

    TAG: ClassVar[bytes] = b"TES3"

    flags: ObjectFlags = field(default_factory=lambda: ObjectFlags(0))
    version: float = _DEFAULT_VERSION
    file_type: FileType = field(default_factory=lambda: FileType.Esp)
    author: str = ""
    description: str = ""
    num_objects: int = 0
    masters: list[tuple[str, int]] = field(default_factory=list)

    @classmethod
    def load(cls, reader: Reader, flags: ObjectFlags) -> Header:
        """Read the header block and the master list."""
        self = cls(flags=flags)
        while not reader.at_end:
            tag = reader.tag()
            if tag == b"HEDR":
                expect_size(reader, "TES3", "HEDR", _HEDR_SIZE)
                self.version = reader.f32()
                self.file_type = FileType(reader.u32())
                self.author = reader.string_of(_AUTHOR_SIZE)
                self.description = reader.string_of(_DESC_SIZE)
                self.num_objects = reader.u32()
            elif tag == b"MAST":
                name = reader.string()
                data = reader.tag()
                if data != b"DATA":
                    raise EspError(f"TES3: expected DATA after MAST, got {data!r}")
                expect_size(reader, "TES3", "DATA", _MASTER_DATA_SIZE)
                self.masters.append((name, reader.u64()))
            else:
                raise unexpected("TES3", tag)
        return self

    def save(self, writer: Writer) -> None:
        """Write the header block, then each master and its size."""
        writer.tag(b"HEDR")
        writer.u32(_HEDR_SIZE)
        writer.f32(self.version)
        writer.u32(int(self.file_type))
        writer.fixed_string(self.author, _AUTHOR_SIZE)
        writer.fixed_string(self.description, _DESC_SIZE)
        writer.u32(self.num_objects)
        for name, size in self.masters:
            writer.tag(b"MAST")
            writer.string(name)
            writer.tag(b"DATA")
            writer.u32(_MASTER_DATA_SIZE)
            writer.u64(size)
