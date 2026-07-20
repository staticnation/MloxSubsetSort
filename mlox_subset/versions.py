"""Plugin version numbers: the regex, and mlox's canonical form.

This lives at the foundation rather than inside ``rules/`` or ``plugins/``
because both need it and neither should depend on the other. Rule patterns
expand ``<VER>`` using :data:`MLOX_VERSION_PATTERN`; plugin metadata reading
uses the same fragment to find a version in a filename or header. Putting it in
either package would create a cycle the moment the predicate evaluator (which
needs plugin metadata) moves into ``rules/``.

:func:`format_version` is a direct port of mlox's ``format_version``. Its
output is deliberately fixed-width and zero-padded so that ordinary string
comparison orders versions correctly -- which is what ``[VER < 2.0 Mod.esp]``
relies on. Changing the padding silently changes which version rules fire, so
the format is pinned by ``tests/test_differential.py``.
"""

from __future__ import annotations

import re
from typing import Final

#: mlox's ``plugin_version`` regex: digits, optional ``_.-`` separators, and an
#: optional trailing letter (``1.0``, ``2_1``, ``3.4b``).
MLOX_VERSION_PATTERN: Final = r"(\d+(?:[_.-]?\d+)*[a-zA-Z]?)"

#: Separators mlox accepts between version components.
_RE_VER_DELIM: Final = re.compile(r"[_.-]")

#: A trailing letter attached to the final numeric component (``1.0a``).
_RE_ALPHA_TAIL: Final = re.compile(r"(\d+)([a-zA-Z])", re.IGNORECASE)

#: A version embedded in a plugin filename (``Better Bodies 2.2.esp``).
RE_FILENAME_VERSION: Final = re.compile(
    r"\D" + MLOX_VERSION_PATTERN + r"\D*\.es[mp]", re.IGNORECASE
)

#: A version stated in a plugin's header description (``version 1.3``, ``v2.0``).
RE_HEADER_VERSION: Final = re.compile(
    r"\b(?:version\b\D+|v(?:er)?\.?\s*)" + MLOX_VERSION_PATTERN, re.IGNORECASE
)

#: Number of components kept. mlox keeps three and discards the rest.
_COMPONENTS: Final = 3


def format_version(version: str) -> str:
    """Canonicalise a version string into a comparable fixed-width form.

    The result is zero-padded so plain string comparison sorts correctly, with
    the alpha suffix last: ``"1.0a"`` becomes ``"00001.00000.00000.a"`` and
    ``"1.0"`` becomes ``"00001.00000.00000._"`` (``_`` sorts before letters,
    so an unsuffixed version precedes its lettered revisions).

    Only the first three components are kept, matching mlox -- ``"1.2.3.4"``
    and ``"1.2.3"`` compare equal.

    Args:
        version: A raw version string from a filename or plugin header.

    Returns:
        The canonical form, or ``""`` when the string holds no parseable
        version. Callers treat ``""`` as "version unknowable" rather than as
        a low version, which matters: mlox assumes an ``=`` comparison holds
        when it cannot determine a version, rather than inventing a warning it
        cannot substantiate.
    """
    parts = _RE_VER_DELIM.split(version, 3)
    match = _RE_ALPHA_TAIL.match(parts[-1])
    alpha = "_"
    if match:
        parts[-1] = match.group(1)
        alpha = match.group(2)
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return ""
    while len(numbers) < _COMPONENTS:
        numbers.append(0)
    return f"{numbers[0]:05d}.{numbers[1]:05d}.{numbers[2]:05d}.{alpha}"
