# Changelog

## 2.1

Performance and packaged-build (`.exe`) fixes, focused on big load orders and the
Windows one-file build.

**Performance**

- **Scan caching for fast repeats.** The first Check Conflicts / Cell Map reads
  each plugin's JSON once and writes two tiny per-plugin sidecars in a single pass
  — `*.keys.json` (record ids, for conflict detection) and `*.cells.json` (cells
  touched, for the map) — so running both features reads each big JSON only once
  per run. Later scans read those few-KB files instead of re-parsing the multi-MB
  JSON, so **repeat Check Conflicts and Cell Map runs are near-instant**. Sidecars
  are mtime-invalidated per plugin; the on-click field diff still reads the full
  record, so accuracy is unchanged.
- **tes3conv JSON reused within a run.** Conversions always spool to a stable
  `tes3conv_json` folder and are reused, so Check Conflicts followed by Cell Map no
  longer re-runs tes3conv; a plugin is only re-converted if it changed. "Keep
  tes3conv JSON dump" now only controls whether that folder is kept or removed on
  exit.

**UI**

- **Custom mods flagged in the field comparison.** Your custom mods are marked
  with a ★ in the Check Conflicts field-comparison column headers, and shown in
  orange (vs grey for curated-list plugins) in the double-click field popout, so
  it's obvious which side of a conflict is yours. The popout also gained a
  **Word wrap** toggle for long values.

**Fixes**

- **Pathgrid conflicts no longer collapse into one bogus record.** Interior-cell
  pathgrids all carry grid `(0, 0)`, so (under tes3conv) every interior's pathgrid
  from every plugin was being merged into a single fake `PathGrid (0, 0)` conflict
  spanning hundreds of plugins. Pathgrids are now keyed by their cell (name for
  interiors, coords for exteriors), so only plugins editing the *same* cell's
  pathgrid are flagged. (Cached scan sidecars are versioned and rebuild
  automatically for this fix.)
- **No more tes3conv console-window popups** in the windowed / auto-py-to-exe
  build — tes3conv is launched with `CREATE_NO_WINDOW`.
- **Embedded cell-map window now appears in the exe.** A console-suppression flag
  (`SW_HIDE`) was being inherited by the pywebview child's WebView2 window, so it
  spawned hidden — looking like a hang and leaking processes, then falling back to
  the browser. The viewer launch no longer hides its window.
- pywebview is the preferred in-app cell-map viewer; detection is now a real
  import (reliable when frozen). A `cell_map_viewer.log` records the viewer's
  outcome, and `MLOX_MAP_VIEWER=pywebview|tkinterweb|browser` can force a viewer.

**Docs / packaging**

- README: PyInstaller/auto-py-to-exe steps for bundling pywebview
  (`--collect-all webview clr_loader pythonnet`, `--hidden-import clr
  webview.platforms.edgechromium`), plus the scan-caching and keep-dump behavior.

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
