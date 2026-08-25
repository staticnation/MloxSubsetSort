#!/usr/bin/env python3
"""Sign release artifacts with minisign, from CI.

Signs each file given on the command line, writing ``<file>.minisig`` beside it,
using the pure-Python `py-minisign` package -- so the release runners need no C
or Rust toolchain, only the Python they already set up.

The signing key never appears on the command line or in the repository. It comes
from the environment, which CI populates from repository secrets:

* ``MINISIGN_SECRET_KEY`` -- the *full text* of a ``minisign -G`` secret-key
  file (two lines: an ``untrusted comment`` line and the base64 body).
* ``MINISIGN_PASSWORD`` -- the password that key was encrypted with. Omit it
  only if the key was generated without one (``minisign -G -W``).

Generate the key pair once, locally (never in CI)::

    minisign -G                    # writes minisign.key (secret) + minisign.pub

Then, in the repository's *Settings -> Secrets and variables -> Actions*, add
``MINISIGN_SECRET_KEY`` (paste the whole ``minisign.key`` file) and
``MINISIGN_PASSWORD``. Commit ``minisign.pub`` so downloaders can verify::

    minisign -Vm wraithguard-toolkit-windows-x86_64.exe -p minisign.pub

The signatures are ordinary minisign signatures (prehashed, which any minisign
from 0.6 onward verifies), so users do not need this package -- the standard
``minisign`` binary checks them.

Usage::

    python tools/sign_release.py FILE [FILE ...]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def sign(files: list[str], key_text: str, password: str | None) -> list[str]:
    """Sign each file, returning the ``.minisig`` paths written.

    Args:
        files: The artifact paths to sign.
        key_text: The full secret-key file text (two lines).
        password: The key's password, or ``None`` for an unencrypted key.

    Returns:
        The paths of the signatures written, one per input file.

    Raises:
        ValueError: The key is encrypted but no password was given.
    """
    import minisign  # imported here so --help works without the dependency

    secret_key = minisign.SecretKey.from_bytes(key_text.encode())
    if secret_key.is_encrypted():
        if not password:
            raise ValueError("the secret key is encrypted but MINISIGN_PASSWORD is not set")
        secret_key.decrypt(password)

    written: list[str] = []
    for name in files:
        path = Path(name)
        # prehash: sign a hash of the file, not the whole file -- fast on a
        # tens-of-MB executable, and verified transparently by `minisign -V`.
        signature = secret_key.sign_file(path, prehash=True, trusted_comment=f"file:{path.name}")
        out = path.with_name(path.name + ".minisig")
        out.write_bytes(bytes(signature))
        written.append(str(out))
        print(f"signed {path} -> {out}")
    return written


def main(argv: list[str]) -> int:
    """Sign the files named in ``argv[1:]`` using the key from the environment."""
    files = argv[1:]
    if not files:
        print("usage: sign_release.py FILE [FILE ...]", file=sys.stderr)
        return 2
    key_text = os.environ.get("MINISIGN_SECRET_KEY")
    if not key_text:
        print("MINISIGN_SECRET_KEY is not set", file=sys.stderr)
        return 1
    try:
        sign(files, key_text, os.environ.get("MINISIGN_PASSWORD"))
    except (ValueError, OSError) as exc:
        print(f"signing failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
