"""The decisions queued for a patch, and the rules for changing them.

Queuing a record is a decision, and decisions get revisited: you pick a winner,
look at three more conflicts, then realise the first one should have taken one
field from somewhere else. So the queue has to be editable, and editing it has
rules that are easy to get subtly wrong.

This holds no widgets and imports nothing from the interface. That is
deliberate and was learned the hard way twice in this codebase: logic behind a
``tkinter`` import cannot be tested without a display, and what cannot be
tested is where the bugs live.

**The rules.**

*Re-deciding replaces.* Choosing a winner for a record you already chose, or a
plugin for a field you already picked, overwrites the earlier answer. Keeping
both would put two versions of one record in the patch and leave the patch's
*own* last-wins to decide, so the answer given last might not be the one that
reaches the game.

*Whole or merged, never both.* Same reason. Choosing one drops the other.

*The base is whatever currently wins.* A merge is then a list of departures
from what the load order already does, which is the smallest thing that can be
wrong.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wraithguard.patch.merge import Merge

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from wraithguard.patch.merge import Choice
    from wraithguard.patch.records import Selection


class PatchQueue:
    """What a patch will carry, before it is written."""

    def __init__(self) -> None:
        """Start empty."""
        self._whole: list[Selection] = []
        self._fields: dict[tuple[str, str], list[Choice]] = {}

    @property
    def selections(self) -> list[Selection]:
        """Records to be taken whole, in the order chosen."""
        return self._whole

    @property
    def fields(self) -> dict[tuple[str, str], list[Choice]]:
        """Field decisions (from a plugin or literal), keyed by ``(record type, key)``."""
        return self._fields

    def __len__(self) -> int:
        """How many records the patch would carry."""
        return len(self._whole) + len(self._fields)

    def clear(self) -> None:
        """Drop every decision."""
        self._whole.clear()
        self._fields.clear()

    def add_whole(self, selection: Selection) -> None:
        """Queue a record to be taken whole.

        Args:
            selection: The record, and the plugin whose version wins.
        """
        self._drop_whole(selection.record_type, selection.key)
        self._whole.append(selection)
        self._fields.pop((selection.record_type, selection.key), None)

    def add_field(self, record_type: str, key: str, choice: Choice) -> None:
        """Queue one field of a record.

        Args:
            record_type: The record's type.
            key: Its identifying key.
            choice: The field, either taken from a plugin (``FieldChoice``) or
                given a literal value (``FieldValue``). Re-deciding the same
                path replaces the earlier answer, whichever kind either was.
        """
        choices = self._fields.setdefault((record_type, key), [])
        choices[:] = [entry for entry in choices if entry.path != choice.path]
        choices.append(choice)
        self._drop_whole(record_type, key)

    def remove_record(self, record_type: str, key: str) -> None:
        """Drop a record entirely, however it was queued.

        Args:
            record_type: The record's type.
            key: Its identifying key.
        """
        self._drop_whole(record_type, key)
        self._fields.pop((record_type, key), None)

    def remove_field(self, record_type: str, key: str, path: str) -> None:
        """Drop one field choice.

        A record left with no field choices is dropped too: it would write the
        base record unchanged, which is what the load order already does.

        Args:
            record_type: The record's type.
            key: Its identifying key.
            path: The field's dotted path.
        """
        choices = self._fields.get((record_type, key))
        if choices is None:
            return
        choices[:] = [entry for entry in choices if entry.path != path]
        if not choices:
            self._fields.pop((record_type, key), None)

    def merges(self, base_for: Callable[[str, str], str]) -> list[Merge]:
        """The queued field choices, as the writer takes them.

        Args:
            base_for: Called with ``(record_type, key)``; returns the plugin
                supplying the fields not chosen. Passed in rather than looked
                up here because it depends on the current scan, which this does
                not know about.

        Returns:
            One :class:`~wraithguard.patch.merge.Merge` per record.
        """
        return [
            Merge(
                record_type=record_type,
                key=key,
                base_plugin=base_for(record_type, key),
                choices=tuple(choices),
            )
            for (record_type, key), choices in self._fields.items()
        ]

    def _drop_whole(self, record_type: str, key: str) -> None:
        """Remove any whole-record choice for one record.

        Args:
            record_type: The record's type.
            key: Its identifying key.
        """
        self._whole[:] = [
            entry
            for entry in self._whole
            if not (entry.record_type == record_type and entry.key == key)
        ]


def base_from_conflicts(conflicts: Sequence[Mapping[str, Any]], record_type: str, key: str) -> str:
    """Find which plugin currently wins a record, from a scan's conflict list.

    Args:
        conflicts: The conflicts as the scanner reports them.
        record_type: The record's type.
        key: Its identifying key.

    Returns:
        The plugin that loads last among those defining it, or an empty string
        when the record is not in the list -- which means the queue has
        outlived a rescan, and is worth reporting rather than guessing at.
    """
    for conflict in conflicts:
        if str(conflict.get("type")) == record_type and str(conflict.get("id")) == key:
            plugins = conflict.get("plugins") or [""]
            return str(plugins[-1])
    return ""
