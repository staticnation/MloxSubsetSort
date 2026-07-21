"""Evaluate the mlox predicate language against a load order.

The back half of the pipeline that :mod:`mlox_subset.rules.expressions` starts:
given an AST, decide whether it holds for the plugins actually present, and
turn ``[Requires]``/``[Conflict]``/``[Note]`` blocks into warnings.

Unlike the front half, this needs to read real plugin files -- ``[VER]`` and
``[DESC]`` test a plugin's header, ``[SIZE]`` its byte length. It therefore
depends on :mod:`mlox_subset.plugins`, which is why that package had to be
extracted first: while the plugin layer still lived in the engine module, this
code could not move without importing the engine back into ``rules/``.

Throughout, an unreadable or unreachable plugin means *unknown*, not *absent*.
mlox declines to raise a warning it cannot substantiate, and so does this --
see :class:`~mlox_subset.plugins.PluginFileIndex`.

Pinned by ``tests/test_differential.py``, which drives all 2,964 predicate
bodies in the real rule files through tokenise -> parse -> evaluate.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Final

from mlox_subset.plugins import PluginFileIndex, plugin_version, read_plugin_description
from mlox_subset.rules.expressions import (
    describe_node,
    parse_mlox_lisp,
    tokenize_mlox_logic,
)
from mlox_subset.rules.parser import TOP_RE, strip_comment
from mlox_subset.rules.patterns import mlox_pattern_to_regex
from mlox_subset.versions import MLOX_VERSION_PATTERN, format_version

# Atomic function forms, matched against a single whole token from the
# tokeniser. Kept with the evaluator because only it dispatches on them.
_re_ver_fun: Final = re.compile(
    r"^\[\s*VER\s*([=<>])\s*" + MLOX_VERSION_PATTERN + r"\s*([^\]]+?)\s*\]$",
    re.IGNORECASE,
)
_re_size_fun: Final = re.compile(
    r"^\[\s*SIZE\s*(!?)(\d+)\s+(\S.*?\.(?:es[mp]|omwaddon|omwgame|omwscripts)\b)\s*\]$",
    re.IGNORECASE,
)
_re_desc_fun: Final = re.compile(r"^\[\s*DESC\s*(!?)/([^/]+)/\s+([^\]]+?)\s*\]$", re.IGNORECASE)
_re_mwselua_fun: Final = re.compile(
    r"^\[\s*MWSE-LUA\s*(!?)/([^/]+)/\s+([^\]]+?)\s*\]$", re.IGNORECASE
)

# Relocated bodies reference these under their original engine-module names.
_format_version = format_version
_plugin_version = plugin_version
_read_plugin_description = read_plugin_description


def _eval_ver(
    op: str,
    want_raw: str,
    plugin_pat: str,
    active_plugins: Collection[str],
    index: PluginFileIndex | None,
) -> bool:
    """Evaluate a ``[VER op version Plugin.esp]`` predicate.

    When a matched plugin's version cannot be determined, an ``=`` comparison
    is treated as holding -- mlox's behaviour, and deliberate: the tool refuses
    to raise a version warning it cannot substantiate.

    Args:
        op: One of ``=``, ``<``, ``>``.
        want_raw: The version from the rule, before canonicalisation.
        plugin_pat: The plugin pattern the rule is about.
        active_plugins: Lowercased names of plugins in the load order.
        index: Used to read real versions, or ``None`` when unavailable.

    Returns:
        ``True`` when any matched active plugin satisfies the comparison.
        ``False`` when the rule names no active plugin at all.
    """
    want = _format_version(want_raw)
    rx = mlox_pattern_to_regex(plugin_pat)
    matched = [p for p in active_plugins if rx.match(p)]
    if not matched:
        return False  # the plugin the rule is about isn't even active
    for p in matched:
        pv = _plugin_version(p, index)
        if pv is None:
            if op == "=":
                return True  # mlox: version unknowable -> assume '=' holds
            continue
        if op == "=" and pv == want:
            return True
        if op == "<" and pv < want:
            return True
        if op == ">" and pv > want:
            return True
    return False


def _eval_size(
    bang: str,
    want_size: int,
    plugin_pat: str,
    active_plugins: Collection[str],
    index: PluginFileIndex | None,
) -> bool:
    """Evaluate a ``[SIZE bytes Plugin.esp]`` predicate.

    Args:
        bang: ``"!"`` to negate the comparison, otherwise empty.
        want_size: Expected file size in bytes.
        plugin_pat: The plugin pattern the rule is about.
        active_plugins: Lowercased names of plugins in the load order.
        index: Used to locate the file, or ``None`` when unavailable -- in
            which case the predicate declines to fire rather than guessing.

    Returns:
        ``True`` when a matched plugin's size satisfies the comparison.
    """
    rx = mlox_pattern_to_regex(plugin_pat)
    matched = [p for p in active_plugins if rx.match(p)]
    if not matched:
        return False
    for p in matched:
        path = index.find(p) if index else None
        if path is None:
            # mlox gates this on `self.datadir is None` -- NO data directory at
            # all, where the test degrades to mere file existence. It does not
            # apply when the directories are readable and this particular
            # plugin simply is not in them. Conflating the two made every
            # [SIZE] predicate about a missing plugin assert a size match.
            if index is None or not index.usable:
                return True  # no datadir: err on the side of existence, as mlox does
            continue  # dirs readable, plugin absent -- cannot substantiate; try the next
        try:
            actual = path.stat().st_size
        except OSError:
            return True  # unreadable file: same "cannot verify" case as no datadir
        b = actual == want_size
        if bang == "!":
            b = not b
        if b:
            return True
    return False


def _eval_desc(
    bang: str,
    pat: str,
    plugin_pat: str,
    active_plugins: Collection[str],
    index: PluginFileIndex | None,
) -> bool:
    """Evaluate a ``[DESC /regex/ Plugin.esp]`` predicate.

    Args:
        bang: ``"!"`` to negate the match, otherwise empty.
        pat: The regex, without its surrounding slashes.
        plugin_pat: The plugin pattern the rule is about.
        active_plugins: Lowercased names of plugins in the load order.
        index: Used to read the plugin header, or ``None`` when unavailable.

    Returns:
        ``True`` when a matched plugin's header description satisfies the test.
    """
    rx = mlox_pattern_to_regex(plugin_pat)
    matched = [p for p in active_plugins if rx.match(p)]
    if not matched:
        return False
    for p in matched:
        path = index.find(p) if index else None
        if path is None:
            # Same distinction as _eval_size: "no datadir" is not the same as
            # "this plugin is not on disk".
            if index is None or not index.usable:
                return True  # no datadir: assume true, as mlox does
            continue  # dirs readable, plugin absent -- nothing to match against
        desc = _read_plugin_description(path)
        try:
            b = re.search(pat, desc) is not None
        except re.error:
            b = False
        if bang == "!":
            b = not b
        if b:
            return True
    return False


def _eval_func_token(
    token: str, active_plugins: Collection[str], index: PluginFileIndex | None
) -> bool:
    """Evaluate one atomic ``[VER]``/``[SIZE]``/``[DESC]``/``[MWSE-LUA]`` token.

    Args:
        token: The whole function form, brackets included.
        active_plugins: Lowercased names of plugins in the load order.
        index: Used to read plugin metadata, or ``None`` when unavailable.

    Returns:
        ``True`` when the predicate holds. An unrecognised token yields
        ``False`` rather than raising -- rule databases are community-authored
        and may use forms this tool does not model.
    """
    m = _re_ver_fun.match(token)
    if m:
        return _eval_ver(m.group(1), m.group(2), m.group(3).strip(), active_plugins, index)
    m = _re_size_fun.match(token)
    if m:
        return _eval_size(m.group(1), int(m.group(2)), m.group(3).strip(), active_plugins, index)
    m = _re_desc_fun.match(token)
    if m:
        return _eval_desc(m.group(1), m.group(2), m.group(3).strip(), active_plugins, index)
    if _re_mwselua_fun.match(token):
        return False  # MWSE-Lua content doesn't exist under OpenMW
    return False  # unrecognized bracketed token -> can't assert it holds


def _func_token_matches(token: str, active_plugins: Collection[str]) -> set[str]:
    """The active plugins a function token's inner pattern names.

    Used to attribute a warning to specific plugins, so the message can say
    which mod triggered it rather than only quoting the rule.

    Args:
        token: The whole function form, brackets included.
        active_plugins: Lowercased names of plugins in the load order.

    Returns:
        Matching plugin names; empty for a token this tool does not model.
    """
    for rx in (_re_ver_fun, _re_size_fun, _re_desc_fun, _re_mwselua_fun):
        m = rx.match(token)
        if m:
            prx = mlox_pattern_to_regex(m.group(3).strip())
            return {p for p in active_plugins if prx.match(p)}
    return set()


def evaluate_node(
    node: object,
    active_plugins: Collection[str],
    index: PluginFileIndex | None = None,
) -> bool:
    """Evaluate one AST node against the active plugins.

    Handles a plugin pattern, an ``ALL``/``ANY``/``NOT``/``DESC`` group, or an
    atomic function token.

    Args:
        node: A node from :func:`~mlox_subset.rules.expressions.parse_mlox_lisp`.
        active_plugins: Lowercased names of plugins in the load order.
        index: Lets the function predicates read real plugin metadata.
            ``None`` falls back to mlox's conservative behaviour rather than
            guessing.

    Returns:
        Whether the node holds for this load order.
    """
    if isinstance(node, str):
        if node.startswith("["):  # atomic [VER]/[SIZE]/[DESC]/[MWSE-LUA] token
            return _eval_func_token(node, active_plugins, index)
        if node.startswith("/") and node.endswith("/"):
            return True  # /DESC message/ strings carry no truth value
        rx = mlox_pattern_to_regex(node)
        return any(rx.match(p) for p in active_plugins)

    if isinstance(node, list) and node:
        op = node[0].upper() if isinstance(node[0], str) else ""
        if op == "ALL":
            return all(evaluate_node(arg, active_plugins, index) for arg in node[1:])
        if op == "ANY":
            return any(evaluate_node(arg, active_plugins, index) for arg in node[1:])
        if op == "NOT":
            return len(node) >= 2 and not evaluate_node(node[1], active_plugins, index)
        if op == "DESC":
            return evaluate_node(node[-1], active_plugins, index)
        # a flat list with no leading operator implies ANY in mlox
        return any(evaluate_node(arg, active_plugins, index) for arg in node)
    return False


def get_triggered_plugins(
    node: object,
    active_plugins: Collection[str],
    index: PluginFileIndex | None = None,
) -> set[str]:
    """The active plugins that actually matched inside an AST node.

    Used so a printed warning can name the plugins responsible rather than
    only quoting the rule that fired.

    Args:
        node: A node from :func:`~mlox_subset.rules.expressions.parse_mlox_lisp`.
        active_plugins: Lowercased names of plugins in the load order.
        index: Lets the function predicates read real plugin metadata.

    Returns:
        Matching plugin names. ``/message/`` strings match nothing and
        contribute an empty set.
    """
    found: set[str] = set()
    if isinstance(node, str):
        if node.startswith("["):  # atomic function token
            return _func_token_matches(node, active_plugins)
        if node.startswith("/") and node.endswith("/"):
            return found  # /DESC message/ strings match no plugin
        rx = mlox_pattern_to_regex(node)
        for p in active_plugins:
            if rx.match(p):
                found.add(p)
    elif isinstance(node, list) and node:
        for arg in node[1:]:
            found.update(get_triggered_plugins(arg, active_plugins, index))
    return found


def check_predicates(
    rules_text: str,
    final_order: Sequence[str],
    subset_origins: Mapping[str, str] | None = None,
    # Sequence, not list: `list` is invariant, so the list[str] every caller
    # builds is not a list[str | Path]. Same defect as PluginFileIndex had.
    data_dirs: Sequence[str | Path] | None = None,
) -> list[str]:
    """Evaluate every predicate block against the sorted load order.

    Extracts ``[Conflict]``, ``[Requires]`` and ``[Note]`` blocks and reports
    the ones that fire.

    Args:
        rules_text: Raw concatenated rule-file text, from
            :func:`~mlox_subset.rules.expressions.load_rules_raw_text`.
        final_order: The sorted plugin order to evaluate against.
        subset_origins: ``{plugin: origin}`` used to annotate which of the
            user's own mods a warning refers to.
        data_dirs: The cfg's ``data=`` directories, used to locate plugin
            files so ``[VER]``/``[SIZE]``/``[DESC]`` predicates can read real
            version/size/description info.

    Returns:
        Warning strings, each prefixed with its rule kind. Empty when nothing
        fires.

    Note:
        If ``data_dirs`` is omitted or unreadable, those predicates fall back to
    mlox's conservative behaviour.
    Purely read-only -- never affects sorting or what gets written.

    subset_origins (optional): {plugin_name_lower: "where this came from"},
    e.g. "customizations.toml -> 'total-overhaul'" or "subset file (foo.txt)"
    -- lets a warning say "NewMod.esp [customizations.toml -> 'total-overhaul']"
    instead of just "NewMod.esp", making it obvious which of YOUR mods (as
    opposed to something already sitting in the frozen openmw.cfg base) is
    the one to go fix. Plugins with no entry here are printed unannotated.
    """
    subset_origins = subset_origins or {}
    active_set = set(final_order)
    index = PluginFileIndex(data_dirs)
    warnings = []

    def annotate(name: str) -> str:
        """Render a plugin name with its origin, when one is known."""
        origin = subset_origins.get(name.lower())
        return f"{name} [{origin}]" if origin else name

    def annotate_all(names: Collection[str]) -> str:
        """Render several plugin names, each annotated with its origin."""
        return ", ".join(annotate(n) for n in sorted(names))

    matches = list(TOP_RE.finditer(rules_text))
    for idx, m in enumerate(matches):
        keyword = m.group(1).title()
        if keyword not in ("Conflict", "Requires", "Note"):
            continue

        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(rules_text)
        body = rules_text[start:end]

        # Split lines into "logic" (expressions) vs. message text the way the
        # real mlox does: a line that starts with WHITESPACE is message text
        # (mlox's re_message = ^\s), everything else is an expression line.
        # The first body line is the remainder of the header line itself, so
        # it's always logic. (The old heuristic classified by content --
        # "contains brackets or a plugin extension" -- which turned the
        # thousands of indented message lines in mlox_base that happen to
        # mention a plugin name into phantom logic operands, producing false
        # conflict/note warnings.)
        message_lines = []
        header_arg = (m.group(2) or "").strip()
        if header_arg:
            message_lines.append(header_arg)  # mlox: header args are the message
        logic_text = ""
        depth = 0  # unclosed brackets carried across lines
        for i, raw_line in enumerate(body.splitlines()):
            line = strip_comment(raw_line).strip()
            if not line:
                continue
            # An indented line is message text ONLY when no bracket expression
            # is open: mlox expressions like  [ALL A.esp\n\t[NOT B.esp]\n\tC.esm]
            # continue across indented lines, and treating those continuations
            # as message text truncated the condition -- e.g. the Uvirith's
            # Legacy / Children of Morrowind note fired for people without
            # Children of Morrowind because its [ALL ...] lost two conjuncts.
            if i > 0 and depth == 0 and raw_line[:1] in (" ", "\t"):
                message_lines.append(line)
            else:
                logic_text += " " + line
                depth += line.count("[") - line.count("]")
                depth = max(depth, 0)

        message = " ".join(message_lines).strip()
        ast = parse_mlox_lisp(tokenize_mlox_logic(logic_text))
        if not ast:
            continue

        if keyword == "Conflict":
            # a [Conflict] block is a flat list of mutually-exclusive
            # items/groups -- warn if more than one is simultaneously active
            true_nodes = [n for n in ast if evaluate_node(n, active_set, index)]
            if len(true_nodes) > 1:
                triggered_by = set()
                for n in true_nodes:
                    triggered_by.update(get_triggered_plugins(n, active_set, index))
                warning_msg = f"[CONFLICT] {message}"
                if triggered_by:
                    warning_msg += f"\n    Caused by: {annotate_all(triggered_by)}"
                warnings.append(warning_msg)

        elif keyword == "Requires":
            # first item is the "target", the rest are its dependencies
            if len(ast) >= 2 and evaluate_node(ast[0], active_set, index):
                target_names = get_triggered_plugins(ast[0], active_set, index)
                missing = [n for n in ast[1:] if not evaluate_node(n, active_set, index)]
                if missing:
                    warning_msg = f"[REQUIRES] {message}"
                    if target_names:
                        warning_msg += f"\n    Needed by: {annotate_all(target_names)}"
                    warning_msg += f"\n    Missing: {', '.join(describe_node(n) for n in missing)}"
                    warnings.append(warning_msg)

        elif keyword == "Note":
            # notes fire when everything listed is simultaneously true
            if all(evaluate_node(n, active_set, index) for n in ast):
                triggered_by = set()
                for n in ast:
                    triggered_by.update(get_triggered_plugins(n, active_set, index))
                warning_msg = f"[NOTE] {message}"
                if triggered_by:
                    warning_msg += f"\n    About: {annotate_all(triggered_by)}"
                warnings.append(warning_msg)

    return warnings
