"""Behaviour-neutrality guard for the module split.

The engine is being broken up into packages (``rules/``, ``sort/``,
``configurator/``, ...). Every one of those moves is *supposed* to be purely
mechanical, but "supposed to be" is exactly the assumption that quietly breaks
a working tool. These tests pin the engine's observable behaviour on real
inputs to a stored baseline, so a refactor that changes an answer fails loudly
instead of silently shipping a different load order.

The baseline is a set of hashes committed under ``tests/baselines/``. It is
regenerated deliberately, never automatically::

    python -m pytest tests/test_differential.py --update-baseline

Regenerating is legitimate when behaviour was *meant* to change -- a bug fix
alters output on purpose. The point is that it takes a decision, and the diff
in the baseline file shows exactly what moved.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_integration import _find_data_dir

BASELINE_DIR = Path(__file__).resolve().parent / "baselines"
BASELINE_FILE = BASELINE_DIR / "engine_behaviour.json"


def _digest(value: object) -> str:
    """Hash a value stably, so ordering changes are caught but noise is not.

    Args:
        value: Any JSON-serialisable structure.

    Returns:
        A hex SHA-256 digest of the value's canonical JSON form.
    """
    canonical = json.dumps(value, sort_keys=False, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_baseline() -> dict[str, str]:
    """Read the stored baseline, or an empty mapping when none exists."""
    if not BASELINE_FILE.exists():
        return {}
    return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))


def _save_baseline(data: dict[str, str]) -> None:
    """Write the baseline, creating its directory if needed."""
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


@pytest.fixture(scope="module")
def data_dir():
    """Real input files, or skip when none are available."""
    found = _find_data_dir()
    if found is None:
        pytest.skip("no real input data available")
    return found


@pytest.fixture(scope="module")
def observations(core, data_dir) -> dict[str, str]:
    """Compute every pinned behaviour once, as ``{name: digest}``.

    Each entry is a distinct engine surface the split could disturb. They are
    deliberately end-to-end rather than unit-level: a mechanical move should
    change nothing a caller can observe.

    Args:
        core: The engine module.
        data_dir: Directory holding real ``openmw.cfg`` and rule files.

    Returns:
        A mapping of observation name to digest.
    """
    _lines, _cpos, content_order, _dpos, data_order = core.read_cfg(data_dir / "openmw.cfg")
    base_order = [core.basename_if_plugin(value) for value, _line in content_order]
    base_order = [name for name in base_order if name]
    rule_paths = [data_dir / "mlox_base.txt", data_dir / "mlox_user.txt"]
    blocks, nearstart, nearend = core.load_rule_blocks(rule_paths)

    out: dict[str, str] = {
        "cfg.content_order": _digest(base_order),
        "cfg.data_line_count": _digest(len(data_order)),
        "rules.block_count": _digest(len(blocks)),
        "rules.block_keywords": _digest([b[0] for b in blocks][:2000]),
        "rules.nearstart": _digest(nearstart),
        "rules.nearend": _digest(nearend),
    }

    # The central promise: sorting with no custom mods must return the
    # curated order untouched, and must be deterministic.
    def _sort() -> list:
        """Sort with no custom mods, exactly as the real caller does."""
        return core.build_and_sort(base_order, [], blocks, {}, nearstart=nearstart, nearend=nearend)

    sorted_order = _sort()
    out["sort.identity_no_customs"] = _digest(sorted_order)
    out["sort.is_stable"] = _digest(_sort() == sorted_order)
    # The tool's central promise, pinned explicitly rather than only by hash.
    out["sort.curated_order_untouched"] = _digest(sorted_order == base_order)

    # Pattern translation drives all rule matching; pin a spread of shapes.
    patterns = [
        "Morrowind.esm",
        "Bloodmoon.esm",
        "*.esp",
        "Tribunal*",
        "foo?bar.esp",
        "[Aa]ntares*.esp",
        "Mod With Spaces.esp",
        "weird+chars(1).esp",
    ]
    out["rules.pattern_regexes"] = _digest(
        [core.mlox_pattern_to_regex(p).pattern for p in patterns]
    )
    out["rules.pattern_has_meta"] = _digest([core.pattern_has_meta(p) for p in patterns])

    # --- predicate evaluation -------------------------------------------
    # The [Requires]/[Conflict]/[Note] language is the most intricate part of
    # the rules code, so it is pinned at every stage: tokens, AST, evaluation,
    # attribution and rendering. Pinning only the end result would let a
    # tokeniser change hide behind an evaluator change that cancels it out.
    expressions = [
        "Morrowind.esm",
        "[ALL Tribunal.esm Bloodmoon.esm]",
        "[ANY Foo.esp Bar.esp]",
        "[NOT Missing.esp]",
        "[ALL [ANY A.esp B.esp] [NOT C.esp]]",
        # A DESC regex may itself contain ']', which naive bracket matching
        # would treat as the end of the token.
        "[DESC /[Tt]ribunal/ Foo.esp]",
        "[DESC !/nomask/ Bar.esp]",
        "[VER < 2.0 SomeMod.esp]",
        "[VER = 1.0a Other.esp]",
        "[SIZE 12345 Sized.esp]",
        "[SIZE !999 Sized.esp]",
        "[MWSE-LUA /require/ Script.esp]",
        "[ANY Wild*.esp plugin-?.esp Mod <VER>.esp]",
        "/a free-text DESC message/",
    ]
    token_lists = [core.tokenize_mlox_logic(text) for text in expressions]
    out["predicates.tokens"] = _digest(token_lists)

    # parse_mlox_lisp consumes its input via pop(0), so each call gets a copy.
    asts = [core.parse_mlox_lisp(list(tokens)) for tokens in token_lists]
    out["predicates.ast"] = _digest(asts)
    out["predicates.describe"] = _digest([core.describe_node(node) for node in asts])

    active = {
        "morrowind.esm",
        "tribunal.esm",
        "bloodmoon.esm",
        "a.esp",
        "foo.esp",
        "wildthing.esp",
    }
    out["predicates.evaluate"] = _digest([core.evaluate_node(node, active, None) for node in asts])
    out["predicates.triggered"] = _digest(
        [sorted(core.get_triggered_plugins(node, active, None)) for node in asts]
    )

    # End-to-end: every predicate in the real rule files, against the real
    # sorted order. This is the observation that would actually catch a
    # regression a user would notice.
    rules_text = core.load_rules_raw_text(rule_paths)
    out["predicates.rules_text_length"] = _digest(len(rules_text))
    warnings = core.check_predicates(rules_text, sorted_order)
    out["predicates.warning_count"] = _digest(len(warnings))
    out["predicates.warnings"] = _digest(warnings)

    # Only a handful of predicates actually fire against this load order, so
    # the warning list alone exercises very little of the evaluator. Push
    # *every* predicate body in the real rule files through tokenise -> parse
    # -> evaluate as well, which covers thousands of real expressions
    # including the shapes no synthetic sample would think to include.
    # --- version handling ------------------------------------------------
    # _format_version produces the fixed-width string that [VER] comparisons
    # sort on, so a change here silently changes which version rules fire.
    # Ported from mlox's format_version; the odd cases matter more than the
    # ordinary ones.
    versions = [
        "1",
        "1.0",
        "1.2.3",
        "1.2.3.4",  # more parts than the format keeps
        "2_1",  # underscore delimiter
        "3-4",  # hyphen delimiter
        "1.0a",  # alpha suffix
        "10b",
        "0",
        "",  # unparseable -> ""
        "not.a.version",
        "1.x",
    ]
    out["versions.format"] = _digest([core._format_version(v) for v in versions])
    # The comparison these feed is lexicographic, so pin the induced ordering
    # too -- formatting could change while still sorting the same, or vice
    # versa, and only one of those is a regression.
    formatted = [core._format_version(v) for v in versions if core._format_version(v)]
    out["versions.ordering"] = _digest(sorted(formatted))

    # --- MOMW curated-list data ------------------------------------------
    # plugin-order.yml is the source of truth for which plugins belong to
    # which curated list. Parsing it wrongly would silently reclassify a
    # curated plugin as a custom one -- the exact failure the tool exists to
    # prevent.
    order_yml = data_dir / "plugin-order.yml"
    if order_yml.exists():
        entries = core.parse_plugin_order_yml(order_yml)
        out["momw.entry_count"] = _digest(len(entries))
        out["momw.entries"] = _digest(entries)
        out["momw.needs_cleaning"] = _digest(sorted(core.needs_cleaning_set(entries)))
        lists = sorted({name for entry in entries for name in (entry.get("lists") or [])})
        out["momw.list_names"] = _digest(lists)
        out["momw.curated_per_list"] = _digest(
            {name: core.curated_for_list(entries, name) for name in lists}
        )

    # --- URL validation ---------------------------------------------------
    # fetch_url_bytes guards user-configurable URLs read from a settings file
    # and environment variables. Its rejection paths are pure -- no network is
    # touched when a scheme is refused -- so they can be pinned directly. A
    # refactor that let file:// or a missing host through would be a security
    # regression, not a style one.
    hostile = [
        "file:///etc/passwd",
        "file://C:/Windows/win.ini",
        "ftp://example.com/x",
        "gopher://example.com/x",
        "data:text/plain,hello",
        "javascript:alert(1)",
        "HTTP://",  # allowed scheme, no host
        "https://",  # allowed scheme, no host
        "not-a-url",
        "",
    ]
    rejections = []
    for url in hostile:
        try:
            core.fetch_url_bytes(url, timeout=1)
        except ValueError as exc:  # noqa: PERF203 - per-URL isolation is the point
            rejections.append((url, type(exc).__name__, str(exc)[:60]))
        except Exception as exc:
            rejections.append((url, type(exc).__name__, "non-ValueError"))
        else:  # pragma: no cover - would mean the guard let it through
            rejections.append((url, "ALLOWED", ""))
    out["net.url_rejections"] = _digest(rejections)
    out["net.allowed_schemes"] = _digest(sorted(core.ALLOWED_URL_SCHEMES))
    out["net.max_download_bytes"] = _digest(core.MAX_DOWNLOAD_BYTES)

    out.update(_configurator_observations(core, data_dir))

    bodies = _real_predicate_bodies(core, rules_text)
    out["predicates.corpus_size"] = _digest(len(bodies))
    corpus_tokens = [core.tokenize_mlox_logic(body) for body in bodies]
    out["predicates.corpus_tokens"] = _digest(corpus_tokens)
    corpus_asts = [core.parse_mlox_lisp(list(tokens)) for tokens in corpus_tokens]
    out["predicates.corpus_ast"] = _digest(corpus_asts)
    active_lower = {name.lower() for name in sorted_order}
    out["predicates.corpus_evaluate"] = _digest(
        [core.evaluate_node(node, active_lower, None) for node in corpus_asts]
    )
    return out


def _configurator_observations(core, data_dir: Path) -> dict[str, str]:
    """Pin the code that rewrites ``openmw.cfg``.

    This is the highest-consequence surface in the tool: it edits the file
    OpenMW actually loads. Two failures already found here were silent data
    loss, not crashes -- a ``removeContent`` string iterated character by
    character and wiped most of the cfg, and a non-UTF-8 ``data=`` path was
    destroyed on rewrite. Both would have passed any test that only checked
    "did it run". So the pins below capture *content*, not success.

    Args:
        core: The engine module.
        data_dir: Directory holding a real ``openmw.cfg`` and customisations.

    Returns:
        Observation name to digest, for whichever inputs are available.
    """
    out: dict[str, str] = {}

    # Line/value helpers. Quoting and normalisation decide what OpenMW sees,
    # so their exact output matters more than it looks.
    cfg_lines = [
        "content=Morrowind.esm",
        "content = Spaced.esp ",
        'data="C:\\Games\\Mods\\Some Mod"',
        "data=/home/user/mods/plain",
        "data=/trailing/slash/",
        "# comment=not a value",
        "",
        "fallback=Weather_Clear,1",
    ]
    out["cfg.line_values"] = _digest([core.cfg_line_value(line) for line in cfg_lines])
    data_values = [
        '"C:\\Games\\Mods\\Some Mod"',
        "/home/user/mods/plain",
        "/trailing/slash/",
        '"quoted with spaces"',
        "unquoted with spaces",
    ]
    out["cfg.normalize_data_path"] = _digest(
        [core.normalize_data_path(value) for value in data_values]
    )
    out["cfg.format_data_line"] = _digest(
        [core.format_data_line(v, quoted) for v in data_values for quoted in (True, False)]
    )
    out["cfg.detect_data_quoting"] = _digest(
        [
            core.detect_data_quoting(['data="a"', 'data="b"']),
            core.detect_data_quoting(["data=a", "data=b"]),
            core.detect_data_quoting(['data="a"', "data=b"]),
            core.detect_data_quoting([]),
        ]
    )
    out["cfg.toml_value"] = _digest(
        [core.toml_value(v) for v in ["plain", 'has "quotes"', "back\\slash", "", "C:\\path\\here"]]
    )

    # The string-vs-array guard. A regression here silently deletes cfg lines,
    # so both the accepted and rejected shapes are pinned, with their errors.
    string_list_cases: list[object] = []
    for value in (["A.esp", "B.esp"], "A.esp", None, 42, [], ["A.esp", 7]):
        errors: list[str] = []
        result = core.customization_string_list({"removeContent": value}, "removeContent", errors)
        string_list_cases.append((repr(value), result, errors))
    out["cfg.customization_string_list"] = _digest(string_list_cases)

    out["cfg.configurator_remove_matches"] = _digest(
        [
            core.configurator_remove_matches("Mod.esp", "content=Mod.esp"),
            core.configurator_remove_matches("Mod.esp", "content=Other.esp"),
            core.configurator_remove_matches("Mod", "content=Mod.esp"),
            core.configurator_remove_matches("", "content=Mod.esp"),
        ]
    )

    # End-to-end: apply the real customisations TOML to the real cfg. This is
    # the observation a user would notice breaking.
    cfg_path = data_dir / "openmw.cfg"
    toml_path = data_dir / "momw-customizations.toml"
    if cfg_path.exists() and toml_path.exists():
        lines, _cpos, _co, _dpos, _do = core.read_cfg(cfg_path)
        toml_text = toml_path.read_text(encoding="utf-8", errors="replace")
        result = core.simulate_configurator_apply(lines, toml_text)
        # The simulation returns a result whose shape varies by version; digest
        # it wholesale via the JSON default=str fallback rather than assuming.
        out["configurator.simulate_real"] = _digest(result)
    return out


def _real_predicate_bodies(core, rules_text: str, limit: int = 4000) -> list[str]:
    """Every ``[Requires]``/``[Conflict]``/``[Note]`` body in the rule files.

    Extracted the same way :func:`check_predicates` does -- by splitting on
    rule headers -- so the corpus is exactly what the evaluator really sees.

    Args:
        core: The engine module.
        rules_text: Concatenated raw text of the rule files.
        limit: Cap on bodies collected, to keep the observation quick.

    Returns:
        Body strings, in file order.
    """
    wanted = {"requires", "conflict", "note"}
    matches = list(core.TOP_RE.finditer(rules_text))
    bodies: list[str] = []
    for index, match in enumerate(matches):
        if match.group(1).lower() not in wanted:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(rules_text)
        body = rules_text[start:end].strip()
        if body:
            bodies.append(body)
        if len(bodies) >= limit:
            break
    return bodies


def test_baseline_exists(observations, request):
    """A missing baseline must fail, not silently pass.

    Otherwise the guard would be vacuous exactly when it matters most -- on a
    fresh checkout, mid-refactor.
    """
    if request.config.getoption("--update-baseline"):  # pragma: no cover
        pytest.skip("generating the baseline now")
    if not _load_baseline():
        pytest.fail(
            "no differential baseline stored. Generate it from KNOWN-GOOD code "
            "with: python -m pytest tests/test_differential.py --update-baseline"
        )


@pytest.mark.parametrize(
    "name",
    [
        "cfg.content_order",
        "cfg.data_line_count",
        "rules.block_count",
        "rules.block_keywords",
        "rules.nearstart",
        "rules.nearend",
        "sort.identity_no_customs",
        "sort.curated_order_untouched",
        "sort.is_stable",
        "rules.pattern_regexes",
        "rules.pattern_has_meta",
        "predicates.tokens",
        "predicates.ast",
        "predicates.describe",
        "predicates.evaluate",
        "predicates.triggered",
        "predicates.rules_text_length",
        "predicates.warning_count",
        "predicates.warnings",
        "predicates.corpus_size",
        "predicates.corpus_tokens",
        "predicates.corpus_ast",
        "predicates.corpus_evaluate",
        "versions.format",
        "versions.ordering",
        "momw.entry_count",
        "momw.entries",
        "momw.needs_cleaning",
        "momw.list_names",
        "momw.curated_per_list",
        "net.url_rejections",
        "net.allowed_schemes",
        "net.max_download_bytes",
        "cfg.line_values",
        "cfg.normalize_data_path",
        "cfg.format_data_line",
        "cfg.detect_data_quoting",
        "cfg.toml_value",
        "cfg.customization_string_list",
        "cfg.configurator_remove_matches",
        "configurator.simulate_real",
    ],
)
def test_behaviour_matches_baseline(observations, request, name):
    """Each pinned engine behaviour still produces its recorded result."""
    if request.config.getoption("--update-baseline"):  # pragma: no cover
        pytest.skip("updating baseline")
    baseline = _load_baseline()
    if name not in baseline:
        pytest.skip(f"{name} not in baseline; regenerate to include it")
    assert observations[name] == baseline[name], (
        f"{name} changed. If this was intentional, regenerate the baseline; "
        f"if not, the refactor altered behaviour."
    )


def test_update_baseline(observations, request):  # pragma: no cover
    """Rewrite the baseline. Only does anything under ``--update-baseline``."""
    if not request.config.getoption("--update-baseline"):
        pytest.skip("pass --update-baseline to regenerate")
    _save_baseline(observations)
