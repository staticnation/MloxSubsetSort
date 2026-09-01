r"""Build a ``Merged Lands.esp`` that recovers terrain a load order discards.

**What it produces.** One plugin holding ``LTEX`` and ``LAND`` records for
every exterior cell more than one mod edits. Place it last in the load order.
Where two mods changed different vertices of the same cell, both survive; where
they changed the same vertex, a strategy decides (see
:mod:`wraithguard.land.merge`).

**What it does not touch.** References, objects, NPCs, scripts, interiors.
Cells are read for their terrain and nothing else, so everything else in the
load order resolves exactly as it did before. The output is additive: your mods
are never modified, and deleting the merged plugin restores the previous
behaviour completely.

**The safety argument, such as it is.** The binary encoding is done by
``tes3conv`` rather than by code written here, and
``tools/check_plugin_roundtrip.py`` established that its conversion converges on
real plugins. Every grid this writes was verified to decode back identically on
real cells. That is a good position, and it is still a new file being loaded by
a game -- so back up your saves before playing with a merged plugin for the
first time, and read the summary this prints rather than trusting it silently.

**Load order matters.** Texture indices resolve against the load order, and a
merge performed in the wrong order paints terrain with the wrong textures. Pass
``--order`` with a real plugin list. Without one the tool falls back to
alphabetical and says so, which is fine for a dry run and not for a plugin you
intend to play with.

Usage:
    python tools/build_merged_lands.py "E:/OpenMW/Morrowind/Data Files" --dry-run
    python tools/build_merged_lands.py "E:/.../Data Files" --order plugins.txt
    python tools/build_merged_lands.py --json-dir tes3conv_json --order plugins.txt \
        --out "E:/.../Data Files/Merged Lands.esp"
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.check_plugin_roundtrip import find_tes3conv
from tools.survey_landscape import VANILLA_MASTERS, _from_dump, _from_plugins, read_order
from wraithguard.land.cells import cells_for, merge_cells
from wraithguard.land.conflict_image import landmass_conflict_image
from wraithguard.land.diff import LandData, RelativeGrid
from wraithguard.land.emit import (
    EmitError,
    attach_texture_indices,
    build_landscape_record,
    build_plugin,
    build_texture_records,
)
from wraithguard.land.landmass import build_reference
from wraithguard.land.merge import ConflictStrategy, merge_layer
from wraithguard.land.meta import MetaError, load_all
from wraithguard.land.pipeline import finish, merge_landmass
from wraithguard.land.textures import compact_textures
from wraithguard.tes3fields.landscape import LAND_SIZE, WNAM_SIZE

_log: Final = logging.getLogger("merged_lands")

#: Default output name, matching what the community expects to see.
DEFAULT_NAME: Final = "Merged Lands.esp"


def _rows_from(grid: RelativeGrid) -> list[list[float]]:
    """Turn a merged single-component grid into rows.

    Args:
        grid: The merged differences.

    Returns:
        Rows of values.
    """
    flat = grid.to_flat()
    side = grid.side
    return [[float(v) for v in flat[y * side : (y + 1) * side]] for y in range(side)]


def _int_rows_from(grid: RelativeGrid) -> list[list[int]]:
    """Turn a merged single-component grid into integer rows.

    Args:
        grid: The merged differences.

    Returns:
        Rows of integers.
    """
    flat = grid.to_flat()
    side = grid.side
    return [[int(v) for v in flat[y * side : (y + 1) * side]] for y in range(side)]


def _triples_from(grid: RelativeGrid) -> list[list[tuple[int, int, int]]]:
    """Turn a merged three-component grid into rows of triples.

    Args:
        grid: The merged differences.

    Returns:
        Rows of ``(r, g, b)`` triples, each component clamped to a byte -- a
        merge can average two colours into a value a byte cannot hold, and
        wrapping would show as a bright speck in dark terrain.
    """
    flat = grid.to_flat()
    side = grid.side
    return [
        [
            tuple(max(0, min(255, flat[(y * side + x) * 3 + c])) for c in range(3))  # type: ignore[misc]
            for x in range(side)
        ]
        for y in range(side)
    ]


def _normal_rows(flat: list[int] | None) -> list[list[tuple[int, int, int]]] | None:
    """Read the pipeline's resolved normals back as rows of triples.

    Args:
        flat: Interleaved signed-byte components, or ``None``.

    Returns:
        Rows of ``(x, y, z)``, or ``None`` when the pipeline supplied none --
        in which case the writer recomputes them from the heights.
    """
    if flat is None:
        return None
    return [
        [
            (
                flat[(y * LAND_SIZE + x) * 3],
                flat[(y * LAND_SIZE + x) * 3 + 1],
                flat[(y * LAND_SIZE + x) * 3 + 2],
            )
            for x in range(LAND_SIZE)
        ]
        for y in range(LAND_SIZE)
    ]


def merge_cell(
    changes: list[Any], strategy: ConflictStrategy
) -> tuple[
    RelativeGrid | None, RelativeGrid | None, RelativeGrid | None, RelativeGrid | None, int, int
]:
    """Fold every plugin's edits to one cell into a single result.

    Plugins are folded in load order, so ``OVERWRITE`` really does mean the
    later plugin, and each step merges the accumulated result with the next
    plugin's differences.

    Args:
        changes: One :class:`~wraithguard.land.diff.LandscapeDiff` per plugin
            that changed the cell, in load order.
        strategy: How to settle contested vertices.

    Returns:
        The merged heights, merged textures, contested count and major count.
    """
    heights: RelativeGrid | None = None
    textures: RelativeGrid | None = None
    world_map: RelativeGrid | None = None
    colors: RelativeGrid | None = None
    contested = major = 0

    # A cell the masters never had has no common ancestor, and averaging is
    # meaningless without one. Two landmass mods that each define their own
    # version of the same new cell have not "edited different vertices of a
    # shared terrain" -- they have authored two unrelated landscapes, and every
    # vertex reads as contested because both differ from a zero reference.
    # Blending them produces terrain neither author made: a smeared average of
    # two different coastlines. Last-wins is the only defensible answer there,
    # and it is what the load order would have done anyway.
    if any(change.new_land for change in changes):
        strategy = ConflictStrategy.OVERWRITE

    for change in changes:
        if change.heights is not None:
            if heights is None:
                heights = change.heights
            else:
                heights, report = merge_layer(
                    LandData.VERTEX_HEIGHTS, heights, change.heights, strategy
                )
                contested += report.contested
                major += report.major
        if change.textures is not None:
            if textures is None:
                textures = change.textures
            else:
                # Textures never average -- an averaged index names a texture
                # neither mod chose. merge_layer enforces this; AUTO is used
                # here rather than the caller's strategy so a --strategy
                # resolve does not reach a layer that cannot honour it.
                textures, _ = merge_layer(
                    LandData.TEXTURES, textures, change.textures, ConflictStrategy.AUTO
                )
        # The world map and vertex colours are merged too, not defaulted. tes3
        # requires every grid to be present in the JSON, so a cell has to carry
        # them either way; carrying a *merged* one costs nothing extra and
        # keeps the world map and lighting consistent with the terrain.
        # CURVATURE is a height-only idea, so these use AUTO.
        if change.world_map is not None:
            world_map = (
                change.world_map
                if world_map is None
                else merge_layer(
                    LandData.WORLD_MAP, world_map, change.world_map, ConflictStrategy.AUTO
                )[0]
            )
        if change.colors is not None:
            colors = (
                change.colors
                if colors is None
                else merge_layer(
                    LandData.VERTEX_COLORS, colors, change.colors, ConflictStrategy.AUTO
                )[0]
            )
    return heights, textures, world_map, colors, contested, major


def master_sizes(folder: Path | None, names: list[str]) -> list[tuple[str, int]]:
    """Look up each master's size on disk.

    The engine matches masters by name *and* size. A wrong size is not a
    cosmetic problem, so a master that cannot be measured is refused rather
    than guessed at.

    Args:
        folder: The Data Files directory, or ``None`` when unavailable.
        names: Master file names.

    Returns:
        ``(name, size)`` pairs.

    Raises:
        SystemExit: If a master cannot be found and measured.
    """
    if folder is None:
        raise SystemExit(
            "the masters' file sizes are needed for the plugin header and can "
            "only be read from the real files. Run against a Data Files "
            "directory, or pass --data-files alongside --json-dir."
        )
    present = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    sizes: list[tuple[str, int]] = []
    for name in names:
        path = present.get(name.lower())
        if path is None:
            raise SystemExit(f"master {name} is not in {folder}; cannot write a header.")
        sizes.append((path.name, path.stat().st_size))
    return sizes


def main(argv: list[str] | None = None) -> int:
    """Build a merged landscape plugin.

    Args:
        argv: Command-line arguments.

    Returns:
        A process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, nargs="?", help="a Data Files directory")
    parser.add_argument("--json-dir", type=Path, default=None, help="an existing tes3conv dump")
    parser.add_argument("--data-files", type=Path, default=None, help="masters, with --json-dir")
    parser.add_argument("--order", type=Path, default=None, help="a load order, one plugin a line")
    parser.add_argument("--out", type=Path, default=None, help=f"output path ({DEFAULT_NAME})")
    parser.add_argument("--tes3conv", default=None, help="path to the converter")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many mods")
    parser.add_argument(
        "--strategy",
        choices=[s.value for s in ConflictStrategy],
        default=ConflictStrategy.AUTO.value,
        help="how to settle a vertex two mods both moved",
    )
    parser.add_argument(
        "--no-seam-repair",
        action="store_true",
        help="skip seam repair (diagnosis only; output can tear at cell borders)",
    )
    parser.add_argument(
        "--no-clean", action="store_true", help="keep cells the load order already delivers"
    )
    parser.add_argument(
        "--no-slope-limit",
        action="store_true",
        help="skip terrain conditioning (the writer will clamp unencodable steps)",
    )
    parser.add_argument(
        "--cells",
        action="store_true",
        help="also emit CELL records (region, water height, map colour; never references)",
    )
    parser.add_argument(
        "--add-debug-vertex-colors",
        action="store_true",
        help="paint conflict severity into the terrain. Diagnostic, not playable",
    )
    parser.add_argument(
        "--conflicts-dir",
        type=Path,
        default=None,
        help="write a PNG conflict map of the whole landmass here",
    )
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    parser.add_argument("--verbose", action="store_true", help="per-plugin warnings")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR, format="%(message)s")

    if (args.folder is None) == (args.json_dir is None):
        print("give either a Data Files directory or --json-dir, not both.", file=sys.stderr)
        return 2

    tool = find_tes3conv(args.tes3conv)
    if tool is None and not args.dry_run:
        print(
            "tes3conv was not found; encoding the merged plugin with the "
            "built-in writer instead (byte-compatible, verified against tes3conv).",
            file=sys.stderr,
        )

    order = read_order(args.order) if args.order else []
    if not order:
        print(
            "WARNING: no --order given, so plugins merge alphabetically.\n"
            "         Texture indices resolve against load order, so a plugin\n"
            "         built this way may paint terrain wrongly. Fine for a dry\n"
            "         run; pass --order before playing with the result.\n"
        )

    if args.json_dir is not None:
        loaded, mods, unreadable = _from_dump(args.json_dir, order, args.limit)
        data_files = args.data_files
    else:
        loaded, mods, unreadable = _from_plugins(args.folder, order, args.limit, args.tes3conv)
        data_files = args.folder
    if loaded is None:
        return 2

    reference, known = build_reference(loaded)
    print(f"reference: {len(reference)} cell(s), {len(known)} land texture(s)")
    if unreadable:
        print(f"{len(unreadable)} plugin(s) could not be read: {', '.join(unreadable[:5])}")

    try:
        metas = load_all(data_files, [p.name for p in mods]) if data_files else {}
    except MetaError as exc:
        print(f"a .mergedlands.toml could not be trusted: {exc}", file=sys.stderr)
        return 2
    patched = [name for name, meta in metas.items() if meta.layers]
    if patched:
        print(f"read merge settings from {len(patched)} .mergedlands.toml sidecar(s)")

    strategy = ConflictStrategy(args.strategy)
    outcome = merge_landmass(reference, mods, known, metas, strategy)
    for name, why in outcome.skipped_plugins:
        print(f"  skipped {name}: {why}")
    print(f"\nmerged {len(outcome.cells)} modified cell(s)")

    finish(
        outcome,
        reference,
        repair=not args.no_seam_repair,
        clean=not args.no_clean,
        limit=not args.no_slope_limit,
    )

    if not args.no_seam_repair:
        print(
            f"  seam repair moved {outcome.seams.total} vertex/vertices "
            f"({outcome.seams.corner_vertices} at corners), widest gap "
            f"{outcome.seams.largest_gap} units"
        )
    if not args.no_slope_limit and outcome.slopes.adjusted:
        print(
            f"  slope limiter moved {outcome.slopes.adjusted} vertex/vertices in "
            f"{outcome.slopes.passes} pass(es) so every step fits in VHGT"
        )
        if not outcome.slopes.converged:
            print("  WARNING: some steps remain unencodable and will be clamped")
    if not args.no_clean:
        print(
            f"  cleaning dropped {outcome.cleaning.dropped} redundant cell(s) "
            f"({outcome.cleaning.unmodified} unmodified, "
            f"{outcome.cleaning.single_source} already delivered by one mod)"
        )
        if outcome.cleaning.kept_for_seams:
            print(
                f"  {outcome.cleaning.kept_for_seams} single-mod cell(s) kept only "
                "because seam repair moved them"
            )

    records: list[dict[str, Any]] = []
    total_contested = sum(c.contested for c in outcome.cells.values())
    total_major = sum(c.major for c in outcome.cells.values())
    total_clamped = 0
    new_land_cells = sum(1 for c in outcome.cells.values() if c.new_land)
    skipped: list[tuple[int, int]] = []
    pending_textures: list[tuple[dict[str, Any], list[list[int]]]] = []
    used_indices: set[int] = set()

    for coords, cell in sorted(outcome.cells.items()):
        height_rows = None
        if cell.heights is not None:
            height_rows = [
                [float(v) for v in cell.heights[y * LAND_SIZE : (y + 1) * LAND_SIZE]]
                for y in range(LAND_SIZE)
            ]
        world_rows = None
        if cell.world_map is not None:
            world_rows = [
                list(cell.world_map[y * WNAM_SIZE : (y + 1) * WNAM_SIZE]) for y in range(WNAM_SIZE)
            ]
        color_rows = None
        if cell.colors is not None:
            color_rows = [
                [
                    tuple(
                        max(0, min(255, cell.colors[(y * LAND_SIZE + x) * 3 + c])) for c in range(3)
                    )
                    for x in range(LAND_SIZE)
                ]
                for y in range(LAND_SIZE)
            ]
        try:
            record, clamped = build_landscape_record(
                coords,
                heights=height_rows,
                normals=_normal_rows(cell.normals),
                world_map=world_rows,
                colors=color_rows,
            )
        except (EmitError, ValueError) as exc:
            _log.warning("cell %s could not be written: %s", coords, exc)
            skipped.append(coords)
            continue

        if cell.textures is not None:
            rows = [list(cell.textures[y * 16 : (y + 1) * 16]) for y in range(16)]
            pending_textures.append((record, rows))
            used_indices.update(v for row in rows for v in row)

        records.append(record)
        total_clamped += len(clamped)

    cells_written = len(records)
    print(f"\nwriting {cells_written} cell(s)")
    print(f"  {total_contested} vertex/vertices both mods moved, settled by {strategy.value}")
    print(f"  {total_major} of those landed far from at least one mod's intent")
    if new_land_cells:
        print(f"  {new_land_cells} cell(s) are land the masters never had")
    if total_clamped:
        print(
            f"  {total_clamped} vertex/vertices were too steep for VHGT to express "
            "and were clamped."
        )
    if skipped:
        print(f"  {len(skipped)} cell(s) skipped: {', '.join(str(c) for c in skipped[:5])}")

    if args.conflicts_dir is not None:
        severity = {
            coords: ("major" if cell.major else "minor" if cell.contested else "clean")
            for coords, cell in outcome.cells.items()
        }
        if severity:
            args.conflicts_dir.mkdir(parents=True, exist_ok=True)
            target = args.conflicts_dir / "MERGED.png"
            target.write_bytes(landmass_conflict_image(severity))
            majors = sum(1 for level in severity.values() if level == "major")
            print(
                f"\nwrote {target} -- one block per cell, red where a merge "
                f"landed far from an intent ({majors} of {len(severity)})"
            )

    cell_records: list[dict[str, Any]] = []
    if args.cells:
        merged_cells = merge_cells(
            [(p.name, p.records) for p in (*loaded, *mods)],
            skip=frozenset(name for name, _ in outcome.skipped_plugins),
        )
        cell_records = cells_for(merged_cells, set(outcome.cells))
        print(
            f"\nemitting {len(cell_records)} CELL record(s) -- region, water "
            "height, map colour and flags. References are never carried, so "
            "nothing placed in these cells moves."
        )
        records = [*records, *cell_records]

    if not records:
        print("\nNothing to merge. No plugin was written.")
        return 0

    # Second pass: compact the shared table down to what the merge actually
    # paints with, rewrite every grid into the compacted numbering, and emit
    # one LTEX record per surviving texture.
    texture_records: list[dict[str, Any]] = []
    if pending_textures:
        mapping, kept = compact_textures(known, used_indices)
        unresolved = 0
        for record, rows in pending_textures:
            compacted = [[mapping.get(value, value) for value in row] for row in rows]
            unresolved += sum(1 for row in rows for value in row if value not in mapping)
            try:
                attach_texture_indices(record, compacted)
            except EmitError as exc:
                _log.warning("cell textures could not be written: %s", exc)
        texture_records = build_texture_records(kept)
        print(
            f"\nland textures: {len(known)} known, {len(kept)} actually painted "
            f"by the merge and emitted"
        )
        if unresolved:
            print(
                f"  {unresolved} painted index/indices resolve to no LTEX record and were\n"
                "  left as they are. That is a missing master, not a merge fault."
            )
        # LTEX records must precede the LAND records that index them.
        records = [*texture_records, *records]

    # LTEX records are deliberately NOT emitted yet. A merged plugin that
    # renumbers textures must carry the whole table, and doing that correctly
    # needs the load order the textures were resolved against. Emitting a
    # partial table would produce terrain painted with the wrong textures --
    # worse than not merging them at all -- so texture merging stays behind
    # --order and is reported rather than guessed.
    if not order:
        textured = len(pending_textures)
        if textured:
            print(
                f"\nNOTE: {textured} cell(s) carry merged texture indices resolved "
                "in alphabetical\norder, which is probably not your load order. "
                "Re-run with --order before using this."
            )

    if args.dry_run:
        print("\n--dry-run: nothing was written.")
        return 0

    masters = master_sizes(data_files, list(VANILLA_MASTERS))
    document = build_plugin(records, masters)

    out = args.out or ((data_files or Path.cwd()) / DEFAULT_NAME)
    with tempfile.TemporaryDirectory() as scratch:
        as_json = Path(scratch) / "merged.json"
        as_json.write_text(json.dumps(document), encoding="utf-8")
        assert tool is not None  # noqa: S101 -- checked above when not a dry run
        try:
            result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
                [tool, str(as_json), str(out), "--overwrite"],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"tes3conv could not be run: {exc}", file=sys.stderr)
            return 2
        if result.returncode != 0:
            print(
                f"tes3conv refused the JSON: {(result.stderr or result.stdout).strip()[:300]}",
                file=sys.stderr,
            )
            return 1

    print(f"\nwrote {out} ({out.stat().st_size} bytes, {len(document)} records)")
    if args.add_debug_vertex_colors:
        print(
            "\nWARNING: --add-debug-vertex-colors was on. The terrain in this "
            "plugin is\npainted red/yellow/green by conflict severity and is "
            "for inspection, not play.\nRebuild without the switch before "
            "using it."
        )
    print(
        "\nPlace it LAST in your load order. It carries terrain only -- no "
        "references,\nobjects or scripts -- so deleting it restores your "
        "previous behaviour exactly.\nBack up your saves before playing with it "
        "the first time."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
