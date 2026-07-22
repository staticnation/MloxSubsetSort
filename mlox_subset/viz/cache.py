"""A persistent, mtime-keyed cache for decoded per-cell detail.

Decoding a cell's landscape and path grid means a tes3conv field lookup and a
VHGT reconstruction -- the slow part of building the explorer, repeated on
every open even when nothing changed. This caches the decoded JSON on disk,
keyed on the modification signature of the plugins that produced it, so a cell
is re-decoded only when one of its source plugins actually changed.

The cache is injected rather than reached for: :func:`~mlox_subset.viz.detail.collect_detail`
takes an optional cache object, and the default is no cache at all. That keeps
the viz package free of filesystem knowledge and lets the tests drive it with a
plain dictionary, while the app supplies a real on-disk cache.

The signature is ``(name, mtime_ns, size)`` per contributing plugin. mtime plus
size is what version-control and build tools use to detect change cheaply; a
content hash would be more certain but would mean reading every plugin in full
on every open, which is the cost this exists to avoid.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol


def plugin_signature(plugins: Sequence[str], paths: Mapping[str, str]) -> str:
    """Build a change signature for the plugins that touch a cell.

    Args:
        plugins: The plugin filenames contributing to the cell, in load order.
        paths: Filename to absolute path, as the conflict scan records them.

    Returns:
        A compact string that changes whenever any contributing plugin's mtime
        or size changes, or one becomes unreadable. Order-sensitive, because
        load order decides the winner.
    """
    parts: list[str] = []
    for name in plugins:
        path = paths.get(name)
        try:
            st = Path(path).stat() if path else None
        except OSError:
            st = None
        if st is None:
            parts.append(f"{name}:missing")
        else:
            parts.append(f"{name}:{st.st_mtime_ns}:{st.st_size}")
    return "|".join(parts)


class DetailCacheProtocol(Protocol):
    """What :func:`~mlox_subset.viz.detail.collect_detail` needs of a cache."""

    def get(self, key: str, signature: str) -> dict[str, Any] | None:
        """Return cached detail for ``key`` if its signature still matches."""
        ...

    def put(self, key: str, signature: str, detail: Mapping[str, Any]) -> None:
        """Store decoded detail for ``key`` under ``signature``."""
        ...


class DetailCache:
    """An on-disk cache of decoded per-cell detail.

    One JSON file per cell, holding ``{"sig": ..., "detail": ...}``. A read
    whose stored signature differs from the caller's is a miss, so a changed
    plugin transparently forces a re-decode. Corrupt or unreadable entries are
    misses too -- the cache is an optimisation and must never be able to break
    the page.
    """

    def __init__(self, folder: str | Path) -> None:
        """Open (creating if needed) a cache under ``folder``.

        Args:
            folder: Directory to hold the cache files.
        """
        self._folder = Path(folder)

    def _file(self, key: str) -> Path:
        """Path of the cache file for a cell key.

        Args:
            key: A ``"x,y"`` cell key.

        Returns:
            The file path, with the key made filesystem-safe.
        """
        safe = key.replace(",", "_").replace("-", "m")
        return self._folder / f"{safe}.json"

    def get(self, key: str, signature: str) -> dict[str, Any] | None:
        """Return cached detail for ``key`` when its signature matches.

        Args:
            key: The cell key.
            signature: The caller's current signature for the cell.

        Returns:
            The cached detail, or ``None`` on a miss (absent, stale, or
            unreadable).
        """
        try:
            raw = self._file(key).read_text(encoding="utf-8")
            stored = json.loads(raw)
        except (OSError, ValueError):
            return None
        if not isinstance(stored, dict) or stored.get("sig") != signature:
            return None
        detail = stored.get("detail")
        return detail if isinstance(detail, dict) else None

    def put(self, key: str, signature: str, detail: Mapping[str, Any]) -> None:
        """Store decoded detail for a cell.

        Writing failures are swallowed: a cache that cannot be written must not
        take down the view it was meant to speed up.

        Args:
            key: The cell key.
            signature: The signature to store it under.
            detail: The decoded detail.
        """
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            self._file(key).write_text(
                json.dumps({"sig": signature, "detail": detail}, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError:
            return
