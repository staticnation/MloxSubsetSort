# Changelog

## 2.3

- **Type-to-jump in every list.** Click a list (plugin order, data paths,
  rule files, tes3cmd plugins, backups) and just start typing a name:
  prefix match first, substring fallback, press one letter repeatedly to
  cycle its matches, Backspace edits, Esc clears. The panel title shows
  what you've typed; the buffer resets after a short pause.
- **Configurator dry-run preview on every TOML export.** The emitted
  customizations are applied to a simulated fresh curated cfg using a
  faithful re-implementation of momw-configurator's own apply logic
  (cfg/custom.go: substring matching, insert/replace/remove/append order,
  same-anchor stacking quirks, ambiguity aborts) and the result is verified
  against the sorted order — `VERIFIED` in green when the round trip is
  exact, a red `MISMATCH`/`PREVIEW ABORTED` with details when it isn't. What
  the Configurator will do to your cfg is now known before it runs.
- **Save Check.** Pick an `.omwsave` and every content file it depends on
  (the SAVE record's DEPE list) is verified against the sorted, enabled
  order — OpenMW refuses to load a save with missing plugins, so this warns
  before an export orphans a character.
- **Backups window.** Lists every backup this tool, tes3cmd and the
  Configurator leave behind (`.preclean.bak`, `.masterfix.bak`, `name~1.esp`,
  timestamped `.bak-*` / `.backup.*`) across the data folders, with
  restore-over-original and delete.
- **Rule maker hardening** (checked against the mlox rule guidelines). A rule
  that lists the same plugin twice is now rejected — ordering a plugin relative
  to itself is a self-cycle mlox would discard. And when a new `[Order]` rule
  contradicts the frozen curated (MOMW) order, the maker warns before writing
  that mlox will discard those orderings (it never reorders the curated list),
  so you don't get a silently-ineffective rule. Engine cycle handling was
  re-verified against the guidelines: conflicting orderings are discarded (no
  hang), user-file rules win over base rules, and the curated order is never
  broken. The comment field now hints at the `(Ref: ...)` citation convention.
- **Rule maker.** A "New Rule..." button on the rule-files panel writes mlox
  rules without knowing the syntax: pick `[Order]` / `[NearStart]` /
  `[NearEnd]`, build the plugin list by grabbing the selected rows from the
  plugin panel (their displayed order becomes the rule order), typing names
  (wildcards and `<VER>` allowed, validated with the same regex the parser
  uses — a rule that writes is a rule that loads), add an optional `;;`
  comment, watch the live preview, append. Rules go to a personal file
  (mlox_base/mlox_user are refused — "Update Rules..." would overwrite them)
  that's auto-added LAST in the rules list, so your rules win conflicts.
  This is how rules for modern mods get made; contribute good ones upstream.
- **Configurable download sources.** A "Sources..." button on the rule-files
  panel opens a dialog for pointing the two updaters at a fork or mirror if
  upstream moves: an mlox-rules URL template (must contain `{name}`, filled
  with `mlox_base.txt`/`mlox_user.txt`) and a plugin-order.yml URL. Both
  persist in settings, blank means the built-in defaults, and
  `$MLOX_RULES_URL_TEMPLATE` / `$MLOX_PLUGIN_ORDER_URL` still work as env
  overrides. Downloads are validated before anything is written regardless of
  source. (The plugin-order.yml default now points at the current upstream
  location, `.../momw/momw/data_seeds/data/plugin-order.yml`, with the GitLab
  API raw endpoint as a fallback.)
- **Tooltips stay on screen.** A tooltip on a right-edge or bottom-edge widget
  (common when the window is maximized) is now clamped to the screen — it
  slides left to fit and flips above the widget when there's no room below,
  instead of being cut off past the edge.
- **Two-row action layout.** The action buttons are split across two compact,
  left-aligned rows — primary + read-only analysis on top (with the status
  label trailing), tools below — so the growing toolset doesn't crowd into one
  long row.
- **Update plugin-order.yml button** (next to its path field). Downloads the
  current MOMW plugin-order.yml, trying the website then the site's GitLab
  repo (`$MLOX_PLUGIN_ORDER_URL` overrides for mirrors). The download must
  parse as plugin-order data with hundreds of entries before a single byte
  is written — an error page or moved URL can never clobber the file — and
  the old copy is kept as a timestamped .bak.
- **Update Rules button.** Downloads the current `mlox_base.txt` /
  `mlox_user.txt` from the actively maintained rules repo
  (github.com/DanaePlays/mlox-rules — the same source plox uses and mlox
  1.1+ auto-updates from) over the matching configured files, keeping
  timestamped backups; shows each file's age first. Personal rules files
  with other names are never touched.
- **New lint checks:** `[TWIN]` — an active `.omwaddon`/`.esp` whose
  `.omwscripts` sibling sits in the same folder but isn't in the load order
  (or vice versa), which silently disables a mod's Lua half; `[EXP-DEP]` —
  scripts calling Tribunal/Bloodmoon-only functions in a plugin that doesn't
  master the expansion (tes3lint's !TB-FUN/!BM-FUN, comment-aware).
- **Watchdogs:** `[STALE]` warns when `delta-merged.omwaddon` /
  `deleted_groundcover.omwaddon` / `S3LightFixes.esp` is older than active
  plugins (the merge no longer reflects the load order — re-run the
  Configurator); the GUI warns on Export when openmw.cfg changed on disk
  since the Sort.
- **`--lint` CLI flag** for the same checks the GUI Lint button runs.
- **Unconstrained mods keep YOUR declared order.** The subset was being
  alphabetized on input, so mods that no rule or dependency constrains landed
  at the end A→Z instead of in the order written in your subset file /
  customizations TOML (or scan order). Declaration order is now preserved
  (de-duped, not sorted). *(user feedback)*
- **Multi-line mlox expressions parse correctly.** An indented line inside a
  [Note]/[Conflict]/[Requires] body is only message text when no bracket is
  open — mlox conditions like `[ALL a.esp ⏎ [NOT b.esp] ⏎ c.esm]` continue
  across indented lines, and treating the continuations as message text
  truncated the condition (e.g. the Uvirith's Legacy "Children of Morrowind"
  note fired for people without Children of Morrowind, with the lost
  condition text leaking into the message). *(user feedback)*
- **removeContent / removeData etc. are emitted one entry per line**, matching
  the style of MOMW's own documentation examples instead of an unreadable
  single line. *(user feedback)*
- **Every emitted insert is annotated with its REAL constraint.** The
  `after=` in the generated TOML is the mod's chained position (documented
  Configurator semantics, kept deliberately — see below), but a comment above
  each insert now says *why* the sort put it there: `# constraint: must load
  after 'X'` (header master or mlox rule), `must load before 'X'`, an mlox
  NearStart/NearEnd hint, or `# no ordering constraint -- positional only`.
  The generated file reads like dependency documentation without betting the
  load order on the Configurator's undocumented same-anchor stacking
  behaviour. *(user feedback)*
- **Ambiguity warnings, verified against momw-configurator's source.** Its
  `cfg/custom.go` matches `after`/`before`/`source` values with
  `strings.Contains` against whole cfg lines and hard-errors on multiple
  matches — so a filename nested inside another (`Incantation.omwscripts`
  inside `content=Incantation.omwscripts.esp` — a real pair on a real list)
  breaks the run. Worse, `remove*` entries use the same substring match with
  NO multi-match error: every matching line is deleted **silently**
  (path-like values instead match exactly / by suffix). The emitted TOML is
  now checked both ways and collisions are flagged with the exact lines.
  Warn-only; output unchanged. Also confirmed from source while in there:
  same-anchor `before=` inserts stack in file order but same-anchor `after=`
  inserts stack in REVERSE file order — undocumented either way, which is
  why this tool keeps explicit chained anchors.
- **Cell map: "Focus on mod" filter** (the good idea in cell_conflicts.pl).
  A dropdown above the map — customs first, starred — dims every cell the
  chosen mod doesn't touch, filters both cell lists to match (combined with
  the existing text filter), and summarizes its footprint: how many
  exterior/interior cells it touches and which other mods share those cells,
  ranked by overlap. One click answers "what does this mod actually edit,
  and who else is in those cells?".
- **Lint: native tes3lint-style checks.** A Lint button runs ports of the
  worthwhile tes3lint / missing_pathgrids.pl diagnostics directly on the
  plugin binaries (VFS-aware, no perl needed): `[EVLGMST]` — the 72 evil
  GMSTs, flagged only when name AND value match tes3lint's table so
  deliberate changes aren't accused (cross-validated: tes3cmd clean removes
  exactly the ones we flag); `[FOGBUG]` — interior cells with AMBI fog
  density 0.0 (black-void bug), exact port including the behave-like-exterior
  exemption; `[NO PATHGRID]` — new interior cells with no pathgrid anywhere
  in the load order (improves on the reference script, which missed grids
  supplied by later plugins); `[HEADER]` — customs with a blank
  author/description. Vanilla masters and merged/multipatch artifacts are
  skipped, like the reference scripts do.

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
