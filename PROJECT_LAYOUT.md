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
│   ├── mwscript/              Compiled-script (SCDT) reading + disassembly.
│   │                          Makes the diff window's bytecode legible.
│   ├── rules/                 mlox rule handling (split in progress):
│   │                          patterns, parser, expression front-end.
│   ├── configurator/          openmw.cfg: read, simulate, emit TOML.
│   ├── momw.py                MOMW plugin-order.yml (curated lists).
│   ├── net/                   Downloads: rule files, curated order.
│   ├── plugins/               Plugin location + header metadata.
│   ├── sort/                  Load-order sort: graph primitives + engine.
│   ├── tracing.py             Crash-survival trace logs (main + sort).
│   └── versions.py            Version regex + mlox's canonical form.
├── tools/                     Developer scripts (not shipped).
│   ├── check_undefined.py     Finds names a module uses but never imports.
│   └── gen_opcodes.py         Regenerates the opcode table from MWEdit.
├── tests/                     pytest suite (717 tests, no network, headless).
├── testdata/                  Copies of a real setup, used by the tests.
├── locale/                    Translation catalogues + translator guide.
├── art/                       Icons, banner, Nexus description.
├── build/                     PyInstaller / auto-py-to-exe configuration.
├── License/                   Licences of the projects this tool ports from.
├── pyproject.toml             ruff / black / pytest / mypy configuration.
└── *.md                       README, QUICKSTART, CHANGELOG, CREDITS,
                               CODE_REVIEW.
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
python -m pytest                # whole suite (717 tests)
python -m ruff check .          # lint (PEP 8 incl. naming + import order)
python -m mypy                  # types (PEP 484) -- gates mlox_subset/
python -m black --check .       # formatting
```

The suite is hermetic: no network (a local HTTP server stands in for
upstream), no Tkinter, no reliance on anything outside this folder. The
integration tests use `testdata/`; point them elsewhere with
`MLOX_TEST_DATA_DIR=/path/to/data`.

## Building a binary

`build/build subset sort.json` is an auto-py-to-exe configuration. Paths in it
are absolute and will need updating for your checkout. The essentials:

* entry point: `mlox_subset_sort_gui.py`
* one-file, windowed (no console)
* icon: `art/mlox_subset_sort_icon.ico`
* include `mlox_subset/` as a package, and `locale/` as data if you ship
  translations

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
