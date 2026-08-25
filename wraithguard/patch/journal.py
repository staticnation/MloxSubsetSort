"""Resolve a quest's journal stages across every plugin that touches it.

**Why this needs its own resolver rather than reusing** :mod:`~wraithguard.patch.dialogue`
**as-is.** A ``Dialogue`` record of kind ``"Journal"`` is a quest, and each
``DialogueInfo`` beneath it is one stage -- structurally identical to an
ordinary topic and its responses, which is why :func:`stages_by_quest` mirrors
:func:`~wraithguard.patch.dialogue.responses_by_topic` almost line for line.
But what a stage's *position* means is not the same thing. An ordinary
response's position is a priority order built from a ``prev_id`` chain,
because the engine picks the first filter match it reads. A journal stage has
no such chain to walk: Bethesda repurposes the INFO's disposition field as the
journal index -- what a ``Journal "id", N`` script call is actually setting --
and the game shows whichever stage's index matches the quest's current value.
Order here means "sorted by that number", not "resolved from a linked list".

**What does not change.** A stage's *identity* is still its own INFO id, and a
later plugin overriding one still means: same topic, same id, doing standard
last-plugin-wins-by-id override. A different id is a different stage, even if
it happens to declare the same index -- two plugins are not automatically in
conflict just because their numbers collide; only reusing the same id is an
edit of the same stage. That is exactly the override rule
:func:`~wraithguard.patch.dialogue.topic_order` already uses; only its
chain-walking half is journal-specific and left out here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from wraithguard.patch.records import topic_kind

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

#: dialogue_type value marking a Dialogue record as a quest rather than
#: spoken topic/greeting/voice/persuasion. Matches the constant private to
#: wraithguard.tes3fields.dialogue -- kept local rather than imported, the
#: same call patch.dialogue already makes for its own literals.
_JOURNAL_KIND = "Journal"

#: quest_state value marking a stage as the quest's end. The schema also
#: allows "Restart" and "Name"; those are carried through on Stage/Resolved
#: rather than collapsed here, since a wrong guess baked into a bool would be
#: worse than the raw string.
_FINISHED_STATE = "Finished"


@dataclass(frozen=True, slots=True)
class Stage:
    """One plugin's definition of one journal stage.

    Attributes:
        info_id: The stage's own identity -- an ``INFO`` id, exactly like an
            ordinary dialogue response's. A later plugin overriding this
            stage's text is expected to reuse the same id.
        index: The journal index this stage sets, from the INFO's
            (repurposed) disposition field.
        text: The journal entry's text, shown to the player at this index.
        quest_state: ``"Finished"``, ``"Restart"``, ``"Name"``, or empty.
        script_text: The stage's own result script, mwscript source -- what a
            later scan for ``Journal``/``SetJournalIndex`` calls reads
            (:mod:`~wraithguard.patch.journal_scripts`). Carried here rather
            than re-read from the source plugin later so that a Stage 1
            override already tells Stage 2 which version of the script
            actually runs.
        plugin: The file this definition came from.
        deleted: Whether this definition is a deletion (the ``DELETED``
            object flag). A later plugin re-adding the id is a fresh
            insertion, not a further override of the deleted stage.
        raw: The complete decoded INFO record, kept alongside the extracted
            fields above rather than instead of them -- callers that only
            need the common case (text, index, script) never have to touch
            it, and one that wants more (speaker requirements, filters, sound)
            has the whole record rather than a second, narrower extraction to
            keep in sync with this one. Cheap to carry: the same dict already
            lives in the source plugin's own decoded records, so this is one
            more reference to it, not a copy.
    """

    info_id: str
    index: int = 0
    text: str = ""
    quest_state: str = ""
    script_text: str = ""
    plugin: str = ""
    deleted: bool = False
    raw: Mapping[str, object] | None = None


@dataclass(slots=True)
class Resolved:
    """One journal stage as the engine will actually show it.

    Attributes:
        info_id: The stage's identity.
        index: The journal index it sets.
        text: The surviving text -- the last plugin's, by ordinary override.
        quest_state: The surviving state marker.
        script_text: The surviving result script.
        plugins: Every plugin that defined this stage, in the order they were
            read. The last is the one the game uses.
        raw: The winning definition's complete decoded INFO record. See
            :class:`Stage`'s own ``raw`` for why it rides along unparsed.
    """

    info_id: str
    index: int = 0
    text: str = ""
    quest_state: str = ""
    script_text: str = ""
    plugins: list[str] = field(default_factory=list)
    raw: Mapping[str, object] | None = None

    @property
    def finished(self) -> bool:
        """Whether this stage is the quest's ``Finished`` marker."""
        return self.quest_state == _FINISHED_STATE


def stages_by_quest(
    records: Sequence[Mapping[str, object]], plugin: str = ""
) -> dict[str, list[Stage]]:
    """Group one plugin's journal stages under the quests they belong to.

    Restricted to ``Journal``-kind topics -- everything else is
    :func:`~wraithguard.patch.dialogue.responses_by_topic`'s job, not this
    one's. A stage carries no quest id of its own; it belongs to the last
    ``Dialogue`` record read before it, same as an ordinary response belongs
    to the last topic.

    Args:
        records: The plugin's records, in file order.
        plugin: The file name, recorded on each stage.

    Returns:
        Quest id to its stages, in file order.
    """
    found: dict[str, list[Stage]] = {}
    quest = ""
    journal = False
    for record in records:
        kind = record.get("type")
        if kind == "Dialogue":
            quest = str(record.get("id") or "")
            journal = topic_kind(record) == _JOURNAL_KIND
        elif kind == "DialogueInfo" and journal and quest:
            raw_data = record.get("data")
            data: Mapping[str, object] = raw_data if isinstance(raw_data, dict) else {}
            index = data.get("disposition")
            found.setdefault(quest, []).append(
                Stage(
                    info_id=str(record.get("id") or ""),
                    index=index if isinstance(index, int) else 0,
                    text=str(record.get("text") or ""),
                    quest_state=str(record.get("quest_state") or ""),
                    script_text=str(record.get("script_text") or ""),
                    plugin=plugin,
                    deleted="DELETED" in str(record.get("flags") or "").upper(),
                    raw=record,
                )
            )
    return found


def resolve_quest(stages: Iterable[Stage]) -> list[Resolved]:
    """Resolve one quest's stages the way the engine will read them.

    Args:
        stages: Every definition of every stage in one quest, in load order.

    Returns:
        The surviving stages, sorted by journal index. Two stages sharing an
        index are not merged -- they are distinct ids, and the sort is
        stable, so they come out in the order they first appeared.
    """
    at: dict[str, Resolved] = {}
    order: list[str] = []

    for stage in stages:
        existing = at.get(stage.info_id)
        if stage.deleted:
            if existing is not None:
                del at[stage.info_id]
                order.remove(stage.info_id)
            continue
        if existing is not None:
            # An override replaces the stage wholesale -- same as any other
            # TES3 record re-defined under the same id, not a field merge.
            existing.index = stage.index
            existing.text = stage.text
            existing.quest_state = stage.quest_state
            existing.script_text = stage.script_text
            existing.raw = stage.raw
            if stage.plugin:
                existing.plugins.append(stage.plugin)
            continue
        at[stage.info_id] = Resolved(
            info_id=stage.info_id,
            index=stage.index,
            text=stage.text,
            quest_state=stage.quest_state,
            script_text=stage.script_text,
            plugins=[stage.plugin] if stage.plugin else [],
            raw=stage.raw,
        )
        order.append(stage.info_id)

    return sorted((at[info_id] for info_id in order), key=lambda resolved: resolved.index)


def resolve_journals(
    sources: Mapping[str, Sequence[Mapping[str, object]]], load_order: Sequence[str]
) -> dict[str, list[Resolved]]:
    """Resolve every quest across a whole load order.

    Args:
        sources: Plugin name to that plugin's records, in file order.
        load_order: The order those plugins load in. Order is the input.

    Returns:
        Quest id to its resolved stages, sorted by journal index. A quest
        with zero surviving stages (every definition deleted) is left out
        rather than reported as an empty chain.
    """
    by_quest: dict[str, list[Stage]] = {}
    for plugin in load_order:
        records = sources.get(plugin)
        if records is None:
            continue
        for quest, found in stages_by_quest(records, plugin).items():
            by_quest.setdefault(quest, []).extend(found)

    resolved = {quest: resolve_quest(stages) for quest, stages in by_quest.items()}
    return {quest: stages for quest, stages in resolved.items() if stages}


__all__ = [
    "Resolved",
    "Stage",
    "resolve_journals",
    "resolve_quest",
    "stages_by_quest",
]
