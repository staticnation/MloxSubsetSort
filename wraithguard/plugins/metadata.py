"""Locate plugin files and read their metadata.

The ``[VER]``, ``[SIZE]`` and ``[DESC]`` rule predicates test real properties
of real files, so something has to find those files across the several
``data=`` directories OpenMW's virtual file system stitches together. That is
what :class:`PluginFileIndex` does.

The governing principle is mlox's: **never invent a warning that cannot be
substantiated.** The tool is routinely run somewhere the mods are not installed
-- a different machine, a copied config, a packaged build. In that situation
"file not found" means "cannot see the mod folders", not "the plugin is
missing", and reporting the latter would produce confident nonsense. So
lookups return ``None``, :attr:`PluginFileIndex.usable` lets callers tell the
two cases apart, and every predicate falls back to mlox's conservative
behaviour rather than guessing.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import TYPE_CHECKING, Final

from wraithguard.esp import EspError, Header, read_header
from wraithguard.versions import (
    RE_FILENAME_VERSION,
    RE_HEADER_VERSION,
    format_version,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Extensions OpenMW treats as content plugins.
PLUGIN_EXTS: Final = (".esp", ".esm", ".omwaddon", ".omwgame", ".omwscripts")

#: Smallest byte length a TES3 plugin header can occupy. A shorter file cannot
#: contain a complete header, so it is rejected rather than parsed for junk.
TES3_MIN_PLUGIN_SIZE: Final = 362

#: Offset of the description field within a TES3 header.
_DESCRIPTION_OFFSET: Final = 64

#: How much of a plugin to read when only the header is wanted.
_HEADER_READ_BYTES: Final = 4096

#: Defensive cap on the header record's declared body size: the ``TES3`` header
#: is tiny, so a larger size means a corrupt file, not a real header to read.
_HEADER_MAX_BYTES: Final = 1 << 20


class PluginFileIndex:
    """A lazy, case-insensitive index of plugin files across data directories.

    Built on first use rather than at construction, because most runs never
    consult it: the index only matters when a rule carries a ``[VER]``,
    ``[SIZE]`` or ``[DESC]`` predicate.

    Attributes:
        usable: Whether any directory was readable -- see :attr:`usable`.
    """

    # Sequence, not list: `list` is invariant, so a plain list[str] -- what
    # every caller actually builds -- is NOT a list[str | Path] and was
    # rejected. Found when the GUI package came under mypy (§17).
    def __init__(self, data_dirs: Sequence[str | Path] | None = None) -> None:
        """Record the directories to index.

        Args:
            data_dirs: The cfg's ``data=`` directories, in VFS order. Missing
                or unreadable entries are skipped at build time.
        """
        self._dirs: list[str | Path] = list(data_dirs or [])
        self._index: dict[str, Path] | None = None

    def _build(self) -> None:
        """Populate the index, skipping directories that cannot be read.

        **Later directories win on a name collision**, matching OpenMW: a
        plugin (or loose file) provided by more than one ``data=`` folder
        resolves to the one declared *latest*, because a later ``data=`` line
        shadows an earlier one. ``data_dirs`` arrives in load order (earliest
        ``data=`` first), so a plain assignment -- last write wins -- is
        correct, and ``setdefault`` would be exactly wrong: it kept the first
        copy, so a mod split across ``00 Core`` and ``01 Patch`` resolved to
        the pre-patch file, and every downstream read (tes3conv JSON, the
        conflict scan, Merged Lands) then saw terrain the player never loads.
        """
        index: dict[str, Path] = {}
        for directory in self._dirs:
            try:
                path = Path(directory)
                if not path.is_dir():
                    continue
                for entry in path.iterdir():
                    if entry.is_file() and entry.name.lower().endswith(PLUGIN_EXTS):
                        index[entry.name.lower()] = entry  # last data= dir wins
            except OSError:
                continue  # unreadable directory: skip, do not fail the run
        self._index = index

    def find(self, plugin_name: str) -> Path | None:
        """Locate a plugin by filename, case-insensitively.

        Args:
            plugin_name: A plugin filename, without a directory component.

        Returns:
            The file's path, or ``None`` if it was not found. ``None`` does
            *not* prove absence -- check :attr:`usable` first.
        """
        if self._index is None:
            self._build()
        # _build always assigns a dict; the `or {}` keeps the type checker
        # happy without an assert, which `python -O` would strip.
        return (self._index or {}).get(plugin_name.lower())

    @property
    def usable(self) -> bool:
        """Whether at least one data directory was actually readable.

        Returns:
            ``True`` when a "not found" can be trusted to mean the plugin is
            genuinely absent, rather than the mod folders being invisible from
            wherever this is running.
        """
        if self._index is None:
            self._build()
        return bool(self._index)


def read_plugin_description(path: str | Path) -> str:
    """Read the description field from a TES3 plugin header.

    OpenMW is TES3-only, so anything without the ``TES3`` magic is not a
    plugin this tool can read.

    Prefers the in-process ESP header reader, which parses the ``HEDR``
    subrecord by the format's own framing and decodes it as cp1252 (so a
    description with a smart quote or an accented name comes back right, where
    the old fixed-offset ``latin-1`` scan turned bytes 0x80-0x9F into control
    characters). The byte scan remains the fallback for a header the strict
    reader will not parse, so an odd file still yields what it can.

    Args:
        path: Path to a plugin file.

    Returns:
        The description, or ``""`` for any read problem, a non-TES3 file, or a
        file too short to hold a complete header. Never raises: plugin files
        come from the internet, and an unreadable one must not abort a scan.
    """
    header = _read_plugin_header(path)
    if header is not None:
        return header.description
    return _read_description_scan(path)


def _read_plugin_header(path: str | Path) -> Header | None:
    """Parse a plugin's ``TES3`` header via the ESP reader, or ``None``.

    Reads only the first record (its declared size, capped defensively), so a
    large plugin is not read whole and a file whose later records ``tes3conv``
    would refuse still yields its header. Never raises.
    """
    try:
        with Path(path).open("rb") as handle:
            prefix = handle.read(16)  # tag + size + padding + flags
            if len(prefix) < 16 or prefix[:4] != b"TES3":
                return None
            (body_size,) = struct.unpack_from("<I", prefix, 4)
            body = handle.read(min(body_size, _HEADER_MAX_BYTES))
    except (OSError, struct.error):
        return None
    try:
        return read_header(prefix + body)
    except EspError:
        return None


def _read_description_scan(path: str | Path) -> str:
    """Fixed-offset byte scan for the description -- the header-parse fallback."""
    try:
        with Path(path).open("rb") as handle:
            block = handle.read(_HEADER_READ_BYTES)
    except OSError:
        return ""
    if block[:4] != b"TES3" or len(block) < TES3_MIN_PLUGIN_SIZE:
        return ""
    end = block.find(b"\x00", _DESCRIPTION_OFFSET)
    raw = block[_DESCRIPTION_OFFSET:end] if end != -1 else block[_DESCRIPTION_OFFSET:]
    return raw.decode("cp1252", "replace")


def plugin_version(plugin_name: str, index: PluginFileIndex | None) -> str | None:
    """Best-effort canonical version for a plugin.

    Prefers the version stated in the plugin's own header, falling back to one
    embedded in its filename (``Better Bodies 2.2.esp``). The header is
    authoritative; the filename is a guess that is usually right.

    Args:
        plugin_name: The plugin's filename.
        index: Index used to locate the file, or ``None`` when no data
            directories are available.

    Returns:
        A canonical version from :func:`~wraithguard.versions.format_version`,
        or ``None`` when neither source yields one. Callers must treat ``None``
        as "unknowable" rather than "old" -- mlox assumes an ``=`` comparison
        holds in that case rather than raising a warning it cannot support.
    """
    path = index.find(plugin_name) if index else None
    if path is not None:
        match = RE_HEADER_VERSION.search(read_plugin_description(path))
        if match:
            return format_version(match.group(1))
    match = RE_FILENAME_VERSION.search(plugin_name)
    if match:
        return format_version(match.group(1))
    return None


def list_plugins_in_dir(path_value: str, base_dir: Path | None = None) -> list[str]:
    """List the plugin filenames directly inside a ``data=`` directory.

    Non-recursive, because OpenMW and mlox both expect plugins to sit directly
    in the folder a ``data=`` line points at.

    Heavily guarded on purpose. ``path_value`` can be almost anything: an
    absolute Windows path pasted into a TOML written on Linux, an MO2 variable
    that was never substituted, a typo, or a network share that is not mounted
    right now. Any failure means "we do not know what is in this folder", which
    must never escalate into crashing the whole sort.

    Args:
        path_value: The raw value of a ``data=`` line, quoted or not.
        base_dir: Directory to resolve ``path_value`` against when it is
            relative. Ignored for absolute paths.

    Returns:
        Plugin filenames in sorted order, or an empty list when the directory
        is missing, unreadable, or the path could not be interpreted.
    """
    if not path_value:
        return []
    candidates = []
    try:
        raw = path_value.strip().strip('"').strip("'")
        if not raw:
            return []
        p = Path(raw)
        candidates.append(p)
        if base_dir is not None and not p.is_absolute():
            candidates.append(base_dir / p)
    except (TypeError, ValueError, OSError):
        return []

    for p in candidates:
        try:
            if not p.is_dir():
                continue
            return sorted(
                entry.name
                for entry in p.iterdir()
                if entry.is_file() and entry.name.lower().endswith(PLUGIN_EXTS)
            )
        except (OSError, PermissionError):
            continue
    return []
