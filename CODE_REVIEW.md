# Code review — MLOX Subset Sort

Senior-developer review of `mlox_subset_sort.py` (engine) and
`mlox_subset_sort_gui.py` (Tkinter front-end), covering correctness,
security, PEP 8/PEP 20 conformance, testing, and performance.

**Verdict:** the codebase is in good shape. The domain logic is careful and
unusually well commented — the *why* is recorded, not just the *what*, which
is rare and valuable. Review found **four real defects** (one security-
relevant, one resource leak, one crash, one dead conditional), all fixed, and
added a **129-test pytest suite** that previously did not exist.

Tooling: `ruff` 0.15, `black` 26.5, `pytest` 9.1, `mypy` 2.3, configured in
`pyproject.toml`.

---

## 1. Defects found and fixed

### 1.1 Unvalidated download scheme (security) — `fetch_url_bytes`

The two updaters (`update_rule_files`, `update_plugin_order_yml`) passed a URL
straight to `urllib.request.urlopen`. Those URLs come from a **persisted
settings file** and from **environment variables**, so a tampered value could
make an "Update" button read an arbitrary local file (`file:///…`) and write
it over the user's rule files. There was also no size cap, so a hostile or
misconfigured endpoint could exhaust memory.

Fixed by routing both through a new `fetch_url_bytes()` that enforces an
`http`/`https` allow-list, requires a host, and caps the body at 32 MB.

```python
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
```

Covered by `tests/test_updaters.py::TestUrlSchemeAllowList` (including a test
that a `file://` template leaves the target file byte-identical).

### 1.2 Temp-file leak on every failed validation — `update_plugin_order_yml`

The downloaded YAML was written to a `NamedTemporaryFile(delete=False)` and
only unlinked on the success path. Any parse failure leaked the file, once per
attempt. Fixed with `try/finally` + `unlink(missing_ok=True)`; regression test
runs five failing downloads and asserts zero leaked files.

### 1.3 Crash on a malformed URL template — `update_rule_files`

The GUI's Sources dialog only checks that the template contains `{name}`. A
template such as `https://host/{name}/{branch}` reached `str.format()` and
raised an uncaught `KeyError`, killing the update. Now caught and reported as
a bad template. Parametrised regression test covers four malformed shapes.

### 1.4 Dead conditional — `scan_backups`

```python
out.append((p, orig if (orig and orig.exists()) else orig, kind))   # both branches identical
```

The ternary returned `orig` either way. Reviewing the consumers showed the GUI
already renders its own "original missing" marker, and restoring a backup
whose original was deleted is *valid recovery* — so always reporting the path
is the correct behaviour. Simplified and documented, with a test pinning the
intent.

### 1.5 Smaller correctness items

| Item | Fix |
| --- | --- |
| `trace_sort` declared `global _SORT_TRACE_FH` but never assigned it | Removed the misleading declaration |
| Implicit `Optional` in 4 signatures (PEP 484 violation) | `Optional[...]` + `typing` import |
| `raise SystemExit(...)` inside `except` lost the cause | `raise ... from exc` |
| `subprocess.run` without explicit `check` | `check=False` + comment (caller inspects `returncode`) |
| Unused `io` import, unused `text` variable | Removed |

---

## 2. PEP 8 / PEP 20 conformance

- **E741 (ambiguous name `l`)** — 20 occurrences, all meaning "line". Renamed
  to `line`/`name`. Because this was a mechanical rename across parser and
  emitter internals, it was **verified behaviour-neutral by differential
  testing**: the pre-rename and post-rename modules were loaded side by side
  and produced identical output for the real 975-plugin load order,
  `simulate_configurator_apply`, `find_anchor_index`, `parse_mlox_file`
  (1,544 blocks) and `preview_configurator_result`.
- **E702 (semicolon-joined statements)** — 6 occurrences, expanded.
- **W291/W293 (trailing whitespace)** — cleared repo-wide.
- Dead imports and unused locals removed.

*PEP 20 note:* the code already follows "explicit is better than implicit" in
the places that matter — the Configurator simulation documents each upstream
quirk it deliberately mirrors, which is exactly the "special cases aren't
special enough to break the rules… although practicality beats purity"
balance this domain needs.

---

## 3. Linter findings deliberately **not** applied

A linter is an advisor, not an authority. Each of these was inspected and
rejected with a reason, recorded in `pyproject.toml` so the decision survives:

| Rule | Why it is wrong here |
| --- | --- |
| `B905` (`zip(strict=)`) | **Would break the documented Python 3.8+ target** (`strict=` is 3.10+). Every call site is either an intentional offset pairing `zip(xs, xs[1:])` — where `strict=True` would raise *always* — or a comparison that already reports length mismatch with a better message than an exception. |
| `SIM115` (context manager) | The trace-log handles are deliberately long-lived; reopening per line made the sort crawl when logging thousands of steps. They flush per write and are closed explicitly. One flagged site already *does* use `with fh:`. |
| `PLC0415` (import outside top level) | Optional dependencies (`tomli`, `yaml`, viewer backends) are imported lazily so a missing optional dep degrades one feature instead of preventing startup. |
| `S110`/`S112`/`SIM105` (silent except) | Confined to cosmetic paths (a tooltip that cannot render, a trace line that cannot be written). Failing loudly there would take down a sort for a decorative reason. |
| `S603` (subprocess) | Verified safe: no `shell=True` anywhere, argument lists only, executables chosen by the user. |
| `S105` ("hardcoded password") | False positive — `token == "["` in the mlox expression tokenizer. |
| `PLR09xx` (complexity) | The sort and emit functions are long because the domain is sequential; splitting them to satisfy a counter would hurt readability. |

Remaining ~85 findings are pure style preferences (`PTH123` `open()` vs
`Path.open()`, `PERF401` comprehension rewrites, `UP031` `%`-formatting).
They are harmless and were left alone deliberately: churning ~9,000 lines of
working, well-commented code for style points is a poor risk/benefit trade,
and would bury the substantive fixes above in an unreviewable diff.

**On `black`:** deliberately *not* run repo-wide. It would reformat nearly
every line of both files, destroying the reviewability of this change set and
the careful manual alignment in the comment blocks. `pyproject.toml` pins the
config so it can be applied later as its own isolated commit if wanted.

---

## 4. Test suite (new)

129 tests, `pytest tests/`, no network and no GUI required.

| File | Focus |
| --- | --- |
| `conftest.py` | Loads the engine by path; builds real TES3 binaries in-memory (headers, cells, pathgrids, scripts, GMSTs) so fixtures are explicit and no binaries are committed |
| `test_rule_parser.py` | mlox rule parsing: multi-word names, comments, wildcards, BOM, block delimiting, NearStart/NearEnd separation, priority, bracket-continuation predicates |
| `test_sort.py` | The core promise: curated order frozen, anchoring, declaration order, cycle termination, user-rule precedence, determinism, near-hints |
| `test_configurator.py` | Fidelity to upstream Go: substring anchors, fatal ambiguity, **silent** multi-removal, append routing, asymmetric same-anchor stacking, round-trip verification |
| `test_updaters.py` | Security allow-list, size caps, validate-before-write, backups, temp-file leak, malformed templates |
| `test_plugins.py` | Master check, byte-exact resync, lint checks (evil GMSTs, fog bug, pathgrids, expansion deps, twins), savegame deps, backup scanner |
| `test_rule_maker.py` | Rule authoring validation, self-cycle rejection, frozen-order conflict pre-check tied to real engine behaviour |
| `test_integration.py` | Pure helpers + the **real** `openmw.cfg` and rule files: 975 plugins, order preserved, deterministic (skips cleanly if absent) |

Tests assert *behaviour and intent*, not implementation details, and each
regression test names the bug it pins.

---

## 5. Performance and scalability

Measured on the real 975-plugin, 4,437-rule-block load order:

- Full sort: **~1 s**. The graph build is `O(V+E)`; the earlier
  quadratic-ish anchor resolution is memoised with a bounded settle loop.
- Rule parsing: ~10 k plugin references across two files, parsed once per run.
- Conflict/cell-map scans stream plugin JSON **to disk**, one plugin at a
  time, with per-plugin caching — bounded memory on large installs rather
  than holding every record in RAM.
- Downloads are now bounded (32 MB) instead of unbounded.

No scalability problems found. The one hot path worth knowing about is
`expand_pattern` over wildcard rules, already mitigated by an `lru_cache` on
the compiled regex.

---

## 6. Hardening pass (second round)

Adversarial probing of every path that consumes files the tool did not create
found **three more defects**, all fixed with regression tests.

### 6.1 `openmw.cfg` round-trip destroyed non-UTF-8 bytes (data loss)

The cfg was read with `errors="replace"` and written back as UTF-8. Any byte
that is not valid UTF-8 -- a cp1252 accented mod folder such as
`E:\Mods\Café\` -- was permanently replaced with U+FFFD, **rewriting the
user's `data=` path and breaking their load order**. Worse, `backup_file()`
decoded and re-encoded too, so it raised `UnicodeDecodeError` and blocked
export entirely rather than protecting anything.

Fixed by reading and writing with `errors="surrogateescape"` (byte-preserving
round-trip) and making the backup a straight **byte copy** -- a backup must be
byte-identical by definition. The same crash was fixed in the subset-file
reader; TOML, which the spec requires to be UTF-8, now reports an actionable
message instead of a raw traceback.

### 6.2 A TOML typo wiped the entire load order in the preview

`removeContent = 'X.esp'` (a string where an array was meant -- an easy typo)
was iterated **character by character**, so every character became a removal
pattern and matched nearly every line. The preview silently reported an empty
cfg with **zero errors**. Fixed with a shared, type-checked accessor
(`customization_string_list`) used by both call sites; wrong types are now
rejected loudly, matching the real Go tool, which cannot unmarshal a string
into `[]string` either.

### 6.3 `sync_plugin_master_sizes` crashed on a 4-byte file

A file containing exactly `b"TES3"` passed the magic check and then unpacked
past the end of the buffer (`struct.error`). Since this function *writes to
the user's plugins*, it now requires a full 16-byte record header first.

### What was probed and found clean

Confirmed robust, and pinned by 113 new tests: all binary readers against 13
malformed byte streams (truncated, oversized declared sizes, zero-length
subrecords, non-UTF-8 names); the sort engine against degenerate inputs
(empty, self-mastering, missing masters, duplicate cfg lines) and a
600-deep transitive chain; `expand_pattern` against plugin names containing
regex metacharacters; the TOML emitter against names with quotes, backslashes
and unicode; and the simulator against nine malformed documents. Scale is
comfortable: 6,000 plugins sort in 0.28 s.

---

## 7. Foundation package (`mlox_subset/`)

New shared package, held to a stricter standard than the legacy scripts and
passing `ruff --select ALL`: full type annotations, PEP 257 Google-style
docstrings with Args/Returns/Raises, and no silent excepts.

* **`logging_setup.py`** -- levelled logging with a documented policy. The key
  decision: **stdout stays the report** (`print` is the product a user pipes
  or pastes into a bug report) while **logging carries diagnostics** to
  stderr, off by default, `-v`/`-vv` to raise it, and always full-detail to
  the trace file. `add_log_handler` lets the GUI mirror records into its log
  pane without losing console or file output.
* **`i18n.py`** -- gettext-based l10n. `_()` marks strings for both runtime
  lookup and `xgettext` extraction; `ngettext` handles counted messages so
  each language applies its own plural rules. Language comes from `$MLOX_LANG`
  or the system locale, and every lookup falls back to English, so marking
  strings is always safe even with no catalogue present.
* **`locale/README.md`** -- the extract/translate/compile workflow and notes
  for translators (named placeholders are reorderable; plugin names and mlox
  keywords are data, not prose).

23 tests cover level filtering, handler de-duplication, file capture, language
detection and fallback.

---

## 8. Roadmap for the remaining requests

These are deliberately staged rather than attempted in one pass. The engine is
~5,100 lines and the GUI ~4,100; a single sweeping rewrite would produce an
unreviewable diff over a working tool, and the value is in doing it
incrementally behind the test suite that now exists.

**Status: the module split is COMPLETE.** `rules/`, `sort/`, `plugins/`,
`configurator/`, `momw`, `net/`, plus `versions` + `tracing` at the
foundation. Engine: 5,263 -> 3,322 lines. `patterns.py`, `parser.py` and
`expressions.py` have moved, guarded by `tests/test_differential.py`. The
predicate *evaluator* has not, for a reason recorded in §11.

1. **Module split.** `mlox_subset/` is the target package and its foundation
   is in place. Suggested order, each move verified by the existing tests:
   `rules/` (parser, matching) → `sort/` (graph, anchors) → `configurator/`
   (simulate, emit) → `plugins/` (TES3 binary readers, lint) → `net/`
   (updaters). The GUI splits along its natural seams: theming, the conflict
   windows, the tes3cmd front-end.
2. **Full typing + PEP 257 across the legacy scripts.** Apply per module as it
   moves, not as a separate sweep -- the annotations are most valuable, and
   most reviewable, at the moment the code is being relocated anyway.
3. **`print` → logging migration.** Mechanical once the modules move; keep the
   user-facing report on stdout and demote genuine diagnostics.
4. **String extraction for i18n.** Wrap user-facing strings in `_()` module by
   module, then generate the `.pot`.
5. **Coverage measurement.** Add `pytest-cov` and set a floor once the split
   settles; measuring against the current monolith would mostly report on
   GUI code that the headless suite intentionally does not touch.

## 9. Recommendations (not done here)

1. **`RUF012`** — 8 mutable class attributes in the GUI would be clearer as
   `ClassVar[...]`. Low risk, cosmetic.
2. **Split the GUI module.** At ~4,100 lines it is the one genuine structural
   smell; the theme system, the conflict windows, and the tes3cmd front-end
   are natural separate modules. Worth doing when it next needs real work —
   not as a drive-by.
3. **CI.** `ruff check .` + `pytest` in a GitHub Action would keep this state
   from regressing.
4. ~~**`mypy`** is configured but advisory; version 2.3.0 crashes on this
   codebase.~~ **Resolved** -- see §14. mypy 2.3.0 no longer crashes, the
   package is clean, and it now gates rather than advises.

---

## 10. Licence audit: the opcode table (found during the disassembler work)

**Defect: GPL-derived data in a non-copyleft project.**

The first version of `mlox_subset/mwscript/opcodes.py` was generated by merging
two sources — MWEdit's `Functions.dat` and MWSE's `MWSE/OpCodes.h` — and its
header, and `CREDITS.md`, both described MWSE as MIT-licensed.

**MWSE is GPLv2.** Reading `License/MWSE/LICENSE` rather than trusting the
assumption is what surfaced this. That directly contradicted the project's own
stated policy, recorded in `CREDITS.md`: *"No GPL source was copied into this
tool, so no copyleft obligations attach to it."* Shipping the merged table
would have made that sentence false, and arguably obliged the whole tool to be
GPLv2.

**Fix.** The table is regenerated from MWEdit (MIT) alone by the new
`tools/gen_opcodes.py`. The only entry MWEdit does not cover — `_SetReference`,
which the compiler emits for `id->Func` — was **re-derived by measurement**
rather than copied: correlating each script's bytecode against its own source
text produced `0x010C` in 200 of 200 cases with no competing candidate. An
opcode's numeric value observed in one's own data files is a fact, not an
expression of someone else's authorship.

**Cost of the fix: nothing measurable.** The table shrank from 938 opcodes to
564, and the decode ratio across the real corpus was *identical* before and
after (min 6%, median 40%, max 54%, zero false positives). The 377 MWSE-only
functions never occurred in any script tested — they are MWSE-mod-only calls.
`License/MWSE/` was removed, since nothing is derived from it, and `CREDITS.md`
now lists MWSE under *referenced, no source copied* alongside OpenMW and MO2.

**The generalisable lesson:** a dependency's licence is a fact to be read from
its `LICENSE` file, not inferred from the company it keeps. Every MIT claim in
`CREDITS.md` was re-checked against the corresponding licence text after this.

### Disassembler status

* 564-opcode table, generated and regenerable (`tools/gen_opcodes.py`).
* 305 tests passing; `ruff` and `black` clean across `mlox_subset/`, `tests/`
  and `tools/`.
* Zero false positives on the real corpus when `source_text` is supplied; the
  source-hint filter deliberately exempts compiler-internal opcodes, whose
  names appear in no source text (regression-tested).
* Undecodable spans are reported verbatim as `RawBytes` rather than guessed at.
  The ~40% median decode ratio is not a defect: Bethesda's compiler stores
  expressions as semi-textual data, so the remainder is genuinely not opcodes.

**Not yet done:** wiring `format_listing()` into the GUI diff window
(`_show_field_detail`).

### 10b. Licence audit, second pass: the ported Perl scripts

Prompted by the MWSE finding, the same "read the header, don't assume" pass was
run over the Perl tools the Lint feature came from. It found two more errors.

**Misattribution: `tes3cmd`.** `CREDITS.md` credited it to *Paul Halliday
("Yacoby") and contributors*. The file's own header says
**Copyright 2016 by John Moonsugar, MIT** — same author as mlox and tes3lint.
Corrected, with the upstream repository linked.

**Unnoticed MIT obligation: the evil-GMST table.** All 72 name/value pairs in
`_EVIL_GMSTS` are reproduced from `tes3lint.pl` (© 2009 John Moonsugar, MIT).
These are genuinely copied data, not a reimplementation — they record exactly
what a buggy Construction Set wrote, which cannot be rederived from first
principles. MIT requires the notice travel with the copy, so it now sits inline
above the table, and `tes3lint` has its own `CREDITS.md` entry rather than
being folded into mlox's.

**No licence at all: `missing_pathgrids.pl`, `cell_conflicts.pl.`** Neither
file carries a copyright line, an author, or a licence. *No licence granted*
is not the same as permissive — the default is that the author retains all
rights, which makes these the most restricted inputs in the project, not the
least. Both were treated as read-only inspiration: our implementations are
independent Python working on plugin binaries, and the missing-pathgrid check
deliberately diverges from the Perl by fixing its false positives. Only the
diagnostic *idea* was taken, which copyright does not reach.

Authorship was then **confirmed rather than assumed**: both are published as
*Missing Pathgrids* and *Cell Conflicts* on abot's own site (Downloads →
Morrowind tools), alongside abot's other utilities. `CREDITS.md` cites that
page, so the attribution rests on a source a reader can check instead of on
anyone's recollection.

`CREDITS.md` also wrongly implied `missing_pathgrids.pl` was part of mlox. It
is not; that sentence was rewritten.

**The pattern across both passes:** every attribution error here ran in the
*permissive* direction — GPLv2 recorded as MIT, an unrelated author credited,
an MIT notice requirement missed, unlicensed scripts filed under a licensed
project. Attribution guesses are not randomly wrong; they drift toward
whatever is convenient. Each of the four was found by opening the file and
reading its header, which took minutes.


---

## 11. The module split: what moved, and one thing that deliberately did not

### The guard came first

`tests/test_differential.py` pins 23 observations of the engine's behaviour on
real inputs to hashes in `tests/baselines/`. It was generated from known-good
code *before* any code moved, which is the only order in which such a guard
means anything.

Three properties were checked rather than assumed, because a guard nobody has
watched fail is just a test that passes:

* **It fails when it should.** Every observation was negative-controlled:
  corrupt the stored hash, confirm that specific test fails, restore.
* **It is not vacuous.** `check_predicates` fires only 6 warnings against the
  real load order, so pinning warnings alone would exercise almost none of the
  evaluator. The corpus observation therefore pushes all 2,964 real predicate
  bodies (24,739 tokens) through tokenise -> parse -> evaluate, of which 913
  evaluate true -- a genuine mix, not a uniform result that would hash stably
  while catching nothing.
* **It is deterministic.** `evaluate_node` takes a `set`, so the whole pipeline
  was run under three `PYTHONHASHSEED` values in separate processes. Stable
  throughout. Without this, set-iteration order would produce baseline failures
  indistinguishable from real regressions.

Each pipeline stage is pinned separately. Sharing one hash across tokens, AST
and evaluation would let a tokeniser change hide behind an evaluator change
that cancels it out.

### What the guard caught

* **A real regression.** `_RE_ORDER_NAME` was private to the engine but also
  used by the user-rule maker's validation; moving it broke rule creation with
  a `NameError`. It is now public as `ORDER_NAME_RE`, since "what a valid
  plugin name looks like" is part of the rules API, not a parser internal.
* **A rewrite that needed proving.** Ruff flagged a `%`-format inside the regex
  builder. Backslash-heavy regex construction is exactly where a cosmetic edit
  silently changes a pattern; the guard confirmed the compiled regexes still
  hashed identically.
* **A narrowed exception, caught in review.** While retyping `load_rule_blocks`
  its `except Exception` became `except OSError`. That reads as an improvement
  and satisfies the no-broad-except rule, but rule files are untrusted
  community downloads -- a decode or regex error would then propagate and kill
  a sort the remaining files could have completed. Reverted, with the reasoning
  written down so it is not "fixed" again later.

### Why the evaluator stayed put

A dependency analysis of the twelve predicate functions found only three
coupled to the plugin layer -- `_eval_ver` and `_eval_desc` (which read version
and description data) and `check_predicates` (which builds a
`PluginFileIndex`). The rest are pure.

That would suggest moving nine of twelve. It does not work, because the three
sit on the dispatch path: `evaluate_node` -> `_eval_func_token` ->
`_eval_ver`/`_eval_desc`. Moving the evaluator into `rules/` now would either
create a circular import (`rules` -> engine -> `rules`) or force a signature
change threaded through the GUI, to no present benefit.

So `rules/` currently holds the parts with genuinely no plugin dependency:
pattern translation, rule-file parsing, and the expression front-end (tokenise,
parse, describe, raw-text loading). The evaluator moves when `plugins/` exists
and can be depended on in the right direction.

Deferring it is the point. A split that forces a circular import has not
decoupled anything -- it has just moved the coupling somewhere harder to see.

### Linter findings deliberately not applied (additions to §3)

* **`S105`** ("hardcoded password") on `token == "["` in `parse_mlox_lisp`.
  The variable is a parser token; the rule matches on the name alone.
* **`PERF203`** (try/except inside a loop) in `load_rules_raw_text`. Per-file
  isolation is the entire purpose -- hoisting the `try` out of the loop would
  let one unreadable file discard every file after it.


### `sort/` and the foundation move

`build_and_sort` had exactly one engine-level dependency: `trace_sort`. Tracing
is foundation-level, so it moved to `mlox_subset/tracing.py` first. That gave
`sort/` a dependency pointing the right way (`sort` -> foundation) instead of
back into the engine -- the same reasoning that kept the predicate evaluator
where it is.

`sort/` now holds `graph.py` (pattern expansion, cycle detection, master-file
recognition) and `engine.py` (`build_and_sort`).

**The 370-line body was relocated verbatim.** Retyping it in the same step
would have made a behaviour change indistinguishable from a relocation error,
and this is the function whose output *is* the product. The move was verified
green on its own; only then were the two `RUF007` findings (`zip(xs, xs[1:])`
-> `itertools.pairwise`) applied and re-verified. Typing and docstrings for
this module are deliberately still outstanding.

That ordering is the point. `sort.curated_order_untouched` and
`sort.is_stable` are pinned against a real 687-plugin order, so "the curated
list came back byte-identical" is checked rather than hoped for -- but only if
each change is isolated enough for a failure to name its cause.

### Engine size through the split

| Stage | Lines |
|---|---|
| Before the split | 5,263 |
| After `rules/` (patterns, parser, expressions) | 5,075 |
| After `tracing.py` | 5,012 |
| After `sort/` | 4,629 |
| After `versions.py` | 4,614 |
| After `plugins/metadata.py` | 4,553 |
| After `rules/predicates.py` | 4,312 |
| After `momw.py` | 4,212 |
| After `net/` | 4,063 |
| After `configurator/` | 3,322 |


### The evaluator finally moved -- and why the order mattered

§11 recorded that the predicate evaluator could not move into `rules/` while
the plugin layer lived in the engine: `_eval_ver`, `_eval_desc` and
`check_predicates` sit on the dispatch path, so extracting them would have
imported the engine back into `rules/`.

Extracting `plugins/` removed that obstacle, and a fresh dependency scan
confirmed it -- the same three functions that were `PLUGIN-COUPLED` before now
report no engine dependencies at all, because what they depend on is a package.
All 238 lines then moved verbatim.

**One shared primitive had to move first.** The version regexes used by plugin
metadata are built from `MLOX_VERSION_PATTERN`, which lived in
`rules/patterns.py`. Importing it into `plugins/` would have created
`plugins -> rules`, and the evaluator move then adds `rules -> plugins`: a
package cycle, arrived at one reasonable-looking import at a time. So
`MLOX_VERSION_PATTERN` and `format_version` moved to `mlox_subset/versions.py`
at the foundation, where both packages can depend on them and neither depends
on the other.

The dependency graph is now acyclic by construction:

```
versions, tracing          (foundation: no internal dependencies)
    ^          ^
plugins ------ |           (plugins -> versions)
    ^          |
rules ---------+           (rules -> plugins, versions)
    ^
sort                       (sort -> rules, tracing)
    ^
engine                     (re-exports everything for the GUI and CLI)
```

Verified rather than asserted: each package is imported alone in a fresh
interpreter, so a cycle would surface as an `ImportError` rather than being
masked by whatever the engine happened to import first.

### A static check beat iterative failure

The relocated `check_predicates` referenced `strip_comment`, which was not in
the import list -- the same class of miss as `_RE_ORDER_NAME` earlier. Rather
than rerun the suite and fix one `NameError` at a time, an AST pass listed
every name loaded in the new module but neither imported nor defined there. It
reported exactly one, which was then the only fix needed.

Worth preferring in general: a test failure tells you the first thing that
broke, a static scan tells you all of them.


### `momw` and `net/`

`plugin-order.yml` parsing became `mlox_subset/momw.py`: it is the curated-list
source of truth, and a plugin misread as absent from a list would be treated as
one of the user's own and become eligible for reordering -- the exact failure
the tool exists to prevent. Pinned by five new observations covering the parsed
entries, the per-list curated orders and the needs-cleaning set.

`net/` then followed, since the updater depends on that parser. Its guard came
first: `fetch_url_bytes` validates URLs that are *user-configurable* (settings
file, environment variable), so its rejection paths are now pinned against ten
hostile inputs -- `file:///etc/passwd`, `file://C:/Windows/win.ini`, `data:`,
`javascript:`, `ftp:`, `gopher:`, and schemes with no host. All ten are
refused; the observation records how each one fails, so a refactor that let one
through fails the suite rather than shipping a local-file read.

### Two runtime errors that a static check would have caught first

Relocating code twice produced a `NameError` only at test time: `strip_comment`
in `rules/predicates.py`, `datetime` in `net/updaters.py`. Both are the same
mistake -- a name used by moved code but left behind in the import list.

`tools/check_undefined.py` now reports every name a module loads but neither
imports nor defines. Run across all 23 package modules it found exactly one
issue. A test run reports the *first* missing name; this reports *all* of them.

**It is not a replacement for the linter, and the same batch proved why.**
After renaming a loop variable, ruff caught an `F821` on a stale reference in
an `except` branch that `check_undefined.py` passed -- because the checker
deliberately over-collects names bound anywhere in a function scope, trading
some real misses for zero false positives. The two tools fail in different
directions, which is the argument for running both rather than picking one.


## 12. `configurator/` -- and enforcing the PEP requirements

### The guard, again first

`configurator/` rewrites `openmw.cfg` -- the file OpenMW actually loads -- so
it got the largest guard before it moved. Eight new observations cover line
value extraction, path normalisation and quoting, TOML value escaping, the
``remove*`` string-vs-array check, and a full simulated apply of the real
customisations TOML against the real cfg.

**The pins capture content, not success.** Both defects previously found in
this area were silent data loss rather than crashes: a ``removeContent``
written as a string was iterated character by character and wiped most of the
cfg, and a non-UTF-8 ``data=`` path was destroyed on rewrite. Neither would
fail a test that only asked "did it run".

The 718 lines then split into four modules by concern -- `cfglines`,
`datapaths`, `apply`, `emit` -- and `list_plugins_in_dir` moved to `plugins/`,
where a plugin-directory scan belongs.

`tools/check_undefined.py` found seven missing names in one pass. Two of them
(`REMOVE_KEYS`, `list_plugins_in_dir`) needed a *home*, not an import, which
is the kind of thing a one-failure-at-a-time test loop obscures.

### PEP compliance is now machine-enforced, and honest about the gap

`D` (pydocstyle/PEP 257) and `ANN` (PEP 484) are enabled for `mlox_subset/`.
That turned an unmeasured aspiration into a number: **143 findings, all of
them in modules relocated verbatim.** The 19 modules written fresh were
already clean and are now protected from regressing.

Those 8 modules are listed individually in `per-file-ignores` with the reason
recorded inline, rather than hidden behind a blanket exemption. The comment
says the list is meant to shrink to nothing, and each entry names a real file
so the debt is countable.

**Why not simply annotate them now?** Because the same argument that governed
the moves governs this: retyping 143 signatures in the same breath as
relocating them would make a behaviour change indistinguishable from a
relocation error, in the code that decides load order and rewrites the user's
config. Every one of those modules is pinned by the differential baseline, so
the typing pass can be done afterwards and *proven* neutral. Deferring it is
the reason it will be safe.

Recorded as an open task, not as finished work.


### Where PEP compliance actually stands

Measured, not asserted:

| Scope | ruff (full ruleset incl. D + ANN) | black |
|---|---|---|
| `mlox_subset/` (28 modules) | **clean** | clean |
| -- of which fully typed + documented | **28 / 28** | -- |
| `tests/` (11 modules) | **clean** | clean |
| `tools/` (2 scripts) | **clean** | clean |
| `mlox_subset_sort.py` (legacy) | **clean** | clean |
| `mlox_subset_sort_gui.py` (legacy) | **clean** | clean |

The 55 remaining findings are pre-existing debt in the two legacy scripts, and
they are *shrinking as a side effect of the split* -- 89 before it started, 55
now, because the code that moved was brought up to standard on the way out.
They are concentrated in `PTH*` (``os.path`` -> ``pathlib``), `PERF203`
(try/except in a loop, often deliberate per-item isolation) and `RUF012`
(mutable class attributes in the GUI, mostly colour dictionaries).

None of them are new, and none were introduced by this work.

**Two things are deliberately not claimed as done:**

1. The 8 relocated modules still lack docstrings and annotations, listed
   individually in `per-file-ignores` (task #39).
2. The legacy scripts carry the 55 findings above. Fixing them is worth doing
   when each area next needs real work, not as a drive-by sweep across a
   working tool -- the same reasoning as §9.2.


### The typing pass -- complete

All eight relocated modules are now fully typed and PEP 257 documented, and
**every `D`/`ANN` entry has been deleted from `per-file-ignores`** -- the list
the earlier comment said should shrink to nothing did. Package compliance:
19/28 -> **28/28**. The exemption block is gone rather than merely empty.

Each was done as its own step, with the differential guard run in between.
That immediately paid twice:

* **A mangled rewrite.** Retyping `toml_value` corrupted its triple-quote
  handling and left three references to a renamed parameter. The suite went to
  14 failures instantly. Had this been bundled into a larger sweep, the cause
  would have been one of dozens of edits rather than the obvious last one.
* **A wrong return annotation, caught by asking the code.** `insert_data_paths`
  documents "a list of (line_text, is_new, source_value)". I first annotated it
  `tuple[list[str], list[str]]` from a glance at the name. Calling it once
  showed a `list` of 3-tuples. The annotation now matches reality rather than
  my assumption -- and a wrong type hint is worse than none, because it is
  believed.

One module at a time, with the differential guard run between each. The two
hardest were left for last on purpose -- `sort/engine.py`, whose output *is*
the product, and `rules/predicates.py`, the mlox predicate evaluator.

Some of what the annotations forced into the open:

* **`simulate_configurator_apply` returns `tuple[list[str] | None, ...]`,** and
  that `None` is load-bearing: it means the Configurator run would *abort*,
  mirroring the Go code returning a nil cfg on an ambiguous insert anchor.
  Untyped, a caller could treat the first element as always-a-list and silently
  apply nothing.
* **`build_and_sort`'s `anchor_out` is mutated in place**, which the signature
  never said. It now does, and the docstring states the frozen-curated-order
  guarantee as a contract rather than leaving it as folklore.
* **`_eval_ver` treats an unknowable version as satisfying `=`.** That looks
  like a bug until you know it is mlox's behaviour and deliberate -- the tool
  refuses to raise a version warning it cannot substantiate. Now written down
  where someone "fixing" it will see it.
* **Three `r"""` corrections.** Docstrings containing Windows paths were being
  parsed for escape sequences. Harmless today; a future `\U` or `\N` in an
  example would be a syntax error or a silently mangled path.


## 13. The legacy scripts: 55 findings, and the two that were refused

`ruff check .` is now clean across the entire project. Most of the 55 were
mechanical -- `open()` -> `Path.open()`, `for`/`append` -> `extend`,
`zip(xs, xs[1:])` -> `pairwise`, `RUF012` colour dictionaries -> `ClassVar`.
Every engine change was verified by the 374-test suite and the differential
baseline.

### `os.path.abspath` must not become `Path.resolve()`

`PTH100` fires seven times and is **wrong every time**, which was worth
checking rather than assuming:

```
os.path.abspath('/tmp/pthtest/link')  ->  /tmp/pthtest/link
Path('/tmp/pthtest/link').resolve()   ->  /tmp/pthtest/real
```

`abspath` normalises without resolving symlinks; `resolve()` follows them.
That distinction is not academic for this tool: Morrowind setups are full of
MO2 junctions and symlinked mod folders, and one of these calls puts the
script's own directory on `sys.path`. "Fixing" it would have changed every
displayed mod path and, in the GUI's case, broken the engine import when run
through a symlink. Refused, with the reasoning recorded at each site.

Likewise `PTH118` on `os.path.join` inside an `os.path.relpath` call:
`Path.relative_to` raises on a non-subpath where `relpath` copes, so the join
stays `os.path` rather than mixing idioms mid-expression.

`PERF203` and `S603` in the GUI are documented per-file exemptions. Per-widget
`try`/`except` is not a performance mistake in Tk -- a `TclError` on one
destroyed widget must not blank the whole panel -- and every `subprocess` call
builds its own argument list from `sys.executable` and paths this code
constructed.

### `BLE001`: enabled precisely because most of its findings are refusals

Turning on `flake8-blind-except` flagged 68 `except Exception` sites. The easy
readings are both wrong: they are not 68 bugs, and they are not 68 false
positives. Each was read individually and landed in one of two buckets.

**28 were narrowed**, because the raise-set was provable rather than guessed:

| Was | Now | Why it's provable |
|---|---|---|
| `_toml.loads` | `ValueError` | `TOMLDecodeError` subclasses it in *both* `tomllib` and `tomli`, so this catches either without importing whichever one won |
| `fetch_url_bytes` | `(OSError, ValueError)` | its own docstring's documented contract; `URLError`, socket timeouts and `ssl` errors all subclass `OSError` |
| `json.load` + `open` | `(OSError, ValueError)` | `JSONDecodeError` and `UnicodeDecodeError` are both `ValueError` |
| `subprocess.run(check=True)` | `(OSError, SubprocessError)` | covers `CalledProcessError` *and* `TimeoutExpired` |
| `after_cancel`, `listbox.insert` | `tk.TclError` | the only thing Tk raises for a stale id or a destroyed widget |

**40 stayed broad**, each with `# noqa: BLE001` and a one-line reason. They fall
into four honest patterns, none of which narrowing would improve:

- **Untrusted input.** Rule files and `plugin-order.yml` are community
  downloads. Narrowing to `OSError` would let a decode or regex failure
  propagate and take out a sort the other files could still complete.
- **Worker-thread top levels.** These exist to catch the unexpected and write
  `traceback.format_exc()` into the log panel. A narrowed one would let a
  background thread die silently -- the exact failure mode the log exists to
  make visible.
- **Optional third-party imports.** `tkinterweb`/`tkhtmlview`/`webview` can
  fail at import for reasons beyond `ImportError` (broken installs, missing
  native libs). The app must degrade to the browser, not refuse to start.
- **Deliberate backstops.** Sites that already catch the specific error above
  and whose `except Exception` arm *is* the "unexpected" case by construction.

The point of enabling the rule was never to reach zero findings. It was to make
each blind catch a decision someone signed for, rather than an invisible
default -- and to make the next one someone adds argue for itself.

The split is not where you would guess: 21 of the 28 narrowings are in the GUI,
which has no test coverage at all. That is defensible only because of *which*
sites they are. Every GUI narrowing rests on a guarantee from Tk or the standard
library -- `after_cancel` on a stale id raises `TclError`, `json.load` raises
`ValueError`, a failed `write_text` raises `OSError` -- so the raise-set is a
documented property of the callee, not an inference about this program's state.

The GUI catches that wrap *our own* engine calls, worker-thread bodies, or
third-party widgets were all left broad, precisely because there no such
guarantee exists and there is no Tk in the test environment to catch a wrong
guess. A bad narrowing there would convert a currently-survivable failure into
a crash that nothing in CI would ever see. The engine's 7 narrowings are the
cheap ones to trust: the 717-test suite and the differential baseline re-ran
green after each.

### The GUI has no automated coverage, and that is stated rather than glossed

There is no Tk in the test environment, so GUI changes were verified by what
*can* be checked: it parses, ruff and black are clean, every method referenced
by a rewritten callback exists, and `tools/check_undefined.py` reports nothing.
That is weaker than a passing test and should be treated as such -- the GUI
wants a manual smoke test.

### Fixing the checker that cried wolf

Running `tools/check_undefined.py` over the GUI produced **fifteen false
positives**. Ruff's `F821` disagreed, and ruff was right: the tool did not
model closures, so any nested function using a variable from the function
around it looked undefined.

Three defects, each caught by a negative control rather than by inspection:

1. **No enclosing scope.** Nested functions were checked in isolation.
2. **Double-visiting.** Seeding from `ast.walk` re-entered every nested
   function with an empty enclosing scope, undoing fix (1). Seeding now starts
   only from outermost functions.
3. **Lambda parameters unbound**, and module-level lambdas never visited at
   all -- `f = lambda x: x + typo` passed silently.

It now reports zero false positives across all first-party files while still
catching a bare undefined name, an undefined name inside a nested method
closure, and one inside a module-level lambda.

The lesson is about tools, not this tool: a checker with false positives is
worse than no checker, because the habit it teaches is ignoring it. Its
docstring now says plainly that it does not replace the linter and that the
two fail in different directions.


## 14. PEP conformance, verified rather than asserted

"Apply every PEP" is not a checkable claim -- there are 700+, and most are
informational (PEP 20), process documents (PEP 1), rejected proposals, or
*optional* language features. Using ``match`` where ``if``/``elif`` reads
better would make the code worse, not more compliant.

What is checkable is the finite set of PEPs that define a standard this
project should conform to. `tests/test_standards.py` asserts each one
mechanically, so the claim survives future edits:

| PEP | Standard | Enforced by |
|---|---|---|
| 8 | Style, **naming**, **import order** | ruff `E`/`W`/**`N`**/**`I`** + black |
| 257 | Docstring conventions | ruff `D` |
| 484 / 526 | Type hints, variable annotations | ruff `ANN` + mypy |
| 563 | `from __future__ import annotations` | test_standards |
| 585 / 604 | `list[str]`, `X \| Y` | ruff `UP` + test_standards |
| 3120 / 263 | UTF-8 source, no contradictory declaration | test_standards |
| 3131 | ASCII identifiers (policy: no homoglyphs) | test_standards |
| 328 | Absolute imports | test_standards |
| 440 | Version identifier format | test_standards |
| 621 | `[project]` metadata in pyproject.toml | test_standards |
| 561 | `py.typed` marker | test_standards |
| 594 / 632 | No removed stdlib modules, no distutils | test_standards |
| 394 | `python3` in shebangs | test_standards |

Test count went 374 -> **681**, almost all of it parametrised per source file.

### What turning the checks on actually found

* **`N` and `I` were never enabled.** PEP 8 naming and import ordering had
  gone unenforced the whole time. 18 findings: unsorted imports, and
  function-local `SANE`/`STEP`/`SIZE`/`CUST`/`_EPS` in UPPER_CASE, which PEP 8
  reserves for module-level constants. The genuine constants were hoisted to
  module level (where that spelling is correct) rather than lowercased.
* **Two dead aliases.** `RE_FILENAME_VERSION as _re_filename_version` and its
  pair were re-export leftovers from the split, referenced nowhere. `N811`
  found them because the alias broke the constant-naming convention.
* **PEP 563 was missing from both legacy scripts** -- they carry annotations
  but never opted into postponed evaluation.
* **No `[project]` table and no `py.typed`.** The package ships inline type
  hints that a consuming type checker would have silently ignored. There is
  now a test asserting `[project].version` and `mlox_subset.__version__` never
  drift apart.
* **isort fought the re-export shim.** It splits `from x import (a as b)` into
  one statement per alias, detaching the trailing `# noqa`. Fixed properly with
  `combine-as-imports` plus a file-level rule saying what the shim *is*, rather
  than by scattering comments isort would keep breaking.

### mypy: 22 errors, all in annotations I had written

mypy 2.3.0 no longer crashes on this codebase (it did when §9 was written), so
PEP 484 is now verifiable rather than assumed. It found **22 errors, every one
of them in the annotations added during the typing pass** -- hints the runtime
tolerated but that were wrong:

* `base_order_names: Sequence[str]` on `build_and_sort`, when the body
  concatenates it with a list. `Sequence` has no `+`.
* `anchor_out: tuple[str, str]` when the anchor is `None` for a plugin with no
  positioning signal.
* `pos: dict[str, int]` when custom plugins resolve to *fractional* positions
  between the integers -- that is the entire mechanism `_POSITION_EPSILON`
  exists for.
* `preds`/`succs` annotated `set` when they are lists that get `.sort()`ed.

**This is the argument for running a type checker rather than writing
annotations and calling it typed.** Every one of those was a confident hint
that was simply false, and a wrong type hint is worse than none because it
gets believed.

One was a latent *behaviour* bug rather than a typing nit: `toml_value(val)`
in the emitter could receive `None`. It would not crash -- it would write the
literal string `'None'` into `openmw.cfg` as a data path. The invariant that
prevents it holds, but is not expressible in the type, so it is now checked
explicitly with the reasoning recorded.

**Status: 22 -> 0. `mypy` now reports `Success: no issues found in 28 source
files`,** and it *gates* rather than advises: `pyproject.toml` sets
`files = ["mlox_subset"]`, `check_untyped_defs`, `warn_redundant_casts` and
`warn_unused_ignores`, so a bare `python -m mypy` checks the package.
`tests/test_standards.py` asserts that configuration, so the gate cannot be
quietly narrowed later.

Clearing the last twelve turned up two things worth more than the annotations:

* **A latent import bug.** `net/updaters.py` called `urllib.parse.urlparse`
  while importing only `urllib.request`. It resolved because `urllib.request`
  happens to import `urllib.parse` itself -- true today, guaranteed by nothing,
  and it would fail on a stdlib reshuffle in the one function that validates
  URLs. Now imported explicitly.
* **`PluginOrderEntry` became a `TypedDict` (PEP 589).** It had been
  `dict[str, Any]`, which erased precisely what matters: that `on_lists` is a
  list of strings and `needs_cleaning` a bool. Misreading either silently
  reclassifies a curated plugin as one of the user's own -- the failure this
  whole tool exists to prevent. Typing it also forced the split between
  `_PartialEntry` (mid-parse, `file_name` may be `None`) and the public
  `PluginOrderEntry` (`file_name: str`), because both parsers drop entries
  without a filename. Callers no longer have to guard a case that cannot
  reach them.

Two more of my own wrong annotations surfaced on the way: `anchor_map` typed
as holding 2-tuples when it holds 3, and `leftover`/`result` left unannotated
so mypy inferred `list[Never]`. Turning `warn_unused_ignores` on immediately
found two stale `# type: ignore` comments that `ignore_missing_imports` had
already made redundant -- the exact rot the setting exists to catch.


## 15. Re-verification, and PEP 20

### Gaps found on the second pass

Re-auditing the applicable-PEP list turned up three it had missed:

* **PEP 518/517** -- `pyproject.toml` declared `[project]` but no
  `[build-system]`. Declaring project metadata without saying how to build it
  is exactly the ambiguity those PEPs exist to remove: a tool has to fall back
  on guessing setuptools implicitly. Added, with explicit `packages` and
  `py-modules` because the flat layout gives auto-discovery two top-level
  modules it cannot choose between.
* **PEP 508** -- the four optional-dependency specifiers were never checked to
  parse. They do; now asserted, because a typo there is silent until someone
  installs the extra.
* **PEP 420** -- nothing was verifying that every subpackage has an
  `__init__.py`. A missing one still imports as a namespace package *until*
  PyInstaller bundles it, so the failure would surface only in the built
  binary. There is also now a test that the declared package list matches the
  directories that actually exist, so adding a subpackage without declaring it
  fails immediately rather than producing a broken wheel.

Test count: 682 -> **713**.

### PEP 20: what can be checked, and what cannot

Most of the Zen is not assertable. "Beautiful is better than ugly" and
"Readability counts" are review judgements; a test claiming to enforce them
would be theatre.

**One line is mechanical, and it is the one that hides bugs:** *"Errors should
never pass silently. Unless explicitly silenced."* A bare `except ...: pass`
is either a deliberate decision or a swallowed defect, and from the outside
those are identical. `test_pep20_silenced_errors_are_explicitly_silenced`
requires a comment on every one -- not that the silence be *correct*, which is
a review question, but that the reasoning was written down.

It found two violations on first run, in `i18n.py` and `tracing.py`. Both were
deliberate; neither said so. They do now.

### Where this codebase does *not* satisfy the Zen

Recorded because a self-assessment that finds nothing is not an assessment:

* **"Simple is better than complex" / "Flat is better than nested".**
  `build_and_sort` is **456 lines** at nesting depth 5;
  `generate_customizations_toml` is **311** at depth 6. Both are well past what
  anyone would defend on principle. Both are also pinned by the differential
  baseline and were relocated verbatim precisely *because* splitting them is a
  behaviour-changing refactor, not a formatting one. Breaking them up is real
  work with real risk, and it is outstanding -- not done, and not pretended
  otherwise.
* **"Special cases aren't special enough to break the rules."** This codebase
  breaks that deliberately and repeatedly, and it is the right call: the
  configurator simulation reproduces momw-configurator's *sharp edges*
  (fatal-on-ambiguous-anchor, silent multi-removal) rather than improving on
  them, because a preview that behaves better than the real thing is a lie.
  Recorded as a conscious exception rather than an oversight.
* **"There should be one -- and preferably only one -- obvious way."**
  The engine module re-exports ~60 names it no longer implements, so
  `core.build_and_sort` and `mlox_subset.sort.build_and_sort` are both valid.
  That is two obvious ways. It is a deliberate compatibility shim during the
  split, and it should eventually go -- callers moved to the packages, the
  shim deleted.

Where the Zen is *followed*, it is followed on purpose and the reasoning is in
the code: "Explicit is better than implicit" is why the disassembler emits raw
hex spans instead of guessing instructions; "In the face of ambiguity, refuse
the temptation to guess" is why `PluginFileIndex` returns `None` rather than
inventing a warning it cannot substantiate.
