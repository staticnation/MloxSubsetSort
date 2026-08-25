"""Build one record out of several, field by field.

Carrying a whole record settles a conflict by picking a side. Sometimes neither
side is right: one mod fixed the script, another retextured the mesh, and what
you want is a record that has both. That is what this does, and it is what
TES3Edit and TES3 Conflict Solver call merging down.

**Fields are the ones the diff panel already shows.** ``flatten_dict`` produces
dotted paths for nested dictionaries and keeps *lists whole* -- so
``references`` is one value, not one path per reference. That is a deliberate
inheritance from TES3 Conflict Solver, and it is what makes this tractable: a
choice is always a whole value at a path, never half of one.

**Two things are refused rather than merged.**

*Identity.* ``type``, ``id`` and the grid say which record this is. Taking them
from a different plugin does not merge a record; it silently turns it into a
different one, which the patch would then apply somewhere the user never looked
at.

*A field the chosen plugin does not have.* It is genuinely ambiguous -- it
could mean "delete this field" or "I misread the panel" -- and guessing wrong
writes a record no author ever produced.

**References follow their own source.** If the ``references`` list is taken
from a different plugin than the rest of the record, its ``mast_index`` values
belong to *that* plugin's master list and are remapped against it. Using the
base record's mapping would repoint every object in the cell, which is the same
failure :mod:`wraithguard.patch.records` exists to prevent, arriving by a
different door.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from wraithguard.patch.records import (
    PatchError,
    index_map,
    master_names,
    record_key,
    remap_reference_list,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: Separator ``flatten_dict`` uses, and therefore the one a path arrives with.
SEP: Final = "."

#: Paths that say *which record this is*. A merge may not take them from
#: elsewhere: that makes a different record rather than a merged one.
IDENTITY: Final[frozenset[str]] = frozenset({"type", "id", "grid", "data.grid"})

#: Fields whose values are numbered against a plugin's master list.
_MASTER_INDEXED_PATHS: Final[frozenset[str]] = frozenset({"references"})


@dataclass(frozen=True, slots=True)
class FieldChoice:
    """One field of a record, and whose version of it to use.

    Attributes:
        path: The dotted path, exactly as the diff panel labels it.
        plugin: The plugin to take that field's value from.
    """

    path: str
    plugin: str


@dataclass(frozen=True, slots=True)
class FieldValue:
    """One field of a record, and a literal value the user typed for it.

    The third way to settle a field, besides carrying a whole record or taking
    the field from another plugin (:class:`FieldChoice`): supply the value
    directly, so a patch can hold a number or string that *no* plugin in the
    conflict uses -- a compromise weight, a corrected health, a name of your
    own. The value is written verbatim, exactly as given; unlike a
    :class:`FieldChoice` it belongs to no plugin's master list, so it is never
    reindexed (a literal reference list is taken as typed -- the user owns its
    correctness).

    Attributes:
        path: The dotted path, exactly as the diff panel labels it.
        value: The value to write there, already parsed to the field's type.
    """

    path: str
    value: object


#: The two ways to settle one field of a record: take it from a plugin, or type
#: it. Both carry a ``path``, so the queue keys and de-dupes them the same way.
Choice = FieldChoice | FieldValue


@dataclass(frozen=True, slots=True)
class Merge:
    """One record to build from several, and how.

    Attributes:
        record_type: The record's ``type``.
        key: What identifies it within that type.
        base_plugin: Supplies every field not chosen. Usually the plugin that
            currently wins, so a merge reads as departures from what the load
            order already does.
        choices: The fields to settle -- each taken from a plugin
            (:class:`FieldChoice`) or given a literal value (:class:`FieldValue`).
    """

    record_type: str
    key: str
    base_plugin: str
    choices: tuple[Choice, ...]

    @property
    def plugins(self) -> set[str]:
        """Every plugin this merge reads from, base included.

        Literal :class:`FieldValue` fields belong to no plugin, so they add
        nothing here -- the patch needs a master only for fields it *sources*.
        """
        return {self.base_plugin} | {
            choice.plugin for choice in self.choices if isinstance(choice, FieldChoice)
        }


def value_at(record: Mapping[str, Any], path: str) -> tuple[Any, bool]:
    """Read a dotted path out of a record.

    Args:
        record: A decoded record.
        path: A dotted path, as ``flatten_dict`` produces.

    Returns:
        The value and ``True``, or ``(None, False)`` when the path is not
        there. The flag is separate because ``None`` is a legitimate value and
        must not be confused with absence.
    """
    current: Any = record
    for part in path.split(SEP):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def set_at(record: dict[str, Any], path: str, value: object) -> None:
    """Write a dotted path into a record, in place.

    Intermediate dictionaries are created when missing, so a field one plugin
    has and the base does not can still be merged in.

    Args:
        record: The record being built. Modified.
        path: The dotted path.
        value: What to put there.

    Raises:
        PatchError: If the path runs through something that is not a
            dictionary, which would mean overwriting a value with a structure.
    """
    parts = path.split(SEP)
    current: Any = record
    for part in parts[:-1]:
        nxt = current.get(part)
        if nxt is None:
            nxt = {}
            current[part] = nxt
        if not isinstance(nxt, dict):
            raise PatchError(f"cannot set {path}: {part} is not a group of fields in this record.")
        current = nxt
    current[parts[-1]] = value


def merge_record(
    base_plugin: str,
    record_type: str,
    key: str,
    choices: Sequence[Choice],
    records_by_plugin: Mapping[str, Sequence[Mapping[str, Any]]],
    patch_masters: Sequence[str],
) -> dict[str, Any]:
    """Build one record from a base plus chosen fields from other plugins.

    Args:
        base_plugin: The plugin supplying everything not chosen. Usually the
            one that currently wins, so a merge is a set of departures from
            what the load order already does.
        record_type: The record's ``type``.
        key: What identifies it, as :func:`~wraithguard.patch.records.record_key`
            computes.
        choices: The fields to take from elsewhere. A choice naming
            ``base_plugin`` is allowed and does nothing, so a caller may pass
            every field it displayed without filtering.
        records_by_plugin: Each plugin's decoded records, headers included.
        patch_masters: The master list the patch will declare, in order.

    Returns:
        The merged record, with every reference numbered for the patch.

    Raises:
        PatchError: If a plugin or record is missing, if a chosen field is not
            in the plugin chosen for it, or if a choice touches identity.
    """
    base = _find(base_plugin, record_type, key, records_by_plugin)
    maps: dict[str, dict[int, int]] = {}

    def mapping_for(plugin: str) -> dict[int, int]:
        """The index map for one plugin, built once and reused.

        A merge can take fields from several plugins, and each needs its *own*
        mapping -- that is the whole hazard this module exists for. Caching is
        not just speed: it keeps one mapping per plugin, so two fields taken
        from the same source cannot end up renumbered differently.

        Args:
            plugin: The file the field is being taken from.

        Returns:
            Its ``mast_index`` mapping into the patch's master list.
        """
        found = maps.get(plugin)
        if found is None:
            found = index_map(
                plugin, master_names(records_by_plugin.get(plugin) or []), patch_masters
            )
            maps[plugin] = found
        return found

    merged = copy.deepcopy(dict(base))
    if isinstance(merged.get("references"), list):
        merged["references"] = remap_reference_list(merged["references"], mapping_for(base_plugin))

    for choice in choices:
        if isinstance(choice, FieldValue):
            if choice.path in IDENTITY:
                raise PatchError(
                    f"{choice.path} says which record this is. Giving it a typed "
                    "value would not merge this record, it would make a different one."
                )
            # Written exactly as given: a literal belongs to no plugin's master
            # list, so it is never reindexed.
            set_at(merged, choice.path, copy.deepcopy(choice.value))
            continue

        if choice.plugin == base_plugin:
            continue
        if choice.path in IDENTITY:
            raise PatchError(
                f"{choice.path} says which record this is. Taking it from "
                f"{choice.plugin} would not merge this record, it would make a "
                "different one."
            )

        source = _find(choice.plugin, record_type, key, records_by_plugin)
        value, present = value_at(source, choice.path)
        if not present:
            raise PatchError(
                f"{choice.plugin} has no {choice.path} in this record, so there "
                "is nothing to take. Choose a plugin that has the field, or "
                "leave it as the base has it."
            )

        if choice.path in _MASTER_INDEXED_PATHS and isinstance(value, list):
            # Numbered against *its own* plugin, not the base's.
            value = remap_reference_list(value, mapping_for(choice.plugin))
        set_at(merged, choice.path, copy.deepcopy(value))

    return merged


def _find(
    plugin: str,
    record_type: str,
    key: str,
    records_by_plugin: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Mapping[str, Any]:
    """Locate one record in one plugin.

    Args:
        plugin: The plugin to look in.
        record_type: The record's type.
        key: Its identifying key.
        records_by_plugin: Every plugin's decoded records.

    Returns:
        The record.

    Raises:
        PatchError: If the plugin was not read, or does not define it.
    """
    records = records_by_plugin.get(plugin)
    if records is None:
        raise PatchError(f"no records were read for {plugin}")
    found = next(
        (
            record
            for record in records
            if record.get("type") == record_type and record_key(record) == key
        ),
        None,
    )
    if found is None:
        raise PatchError(f"{plugin} has no {record_type} record {key!r}")
    return found


def describe(choices: Sequence[Choice], base_plugin: str) -> list[str]:
    """Summarise a merge for a log or a confirmation.

    Args:
        choices: The field choices -- from a plugin, or given a literal value.
        base_plugin: The plugin everything else comes from.

    Returns:
        One line per field actually changed (taken from another plugin, or set
        to a typed value), so a user can see what they are about to write rather
        than a count.
    """
    lines: list[str] = []
    for choice in choices:
        if isinstance(choice, FieldValue):
            lines.append(f"{choice.path}: set to {choice.value!r}")
        elif choice.plugin != base_plugin:
            lines.append(f"{choice.path}: from {choice.plugin}")
    return lines or [f"nothing taken from elsewhere; this is {base_plugin}'s record whole"]
