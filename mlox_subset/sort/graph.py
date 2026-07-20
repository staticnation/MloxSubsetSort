"""Graph primitives for the load-order sort.

Small, pure helpers underneath :func:`~mlox_subset.sort.engine.build_and_sort`:
expanding a rule's plugin pattern to the plugins actually present, detecting
whether a proposed edge would close a cycle, and recognising master files.

Nothing here touches plugin files or rule text, which is what makes it
testable in isolation.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping

from mlox_subset.rules import mlox_pattern_to_regex, pattern_has_meta

#: Extensions that load before ordinary plugins.
_MASTER_SUFFIXES = (".esm", ".omwgame")


def expand_pattern(pattern: str, node_pool: Iterable[str]) -> list[str]:
    """Resolve one rule pattern to the plugins present in the load order.

    Plain filenames -- the overwhelming majority -- skip the regex entirely and
    take a case-insensitive equality path, which is both faster and exact.

    Args:
        pattern: An mlox filename pattern, possibly with ``*``, ``?`` or
            ``<VER>``.
        node_pool: The plugin names available to match against.

    Returns:
        Matching names in ``node_pool`` order. Empty when nothing matches,
        which is normal: rules routinely name plugins the user does not have.
    """
    if pattern_has_meta(pattern):
        regex = mlox_pattern_to_regex(pattern)
        return [name for name in node_pool if regex.match(name)]
    lowered = pattern.lower()
    for name in node_pool:
        if name.lower() == lowered:
            return [name]
    return []


def would_create_cycle(
    adjacency: Mapping[str, Iterable[str]],
    start: str,
    target: str,
    nodes: Collection[str] | None = None,
) -> bool:
    """Whether adding ``start -> target`` would close a cycle.

    Searches forward from ``target``: if ``start`` is already reachable, the
    new edge would complete a loop. Contradictory ordering rules are common in
    community rule databases, so this is a routine check rather than an error
    path -- the caller drops the offending edge and carries on.

    Args:
        adjacency: Existing edges as ``{node: successors}``.
        start: Proposed edge's source.
        target: Proposed edge's destination.
        nodes: Unused; retained for call-site compatibility.

    Returns:
        ``True`` if the edge would create a cycle.
    """
    stack = [target]
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if node == start:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adjacency.get(node, ()))
    return False


def is_master_file(name: str) -> bool:
    """Whether a plugin is a master, which must load before ordinary plugins.

    Args:
        name: A plugin filename.

    Returns:
        ``True`` for ``.esm`` and ``.omwgame`` files.
    """
    return name.lower().endswith(_MASTER_SUFFIXES)
