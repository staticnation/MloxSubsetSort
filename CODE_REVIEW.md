# Code review - Wraithguard Toolkit (running log)

> **This is a running log, not a single review.** Sections are appended as work
> happens and are ordered **oldest first**, so §1 is the original review and the
> highest-numbered section is the most recent. Nothing here is rewritten when
> later work supersedes it - the point is to keep the reasoning that was
> actually used at the time, including the decisions that were later revised.
>
> **Read every figure as "true when written," not as current.** The clearest
> example is the test suite, quoted at whatever size it happened to be:
>
> | Section | Test count at the time |
> |---|---|
> | §4 (test suite, original review) | 129 |
> | §10 (licence audit) | 305 |
> | §13 (legacy scripts) | 374 |
> | 3.0 as released | 724 |
> | §28 (audit after several days of solo work) | 1,079 |
> | §30 (Help, banding, the TES3 schema) | 1,260 |
> | §33 (grass follow-ups) | 1,330 |
> | §34 (rule maker, audit, TOML, terrain) | 1,557 |
> | §35 (the NIF reader reaches every vanilla mesh) | 1,696 |
> | §36 (80,197 modded meshes, and the reader reaches the app) | 1,811 |
> | §37 (audit of the session's own work) | 1,822 |
> | §39 (geometry, a 3D viewer, and a loopback server) | 1,916 |
> | §43 (colour, escapes, and the coverage floor) | **3,442** (3,438 passed, 4 skipped; coverage 80.5%) |
> | §44 (two icon systems, a case-sensitive filesystem, and a Linux build that finally ships) | **3,648** (3,646 passed, 2 skipped: `test_differential` deliberate baseline skip, `test_viz_pages` symlinks not permitted; coverage 86.77%, floor 77%) |
> | §45 (dialogue rendered readable, xEdit conflict colours, a macOS app build, and an `abspath`/`resolve` audit) | **4,519** (4,514 passed, 5 skipped; coverage 93.44%, floor raised 77% -> 90%; the four `test_service_merge` POSIX-converter skips were then made cross-platform) |
> | §46 (3.1.6: a cfg orphan-pull subset source, and the `clean_` alias reconciliation) | **4,428** (4,412 passed, 16 skipped on the hermetic basis with no `tes3conv` on PATH, so the converter-gated tests skip; coverage 93.52% with one present, floor 90%; +22 tests in `test_subset_from_cfg.py`) |
>
> The same applies to tooling versions, file layouts, message counts and line
> counts. For the current state of anything, check the code, `CHANGELOG.md`, or
> run the gates.
>
> **Where the version line falls.** Everything up to and including §33's early
> entries is 3.0 as shipped; §34 onward is 3.1. `CHANGELOG.md` keeps the same
> split, and `MloxSubsetSort-3.0 release/` is the actual backup of what went
> out, so the two can be compared rather than argued about.
>
> Where a section records a decision that was *deliberately refused* (a linter
> rule, a "fix" that would have been wrong), that reasoning is usually still
> live and is cross-referenced from `pyproject.toml`. Those are the parts most
> worth reading before changing something.
>
> **The GUI is measured separately.** It needs Tk, so it is excluded from the
> hermetic suite and the counts above; `tests/test_gui_smoke.py` runs under a
> virtual display in CI and on any desktop with Tk. It was 16 tests when it was
> written in §31 and is 42 now (§34.4), and a *skip* there is treated as a
> failure - a skipped GUI test is a check that silently did not run, which has
> already hidden one real defect.

---

**The first entry - the original review.** Everything from §1 onward was
written against the state of the code described here.

Senior-developer review of `wraithguard_toolkit.py` (engine) and
`wraithguard_toolkit_gui.py` (Tkinter front-end), covering correctness,
security, PEP 8/PEP 20 conformance, testing, and performance.

**Verdict:** the codebase is in good shape. The domain logic is careful and
unusually well commented - the *why* is recorded, not just the *what*, which
is rare and valuable. Review found **four real defects** (one security-
relevant, one resource leak, one crash, one dead conditional), all fixed, and
added a **129-test pytest suite** that previously did not exist.

Tooling at the time: `ruff` 0.15, `black` 26.5, `pytest` 9.1, `mypy` 2.3,
configured in `pyproject.toml`.

---

## 1. Defects found and fixed

### 1.1 Unvalidated download scheme (security) - `fetch_url_bytes`

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

### 1.2 Temp-file leak on every failed validation - `update_plugin_order_yml`

The downloaded YAML was written to a `NamedTemporaryFile(delete=False)` and
only unlinked on the success path. Any parse failure leaked the file, once per
attempt. Fixed with `try/finally` + `unlink(missing_ok=True)`; regression test
runs five failing downloads and asserts zero leaked files.

### 1.3 Crash on a malformed URL template - `update_rule_files`

The GUI's Sources dialog only checks that the template contains `{name}`. A
template such as `https://host/{name}/{branch}` reached `str.format()` and
raised an uncaught `KeyError`, killing the update. Now caught and reported as
a bad template. Parametrised regression test covers four malformed shapes.

### 1.4 Dead conditional - `scan_backups`

```python
out.append((p, orig if (orig and orig.exists()) else orig, kind))   # both branches identical
```

The ternary returned `orig` either way. Reviewing the consumers showed the GUI
already renders its own "original missing" marker, and restoring a backup
whose original was deleted is *valid recovery* - so always reporting the path
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

- **E741 (ambiguous name `l`)** - 20 occurrences, all meaning "line". Renamed
  to `line`/`name`. Because this was a mechanical rename across parser and
  emitter internals, it was **verified behaviour-neutral by differential
  testing**: the pre-rename and post-rename modules were loaded side by side
  and produced identical output for the real 975-plugin load order,
  `simulate_configurator_apply`, `find_anchor_index`, `parse_mlox_file`
  (1,544 blocks) and `preview_configurator_result`.
- **E702 (semicolon-joined statements)** - 6 occurrences, expanded.
- **W291/W293 (trailing whitespace)** - cleared repo-wide.
- Dead imports and unused locals removed.

*PEP 20 note:* the code already follows "explicit is better than implicit" in
the places that matter - the Configurator simulation documents each upstream
quirk it deliberately mirrors, which is exactly the "special cases aren't
special enough to break the rules… although practicality beats purity"
balance this domain needs.

---

## 3. Linter findings deliberately **not** applied

A linter is an advisor, not an authority. Each of these was inspected and
rejected with a reason, recorded in `pyproject.toml` so the decision survives:

| Rule | Why it is wrong here |
| --- | --- |
| `B905` (`zip(strict=)`) | Every call site is either an intentional offset pairing `zip(xs, xs[1:])` - where `strict=True` would raise on *every* call - or a comparison that already reports length mismatch with a better message than an exception. (This exemption originally also cited the Python 3.8 target, since `strict=` is 3.10+. That half is obsolete as of 3.0, which requires 3.10+; the reasoning above stands on its own.) |
| `SIM115` (context manager) | The trace-log handles are deliberately long-lived; reopening per line made the sort crawl when logging thousands of steps. They flush per write and are closed explicitly. One flagged site already *does* use `with fh:`. |
| `PLC0415` (import outside top level) | Optional dependencies (`tomli`, `yaml`, viewer backends) are imported lazily so a missing optional dep degrades one feature instead of preventing startup. |
| `S110`/`S112`/`SIM105` (silent except) | Confined to cosmetic paths (a tooltip that cannot render, a trace line that cannot be written). Failing loudly there would take down a sort for a decorative reason. |
| `S603` (subprocess) | Verified safe: no `shell=True` anywhere, argument lists only, executables chosen by the user. |
| `S105` ("hardcoded password") | False positive - `token == "["` in the mlox expression tokenizer. |
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
  time, with per-plugin caching - bounded memory on large installs rather
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

## 7. Foundation package (`wraithguard/`)

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

1. **Module split.** `wraithguard/` is the target package and its foundation
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

1. **`RUF012`** - 8 mutable class attributes in the GUI would be clearer as
   `ClassVar[...]`. Low risk, cosmetic.
2. **Split the GUI module.** At ~4,100 lines it is the one genuine structural
   smell; the theme system, the conflict windows, and the tes3cmd front-end
   are natural separate modules. Worth doing when it next needs real work -
   not as a drive-by.
3. **CI.** `ruff check .` + `pytest` in a GitHub Action would keep this state
   from regressing.
4. ~~**`mypy`** is configured but advisory; version 2.3.0 crashes on this
   codebase.~~ **Resolved** -- see §14. mypy 2.3.0 no longer crashes, the
   package is clean, and it now gates rather than advises.

---

## 10. Licence audit: the opcode table (found during the disassembler work)

**Defect: GPL-derived data in a non-copyleft project.**

The first version of `wraithguard/mwscript/opcodes.py` was generated by merging
two sources - MWEdit's `Functions.dat` and MWSE's `MWSE/OpCodes.h` - and its
header, and `CREDITS.md`, both described MWSE as MIT-licensed.

**MWSE is GPLv2.** Reading `License/MWSE/LICENSE` rather than trusting the
assumption is what surfaced this. That directly contradicted the project's own
stated policy, recorded in `CREDITS.md`: *"No GPL source was copied into this
tool, so no copyleft obligations attach to it."* Shipping the merged table
would have made that sentence false, and arguably obliged the whole tool to be
GPLv2.

**Fix.** The table is regenerated from MWEdit (MIT) alone by the new
`tools/gen_opcodes.py`. The only entry MWEdit does not cover - `_SetReference`,
which the compiler emits for `id->Func` - was **re-derived by measurement**
rather than copied: correlating each script's bytecode against its own source
text produced `0x010C` in 200 of 200 cases with no competing candidate. An
opcode's numeric value observed in one's own data files is a fact, not an
expression of someone else's authorship.

**Cost of the fix: nothing measurable.** The table shrank from 938 opcodes to
564, and the decode ratio across the real corpus was *identical* before and
after (min 6%, median 40%, max 54%, zero false positives). The 377 MWSE-only
functions never occurred in any script tested - they are MWSE-mod-only calls.
`License/MWSE/` was removed, since nothing is derived from it, and `CREDITS.md`
now lists MWSE under *referenced, no source copied* alongside OpenMW and MO2.

**The generalisable lesson:** a dependency's licence is a fact to be read from
its `LICENSE` file, not inferred from the company it keeps. Every MIT claim in
`CREDITS.md` was re-checked against the corresponding licence text after this.

### Disassembler status

* 564-opcode table, generated and regenerable (`tools/gen_opcodes.py`).
* 305 tests passing; `ruff` and `black` clean across `wraithguard/`, `tests/`
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
**Copyright 2016 by John Moonsugar, MIT** - same author as mlox and tes3lint.
Corrected, with the upstream repository linked.

**Unnoticed MIT obligation: the evil-GMST table.** All 72 name/value pairs in
`_EVIL_GMSTS` are reproduced from `tes3lint.pl` (© 2009 John Moonsugar, MIT).
These are genuinely copied data, not a reimplementation - they record exactly
what a buggy Construction Set wrote, which cannot be rederived from first
principles. MIT requires the notice travel with the copy, so it now sits inline
above the table, and `tes3lint` has its own `CREDITS.md` entry rather than
being folded into mlox's.

**No licence at all: `missing_pathgrids.pl`, `cell_conflicts.pl.`** Neither
file carries a copyright line, an author, or a licence. *No licence granted*
is not the same as permissive - the default is that the author retains all
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
*permissive* direction - GPLv2 recorded as MIT, an unrelated author credited,
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
is foundation-level, so it moved to `wraithguard/tracing.py` first. That gave
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
`MLOX_VERSION_PATTERN` and `format_version` moved to `wraithguard/versions.py`
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

`plugin-order.yml` parsing became `wraithguard/momw.py`: it is the curated-list
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

`D` (pydocstyle/PEP 257) and `ANN` (PEP 484) are enabled for `wraithguard/`.
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
| `wraithguard/` (28 modules) | **clean** | clean |
| -- of which fully typed + documented | **28 / 28** | -- |
| `tests/` (11 modules) | **clean** | clean |
| `tools/` (2 scripts) | **clean** | clean |
| `wraithguard_toolkit.py` (legacy) | **clean** | clean |
| `wraithguard_toolkit_gui.py` (legacy) | **clean** | clean |

The 55 remaining findings are pre-existing debt in the two legacy scripts, and
they are *shrinking as a side effect of the split* -- 89 before it started, 55
now, because the code that moved was brought up to standard on the way out.
They are concentrated in `PTH*` (``os.path`` -> ``pathlib``), `PERF203`
(try/except in a loop, often deliberate per-item isolation) and `RUF012`
(mutable class attributes in the GUI, mostly color dictionaries).

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
`zip(xs, xs[1:])` -> `pairwise`, `RUF012` color dictionaries -> `ClassVar`.
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
cheap ones to trust: the full test suite and the differential baseline re-ran
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
| 8 | Style, **naming**, **import order** | ruff `E`/`W`/`N`/`I` + black |
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
  now a test asserting `[project].version` and `wraithguard.__version__` never
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
`files = ["wraithguard"]`, `check_untyped_defs`, `warn_redundant_casts` and
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
  `core.build_and_sort` and `wraithguard.sort.build_and_sort` are both valid.
  That is two obvious ways. It is a deliberate compatibility shim during the
  split, and it should eventually go -- callers moved to the packages, the
  shim deleted.

Where the Zen is *followed*, it is followed on purpose and the reasoning is in
the code: "Explicit is better than implicit" is why the disassembler emits raw
hex spans instead of guessing instructions; "In the face of ambiguity, refuse
the temptation to guess" is why `PluginFileIndex` returns `None` rather than
inventing a warning it cannot substantiate.

---

## 16. Outstanding-work reconciliation, at 3.0

This log is never rewritten, so §8 ("roadmap"), §9 ("recommendations") and §15
("where this codebase does not satisfy the Zen") still read as open even where
the work has since landed. This section reconciles all three against the code
as it actually is at 3.0, so the next person does not have to re-derive it.

Every figure below was measured, not recalled.

### Closed since it was written

| Item | Where | Evidence now |
|---|---|---|
| `RUF012` - 8 mutable class attributes in the GUI | §9.1 | `ruff check --select RUF012` is clean; the rule is enabled repo-wide |
| `mypy` advisory / crashing | §9.4 | already struck through in §9; it gates, and is clean on 28 files |
| Module split | §8.1 | §8 carries its own COMPLETE note; 6 subpackages, 28 modules |
| i18n string extraction | §8.4 | **partially** - plumbing, 141 marked strings, `.pot`, and `tools/make_pot.py` shipped in 3.0. The remaining 127 f-string sites are specified in the retired i18n brief |
| **CI** | §9.3 | `.github/workflows/ci.yml` runs the full gate list (ruff, black, mypy, `check_undefined`, `make_pot --check`, pytest) on Python 3.10 and 3.13. It installs `zstandard` deliberately: without it 3 bytecode tests skip, and a skipped test proves nothing |
| **Coverage measurement** | §8.5 | `[tool.coverage.*]` configured in `pyproject.toml`, with branch coverage and the GUI omitted (it cannot be imported without Tk, so including it would report a meaningless ~0%). CI publishes an HTML report as an artifact |

### Still outstanding

| Item | Where | Measured at 3.0 |
|---|---|---|
| **`print` → logging** | §8.3 | 75 `print()` in the engine vs 8 `get_logger` uses |
| **Typing + PEP 257 on the legacy scripts** | §8.2 | `wraithguard_toolkit.py` and the GUI still carry `"D", "ANN"` per-file exemptions; only `wraithguard/` meets the strict standard |
| **Split the GUI module** | §9.2 | Flagged at ~4,100 lines; now **5,586** |
| **Oversized functions** | §15 | See below |
| **Delete the re-export shim** | §15 | 58 names re-exported via 10 `from wraithguard` imports |

### Three of those need a correction or a caveat

**The oversized functions moved, and a bigger one is now unlisted.** §15 named
`build_and_sort` (456 lines, depth 5) and `generate_customizations_toml` (311,
depth 6). Both relocated during the split and are essentially unchanged - 457
and 312 lines, same depths, now in `wraithguard/sort/engine.py` and
`wraithguard/configurator/emit.py`. But the largest function in the codebase is
**`compute_plan` at 545 lines, depth 5**, which §15 never named. It is also
where the `_`-shadowing `NameError` hid until the gettext marker exposed it
(see 3.0's changelog), which is weak but real evidence that its size costs
something. `_build_controls` in the GUI is 378 lines but only depth 1 - long
rather than tangled, and correspondingly lower priority.

**Deleting the shim is now a breaking change.** §15 is right that
`core.build_and_sort` and `wraithguard.sort.build_and_sort` are two obvious
ways to reach one function. But 3.0's changelog states publicly that
`wraithguard_toolkit.py` keeps every existing `core.<name>` call site working.
Removing it is therefore a 4.0-scoped change with a deprecation period, not
tidy-up. Recorded so the next reader does not treat §15 as licence to delete it.

**`print` → logging and the i18n f-strings touch the same lines.** The 127
sites in the retired i18n brief are mostly `print(f"...")` in report output; §8.3 wants
those same calls demoted or routed through a logger. Doing them as one pass is
substantially cheaper than two, and avoids re-litigating which output is a
user-facing report and which is a diagnostic - a question both jobs must answer
identically.

### Suggested order, if someone picks this up

1. **Set the coverage floor.** The config landed without a `fail_under`, on
   purpose: the honest number comes from the first CI run, not from a figure
   guessed at a desk. Read it off that run and set the floor slightly below,
   so it ratchets upward instead of blocking the next PR.
2. **i18n f-strings + `print` → logging together**, per the retired i18n brief, whose
   first step (a static placeholder checker) is the safety net for both.
3. **GUI split** - the largest and most disruptive; worth having 1–2 first,
   and the coverage report will show which parts are least protected.
4. `compute_plan` / `build_and_sort` decomposition and the shim deletion are
   behaviour-risk work pinned by the differential baseline. They are not
   blocked, but neither should be attempted casually, and the shim wants a
   major version.

## 17. The §16 pick-up: what landed, what remains, and why

§16's suggested order was followed as written. Every figure below was
measured, not recalled.

### 1. The coverage floor (§16 item 1) -- set

Measured on the full suite with `zstandard` installed, branch coverage on:
**54%** (54.46% at the time of writing). `fail_under = 52` is now in
`pyproject.toml` -- slightly below the honest number, so it ratchets upward
instead of blocking the next PR. Discovered on the way: `coverage` cannot
write its data file on some mounted filesystems ("Operation not permitted" on
the temp data file), which presents as pytest dying with an INTERNALERROR
mid-run. `COVERAGE_FILE=/tmp/.coverage` works around it; recorded here
because the failure looks exactly like a hung test run.

### 2. i18n f-strings + print -> logging, as one pass (§16 items 2, §8.3/§8.4)

**The checker came first**, per the retired i18n brief's "build this *before*
converting": `tools/check_placeholders.py`, in `check_undefined.py`'s AST
style. For every `_("...") % {...}` / `ngettext(...) % {...}` it reports
missing keys (the runtime `KeyError`), unused keys (usually a typo'd twin),
positional `%s` in any marked string (translators reorder words), and
non-literal dicts as unverifiable rather than guessed at. `%%` is stripped
before scanning -- "100%% done" is prose, not a `% d` space-flag conversion,
a false positive the negative controls caught. It is proven in
`tests/test_i18n_placeholders.py` against deliberately broken inputs (a
checker is only trustworthy once it has been watched failing), wired into CI
between check_undefined and the .pot check, and gated in pytest so a local
run catches what CI would.

**The conversions**: the .pot went 141 -> 267 messages. Package sites (13),
CLI (~47 marked + 13 left as data), GUI (~54). Sites that turned out to be
pure data or decoration -- `content=` echoes, `{w}` warning passthroughs,
`=== title ===` banners, `removeContent:` lines -- were deliberately left
unmarked: a msgid with no prose is noise a translator has to skip. Plurals
use `ngettext`; strings carrying **two** independent counts keep the "(s)"
style deliberately, since ngettext handles exactly one count and splitting
the sentence would concatenate fragments (forbidden by i18n.py's own rules).

**The trap fired exactly where predicted.** `build_and_sort` contained
`_, n = heapq.heappop(ready)`; with `_` now the module-level gettext marker,
that binding made every earlier `_()` call in the function an
`UnboundLocalError`. Ruff's F823 flagged it and the test suite reproduced it;
renamed `_rank`. This is the third time the `_`-shadowing class of bug has
appeared the moment the marker reached a new file, and the reason the brief
insisted on AST-based checking over grep.

**print -> logging landed with it**, resolving the question both jobs share
("which output is report, which is diagnostics") once, per
`logging_setup.py`'s own contract: the stdout report -- including warnings
*about the user's mods* -- stays `print()` and is now marked for translation;
diagnostics *about the run* (unparseable rule file, failed CSV write, failed
staging) route through `get_logger(__name__)` at WARNING/ERROR. The CLI
finally gained the missing wiring: a `-v/--verbose` count flag and a
`setup_logging()` call in `main()` -- the plumbing existed since the
foundation package landed but had zero callers, which §16's "75 print vs 8
get_logger uses" measurement was politely understating. With no `-v`,
behaviour is unchanged except that diagnostics now carry a `WARNING`/`ERROR`
prefix on stderr; in the GUI they still land in the log panel via logging's
last-resort stderr handler, which the output-capture redirect picks up.

**End-to-end proof (brief step 6)**: a synthetic German catalogue was
compiled and loaded at runtime -- translation, plural selection
(1 Sicherungsdatei / 3 Sicherungsdateien) and English passthrough for
unmarked strings all behaved. Until this run, nothing had ever exercised a
non-null catalogue.

### 3. The GUI split (§16 item 3, §9.2) -- done, same discipline as the engine's

`wraithguard_toolkit_gui.py`: **5,765 -> 3,203 lines.** New subpackage
`wraithguard/gui/`:

| Module | Lines | Contents |
|---|---|---|
| `theme.py` | 1,026 | chrome palette (`DARK`), theme parsing (base16/native), live restyle walk, JSON/HTML highlighters |
| `widgets.py` | 387 | Tooltip, QueueWriter, PathField, DragReorderListbox, typeahead |
| `t3.py` | 573 | `Tes3cmdMixin` -- the tes3cmd window and its workers |
| `conflicts.py` | 650 | `ConflictWindowsMixin` -- record/resource windows, field diff, CSV export |
| `__init__.py` | 91 | `app_base_dir()`, the shared tkinterdnd2 probe, `trace_first_fire()` |

Bodies moved **verbatim**; the main module re-imports every name, so the
smoke-test instructions and all internal references are unchanged. The two
window groups are *mixins* -- `class App(Tes3cmdMixin, ConflictWindowsMixin)`
-- so `self` is the same object and cross-group method calls resolve through
the MRO exactly as before. `app_base_dir()` moved into the package and now
derives the source-run app folder from its own location
(`<app>/wraithguard/gui/__init__.py` -> two parents up) instead of the GUI
script's `__file__`; the frozen branch is untouched.

The static check earned its keep again: `check_undefined.py` produced the
exact import list each new module needed (including the non-obvious
`scrolledtext` and the optional `mwscript` fallback pair), before anything
was run. The moved modules carry the legacy scripts' `D`/`ANN`/`PERF203`/
`S603` exemptions and a mypy `ignore_errors` override -- documented debt in
`pyproject.toml`, same as the engine relocations were, meant to shrink to
nothing. They are excluded from coverage for the same reason the GUI script
is: no Tk in the hermetic suite. Runtime verification is SMOKE_TEST.md §2
and §5, which exercise precisely the moved windows.

The PEP 20 silenced-errors test immediately caught 12 formerly-exempt
`except tk.TclError: pass` handlers now living under `wraithguard/`; each
now states its reason. The gate did its job the moment the code crossed into
its jurisdiction.

### 4. PEP audit, third pass (user request: "any PEP not yet applied")

`test_standards.py` already asserts 8, 257, 263, 328, 394, 420, 440, 484,
508, 517/518, 561, 563, 585/604, 594, 621, 632, 3120, 3131 and the checkable
line of PEP 20. One applicable PEP was missing: **PEP 639**. `license` was
the deprecated `{file = ...}` table; it is now the SPDX expression `"MIT"`
with `license-files` keeping the text in the distribution, `[build-system]`
bumped to `setuptools>=77` (the first release that understands the field),
and a new `test_pep639_license_is_an_spdx_expression` pins it -- including
that no deprecated `License ::` classifier sneaks back in. The 3.13
classifier CI already tests against was added at the same time.

### Still outstanding after this pass

* **Typing + PEP 257 on the legacy scripts** (§8.2). Unchanged in scope, and
  now slightly larger on paper: the four relocated GUI modules carry the same
  per-file exemptions. It is a sweep of hundreds of annotations over
  `wraithguard_toolkit.py` (3,810 lines) and the GUI file (3,203); mechanical
  but long, and worthless if rushed -- §14's lesson was that 22 of the
  hand-written annotations were simply wrong until a checker read them.
* **`compute_plan` (545) / `build_and_sort` (457) decomposition** (§15/§16).
  Deliberately not attempted at the tail of the session that did everything
  above. §16's own caveat stands: this is behaviour-risk work on the two
  functions whose output *is* the product, and it deserves a fresh session
  with the differential baseline run before, during and after -- not a tired
  one. The baseline (41 pinned observations) is green and waiting.
* **The re-export shim** -- 4.0-scoped, per §16. Not touched.

### Suite and gates at the end of this pass

798 tests (724 at §16), 1 deliberate skip. Green: pytest, ruff, black, mypy
(31 files), check_undefined, check_placeholders, `make_pot --check`,
coverage >= 52%. CI runs all of it on 3.10 and 3.13.

## 18. §17's two deferrals, picked up: decomposition and the typing pass

§17 left exactly two items open and said why. Both were then done, in the
order that made the second cheaper: decompose first, annotate the results.

### The oversized functions (§15/§16) -- decomposed

| Function | Before | After | Extracted into |
|---|---|---|---|
| `compute_plan` | **644** | **105** | 10 stage helpers in `wraithguard_toolkit.py` |
| `build_and_sort` | **476** | **119** | `_build_edges`, `_anchor_positions`, `_kahn_place` |

Every body moved **verbatim**; the helpers are named for the pipeline stages
the original comments already marked (`# --- plugin-order.yml ...`,
`# 1) frozen chain`, `# 3) stable Kahn's ...`), so the split follows seams the
code had drawn itself rather than ones invented for the occasion. Report and
trace output are byte-identical by construction, and the 41-observation
differential baseline stayed green at every step -- which is the only reason a
refactor of the two functions whose output *is* the product was attempted at
all.

Method used, worth recording because it is repeatable: cut the stage body,
paste it under a `def` with the same locals as parameters, run
`tools/check_undefined.py` on the fragment to get the exact free-variable list,
then wire the call. The checker named every missing name up front (including
non-obvious ones like `scrolledtext` and the optional `mwscript` fallback
pair) instead of surfacing them one `NameError` at a time.

`generate_customizations_toml` (312) was left alone: it is a single linear
emitter with no internal stage boundaries, so splitting it would mean inventing
seams rather than following them -- lower value and higher risk than either
function above.

### The typing pass (§8.2) -- complete for `wraithguard/gui/`, partial for the scripts

**`wraithguard/gui/` now meets the package's strict standard.** All four
relocated modules are fully annotated, PEP 257 clean, and **mypy-clean** --
so the `D`/`ANN` per-file ignores and the `ignore_errors` mypy override that
§17 recorded as debt are **deleted**, not relaxed. mypy now gates 33 files
instead of 28. Only `PERF203` (per-widget `try/except` is mandatory in Tk) and
`S603` (subprocess argv built entirely from our own paths) remain, each still
carrying its stated reason.

Turning mypy on found 177 errors. Two classes were worth the trip:

* **The mixin host contract, 106 errors.** A mixin is half a class: the
  methods reference ~36 attributes and helpers that live on `App`. Rather than
  silence that, each mixin now declares what it expects from its host in an
  `if TYPE_CHECKING:` block. The coupling was always there; it is now written
  down and checked, which is the difference between a documented interface and
  an implicit one.
* **A real API defect.** `PluginFileIndex(data_dirs: list[str | Path])` cannot
  accept a `list[str]` -- `list` is invariant -- and every caller builds
  exactly that. It happened to work because nothing type-checked those call
  sites. Fixed at the source (`Sequence`, per mypy's own advice), not papered
  over at the call site.

The rest were ordinary and real: a function annotated `-> None` that returns a
value, a `dict[str, object]` scratch dict whose three values have three types,
`text` assigned a raw field value before being stringified. §14's lesson held
again -- the annotations I wrote by hand were the ones mypy found wrong.

**The two legacy scripts are improved but NOT finished, and that is stated
rather than glossed.** Measured now:

| File | Returns typed | Args typed | D/ANN findings |
|---|---|---|---|
| `wraithguard_toolkit.py` | 25/89 | 19/200 | 337 |
| `wraithguard_toolkit_gui.py` | 87/118 | 2/84 | 165 |

What landed: **96 functions gained `-> None`** by a static pass that only
annotates a function when its own scope provably returns nothing (nested
scopes excluded, any `return <value>` or `yield` disqualifies it), plus 87
auto-fixable docstring corrections. Both files still carry their `D`/`ANN`
exemptions, because the remainder is **263 `ANN001` argument annotations**
across 7,300 lines, and those cannot be inferred mechanically -- each needs the
function read. Doing them fast is precisely how §14's 22 wrong annotations got
written, and a wrong hint is worse than none because it gets believed. This is
the next unit of work; it is bounded, uninteresting, and wants its own session
with mypy turned on per-file as each one lands.

### Gates at the end of this pass

797 passed / 1 deliberate skip. Green: ruff, black, **mypy (33 files, GUI
package included)**, `check_undefined`, `check_placeholders`,
`make_pot --check` (269 messages), coverage ≥ 52%.

## 19. Verification pass, third PEP audit, and the hand-off

A pass with no new feature work: re-run everything, re-derive the applicable
PEP list against the code as it now is, reconcile every figure in the docs
against the code, and write the remaining work down properly.

### The PEP audit found one gap, and it was in what was *not* exercised

The applicable-PEP list itself came out unchanged -- 8, 257, 263, 328, 394,
420, 440, 484/526, 508, 517/518, 561, 563, 585/604, 594, 621, 632, 639, 3120,
3131, plus the checkable line of PEP 20. Nothing newly applicable had appeared.

What *had* gone unverified was PEP 517/518 itself. `[build-system]` and
`[project]` were declared in §15 "for correctness and inspectability rather
than because a wheel is published" -- and then nothing ever built a wheel. A
declaration that is never executed is a claim, not a fact, so it was tested:

* **`python -m build --wheel` succeeds**, producing
  `wraithguard_toolkit-3.0.0-py3-none-any.whl` with all 7 subpackages and both
  top-level modules collected. The declaration is sound.
* It also surfaced that the declared floor is real: `setuptools>=77` (needed
  for PEP 639 licence expressions) is genuinely newer than what ships on a
  stock Ubuntu 22.04 Python (59.6.0), so an old environment fails with a clear
  `Missing dependencies: setuptools>=77` rather than a confusing metadata
  error. That is the correct behaviour and worth knowing before someone
  reports it as a bug.

Two gates were added rather than leaving this as a one-off observation:
`test_pep517_build_metadata_is_resolvable` asserts every declared package and
py-module exists on disk (cheap, runs in the suite), and **CI now runs the
real `python -m build`** -- the slow half belongs where it can take the time.

A second, smaller check came out of the same audit:
`test_public_api_all_names_resolve`. `__all__` is the package's stated public
surface and it is maintained by hand; a rename that misses it is an
`AttributeError` on `from wraithguard import *` and a silent hole in what the
docs promise. All 9 names currently resolve.

### Docs reconciled against measurement, not memory

Every figure was re-derived from the code and corrected where it had drifted
during the session: the suite is **800 tests** (not 798), the package is
**7 subpackages / 33 modules** (not 6/28), the `.pot` holds **269 messages**
(not 267), mypy gates **33 files**, and the theme picker offers **23** presets.
Two claims in the changelog had become outright false and were rewritten: the
GUI modules no longer "keep the legacy exemptions for now" (they were paid off
in the same release), and `gui/` was missing from the subpackage list.

This is the recurring lesson of this log: figures written from memory drift
within a single session, let alone across releases. Re-derive them.

### The remaining work is written down, not remembered

The retired typing brief was the hand-off for the last open item -- the `D`/`ANN`
exemptions on the two legacy scripts. It records the measured scope (502
findings, 263 of them `ANN001` argument annotations across 7,290 lines), what
was already done so it is not redone, the per-module method that worked for
`wraithguard/gui/`, a definition of done, and the five traps that actually bit
during that pass -- including the two most expensive: `object` as a
placeholder annotation turns one missing hint into five new errors, and `list`
invariance rejects the `list[str]` every caller builds.

It follows the retired i18n brief's format deliberately. That brief was written the
same way and the work landed from it in one session, along its suggested order,
with its predicted trap firing exactly where it said it would.

### Gates at the end of this pass

800 tests: 799 passed, 1 deliberate skip. Green: ruff, black, mypy (33 files),
`check_undefined`, `check_placeholders`, `make_pot --check` (269 messages),
coverage 54.45% against the 52% floor, and `python -m build` produces a valid
wheel.

## 20. The typing pass, finished: both legacy scripts, and what mypy caught

The retired typing brief's work, done along its own suggested order. Both scripts now
meet the package standard, so **every `D`/`ANN` per-file ignore and every mypy
`ignore_errors` override is deleted** -- not relaxed, deleted. mypy gates
**35 files**, the whole codebase.

| File | Returns typed | Args typed | D/ANN |
|---|---|---|---|
| `wraithguard_toolkit.py` | 25/89 → **89/89** | 19/200 → **200/200** | 337 → **0** |
| `wraithguard_toolkit_gui.py` | 87/118 → **118/118** | 2/84 → **84/84** | 165 → **0** |

The brief's per-module method held: annotate from the call sites, *then* turn
mypy on for that file and fix what it finds before moving on. Turning it on
found **75 errors in the engine and 46 in the GUI**, and the split between
"my annotation was wrong" and "the code was wrong" is the interesting part.

### Annotations I wrote that were simply wrong

§14's lesson, third confirmation. Seven signatures I had written from reading
the function name and body were contradicted by the code the moment a checker
read them:

* `read_plugin_masters_with_sizes` -- I wrote `list[tuple[str, int, int]]`; it
  returns **pairs**, and the recorded size is `int | None` when the DATA
  subrecord is absent.
* `_iter_tes3_records` / `_iter_subrecords` -- I wrote `tuple[str, bytes]`; the
  record tags are raw **4-byte `bytes`** (`b"CELL"`), never decoded.
* `_load_sidecar(side: str)` -- `side` is the sidecar **`Path`**, and the very
  next line calls `side.exists()`.
* `read_cfg` -- I wrote `list[tuple[str, int]]` for the content order; it pairs
  each name with its **raw line value**, a `str`.
* `write_cfg(segments: Mapping)` -- it is a **sequence of
  `(positions, new_lines)` pairs**, and mypy caught it by refusing to unpack a
  string.
* `lint_plugins(progress)` -- I gave the callback three parameters; it is
  called with **two**.

Every one of these would have been believed by the next reader. None was
caught by the test suite, because a wrong annotation changes nothing at
runtime -- which is exactly why the checker has to run.

### Defects in the code, not the annotations

* **A second invariance defect.** `check_predicates(data_dirs: list[str | Path])`
  cannot accept the `list[str]` its only caller builds -- the same bug as
  `PluginFileIndex` in §17, in a different function, found the same way. Fixed
  at the source with `Sequence`. Two independent instances is a pattern, not
  an accident: prefer `Sequence`/`Mapping` for parameters you only read.
* **A latent `None` dereference in the tes3cmd worker.**
  `stage_for_tes3cmd` returns `(Path | None, missing)`, and the GUI used the
  path after checking only `missing`. If staging ever failed without
  populating `missing`, that was an `AttributeError` in a worker thread. Now
  an explicit `staged is None` branch that reports and skips.
* **A `_get_session` contract that was too weak to be useful.** The mixin
  declared it `-> object`, which silently made every downstream call
  (`detect_conflicts`, `dump_tes3conv_json`) an error the moment those gained
  real signatures. Corrected to `Tes3ConvSession | None`.
* **A bug I introduced and the linter caught immediately.** Renaming a loop
  variable to disambiguate two `for k, var in (...)` loops left the body still
  assigning through the old name. Ruff's `B007` ("loop control variable not
  used within loop body") flagged it within seconds. Worth recording as the
  counter-example to "the linter is noise": that one would have silently
  stopped every boolean setting from being restored.

### Two things deliberately *not* silenced

* **The mixin host contracts stay.** They now also carry `_tes3conv_override`
  and `worker_running`, which the pass surfaced. Where the two mixins disagreed
  about a type (`T3_NEVER_CLEAN` declared `frozenset`, defined `set`), the
  declaration was corrected to match reality rather than the reverse.
* **`assert` where a contract is real but unprovable.** Three sites -- the
  export worker's plan, the staging dir on the clean path, the savegame file
  list after an error check -- assert a condition the caller guarantees but
  mypy cannot see. Stating it is better than an ignore comment: it documents
  the contract *and* fails loudly if a future caller breaks it.

### Method notes for next time

`-> None` was inferred mechanically for 96 functions by a static pass that
only annotates when the function's own scope provably returns nothing (nested
scopes excluded; any `return <value>` or `yield` disqualifies it). Docstring
D205 reflows were scripted the same way -- split at the first sentence, but
only when the resulting summary fits on one line; the ~50 that did not were
rewritten by hand. Both scripts are in the session's scratch, not shipped:
they are one-shot migration aids, not tools worth maintaining.

### Gates at the end of this pass

800 tests: 799 passed, 1 deliberate skip. Green: ruff, black, **mypy (35
files -- the entire codebase)**, `check_undefined`, `check_placeholders`,
`make_pot --check` (270 messages), coverage 54.43% against the 52% floor.

## 21. Fourth PEP audit, the tooltip gap, and doc retirement

### The audit found one real gap, and it was in a family never enabled

The applicable-PEP list is stable -- three consecutive audits have not grown
it, which is itself the useful result. So this pass went at the *rule families
never turned on*, which is where an unexamined decision can hide. Eight were
measured. Seven are style preferences and are recorded in `REMAINING_WORK.md`
with their counts, so the choice not to enable them is now informed rather
than implicit.

One was a genuine standards question: **`DTZ`, naive datetimes.** Nine sites
call `datetime.now()` without a timezone. Every one is correct -- `.bak`
filenames, trace lines, the build stamp and the `.pot` header are all read by
the user against their own wall clock, and UTC would be actively wrong -- but
none of them *said so*. That is precisely the condition `BLE001` was enabled
to fix in §13, so `DTZ` is now enabled on the same terms: the rule is on, and
each of the nine sites carries a `# noqa: DTZ005` with its reason.
`test_naive_datetimes_are_explicitly_local` pins the shape so a new naive
`now()` cannot arrive undocumented.

Also checked and clean, worth recording so the next audit skips them: no
`os.listdir` (PEP 471), no legacy `IOError`/`EnvironmentError` aliases (PEP
3151), no `raise StopIteration` in a generator (PEP 479), no text I/O without
an explicit `encoding=`, and ruff's and black's line lengths agree.

### The i18n pass had missed 42 strings, and the claim was wrong

`CHANGELOG.md` said "every user-facing string is marked -- buttons, labels,
**tooltips**, dialogs". That was false. An AST sweep of `add_tooltip()`,
`messagebox.*` and the `text=`/`title=`/`message=` keywords found **42
unmarked literals**: 28 tooltips and 14 dialog bodies and panel titles.

They were missed for an understandable reason and that is the lesson: the
§17 pass converted *f-strings* to named-placeholder form, and these are plain
literals, so they never appeared in that pass's 127-site inventory. They also
sit next to widgets whose `text=` **was** marked, which makes them invisible
on a read-through.

The `.pot` went **270 -> 312 messages**. A one-off sweep would leave the same
hole open, so `TestUserFacingStringsAreMarked` now asserts the property
directly: any string literal in a user-facing call or keyword, in any GUI
module, must be wrapped. It fails with the offending file, line and text.

**The generalisable point:** "I converted all 127 sites" and "every user-facing
string is marked" are different claims, and the first was quietly substituted
for the second in the changelog. A checker that asserts the *property* is the
only version of that claim worth making.

### Completed briefs retired

The retired i18n, theming and typing briefs all said COMPLETED
and together ran to 562 lines of "hand this to a fresh session" instructions
for work that is done. A hand-off note for finished work is worse than no note:
a reader picks it up expecting a task. They were first reduced to ~18-line stubs
pointing at the `CODE_REVIEW.md` section that holds the real record, with the
live code references (in `engine.py`, `check_placeholders.py`, the CI workflow
and the placeholder tests) redirected to those sections so nothing dangled. In
3.1.3 the stubs were deleted outright, and this log's own mentions of them were
changed from filenames to "the retired X brief" so nothing points at a file
that is gone. The record they held lives on in §17 (i18n) and §20 (typing);
theming is in `SMOKE_TEST.md` §5 and the 3.0 changelog.

Their replacement is **`REMAINING_WORK.md`** -- the only forward-looking
document, listing what a reviewer would still flag: the deliberate rule
exemptions and why each stands, the six rule families not enabled and their
measured counts, the seven oversized functions in priority order with a verdict
on each, the coverage distribution and why the GUI has none, and a "do not do
this" section for the decisions that have already been made on evidence.

### Gates at the end of this pass

802 tests: 801 passed, 1 deliberate skip. Green: ruff (now including `DTZ`),
black, mypy (35 files), `check_undefined`, `check_placeholders`,
`make_pot --check` (312 messages), and the CI wheel build. Coverage last
measured at 54.4% against the 52% floor; the sandbox this pass ran in reaped
the long re-measure, so that figure is carried forward from §20 rather than
re-derived -- and CI will report it authoritatively on the next run.

## 22. Landscape and path-grid field decoding

### What was added

The field-diff window could already disassemble a script's `bytecode` and
decode its `variables`. Everything else stored as binary was still shown as
base64, which is worse than useless in a diff: two landscape cells differing by
one vertex produce *entirely* different base64, so the viewer said "these are
completely different" for a one-vertex nudge.

New package `wraithguard/tes3fields/` decodes six more fields:

| Field | Rendered as |
|---|---|
| `vertex_heights.data` | absolute world-unit heights, one terrain row per line |
| `vertex_normals.data` | `(x,y,z)` int8 normals, one row per line |
| `vertex_colors.data` | `#rrggbb`, one row per line |
| `world_map_data.data` | the 9x9 world-map heightmap |
| `texture_indices.data` | the 16x16 LTEX index grid |
| `connections` (PGRD) | a per-point adjacency list |

Two of these are only meaningful *beside a sibling field*, which is the same
shape as `bytecode` needing the record's `text`: heights need
`vertex_heights.offset` to be absolute rather than relative, and path-grid
edges need `points` to be sliced at all -- `PGRC` is a bare concatenation of
every point's neighbour list with no delimiters. The whole flattened record is
therefore passed to the renderer, and the dispatcher is a dict, so adding a
seventh field is one entry rather than another `elif` in the GUI.

### Where the format came from

Two permissively-licensed sources, both already credited in `CREDITS.md`:

* **UESP's record documentation** for [LAND] and [PGRD] -- prose describing the
  subrecords, their sizes, and PGRD's worked adjacency example.
* **TES3Tool** (MIT) -- `TES3Lib/Subrecords/LAND/*.cs` for field order and the
  height-reconstruction semantics.

This project keeps a hard line on provenance (§10): every dependency's licence
is read from its `LICENSE` file, and `CREDITS.md` states that no copyleft
source was copied. Reference implementations under copyleft licences sit in the
workspace and were **not** used as sources for this module. Being able to read
an implementation is not the same as being free to derive from it, and the
distinction is worth stating as policy rather than rediscovering later.

### How the ambiguous parts were settled: measurement, not assumption

Documentation covers the layout but leaves two things a reader could get wrong
in ways that still *look* plausible. Both were settled against real plugins in
the workspace, by extracting the subrecords directly from the record stream
with `struct.unpack_from` -- no third-party tooling in the loop.

**1. Subrecord sizes and the height reconstruction.** Every subrecord came out
at exactly its documented size (VNML 12,675; VHGT 4,232; WNAM 81; VCLR 12,675;
VTEX 512). More usefully, reconstructed heights bottom out at exactly
**-2048** -- the format's documented default-height sentinel -- and top out in
a plausible terrain range. Hitting a documented constant on the nose is a
strong signal that the doubly-cumulative reconstruction (a carried row height,
then per-column accumulation within the row) is right; a naive flat sum over
all 4,225 deltas does not land there.

**2. Path-grid slicing.** `PGRC` cannot be sliced without each point's
connection count, which lives at a specific byte offset inside `PGRP`. The
check that settles both at once: **the sum of every point's connection count
must equal the edge count.** On the first record tested that was 282 = 282,
exactly, and the renderer consumes every edge with no trailing remainder.

**3. VTEX ordering, decided by a statistic rather than a guess.** The 16x16
texture grid is stored as sixteen 4x4 blocks rather than plain rows. Reading it
row-major produces a grid that looks reasonable and is wrong -- every index in
the wrong square. Rather than assume either reading, both were scored on real
data using a property real terrain has and scrambled data does not: **the
fraction of orthogonally-adjacent cells holding the same texture index.**

| Reading | Raw plugin bytes (2,190 cells) | tes3conv JSON (367 cells) |
|---|---|---|
| row-major (storage order) | 0.714 | 0.716 |
| de-swizzled (4x4 blocks) | **0.852** | **0.855** |

De-swizzling won in 97% of cells individually on the raw bytes and 99% on the
JSON. The de-swizzle is expressed as an index mapping over the base-4 digits of
the stored position, and `decode_texture_indices(deswizzle=False)` still
exposes storage order.

### What the JSON dump settled that nothing else could

Everything above was derived from plugin bytes. A tes3conv JSON dump of a real
4,032-cell plugin then closed the remaining questions outright, because it is
*the exact input this code consumes*:

* **Field names and shapes** are as assumed: `vertex_heights` carries `offset`
  and `data` separately, the rest carry `data` alone.
* **`vertex_heights.data` is exactly `subrecord[4:4229]`** -- the float offset
  lifted out into its own field and the three trailing unused bytes dropped.
  Byte-compared against the same cell read straight from the ESP.
* **VTEX bytes are byte-identical between the JSON and the ESP.** That closes
  the one assumption the de-swizzle rested on: tes3conv passes the payload
  through unchanged, so un-swizzling here is correct rather than probable.
* **Path-grid points use `location` and `connection_count`** -- the first
  spellings the decoder probes for.

**And it found a real bug.** tes3conv **prefixes `connections` with a `uint32`
count**; the raw subrecord in a plugin does not. Left in place, that prefix is
not a cosmetic off-by-one -- it shifts every edge by one slot, so each point is
attributed its *neighbour's* connections, and the whole adjacency list is
quietly wrong. On the record it was found with, it also produced a leading
"edge" of 224 in a 62-point grid: an index that cannot exist.

tes3conv's own source (MIT) confirms this architecturally rather than
statistically: it is `serde_json::to_string(&plugin.objects)` with
`features = ["esp", "serde", "serde-zstd"]` and performs **no field-level
transformation**. So the landscape payloads are the raw subrecord bytes -- the
de-swizzle is ours to do -- and the `connections` prefix comes from the `tes3`
crate's serde encoding of a length-prefixed collection, not from tes3conv. The
`serde-zstd` feature is also why these arrive zstd-compressed under the base64.

The behaviour is systematic, not a quirk of one record: across **717 path
grids in 120 cached tes3conv dumps, 100% carry the prefix**, while the 290
landscape records in the same dumps carry none -- every one of their fields
decodes to exactly its documented size. So the prefix belongs to the
length-prefixed fields specifically, and the detection is safe to leave on.

Two things made it findable rather than invisible. The decoder already
*reported* leftover edges instead of discarding them, so the mismatch surfaced
as a `; NOTE: 1 trailing edge(s) unaccounted for` line. And the count could be
cross-checked: `sum(connection_count)` was 224 against 225 decoded values, and
stripping the first value put every target inside `0..61` where one had been
out of range. `decode_connections` now detects the prefix -- confirmed by the
points' own total when available, by the self-describing shape when not -- and
this is the same wrapping `decode_variables_field` already strips from
`variables`, which is a consistency worth knowing about tes3conv generally.

### Verification

`tests/test_tes3fields.py` (35 tests) uses synthetic fixtures whose answers are
exact by construction -- and deliberately commits no third-party mod data. It
pins the height reconstruction (including an explicit assertion against the
plausible wrong answer), the de-swizzle as a *permutation* that moves values
without losing them, UESP's worked path-grid example, the length-prefix strip
in both directions (present, and a genuine leading edge that must not be
mistaken for one), and totality: every decoder must return a `;` comment for
truncated or garbage input rather than raise.

The real-plugin and real-JSON validation above is the complement to that, and
is recorded here rather than committed as fixtures -- 504 sampled landscape
records decoded without a single failure, and the one path grid renders with
every edge attributed and no remainder.

### Gates

868 tests: 867 passed, 1 skipped. ruff, black, mypy (38 files),
`check_undefined`, `check_placeholders`, `make_pot --check` (312 messages).

---

## 23. Deleting the re-export shim (3.0, pre-release)

The last architectural item on `REMAINING_WORK.md`. It had been scoped to 4.0
in §16 and re-affirmed as 4.0-scoped in §21, both times for the same stated
reason: 3.0's changelog promised every `core.<name>` call site would keep
working, so removing it would be a breaking change owed a deprecation cycle.

**The premise was checked and it did not hold.** 3.0 has not shipped. The
"promise" was a sentence in an unreleased changelog, not an interface anyone
depends on. Once that is noticed the cost comparison inverts: removing the shim
now is one refactoring pass, and removing it later is a major-version cycle
plus a release carrying a `DeprecationWarning` nobody would have needed to see.
Recorded here because the *conclusion* in §16 and §21 was wrong while the
*reasoning* was fine - the defect was an unexamined assumption, and it survived
two review passes precisely because it looked settled.

### What the shim actually was

The phrase "62 re-exported names" was itself imprecise, and the imprecision
mattered. Measured by parsing the module and asking which imported names its
own body ever mentions:

| | Count | Disposition |
|---|---|---|
| Imported from `wraithguard/` | 62 | |
| ...used by the engine itself | **26** | **Kept.** Ordinary imports; never re-exports. |
| ...never used, imported to be re-exported | **36** | **Deleted.** |

So a third of the "shim" was not a shim. Deleting all 62, which is what the
item as written invited, would have broken the engine.

### Method

1. **Rewire callers first, delete second.** Every `core.<name>` reference in
   `wraithguard_toolkit_gui.py`, `wraithguard/gui/` and `tests/` that resolved to
   an import was repointed at the module the name really lives in - **42
   distinct names across 12 files**.
2. **Delete only names the engine never mentions**, recomputed after the
   rewire rather than from the list in step 1.
3. **Remove `F401` from the per-file exemption**, which is the step that makes
   the result durable. While it stood, an unused import in this file was the
   house style and therefore invisible.

### Two things worth recording

**`F401` immediately found a real defect.** With the exemption gone, ruff
reported `sys` imported but unused - genuinely unused, its only remaining
mention being the word "sys.stdout" inside a docstring. It had been dead since
the CLI moved from `sys.exit()` to `raise SystemExit`, and the exemption had
been hiding it. This is the argument for the whole exercise in miniature: a
blanket exemption does not just permit the thing it was written for, it
silences everything that looks like it.

**Aliased imports nearly caused a silent break.** The rewiring map was built by
parsing the engine's imports, and deliberately skipped aliased ones
(`format_version as _format_version`) rather than guess at intent. Seven names
were aliased, and two of them - `core._format_version` and `core._is_master_file`
- had live call sites in the tests. The safety net was refusing to delete any
name still referenced *anywhere*, checked across every `.py` and `.md` in the
tree before deletion rather than trusting the rewrite to have been exhaustive.
Both were caught there, not by a failing test. A rewrite pass should be assumed
incomplete and verified against the source of truth, not against its own output.

**75 test functions were taking the `core` fixture without using it** once
their bodies stopped saying `core.`. Removing the parameter was mechanical but
not free: three helpers (`parse`, `_sort_real`, `_configurator_observations`)
were caught by the same sweep, and their call sites passed arguments
positionally, so the signature and the callers had to move together. Two rounds
of regex got this wrong in opposite directions before it was redone as an AST
fixpoint - *a function must declare `core` if and only if its body loads it* -
which converged in one pass. The lesson is the ordinary one about regex and
syntax, and it is here because the first two attempts both left a green-looking
tree with a broken test file underneath.

### The end state, stated as something checkable

`core.<name>` is still used 41 times, and that is correct: every one of those
names is **defined in `wraithguard_toolkit.py` itself** - `compute_plan`,
`lint_plugins`, the scanners, the CLI surface. The GUI reaching into the engine
for engine things is not the shim; the shim was the engine standing in front of
`wraithguard/` for names it never touched.

The check that says so, and that would catch a regression:

* no name reached via `core.` is one the engine merely imports, and
* no import in the engine is unused (`F401`, now enforced).

Together those two are the invariant. Either one alone can be satisfied by a
shim creeping back.

### Gates

870 tests: **869 passed, 1 skipped** - including the differential baseline's 41
pinned observations, which reproduced unchanged across the rewire and are the
reason this was safe to do mechanically at all. ruff (now with `F401` live on
the engine), black, mypy (38 files), `check_undefined`, `check_placeholders`,
`make_pot --check` (312 messages, unchanged).

---

## 24. Visualising conflicts (`wraithguard/viz/`)

The field-diff window can now say *where* mods collide, *how much* terrain a
plugin moved, and *which* navigation edges it rewired, as HTML pages generated
from data the tool already had.

### Why this was cheap

Nothing here required new reverse-engineering. §22 had already decoded VHGT to
absolute heights, VTEX, VCLR, WNAM and PGRD adjacency, and the conflict scanner
already keys id-less records by grid coordinates. The only missing piece was
drawing, which is why the whole package is pure functions from data to a string.

`merged_lands` (MIT, in the resource folder) confirmed the approach: it writes
per-cell conflict images with a green/yellow/red severity language. That
language is reused here deliberately -- matching a tool people already read
beats a nicer palette. The *jobs* differ, though, and the pages say so: it
merges land and answers "what did the merge do to your mod"; this tool sorts
and reports, so these answer "where do my mods collide and who wins".

### Design constraints, both inherited

**Self-contained.** No CDN, no external script. `generate_cell_map_html` holds
to this already and it is not aesthetic: the tool runs offline and ships frozen,
and a page that silently loses its script tag is worse than one that never had
it. This is the whole reason the 3D view is hand-rolled on a 2D canvas rather
than built on Three.js. A height *field* is a much smaller problem than general
3D -- a regular grid of quads sorts back-to-front exactly, with no depth buffer
and no camera library -- so it fits in the page.

**No f-string templates.** The cell map's generator is one 185-line f-string
that §5 of `REMAINING_WORK.md` flags as effectively uneditable. `viz/html.py`
assembles pages from small helpers that each escape their own input, so the
parts are individually testable and a plugin name cannot inject markup.

### The bug that only rendering could find

The severity ramp originally used a square root, reasoning that conflict counts
are heavily skewed and a few huge cells would otherwise flatten everyone else to
green. Sound reasoning; wrong conclusion. Rendered against a realistic spread it
did the *opposite* of the intent: 3 conflicts against a worst of 30 came out
yellow, so the entire map read as "everything is on fire" and the genuinely busy
region did not stand out at all.

The skew is real, but it belongs in the **scale**, not the curve: the ramp is
now linear and saturates at the 95th percentile, so one pathological cell clamps
instead of rescaling everybody. Both the old and new behaviours are now pinned
by tests.

This is worth recording as a method point. Every test in `test_viz.py` passed
while the map was unreadable, because "unreadable" is not a property any
assertion here was checking -- it was found by generating a page, rasterising
the SVG and looking at it. For visual output, rendering *is* part of
verification, and the tests that now guard it were written after the fact from
what the picture showed.

### The `_` shadowing hazard, now enforced

`_coords, before = _points_and_edges(...)` in the path-grid renderer started
life as `_, before = ...`, which rebinds the gettext marker and makes every
later `_("...")` in that function raise `TypeError`. This is the second time it
has cost a debugging round (the sort engine's `_rank` was the first), so it is
now pinned by `test_gettext_marker_is_never_shadowed_by_unpacking`.

Writing that test surfaced a distinction worth keeping: **comprehension targets
are not the bug.** `[name for name, _ in pairs]` appears seven times in shipped
code and is correct, because a comprehension has its own scope. The check
therefore flags `Assign` and `For` targets always, and comprehension targets
only when the comprehension itself calls `_()`. The first version of the test
flagged all seven and would have caused seven pointless edits.

The test was verified by injecting a real shadowing bug and confirming it failed
-- a negative control, because a checker that cannot fail is not a check.

### Cross-linking: an alternative map, not an overlay

The conflict map is a **parallel view over the same world grid**, and
`generate_cell_map_html` is left byte-for-byte unchanged.

The first attempt did modify it -- an optional `conflict_cells` set that marked
the affected cells in the existing SVG. That was reverted on the explicit point
that the cell map should stay as it is. The reasoning holds up independently:
coverage is much the larger set, so painting collisions onto it invites reading
a busy cell as a broken one, and the two questions stay clearer as two maps. The
conflict map links back to the cell map; nothing links forward, so the coverage
view has no new failure mode and no new parameter.

The two genuinely carry different data, which is why one cannot replace the
other: the cell map says *what touches a cell*, and this one says *what edits
the land record and path grid in that cell, and how those edits conflict*. The
page therefore breaks its counts down by record type and states what each type
governs -- terrain shape for `Landscape`, NPC navigation for `PathGrid` -- since
"12 conflicts here" does not distinguish two mods reshaping the same hillside
from two mods both placing a barrel.

### Gates

984 tests: 983 passed, 1 skipped. ruff, black, mypy (**46** files, up from 38),
`check_undefined`, `check_placeholders`, `make_pot --check` (393 messages, up from 312). The jump in test
count is mostly `test_standards.py`'s per-file parametrisation picking up six new
modules -- the conformance sweep applies to new code automatically, which is the
point of writing it that way.

---

## 25. Full-resolution cell pages, shared drawing, and an mtime cache

Three connected additions to `viz`, plus one security fix found on the way.

### One drawing implementation, two consumers

The terrain surface, height difference and nav graph were drawn inline in the
explorer's client. Adding a second place that draws them -- the dedicated
cell pages -- would have meant two copies destined to drift, the exact failure
the `viz` split exists to prevent. So the three draws were extracted into
`viz/draw_js.py` as `window.VizDraw`: pure functions over `(canvas, detail,
opts)` that render and return the numbers worth reporting, knowing no labels.
The explorer and the cell pages both call them; the client tests were updated
to load `DRAW_JS` alongside, and all pass unchanged, which is the evidence the
extraction preserved behaviour.

### The pages

`viz/cellpage.py` builds a standalone page per detailed cell: one cell, its own
document, **full resolution** (stride 1, where the explorer's embedded preview
decimates for drag smoothness). Reached from the explorer's cell-detail tab via
an "Open full-resolution page" link, written into the sidecar folder under
`pages/`. Generated at the same two points that already dump the JSON -- the
Check Conflicts scan and the Cell Map button -- so there is no third code path
and no new ordering to remember.

The surface view also draws the centre cell's neighbours as **border strips**:
a half-cell on each edge neighbour, a quarter on each corner, abutting the
centre at their true world extent. A border height that does not match the next
cell shows as a step -- a seam, a visible cliff in game. Only neighbours that
are themselves in the detail set are shown, because a non-conflict neighbour's
terrain would need a separate load-order-winner lookup, and the neighbours that
matter for a seam are the ones both mods edit, which are conflicts by
definition. Rendering it confirmed the seam reads: the centre draws smooth and
full-res, the mismatched neighbour floats a clear step above the shared edge.

### The cache

Decoding a cell is the slow part -- a tes3conv field lookup plus a VHGT
reconstruction -- and it was repeated on every open. `viz/cache.py` persists the
decoded per-cell JSON under the app directory, keyed on a `(name, mtime_ns,
size)` signature of the plugins that produced it. A cell is re-decoded only when
one of its plugins actually changed; everything else is served from disk. The
cache is **injected** into `collect_detail` (default: none), so the viz package
stays free of the filesystem and the tests drive it with a temp dir. The cache
key folds in the sampling stride, because the overview and the full-resolution
page decode the same cell to different data and must not collide.

### The security fix rendering forced

Writing the cell page's XSS test surfaced a real hole, not a test nicety.
`json.dumps` does not escape `<`, so a plugin literally named
`</script><script>...` -- an attacker-controlled filename on disk -- would close
an inline `<script>` block and inject arbitrary markup. Every page that inlines
JSON (explorer, cell page, world terrain) was affected. `html.script_json`
escapes `<`, `>`, `&` and the two Unicode line separators to their `\uXXXX`
forms, which is inert as HTML and byte-identical once parsed; it is now used at
every inline-JSON site. The `.js` sidecars loaded via `<script src>` are not
affected -- a script file is not parsed as HTML -- but the inline blocks were,
and are the ones that ship a filename into the page. Pinned by a regression
test that feeds a `</script>`-bearing plugin name through and asserts it cannot
break out.

### Gates

ruff, black, mypy (**53** files), `check_undefined`, `check_placeholders`,
`make_pot --check` (441 messages) all clean; every test group green. The suite
is run in groups now because the client tests spawn a Node process each, and
the whole suite in one invocation exceeds the sandbox's per-command timeout --
a harness limit, not a code one. Batching those into a single Node process is a
worthwhile later cleanup, noted rather than done.

---

## 26. Externalising the page assets, and holding back the world map

Two release-shaping changes, on the user's direction.

### The inline blob was undebuggable

Every generated page inlined its scripts and stylesheet into one HTML string.
The consequence, in the user's words, was that it "doesn't help with us trying
to do anything": a browser's dev tools saw one anonymous `<script>` thousands of
lines long, there was no file name to breakpoint against, and editing meant
finding the code inside a Python string literal. The HTML itself was unreadable
at ~110 KB.

The shared JavaScript and CSS are now written **once** into `<data>/assets/` and
every page references them with `<script src>` and `<link rel="stylesheet">`.
The explorer dropped from ~110 KB to ~6 KB; a cell page is a readable shell.
Those tags work from `file://` -- only `fetch()` is blocked there -- so nothing
about the offline guarantee changed. When no data folder is given (the tests,
one-off standalone pages) the assets are still inlined, so a self-contained page
is available on demand; the real app always writes a folder and so always gets
the debuggable form.

**No effect on the packaged binary**, which was the user's specific concern. The
JS/CSS still live as string constants inside the modules, so PyInstaller bundles
them exactly as before -- no `--add-data`, no new bundled data files. They are
merely *written out* at generation time rather than pasted into the markup, the
same mechanism the data sidecars already use. Assets are shared rather than
copied per page because the data folder already exists and a 10 KB `draw.js`
duplicated across sixty cell pages would be 600 KB of identical bytes.

### The world 3D map is held back, not deleted

The knitted world-terrain view works but its rendering needs another pass, and
it was blocking a shippable feature set. Its toggle is gated behind
`_WORLD_TERRAIN_TOGGLE = ""` in the explorer -- one string to restore -- and the
data collector (`collect_world_terrain`) and the client's `drawWorld` stay in
the tree, still tested. So the rest of the explorer ships now and the world map
is a one-line re-enable later, once the inline work has made it debuggable.

### The XSS fix from §25 earns its place here

Externalising moved the big constants out of the inline blocks, but the data
payload (`window.__viz = {...}`) is still inlined -- it has to be, it is
per-page. That payload carries plugin filenames, so `html.script_json` is still
what stands between a plugin named `</script>...` and a broken-out script tag.
The regression test guards it unchanged.

### Gates

ruff, black, mypy (**54** files), `check_undefined`, `check_placeholders`,
`make_pot --check` all clean; every test group green. A source-only development
snapshot was archived (5 MB zip, `tes3conv_json` and the `.7z` excluded) so this
work-in-progress can be revisited independently of the shippable set.

---

## 27. The freeze, the missing button, and the gray window -- one cause

A field report: the cell map "damn near froze" when it tried to fill in the 3D
map, sometimes rendered with no Conflicts button, and once showed a gray,
unpopulated 3D window. Three symptoms, one cause, plus a structural fix.

**The freeze was the world terrain.** The cell-map path called
`collect_world_terrain(conflicts, fields_for)` with a limit of **4000 cells** --
a tes3conv field lookup and a VHGT decode for every landscape cell in the load
order. On a real 989-plugin order that is minutes of work on the generation
thread. It is now **not called at all**: the 3D world map is held back (§26), so
decoding its data was pure waste.

**The missing button was the same work, in the wrong place.** The world decode
ran *before* the explorer was written and its filename returned, all inside one
`try`. When the decode was slow enough to look hung, or threw, the `except`
returned `""` and the cell map got no button. The generation is now **two-phase**:

* Phase 1, which the map waits for, writes only the sampled overview, the
  explorer HTML and the shared assets, then returns the button href. Measured at
  ~1.5 s on a synthetic 60-cell fixture.
* Phase 2, a background thread, decodes the full-resolution cells and writes
  their pages. Best-effort: if it is slow or fails, the map, the lists and the
  button are already on screen, and the client degrades a not-yet-written cell
  to its sampled view.

`write_sidecars` was made write-only-what-it-is-given so phase 2 can add
per-cell files and pages without clobbering the overview phase 1 wrote. Both
properties are pinned by tests.

**The gray window was the held-back feature showing through.** The world-terrain
toggle is gone (§26), so there is no empty 3D canvas to render gray. The
explorer no longer references `world.js` either -- requesting a file that is
deliberately not written would only 404.

### Gates

ruff, black, mypy (54 files), `check_undefined`, `check_placeholders`,
`make_pot --check` all clean; every test group green.

---

## 28. Audit after several days of solo work

A review pass over changes made without me, asked for as: update the docs and
tests, re-check typing and PEPs, find dead code and logic errors. Four real
defects, one large dead subsystem, and a test suite that had drifted behind a
deliberate API change.

### Two defects that stopped things working

**A syntax error had disabled the entire toolchain.** `conflictmap.py:198` put a
`★` escape inside an f-string *expression*, which is illegal before Python
3.12; this project targets 3.10. The module could not import, and because ruff,
black and mypy all parse before they check, **every gate was silently reporting
on an unparseable tree**. Fixed by hoisting the star to a local. Worth noting as
a process point: a green-looking gate run is not evidence when the parse failed
first.

**The cell-map path called two methods that no longer existed.**
`_detail_cache` and `_fill_cell_pages` were removed during the rework but
`wraithguard_toolkit_gui.py` still called them, inside a `try` whose `except`
returned `""`. That is precisely the "no Conflicts button on the map" symptom
reported repeatedly: an `AttributeError` swallowed and reported as absence. mypy
found both. Resolved by removing the orphaned generation path entirely -- the
cell map is coverage-only now, and conflicts are reached from the Conflicts
window, so the two maps no longer have an ordering dependency at all.

### Two defects that would have fired later

**`_("%s") % {"error": error}**` in the conflict-map failure dialog -- a
positional placeholder against a named dict, which raises `TypeError`. The error
path would itself have crashed, hiding the original error. `check_placeholders`
caught it; this is the second time that checker has paid for itself.

**A mutable dict as a class attribute** (`_SINGLES_KINDS`), now `ClassVar`,
since it is shared read-only configuration.

### The dead subsystem

The rework moved the Conflicts window from the heavy explorer to the direct
`build_conflict_map` page. That left **no live caller** for a large body of code:
`explorer.py`, `explorer_js.py`, `cellpage.py`, `sidecar.py`, `assets.py`,
`cache.py`, `draw_js.py`, and `collect_detail`/`cell_page_detail`/
`collect_world_terrain` in `detail.py` -- roughly 1,700 lines, reachable only
from each other and from tests. All of it is unwired: `viz/__init__` now exports
exactly the four page builders the GUI uses, and nothing imports the rest.

The world-3D LOD machinery goes with it, as intended -- there is no conflict-map
3D terrain any more, so `WORLD_SIDE`, `_world_patch` and the knitting code have
no purpose.

*At the time of that review the files could not be deleted from the environment,
so they stayed on disk while fully unreferenced. **They have since been
removed** -- see §28.1, which is now a record of what went rather than a list of
what to do.*

### The tests had drifted behind a real improvement

`build_height_delta` and `build_pathgrid_graph` were reworked from pairwise
(winner vs loser) to a **chain of edits** over a `surfaces` mapping. That is the
more correct model: Morrowind landscape records do not merge, so each plugin's
meaningful change is against whatever the plugin *before* it left, not against
the eventual winner. Eleven tests still asserted the old signature.

Rewritten to the chain API, and while doing so two assumptions of mine were
found wrong by the code rather than the reverse:

* A **single-plugin** path grid emits no chain payload at all and falls back to
  a plain server-rendered graph. That is correct -- there is nothing to diff --
  and the test now asserts that fallback instead of a payload.
* Assertions on embedded JSON must **decode** it. `html.script_json` escapes
  `<`, `>` and `&` to `\uXXXX` (the XSS fix from §25), so substring checks
  against raw text fail misleadingly. Tests now parse the payload out and assert
  on data, via a shared `payload_of` helper.

### Gates

**1079 passed, 1 skipped.** ruff, black, mypy (54 files), `check_undefined`,
`check_placeholders`, `make_pot --check` (456 messages) all clean. A structural
check that every `self.<method>()` call in the GUI resolves to a definition on
`App` or a mixin found only one hit, and it was a false positive (an assigned
callback attribute, not a method).

### §28.1 Files deleted

Fully unreferenced after that pass, and **since removed**. Verified by the
module audit: none of these paths exists any more, and nothing imports them.

```
wraithguard/viz/explorer.py
wraithguard/viz/explorer_js.py
wraithguard/viz/cellpage.py
wraithguard/viz/sidecar.py
wraithguard/viz/assets.py
wraithguard/viz/cache.py
wraithguard/viz/draw_js.py
wraithguard/viz/detail.py
tests/test_viz_client.py
```

`detail.py` was included because its three public functions were the explorer's
and the cell pages' data layer; the four surviving page builders take their
`surfaces` mappings straight from the GUI's own field lookup. `pyproject.toml`
needed no change, since its package list names packages rather than modules.

The live `viz` surface is now:

| Module | Purpose |
|---|---|
| `conflictmap.py` | The 2D conflict map, with per-plugin focus and instant tooltips |
| `heightdelta.py` | Terrain height as a chain of per-plugin edits |
| `pathgrid.py` | Navigation graph, chained the same way |
| `terrain3d.py` | One cell as a rotatable/pannable surface |
| `geometry.py` | Grid-id parsing and per-cell aggregation |
| `palette.py` | Severity and divergence color ramps |
| `html.py` | Shared page shell, escaping, safe inline JSON |

---

## §29 The cell map leaves the engine, and the housekeeping it needed

Five requests landed together, and they turned out to be one piece of work: the
cell map could not sensibly grow scrolling, timestamps or a wider palette while
it was a 216-line f-string in the middle of the sort engine.

### The extraction

`generate_cell_map_html` is now `wraithguard/viz/cellmap.py`, assembled from ten
functions that each return a fragment - `_escape`, `_anchor`, `_modattr`,
`_in_bounds`, `_focus_options`, `_svg_grid`, `_exterior_rows`, `_interior_rows`,
`_legend` - plus the builder that composes them. CSS and JS moved to
`wraithguard/viz/cellmap_js.py` as plain `Final[str]` constants, which is the
whole point: no interpolation means no doubled braces, so the JS can be read and
edited as JS.

`wraithguard_toolkit.py` keeps the public name as a one-line delegation, so nothing
that imports it had to change. The dead `_cell_heat` went with it.

Two things fell out of the split that were not visible before:

* The dropped `explorer_href` parameter - a leftover of the cross-link that §27
  removed. Nothing passed it.
* Out-of-range cells were being filtered **silently**. One corrupt grid
  coordinate stretches the SVG to millions of pixels, so dropping them is right,
  but the page now says how many were dropped. A quietly incomplete map is worse
  than a noisy one.

### The requested changes

* **Scrolling**, matching the cell map's own panes: `.mapwrap` and `.listwrap`,
  both `overflow:auto` with `resize:vertical` so a pane can be dragged taller.
  Added to the conflict map too, which is where the request started.
* **A generated timestamp** on the cell map, and on the shared shell in
  `viz/html.py:page()` via a new `generated_at` argument. These files accumulate;
  an hour-old map that looks identical to a fresh one is a real trap. The
  argument defaults to now and is injectable, which is also what makes the
  header assertable in a test.
* **Wider palettes.** Severity went from three stops to five: with only
  green → yellow → red the entire middle of a busy map collapsed into one narrow
  yellow band. Coverage got its own seven-stop ramp - slate → blue → periwinkle
  → violet → amber - deliberately *not* green-to-red, because coverage is not
  badness (ten mods touching a cell is normal) and it must not be mistaken for
  the conflict map at a glance.

  Both legends are now generated from the ramp they sit beside
  (`coverage_legend_stops`), and the conflict map's client-side redraw is fed the
  stop table as data (`severity_stops`) rather than re-implementing the curve in
  JavaScript. The old arrangement had a hand-written legend of five fixed
  swatches next to a map with its own hard-coded five; keeping them in step was
  manual, which is to say it was not kept.

### One regression, caught by an existing test

Expanding the severity ramp broke `test_severity_is_monotonic`: the new orange
shoulder was *brighter* in red than the final red, so over the last quarter of
the ramp more conflicts rendered with less red. The test is right and the ramp
was wrong - "more conflicts never renders cooler" is the property the map is
read against - so the shoulder's red was moved just below the endpoint's. The
hue is unchanged; only the channel ordering is.

The coverage ramp cannot hold that same invariant, because it rotates through
hues and its first segment (desaturated slate to saturated blue) genuinely goes
cooler. Its invariant is **monotonically increasing luminance**, which is the
standard criterion for a sequential ramp and keeps it ordered in greyscale and
for a color-blind reader. That is what the new test asserts, with the reasoning
in its docstring rather than in a commit message.

### Housekeeping

`wraithguard/viz/housekeeping.py`, wired to a **Tidy old HTML views** checkbox on
the main window (default on, persisted as `cleanup_html`), run from `_on_close`.
Two properties matter more than the tidying:

* **It only ever deletes files this tool wrote.** A candidate must match one of
  the six known filename stems *and* carry a `_YYYYmmdd_HHMMSS` suffix. Never a
  blanket `*.html` sweep, and specifically never an un-timestamped
  `conflict_map.html`, which is a file the user named themselves via *Save*.
  Sorting is by the timestamp *in the filename*, not mtime, which a copy or a
  sync tool rewrites.
* **Off means off.** With the box unchecked nothing is removed at all. A tool
  that quietly deletes output someone meant to keep is worse than a cluttered
  folder.

A page's sidecar `_data` folder goes with it, since the folder is useless
without the page that references it. A locked file (open in a viewer) is skipped
rather than reported, and - checked by test - is not claimed as removed in the
log line.

### Gates

ruff, black, mypy (49 source files), `check_undefined`, `check_placeholders`,
`make_pot --check` (419 messages) all clean. Full suite green, coverage gate
met; `wraithguard/viz` is at **97%** (branch), with `cellmap.py`,
`cellmap_js.py`, `conflictmap.py` and `html.py` fully covered.

`tests/test_viz_pages.py` adds 90 tests over the cell map, its client assets,
housekeeping and the new ramps. Five of them were then verified by **injecting
the real defect** and confirming a red test: a non-monotonic ramp, a legend
hard-coded away from the map, an undelimited mod-filter token, a matcher that no
longer requires a timestamp (which makes the user's *Save* output a deletion
candidate), and escaping switched off. All five were caught.

Two of my own assumptions were wrong and the code corrected them, which is worth
recording as it is the same pattern as §28:

* A test asserting no `<title>` anywhere in the body failed on the *comment* in
  `CELLMAP_JS` explaining why native SVG tooltips are not used. The assertion
  now scopes itself to the SVG element, which is what it meant.
* The coverage ramp's red channel is not monotonic, and asserting that it was
  would have been asserting a property the design does not have. See above.

---

## §30 Help, banding, the format schema, and a wall of widgets

Four requests, one release. Three were small; the fourth turned out to be the
largest single piece of reference data this project carries.

### The wall of widgets

`_build_controls` was 435 lines of flat widget construction. Nothing in it was
*hard* -- that was the problem. A one-line change meant an edit in the middle of
a wall of text with no landmarks, and the request that prompted this ("break up
anything that makes changes harder") named exactly that feeling.

It is now `_build_controls` (34 lines, which assigns the row numbers in one
place so the vertical order stays readable) delegating to `_build_dnd_note`,
`_build_input_fields`, `_build_output_fields`, `_build_options_panel` ->
`_build_write_options` / `_build_scan_options`, and `_build_action_bar` ->
`_build_primary_actions` / `_build_tool_actions`. The button helper became a
module-level `_action_button`, because a closure two builders have to share is a
closure that belongs outside both.

The proof it worked is the next item: adding the Help button was two lines in
one small method.

One defect surfaced during the split -- a tooltip attached to a widget built in
a different method, which mypy caught as an undefined name. That is the failure
mode this kind of refactor has, and the reason the gate list runs after every
step rather than at the end.

### Help, rendered rather than handed off

`wraithguard/viz/docs.py` renders the project's own Markdown to a
self-contained page: no CDN, no external stylesheet, **no JavaScript at all**.
Opening the `.md` with the operating system was considered and rejected -- what
opens is whatever happens to be associated with `.md` on that machine, which is
not a reading experience anyone would choose.

The Markdown subset is deliberately small and defined by what these documents
actually use, verified against them. Anything unrecognised is emitted as escaped
text rather than guessed at, so an unsupported construct is visibly plain rather
than silently mangled. All input is escaped and link schemes are vetted: the
documents are ours, but a renderer that emits whatever URL it is handed cannot
safely be pointed at anything else later.

Two things the tests caught that reading would not have:

* `*Export writes nothing while **Dry run** is checked*` -- italics wrapping
  bold. A shared `[*_]` delimiter closed the italic on the first asterisk of
  **`Dry`, leaving stray asterisks on the page. The two emphasis alternatives
  are now spelled out separately with lookarounds on both ends.
* An unterminated fence used to swallow the rest of the file. It now runs to the
  end and renders: most of a document beats refusing to show any of it.

`doc_path` looks in the PyInstaller bundle **first**, then beside the executable,
then the source tree -- in that order, because a folder next to a shipped .exe
may hold an older copy someone extracted by hand. Both documents were added to
the build's data list; without that the frozen build would have had a Help button
with nothing behind it.

### Banding the cell map

Counts 1-5 each get a band; above that they group in fives. The reasoning is that
the distinctions people act on are crowded at the bottom -- one, two and three
mods in a cell are different situations, 23 and 24 are not -- and a continuous
ramp normalised against the busiest cell on a big map spent most of its color on
distinctions nobody needs, rendering all the low counts as the same dark blue.

Above sixteen bands the top one goes open-ended (`76+`). Forty bands over a
seven-stop ramp is a gradient again, with a legend nobody can use.

The legend is now one row per band rather than a six-point sample, which is the
honest thing once the map is quantised: it is the map's *key*, and a sampled key
beside a banded map would be a key that lies.

### The format schema

`tools/gen_tes3_schema.py` reads a CSV export of UESP's *Morrowind Mod File
Format* pages into `wraithguard/tes3fields/schema.py`: **46 record types, 313
subrecords, 62 with parsed struct layouts.** (`BODY` was missing from the first
export and was added to it afterwards, which closed the last gap: every record
type tes3conv can emit now resolves to a documented one, and the test asserts
that the set of gaps is *empty* rather than tolerating a list.) The hand-written shapes live in
`schema_types.py` so the generated file can be overwritten wholesale without
taking any behaviour with it.

This is documentation, not code. What is taken are format facts -- a `NPDT` is 12
or 52 bytes, its first two a uint16 Level -- which describe Bethesda's file
format rather than anyone's implementation of it. `CREDITS.md` carries the
attribution.

**How we know the parse is right.** 56 of the parsed layouts have a plainly
stated byte count, and every one of the 56 now equals the sum of its parsed
members. That check found four real defects, each of which had silently dropped
data:

* a full-width **note row** between a table's header and its fields ended the
  table early -- which cost the AI package fields and every `INFO` field their
  entire entry;
* the first Info line was taken as a description unconditionally, but `CELL`'s
  `DATA` opens straight onto `uint32 - Flags`, so that field lost its first
  member and came up four bytes short;
* `float` was not recognised as `float32`, dropping `CELL`'s fog density;
* `CLAS` writes `uint32 = Flags` where it means `-`, a typo in the source that
  cost four bytes of a sixty-byte struct.

A fifth was caught by a *test* rather than by the size check: several pages carry
a second four-column table after the subrecord one (the magic-effect list, the
GMST value list), and its rows were being read as subrecords. The schema
contained `Subrecord(name="Jump", cardinality="9")`. Requiring a subrecord tag to
be short and upper case removed 194 such rows.

Where the tables hedge, the schema hedges. `NPDT` has two documented layouts
under one tag, so it carries **neither**: which applies depends on a flag
elsewhere in the record, and running the two together would mis-read every NPC
in the game. `LAND`'s vertex normals document one three-byte element and declare
12,675 bytes, so the element layout is kept and the multiple (4,225) recorded
beside it.

### Using it

The diff window now labels a field in the file format's own terms -- `VHGT -
Height Data (struct, 4,232 bytes, optional)` -- and offers a **Format reference**
view of the whole record type.

Two deliberate omissions:

* **No struct decoding.** tes3conv already expands struct subrecords into JSON,
  so re-deriving them from bytes would add a second, worse answer to a question
  already answered. The blobs that arrive *unexpanded* are the compressed ones,
  and those decoders already exist.
* **No guessed key mapping.** tes3conv's JSON key names are its own invention and
  nothing states the correspondence to subrecord tags, so the mapping is written
  out by hand and only for the record types whose JSON this project has actually
  read. An unmapped key is simply not annotated. `LandscapeTexture` does not
  shorten to `LTEX` by any rule a computer would find, and `Header` to `TES3`
  least of all -- a heuristic here would be wrong quietly.

### MWSE functions

`customfunctions.dat` adds **360 opcodes** the base game has no equivalent for,
so an MWSE-scripted mod disassembles instead of coming out as raw bytes. Calls to
them are marked in the listing, because a script using one will not run at all
without that runtime.

The licence position is stated fully in `CREDITS.md` and is worth repeating here:
that file is a **data file in MWEdit's own text format**, installed by running
the MWSE updater rather than part of the MWSE source tree. No MWSE source is
read.

It spells parameter types symbolically (`Long | String`) where `Functions.dat`
uses hex flag words, and the mapping was **derived, not copied**: the two files
describe 106 of the same functions, and correlating those pins each symbolic name
to exactly one bit value -- every name resolved unambiguously, and the result
matches the `FLAG_*` constants already taken from MWEdit's MIT header, which is
the check that the derivation is right.

Where the two disagree (two renames, 26 differing operand shapes) the existing
MWEdit-derived entry is **kept**, because that is the one the corpus and the test
suite have been run against. The generator prints every disagreement rather than
resolving it silently.

### Gates

ruff, black, mypy (54 source files), `check_undefined`, `check_placeholders`,
`make_pot --check` (431 messages) all clean. **1,260 passed, 1 skipped.**

`make_pot` earned its place again: it flagged `_(label)` in the new Help menu --
a non-literal the extractor cannot see, so those two menu entries could never
have been translated. The labels moved to literal calls at the call site.

Five of the new tests were verified by **injecting the real defect** and
confirming a red test: escaping switched off, `javascript:` links allowed, a
scalar type dropped from the schema parser, a record-type mapping broken, and the
banding grouped by ten instead of five. All five were caught.

### §30.1 A blank window, and the row that moved

Two defects in the §30 work, both found by running it rather than reading it.

**The format-reference window opened blank.** `DARK["entry_bg"]` -- there is no
such key; the palette calls it `log_bg`. The lookup ran *after* the `Toplevel`
was created and before the text widget was packed, so Tk showed an empty window
and put the `KeyError` on stderr, where nobody was looking. Blank windows are a
miserable thing to debug, and this one was reported as "it does nothing".

The fix is one word. The guard is
`test_standards.py::test_gui_palette_lookups_all_resolve`, which walks every
first-party source for `DARK["..."]` and checks the key against the dict literal
parsed out of `theme.py` (parsed, not imported: the hermetic suite has no Tk).
Verified by putting the bad key back and watching it fail with the file and key
named. This is the cheapest available guard against a whole class of GUI defect
the suite otherwise cannot reach at all, since the GUI has no automated coverage.

**Rows collided in the controls panel.** When `_build_controls` was split, the
output-fields body moved *verbatim* -- and it still carried the absolute offsets
`start_row + 3 .. + 6` from when there was one shared base row. Calling it with
`start_row + 3` therefore placed it on rows 6-9, on top of the rule-files panel,
the options box and the action bar. Some rows simply did not appear.

Fixed by making each builder's parameter mean what it says -- "the first row I
use" -- so the panel now uses `+0..+3` internally and the caller passes
`start_row + 3`. Absolute rows are unchanged from before the split (inputs 0-2,
outputs 3-6, rules 7, options 8, actions 9), which is the property that was
checked afterwards.

Worth recording as a *method* failure rather than a typo: moving a body verbatim
is the safe way to split a function, but it is only safe if the body has no
implicit relationship to its old surroundings. Row offsets computed from a
shared base are exactly such a relationship, and nothing in the gate list can see
it -- ruff, mypy and the test suite were all green with the panels stacked on top
of each other.

### §30.2 The 28 disagreements, itemised

"Two renames and 26 differing operand shapes" was too compressed to be useful,
and the interesting part was hiding inside the summary. The generator prints all
28 on every run; here is what they are.

**The renames** are cosmetic -- same opcode, different label:
`0x3F0D` `XDrop`/`XDropItem` and `0x3F0E` `XEquip`/`XEquipItem`.

**25 of the 26 operand differences are the same difference**: MWEdit says `0x10`
(String) where `customfunctions.dat` says `0x14` (Long | String), on the first
parameter of the `XFile*` family and a handful of others.

That is not cosmetic. The decoder checks fixed widths first, so `0x14` reads a
**4-byte long** while `0x10` reads a **length-prefixed string**. MWSE means
"either, depending on what the script passed", which no single flag word can
express to a byte-walker -- so the table has to pick one. Two reasons to keep
`0x10`: UESP's per-function pages document these parameters as strings, and the
string path is guarded by `_plausible_identifier`, so a wrong guess is *detected*
and degrades to an honest raw span, while a wrong long read silently consumes
four bytes and desynchronises everything after it. Asymmetric costs, so prefer
the checkable branch.

**The 26th was a real defect, and is now corrected.** `0x3C33`
`XFileWriteFloat` had a single float operand in MWEdit's table and no filename.
Three things agree against it: `customfunctions.dat` lists two parameters, UESP
documents the syntax as `xFileWriteFloat filename (string), value (float)`, and
its three siblings (`XFileWriteShort`/`Long`/`String`) all take the filename
first. Uncorrected, every call to it decodes one operand short and desyncs the
rest of the stream.

Worth noting how the defect was found, since it was not by reading: the
disagreement report exists precisely because two sources describing the same 106
functions is a free consistency check, and this was the one difference in the 28
that was not explainable as a naming or ambiguity choice. The other 25 look
identical in a summary line -- "operand shapes differ" -- which is why summarising
them was the mistake.

The fix is a new `CORRECTIONS` table in `tools/gen_opcodes.py` -- deliberately
tiny and individually justified, because a generator that quietly "improves" its
inputs is one nobody can check. It prints what it corrected, and
`tests/test_mwscript.py` pins both the entry and the family-wide symmetry that
gave the omission away. Verified by reverting the correction and watching both
tests fail.

This also revised the stated reason for the keep-existing rule. "The existing
entry is the one the corpus was run against" is true for the vanilla range and
**false for the MWSE range** -- CREDITS records that no MWSE-only function ever
appeared in the corpus, so those entries had never been validated by anything.
The rule survives on the `_plausible_identifier` argument above, which is a
better reason than the one originally given.

---

## §31 Clearing the remaining-work list

Four items, in the order they were worth doing rather than the order the list
gave them.

### A headless Tk smoke job -- the one that mattered

The GUI cannot be imported without Tk, so it is excluded from the hermetic suite
*and* from mypy, and its verification has been a manual `SMOKE_TEST.md` run.
That is not an abstract gap: the last two defects to reach a user were both in
the GUI, and both invisible to ruff, mypy and twelve hundred passing tests --
a window that opened blank (§30.1) and two panels gridded on top of each other
(§30.1 again).

Neither needed a human to spot. Both needed a *display*. `tests/test_gui_smoke.py`
builds the real application on a virtual X server and checks what a person would
otherwise have to look for: that every action button exists, is bound, and starts
in its documented state; that no two widgets are gridded into the same cell; that
the control rows are consecutive; and that each window the app opens comes up
with content in it.

Two details are the point rather than the decoration:

* **The module skips when Tk or a display is missing**, so the hermetic suite is
  unaffected -- but a skip means "not checked", so the CI job greps its own
  output and *fails* if anything skipped. Otherwise a missing `python3-tk` would
  turn the whole job green while verifying nothing, which is worse than not
  having it.
* **The collision check was verified against the real defect.** Tk is not
  available in the environment this was written in, so the grid-collision maths
  was exercised directly against two fake layouts: a clean one (no collisions
  reported) and the exact shape of the §30.1 bug -- a four-row panel placed three
  rows too low, over the rules panel, the options box and the action bar. It
  reports nine colliding cells. A check that has never seen a failure is a check
  nobody should trust.

Stated plainly: **the smoke job itself has not run yet.** It could not be
executed here, and CI is its first real run.

### Type-checking imports

76 annotation-only imports (73 `TC003`, 3 `TC001`) moved under `TYPE_CHECKING`,
and `TC` added to ruff's `select` so new ones cannot creep back.

Ruff calls this fix "unsafe" generically, because moving an import under
`TYPE_CHECKING` breaks any annotation evaluated at runtime. It is safe *here*,
and the reason is checkable rather than assumed: every module carries
`from __future__ import annotations` (PEP 563), so every annotation is a string,
and nothing in the project introspects them -- verified, no `get_type_hints`, no
`__annotations__` reads anywhere.

Verification was not "the tests still pass": every first-party module was
imported in a fresh interpreter, because a TC move that breaks a module at
*import* time can still leave a test suite green if nothing imports it on the
tested path. 53 modules, no failures, and `--help` still runs.

### lint_plugins

201 lines -> 88, plus an 89-line per-plugin walk that dispatches to seven small
checkers: `_lint_expansion_calls`, `_lint_masters`, `_lint_header_gaps`,
`_lint_evil_gmst`, `_lint_cell`, `_lint_interior_pathgrid` and
`_lint_twin_warnings`.

Two of the checks are load-order-wide rather than per-plugin -- whether an
interior cell has a path grid *anywhere*, and which plugin introduced it -- so
those two accumulators are passed into the walk explicitly and documented as
such, rather than being hidden in a closure.

The refactor was pinned the same way the earlier ones were: a probe builds one
synthetic load order that trips **every** branch at once (evil GMST beside a
legitimately changed one, fog bug beside a healthy cell beside a
behave-like-exterior exemption, a cell with no path grid, Tribunal and Bloodmoon
calls with no matching master, a blank header, an orphaned `.omwscripts` twin),
captures all 11 warnings plus the stats, and the same probe was run after. The
JSON is byte-identical. That is a stronger statement than the suite alone, which
touches those branches one at a time.

### The CI matrix

`requires-python = ">=3.10"` while CI ran 3.10 and 3.13 only. Testing the two
ends was a reasonable economy while there was no version-conditional code, but
it was an assumption about 3.11 and 3.12, and the promise is made to anyone who
installs this. Four short jobs are cheap.

---

## §32 A user report: "it toggles on ALL mods"

> *"Is it normal for it to toggle on ALL mods, including mods flagged as grass
> mods and mods that were already disabled via one of the modding-openmw
> lists?"*

Two claims in one sentence, and they have different answers. Worth recording
because the first-pass explanation -- "the scan can't tell the difference" --
was right about one and wrong about the other, in a way that reading the code
casually would not reveal.

### The disabled mods: not us

`read_cfg` matches `^\s*content\s*=`, so `#content=Foo.esp` and
`# content=Foo.esp` do not match, verified by feeding it both. `build_and_sort`
has no concept of enabled or disabled: it takes a flat list and returns every
entry. A plugin a curated list leaves inactive is simply *not in* `content=`, so
nothing in the sort path can bring it back.

The only route back is `scan_mod_directories`, which does `os.walk` and takes
every plugin under the tree with no filter of any kind. Point it at a shared
mods folder and it picks up everything that mod manager holds, including the
things deliberately left off. That is the documented behaviour of a folder scan,
and the answer is to scan a narrower folder or hand-edit the exported subset.

### The grass mods: ours

This one was a real defect, and the give-away is that **the tool already had the
information**. It reads the cfg. The cfg declares grass on `groundcover=` lines.
It read those lines and ignored them.

Reproduced end to end:

```
content=Morrowind.esm
content=Patch for Purists.esp
content=MyNewQuest.esp
content=Remiros_Groundcover.esp     <-- inserted by us
groundcover=Remiros_Groundcover.esp <-- already there, untouched
```

Declared twice. OpenMW then loads the grass through the groundcover system *and*
spawns every blade as a real object -- exactly the cost groundcover exists to
avoid, arriving silently. The emitted TOML did the same thing, so going through
momw-configurator did not avoid it either.

### The fix, and the line it does not cross

`read_groundcover_names` parses the lines that were already being read;
`hold_back_groundcover` drops those plugins from the subset before sorting, so
they reach neither the cfg nor the TOML. The run prints what it held back and
why.

Three deliberate limits:

* **The data= path is still written.** OpenMW must be able to find the file for
  the `groundcover=` line to work at all, so dropping the data entry would break
  the mod this check exists to protect.
* **The `groundcover=` lines are never touched.** The mod stays enabled, as
  grass, which is what it was.
* **No filename heuristics.** The obvious shortcut -- hold back anything
  matching `*grass*` or `*groundcover*` -- is wrong, and the project's own
  sample cfg proves it: `deleted_groundcover.omwaddon` is ordinary content whose
  name says grass. A pattern would silently drop a plugin the user wants. The
  rule is "what your cfg declares", and nothing else.

`read_groundcover_names` was added as a separate function rather than a sixth
return value from `read_cfg`, because seven call sites unpack that five-tuple
positionally and none of them want this.

### Verification

Eight synthetic cases in `test_hardening.py` covering the rule, the case
folding, the empty case and the end-to-end export; four more in
`test_integration.py` against the real 687-plugin sample cfg, which has 23
`groundcover=` lines and the `deleted_groundcover.omwaddon` trap. Confirmed by
reverting the fix and watching the end-to-end test fail with "grass was inserted
as content".

### What this says about the gap

The lint checks read plugins for problems inside them. Nothing checked the
*shape of the output* against the rest of the user's cfg -- and this defect
lived entirely there. The natural home for a check like "a plugin must not be
declared two ways at once" is the export path, not the linter, and there is
currently no such stage.

---

## §33 Three follow-ups from the grass report

### The TOML now uses `insertBlock`

A run of consecutive custom plugins is one block on one anchor, rather than one
`insert` per plugin chained on its predecessor. Read from momw-configurator's
`doInsert`: the prefix comes from the *anchor* line, and block lines are
inserted in order with `destIdx++`, so the placement is identical.

The reason it is worth doing is not brevity. Anchors are matched with
`strings.Contains` against whole lines, and more than one match makes the Go
code return a nil cfg -- the whole rebuild is abandoned. Chaining anchored every
plugin on the *previously inserted plugin name*, so each one was another chance
to hit that.

**The first version of the claim was wrong, and the harness caught it.** I
asserted the collision came from two inserted names interfering, wrote a test
that passed, and only found it was passing for the wrong reason when checking it
properly. The real construction is narrower: an inserted name has to be a
substring of a line **already in the cfg**. `Wares.esp` inserted into a list that
ships `Better Wares.esp` aborts the run; that is an ordinary Morrowind pairing.
A third test pins the limit honestly -- if the *first* anchor is itself
ambiguous, both forms fail identically. `insertBlock` removes the additional
exposure from chaining, not the exposure itself, and the tests say so, so nobody
later reads it as a cure.

Anchor choice now uses that: `after` the preceding line when it is unique, else
`before` the following one (same placement, second chance at a unique line),
else the natural anchor with the existing ambiguity warning. Silently dropping
the insert would be worse than a rebuild that stops and says why.

**What this exposed.** The differential baseline passed unchanged, which looked
like reassurance and was not: it pinned `toml_value` and a *checked-in* TOML, not
anything the emitter generates. An emitter change could have rewritten every
user's customizations file with the suite fully green. `cfg.emit_customizations_toml`
now pins the output over five shapes, and the first negative control on it
**missed** -- because `.replace(..., 1)` mutated the data-insert branch, which
those cases do not exercise. Retargeted at the content path, it caught it. Worth
recording: the first "MISSED" was the test being wrong, not the baseline.

### Removals are for what we do not own

`removeContent`/`removeData` were emitted for anything opted out that was already
in openmw.cfg -- which is every one of the user's own mods the moment they have
exported once. The Configurator rebuilds from the curated list plus these
customizations, so a mod we stop inserting is already absent; the block did
nothing but clutter a hand-edited file.

The rule is now "does the curated list own this", via `plugins_needing_removal`
and `data_paths_needing_removal`. Both were pulled out as functions because the
alternative was a test that had to build two-thirds of a plan dict to reach four
lines of decision.

Data paths were **not** covered by the first pass and had to be asked about --
a reminder that "a mod" is a plugin *and* a folder, and fixing half of that is
fixing none of it from the user's side. Ownership there is "is it one of this
run's inserts", from the subset or from the source TOML, since the emitted file
replaces that one wholesale.

Empty is **unknown**, not "nothing is curated": without a `plugin-order.yml`
there is no curated set at all, so presence stays the fallback. Guessing the
other way would leave a plugin enabled that the user asked to disable.

### Declaring your own grass

§32 held back what the cfg already declares. That only helps a mod already
installed *and* declared -- a newly added grass mod is in neither place, so
there is nothing to read the fact off.

It can now be declared, in whichever form fits: a `groundcover=X.esp` line in a
subset file (deliberately the same spelling openmw.cfg uses, so the line means
what it looks like), `groundcover = [...]` in the TOML form, `--groundcover`, or
the **Declare as groundcover** field in Options. The declaration keeps the plugin
out of `content=` and writes the `groundcover=` line in both outputs -- as an
`append` entry in the TOML, which is how the Configurator writes one, verified by
running the emitted file through `simulate_configurator_apply` and watching the
groundcover section appear.

Two details that are the whole point:

* **The data path still goes in.** OpenMW has to find the file for the
  groundcover line to mean anything, so the folder is inserted through the
  ordinary data path -- a `data=` entry is not grass-specific. This is asserted
  in the end-to-end test rather than left implied.
* **On direct cfg write the new lines are appended**, not spliced into an
  existing groundcover section. Appending cannot shift any index, and every
  `content=`/`data=` position in the write segments is an index into those same
  lines. Placement does not matter to OpenMW; only the order of groundcover lines
  relative to each other does, and appending preserves it.

### Gates

ruff, black, mypy (54 files), `check_undefined`, `check_placeholders`,
`make_pot --check` (438 messages). **1,330 passed, 2 skipped.** Four negative
controls, all caught: data removal ignoring ownership, a declared grass plugin
also becoming content, the old presence-based content rule, and a reversed
`insertBlock` body against the new baseline key.

---

## §34 The rule maker, an audit, and a TOML that could not be applied

Six pieces of work, reviewed together because four of them were found by the
previous one.

### The rule model (`wraithguard/rules/authoring.py`)

The rule maker wrote three kinds of rule out of seven and validated almost
nothing. It now covers the whole vocabulary -- `[Order]`, `[NearStart]`,
`[NearEnd]`, `[Note]`, `[Requires]`, `[Conflict]`, `[Patch]`, the `ALL`/`ANY`/
`NOT` expression tree, the `DESC`/`SIZE`/`VER` predicates, filename expansion,
`(Ref:)` citations, `@Section` headings and the `!`/`!!`/`!!!` marks.

The design decision worth recording is **where validation lives**. mlox
discards a rule it cannot use *without saying so*, which makes the moment of
writing the only opportunity anyone has to find out. So the model refuses at
authoring time rather than warning afterwards, and the GUI's write button is
driven by the validator rather than by its own opinion.

The rendered rule is fed back through this project's own parser -- the one that
reads `mlox_base.txt` -- and has to come back as the rule that went in. String
comparison cannot find a renderer that emits something valid-looking but
unparseable; a round trip can.

### Deriving rules from what we scanned (`rules/derive.py`)

Two tiers, kept apart deliberately. A **fact** comes out of a plugin header --
if `B.esp` masters `A.esp` then `A` loads first, and that is read from the file
rather than inferred. A **candidate** comes from observed conflicts, which is
evidence of a relationship without saying which relationship: two mods editing
one cell might want ordering either way, or might be incompatible.

A tool that presented both the same way would be inviting someone to
rubber-stamp guesses, and a wrong rule in a load-order file is worse than no
rule -- it is a wrong answer that looks researched. Candidates therefore carry
their evidence, propose the reading the current order implies, and offer the
alternatives.

### `[Patch]` was never evaluated

`predicates.py` handled Conflict, Requires and Note and silently skipped Patch
-- 267 such rules in `mlox_base.txt`, 32 in `mlox_user.txt`, all inert. Now
implemented, and it fires both ways: a patch present without what it patches,
and the originals present without their patch. On real data it adds exactly two
genuine warnings, which is the right order of magnitude for a rule type that
only speaks when something is actually missing.

### §34.1 The audit: six defects, five of them in this release's own code

Auditing new work rather than only reviewing it is the point; five of the six
were written during 3.1.

1. **A leading space swallowed a plugin.** mlox reads an indented line as
   message text, so a name typed with a leading space vanished from the rule --
   and the rule still loaded, still looked right, and simply did not apply to
   that plugin. Verified against the real loader before fixing.
2. **`table()` could drop rows** (`viz/html.py`). It paired rows with attributes
   using `zip`, which stops at the shorter list, and the short list is the
   attributes -- so a caller one attribute shy lost a *table row*. The project's
   blanket `B905` exemption claims every `zip()` was reviewed individually; this
   one had not been.
3. **`plugins` as a string produced ten proposals about single letters**
   (`derive.py`). A bare string is iterable. That module exists to keep guesses
   from being presented as facts, so confident nonsense is the one output it
   must never produce.
4. **`@@Section`**, because the field is labelled `@section:` and the guidelines
   write `@Name`, so typing the `@` -- the natural thing -- doubled it.
5. **An out-of-range priority rendered no mark**, silently, which is not what
   asking for one means.
6. **A dead `_REF` regex**, defined and never used.

Negative controls: five mutations, four caught immediately, **one missed** --
the `table()` fix had shipped without a test. A test was added and it then
caught. That is the control doing its job on the auditor.

### §34.2 The generated TOML: bloated, and in one case fatal

Found in a real user's file: 389 insert entries over 2,229 lines, one of its
anchors fatal to the whole rebuild.

* **`data=` inserts never got the `insertBlock` treatment.** That change landed
  for `content=` only. 372 data-path inserts, 152 of them sharing one anchor,
  where 39 block entries would do.
* **Data anchors were never checked for uniqueness.** The Configurator matches
  anchors with `strings.Contains` against whole lines and treats more than one
  match as **fatal** -- it returns a nil cfg and nothing is applied.
  `_anchor_is_unique` existed but was wired only into the content path. In the
  reported file `...\UvirithsLegacy\Data Files` was chosen while
  `...\UvirithsLegacy\Data Files\Addons` was also a real line.

  The fix **widens** rather than gives up: an ambiguous value is retried as its
  whole cfg line, which is very often unique where the value was not, because
  the line carries delimiters the value lacks. `data="...\Data Files"` is not a
  substring of `data="...\Data Files\Addons"` -- the closing quote ends it. The
  same widening resolves a long-standing content-side case: `content=Wares.esp`
  is not a substring of `content=Better Wares.esp`.
* **The `after` reversal had to go with the change.** N inserts sharing one
  `after` anchor each land immediately after that same line, so they come out
  reversed and were deliberately written reversed to compensate. A block is
  placed as a unit, so carrying the reversal across would have silently
  inverted every run anchored that way.

The equivalence harness had modelled two forms -- chaining each insert on the
previous, and one block -- but **never the third the data emitter actually
used**, N inserts on one fixed anchor. That is why none of this was caught, and
it is the more useful finding than any individual bug: a harness is only as good
as its inventory of the shapes the code really emits.

The differential baseline had a matching gap. It covered `content=` only, and
its one `after`-mode case was a *single-entry* run -- and reversing one element
is a no-op, so the first control on the reversal **passed**. Extended with a
multi-entry run at the end and a run with frozen lines on both sides (the
commonest real shape, and the only one that pins which neighbour is preferred).

Note also that the baseline had been recording the emitter's `"""` bug as
correct behaviour, which is the standing hazard of characterization tests: they
pin whatever was there. `"""` is a multi-line *basic* string and TOML processes
escapes inside it, so a Windows path's `\M` and `\G` are invalid escapes and the
file fails to parse outright.

### §34.3 Drag and drop could stop the app opening

`HAVE_DND` recorded whether the *Python* package imports; every registration
then assumed the **tkdnd Tcl package was loaded**, which is a different fact and
only true when the root was built with `TkinterDnD.Tk()`. Where they disagree,
the first path field raised during construction and took the whole window build
with it: no window at all, over a convenience feature, and a traceback pointing
at a text entry rather than at the missing package.

Found by running the new Tk suite on a real desktop for the first time. The
suite built a plain root, every test errored in setup, and the test bug and the
product bug turned out to be the same mistake.

The banner was fixed with it. "tkinterdnd2 not installed" is false in exactly
the case that matters and sends people to reinstall a package they already have.

### §34.4 The Tk suite, and two skips that hid checks

16 tests to 42: every Help document rendering, every theme applying and
repainting, the backups window replacing itself, six record types in the format
reference, and settings surviving a save and load -- each field, each checkbox,
the rule list in order.

The general form of that last one is worth keeping: a test compares the keys
`_gather_settings` writes against the keys `_load_settings` reads, because an
option saved but never loaded is written on every exit and discarded on every
start with nothing erroring.

**A skip is a check that did not run.** The first version put the drag-and-drop
regression test behind a second Tk root, the environment declined to provide
one, and the single most important test in the suite silently did not execute
while the run reported green. It is now simulated on the existing root and
cannot skip. Verified: **42 passed, 0 skipped** on Windows 11 / Python 3.14.5.
The expected count is recorded in `SMOKE_TEST.md`, because a suite that collects
38 instead of 42 has lost four checks and still passes.

### §34.5 Banding the conflict map

The same treatment the cell map had: each of the first five counts its own
color, then groups of five. A linear ramp against the worst cell rendered one,
two and three conflicting records as three near-identical greens, and those are
the counts that decide whether a cell is worth opening. Sharing the banding rule
between the two maps is deliberate -- they are read one after the other, and two
maps that look alike but band differently is a worse trap than two that look
different.

Percentile clamping was dropped for the true maximum. It existed to stop an
outlier flattening the ramp, which banding solves outright: an outlier lands in
the open-ended top band and costs the lower bands nothing.

The client-side redraw now **looks a count up in a table** instead of
re-implementing the ramp in JavaScript. The duplicated curve was the likeliest
thing to drift between the focused and unfocused views; a lookup cannot drift.
`severity`, `severity_stops`, `legend_stops` and `saturation_point` went with it
-- all four had become dead code held alive only by their own tests.

**Two negative controls missed here, both the same mistake in different
clothes:** asserting something adjacent to the property instead of the property.

* Reverting to a continuous ramp *passed*, because the test asserted the colors
  for 1, 2 and 3 were "distinct" -- and a linear ramp does give three distinct
  values, differing by about five units per channel, which is invisible on a
  nine-pixel square. Now measured as color *distance*, requiring ≥40 where
  banding gives 67.
* Rescaling only the cell fill *passed*, because the test searched the whole
  page and found the expected color in the **legend** while the rect was
  painted something else -- the map and its own key could disagree with the test
  green. Now asserted on the `<rect>` fill.

After tightening, all six mutations caught.

### §34.6 The 3D view was 55x too steep

Reported as "correct from the top, way too extreme from the side". That framing
is the diagnosis: looking straight down hides the vertical axis, so a wrong
vertical scale is invisible from above and only an oblique view exposes it.

Heights are in world units and 65 vertices span 8,192 of them, so adjacent
vertices are 128 units apart. The renderer plotted x/y as vertex *indices* and
then scaled height to a constant -- `((z-lo)/span)*110` -- which is a
normalisation, not a scale. Every cell came out 110 units tall on a 32-unit
footprint regardless of its relief, and the error grew as the terrain flattened:
512 units of relief should stand 2 units tall and stood 110.

The fix divides by the real spacing, including the sampling stride -- the grid
is drawn at every other vertex, so the horizontal step is 256 units, and
dividing by 128 would have reintroduced the same bug at 2x while looking
plausible. The invariant the tests state is the one anyone can check by eye: a
45-degree slope in the world draws at 45 degrees on screen.

Exaggeration is now explicit rather than accidental. 1x is the default and the
readout announces any other setting, because the previous behaviour's real cost
was not that it exaggerated but that it did so silently, and a view that lies
without saying so is worse than one that refuses to.

Four negative controls, all caught: restoring the normalisation, ignoring the
stride, exaggerating by default, and computing the spacing from 65 gaps instead
of 64.

### §34.7 Relief shading, contours and a true isometric

Three requests against the 3D view, all of them about reading the surface
rather than about the geometry fixed in §34.6.

**Two layers instead of one number.** The renderer flat-filled each quad with a
single color derived from both slope and height. That is a lossy mix: neither
quantity can be recovered from it, and a smooth hillside came out as 1,024
visible facets. It is now a greyscale hillshade with a hypsometric tint
composited over it at 0.55 -- roughly where relief maps put it, high enough that
elevation reads at a glance and low enough that the shading carrying the shape
is not washed out.

**Per pixel rather than per face**, by interpolating the vertex normal and the
height barycentrically across each triangle. Worth doing because the mesh is
only 32x32 after sampling, so a facet is tens of pixels wide. Two details that
would otherwise bite: adjacent triangles sharing an edge can leave a pixel
centre marginally outside both, which shows as a hairline crack across a
continuous surface (closed with a small tolerance, verified by checking that the
two triangles of a quad tile it with zero missing interior pixels); and clearing
800,000 pixels in a JS loop every mousemove costs more than rasterising the
terrain does, so the background is a prefilled buffer and one memcpy.

The light is fixed to the terrain, not the camera. A camera-fixed light keeps
every slope equally lit however the model is turned, which defeats the purpose.

**Contours** come out of the same pass, since the height is already interpolated
per pixel. Two decisions carry the feature:

* The interval is a *round* number -- 1, 2 or 5 times a power of ten, chosen to
  land near a dozen lines. Contours at 137 units are a texture; at 100 they are
  a measurement. The interval is stated in the readout.
* Line width is divided by the local slope, so a line is a constant width on
  screen rather than fat on flat ground and hairline on a cliff. That is also
  what makes crowding meaningful: lines bunch where the ground is steep, which
  is the information contours carry.

The first attempt at that second point was wrong in a way worth recording. A
probe of the width rule showed that on a slope of 1.0 the neighbouring contours
land about three pixels apart while the line itself is one and a half wide --
half ink, which reads as a dark smear over precisely the cliffs the hillshade is
describing. Contours are now dropped wherever the spacing falls below three
line-widths, which is what paper maps do, and the threshold is expressed in
terms of the line width rather than as a magic number so the two cannot drift.

**Isometric and Top down are buttons** because neither is reachable by dragging:
isometric needs a pitch of ``asin(tan(30 degrees))`` for the three axes to
foreshorten equally (verified by projecting the unit axes and checking their
screen lengths agree to 1e-9), and top-down needs an exact right angle.

The tint ramp is sent to the page as a table, the same decision as the conflict
map's bands. 256 samples: at 64 the steepest segment stepped by seven units per
channel, which is visible as a band, and the table costs a few kilobytes.

**Both modes are switchable**, which needed one decision stated rather than
assumed: the request was to be able to get the old view back, and the old view
had two properties -- a flat-shaded look and a broken vertical scale. Only the
first is a style. The button therefore swaps the shading and nothing else, and
`test_switching_shading_cannot_change_the_geometry` pins that the projection
inputs are shared, so no future edit can quietly reattach the distortion to the
mode it came from. The toggle is labelled with what clicking it *will do*
rather than with the current state, since a toggle captioned with its own state
reads as a status line and gets clicked by mistake.

Ten negative controls in total, all caught: the ramp not handed over, a fully
opaque
tint, an inverted ramp, contours off by default, the isometric preset removed,
a ramp coarse enough to band, flat as the default mode, the toggle removed, and
two attempts to reintroduce the old vertical scale through the flat path.

### §34.8 Exposing every setting

The request was to put everything on the page: multidirectional lighting, a
rainbow palette, azimuth and solar altitude, and checkboxes and dropdowns for
the rest -- with the existing hillshade kept as one of the options rather than
replaced. Three things are worth recording.

**Exposing a value must not change it.** The light was a hard-coded vector; the
defaults are that same vector written as compass degrees, and a test pins the
azimuth at 225 so turning the controls on cannot restyle anybody's view.
Deriving those degrees also settled a small discrepancy: the vector's comment
claimed north-west and the vector was south-west. The vector was right.

**Reset needs one source of truth.** Every default is a single dict keyed by the
script's own state names, shipped both flat (what the page reads at startup) and
under ``defaults`` (what Reset restores). The alternative -- a mapping table
between two lists of setting names -- is exactly the thing that goes stale when
a tenth control is added, and a Reset that misses one control is worse than none
because it looks finished. A test asserts every adjustable control is covered.

**Multidirectional is weighted, not averaged.** Six lights evenly around the
compass at one altitude, each weighted by its agreement with the primary
azimuth. An unweighted mean is ambient light with no shape at all; the weighting
keeps the primary direction dominant while filling the shadow side. Verified
that the weights sum to one, every light is a unit vector at the chosen
altitude, and flat ground is lit identically at one light or six -- so the
setting moves shadows without changing exposure.

**Multiscale blends normals rather than hillshades**, which is a deviation from
how the technique is normally described and is documented as one: it costs a
single per-pixel pass instead of three, and the results diverge only near the
terminator, which the shading floor already softens. Checked against a probe --
a broad ramp with a one-vertex gully cut into it -- that the fine radius sees the
gully, the coarse radius smooths it away, and the blend keeps both.

The rainbow palette is written from our own stops rather than taken from
Google's Turbo table. The ordering is the useful part and is a fact about
rainbows; the table is somebody's work.

**One negative control missed and had to be tightened.** Replacing the azimuth
slider with a checkbox passed, because the test asserted the *id* was present
rather than what kind of control carried it -- an element with the right id and
the wrong type is unusable while satisfying a presence check. The tests now
assert the control type and its range, and three further mutations (a checkbox,
a half-circle azimuth, a tint slider that cannot reach zero) are all caught.

### Gates

ruff, black, mypy (56 files), `check_undefined`, `check_placeholders`,
`make_pot --check` (479 messages), differential baseline regenerated with the
data-path half covered for the first time. **1,557 passed, 1 skipped**, plus 42
passed / 0 skipped in the Tk suite on a real desktop.


## §35 The NIF reader reaches every vanilla mesh, and what the checking taught

The reader went from 85.5% of vanilla meshes to **7,339 of 7,343 identical, 0
stopped early, 0 diverged**. That is the headline, but it is the least
interesting part of this section. What is worth recording is that almost every
finding here came from *checking*, and that the checks were wrong before the
code was.

### §35.1 The reference was wrong, and three methods were needed to prove it

An externally supplied block census had been treated as ground truth. It
reported the reader over-counting property blocks in 1,745 files, filed under
the alarming heading "misparse".

The reader was right and the census was wrong. Establishing that took three
independent methods agreeing: the layout reader, a raw byte scan sharing none
of its code, and NifSkope's own block list. `c/amulet_common_1.nif` holds two
`NiMaterialProperty` blocks - indices 4 and 11 - where the census records one.

Two lessons, and the second is the one that generalises. First, the census
undercounts *property* blocks specifically while matching on every other type,
which is exactly the kind of systematic error that survives casual checking.
Second, **the tool had assigned blame**. It said "misparse", which named the
reader as the culprit on the strength of an assumption nobody had tested. It
now reports "exceeds the census" and declines to name a party.

### §35.2 The self-check that caught the checker

The census was replaced with a scan that generates its own reference. The
first version of that scan was wrong, and its own reconciliation check caught
it within one run.

The claim was that a type name is a `u32` length followed by that many bytes.
True, but not *sufficient*: every string in a NIF is length-prefixed, so the
rule matches a node called `Bip01` as readily as `NiNode`. It over-counted 522
of 556 files. Adding NIF's naming convention as a second filter took that to
553 of 556.

The check that caught it exists because the file declares its own block count,
so a scan finding a different number can disqualify itself. That property was
built in before it was needed, which is the only reason the error surfaced as
a number rather than as a wrong conclusion published with confidence.

### §35.3 Blame the right thing: three reporting defects

Three defects in *reporting* were found, each of which had already sent an
investigation in the wrong direction:

- **A desynchronised cursor was reported as an unknown block type.** That
  blamed a missing layout for a wrong field width and inflated the
  missing-type ranking with files that were really layout failures.
- **Stop reasons carried raw binary into logs and terminals.** A
  desynchronised read produces arbitrary bytes and they were interpolated
  unescaped; one survey printed an embedded NUL and a run of high bytes to
  stdout.
- **Files that stopped early were classified as over-reporting**, because
  excess outranked truncation. 172 files sat under the wrong heading.

The third is the subtlest and the most instructive: the classification was not
wrong about any *fact*, only about which fact mattered. Excess is only evidence
about the reader when the reader reached the end.

### §35.4 One byte, and the value of a failure that names itself

Every alignment failure in the corpus read a type name of `\x00NiMorphData` -
the correct name behind a leading NUL, which is precisely what a cursor one
byte early looks like. `NiGeomMorpherController` was one byte short. It was the
**only** alignment bug in all 7,343 vanilla meshes.

The byte is `0` in every observed file, so its meaning is not determinable from
the evidence. It is named `trailing_flag`. Naming it after a guess would have
been the moment observation turned into invention, and the name is part of the
record.

### §35.5 The bug class that matters, and separating it out

`NiTexturingProperty` truncated any mesh with more than one decal, because
`texture_count` was read as a cap of seven rather than as a slot count.

This is worth its own heading because of what *kind* of bug it is. A missing
block type is a gap: the reader stops and says so, and `Structure.partial`
stops a caller drawing conclusions from an absence. A wrong layout in a
supported type produces confident wrong output. `--verify` now separates the
two, and doing so surfaced 11 real bugs that had been buried under 397
ordinary gaps.

### §35.6 Naming what is not known

Four blocks contain fields that could not be identified from the bytes. They
are stepped over as measured spans called `emitter_parameters`,
`unidentified_tail`, `path_parameters` and `projection`.

The alternative - plausible names guessed from what such a block usually holds
- would have produced code that reads better and means less. The width is the
only part the rest of the file depends on. An invented field name is worse than
an admitted gap because it gets believed, and then repeated.

### §35.7 A test that asserted the wrong thing

Implementing `NiPixelData` broke two existing tests. They had used it as their
example of an *unknown* block type, so they were asserting the state of the
layout table rather than the behaviour they meant to pin down. Replaced with an
`UNKNOWN_TYPE` constant that will never be implemented, with a comment saying
why, so the trap is not re-laid.

A third failure the same day was mine and in the opposite direction: a
regression test for the decal fix failed because the *fixture* was wrong - slot
5 is the bump slot and carries 24 extra bytes I had not written. Worth
recording plainly, since a test written wrong can just as easily be written to
pass against wrong behaviour.

### §35.8 What was not done, and why

Coverage stopped at the game as shipped. Four block types were left
unimplemented - `NiCollisionSwitch`, `NiFogProperty`, `NiRollController`,
`NiSpotLight` - on the reasoning that they occur only in the documentation
packages' demonstration files and never in a vanilla mesh, so implementing them
would be work whose result nothing consumes.

**That reasoning was wrong, and a run over 80,197 modded meshes proved it
within a day.** Mods use all four: 242 files stop on `NiCollisionSwitch`, 18 on
`NiFogProperty`, 2 on `NiRollController`, 1 on `NiPointLight`. The error was
using *vanilla* as the definition of "what exists" when this tool's entire
purpose is comparing **mods**. The denominator was picked from the corpus that
happened to be measured rather than from the problem being solved, which is a
comfortable mistake to make and an easy one to miss, because every number in
§35 was correct - they were just answers about the wrong population.

The mod run also reordered the priorities entirely. `NiSwitchNode` (2,127
files) and `NiLODNode` (1,382) do not occur in vanilla at all and together
account for 92% of everything that stops early in a real load order.

The provenance of every layout is recorded separately in `NIF_PROVENANCE.md`,
including what was deliberately not read to derive it. That document declines
to describe this work as "clean room": the phrase means two isolated teams, and
this project has one author. Overclaiming in the one document whose entire
value is that it can be trusted would be a poor trade.


## §36 Eighty thousand modded meshes, and the reader finally reaches the app

Two things happened here. The reader was tested against 80,197 meshes from a
real mod collection rather than a game install, and it was wired into the
product, which until this point it was not: `wraithguard/nif/` had no importer
outside its own tests and tools. A parser nothing calls is worth nothing, and
that was true of it for several sections.

### §36.1 The denominator was wrong, and it flattered us

§35 closed by naming four block types as not worth implementing because they
"occur only in the documentation packages' demonstration files and never in a
vanilla mesh". A mod run disproved that within a day: 242 files stop on
`NiCollisionSwitch`, 18 on `NiFogProperty`, 2 on `NiRollController`.

Worse, it had ranked the two types that actually mattered at **zero**.
`NiSwitchNode` (2,127 files) and `NiLODNode` (1,382) do not appear in vanilla
at all and between them caused 92% of everything that stopped early in a real
load order.

Every number in §35 was correct. They were answers about the wrong population.
This tool exists to compare **mods**, and the coverage denominator had been
taken from the corpus that happened to be measured. That is a comfortable
mistake: the measurements were rigorous, cross-checked and honestly reported,
which made the conclusion drawn from them feel earned.

### §36.2 A fix that was right about the failures and wrong about the format

Three mod meshes failed inside a bounding box. Solving the span from those
three alone gave 20 bytes, and it was written into the code as established --
"solved rather than guessed", with the reasoning spelled out.

It broke 13 files that had been working.

The evidence standard this project had already set requires the layout-free
scan to agree **across the corpus**, and that step was skipped because the
derivation from the failing files looked so clean. The real answer needed the
two populations separated rather than averaged: the box is *typed*, the type
word is 1 in all 27 blocks that parse and 0 in every mesh that failed, and no
single width can be right for both.

The lesson is not "check more". It is that a derivation which explains every
failure in front of you is not thereby correct, because the files that already
work are evidence too and they are the ones you never look at.

### §36.3 Three gates caught three real mistakes

Worth recording because they are the return on the standards suite:

- `test_declared_packages_match_what_exists` caught `wraithguard.dds` missing
  from `pyproject.toml` -- a package that would have been absent from a built
  artefact while passing every test.
- `TestNoReExportShim` caught `core.MeshAnalyser` in the GUI, reaching a
  library name through the engine's imports instead of its own module.
- A total that disagreed with its own parts: `--verify` reported 81,026 files
  for a run over 80,197, because a subdivision of one category was being summed
  alongside it.

### §36.4 Cross-checking, again, and what it cost to not have one

The DDS decoder was checked against Pillow: all 50 corpus textures decode
byte-for-byte identically, and every PNG round-trips exactly. Pillow is not a
dependency -- it was an oracle, the same role the layout-free scan plays for
NIF, and it turned "the images look right" into a byte-exact claim.

The contrast with §36.2 is the point. Where an independent check existed the
answer was certain in one run; where one was skipped, a wrong fix shipped into
the working tree and was caught only by re-running the corpus afterwards.

### §36.5 Cost, and where it actually was

Caching parses took a second pass over the corpus to five seconds, and almost
all of it was **hashing**, not parsing -- the cache had removed the wrong cost.
Identity is now three tiers: a digest the caller already has (free, since the
conflict scan hashes those files anyway), a memo on path, size and mtime, then
hashing. Second pass 5.2s to 2.0s.

The scan-time pass opens only meshes that already conflict *and* already differ
in bytes. The detail panel opens nothing at all until a row is selected. Both
follow from the same observation: a mod setup holds tens of thousands of
meshes and the scan has no idea which one anybody cares about.

### §36.6 What is still open - and one thing that turned out not to be

- **`dbs_meatstick.nif` is a malformed file, not a missing layout.** It was
  left failing rather than guessed at, and that was the right call: opened in
  NifSkope it has orphaned blocks, and its 26 block type names reconcile
  exactly with the header while the contents of block 10 do not - a property
  count of `0xFFFFFFFF`. The boundaries are sound and the block is not.

  Two things follow. The reader refusing it is **correct behaviour**, not a
  gap; inventing a layout to fit one broken file would have broken the sound
  ones, which is precisely what the bounding-box episode in §36.2 already did
  once. And the tool was **mislabelling it**: `--verify` filed it under
  "layout bugs", which asserts the fault is ours.

  That is the third category in this tool to have assigned blame before the
  evidence supported it - "misparse" was the first, and the "unknown block
  type" that was really a desynchronised cursor was the second. The heading now
  states the observation ("stopped inside a type this reader supports") and
  leaves the diagnosis to whoever opens the file. The recurring error is not
  carelessness about facts; every measurement was right each time. It is that a
  category name is an argument, and naming one after the most likely cause
  quietly converts a measurement into a conclusion.
- **Three Tk tests** for the detail view, written but never executed: the
  sandbox has no `tkinter`. Statically checked against the GUI's attributes,
  which is not the same as passing.
- **BC7**, deferred at the user's direction until BC1/BC3 were proven. Now
  done - see §40.


## §37 Auditing the work of §36, and what an audit is actually for

An audit pass over the code written in §36. Four findings, and the pattern is
worth more than any of them individually: **every one was in a place the tests
were green.** A passing suite is evidence about the cases it contains, and an
audit is the exercise of looking where it does not.

### §37.1 A coupling that was invisible because it was true

`analyse_mesh_conflicts` and `describe_mesh_detail` disagreed about how to
find the winning provider. One used the declared `entry["winner"]`; the other
took `structures[-1]`. Worse, the first was *half* positional -- it took the
winner by value and the losers as `providers[:-1]`.

They agreed on every input because `detect_resource_conflicts` sets
`winner = prov[-1]`. So the whole suite passed, and would have kept passing
right up until anything set a different winner -- at which point the detail
panel would have compared the winner against itself and reported the
comparison **backwards**, which is worse than not reporting it.

Both now take the winner by value. The new test builds a conflict whose winner
is deliberately *first*, which is the case no existing test contained.

### §37.2 A guard that bounded the wrong quantity

`read_dds` refused implausible dimensions by bounding each side at 32768. A
32768 x 32768 header passes that and asks for **4.3 GB** of RGBA before a
single byte of texture data is read. Bounding each side is not bounding the
allocation. The total pixel count is now capped at 64 megapixels, far above
any real texture and far below anything dangerous.

Found by reading the code and asking what the guard actually guarantees, which
is the only way to find it -- no file in any corpus triggers it.

### §37.3 A handler for something that cannot happen

`MeshAnalyser.structure` caught `OSError` beside `NifParseError`. It could
never run: `read_nif` already converts an unreadable path into a
`NifParseError`. Coverage found it, because coverage cannot reach code that
cannot execute, and a line that can never be covered is either dead or a bug.

A handler for an impossible case is worse than none. It advertises a failure
mode that does not exist, and the next reader has to work out whether the
impossibility is deliberate or a mistake. Removed, with a comment saying why
there is deliberately no `OSError` branch.

### §37.4 A leaked file handle, and a silent branch

`p.open("rb").read(256)` in the tes3cmd probe left the handle to the garbage
collector -- which CPython happens to close promptly and other interpreters do
not. Now context-managed. And a `stat()` failure in the analyser returned
silently while its sibling handler logged, so a missing provider looked
identical to a working one at every log level.

### §37.5 What the audit checked and found clean

Recorded so the absence of findings is visible rather than assumed: no bare
`except:`, no `except Exception` outside the two worker top levels that report
tracebacks into the log panel, no mutable default arguments, no TODO/FIXME
markers, and no undocumented public functions -- the 69 "missing" docstrings an
initial sweep reported were 63 private functions and 6 nested closures, neither
of which the project's pydocstyle configuration requires. Reporting those as
defects would have been a false finding, which is its own kind of failure.

Coverage of the two new modules went from 95% and 94% to **100%**, and every
line added to get there was an error branch -- the paths that run on files from
mod archives, and therefore the ones that most need to fail as findings rather
than as tracebacks.

### §37.6 The one thing this audit could not check - since resolved

Three Tk tests for the detail view had never been executed: the development
sandbox has no `tkinter` and it could not be installed. They were statically
verified to reference only attributes the GUI defines, which is not the same as
passing, and that was recorded here rather than assumed away.

They have since been run on Windows 11 / Python 3.14.5: **45 passed, 0
skipped**, up from 42. So they work - but the gap between "statically checked"
and "actually ran" was real for a day, and the only reason it closed is that it
was written down as an open question instead of quietly counted as done.


## §38 "Wired into the app" was half true, and every test agreed with me

The user opened the Resource Conflicts window and could not find the mesh
analysis. They were right: it was not there.

`analyse_mesh_conflicts` had exactly one caller, `_run_resource_scan` in the
engine -- the **command line**. The GUI worker called `detect_resource_conflicts`
and `format_resource_report` and nothing in between, so the window showed a
conflict list with no findings in it. The detail panel *was* wired, but only
fired on selecting a `.nif` row, in a five-line box at the bottom of a split
pane with placeholder text that never mentioned meshes. Nothing on screen
suggested there was anything to click for.

§36 opened by observing that "a parser nothing calls is worth nothing". The
irony is exact: the fix for that was applied to one of the two front ends and
then reported as done.

### §38.1 Why the tests were no help

Coverage of the pieces was good and coverage of the *path* was zero:

- engine functions: tested directly, 100% covered;
- the detail panel: tested, including that it reads nothing until selection;
- the GUI worker: tested that it opens a window and populates a tree.

No test asserted that the scan the GUI runs produces conflicts with findings
attached. Each piece worked; the wire between two of them did not exist, and
nothing was looking at the wire. This is the same shape as §37.1 -- a fact
nothing tested because every test was about something adjacent to it.

### §38.2 What changed

- The GUI worker now runs the analysis, so the report text and the tree both
  have it.
- A **column** in the tree, not just the detail panel: `!` where reading the
  mesh found something, `?` where the mesh could not be read. Those are
  deliberately different marks, because "this loses collision" and "we could
  not read this" send a user to completely different places. The whole value of
  a finding is triage, and a signal you have to click every row to discover is
  not triage.
- The placeholder text says what selecting a mesh does, since a feature nobody
  can find is indistinguishable from one that does not exist.
- Tests: three in the Tk suite asserting a marked row is visible *without*
  selecting anything, plus one in the engine suite asserting the pass attaches
  findings to scan output.

### §38.3 The reporting lesson, again

"Wired into the product" was a claim about the system made from knowledge of a
component. It was not a lie and it was not carelessness -- the CLI genuinely
worked -- but the user could not use it, and the user is the system. The check
that would have caught it is the dullest one available: open the thing and look
for the feature.


## §39 A viewer, and a question that reframed it

The reader could say a mesh had 456 triangles. It could not say whether the
winner *looked* different, which is what a person actually wants. This section
is the work that closed that, and the two questions from the user that changed
its shape.

### §39.1 The reader was built to discard exactly what a viewer needs

`vertices = 300` was a *byte count*. `children = 2` was a count, not the
indices, so the scene graph could not even be walked. That was deliberate and
documented -- a structure report needs "how many", never the coordinates -- but
it meant the honest answer to "how close are we to a 3D viewer" was "further
than it looks", and saying so was more useful than a hopeful estimate.

Retention was added by recording the byte span each field consumed rather than
teaching every reader to return its data, so the scan path stays
byte-identical. Verified across 300 files: every field the default path
produces is unchanged with geometry on.

### §39.2 The constraint that decided the architecture

Modern three.js ships ESM only, split across two files, and **ES module scripts
do not load from `file://`** -- origin `null`, CORS fails. That one fact ruled
out the obvious packaging and forced the CommonJS build behind a three-line
shim, which was verified in node before a line of the viewer was written.

The same fact has a second consequence that only surfaced when the user asked
why geometry was embedded at all: **a `file://` page cannot fetch anything**.
Not the NIF, not a sidecar, not the library. Embedding was not a design
preference, it was the only channel available -- and the moment the page is
served over `http://127.0.0.1` the restriction disappears and with it the
reason to embed. The user's follow-up ("HTTP loopback default, embedded
export?") was a better answer than any of the three options offered, because it
turns the fallback into a feature: the standalone page is what you keep or
send, not dead code kept alive for an edge case.

### §39.3 Measuring instead of arguing

Three questions were settled by measurement rather than opinion:

- **Encoding.** JSON decimals 5.40 MB, base64 typed arrays 4.91 MB, deflated
  typed arrays 1.86 MB. Base64 alone is nearly pointless -- its 33% overhead
  hands the binary saving straight back. Compression was doing the work.
- **Streaming the raw NIF.** Would ship 4.37 MB against 1.86, because the file
  carries normals, UVs, animation and blocks a viewer never draws -- *and*
  would need a JavaScript NIF parser, which three.js has never had. The 2012
  request for one is still open.
- **`fastapi` + `uvicorn`.** 14 packages, 34 MB, one compiled extension,
  against a ~38 MB app, to serve a fixed dictionary to one local browser.

Each of those felt like a matter of taste until it was a number.

### §39.4 Security stated as a property, not a promise

The server has **no code path from a URL to `open()`**. Payloads are registered
in memory and served by key. That is why `SimpleHTTPRequestHandler` is not used:
it exists to serve a directory, which is precisely what must not happen here.

The distinction matters for how it is tested. "Traversal is blocked" invites a
test that tries a few clever paths and concludes safety from their failure.
"There is no filesystem mapping" is a claim about the code, and the traversal
tests exist to confirm the reasoning rather than to constitute it. A missing
token returns 404 rather than 403 for the same reason: 403 confirms the key
exists.

### §39.5 One inconsistency, caught by reading rather than by a test

The first wiring called `webbrowser.open()` directly, bypassing
`_open_html_view` -- the pywebview/tkinterweb/browser chain every other
visualisation uses. Nothing failed. The view simply behaved unlike the rest of
the app, which no test asserts and no gate checks, and which was found only by
looking at the neighbouring code before declaring the work done.


### §39.6 A bug my tests bracketed, and a harness that lied about it

The served page reported **"THREE is not defined"** in a real browser while
every test passed.

The CommonJS build assigns to ``exports`` and defines no global, so it needs a
shim before it (``var module = {exports:{}}, exports = module.exports;``) and
after it (``var THREE = module.exports;``). The inline path wrapped it in both.
The served path emitted ``<script src=...>`` alone, so the library ran against
an undefined ``exports``.

**Why nothing caught it.** Two tests existed near the defect and neither
touched it:

* one asserted the library is *not inlined* when served -- true, and true of a
  broken page;
* one asserted both modes share their rendering code, comparing from
  ``function render(`` onward -- which begins *after* the library block.

They bracketed the bug. The replacement asserts the property that spans it: the
shim opens before the library and closes after it, parametrised over both
modes. A test written just to one side of a thing proves nothing about the
thing.

**And the first verification was wrong in the other direction.** The node
harness ran each script through ``new Function``, giving every script its own
scope, so the prologue's ``var exports`` was invisible to the library and the
harness reported a failure the page did not have. Browsers share one global
scope across classic scripts; ``vm.createContext`` models that and the fix
verified cleanly. Trusting the first harness would have meant "fixing" an
imaginary bug on top of the real one -- the same shape as the bounding box in
§36.2, where a derivation that explained the visible failures was still wrong.


## §40 Six hundred numbers, and a check that had to disagree with me

BC7 is the first thing in this project whose *definition* is large. The NIF
layouts were derived one field at a time, each verified by landing exactly on
the next block's type name. BC1 is an interpolation. BC7 is an eight-row mode
table, two 64-entry partition tables and three 64-entry anchor tables -
roughly six hundred numbers, transcribed by hand from a published
specification, with nothing after the mode bits at a fixed offset.

### The failure mode transcription has

A wrong table entry does not throw. It produces a correct-looking image with a
handful of wrong 4×4 blocks, in whichever partition shapes the encoder happened
to pick. Nobody notices on one texture. It is invisible to a unit test, because
the test and the table came from the same reading by the same person - the
identical structure to §36's "a reader validated only against a writer that
shares its assumptions proves nothing".

So the check was designed to be *incapable* of sharing the assumption:
`tools/check_bc7.py` generates blocks that force every table entry to be used
- all eight modes crossed with all 64 partitions - and compares against
Pillow. **19,380 blocks, byte for byte.** Random bits are legitimate input
here, which is the useful accident of this format: every 128-bit pattern is a
valid BC7 block bar one reserved value, so noise exercises endpoint ordering,
P-bits, index packing and anchor widths far more harshly than a photograph.

It passed on the first run. That is worth recording honestly rather than
quietly: the expected outcome was a handful of wrong entries and a bisect. The
value of the check does not depend on it having found something - it is what
makes "the tables are right" a claim about evidence rather than about care.

### The one thing an oracle cannot settle

BC5 stores two channels; blue is reconstructed. There is no right answer to
compare against, only a convention, so comparing our blue against Pillow's
would compare two conventions and call it a test. The cross-check compares red
and green only - **not a weakened test but the correct one** - and the
reconstruction is checked separately by geometry.

**That geometric check was wrong.** It asserted every pixel of a random block
decodes to a unit vector. It passed on one seed and failed on the next, and the
decoder was right both times: random bytes are not normals, and where
x² + y² > 1 there is no real z, so the reconstruction correctly clamps. The
check now tests the representable and clamped cases separately.

Note how it was caught - not by reasoning, but by running it again with
different data. A check that is only ever run once is a check whose own
correctness is untested. This is the fourth time in this project that the
*verification* was the broken part (§36.2's bounding box, §35's texture
comparison, §39's `new Function` harness), and the pattern is consistent
enough now to state plainly: **a new check deserves the same suspicion as new
code, and the cheapest way to earn confidence in one is to run it against
inputs it has not seen.**

### A licence decision that needed no technical argument

`pydds` was the obvious candidate: DDS decompression bindings, BC7 included,
exactly the hard part. It is **GPLv3-or-later**. That ended the evaluation
before performance or API entered into it - adopting it would relicense
everything here, against a standing constraint. Two further facts made it moot:
it depends on Pillow, so it would have *added* a dependency rather than
replaced one, and it is 0.0.8, marked alpha.

Worth noting what the useful step was: reading the metadata. The summary line
promised the right thing. The classifier was three lines further down.

### Naming a thing after what it does

`wraithguard/dds/` became `wraithguard/images/` because `wraithguard.dds.targa`
is a lie, and the rename was cheap while the package had one external caller.
The `Image` type moved out of the DDS module so that `bitmap` and `targa` were
not importing from `dds` to borrow a dataclass.

`Image` also gained a `__post_init__` that refuses a buffer inconsistent with
its dimensions. A decoder that miscounts rows renders as a diagonal smear
rather than failing, and a smear is far harder to diagnose from a screenshot
than an exception is.

### Roles, and a correction from the user

The first version of the role model had two errors, both from reasoning about
the formats rather than about the engines:

* it treated `_spec` as a greyscale mask. It is RGB specular *color* with
  shininess in alpha.
* it mapped the vanilla **bump slot** straight to "normal map". Vanilla
  Morrowind renders neither bump nor normal maps; MGE-XE and MCP add the
  capability by repurposing the *environment map* slot, and NifSkope follows
  that convention. What a bump slot holds depends on which toolchain wrote the
  file, so it is now its own role and the module declines to guess.

And a framing error of mine, corrected directly: I described BC5 normal-map
decoding as marginal - "meaningful only if we're showing normal maps
deliberately" - when comparing one mod's normal map against another's is a
stated goal. Both sides are normal maps; the comparison is exactly
apples-to-apples. That changed the implementation, not just the wording: blue
is reconstructed rather than left flat, because discarding z would call two
different maps identical whenever they shared x and y.

The green channel is **not** flipped. Both engines use the DirectX convention,
and tooling written for OpenGL flips on load by default - which here would
report every normal map as differing from a byte-identical copy of itself, in
the one comparison the feature exists to make.

### Two smaller things the work surfaced

* `TextureResolver` tested archive membership with `archive.read()`, pulling
  and discarding the bytes. Around sixty wasted reads per texture against the
  three vanilla archives, on the path the 3D viewer walks for every shape.
  `BsaArchive.__contains__` answers from the index.
* `pyproject.toml` declared `license-files = ["License/LICENSE"]` and no such
  file existed - `License/` held only third-party licences. A pre-existing
  failure in `test_standards.py`, unrelated to this work, surfaced by running
  the suite rather than the subset under change.


## §41 A relicence, and the gap a corpus cannot see

Greatness7 - author of the Morrowind Blender Plugin - offered in chat to
relicense his NIF library MIT, then did it: `cbe18b5` adds an MIT `LICENSE` to
`io_scene_mw/lib/es3/`. `Greatness7/tes3` was already MIT.

**The offer was not treated as the grant.** Nothing was read until the commit
existed, because a chat message is not something anyone can cite in two years,
and this project had spent months documenting exactly what it had not read. The
grant covers `lib/es3/` and nothing else; the rest of the plugin stays GPL-3.0
where Blender requires it, and that boundary is a directory path it would be
very easy to slide across.

### What a second implementation saw that no measurement could

Within an hour of reading `tes3`:

* It **confirmed** `_BOUNDING_BOX_TAILS`, the derivation recorded in §36.2 where
  "20 bytes, solved not guessed" - concluded from two files - broke thirteen
  meshes that already parsed. Both type numbers and both widths match, arrived
  at from bytes with no shared assumptions.
* It found **`NiUnionBV`**: bound type 4, which is not a width at all but a
  count followed by that many complete volumes, each with its own type word,
  nestable. A width table cannot express that shape.

No file in either corpus carries a union bound. That is the whole point, and it
is a sharper claim than "our tests might miss corner cases":

> **A gap in coverage produces no failure to count.** The 100%-of-vanilla and
> ~99%-of-mods figures are evidence about files that have been *seen*. Running
> the same corpora harder cannot find a type they do not contain, no matter how
> many files they hold. The only instrument that sees it is a second
> implementation of the same format.

Nineteen more types followed the same way - found by running against the
categorised NIF sample archive, which took it from 624/768 to 754/768 (98.2%).
Two needed the reading rather than a guess: `NiBltSource` descends from
`NiObject` rather than `NiObjectNET`, so it has *no* name, extra data or
controller, and assuming the usual preamble would eat twelve absent bytes;
`NiTriStripsData`'s final run has no stored length at all, being the sum of the
array before it, which needed a new field kind rather than a new table entry.

### Marking taken layouts as taken

Every one is commented as **taken from tes3, not derived**. This is not
bookkeeping. A derived layout has survived the exact-landing test across
thousands of files; a taken one is a transcription confirmed against however
many samples carry that type - for several, exactly one file. Both are true;
they are not equally well-evidenced, and a future failure should be debugged in
the right place. `NIF_PROVENANCE.md` now carries a dated boundary so that
nothing later blurs how the *existing* layouts were obtained.

### On the criticism that prompted it

The remark in the same conversation was that an AI "rewrite from scratch" will
be buggy because it lacks the years of testing that maintained libraries have.
That is fair, and it is worth answering with what actually happened rather than
with a defence.

Where it lands least: the NIF format is *self-falsifying*. Blocks carry no
length field, so a wrong layout cannot land on the next block's type name. Every
layout here was verified that way across 87,000 real files, and the bugs that
found - the typed bounding box, `NiGeomMorpherController`'s single byte, decal
slots past the table, UV set flags - are exactly the corner cases the remark
means.

Where it lands hardest, and where it was right: **unknown unknowns**. Not that
the code is wrong on what it handles, but that it silently handles less than it
appears to, and no amount of running the existing corpora reveals it. Nineteen
types in an afternoon is the measure of that. The corpus was never the problem;
treating corpus coverage as coverage was.

### The comparison feature this unblocked

`wraithguard/images/compare.py` finally answers the question the whole texture
effort exists for. Four choices in it are each the opposite of the obvious one:
different sizes are reported rather than rescaled (resampling invents pixels and
then reports differences in the invented ones); two metrics rather than a mean
(a re-compression nudges every pixel by a level, a retexture moves a few pixels
a long way, and a mean ranks the first higher); a one-level difference is not a
change (or every recompressed texture reports as 100% changed, truly and
uselessly); and roles are checked before pixels.


## §42 Knowing when to stop writing code you cannot run

The WebAssembly bridge in `wasm/` is written and has never been compiled: the
environment it was authored in has no Rust toolchain and no root to install one.

The first version was ambitious - world-space transform composition, triangle
flattening, texture path extraction, roughly three hundred lines. It was
deleted, and the reason is worth recording because it is a judgement this
project has to keep making.

It guessed at `tes3`'s API in three places and was **wrong in two**:

* `shape.base.base.name`, when the `Meta` derive emits a `Deref` to `base` on
  every type and the correct spelling is `shape.name` - wrong by two levels
  *and* unnecessary.
* `property.base_map`, when the field is `texture_maps: Vec<Option<TextureMap>>`.
* `NiLink::from_index`, which does not appear to exist at all.

Each was found by reading `tes3`'s source *after* writing code against an
imagined version of it. The hit rate on unverifiable guesses was one in three.

**The argument for deleting it rather than shipping it with a caveat.** A
caveat at the top of a file does not survive contact with someone skimming for
the function they need. Untestable code that looks finished is worse than
obviously-unfinished code, because the first invites use and the second invites
a compiler. What remains uses only calls verified against the source, and says
in its own docstring which ones those are.

This is the same principle as `NIF_PROVENANCE.md`'s "taken versus derived", and
as §40's refusal to compare BC5's blue against Pillow's: **the confidence a
piece of work claims should match the evidence behind it.** Three hundred lines
of plausible Rust claims much more than "written blind against an API I misread
twice" supports.

The remaining crate exposes only values `wraithguard.nif` also produces, so the
first thing to build with it is a differential check rather than a feature -
which is also the cheapest possible way to find out whether the toolchain works
at all.

### The lit material view, and why it is not decoration

`wraithguard/images/viewer.py` gained a fourth mode. The three flat modes
answer "which do I prefer", "what moved" and "where is the change". None of
them can compare two **normal maps**, because a normal map is not a picture: it
encodes how a surface responds to light, and rendered flat it is a field of
pale blue in which two quite different maps look identical.

So each texture is drawn on a quad with its own auxiliary maps applied, under a
single draggable light. Three choices in it were each the opposite of the
obvious one:

* **Orthographic camera.** A perspective camera foreshortens the left and right
  quads differently, so the two sides disagree for a reason that is not in
  either file - the same class of error as rescaling a texture to compare it.
* **Normal maps loaded linear, not sRGB.** They are vectors. Applying a color
  transfer function bends all of them, and the result looks like a subtly wrong
  material rather than a bug.
* **One light for both quads, and dragging moves the light rather than the
  camera.** On a flat quad there is nothing to orbit, and a comparison in which
  each side is lit differently is not a comparison.

The map toggles exist for the same reason the mesh viewer's texture toggle
does: turning a layer off is frequently the clearest way to see what it was
contributing. They are offered only where the map exists.

## §43 Colour that carries meaning, escapes from dead ends, and a floor that had drifted

A run of user-reported papercuts, each of which turned out to be a small design
question rather than a bug to patch.

### Ownership was spending a colour it did not need

The record-conflict list painted every row that touches one of your mods a flat
orange, and that orange won over the verdict colour -- so a record of yours read
the same whether it was losing work or perfectly benign. But the list already
has a ★ column that says "yours". The colour was being spent twice on ownership
and not at all on the thing the colour is *for*. The fix was to let an owned row
take its verdict colour like any other, a shade more saturated so it still
catches the eye, and let the ★ carry ownership alone. Two follow-ups fell out of
using it: the "nothing lost" greys were `#9a9a9a`, too faint to read against the
dark rows, so they were lifted for every record (brighter still on yours); and
the whole exercise is a reminder that a second signal competing for the same
channel usually means one of them is in the wrong channel.

### A select-all you could not get out of

Ctrl+A (Tk's `<<SelectAll>>`) selected every row in a draggable list, and then
nothing but a reset would clear it. The cause was in the block-drag handler:
a press inside a contiguous multi-selection returns `"break"` to drag the whole
block, and once every row is selected they *are* one contiguous block, so every
click was swallowed as a drag-grab. The fix is the file-manager rule -- a press
that grabs a block but never moves is a plain click, so on release it collapses
to the clicked row. The feature (drag a block) and the escape (click to reduce)
are the same gesture told apart by whether the mouse moved.

### The colours that waited for a window nobody opened

The conflict list's verdict colours came from a survey that only ran when you
opened Plugin summary or the Plugin view. People who never opened either saw an
uncoloured list and assumed it had nothing to say. The survey already had a
`quiet` mode (built to colour the plugin tree in the background); the change was
just to fire it when the conflict window opens, reusing the warm cache from the
scan that just ran. The lesson repeats one from earlier sections: a result that
requires a second, non-obvious action to appear is a result most users never
see.

### An escape hatch instead of a looser guess

The mods-folder scan recognises a mod by a standard asset subfolder or a plugin;
an OpenMW Lua mod with a non-standard VFS layout (`NgardeParrySounds\sounds\`)
has neither and is skipped. The tempting fix -- add `sounds` and friends to the
recognised set -- trades a miss for a false match on some *other* mod's stray
folder, and there is no folder-name set that covers arbitrary Lua layouts. So
the walk stays conservative and the user gets a manual override instead: an
**Add data folder... / Add plugin...** button and drag-drop, decoupled from the
scan and merged into the subset through the `subset_lines` input the core
already reads additively. When a heuristic cannot be made reliable, the honest
move is a cheap manual path around it, not a heuristic that is wrong more
quietly.

### A floor that had drifted below the building

The cell map was still opening as a `file://` path (refused by some webviews)
and writing a fixed `cell_map.html` that housekeeping never pruned; both were
brought in line with the other views -- served over loopback, written
timestamped. And the coverage floor, set at `fail_under = 52` in 3.0 against a
measured 54%, was found by the 3.1.3 audit to be sitting under a suite that now
covers **79.3%**. A floor 27 points below the real number no longer ratchets
anything, which is the entire job of a floor. It was raised to 77 -- a couple of
points below the honest figure, the same discipline it was set with -- so it
tracks the number up instead of rubber-stamping a large regression. Never
lowered to make a change pass; raised to keep meaning what it says.

## §44 Two icon systems, a case-sensitive filesystem, and a Linux build that finally ships

Steam Deck testing surfaced two real defects that Windows use never could,
both of them accidents of a platform Windows simply doesn't have: a
case-sensitive filesystem, and a window manager that doesn't special-case
`.ico`. Neither was findable by reading the code -- it looked correct on the
only platform it had ever run against.

### One window manager reads `.ico`, and Tk let the assumption stand

`root.iconbitmap(default=icon_path)` looked platform-agnostic and, on the only
platform it had ever run on, behaved that way -- Windows is where Tk
special-cases `.ico` loading, so the call just worked and there was never a
reason to question it. On X11/Wayland (Steam Deck's Desktop Mode runs KWin) it
only understands the legacy XBM bitmap format; the call either raises
`TclError` or silently does nothing depending on the Tk build, and it was
wrapped in a bare `except Exception: print(..., file=sys.stderr)` -- invisible
in a `--windowed` build with no console. The window just fell back to Tk's
generic feather icon, which reads exactly like "the icon doesn't work" and
nothing more specific than that.

The fix goes through `iconphoto()` instead -- the mechanism `_NET_WM_ICON`
actually specifies, honoured by every real window manager including KWin --
but it wants a `PhotoImage`, and Tk's built-in image formats stop at
GIF/PPM/PNG, never `.ico`. Rather than ship a second icon asset to keep in
sync with the first, `_icon_photo_image()` reads the `.ico` container directly
and pulls out its largest PNG-encoded frame: any icon built for Vista or later
carries at least one, since PNG compression is required once a frame exceeds
classic BMP's 256x256 limit. `.ico` was only ever a container format; the
extension just hides that. `iconbitmap(default=...)` stays for Windows,
`iconphoto(True, ...)` runs alongside it everywhere else, and the resulting
`PhotoImage` is kept as `self._icon_photo` -- Tk drops a `PhotoImage`'s pixels
the moment nothing in Python still references it, even though the window
keeps pointing at it, so a local-only reference would have let the icon go
blank the instant `__init__` returned.

### The same bug, twice, because pywebview isn't Tk

The cell map and mesh viewer windows kept the plain Python icon after that
fix landed, and the reason is structural rather than a miss: those are
pywebview windows, opened in a separate child process (`_run_pywebview_window`
re-invokes the script with `--show-map` so `webview.start()` can own its own
main thread) and built by WinForms/WebView2 or GTK, not by Tk at all --
`root`'s icon setting has no path to reach a window Tk never created.
`webview.start(icon=...)` is a documented no-op on Windows -- it expects the
icon baked into the `.exe` at freeze time, which is meaningless running from
source -- but is honoured on Linux's GTK/Qt backend, so the fix splits by
platform. Linux gets `icon=` passed straight through. Windows gets a
purpose-built `_apply_windows_icon_to_own_windows()` that loads the `.ico` via
`LoadImageW`, finds the child process's own window by matching **PID** rather
than title ("Cell Map" / "View" aren't guaranteed unique on someone's
desktop), and pushes the icon in with `WM_SETICON` -- the same message
Explorer reads for the taskbar, and the same mechanism `iconbitmap` uses
internally. It runs on a background thread started just before
`webview.start()` blocks, polls for up to 8 seconds, and gives up quietly;
icon lookup is wrapped separately from window creation so a missing or bad
`.ico` can never be mistaken for an actual pywebview failure and needlessly
bounce the view out to the browser fallback.

### A raw `.is_file()` check, and the filesystem that finally disagreed with it

The more interesting bug: on Steam Deck, the mesh viewer showed one checkbox
for a cell with three plugins overriding its mesh, where there should have
been three to step through. `wraithguard/nif/vfs.py`'s `read_mesh` resolved a
loose file with `(folder / path).is_file()` -- a plugin referencing
`Mesh\Foo_Bar.NIF` finds a file actually named `foo_bar.nif` without
complaint on Windows or macOS's case-insensitive filesystems, and fails that
exact check on Steam Deck's case-sensitive one. Mismatched case in a mesh path
is unremarkable in Morrowind modding; nobody building mods on Windows over the
last twenty years has had a reason to match it exactly. The failure was an
`OSError`, caught and dropped silently -- the mesh never reached
`_view_field_mesh`'s `sides` list, so the viewer page was built with one
entry instead of three. The base-game copy loaded because it lives in
`Morrowind.bsa`, and BSA lookups were already case-normalized through
`normalise()`; only the loose-file path had the gap.

Worth noting: `textures.py` already gets this right, pre-building a
lower-cased, slash-normalized index of every loose file up front. That the two
modules diverged on the identical problem is its own small lesson -- a pattern
established once in one file doesn't propagate to a sibling file on its own --
and the fix here is exactly that pattern, applied where it was missing: a
cached, case-insensitive index as a fallback between the exact-match check and
the archive search, preserving loose-over-archived priority.

### Two small-screen fixes, made by hand

The mlox rule maker window now scrolls instead of clipping its content, and
the TES3 record conflict window no longer swallows its "include other mods" /
"include my mods' non-conflicting records" options or its bottom action
buttons on a small screen -- both packing-order problems in frames sized to
assume more vertical room than Steam Deck's display gives them.

### A wheel binding that never worked, found by fixing a test instead of writing one

Re-syncing `test_gui_smoke.py` to the scrollable-controls refactor -- `help_button`
renamed to `help_btn`; `controls_frame` and the Canvas's scrollable content,
neither ever stored on `self`, now reached through helpers that walk the
widget tree instead -- turned up a real defect rather than the stale attribute
references it went looking for: `controls_canvas` never had `yscrollincrement`
set, so `yview_scroll(n, "units")`, what all three wheel bindings call, was
silently a no-op. Dragging the scrollbar itself worked fine, since that goes
through `yview_moveto` instead -- which is exactly why nobody had noticed the
wheel doing nothing. Fixed by setting `yscrollincrement=24`. `clear_memory_btn`,
a bare local never assigned to `self`, is now `self.clear_memory_button` like
every other action-bar button, closing the one gap the sync pass couldn't
paper over with a widget-tree search.

### The Linux binary, fixed in the order the failures actually appeared

Three separate problems, each only visible once the previous one stopped
hiding it:

1. **The build failed outright.** PyInstaller does not support two Qt bindings
   packages in one frozen app, and both were present: `pywebview[qt]` pulls in
   PyQt6 as the actual Linux backend behind `webview.platforms.qt`, while the
   Dockerfile also explicitly installed PySide6 and told PyInstaller to
   `--collect-all` it. The freeze hook picked PyQt6 first, then aborted hitting
   PySide6 through the explicit collection. PySide6 is dropped; PyQt6 stays,
   since that's what the app's own Qt usage already resolves to. (The
   Dockerfile CMD regenerates the `.spec` file from the `.py` script on every
   build, so the fix had to go in `Dockerfile.wraithguard` -- a hand-edited
   `.spec` would not have survived the next run.)
2. **The binary built but couldn't be trusted on a bare system.** `libnss3`,
   `libnspr4`, `libasound.so.2`, `libpulse.so.0`, `libxkbfile.so.1`, and
   `libXtst.so.6` -- QtWebEngine's runtime dependencies for certs, audio, and
   input handling -- were absent from a minimal system like Steam Deck's, and
   PyInstaller doesn't fail a build over a library it can't resolve at freeze
   time, it just warns. Now bundled by adding the equivalent apt packages to
   the Docker build image. `libtiff.so.5` was left alone on purpose: Ubuntu
   24.04 only ships `.so.6`, and it turned out not to matter anyway -- the
   app's texture handling is entirely home-rolled decoders (`targa.py` and,
   presumably, a DDS counterpart) that never route through Qt's `QImage`
   machinery, so `libqtiff.so`, the only thing that warning was ever about, is
   dead weight the app never touches regardless of whether it's present.
   (Confirmed separately: TIFF only ever mattered for old Morrowind
   Construction Set map exports, not anything this app reads at runtime.)
3. **The binary was carrying Qt modules the app never calls.** Tracing actual
   pywebview usage down to the two calls it makes -- `webview.create_window(...)`
   and `webview.start(icon=...)`, no `js_api`, no tray icon, nothing
   QML-backed -- confirmed against a full grep of the codebase that `QtQml`,
   `QtQuick`, `QtPositioning`, `QtDBus`, `QtMultimedia`, and `QtTextToSpeech`
   are only present because `QtWebEngineWidgets`' PyInstaller hook pulls them
   in defensively, not because anything here calls them. Excluding them
   shrinks the binary and, as a side effect, quiets several of the
   `Library not found` warnings from step 2 -- `libpulse.so.0` turned out to be
   a dependency of the now-excluded `QtMultimedia`/`QtTextToSpeech`/
   `QtSpatialAudio`, not of anything the app's WebGL viewer actually needs.

### Coverage: the file the numbers already pointed at

A full-suite coverage report (3,549 passed, 4 skipped, 83.86%) showed two
files accounting for 58% of every missed statement project-wide;
`wraithguard/land/service.py` was the sharper of the two by percentage, at
31%, with its misses sitting in long contiguous blocks rather than scattered
edge cases -- the signature of whole functions never exercised at all, not an
untested branch here or there. `tests/test_land_service.py` gained 23 tests
across two passes, closing `_build_records`, `_finish_textures`, and `_write`
outright, and finally `build_merged_lands` itself, the hardest piece: rather
than mocking the orchestration, only `subprocess.run` is stubbed, so no real
`tes3conv` binary is needed while everything downstream runs unmodified
against records built by the real encoder. File coverage: 31% to 84%. One real
defect turned up along the way and stayed a footnote rather than a fix:
`_compress()` needs `zstandard`, or Python 3.14's stdlib `zstd`, or every
write fails -- invisible on the project's Python 3.14 CI, worth knowing if
anyone ever runs the suite on an older interpreter.

Gate state on the latest full run: **3,646 passed, 2 skipped**
(`test_differential`'s deliberate baseline skip; `test_viz_pages` for symlinks
not permitted in that environment), **coverage 86.77%** against the 77% floor.
`CHANGELOG.md`'s 3.1.5 entry has the user-facing side of all of the above.

## §45 A fourth audit, and three documents retired into this log

Run on a fresh checkout at **3.1.6** (Python 3.10.12, Linux, no Tk, no
network), the same conditions as the 3.1.2 audit that became `AUDIT_REPORT.md`.
Every gate is clean and there was **no code to change**: `ruff check .` passes
under the full strict select, `black --check` clean, `python -m mypy` reports
*no issues found in 178 source files* (up from the 109 the docs still cited --
the package grew, the gate kept pace), `check_undefined` / `check_placeholders`
ok, `make_pot.py --check` current. `vulture` at confidence 70 finds **zero**
unused functions, classes, methods or imports across `wraithguard/` and both
root scripts -- for 117k lines that is the notable result. `pytest --collect-only`
in this hermetic checkout collects **5,578 tests**.

That last number is the point of this section. The test count has been written
into prose in four places and has been wrong in all of them for some time:
`REMAINING_WORK.md` said 3,289, `PROJECT_LAYOUT.md` said 3,442 in one paragraph
and 3,202 in the next, §44 above recorded 3,646, and the suite now collects
5,578. A count hand-copied into prose rots the moment the next test lands; the
3.1.2 audit already diagnosed exactly this drift and it recurred anyway. So the
fix this time is structural rather than another round of find-and-replace:

- **`AUDIT_REPORT.md` retired.** It was the 3.1.2 audit with every finding
  marked resolved and re-verified at 3.1.3 -- a point-in-time record whose
  living conclusions are this section, and whose resolution narrative is already
  duplicated in the changelog. Deleted rather than left to drift a third time.
- **`REMAINING_WORK.md` retired**, its still-live content migrated below so
  nothing forward-looking is lost. What it mostly held was a status snapshot
  (all items `~~struck through~~` as done) plus the four drifted numbers.
- **`PROJECT_LAYOUT.md` folded into `README.md`.** The directory tree and the
  `viz/assets` three.js `--add-data` build caveat moved into a new *Project
  layout* section and the packaging notes; its own stale counts did not.

Documentation dropped from 19 tracked markdown files to 16. `README`,
`QUICKSTART` and `MLOX_RULES` (the Help menu's `HELP_DOCUMENTS`) are untouched;
`CHANGELOG`, `CREDITS`, `SMOKE_TEST`, `MERGED_LANDS`, `NIF_PROVENANCE` and this
log all stay -- each is cited by CI, a test, a generator or a source module for
provenance, and none carried a rotting number.

### Migrated from REMAINING_WORK §1 -- ruff families deliberately not enabled

Each would pass judgement on real code; listed with finding counts so the
decision to leave it off is informed rather than implicit. `TC` from the
original list is **done** (in `select` now); the rest stand:

| Family | Findings | Assessment |
|---|---|---|
| `C901` complexity | 42 | Overlaps the oversized-functions list below. Enabling it without first splitting those functions would just add 42 `noqa`s, which is worse than the status quo. Split first, then consider it. |
| `ARG` unused arguments | 37 | Mostly Tk callback signatures (`_event` parameters that must exist but are unused). Enabling means ~37 renames to `_`-prefixed names. Cosmetic. |
| `EM`/`TRY` exception style | 51 | Would rewrite every `raise SystemExit("...")` into a pre-assigned message variable. Ruff's preferred style; this codebase's inline messages are clearer. Leave off. |
| `SLF` private access | 20 | Almost entirely tests reaching into internals deliberately. Would need a per-file exemption saying exactly that. Low value. |
| `PT` pytest style | 26 | 24 are composite `assert a and b`; splitting gives better failure messages -- a mild, real benefit. |

### Migrated from REMAINING_WORK §3 -- oversized functions, left alone on purpose

Every one is *sequential* rather than tangled, which is why they read better
than their length suggests. Pinned by the differential baseline; touch only with
it running.

| Function | Lines | Why it stays |
|---|---|---|
| `generate_customizations_toml` | 327 | A single linear emitter with no internal stage boundaries; splitting means inventing seams rather than following them. |
| `_anchor_positions` (sort) | 258 | The transitive anchor resolver; the nested `_final_pos` closure is the hard part. Genuinely intricate. |
| `build_arg_parser` | 197 | 40+ `add_argument` calls. Long by nature; splitting by group would be cosmetic. |
| `simulate_configurator_apply` | 181 | Reproduces momw-configurator step for step, sharp edges included; splitting would obscure the correspondence. |

### Migrated from REMAINING_WORK §6 -- what NOT to do

Each considered and rejected on evidence:

- **Do not "fix" the skipped differential test.** It guards the baseline.
- **Do not narrow the 40 `BLE001` catches** without reading each reason --
  untrusted downloaded rule files, optional imports, Tk quirks (§13 records the
  two refusals and why).
- **Do not change `T3_NEVER_CLEAN`** or anything on the tes3cmd path.
  Morrowind.esm / Tribunal.esm / Bloodmoon.esm must never be cleaned.
- **Do not enable `EM`/`TRY`.** The resulting style is worse here.
- **Do not delete the mixin `TYPE_CHECKING` blocks** in `gui/t3.py` and
  `gui/conflicts.py` -- the documented host contract mypy checks `App` against.
- **Do not reformat with `ruff format`.** `quote-style = "preserve"` is set on
  purpose; black owns formatting here.

The coverage story (79% overall, the GUI's smoke-test-only verification, the
`tools/` gap and the untranslated `.mo`) is unchanged from §4 of the retired
document and recorded across §8.5, §22 and §44 above; it is not repeated here.
