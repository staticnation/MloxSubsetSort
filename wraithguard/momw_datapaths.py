r"""Consume a MOMW list's data-path order and reconcile it against openmw.cfg.

Modding-OpenMW's configurator does not ship the ``data=`` order as a file you have
to match against -- it renders the exact, ordered ``data=`` block per curated list
from its API (``/api/cfg-generator/<list>``), under a fixed placeholder mod base
directory::

    data=C:\games\OpenMWMods\<Category>\<ModName>\<extra dirs...>

We fetch that once (see :func:`wraithguard.net.updaters.fetch_list_data_paths`) and
cache the paths as *relative tails* -- everything after the placeholder base -- one
per line, in load order. This module reads that cache and reconciles it against the
``data=`` paths already in a user's ``openmw.cfg``.

Matching is by *compact suffix*, not by mod name: a user's install can sit under
any base directory (and umo adds a per-list sub-folder), but the tail
``<Category>/<ModName>/<dirs>`` is identical, so a cfg path is "managed by the
list" when its compact (lower-cased, alphanumeric-only) form ends with a cached
tail's compact form. That is exact -- no fuzzy name matching -- and independent of
base directory, slash direction and case. It replaces the older
``data-path-order.yml`` fuzzy matcher, which only inspected the last two path
components and so missed any mod whose data path nested deeper than
``ModName/DataFiles`` (patch collections, numbered sub-dirs).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from wraithguard.configurator.cfglines import normalize_data_path

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

#: The placeholder mod base the MOMW API renders paths under (momw-configurator's
#: ``DefaultBaseDir``). Stripped to leave the relative ``<Category>/<ModName>/...``
#: tail, which is stable across installs.
DEFAULT_BASE_DIR = "C:/games/OpenMWMods"


def _compact(value: str) -> str:
    """Lower-cased, alphanumeric-only key -- ignores case, slashes, punctuation.

    The same reduction umo applies to a mod folder name, applied to whole paths
    here so two spellings of the same directory compare equal.

    Args:
        value: Any path or path fragment.

    Returns:
        The compacted key.
    """
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def relative_tail(value: str, base: str = DEFAULT_BASE_DIR) -> str:
    """The portion of a ``data=`` value after the mod base directory.

    Strips the placeholder (or any) base prefix and normalises slashes, leaving
    ``<Category>/<ModName>/<dirs>`` -- the part identical across installs. When
    ``base`` is not found the whole value is returned (already relative).

    Args:
        value: A ``data=`` path value (quoted or not, either slash style).
        base: The base directory to strip.

    Returns:
        The relative tail, forward-slashed, unquoted.
    """
    v = value.strip().strip('"').strip("'").replace("\\", "/")
    marker = base.replace("\\", "/").rstrip("/").casefold() + "/"
    idx = v.casefold().find(marker)
    return v[idx + len(marker) :] if idx != -1 else v


def parse_data_paths_cache(path: Path) -> list[str]:
    """Read a cached MOMW data-paths file into ordered relative tails.

    One tail per line, in load order; blank lines and ``#`` comments are ignored.
    Written by :func:`wraithguard.net.updaters.fetch_list_data_paths`.

    Args:
        path: The cache file.

    Returns:
        The relative tails, in order.
    """
    tails: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            tails.append(stripped.replace("\\", "/"))
    return tails


def _index_tails(tails: Sequence[str]) -> dict[str, list[tuple[str, int]]]:
    """Index tails by their last component's compact key, for fast suffix match.

    Args:
        tails: Relative tails, in canonical order.

    Returns:
        ``{last_component_compact: [(full_tail_compact, canonical_index), ...]}``.
    """
    idx: dict[str, list[tuple[str, int]]] = {}
    for i, tail in enumerate(tails):
        comps = [c for c in tail.split("/") if c]
        if not comps:
            continue
        idx.setdefault(_compact(comps[-1]), []).append((_compact(tail), i))
    return idx


def _match_index(cfg_value: str, idx: dict[str, list[tuple[str, int]]]) -> int | None:
    """The canonical index of the tail a cfg ``data=`` value belongs to, or None.

    A cfg path belongs to a tail when its compact form ends with the tail's
    compact form -- the base directory and any list sub-folder are a prefix we
    ignore. Narrowed by the last-component index so this stays near-instant even
    on a full list.

    Args:
        cfg_value: A ``data=`` path value from the cfg.
        idx: The index from :func:`_index_tails`.

    Returns:
        The matching tail's canonical index, or ``None``.
    """
    comps = [c for c in cfg_value.replace("\\", "/").split("/") if c]
    if not comps:
        return None
    candidates = idx.get(_compact(comps[-1]))
    if not candidates:
        return None
    whole = _compact(cfg_value)
    for tail_compact, canonical_index in candidates:
        if tail_compact and whole.endswith(tail_compact):
            return canonical_index
    return None


def managed_cfg_data_path_norms(tails: Sequence[str], cfg_data_paths: Sequence[str]) -> set[str]:
    """The ``normalize_data_path`` keys of cfg paths the list's tails account for.

    The "managed" set: a cfg ``data=`` path is managed when it matches one of the
    list's cached tails. Everything else in ``data=`` (not the base game, not your
    customizations) is an orphan. Exact suffix matching -- no fuzzy name guess.

    Args:
        tails: The list's cached relative tails.
        cfg_data_paths: The ``data=`` path values from ``openmw.cfg``, in order.

    Returns:
        The normalized keys of the managed cfg paths; empty when no tails.
    """
    if not tails:
        return set()
    idx = _index_tails(tails)
    managed = {normalize_data_path(v) for v in cfg_data_paths if _match_index(v, idx) is not None}
    managed.discard("")
    return managed


def reconcile_data_paths(
    tails: Sequence[str],
    cfg_data_paths: Sequence[str],
    *,
    report_missing: bool = False,
) -> list[str]:
    """Reconcile a list's cached data-path order against the cfg's ``data=`` paths.

    Reports the list's paths not present in the cfg (mods you haven't installed --
    the normal case for a customized setup, so collapsed to a count unless
    ``report_missing``), and the first place your cfg's managed paths fall in a
    different relative order than the list's canonical one. Read-only.

    Args:
        tails: The list's cached relative tails, in canonical order.
        cfg_data_paths: The ``data=`` path values from ``openmw.cfg``, in order.
        report_missing: List each missing tail instead of a single count line.

    Returns:
        Human-readable findings; empty when everything present is in order.
    """
    if not tails:
        return []
    idx = _index_tails(tails)
    matched_canonical: list[int] = []  # canonical index per cfg path, in cfg order
    present: set[int] = set()
    for value in cfg_data_paths:
        ci = _match_index(value, idx)
        if ci is not None:
            matched_canonical.append(ci)
            present.add(ci)

    findings: list[str] = []
    missing = [t for i, t in enumerate(tails) if i not in present]
    if missing:
        if report_missing:
            findings.extend(f"[DATA PATH] the list's '{t}' is not in openmw.cfg." for t in missing)
        else:
            findings.append(
                f"[DATA PATH] {len(missing)} of the list's data= path(s) are not in "
                f"openmw.cfg (usually mods you haven't installed -- not enforced). "
                f"Re-run with -v to list them."
            )

    prev = -1
    for ci in matched_canonical:
        if ci < prev:
            findings.append(
                "[DATA PATH ORDER] a data= path appears earlier in openmw.cfg than one "
                "the list's order puts before it."
            )
            break
        prev = ci
    return findings
