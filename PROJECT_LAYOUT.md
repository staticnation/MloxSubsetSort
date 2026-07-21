# Project layout

Everything needed to **build, run and test** MLOX Subset Sort lives in this
folder. Reference material (the upstream projects whose formats and behaviour
this tool mirrors) and scratch output were deliberately left outside it.

```
MLOXSubsetSort/
├── mlox_subset_sort.py        Engine + CLI. No GUI import; runs headless.
├── mlox_subset_sort_gui.py    Tkinter front-end. Imports the engine.
├── mlox_subset/               Shared foundation package.
│   ├── i18n.py                gettext translation, the _() marker.
│   ├── logging_setup.py       Levelled logging (stderr) + trace file.
│   ├── gui/                   GUI support (needs Tk): theming, widgets,
│   │                          tes3cmd + conflict-window mixins, app dir.
│   │                          Fully typed and mypy-gated, like the rest.
│   ├── mwscript/              Compiled-script (SCDT) reading + disassembly.
│   │                          Makes the diff window's bytecode legible.
│   ├── rules/                 mlox rule handling: patterns, parser,
│   │                          expression front-end.
│   ├── configurator/          openmw.cfg: read, simulate, emit TOML.
│   ├── momw.py                MOMW plugin-order.yml (curated lists).
│   ├── net/                   Downloads: rule files, curated order.
│   ├── plugins/               Plugin location + header metadata.
│   ├── sort/                  Load-order sort: graph primitives + engine.
│   ├── tracing.py             Crash-survival trace logs (main + sort).
│   └── versions.py            Version regex + mlox's canonical form.
├── tools/                     Developer scripts (not shipped).
│   ├── check_placeholders.py  Verifies %(key)s placeholders match their dicts.
│   ├── check_undefined.py     Finds names a module uses but never imports.
│   ├── gen_opcodes.py         Regenerates the opcode table from MWEdit.
│   └── make_pot.py            Extracts _() strings into the .pot template.
├── tests/                     pytest suite (802 tests, no network, headless).
├── testdata/                  Copies of a real setup, used by the tests.
├── locale/                    mlox_subset_sort.pot (English template),
│                               translator guide, .mo catalogues.
├── art/                       Icons, banner, Nexus description.
├── build/                     PyInstaller / auto-py-to-exe configuration.
├── License/                   Licences of the projects this tool ports from.
├── pyproject.toml             ruff / black / pytest / mypy configuration.
├── theme_template.json        Commented starting point for a custom GUI theme.
└── *.md                       README, QUICKSTART, CHANGELOG, CREDITS,
                               SMOKE_TEST; REMAINING_WORK (what a reviewer
                               would still flag, measured); CODE_REVIEW (a
                               running log, appended per work-block, oldest
                               first); *_BRIEF.md retired stubs (work done;
                               each points at its CODE_REVIEW section and
                               can be deleted).
```

## Running

```bash
python mlox_subset_sort_gui.py          # GUI
python mlox_subset_sort.py --help       # CLI
```

Only the standard library is required. Optional extras (`tkinterdnd2`,
`PyYAML`, `pywebview`/`tkinterweb`, `tomli` on Python < 3.11) each enable one
feature and degrade gracefully when missing.

## Testing

```bash
python -m pytest                # whole suite (802 tests)
python -m ruff check .          # lint (PEP 8 incl. naming + import order)
python -m mypy                  # types (PEP 484) -- gates every shipped file
python -m black --check .       # formatting
python tools/check_undefined.py mlox_subset_sort_gui.py
python tools/check_placeholders.py   # i18n %(key)s vs dict keys
python tools/make_pot.py --check     # .pot template must be current
```

CI runs exactly this list on Python 3.10 and 3.13, plus `python -m build`
(which exercises the packaging metadata) and coverage against a `fail_under`
floor. Every shipped file is mypy-gated.

The suite is hermetic: no network (a local HTTP server stands in for
upstream), no Tkinter, no reliance on anything outside this folder. The
integration tests use `testdata/`; point them elsewhere with
`MLOX_TEST_DATA_DIR=/path/to/data`.

## Building a binary

`build/auto-py-to-exe_build.json` is an auto-py-to-exe configuration. Paths in
it are absolute and will need updating for your checkout — load it via
*Settings -> Import Config From JSON File* rather than retyping them. The
essentials:

* entry point: `mlox_subset_sort_gui.py`
* one-file, windowed (no console)
* icon: `mlox_subset_sort_icon.ico` (the copy in the project root; `art/` holds
  an identical one for reference)
* `--clean` on, so PyInstaller does not reuse a cached analysis

**You do not need to add `mlox_subset/` or `locale/` by hand.** PyInstaller
follows the import graph, so the package is collected automatically; the only
`--add-data` entry is `mlox_subset_sort.py`. `locale/` is a *developer*
directory — the `.pot` template is not a runtime file, and no `.mo` catalogues
ship yet. If you ever do ship translations, add `locale/` as data then; until
that day the app finds no catalogue directory, handles it, and runs in English.

**Verifying the build.** The Log panel's first line is a build stamp:
`MLOX Subset Sort <version> -- frozen=True built=<timestamp>`. Check it before
believing any exe-only symptom — a stale build looks exactly like a code bug,
which has cost two debugging rounds. See `SMOKE_TEST.md` §5a.

## What was left outside this folder

Kept in the parent workspace, because none of it is needed to build or run:

* **Reference sources** — `mlox-master/`, `plox-main/`, `openmw-master/`,
  `momw-configurator-master/`, `tes3conv-master/`, `TES3Tool-master/`,
  `Tes3EditX-main/`, `modmapper-main/`, `modorganizer-master/`,
  `TES3 Conflictsolver/`. Read while porting; credited in `CREDITS.md`.
* **Third-party tools** — `tes3cmd`, `tes3lint.pl`, `cell_conflicts.pl`,
  `missing_pathgrids.pl` and their `.bat` wrappers. The tool drives `tes3cmd`
  when you point it at one; the Perl scripts' useful checks were ported into
  the native Lint feature.
* **Run output** — logs, `cell_map.html`, `resource_conflicts.csv`,
  `tes3conv_json/`, `output/`, the packaged `.exe` and `.7z`.
* **Superseded** — `mod_scan.py` (folded into the engine's scanner),
  `BRIEFING_sort_engine.md` (the original problem statement).
