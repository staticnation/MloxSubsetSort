"""Read, simulate and emit ``openmw.cfg`` customisations.

Split by concern: :mod:`~wraithguard.configurator.cfglines` for individual
lines, :mod:`~wraithguard.configurator.datapaths` for VFS ordering,
:mod:`~wraithguard.configurator.apply` for the dry-run simulation, and
:mod:`~wraithguard.configurator.emit` for generating the TOML.
"""

from __future__ import annotations

from wraithguard.configurator.apply import (
    REMOVE_KEYS,
    configurator_remove_matches,
    customization_string_list,
    preview_configurator_result,
    simulate_configurator_apply,
)
from wraithguard.configurator.cfglines import (
    BASE_GAME_MASTERS,
    cfg_line_value,
    curated_covers,
    detect_data_quoting,
    escape_cfg_value,
    extract_data_path_value,
    find_anchor_index,
    format_data_line,
    is_base_data_path,
    normalize_data_path,
    orphan_cfg_entries,
    toml_value,
    unescape_cfg_value,
)
from wraithguard.configurator.datapaths import (
    infer_data_path_anchors,
    insert_data_paths,
)
from wraithguard.configurator.emit import generate_customizations_toml

__all__ = [
    "BASE_GAME_MASTERS",
    "REMOVE_KEYS",
    "cfg_line_value",
    "configurator_remove_matches",
    "curated_covers",
    "customization_string_list",
    "detect_data_quoting",
    "escape_cfg_value",
    "extract_data_path_value",
    "find_anchor_index",
    "format_data_line",
    "generate_customizations_toml",
    "infer_data_path_anchors",
    "insert_data_paths",
    "is_base_data_path",
    "normalize_data_path",
    "orphan_cfg_entries",
    "preview_configurator_result",
    "simulate_configurator_apply",
    "toml_value",
    "unescape_cfg_value",
]
