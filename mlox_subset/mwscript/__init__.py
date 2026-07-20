"""Morrowind compiled-script (``SCDT``) reading and disassembly.

A ``SCPT`` record carries both the script's source text (``SCTX``) and the
bytecode Bethesda's compiler produced (``SCDT``). Only the source is normally
readable, which makes diffing two versions of a compiled script impractical --
this package makes the bytecode legible instead.

Two deliberate design choices:

* **Bytes in, structure out.** :func:`disassemble` takes raw bytes and returns
  data, never formatted text. Rendering belongs to the caller (the GUI diff
  view formats it one way, a test asserts on it another).
* **Never desync, never invent.** Morrowind stores expressions as
  semi-textual data rather than opcodes, so a table-driven walker cannot
  decode everything. Anything unrecognised is emitted as a labelled
  :class:`~mlox_subset.mwscript.disassembler.RawBytes` span rather than
  guessed at, and the walker resynchronises on the next known opcode. A
  disassembler that lies is worse than one that admits ignorance.

Example:
    >>> from mlox_subset.mwscript import disassemble, format_listing
    >>> listing = disassemble(scdt_bytes)          # doctest: +SKIP
    >>> print(format_listing(listing))             # doctest: +SKIP
"""

from __future__ import annotations

from mlox_subset.mwscript.disassembler import (
    Instruction,
    Listing,
    RawBytes,
    disassemble,
    format_listing,
)
from mlox_subset.mwscript.opcodes import BY_NAME, FUNCTIONS, INTERNAL
from mlox_subset.mwscript.script_record import ScriptRecord, read_script_records
from mlox_subset.mwscript.tes3conv import (
    BytecodeDecodeError,
    decode_bytecode_field,
    decode_variables_field,
    listing_for_bytecode_field,
    variables_text_for_field,
)

__all__ = [
    "BY_NAME",
    "FUNCTIONS",
    "INTERNAL",
    "BytecodeDecodeError",
    "Instruction",
    "Listing",
    "RawBytes",
    "ScriptRecord",
    "decode_bytecode_field",
    "decode_variables_field",
    "disassemble",
    "format_listing",
    "listing_for_bytecode_field",
    "read_script_records",
    "variables_text_for_field",
]
