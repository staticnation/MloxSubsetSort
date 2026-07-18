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
  `[Conflict]/[Requires]/[Note]` predicate logic is a port of mlox's. Our
  **Lint** checks (evil GMSTs, the interior fog-density-0 bug, missing
  pathgrids, expansion-function dependencies) are ports of the diagnostics in
  mlox's `tes3lint` and the `missing_pathgrids.pl` helper.
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

## Approach referenced (no code copied)

- **TES3 Conflictsolver Editor** — ©2026 kirgan 
  (a Mini-TES3Edit–style patch tool). No license file is
  distributed with it; **no code was copied**. We credit it for the field-level
  record-diff *approach* that inspired our field comparison view. All rights
  remain with its author.

## Referenced for formats & behavior (GPLv3 — no source copied)

We read these projects to understand file formats and expected behavior. **No
GPL source was copied into this tool**, so no copyleft obligations attach to it;
the credit is one of gratitude and correctness.

- **OpenMW** — GPLv3. The engine that makes modern Morrowind modding possible.
  Referenced for `openmw.cfg` semantics, the `.omwaddon`/`.omwscripts` Lua
  formats, and VFS (`data=`) resolution rules.
- **Mod Organizer 2** — GPLv3. Referenced for the "Data" loose-file conflict
  concept behind our data-path (VFS) resource conflict checker.

## Curated data & tooling

- **[Modding-OpenMW.com](https://modding-openmw.com/) (MOMW)** — the curated mod
  lists, the `umo` installer, the MOMW Configurator, and `plugin-order.yml` (the
  source of truth for which plugins belong to which list). This tool is designed
  specifically to *complement* MOMW lists without ever reordering them.
  Customizations are not supported by the MOMW team.
- **tes3cmd** — © Paul Halliday ("Yacoby"/community) and contributors. The
  plugin-maintenance Swiss-army knife distributed with the MOMW Tools Pack. Our
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

*MLOX Subset Sort itself is provided as-is. It bundles no third-party source in
its two Python files; the reference projects above live in their own folders with
their own licenses. Where we ported MIT-licensed logic, we retain the original
copyright and license notices.*
