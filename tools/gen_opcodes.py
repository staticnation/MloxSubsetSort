"""Regenerate ``mlox_subset/mwscript/opcodes.py``.

Licence policy: this project copies no GPL source, so the table is built from
MWEdit's ``Functions.dat`` (MIT) only. Compiler-internal opcodes -- which no
function table lists -- are re-derived from a corpus of real compiled scripts
instead, making them observations about the game's own data rather than a copy
of anyone's source. See ``tests/test_mwscript.py::TestOpcodeTable``.

Usage:
    python tools/gen_opcodes.py ../MWEdit-dev/data/Functions.dat
"""

from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "mlox_subset/mwscript/opcodes.py"

#: Opcodes the compiler emits that appear in no function table. Values derived
#: by correlating real bytecode against its own source text, not copied.
#: ``_SetReference``: emitted for ``id->Func``; 0x010C carried the
#: length-prefixed target id in 200/200 observed cases, with no rival value.
CORPUS_DERIVED: dict[int, tuple[str, tuple[int, ...]]] = {
    0x010C: ("_SetReference", (0x20,)),
}


def parse_functions_dat(path: Path) -> dict[int, tuple[str, tuple[int, ...]]]:
    """Parse MWEdit's ``Functions.dat`` into ``{opcode: (name, params)}``.

    The file is a sequence of blocks::

        Function = Activate
            Options = 0x8
            Opcode = 0x1017
            Param1 = 0x820, "player"
        End

    ``ParamN`` keys are read in numeric order; the flag word before the comma
    is the operand's encoding, and the quoted label is discarded.

    Args:
        path: Location of ``Functions.dat``.

    Returns:
        Every function block that declared both a name and an opcode.
    """
    text = path.read_text(encoding="latin-1")
    table: dict[int, tuple[str, tuple[int, ...]]] = {}
    name: str | None = None
    opcode: int | None = None
    params: dict[int, int] = {}

    def flush() -> None:
        """Commit the block just parsed, if it was complete."""
        if name and opcode is not None:
            table[opcode] = (name, tuple(params[k] for k in sorted(params)))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.lower() == "end":
            flush()
            name, opcode, params = None, None, {}
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().lower(), value.strip()
        if not value:
            continue
        try:
            if key == "function":
                flush()
                name, opcode, params = value, None, {}
            elif key == "opcode":
                opcode = int(value, 16)
            elif key.startswith("param") and key[5:].isdigit():
                params[int(key[5:])] = int(value.split(",")[0].strip(), 16)
        except ValueError:
            continue  # a malformed field should not abort the whole parse
    flush()
    return table


def main(argv: list[str]) -> int:
    """Write the generated module. Returns a process exit code."""
    if len(argv) != 2:
        print(__doc__)
        return 2
    table = parse_functions_dat(Path(argv[1]))
    if not table:
        print("no functions parsed -- is that really Functions.dat?", file=sys.stderr)
        return 1
    for opcode, entry in CORPUS_DERIVED.items():
        table.setdefault(opcode, entry)

    lines = [
        '"""Morrowind script opcodes and their operand shapes.',
        "",
        "GENERATED FILE -- do not edit by hand. See ``tools/gen_opcodes.py``.",
        "",
        "Sources:",
        "",
        "* MWEdit ``data/Functions.dat`` -- names and the parameter flag words",
        "  that give each function's operand shape. Copyright 2025 Walrus Tech,",
        "  MIT-licensed, so safe to derive from.",
        "* A corpus of real compiled scripts -- for the compiler-internal opcodes",
        "  that no function table lists. These were measured, not copied: an",
        "  opcode's value is a fact about the game's data files.",
        "",
        "Deliberately *not* used: MWSE's ``OpCodes.h``. It is GPLv2, and this",
        "project's standing policy is to copy no GPL source (see CREDITS.md).",
        "MWSE was consulted only to confirm agreement, which cost nothing: the",
        "MWSE-only functions never appeared in the corpus, and the one internal",
        "opcode that matters was re-derived independently.",
        "",
        "Note: ``else``/``endif`` have no opcodes -- the compiler emits jumps.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Final",
        "",
        "#: Opcode -> (name, parameter flag words).",
        "FUNCTIONS: Final[dict[int, tuple[str, tuple[int, ...]]]] = {",
    ]
    for opcode in sorted(table):
        name, params = table[opcode]
        args = ", ".join(hex(p) for p in params) + ("," if len(params) == 1 else "")
        lines.append(f'    0x{opcode:04X}: ("{name}", ({args})),')
    lines += [
        "}",
        "",
        "#: Opcodes the compiler emits itself; their names appear in no source",
        "#: text, so name-based filtering must never exclude them.",
        "INTERNAL: Final[frozenset[int]] = frozenset(",
        "    {" + ", ".join(f"0x{o:04X}" for o in sorted(CORPUS_DERIVED)) + "}",
        ")",
        "",
        "#: Lowercased name -> opcode.",
        "BY_NAME: Final[dict[str, int]] = {",
        "    name.lower(): opcode for opcode, (name, _params) in FUNCTIONS.items()",
        "}",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {len(table)} opcodes ({len(CORPUS_DERIVED)} corpus-derived) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
