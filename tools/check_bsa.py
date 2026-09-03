"""Check the BSA reader against a real Morrowind archive.

The reader's own tests build a synthetic archive and read it back, which proves
the reader and the test writer agree -- and nothing more. Both encode the same
understanding of the format, so a wrong understanding passes cleanly. This
checks against an archive Bethesda actually shipped, which is the only thing
that can find that class of error.

What it verifies:

* the index parses and holds a plausible number of files;
* every entry lies inside the file, which is what a wrong table offset breaks;
* names look like asset paths rather than binary noise, which is what a wrong
  *name table* offset breaks while leaving the sizes looking fine;
* extracted bytes carry the right magic for their extension -- a ``.dds`` that
  does not start with ``DDS `` means the data offset is wrong even though every
  other number looked right.

That last one matters most: an index can be entirely self-consistent and still
point at the wrong place.

Usage:
    python tools/check_bsa.py "E:/OpenMW/Morrowind/Data Files/Morrowind.bsa"
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wraithguard.nif.bsa import BsaArchive, BsaError

#: Leading bytes each kind of asset should have, by extension. Used to tell a
#: correct data offset from a plausible-looking wrong one.
MAGIC: dict[str, bytes] = {
    ".dds": b"DDS ",
    ".nif": b"NetImmerse",
    ".kf": b"NetImmerse",
    ".bmp": b"BM",
    ".wav": b"RIFF",
}

#: How many files to extract and check. The archive holds thousands; a spread
#: of a few hundred finds a systematic offset error just as well as all of them
#: and does not read half a gigabyte to do it.
SAMPLE = 300


def main(argv: list[str] | None = None) -> int:
    """Check one archive.

    Args:
        argv: Command-line arguments.

    Returns:
        A process exit code; non-zero when something did not check out.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("archive", help="a Morrowind .bsa file")
    parser.add_argument(
        "--sample", type=int, default=SAMPLE, help=f"files to extract (default {SAMPLE})"
    )
    args = parser.parse_args(argv)

    path = Path(args.archive)
    try:
        archive = BsaArchive(path)
    except BsaError as exc:
        print(f"could not open: {exc}", file=sys.stderr)
        return 2

    size = path.stat().st_size
    names = archive.names
    print(f"{path.name}: {len(names)} file(s), {size / 1e6:.1f} MB")

    kinds = Counter(Path(n).suffix.lower() for n in names)
    print("  contents:", ", ".join(f"{n}{s}" for s, n in kinds.most_common(6)))

    # Names should look like paths, not like binary read at the wrong offset.
    odd = [n for n in names if not n or any(ch < " " for ch in n)]
    print(f"  names that are not plausible paths: {len(odd)}")

    step = max(1, len(names) // max(1, args.sample))
    checked = mismatched = unreadable = 0
    for name in names[::step][: args.sample]:
        try:
            data = archive.read(name)
        except BsaError:
            unreadable += 1
            continue
        if data is None:  # pragma: no cover -- names come from the archive's own
            unreadable += 1  # index, so every one resolves; None is a defensive guard
            continue
        checked += 1
        expected = MAGIC.get(Path(name).suffix.lower())
        if expected and not data.startswith(expected):
            mismatched += 1
            if mismatched <= 5:
                print(f"    WRONG MAGIC: {name} starts {data[:8]!r}, expected {expected!r}")

    print(f"  extracted {checked} file(s): {mismatched} with wrong magic, {unreadable} unreadable")
    if mismatched or unreadable or odd:
        print("\nSomething is off -- the index parses but does not point where it should.")
        return 1
    print("\nEvery sampled file starts with the magic its extension implies.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
