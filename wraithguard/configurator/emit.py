"""Emit ``momw-customizations.toml``.

Generates the customisations file the MOMW Configurator consumes: the insert
blocks for the user's own plugins, their ``data=`` paths, and any removals --
while preserving verbatim everything in the source TOML this tool does not
own.

That preservation is deliberate. The file is hand-edited, and silently
dropping a block the tool does not understand would lose the user's work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wraithguard.configurator.apply import configurator_remove_matches
from wraithguard.configurator.cfglines import (
    extract_data_path_value,
    normalize_data_path,
    toml_value,
)
from wraithguard.i18n import gettext as _

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence


def _subset_runs(
    final_content_order: Sequence[str],
    subset_lower: Collection[str],
    replace_dest_names: Collection[str],
) -> list[tuple[int, int]]:
    """Group the subset's plugins into runs of consecutive positions.

    Each run becomes one ``insertBlock`` on one anchor. A plugin handled by a
    ``replace`` block breaks a run, because it is emitted elsewhere and must not
    appear in an insert as well.

    Args:
        final_content_order: The whole sorted ``content=`` order.
        subset_lower: Lower-cased names of the user's own plugins.
        replace_dest_names: Plugins emitted via ``replace`` instead.

    Returns:
        Half-open ``(start, end)`` index pairs, in order.
    """
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, name in enumerate(final_content_order):
        mine = name.lower() in subset_lower and name not in replace_dest_names
        if mine and start is None:
            start = index
        elif not mine and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(final_content_order)))
    return runs


def _anchor_is_unique(anchor: str, haystack: Sequence[str]) -> bool:
    """Whether an anchor matches exactly one cfg line.

    momw-configurator matches anchors with ``strings.Contains`` against whole
    lines and treats more than one match as fatal for the entire run, so an
    ambiguous anchor is not a warning to the user -- it is a failed rebuild.

    Args:
        anchor: The candidate ``after``/``before`` value.
        haystack: The cfg lines the Configurator will be matching against.

    Returns:
        ``True`` if exactly one line contains it.
    """
    return sum(1 for line in haystack if anchor in line) == 1


def _widen_anchor(value: str, line: str, haystack: Sequence[str]) -> str | None:
    r"""Find the least specific form of an anchor that matches exactly one line.

    The bare value is tried first because it is what a person reading the file
    expects to see. When it is ambiguous the *whole cfg line* is tried, which is
    the widest anchor available and very often unique where the value was not --
    because the line carries delimiters the value lacks:

    * ``E:\\...\\Data Files`` is a substring of ``E:\\...\\Data Files\\Addons``,
      but ``data="E:\\...\\Data Files"`` is not a substring of
      ``data="E:\\...\\Data Files\\Addons"`` -- the closing quote ends it.
    * ``Wares.esp`` is a substring of ``Better Wares.esp``, but
      ``content=Wares.esp`` is not a substring of ``content=Better Wares.esp``
      -- the ``content=`` prefix has to match at the same place.

    Widening cannot always work. An unquoted ``data=`` line has no closing
    delimiter, so a path that is a prefix of another stays ambiguous however
    much of the line is included; the caller falls back to the other neighbour
    in that case.

    Args:
        value: The bare anchor value -- a plugin name or a data path.
        line: The whole cfg line that value came from.
        haystack: The cfg lines the Configurator will be matching against.

    Returns:
        The narrowest unique anchor, or ``None`` when neither form is unique.
    """
    for candidate in (value, line.strip()):
        if candidate and _anchor_is_unique(candidate, haystack):
            return candidate
    return None


def _pick_data_anchor(
    next_frozen: tuple[tuple[str, bool, str | None], str | None, bool] | None,
    prev_frozen: tuple[tuple[str, bool, str | None], str | None, bool] | None,
    anchor_val: Callable[[tuple[str, bool, str | None]], str],
    haystack: Sequence[str],
) -> tuple[str, str | None]:
    """Choose an unambiguous anchor for one run of ``data=`` inserts.

    The data equivalent of :func:`_pick_anchor`, with the same preference for a
    unique anchor over a natural-reading one, and the same refusal to drop an
    insert when no unique anchor exists.

    The order of preference is inverted relative to the content version: a data
    run prefers the line that *follows* it. Data paths are emitted before the
    content inserts that depend on them, and anchoring forward keeps a run
    attached to the curated path it was sorted in front of.

    Args:
        next_frozen: The classified entry following the run, if any.
        prev_frozen: The classified entry preceding the run, if any.
        anchor_val: Extracts the bare path value from a raw entry.
        haystack: The cfg lines the Configurator will be matching against.

    Returns:
        ``(mode, anchor)`` where mode is ``"before"`` or ``"after"``; the anchor
        is ``None`` when the run has no frozen neighbour at all.
    """
    candidates = (("before", next_frozen), ("after", prev_frozen))
    for mode, neighbour in candidates:
        if neighbour is None:
            continue
        widened = _widen_anchor(anchor_val(neighbour[0]), neighbour[0][0], haystack)
        if widened is not None:
            return mode, widened
    # Nothing unique in either direction and in either form. Emit the natural
    # anchor anyway and let the ambiguity warning fire: a rebuild that stops
    # and says which line was ambiguous is recoverable, whereas dropping the
    # run would produce a cfg that is quietly missing mods.
    for mode, neighbour in candidates:
        if neighbour is not None:
            return mode, anchor_val(neighbour[0])
    return "before", None


def _pick_anchor(
    final_content_order: Sequence[str],
    start: int,
    end: int,
    haystack: Sequence[str],
) -> tuple[str, str | None]:
    """Choose an unambiguous anchor for one run of inserts.

    Preference order, and the reasoning for it:

    1. ``after`` the plugin immediately before the run -- the natural choice,
       and the one that reads correctly in the file.
    2. ``before`` the plugin immediately after it. Places the run identically
       and gives a second chance at a unique line.
    3. Neither, when the run is the whole file.

    Each candidate is *widened* before being rejected: a bare plugin name that
    is ambiguous is retried as its whole ``content=`` line, which disambiguates
    the common case of one name being a substring of another (``Wares.esp``
    inside ``Better Wares.esp``). See :func:`_widen_anchor`.

    An ambiguous candidate is skipped rather than emitted: the run's position is
    the same either way, so there is no cost to preferring the one that works.
    Where *both* neighbours are ambiguous in every form the natural anchor is
    emitted anyway and the existing ambiguity warning fires -- the alternative
    would be to silently drop the insert, which is worse than a rebuild that
    stops and says so.

    Args:
        final_content_order: The whole sorted ``content=`` order.
        start: First index of the run.
        end: One past the last index of the run.
        haystack: The cfg lines anchors are matched against.

    Returns:
        ``(mode, anchor)`` where mode is ``"after"`` or ``"before"``; the anchor
        is ``None`` when the run covers the entire order.
    """
    before_run = final_content_order[start - 1] if start > 0 else None
    after_run = final_content_order[end] if end < len(final_content_order) else None
    if before_run is not None:
        widened = _widen_anchor(before_run, f"content={before_run}", haystack)
        if widened is not None:
            return "after", widened
    if after_run is not None:
        widened = _widen_anchor(after_run, f"content={after_run}", haystack)
        if widened is not None:
            return "before", widened
    if before_run is not None:
        return "after", before_run
    if after_run is not None:
        return "before", after_run
    return "after", None


def _replace_notes(rep: Mapping[str, Any], final_content_order: Sequence[str]) -> list[str]:
    """Explain a ``replace`` block that this tool carried through untouched.

    **Why this needs saying.** A regenerated file moves things around, so a
    block the user wrote by hand turns up somewhere it was not before, with no
    note attached, looking exactly like something the tool invented. That
    happened: a user asked publicly whether a ``replace`` at the bottom of
    their exported file was normal, and it was theirs all along -- written to
    reconcile a plugin momw names one way and their install names another.

    The second thing worth saying is a real limitation. momw-configurator's
    ``replace`` has no ``after``/``before``: it inherits the position of
    ``source``. So when mlox wants the replaced plugin somewhere else, this
    tool cannot express that as a replace, and silently leaves it where it is.
    The note gives the position mlox chose, so the user can act on it -- by
    turning the block into an insert, or by leaving it alone deliberately.

    Args:
        rep: The ``replace`` entry, as parsed from the source TOML.
        final_content_order: The full mlox-sorted plugin list.

    Returns:
        Comment lines to emit above the block. Empty when there is nothing
        useful to say -- a malformed entry gets no invented commentary.
    """
    dest = str(rep.get("dest") or "").strip()
    source = str(rep.get("source") or "").strip()
    if not dest or not source:
        return []

    notes = [
        "# Yours: carried through unchanged. This tool never writes a replace of",
        "# its own -- only insert, append and remove blocks are regenerated.",
    ]

    # Data-path replaces have no place in the plugin order and nothing to say.
    lowered = [name.lower() for name in final_content_order]
    try:
        position = lowered.index(dest.lower())
    except ValueError:
        return notes

    notes.append(f"# mlox sorts {dest} to position {position + 1} of {len(final_content_order)}")
    if position > 0:
        notes.append(f"#   after: {final_content_order[position - 1]}")
    notes.append("# A replace has no after/before -- it inherits the position of source --")
    notes.append("# so if that is not where it sits now, only you can move it: change this")
    notes.append("# block to an insert, or leave it as it is on purpose.")
    return notes


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
    new_groundcover: Sequence[str] | None = None,
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
            :func:`~wraithguard.configurator.datapaths.insert_data_paths`, used
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
        new_groundcover: Plugins this run declares as grass that the cfg does not
            already have. Emitted as ``append`` entries (``groundcover=NAME``),
            which is how the Configurator writes a groundcover line -- there is
            no insert form for one. Their **data paths are not handled here**:
            those go through the ordinary data-insert path, because OpenMW has
            to be able to find the file and a data= entry is not grass-specific.

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

    # The lines momw-configurator will match anchors against. Built before the
    # emit loop, not after it, because choosing an anchor now depends on knowing
    # whether it is unique -- the check at the end of this function only warns.
    haystack_for_anchors = [f"content={n}" for n in final_content_order]
    if data_result_tuples:
        haystack_for_anchors += [line for line, _is_new, _val in data_result_tuples]

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
        # Only in the first block. The inserts are this run's sorted subset --
        # global, not a property of any one source [[Customizations]] block --
        # so emitting them once per block (as this used to) inserted every
        # plugin and data path once per block, duplicating them in the rebuilt
        # cfg. The removes and groundcover appends are gated the same way above.
        if bi == 0 and data_result_tuples:
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
                # bool() around the second half too: `norm and norm in ...`
                # evaluates to the empty string when norm is empty, and a
                # Literal[''] sitting in a field named is_ours is a type that
                # lies about itself even though it happens to be falsy.
                is_ours = bool(path_val) and (is_new or bool(norm and norm in user_norms))
                classified.append((entry, path_val, is_ours))

            # Anchoring each new insert "after" the insert immediately before
            # it (as this used to do) is fragile: if two consecutive new
            # paths happen to share a text prefix -- e.g.
            # '...\OpenMW_SetBonus' and '...\OpenMW_SetBonusRebalance' --
            # the first one's own path is a literal substring of the
            # second's cfg line, so momw-configurator's whole-line substring
            # anchor match finds 2 hits for it and aborts the whole apply.
            # So instead, every contiguous run of new inserts is emitted as ONE
            # insertBlock anchored to a single existing (frozen, never another
            # new insert) neighbouring line:
            #   - a frozen line follows the run -> anchor the block "before" it.
            #   - otherwise (the run is at the very end of the data= list) ->
            #     anchor the block "after" the preceding frozen line.
            #
            # Both forms are emitted in FORWARD order. That is worth stating
            # plainly, because it is the opposite of what the chained form
            # needed: N separate inserts all sharing one "after" anchor each
            # land immediately after that same line, so they come out reversed
            # and had to be *written* reversed to compensate. A block is placed
            # as a unit and keeps its own order, so carrying that reversal over
            # would silently invert the run. Both directions are pinned in
            # tests/test_toml_equivalence.py against simulate_configurator_apply.
            #
            # The anchor is widened to the whole cfg line when the bare path is
            # ambiguous (_widen_anchor): a path that is a prefix of a longer one
            # -- '...\Data Files' inside '...\Data Files\Addons', both real
            # lines in a real user's cfg -- matches twice under the
            # Configurator's strings.Contains and is FATAL for the entire run.
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
                mode, anchor = _pick_data_anchor(
                    next_frozen, prev_frozen, _anchor_val, haystack_for_anchors
                )
                # `run` holds only entries whose is_ours is True, and is_ours
                # requires a truthy path_val -- so a value cannot be None here.
                # Filtered rather than assumed: emitting toml_value(None) would
                # write the literal string 'None' into the cfg as a data path,
                # which fails silently.
                values = [val for _entry, val, _ours in run if val]
                if values:
                    out.append("[[Customizations.insert]]")
                    if len(values) == 1:
                        # A single path needs no block; `insert` is the plainer
                        # form and applies identically.
                        out.append(f"insert = {toml_value(values[0])}")
                    else:
                        body = "\n".join(values)
                        out.append(f"insertBlock = {toml_value(body)}")
                    if anchor is not None:
                        out.append(f"{mode} = {toml_value(anchor)}")
                        _anchors.append(anchor)
                    else:
                        out.append(
                            "# WARNING: no existing data= line anywhere in the cfg to anchor to"
                        )
                    out.append("")
                i = j
        elif bi == 0 and raw_data_inserts:
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
                else:
                    # A manually added/dropped folder has no anchor syntax of
                    # its own -- only --sort-data-paths works one out. Without
                    # this, the block above is written with no before/after
                    # at all: valid TOML, but momw-configurator has nothing to
                    # place it against, and nothing here or on the console
                    # ever said so. Mirrors the warning the sorted branch
                    # already gives for the equivalent "no anchor found" case.
                    out.append(
                        "# WARNING: no anchor for this path -- pass --sort-data-paths "
                        "(or tick 'Sort data= paths too') to place it"
                    )
                out.append("")

        # 2) CONTENT INSERTS SECOND
        # The subset's plugins go in as insertBlock runs rather than one insert
        # per plugin chained on its predecessor.
        #
        # Why: `after`/`before` are matched with strings.Contains against WHOLE
        # cfg lines, and >1 match makes momw-configurator abandon the cfg it was
        # building. Chaining anchored every plugin on the *previously inserted
        # plugin name*, so each one was another chance to hit that -- and it is
        # a real shape, not a hypothetical: inserting 'Wares.esp' into a list
        # that already ships 'Better Wares.esp' matches both lines.
        #
        # A run of consecutive subset plugins is one block on one anchor, and
        # that anchor is a line the sort already placed, so it can be checked
        # (see _pick_anchor). Equivalence with the chained form is proven in
        # tests/test_toml_equivalence.py by applying both through
        # simulate_configurator_apply and diffing the resulting cfg.
        # Only in the first block, for the same reason the data inserts are:
        # the subset is global, not per source [[Customizations]] block.
        subset_runs = _subset_runs(final_content_order, subset_set_lower, replace_dest_names)
        for start, end in subset_runs if bi == 0 else ():
            names = final_content_order[start:end]
            values = [original_content_values.get(n, n) for n in names]

            # Keep the per-plugin "why is it here" annotations. They explain the
            # sort's reasoning, which is per plugin even when the insert is not.
            for name in names:
                info = (custom_anchors or {}).get(name.lower())
                if not info:
                    continue
                how, anch = info
                if how == "after":
                    out.append(f"# {name}: must load after {toml_value(anch)}")
                elif how == "before":
                    out.append(f"# {name}: must load before {toml_value(anch)}")
                elif how in ("nearstart", "nearend"):
                    label = "NearStart" if how == "nearstart" else "NearEnd"
                    out.append(f"# {name}: mlox [{label}] hint")
                else:
                    out.append(f"# {name}: no ordering constraint -- positional only")

            mode, anchor = _pick_anchor(final_content_order, start, end, haystack_for_anchors)
            out.append("[[Customizations.insert]]")
            if len(values) == 1:
                # A single plugin needs no block; `insert` is the plainer form
                # and applies identically.
                out.append(f"insert = {toml_value(values[0])}")
            else:
                body = "\n".join(values)
                out.append(f"insertBlock = {toml_value(body)}")
            if anchor is None:
                out.append("# WARNING: this is the only content= plugin -- no anchor to write")
            else:
                out.append(f"{mode} = {toml_value(anchor)}")
                _anchors.append(anchor)
            out.append("")

        for rep in block.get("replace", []):
            out.extend(_replace_notes(rep, final_content_order))
            out.append("[[Customizations.replace]]")
            if "source" in rep:
                out.append(f"source = {toml_value(rep['source'])}")
                _anchors.append(rep["source"])
            if "dest" in rep:
                out.append(f"dest = {toml_value(rep['dest'])}")
            out.append("")

        # Declared grass, as append entries. Only in the first block: repeating
        # them per block would write the line once per customization.
        if bi == 0:
            for name in new_groundcover or []:
                out.append("[[Customizations.append]]")
                out.append(f"append = {toml_value(f'groundcover={name}')}")
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
    haystack = haystack_for_anchors

    _remove_matches = configurator_remove_matches

    for a in dict.fromkeys(_anchors):  # dedupe, keep order
        hits = [line for line in haystack if a in line]
        if len(hits) > 1:
            print(
                _(
                    "WARNING: anchor '%(anchor)s' in the emitted TOML matches "
                    "%(count)d openmw.cfg lines -- momw-configurator errors on "
                    "ambiguous matches. Colliding lines: %(lines)s%(more)s"
                )
                % {
                    "anchor": a,
                    "count": len(hits),
                    "lines": "; ".join(hits[:4]),
                    "more": " ..." if len(hits) > 4 else "",
                }
            )
    for r in dict.fromkeys(_removes):
        hits = [line for line in haystack if _remove_matches(r, line)]
        if len(hits) > 1:
            print(
                _(
                    "WARNING: remove entry '%(entry)s' matches %(count)d openmw.cfg "
                    "lines -- momw-configurator removes ALL of them, silently. "
                    "Colliding lines: %(lines)s%(more)s"
                )
                % {
                    "entry": r,
                    "count": len(hits),
                    "lines": "; ".join(hits[:4]),
                    "more": " ..." if len(hits) > 4 else "",
                }
            )

    return "\n".join(out).rstrip() + "\n"
