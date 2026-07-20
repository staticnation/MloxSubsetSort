"""Load-order sorting: graph construction and the sort engine.

Being populated incrementally from the engine module, each move guarded by
``tests/test_differential.py``. The engine re-exports these names so existing
callers keep working during the transition.
"""

from __future__ import annotations

from mlox_subset.sort.engine import build_and_sort
from mlox_subset.sort.graph import expand_pattern, is_master_file, would_create_cycle

__all__ = [
    "build_and_sort",
    "expand_pattern",
    "is_master_file",
    "would_create_cycle",
]
