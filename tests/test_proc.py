"""The Windows console-suppression kwargs helper.

The interesting behaviour is Windows-only, so the platform and the
``STARTUPINFO`` API are emulated to exercise the branch that a Linux CI run
can never reach on its own.
"""

from __future__ import annotations

import subprocess

import pytest

from wraithguard import proc


def test_non_windows_is_a_no_op(monkeypatch) -> None:
    """Off Windows there is no console window to suppress."""
    monkeypatch.setattr(proc.os, "name", "posix")
    assert proc.no_window_kwargs() == {}


def test_windows_sets_the_hidden_startupinfo(monkeypatch) -> None:
    """On Windows the helper adds CREATE_NO_WINDOW and a hidden STARTUPINFO."""

    class _FakeStartupInfo:
        def __init__(self) -> None:
            self.dwFlags = 0
            self.wShowWindow = 99

    monkeypatch.setattr(proc.os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", _FakeStartupInfo, raising=False)
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 0x1, raising=False)

    kw = proc.no_window_kwargs()
    assert kw["creationflags"] == 0x08000000
    si = kw["startupinfo"]
    assert si.dwFlags == 0x1
    assert si.wShowWindow == 0


def test_windows_without_startupinfo_still_gets_the_flag(monkeypatch) -> None:
    """A stripped build lacking STARTUPINFO keeps CREATE_NO_WINDOW alone."""
    monkeypatch.setattr(proc.os, "name", "nt")
    monkeypatch.delattr(subprocess, "STARTUPINFO", raising=False)

    kw = proc.no_window_kwargs()
    assert kw == {"creationflags": 0x08000000}
    assert "startupinfo" not in kw


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
