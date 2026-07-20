"""Tests for the shared logging and i18n foundation."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from mlox_subset import i18n, logging_setup
from mlox_subset.logging_setup import LogLevel, get_logger, setup_logging


@pytest.fixture(autouse=True)
def _reset_logging():
    """Keep handler state from leaking between tests."""
    yield
    logger = logging.getLogger(logging_setup.ROOT_LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


class TestLogLevels:
    @pytest.mark.parametrize(
        ("verbosity", "expected"),
        [(0, LogLevel.WARNING), (1, LogLevel.INFO), (2, LogLevel.DEBUG), (5, LogLevel.DEBUG)],
    )
    def test_verbosity_maps_to_level(self, verbosity, expected):
        assert LogLevel.from_verbosity(verbosity) is expected

    def test_negative_verbosity_is_treated_as_quiet(self):
        assert LogLevel.from_verbosity(-1) is LogLevel.WARNING

    def test_levels_match_stdlib_numbers(self):
        assert LogLevel.DEBUG == logging.DEBUG
        assert LogLevel.CRITICAL == logging.CRITICAL


class TestLoggerNaming:
    def test_module_names_nest_under_the_app_root(self):
        assert get_logger("engine").name == "mlox_subset.engine"

    def test_already_namespaced_names_are_left_alone(self):
        assert get_logger("mlox_subset.engine").name == "mlox_subset.engine"

    def test_no_name_returns_the_root(self):
        assert get_logger().name == logging_setup.ROOT_LOGGER_NAME


class TestConsoleOutput:
    def test_default_verbosity_hides_info_shows_warning(self):
        stream = io.StringIO()
        setup_logging(verbosity=0, stream=stream)
        log = get_logger("t")

        log.info("progress detail")
        log.warning("something to look at")

        output = stream.getvalue()
        assert "progress detail" not in output
        assert "something to look at" in output

    def test_verbose_reveals_info(self):
        stream = io.StringIO()
        setup_logging(verbosity=1, stream=stream)

        get_logger("t").info("progress detail")

        assert "progress detail" in stream.getvalue()

    def test_double_verbose_reveals_debug(self):
        stream = io.StringIO()
        setup_logging(verbosity=2, stream=stream)

        get_logger("t").debug("per-item detail")

        assert "per-item detail" in stream.getvalue()

    def test_repeated_setup_does_not_duplicate_output(self):
        stream = io.StringIO()
        setup_logging(verbosity=1, stream=stream)
        setup_logging(verbosity=1, stream=stream)

        get_logger("t").warning("once")

        assert stream.getvalue().count("once") == 1


class TestFileOutput:
    def test_file_captures_debug_even_when_console_is_quiet(self, tmp_path: Path):
        log_file = tmp_path / "trace.log"
        stream = io.StringIO()
        setup_logging(verbosity=0, log_file=log_file, stream=stream)

        get_logger("t").debug("detail for the bug report")

        assert "detail for the bug report" not in stream.getvalue()
        assert "detail for the bug report" in log_file.read_text(encoding="utf-8")

    def test_missing_parent_directory_is_created(self, tmp_path: Path):
        log_file = tmp_path / "nested" / "deeper" / "trace.log"

        setup_logging(verbosity=0, log_file=log_file, stream=io.StringIO())
        get_logger("t").warning("written")

        assert log_file.exists()

    def test_file_entries_carry_level_and_logger_name(self, tmp_path: Path):
        log_file = tmp_path / "trace.log"
        setup_logging(verbosity=0, log_file=log_file, stream=io.StringIO())

        get_logger("engine").error("bad thing")

        contents = log_file.read_text(encoding="utf-8")
        assert "ERROR" in contents and "mlox_subset.engine" in contents


class TestExtraHandlers:
    def test_gui_style_handler_receives_records(self):
        setup_logging(verbosity=0, stream=io.StringIO())
        captured: list[str] = []

        class Collector(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record.getMessage())

        logging_setup.add_log_handler(Collector(), LogLevel.DEBUG)
        get_logger("t").debug("mirrored to the gui pane")

        assert "mirrored to the gui pane" in captured


class TestTranslation:
    def test_untranslated_strings_pass_through(self):
        assert i18n.gettext("Sorting plugins") == "Sorting plugins"

    def test_plural_selection_falls_back_correctly(self):
        assert i18n.ngettext("%(n)d plugin", "%(n)d plugins", 1) == "%(n)d plugin"
        assert i18n.ngettext("%(n)d plugin", "%(n)d plugins", 3) == "%(n)d plugins"

    def test_setting_an_unknown_language_is_safe(self):
        previous = i18n.get_language()
        try:
            assert i18n.set_language("zz_ZZ") == "zz_ZZ"
            # no catalogue -> English source strings still work
            assert i18n.gettext("Sorting plugins") == "Sorting plugins"
        finally:
            i18n.set_language(previous)

    def test_default_language_is_always_available(self):
        assert i18n.DEFAULT_LANGUAGE in i18n.available_languages()

    def test_language_env_var_is_honoured(self, monkeypatch):
        previous = i18n.get_language()
        try:
            monkeypatch.setenv(i18n.LANGUAGE_ENV_VAR, "fr_FR.UTF-8")
            assert i18n.set_language() == "fr_FR"
        finally:
            monkeypatch.delenv(i18n.LANGUAGE_ENV_VAR, raising=False)
            i18n.set_language(previous)

    def test_underscore_alias_is_exported(self):
        from mlox_subset import _

        assert _("Sorting plugins") == "Sorting plugins"
