"""Look up a patch field's declared type, and read/write its flag strings.

The type table lives in :mod:`wraithguard.patch.field_types`, generated from the
tes3 crate by ``tools/gen_tes3_fieldtypes.py``. This is the lookup over it plus
the small string handling flags need. All pure: the dialog collects widgets,
this decides what a field *is*, and it is tested without a display.

A *kind* is one of ``float``, ``int:<min>:<max>``, ``str``, ``bool``, ``enum``,
``flags:<FlagsName>``, or ``list``. tes3conv writes a flags value as the flag
names joined by `` | `` (empty when none), and can leave a raw ``0x..`` token for
bits no named flag covers -- which is preserved untouched rather than dropped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wraithguard.patch.field_types import FLAG_VARIANTS, RECORD_FIELDS

if TYPE_CHECKING:
    from collections.abc import Sequence

#: How a flags value is joined in tes3conv's JSON.
_SEP = " | "


def field_kind(record_type: str, path: str) -> str | None:
    """The crate-declared kind of a field, or ``None`` when it is not known.

    Args:
        record_type: The tes3conv record ``type`` (e.g. ``"Weapon"``).
        path: The flattened field path (e.g. ``"data.health"``).

    Returns:
        The kind string, or ``None`` -- the caller then falls back to the
        field's current-value type.
    """
    return RECORD_FIELDS.get(record_type, {}).get(path)


def int_bounds(kind: str) -> tuple[int, int] | None:
    """The ``(min, max)`` of an ``int:min:max`` kind, or ``None``.

    Args:
        kind: A kind string.

    Returns:
        The inclusive bounds for an integer field, else ``None``.
    """
    if kind.startswith("int:"):
        _, low, high = kind.split(":")
        return int(low), int(high)
    return None


def flags_name(kind: str) -> str | None:
    """The bitflags set name of a ``flags:Name`` kind, or ``None``.

    Args:
        kind: A kind string.

    Returns:
        The flags set name (a key into :func:`flag_options`), else ``None``.
    """
    prefix = "flags:"
    return kind[len(prefix) :] if kind.startswith(prefix) else None


def flag_options(name: str) -> tuple[str, ...]:
    """The flag names of a bitflags set, in declaration order.

    Args:
        name: A bitflags set name, as :func:`flags_name` returns.

    Returns:
        Its flag names, or ``()`` if the set is unknown.
    """
    return FLAG_VARIANTS.get(name, ())


def split_flags(text: str, known: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split a `` | ``-joined flag string into its known and unknown parts.

    Args:
        text: The current flags value, e.g. ``"AUTO_CALCULATE | 0xfffe"``.
        known: The flag names the field's set defines.

    Returns:
        ``(enabled, unknown)`` -- the recognised flags that are set, and any raw
        tokens (like ``0xfffe``) for bits no named flag covers, kept verbatim.
    """
    known_set = set(known)
    enabled: list[str] = []
    unknown: list[str] = []
    for token in (part.strip() for part in text.split("|")):
        if not token:
            continue
        (enabled if token in known_set else unknown).append(token)
    return enabled, unknown


def join_flags(enabled: Sequence[str], unknown: Sequence[str]) -> str:
    """Compose a flag string from checked flags plus preserved unknown tokens.

    Args:
        enabled: The flag names that are checked.
        unknown: Raw tokens carried over from the original value.

    Returns:
        The `` | ``-joined value, or ``""`` when nothing is set.
    """
    return _SEP.join([*enabled, *unknown])
