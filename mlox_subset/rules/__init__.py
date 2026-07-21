"""mlox rule handling: pattern matching, parsing, and predicate evaluation.

This package is being populated incrementally from the engine module. Each
piece is moved with its behaviour pinned by ``tests/test_differential.py``, so
a move that changes an answer fails loudly rather than shipping a different
load order.

This package is the one place these names live. The engine module used to
re-export them so ``core.mlox_pattern_to_regex`` also resolved; that shim was
removed in 3.0 and every caller -- engine, GUI and tests -- now imports from
here (``CODE_REVIEW.md`` §23).
"""

from __future__ import annotations

from mlox_subset.rules.expressions import (
    describe_node,
    load_rules_raw_text,
    parse_mlox_lisp,
    tokenize_mlox_logic,
)
from mlox_subset.rules.parser import (
    ORDER_NAME_RE,
    TOP_KEYWORDS,
    TOP_RE,
    load_rule_blocks,
    parse_mlox_file,
    strip_comment,
)
from mlox_subset.rules.patterns import (
    MLOX_VERSION_PATTERN,
    mlox_pattern_to_regex,
    pattern_has_meta,
)
from mlox_subset.rules.predicates import (
    check_predicates,
    evaluate_node,
    get_triggered_plugins,
)

__all__ = [
    "MLOX_VERSION_PATTERN",
    "ORDER_NAME_RE",
    "TOP_KEYWORDS",
    "TOP_RE",
    "check_predicates",
    "describe_node",
    "evaluate_node",
    "get_triggered_plugins",
    "load_rule_blocks",
    "load_rules_raw_text",
    "mlox_pattern_to_regex",
    "parse_mlox_file",
    "parse_mlox_lisp",
    "pattern_has_meta",
    "strip_comment",
    "tokenize_mlox_logic",
]
