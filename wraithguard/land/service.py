"""One call that turns a load order into a merged landscape plugin.

The GUI and the command-line tool both need the same six steps in the same
order, and neither should own them. This is that sequence behind one function,
with progress reported as text so a Tk log panel and a terminal can show the
same thing without either knowing about the other.

**Everything it does is additive.** Your plugins are never modified. The output
is one new file; deleting it restores the previous behaviour exactly. The only
destructive act available is overwriting a previous ``Merged Lands.esp``, and
that is the caller's decision.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from wraithguard.land.cells import cells_for, merge_cells
from wraithguard.land.emit import (
    EmitError,
    attach_texture_indices,
    build_landscape_record,
    build_plugin,
    build_texture_records,
)
from wraithguard.land.landmass import Landmass, PluginRecords, build_reference
from wraithguard.land.merge import ConflictStrategy
from wraithguard.land.meta import (
    LAYER_NAMES,
    MetaError,
    PluginMeta,
    load_meta,
    strategy_display_name,
    write_merged_marker,
)
from wraithguard.land.native import (
    NativeReadError,
    has_landscape,
    landscape_records_from_sidecar,
    read_landscape_records,
)
from wraithguard.land.pipeline import MergeOutcome, finish, merge_landmass
from wraithguard.land.textures import KnownTextures
from wraithguard.proc import no_window_kwargs
from wraithguard.tes3fields.landscape import LAND_SIZE, TEXTURE_SIZE, WNAM_SIZE

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

_log: Final = logging.getLogger(__name__)

#: The name the community expects.
DEFAULT_NAME: Final = "Merged Lands.esp"

#: Extensions that make a plugin a master rather than a mod.
_MASTER_SUFFIXES: Final[tuple[str, ...]] = (".esm", ".omwgame")

#: Extensions that are not plugins at all. ``.omwscripts`` is OpenMW's list of
#: Lua scripts to attach; it holds no records, and a load order may name one
#: quite legitimately. Reporting it as an unreadable plugin is noise that looks
#: like a problem.
_NOT_PLUGINS: Final[tuple[str, ...]] = (".omwscripts",)

#: Ceiling on concurrent tes3conv conversions while reading mods. Each worker is
#: a live converter process plus its JSON, so the cap is what keeps peak memory
#: to a few conversions instead of the whole load order. Capped against the CPU
#: count at the call site; four hides per-process latency without stacking large
#: conversions.
_READ_WORKERS: Final = 4

#: How progress is reported. Called with one line at a time.
Report = "Callable[[str], None]"


class MergeServiceError(Exception):
    """Raised when a merge cannot be performed or written."""


@dataclass(slots=True)
class MergeResult:
    """What a merge produced.

    Attributes:
        output: The plugin written, or ``None`` for a dry run.
        cells_written: ``LAND`` records emitted.
        textures_written: ``LTEX`` records emitted.
        cell_records: ``CELL`` records emitted.
        contested: Vertices more than one mod moved.
        major: Contested vertices settled far from an intent.
        seam_vertices: Vertices seam repair moved.
        slope_vertices: Vertices the limiter moved.
        clamped: Vertices the format could not express.
        dropped: Cells cleaning removed as redundant.
        borrowed: Untouched cells pulled in to reconcile their borders.
        lines: The progress report, for a log panel.
    """

    output: Path | None = None
    cells_written: int = 0
    textures_written: int = 0
    cell_records: int = 0
    contested: int = 0
    major: int = 0
    seam_vertices: int = 0
    slope_vertices: int = 0
    clamped: int = 0
    dropped: int = 0
    borrowed: int = 0
    lines: list[str] = field(default_factory=list)


def _split_order(names: Sequence[str]) -> tuple[list[str], list[str]]:
    """Separate masters from mods.

    Args:
        names: Plugin file names, in load order.

    Returns:
        Masters and mods, each in load order. Entries that are not plugins at
        all -- ``.omwscripts`` -- appear in neither.
    """
    plugins = [name for name in names if not name.lower().endswith(_NOT_PLUGINS)]
    masters = [name for name in plugins if name.lower().endswith(_MASTER_SUFFIXES)]
    mods = [name for name in plugins if not name.lower().endswith(_MASTER_SUFFIXES)]
    return masters, mods


def resolve_plugin(name: str, directories: Sequence[Path]) -> Path | None:
    """Find which data folder holds a plugin.

    OpenMW composes its load order from *several* ``data=`` directories, and a
    single load order routinely spans a dozen of them -- vanilla in one, each
    mod in its own. Looking in only the folder where the first plugin happened
    to be found makes every master installed elsewhere unreadable.

    **The last directory holding the file wins, not the first.** OpenMW
    resolves a name that several ``data=`` folders provide to the one declared
    *latest*: a later ``data=`` line shadows an earlier one, exactly as a later
    plugin overrides an earlier plugin. So a mod split across
    ``.../00 Core`` and ``.../01 Patch`` -- both providing the same
    ``Foo.esp``, with the patch folder second -- must resolve to the patch
    copy. Returning the first match instead reads the pre-patch file: the merge
    then diffs terrain the player never loads, and a ``.mergedlands.toml`` aimed
    at the patch has no effect because the patch was never opened.

    ``directories`` therefore arrives in load order (earliest ``data=`` first),
    and the search keeps the *last* match rather than returning the first.

    The search is case-insensitive because ``openmw.cfg`` and the file system
    routinely disagree: ``RepopulatedMorrowind.ESM`` against
    ``RepopulatedMorrowind.esm``, and a case-sensitive filesystem will not
    match them.

    Args:
        name: The plugin's file name, as the load order spells it.
        directories: Data folders, in load order (earliest ``data=`` first).

    Returns:
        The path in the last directory that holds the plugin, or ``None`` when
        no folder holds it.
    """
    wanted = name.lower()
    found: Path | None = None
    for directory in directories:
        candidate = directory / name
        if candidate.is_file():
            found = candidate
            continue
        try:
            for entry in directory.iterdir():
                if entry.name.lower() == wanted and entry.is_file():
                    found = entry
                    break
        except OSError:
            # An unreadable or vanished data folder is not a reason to abandon
            # the search; the plugin may well be in the next one.
            continue
    return found


def _records_via(
    converter: str | None,
    plugin: Path,
    scratch: Path,
    sidecar_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Read one plugin's landscape records.

    Three steps, in order, and the first two exist because the third is
    expensive and fragile:

    **Skip what has no terrain.** Of 972 plugins measured here, 869 -- 89.4% --
    have no ``LAND`` or ``LTEX`` record at all. Converting each one costs a
    subprocess and a JSON document, to learn there was nothing to merge.

    **Convert with tes3conv.** The verified path, and still what writes the
    merged plugin.

    **Fall back to reading the file directly.** tes3conv refuses a whole plugin
    over a single record type it does not know -- ``LUAL``, OpenMW's Lua
    configuration record, stopped a nine-hundred-mod merge dead. A
    length-driven walk skips unknown records without understanding them, so
    :mod:`wraithguard.land.native` gets the terrain out anyway.

    Args:
        converter: The tes3conv executable.
        plugin: The plugin file.
        scratch: A directory for the intermediate JSON.
        sidecar_dir: Where the conflict scanner keeps its record-key sidecars,
            which answer "has terrain?" exactly and almost for free.

    Returns:
        The decoded records and an empty string, or an empty list and the
        reason it failed. A plugin with no terrain returns no records and no
        reason: that is an answer, not a failure. The reason is returned rather
        than logged because a master that will not read stops the whole merge,
        and "could not read it" without saying why is not something a user can
        act on.
    """
    if not has_landscape(plugin, sidecar_dir):
        return [], ""

    # Reuse the conflict scanner's landscape sidecar when it is present and
    # current: it already holds exactly the records this would convert to, so a
    # merge over a load order that was just scanned skips the converter here
    # entirely. A stale or absent sidecar falls through to a fresh conversion.
    if sidecar_dir is not None:
        cached = landscape_records_from_sidecar(plugin, sidecar_dir)
        if cached is not None:
            return cached, ""

    if not converter:
        # No tes3conv configured: read the terrain in process rather than fail a
        # subprocess per plugin. Same records the converter path returns.
        try:
            return read_landscape_records(plugin), ""
        except NativeReadError as exc:
            return [], f"reading {plugin.name} directly failed: {exc}"

    target = scratch / (plugin.stem + ".json")
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            [converter, str(plugin), str(target), "--overwrite"],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            # Suppress the Windows console flash: a --noconsole build otherwise
            # pops one window per plugin converted during a Merged Lands run.
            **no_window_kwargs(),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:200]
            return [], f"tes3conv exited {result.returncode}: {detail or 'no message'}"
        if not target.is_file():
            return [], "tes3conv reported success but wrote no JSON"
        data = json.loads(target.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        return [], "tes3conv timed out after 600s"
    except MemoryError:
        return [], "ran out of memory decoding the converted JSON"
    except OSError as exc:
        return [], f"could not run tes3conv or read its output: {exc}"
    except ValueError as exc:
        return [], f"tes3conv wrote JSON that will not parse: {exc}"
    finally:
        target.unlink(missing_ok=True)
    if not isinstance(data, list):
        return [], "tes3conv wrote something that is not a record list"
    return data, ""


def _records_natively(plugin: Path, why: str) -> tuple[list[dict[str, Any]], str]:
    """Read a plugin tes3conv refused, by walking its records directly.

    Args:
        plugin: The plugin file.
        why: What tes3conv said -- kept for the log, and for the message if
            this fails too, since the converter's complaint is usually the more
            informative of the two.

    Returns:
        The landscape records and an empty string, or an empty list and a
        reason naming both attempts.
    """
    try:
        records = read_landscape_records(plugin)
    except NativeReadError as exc:
        return [], f"{why}; reading it directly also failed: {exc}"
    _log.info(
        "%s: tes3conv refused it (%s); read %d landscape record(s) directly",
        plugin.name,
        why,
        len(records),
    )
    return records, ""


def _describe_meta(meta: PluginMeta) -> str:
    """One-line summary of a plugin's ``.mergedlands.toml`` for a verbose log.

    Names every layer and what it is set to -- ``on``/``OFF`` and the conflict
    strategy -- so a troubleshooting log shows exactly *why* a plugin's terrain
    merged the way it did, not just that it carried settings.

    Args:
        meta: The plugin's settings.

    Returns:
        A compact ``meta_type=...; layer: state/strategy; ...`` line.
    """
    layers = "; ".join(
        f"{name}: {'on' if settings.included else 'OFF'}"
        f"/{strategy_display_name(settings.conflict_strategy)}"
        for name in LAYER_NAMES
        for settings in (meta.settings_for(name),)
    )
    return f"meta_type={meta.meta_type}; {layers}"


def build_merged_lands(
    data_files: Path | Sequence[Path],
    load_order: Sequence[str],
    converter: str | None,
    output: Path | None = None,
    strategy: ConflictStrategy = ConflictStrategy.AUTO,
    include_cells: bool = False,
    sidecars: Path | None = None,
    dry_run: bool = False,
    report: Callable[[str], None] | None = None,
    substitute_unknown_textures: bool = True,
    verbose: bool = False,
) -> MergeResult:
    """Merge a load order's terrain and write ``Merged Lands.esp``.

    Args:
        data_files: The Data Files directory, or every ``data=`` folder in
            search order. OpenMW load orders routinely span many.
        load_order: Plugin file names, in load order, masters first.
        converter: Path to ``tes3conv``, or ``None`` to read and encode with the
            built-in reader/writer -- a merge needs no external tool either way.
        output: Where to write, or ``None`` for ``Merged Lands.esp`` beside
            the plugins.
        strategy: How to settle a vertex two mods both moved.
        include_cells: Also emit ``CELL`` records. References are never
            carried either way.
        sidecars: The conflict scanner's ``tes3conv_json`` folder. When given,
            its ``<stem>.keys.json`` files say exactly which plugins have
            terrain, so the rest are never converted -- 869 of 972 here.
        dry_run: Report without writing.
        report: Called with each progress line.
        substitute_unknown_textures: What to do with a texture index no ``LTEX``
            record defines (a missing master). ``True`` (the default) replaces
            it with the smallest valid painted texture so the plugin always
            loads; ``False`` passes it through as a dangling index. Both are
            reported at emit -- the default is the safe choice for a GUI run,
            and a CLI caller that would rather see the dangling index can turn
            it off.
        verbose: Report per-plugin detail as well as the headline numbers --
            every plugin that carries settings and what those settings are, and
            the full list of anything that could not be read, rather than the
            truncated summaries. Off by default; the headline log is enough for
            a run that goes as expected, and this is for troubleshooting one
            that did not.

    Returns:
        What was produced.

    Raises:
        MergeServiceError: If the merge cannot be performed or written.
    """
    lines: list[str] = []

    def say(text: str) -> None:
        """Record one progress line and pass it on.

        Kept for the caller either way: the report callback is optional, but
        the collected lines go back in the result, so a failure can be read
        after the fact by anything that was not watching live.

        Args:
            text: The line to record.
        """
        lines.append(text)
        if report is not None:
            report(text)

    directories = [data_files] if isinstance(data_files, Path) else list(data_files)
    if sidecars is not None and not sidecars.is_dir():
        # Not fatal: the sidecars are an optimisation, and a merge without them
        # reads every plugin and reaches the same answer, slowly.
        _log.info("no sidecar folder at %s; every plugin will be converted", sidecars)
        sidecars = None
    if not directories:
        raise MergeServiceError("no data folder was given, so no plugin can be found")

    master_names, mod_names = _split_order(load_order)
    if not master_names:
        raise MergeServiceError(
            "no masters in the load order. Without them there is no reference "
            "terrain to merge against, and every mod would look as though it "
            "had rewritten the world."
        )

    say(f"masters: {', '.join(master_names)}")
    say(f"mods:    {len(mod_names)}")

    known = KnownTextures()
    with tempfile.TemporaryDirectory() as scratch_dir:
        scratch = Path(scratch_dir)

        say("reading masters ...")
        # Settings for every plugin, masters included. A master's
        # .mergedlands.toml was silently ignored before -- it is a master, so it
        # builds the reference rather than being diffed, and only the diff loop
        # loaded settings. That both under-counted the "carry settings" total and
        # left a master's `included = false` with no effect. Masters are loaded
        # here so their settings are reported and applied to the reference.
        metas: dict[str, PluginMeta] = {}
        masters: list[PluginRecords] = []
        rescued = 0
        for name in master_names:
            path = resolve_plugin(name, directories)
            if path is None:
                raise MergeServiceError(
                    f"master {name} is not in any of the {len(directories)} data "
                    "folder(s) searched. Every master has to be readable: they "
                    "are the reference terrain the whole merge is measured "
                    "against."
                )
            try:
                metas[name] = load_meta(path)
            except MetaError as exc:
                raise MergeServiceError(str(exc)) from exc
            records, failure = _records_via(converter, path, scratch, sidecars)
            if failure:
                records, failure = _records_natively(path, failure)
                if not failure:
                    rescued += 1
            if failure:
                raise MergeServiceError(f"could not read master {name}: {failure}")
            masters.append(PluginRecords(name=name, records=records))
        if rescued:
            say(f"  {rescued} master(s) tes3conv refused were read directly")
        reference, known = build_reference(masters, metas=metas)
        say(f"  reference landmass: {len(reference)} cell(s), {len(known)} land texture(s)")

        say("reading mods ...")
        mods: list[PluginRecords] = []
        unreadable: list[str] = []

        # Resolve paths and read settings serially first: both are cheap, and a
        # bad .mergedlands.toml must abort the whole run rather than surface from
        # inside a worker thread.
        resolved: list[tuple[str, Path]] = []
        for name in mod_names:
            path = resolve_plugin(name, directories)
            if path is None:
                # A mod that is not installed is not fatal -- the load order may
                # simply list something the user removed. Masters are different.
                unreadable.append(f"{name} (not found)")
                continue
            try:
                metas[name] = load_meta(path)
            except MetaError as exc:
                raise MergeServiceError(str(exc)) from exc
            resolved.append((name, path))

        def _read_one(item: tuple[str, Path]) -> tuple[str, list[dict[str, Any]], str]:
            """Read one plugin's terrain -- the parallelised, thread-safe step.

            tes3conv runs per plugin as a separate process writing a uniquely
            named JSON, and the native fallback only reads the file, so several
            can run at once. No shared state is touched here.
            """
            name, path = item
            records, failure = _records_via(converter, path, scratch, sidecars)
            if failure:
                records, failure = _records_natively(path, failure)
            return name, records, failure

        # Convert in parallel, bounded so peak memory stays a few conversions,
        # not the whole load order. Results come back in load order (map keeps
        # input order), which the merge depends on -- later plugins win.
        workers = max(1, min((os.cpu_count() or 1), _READ_WORKERS, len(resolved) or 1))
        reads: Iterator[tuple[str, list[dict[str, Any]], str]]
        if workers == 1:
            reads = (_read_one(item) for item in resolved)
        else:
            pool = ThreadPoolExecutor(max_workers=workers)
            reads = pool.map(_read_one, resolved)
        try:
            for index, (name, records, failure) in enumerate(reads, start=1):
                if records:
                    mods.append(PluginRecords(name=name, records=records))
                elif failure:
                    # No records and no reason means the plugin simply has no
                    # terrain, which is the common case and not worth reporting.
                    unreadable.append(f"{name} ({failure})")
                if index % 50 == 0:
                    say(f"  {index}/{len(resolved)}")
        finally:
            if workers != 1:
                pool.shutdown()

    if unreadable:
        if verbose:
            say(f"  {len(unreadable)} plugin(s) could not be read:")
            for entry in unreadable:
                say(f"    {entry}")
        else:
            say(f"  {len(unreadable)} plugin(s) could not be read: {', '.join(unreadable[:5])}")
    patched = [name for name, meta in metas.items() if meta.layers]
    if patched:
        listed = ", ".join(patched[:12]) + (", ..." if len(patched) > 12 else "")
        say(f"  {len(patched)} plugin(s) carry .mergedlands.toml settings: {listed}")
        if verbose:
            for name in patched:
                say(f"    {name}: {_describe_meta(metas[name])}")

    say("merging ...")
    outcome = merge_landmass(reference, mods, known, metas, strategy)
    for name, why in outcome.skipped_plugins:
        say(f"  skipped {name}: {why}")
    say(f"  {len(outcome.cells)} modified cell(s)")

    finish(outcome, reference)
    say(
        f"  seam repair moved {outcome.seams.total} vertex/vertices "
        f"(widest gap {outcome.seams.largest_gap} units)"
    )
    if outcome.borrowed:
        say(f"  brought in {outcome.borrowed} untouched cell(s) to reconcile their borders")
    if outcome.slopes.adjusted:
        say(
            f"  slope limiter moved {outcome.slopes.adjusted} vertex/vertices "
            f"in {outcome.slopes.passes} pass(es)"
        )
    if outcome.slopes.pinned:
        say(
            f"  {outcome.slopes.pinned} adjustment(s) refused: the vertex is shared "
            "with terrain outside the merge"
        )
    say(f"  cleaning dropped {outcome.cleaning.dropped} redundant cell(s)")

    if outcome.seams.tears:
        # A torn border is the one defect a player sees in the first minute:
        # a wall or a chasm along a cell boundary, in a game where every
        # boundary is somewhere you walk. Writing the file anyway would ship
        # visibly broken terrain, so this refuses instead -- and says which
        # border, because it is a bug here rather than anything the user did.
        worst = outcome.seams.tears[0]
        neighbour = worst.right if worst.right is not None else "terrain outside the merge"
        raise MergeServiceError(
            f"{len(outcome.seams.tears)} cell border(s) still disagree after seam "
            f"repair -- worst between {worst.left} and {neighbour}, "
            f"{worst.vertices} vertex/vertices apart by up to {worst.worst} units. "
            "Nothing was written: this would be a visible tear in the landscape, "
            "and it is a defect in the merge rather than in your load order. "
            "Please report it."
        )

    result = MergeResult(
        contested=sum(cell.contested for cell in outcome.cells.values()),
        major=sum(cell.major for cell in outcome.cells.values()),
        seam_vertices=outcome.seams.total,
        slope_vertices=outcome.slopes.adjusted,
        dropped=outcome.cleaning.dropped,
        borrowed=outcome.borrowed,
        lines=lines,
    )

    records, pending, used, clamped = _build_records(outcome, say)
    result.clamped = clamped
    if not records:
        say("nothing to merge; no plugin written")
        result.lines = lines
        return result

    texture_records, texture_sources = _finish_textures(
        pending, used, known, say, substitute_unknown=substitute_unknown_textures
    )
    records = [*texture_records, *records]
    result.cells_written = len(records) - len(texture_records)
    result.textures_written = len(texture_records)

    if include_cells:
        merged_cells = merge_cells(
            [(p.name, p.records) for p in (*masters, *mods)],
            skip=frozenset(name for name, _ in outcome.skipped_plugins),
        )
        cell_records = cells_for(merged_cells, set(outcome.cells))
        records = [*records, *cell_records]
        result.cell_records = len(cell_records)
        say(f"  {len(cell_records)} CELL record(s); references are never carried")

    if dry_run:
        say("dry run: nothing was written")
        result.lines = lines
        return result

    declared = _contributors(outcome, texture_sources, reference, load_order) or list(master_names)
    say(f"  declaring {len(declared)} master(s): the plugins that actually contributed")

    target = output or (directories[0] / DEFAULT_NAME)
    _write(records, declared, directories, target, converter)
    say(f"wrote {target} ({target.stat().st_size} bytes, {len(records) + 1} records)")
    try:
        marker = write_merged_marker(target)
    except MetaError as exc:
        raise MergeServiceError(str(exc)) from exc
    say(f"wrote {marker.name} so a later merge ignores this file")
    result.output = target
    result.lines = lines
    return result


def _contributors(
    outcome: MergeOutcome,
    texture_sources: Sequence[str],
    reference: Landmass,
    load_order: Sequence[str],
) -> list[str]:
    """Work out which plugins the merged file actually depends on.

    Merged Lands declares as masters exactly the plugins that contributed --
    every plugin supplying a land texture that survived cleaning, and every
    plugin that edited a cell that was written -- rather than every master in
    the load order. Two reasons, and both matter:

    *It is honest.* A load order with twenty-seven masters does not mean this
    plugin depends on twenty-seven masters. Declaring the ones it read from is
    what the dependency list is for.

    *It is enforcement.* A declared plugin has to be present and has to load
    first. A merged file built from a mod's terrain and then loaded without
    that mod is a file describing edits to land that is no longer there;
    declaring the mod makes the engine refuse rather than render it.

    Note that the ``.esp`` mods appear here as masters. That is not a category
    error -- a TES3 master list is a dependency list, and nothing stops an
    ``.esp`` being on it.

    Args:
        outcome: The merge, after cleaning.
        texture_sources: Plugins that supplied a surviving texture's file name.
        reference: The reference landmass, for the master behind each cell.
        load_order: The full load order, which decides the result's order.

    Returns:
        The contributing plugins, in load order. Empty when nothing
        contributed, which the caller treats as a reason to fall back.
    """
    contributors: set[str] = set(texture_sources)
    for coords, cell in outcome.cells.items():
        contributors.update(cell.editors)
        # The master supplying the cell's reference terrain is a dependency too,
        # and not an optional one: a LAND record is an *override* of the record
        # in the file that first defined that cell, and an override whose
        # original is not declared is a record the engine has no basis to
        # replace. Only the mod editors would be declared without this, and a
        # merged file that does not name Morrowind.esm is not a patch of
        # Morrowind.
        origin = reference.sources.get(coords)
        if origin:
            contributors.add(origin)

    # Load order is authoritative for the order masters are declared in; a name
    # that is not in it (a source we recorded but the caller did not list) is
    # appended rather than dropped, because omitting a real dependency is worse
    # than declaring one out of order.
    seen = {name.lower() for name in load_order}
    ordered = [name for name in load_order if name in contributors]
    ordered.extend(sorted(name for name in contributors if name.lower() not in seen))
    return ordered


def _build_records(
    outcome: MergeOutcome, say: Callable[[str], None]
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], list[list[int]]]], set[int], int]:
    """Turn merged cells into ``LAND`` records.

    Args:
        outcome: The merged landmass.
        say: Progress reporter.

    Returns:
        The records, the ones awaiting texture grids, every shared texture
        index used, and how many vertices had to be clamped.
    """
    records: list[dict[str, Any]] = []
    pending: list[tuple[dict[str, Any], list[list[int]]]] = []
    used: set[int] = set()
    clamped = 0

    for coords, cell in sorted(outcome.cells.items()):
        heights = None
        if cell.heights is not None:
            heights = [
                [float(v) for v in cell.heights[y * LAND_SIZE : (y + 1) * LAND_SIZE]]
                for y in range(LAND_SIZE)
            ]
        world = None
        if cell.world_map is not None:
            world = [
                list(cell.world_map[y * WNAM_SIZE : (y + 1) * WNAM_SIZE]) for y in range(WNAM_SIZE)
            ]
        colors = None
        if cell.colors is not None:
            # Written out rather than built with a comprehension over range(3):
            # a generator makes this a tuple of *some* length as far as anything
            # reading the code or the types is concerned, and the record writer
            # needs exactly three. Spelling the channels out says which is which.
            channels = cell.colors
            colors = [
                [
                    (
                        max(0, min(255, channels[(y * LAND_SIZE + x) * 3])),
                        max(0, min(255, channels[(y * LAND_SIZE + x) * 3 + 1])),
                        max(0, min(255, channels[(y * LAND_SIZE + x) * 3 + 2])),
                    )
                    for x in range(LAND_SIZE)
                ]
                for y in range(LAND_SIZE)
            ]
        normals = None
        if cell.normals is not None:
            normals = [
                [
                    (
                        cell.normals[(y * LAND_SIZE + x) * 3],
                        cell.normals[(y * LAND_SIZE + x) * 3 + 1],
                        cell.normals[(y * LAND_SIZE + x) * 3 + 2],
                    )
                    for x in range(LAND_SIZE)
                ]
                for y in range(LAND_SIZE)
            ]
        try:
            record, clamps = build_landscape_record(
                coords, heights=heights, normals=normals, world_map=world, colors=colors
            )
        except (EmitError, ValueError) as exc:
            say(f"  cell {coords} could not be written: {exc}")
            continue

        clamped += len(clamps)
        if cell.textures is not None:
            rows = [
                list(cell.textures[y * TEXTURE_SIZE : (y + 1) * TEXTURE_SIZE])
                for y in range(TEXTURE_SIZE)
            ]
            pending.append((record, rows))
            used.update(v for row in rows for v in row)
        records.append(record)

    if clamped:
        say(f"  {clamped} vertex/vertices could not be expressed in VHGT and were clamped")
    return records, pending, used, clamped


def _finish_textures(
    pending: list[tuple[dict[str, Any], list[list[int]]]],
    used: set[int],
    known: KnownTextures,
    say: Callable[[str], None],
    *,
    substitute_unknown: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compact the texture table and attach every grid.

    Args:
        pending: Records awaiting their texture grid.
        used: Every shared index the merge paints with.
        known: The shared texture table.
        say: Progress reporter.
        substitute_unknown: Passed to
            :func:`~wraithguard.land.textures.compact_textures` -- replace a
            texture index no ``LTEX`` defines with the fallback rather than
            writing it through as a dangling index.

    Returns:
        The ``LTEX`` records to emit, and the plugins that supplied their file
        names -- which are dependencies of the merged file.
    """
    from wraithguard.land.textures import compact_textures

    if not pending:
        return [], []
    mapping, kept, unresolved = compact_textures(known, used, substitute_unknown=substitute_unknown)
    for record, rows in pending:
        compacted = [[mapping.get(value, value) for value in row] for row in rows]
        try:
            attach_texture_indices(record, compacted)
        except EmitError as exc:
            say(f"  texture grid could not be written: {exc}")
    say(f"  land textures: {len(known)} known, {len(kept)} painted by the merge")
    if unresolved:
        # A missing master left these indices with no LTEX. Say what was done
        # about it either way -- the fallback keeps the plugin loadable, the
        # passthrough keeps it honest, and the user chose which risk to take.
        count = len(unresolved)
        if substitute_unknown:
            say(
                f"  {count} texture index/indices no LTEX defines: substituted with the "
                "fallback (smallest painted texture) so the plugin loads -- a missing master"
            )
        else:
            say(
                f"  {count} texture index/indices no LTEX defines: passed through unchanged, "
                "so the plugin carries a dangling index -- a missing master"
            )
    sources = sorted({texture.source for texture in kept if texture.source})
    return build_texture_records(kept), sources


def _write(
    records: list[dict[str, Any]],
    master_names: Sequence[str],
    directories: Sequence[Path],
    target: Path,
    converter: str | None,
) -> None:
    """Serialise the records and encode them to a plugin.

    With a ``converter`` this hands the JSON to ``tes3conv`` (the verified path);
    without one it encodes in process with the native writer, which produces a
    byte-compatible plugin -- verified by having ``tes3conv`` read the native
    output back identically to its own. So a merge no longer needs ``tes3conv``.

    Args:
        records: Every record after the header.
        master_names: The masters to declare.
        directories: Data folders to find them in.
        target: The plugin to write.
        converter: The tes3conv executable, or ``None`` to encode natively.

    Raises:
        MergeServiceError: If a master cannot be measured, or the conversion
            fails.
    """
    masters: list[tuple[str, int]] = []
    for name in master_names:
        path = resolve_plugin(name, directories)
        if path is None:
            raise MergeServiceError(f"cannot find master {name} to measure it")
        try:
            masters.append((name, path.stat().st_size))
        except OSError as exc:
            raise MergeServiceError(f"cannot measure master {name}: {exc}") from exc

    document = build_plugin(records, masters)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not converter:
        from wraithguard.esp import EspError, plugin_from_json, write_plugin

        try:
            target.write_bytes(write_plugin(plugin_from_json(document)))
        except (OSError, EspError, ValueError) as exc:
            raise MergeServiceError(f"could not encode the merged plugin natively: {exc}") from exc
        return
    with tempfile.TemporaryDirectory() as scratch:
        as_json = Path(scratch) / "merged.json"
        as_json.write_text(json.dumps(document), encoding="utf-8")
        try:
            result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
                [converter, str(as_json), str(target), "--overwrite"],
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
                # No console flash when a --noconsole build encodes the merge.
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise MergeServiceError(f"tes3conv could not be run: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:300]
            raise MergeServiceError(f"tes3conv refused the merged JSON: {detail}")
