"""Reading Morrowind plugin files: location, metadata, binary records.

Depends only on the foundation (``versions``, ``tracing``), never on
``rules/`` or ``sort/``. That direction is deliberate: the predicate evaluator
in ``rules/`` needs plugin metadata, so plugins must not need rules back.
"""

from __future__ import annotations

from mlox_subset.plugins.metadata import (
    PLUGIN_EXTS,
    TES3_MIN_PLUGIN_SIZE,
    PluginFileIndex,
    list_plugins_in_dir,
    plugin_version,
    read_plugin_description,
)

__all__ = [
    "PLUGIN_EXTS",
    "TES3_MIN_PLUGIN_SIZE",
    "PluginFileIndex",
    "list_plugins_in_dir",
    "plugin_version",
    "read_plugin_description",
]
