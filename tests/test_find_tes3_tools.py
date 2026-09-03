"""find_tes3conv, find_tes3cmd, and tes3cmd_invocation: locating the two
optional external helpers and working out how to actually run one of them.

The three are worth pinning together: the search order for both finders is
identical (explicit path, env var, PATH, alongside the script / extra dirs)
and both need to survive one dead candidate without losing the rest -- a
stale PATH entry or an unreadable network share must not stop the search.
tes3cmd_invocation then has its own two-way branch on top of finding the
file at all: the compiled build runs directly, the pure-perl script needs a
perl on PATH, detected either by a shebang or by the word "perl" turning up
in the first 256 bytes.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import wraithguard_toolkit as core


@pytest.fixture(autouse=True)
def _no_real_path_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither finder should see whatever tools happen to be on this
    machine's real PATH -- these tests want to control every candidate."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.delenv("MLOX_TES3CONV", raising=False)
    monkeypatch.delenv("MLOX_TES3CMD", raising=False)


class TestFindTes3conv:
    def test_an_explicit_existing_path_is_returned(self, tmp_path: Path) -> None:
        exe = tmp_path / "tes3conv"
        exe.write_bytes(b"")

        assert core.find_tes3conv(explicit=str(exe)) == str(exe)

    def test_an_explicit_nonexistent_path_falls_through_to_the_next_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = tmp_path / "real_tes3conv"
        real.write_bytes(b"")
        monkeypatch.setenv("MLOX_TES3CONV", str(real))

        assert core.find_tes3conv(explicit=str(tmp_path / "does-not-exist")) == str(real)

    def test_the_env_var_is_used_when_no_explicit_path_is_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exe = tmp_path / "tes3conv"
        exe.write_bytes(b"")
        monkeypatch.setenv("MLOX_TES3CONV", str(exe))

        assert core.find_tes3conv() == str(exe)

    def test_a_path_hit_is_used_when_nothing_more_specific_is_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exe = tmp_path / "tes3conv"
        exe.write_bytes(b"")
        monkeypatch.setattr(shutil, "which", lambda name: str(exe) if name == "tes3conv" else None)

        assert core.find_tes3conv() == str(exe)

    def test_an_extra_dir_is_searched_when_nothing_else_matches(self, tmp_path: Path) -> None:
        extra = tmp_path / "tools"
        extra.mkdir()
        exe = extra / "tes3conv.exe"
        exe.write_bytes(b"")

        assert core.find_tes3conv(extra_dirs=[str(extra)]) == str(exe)

    def test_nothing_found_anywhere_returns_none(self) -> None:
        assert core.find_tes3conv() is None

    def test_a_candidate_that_cannot_be_checked_does_not_stop_the_search(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A dead network share or a permission-denied path as the first
        # candidate (explicit) must not prevent the env-var candidate behind
        # it from being found.
        real = tmp_path / "real_tes3conv"
        real.write_bytes(b"")
        monkeypatch.setenv("MLOX_TES3CONV", str(real))

        original_is_file = Path.is_file

        def _flaky(self: Path) -> bool:
            if self.name == "unreachable":
                raise OSError("simulated: dead network share")
            return original_is_file(self)

        monkeypatch.setattr(Path, "is_file", _flaky)

        assert core.find_tes3conv(explicit=str(tmp_path / "unreachable")) == str(real)


class TestFindTes3cmd:
    def test_the_compiled_exe_is_preferred_when_multiple_names_exist(self, tmp_path: Path) -> None:
        extra = tmp_path / "tools"
        extra.mkdir()
        (extra / "tes3cmd").write_bytes(b"")  # perl script, lower priority
        exe = extra / "tes3cmd.exe"
        exe.write_bytes(b"")  # compiled build, higher priority

        assert core.find_tes3cmd(extra_dirs=[str(extra)]) == str(exe)

    def test_the_pure_perl_script_is_still_found_when_it_is_all_there_is(
        self, tmp_path: Path
    ) -> None:
        extra = tmp_path / "tools"
        extra.mkdir()
        script = extra / "tes3cmd"
        script.write_bytes(b"#!/usr/bin/perl\n")

        assert core.find_tes3cmd(extra_dirs=[str(extra)]) == str(script)

    def test_nothing_found_anywhere_returns_none(self) -> None:
        assert core.find_tes3cmd() is None

    def test_a_path_hit_is_used_when_nothing_more_specific_is_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exe = tmp_path / "tes3cmd.exe"
        exe.write_bytes(b"")
        monkeypatch.setattr(
            shutil, "which", lambda name: str(exe) if name == "tes3cmd.exe" else None
        )

        assert core.find_tes3cmd() == str(exe)

    def test_a_candidate_that_cannot_be_checked_does_not_stop_the_search(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = tmp_path / "real_tes3cmd"
        real.write_bytes(b"")
        monkeypatch.setenv("MLOX_TES3CMD", str(real))

        original_is_file = Path.is_file

        def _flaky(self: Path) -> bool:
            if self.name == "unreachable":
                raise OSError("simulated: dead network share")
            return original_is_file(self)

        monkeypatch.setattr(Path, "is_file", _flaky)

        assert core.find_tes3cmd(explicit=str(tmp_path / "unreachable")) == str(real)


class TestTes3cmdInvocation:
    def test_an_exe_suffix_runs_directly_without_reading_the_file(self, tmp_path: Path) -> None:
        # No content written at all -- if this tried to read the file it
        # would still succeed on empty bytes, but the point is it shouldn't
        # need to.
        path = tmp_path / "tes3cmd.exe"
        path.write_bytes(b"")

        argv, error = core.tes3cmd_invocation(path)

        assert argv == [str(path)]
        assert error is None

    def test_a_bat_suffix_also_runs_directly(self, tmp_path: Path) -> None:
        path = tmp_path / "tes3cmd.bat"
        path.write_bytes(b"")

        argv, error = core.tes3cmd_invocation(path)

        assert argv == [str(path)]
        assert error is None

    def test_a_shebang_script_with_perl_on_path_is_run_through_perl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "tes3cmd"
        path.write_bytes(b"#!/usr/bin/perl\nprint 'hi';\n")
        monkeypatch.setattr(
            shutil, "which", lambda name: "/usr/bin/perl" if name == "perl" else None
        )

        argv, error = core.tes3cmd_invocation(path)

        assert argv == ["/usr/bin/perl", str(path)]
        assert error is None

    def test_a_shebang_script_without_perl_on_path_reports_a_clear_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "tes3cmd"
        path.write_bytes(b"#!/usr/bin/perl\n")
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        argv, error = core.tes3cmd_invocation(path)

        assert argv is None
        assert error is not None and "no perl interpreter" in error

    def test_the_word_perl_in_the_body_without_a_shebang_still_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "tes3cmd"
        path.write_bytes(b"# a pure-Perl build of tes3cmd\nuse strict;\n")
        monkeypatch.setattr(
            shutil, "which", lambda name: "/usr/bin/perl" if name == "perl" else None
        )

        argv, _error = core.tes3cmd_invocation(path)

        assert argv == ["/usr/bin/perl", str(path)]

    def test_a_file_with_neither_marker_is_run_directly(self, tmp_path: Path) -> None:
        path = tmp_path / "tes3cmd"  # no .exe suffix, no shebang, no "perl"
        path.write_bytes(b"this is presumably a compiled binary\x00\x01\x02")

        argv, error = core.tes3cmd_invocation(path)

        assert argv == [str(path)]
        assert error is None

    def test_an_unreadable_file_reports_the_read_error(self, tmp_path: Path) -> None:
        argv, error = core.tes3cmd_invocation(tmp_path / "does-not-exist")

        assert argv is None
        assert error is not None and "can't read" in error
