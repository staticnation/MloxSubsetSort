"""Read and write individual ``openmw.cfg`` lines.

Small helpers, but the ones that decide what OpenMW actually sees. Quoting in
particular is not cosmetic: a ``data=`` path containing spaces behaves
differently quoted and unquoted, and the surrounding file's existing style has
to be matched rather than imposed -- rewriting every line in our preferred form
would produce a diff the user did not ask for in a file they hand-edit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Final

#: A ``data=`` line, capturing its value.
_DATA_LINE_RE: Final = re.compile(r"^\s*data\s*=\s*(.+?)\s*$", re.IGNORECASE)

#: Quote characters a cfg value may be wrapped in.
_QUOTES: Final = "\"'"


def detect_data_quoting(data_lines: Iterable[str]) -> bool:
    r"""Whether this cfg predominantly quotes its ``data=`` paths.

    New lines are then formatted to match the file's own convention rather
    than being unconditionally quoted. This is not cosmetic: a cfg written in
    the classic bare style treats quote characters as *literal parts of the
    path*, so injecting ``data="C:\\Foo"`` into an otherwise-unquoted file can
    make OpenMW look for a folder literally named ``"C:\\Foo"``, quotes
    included, and silently fail to load the mod.

    Args:
        data_lines: Raw cfg lines; non-``data=`` lines are ignored.

    Returns:
        ``True`` if quoted lines outnumber bare ones. Ties, and files with no
        ``data=`` lines at all, return ``False`` -- bare is the
        momw-configurator/umo default on the setups this tool targets.
    """
    quoted = unquoted = 0
    for line in data_lines:
        match = _DATA_LINE_RE.match(line)
        if not match:
            continue
        val = match.group(1).strip()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            quoted += 1
        else:
            unquoted += 1
    return quoted > unquoted


def format_data_line(path_value: str, quoted: bool = False) -> str:
    """Render a ``data=`` line, matching the file's quoting convention.

    Args:
        path_value: The path, with or without surrounding quotes.
        quoted: Whether to wrap the value in double quotes. Pass the result of
            :func:`detect_data_quoting` rather than a fixed choice.

    Returns:
        A complete ``data=`` line.
    """
    value = path_value.strip().strip('"').strip("'")
    return f'data="{value}"' if quoted else f"data={value}"


def find_anchor_index(lines: Sequence[str], anchor: str) -> int | None:
    """Find the first line containing ``anchor``, case-insensitively.

    Substring matching is deliberate: it mirrors how momw-configurator locates
    its anchors, so a preview here matches what the real Configurator will do.

    Args:
        lines: The cfg lines to search.
        anchor: Substring to look for.

    Returns:
        The index of the first match, or ``None`` when nothing matches.
    """
    anchor_lower = anchor.lower()
    for index, line in enumerate(lines):
        if anchor_lower in line.lower():
            return index
    return None


def extract_data_path_value(line: str) -> str | None:
    """Pull the bare path out of a raw ``data=`` cfg line.

    Args:
        line: Any cfg line. Callers pass arbitrary lines, so non-matches are
            expected rather than exceptional.

    Returns:
        The unquoted path, or ``None`` when the line is not a ``data=`` line.
    """
    match = _DATA_LINE_RE.match(line)
    if not match:
        return None
    return match.group(1).strip().strip('"').strip("'")


def normalize_data_path(value: str) -> str:
    """Normalise a ``data=`` path for duplicate detection only.

    Warning:
        Never use the result for display or for writing back to the cfg. It is
        lossy by design -- lowercased, slashes unified, trailing slash removed
        -- so that two spellings of the same directory compare equal. Writing
        it back would silently rewrite the user's paths.

    Args:
        value: A raw ``data=`` value, quoted or not.

    Returns:
        A comparison key, or ``""`` for an empty value.
    """
    if not value:
        return ""
    return value.strip().strip('"').strip("'").replace("\\", "/").rstrip("/").lower()


def toml_value(value: str) -> str:
    r"""Render a string as a TOML value, preferring a literal string.

    Prefer a single-quoted TOML literal string ('...') for everything --
    plugin names, script names, and especially paths. Literal strings are
    raw (TOML does zero escape processing on their contents), which is
    exactly what a Windows path full of backslashes needs: 'C:\\Games\\...'
    is correct and readable as-is, whereas a double-quoted *basic* string
    would require every backslash doubled ("C:\\\\Games\\\\..."), which is
    not what momw-configurator/umo actually write.

    A literal string can't contain a `'` itself (it would end the string
    early), so a name with an apostrophe -- e.g. "MyMod's.esp" -- gets
    escalated to a triple-single-quoted multi-line literal string instead
    ('''MyMod's.esp'''), which tolerates a lone `'` (just not three in a
    row). In the vanishingly unlikely case a filename contains `'''`
    itself, fall back to a properly escaped double-quoted basic string as
    a last resort.

    Args:
        value: The string to render -- a plugin name, script name, or path.

    Returns:
        A TOML value literal, quotes included.
    """
    if "'''" in value:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if "'" in value:
        return "'''" + value + "'''"
    return "'" + value + "'"


def cfg_line_value(line: str) -> str | None:
    """Extract the value part of a cfg line, unquoted.

    A mirror of ``cfgLineValue()`` in momw-configurator's ``custom.go``,
    including its handling of matched surrounding quotes.

    Args:
        line: Any cfg line.

    Returns:
        The value with matched quotes stripped, or ``None`` when the line
        contains no ``=``.
    """
    if "=" not in line:
        return None
    value = line.split("=", 1)[1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
        value = value[1:-1]
    return value
