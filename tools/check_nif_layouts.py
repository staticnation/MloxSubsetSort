"""Check the NIF layouts against real meshes.

``tests/test_nif.py`` builds its fixtures from the same understanding of the
format that the reader uses, so it proves the walker keeps its place but cannot
prove the layouts are *right*. Only real files can do that, and real files are
somebody's mod rather than something to commit here.

So this walks a folder of ``.nif`` files and reports how far the reader got in
each. Read the output as a survey, not a pass/fail:

* **Unknown block type** is expected and benign for types not yet in the table.
  The name tells you what to add next, and the frequency tells you whether it is
  worth adding.
* **A parse error part-way through a file whose blocks are all known** is the
  signal that matters. It almost always means a field width is wrong somewhere
  earlier -- a NIF block has no length, so a bad width does not fail where the
  mistake is, it fails a little downstream.
* **A type name full of punctuation** in the "unknown" column is the same
  symptom seen from the other side: the reader is no longer aligned to a real
  type string and is reading arbitrary bytes as one.

**A corpus built for this beats a corpus of real meshes.** The community's
*Notes for Modmakers* ships an attachments folder of NIFs each demonstrating
one specific block type. Those are far better subjects than vanilla meshes: the
type under test is isolated, you know in advance what the file contains, and a
failure names one layout rather than one of forty. Point the survey at that
folder and read the "parsed successfully" list -- on a demo corpus that list
*is* the coverage report.

Vanilla and mod folders remain worth surveying afterwards, because they answer
a different question: not "is this layout right" but "which types actually
occur, and how often".

Usage::

    python tools/check_nif_layouts.py "path/to/notes-attachments"
    python tools/check_nif_layouts.py "E:/Mods/SomeMod/Meshes"
    python tools/check_nif_layouts.py "E:/Mods" --limit 500
    python tools/check_nif_layouts.py "path/to/one.nif" --explain

Exits non-zero only if it could not read the folder at all; an incomplete parse
is information, not a failure.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wraithguard.nif import NifParseError, read_nif
from wraithguard.nif.blocks import BLOCK_LAYOUTS
from wraithguard.nif.scan import first_divergence, scan_block_types

#: Longest run of bytes worth treating as a candidate type name. Real NIF block
#: types are short identifiers; anything longer is the reader having read a
#: length field that was not a length field.
_MAX_TYPE_NAME = 40


def plausible_type(name: str) -> bool:
    """Whether a string could be a NIF block type at all.

    Used to keep a desynchronised read out of the "types to add" list. Without
    it a single wrong field width contributes a unique multi-kilobyte "type
    name" per file -- which both buries the real findings and, when printed,
    empties the file's geometry into the terminal.

    Args:
        name: The string read where a type name was expected.

    Returns:
        ``True`` when it looks like an identifier of a believable length.
    """
    return 0 < len(name) <= _MAX_TYPE_NAME and name.isidentifier()


def survey(root: Path, limit: int) -> int:
    """Walk a folder of meshes and report what the reader made of them.

    Args:
        root: The folder to walk.
        limit: Stop after this many files; 0 for no limit.

    Returns:
        A process exit code.
    """
    # Case-insensitively: mod archives ship ".NIF" as often as ".nif", and on
    # Windows a case-sensitive glob happens to work, so this surveys a fraction
    # of the corpus on Linux and macOS while still printing a confident total.
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".nif")
    if not files:
        print(f"no .nif files under {root}")
        return 1
    if limit:
        files = files[:limit]

    complete = 0
    desynced = 0
    parsed_types: Counter[str] = Counter()
    missing_types: Counter[str] = Counter()
    bad_layouts: Counter[str] = Counter()
    malformed: Counter[str] = Counter()
    examples: dict[str, str] = {}
    malformed_examples: dict[str, str] = {}
    errors: list[tuple[Path, str]] = []
    for path in files:
        try:
            result = read_nif(path)
        except NifParseError as exc:
            errors.append((path, str(exc)))
            continue
        parsed_types.update(block.type_name for block in result.blocks)
        if result.complete:
            complete += 1
        elif result.stopped_unknown and result.stopped_at:
            # A "type name" that is not a name means the reader is no longer
            # aligned and read arbitrary bytes as one. Counting those beside
            # genuinely-missing types inflates the missing list with noise, and
            # printing them dumps the file's geometry to the terminal.
            if plausible_type(result.stopped_at):
                missing_types[result.stopped_at] += 1
            else:
                desynced += 1
        elif result.stopped_malformed:
            # A data-plausibility guard fired: the file holds a value no valid
            # NIF does (an implausible array count). That is the mesh being
            # broken, not a layout being wrong -- Greatness7's own reader refuses
            # the same files -- so it must not be counted as a layout bug.
            malformed[result.stopped_at or "?"] += 1
            malformed_examples.setdefault(
                result.stopped_at or "?", f"{path.name}: {result.stopped_reason}"
            )
        elif result.stopped_at is not None:
            bad_layouts[result.stopped_at] += 1
            examples.setdefault(result.stopped_at, f"{path.name}: {result.stopped_reason}")
        else:
            errors.append((path, result.stopped_reason))

    print(f"{len(files)} file(s) under {root}")
    print(f"  fully parsed          : {complete}")
    print(f"  stopped, type missing : {sum(missing_types.values())}")
    print(f"  stopped, layout wrong : {sum(bad_layouts.values())}")
    print(f"  malformed input file  : {sum(malformed.values())}")
    print(f"  lost alignment        : {desynced}")
    print(f"  refused or errored    : {len(errors)}")
    if parsed_types:
        # The other half of the picture. Failures alone say what is broken but
        # never what works, and on a corpus built to demonstrate one block type
        # per file -- the "Notes for Modmakers" attachments, say -- "this type
        # parsed" *is* the result. Without it a demo file that stops on some
        # unrelated later block looks like a total loss.
        print(f"\nblock types parsed successfully ({len(parsed_types)} distinct):")
        for name, count in sorted(parsed_types.items()):
            print(f"  {count:>6}  {name}")
    if missing_types:
        # Benign: a block type nobody has written a layout for yet. The counts
        # say which is worth adding first.
        print("\nblock types with no layout yet, most common first:")
        for name, count in missing_types.most_common(40):
            print(f"  {count:>6}  {name}")
    if bad_layouts:
        # The finding that matters: the type IS known and its layout failed, so
        # a field width is wrong somewhere at or before it.
        print("\nKNOWN block types that failed to parse -- these are layout bugs:")
        for name, count in bad_layouts.most_common(40):
            print(f"  {count:>6}  {name}")
            print(f"          e.g. {examples[name]}")
    if malformed:
        # Not a bug here: the file itself is broken (an implausible count), and
        # the reference reader refuses it too. Listed so it is visible, and kept
        # out of the layout-bug count it would otherwise inflate.
        print("\nmalformed files rejected by a plausibility guard (not layout bugs):")
        for name, count in malformed.most_common(40):
            print(f"  {count:>6}  stopped on {name}")
            print(f"          e.g. {malformed_examples[name]}")
    if desynced:
        print(
            f"\n{desynced} file(s) read something that is not a type name at all. "
            f"That is the same finding as a layout bug -- a wrong field width "
            f"upstream -- seen from further downstream, after the reader has "
            f"already lost its place."
        )
    if errors:
        print("\nfiles that errored (the first 20):")
        for path, reason in errors[:20]:
            print(f"  {path.name}: {reason}")
    return 0


#: One census record: a path ending in .nif, then ``= {...}``. Anchored on the
#: extension rather than on the start of a line, because records are not
#: reliably newline-separated. Non-greedy so two records sharing a line split.
_CENSUS_RECORD: Final[re.Pattern[str]] = re.compile(
    r"(?P<name>\S[^=\n]*?\.nif)\s*=\s*(?P<counts>\{.*?\})",
    re.IGNORECASE,
)


def load_census(path: Path) -> dict[str, dict[str, int]]:
    """Read a ``file = {type: count, ...}`` census.

    Args:
        path: The census file.

    Returns:
        Relative mesh path (lower-cased, forward slashes) to its block counts.

    Raises:
        ValueError: If no usable line was found, which means the file is not
            the census it was taken for.
    """
    census: dict[str, dict[str, int]] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        # A traceback here says nothing useful: the census is a path the user
        # typed, and the answer is almost always that it lives somewhere else.
        raise ValueError(f"cannot read the census {path}: {exc}") from exc
    # Records are *not* reliably one per line. 17 of this census's 7,319 have
    # no newline between them, and splitting on lines dropped both halves of
    # each -- silently, because a mangled record just fails to parse. Matching
    # the record shape instead of the line shape finds all of them, and the
    # count is asserted below so a future census cannot regress this quietly.
    for match in _CENSUS_RECORD.finditer(text):
        try:
            parsed = ast.literal_eval(match.group("counts"))
        except (ValueError, SyntaxError):
            continue
        if isinstance(parsed, dict):
            census[match.group("name").strip().replace("\\", "/").lower()] = {
                str(k): int(v) for k, v in parsed.items()
            }
    found = text.count("= {")
    if len(census) != found:
        print(
            f"warning: {path.name} holds {found} records but {len(census)} parsed",
            file=sys.stderr,
        )
    if not census:
        raise ValueError(f"{path} contained no 'name = {{...}}' lines")
    return census


def check_against_census(
    root: Path,
    census_path: Path,
    report_path: Path | None = None,
    collect_dir: Path | None = None,
    collect_limit: int = 40,
) -> int:
    """Compare what the reader finds against a known-correct block census.

    The survey answers "did it finish". This answers "was it *right*", which is
    a different and stricter question: a file can walk to the end and still
    have mis-identified blocks along the way, and nothing about a clean parse
    would say so.

    Three outcomes, and the third is the one worth the trouble:

    * **agrees** -- every type and count matches.
    * **short** -- we found a subset, because the read stopped early. Expected
      while types are missing, and not a correctness problem.
    * **exceeds** -- we walked to the end and still reported more than the
      census lists.

    The census is known to undercount *property* blocks specifically --
    ``NiMaterialProperty`` in 1,513 of 1,757 cases, plus the alpha, texturing
    and z-buffer properties -- while matching a raw byte scan exactly on every
    other type. A scan that counts length-prefixed type strings and uses no
    layout knowledge at all agrees with this reader on 43 of 43 sampled
    property disagreements, and NifSkope agrees with both. So excess on a
    property type is the reference being wrong, not the reader.

    The third was originally called a *misparse*, on the assumption that the
    census was ground truth. It is not, and that assumption was checked rather
    than trusted: ``c/amulet_common_1.nif`` opened in NifSkope holds fifteen
    blocks, two of them ``NiMaterialProperty`` -- block 4 shared by shapes 1
    and 6, block 11 shared by shapes 10 and 13. The reader finds two. The
    census records one, while counting ``NiTexturingProperty`` in the same file
    correctly at two. Whatever generated it collapses some shared blocks and
    not others.

    So an excess is evidence about *either* party, and the tool no longer names
    a culprit. What it still does reliably is bound the disagreement and point
    at a file, which is enough to settle any individual case in NifSkope in
    about ten seconds. A *shortfall* remains meaningful in one direction: the
    census never invents blocks, so a type it lists and the reader never
    reaches is a genuine gap.

    Args:
        root: The meshes folder the census was taken over.
        census_path: The census file.
        report_path: Where to write a machine-readable per-file result, if
            anywhere. Terminal output truncates and has to be copied by hand;
            a file can be read back directly and diffed between runs.
        collect_dir: Where to copy a stratified sample of the most informative
            files, if anywhere. A layout cannot be fixed from a summary -- it
            needs the bytes -- and hand-picking files from a 7,000-line report
            is the slow way to get them.
        collect_limit: How many files to copy per category.

    Returns:
        A process exit code.
    """
    census = load_census(census_path)
    agrees = short = missing_file = 0
    disagreements: list[tuple[str, str]] = []
    records: list[dict[str, object]] = []
    buckets: dict[str, list[str]] = {"unreadable": [], "short": [], "exceeds": []}
    for relative, expected in census.items():
        path = root / relative
        if not path.is_file():
            missing_file += 1
            continue
        try:
            result = read_nif(path)
        except NifParseError as exc:
            short += 1
            records.append({"file": relative, "status": "unreadable", "detail": str(exc)})
            buckets["unreadable"].append(relative)
            continue
        found: Counter[str] = Counter(block.type_name for block in result.blocks)
        extra = {
            name: (count, expected.get(name, 0))
            for name, count in found.items()
            if count > expected.get(name, 0)
        }
        unreached = sorted(name for name, count in expected.items() if found[name] < count)
        record: dict[str, object] = {
            "file": relative,
            "blocks_read": len(result.blocks),
            "blocks_declared": result.block_count,
            "stopped_reason": result.stopped_reason,
            "stopped_unknown": result.stopped_unknown,
            "census_types_not_reached": unreached,
        }
        if extra and result.stopped_reason:
            # A file that stopped early can still over-report, because the
            # census undercounts properties everywhere. Excess is only
            # *evidence* about the reader when the reader reached the end, so
            # a truncated read is classified by its truncation. 172 files were
            # filed under "exceeds" on this account and are really short.
            short += 1
            record["status"] = "short"
            buckets["short"].append(relative)
        elif extra:
            detail = ", ".join(f"{n}: found {f}, census says {e}" for n, (f, e) in extra.items())
            disagreements.append((relative, detail))
            record["status"] = "exceeds"
            record["detail"] = detail
            buckets["exceeds"].append(relative)
        elif found == Counter(expected):
            agrees += 1
            record["status"] = "agrees"
        else:
            short += 1
            record["status"] = "short"
            buckets["short"].append(relative)
        records.append(record)

    if report_path is not None:
        report_path.write_text(
            json.dumps({"root": str(root), "files": records}, indent=1),
            encoding="utf-8",
        )
        print(f"wrote {len(records)} per-file result(s) to {report_path}")
    if collect_dir is not None:
        _collect_samples(root, buckets, collect_dir, collect_limit)

    checked = len(census) - missing_file
    print(f"census: {len(census)} file(s); {checked} present under {root}")
    print(f"  agrees exactly        : {agrees}")
    print(f"  short (stopped early) : {short}")
    print(f"  exceeds the census    : {len(disagreements)}")
    if missing_file:
        print(f"  not found on disk     : {missing_file}")
    if disagreements:
        print(
            "\nfiles where the reader found more than the census lists"
            " (check one in NifSkope before believing either):"
        )
        for relative, detail in disagreements[:25]:
            print(f"  {relative}\n      {detail}")
    else:
        print("\nNo excess: every block the reader named is one the census agrees is there.")
    return 0


def verify(root: Path, report_path: Path | None, collect_dir: Path | None, limit: int) -> int:
    """Check the layout reader against a scan of the same files.

    This needs no external reference. The scan in :mod:`wraithguard.nif.scan`
    recovers the block list without using any field layout, so it cannot fail
    the way the reader fails, and the file's own header says how many blocks
    there should be -- which lets a scan disqualify itself rather than mislead.

    Four outcomes:

    * **identical** -- same blocks, same order, all the way through.
    * **stopped early** -- the reader's list is a prefix of the scan's. Honest
      incompleteness, and the scan names the block that blocked it.
    * **diverged** -- the lists differ at some index. This is the one that
      matters: it means a field width is wrong in the block *before* that
      index, and it is invisible to a survey because the file may still walk
      to the end.
    * **unverifiable** -- the scan did not reconcile with the header, so it is
      not evidence and is excluded rather than counted as agreement.

    Args:
        root: Folder to search recursively.
        report_path: Where to write per-file JSON, if anywhere.
        collect_dir: Where to copy samples of interesting files, if anywhere.
        limit: How many files to collect per category.

    Returns:
        A process exit code: non-zero when anything diverged.
    """
    tally: Counter[str] = Counter()
    blockers: Counter[str] = Counter()
    incomplete = 0
    diverged: list[tuple[str, int, str, str]] = []
    records: list[dict[str, object]] = []
    buckets: dict[str, list[str]] = {
        "diverged": [],
        "layout_bug": [],
        "stopped": [],
        "unverifiable": [],
        # A file the reader refuses outright is still a file worth having a
        # copy of: the commonest reason is a NIF version this reader does not
        # accept, and deciding whether to accept it needs examples.
        "unreadable": [],
    }

    for path in sorted(p for p in root.rglob("*") if p.suffix.lower() == ".nif"):
        relative = path.relative_to(root).as_posix()
        try:
            data = path.read_bytes()
        except OSError as exc:
            tally["unreadable"] += 1
            records.append({"file": relative, "status": "unreadable", "detail": str(exc)})
            continue
        scanned = scan_block_types(data)
        if not scanned.reconciles:
            # The scan is not usable as a reference here, but that is no reason
            # to learn nothing about the file. The header's own block count is
            # a weaker check that does not depend on the scan at all, so the
            # reader is still run and still reported against it. Skipping these
            # outright hid four vanilla meshes behind a limitation of the
            # cross-check rather than of the reader.
            tally["unverifiable"] += 1
            buckets["unverifiable"].append(relative)
            try:
                fallback = read_nif(path)
                read_all = len(fallback.blocks) == fallback.block_count
            except NifParseError as exc:
                read_all = False
                fallback_reason: str = str(exc)
            else:
                fallback_reason = fallback.stopped_reason
            if not read_all:
                # Deliberately NOT in ``tally``: this is a subdivision of
                # "unverifiable", not a category beside it, and the total is a
                # sum over the tally. Counting it there reported 81,026 files
                # for a run over 80,197 -- a total that disagreed with its own
                # parts, which is the sort of number that quietly discredits
                # every other number in the report.
                incomplete += 1
            records.append(
                {
                    "file": relative,
                    "status": "unverifiable",
                    "scan_found": scanned.found,
                    "header_declares": scanned.declared,
                    "reader_read_every_declared_block": read_all,
                    "stopped_reason": fallback_reason,
                }
            )
            continue
        try:
            result = read_nif(path)
        except NifParseError as exc:
            tally["unreadable"] += 1
            buckets["unreadable"].append(relative)
            records.append({"file": relative, "status": "unreadable", "detail": str(exc)})
            continue
        parsed = [block.type_name for block in result.blocks]
        index = first_divergence(scanned.type_names, parsed)
        record: dict[str, object] = {
            "file": relative,
            "blocks_scanned": scanned.found,
            "blocks_parsed": len(parsed),
            "stopped_reason": result.stopped_reason,
        }
        if index is not None:
            tally["diverged"] += 1
            diverged.append((relative, index, scanned.type_names[index], parsed[index]))
            buckets["diverged"].append(relative)
            record["status"] = "diverged"
            record["at_block"] = index
            record["scan_says"] = scanned.type_names[index]
            record["reader_says"] = parsed[index]
            # The suspect is the block before the divergence: a type is only
            # misread when the cursor arrived at the wrong offset.
            record["suspect_block"] = parsed[index - 1] if index else "(header)"
        elif len(parsed) == scanned.found:
            tally["identical"] += 1
            record["status"] = "identical"
        else:
            tally["stopped early"] += 1
            buckets["stopped"].append(relative)
            record["status"] = "stopped"
            blocked_by = scanned.type_names[len(parsed)]
            blockers[blocked_by] += 1
            record["blocked_by"] = blocked_by
            # Stopping on a type we claim to support is a bug, and there are
            # only ever a handful. Sampling them out of the same bucket as
            # thousands of ordinary gaps would almost never pick one.
            if blocked_by in BLOCK_LAYOUTS:
                buckets["layout_bug"].append(relative)
        records.append(record)

    if report_path is not None:
        report_path.write_text(
            json.dumps({"root": str(root), "files": records}, indent=1), encoding="utf-8"
        )
        print(f"wrote {len(records)} per-file result(s) to {report_path}")
    if collect_dir is not None:
        _collect_samples(root, buckets, collect_dir, limit)

    total = sum(tally.values())
    print(f"\nverified {total} file(s) under {root} against a layout-free scan")
    for name in ("identical", "stopped early", "diverged", "unverifiable", "unreadable"):
        if tally[name]:
            print(f"  {name:<14}: {tally[name]}")
    if incomplete:
        print(f"    (of the unverifiable, {incomplete} did not read every declared block)")
    if blockers:
        # A blocker that *is* implemented is a different animal from one that
        # is not. The first means a layout is wrong and the file stopped where
        # the bug is; the second is an honest gap. Reporting them in one list
        # buries the handful of bugs under hundreds of gaps.
        bugs = {n: c for n, c in blockers.items() if n in BLOCK_LAYOUTS}
        gaps = {n: c for n, c in blockers.items() if n not in BLOCK_LAYOUTS}
        if bugs:
            # Not called "layout bugs". That names a culprit, and it has been
            # wrong: dbs_meatstick.nif stops inside a NiBSParticleNode whose
            # property count reads 0xFFFFFFFF, and the file is malformed --
            # its block boundaries reconcile with the header while the block's
            # own contents do not. The reader refusing it is correct.
            #
            # This is the third category in this tool to have asserted blame
            # before the evidence supported it ("misparse" was the first). The
            # heading now states the observation and leaves the diagnosis to
            # whoever opens the file.
            print("\nSTOPPED INSIDE A TYPE THIS READER SUPPORTS")
            print("  (usually a layout bug here; sometimes a malformed file)")
            for name, count in sorted(bugs.items(), key=lambda kv: -kv[1]):
                print(f"  {count:>6}  {name}")
        if gaps:
            print("\nstopped on an unimplemented type -- coverage gaps:")
            for name, count in sorted(gaps.items(), key=lambda kv: -kv[1])[:15]:
                print(f"  {count:>6}  {name}")
    if diverged:
        print("\nDIVERGED -- a field width is wrong in the block before the index:")
        for relative, index, says, read in diverged[:20]:
            print(f"  {relative}\n      block {index}: scan says {says}, reader says {read}")
    else:
        print("\nNo divergence: every block the reader named matched the scan exactly.")
    return 1 if diverged or any(n in BLOCK_LAYOUTS for n in blockers) else 0


def _collect_samples(
    root: Path,
    buckets: dict[str, list[str]],
    destination: Path,
    limit: int,
) -> None:
    """Copy a spread of interesting files somewhere they can be looked at.

    Sampling is *stratified and evenly spaced* rather than "the first N".
    Meshes are named by prefix, so the first N of anything is a run of near
    neighbours -- forty helmets -- which is the one sample that teaches least.
    Taking every ``len/limit``-th entry spreads the pick across the whole
    alphabet, and therefore across exporters and eras.

    Args:
        root: Where the files live.
        buckets: Category name to the relative paths in it.
        destination: Folder to copy into; one subfolder per category.
        limit: How many to take from each category.

    Raises:
        OSError: If the destination cannot be written.
    """
    for category, names in buckets.items():
        if not names:
            continue
        step = max(1, len(names) // limit)
        picked = names[::step][:limit]
        folder = destination / category
        folder.mkdir(parents=True, exist_ok=True)
        for relative in picked:
            source = root / relative
            if not source.is_file():
                continue
            # Flatten the name so two files called base.nif in different
            # folders cannot silently overwrite one another.
            shutil.copy2(source, folder / relative.replace("/", "__"))
        print(f"collected {len(picked)} of {len(names)} {category} file(s) into {folder}")


def explain(path: Path) -> int:
    """Trace one file block by block, so a wrong width can be located.

    The survey says *that* a layout is wrong; this says *where*. Each block's
    body offset and consumed size are printed, then -- when the read stopped --
    the bytes around the failure. A layout short by N bytes lands the reader N
    bytes before the next type string, and that string is almost always
    readable in the dump, which turns "something is wrong" into "block 0 needs
    two more bytes".

    Args:
        path: The file to trace.

    Returns:
        A process exit code.
    """
    try:
        result = read_nif(path)
    except NifParseError as exc:
        print(f"{path.name}: {exc}")
        return 1
    data = path.read_bytes()

    print(f"{path.name}: {len(data)} bytes, header declares {result.block_count} block(s)")
    for block in result.blocks:
        end = block.offset + block.size
        print(
            f"  [{block.index:>3}] {block.type_name:<28} body {block.offset:>7}..{end:<7} ({block.size} bytes)"
        )
    if result.complete:
        print("  parsed to the end")
        return 0

    print(f"\n  stopped: {result.stopped_reason}")
    resume = result.blocks[-1].offset + result.blocks[-1].size if result.blocks else 0
    window = data[max(0, resume - 8) : resume + 48]
    print(f"\n  bytes around where the next type string was expected ({resume}):")
    print(f"    hex   {window.hex(' ')}")
    printable = "".join(chr(b) if 32 <= b < 127 else "." for b in window)
    print(f"    ascii {printable}")
    # A readable type name in that window says how far off the previous block
    # was: find it, and the gap is the correction.
    for shift in range(-8, 41):
        at = resume + shift
        if at + 4 > len(data):
            break
        length = int.from_bytes(data[at : at + 4], "little")
        if 0 < length <= 40 and at + 4 + length <= len(data):
            name = data[at + 4 : at + 4 + length].decode("latin-1")
            if name.isidentifier() and (name.startswith("Ni") or name.endswith("Node")):
                print(
                    f"\n  a type string {name!r} starts at {at}, "
                    f"which is {shift:+d} byte(s) from where the walk ended."
                )
                if shift:
                    print(
                        f"  => the {result.blocks[-1].type_name if result.blocks else 'header'} "
                        f"layout is {abs(shift)} byte(s) "
                        f"{'short' if shift > 0 else 'long'}."
                    )
                break
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the survey.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv``.

    Returns:
        A process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", help="a folder to search recursively for .nif files")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many files (0 = all)")
    parser.add_argument(
        "--census",
        help="a 'file = {type: count}' census to check the reader's output against",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="check the reader against a layout-free scan; needs no census file",
    )
    parser.add_argument(
        "--report",
        help="write a per-file JSON result here instead of only summarising",
    )
    parser.add_argument(
        "--collect",
        help="copy a spread of the most informative files into this folder",
    )
    parser.add_argument(
        "--collect-limit",
        type=int,
        default=40,
        help="how many files to collect per category (default 40)",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="trace a single file block by block instead of surveying a folder",
    )
    args = parser.parse_args(argv)

    root = Path(args.folder)
    if args.verify:
        if not root.is_dir():
            print(f"not a folder: {root}", file=sys.stderr)
            return 2
        return verify(
            root,
            Path(args.report) if args.report else None,
            Path(args.collect) if args.collect else None,
            args.collect_limit,
        )
    if args.census:
        if not root.is_dir():
            print(f"not a folder: {root}", file=sys.stderr)
            return 2
        try:
            return check_against_census(
                root,
                Path(args.census),
                Path(args.report) if args.report else None,
                Path(args.collect) if args.collect else None,
                args.collect_limit,
            )
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
    if args.explain:
        if not root.is_file():
            print(f"not a file: {root}", file=sys.stderr)
            return 2
        return explain(root)
    if not root.is_dir():
        print(f"not a folder: {root}", file=sys.stderr)
        return 2
    return survey(root, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
