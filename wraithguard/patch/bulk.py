"""Take one field from a source plugin across *every* record it defines.

The per-field merge answers one record at a time: pick this weapon's weight
from that plugin. Doing it by hand for a source plugin that fixes the same
field in a hundred records -- a patch that rebalances every creature's health,
say -- is a hundred clicks. This turns that into one decision: *take this
field, wherever this plugin defines a record that disagrees.*

It is deliberately only enumeration. Given the records the scanner already
found and the values the diff already read, it decides which
:class:`~wraithguard.patch.merge.FieldChoice` decisions to queue and leaves
every other part alone -- the queue de-dupes and re-decides them, the merge
writes them, the writer re-indexes them. No new writing path exists; the bulk
button feeds the same queue the single-field button does.

A decision is emitted for a record only when all of these hold, so the patch
stays the smallest thing that changes what the user asked to change:

* the source plugin actually **defines** that record (an override or the
  original -- either way it has a version of the field to give);
* the source is **not already the winner** (if it were, its value is what
  loads now and forcing it would change nothing);
* the field is **not an identity field** (``type``/``id``/a cell's grid say
  *which record this is*; taking them would make a different record, not a
  patched one -- the same rule :data:`wraithguard.patch.merge.IDENTITY`
  enforces at write time);
* the source's value actually **differs** from the current winner's (equal
  values need no patch -- the load order already yields that value).

This module imports no widgets and is tested without a display; the dialog in
:mod:`wraithguard.gui.conflicts` reads the records and calls
:func:`bulk_field_choices`, then queues what it returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wraithguard.patch.merge import FieldChoice

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

#: Fields that name *which record this is*, refused before a value is taken.
#: Mirrors :data:`wraithguard.patch.merge.IDENTITY` and the conflict window's
#: own ``_IDENTITY_FIELDS`` so the bulk path and the single-field path agree.
IDENTITY_FIELDS: frozenset[str] = frozenset({"type", "id", "grid", "data.grid"})


@dataclass(frozen=True, slots=True)
class BulkRecord:
    """One conflicting record, with the field values each plugin gives it.

    Exactly what the conflict scanner and the diff reader already produce:
    ``plugins`` is the record's definers in load order (the last one wins), and
    ``values`` maps each of those plugins to its ``{path: value}`` for the
    record. The GUI builds these from ``conflict["plugins"]`` and the ``per``
    mapping :meth:`read_fields_now` returns; tests build them by hand.

    Attributes:
        record_type: The record's type tag, e.g. ``"Weapon"``.
        key: Its identifying key (the record ``id``, or a cell's key).
        plugins: The plugins defining it, in load order, winner last.
        values: Per plugin, its ``{path: value}`` for this record.
    """

    record_type: str
    key: str
    plugins: tuple[str, ...]
    values: Mapping[str, Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class BulkFieldDecision:
    """One queued outcome: take ``choice`` for ``(record_type, key)``.

    A thin carrier so the caller can feed
    :meth:`wraithguard.patch.queue.PatchQueue.add_field` without unpacking a
    bare tuple, and so a preview can count and describe them.
    """

    record_type: str
    key: str
    choice: FieldChoice


def bulk_field_choices(
    records: Iterable[BulkRecord],
    source_plugin: str,
    field_paths: Sequence[str],
    *,
    identity_fields: frozenset[str] = IDENTITY_FIELDS,
) -> list[BulkFieldDecision]:
    """Decide which field takes to queue for a source plugin, in one sweep.

    For every record the source defines but does not already win, and every
    requested field the source carries with a value the current winner does not
    already have, emit a :class:`~wraithguard.patch.merge.FieldChoice` taking
    that field from the source. Identity fields are skipped. See the module
    docstring for why each condition exists.

    Args:
        records: The conflicting records to consider, each with its per-plugin
            field values. Order is preserved in the result.
        source_plugin: The plugin to take fields from.
        field_paths: The dotted field paths to take, in the order to emit them.
        identity_fields: Paths that name the record and must never be taken;
            defaults to :data:`IDENTITY_FIELDS`.

    Returns:
        The decisions to queue, in ``records`` order then ``field_paths`` order.
        Empty when the source wins or defines nothing in ``records``, or when no
        requested field differs.
    """
    wanted = [p for p in field_paths if p not in identity_fields]
    decisions: list[BulkFieldDecision] = []
    if not wanted:
        return decisions
    for record in records:
        if not record.plugins:
            continue
        winner = record.plugins[-1]
        if winner == source_plugin:
            continue  # the source already loads last: forcing it changes nothing
        source_values = record.values.get(source_plugin)
        if source_values is None:
            continue  # the source does not define this record
        winner_values = record.values.get(winner, {})
        for path in wanted:
            if path not in source_values:
                continue  # the source's record has no such field to give
            if source_values[path] == winner_values.get(path):
                continue  # already the winning value -- no patch needed
            decisions.append(
                BulkFieldDecision(
                    record_type=record.record_type,
                    key=record.key,
                    choice=FieldChoice(path=path, plugin=source_plugin),
                )
            )
    return decisions
