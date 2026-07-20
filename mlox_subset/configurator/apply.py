"""Simulate momw-configurator's apply step before it runs for real.

A faithful re-implementation of ``ApplyCustomizations`` from the Configurator's
``cfg/custom.go``, so the TOML this tool emits can be dry-run against a cfg
*before* the user runs the real thing. Fidelity to the Go source matters more
than elegance here -- including its sharp edges:

* inserts match by whole-line substring; zero matches is a per-entry error,
  more than one is fatal (the Go code returns a nil cfg);
* replaces match the same way, but more than one match skips the entry;
* removals are silent when they match nothing.

Reproducing those behaviours is the point. A "better" implementation that
resolved ambiguity differently would make the preview a lie.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence

from mlox_subset.configurator.cfglines import (
    cfg_line_value,
    normalize_data_path,
)

#: ``remove*`` keys the Configurator understands. Each takes an array of
#: whole-line substrings; see :func:`customization_string_list` for why the
#: array-ness is enforced rather than assumed.
REMOVE_KEYS: tuple[str, ...] = (
    "removeData",
    "removeFallbackArchive",
    "removeContent",
    "removeFallback",
    "removeGroundcover",
)


def configurator_remove_matches(val: str, line: str) -> bool:
    """Whether a ``remove*`` entry matches a cfg line.

    A mirror of ``shouldRemoveLine()`` in momw-configurator's ``custom.go``.
    Path-like values -- those containing a slash -- compare against the line's
    *value* (exact, or a ``/``-suffix match for relative paths); everything
    else is a plain whole-line substring test.

    Args:
        val: The ``remove*`` entry.
        line: A cfg line.

    Returns:
        ``True`` when the Configurator would remove this line.
    """
    if "/" in val or "\\" in val:
        lv = cfg_line_value(line)
        if lv is None:
            return False
        lvn = lv.replace("\\", "/").strip()
        vn = val.replace("\\", "/").strip()
        is_abs = vn.startswith("/") or (len(vn) >= 3 and vn[1] == ":" and vn[2] == "/")
        return lvn == vn or (not is_abs and lvn.endswith("/" + vn))
    return val in line


def customization_string_list(
    customization: dict, key: str, errors: list[str] | None = None
) -> list[str]:
    r"""Return a ``remove*`` customization value as a list of strings.

    TOML lets a user write ``removeContent = \'X.esp\'`` (a string) where an
    array was meant. Iterating that string would yield single *characters* as
    removal patterns and silently delete most of the cfg, so anything that is
    not a list is rejected -- the real Go Configurator cannot unmarshal a
    string into ``[]string`` either.

    Args:
        customization: One ``[[Customizations]]`` table.
        key: The ``remove*`` key to read.
        errors: Optional list that rejection messages are appended to.

    Returns:
        The string entries, or an empty list if the value is absent or the
        wrong type.
    """
    value = customization.get(key)
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        if errors is not None:
            errors.append(
                f"{key} must be an array of strings, got {type(value).__name__} "
                f"({value!r}) -- write it as {key} = ['{value}'] -- entry ignored"
            )
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        elif errors is not None:
            errors.append(f"{key} entry {item!r} is not a string -- ignored")
    return out


def simulate_configurator_apply(
    cfg_lines: Sequence[str],
    toml_text: str,
    list_name: str | None = None,
) -> tuple[list[str] | None, list[str], list[str]]:
    """Dry-run momw-configurator's apply step against a cfg.

    A faithful re-implementation of ``ApplyCustomizations`` from the
    Configurator's ``cfg/custom.go``, so the emitted TOML can be previewed
    *before* anyone runs the real thing. Mirrors, per the Go source:

      * insert: after/before matched by whole-line substring; 0 matches ->
        error per insert; >1 matches -> FATAL (the Go code returns a nil cfg);
        after -> target_idx+1, before -> target_idx; prefix copied from the
        matched line; insertBlock lines inserted sequentially.
      * replace: whole-line substring; >1 matches -> error, entry skipped;
        data lines get quoted values.
      * remove: shouldRemoveLine semantics (see configurator_remove_matches);
        EVERY matching line is removed, silently.
      * append: groundcover= lines routed to the groundcover section (after
        the last groundcover= line, or a new section), the rest to an
        '# APPENDED LINES #' section at the end.
      * apply order per block: inserts, replaces, removes, appends.

    Template vars ({{.ModBaseDir}}) and $ENV vars are NOT expanded -- a note
    is returned instead, since the preview can't know the Configurator's
    config.

    Args:
        cfg_lines: The cfg to apply against, in file order.
        toml_text: The customisations TOML to simulate.
        list_name: Restrict to customisations for this curated list. ``None``
            applies every block in the file.

    Returns:
        ``(new_lines, errors, notes)``. ``new_lines`` is ``None`` when the run
        would abort outright -- an ambiguous insert anchor, which the Go code
        treats as fatal by returning a nil cfg. Errors are per-entry problems;
        notes are advisory, such as an unexpanded template.
    """
    try:
        import tomllib as _toml
    except ModuleNotFoundError:
        try:
            import tomli as _toml
        except ModuleNotFoundError:
            return (
                None,
                [],
                ["preview skipped: needs Python 3.11+ (tomllib) or 'pip install tomli'"],
            )
    try:
        data = _toml.loads(toml_text)
    except Exception as e:
        return None, [f"emitted TOML failed to parse: {e}"], []

    lines = list(cfg_lines)
    errs, notes = [], []

    def _check_templates(v: str) -> None:
        """Note any unexpanded template or environment variable.

        The real Configurator expands these; the preview cannot, so the
        difference is reported rather than silently shown as literal text.
        """
        if "{{" in v or "$" in v:
            notes.append(f"'{v}': template/env var left unexpanded in the preview")

    for cust in data.get("Customizations") or []:
        if not isinstance(cust, dict):
            errs.append(f"[[Customizations]] entry is not a table: {cust!r} -- ignored")
            continue
        if list_name and cust.get("listName") and cust.get("listName") != list_name:
            continue

        # 1) inserts
        for st in cust.get("insert") or []:
            insert, iblock = st.get("insert"), st.get("insertBlock")
            after, before = st.get("after"), st.get("before")
            target = after if after is not None else before
            if (insert is None and iblock is None) or target is None:
                errs.append("insert entry needs insert/insertBlock plus after or before")
                continue
            if after is not None and before is not None:
                errs.append("after and before cannot be used together")
                continue
            matches = [i for i, line in enumerate(lines) if target in line]
            if not matches:
                errs.append(f"target line is not present in openmw.cfg: {target}")
                continue
            if len(matches) > 1:
                shown = matches[:5]
                detail = "; ".join(f"line {i + 1}: {lines[i]!r}" for i in shown)
                if len(matches) > len(shown):
                    detail += f"; ... and {len(matches) - len(shown)} more"
                errs.append(
                    f"FATAL: multiple matches for anchor '{target}' -- the real "
                    f"Configurator abandons the cfg here. Matching lines -> {detail}"
                )
                return None, errs, notes
            idx = matches[0]
            prefix = lines[idx].split("=")[0]
            dest = idx + 1 if after is not None else idx
            vals = (
                [insert]
                if insert is not None
                else [line.replace("\r", "") for line in iblock.split("\n") if line]
            )
            for v in vals:
                _check_templates(v)
                lines.insert(dest, f"{prefix}={v}")
                dest += 1

        # 2) replaces
        for st in cust.get("replace") or []:
            src, dst = st.get("source"), st.get("dest")
            if src is None or dst is None:
                errs.append("replace entry needs source and dest")
                continue
            matches = [i for i, line in enumerate(lines) if src in line]
            if len(matches) > 1:
                shown = matches[:5]
                detail = "; ".join(f"line {i + 1}: {lines[i]!r}" for i in shown)
                if len(matches) > len(shown):
                    detail += f"; ... and {len(matches) - len(shown)} more"
                errs.append(
                    f"replace source '{src}' matches more than one line -- skipped. "
                    f"Matching lines -> {detail}"
                )
                continue
            if matches:
                idx = matches[0]
                prefix = lines[idx].split("=")[0]
                _check_templates(dst)
                lines[idx] = f'{prefix}="{dst}"' if prefix == "data" else f"{prefix}={dst}"

        # 3) removes (every match, silently)
        rm = []
        for key in REMOVE_KEYS:
            rm += customization_string_list(cust, key, errs)
        if rm:
            lines = [
                line for line in lines if not any(configurator_remove_matches(v, line) for v in rm)
            ]

        # 4) appends
        gc: list[str] = []
        other: list[str] = []
        for st in cust.get("append") or []:
            vals = (
                [st["append"]]
                if "append" in st
                else [
                    line.replace("\r", "") for line in st.get("appendBlock", "").split("\n") if line
                ]
            )
            for v in vals:
                _check_templates(v)
                (gc if v.startswith("groundcover=") else other).append(v)
        if gc:
            last = max(
                (i for i, line in enumerate(lines) if line.startswith("groundcover=")), default=-1
            )
            if last >= 0:
                for j, v in enumerate(gc):
                    lines.insert(last + 1 + j, v)
            else:
                lines += [
                    "",
                    "#                   #",
                    "# GROUNDCOVER FILES #",
                    "#                   #",
                    *gc,
                ]
        if other:
            lines += ["", "#                #", "# APPENDED LINES #", "#                #", *other]

    return lines, errs, notes


def preview_configurator_result(
    plan_lines: Sequence[str],
    toml_text: str,
    expected_content_order: Sequence[str],
    subset_names: Collection[str],
    user_data_norms: Collection[str] | None = None,
    list_name: str | None = None,
) -> tuple[bool, list[str]]:
    """Dry-run the emitted TOML and verify the round trip.

    The real Configurator applies customizations to a FRESH curated cfg (no
    customs in it yet), so the simulation base is the current cfg with this
    run's custom content= lines and custom data= paths stripped out.

    Args:
        plan_lines: The planned cfg lines for this run.
        toml_text: The customisations TOML to dry-run.
        expected_content_order: The content order the sort produced, which the
            simulation must reproduce.
        subset_names: The user's own plugins, stripped from the simulation base
            so it resembles the fresh curated cfg the Configurator sees.
        user_data_norms: Normalised ``data=`` paths from this run, likewise
            stripped from the base.
        list_name: Restrict to customisations for this curated list.

    Returns:
        ``(ok, report_lines)``. ``ok`` is ``True`` when the simulated content= order
    exactly matches what the sort computed (accounting for removals).
    """
    subset_lower = {str(s).lower() for s in subset_names or ()}
    user_norms = {n for n in (user_data_norms or ()) if n}
    base = []
    for line in plan_lines:
        m = re.match(r"^\s*content\s*=\s*(.+?)\s*$", line, re.IGNORECASE)
        if m and m.group(1).lower() in subset_lower:
            continue
        m = re.match(r"^\s*data\s*=\s*(.+?)\s*$", line, re.IGNORECASE)
        if m and normalize_data_path(m.group(1).strip().strip('"')) in user_norms:
            continue
        base.append(line)

    report: list[str] = []
    sim, errs, notes = simulate_configurator_apply(base, toml_text, list_name=list_name)
    report.extend(f"  NOTE: {n}" for n in notes)
    report.extend(f"  WARNING: {e}" for e in errs)
    if sim is None:
        report.append("  PREVIEW ABORTED -- the real Configurator run would fail the same way.")
        return False, report

    sim_content = [
        m.group(1)
        for line in sim
        for m in [re.match(r"^\s*content\s*=\s*(.+?)\s*$", line, re.IGNORECASE)]
        if m
    ]
    # expected: the computed order, minus anything the TOML's removes catch
    try:
        import tomllib as _toml
    except ModuleNotFoundError:
        import tomli as _toml
    data = _toml.loads(toml_text)
    rm = []
    for cust in data.get("Customizations") or []:
        for key in REMOVE_KEYS:
            rm += customization_string_list(cust, key)
    expected = [
        n
        for n in expected_content_order
        if not any(configurator_remove_matches(v, f"content={n}") for v in rm)
    ]

    if sim_content == expected:
        report.append(
            f"  VERIFIED: simulated apply reproduces the sorted order exactly "
            f"({len(sim_content)} content= lines)."
        )
        return True, report
    report.append("  MISMATCH: the simulated Configurator result differs from the sorted order!")
    for i, (a, b) in enumerate(zip(sim_content, expected)):
        if a != b:
            report.append(f"    first difference at #{i}: simulated '{a}' vs expected '{b}'")
            break
    if len(sim_content) != len(expected):
        report.append(
            f"    lengths differ: simulated {len(sim_content)} vs expected {len(expected)}"
        )
    return False, report
