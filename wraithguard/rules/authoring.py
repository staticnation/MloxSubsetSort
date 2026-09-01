"""Writing mlox rules, to the rule-base's own standards.

The engine already *reads* every rule form (:mod:`~wraithguard.rules.parser`,
:mod:`~wraithguard.rules.expressions`). This module writes them, and enforces
the conventions the community guidelines set out for editors -- because a rule
that parses is not the same as a rule that is any good.

What the guidelines ask for, and what is checked here:

* **Every rule cites a source.** ``(Ref: ...)`` naming the readme, forum post or
  URL the claim came from, so someone else can check it. URLs must have
  whitespace around them, because pages that auto-link URLs otherwise swallow
  the closing parenthesis into the link.
* **Rules have the right shape.** ``[Order]`` needs two or more plugins;
  ``[Requires]`` and ``[Patch]`` take exactly a dependant and a consequent;
  ``[Conflict]`` needs two or more things to conflict.
* **[Patch] cannot express NOT.** The guidelines say so explicitly and give the
  remedy -- a ``[Conflict]`` and ``[Requires]`` pair -- so a NOT inside a patch
  is refused *with that remedy in the message*, rather than silently emitted as
  something mlox will not do.
* **An inline message cannot contain ``]``**, which would close the rule label
  early. The block form has no such limit, so an offending message is a signal
  to use the block form rather than an error.
* **[NearStart]/[NearEnd] are discouraged.** "Abuse of the [NearEnd] rule is
  frowned upon"; ordering belongs in ``[Order]``. Writing one is allowed and
  warned about.
* **Filename expansion is expensive.** ``?``, ``*`` and ``<VER>`` are supported
  and flagged, since the guidelines ask for them to be used sparingly.

Nothing here writes a file or touches Tk: rules are built, rendered and checked
as data, so the whole vocabulary is testable without a display. Severity is
carried on each problem so a caller can decide what is fatal -- the GUI refuses
to write on an error and shows warnings beside the preview.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

from wraithguard.rules.parser import ORDER_NAME_RE
from wraithguard.rules.patterns import MLOX_VERSION_PATTERN

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

#: Rule labels this module can write, in the order the guidelines introduce
#: them. ``Version`` is deliberately absent: it is a rule-base header, not
#: something a user writes about their own load order.
RULE_KINDS: Final[tuple[str, ...]] = (
    "Order",
    "NearStart",
    "NearEnd",
    "Note",
    "Requires",
    "Conflict",
    "Patch",
)

#: Rules that are a plain list of plugins, with no message and no expressions.
ORDERING_KINDS: Final[frozenset[str]] = frozenset({"Order", "NearStart", "NearEnd"})

#: Rules built from boolean expressions and carrying a message.
WARNING_KINDS: Final[frozenset[str]] = frozenset({"Note", "Requires", "Conflict", "Patch"})

#: Highlight prefixes. The guidelines assign meaning to each, so the levels are
#: named rather than left as a bare count of exclamation marks.
PRIORITY_MARKS: Final[dict[int, str]] = {0: "", 1: "!", 2: "!!", 3: "!!!"}

#: What each level is *for*, per the guidelines. Used by the GUI's help text so
#: the two cannot drift.
PRIORITY_MEANING: Final[dict[int, str]] = {
    0: "no highlight",
    1: "blue -- worth noticing, little or no impact on play",
    2: "yellow -- could affect the game, should be attended to",
    3: "red -- could break the mod or the game, should be fixed",
}

#: Rules mlox highlights on its own, so an explicit prefix adds nothing.
SELF_HIGHLIGHTING: Final[dict[str, str]] = {
    "Requires": "red",
    "Conflict": "yellow",
    "Patch": "yellow",
}

#: Comparison operators the ``[VER]`` predicate accepts.
VERSION_OPERATORS: Final[tuple[str, ...]] = ("<", "=", ">")

#: Characters that make a filename an expansion pattern rather than a literal.
_EXPANSION_CHARS: Final[tuple[str, ...]] = ("?", "*", "<VER>")

#: Characters a plugin name may never contain, because mlox's own reader gives
#: them structural meaning: brackets open and close expressions, ``;`` starts a
#: comment, and a newline ends the line. A name containing one is not rejected
#: for tidiness -- ``semi;colon.esp`` would be silently read as ``semi``, and a
#: rule that quietly refers to a different plugin than the one written is the
#: worst outcome available here.
_FORBIDDEN_IN_NAMES: Final[tuple[str, ...]] = ("[", "]", ";", "\n", "\r")

#: A bare URL, for the whitespace check the guidelines ask for.
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")

_VERSION_ONLY = re.compile(rf"^{MLOX_VERSION_PATTERN}$")

Severity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class Problem:
    """Something wrong with, or questionable about, a rule.

    Attributes:
        severity: ``"error"`` means mlox would misread the rule or reject it;
            ``"warning"`` means it is valid but falls short of the guidelines.
        message: What is wrong, in the terms the guidelines use.
        remedy: What to do instead, when the guidelines say. Empty otherwise --
            an invented suggestion is worse than none.
    """

    severity: Severity
    message: str
    remedy: str = ""

    def describe(self) -> str:
        """Render for display.

        Returns:
            e.g. ``"error: [Patch] cannot express NOT -- use a [Conflict] and
            [Requires] pair instead"``.
        """
        tail = f" -- {self.remedy}" if self.remedy else ""
        return f"{self.severity}: {self.message}{tail}"


# ---------------------------------------------------------------------------
# expressions
# ---------------------------------------------------------------------------


class Expr:
    """Base class for a boolean expression.

    Subclasses render themselves and report the plugins they mention. Rendering
    takes an indent because the block form of a rule nests expressions across
    lines, and mlox's parser reads leading whitespace as continuation.
    """

    def render(self, indent: int = 0) -> str:
        """Render this expression as rule text.

        Args:
            indent: How many spaces to prefix.

        Returns:
            The rendered text.

        Raises:
            NotImplementedError: Always; subclasses implement this.
        """
        raise NotImplementedError

    def plugins(self) -> list[str]:
        """List every plugin filename this expression mentions.

        Returns:
            The names, in encounter order, with duplicates kept.

        Raises:
            NotImplementedError: Always; subclasses implement this.
        """
        raise NotImplementedError

    def walk(self) -> Iterable[Expr]:
        """Yield this expression and every one nested inside it.

        Yields:
            Each expression in the tree, this one first.
        """
        yield self


@dataclass(frozen=True, slots=True)
class Plugin(Expr):
    """The basic predicate: a plugin is active in the load order.

    Attributes:
        name: The filename, which may contain ``?``, ``*`` or ``<VER>``.
    """

    name: str

    def render(self, indent: int = 0) -> str:
        """Render the filename.

        Args:
            indent: Leading spaces.

        Returns:
            The name, indented.
        """
        # Stripped for the same reason the ordering rules strip: an indented
        # line is message text to mlox, not a plugin.
        return " " * indent + self.name.strip()

    def plugins(self) -> list[str]:
        """The one name.

        Returns:
            A single-element list.
        """
        return [self.name]


@dataclass(frozen=True, slots=True)
class Desc(Expr):
    """``[DESC /regex/ plugin]`` -- the plugin's description matches a regex.

    Attributes:
        pattern: The regular expression, without its slashes.
        plugin: The plugin whose header is searched.
        negated: Whether the description must *not* match.
    """

    pattern: str
    plugin: str
    negated: bool = False

    def render(self, indent: int = 0) -> str:
        """Render the predicate on one line, as mlox requires.

        Args:
            indent: Leading spaces.

        Returns:
            The rendered predicate.
        """
        bang = "!" if self.negated else ""
        return " " * indent + f"[DESC {bang}/{self.pattern}/ {self.plugin}]"

    def plugins(self) -> list[str]:
        """The plugin it tests.

        Returns:
            A single-element list.
        """
        return [self.plugin]


@dataclass(frozen=True, slots=True)
class Size(Expr):
    """``[SIZE ### plugin]`` -- the plugin's file size in bytes.

    Attributes:
        size: The size in bytes.
        plugin: The plugin measured.
        negated: Whether the size must *not* match.
    """

    size: int
    plugin: str
    negated: bool = False

    def render(self, indent: int = 0) -> str:
        """Render the predicate on one line.

        Args:
            indent: Leading spaces.

        Returns:
            The rendered predicate.
        """
        bang = "!" if self.negated else ""
        return " " * indent + f"[SIZE {bang}{self.size} {self.plugin}]"

    def plugins(self) -> list[str]:
        """The plugin it measures.

        Returns:
            A single-element list.
        """
        return [self.plugin]


@dataclass(frozen=True, slots=True)
class Ver(Expr):
    """``[VER op version plugin]`` -- compare against the plugin's version.

    mlox reads the version from the plugin header, falling back to the
    filename.

    Attributes:
        operator: ``<``, ``=`` or ``>``.
        version: The version to compare against, in any of mlox's accepted
            forms (``1.2.3a``, ``1_3a``, ``77g``).
        plugin: The plugin compared.
    """

    operator: str
    version: str
    plugin: str

    def render(self, indent: int = 0) -> str:
        """Render the predicate on one line.

        Args:
            indent: Leading spaces.

        Returns:
            The rendered predicate.
        """
        return " " * indent + f"[VER {self.operator} {self.version} {self.plugin}]"

    def plugins(self) -> list[str]:
        """The plugin it compares.

        Returns:
            A single-element list.
        """
        return [self.plugin]


@dataclass(frozen=True, slots=True)
class Group(Expr):
    """A boolean group: ``[ALL ...]``, ``[ANY ...]`` or ``[NOT ...]``.

    Attributes:
        operator: ``ALL``, ``ANY`` or ``NOT``.
        parts: The nested expressions.
    """

    operator: str
    parts: tuple[Expr, ...]

    def render(self, indent: int = 0) -> str:
        """Render the group, nesting its parts across lines when there are several.

        A short group stays on one line because that is how the guidelines'
        own examples read; a longer one breaks, with continuation lines
        indented, which mlox's parser accepts and a person can follow.

        Args:
            indent: Leading spaces.

        Returns:
            The rendered group.
        """
        pad = " " * indent
        rendered = [part.render(0) for part in self.parts]
        one_line = f"{pad}[{self.operator} {' '.join(rendered)}]"
        if len(one_line) <= 78 or not self.parts:
            return one_line
        head = f"{pad}[{self.operator} {rendered[0]}"
        tail = [" " * (indent + 1) + text for text in rendered[1:]]
        return "\n".join([head, *tail]) + "]"

    def plugins(self) -> list[str]:
        """Every plugin mentioned anywhere inside.

        Returns:
            The names, in encounter order.
        """
        found: list[str] = []
        for part in self.parts:
            found.extend(part.plugins())
        return found

    def walk(self) -> Iterable[Expr]:
        """Yield this group and everything nested in it.

        Yields:
            Each expression in the subtree.
        """
        yield self
        for part in self.parts:
            yield from part.walk()


def all_of(*parts: Expr) -> Group:
    """Build an ``[ALL ...]`` group.

    Args:
        parts: The expressions that must all hold.

    Returns:
        The group.
    """
    return Group("ALL", tuple(parts))


def any_of(*parts: Expr) -> Group:
    """Build an ``[ANY ...]`` group.

    Args:
        parts: The expressions, any of which may hold.

    Returns:
        The group.
    """
    return Group("ANY", tuple(parts))


def not_(part: Expr) -> Group:
    """Build a ``[NOT ...]`` group.

    Args:
        part: The expression to negate.

    Returns:
        The group.
    """
    return Group("NOT", (part,))


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Rule:
    """One mlox rule, ready to render and check.

    Attributes:
        kind: One of :data:`RULE_KINDS`.
        plugins: For an ordering rule, the plugins in order. Ignored otherwise.
        expressions: For a warning rule, its expressions. Ignored otherwise.
        message: The rule's message. Ordering rules have none.
        ref: The source this rule is based on -- a readme name, a URL, a forum
            post. Rendered as ``(Ref: ...)`` inside the message.
        priority: 0-3, rendered as the ``!``/``!!``/``!!!`` highlight prefix.
        section: Optional ``@Section`` heading written above the rule.
        comment: Optional ``;`` comment lines written above the rule.
        force_block: Render the block form even when the message would fit
            inline. The block form is chosen automatically when the message is
            long, multi-line, or contains ``]``.
    """

    kind: str
    plugins: list[str] = field(default_factory=list)
    expressions: list[Expr] = field(default_factory=list)
    message: str = ""
    ref: str = ""
    priority: int = 0
    section: str = ""
    comment: str = ""
    force_block: bool = False

    def mentioned_plugins(self) -> list[str]:
        """Every plugin this rule names, from either half.

        Returns:
            The names, in encounter order, duplicates kept.
        """
        if self.kind in ORDERING_KINDS:
            return list(self.plugins)
        found: list[str] = []
        for expr in self.expressions:
            found.extend(expr.plugins())
        return found


def format_ref(ref: str) -> str:
    """Render a citation, with the whitespace the guidelines require.

    A URL immediately followed by ``)`` gets swallowed into the link by forums
    and wikis that auto-link, producing a citation nobody can follow. The
    guidelines therefore ask for whitespace around URLs, and this adds it rather
    than only complaining about its absence.

    Args:
        ref: The raw citation text.

    Returns:
        ``"(Ref: ... )"``, or an empty string when there is nothing to cite.
    """
    body = " ".join(ref.split())
    if not body:
        return ""
    if _URL.search(body):
        return f"(Ref: {body} )"
    return f"(Ref: {body})"


def _message_lines(rule: Rule) -> list[str]:
    """Build the message body, highlight prefix and citation included.

    Args:
        rule: The rule.

    Returns:
        The lines, without indentation.
    """
    mark = PRIORITY_MARKS.get(rule.priority, "")
    lines = [line.rstrip() for line in rule.message.splitlines() if line.strip()]
    if mark and lines:
        lines[0] = f"{mark} {lines[0]}"
    elif mark:
        lines = [mark]
    citation = format_ref(rule.ref)
    if citation:
        lines.append(citation)
    return lines


def _wants_block(rule: Rule, lines: Sequence[str]) -> bool:
    """Decide between the inline and block message forms.

    Args:
        rule: The rule.
        lines: Its message lines.

    Returns:
        ``True`` for the block form. Chosen when asked for, when the message
        spans lines, when it is long, or when it contains ``]`` -- which would
        close the rule label early in the inline form.
    """
    if rule.force_block or len(lines) > 1:
        return True
    if not lines:
        return False
    return "]" in lines[0] or len(lines[0]) > 60


def render_rule(rule: Rule) -> str:
    """Render a rule as the text that goes in a rules file.

    Args:
        rule: The rule to render.

    Returns:
        The rule text, without a trailing newline. Section heading and comments
        come first when present.
    """
    out: list[str] = []
    if rule.section.strip():
        # The guidelines write sections as "@Name", so someone typing the name
        # into a field labelled "@section:" may reasonably include the @. One
        # is the marker; two is a section literally called "@My Mod".
        out.append("@" + rule.section.strip().lstrip("@").strip())
    out.extend(f"; {line}".rstrip() for line in rule.comment.splitlines() if line.strip())

    if rule.kind in ORDERING_KINDS:
        out.append(f"[{rule.kind}]")
        # Stripped, because mlox reads a leading-whitespace line as message
        # text rather than as a plugin. A stray space typed into the rule maker
        # would otherwise drop that plugin from the rule with no sign anything
        # was wrong -- verified against the real loader before this was added.
        out.extend(name.strip() for name in rule.plugins)
        return "\n".join(out)

    lines = _message_lines(rule)
    if _wants_block(rule, lines):
        out.append(f"[{rule.kind}]")
        out.extend(f" {line}" for line in lines)
    elif lines:
        out.append(f"[{rule.kind} {lines[0]}]")
    else:
        out.append(f"[{rule.kind}]")
    out.extend(expr.render(0) for expr in rule.expressions)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _looks_like_plugin(name: str) -> bool:
    """Whether a name is a plugin filename or an expansion pattern for one.

    Args:
        name: The candidate.

    Returns:
        ``True`` if mlox would accept it.
    """
    candidate = name.strip()
    if not candidate:
        return False
    if any(char in candidate for char in _FORBIDDEN_IN_NAMES):
        return False
    if any(token in candidate for token in _EXPANSION_CHARS):
        # An expansion cannot be matched against the literal-name pattern; the
        # extension is what still has to look right.
        stem = candidate.replace("<VER>", "0").replace("?", "a").replace("*", "a")
        return bool(ORDER_NAME_RE.fullmatch(stem))
    return bool(ORDER_NAME_RE.fullmatch(candidate))


def _name_problem(name: str) -> Problem | None:
    """Explain why a plugin name is unusable, if it is.

    Args:
        name: The candidate name.

    Returns:
        The problem, or ``None`` when the name is fine.
    """
    if _looks_like_plugin(name):
        return None
    bad = [char for char in _FORBIDDEN_IN_NAMES if char in name]
    if bad:
        shown = ", ".join(repr(char) for char in bad)
        return Problem(
            "error",
            f"{name!r} contains {shown}, which mlox reads as rule syntax",
            "';' starts a comment and brackets delimit expressions, so the name "
            "would be read as something other than what you typed",
        )
    return Problem(
        "error",
        f"{name!r} does not look like a plugin filename",
        "it needs a .esp/.esm/.omwaddon/.omwgame/.omwscripts extension",
    )


def _check_expression(expr: Expr, problems: list[Problem]) -> None:
    """Check one expression tree, appending anything wrong.

    Args:
        expr: The root expression.
        problems: Accumulator, appended to in place.
    """
    for node in expr.walk():
        if isinstance(node, Group):
            if node.operator == "NOT" and len(node.parts) != 1:
                problems.append(
                    Problem("error", f"[NOT] takes exactly one expression, got {len(node.parts)}")
                )
            elif node.operator in ("ALL", "ANY") and not node.parts:
                problems.append(
                    Problem("error", f"[{node.operator}] needs at least one expression")
                )
            elif node.operator in ("ALL", "ANY") and len(node.parts) == 1:
                problems.append(
                    Problem(
                        "warning",
                        f"[{node.operator}] with a single expression has no effect",
                        "drop the group, or add the other expressions you meant",
                    )
                )
        elif isinstance(node, Ver):
            if node.operator not in VERSION_OPERATORS:
                problems.append(
                    Problem(
                        "error",
                        f"[VER] operator {node.operator!r} is not one of "
                        f"{', '.join(VERSION_OPERATORS)}",
                    )
                )
            if not _VERSION_ONLY.fullmatch(node.version.strip()):
                problems.append(
                    Problem(
                        "error",
                        f"{node.version!r} is not a version mlox recognises",
                        "up to three numbers separated by . _ or -, optionally "
                        "followed by a letter (1.2.3a, 1_3a, 77g)",
                    )
                )
        elif isinstance(node, Size):
            if node.size < 0:
                problems.append(Problem("error", "[SIZE] needs a byte count of zero or more"))
        elif isinstance(node, Desc):
            try:
                re.compile(node.pattern)
            except re.error as exc:
                problems.append(Problem("error", f"[DESC] regex does not compile: {exc}"))

        named = [] if isinstance(node, Group) else node.plugins()
        problems.extend(problem for problem in (_name_problem(name) for name in named) if problem)


def _check_shape(rule: Rule, problems: list[Problem]) -> None:
    """Check a rule has the number of parts its kind requires.

    Args:
        rule: The rule.
        problems: Accumulator, appended to in place.
    """
    if rule.kind not in RULE_KINDS:
        problems.append(
            Problem(
                "error",
                f"{rule.kind!r} is not a rule mlox knows",
                f"one of {', '.join(RULE_KINDS)}",
            )
        )
        return

    if rule.kind in ORDERING_KINDS:
        if rule.kind == "Order" and len(rule.plugins) < 2:
            problems.append(
                Problem(
                    "error",
                    "an [Order] rule needs at least two plugins",
                    "it states that one plugin precedes another",
                )
            )
        elif not rule.plugins:
            problems.append(Problem("error", f"a [{rule.kind}] rule needs at least one plugin"))
        return

    count = len(rule.expressions)
    if rule.kind == "Note" and count < 1:
        problems.append(Problem("error", "a [Note] rule needs at least one expression"))
    elif rule.kind == "Conflict" and count < 2:
        problems.append(
            Problem(
                "error",
                "a [Conflict] rule needs at least two expressions",
                "it warns when any two of them are true at once",
            )
        )
    elif rule.kind in ("Requires", "Patch") and count != 2:
        problems.append(
            Problem(
                "error",
                f"a [{rule.kind}] rule takes exactly two expressions, got {count}",
                "the dependant first, then what it needs",
            )
        )


def _check_conventions(rule: Rule, problems: list[Problem]) -> None:
    """Check the guideline conventions that are not about shape.

    Args:
        rule: The rule.
        problems: Accumulator, appended to in place.
    """
    if not rule.ref.strip():
        problems.append(
            Problem(
                "warning",
                "no (Ref:) citation",
                "name the readme, forum post or URL this is based on, so someone "
                "else can check it",
            )
        )
    if rule.priority not in PRIORITY_MARKS:
        problems.append(
            Problem(
                "error",
                f"highlight level {rule.priority} is not one of {sorted(PRIORITY_MARKS)}",
                "0 none, 1 blue, 2 yellow, 3 red -- anything else renders no mark "
                "at all, which is not what asking for one means",
            )
        )
    if rule.kind in SELF_HIGHLIGHTING and rule.priority:
        problems.append(
            Problem(
                "warning",
                f"[{rule.kind}] is already highlighted {SELF_HIGHLIGHTING[rule.kind]} by mlox",
                "the ! prefix adds nothing here",
            )
        )
    if rule.kind in ("NearStart", "NearEnd"):
        problems.append(
            Problem(
                "warning",
                f"[{rule.kind}] rules are discouraged by the guidelines",
                "place plugins relative to each other with [Order] where you can",
            )
        )
    if rule.kind in WARNING_KINDS and not rule.message.strip():
        problems.append(
            Problem(
                "warning",
                f"a [{rule.kind}] rule with no message tells the reader nothing",
                "say what is wrong and what to do about it",
            )
        )

    expanded = [
        name for name in rule.mentioned_plugins() if any(t in name for t in _EXPANSION_CHARS)
    ]
    if expanded:
        problems.append(
            Problem(
                "warning",
                f"filename expansion used in {', '.join(sorted(set(expanded)))}",
                "expansion is CPU intensive; the guidelines ask for it to be used sparingly",
            )
        )

    if (  # pragma: no cover - format_ref always spaces a URL before ')', so this cannot fire
        rule.ref.strip()
        and _URL.search(rule.ref)
        and not format_ref(rule.ref).endswith(" )")
    ):
        problems.append(
            Problem("warning", "a URL citation needs whitespace before the closing parenthesis")
        )


def validate(rule: Rule) -> list[Problem]:
    """Check a rule against the guidelines.

    Args:
        rule: The rule to check.

    Returns:
        Every problem found, errors and warnings together, in the order they
        were detected. An empty list means the rule is both valid and
        conventional.
    """
    problems: list[Problem] = []
    _check_shape(rule, problems)

    if rule.kind == "Patch":
        for expr in rule.expressions:
            if any(isinstance(node, Group) and node.operator == "NOT" for node in expr.walk()):
                problems.append(
                    Problem(
                        "error",
                        "[Patch] does not recognise the NOT expression",
                        "write a [Conflict] rule for what must not be present and a "
                        "[Requires] rule for what must, as the guidelines describe",
                    )
                )
                break

    if rule.kind in ORDERING_KINDS:
        problems.extend(
            problem for problem in (_name_problem(name) for name in rule.plugins) if problem
        )
        seen: set[str] = set()
        for name in rule.plugins:
            lowered = name.lower()
            if lowered in seen:
                problems.append(
                    Problem(
                        "error",
                        f"{name!r} is listed twice",
                        "a rule ordering a plugin against itself is a cycle and mlox "
                        "discards it",
                    )
                )
            seen.add(lowered)
    else:
        for expr in rule.expressions:
            _check_expression(expr, problems)

    lines = _message_lines(rule)
    if lines and not _wants_block(rule, lines) and "]" in lines[0]:  # pragma: no cover - defensive
        problems.append(
            Problem(
                "error",
                "an inline message cannot contain ']'",
                "use the block form, which has no such limit",
            )
        )

    _check_conventions(rule, problems)
    return problems


def errors(problems: Iterable[Problem]) -> list[Problem]:
    """Filter problems down to the fatal ones.

    Args:
        problems: What :func:`validate` returned.

    Returns:
        Only the errors.
    """
    return [problem for problem in problems if problem.severity == "error"]
