"""Extract translatable strings into ``locale/mlox_subset_sort.pot``.

The `locale/README.md` recipe uses GNU ``xgettext``, which is fine on Linux but
is not present on a stock Windows install -- and this project is developed and
built on Windows. This does the same job with nothing but the standard library,
so regenerating the template never depends on what happens to be on PATH.

It parses each file with ``ast`` rather than scanning text, which matters for
correctness in both directions: a ``_("...")`` written inside a docstring or a
comment is *not* a call and is correctly ignored, while a real call split over
several lines is still found.

Only literal strings can be extracted. ``_(variable)`` is reported as a warning
rather than silently skipped, because a marker the extractor cannot read is a
string that will never reach a translator -- exactly the failure this tool
exists to make visible.

Usage:
    python tools/make_pot.py                    # write locale/mlox_subset_sort.pot
    python tools/make_pot.py --check            # fail if the .pot is out of date
    python tools/make_pot.py -o other.pot       # write somewhere else

Exits non-zero if ``--check`` finds the template stale, or on a warning.
"""

from __future__ import annotations

import argparse
import ast
import datetime
import sys
from pathlib import Path

#: gettext domain; must match ``mlox_subset.i18n.DOMAIN``.
DOMAIN = "mlox_subset_sort"

#: Call names treated as single-string markers.
GETTEXT_NAMES = frozenset({"_", "gettext"})

#: Call names treated as (singular, plural) markers.
NGETTEXT_NAMES = frozenset({"ngettext"})

#: Sources scanned by default, relative to the project root.
DEFAULT_TARGETS = ("mlox_subset", "mlox_subset_sort.py", "mlox_subset_sort_gui.py")

#: Never scanned: this module *implements* gettext, so its internal
#: ``gettext(message)`` / ``ngettext(singular, plural, n)` calls are
#: delegations, not markers. Scanning it reports two permanent false warnings.
EXCLUDED = frozenset({"mlox_subset/i18n.py"})


class Message:
    """One translatable string and every place it appears.

    Attributes:
        singular: The English source string.
        plural: The plural form for an ``ngettext`` call, else ``None``.
        locations: ``(relative_path, lineno)`` pairs, in discovery order.
    """

    def __init__(self, singular: str, plural: str | None) -> None:
        """Create a message with no locations yet."""
        self.singular = singular
        self.plural = plural
        self.locations: list[tuple[str, int]] = []


def _literal(node: ast.expr) -> str | None:
    """Return the value of a string-literal node, or ``None`` if it isn't one.

    Args:
        node: Any expression node from a call's argument list.

    Returns:
        The string value, or ``None`` when the argument is not a plain literal
        (a variable, an f-string, a concatenation of names, ...).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.Call) -> str | None:
    """Return the bare function name of a call, ignoring any attribute path.

    ``_("x")`` gives ``"_"``; ``i18n.gettext("x")`` gives ``"gettext"``.

    Args:
        node: The call node to inspect.

    Returns:
        The function's name, or ``None`` for calls we cannot name.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def extract_file(
    path: Path, root: Path, messages: dict[tuple[str, str | None], Message]
) -> list[str]:
    """Collect marker calls from one source file into ``messages``.

    Args:
        path: The ``.py`` file to parse.
        root: Project root, used to build the relative location comments.
        messages: Accumulator keyed by ``(singular, plural)``; updated in place
            so a string used in several files gets several ``#:`` lines.

    Returns:
        Human-readable warnings for markers whose arguments are not literals.
    """
    warnings: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return [f"{path}: could not parse ({exc})"]

    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        # a target outside the project root (an ad-hoc file passed on the
        # command line); its absolute path is the only sensible reference
        rel = path.as_posix()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in GETTEXT_NAMES and name not in NGETTEXT_NAMES:
            continue
        if not node.args:
            continue
        singular = _literal(node.args[0])
        if singular is None:
            warnings.append(
                f"{rel}:{node.lineno}: {name}() called with a non-literal; not extractable"
            )
            continue
        plural = None
        if name in NGETTEXT_NAMES and len(node.args) >= 2:
            plural = _literal(node.args[1])
            if plural is None:
                warnings.append(
                    f"{rel}:{node.lineno}: ngettext() plural is not a literal; not extractable"
                )
                continue
        key = (singular, plural)
        message = messages.setdefault(key, Message(singular, plural))
        message.locations.append((rel, node.lineno))
    return warnings


def _escape(text: str) -> str:
    """Escape a string for a PO ``msgid``/``msgstr`` literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")


def _format_entry(message: Message) -> str:
    """Render one message as a PO entry, location comments included."""
    lines = [f"#: {path}:{line}" for path, line in message.locations]
    lines.append(f'msgid "{_escape(message.singular)}"')
    if message.plural is None:
        lines.append('msgstr ""')
    else:
        lines.append(f'msgid_plural "{_escape(message.plural)}"')
        lines.append('msgstr[0] ""')
        lines.append('msgstr[1] ""')
    return "\n".join(lines)


def build_pot(messages: dict[tuple[str, str | None], Message], version: str) -> str:
    """Assemble the full ``.pot`` file contents.

    Args:
        messages: Every extracted message.
        version: Project version, recorded in the header.

    Returns:
        The template text, ending in a trailing newline.
    """
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M%z")
    header = "\n".join(
        (
            f"# Translation template for MLOX Subset Sort {version}.",
            "# Generated by tools/make_pot.py -- do not edit by hand.",
            "#",
            "# To start a language:  msginit -i locale/mlox_subset_sort.pot \\",
            f"#                          -o locale/<lang>/LC_MESSAGES/{DOMAIN}.po -l <lang>",
            "# To compile it:        msgfmt locale/<lang>/LC_MESSAGES/"
            f"{DOMAIN}.po -o locale/<lang>/LC_MESSAGES/{DOMAIN}.mo",
            "#",
            "# Plugin names, file paths and mlox rule keywords ([Order], content=)",
            "# are data, not prose -- leave them untranslated.",
            "#",
            "#, fuzzy",
            'msgid ""',
            'msgstr ""',
            '"Project-Id-Version: MLOX Subset Sort ' + version + '\\n"',
            '"Report-Msgid-Bugs-To: \\n"',
            f'"POT-Creation-Date: {stamp}\\n"',
            '"PO-Revision-Date: YEAR-MO-DA HO:MI+ZONE\\n"',
            '"Last-Translator: FULL NAME <EMAIL@ADDRESS>\\n"',
            '"Language-Team: LANGUAGE <LL@li.org>\\n"',
            '"Language: \\n"',
            '"MIME-Version: 1.0\\n"',
            '"Content-Type: text/plain; charset=UTF-8\\n"',
            '"Content-Transfer-Encoding: 8bit\\n"',
            '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"',
        )
    )
    # sorted by first location so the template reads in source order, which
    # gives a translator the app's own narrative rather than alphabetical soup
    entries = sorted(messages.values(), key=lambda m: (m.locations[0][0], m.locations[0][1]))
    body = "\n\n".join(_format_entry(entry) for entry in entries)
    return header + "\n\n" + body + "\n" if body else header + "\n"


def _iter_sources(targets: list[Path]) -> list[Path]:
    """Expand files and directories into a sorted list of ``.py`` files."""
    found: set[Path] = set()
    for target in targets:
        if target.is_dir():
            found.update(p for p in target.rglob("*.py") if "__pycache__" not in p.parts)
        elif target.is_file():
            found.add(target)
    return sorted(found)


def _project_version(root: Path) -> str:
    """Read ``__version__`` from the package, or ``"?"`` if unreadable."""
    init = root / "mlox_subset" / "__init__.py"
    try:
        for line in init.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return "?"


def _strip_creation_date(text: str) -> str:
    """Drop the POT-Creation-Date line, which changes on every run."""
    return "\n".join(
        line for line in text.splitlines() if not line.startswith('"POT-Creation-Date:')
    )


def main(argv: list[str] | None = None) -> int:
    """Extract strings and write (or verify) the ``.pot`` template.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit status: 0 on success, 1 on a stale template or a warning.
    """
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Generate the gettext .pot template.")
    parser.add_argument(
        "targets",
        nargs="*",
        default=None,
        help="Files or directories to scan (default: the shipped sources).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(root / "locale" / f"{DOMAIN}.pot"),
        help="Where to write the template.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit non-zero if the template is out of date.",
    )
    args = parser.parse_args(argv)

    targets = (
        [Path(t) for t in args.targets] if args.targets else [root / t for t in DEFAULT_TARGETS]
    )
    sources = _iter_sources(targets)
    if not sources:
        print("no Python sources found to scan", file=sys.stderr)
        return 1

    messages: dict[tuple[str, str | None], Message] = {}
    warnings: list[str] = []
    for source in sources:
        try:
            if source.relative_to(root).as_posix() in EXCLUDED:
                continue
        except ValueError:
            pass  # outside the project root; scan it as given
        warnings.extend(extract_file(source, root, messages))

    text = build_pot(messages, _project_version(root))
    out = Path(args.output)

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    if args.check:
        try:
            current = out.read_text(encoding="utf-8")
        except OSError:
            print(f"{out} does not exist -- run: python tools/make_pot.py", file=sys.stderr)
            return 1
        if _strip_creation_date(current) != _strip_creation_date(text):
            print(f"{out} is out of date -- run: python tools/make_pot.py", file=sys.stderr)
            return 1
        print(f"{out}: up to date ({len(messages)} message(s))")
        return 1 if warnings else 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} -- {len(messages)} message(s) from {len(sources)} file(s)")
    if not messages:
        print(
            "NOTE: no strings are marked with _() yet, so the template is empty. "
            'Wrap user-facing strings as _("text") to populate it.',
            file=sys.stderr,
        )
    return 1 if warnings else 0


if __name__ == "__main__":
    sys.exit(main())
