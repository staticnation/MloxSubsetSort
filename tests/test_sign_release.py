"""Tests for ``tools/sign_release.py``.

Real minisign keys throughout -- ``minisign.KeyPair.generate()`` makes a
fresh keypair per test, so nothing here depends on a fixture key committed
to the repo, and a real signature is verified against the real public key
at the end of the happy path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

minisign = pytest.importorskip("minisign", reason="py-minisign not installed; CI-only dependency")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from sign_release import main, sign  # noqa: E402  (needs the sys.path line above)


def _key_text(secret_key: minisign.SecretKey) -> str:
    """The two-line secret-key *file* text sign() expects, from a live key."""
    return "untrusted comment: test key\n" + secret_key.to_base64().decode() + "\n"


class TestSign:
    def test_an_unencrypted_key_signs_and_verifies(self, tmp_path: Path, capsys) -> None:
        pair = minisign.KeyPair.generate()
        target = tmp_path / "artifact.exe"
        target.write_bytes(b"pretend binary contents")

        written = sign([str(target)], _key_text(pair.secret_key), None)

        assert written == [str(target) + ".minisig"]
        sig = minisign.Signature.from_file(written[0])
        pair.public_key.verify(target.read_bytes(), sig)  # raises if invalid
        assert "signed" in capsys.readouterr().out

    def test_an_encrypted_key_without_a_password_is_refused(self, tmp_path: Path) -> None:
        pair = minisign.KeyPair.generate()
        pair.secret_key.encrypt("correct horse")
        target = tmp_path / "artifact.exe"
        target.write_bytes(b"x")

        with pytest.raises(ValueError, match="MINISIGN_PASSWORD is not set"):
            sign([str(target)], _key_text(pair.secret_key), None)

    def test_an_encrypted_key_with_the_right_password_signs(self, tmp_path: Path) -> None:
        pair = minisign.KeyPair.generate()
        pair.secret_key.encrypt("correct horse")
        target = tmp_path / "artifact.exe"
        target.write_bytes(b"x")

        written = sign([str(target)], _key_text(pair.secret_key), "correct horse")

        assert Path(written[0]).is_file()

    def test_multiple_files_each_get_their_own_signature(self, tmp_path: Path) -> None:
        pair = minisign.KeyPair.generate()
        a = tmp_path / "a.exe"
        a.write_bytes(b"a")
        b = tmp_path / "b.exe"
        b.write_bytes(b"b")

        written = sign([str(a), str(b)], _key_text(pair.secret_key), None)

        assert written == [str(a) + ".minisig", str(b) + ".minisig"]


class TestMain:
    def test_no_files_given_is_a_usage_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["sign_release.py"])

        assert rc == 2
        assert "usage:" in capsys.readouterr().err

    def test_a_missing_secret_key_env_var_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("MINISIGN_SECRET_KEY", raising=False)
        target = tmp_path / "a.exe"
        target.write_bytes(b"x")

        rc = main(["sign_release.py", str(target)])

        assert rc == 1
        assert "MINISIGN_SECRET_KEY is not set" in capsys.readouterr().err

    def test_a_real_run_via_the_environment_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pair = minisign.KeyPair.generate()
        target = tmp_path / "a.exe"
        target.write_bytes(b"x")
        monkeypatch.setenv("MINISIGN_SECRET_KEY", _key_text(pair.secret_key))
        monkeypatch.delenv("MINISIGN_PASSWORD", raising=False)

        rc = main(["sign_release.py", str(target)])

        assert rc == 0
        assert (tmp_path / "a.exe.minisig").is_file()

    def test_a_signing_failure_is_reported_and_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A missing target file: sign_file raises OSError, caught and reported by main()."""
        pair = minisign.KeyPair.generate()
        monkeypatch.setenv("MINISIGN_SECRET_KEY", _key_text(pair.secret_key))
        monkeypatch.delenv("MINISIGN_PASSWORD", raising=False)

        rc = main(["sign_release.py", str(tmp_path / "does-not-exist.exe")])

        assert rc == 1
        assert "signing failed" in capsys.readouterr().err

    def test_the_wrong_password_is_reported_and_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pair = minisign.KeyPair.generate()
        pair.secret_key.encrypt("correct horse")
        target = tmp_path / "a.exe"
        target.write_bytes(b"x")
        monkeypatch.setenv("MINISIGN_SECRET_KEY", _key_text(pair.secret_key))
        monkeypatch.setenv("MINISIGN_PASSWORD", "wrong password")

        rc = main(["sign_release.py", str(target)])

        assert rc == 1
        assert "signing failed" in capsys.readouterr().err
