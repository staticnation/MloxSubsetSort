"""The load-order sort engine.

Places the user's custom plugins into a frozen curated order without ever
reordering the curated portion. That constraint is the whole point of the tool:
a MOMW list is a tested artefact, and a "better" order is still a broken one if
it is not the order the list authors validated.

Pinned by ``tests/test_differential.py`` against a real 687-plugin order,
including that sorting with no custom mods returns the curated order
byte-identical and that repeated sorts agree.

The body was relocated from the engine module verbatim, and typed in a
*separate* later pass. Doing both at once would have made a behaviour change
indistinguishable from a relocation error, in the one function whose output is
the product.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from itertools import pairwise

from mlox_subset.sort.graph import expand_pattern, is_master_file, would_create_cycle
from mlox_subset.tracing import trace_sort

#: Nudge applied to push a dependent just past (or just short of) the custom
#: plugin it is anchored to, so the two never resolve to the same position.
_POSITION_EPSILON = 1e-6

# Retained under its original private name so the relocated body is unchanged.
_is_master_file = is_master_file


def build_and_sort(
    base_order_names: list[str],
    subset_names: Sequence[str],
    rule_blocks: Sequence[tuple[list[str], int]],
    masters: Mapping[str, Sequence[str]] | None = None,
    nearstart: Sequence[str] | None = None,
    nearend: Sequence[str] | None = None,
    anchor_out: MutableMapping[str, tuple[str, str | None]] | None = None,
) -> list[str]:
    """Place the user's custom plugins into the frozen curated order.

    The curated portion is never reordered. That is the tool's entire purpose:
    a MOMW list is a tested artefact, and an order its authors did not validate
    is a broken one even if some rule says it is "better". Custom plugins are
    positioned around that fixed spine using the mlox ordering rules, with
    master files kept ahead of ordinary plugins.

    Sorting with no custom plugins returns ``base_order_names`` unchanged, and
    repeated sorts of the same input agree. Both properties are pinned against
    a real 687-plugin order by ``tests/test_differential.py``.

    Args:
        base_order_names: The curated ``content=`` order, treated as frozen.
            A ``list`` specifically: it is concatenated to build the node pool.
        subset_names: The user's own plugins to place. Entries matching a base
            plugin under different casing are canonicalised onto the base
            spelling and de-duplicated -- OpenMW's VFS is case-insensitive, so
            ``NewMod.esp`` and ``newmod.esp`` are one file, and treating them
            as two graph nodes would insert the plugin twice.
        rule_blocks: ``(names, priority)`` ordering chains from ``[Order]``
            blocks. Chains are kept whole so an uninstalled middle plugin acts
            as a bridge rather than dropping the constraint.
        masters: ``{plugin: required masters}``, used to keep a plugin after
            everything it depends on.
        nearstart: Plugins to pull toward the start, as far as constraints
            allow. Position hints, not an ordering chain.
        nearend: Plugins to pull toward the end, likewise.
        anchor_out: If given, receives ``{plugin: (how, anchor)}`` -- ``anchor``
            is ``None`` when the plugin had no positioning signal -- describing
            why each custom plugin landed where it did -- used to annotate the
            emitted TOML. **Mutated in place.**

    Returns:
        The full plugin order: every base plugin in its original relative
        position, with the custom plugins placed among them.
    """
    # Guard against the same plugin becoming two different graph nodes just
    # because of a casing difference (OpenMW's VFS is case-insensitive, so
    # 'NewMod.esp' and 'newmod.esp' are the same file) -- canonicalize any
    # subset entry that matches an existing base entry onto that entry's
    # exact spelling, and drop the resulting case-duplicates. Without this,
    # a subset plugin already in the cfg under different casing would get
    # inserted a second time instead of being repositioned.
    trace_sort(
        f"[sort] === build_and_sort: {len(base_order_names)} base plugin(s), "
        f"{len(subset_names)} subset (custom) plugin(s), {len(rule_blocks)} mlox rule block(s), "
        f"masters for {len(masters or {})} plugin(s) ==="
    )
    base_lower_map = {n.lower(): n for n in base_order_names}
    canonical_subset_names = []
    seen_lower = set()
    for n in subset_names:
        canon = base_lower_map.get(n.lower(), n)
        if canon.lower() != n.lower():
            trace_sort(f"[sort] canonicalize subset '{n}' -> cfg spelling '{canon}'")
        if canon.lower() not in seen_lower:
            seen_lower.add(canon.lower())
            canonical_subset_names.append(canon)
        else:
            trace_sort(f"[sort] drop duplicate subset entry '{n}' (already have '{canon}')")
    subset_names = canonical_subset_names

    base_index = {name: i for i, name in enumerate(base_order_names)}
    nodes = set(base_order_names) | set(subset_names)
    # Deterministic iteration pool: set order is randomized per process
    # (PYTHONHASHSEED), and using `nodes` directly for rule expansion made
    # edge insertion order -- and through it the final sort -- vary from
    # run to run. Same membership, fixed order.
    node_pool = base_order_names + [n for n in subset_names if n not in base_index]
    subset_set = set(subset_names)
    node_lower = {n.lower(): n for n in nodes}
    in_cfg = [n for n in subset_names if n in base_index]
    new_cust = [n for n in subset_names if n not in base_index]
    trace_sort(
        f"[sort] {len(in_cfg)} custom(s) already in cfg (will be repositioned), "
        f"{len(new_cust)} brand-new custom(s) to insert"
    )

    adj: dict[str, set[str]] = {n: set() for n in nodes}
    indeg = dict.fromkeys(nodes, 0)
    conflicts = []  # mlox rules we couldn't apply without reordering the frozen cfg

    def add_edge(a: str, b: str, label: str, quiet: bool = False) -> bool:
        """Add an ordering edge ``a`` before ``b``, unless it closes a cycle.

        Returns:
            ``True`` if the edge exists after the call -- either newly added,
            already present, or a self-edge. ``False`` when it was refused to
            avoid a cycle, which contradictory community rules make routine
            rather than exceptional.
        """
        if a == b or b in adj.get(a, ()):
            return True
        if would_create_cycle(adj, a, b, nodes):
            conflicts.append((a, b))
            if not quiet:
                trace_sort(f"[sort]   edge REJECTED (would cycle): '{a}' -> '{b}'  [{label}]")
            return False
        adj[a].add(b)
        indeg[b] += 1
        if not quiet:
            trace_sort(f"[sort]   edge OK: '{a}' -> '{b}'  [{label}]")
        return True

    # 1) frozen chain from the existing cfg order -- but ONLY the curated (non-
    #    subset) plugins. Your custom mods that are already in the cfg must NOT be
    #    chained in place, or they'd be locked between their current neighbors and
    #    couldn't be re-sorted. Bridging over them chains curated[i] -> curated[i+1]
    #    directly, freezing the curated list while leaving customs free to move.
    frozen_seq = [n for n in base_order_names if n not in subset_set]
    trace_sort(
        f"[sort] step 1: frozen chain over {len(frozen_seq)} curated plugin(s), "
        f"bridging over {len(in_cfg)} custom(s) in the cfg (chain edges not logged individually)"
    )
    for a, b in pairwise(frozen_seq):
        add_edge(a, b, "existing cfg order", quiet=True)

    # 1b) header-master dependencies: every custom plugin must load AFTER each
    #     master it lists in its TES3 header (the real dependency, which mlox's
    #     rule DB doesn't capture for arbitrary mods). Only added for CUSTOM
    #     dependents so the curated list (already master-correct) is never touched.
    trace_sort("[sort] step 1b: header-master dependency edges")
    if masters:
        for p in subset_names:
            ms = masters.get(p.lower(), ())
            if ms:
                trace_sort(f"[sort]  '{p}' header masters: {list(ms)}")
            for m in ms:
                mn = node_lower.get(m.lower())
                if mn and mn != p:
                    add_edge(mn, p, "master (header)")
                elif not mn:
                    trace_sort(f"[sort]   master '{m}' of '{p}' NOT installed -- no edge")
    else:
        trace_sort("[sort]  (no header masters available -- mod files not reachable)")

    # 2) mlox ordering edges, but only where they touch a subset plugin (the
    #    frozen base is already ordered by step 1). Within each block we chain
    #    consecutive INSTALLED matches, skipping over patterns that match
    #    nothing you have -- so [Order] A, B, C with B not installed still
    #    yields A -> C directly, preserving the constraint instead of losing it
    #    when B drops out. This is the transitive-bridge behaviour the real mlox
    #    engine gets by keeping a not-installed plugin as a phantom node.
    trace_sort("[sort] step 2: mlox [Order] rule edges (only those touching a custom plugin)")
    _rule_edge_count = 0
    # Higher priority (later file, e.g. mlox_user.txt) FIRST: since add_edge
    # rejects any edge that would close a cycle, the edges added earliest win
    # a conflict -- exactly how the real mlox gives user rules precedence by
    # reading mlox_user.txt before mlox_base.txt. (Sorting ascending here
    # silently gave the BASE file precedence -- backwards.)
    blocks_sorted = sorted(rule_blocks, key=lambda b: -b[1])
    for names, priority in blocks_sorted:
        # expand each token to its installed matches; a token that matches
        # nothing is dropped (it becomes an order bridge, not a broken link)
        survivors = [ms for ms in (expand_pattern(tok, node_pool) for tok in names) if ms]
        for a_matches, b_matches in pairwise(survivors):
            for a in a_matches:
                for b in b_matches:
                    if a == b:
                        continue
                    if a in subset_set or b in subset_set:
                        add_edge(a, b, f"mlox rule (priority {priority})")
                        _rule_edge_count += 1
    trace_sort(f"[sort]  considered rule edges touching customs: {_rule_edge_count}")

    # Report rules we couldn't apply -- deduped and phrased as info, not alarm.
    # These aren't errors: they happen whenever a curated MOMW cfg order
    # intentionally differs from raw mlox. The frozen cfg order is kept and the
    # affected plugin is still placed as well as the non-conflicting rules allow.
    if conflicts:
        seen, unique = set(), []
        for a, b in conflicts:
            key = (a.lower(), b.lower())
            if key not in seen:
                seen.add(key)
                unique.append((a, b))
        print(
            f"\n  {len(unique)} mlox ordering rule(s) not applied -- your openmw.cfg already "
            f"orders these the other way, so your (curated) cfg order is kept:"
        )
        for a, b in unique:
            print(
                f"    - mlox wanted '{a}' before '{b}', but your load order already has "
                f"'{b}' before '{a}'"
            )

    # 3) stable Kahn's topological sort. Tie-break among ready nodes:
    #    (a) masters (.esm/.omwgame) before ordinary plugins -- ESM-first, so a
    #        custom master with no rule still floats up into the master block;
    #    (b) then original cfg position (curated keep their exact order; a custom
    #        already in the cfg keeps its rough spot; brand-new customs sort after
    #        the plugins they were declared after);
    #    (c) then name, for determinism.
    #    Edges always win over the tie-break, so real dependencies/rules dominate.
    import heapq

    nb = len(base_order_names)
    # float-valued: base plugins land on integers, customs on fractional
    # positions between them (see _POSITION_EPSILON).
    pos: dict[str, float] = dict(base_index)
    # Position each custom from ALL of its graph predecessors -- header-master
    # edges AND applied mlox [Order] rule edges -- resolved TRANSITIVELY through
    # custom->custom chains: "place this custom right after the latest-loading
    # thing it must come after", whatever that thing is (a curated plugin or
    # another custom). Master-type predecessors (.esm/.omwgame) are NOT a
    # position signal: they sit in the master block at the very top and half
    # the list depends on them, so anchoring to them would cluster everything
    # at the front (a previous failed attempt). A custom with no non-master
    # predecessor keeps its cfg position (if already in the cfg) or goes to
    # the end, in declared order -- same place the Configurator would append it.
    trace_sort(
        "[sort] step 3: anchoring custom plugins from their graph neighbors "
        "(master edges + applied mlox rule edges, resolved transitively)"
    )
    preds: dict[str, list[str]] = {}
    succs: dict[str, list[str]] = {}
    for a, tgts in adj.items():
        for b in tgts:
            if b in subset_set:
                preds.setdefault(b, []).append(a)
            if a in subset_set:
                succs.setdefault(a, []).append(b)
    # adj's edge sets iterate in hash order (randomized per process); sort the
    # neighbor lists so tie-breaks and resolution order -- and therefore the
    # final sort -- are identical run to run.
    for lst in preds.values():
        lst.sort(key=str.lower)
    for lst in succs.values():
        lst.sort(key=str.lower)

    declared_end = {n: nb + j for j, n in enumerate(subset_names)}
    resolved: dict[str, tuple[float, str | None, str]] = {}  # custom -> (pos, anchor, how)
    derives: dict[str, set[str]] = {}  # custom -> customs its position derived from
    _resolving = set()  # in-flight nodes (graph is a DAG, but pred/succ lookups interlock)

    def _no_signal_pos(n: str) -> float:
        """Fallback position for a plugin with no ordering signal."""
        # keep cfg pos if already in the cfg, else end
        return float(base_index[n]) if n in base_index else float(declared_end[n])

    def _derives_from(x: str, n: str) -> bool:
        """Whether x's resolved position was derived from n's.

        Checked transitively. Anchoring n against such a value would be
        circular and inflate both positions -- "A loads before B" must not
        make B anchor after A's own fallback-end position.
        """
        stack, seen = [x], set()
        while stack:
            c = stack.pop()
            if c == n:
                return True
            if c in seen:
                continue
            seen.add(c)
            stack.extend(derives.get(c, ()))
        return False

    def _final_pos(n: str) -> float:
        """Anchor position for custom n.

        1. "After" signal (preferred): right after the latest-loading NON-master
           thing n must load after -- a curated plugin (rule edge / header
           master) or another custom (resolved transitively). Master-type
           (.esm/.omwgame) predecessors are NOT a signal: they sit in the
           master block at the very top and half the list depends on them, so
           anchoring to them clusters everything at the front.
        2. "Before" signal: otherwise, just before the earliest-loading thing
           n must load BEFORE (mlox [Order] rules mostly constrain customs
           this way). Without this, a before-constrained custom keeps its
           end position, and when the frozen chain reaches its curated
           successor, Kahn's stalls there and dumps every earlier pending
           custom in one big block -- the exact bug being fixed.
        3. Neither: keep cfg position (if already in the cfg) or go to the
           end, in declared order -- where the Configurator would append it.

        Neighbors whose position derives from n (see _derives_from), or that
        are still being resolved, are skipped -- their value comes FROM n's,
        so it can't ground n's. A skip is recorded in `derives` so the round
        loop below can recompute n once the neighbor has settled.
        """
        got = resolved.get(n)
        if got is not None:
            return got[0]
        _resolving.add(n)
        deps = derives.setdefault(n, set())
        try:
            best, best_p = None, None
            for p in preds.get(n, ()):
                if _is_master_file(p):
                    continue  # master block, top of list: no position signal
                if p in subset_set:
                    if p in _resolving:
                        deps.add(p)  # interlock: p's value needs n's -- skip
                        continue
                    bp = _final_pos(p)
                    if _derives_from(p, n):
                        continue  # p's position came from n -- circular
                    bp += _POSITION_EPSILON  # right after that custom
                else:
                    bp = base_index[p] + 0.5  # right after that curated plugin
                if best is None or bp > best:
                    best, best_p = bp, p
            if best is not None:
                if best_p in subset_set:
                    deps.add(best_p)
                    deps.update(derives.get(best_p, ()))
                resolved[n] = (best, best_p, "after")
                return best
            low, low_s = None, None
            for s in succs.get(n, ()):
                if s in subset_set:
                    if s in _resolving:
                        deps.add(s)
                        continue
                    bs = _final_pos(s)
                    if _derives_from(s, n):
                        continue
                    bs -= _POSITION_EPSILON  # just before that custom
                else:
                    bs = base_index[s] - 0.5 + _POSITION_EPSILON  # just before that curated plugin
                if low is None or bs < low:
                    low, low_s = bs, s
            if low is not None:
                if low_s in subset_set:
                    deps.add(low_s)
                    deps.update(derives.get(low_s, ()))
                resolved[n] = (low, low_s, "before")
                return low
            resolved[n] = (_no_signal_pos(n), None, "none")
            return resolved[n][0]
        finally:
            _resolving.discard(n)

    import sys as _sys

    _old_rlimit = _sys.getrecursionlimit()
    _sys.setrecursionlimit(max(_old_rlimit, 10 * len(subset_names) + 1000))
    try:
        # A node resolved while a neighbor was still in flight may hold a
        # degraded value (the interlocked contribution was skipped). Re-run
        # nodes whose value depended on another custom's -- everything else
        # stays memoized -- until the values stop changing (bounded).
        for n in subset_names:
            _final_pos(n)
        for _round in range(len(subset_names) + 1):
            changed = False
            for n in [x for x in subset_names if derives.get(x)]:
                old = resolved.pop(n)
                derives[n] = set()
                _final_pos(n)
                if resolved[n][0] != old[0]:
                    changed = True
            if not changed:
                break
    finally:
        _sys.setrecursionlimit(_old_rlimit)

    n_after = n_before = 0
    for n in subset_names:
        val, anch, how = resolved[n]
        if how == "after":
            pos[n] = val
            n_after += 1
            kind = "custom" if anch in subset_set else "curated"
            trace_sort(f"[sort]  anchor '{n}' -> right after {kind} '{anch}' (pos {val:.6g})")
        elif how == "before":
            pos[n] = val
            n_before += 1
            kind = "custom" if anch in subset_set else "curated"
            trace_sort(f"[sort]  anchor '{n}' -> right before {kind} '{anch}' (pos {val:.6g})")
        else:
            pos.setdefault(n, declared_end[n])
            where = "keeps cfg pos" if n in base_index else "end of load order"
            trace_sort(f"[sort]  '{n}' no non-master neighbor -> {where}")
    trace_sort(
        f"[sort]  anchored after a dependency: {n_after}, before a successor: {n_before}, "
        f"unanchored (standalone / masters-only): {len(subset_names) - n_after - n_before} "
        f"/ {len(subset_names)} customs"
    )

    if anchor_out is not None:
        # expose WHY each custom sits where it does -- ("after"|"before", anchor
        # name) for real constraints, ("none", None) for positional-only -- so
        # the TOML emitter can annotate its inserts
        for n in subset_names:
            _v, _a, _how = resolved[n]
            anchor_out[n.lower()] = (_how, _a)

    # [NearStart]/[NearEnd] position hints (mlox semantics: pull each matching
    # plugin as close to the start/end as the edges allow -- NOT a chain).
    # Applied to CUSTOMS only (the curated list is frozen), and they override
    # the anchor heuristic above; graph edges still always win.
    for pats, to_start, label in ((nearstart, True, "NearStart"), (nearend, False, "NearEnd")):
        for pat in pats or ():
            for n in expand_pattern(pat, node_pool):
                if n not in subset_set:
                    continue
                j = declared_end[n] - nb  # stable tie-break among hinted customs
                pos[n] = (
                    (-1.0 + j * _POSITION_EPSILON)
                    if to_start
                    else float(2 * nb + len(subset_names) + j)
                )
                if anchor_out is not None:
                    anchor_out[n.lower()] = ("nearstart" if to_start else "nearend", None)
                trace_sort(
                    f"[sort]  [{label}] hint: '{n}' -> {'front' if to_start else 'very end'} "
                    f"(pos {pos[n]:.6g})"
                )

    def rank(n: str) -> tuple[int, float, str]:
        """Sort key: masters first, then resolved position, then name.

        The name breaks ties so the result is deterministic rather than
        dependent on set or dict iteration order.
        """
        return (0 if _is_master_file(n) else 1, pos.get(n, nb), n.lower())

    trace_sort("[sort] step 4: topological placement (order each plugin is emitted)")
    ready = [(rank(n), n) for n in nodes if indeg[n] == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        _, n = heapq.heappop(ready)
        result.append(n)
        if n in subset_set:  # log only customs to keep the trace readable
            trace_sort(f"[sort]  place #{len(result)}: '{n}'  (CUSTOM, rank={rank(n)})")
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                heapq.heappush(ready, (rank(m), m))

    if len(result) != len(nodes):
        remaining = nodes - set(result)
        trace_sort(f"[sort] UNPLACED (cycle): {sorted(remaining)}")
        print(
            f"WARNING: {len(remaining)} plugin(s) could not be placed due to an "
            f"unresolved cycle and were appended at the end: {sorted(remaining)}"
        )
        result.extend(sorted(remaining, key=str.lower))

    trace_sort(
        f"[sort] === done: {len(result)} plugin(s) placed "
        f"({len(conflicts)} rule edge(s) rejected as cycles) ==="
    )
    return result
