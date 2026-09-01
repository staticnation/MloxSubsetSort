"""Find every script call that sets a journal stage, and link it to the stage it sets.

**What "extend script_tokens() rather than reinventing tokenization" actually
means.** That function is a highlighting lexer: it turns source into
``(kind, text)`` spans so a text widget can colour them, and an identifier
like ``Journal`` comes out as an ordinary ``"text"`` span indistinguishable
from any variable name. There is no call-site boundary to read off directly.
:func:`calls_in_text` adds a second pass over that same token stream, looking
for the shape ``Journal <id>, N`` (or ``SetJournalIndex``) -- where ``<id>`` is
the quest, quoted or not, since MWScript accepts ``Journal "Q" 40`` and
``Journal Q 40`` alike and real mods use both -- a small scanner built on the
existing lexer, not a second one.

**Why this needs the compiled-bytecode fallback path too.** An INFO's result
script (``script_text``) is always source -- Bethesda never gives it a
compiled form of its own, so nothing here is ever missing for a stage. A
*standalone* ``Script`` record is different: it carries both ``text`` and a
precompiled ``bytecode``, and some plugins ship the bytecode with the source
stripped. A call that only exists in a script like that still runs in game;
skipping it because the source is gone would make this tool's picture of a
questline quietly wrong for exactly the mods most worth checking. So a
standalone Script with no source falls back to
:func:`~wraithguard.mwscript.disassembler.disassemble`, the same disassembler
the field-diff viewer already uses for the same reason -- see that module's
own docstring for why it labels what it cannot decode rather than guessing.

**Why override resolution matters here too.** A later plugin can redefine an
entire ``Script`` id, and only the surviving version's calls actually run --
an earlier one's is dead code once overridden, same as any other TES3 record.
:func:`calls_from_stages` gets this for free by reading ``Stage.script_text``,
which :mod:`~wraithguard.patch.journal` already resolved through the same
last-plugin-wins-by-id rule for its own purposes. Standalone scripts have no
equivalent upstream resolver, so :func:`calls_from_scripts` does its own,
smaller version of the same thing.

**Why every call also carries its raw enclosing condition, and not an
evaluated one.** A ``Journal`` call sitting inside ``if ( GetDisposition >=
50 )`` only actually runs when that holds -- reporting the call site alone,
with no hint that it is gated, would call itself a "flowchart" while quietly
lying about which paths are real. :func:`calls_in_text_with_context` tracks
``if``/``elseif``/``else``/``while`` nesting in the same single pass over the
token stream and hands back each call's enclosing condition text verbatim,
outermost first. It does not evaluate that text, resolve variables, or decide
whether two branches are mutually exclusive -- the same restraint
:mod:`~wraithguard.mwscript.disassembler` already applies to bytecode
expressions, and for the same reason: a table-driven scan over arbitrary
third-party mwscript cannot safely claim to know what a condition means, only
where it is. A call read from disassembled bytecode carries no condition text
at all (``conditions=()``) rather than a guess -- reconstructing block
structure from compiled jumps is a different, larger problem than this scan
takes on.

**What :func:`statements_in_text_with_context` is for.** A quest stage's
result script rarely only sets the journal -- the same block that advances a
quest is usually also where an NPC hands over gold, an item, a disposition
change, or starts another script, and that is exactly the kind of thing worth
seeing beside a journal entry: what happens here changes from mod to mod, and
knowing *which plugin* did it is the whole point of a tool like this one. A
curated list of "reward" functions would always miss something, so this does
not keep one. MWScript is one statement per line, which is the boundary this
scans against: every line is read as ``function`` (or ``target->function``)
plus its raw ``arguments``, unless its first token is a control-flow keyword
(``if``/``elseif``/``else``/``endif``/``while``/``endwhile``) or a local
declaration (``float``/``short``/``long``) -- those are structure this module
already reports through condition-tracking, not an effect of their own.
Journal/SetJournalIndex lines are excluded too, since :class:`JournalCall`
already owns those. Nothing here interprets what a function's arguments
*mean* -- no per-function argument schema, no "item: gold_001, count: 100" --
only ``function``/``target`` split out from ``arguments`` for scanning, plus
the verbatim line. That is the same restraint as the condition text: this
scan can say what runs and roughly what it is called with, not what it does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from wraithguard.mwscript.disassembler import disassemble
from wraithguard.mwscript.tes3conv import BytecodeDecodeError, decode_bytecode_field
from wraithguard.tes3fields.dialogue import script_tokens

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from wraithguard.patch.journal import Resolved

#: Function names this scan looks for, lower-cased for matching. Both write a
#: journal index; GetJournalIndex is a read and is not a place a stage gets
#: set, so it is deliberately not here.
_JOURNAL_FUNCTIONS: Final = frozenset({"journal", "setjournalindex"})


def _is_bare_id(word: str) -> bool:
    """Whether an unquoted token is usable as a quest id.

    MWScript lets the ``Journal``/``SetJournalIndex`` quest id be written
    unquoted -- ``Journal TDM_CM_Telvanni 40`` is as valid as
    ``Journal "TDM_CM_Telvanni" 40``, and whole mods (Caldera Mine Expanded
    among them) write every one of theirs that way. The lexer hands such an id
    back as an ordinary identifier token, so a bareword is accepted when it
    opens like an identifier -- a letter or underscore -- which rejects the
    whitespace, commas and other punctuation that also arrive as ``"text"``
    tokens. A purely numeric argument never reaches here: the lexer classifies
    it as ``"number"``, not ``"text"``.

    Args:
        word: The token's text.

    Returns:
        ``True`` when ``word`` can be a quest id written without quotes.
    """
    return bool(word) and (word[0].isalpha() or word[0] == "_")


def _canonical(name: str) -> str:
    """The call's proper spelling, regardless of the case it was written in.

    MWScript is not case-sensitive about function names; the reported call
    should not look like two different functions just because one script
    author capitalised it and another did not.
    """
    return "Journal" if name.lower() == "journal" else "SetJournalIndex"


@dataclass(frozen=True, slots=True)
class JournalCall:
    """One place a script sets a journal stage.

    Attributes:
        function: ``"Journal"`` or ``"SetJournalIndex"``, canonicalised.
        quest: The quest id argument, exactly as the script wrote it. Kept
            case-preserved rather than lower-cased -- matching a resolved
            quest id is the caller's job (:func:`attach`), done
            case-insensitively there, not by mangling this for display.
        index: The journal index argument.
        owner_type: ``"DialogueInfo"`` or ``"Script"`` -- the kind of record
            the call was found in.
        owner_id: That record's own id.
        plugin: The plugin whose surviving definition of ``owner_id`` this
            call was read from. Never the plugin that first wrote a call a
            later override has since removed.
        from_bytecode: Whether this was read from disassembled bytecode
            rather than source text -- only possible for a standalone Script
            whose source was stripped. A heuristic disassembler's reading of
            an expression-laden instruction stream deserves less confidence
            than a direct token match on real source, and a caller showing
            these to a person should probably say so.
        conditions: The raw text of every ``if``/``elseif``/``while`` this
            call sits inside, outermost first -- an empty tuple means it runs
            unconditionally within its script. Verbatim source, not
            evaluated: no attempt is made to resolve what the condition
            checks or whether it can ever be true. Always empty for a
            bytecode-derived call (``from_bytecode`` is ``True``);
            reconstructing block structure from compiled jumps is out of
            scope for this scan.
        raw: The complete decoded owning record -- the DialogueInfo or Script
            this call was found in -- carried alongside the extracted fields
            above rather than instead of them, the same reason
            :attr:`~wraithguard.patch.journal.Stage.raw` exists. A caller
            that wants a call's own dialogue requirements
            (``condition_lines``), full response text, or every other effect
            the same record has (:func:`effects_from_stages` and siblings,
            filtered to this ``owner_id``) has the whole record to work from
            rather than a second extraction to keep in sync with this one.
    """

    function: str
    quest: str
    index: int
    owner_type: str = ""
    owner_id: str = ""
    plugin: str = ""
    from_bytecode: bool = False
    conditions: tuple[str, ...] = ()
    owner_raw: Mapping[str, object] | None = None


def _scan_calls(tokens: list[tuple[str, str]]) -> list[tuple[int, str, str, int]]:
    """The token-stream scan shared by ``calls_in_text`` and the context-aware version.

    One pass, matching ``Journal <id>, N`` -- ``<id>`` quoted or a bareword.

    Args:
        tokens: From :func:`~wraithguard.tes3fields.dialogue.script_tokens`.

    Returns:
        ``(token_index, function, quest, index)`` tuples, ``token_index``
        being where the matched function name starts -- what a caller tracking
        position (like :func:`_condition_stack_at_each_token`'s consumer)
        looks up against.
    """
    calls: list[tuple[int, str, str, int]] = []
    i, n = 0, len(tokens)

    while i < n:
        kind, word = tokens[i]
        if kind != "text" or word.lower() not in _JOURNAL_FUNCTIONS:
            i += 1
            continue
        start = i

        j = i + 1
        while j < n and tokens[j][0] == "text" and tokens[j][1].isspace():
            j += 1
        if j >= n:
            i += 1
            continue
        id_kind, id_word = tokens[j]
        if id_kind == "string":
            quest = id_word.strip('"')
        elif id_kind == "text" and _is_bare_id(id_word):
            quest = id_word
        else:
            i += 1
            continue

        j += 1
        while j < n and tokens[j][0] == "text" and (tokens[j][1].isspace() or tokens[j][1] == ","):
            j += 1
        if j >= n or tokens[j][0] != "number":
            i += 1
            continue
        try:
            index = int(float(tokens[j][1]))
        except ValueError:
            i = j
            continue

        calls.append((start, _canonical(word), quest, index))
        i = j + 1

    return calls


def calls_in_text(text: str) -> list[tuple[str, str, int]]:
    """Find every Journal/SetJournalIndex call in mwscript source.

    A heuristic scan of the token stream, not a parser: on its own it has no
    notion of which ``if``/``elseif`` branch a call sits in, so two calls that
    can never both run in the same playthrough are reported exactly like two
    that always both do -- see :func:`calls_in_text_with_context` for the
    version that also reports (without evaluating) that branch.

    Args:
        text: mwscript source -- an INFO's result script, or a Script
            record's own text.

    Returns:
        ``(function, quest, index)`` tuples, in source order.
    """
    return [
        (function, quest, index) for _, function, quest, index in _scan_calls(script_tokens(text))
    ]


#: Keywords that open a new condition scope this scan tracks. Not `ifx`:
#: real mwscript documentation for it is scarce enough that guessing its
#: block-nesting behaviour risks a confidently wrong condition text, worse
#: than the honest gap of leaving it untracked.
_CONDITION_OPENERS: Final = frozenset({"if", "elseif", "while"})

#: Keywords that close the innermost open scope.
_CONDITION_CLOSERS: Final = frozenset({"endif", "endwhile"})


def _matching_paren(tokens: list[tuple[str, str]], start: int) -> int:
    """The index just past the ``)`` matching the ``(`` at ``start``.

    Args:
        tokens: From :func:`~wraithguard.tes3fields.dialogue.script_tokens`.
        start: Index of a token whose text is exactly ``"("``.

    Returns:
        One past the matching close-paren's index, tracking nesting so a
        condition containing its own parenthesised sub-expressions is not cut
        short at the first ``)``. An unbalanced script consumes to the end
        of the token stream rather than looping -- a best-effort answer, not
        an error, for a scan that is not a parser.
    """
    depth = 0
    i, n = start, len(tokens)
    while i < n:
        if tokens[i][1] == "(":
            depth += 1
        elif tokens[i][1] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _condition_stack_at_each_token(tokens: list[tuple[str, str]]) -> list[tuple[str, ...]]:
    """The enclosing condition text at every position in a token stream.

    One left-to-right pass, tracking ``if``/``elseif``/``while`` nesting with
    a single stack: entered on ``if``/``elseif``/``while``, the top replaced
    on a sibling ``elseif``/``else``, popped on ``endif``/``endwhile``. Text
    is taken verbatim from the source between a condition's own parentheses;
    nothing here evaluates it, and an ``else`` with no expression of its own
    is recorded as the literal string ``"else"`` rather than a guess at what
    it negates.

    Args:
        tokens: From :func:`~wraithguard.tes3fields.dialogue.script_tokens`.

    Returns:
        One tuple per token (same length as ``tokens``), outermost condition
        first, giving the stack *before* that token is processed -- what a
        call found starting at token index ``i`` sits inside is this list's
        element ``i``.
    """
    stack: list[str] = []
    n = len(tokens)
    at: list[tuple[str, ...]] = [()] * n
    i = 0

    while i < n:
        at[i] = tuple(stack)
        kind, word = tokens[i]
        low = word.lower()

        if kind != "keyword" or low not in _CONDITION_OPENERS | _CONDITION_CLOSERS | {"else"}:
            i += 1
            continue

        if low in _CONDITION_OPENERS:
            j = i + 1
            while j < n and tokens[j][0] == "text" and tokens[j][1].isspace():
                j += 1
            if j >= n or tokens[j][1] != "(":
                i += 1  # "if"/"while" with no recognisable condition follows
                continue
            k = _matching_paren(tokens, j)
            # Positions i+1..k-1 -- the whitespace and the condition's own
            # tokens -- belong to evaluating the condition, not yet to the
            # block it introduces, so they keep the pre-push stack too.
            for pos in range(i + 1, k):
                at[pos] = tuple(stack)
            condition = "".join(t[1] for t in tokens[j:k]).strip()
            label = f"{low} {condition}"
            if low == "elseif" and stack:
                stack[-1] = label  # a sibling branch, not a deeper nesting
            else:
                stack.append(label)
            i = k
            continue

        if low == "else" and stack:
            stack[-1] = "else"
        elif low in _CONDITION_CLOSERS and stack:
            stack.pop()
        i += 1

    return at


def calls_in_text_with_context(text: str) -> list[tuple[str, str, int, tuple[str, ...]]]:
    """:func:`calls_in_text`, with each call's raw enclosing condition attached.

    Args:
        text: mwscript source.

    Returns:
        ``(function, quest, index, conditions)`` tuples, in source order.
        ``conditions`` is outermost first, empty for a call that runs
        unconditionally within its script.
    """
    tokens = script_tokens(text)
    condition_at = _condition_stack_at_each_token(tokens)
    return [
        (function, quest, index, condition_at[position])
        for position, function, quest, index in _scan_calls(tokens)
    ]


#: A line whose first real token is one of these is structure this module
#: already reports through condition-tracking (if/elseif/else/endif/while/
#: endwhile), a local declaration with nothing to report (float/short/
#: long), or a script boundary declaring nothing beyond the script's own id,
#: which the tree already shows via the record itself (begin/end) -- not a
#: statement :func:`statements_in_text_with_context` counts as an effect.
#: Not "ifx": see _CONDITION_OPENERS's own note on it.
_SKIP_LEADING_KEYWORDS: Final = frozenset(
    {
        "if",
        "elseif",
        "else",
        "endif",
        "while",
        "endwhile",
        "float",
        "short",
        "long",
        "begin",
        "end",
    }
)


def _statement_lines(tokens: list[tuple[str, str]]) -> list[tuple[int, list[tuple[str, str]]]]:
    """Split a token stream into logical (start_index, line_tokens) groups.

    MWScript is one statement per line; this is the boundary
    :func:`statements_in_text_with_context` reads statements against. A blank
    line, or several in a row, produces no entry at all rather than an empty
    one.

    Args:
        tokens: From :func:`~wraithguard.tes3fields.dialogue.script_tokens`.

    Returns:
        Each line's tokens, with ``start_index`` being that line's first
        token's position in the original stream -- what a caller looks up a
        condition snapshot against.
    """
    lines: list[tuple[int, list[tuple[str, str]]]] = []
    current: list[tuple[str, str]] = []
    start = 0
    for index, (kind, text) in enumerate(tokens):
        if kind == "text" and text.isspace() and "\n" in text:
            if current:
                lines.append((start, current))
            current = []
            start = index + 1
            continue
        current.append((kind, text))
    if current:
        lines.append((start, current))
    return lines


def _parse_statement_line(
    line_tokens: list[tuple[str, str]],
) -> tuple[str, str, str, str] | None:
    """Read one logical line as ``(function, target, arguments, raw)``, or ``None`` to skip it.

    Args:
        line_tokens: One line's tokens, from :func:`_statement_lines`.

    Returns:
        ``None`` for a blank or comment-only line, or one whose first real
        token is pure control flow or a local declaration. Otherwise the
        line's own function name (the part after ``->`` for a target-
        qualified call), the target reference before ``->`` (empty for an
        ordinary call), the argument text verbatim, and the whole line
        verbatim.
    """
    i, n = 0, len(line_tokens)
    while i < n and line_tokens[i][0] == "text" and line_tokens[i][1].isspace():
        i += 1
    if i >= n or line_tokens[i][0] == "comment":
        return None
    first_kind, first_word = line_tokens[i]
    if first_kind == "keyword" and first_word.lower() in _SKIP_LEADING_KEYWORDS:
        return None

    raw = "".join(t[1] for t in line_tokens).strip()
    if not raw:
        return None

    # script_tokens' own operator regex lists "-" before "->" in its
    # alternation, so re always matches the lone "-" first and never reaches
    # the two-character alternative -- confirmed empirically, not assumed:
    # "->" comes back as two adjacent operator tokens, "-" then ">", never
    # one "->" token. Detect the two-token shape rather than a literal "->".
    arrow = next(
        (k for k in range(i, n - 1) if line_tokens[k][1] == "-" and line_tokens[k + 1][1] == ">"),
        None,
    )
    if arrow is not None:
        target = "".join(t[1] for t in line_tokens[i:arrow]).strip()
        rest = line_tokens[arrow + 2 :]
        j = 0
        while j < len(rest) and rest[j][0] == "text" and rest[j][1].isspace():
            j += 1
        if j >= len(rest):
            return None  # "target->" with nothing after it; too malformed to name
        function = rest[j][1]
        arguments = "".join(t[1] for t in rest[j + 1 :]).strip()
        return (function, target, arguments, raw)

    function = first_word
    arguments = "".join(t[1] for t in line_tokens[i + 1 :]).strip()
    return (function, "", arguments, raw)


def statements_in_text_with_context(
    text: str,
) -> list[tuple[str, str, str, str, tuple[str, ...]]]:
    """Every non-Journal statement in mwscript source, with its raw enclosing condition.

    Args:
        text: mwscript source -- an INFO's result script, or a Script
            record's own text.

    Returns:
        ``(function, target, arguments, raw, conditions)`` tuples, in source
        order. ``target`` is the reference before a ``->``-qualified call's
        function name, empty for an ordinary one. ``conditions`` is outermost
        first, matching :func:`calls_in_text_with_context`'s own meaning.
    """
    tokens = script_tokens(text)
    condition_at = _condition_stack_at_each_token(tokens)
    out: list[tuple[str, str, str, str, tuple[str, ...]]] = []
    for start, line_tokens in _statement_lines(tokens):
        parsed = _parse_statement_line(line_tokens)
        if parsed is None:
            continue
        function, target, arguments, raw = parsed
        if function.lower() in _JOURNAL_FUNCTIONS:
            continue
        conditions = condition_at[start] if start < len(condition_at) else ()
        out.append((function, target, arguments, raw, conditions))
    return out


def _calls_in_bytecode(bytecode: object, source_text: str | None) -> list[tuple[str, str, int]]:
    """Journal calls read from a Script record's compiled bytecode.

    Never raises -- a malformed or unrecognised compiled field yields no
    calls rather than taking the scan down, the same treatment the field-diff
    viewer already gives this data (see ``listing_for_bytecode_field``).

    Args:
        bytecode: The record's ``bytecode`` field, expected to be the base64
            text tes3conv writes. Anything else yields nothing.
        source_text: The record's own source, if any, passed through to
            :func:`~wraithguard.mwscript.disassembler.disassemble` to narrow
            which opcode values are trusted.

    Returns:
        ``(function, quest, index)`` tuples.
    """
    if not isinstance(bytecode, str) or not bytecode:
        return []
    try:
        data = decode_bytecode_field(bytecode)
        listing = disassemble(data, source_text=source_text or None)
    except BytecodeDecodeError:
        return []
    except Exception:  # noqa: BLE001 - field data is attacker-shaped; degrade, don't raise
        return []

    hits: list[tuple[str, str, int]] = []
    for instruction in listing.instructions:
        if instruction.name.lower() not in _JOURNAL_FUNCTIONS or len(instruction.operands) != 2:
            continue
        quest, index = instruction.operands
        if isinstance(quest, str) and isinstance(index, (int, float)):
            hits.append((_canonical(instruction.name), quest, int(index)))
    return hits


def calls_from_stages(resolved: Mapping[str, Sequence[Resolved]]) -> list[JournalCall]:
    """Every Journal call in every resolved INFO stage's own result script.

    Args:
        resolved: A whole load order's resolved quests, from
            :func:`~wraithguard.patch.journal.resolve_journals` --
            ``script_text`` on each stage is already the surviving,
            overridden-through version.

    Returns:
        The calls found, each owned by the stage's own INFO id.
    """
    calls: list[JournalCall] = []
    for stages in resolved.values():
        for stage in stages:
            if not stage.script_text:
                continue
            plugin = stage.plugins[-1] if stage.plugins else ""
            calls.extend(
                JournalCall(
                    function,
                    quest,
                    index,
                    "DialogueInfo",
                    stage.info_id,
                    plugin,
                    False,
                    conditions,
                    stage.raw,
                )
                for function, quest, index, conditions in calls_in_text_with_context(
                    stage.script_text
                )
            )
    return calls


def _winning_dialogue_infos(
    sources: Mapping[str, Sequence[Mapping[str, object]]], load_order: Sequence[str]
) -> dict[str, tuple[Mapping[str, object], str]]:
    """The surviving definition of every DialogueInfo record, any topic.

    Deliberately not filtered to journal-type topics -- most quest
    progression happens through ordinary NPC dialogue whose Result script
    sets a journal index as one of several things it does, not through the
    journal-type topic's own (usually empty) entry. An INFO id moving
    between topics across plugins is not a thing real data does, so no
    topic-id bookkeeping is needed here beyond the id itself -- the same
    assumption :mod:`wraithguard.patch.journal` already makes for
    journal-type INFOs.

    Args:
        sources: Plugin name to that plugin's records.
        load_order: The order to apply overrides in.

    Returns:
        INFO id to (its winning record, the plugin that supplied it). Same
        override rule as :func:`_winning_scripts`: a later plugin redefining
        or deleting the same id fully replaces the earlier one.
    """
    winners: dict[str, tuple[Mapping[str, object], str]] = {}
    for plugin in load_order:
        records = sources.get(plugin)
        if records is None:
            continue
        for record in records:
            if record.get("type") != "DialogueInfo":
                continue
            info_id = str(record.get("id") or "")
            if not info_id:
                continue
            if "DELETED" in str(record.get("flags") or "").upper():
                winners.pop(info_id, None)
                continue
            winners[info_id] = (record, plugin)
    return winners


def calls_from_dialogue(
    sources: Mapping[str, Sequence[Mapping[str, object]]],
    load_order: Sequence[str],
    *,
    exclude_info_ids: Collection[str] = (),
) -> list[JournalCall]:
    """Every Journal call in every ordinary dialogue response's winning script.

    Most quest progression happens here, not in a standalone script or a
    journal-type topic's own entry: an NPC's dialogue response sets the next
    journal stage as part of what talking to them does.
    :func:`calls_from_stages` only ever sees a journal-type topic's own
    (usually empty) ``script_text``; this is the source that actually
    carries most real journal-setting calls.

    Args:
        sources: Plugin name to that plugin's records.
        load_order: The order to resolve DialogueInfo overrides in.
        exclude_info_ids: INFO ids to skip -- pass every journal-type stage's
            own ``info_id`` (e.g. ``{s.info_id for stages in resolved.values()
            for s in stages}``) so a journal-type entry that does carry a
            script is not scanned twice, once here and once by
            :func:`calls_from_stages`.

    Returns:
        The calls found, in info-id order, each owned by its DialogueInfo id.
    """
    calls: list[JournalCall] = []
    for info_id, (record, plugin) in sorted(_winning_dialogue_infos(sources, load_order).items()):
        if info_id in exclude_info_ids:
            continue
        text = str(record.get("script_text") or "")
        if not text.strip():
            continue
        calls.extend(
            JournalCall(
                function, quest, index, "DialogueInfo", info_id, plugin, False, conditions, record
            )
            for function, quest, index, conditions in calls_in_text_with_context(text)
        )
    return calls


def effects_from_dialogue(
    sources: Mapping[str, Sequence[Mapping[str, object]]],
    load_order: Sequence[str],
    *,
    exclude_info_ids: Collection[str] = (),
) -> list[Effect]:
    """Every non-Journal statement in every ordinary dialogue response's winning script.

    Sibling to :func:`calls_from_dialogue`, same source and same reason it
    matters: an NPC's dialogue response is where most of "what does this
    stage actually do" -- item grants, disposition changes, other scripts
    starting -- actually lives, not in a standalone script.

    Args:
        sources: Plugin name to that plugin's records.
        load_order: The order to resolve DialogueInfo overrides in.
        exclude_info_ids: Same meaning as :func:`calls_from_dialogue`'s --
            pass the same set, so a journal-type entry's own effects are not
            double-reported alongside :func:`effects_from_stages`.

    Returns:
        The effects found, in info-id order, each owned by its DialogueInfo id.
    """
    effects: list[Effect] = []
    for info_id, (record, plugin) in sorted(_winning_dialogue_infos(sources, load_order).items()):
        if info_id in exclude_info_ids:
            continue
        text = str(record.get("script_text") or "")
        if not text.strip():
            continue
        effects.extend(
            Effect(
                function,
                target,
                arguments,
                raw,
                conditions,
                "DialogueInfo",
                info_id,
                plugin,
                record,
            )
            for function, target, arguments, raw, conditions in statements_in_text_with_context(
                text
            )
        )
    return effects


def _winning_scripts(
    sources: Mapping[str, Sequence[Mapping[str, object]]], load_order: Sequence[str]
) -> dict[str, tuple[Mapping[str, object], str]]:
    """The surviving definition of every standalone Script record.

    Args:
        sources: Plugin name to that plugin's records.
        load_order: The order to apply overrides in.

    Returns:
        Script id to (its winning record, the plugin that supplied it). A
        later plugin redefining -- or deleting -- the same id fully replaces
        the earlier one, same override rule as any other TES3 record, and the
        reason an overridden definition's calls must not be reported: they
        are dead code once overridden.
    """
    winners: dict[str, tuple[Mapping[str, object], str]] = {}
    for plugin in load_order:
        records = sources.get(plugin)
        if records is None:
            continue
        for record in records:
            if record.get("type") != "Script":
                continue
            script_id = str(record.get("id") or "")
            if not script_id:
                continue
            if "DELETED" in str(record.get("flags") or "").upper():
                winners.pop(script_id, None)
                continue
            winners[script_id] = (record, plugin)
    return winners


def calls_from_scripts(
    sources: Mapping[str, Sequence[Mapping[str, object]]], load_order: Sequence[str]
) -> list[JournalCall]:
    """Every Journal call in every standalone Script record's winning text.

    Args:
        sources: Plugin name to that plugin's records.
        load_order: The order to resolve Script overrides in.

    Returns:
        The calls found, in script-id order, each owned by its Script id.
    """
    calls: list[JournalCall] = []
    for script_id, (record, plugin) in sorted(_winning_scripts(sources, load_order).items()):
        text = str(record.get("text") or "")
        if text.strip():
            calls.extend(
                JournalCall(
                    function, quest, index, "Script", script_id, plugin, False, conditions, record
                )
                for function, quest, index, conditions in calls_in_text_with_context(text)
            )
        else:
            calls.extend(
                JournalCall(function, quest, index, "Script", script_id, plugin, True, (), record)
                for function, quest, index in _calls_in_bytecode(record.get("bytecode"), text)
            )
    return calls


@dataclass(frozen=True, slots=True)
class Effect:
    """One other thing a script does at a quest stage -- not a Journal call.

    Attributes:
        function: The statement's own function or keyword, as written --
            e.g. ``"AddItem"``, ``"StartScript"``, ``"set"``, or (for a
            target-qualified call) the part after ``->``.
        target: The reference a target-qualified call runs on, before the
            ``->``, or empty for an ordinary call.
        arguments: The statement's own argument text, verbatim -- not
            reformatted, since MWScript accepts comma- or space-separated
            arguments interchangeably and this does not have an opinion
            about which a given plugin used.
        raw: The statement's whole source line, unmodified.
        conditions: Its enclosing ``if``/``elseif``/``while`` conditions,
            outermost first -- same meaning as :attr:`JournalCall.conditions`.
        owner_type: ``"DialogueInfo"`` or ``"Script"``.
        owner_id: That record's own id.
        plugin: The plugin whose surviving definition this was read from.
        owner_raw: The complete decoded owning record -- see
            :attr:`JournalCall.owner_raw` for why it rides along unparsed.
    """

    function: str
    target: str
    arguments: str
    raw: str
    conditions: tuple[str, ...] = ()
    owner_type: str = ""
    owner_id: str = ""
    plugin: str = ""
    owner_raw: Mapping[str, object] | None = None


def effects_from_stages(resolved: Mapping[str, Sequence[Resolved]]) -> list[Effect]:
    """Every non-Journal statement in every resolved INFO stage's own result script.

    Args:
        resolved: A whole load order's resolved quests, from
            :func:`~wraithguard.patch.journal.resolve_journals` --
            ``script_text`` on each stage is already the surviving,
            overridden-through version, the same reuse :func:`calls_from_stages`
            makes.

    Returns:
        The effects found, each owned by the stage's own INFO id.
    """
    effects: list[Effect] = []
    for stages in resolved.values():
        for stage in stages:
            if not stage.script_text:
                continue
            plugin = stage.plugins[-1] if stage.plugins else ""
            effects.extend(
                Effect(
                    function,
                    target,
                    arguments,
                    raw,
                    conditions,
                    "DialogueInfo",
                    stage.info_id,
                    plugin,
                    stage.raw,
                )
                for function, target, arguments, raw, conditions in statements_in_text_with_context(
                    stage.script_text
                )
            )
    return effects


def effects_from_scripts(
    sources: Mapping[str, Sequence[Mapping[str, object]]], load_order: Sequence[str]
) -> list[Effect]:
    """Every non-Journal statement in every standalone Script record's winning text.

    Args:
        sources: Plugin name to that plugin's records.
        load_order: The order to resolve Script overrides in, reusing
            :func:`_winning_scripts` exactly as :func:`calls_from_scripts` does.

    Returns:
        The effects found, in script-id order, each owned by its Script id.
        A Script with its source stripped contributes nothing here -- unlike
        :class:`JournalCall`, which falls back to disassembled bytecode for
        exactly that case, a generic statement scan of a compiled instruction
        stream (recovering arbitrary function names and their arguments
        rather than one known opcode shape) is a different, larger problem
        this does not take on.
    """
    effects: list[Effect] = []
    for script_id, (record, plugin) in sorted(_winning_scripts(sources, load_order).items()):
        text = str(record.get("text") or "")
        if not text.strip():
            continue
        effects.extend(
            Effect(
                function, target, arguments, raw, conditions, "Script", script_id, plugin, record
            )
            for function, target, arguments, raw, conditions in statements_in_text_with_context(
                text
            )
        )
    return effects


@dataclass(frozen=True, slots=True)
class Attachment:
    """One journal call, linked to the stage it sets, when that can be told.

    Attributes:
        call: The call site itself.
        stage: The resolved stage this call's ``(quest, index)`` pair points
            to, or ``None`` when nothing matches -- a typo in the quest id, an
            index no stage's text was ever written for, or a quest this scan
            never resolved at all. A call setting a *different* quest's index
            than the one its own stage belongs to is a real, useful case, not
            an error: that is a questline link (a stage of quest A moving
            quest B along), the exact "link between quests" Balketh asked
            about -- so ``stage.info_id`` need not match ``call.owner_id``.
    """

    call: JournalCall
    stage: Resolved | None


def attach(
    calls: Sequence[JournalCall], resolved: Mapping[str, Sequence[Resolved]]
) -> list[Attachment]:
    """Link every call to the resolved stage its arguments point to.

    Matching is by quest id (case-insensitively -- TES3 ids always are) and
    journal index. Two different stages can never share an index within the
    same quest by construction (:func:`~wraithguard.patch.journal.resolve_quest`
    already keeps only one id-defined stage per index it was last given), so
    at most one stage can match.

    Args:
        calls: From :func:`calls_from_stages` and/or :func:`calls_from_scripts`.
        resolved: A whole load order's resolved quests.

    Returns:
        One :class:`Attachment` per call, in the same order.
    """
    by_quest_index: dict[tuple[str, int], Resolved] = {}
    for quest, stages in resolved.items():
        for stage in stages:
            by_quest_index[(quest.lower(), stage.index)] = stage

    return [
        Attachment(call, by_quest_index.get((call.quest.lower(), call.index))) for call in calls
    ]


__all__ = [
    "Attachment",
    "Effect",
    "JournalCall",
    "attach",
    "calls_from_dialogue",
    "calls_from_scripts",
    "calls_from_stages",
    "calls_in_text",
    "calls_in_text_with_context",
    "effects_from_dialogue",
    "effects_from_scripts",
    "effects_from_stages",
    "statements_in_text_with_context",
]
