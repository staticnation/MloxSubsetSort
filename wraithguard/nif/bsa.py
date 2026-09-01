r"""Reading Morrowind's BSA archives, which is where most of the game lives.

The base game ships nearly all of its meshes and textures inside
``Morrowind.bsa``, so a vanilla asset resolves to nothing on disk while the
engine finds it perfectly well. Without this, every base-game mesh looks
untextured and every base-game texture looks missing -- both false, and both on
the lines a user acts on.

**Morrowind's BSA is not Bethesda's later BSA.** The archives shipped with
Oblivion and everything after begin with the magic ``BSA\\0`` and use a folder
tree, compression and different hashing. Morrowind's begins with a version word
of ``0x100`` and is a flat table of names and offsets. Only the older format is
handled here, and one of the other kind is refused by name rather than
misparsed: they share an extension and nothing else.

**Why this is written rather than imported.** The obvious library,
``bethesda-structs``, is MIT and so would have been usable -- but it pulls in
``construct``, ``multidict``, ``attrs`` and ``lz4`` (the last with a compiled
extension), ships a 49 MB tree covering Fallout and Skyrim record formats this
project will never touch, and every BSA in its own test suite is the *later*
format. The Morrowind layout is a header and three tables. This is the same
trade the NIF reader, the DDS decoder and the PNG encoder all make.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003 -- used at runtime, not only in annotations
from typing import Final

from wraithguard.logging_setup import get_logger

LOG = get_logger(__name__)

#: The version word a Morrowind archive starts with.
_TES3_VERSION: Final[int] = 0x100

#: What the later, incompatible format starts with. Recognised only so it can
#: be refused with an explanation instead of producing nonsense.
_LATER_MAGIC: Final[bytes] = b"BSA\x00"

#: Bytes of header before the file table.
_HEADER_SIZE: Final[int] = 12

#: Bytes per entry in the hash table, which sits between the names and the data.
_HASH_ENTRY: Final[int] = 8

#: A ceiling on the declared file count, so a corrupt header cannot ask for an
#: enormous allocation before a single record has been read.
_MAX_FILES: Final[int] = 1 << 20


class BsaError(Exception):
    """Raised when an archive cannot be read."""


@dataclass(frozen=True, slots=True)
class BsaEntry:
    """One file inside an archive.

    Attributes:
        name: The stored path, lower-cased and slash-normalised, so it can be
            matched against a texture reference without a second pass.
        size: Size in bytes.
        offset: Absolute offset of the data within the archive file.
    """

    name: str
    size: int
    offset: int


class BsaArchive:
    """A Morrowind archive, read lazily.

    The index is parsed on open; file *contents* are read only when asked for.
    ``Morrowind.bsa`` is over 400 MB and a viewer wants two textures out of it,
    so reading it whole would be the slowest thing in the program by orders of
    magnitude.
    """

    def __init__(self, path: Path) -> None:
        """Open an archive and read its index.

        Args:
            path: The ``.bsa`` file.

        Raises:
            BsaError: If it is not a readable Morrowind archive.
        """
        self.path = path
        self._entries: dict[str, BsaEntry] = {}
        self._read_index()

    def __len__(self) -> int:
        """How many files the archive holds."""
        return len(self._entries)

    @property
    def names(self) -> list[str]:
        """Every stored path, normalised."""
        return list(self._entries)

    def __contains__(self, name: object) -> bool:
        """Whether the archive holds this path, without reading it.

        Resolving a texture tries roughly ten candidate names against every
        open archive. Answering that with :meth:`read` opens the file and
        pulls the bytes each time, only to throw them away -- around sixty
        wasted reads per texture against the three vanilla archives, on a path
        the 3D viewer walks for every shape in a mesh.

        Args:
            name: The stored path, in any case and with either separator.

        Returns:
            Whether it is in the index.
        """
        return isinstance(name, str) and normalise(name) in self._entries

    def _read_index(self) -> None:
        """Parse the header and the three tables.

        Raises:
            BsaError: If the file is not a Morrowind archive or is truncated.
        """
        try:
            with self.path.open("rb") as handle:
                header = handle.read(_HEADER_SIZE)
                if len(header) < _HEADER_SIZE:
                    raise BsaError(f"{self.path.name} is too short to be an archive")
                if header.startswith(_LATER_MAGIC):
                    raise BsaError(
                        f"{self.path.name} is a post-Morrowind BSA; "
                        f"only the Morrowind format is supported"
                    )
                version, hash_offset, count = struct.unpack("<III", header)
                if version != _TES3_VERSION:
                    raise BsaError(
                        f"{self.path.name} has version {version:#x}, "
                        f"not Morrowind's {_TES3_VERSION:#x}"
                    )
                if count > _MAX_FILES:
                    raise BsaError(f"{self.path.name} claims {count} files")

                # sizes and offsets, then name offsets, then the name table.
                records = _read_exact(handle, count * 8, "file records")
                name_offsets = _read_exact(handle, count * 4, "name offsets")
                # The name table runs from here to the hash table. Its length
                # is the space left, which is why the hash offset is read from
                # the header rather than assumed.
                names_length = hash_offset - (count * 12)
                if names_length < 0:
                    raise BsaError(f"{self.path.name} has an inconsistent header")
                names_blob = _read_exact(handle, names_length, "name table")
                # Data begins after the hash table, which the header locates.
                data_start = _HEADER_SIZE + hash_offset + count * _HASH_ENTRY

                sizes = struct.unpack(f"<{count * 2}I", records)
                starts = struct.unpack(f"<{count}I", name_offsets)
                for index in range(count):
                    name = _read_name(names_blob, starts[index])
                    if not name:
                        continue
                    self._entries[name] = BsaEntry(
                        name=name,
                        size=sizes[index * 2],
                        offset=data_start + sizes[index * 2 + 1],
                    )
        except OSError as exc:
            raise BsaError(f"cannot read {self.path}: {exc}") from exc
        except struct.error as exc:  # pragma: no cover - _read_exact guarantees each
            # unpack gets exactly the byte count its format needs, so struct never
            # raises here; kept as a defensive belt against future refactors.
            raise BsaError(f"{self.path.name} is malformed: {exc}") from exc
        LOG.debug("indexed %d file(s) in %s", len(self._entries), self.path.name)

    def read(self, name: str) -> bytes | None:
        """Read one file out of the archive.

        Args:
            name: The stored path, in any case and with either separator.

        Returns:
            The bytes, or ``None`` when the archive does not hold it.

        Raises:
            BsaError: If the archive is truncated where the file should be.
        """
        entry = self._entries.get(normalise(name))
        if entry is None:
            return None
        try:
            with self.path.open("rb") as handle:
                handle.seek(entry.offset)
                data = handle.read(entry.size)
        except OSError as exc:
            raise BsaError(f"cannot read {name} from {self.path.name}: {exc}") from exc
        if len(data) != entry.size:
            raise BsaError(
                f"{name} runs past the end of {self.path.name}: "
                f"wanted {entry.size} byte(s), got {len(data)}"
            )
        return data


def normalise(name: str) -> str:
    """Reduce a stored or referenced path to a comparable form.

    Args:
        name: A path from an archive or from a mesh.

    Returns:
        Lower-cased, forward slashes, no leading separator.
    """
    return name.strip().replace("\\", "/").lstrip("/").lower()


def _read_name(blob: bytes, start: int) -> str:
    """Read one NUL-terminated name out of the name table.

    Args:
        blob: The whole name table.
        start: Where this name begins.

    Returns:
        The name, normalised, or ``""`` when the offset is out of range.
    """
    if not 0 <= start < len(blob):
        return ""
    end = blob.find(b"\x00", start)
    raw = blob[start:] if end < 0 else blob[start:end]
    # These are Windows-era paths; a stray byte must not lose the whole name.
    return normalise(raw.decode("cp1252", errors="replace"))


def _read_exact(handle: object, count: int, what: str) -> bytes:
    """Read exactly this many bytes or fail.

    Args:
        handle: An open binary file.
        count: How many bytes.
        what: What is being read, for the message.

    Returns:
        The bytes.

    Raises:
        BsaError: If the file ends first. A short read here would otherwise
            silently produce a shorter index than the header declares.
    """
    data = handle.read(count)  # type: ignore[attr-defined]
    if len(data) != count:
        raise BsaError(f"archive ends inside the {what}: wanted {count}, got {len(data)}")
    return bytes(data)
