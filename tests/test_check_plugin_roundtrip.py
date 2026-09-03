"""Tests for ``tools/check_plugin_roundtrip.py``.

The four helpers (locate the converter, run one conversion, count records,
round-trip one plugin twice) and the CLI, all previously untested. Every
conversion here is a mocked ``subprocess.run`` -- what's under test is this
script's own decision-making (identical/stable/normalised/drifting/lossy,
and each of the four steps' own failure), not tes3conv's actual behaviour.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.check_plugin_roundtrip import check_one, convert, find_tes3conv, main, record_count

if TYPE_CHECKING:
    import pytest


class TestFindTes3conv:
    def test_an_explicit_path_that_exists_is_used(self, tmp_path: Path) -> None:
        exe = tmp_path / "tes3conv"
        exe.write_bytes(b"")
        assert find_tes3conv(str(exe)) == str(exe)

    def test_an_explicit_path_that_does_not_exist_is_refused(self, tmp_path: Path) -> None:
        assert find_tes3conv(str(tmp_path / "no-such-file")) is None

    def test_a_path_hit_is_used_when_nothing_explicit_is_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
        assert find_tes3conv(None) == "/usr/bin/tes3conv"

    def test_falls_back_to_a_nearby_location(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import tools.check_plugin_roundtrip as cpr

        monkeypatch.setattr(shutil, "which", lambda _name: None)
        nearby = tmp_path / "tes3conv.exe"
        nearby.write_bytes(b"")
        # An absolute path here replaces (root / relative) entirely -- Path's
        # own semantics for joining an absolute path onto anything.
        monkeypatch.setattr(cpr, "_NEARBY", (str(nearby),))

        assert cpr.find_tes3conv(None) == str(nearby)

    def test_nothing_found_anywhere_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tools.check_plugin_roundtrip as cpr

        monkeypatch.setattr(shutil, "which", lambda _name: None)
        # This repo's own checkout may have a real tes3conv sitting exactly
        # where _NEARBY looks (that is what the previous test proves) -- so
        # "nothing anywhere" has to blank that list out too, not just PATH.
        monkeypatch.setattr(cpr, "_NEARBY", ())

        assert find_tes3conv(None) is None


class TestConvert:
    def test_a_subprocess_failure_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a: object, **_k: object) -> None:
            raise OSError("simulated: not runnable")

        monkeypatch.setattr(subprocess, "run", _boom)

        error = convert("tes3conv", tmp_path / "a.esp", tmp_path / "a.json")

        assert "could not run the converter" in error

    def test_a_nonzero_exit_is_reported_with_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(*_a: object, **_k: object) -> types.SimpleNamespace:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="bad header")

        monkeypatch.setattr(subprocess, "run", fake_run)

        error = convert("tes3conv", tmp_path / "a.esp", tmp_path / "a.json")

        assert error == "bad header"

    def test_success_but_nothing_written_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(*_a: object, **_k: object) -> types.SimpleNamespace:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        error = convert("tes3conv", tmp_path / "a.esp", tmp_path / "a.json")

        assert "wrote nothing" in error

    def test_a_real_success_reports_no_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "a.json"

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            Path(argv[2]).write_text("[]", encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert convert("tes3conv", tmp_path / "a.esp", target) == ""


class TestRecordCount:
    def test_a_record_list_is_counted(self, tmp_path: Path) -> None:
        path = tmp_path / "a.json"
        path.write_text('[{"type": "Static"}, {"type": "Npc"}]', encoding="utf-8")
        assert record_count(path) == 2

    def test_malformed_json_yields_none(self, tmp_path: Path) -> None:
        path = tmp_path / "a.json"
        path.write_text("not json", encoding="utf-8")
        assert record_count(path) is None

    def test_valid_json_that_is_not_a_list_yields_none(self, tmp_path: Path) -> None:
        path = tmp_path / "a.json"
        path.write_text('{"type": "Static"}', encoding="utf-8")
        assert record_count(path) is None

    def test_a_missing_file_yields_none(self, tmp_path: Path) -> None:
        assert record_count(tmp_path / "gone.json") is None


class TestCheckOne:
    """check_one's own decisions, entirely through a fake converter."""

    def _rig(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, plugin_bytes: bytes = b"\x00"
    ) -> Path:
        plugin = tmp_path / "Mine.esp"
        plugin.write_bytes(plugin_bytes)
        return plugin

    def test_a_byte_identical_result_is_identical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin = self._rig(monkeypatch, tmp_path, b"fixed-bytes")

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            dst = Path(argv[2])
            if dst.suffix == ".json":
                dst.write_text('[{"type": "Static"}]', encoding="utf-8")
            else:
                dst.write_bytes(plugin.read_bytes())  # every rewrite reproduces the original
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        outcome, detail = check_one("tes3conv", plugin, tmp_path)

        assert outcome == "identical"
        assert "bytes" in detail

    def test_step_one_failure_is_blamed_as_unreadable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin = self._rig(monkeypatch, tmp_path)

        def fake_run(*_a: object, **_k: object) -> types.SimpleNamespace:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="corrupt header")

        monkeypatch.setattr(subprocess, "run", fake_run)

        outcome, detail = check_one("tes3conv", plugin, tmp_path)

        assert outcome == "unreadable"
        assert "corrupt header" in detail

    def test_step_two_failure_is_blamed_as_unwritable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin = self._rig(monkeypatch, tmp_path)

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            dst = Path(argv[2])
            if dst.name == "one.json":
                dst.write_text('[{"type": "Static"}]', encoding="utf-8")
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            return types.SimpleNamespace(returncode=1, stdout="", stderr="write refused")

        monkeypatch.setattr(subprocess, "run", fake_run)

        outcome, detail = check_one("tes3conv", plugin, tmp_path)

        assert outcome == "unwritable"
        assert "write refused" in detail

    def test_a_record_count_mismatch_is_lossy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin = self._rig(monkeypatch, tmp_path)

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            dst = Path(argv[2])
            if dst.name == "one.json":
                dst.write_text('[{"type": "Static"}, {"type": "Npc"}]', encoding="utf-8")
            elif dst.name == "two.json":
                dst.write_text('[{"type": "Static"}]', encoding="utf-8")
            elif dst.suffix == plugin.suffix:
                dst.write_bytes(b"different-bytes")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        outcome, detail = check_one("tes3conv", plugin, tmp_path)

        assert outcome == "lossy"
        assert "2 -> 1" in detail

    def test_matching_json_but_different_bytes_is_stable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin = self._rig(monkeypatch, tmp_path)

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            dst = Path(argv[2])
            if dst.suffix == ".json":
                dst.write_text('[{"type": "Static"}]', encoding="utf-8")
            elif dst.suffix == plugin.suffix:
                dst.write_bytes(b"resorted-but-equivalent-bytes")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        outcome, _detail = check_one("tes3conv", plugin, tmp_path)

        assert outcome == "stable"

    def test_a_one_time_normalisation_converges_and_is_normalised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin = self._rig(monkeypatch, tmp_path)

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            dst = Path(argv[2])
            if dst.name == "one.json":
                dst.write_text('[{"type": "Static", "extra": 1}]', encoding="utf-8")
            elif dst.name == "two.json":
                dst.write_text('[{"type": "Static"}]', encoding="utf-8")
            elif dst.name == f"one{plugin.suffix}":
                dst.write_bytes(b"normalised-bytes")
            elif dst.name == f"two{plugin.suffix}":
                dst.write_bytes(b"normalised-bytes")  # settled: same as the first rewrite
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        outcome, _detail = check_one("tes3conv", plugin, tmp_path)

        assert outcome == "normalised"

    def test_a_second_pass_that_keeps_changing_is_drifting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin = self._rig(monkeypatch, tmp_path)

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            dst = Path(argv[2])
            if dst.name == "one.json":
                dst.write_text('[{"type": "Static", "extra": 1}]', encoding="utf-8")
            elif dst.name == "two.json":
                dst.write_text('[{"type": "Static"}]', encoding="utf-8")
            elif dst.name == f"one{plugin.suffix}":
                dst.write_bytes(b"first-rewrite")
            elif dst.name == f"two{plugin.suffix}":
                dst.write_bytes(b"second-rewrite-still-different")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        outcome, detail = check_one("tes3conv", plugin, tmp_path)

        assert outcome == "drifting"
        assert "second pass changed it again" in detail


class TestMain:
    def test_tes3conv_not_found_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        import tools.check_plugin_roundtrip as cpr

        monkeypatch.setattr(cpr, "find_tes3conv", lambda *_a, **_k: None)

        assert main([str(tmp_path)]) == 2
        assert "tes3conv was not found" in capsys.readouterr().err

    def test_not_a_directory_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        import tools.check_plugin_roundtrip as cpr

        monkeypatch.setattr(cpr, "find_tes3conv", lambda *_a, **_k: "tes3conv")

        assert main([str(tmp_path / "gone")]) == 2
        assert "not a directory" in capsys.readouterr().err

    def test_no_plugins_present_is_a_clean_no_op(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        import tools.check_plugin_roundtrip as cpr

        monkeypatch.setattr(cpr, "find_tes3conv", lambda *_a, **_k: "tes3conv")

        assert main([str(tmp_path)]) == 0
        assert "no plugins under" in capsys.readouterr().out

    def test_an_identical_plugin_prints_the_success_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        import tools.check_plugin_roundtrip as cpr

        monkeypatch.setattr(cpr, "find_tes3conv", lambda *_a, **_k: "tes3conv")
        plugin = tmp_path / "Mine.esp"
        plugin.write_bytes(b"same-bytes")

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            dst = Path(argv[2])
            if dst.suffix == ".json":
                dst.write_text('[{"type": "Static"}]', encoding="utf-8")
            else:
                dst.write_bytes(b"same-bytes")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = main([str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "identical" in out
        assert "survived the round trip unchanged" in out

    def test_a_lossy_plugin_fails_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        import tools.check_plugin_roundtrip as cpr

        monkeypatch.setattr(cpr, "find_tes3conv", lambda *_a, **_k: "tes3conv")
        (tmp_path / "Mine.esp").write_bytes(b"x")

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            dst = Path(argv[2])
            if dst.name == "one.json":
                dst.write_text('[{"type": "Static"}, {"type": "Npc"}]', encoding="utf-8")
            elif dst.name == "two.json":
                dst.write_text('[{"type": "Static"}]', encoding="utf-8")
            elif dst.suffix == ".esp":
                dst.write_bytes(b"different")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = main([str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 1
        assert "LOSSY" in out
        assert "Emitting JSON is NOT safe" in out

    def test_a_normalised_plugin_prints_the_normalised_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        import tools.check_plugin_roundtrip as cpr

        monkeypatch.setattr(cpr, "find_tes3conv", lambda *_a, **_k: "tes3conv")
        plugin = tmp_path / "Mine.esp"
        plugin.write_bytes(b"x")

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            dst = Path(argv[2])
            if dst.name == "one.json":
                dst.write_text('[{"type": "Static", "extra": 1}]', encoding="utf-8")
            elif dst.name == "two.json":
                dst.write_text('[{"type": "Static"}]', encoding="utf-8")
            elif dst.name in {"one.esp", "two.esp"}:
                dst.write_bytes(b"normalised")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = main([str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "Every sampled plugin converged" in out

    def test_the_limit_and_step_sample_fewer_plugins_than_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        import tools.check_plugin_roundtrip as cpr

        monkeypatch.setattr(cpr, "find_tes3conv", lambda *_a, **_k: "tes3conv")
        for i in range(10):
            (tmp_path / f"M{i}.esp").write_bytes(b"x")

        def fake_run(argv: list[str], **_kw: object) -> types.SimpleNamespace:
            dst = Path(argv[2])
            if dst.suffix == ".json":
                dst.write_text('[{"type": "Static"}]', encoding="utf-8")
            else:
                dst.write_bytes(b"x")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = main([str(tmp_path), "--limit", "3"])

        out = capsys.readouterr().out
        assert rc == 0
        assert "10 plugin(s) present; round-tripping 3" in out
