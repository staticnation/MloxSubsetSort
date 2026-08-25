"""Copy whole records out of one plugin and into a patch.

**What a record patch is.** TES3 resolves a record by last-wins: whichever
loaded file defines it last is the one the game uses, whole. So to make a
chosen plugin's version of a record win, a patch does not need to describe a
difference -- it carries that record verbatim and loads last. Everything the
patch does not carry still comes from the original mods, untouched. That is the
whole mechanism, and it is why a patch can be small.

**The part that is not simple: ``mast_index``.** Every reference inside a
``Cell`` carries one, and it does not name a file -- it is a position:

* ``0`` means *this plugin*, the file the record is being read from.
* ``k >= 1`` means the ``k``-th entry of **that file's own master list**.

Measured on real plugins: ``Clean Solstheim_Castle_v1.1`` declares three
masters and uses indices 0 to 3, with 11,972 references at 0 -- its own placed
objects. ``Bloodmoon`` declares one master and every one of its 26,473
references sits at 0.

Copying such a record into a patch changes what "this plugin" means and
renumbers every master. Left alone, each reference silently comes to mean a
different file: placed objects point at the wrong things, and nothing reports
it. So references are remapped here rather than trusted, and a record whose
references cannot be remapped is refused rather than written wrong.

Nothing in this module writes a file. It builds the record list;
:mod:`wraithguard.land.emit` turns that into a plugin, because a plugin is a
plugin whatever its records are.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

#: The record type that carries the plugin header.
HEADER_TYPE: Final = "Header"

#: Record types whose contents index the master list. Only ``Cell`` does, via
#: its references, but naming it as a set keeps the check honest if another
#: type ever joins it.
_MASTER_INDEXED: Final[frozenset[str]] = frozenset({"Cell"})

#: A dialogue response. It is not a free-standing record: it belongs to the
#: most recent ``Dialogue`` in file order, which is how the engine learns what
#: topic it answers. Verified on a real plugin -- all 458 of its responses
#: follow a ``Dialogue``, none is orphaned.
INFO_TYPE: Final = "DialogueInfo"

#: The topic a response belongs to.
DIALOGUE_TYPE: Final = "Dialogue"

#: A topic whose responses are chosen by position alone. Greetings are numbered
#: buckets, ``Greeting 0`` to ``Greeting 9``, and every author's instruction for
#: them is a placement: "place this at the top of the greetings". There is no
#: identifier to match on, so for these the position *is* the record's meaning.
GREETING: Final = "Greeting"

#: How a ``Dialogue`` record says what kind of topic it is. tes3conv writes it
#: flat, but a nested ``data`` group is read too rather than assumed away.
_KIND_FIELD: Final = "dialogue_type"


class PatchError(Exception):
    """Raised when a record cannot be carried into a patch safely."""


@dataclass(frozen=True, slots=True)
class Selection:
    """One record to take, and which plugin to take it from.

    Attributes:
        plugin: The file name of the plugin whose version should win.
        record_type: The record's ``type`` as tes3conv writes it.
        key: What identifies the record within that type -- an ``id`` for most,
            a grid for cells and landscapes. Compared with :func:`record_key`.
    """

    plugin: str
    record_type: str
    key: str


def record_key(record: Mapping[str, Any]) -> str:
    """Identify a record within its type.

    Most records are identified by ``id``. Exterior cells and landscapes have
    none and are identified by their grid, which is why this exists rather than
    reading ``id`` at every call site.

    Args:
        record: A decoded record.

    Returns:
        A stable key, empty when the record carries nothing to identify it.
    """
    identifier = record.get("id")
    if isinstance(identifier, str) and identifier:
        return identifier

    grid = record.get("grid")
    if grid is None:
        data = record.get("data")
        if isinstance(data, dict):
            grid = data.get("grid")
    if isinstance(grid, (list, tuple)) and len(grid) == 2:
        return f"({int(grid[0])}, {int(grid[1])})"
    return ""


def master_names(records: Sequence[Mapping[str, Any]]) -> list[str]:
    """Read a plugin's declared masters, in order.

    Args:
        records: The plugin's decoded records.

    Returns:
        The master file names. Empty when there is no header or no masters --
        both are legitimate for a plugin that depends on nothing.
    """
    for record in records:
        if record.get("type") != HEADER_TYPE:
            continue
        masters = record.get("masters")
        if not isinstance(masters, list):
            return []
        return [str(entry[0]) for entry in masters if isinstance(entry, (list, tuple)) and entry]
    return []


def index_map(
    source: str, source_masters: Sequence[str], patch_masters: Sequence[str]
) -> dict[int, int]:
    """Work out how one plugin's ``mast_index`` values read in a patch.

    Args:
        source: The plugin the records are being taken from.
        source_masters: That plugin's own master list, in order.
        patch_masters: The master list the patch will declare, in order.

    Returns:
        Old index to new index, covering ``0`` (the source plugin itself) and
        every one of its masters.

    Raises:
        PatchError: If a file the source references is not in the patch's
            master list. Emitting the record anyway would repoint every
            reference that used it.
    """
    lowered = {name.lower(): position for position, name in enumerate(patch_masters, start=1)}

    def position_of(name: str) -> int:
        """Where one file sits in the patch's master list.

        Args:
            name: The file being looked for.

        Returns:
            Its one-based position.

        Raises:
            PatchError: If the patch does not declare it. Returning a guess
                would repoint every reference that used it, silently.
        """
        found = lowered.get(name.lower())
        if found is None:
            raise PatchError(
                f"{name} is referenced by records taken from {source} but is not "
                "in the patch's master list. Every reference to it would point "
                "somewhere else, so the record is refused rather than written wrong."
            )
        return found

    mapping = {0: position_of(source)}
    for old, name in enumerate(source_masters, start=1):
        mapping[old] = position_of(name)
    return mapping


def remap_references(record: Mapping[str, Any], mapping: Mapping[int, int]) -> dict[str, Any]:
    """Rewrite a record's ``mast_index`` values for its new home.

    Args:
        record: The record as it appears in the source plugin.
        mapping: From :func:`index_map`.

    Returns:
        A deep copy with every reference renumbered. The original is not
        touched, because the caller may still be showing it.

    Raises:
        PatchError: If a reference carries an index the mapping does not cover,
            which means the source plugin declared fewer masters than it uses.
    """
    copied = copy.deepcopy(dict(record))
    references = copied.get("references")
    if isinstance(references, list):
        copied["references"] = remap_reference_list(references, mapping)
    return copied


def remap_reference_list(references: Sequence[Any], mapping: Mapping[int, int]) -> list[Any]:
    """Renumber a bare list of references.

    Separate from :func:`remap_references` because a field-level merge can take
    the ``references`` list from one plugin while the rest of the record comes
    from another -- and then the list must be remapped against *its own*
    plugin's master list, not the base record's.

    Args:
        references: The list as it appears in its source plugin.
        mapping: From :func:`index_map`, for that source plugin.

    Returns:
        A deep copy with every index renumbered.

    Raises:
        PatchError: If a reference carries an index the mapping does not cover.
    """
    copied = copy.deepcopy(list(references))
    for reference in copied:
        if not isinstance(reference, dict):
            continue
        old = reference.get("mast_index")
        if not isinstance(old, int):
            continue
        new = mapping.get(old)
        if new is None:
            raise PatchError(
                f"a reference uses mast_index {old}, which its plugin's master "
                "list does not reach. The plugin is inconsistent; patching it "
                "would guess at what that index meant."
            )
        reference["mast_index"] = new
    return copied


def needs_remapping(record: Mapping[str, Any]) -> bool:
    """Whether a record's meaning depends on the master list.

    Args:
        record: A decoded record.

    Returns:
        ``True`` when it carries master-indexed references.
    """
    return str(record.get("type")) in _MASTER_INDEXED and bool(record.get("references"))


def owning_dialogue(
    records: Sequence[Mapping[str, Any]], record_type: str, key: str
) -> Mapping[str, Any] | None:
    """Find the topic a dialogue response belongs to.

    A ``DialogueInfo`` carries no topic of its own. The engine attaches it to
    the last ``Dialogue`` it read, so the topic is whichever one precedes the
    response in the plugin. Carrying the response into a patch without it
    leaves the engine no topic to attach it to.

    Args:
        records: The source plugin's records, in file order.
        record_type: The record's type.
        key: Its identifying key.

    Returns:
        The owning ``Dialogue`` record, or ``None`` when the record is not a
        response or nothing precedes it.
    """
    if record_type != INFO_TYPE:
        return None
    topic: Mapping[str, Any] | None = None
    for record in records:
        if record.get("type") == DIALOGUE_TYPE:
            topic = record
        elif record.get("type") == INFO_TYPE and record_key(record) == key:
            return topic
    return None


def topic_kind(topic: Mapping[str, Any] | None) -> str:
    """What kind of topic a ``Dialogue`` record is.

    Args:
        topic: A ``Dialogue`` record, or ``None``.

    Returns:
        ``"Greeting"``, ``"Topic"``, ``"Journal"``, ``"Voice"``, or an empty
        string when it cannot be told.
    """
    if topic is None:
        return ""
    flat = topic.get(_KIND_FIELD)
    if flat is None:
        nested = topic.get("data")
        flat = nested.get(_KIND_FIELD) if isinstance(nested, dict) else None
    return str(flat or "")


def defining_plugins(
    sources: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[str]]:
    """Which plugins define each dialogue response.

    Args:
        sources: Plugin name to that plugin's records, in file order.

    Returns:
        Response id to the names of the plugins defining it, in the order the
        mapping supplied them.
    """
    holders: dict[str, list[str]] = {}
    for plugin, records in sources.items():
        for record in records:
            if record.get("type") == INFO_TYPE:
                holders.setdefault(record_key(record), []).append(plugin)
    return holders


def position_anchors(
    carried: Sequence[Mapping[str, Any]],
    sources: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[tuple[str, str, str]]:
    """Find the neighbouring responses that hold a carried line in place.

    **The technique this exists for.** To insert a response between two
    existing ones, an author drags the neighbours into their plugin as well.
    The content is untouched; only ``prev_id`` and ``next_id`` change, so that
    the plugin itself states where the new line goes. Those neighbours carry no
    edit of their own -- they are there purely to hold a place.

    Measured across the 298 plugins in this corpus that contain dialogue: 1,729
    responses are byte-identical to their master's version apart from those two
    links, and **1,711 of them -- 98% -- sit directly beside a response the same
    plugin added or edited**. Tribunal and Bloodmoon do it too, 1,125 times
    between them. It is not an accident and it is not dirt.

    It reads as dirt, though, and that is the danger. ``tes3cmd clean`` lists
    ``INFO`` among the record types it deletes when they duplicate a master
    (``@CLEAN_DUP_TYPES``), and its own manual warns that "sometimes duplicate
    dialog is intentional in order for dialog sorting to be correct". A diff
    view has the same problem: an anchor looks like a record with no changes.

    Args:
        carried: The records the patch will contain.
        sources: Plugin name to that plugin's records, in file order.

    Returns:
        One ``(response id, anchor id, plugin)`` per anchor that is not itself
        being carried, in the order the carried responses appear.
    """
    carried_ids = {record_key(record) for record in carried if record.get("type") == INFO_TYPE}
    holders = defining_plugins(sources)
    found: list[tuple[str, str, str]] = []
    for record in carried:
        if record.get("type") != INFO_TYPE:
            continue
        for field in ("prev_id", "next_id"):
            neighbour = str(record.get(field) or "")
            if not neighbour or neighbour in carried_ids:
                continue
            found.extend(
                (record_key(record), neighbour, plugin) for plugin in holders.get(neighbour, [])
            )
    return found


def dialogue_position_risk(
    carried: Sequence[Mapping[str, Any]],
    sources: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> list[str]:
    """Report responses whose position in their topic the patch cannot promise.

    **Position is priority.** A ``DialogueInfo`` sits in a doubly-linked list
    within its topic -- ``prev_id`` and ``next_id``, ``PNAM`` and ``NNAM`` on
    disk -- and the engine reads a topic from the top and uses the *first*
    response whose filters match. Move one and you change which line an NPC
    says, with no error and no log entry: the line is simply never spoken.

    A patch carries the record with those links intact, but they routinely name
    responses in other files: measured across a real load order, 23 of 458 in
    one voice mod and **2,791 of 6,573 -- 42% -- in Patch for Purists**. Those
    files are still loaded, so the links usually resolve; what cannot be
    promised is that the neighbourhood is the same one the response was written
    into, because another plugin may have rewritten it.

    This does not refuse anything -- the patch is very probably right, and
    refusing every dialogue edit would make the feature useless. It says which
    ones rest on an assumption, so a user testing in game knows where to look.

    Args:
        carried: The records the patch will contain.
        sources: Plugin name to that plugin's records, in file order. Given,
            each loose neighbour is named with the plugins that define it, and
            one defined by several is called out -- that is the case where the
            position genuinely may not be the one the author wrote. Omitted,
            the note falls back to saying only that the neighbour is elsewhere.

    Returns:
        One line per response whose neighbours are not also being carried.
    """
    carried_ids = {record_key(record) for record in carried if record.get("type") == INFO_TYPE}
    holders = defining_plugins(sources) if sources else {}
    topics = _topics_by_response(carried)
    notes: list[str] = []
    for record in carried:
        if record.get("type") != INFO_TYPE:
            continue
        key = record_key(record)
        loose = [
            _neighbour_note(side, str(record.get(field) or ""), holders)
            for side, field in (("previous", "prev_id"), ("next", "next_id"))
            if record.get(field) and str(record.get(field)) not in carried_ids
        ]
        if not loose:
            continue
        tail = (
            "and this is a greeting, where position is the whole of it: "
            "greetings are numbered buckets read top down and matched on "
            "filters alone, so a line that moves down is a line that stops "
            "being said"
            if topics.get(key) == GREETING
            else "so where this line falls in its topic depends on those staying put"
        )
        notes.append(f"{key}: its {' and its '.join(loose)}, {tail}")
    return notes


def _topics_by_response(carried: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Map each carried response to the kind of topic preceding it.

    Args:
        carried: The records the patch will contain, in order.

    Returns:
        Response id to topic kind, for responses that follow a ``Dialogue``.
    """
    kinds: dict[str, str] = {}
    current = ""
    for record in carried:
        if record.get("type") == DIALOGUE_TYPE:
            current = topic_kind(record)
        elif record.get("type") == INFO_TYPE and current:
            kinds[record_key(record)] = current
    return kinds


def _neighbour_note(side: str, neighbour: str, holders: Mapping[str, list[str]]) -> str:
    """Describe one neighbour the patch is not carrying.

    Args:
        side: ``"previous"`` or ``"next"``.
        neighbour: The neighbour's id.
        holders: Response id to the plugins defining it, possibly empty.

    Returns:
        A phrase naming the neighbour and where it comes from.
    """
    where = holders.get(neighbour, [])
    if len(where) > 1:
        return (
            f"{side} response {neighbour[:12]}... is defined by "
            f"{len(where)} files ({', '.join(where)}), so which of them "
            "supplies it decides where this line sits"
        )
    if where:
        return f"{side} response {neighbour[:12]}... is held in place by {where[0]}"
    return f"{side} response {neighbour[:12]}... comes from another file"


def collect(
    selections: Sequence[Selection],
    records_by_plugin: Mapping[str, Sequence[Mapping[str, Any]]],
    patch_masters: Sequence[str],
) -> list[dict[str, Any]]:
    """Gather the chosen records, ready to write as a patch.

    Args:
        selections: What to take, and from where.
        records_by_plugin: Every source plugin's decoded records.
        patch_masters: The master list the patch will declare, in load order.

    Returns:
        The records, in the order selected, with references remapped. A
        dialogue response is preceded by the topic it belongs to, which is
        what tells the engine which topic it answers.

    Raises:
        PatchError: If a selection names a plugin or record that is not there,
            or if a record's references cannot be remapped.
    """
    maps: dict[str, dict[int, int]] = {}
    seen_topics: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []

    for selection in selections:
        records = records_by_plugin.get(selection.plugin)
        if records is None:
            raise PatchError(f"no records were read for {selection.plugin}")

        found = next(
            (
                record
                for record in records
                if record.get("type") == selection.record_type
                and record_key(record) == selection.key
            ),
            None,
        )
        if found is None:
            raise PatchError(
                f"{selection.plugin} has no {selection.record_type} record "
                f"{selection.key!r}. It may have been updated since the scan."
            )

        # A dialogue response needs its topic ahead of it, or the engine has
        # nothing to attach it to. Emitted once however many responses share it.
        topic = owning_dialogue(records, selection.record_type, selection.key)
        if topic is not None:
            marker = (DIALOGUE_TYPE, record_key(topic))
            if marker not in seen_topics:
                seen_topics.add(marker)
                out.append(copy.deepcopy(dict(topic)))

        if not needs_remapping(found):
            out.append(copy.deepcopy(dict(found)))
            continue

        mapping = maps.get(selection.plugin)
        if mapping is None:
            mapping = index_map(selection.plugin, master_names(records), patch_masters)
            maps[selection.plugin] = mapping
        out.append(remap_references(found, mapping))

    return out


def carry_forward(
    records: Sequence[Mapping[str, Any]],
    patch_masters: Sequence[str],
    skip: frozenset[tuple[str, str]] = frozenset(),
) -> list[dict[str, Any]]:
    """Re-emit an earlier build of *this same* patch into a new one.

    This is what makes a patch buildable over several sessions: read back the
    file this call is about to overwrite, and carry its records into the new
    build, so a record queued last week is not lost just because this
    session's queue never mentions it again.

    **Index 0 needs no remapping here, and that is the whole difference from**
    :func:`collect`. A record already lives in the file being rewritten, and
    stays there -- the file's own identity does not change just because it is
    being rebuilt, so a ``mast_index`` of ``0`` still means "this file" on
    both sides of the rewrite. Only indices into the *old* header's own master
    list change, because the new build may declare a different one. Nothing
    here requires the old file's name to appear in ``patch_masters``, and it
    must not: declaring a patch as a master of its own replacement is
    circular, and tes3conv will not write it.

    Args:
        records: The previous build's decoded records, header included.
        patch_masters: The master list the new patch will declare.
        skip: ``(record type, key)`` pairs this session already decided.
            Carrying the old answer forward as well would leave the patch's
            own last-wins to pick between it and the new one -- the same
            reason :class:`~wraithguard.patch.queue.PatchQueue` overwrites
            rather than accumulates when a record is re-decided.

    Returns:
        The old records worth keeping, in their original relative order, with
        references renumbered for the new master list. Order matters for a
        dialogue response: it must still follow the topic that owns it, and
        the previous build already put it there.

    Raises:
        PatchError: If an old record references a master the new build does
            not declare. Rescan with that plugin present, or replace the
            patch instead of appending to it.
    """
    old_masters = master_names(records)
    lowered = {name.lower(): position for position, name in enumerate(patch_masters, start=1)}
    mapping: dict[int, int] | None = None

    def old_index_map() -> dict[int, int]:
        """Old master position to new, built once and only if actually needed."""
        built = {0: 0}
        for old, name in enumerate(old_masters, start=1):
            found = lowered.get(name.lower())
            if found is None:
                raise PatchError(
                    f"the existing patch references {name}, which the new build "
                    "does not declare. Rescan with it present, or replace the "
                    "patch instead of appending to it."
                )
            built[old] = found
        return built

    out: list[dict[str, Any]] = []
    for record in records:
        if record.get("type") == HEADER_TYPE:
            continue
        if (str(record.get("type")), record_key(record)) in skip:
            continue
        if not needs_remapping(record):
            out.append(copy.deepcopy(dict(record)))
            continue
        if mapping is None:
            mapping = old_index_map()
        out.append(remap_references(record, mapping))
    return out


def required_masters(
    selections: Sequence[Selection],
    records_by_plugin: Mapping[str, Sequence[Mapping[str, Any]]],
    load_order: Sequence[str],
    extra: Iterable[str] = (),
) -> list[str]:
    """Work out what a patch of these records must declare.

    Every plugin a record is taken from, plus everything those plugins depend
    on, in load order. A patch that carries a plugin's record and does not
    declare that plugin is a patch of something that may not be there.

    Args:
        selections: What the patch will carry.
        records_by_plugin: The source plugins' decoded records.
        load_order: The full load order, which decides the result's order.
        extra: Master names required regardless of any selection. What an
            earlier build of this same patch declared, when appending to one
            -- its records still reference those masters by position even
            though nothing here takes a record from it *as* a plugin (see
            :func:`carry_forward`).

    Returns:
        The masters to declare, in load order.
    """
    needed: set[str] = set()
    for selection in selections:
        needed.add(selection.plugin)
        needed.update(master_names(records_by_plugin.get(selection.plugin) or []))
    needed.update(extra)

    lowered = {name.lower(): name for name in needed}
    ordered = [name for name in load_order if name.lower() in lowered]
    seen = {name.lower() for name in ordered}
    ordered.extend(sorted(name for key, name in lowered.items() if key not in seen))
    return ordered
