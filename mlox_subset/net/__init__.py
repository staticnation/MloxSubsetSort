"""Network access: rule-file and curated-order updates.

All downloading goes through :func:`~mlox_subset.net.updaters.fetch_url_bytes`,
which enforces a scheme allow-list and a size cap on URLs that are, by design,
user-configurable.
"""

from __future__ import annotations

from mlox_subset.net.updaters import (
    ALLOWED_URL_SCHEMES,
    MAX_DOWNLOAD_BYTES,
    PLUGIN_ORDER_URLS,
    RULES_REPO,
    RULES_URL_TEMPLATE,
    fetch_url_bytes,
    rule_file_ages,
    update_plugin_order_yml,
    update_rule_files,
)

__all__ = [
    "ALLOWED_URL_SCHEMES",
    "MAX_DOWNLOAD_BYTES",
    "PLUGIN_ORDER_URLS",
    "RULES_REPO",
    "RULES_URL_TEMPLATE",
    "fetch_url_bytes",
    "rule_file_ages",
    "update_plugin_order_yml",
    "update_rule_files",
]
