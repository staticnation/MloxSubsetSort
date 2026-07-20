"""Translate mlox filename patterns into regular expressions.

Every ordering and predicate rule in the mlox databases names its plugins by
pattern rather than by exact filename, so this translation sits underneath all
rule matching. It follows mlox's own ``_filename_to_regex`` exactly, because
diverging by even one metacharacter would silently change which rules apply to
which plugins -- the kind of bug that produces a subtly wrong load order rather
than an error.

The rules mlox uses, and therefore the rules here:

* Only ``(``, ``)``, ``+`` and ``.`` are escaped. Everything else is passed
  through, including characters a general-purpose escaper would quote.
* ``*`` becomes ``.*`` and ``?`` becomes ``.?`` -- note ``.?``, not ``.``, so a
  ``?`` also matches the empty string.
* ``<VER>`` (in any casing) expands to a version-number sub-pattern.
* Matching is anchored at both ends and case-insensitive, since OpenMW's
  virtual file system treats plugin names case-insensitively.
"""

from __future__ import annotations

import re
from functools import cache
from typing import Final

from mlox_subset.versions import MLOX_VERSION_PATTERN

#: The only characters mlox escapes. Deliberately narrower than
#: :func:`re.escape` -- widening it would change matching behaviour.
_RE_ESCAPE_META: Final = re.compile(r"([()+.])")

#: Filename wildcards.
_RE_PLUGIN_META: Final = re.compile(r"([*?])")

#: The ``<VER>`` token, in any casing.
_RE_PLUGIN_METAVER: Final = re.compile(r"(<VER>)", re.IGNORECASE)


def pattern_has_meta(pattern: str) -> bool:
    """Whether a pattern needs regex expansion at all.

    Plain filenames are the overwhelming majority, and comparing them directly
    is both faster and exact, so callers use this to skip the regex path.

    Args:
        pattern: An mlox filename pattern.

    Returns:
        ``True`` if the pattern contains ``*``, ``?``, or a ``<VER>`` token.
    """
    return ("*" in pattern) or ("?" in pattern) or ("<ver>" in pattern.lower())


@cache
def mlox_pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile one mlox filename pattern to an anchored, case-insensitive regex.

    Cached: the same handful of patterns recur across thousands of graph edges
    during a sort, so recompiling them would dominate the run.

    A malformed pattern falls back to a fully literal match rather than
    raising. mlox escapes only ``()+.``, so a stray bracket in a rule file can
    still produce invalid regex syntax -- and one bad line in a community rule
    database must not abort the entire sort.

    Args:
        pattern: An mlox filename pattern, e.g. ``"Better Bodies*.esp"``.

    Returns:
        A compiled, anchored, case-insensitive pattern.
    """
    expanded = "^" + _RE_ESCAPE_META.sub(r"\\\1", pattern) + "$"
    expanded = _RE_PLUGIN_META.sub(r".\1", expanded)  # * -> .*  and  ? -> .?
    expanded = _RE_PLUGIN_METAVER.sub("<VER>", expanded)  # normalise casing
    expanded = expanded.replace("<VER>", MLOX_VERSION_PATTERN)
    try:
        return re.compile(expanded, re.IGNORECASE)
    except re.error:
        return re.compile("^" + re.escape(pattern) + "$", re.IGNORECASE)
