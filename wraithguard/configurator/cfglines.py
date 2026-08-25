"""Read and write individual ``openmw.cfg`` lines.

Small helpers, but the ones that decide what OpenMW actually sees. Quoting in
particular is not cosmetic: a ``data=`` path containing spaces behaves
differently quoted and unquoted, and the surrounding file's existing style has
to be matched rather than imposed -- rewriting every line in our preferred form
would produce a diff the user did not ask for in a file they hand-edit.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

#: A ``data=`` line, capturing its value.
_DATA_LINE_RE: Final = re.compile(r"^\s*data\s*=\s*(.+?)\s*$", re.IGNORECASE)

#: Quote characters a cfg value may be wrapped in.
_QUOTES: Final = "\"'"

#: The base game's own masters. They lead every load order and are never a
#: user's "custom" mod, so the orphan pull skips them: pulling one as though it
#: were an unmanaged addition would hand the sorter a plugin it must never move.
BASE_GAME_MASTERS: Final = frozenset({"morrowind.esm", "tribunal.esm", "bloodmoon.esm"})


def escape_cfg_value(value: str) -> str:
    """Escape a path for an OpenMW *quoted* ``data=`` value.

    Inside double quotes OpenMW treats ``&`` as its escape character, so a
    literal ``&`` in a folder name has to be written ``&&`` and a literal ``"``
    as ``&"`` -- otherwise the engine reads ``& Friends`` as an escape sequence
    and loads the wrong folder. Only quoted values are escaped; a bare value is
    literal (see :func:`format_data_line`).

    Args:
        value: The real, unescaped path.

    Returns:
        The path with ``&`` and ``"`` escaped for a quoted value.
    """
    return value.replace("&", "&&").replace('"', '&"')


def unescape_cfg_value(inner: str) -> str:
    """Undo :func:`escape_cfg_value` on the *inside* of a quoted value.

    ``&`` escapes the character after it, so ``&&`` becomes ``&`` and ``&"``
    becomes ``"``. A path read back this way compares equal to the same folder
    written by any other tool, which is what stops the same ``data=`` path being
    added twice -- once as ``&`` and once as ``&&``.

    Args:
        inner: The content between the surrounding double quotes.

    Returns:
        The real, unescaped path.
    """
    chars = iter(inner)
    # '&' consumes the following char literally; the generator advances the same
    # iterator, so an escaped pair is read as one character.
    return "".join(next(chars, "") if char == "&" else char for char in chars)


def _unquote_cfg_value(raw: str) -> str:
    """Turn a raw ``data=`` value (quoted or bare) into its real path.

    A double-quoted value is unescaped (OpenMW's ``&`` rule); a bare value is
    literal, so only stray surrounding single quotes are stripped.
    """
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return unescape_cfg_value(raw[1:-1])
    return raw.strip("'")


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
    value = _unquote_cfg_value(path_value.strip())
    return f'data="{escape_cfg_value(value)}"' if quoted else f"data={value}"


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
    return _unquote_cfg_value(match.group(1).strip())


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
    return _unquote_cfg_value(value.strip()).replace("\\", "/").rstrip("/").lower()


def toml_value(value: str) -> str:
    r"""Render a string as a TOML value, preferring a literal string.

    Prefer a single-quoted TOML literal string ('...') for everything --
    plugin names, script names, and especially paths. Literal strings are
    raw (TOML does zero escape processing on their contents), which is
    exactly what a Windows path full of backslashes needs: 'C:\\Games\\...'
    is correct and readable as-is, whereas a double-quoted *basic* string
    would require every backslash doubled ("C:\\\\Games\\\\..."), which is
    not what momw-configurator/umo actually write.

    A single-line literal can hold neither a `'` (it would end the string
    early) nor a raw newline (TOML forbids one in a single-line string of
    either kind) -- an insertBlock/appendBlock value is exactly the case
    that needs a real newline. Either condition escalates to a
    triple-single-quoted multi-line literal string instead
    ('''line one\nline two'''), which tolerates both a lone `'` and
    embedded newlines (just not three quotes in a row). In the vanishingly
    unlikely case a value contains `'''` itself, fall back to a properly
    escaped double-quoted basic string as a last resort -- multi-line
    (\"\"\"...\"\"\") if a newline is also present, since a single-line basic
    string can't hold one either.

    Args:
        value: The string to render -- a plugin name, script name, path, or
            a multi-line insertBlock/appendBlock body.

    Returns:
        A TOML value literal, quotes included.
    """
    has_newline = "\n" in value
    if "'''" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"""{escaped}"""' if has_newline else f'"{escaped}"'
    if "'" in value or has_newline:
        return "'''" + value + "'''"
    return "'" + value + "'"


def is_base_data_path(value: str) -> bool:
    """Whether a ``data=`` value points at the base game's own Data Files folder.

    The vanilla install directory is named ``Data Files`` by every distribution
    (Steam, GOG, retail) and OpenMW's own installer, so its final path component
    is the reliable, filesystem-free signal. Used to keep the base install out
    of the orphan pull: it is not a mod, and re-anchoring it would be wrong.

    Args:
        value: A raw ``data=`` value, quoted or not.

    Returns:
        ``True`` when the path's last component is ``Data Files`` (any case).
    """
    tail = normalize_data_path(value).rsplit("/", 1)[-1]
    return tail == "data files"


def curated_covers(
    name: str,
    curated_lower: Collection[str],
    needs_cleaning_lower: Collection[str],
) -> bool:
    """Whether the curated list accounts for a cfg plugin, ``clean_`` prefix and all.

    A plugin is covered when the list names it directly -- the ordinary case,
    and also the case where the yml already lists it under a cleaned name like
    ``Clean_Foo.esp`` -- OR when it is the umo download of a needs-cleaning list
    plugin, which arrives with a single ``clean_`` prefix (the list names
    ``Foo.esp``; the file on disk is ``Clean_Foo.esp``).

    Only ONE leading ``clean_`` is stripped, deliberately. Outside tools can in
    principle stack them (``clean_clean_Foo``), but that is never seen on the
    list itself, and stripping every prefix would risk mistaking a genuinely
    custom plugin that merely starts with ``clean_`` for a list item. After the
    single strip the base must be BOTH on the list AND flagged needs-cleaning
    for the match to count -- so a custom ``Clean_MyMod.esp`` whose base is not a
    needs-cleaning list plugin stays an orphan (no over-filtering), while a
    legitimately prefixed list item is seen as managed -- caught exactly if the
    yml already carries the prefix, or via its needs-cleaning base otherwise --
    and so is never pulled into the customizations.

    Args:
        name: A cfg plugin name, any case.
        curated_lower: Lower-cased plugin names on the list.
        needs_cleaning_lower: Lower-cased plugin names the list flags for
            cleaning.

    Returns:
        ``True`` when the list accounts for this plugin.
    """
    lowered = name.lower()
    if lowered in curated_lower:
        return True
    prefix = "clean_"
    if lowered.startswith(prefix):
        base = lowered[len(prefix) :]
        return base in curated_lower and base in needs_cleaning_lower
    return False


def orphan_cfg_entries(
    content_names: Sequence[str],
    data_lines: Sequence[str],
    *,
    curated_lower: Collection[str],
    needs_cleaning_lower: Collection[str],
    declared_plugins_lower: Collection[str],
    declared_data_norms: Collection[str],
) -> tuple[list[str], list[str]]:
    """Pick the orphaned -- unmanaged -- ``content=`` and ``data=`` entries.

    An entry is an orphan when nothing accounts for it. A ``content=`` plugin
    is orphaned when the curated list does not cover it (see
    :func:`curated_covers`, which accounts for ``clean_`` prefixes) and it is
    neither declared in the customizations nor a base-game master. A ``data=``
    path is orphaned when it is not declared in the customizations and is not the
    base game's own ``Data Files`` folder -- the ``plugin-order`` yml has no
    data-path concept, so the customizations are the only "managed" signal a
    folder can match.

    Order is preserved: the cfg's own ``content=`` / ``data=`` order is returned
    untouched, so the caller can hand these to the sorter as the frozen starting
    order the user asked to keep "until sorted".

    Args:
        content_names: The cfg's ``content=`` plugin names, in file order.
        data_lines: The cfg's raw ``data=`` lines, in file order.
        curated_lower: Lower-cased plugin names the curated list owns. Empty
            when no ``plugin-order.yml`` / list name was given.
        needs_cleaning_lower: Lower-cased plugin names the list flags for
            cleaning, for the ``clean_`` alias match in :func:`curated_covers`.
        declared_plugins_lower: Lower-cased plugin names already declared in the
            customizations / subset sources -- the "managed" plugins to skip.
        declared_data_norms: :func:`normalize_data_path` keys for data paths
            already declared in the customizations / subset sources.

    Returns:
        ``(orphan_plugins, orphan_data_values)``: the orphaned plugin names (as
        spelled in ``content=``) and the orphaned data-path values (unquoted, as
        :func:`extract_data_path_value` returns them), each in cfg order.
    """
    orphan_plugins = [
        name
        for name in content_names
        if not curated_covers(name, curated_lower, needs_cleaning_lower)
        and name.lower() not in declared_plugins_lower
        and name.lower() not in BASE_GAME_MASTERS
    ]
    orphan_data: list[str] = []
    for line in data_lines:
        value = extract_data_path_value(line)
        if value is None:
            continue
        norm = normalize_data_path(value)
        if norm and norm not in declared_data_norms and not is_base_data_path(value):
            orphan_data.append(value)
    return orphan_plugins, orphan_data


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
