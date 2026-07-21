"""Insert ``data=`` paths at the right place in a cfg.

OpenMW resolves loose files through a virtual file system layered in ``data=``
order, so *where* a path is inserted changes which file wins a conflict.
Anchors are inferred from the plugins a directory actually contains, so an
inserted path lands next to the mod it belongs with rather than at the end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mlox_subset.configurator.cfglines import (
    detect_data_quoting,
    extract_data_path_value,
    find_anchor_index,
    format_data_line,
    normalize_data_path,
)
from mlox_subset.i18n import gettext as _
from mlox_subset.plugins import list_plugins_in_dir

#: One pending ``data=`` insertion: its value plus optional anchors.
DataInsert = dict[str, Any]


def infer_data_path_anchors(
    data_inserts: list[DataInsert],
    data_order: list[str],
    final_order: list[str],
    cfg_path: Path,
) -> None:
    """Guess an anchor for ``data=`` inserts that arrived without one.

    Looks at what is actually in the folder: if it holds a plugin that also
    appears in the sorted ``final_order``, the ``data=`` line is anchored next
    to whichever existing (frozen) ``data=`` path owns the nearest neighbouring
    plugin in that order. The result is that an inserted path lands beside the
    mod it belongs with instead of at the end of the file, which matters
    because VFS position decides which loose file wins.

    Only inserts with *no* anchor at all are touched. An explicit ``after`` or
    ``before`` written in the TOML is the user's stated intent and is always
    left alone; this is a best-effort fallback, not a correction.

    Every lookup is independently guarded. A folder that cannot be scanned, or
    a plugin absent from ``final_order``, falls through to the existing
    "no anchor -> append at end" behaviour in :func:`insert_data_paths` rather
    than raising.

    Args:
        data_inserts: Pending inserts. **Mutated in place** -- anchors are
            written back into these dicts.
        data_order: Existing ``data=`` lines from the cfg, in file order.
        final_order: The mlox-sorted plugin order for this run.
        cfg_path: Path to ``openmw.cfg``, used to resolve relative folders.

    Returns:
        ``None``. The result is the mutation of ``data_inserts``.
    """
    if not final_order:
        return  # no content sort happened this run -- nothing to anchor against

    base_dir = cfg_path.parent if cfg_path else None

    # plugin (lowercased) -> owning EXISTING data= line's path value.
    # Deliberately built from data_order (frozen/base paths) only -- new
    # inserts can't anchor off each other in the same run (see
    # insert_data_paths' docstring), so they're not eligible anchor targets.
    plugin_owner: dict[str, str] = {}
    for line in data_order:
        val = extract_data_path_value(line)
        if not val:
            continue
        for plugin in list_plugins_in_dir(val, base_dir):
            plugin_owner.setdefault(plugin.lower(), val)

    order_index = {name.lower(): i for i, name in enumerate(final_order)}

    for item in data_inserts:
        if item.get("after") or item.get("before"):
            continue  # explicit anchor already given -- don't second-guess it

        own_plugins = {p.lower() for p in list_plugins_in_dir(item["value"], base_dir)}
        if not own_plugins:
            continue  # empty/unreadable/no-plugin folder -- nothing to infer from

        # find where (if anywhere) this folder's plugins land in the sort
        positions = sorted(order_index[p] for p in own_plugins if p in order_index)
        if not positions:
            continue  # plugins exist but aren't part of this run's sorted set

        lo, hi = positions[0], positions[-1]

        anchor_value: str | None = None
        mode: str | None = None
        # walk backward from the folder's own plugins for an owned neighbor
        for i in range(lo - 1, -1, -1):
            owner = plugin_owner.get(final_order[i].lower())
            if owner:
                anchor_value, mode = owner, "after"
                break
        if not anchor_value:
            # nothing usable behind it -- try forward instead
            for i in range(hi + 1, len(final_order)):
                owner = plugin_owner.get(final_order[i].lower())
                if owner:
                    anchor_value, mode = owner, "before"
                    break

        if anchor_value and mode:
            item[mode] = anchor_value
            via = sorted(own_plugins & order_index.keys())[0]
            print(
                _("  Inferred anchor for '%(value)s': %(mode)s '%(anchor)s' (via plugin %(via)s)")
                % {"value": item["value"], "mode": mode, "anchor": anchor_value, "via": via}
            )


def insert_data_paths(
    data_lines: list[str], data_inserts: list[DataInsert]
) -> list[tuple[str, bool, str | None]]:
    """Insert new ``data=`` lines at their anchored positions.

    Anchors are matched as a case-insensitive substring against the
    **existing** ``data_lines`` only: an insert cannot anchor off another new
    insert from the same run. Multi-step chains need separate runs.

    Guarded against duplicates: an insert whose path already matches an
    existing data_lines entry (case/slash-direction/trailing-slash
    insensitive -- see normalize_data_path) is skipped rather than added a
    second time, and so is a second insert in the same run pointing at a
    path another insert already claimed. This only affects whether a NEW
    line gets added -- it never removes or reorders an existing line, so
    dragging existing entries around in the order panel is unaffected.

    Args:
        data_lines: Existing raw ``data=`` lines from the cfg, in file order.
        data_inserts: Inserts in the order they appeared in the customisations
            TOML, each ``{"value": str, "after": str | None, "before":
            str | None}``.

    Returns:
        Every ``data=`` line for the rewritten cfg, in final order, as
        ``(line_text, is_new, source_value)``. ``is_new`` marks lines this call
        added; ``source_value`` carries the originating insert's value for new
        lines and is ``None`` for pre-existing ones.
    """
    existing_normalized = {
        normalize_data_path(extract_data_path_value(line) or "") for line in data_lines
    }
    existing_normalized.discard("")
    quoted = detect_data_quoting(data_lines)

    # (mode, new_line, source_value) per anchor index.
    anchor_map: dict[int, list[tuple[str, str, str]]] = {}
    leftover: list[tuple[str, str]] = []
    for item in data_inserts:
        norm_val = normalize_data_path(item["value"])
        if norm_val and norm_val in existing_normalized:
            print(
                _("NOTE: '%(value)s' already present in data= list -- skipping duplicate insert.")
                % {"value": item["value"]}
            )
            continue
        if norm_val:
            existing_normalized.add(
                norm_val
            )  # also guards duplicate NEW inserts within this same run

        new_line = format_data_line(item["value"], quoted)
        anchor = item.get("after") or item.get("before")
        mode = "after" if item.get("after") else ("before" if item.get("before") else None)
        if not anchor:
            print(
                _("NOTE: '%(value)s' has no after/before anchor -- appending at end of data= list.")
                % {"value": item["value"]}
            )
            leftover.append((new_line, item["value"]))
            continue
        idx = find_anchor_index(data_lines, anchor)
        if idx is None:
            print(
                _(
                    "WARNING: anchor '%(anchor)s' not found among existing data= paths for "
                    "'%(value)s' -- appending at end instead."
                )
                % {"anchor": anchor, "value": item["value"]}
            )
            leftover.append((new_line, item["value"]))
            continue
        # `mode` is non-None here: the `if not anchor` branch above returned,
        # and anchor is set only when after/before was given.
        anchor_map.setdefault(idx, []).append((mode or "after", new_line, str(item["value"])))

    result: list[tuple[str, bool, str | None]] = []
    for i, line in enumerate(data_lines):
        for mode, new_line, val in anchor_map.get(i, []):
            if mode == "before":
                result.append((new_line, True, val))
        result.append((line, False, None))
        for mode, new_line, val in anchor_map.get(i, []):
            if mode == "after":
                result.append((new_line, True, val))
    for new_line, val in leftover:
        result.append((new_line, True, val))
    return result
