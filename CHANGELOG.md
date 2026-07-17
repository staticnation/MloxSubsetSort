# Changelog

## 2.2

Sort-engine correctness, a conflict-detection fix, faster repeat scans, and UI
polish.

**Load-order engine (correctness)**

The subset sorter now places your custom mods properly instead of leaving many
of them stuck or dumped at the end:

- **Customs already in the cfg are no longer frozen in place.** The frozen chain
  is now built from the **curated list only** — custom mods already present in
  `openmw.cfg` are bridged over, so they can actually be re-sorted against the
  curated list and against each other. (Previously each custom was locked between
  its current neighbors and mlox rules couldn't move it.)
- **Header-master dependencies are honored.** Each custom plugin's TES3 header
  masters are read (from the cfg's data= folders *and* the data paths being
  added this run), and it's forced to load **after** every master it declares.
  Applied only to customs; the curated list is never touched.
- **Customs interleave — position comes from the whole graph, both directions.**
  A custom's place in the list is resolved from **all** of its graph neighbors,
  transitively:
  - *"After" anchors (preferred):* a custom lands right after the latest-loading
    non-master thing it must load after — a curated plugin (header master *or*
    mlox rule) or **another custom**, resolved through custom→custom chains. A
    patch of a patch of a custom mod follows its whole chain to the right spot.
  - *"Before" anchors:* a custom with no dependency anchor but an mlox rule
    saying it loads *before* something is placed just before its earliest such
    successor. Previously these customs kept their end-of-list position, and
    when the frozen chain reached their curated successor, the sort stalled
    there and dumped **every** pending custom in one alphabetical block — the
    "big block" bug.
  - Circular derivations are detected and skipped (a "before B" custom can't in
    turn be used to anchor B), `.esm` predecessors give no position signal
    (they'd cluster everything at the front), and truly standalone customs plus
    `.omwscripts` go to the end, where the Configurator would append them too.
- **Rule files parse correctly: plugin names with spaces no longer shatter.**
  `[Order]`/`[NearStart]`/`[NearEnd]` blocks were split on *all* whitespace
  instead of per line, so any rule mentioning a multi-word plugin name
  (`Friends & Frens - TR.ESP`, `Beautiful cities of Morrowind.ESP`, most of
  mlox_base…) dissolved into junk tokens — the rule silently didn't apply, and
  stray wildcard fragments matched plugins they were never about, creating
  bogus edges (and a bogus mid-list cluster). Rules with spaced names are now
  enforced, and edges only come from rules that really name the plugin.
- **Full parser audit against the reference implementations** (mlox-master's
  `ruleParser.py` and plox's `parser.rs`), fixing every divergence found:
  - *Rule headers only start at the beginning of a line* (both references
    require this). Previously a message line mentioning e.g. "[Order]"
    mid-sentence started a phantom rule block and corrupted block boundaries.
  - *[NearStart]/[NearEnd] are position hints, not ordering chains.* They were
    being chained like [Order], inventing edges between unrelated plugins
    (mlox_base's [NearEnd] alone linked Merged Objects.esp → Mashed Lists.esp
    → …). They now pull matching customs toward the start/end, edges permitting
    — real mlox semantics.
  - *mlox_user.txt rules now beat mlox_base.txt in conflicts*, matching mlox
    (which reads user rules first so they win). Precedence was inverted.
  - *Order-block lines are parsed like the references:* a name runs to a
    recognized plugin extension with trailing junk dropped (mlox), multiple
    extension-delimited names per line are accepted (plox), and conditional
    `[DESC …]`/`[SIZE …]` qualifier lines inside Order blocks are bridged over
    as not-installed phantoms exactly like mlox treats them.
  - *[Conflict]/[Requires]/[Note] message lines are identified by indentation*
    (mlox's actual rule) instead of by content-sniffing, which had turned
    thousands of indented mlox_base message lines that mention a plugin name
    into phantom logic operands — source of false conflict/note warnings.
    Header-line comments now also appear in the warning text.
  - *Smaller parity fixes:* UTF-8 BOM no longer hides a header on a file's
    first line; `[DESC]` predicates with brackets inside their `/regex/`
    tokenize correctly; `[SIZE]` accepts OpenMW plugin extensions.
- **ESM-first.** Master-type plugins (`.esm`/`.omwgame`) now tie-break before
  ordinary plugins, so a custom master with no rule floats up into the master
  block instead of sinking to the bottom.
- **The sort is deterministic run to run.** Rule-pattern expansion and the
  anchor resolver used to iterate Python sets, whose order is randomized per
  process — so a fresh app launch could produce a different (equally valid)
  order than the last one. All graph iteration now happens in a fixed order;
  the same inputs give the same output every time.

- **tes3cmd clean is now VFS-safe (staged).** tes3cmd only understands one
  flat "Data Files" directory, so on an OpenMW multi-folder setup it couldn't
  see a plugin's masters — cleaning without masters gives wrong results. Clean
  now stages each plugin into a private Morrowind-shaped folder (minimal
  Morrowind.ini + Data Files with the plugin's masters, hardlinked when
  possible and cached across runs) and runs tes3cmd there; the cleaned result
  is copied back only on success, with a one-time `.preclean.bak` of the
  original. Plugins whose masters can't be found are skipped outright, files
  are cleaned masters-before-dependents in load order, and a "MOMW
  needs-cleaning" button queues exactly the plugins plugin-order.yml flags.
  Verified against real tes3cmd: duplicate-of-master records removed, new
  records kept, original untouched. **multipatch was removed** — it needs the
  entire load order in one flat directory (unfakeable for a multi-GB setup),
  and OpenMW/MOMW users get merged leveled lists from delta-plugin instead.
- **Master-size resync is now done in-app, not by tes3cmd.** tes3cmd's
  `header --synchronize` assumes one flat "Data Files" directory; on an OpenMW
  multi-folder layout it can't find the masters and writes **empty sizes**
  into the plugin header (observed corrupting real plugins). The tes3cmd
  window's resync now resolves each master across ALL data folders and
  rewrites only the 8-byte size fields (one-time `.masterfix.bak` per file,
  idempotent, verified byte-exact). Headers zeroed by a bad tes3cmd sync are
  flagged by the master check and repaired by the same resync. Also, a
  manually-entered tes3cmd path now wins outright or errors — it never
  silently falls back to another copy found on the system.
- **tes3cmd frontend.** A `tes3cmd` button next to Resource Conflicts opens a
  frontend for tes3cmd (auto-detected; the compiled tes3cmd.exe from the MOMW
  Tools Pack is preferred, the pure-perl script works when perl is installed):
  clean plugins, `header --synchronize` to fix `[MASTER SIZE]` notes, view
  headers, or build multipatch.esp. "My mods (last sort)" fills the file list
  with your customs located across the data folders (including pending ones);
  output streams to the log; modifying commands confirm first and rely on
  tes3cmd's own backups. Morrowind.esm, Tribunal.esm and Bloodmoon.esm are
  **never cleaned** — even a careful GMST-preserving clean rewrites bytes
  other content depends on and causes in-game failures — the frontend skips
  them with a warning rather than trusting tes3cmd's own name check.
- **Plugins with master problems are flagged in the load-order panel.** Rows
  whose plugin has a missing or mis-ordered master render in purple (red
  already means "touched by this sort", gold means "yours" on the cell map),
  matching the MASTER CHECK section in the log.
- **Missing-master check on every sort.** Each active plugin's TES3 header
  masters (MAST/DATA subrecords) are verified against the final load order:
  `[MISSING MASTER]` (red) when a required master is absent — distinguishing
  "installed but not in the load order" from "not found in any data folder,
  the game will fail to load"; `[MASTER ORDER]` (red) when a master loads
  after its dependent; and tes3cmd-style `[MASTER SIZE]` notes (orange) when
  the installed master's size differs from what the plugin was built against.
  Custom mods are checked before the cfg is written, and warnings carry the
  mod's origin (scan / customizations.toml) so it's clear which is yours.
- **Conflict / Cell Map / Resource scans now see your custom mods BEFORE the
  cfg is written.** All three scans (and the CLI equivalents) searched only the
  data= folders already in openmw.cfg, so pending custom mods — the very thing
  being sorted — were invisible to them ("0 involve your custom mods") until
  after export. They now search the cfg's folders plus every pending custom
  data path from the scan/customizations TOML, so you can check conflicts and
  adjust the order before committing anything.

**Fixes**

- **Pathgrid conflicts no longer collapse into one bogus record.** Interior-cell
  pathgrids all carry grid `(0, 0)`, so (under tes3conv) every interior's pathgrid
  from every plugin was being merged into a single fake `PathGrid (0, 0)` conflict
  spanning hundreds of plugins. Pathgrids are now keyed by their cell (name for
  interiors, coords for exteriors), so only plugins editing the *same* cell's
  pathgrid are flagged. (Cached scan sidecars are versioned and rebuild
  automatically for this fix.)

**Performance**

- **Scan caching for fast repeats.** The first Check Conflicts / Cell Map reads
  each plugin's JSON once and writes two tiny per-plugin sidecars in a single pass
  — `*.keys.json` (record ids, for conflict detection) and `*.cells.json` (cells
  touched, for the map) — so running both features reads each big JSON only once
  per run. Later scans read those few-KB files instead of re-parsing the multi-MB
  JSON, so **repeat Check Conflicts and Cell Map runs are near-instant**. Sidecars
  are mtime-invalidated per plugin; the on-click field diff still reads the full
  record, so accuracy is unchanged.

**UI**

- **Custom mods flagged in the field comparison.** Your custom mods are marked
  with a ★ in the Check Conflicts field-comparison column headers, and shown in
  orange (vs grey for curated-list plugins) in the double-click field popout, so
  it's obvious which side of a conflict is yours. The popout also gained a
  **Word wrap** toggle for long values.
- **App icon.** A vector program icon (`art/mlox_subset_sort_icon.svg`) plus a
  multi-size `.ico` for the built exe.

## 2.1

Performance and packaged-build (`.exe`) fixes, focused on big load orders and the
Windows one-file build.

- **tes3conv JSON reused within a run.** Conversions always spool to a stable
  `tes3conv_json` folder and are reused, so Check Conflicts followed by Cell Map no
  longer re-runs tes3conv; a plugin is only re-converted if it changed. "Keep
  tes3conv JSON dump" now only controls whether that folder is kept or removed on
  exit.
- **No more tes3conv console-window popups** in the windowed / auto-py-to-exe
  build — tes3conv is launched with `CREATE_NO_WINDOW`.
- **Embedded cell-map window now appears in the exe.** A console-suppression flag
  (`SW_HIDE`) was being inherited by the pywebview child's WebView2 window, so it
  spawned hidden — looking like a hang and leaking processes, then falling back to
  the browser. The viewer launch no longer hides its window.
- pywebview is the preferred in-app cell-map viewer; detection is a real import
  (reliable when frozen). A `cell_map_viewer.log` records the viewer's outcome, and
  `MLOX_MAP_VIEWER=pywebview|tkinterweb|browser` can force a viewer.
- README: PyInstaller/auto-py-to-exe steps for bundling pywebview
  (`--collect-all webview clr_loader pythonnet`, `--hidden-import clr
  webview.platforms.edgechromium`).

## 2.0

Added the inspection tools on top of the 1.0 sorter:

- **TES3 record-level conflict detection** (Check Conflicts) — flags records that
  two or more plugins define/override (last one wins), via a built-in binary
  parser or, if a `tes3conv` binary is available, tes3conv for exact record ids
  and **field-by-field diffs**.
- **Cell map** — a modmapper-style SVG heatmap of which mods touch which
  exterior/interior cells, with tabs and click-to-jump.
- **Data-path (VFS) resource conflicts** — same loose file provided by 2+ `data=`
  folders (later wins), like MO2's "Data" conflicts.
- Supporting work: exclude patterns for noisy mods, saved settings, disk-backed
  tes3conv (bounded memory on big lists), an in-app cell-map viewer, and
  frozen-`.exe` (PyInstaller / auto-py-to-exe) support.

## 1.0

First public release: the subset sorter. Sorts only your custom mods into an
existing `openmw.cfg` using mlox rules **without** reordering the curated
Modding-OpenMW.com list, and emits a corrected `momw-customizations.toml`.
Included the mlox-ported rule engine (wildcards/`<VER>`, order transitivity,
`[Conflict]/[Requires]/[Note]` + `[VER]/[SIZE]/[DESC]`), `plugin-order.yml`
curated-vs-custom awareness, a drag-and-drop GUI + full CLI, a mods-folder
scanner, row opt-out, and cross-platform (Windows/Linux/macOS) support.
