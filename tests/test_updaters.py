"""Tests for the rules / plugin-order.yml updaters.

These download over files on disk, and their URLs come from a persisted
settings file and from environment variables -- so the scheme allow-list and
the "validate before writing" behaviour are security properties, not
conveniences. A local HTTP server stands in for upstream.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from mlox_subset.net import (
    ALLOWED_URL_SCHEMES,
    fetch_url_bytes,
    update_plugin_order_yml,
    update_rule_files,
)

RULES_BODY = b"[Order]\nAlpha.esp\nBeta.esp\n"


@pytest.fixture
def http_server():
    """A throwaway localhost server; ``routes`` is mutable per test."""
    routes: dict[str, bytes] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = routes.get(self.path)
            self.send_response(200 if body is not None else 404)
            body = body if body is not None else b"not found"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence per-request stderr noise
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", routes
    server.shutdown()


class TestUrlSchemeAllowList:
    """A tampered settings file or env var must not turn 'update' into
    'copy an arbitrary local file over the user's rules'."""

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.invalid/x",
            "data:text/plain,hello",
            "gopher://example.invalid/x",
        ],
    )
    def test_disallowed_schemes_are_refused(self, url):
        with pytest.raises(ValueError, match="refusing to download"):
            fetch_url_bytes(url)

    def test_url_without_host_is_refused(self):
        with pytest.raises(ValueError, match="no host"):
            fetch_url_bytes("https:///nohost")

    def test_https_is_allowed(self):
        assert "https" in ALLOWED_URL_SCHEMES
        assert "file" not in ALLOWED_URL_SCHEMES

    def test_rule_update_refuses_file_url_and_leaves_target_intact(self, tmp_path):
        target = tmp_path / "mlox_base.txt"
        target.write_bytes(b"original")
        secret = tmp_path / "secret.txt"
        secret.write_bytes(b"[Order]\nPWNED.esp\nX.esp\n")

        report = update_rule_files([target], url_template=f"file://{secret}?{{name}}")

        assert target.read_bytes() == b"original"
        assert any("refusing" in line for line in report)


class TestDownloadLimits:
    def test_oversized_response_is_rejected(self, http_server):
        base, routes = http_server
        routes["/big"] = b"x" * 5000
        with pytest.raises(ValueError, match="larger than"):
            fetch_url_bytes(f"{base}/big", max_bytes=1000)

    def test_response_at_the_limit_is_accepted(self, http_server):
        base, routes = http_server
        routes["/exact"] = b"x" * 1000
        assert len(fetch_url_bytes(f"{base}/exact", max_bytes=1000)) == 1000


class TestRulesUpdater:
    def test_updates_managed_file_and_keeps_backup(self, tmp_path, http_server):
        base, routes = http_server
        routes["/mlox_base.txt"] = RULES_BODY
        target = tmp_path / "mlox_base.txt"
        target.write_bytes(b"[Order]\nOld.esp\nOlder.esp\n")

        report = update_rule_files([target], url_template=f"{base}/{{name}}")

        assert target.read_bytes() == RULES_BODY
        assert any("updated" in line for line in report)
        assert list(tmp_path.glob("mlox_base.txt.bak-*")), "no timestamped backup kept"

    def test_personal_rule_files_are_never_touched(self, tmp_path, http_server):
        base, routes = http_server
        routes["/my_rules.txt"] = RULES_BODY
        personal = tmp_path / "my_rules.txt"
        personal.write_bytes(b"mine")

        report = update_rule_files([personal], url_template=f"{base}/{{name}}")

        assert personal.read_bytes() == b"mine"
        assert any("skipped" in line for line in report)

    def test_response_that_is_not_a_rules_file_is_rejected(self, tmp_path, http_server):
        base, routes = http_server
        routes["/mlox_base.txt"] = b"<html>404</html>"
        target = tmp_path / "mlox_base.txt"
        target.write_bytes(b"original")

        update_rule_files([target], url_template=f"{base}/{{name}}")

        assert target.read_bytes() == b"original"

    def test_identical_content_is_a_no_op(self, tmp_path, http_server):
        base, routes = http_server
        routes["/mlox_base.txt"] = RULES_BODY
        target = tmp_path / "mlox_base.txt"
        target.write_bytes(RULES_BODY)

        report = update_rule_files([target], url_template=f"{base}/{{name}}")

        assert any("already up to date" in line for line in report)
        assert not list(tmp_path.glob("*.bak-*"))

    @pytest.mark.parametrize(
        "template",
        [
            "https://x/{name}/{branch}",
            "https://x/{name",
            "https://x/{0}",
            "https://x/no-placeholder",
        ],
    )
    def test_malformed_template_reports_instead_of_crashing(self, tmp_path, template):
        report = update_rule_files([tmp_path / "mlox_base.txt"], url_template=template)
        assert report and report[0].startswith("FAILED")


class TestPluginOrderUpdater:
    def _valid_yml(self, entries: int = 150) -> bytes:
        rows = [
            f'- for_mod: "Mod{i}"\n  file_name: "Plugin{i}.esp"\n  on_lists:\n    - "total-overhaul"\n'
            for i in range(entries)
        ]
        return "".join(rows).encode()

    def test_valid_download_replaces_and_backs_up(self, tmp_path, http_server):
        base, routes = http_server
        routes["/po.yml"] = self._valid_yml()
        target = tmp_path / "plugin-order.yml"
        target.write_bytes(b'- for_mod: "Old"\n  file_name: "Old.esp"\n  on_lists:\n    - "x"\n')

        report = update_plugin_order_yml(target, urls=[f"{base}/po.yml"])

        assert target.read_bytes() == routes["/po.yml"]
        assert any("updated" in line for line in report)
        assert list(tmp_path.glob("plugin-order.yml.bak-*"))

    def test_undersized_file_is_refused(self, tmp_path, http_server):
        """An error page or truncated download must never clobber the file."""
        base, routes = http_server
        routes["/po.yml"] = self._valid_yml(entries=3)
        target = tmp_path / "plugin-order.yml"
        target.write_bytes(b"original")

        report = update_plugin_order_yml(target, urls=[f"{base}/po.yml"])

        assert target.read_bytes() == b"original"
        assert any("refusing" in line for line in report)

    def test_falls_through_to_the_next_candidate_url(self, tmp_path, http_server):
        base, routes = http_server
        routes["/good.yml"] = self._valid_yml()
        target = tmp_path / "plugin-order.yml"

        report = update_plugin_order_yml(target, urls=[f"{base}/missing.yml", f"{base}/good.yml"])

        assert target.read_bytes() == routes["/good.yml"]
        assert any("updated" in line for line in report)

    def test_failed_parses_do_not_leak_temp_files(self, tmp_path, http_server):
        """Regression: the temp file used for validation leaked on every
        parse failure."""
        base, routes = http_server
        routes["/bad.yml"] = b"file_name: x\non_lists: [y]\n" + b"\x00garbage\n" * 10
        target = tmp_path / "plugin-order.yml"
        temp_dir = Path(__import__("tempfile").gettempdir())

        before = set(temp_dir.glob("*.yml"))
        for _ in range(5):
            update_plugin_order_yml(target, urls=[f"{base}/bad.yml"])
        leaked = set(temp_dir.glob("*.yml")) - before

        assert not leaked, f"leaked temp files: {leaked}"
