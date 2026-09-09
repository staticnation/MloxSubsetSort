"""Network access: rule-file and curated-order updates.

All downloading goes through :func:`~wraithguard.net.updaters.fetch_url_bytes`,
which enforces a scheme allow-list and a size cap on URLs that are, by design,
user-configurable.
"""

from __future__ import annotations

from wraithguard.net.updaters import (
    ALLOWED_URL_SCHEMES,
    API_CFG_GENERATOR_URL,
    DEFAULT_MOMW_HOST,
    MANAGED_RULE_FILES,
    MAX_DOWNLOAD_BYTES,
    PLUGIN_ORDER_URLS,
    RULES_REPO,
    RULES_URL_TEMPLATE,
    fetch_list_data_paths,
    fetch_url_bytes,
    rule_file_ages,
    update_plugin_order_yml,
    update_rule_files,
)

__all__ = [
    "ALLOWED_URL_SCHEMES",
    "API_CFG_GENERATOR_URL",
    "DEFAULT_MOMW_HOST",
    "MANAGED_RULE_FILES",
    "MAX_DOWNLOAD_BYTES",
    "PLUGIN_ORDER_URLS",
    "RULES_REPO",
    "RULES_URL_TEMPLATE",
    "ViewerError",
    "fetch_list_data_paths",
    "fetch_url_bytes",
    "rule_file_ages",
    "update_plugin_order_yml",
    "update_rule_files",
]
