"""Verify ``%(name)s`` keys in marked strings match their ``% {...}`` dicts.

A mistyped placeholder key is a **runtime** ``KeyError``:

    _("Loaded %(count)d files") % {"cont": n}      # KeyError: 'count'

Nothing else catches this. The CLI's print sites are only partially asserted by
the suite, and the GUI has no automated coverage at all -- a bad key there
surfaces only when a user clicks the thing. This converts that whole class of
error from "user finds it" to "linter finds it" (CODE_REVIEW.md §17).

For every ``_("...") % {...}`` and ``ngettext("...", "...", n) % {...}`` it
checks, in both directions:

* **missing key** -- a ``%(name)s`` in the format string with no matching key
  in the dict literal (the ``KeyError`` case);
* **unused key** -- a dict key no placeholder consumes (harmless at runtime,
  but almost always a typo'd twin of a missing key);
* **positional placeholder** -- bare ``%s``/``%d`` in a marked string.
  Translators reorder words, and positional conversions cannot be reordered;
  ``locale/README.md`` requires named placeholders, so this enforces it.

Like ``check_undefined.py`` it is deliberately conservative: only the direct
``<marker call> % <dict literal>`` pattern is checked. A dict built in a
variable first cannot be verified statically and is reported as unverifiable
rather than guessed at.

Usage:
    python tools/check_placeholders.py                  # scan the shipped sources
    python tools/check_placeholders.py some_file.py     # scan specific files

Exits non-zero if anything is reported.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

#: Call names treated as single-string markers (matches tools/make_pot.py).
GETTEXT_NAMES = frozenset({"_", "gettext"})

#: Call names treated as (singular, plural) markers.
NGETTEXT_NAMES = frozenset({"ngettext"})

#: Sources scanned by default, relative to the project root.
DEFAULT_TARGETS = ("mlox_subset", "mlox_subset_sort.py", "mlox_subset_sort_gui.py")

#: Never scanned: i18n.py *implements* gettext; its internal delegating calls
#: are not markers (same exclusion, for the same reason, as make_pot.py).
EXCLUDED = frozenset({"mlox_subset/i18n.py"})

#: One printf-style named conversion: ``%(count)d``, ``%(name)s``, ...
_NAMED = re.compile(r"%\((?P<key>[^)]*)\)[#0\- +]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[diouxXeEfFgGcrsa]")

#: A positional conversion -- ``%s``, ``%03d`` -- but not ``%%`` and not ``%(``.
_POSITIONAL = re.compile(r"%(?![%(])[#0\- +]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[diouxXeEfFgGcrsa]")


def placeholder_keys(text: str) -> set[str]:
    """Return every ``%(name)X`` key used in a printf-style format string.

    Args:
        text: The format string as written in source.

    Returns:
        The set of named-placeholder keys; empty when the string has none.
    """
    return {m.group("key") for m in _NAMED.finditer(text.replace("%%", ""))}


def positional_placeholders(text: str) -> list[str]:
    """Return every positional (unnamed) conversion in ``text``.

    Args:
        text: The format string as written in source.

    Returns:
        The literal matched conversions (``["%s", "%d"]``), in order.
    """
    # `%%` is an escaped literal percent, not a conversion; stripping the
    # pairs first stops the scan resuming on the second `%` of a pair
    # ("100%% done" is prose, not a `% d` space-flag conversion).
    return [m.group(0) for m in _POSITIONAL.finditer(text.replace("%%", ""))]


def _marker_strings(call: ast.Call) -> list[str] | None:
    """Return the literal format string(s) of a marker call, or ``None``.

    ``_("x")`` yields one string; ``ngettext("x", "xs", n)`` yields two. A
    non-literal argument yields ``None`` -- make_pot.py already warns about
    those, so re-reporting them here would be noise.

    Args:
        call: The call node on the left of a ``%`` operator.

    Returns:
        The format strings to check, or ``None`` when they cannot be read.
    """
    func = call.func
    name = (
        func.id
        if isinstance(func, ast.Name)
        else (func.attr if isinstance(func, ast.Attribute) else None)
    )
    if name in GETTEXT_NAMES and call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return [first.value]
        return None
    if name in NGETTEXT_NAMES and len(call.args) >= 2:
        strings: list[str] = []
        for arg in call.args[:2]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                strings.append(arg.value)
            else:
                return None
        return strings
    return None


def _dict_keys(node: ast.expr) -> set[str] | None:
    """Return the literal string keys of a dict display, or ``None``.

    Args:
        node: The right operand of the ``%`` operator.

    Returns:
        The keys when every one is a plain string literal; ``None`` when the
        operand is not a dict literal or uses computed keys (``**spread``,
        a variable key), which cannot be verified statically.
    """
    if not isinstance(node, ast.Dict):
        return None
    keys: set[str] = set()
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
        else:
            return None
    return keys


def check_file(path: Path, root: Path) -> list[str]:
    """Report every placeholder mismatch in one source file.

    Args:
        path: The ``.py`` file to parse.
        root: Project root, used for relative paths in the report.

    Returns:
        Human-readable findings, empty when the file is consistent.

    Raises:
        OSError: If the file cannot be read.
    """
    findings: list[str] = []
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{rel}: could not parse ({exc})"]

    for node in ast.walk(tree):
        # Positional placeholders are wrong in ANY marked string, formatted or
        # not, so this check runs on every marker call, not just `%` uses.
        if isinstance(node, ast.Call):
            strings = _marker_strings(node)
            if strings:
                for text in strings:
                    findings.extend(
                        f"{rel}:{node.lineno}: positional {conv!r} in marked string; "
                        f"use a named %(key){conv[-1]} -- translators reorder words"
                        for conv in positional_placeholders(text)
                    )
            continue
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)):
            continue
        if not isinstance(node.left, ast.Call):
            continue
        strings = _marker_strings(node.left)
        if strings is None:
            continue
        keys = _dict_keys(node.right)
        if keys is None:
            findings.append(
                f"{rel}:{node.lineno}: cannot verify -- the right side of % is not a "
                f"dict literal with plain string keys"
            )
            continue
        used: set[str] = set()
        for text in strings:
            wanted = placeholder_keys(text)
            used |= wanted
            findings.extend(
                f"{rel}:{node.lineno}: missing key {missing!r} -- named in the "
                f"format string but absent from the dict (runtime KeyError)"
                for missing in sorted(wanted - keys)
            )
        # For ngettext, a key is "used" if EITHER form consumes it: plural
        # forms legitimately drop the count in some languages.
        findings.extend(
            f"{rel}:{node.lineno}: unused key {unused!r} -- present in the dict "
            f"but no placeholder consumes it (usually a typo'd twin)"
            for unused in sorted(keys - used)
        )
    return findings


def _iter_sources(targets: list[Path]) -> list[Path]:
    """Expand files and directories into a sorted list of ``.py`` files."""
    found: set[Path] = set()
    for target in targets:
        if target.is_dir():
            found.update(p for p in target.rglob("*.py") if "__pycache__" not in p.parts)
        elif target.is_file():
            found.add(target)
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    """Check the named files, or the shipped sources by default.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit status: 0 when consistent, 1 when anything is reported.
    """
    args = sys.argv[1:] if argv is None else argv
    root = Path(__file__).resolve().parent.parent
    targets = [Path(a) for a in args] if args else [root / t for t in DEFAULT_TARGETS]
    sources = _iter_sources(targets)
    if not sources:
        print("no Python sources found to scan", file=sys.stderr)
        return 1

    failed = False
    checked = 0
    for source in sources:
        try:
            if source.relative_to(root).as_posix() in EXCLUDED:
                continue
        except ValueError:
            pass  # outside the project root; scan it as given
        checked += 1
        findings = check_file(source, root)
        if findings:
            failed = True
            print("\n".join(findings))
    if not failed:
        print(f"placeholders ok in {checked} file(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
