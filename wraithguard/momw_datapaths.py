"""Read MOMW's ``data-path-order.yml`` and turn it into real data paths.

Modding-OpenMW publishes, per curated list, the order in which each mod's data
directories should be added to ``openmw.cfg``. The file keys each entry by the
mod's *name* (``for_mod``), lists the sub-directories that actually hold its
loadable data (``extra_dirs``, in precedence order), and scopes the entry to the
lists it applies to (``on_lists``)::

    - for_mod: "Arktwend - OpenMW port"
      extra_dirs: ["Arktwend", "Data Files"]
      on_lists: ["arktwend-enhanced-wip"]

Turning that into a path is not a lookup: ``for_mod`` is a display name, while
the folder on disk was named by whatever installed it -- a version suffix, a
different case, punctuation dropped. So the mod's directory is found by *fuzzy*
matching ``for_mod`` against the folder names actually present, and only then are
``extra_dirs`` appended. A wrong match silently points the game at the wrong
assets, so a low-confidence or ambiguous match is reported for confirmation
rather than guessed at -- the same caution ``momw.py`` takes with plugin order.

Like ``momw.py`` this prefers ``PyYAML`` when installed and falls back to a
focused line parser for this file's regular shape, so it works dependency-free.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from wraithguard.configurator.cfglines import normalize_data_path

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

#: Below this SequenceMatcher ratio a name match is treated as no match rather
#: than a guess. Chosen to accept "TAO" vs "TAO - The Arktwend Overhaul" style
#: differences while rejecting unrelated mods.
_MATCH_FLOOR = 0.6


@dataclass(slots=True)
class DataPathEntry:
    """One mod's data-path record from ``data-path-order.yml``.

    Attributes:
        for_mod: The mod's display name, as the file states it.
        extra_dirs: Sub-directories under the mod's folder that hold loadable
            data, in the order they should be added. Empty means the mod's
            folder is itself the data directory.
        on_lists: Curated lists this entry applies to.
    """

    for_mod: str
    extra_dirs: list[str] = field(default_factory=list)
    on_lists: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResolvedDataPaths:
    """The outcome of resolving one entry against a mods root.

    Attributes:
        entry: The source entry.
        mod_dir: The folder matched to ``for_mod``, or ``None`` when none
            cleared the confidence floor.
        score: The match confidence in ``0.0..1.0`` for the chosen folder.
        ambiguous: Whether a second candidate scored within a hair of the best,
            so the match is worth a human's eyes.
        paths: The built data paths (``mod_dir`` joined with each ``extra_dir``,
            or ``mod_dir`` itself when there are none), in precedence order.
            Only paths that exist on disk are included.
        missing: Declared ``extra_dirs`` that did not exist under ``mod_dir``.
    """

    entry: DataPathEntry
    mod_dir: Path | None
    score: float
    ambiguous: bool
    paths: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def parse_data_path_order_yml(path: Path) -> list[DataPathEntry]:
    """Parse ``data-path-order.yml`` into per-mod entries.

    Prefers PyYAML when installed; otherwise a line parser handles this file's
    regular ``for_mod`` / ``extra_dirs`` / ``on_lists`` shape without a
    dependency.

    Args:
        path: The YAML file.

    Returns:
        Every entry that named a ``for_mod``, in file order.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        import yaml

        raw = yaml.safe_load(text) or []
        out: list[DataPathEntry] = []
        for e in raw:
            if not isinstance(e, dict) or not e.get("for_mod"):
                continue
            out.append(
                DataPathEntry(
                    for_mod=str(e["for_mod"]),
                    extra_dirs=[str(x) for x in (e.get("extra_dirs") or [])],
                    on_lists=[str(x) for x in (e.get("on_lists") or [])],
                )
            )
        return out
    except ImportError:
        return _parse_by_hand(text)


def _parse_by_hand(text: str) -> list[DataPathEntry]:
    """Line parser for the exact shape ``data-path-order.yml`` uses."""
    entries: list[DataPathEntry] = []
    cur: DataPathEntry | None = None
    mode: str | None = None  # which list-valued key we are collecting into

    def unquote(value: str) -> str:
        """Strip surrounding whitespace and a single layer of quotes."""
        return value.strip().strip('"').strip("'")

    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if stripped.startswith("- for_mod:"):
            if cur is not None:
                entries.append(cur)
            cur = DataPathEntry(for_mod=unquote(stripped.split(":", 1)[1]))
            mode = None
            continue
        if cur is None:
            continue

        if indent == 2 and stripped.endswith(":") and ":" in stripped:
            key = stripped[:-1].strip()
            mode = key if key in ("extra_dirs", "on_lists") else None
            continue
        if indent == 2 and ":" in stripped:
            # An inline `key: [a, b]` list, or a scalar we ignore.
            key, val = (s.strip() for s in stripped.split(":", 1))
            if key in ("extra_dirs", "on_lists") and val.startswith("["):
                items = [unquote(x) for x in val.strip("[]").split(",") if x.strip()]
                getattr(cur, key).extend(items)
            mode = None
            continue
        if indent >= 4 and stripped.startswith("- ") and mode in ("extra_dirs", "on_lists"):
            val = unquote(stripped[2:])
            if val:
                getattr(cur, mode).append(val)

    if cur is not None:
        entries.append(cur)
    return entries


def entries_for_list(entries: Iterable[DataPathEntry], list_name: str) -> list[DataPathEntry]:
    """Select the entries that apply to one curated list, in file order.

    Args:
        entries: Parsed entries.
        list_name: The list to select, matched case-insensitively. An entry with
            no ``on_lists`` is treated as applying everywhere.

    Returns:
        The matching entries; empty when ``list_name`` is empty.
    """
    ln = (list_name or "").lower()
    if not ln:
        return []
    picked = []
    for e in entries:
        lists = [x.lower() for x in e.on_lists]
        if not lists or ln in lists:
            picked.append(e)
    return picked


# --------------------------------------------------------------------------- #
# Fuzzy matching a mod name to a folder
# --------------------------------------------------------------------------- #


def normalize_mod_name(name: str) -> str:
    """Reduce a mod name or folder name to a comparison key.

    Casefolds, turns every run of non-alphanumerics into a single space, and
    trims -- so ``"TAO - The Arktwend Overhaul"`` and ``"TAO_The-Arktwend
    Overhaul"`` compare as the same token stream. Used only for matching, never
    for building a path.

    Args:
        name: A mod display name or a directory basename.

    Returns:
        The normalised key.
    """
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.casefold()).split())


def _compact(name: str) -> str:
    """A space- and punctuation-free key, matching how umo unpacks a mod folder.

    umo strips spaces and special characters from a mod's name when it unpacks it
    into a per-list folder, so ``"TAO - The Arktwend Overhaul"`` lands on disk as
    ``"TAOTheArktwendOverhaul"``. Comparing the alphanumeric-only, lower-cased
    form on both sides matches that whatever exact characters umo dropped, since
    the same characters are dropped from the ``for_mod`` name too.

    Args:
        name: A mod display name or a directory basename.

    Returns:
        The lower-cased, alphanumeric-only key.
    """
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def _score(a: str, b: str) -> float:
    """Similarity of two normalised names in ``0.0..1.0``.

    Exact keys score 1.0; so does a match once spaces are removed, which is the
    umo case (its unpacked folder has the spaces stripped out). A full
    token-subset (every token of the shorter name present in the longer) scores
    high, which lets a folder named for an abbreviation match a longer
    ``for_mod``; otherwise a character-level ratio settles it.
    """
    if not a or not b:
        return 0.0
    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    if ta and tb and (ta <= tb or tb <= ta):
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def resolve_mod_dir(
    for_mod: str, candidates: Sequence[tuple[str, Path]]
) -> tuple[Path | None, float, bool]:
    """Fuzzy-match ``for_mod`` to one of ``candidates``.

    Args:
        for_mod: The mod name from the YAML.
        candidates: ``(display_name, path)`` pairs to match against -- typically
            the sub-folders of a mods root, or the cfg's ``data=`` paths.

    Returns:
        ``(path, score, ambiguous)``. ``path`` is ``None`` when nothing cleared
        :data:`_MATCH_FLOOR`. ``ambiguous`` is ``True`` when a runner-up scored
        within ``0.05`` of the best, so the choice deserves confirmation.
    """
    key = normalize_mod_name(for_mod)
    scored = sorted(
        ((_score(key, normalize_mod_name(name)), path) for name, path in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored or scored[0][0] < _MATCH_FLOOR:
        return None, scored[0][0] if scored else 0.0, False
    best_score, best_path = scored[0]
    ambiguous = len(scored) > 1 and best_score - scored[1][0] < 0.05
    return best_path, best_score, ambiguous


# --------------------------------------------------------------------------- #
# Building and reconciling paths
# --------------------------------------------------------------------------- #


def build_data_paths(
    entries: Sequence[DataPathEntry], list_name: str, mods_root: Path
) -> list[ResolvedDataPaths]:
    """Resolve every entry on ``list_name`` to real, ordered data paths.

    Each entry's ``for_mod`` is matched to a sub-folder of ``mods_root``; the
    match is then confirmed by whether the declared ``extra_dirs`` exist under
    it (an existing folder is a far stronger signal than a name alone).

    Args:
        entries: Parsed entries.
        list_name: The curated list to build for.
        mods_root: The directory the mods are installed under.

    Returns:
        One :class:`ResolvedDataPaths` per entry on the list, in file order.
    """
    candidates = [(p.name, p) for p in sorted(mods_root.iterdir()) if p.is_dir()]
    resolved: list[ResolvedDataPaths] = []
    for entry in entries_for_list(entries, list_name):
        mod_dir, score, ambiguous = resolve_mod_dir(entry.for_mod, candidates)
        paths: list[Path] = []
        missing: list[str] = []
        if mod_dir is not None:
            if entry.extra_dirs:
                for sub in entry.extra_dirs:
                    candidate = mod_dir / sub
                    if candidate.is_dir():
                        paths.append(candidate)
                    else:
                        missing.append(sub)
            else:
                # No extra_dirs: the matched folder (always a real dir, since
                # candidates are drawn from iterdir()'s directories) is the path.
                paths.append(mod_dir)
        resolved.append(
            ResolvedDataPaths(
                entry=entry,
                mod_dir=mod_dir,
                score=score,
                ambiguous=ambiguous,
                paths=paths,
                missing=missing,
            )
        )
    return resolved


def reconcile_with_cfg(
    resolved: Sequence[ResolvedDataPaths], cfg_data_paths: Sequence[str]
) -> list[str]:
    """Compare the built data-path order against the cfg's ``data=`` paths.

    Read-only: reports drift, it never rewrites the cfg. Uses
    :func:`~wraithguard.configurator.cfglines.normalize_data_path` so two
    spellings of the same directory compare equal.

    Args:
        resolved: Output of :func:`build_data_paths`.
        cfg_data_paths: The raw ``data=`` values from ``openmw.cfg``, in order.

    Returns:
        Human-readable findings: intended paths absent from the cfg, and any
        pair whose relative order in the cfg contradicts the intended order.
    """
    intended = [p for r in resolved for p in r.paths]
    intended_norm = [normalize_data_path(str(p)) for p in intended]
    cfg_norm = [normalize_data_path(p) for p in cfg_data_paths]
    cfg_rank = {name: i for i, name in enumerate(cfg_norm)}

    findings: list[str] = []
    for path, norm in zip(intended, intended_norm):
        if norm and norm not in cfg_rank:
            findings.append(f"[DATA PATH] intended path not in openmw.cfg: {path}")

    present = [(norm, path) for norm, path in zip(intended_norm, intended) if norm in cfg_rank]
    prev_rank, prev_path = -1, None
    for norm, path in present:
        r = cfg_rank[norm]
        if r < prev_rank:
            findings.append(
                f"[DATA PATH ORDER] '{path}' comes after '{prev_path}' in openmw.cfg, "
                f"but the curated data-path order has it before."
            )
            break
        prev_rank, prev_path = r, path
    return findings


def _index_cfg_data_paths(
    cfg_data_paths: Sequence[str],
) -> tuple[dict[str, int], list[tuple[frozenset[str], int]]]:
    """Build the compact + token indices used to match mods to ``data=`` paths.

    Each ``data=`` path contributes its basename and its parent's name as
    candidates, so both ``.../ModName/Data Files`` and ``.../ModName`` match. The
    exact index is keyed on the compact (space/punctuation-free) form so a
    umo-unpacked folder ("TAOTheArktwendOverhaul") matches its "TAO - The
    Arktwend Overhaul" entry; the token list is the looser abbreviation fallback.
    Built once per reconcile so the match stays O(1)-ish instead of a
    per-entry-per-candidate character-ratio scan (which made a full cfg hang).

    Args:
        cfg_data_paths: The ``data=`` path values from ``openmw.cfg``, in order.

    Returns:
        ``(exact, tokenized)`` -- a compact-key -> first cfg index dict, and a
        list of ``(token frozenset, cfg index)`` pairs in cfg order.
    """
    exact: dict[str, int] = {}
    tokenized: list[tuple[frozenset[str], int]] = []
    for index, raw in enumerate(cfg_data_paths):
        parts = [p for p in raw.replace("\\", "/").rstrip("/").split("/") if p]
        for name in parts[-2:]:
            exact.setdefault(_compact(name), index)
            tokenized.append((frozenset(normalize_mod_name(name).split()), index))
    return exact, tokenized


def _match_entry_to_cfg(
    for_mod: str,
    exact: Mapping[str, int],
    tokenized: Sequence[tuple[frozenset[str], int]],
) -> int | None:
    """Find the cfg ``data=`` index that accounts for ``for_mod``, or ``None``.

    Exact compact match first (the vast majority); a cheap token-subset scan
    then covers abbreviations ("TAO" vs "TAO - The Arktwend Overhaul"). No
    character-ratio work is done, so this stays cheap on a full cfg.

    Args:
        for_mod: The yml entry's mod name.
        exact: The compact-key index from :func:`_index_cfg_data_paths`.
        tokenized: The token index from :func:`_index_cfg_data_paths`.

    Returns:
        The matching cfg ``data=`` index, or ``None`` when nothing matches.
    """
    key = normalize_mod_name(for_mod)
    index = exact.get(_compact(for_mod))
    if index is None:
        key_tokens = frozenset(key.split())
        if key_tokens:
            for cand_tokens, cand_index in tokenized:
                if cand_tokens and (key_tokens <= cand_tokens or cand_tokens <= key_tokens):
                    return cand_index
    return index


def managed_cfg_data_path_norms(
    entries: Sequence[DataPathEntry], list_name: str, cfg_data_paths: Sequence[str]
) -> set[str]:
    """Return the ``normalize_data_path`` keys the curated list accounts for.

    The "managed" side of the reconcile: instead of reporting mods with no cfg
    path, it reports the cfg ``data=`` paths that *do* match a list entry. That
    is the filter a caller needs to tell a curated data folder from an unmanaged
    (orphan) one -- with it, orphan-pulling can now safely pull ``data=`` paths,
    since a path the curated list arranges is no longer mistaken for a hand-added
    one. Same compact + token matching as :func:`reconcile_list_against_cfg`.

    Args:
        entries: Parsed data-path-order.yml entries.
        list_name: The curated list in play.
        cfg_data_paths: The ``data=`` path values from ``openmw.cfg``, in order.

    Returns:
        The normalized keys (see
        :func:`~wraithguard.configurator.cfglines.normalize_data_path`) of the
        cfg ``data=`` paths the list manages; empty when the list selects
        nothing.
    """
    picked = entries_for_list(entries, list_name)
    if not picked:
        return set()
    exact, tokenized = _index_cfg_data_paths(cfg_data_paths)
    managed: set[str] = set()
    for entry in picked:
        index = _match_entry_to_cfg(entry.for_mod, exact, tokenized)
        if index is not None:
            managed.add(normalize_data_path(cfg_data_paths[index]))
    managed.discard("")  # a matched path never normalizes to "", but be safe
    return managed


def reconcile_list_against_cfg(
    entries: Sequence[DataPathEntry],
    list_name: str,
    cfg_data_paths: Sequence[str],
    *,
    report_missing: bool = False,
) -> list[str]:
    """Check a list's mods against the cfg's ``data=`` paths, cfg-only.

    Unlike :func:`reconcile_with_cfg` this needs no mods root: it matches each
    entry's ``for_mod`` against the folder names *already* in the cfg (each
    ``data=`` path's basename and its parent, since a path is typically
    ``.../<ModName>/Data Files``). It reports mods on the list whose data path is
    absent from the cfg, and the first pair whose cfg order contradicts the
    curated order. Read-only.

    A curated list routinely selects mods the user hasn't installed, and the
    upstream yml lags the curated lists -- entries linger after a mod is dropped
    or replaced -- so "missing" is the normal case, not an error. By default the
    missing mods collapse to a single summary line to keep the report readable;
    ``report_missing=True`` restores the per-mod detail (wired to ``-v``). The
    order-drift finding is always emitted, since a *present* path in the wrong
    place is the actionable signal.

    Args:
        entries: Parsed entries.
        list_name: The curated list to check.
        cfg_data_paths: The ``data=`` path values from ``openmw.cfg``, in order.
        report_missing: List each missing mod instead of a single count line.

    Returns:
        Human-readable findings; empty when everything lines up (or the list
        selects nothing).
    """
    picked = entries_for_list(entries, list_name)
    if not picked:
        return []
    exact, tokenized = _index_cfg_data_paths(cfg_data_paths)

    findings: list[str] = []
    missing: list[str] = []  # for_mod values with no cfg data= path
    matched: list[int] = []  # cfg index per list entry, in list order
    for entry in picked:
        index = _match_entry_to_cfg(entry.for_mod, exact, tokenized)
        if index is None:
            missing.append(entry.for_mod)
        else:
            matched.append(index)

    if missing:
        if report_missing:
            findings.extend(
                f"[DATA PATH] '{name}' is on the '{list_name}' list but no "
                f"matching data= path is in openmw.cfg."
                for name in missing
            )
        else:
            findings.append(
                f"[DATA PATH] {len(missing)} mod(s) on the '{list_name}' list have no "
                f"matching data= path in openmw.cfg (usually not installed, or the yml "
                f"lags the curated list upstream -- not enforced). Re-run with -v to list them."
            )

    prev = -1
    for index in matched:
        if index < prev:
            findings.append(
                f"[DATA PATH ORDER] a '{list_name}' data= path appears earlier in "
                f"openmw.cfg than one the curated order puts before it."
            )
            break
        prev = index
    return findings
