"""Parsing a Merged Lands ``.mergedlands.toml`` sidecar into settings.

``parse_meta`` refuses every malformed shape rather than defaulting, because a
typo in a strategy name silently defaulting to ``Auto`` is usually the opposite
of why someone wrote the file. Each refusal is pinned here, plus the two
``_load_toml`` read failures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from wraithguard.land.merge import ConflictStrategy
from wraithguard.land.meta import (
    LAYER_NAMES,
    SUPPORTED_VERSION,
    MetaError,
    PluginMeta,
    _load_toml,
    parse_meta,
)

if TYPE_CHECKING:
    from pathlib import Path

_LAYER = next(iter(LAYER_NAMES))


def test_a_valid_document_parses() -> None:
    """A well-formed sidecar yields its layers and meta type."""
    meta = parse_meta(
        {"version": SUPPORTED_VERSION, _LAYER: {"included": False, "conflict_strategy": "Overwrite"}}
    )
    assert _LAYER in meta.layers
    assert meta.layers[_LAYER].included is False


def test_an_unknown_version_is_refused() -> None:
    """A newer version may mean something different by the same keys."""
    with pytest.raises(MetaError, match="version"):
        parse_meta({"version": "999"})


def test_an_unknown_meta_type_is_refused() -> None:
    """A meta_type outside the known set is rejected, not defaulted."""
    with pytest.raises(MetaError, match="meta_type"):
        parse_meta({"meta_type": "Nonsense"})


def test_a_non_layer_key_is_refused() -> None:
    """A table that is not a landscape layer is a mistake worth reporting."""
    with pytest.raises(MetaError, match="not a landscape layer"):
        parse_meta({"not_a_layer": {}})


def test_a_layer_that_is_not_a_table_is_refused() -> None:
    """A layer key must map to a table of settings."""
    with pytest.raises(MetaError, match="must be a table"):
        parse_meta({_LAYER: "just a string"})


def test_a_non_boolean_included_is_refused() -> None:
    """``included`` is a flag; anything else is rejected."""
    with pytest.raises(MetaError, match="true or false"):
        parse_meta({_LAYER: {"included": "yes"}})


def test_an_unknown_conflict_strategy_is_refused() -> None:
    """A misspelled strategy would silently become Auto, so it is refused."""
    with pytest.raises(MetaError, match="conflict_strategy"):
        parse_meta({_LAYER: {"conflict_strategy": "Blend"}})


def test_load_toml_rejects_invalid_toml(tmp_path: Path) -> None:
    """A sidecar that is not valid TOML is reported, not silently skipped."""
    bad = tmp_path / "bad.mergedlands.toml"
    bad.write_text("this = = not toml", encoding="utf-8")
    with pytest.raises(MetaError, match="not valid TOML"):
        _load_toml(bad)


def test_load_toml_reports_a_read_error(tmp_path: Path) -> None:
    """A path that cannot be opened surfaces as a MetaError, not an OSError."""
    with pytest.raises(MetaError, match="could not read"):
        _load_toml(tmp_path / "does-not-exist.toml")


def test_strategy_for_an_unmatched_layer_is_auto() -> None:
    """A layer mask matching no configured layer falls back to Auto."""
    meta = PluginMeta(meta_type="Auto", layers={})
    assert meta.strategy_for(0) is ConflictStrategy.AUTO
