"""Render a DIAL/INFO record as readable dialogue instead of raw subrecords.

An INFO record on its own is opaque: a pile of one- and two-letter subrecords
(ONAM/RNAM/CNAM speaker filters, a packed DATA struct, a list of SCVR function
conditions each paired with an INTV/FLTV number) plus the response text and a
result script. Shown in a diff that way, a dialogue conflict is unreadable --
you cannot tell *which line an NPC says, to whom, and under what conditions*
two mods disagree about.

This module turns one tes3conv-JSON INFO record into that human sentence:

    Fargoth  (Background)
    - If disposition is at least 50
    - If Player Axe Skill >= 1
    Response: "Hello, friend. Have you seen my ring?"

It reads the shape tes3conv (the ``tes3`` Rust crate) emits: a record tagged
``"DialogueInfo"`` whose speaker filters are named fields (``speaker_id`` ...),
whose ``data`` block is already unpacked, and whose ``filters`` list carries
serde enum *names* (``filter_type="Function"``, ``function="PcAxe"``,
``comparison="GreaterEqual"``) with an adjacently-tagged value
(``{"type": "Integer", "data": 1}``). Nothing here parses bytes; it consumes
the JSON the rest of the tool already has.

The condition phrasing -- the mapping from a filter's type/function/comparison
to an English "If ..." line, including the boolean special cases ("If NPC is
not dead", "If player is a member of faction X") and the result-script token
lexer (:func:`script_tokens`) -- is adapted from **Morrowind Dialog Explorer**
(MIT, Sophie Kirschner); see ``src/info_string.py`` and ``src/syntax_highlight.py``
in that project, and CREDITS.md. It is retargeted here from MWDE's raw-subrecord
model onto the tes3conv enum names. Function labels themselves follow the tes3
crate's own ``FilterFunction`` names (prettified), not MWDE's, so they stay in
step with the JSON the tool actually reads.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

# tes3conv's record ``type`` tag for the two dialogue records (the tes3 crate
# serialises by struct name, not the four-letter DIAL/INFO chunk tag).
INFO_TYPE: Final = "DialogueInfo"
DIAL_TYPE: Final = "Dialogue"

# FilterComparison enum name -> operator. tes3conv emits the name, not the byte.
_COMPARISON_SYMBOLS: Final[dict[str, str]] = {
    "Equal": "=",
    "NotEqual": "!=",
    "Greater": ">",
    "GreaterEqual": ">=",
    "Less": "<",
    "LessEqual": "<=",
}

# A handful of tes3-crate FilterFunction names that a plain CamelCase split
# would render awkwardly. Everything else is prettified by _prettify_function.
_FUNCTION_NAME_OVERRIDES: Final[dict[str, str]] = {
    "PcCorprus": "PC Corprus",
    "FactionRankDifference": "PC Rank Minus NPC Rank",
    "SameSex": "Same Gender As PC",
    "SameRace": "Same Race As PC",
    "SameFaction": "Same Faction As PC",
    "TalkedToPc": "Talked To PC",
    "CreatureTarget": "Is Targeting A Creature",
}


def _prettify_function(name: str) -> str:
    """Turn a tes3-crate FilterFunction variant into a readable label.

    Splits CamelCase into words and upper-cases the ``Pc`` player prefix, so
    ``PcAxe`` reads as ``PC Axe`` and ``ReactionLow`` as ``Reaction Low``. A few
    names that split awkwardly are taken from :data:`_FUNCTION_NAME_OVERRIDES`.

    Args:
        name: The crate's ``function`` enum name from a Function-type filter.

    Returns:
        A human-readable function label; the original name if it does not look
        like a CamelCase identifier.
    """
    if name in _FUNCTION_NAME_OVERRIDES:
        return _FUNCTION_NAME_OVERRIDES[name]
    words = re.findall(r"[A-Z][a-z0-9]*", name)
    if not words:
        return name
    return " ".join("PC" if word == "Pc" else word for word in words)


# dialogue_type "Journal" means the disposition field is a journal index and
# the record is a quest-log entry rather than spoken dialogue.
_JOURNAL = "Journal"


def is_dialogue_record(record: Mapping[str, Any]) -> bool:
    """Whether ``record`` is a DIAL or INFO record in tes3conv form."""
    return isinstance(record, dict) and record.get("type") in (INFO_TYPE, DIAL_TYPE)


def _filter_value(flt: Mapping[str, Any]) -> float | int:
    """Pull the numeric operand out of a filter's adjacently-tagged value."""
    value = flt.get("value")
    if isinstance(value, dict):
        data = value.get("data", 0)
        return data if isinstance(data, (int, float)) else 0
    # Defensive: a bare number, should tes3conv ever emit one.
    return value if isinstance(value, (int, float)) else 0


def _number(value: float | int) -> str:
    """Format an operand, dropping the trailing ``.0`` on whole floats."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def describe_filter(flt: Mapping[str, Any]) -> str | None:
    """Turn one SCVR filter into an English "If ..." clause, or None to skip.

    Adapted from MWDE's ``dialog_function_string``: the ``Function`` case names
    the function and compares it; the variable cases (Global/Local/Journal/
    Item) read the operand name from the filter's ``id``; and the negated-identity
    cases (Dead/NotId/NotFaction/NotClass/NotRace/NotCell) collapse the common
    ``== 0`` / ``== 1`` boolean forms into plain "is" / "is not" sentences.

    Args:
        flt: One entry of an INFO record's ``filters`` list.

    Returns:
        A readable clause without the leading "- ", or ``None`` for an unused
        (``None``-type) filter that carries no condition.
    """
    ftype = flt.get("filter_type")
    if ftype in (None, "None"):
        return None
    comp = _COMPARISON_SYMBOLS.get(str(flt.get("comparison")), "?")
    value = _filter_value(flt)
    num = _number(value)
    variable = str(flt.get("id") or "")

    if ftype == "Function":
        func = str(flt.get("function") or "")
        name = _prettify_function(func)
        if func == "PcSex":
            return f"{name} {comp} {num} ({'female' if value else 'male'})"
        return f"{name} {comp} {num}"
    if ftype == "Global":
        return f"global {variable} {comp} {num}"
    if ftype == "Local":
        return f"local {variable} {comp} {num}"
    if ftype == "Journal":
        return f"quest {variable} {comp} {num}"
    if ftype == "Item":
        return f"player inventory {variable} {comp} {num}"
    if ftype == "Dead":
        if (comp in ("=", "<=") and value == 0) or (comp == "<" and value == 1):
            return f"NPC {variable} is not dead"
        return f"NPC death count for {variable} {comp} {num}"

    # The negated-identity family: "not ID", "not faction", ... Each reads as a
    # plain membership test in its common boolean forms.
    membership = {
        "NotId": ("NPC is {neg}{variable}", ""),
        "NotFaction": ("NPC is {neg}a member of faction {variable}", ""),
        "NotClass": ("NPC is {neg}a member of class {variable}", ""),
        "NotRace": ("NPC is {neg}a member of race {variable}", ""),
        "NotCell": ("NPC is {neg}located in cell {variable}", ""),
    }
    if ftype in membership:
        template = membership[ftype][0]
        if (comp in ("=", "<=") and value == 0) or (comp in ("!=", "<") and value == 1):
            return template.format(neg="not ", variable=variable)
        if (comp in ("=", ">=") and value == 1) or (comp in ("!=", ">") and value == 0):
            return template.format(neg="", variable=variable)
        return f"{ftype} {variable} {comp} {num}"
    if ftype == "NotLocal":
        return f"not local {variable} {comp} {num}"
    # Unknown/newer filter type: show it literally rather than dropping it.
    return f"{ftype} {variable} {comp} {num}".replace("  ", " ").strip()


def _context_line(record: Mapping[str, Any]) -> str:
    """Assemble the speaker/where line: 'Actor: X  &  Race: Y', or a placeholder.

    Adapted from MWDE's context assembly. An INFO with no speaker filters is
    said by ``<Anyone>`` (or is a ``<Journal>`` entry).
    """
    parts: list[str] = []
    for field, label in (
        ("speaker_id", "Actor"),
        ("speaker_race", "Race"),
        ("speaker_class", "Class"),
        ("speaker_faction", "Faction"),
        ("speaker_cell", "Cell"),
    ):
        val = record.get(field)
        if val:
            parts.append(f"{label}: {val}")
    if parts:
        return "  &  ".join(parts)
    data = record.get("data")
    dialogue_type = data.get("dialogue_type") if isinstance(data, dict) else None
    return "<Journal>" if dialogue_type == _JOURNAL else "<Anyone>"


def condition_lines(record: Mapping[str, Any]) -> list[str]:
    """The "If ..." lines from the packed DATA block and the SCVR filters."""
    conditions: list[str] = []
    raw_data = record.get("data")
    data: Mapping[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    dialogue_type = data.get("dialogue_type")

    player_faction = record.get("player_faction")
    player_rank = data.get("player_rank")
    if player_faction and player_rank == 0:
        conditions.append(f"player is not a member of faction {player_faction}")
    elif player_faction and isinstance(player_rank, int) and player_rank > 0:
        conditions.append(f"player rank in faction {player_faction} is at least {player_rank}")
    elif player_faction:
        conditions.append(f"player is a member of faction {player_faction}")

    disposition = data.get("disposition")
    if dialogue_type == _JOURNAL and isinstance(disposition, int):
        state = record.get("quest_state")
        suffix = f" ({state})" if state else ""
        conditions.append(f"quest index is {disposition}{suffix}")
    elif isinstance(disposition, int) and disposition > 0:
        conditions.append(f"disposition is at least {disposition}")

    sex = data.get("speaker_sex")
    if sex == "Male":
        conditions.append("NPC gender is male")
    elif sex == "Female":
        conditions.append("NPC gender is female")

    rank = data.get("speaker_rank")
    if isinstance(rank, int) and rank > 0:
        conditions.append(f"NPC rank is at least {rank}")

    filters = record.get("filters")
    if isinstance(filters, (list, tuple)):
        for flt in filters:
            if isinstance(flt, dict):
                clause = describe_filter(flt)
                if clause:
                    conditions.append(clause)
    return conditions


def describe_info(record: Mapping[str, Any]) -> str:
    """Render a whole INFO record as readable dialogue.

    Args:
        record: One tes3conv-JSON record with ``type == "DialogueInfo"``.

    Returns:
        A multi-line string: the speaker/context line, one "- If ..." line per
        condition, the quoted response text, and the result script if any. Not
        an INFO record -> an empty string, so callers can treat "" as "nothing
        to show here".
    """
    if not isinstance(record, dict) or record.get("type") != INFO_TYPE:
        return ""
    lines = [_context_line(record)]
    lines.extend(f"- If {clause}" for clause in condition_lines(record))
    text = record.get("text")
    if text:
        lines.append(f'Response: "{text}"')
    script = record.get("script_text")
    if script and str(script).strip():
        lines.append(f"Result: {script}")
    return "\n".join(lines)


def describe_dialogue(record: Mapping[str, Any]) -> str:
    """Render a DIAL topic header: its name and kind.

    Args:
        record: One tes3conv-JSON record with ``type == "Dialogue"``.

    Returns:
        e.g. ``'Topic "Background"'``, or an empty string when not a DIAL.
    """
    if not isinstance(record, dict) or record.get("type") != DIAL_TYPE:
        return ""
    kind = record.get("dialogue_type") or "Dialogue"
    name = record.get("id") or ""
    return f'{kind} "{name}"'


def describe_record(record: Mapping[str, Any]) -> str:
    """Readable text for a DIAL or INFO record, or "" for anything else.

    A convenience front door for callers (the diff viewer) that just want "the
    readable version if this is dialogue, otherwise nothing".

    Args:
        record: Any tes3conv-JSON record.

    Returns:
        The rendered dialogue/topic text, or an empty string.
    """
    rtype = record.get("type") if isinstance(record, dict) else None
    if rtype == INFO_TYPE:
        return describe_info(record)
    if rtype == DIAL_TYPE:
        return describe_dialogue(record)
    return ""


# An INFO record's result script is mwscript source. These token patterns are
# ported from MWDE's ``lexscan_list`` (src/syntax_highlight.py) and tried in
# order at each position -- the first match wins. The kinds line up with the
# syntax-colour tags the rest of the app already themes.
_SCRIPT_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("string", re.compile(r'"([^\\"]|\\.)*"')),
    ("number", re.compile(r"[+-]?[0-9]+(\.[0-9]+)?")),
    ("operator", re.compile(r"(\+|\*|-|/|==|<=|<|>=|>|!=|->)")),
    (
        "keyword",
        re.compile(
            r"(?i)\b(elseif|ifx|if|else|endif|while|endwhile|return|begin|end|"
            r"startscript|stopscript|float|short|long|set|to)\b"
        ),
    ),
    ("comment", re.compile(r";.*")),
    ("text", re.compile(r"\s+")),
    ("text", re.compile(r"[_a-zA-Z][_a-zA-Z0-9]*")),
)


def script_tokens(text: str) -> list[tuple[str, str]]:
    """Tokenise a result script into ``(kind, text)`` spans for highlighting.

    Kinds are ``keyword``, ``string``, ``number``, ``operator``, ``comment`` and
    ``text``; concatenating every token's text reproduces the input exactly, so
    a caller can insert each span into a text widget under a colour tag named
    for its kind. Ported from MWDE's ``lexscan_list``.

    Args:
        text: mwscript source, e.g. an INFO record's result script.

    Returns:
        The tokens in order.
    """
    tokens: list[tuple[str, str]] = []
    pos, end = 0, len(text)
    while pos < end:
        for kind, pattern in _SCRIPT_PATTERNS:
            match = pattern.match(text, pos)
            if match and match.end() > pos:
                tokens.append((kind, match.group()))
                pos = match.end()
                break
        else:
            # No pattern matched (a stray punctuation char): emit one character
            # as plain text so the scan always makes progress.
            tokens.append(("text", text[pos]))
            pos += 1
    return tokens


__all__ = [
    "DIAL_TYPE",
    "INFO_TYPE",
    "condition_lines",
    "describe_dialogue",
    "describe_filter",
    "describe_info",
    "describe_record",
    "is_dialogue_record",
    "script_tokens",
]
