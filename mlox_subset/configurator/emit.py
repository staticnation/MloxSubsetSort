"""Emit ``momw-customizations.toml``.

Generates the customisations file the MOMW Configurator consumes: the insert
blocks for the user's own plugins, their ``data=`` paths, and any removals --
while preserving verbatim everything in the source TOML this tool does not
own.

That preservation is deliberate. The file is hand-edited, and silently
dropping a block the tool does not understand would lose the user's work.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from mlox_subset.configurator.apply import configurator_remove_matches
from mlox_subset.configurator.cfglines import (
    cfg_line_value,
    extract_data_path_value,
    normalize_data_path,
    toml_value,
)


def generate_customizations_toml(
    original_data: Mapping[str, Any] | None,
    final_content_order: Sequence[str],
    subset_set: Collection[str],
    original_content_values: Mapping[str, str],
    data_result_tuples: Sequence[tuple[str, bool, str | None]] | None = None,
    raw_data_inserts: Sequence[Mapping[str, Any]] | None = None,
    replace_dest_names: Collection[str] | None = None,
    user_data_values: Collection[str] | None = None,
    list_name: str | None = None,
    remove_content: Sequence[str] | None = None,
    remove_data: Sequence[str] | None = None,
    custom_anchors: Mapping[str, Any] | None = None,
) -> str:
    """Generate the ``momw-customizations.toml`` the Configurator consumes.

    Everything this tool does not own is preserved verbatim from
    ``original_data`` -- ``replace`` and ``append`` blocks, the ``remove*``
    keys, ``listName``. The file is hand-edited, so silently dropping a block
    we do not understand would lose the user's work.

    Args:
        original_data: Parsed source TOML. Its ``listName``, ``remove*``,
            ``replace`` and ``append`` blocks are carried through unchanged.
        final_content_order: The full mlox-sorted ``content=`` plugin list.
        subset_set: Which of those are the user's own, and therefore need a
            regenerated insert block.
        original_content_values: ``{plugin_name: original insert value}``, so
            whatever the user originally wrote is kept -- usually identical to
            the name, but not always.
        data_result_tuples: Output of
            :func:`~mlox_subset.configurator.datapaths.insert_data_paths`, used
            when data paths were sorted, to emit re-anchored inserts.
        raw_data_inserts: The original insert dicts, used when data paths were
            *not* sorted, to pass them through unchanged.
        replace_dest_names: Plugins that arrived as a ``replace`` *dest* rather
            than an ``insert``. They are emitted via the replace passthrough,
            so an insert block here would duplicate and conflict with it.
            Note that momw-configurator's ``replace`` has no ``after``/
            ``before`` of its own -- it inherits the position of ``source`` --
            so mlox moving one of these cannot be expressed as a replace at
            all, and is reported as a warning instead.
        user_data_values: Raw paths of every ``data=`` insert from this run,
            *before* duplicate-skipping. Required for correctness: a path the
            Configurator already baked into ``openmw.cfg`` on a previous run is
            correctly skipped as a live-cfg duplicate, but must still be
            re-emitted here. Without it the regenerated TOML would silently
            lose every data path already in the cfg -- which is all of them,
            since the cfg was built from this very file -- leaving the
            Configurator nothing to re-insert on the next rebuild.
        list_name: Overrides ``listName``. momw-configurator requires it;
            precedence is this argument, then the source TOML, then
            ``"generated"``.
        remove_content: ``removeContent`` entries to emit.
        remove_data: ``removeData`` entries to emit.
        custom_anchors: Per-plugin anchor overrides.

    Returns:
        The complete TOML document, newline-terminated.
    """
    replace_dest_names = replace_dest_names or set()
    subset_set_lower = {s.lower() for s in subset_set}
    original_data = original_data or {}
    # No existing [[Customizations]] block to attach inserts to (e.g. --subset-file
    # was used with no --customizations at all) -- synthesize one so there's
    # somewhere for the insert/replace/append output below to actually go,
    # instead of the whole loop silently iterating zero times.
    # listName is REQUIRED by momw-configurator (it says which curated list the
    # customizations apply to). Precedence: an explicit list_name passed in
    # (--list-name / GUI field) wins; else keep whatever the source TOML had;
    # else fall back to "generated" so the file is at least valid TOML. The
    # override also covers the --subset-file-only case, which otherwise always
    # emitted the useless placeholder "generated".
    default_name = list_name or "generated"
    blocks = original_data.get("Customizations") or [{"listName": default_name}]

    # extra removals from opted-out items that already exist in the cfg -- added
    # to the FIRST block only, so a multi-block file doesn't repeat them
    extra_removes = {
        "removeContent": list(remove_content or []),
        "removeData": list(remove_data or []),
    }

    out = []
    _anchors = []  # every after=/before=/source= value we emit, for the ambiguity check
    _removes = []  # every remove* value we emit -- removal matching is SILENT
    for bi, block in enumerate(blocks):
        out.append("[[Customizations]]")
        name = list_name or block.get("listName")
        if name:
            out.append(f"listName = {toml_value(name)}")
        for key in ("removeData", "removeContent", "removeFallback", "removeGroundcover"):
            vals = list(block.get(key) or [])
            if bi == 0:
                vals += extra_removes.get(key, [])
            # de-dupe case-insensitively, preserving order
            seen, merged = set(), []
            for x in vals:
                if x.lower() not in seen:
                    seen.add(x.lower())
                    merged.append(x)
            if merged:
                # one entry per line, matching the style of MOMW's own
                # documentation examples -- a 25-entry single line is unreadable
                out.append(f"{key} = [")
                out.extend(f"  {toml_value(x)}," for x in merged)
                out.append("]")
                _removes.extend(merged)
        out.append("")

        # 1) DATA INSERTS FIRST (Ensures paths are defined before plugins look for them)
        if data_result_tuples:
            # We emit a block for every line that's OURS -- a genuinely new
            # insert, OR an existing cfg line whose path is one of this run's
            # data paths (i.e. one momw-configurator already applied on a prior
            # run). The latter is why we can't just gate on is_new: after the
            # first rebuild every one of our paths is "already in the cfg", and
            # gating on is_new would drop them all from the regenerated TOML.
            user_norms = {normalize_data_path(v) for v in (user_data_values or [])}
            user_norms.discard("")

            def _anchor_val(entry: tuple[str, bool, str | None]) -> str:
                """The path an entry should be anchored against.

                Falls back to splitting the raw line when the value cannot be
                extracted, so a malformed line still yields *something* to
                anchor on rather than aborting the emit.
                """
                aline, _is_new, _value = entry
                return extract_data_path_value(aline) or aline.split("=", 1)[-1].strip().strip('"')

            classified = []
            for entry in data_result_tuples:
                line, is_new, value = entry
                path_val = value if value else extract_data_path_value(line)
                norm = normalize_data_path(path_val) if path_val else ""
                is_ours = bool(path_val) and (is_new or (norm and norm in user_norms))
                classified.append((entry, path_val, is_ours))

            # Anchoring each new insert "after" the insert immediately before
            # it (as this used to do) is fragile: if two consecutive new
            # paths happen to share a text prefix -- e.g.
            # '...\OpenMW_SetBonus' and '...\OpenMW_SetBonusRebalance' --
            # the first one's own path is a literal substring of the
            # second's cfg line, so momw-configurator's whole-line substring
            # anchor match finds 2 hits for it and aborts the whole apply.
            # So instead, every entry in a contiguous run of new inserts
            # anchors to the SAME existing (frozen, never another new
            # insert) neighboring line:
            #   - a frozen line follows the run -> anchor the whole run
            #     "before" it, emitted in forward order. momw-configurator
            #     inserts each one right before that same target in turn,
            #     which lands them in the correct final order (verified
            #     against simulate_configurator_apply).
            #   - otherwise (run is at the very end of the data= list) ->
            #     anchor the whole run "after" the preceding frozen line
            #     instead, emitted in REVERSE order (same mechanism,
            #     mirrored).
            # Either way, every anchor this emits is a path that existed in
            # openmw.cfg before this run touched it -- never another new
            # insert's own path -- so this whole collision class can't recur.
            i, n = 0, len(classified)
            while i < n:
                entry, path_val, is_ours = classified[i]
                if not is_ours:
                    i += 1
                    continue
                j = i
                while j < n and classified[j][2]:
                    j += 1
                run = classified[i:j]  # contiguous "ours" entries, in final order
                next_frozen = classified[j] if j < n else None
                prev_frozen = classified[i - 1] if i > 0 else None
                if next_frozen is not None:
                    anchor = _anchor_val(next_frozen[0])
                    ordered = run
                    mode = "before"
                elif prev_frozen is not None:
                    anchor = _anchor_val(prev_frozen[0])
                    ordered = list(reversed(run))
                    mode = "after"
                else:
                    anchor = None
                for _entry, val, _ours in ordered if anchor is not None else run:
                    # `run` holds only entries whose is_ours is True, and
                    # is_ours requires a truthy path_val -- so `val` cannot be
                    # None here. Checked rather than assumed: emitting
                    # toml_value(None) would write the literal string 'None'
                    # into the cfg as a data path, which fails silently.
                    if not val:
                        continue
                    out.append("[[Customizations.insert]]")
                    out.append(f"insert = {toml_value(val)}")
                    if anchor is not None:
                        out.append(f"{mode} = {toml_value(anchor)}")
                        _anchors.append(anchor)
                    else:
                        out.append(
                            "# WARNING: no existing data= line anywhere in the cfg to anchor to"
                        )
                    out.append("")
                i = j
        elif raw_data_inserts:
            # --sort-data-paths not given -- pass these through exactly as originally written
            for d in raw_data_inserts:
                out.append("[[Customizations.insert]]")
                out.append(f"insert = {toml_value(d['value'])}")
                if d.get("after"):
                    out.append(f"after = {toml_value(d['after'])}")
                    _anchors.append(d["after"])
                elif d.get("before"):
                    out.append(f"before = {toml_value(d['before'])}")
                    _anchors.append(d["before"])
                out.append("")

        # 2) CONTENT INSERTS SECOND
        # content inserts, in mlox-computed order, each anchored to whatever
        # immediately precedes it (already-existing plugin or an earlier
        # insert block in this same file, which will exist by the time
        # momw-configurator gets to this block)
        for i, name in enumerate(final_content_order):
            if name.lower() not in subset_set_lower or name in replace_dest_names:
                continue
            value = original_content_values.get(name, name)
            # Annotate WHY this mod sits here: the after=/before= below is its
            # chained position (documented Configurator semantics), but the
            # REAL reason comes from the sort -- a dependency/rule target, a
            # NearStart/NearEnd hint, or nothing at all (positional only).
            info = (custom_anchors or {}).get(name.lower())
            if info:
                how, anch = info
                if how == "after":
                    out.append(f"# constraint: must load after {toml_value(anch)}")
                elif how == "before":
                    out.append(f"# constraint: must load before {toml_value(anch)}")
                elif how in ("nearstart", "nearend"):
                    out.append(
                        f"# constraint: mlox [{'NearStart' if how == 'nearstart' else 'NearEnd'}] hint"
                    )
                else:
                    out.append("# no ordering constraint -- positional only")
            out.append("[[Customizations.insert]]")
            out.append(f"insert = {toml_value(value)}")
            if i == 0:
                # sorted to the very start of the load order -- there's no
                # predecessor to anchor "after", so anchor "before" whatever
                # ended up immediately following it instead
                if len(final_content_order) > 1:
                    out.append(f"before = {toml_value(final_content_order[1])}")
                    _anchors.append(final_content_order[1])
                else:
                    out.append("# WARNING: this is the only content= plugin -- no anchor to write")
            else:
                anchor = final_content_order[i - 1]
                out.append(f"after = {toml_value(anchor)}")
                _anchors.append(anchor)
            out.append("")

        for rep in block.get("replace", []):
            out.append("[[Customizations.replace]]")
            if "source" in rep:
                out.append(f"source = {toml_value(rep['source'])}")
                _anchors.append(rep["source"])
            if "dest" in rep:
                out.append(f"dest = {toml_value(rep['dest'])}")
            out.append("")

        for ap in block.get("append", []):
            out.append("[[Customizations.append]]")
            if "append" in ap:
                out.append(f"append = {toml_value(ap['append'])}")
            if "appendBlock" in ap:
                out.append(f"appendBlock = {toml_value(ap['appendBlock'])}")
            out.append("")

    # Ambiguity checks (warn-only, output unchanged). Verified against
    # momw-configurator's cfg/custom.go:
    #  * after=/before=/source= values are matched with strings.Contains
    #    against WHOLE cfg lines, and >1 match is a hard error (doInsert even
    #    discards the cfg it was building) -- so a filename nested inside
    #    another ('Incantation.omwscripts' in 'content=Incantation.omwscripts.esp')
    #    breaks the configurator run.
    #  * remove* values match the same way but with NO multi-match error --
    #    doRemove silently deletes EVERY matching line, so a nested filename
    #    would silently remove a mod the user never opted out. (Path-like
    #    values instead match the line's value exactly or by /-suffix.)
    haystack = [f"content={n}" for n in final_content_order]
    if data_result_tuples:
        haystack += [line for line, _, _ in data_result_tuples]

    _line_value = cfg_line_value
    _remove_matches = configurator_remove_matches

    for a in dict.fromkeys(_anchors):  # dedupe, keep order
        hits = [line for line in haystack if a in line]
        if len(hits) > 1:
            print(
                f"WARNING: anchor '{a}' in the emitted TOML matches "
                f"{len(hits)} openmw.cfg lines -- momw-configurator errors on "
                f"ambiguous matches. Colliding lines: "
                f"{'; '.join(hits[:4])}{' ...' if len(hits) > 4 else ''}"
            )
    for r in dict.fromkeys(_removes):
        hits = [line for line in haystack if _remove_matches(r, line)]
        if len(hits) > 1:
            print(
                f"WARNING: remove entry '{r}' matches {len(hits)} openmw.cfg "
                f"lines -- momw-configurator removes ALL of them, silently. "
                f"Colliding lines: {'; '.join(hits[:4])}{' ...' if len(hits) > 4 else ''}"
            )

    return "\n".join(out).rstrip() + "\n"
