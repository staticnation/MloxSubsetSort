# Credits & Acknowledgements

MLOX Subset Sort stands on the work of a lot of other people. This tool exists
because these projects were generous enough to share their code, formats, and
research. Huge thanks to everyone below — the good ideas are theirs; any bugs are
ours.

If you are one of these authors and want your attribution changed (or removed),
please get in touch and we'll fix it right away.

---

## Code ported or adapted from (MIT-licensed)

These projects are MIT-licensed. We ported logic and/or cross-referenced their
implementations; their copyright notices are reproduced with the relevant parts
and their `LICENSE` files are included in their source folders in this repo.

- **mlox** — © 2009–2017 John Moonsugar (alias), dragon32, Arthur Moore. MIT.
  The load-order rule engine and the rule databases (`mlox_base.txt` /
  `mlox_user.txt`) this whole tool is built around. Our matching, ordering, and
  `[Conflict]/[Requires]/[Note]` predicate logic is a port of mlox's. Several
  of our **Lint** checks (evil GMSTs, the interior fog-density-0 bug,
  expansion-function dependencies) come from mlox's `tes3lint`, credited
  separately below. (The missing-pathgrid check does *not* come from mlox —
  see the unlicensed-scripts section.)
- **tes3lint** — © 2009 John Moonsugar. MIT. Distributed as part of mlox.
  A diagnostic tool for TES3 plugins. Our native **Lint** feature reimplements
  its useful checks against plugin binaries (so they see the whole OpenMW
  multi-folder VFS, with no Perl needed). One thing is *reproduced rather than
  reimplemented*: the table of **72 "evil GMSTs"** — the exact name/value pairs
  an old Construction Set wrote when run without both expansions. Those values
  are research, not something we could rederive, and the table carries John
  Moonsugar's copyright notice inline at `_EVIL_GMSTS` in the engine.
- **[mlox-rules](https://github.com/DanaePlays/mlox-rules)** — maintained by
  DanaePlays and contributors. The **actively-updated** rule database that
  modern mlox (v1.1+) and plox both use. Our "Update Rules..." button downloads
  the current `mlox_base.txt`/`mlox_user.txt` from this repo.
- **plox** — © 2024 Moritz Baron. MIT.
  A Rust reimplementation of mlox. Used as a second reference to harden our
  engine (wildcard/`<VER>` matching, order transitivity, predicate functions).
- **tes3conv** — © 2025 Greatness7. MIT.
  Converts Morrowind plugins ↔ JSON. Used (optionally, if present on PATH) as the
  exact record-identification and field-diff engine behind Check Conflicts.
- **momw-configurator** — © Modding-OpenMW.com (johnnyhostile). MIT.
  We read its `cfg/custom.go` to reimplement its customization-apply logic
  faithfully, so the **Export preview** can simulate exactly what the
  Configurator will do to your `openmw.cfg` (matching, insert/replace/remove
  order, ambiguity errors) before it runs.
- **modmapper** — © 2023 Michiel. MIT.
  The inspiration and reference for the cell-map heatmap (which mods touch which
  exterior/interior cells).
- **Tes3EditX** — © 2023 Moritz Baron. MIT.
  Referenced for TES3 record handling and conflict-resolution UX.
- **TES3Tool** — © 2019 SaintBahamut. MIT.
  Referenced for the TES3 binary record/subrecord layout used by our built-in
  parser.
- **MWEdit** — © Dave Humphrey and contributors. MIT.
  Its `data/Functions.dat` and `mwedit/script_defs.h` are the primary source
  for our script-bytecode opcode table: the function names, their opcode
  values, and the parameter-flag words that describe each operand's encoding.
  Without it the **Bytecode** view in the diff window would be guesswork —
  and guesswork is exactly what we refused to ship.

  The compiler-internal opcodes that no function table lists (notably
  `_SetReference`, emitted for `id->Func`) were **measured from a corpus of
  real compiled scripts** rather than taken from anyone's source — an opcode's
  numeric value is a fact about the game's own data files. `tools/gen_opcodes.py`
  regenerates the table and documents each derivation.

## abot's tes3cmd scripts (idea credited, no code used)

- **`missing_pathgrids.pl`** and **`cell_conflicts.pl`** — © **abot**.
  Published as *Missing Pathgrids* and *Cell Conflicts* on abot's own site,
  ["Morrowind is Home"](https://abitoftaste.modlist.x10.mx/morrowind/index.php?option=downloads&catid=58&Itemid=50&-Morrowind-tools)
  (Downloads → Morrowind tools), alongside MMOG, MRS and abot's other tools.

abot is behind a great deal of what makes Morrowind still worth playing —
Water Life, Silt Striders, the merged-object and resource-scanning tools that
half the community's load orders depend on. These two `tes3cmd --program-file`
scripts are small by comparison and easy to overlook, so: thank you.

**The files themselves carry no copyright line and no licence text.** No
licence granted means the author keeps all rights, which makes these the most
restricted inputs in this project rather than the least. So we treated them as
*read-only inspiration*: **no line of either script is in this tool**, and both
of our implementations were written from scratch in Python against plugin
binaries. What we took is the diagnostic idea, which copyright does not cover.

- *Missing Pathgrids* — the idea: an interior cell with no `PGRD` record is a
  bug, because NPCs cannot pathfind there. Our `[NO PATHGRID]` check
  deliberately *diverges*: the original only considers plugins earlier in the
  load order and so reports false positives, whereas ours accepts a pathgrid
  contributed by **any** plugin.
- *Cell Conflicts* — the idea: "show me every mod touching the same cells as
  this one." That became the **Focus on mod** filter in our cell map (whose
  implementation follows *modmapper*, MIT).

abot — if you would rather we credit this differently, drop the mention, or not
reference your scripts at all, say the word and it is done.

## Approach referenced (no code copied)

- **TES3 Conflictsolver Editor** — ©2026 kirgan 
  (a Mini-TES3Edit–style patch tool). No license file is
  distributed with it; **no code was copied**. We credit it for the field-level
  record-diff *approach* that inspired our field comparison view. All rights
  remain with its author.

## Referenced for formats & behavior (GPL — no source copied)

We read these projects to understand file formats and expected behavior. **No
GPL source was copied into this tool**, so no copyleft obligations attach to it;
the credit is one of gratitude and correctness.

- **OpenMW** — GPLv3. The engine that makes modern Morrowind modding possible.
  Referenced for `openmw.cfg` semantics, the `.omwaddon`/`.omwscripts` Lua
  formats, and VFS (`data=`) resolution rules.
- **Mod Organizer 2** — GPLv3. Referenced for the "Data" loose-file conflict
  concept behind our data-path (VFS) resource conflict checker.
- **MWSE** — © NullCascade, Merzasphor, Greatness7 and contributors. **GPLv2.**
  We read `MWSE/OpCodes.h` to *check* our opcode table and found it agreed with
  MWEdit on all 533 opcodes they share. Because MWSE is copyleft and this tool
  is not, **none of its data was copied**: the shipped table is built from
  MWEdit (MIT) plus our own corpus measurements. The MWSE-only functions it
  additionally lists never occurred in any script we tested, so nothing of
  value was given up by leaving them out.
- **MGE XE** — GPLv3. Referenced alongside MWSE for the same cross-check; no
  source copied.

## Curated data & tooling

- **[Modding-OpenMW.com](https://modding-openmw.com/) (MOMW)** — the curated mod
  lists, the `umo` installer, the MOMW Configurator, and `plugin-order.yml` (the
  source of truth for which plugins belong to which list). This tool is designed
  specifically to *complement* MOMW lists without ever reordering them.
  Customizations are not supported by the MOMW team.
- **tes3cmd** — © 2016 John Moonsugar. MIT.
  ([github.com/john-moonsugar/tes3cmd](https://github.com/john-moonsugar/tes3cmd/))
  The plugin-maintenance Swiss-army knife distributed with the MOMW Tools Pack. Our
  **tes3cmd** window is a front-end that stages plugins with their masters so
  tes3cmd works correctly on a multi-folder OpenMW VFS; we drive the real
  binary for `clean`, and reimplement master-size resync in-app (tes3cmd's own
  sync corrupts headers on this layout). The safe-cleaning workflow (never
  cleaning the vanilla masters, cleaning masters before dependents) is adapted
  from the community "drag-and-drop" cleaning batch by RMWChaos, Pinkertonius,
  and Spirithawke.

## Runtime & optional libraries

- **Python** and **Tkinter/ttk** — the language and GUI toolkit.
- **[tkinterdnd2](https://github.com/pmgagne/tkinterdnd2)** — optional drag-and-drop.
- **[PyYAML](https://pyyaml.org/)** — optional, faster `plugin-order.yml` parsing.
- **[pywebview](https://pywebview.flowrl.com/)** — optional in-app cell-map viewer
  (OS webview).
- **[tkinterweb](https://github.com/Andereoo/TkinterWeb)** /
  **[tkhtmlview](https://github.com/bauripalash/tkhtmlview)** — optional inline
  HTML rendering fallbacks.

## And of course

- **Bethesda Game Studios** — for *The Elder Scrolls III: Morrowind*.
- The wider **OpenMW and Morrowind modding community** — for decades of tools,
  documentation, and reverse-engineering that everything here depends on.

---

*MLOX Subset Sort is provided as-is. Where we reproduce MIT-licensed material
(notably tes3lint's evil-GMST table), the original copyright and licence notice
travels with it in the source. We copy no GPL or unlicensed source: MWSE and
OpenMW were read for cross-checking only, and the unlicensed community Perl
scripts contributed ideas, not code.*

*Attribution is something we would rather over-do than get wrong. If anything
here is inaccurate — a name, a licence, a claim about what we derived from
whom — please tell us and it will be corrected.*
