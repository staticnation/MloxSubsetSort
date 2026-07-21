"""Tokenise and parse the mlox predicate language.

``[Requires]``, ``[Conflict]`` and ``[Note]`` rule bodies are written in a
small lisp-like language: bracketed ``ALL``/``ANY``/``NOT`` groups over plugin
patterns, plus atomic function forms that test a plugin's version, file size or
header description.

This module covers the front half of that pipeline -- text to tokens to AST,
and rendering an AST back to readable text. It is deliberately free of any
dependency on plugin files: evaluating an AST needs to read real plugin
metadata, so that half lives with the code that can do so.

The tokeniser's one subtlety is worth stating plainly, because it is easy to
"simplify" and break: ``DESC`` and ``MWSE-LUA`` forms carry a ``/regex/`` that
may itself contain ``]`` (``[DESC /[Tt]ribunal/ Foo.esp]``). Those forms must
therefore consume the ``/.../`` part explicitly *before* looking for the
closing bracket. Naive bracket matching splits the token in the middle of the
regex and produces nonsense. ``VER`` and ``SIZE`` bodies cannot contain
brackets, so they are matched more simply.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

#: The atomic function forms, captured whole so each becomes a single leaf node
#: the evaluator can dispatch on rather than being split into ``[`` + junk.
_FUNC_FORMS: Final = (
    r"\[\s*VER\b[^\]\n]*\]|"
    r"\[\s*SIZE\b[^\]\n]*\]|"
    r"\[\s*DESC\s*!?\s*/[^/\n]*/[^\]\n]*\]|"
    r"\[\s*MWSE-LUA\s*!?\s*/[^/\n]*/[^\]\n]*\]"
)

#: Full token pattern: function forms first (so they win over bare brackets),
#: then structural brackets, logic keywords, ``/message/`` strings, and plugin
#: filenames with optional wildcards.
_TOKEN_RE: Final = re.compile(
    _FUNC_FORMS + r"|\[|\]|\bALL\b|\bANY\b|\bNOT\b|/[^/]+/|"
    r"[^\[\]\n]+?\.(?:esp|esm|omwaddon|omwgame|omwscripts)\*?",
    re.IGNORECASE,
)

#: Logic operators recognised at the head of a group.
_OPERATORS: Final = frozenset({"ALL", "ANY", "NOT", "DESC"})


def tokenize_mlox_logic(text: str) -> list[str]:
    """Split a predicate body into tokens.

    Args:
        text: The body of a ``[Requires]``/``[Conflict]``/``[Note]`` block.

    Returns:
        Tokens in source order: brackets, logic keywords, ``/message/``
        strings, plugin patterns, and whole atomic function forms. Empty
        tokens are dropped.
    """
    return [token.strip() for token in _TOKEN_RE.findall(text) if token.strip()]


def parse_mlox_lisp(tokens: list[str]) -> list:
    """Build a nested-list AST from bracketed tokens.

    Warning:
        This **consumes** ``tokens`` via ``pop(0)`` -- recursion relies on the
        shared list being drained. Callers that need the tokens afterwards must
        pass a copy.

    Args:
        tokens: Output of :func:`tokenize_mlox_logic`.

    Returns:
        A nested list AST. Unbalanced input degrades gracefully rather than
        raising: a missing ``]`` simply ends the current group at end of input.
    """
    if not tokens:
        return []
    node: list = []
    while tokens:
        token = tokens.pop(0)
        if token == "[":  # noqa: S105 - a parser token, not a secret
            node.append(parse_mlox_lisp(tokens))
        elif token == "]":  # noqa: S105 - a parser token, not a secret
            return node
        else:
            node.append(token)
    return node


def describe_node(node: object) -> str:
    """Render an AST node back to a short human-readable string.

    Used to name a *missing* dependency in a ``[Requires]`` warning: a plugin
    that is not installed cannot be looked up in the active set to recover its
    real name, so the rule's own text is the only thing left to show.

    A group with no explicit operator is described as ``ANY``, matching how the
    evaluator treats it.

    Args:
        node: A string leaf or a nested list from :func:`parse_mlox_lisp`.

    Returns:
        A readable rendering, or ``"?"`` for anything unrecognised.
    """
    if isinstance(node, str):
        return node
    if isinstance(node, list) and node:
        if isinstance(node[0], str) and node[0].upper() in _OPERATORS:
            operator, rest = node[0].upper(), node[1:]
        else:
            operator, rest = "ANY", node
        return f"{operator}({', '.join(describe_node(item) for item in rest)})"
    return "?"


def load_rules_raw_text(rule_paths: Sequence[str | Path]) -> str:
    """Concatenate the raw text of every rule file.

    The predicate evaluator needs the *original* bodies -- uncommented and
    unsplit -- so it can see ``DESC`` message text that the ordering parser
    strips. File discovery matches :func:`~mlox_subset.rules.parser.load_rule_blocks`
    so both see the same files in the same order.

    Args:
        rule_paths: Rule files or directories of ``*.txt`` rule files.

    Returns:
        Every file's text joined by newlines. Unreadable files are reported on
        stderr and skipped rather than aborting the run.
    """
    chunks: list[str] = []
    for raw_path in rule_paths:
        path = Path(raw_path)
        files = [path] if path.is_file() else sorted(path.glob("*.txt"))
        for rule_file in files:
            try:
                chunks.append(rule_file.read_text(encoding="utf-8-sig", errors="replace"))
            except Exception as exc:  # noqa: PERF203, BLE001
                # Deliberately broad, and deliberately inside the loop: rule
                # files are untrusted downloads, and isolating each file is the
                # point -- hoisting the try out would let one bad file discard
                # every file after it.
                print(
                    f"WARNING: could not read rule file {rule_file} for predicate "
                    f"checks: {exc}",
                    file=sys.stderr,
                )
    return "\n".join(chunks)
