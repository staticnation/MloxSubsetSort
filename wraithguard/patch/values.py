"""Parse a user-typed value into the type a record field expects.

The patch builder lets a field be settled three ways: carry a whole record,
take the field from another plugin, or **type a value directly**. This last one
needs a gatekeeper. tes3conv's JSON is typed -- ``data.health`` is an integer,
``data.weight`` a float, ``name`` a string, a flag a boolean -- and writing the
wrong Python type back produces a record tes3conv cannot turn into a plugin, or
worse, one it can but the engine reads as garbage.

So a typed value is parsed against the field's *current* value: whatever type it
is now is the type the input must become. Numbers and booleans are parsed from
their text; strings are taken verbatim (their spaces can matter); lists, groups
and null are entered as JSON and must keep the same container shape. Anything
that will not parse is refused with a message that says what the field wanted,
rather than being written and discovered later as a broken patch.

This holds no widgets: the dialog collects the text, this decides what it means,
and the decision is unit-tested without a display.
"""

from __future__ import annotations

import json
from typing import Final

from wraithguard.patch.records import PatchError

#: Text accepted for a boolean field, lower-cased.
_TRUE: Final[frozenset[str]] = frozenset({"true", "1", "yes", "on"})
_FALSE: Final[frozenset[str]] = frozenset({"false", "0", "no", "off"})


def parse_field_value(text: str, current: object, present: bool) -> object:
    """Parse ``text`` into a value typed to match a field's current value.

    The type to hit is read from ``current`` when the field is present:

    * ``bool`` -- ``true``/``false`` (also ``1``/``0``, ``yes``/``no``,
      ``on``/``off``), case-insensitive. Checked before ``int`` because
      ``bool`` is a subclass of it.
    * ``int`` -- a whole number.
    * ``float`` -- a number.
    * ``str`` -- taken verbatim, not stripped: leading or trailing spaces can be
      part of the intended value.
    * ``list`` / ``dict`` -- entered as JSON and required to stay a JSON array /
      object, so a list is never quietly replaced by a scalar.

    When the field is absent (``present`` is ``False``) or currently ``null``,
    the text is parsed as a free JSON literal, since there is no existing type to
    match.

    Args:
        text: What the user typed.
        current: The field's current value, used only to decide the target type.
        present: Whether the field exists in the record. ``None`` is a real
            value, so absence is signalled separately.

    Returns:
        The parsed value, ready to hand to a ``FieldValue``.

    Raises:
        PatchError: If the text will not parse to the field's type, with a
            message naming what the field expected.
    """
    stripped = text.strip()
    if present and isinstance(current, bool):
        low = stripped.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise PatchError(f"{text!r} is not a true/false value.")
    if present and isinstance(current, int):  # bool already handled above
        try:
            return int(stripped)
        except ValueError:
            raise PatchError(f"{text!r} is not a whole number.") from None
    if present and isinstance(current, float):
        try:
            return float(stripped)
        except ValueError:
            raise PatchError(f"{text!r} is not a number.") from None
    if present and isinstance(current, str):
        return text
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise PatchError(f"{text!r} is not valid JSON for this field ({exc.msg}).") from exc
    if present and isinstance(current, list) and not isinstance(parsed, list):
        raise PatchError("this field is a list -- enter a JSON array, e.g. [1, 2, 3].")
    if present and isinstance(current, dict) and not isinstance(parsed, dict):
        raise PatchError('this field is a group -- enter a JSON object, e.g. {"key": 1}.')
    return parsed


def parse_typed_value(text: str, kind: str) -> object:
    """Parse ``text`` against a crate-declared field *kind*, not a sample value.

    Where :func:`parse_field_value` infers the target type from whatever the
    field currently holds, this trusts the schema: the field *is* a ``u16`` or an
    ``f32`` or a flags string regardless of what value happens to be there now (or
    whether one is there at all). The dialog uses this when the field's kind is
    known, so an absent integer still refuses ``3.5`` and still enforces its
    range.

    Kinds and how each is read:

    * ``int:<min>:<max>`` -- a whole number, required to fall within the inclusive
      bounds.
    * ``float`` -- a number.
    * ``bool`` -- ``true``/``false`` (and the other spellings
      :func:`parse_field_value` accepts).
    * ``list`` -- entered as JSON and required to stay a JSON array.
    * ``str`` / ``enum`` / ``flags:<Name>`` -- a plain string, taken verbatim. The
      flags widget composes the `` | ``-joined string itself; an enum's value is
      one of its names; a string is whatever was typed.

    Args:
        text: What the user typed (or the widget composed).
        kind: The field's kind, as :func:`wraithguard.patch.fieldtypes.field_kind`
            returns.

    Returns:
        The parsed value, ready to hand to a ``FieldValue``.

    Raises:
        PatchError: If the text does not fit the kind, naming what was expected.
    """
    stripped = text.strip()
    if kind.startswith("int:"):
        _, low, high = kind.split(":")
        try:
            value = int(stripped)
        except ValueError:
            raise PatchError(f"{text!r} is not a whole number.") from None
        lo, hi = int(low), int(high)
        if not lo <= value <= hi:
            raise PatchError(f"must be a whole number from {lo} to {hi}.")
        return value
    if kind == "float":
        try:
            return float(stripped)
        except ValueError:
            raise PatchError(f"{text!r} is not a number.") from None
    if kind == "bool":
        low = stripped.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise PatchError(f"{text!r} is not a true/false value.")
    if kind == "list":
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise PatchError(f"{text!r} is not valid JSON for this field ({exc.msg}).") from exc
        if not isinstance(parsed, list):
            raise PatchError("this field is a list -- enter a JSON array, e.g. [1, 2, 3].")
        return parsed
    return text
