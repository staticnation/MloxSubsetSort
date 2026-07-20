"""Read MOMW's ``plugin-order.yml`` -- the curated-list source of truth.

Modding-OpenMW publishes, for each of its curated lists, the exact plugins in
the exact order its authors tested. This module reads that file, and everything
downstream depends on getting it right: a plugin misread as absent from a list
gets treated as one of the user's own mods and becomes eligible for
reordering, which is precisely the failure this tool exists to prevent.

``PyYAML`` is used when available and a small hand-rolled reader stands in when
it is not, so the tool keeps working as a dependency-free single file. The
fallback is intentionally narrow: it parses the shapes this specific file uses
rather than pretending to be a YAML implementation.

The relocated bodies are pinned by ``tests/test_differential.py``, which
digests the parsed entries, the per-list curated orders, and the
needs-cleaning set against the real file.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TypedDict


class PluginOrderEntry(TypedDict):
    """One plugin's record from ``plugin-order.yml``.

    A ``TypedDict`` (PEP 589) rather than a plain ``dict[str, Any]``: the keys
    are fixed and their value types differ, so ``Any`` would erase exactly the
    information worth checking -- that ``on_lists`` is a list of strings and
    ``needs_cleaning`` is a bool. Getting either wrong silently misclassifies
    a curated plugin as one of the user's own.

    ``file_name`` is a plain ``str``, not ``str | None``: an entry without one
    is never returned. Both parsers drop such entries, so callers do not have
    to guard a case that cannot reach them. The half-built shape used *during*
    parsing is :class:`_PartialEntry`.

    Attributes:
        file_name: The plugin's filename.
        for_mod: The mod the plugin belongs to, when the file states one.
        on_lists: Curated lists this plugin appears on.
        needs_cleaning: Whether MOMW flags it as needing a tes3cmd clean.
    """

    file_name: str
    for_mod: str | None
    on_lists: list[str]
    needs_cleaning: bool


class _PartialEntry(TypedDict):
    """An entry mid-parse, before its ``file_name`` has been seen.

    Separate from :class:`PluginOrderEntry` so the public type can promise a
    real filename. The line parser builds one of these and only promotes it
    once ``file_name`` is set.
    """

    file_name: str | None
    for_mod: str | None
    on_lists: list[str]
    needs_cleaning: bool


def _promote(entry: _PartialEntry) -> PluginOrderEntry:
    """Convert a completed partial entry into the public shape.

    Args:
        entry: A partial whose ``file_name`` has been set.

    Returns:
        The same data, typed so callers can rely on ``file_name``.
    """
    file_name = entry["file_name"]
    if file_name is None:  # pragma: no cover - callers check before promoting
        msg = "cannot promote an entry with no file_name"
        raise ValueError(msg)
    return PluginOrderEntry(
        file_name=file_name,
        for_mod=entry["for_mod"],
        on_lists=entry["on_lists"],
        needs_cleaning=entry["needs_cleaning"],
    )


def parse_plugin_order_yml(path: Path) -> list[PluginOrderEntry]:
    """Parse ``plugin-order.yml`` into per-plugin entries.

    Prefers PyYAML
    if it's installed (robust), but falls back to a focused line parser for this
    file's very regular structure so the feature works with a stdlib-only Python
    -- MOMW users won't necessarily have PyYAML. The fallback deliberately
    ignores the nested `depends:` blocks some entries carry (their indented
    `- file_name:` items must NOT be mistaken for top-level plugin entries).
    """
    text = path.read_text(encoding="utf-8", errors="replace")

    try:
        import yaml  # PyYAML, if available

        raw = yaml.safe_load(text) or []
        entries: list[PluginOrderEntry] = []
        for e in raw:
            if not isinstance(e, dict):
                continue
            fn = e.get("file_name")
            if not fn:
                continue
            entries.append(
                PluginOrderEntry(
                    file_name=str(fn),
                    for_mod=e.get("for_mod"),
                    on_lists=[str(x) for x in (e.get("on_lists") or [])],
                    needs_cleaning=bool(e.get("needs_cleaning")),
                )
            )
        return entries
    except ImportError:
        pass  # fall through to the hand parser

    def apply_kv(entry: _PartialEntry, s: str) -> None:
        """Fold one ``key: value`` line into the entry being built."""
        if ":" not in s:
            return
        k, v = s.split(":", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k == "file_name" and entry["file_name"] is None:
            entry["file_name"] = v
        elif k == "for_mod":
            entry["for_mod"] = v
        elif k == "needs_cleaning":
            entry["needs_cleaning"] = v.lower() in ("true", "yes", "1")

    entries = []
    cur: _PartialEntry | None = None
    mode: str | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("- "):  # top-level list item = new plugin entry
            if cur and cur["file_name"]:
                entries.append(_promote(cur))
            cur = _PartialEntry(file_name=None, for_mod=None, on_lists=[], needs_cleaning=False)
            mode = None
            apply_kv(cur, line[2:])
            continue
        if cur is None:
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 2 and ":" in line:
            key = stripped.split(":", 1)[0].strip()
            mode = key if key in ("on_lists", "depends") else None
            if mode is None:
                apply_kv(cur, stripped)
        elif indent >= 4 and stripped.startswith("- ") and mode == "on_lists":
            val = stripped[2:].strip().strip('"').strip("'")
            if val:
                cur["on_lists"].append(val)
        # anything else (incl. nested depends: items) is ignored
    if cur and cur["file_name"]:
        entries.append(_promote(cur))
    return entries


def curated_for_list(
    entries: Sequence[PluginOrderEntry], list_name: str
) -> tuple[set[str], list[str]]:
    """Select the plugins belonging to one curated list.

    Args:
        entries: Parsed entries from :func:`parse_plugin_order_yml`.
        list_name: The curated list to select, matched case-insensitively.

    Returns:
        ``(lowercase_set, ordered_names)`` -- the set for membership tests, the
        list for the canonical load order of that list, in file order. Both are
        empty when ``list_name`` is empty or matches nothing.
    """
    ln = (list_name or "").lower()
    curated_set: set[str] = set()
    curated_order: list[str] = []
    if not ln:
        return curated_set, curated_order
    for e in entries:
        if any(name.lower() == ln for name in e["on_lists"]):
            curated_set.add(e["file_name"].lower())
            curated_order.append(e["file_name"])
    return curated_set, curated_order


def needs_cleaning_set(entries: Iterable[PluginOrderEntry]) -> set[str]:
    """Plugins MOMW flags as needing cleaning, lowercased.

    Args:
        entries: Parsed entries from :func:`parse_plugin_order_yml`.

    Returns:
        Lowercased filenames whose entry sets ``needs_cleaning``.
    """
    return {e["file_name"].lower() for e in entries if e["needs_cleaning"]}


def base_order_matches_yml(
    base_order_names: Sequence[str], curated_order: Sequence[str]
) -> list[str]:
    """Check the cfg's curated plugins against the yml's canonical order.

    Read-only: this reports, it never reorders. Only plugins present in *both*
    are compared, so the user's own mods interleaved in the cfg do not trip it.

    Args:
        base_order_names: Plugin names from the cfg, in file order.
        curated_order: The canonical order for the list, from
            :func:`curated_for_list`.

    Returns:
        Warning strings, empty when the relative order is consistent.
    """
    curated_lower_order = [n.lower() for n in curated_order]
    rank = {n: i for i, n in enumerate(curated_lower_order)}
    cfg_curated = [n for n in base_order_names if n.lower() in rank]
    warnings = []
    prev_rank, prev_name = -1, None
    for name in cfg_curated:
        r = rank[name.lower()]
        if r < prev_rank:
            warnings.append(
                f"[LIST ORDER] '{name}' appears after '{prev_name}' in your cfg, but the "
                f"curated list order has it BEFORE. Your base order may have drifted from the "
                f"canonical list order (or a tool reordered it)."
            )
            break  # one clear report is enough; the rest usually cascade from it
        prev_rank, prev_name = r, name
    return warnings
