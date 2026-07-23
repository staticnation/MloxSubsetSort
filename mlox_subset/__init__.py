"""Shared foundation package for MLOX Subset Sort.

This package holds the cross-cutting concerns that both the engine
(``mlox_subset_sort.py``) and the GUI (``mlox_subset_sort_gui.py``) depend
on. It is deliberately import-light and free of any Tkinter dependency so
that the engine, the command line and the test suite can use it headlessly.

Modules:
    i18n: Translation catalogue lookup and the ``_()`` marker function.
    logging_setup: Application logging configuration and level handling.

Example:
    >>> from mlox_subset import get_logger, setup_logging, _
    >>> setup_logging(verbosity=1)
    >>> get_logger(__name__).info(_("Sorting %(count)d plugins"), {"count": 3})

"""

from __future__ import annotations

from mlox_subset.i18n import (
    available_languages,
    get_language,
    gettext as _,
    ngettext,
    set_language,
)
from mlox_subset.logging_setup import (
    LogLevel,
    add_log_handler,
    get_logger,
    setup_logging,
)

__all__ = [
    "LogLevel",
    "_",
    "add_log_handler",
    "available_languages",
    "get_language",
    "get_logger",
    "ngettext",
    "set_language",
    "setup_logging",
]

__version__ = "3.1.0"
