"""Parse mlox rule files into ordering blocks and position hints.

Only the ordering keywords are extracted here: ``[Order]``, ``[NearStart]``
and ``[NearEnd]``. The predicate keywords (``[Requires]``, ``[Conflict]``,
``[Note]``) are evaluated separately, after sorting.

Two distinctions carry real weight, and both were bugs before they were
rules:

* **An ``[Order]`` body is a chain; ``[NearStart]``/``[NearEnd]`` bodies are
  not.** The former means "A before B before C", the latter means "put each of
  these as near the start/end as constraints allow". Treating the hint lists as
  chains invents edges between unrelated plugins -- mlox_base's ``[NearEnd]``
  block alone would link ``Merged Objects.esp -> Mashed Lists.esp -> ...``.
* **Blocks are kept whole rather than pre-zipped into pairs.** In
  ``[Order] A, B, C`` where B is not installed, keeping the chain lets the sort
  bridge ``A -> C`` and preserve the constraint. Pre-zipping to ``A->B`` and
  ``B->C`` loses it entirely when B expands to nothing. The real mlox engine
  keeps uninstalled plugins as phantom bridge nodes for exactly this reason.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from mlox_subset.i18n import gettext as _, ngettext
from mlox_subset.logging_setup import get_logger

#: Diagnostics about the run (not the user's report) go through here.
_LOG = get_logger(__name__)

#: Rule headers recognised in a rule file.
TOP_KEYWORDS: Final[tuple[str, ...]] = (
    "Order",
    "NearStart",
    "NearEnd",
    "Requires",
    "Conflict",
    "Patch",
    "Note",
    "Version",
)

#: A rule header must start a line.
#:
#: Both reference parsers anchor this way (mlox: ``^\\[(order|...)``; plox:
#: ``line.starts_with("[order")``). Matching anywhere in the line turns prose
#: like "see the [Order] section" inside a rule's message text into a phantom
#: rule start, silently corrupting block boundaries. Arguments must stay on the
#: header line and the closing bracket is required.
TOP_RE: Final = re.compile(
    r"^\[(" + "|".join(TOP_KEYWORDS) + r")\b([^\]\n]*)\]",
    re.IGNORECASE | re.MULTILINE,
)

#: One plugin name/pattern on an ordering body line.
#:
#: Public because it also defines what a valid plugin name looks like when
#: the user-rule maker validates hand-entered names.
#:
#: Follows the reference parsers: a name starts at the first non-space, runs
#: non-greedily to a recognised plugin extension (mlox uses ``^(\\S.*?\\.es[mp]\\b)``;
#: extended here with the OpenMW extensions, as plox does), may carry a
#: trailing ``*``, and must be followed by whitespace or end of line. Names
#: routinely contain spaces, ``&``, ``-``, parentheses, wildcards and ``<VER>``,
#: all of which pass through untouched. ``finditer`` supports plox-style
#: multiple names per line; trailing junk after a name is dropped, as mlox does.
ORDER_NAME_RE: Final = re.compile(
    r"\S[^\n]*?\.(?:esp|esm|omwaddon|omwgame|omwscripts)\*?(?=\s|$)",
    re.IGNORECASE,
)

#: Keywords whose bodies contain plugin names rather than message text.
_ORDERING_KEYWORDS: Final = frozenset({"order", "nearstart", "nearend"})


def strip_comment(line: str) -> str:
    """Remove an mlox trailing comment.

    Comments run from ``;`` to end of line. Quoting is not honoured, which is
    adequate because ordering bodies contain only filenames.

    Args:
        line: One raw line from a rule file.

    Returns:
        The line up to the first ``;``, or unchanged when there is none.
    """
    index = line.find(";")
    return line[:index] if index != -1 else line


def parse_mlox_file(path: Path) -> list[tuple[str, list[str]]]:
    """Extract the ordering blocks from one rule file.

    Unparseable body lines are treated as phantom names that match nothing, so
    the ordering chain bridges over them -- the same behaviour as mlox, and the
    reason a conditional entry inside an ``[Order]`` block does not break the
    surrounding constraint.

    Args:
        path: A rule file (``mlox_base.txt``, ``mlox_user.txt``, ...).

    Returns:
        ``(keyword, names)`` pairs for the ordering keywords only, in file
        order. ``keyword`` is lowercased.
    """
    # utf-8-sig: a BOM would otherwise hide a header on the very first line.
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    text = "\n".join(strip_comment(line) for line in raw.splitlines())

    matches = list(TOP_RE.finditer(text))
    blocks: list[tuple[str, list[str]]] = []
    skipped = 0
    for index, match in enumerate(matches):
        keyword = match.group(1)
        if keyword.lower() not in _ORDERING_KEYWORDS:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        names: list[str] = []
        for raw_line in text[start:end].splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("["):
                # A bracketed qualifier (e.g. "[DESC /.../ Foo.esp]") or a
                # malformed header. Phantom-name treatment: bridge over it.
                skipped += 1
                continue
            found = ORDER_NAME_RE.findall(line)
            if found:
                names.extend(found)
            else:
                skipped += 1  # non-empty line, no recognisable plugin name
        if names:
            blocks.append((keyword.lower(), names))
    if skipped:
        print(
            ngettext(
                "NOTE: %(name)s: %(count)d conditional/unrecognized line inside "
                "ordering rules treated as not-installed and bridged over "
                "(mlox does the same).",
                "NOTE: %(name)s: %(count)d conditional/unrecognized lines inside "
                "ordering rules treated as not-installed and bridged over "
                "(mlox does the same).",
                skipped,
            )
            % {"name": path.name, "count": skipped}
        )
    return blocks


def load_rule_blocks(
    rule_paths: Sequence[str | Path],
) -> tuple[list[tuple[list[str], int]], list[str], list[str]]:
    """Load every rule file into ordering chains and position hints.

    Args:
        rule_paths: Rule files or directories of ``*.txt`` rule files, in
            precedence order. A file's index becomes its priority, so files
            listed later win conflicts -- mirroring mlox reading
            ``mlox_user.txt`` after ``mlox_base.txt``.

    Returns:
        A ``(order_blocks, nearstart, nearend)`` triple, where ``order_blocks``
        holds ``(names, priority)`` chains in rule order, and the other two are
        flat pattern lists.
    """
    blocks_out: list[tuple[list[str], int]] = []
    nearstart: list[str] = []
    nearend: list[str] = []
    for priority, raw_path in enumerate(rule_paths):
        path = Path(raw_path)
        files = [path] if path.is_file() else sorted(path.glob("*.txt"))
        for rule_file in files:
            try:
                blocks = parse_mlox_file(rule_file)
            except Exception as exc:  # noqa: BLE001
                # Deliberately broad. Rule files are untrusted input downloaded
                # from a community repository, and one malformed file must not
                # abort a sort that the other files can still complete.
                # Narrowing this to OSError would let a decode or regex failure
                # propagate and take out the whole run.
                _LOG.warning(
                    _("could not parse rule file %(file)s: %(error)s"),
                    {"file": rule_file, "error": exc},
                )
                continue
            for keyword, names in blocks:
                if keyword == "order":
                    blocks_out.append((names, priority))
                elif keyword == "nearstart":
                    nearstart.extend(names)
                elif keyword == "nearend":
                    nearend.extend(names)
            total = sum(len(names) for _keyword, names in blocks)
            # A milestone about the run, not part of the report the user asked
            # for -- INFO per logging_setup's level table ("parsed N rules").
            _LOG.info(
                _("Loaded %(count)d plugin refs from %(file)s"),
                {"count": total, "file": rule_file.name},
            )
    return blocks_out, nearstart, nearend
