"""Application logging configuration.

The tool has two output channels with different audiences, and conflating
them is what makes CLI tools unpleasant to use:

* **stdout** -- the report the user asked for (the sorted order, warnings
  about their mods). This stays on :func:`print` in the engine: it is the
  product, not diagnostics, and it is what gets piped or copied into a bug
  report.
* **logging** -- diagnostics *about the run* (which file was opened, why a
  rule was skipped, how long a scan took). Off by default, enabled with
  ``-v``/``-vv``, and always written in full to the trace file when one is
  configured.

Typical wiring::

    from mlox_subset import setup_logging, get_logger

    setup_logging(verbosity=args.verbose, log_file=args.trace)
    log = get_logger(__name__)
    log.debug("read %d rule blocks", len(blocks))

Levels used by this project:
    ``DEBUG``    Per-item detail: one line per plugin, per rule, per edge.
    ``INFO``     Milestones: "parsed N rules", "sorted N plugins".
    ``WARNING``  The run continues but the result may not be what was wanted.
    ``ERROR``    An operation the user asked for did not happen.
    ``CRITICAL`` Data integrity is at risk; the run should stop.
"""

from __future__ import annotations

import logging
import sys
from enum import IntEnum
from pathlib import Path
from typing import Final, TextIO

#: Root logger name. Child loggers use ``get_logger(__name__)``.
ROOT_LOGGER_NAME: Final[str] = "mlox_subset"

#: Format for console output -- terse, because the user is reading a report.
CONSOLE_FORMAT: Final[str] = "%(levelname)-8s %(message)s"

#: Format for file output -- verbose, because it is read after the fact.
FILE_FORMAT: Final[str] = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"

#: Timestamp format shared by the file handler.
DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"


class LogLevel(IntEnum):
    """Logging levels, named for this application's usage.

    Mirrors :mod:`logging`'s numeric levels so the two are interchangeable.
    """

    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL

    @classmethod
    def from_verbosity(cls, verbosity: int) -> LogLevel:
        """Map a ``-v`` repeat count onto a level.

        Args:
            verbosity: Number of ``-v`` flags. ``0`` shows warnings and worse,
                ``1`` adds progress information, ``2`` or more adds per-item
                detail.

        Returns:
            The corresponding :class:`LogLevel`.

        """
        if verbosity <= 0:
            return cls.WARNING
        if verbosity == 1:
            return cls.INFO
        return cls.DEBUG


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger under the application's root logger.

    Args:
        name: Usually ``__name__``. A module named ``mlox_subset.foo`` is used
            as-is; anything else is nested under :data:`ROOT_LOGGER_NAME` so
            that one call to :func:`setup_logging` configures everything.

    Returns:
        The configured :class:`logging.Logger`.

    """
    if not name or name == ROOT_LOGGER_NAME:
        return logging.getLogger(ROOT_LOGGER_NAME)
    if name.startswith(f"{ROOT_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")


def setup_logging(
    verbosity: int = 0,
    *,
    log_file: Path | str | None = None,
    stream: TextIO | None = None,
    file_level: LogLevel = LogLevel.DEBUG,
    force: bool = True,
) -> logging.Logger:
    """Configure application logging.

    Console output is deliberately sent to ``stderr`` so that diagnostics
    never contaminate a piped report on ``stdout``.

    Args:
        verbosity: ``-v`` repeat count controlling the console level (see
            :meth:`LogLevel.from_verbosity`).
        log_file: Optional path receiving a full ``DEBUG`` transcript. Parent
            directories are created if needed.
        stream: Console stream. Defaults to :data:`sys.stderr`.
        file_level: Level for the file handler, when one is used.
        force: Remove existing handlers first. Keeps repeated calls (tests,
            a GUI reconfiguring at run time) from duplicating output.

    Returns:
        The configured root logger for the application.

    Raises:
        OSError: The log file's directory could not be created or the file
            could not be opened for writing.

    """
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    console_level = LogLevel.from_verbosity(verbosity)

    if force:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    # The logger itself must pass the most permissive level any handler wants.
    logger.setLevel(min(console_level, file_level) if log_file else console_level)
    logger.propagate = False

    console = logging.StreamHandler(stream if stream is not None else sys.stderr)
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    logger.addHandler(console)

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, mode="w", encoding="utf-8")
        file_handler.setLevel(file_level)
        file_handler.setFormatter(logging.Formatter(FILE_FORMAT, datefmt=DATE_FORMAT))
        logger.addHandler(file_handler)

    return logger


def add_log_handler(handler: logging.Handler, level: LogLevel | None = None) -> None:
    """Attach an extra handler to the application logger.

    Used by the GUI to mirror diagnostics into its log pane without losing
    console or file output.

    Args:
        handler: The handler to attach.
        level: Optional level for this handler only.

    """
    if level is not None:
        handler.setLevel(level)
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.addHandler(handler)
    if handler.level and handler.level < logger.level:
        logger.setLevel(handler.level)
