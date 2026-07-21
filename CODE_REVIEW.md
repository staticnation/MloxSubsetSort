# Code review — MLOX Subset Sort (running log)

> **This is a running log, not a single review.** Sections are appended as work
> happens and are ordered **oldest first**, so §1 is the original review and the
> highest-numbered section is the most recent. Nothing here is rewritten when
> later work supersedes it — the point is to keep the reasoning that was
> actually used at the time, including the decisions that were later revised.
>
> **Read every figure as "true when written," not as current.** The clearest
> example is the test suite, which appears at four different sizes as it grew:
>
> | Section | Test count at the time |
> |---|---|
> | §4 (test suite, original review) | 129 |
> | §10 (licence audit) | 305 |
> | §13 (legacy scripts) | 374 |
> | current (3.0) | **724** |
>
> The same applies to tooling versions, file layouts and line counts. For the
> current state of anything, check the code, `CHANGELOG.md`, or run the gates.
>
> Where a section records a decision that was *deliberately refused* (a linter
> rule, a "fix" that would have been wrong), that reasoning is usually still
> live and is cross-referenced from `pyproject.toml`. Those are the parts most
> worth reading before changing something.

---

**The first entry — the original review.** Everything from §1 onward was
written against the state of the code described here.

Senior-developer review of `mlox_subset_sort.py` (engine) and
`mlox_subset_sort_gui.py` (Tkinter front-end), covering correctness,
security, PEP 8/PEP 20 conformance, testing, and performance.

**Verdict:** the codebase is in good shape. The domain logic is careful and
unusually well commented — the *why* is recorded, not just the *what*, which
is rare and valuable. Review found **four real defects** (one security-
relevant, one resource leak, one crash, one dead conditional), all fixed, and
added a **129-test pytest suite** that previously did not exist.

Tooling at the time: `ruff` 0.15, `black` 26.5, `pytest` 9.1, `mypy` 2.3,
configured in `pyproject.toml`.

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
| `B905` (`zip(strict=)`) | Every call site is either an intentional offset pairing `zip(xs, xs[1:])` — where `strict=True` would raise on *every* call — or a comparison that already reports length mismatch with a better message than an exception. (This exemption originally also cited the Python 3.8 target, since `strict=` is 3.10+. That half is obsolete as of 3.0, which requires 3.10+; the reasoning above stands on its own.) |
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
| `RUF012` — 8 mutable class attributes in the GUI | §9.1 | `ruff check --select RUF012` is clean; the rule is enabled repo-wide |
| `mypy` advisory / crashing | §9.4 | already struck through in §9; it gates, and is clean on 28 files |
| Module split | §8.1 | §8 carries its own COMPLETE note; 6 subpackages, 28 modules |
| i18n string extraction | §8.4 | **partially** — plumbing, 141 marked strings, `.pot`, and `tools/make_pot.py` shipped in 3.0. The remaining 127 f-string sites are specified in `I18N_BRIEF.md` |
| **CI** | §9.3 | `.github/workflows/ci.yml` runs the full gate list (ruff, black, mypy, `check_undefined`, `make_pot --check`, pytest) on Python 3.10 and 3.13. It installs `zstandard` deliberately: without it 3 bytecode tests skip, and a skipped test proves nothing |
| **Coverage measurement** | §8.5 | `[tool.coverage.*]` configured in `pyproject.toml`, with branch coverage and the GUI omitted (it cannot be imported without Tk, so including it would report a meaningless ~0%). CI publishes an HTML report as an artifact |

### Still outstanding

| Item | Where | Measured at 3.0 |
|---|---|---|
| **`print` → logging** | §8.3 | 75 `print()` in the engine vs 8 `get_logger` uses |
| **Typing + PEP 257 on the legacy scripts** | §8.2 | `mlox_subset_sort.py` and the GUI still carry `"D", "ANN"` per-file exemptions; only `mlox_subset/` meets the strict standard |
| **Split the GUI module** | §9.2 | Flagged at ~4,100 lines; now **5,586** |
| **Oversized functions** | §15 | See below |
| **Delete the re-export shim** | §15 | 58 names re-exported via 10 `from mlox_subset` imports |

### Three of those need a correction or a caveat

**The oversized functions moved, and a bigger one is now unlisted.** §15 named
`build_and_sort` (456 lines, depth 5) and `generate_customizations_toml` (311,
depth 6). Both relocated during the split and are essentially unchanged — 457
and 312 lines, same depths, now in `mlox_subset/sort/engine.py` and
`mlox_subset/configurator/emit.py`. But the largest function in the codebase is
**`compute_plan` at 545 lines, depth 5**, which §15 never named. It is also
where the `_`-shadowing `NameError` hid until the gettext marker exposed it
(see 3.0's changelog), which is weak but real evidence that its size costs
something. `_build_controls` in the GUI is 378 lines but only depth 1 — long
rather than tangled, and correspondingly lower priority.

**Deleting the shim is now a breaking change.** §15 is right that
`core.build_and_sort` and `mlox_subset.sort.build_and_sort` are two obvious
ways to reach one function. But 3.0's changelog states publicly that
`mlox_subset_sort.py` keeps every existing `core.<name>` call site working.
Removing it is therefore a 4.0-scoped change with a deprecation period, not
tidy-up. Recorded so the next reader does not treat §15 as licence to delete it.

**`print` → logging and the i18n f-strings touch the same lines.** The 127
sites in `I18N_BRIEF.md` are mostly `print(f"...")` in report output; §8.3 wants
those same calls demoted or routed through a logger. Doing them as one pass is
substantially cheaper than two, and avoids re-litigating which output is a
user-facing report and which is a diagnostic — a question both jobs must answer
identically.

### Suggested order, if someone picks this up

1. **Set the coverage floor.** The config landed without a `fail_under`, on
   purpose: the honest number comes from the first CI run, not from a figure
   guessed at a desk. Read it off that run and set the floor slightly below,
   so it ratchets upward instead of blocking the next PR.
2. **i18n f-strings + `print` → logging together**, per `I18N_BRIEF.md`, whose
   first step (a static placeholder checker) is the safety net for both.
3. **GUI split** — the largest and most disruptive; worth having 1–2 first,
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

**The checker came first**, per `I18N_BRIEF.md`'s "build this *before*
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

`mlox_subset_sort_gui.py`: **5,765 -> 3,203 lines.** New subpackage
`mlox_subset/gui/`:

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
(`<app>/mlox_subset/gui/__init__.py` -> two parents up) instead of the GUI
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
`except tk.TclError: pass` handlers now living under `mlox_subset/`; each
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
  `mlox_subset_sort.py` (3,810 lines) and the GUI file (3,203); mechanical
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
| `compute_plan` | **644** | **105** | 10 stage helpers in `mlox_subset_sort.py` |
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

### The typing pass (§8.2) -- complete for `mlox_subset/gui/`, partial for the scripts

**`mlox_subset/gui/` now meets the package's strict standard.** All four
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
| `mlox_subset_sort.py` | 25/89 | 19/200 | 337 |
| `mlox_subset_sort_gui.py` | 87/118 | 2/84 | 165 |

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
  `mlox_subset_sort-3.0.0-py3-none-any.whl` with all 7 subpackages and both
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
`AttributeError` on `from mlox_subset import *` and a silent hole in what the
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

`TYPING_BRIEF.md` is the hand-off for the last open item -- the `D`/`ANN`
exemptions on the two legacy scripts. It records the measured scope (502
findings, 263 of them `ANN001` argument annotations across 7,290 lines), what
was already done so it is not redone, the per-module method that worked for
`mlox_subset/gui/`, a definition of done, and the five traps that actually bit
during that pass -- including the two most expensive: `object` as a
placeholder annotation turns one missing hint into five new errors, and `list`
invariance rejects the `list[str]` every caller builds.

It follows `I18N_BRIEF.md`'s format deliberately. That brief was written the
same way and the work landed from it in one session, along its suggested order,
with its predicted trap firing exactly where it said it would.

### Gates at the end of this pass

800 tests: 799 passed, 1 deliberate skip. Green: ruff, black, mypy (33 files),
`check_undefined`, `check_placeholders`, `make_pot --check` (269 messages),
coverage 54.45% against the 52% floor, and `python -m build` produces a valid
wheel.

## 20. The typing pass, finished: both legacy scripts, and what mypy caught

`TYPING_BRIEF.md`'s work, done along its own suggested order. Both scripts now
meet the package standard, so **every `D`/`ANN` per-file ignore and every mypy
`ignore_errors` override is deleted** -- not relaxed, deleted. mypy gates
**35 files**, the whole codebase.

| File | Returns typed | Args typed | D/ANN |
|---|---|---|---|
| `mlox_subset_sort.py` | 25/89 → **89/89** | 19/200 → **200/200** | 337 → **0** |
| `mlox_subset_sort_gui.py` | 87/118 → **118/118** | 2/84 → **84/84** | 165 → **0** |

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

`I18N_BRIEF.md`, `THEMING_BRIEF.md` and `TYPING_BRIEF.md` all said COMPLETED
and together ran to 562 lines of "hand this to a fresh session" instructions
for work that is done. A hand-off note for finished work is worse than no note:
a reader picks it up expecting a task. Each is now an ~18-line stub pointing at
the `CODE_REVIEW.md` section that holds the real record, and the live code
references (in `engine.py`, `check_placeholders.py`, the CI workflow and the
placeholder tests) were redirected to those sections so nothing dangles. The
stubs exist only to keep those references resolving and can be deleted
outright.

This log's own references to the briefs are left alone: it is append-only, and
they were correct when written.

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

New package `mlox_subset/tes3fields/` decodes six more fields:

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
*reasoning* was fine — the defect was an unexamined assumption, and it survived
two review passes precisely because it looked settled.

### What the shim actually was

The phrase "62 re-exported names" was itself imprecise, and the imprecision
mattered. Measured by parsing the module and asking which imported names its
own body ever mentions:

| | Count | Disposition |
|---|---|---|
| Imported from `mlox_subset/` | 62 | |
| ...used by the engine itself | **26** | **Kept.** Ordinary imports; never re-exports. |
| ...never used, imported to be re-exported | **36** | **Deleted.** |

So a third of the "shim" was not a shim. Deleting all 62, which is what the
item as written invited, would have broken the engine.

### Method

1. **Rewire callers first, delete second.** Every `core.<name>` reference in
   `mlox_subset_sort_gui.py`, `mlox_subset/gui/` and `tests/` that resolved to
   an import was repointed at the module the name really lives in — **42
   distinct names across 12 files**.
2. **Delete only names the engine never mentions**, recomputed after the
   rewire rather than from the list in step 1.
3. **Remove `F401` from the per-file exemption**, which is the step that makes
   the result durable. While it stood, an unused import in this file was the
   house style and therefore invisible.

### Two things worth recording

**`F401` immediately found a real defect.** With the exemption gone, ruff
reported `sys` imported but unused — genuinely unused, its only remaining
mention being the word "sys.stdout" inside a docstring. It had been dead since
the CLI moved from `sys.exit()` to `raise SystemExit`, and the exemption had
been hiding it. This is the argument for the whole exercise in miniature: a
blanket exemption does not just permit the thing it was written for, it
silences everything that looks like it.

**Aliased imports nearly caused a silent break.** The rewiring map was built by
parsing the engine's imports, and deliberately skipped aliased ones
(`format_version as _format_version`) rather than guess at intent. Seven names
were aliased, and two of them — `core._format_version` and `core._is_master_file`
— had live call sites in the tests. The safety net was refusing to delete any
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
fixpoint — *a function must declare `core` if and only if its body loads it* —
which converged in one pass. The lesson is the ordinary one about regex and
syntax, and it is here because the first two attempts both left a green-looking
tree with a broken test file underneath.

### The end state, stated as something checkable

`core.<name>` is still used 41 times, and that is correct: every one of those
names is **defined in `mlox_subset_sort.py` itself** — `compute_plan`,
`lint_plugins`, the scanners, the CLI surface. The GUI reaching into the engine
for engine things is not the shim; the shim was the engine standing in front of
`mlox_subset/` for names it never touched.

The check that says so, and that would catch a regression:

* no name reached via `core.` is one the engine merely imports, and
* no import in the engine is unused (`F401`, now enforced).

Together those two are the invariant. Either one alone can be satisfied by a
shim creeping back.

### Gates

870 tests: **869 passed, 1 skipped** — including the differential baseline's 41
pinned observations, which reproduced unchanged across the rewire and are the
reason this was safe to do mechanically at all. ruff (now with `F401` live on
the engine), black, mypy (38 files), `check_undefined`, `check_placeholders`,
`make_pot --check` (312 messages, unchanged).

---

## 24. Visualising conflicts (`mlox_subset/viz/`)

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
