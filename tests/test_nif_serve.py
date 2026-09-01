"""Tests for the loopback viewer server.

Most of these are about what the server *refuses*. It is a listening socket on
a machine full of the user's files, so the interesting assertions are that it
cannot be talked into serving anything nobody registered -- and in particular
that there is no path from a URL to the filesystem at all, which is a stronger
property than "traversal is blocked".
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

import pytest

from wraithguard.viz.serve import Payload, ViewerServer, payloads_for, publish_html_file

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def server() -> ViewerServer:
    """A running server, stopped afterwards.

    Yields:
        The server.
    """
    running = ViewerServer()
    running.start()
    try:
        yield running
    finally:
        running.stop()


def get(url: str) -> bytes:
    """Fetch a URL.

    Args:
        url: What to fetch.

    Returns:
        The body.
    """
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - loopback, our own
        return bytes(response.read())


def post(url: str, body: bytes) -> tuple[int, bytes, str]:
    """POST ``body`` to ``url``, returning (status, response body, disposition).

    Args:
        url: What to post to.
        body: The request body.

    Returns:
        The HTTP status, the response bytes, and the Content-Disposition header.
    """
    request = urllib.request.Request(url, data=body, method="POST")  # noqa: S310 - loopback
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - loopback
            return (
                response.status,
                bytes(response.read()),
                response.headers.get("Content-Disposition", ""),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, bytes(exc.read()), ""


class TestItServesWhatWasRegistered:
    """The ordinary path."""

    def test_a_published_payload_comes_back(self, server: ViewerServer) -> None:
        """With its content type intact."""
        url = server.publish("index.html", Payload(b"<h1>hi</h1>", "text/html"))
        assert get(url) == b"<h1>hi</h1>"


class TestPostHandlers:
    """The one way the server takes input: a registered POST handler."""

    def test_a_handler_runs_on_the_body_and_can_return_a_download(
        self, server: ViewerServer
    ) -> None:
        """A POST reaches the handler, whose payload comes back as an attachment."""
        url = server.register_post(
            "apply", lambda body: Payload(body.upper(), "application/octet-stream", "out.bin")
        )
        status, out, disposition = post(url, b"hello")
        assert status == 200
        assert out == b"HELLO"
        assert 'filename="out.bin"' in disposition

    def test_a_handler_that_raises_is_a_400_with_the_reason(self, server: ViewerServer) -> None:
        """A body the handler rejects yields 400 and the reason, not a crash."""

        def picky(_body: bytes) -> Payload:
            raise ValueError("nope, bad body")

        url = server.register_post("apply", picky)
        status, body, _ = post(url, b"anything")
        assert status == 400
        assert b"nope, bad body" in body

    def test_a_wrong_token_is_a_404(self, server: ViewerServer) -> None:
        """The token guards POST exactly as it guards GET."""
        url = server.register_post("apply", Payload)  # Payload(body) is a valid handler
        status, _, _ = post(url.split("?")[0] + "?t=wrong", b"x")
        assert status == 404

    def test_an_unregistered_key_is_a_404(self, server: ViewerServer) -> None:
        """Posting to a key with no handler is a 404, like an unknown payload."""
        # A real token, but no handler at this key.
        status, _, _ = post(f"http://127.0.0.1:{server.port}/nope?t={server.token}", b"x")
        assert status == 404

    def test_publishing_before_starting_is_an_error(self) -> None:
        """A URL to a server that is not listening is worse than an exception."""
        with pytest.raises(RuntimeError, match="not running"):
            ViewerServer().publish("x", Payload(b""))


class TestPublishHtmlFile:
    """Serving a written view over loopback, so no file:// URL is needed.

    Some platforms (the Steam Deck's browser, sandboxed webviews) refuse a
    file:// page, so a generated view is served over loopback by default and
    the written file is only the fallback.
    """

    def test_a_written_view_is_served_over_loopback(self, server, tmp_path) -> None:
        """The file's bytes come back from a real loopback fetch."""
        page = tmp_path / "cell_map.html"
        page.write_text("<html><body>the map</body></html>", encoding="utf-8")
        url = publish_html_file(server, page)
        assert url is not None
        assert url.startswith("http://127.0.0.1:")
        assert b"the map" in get(url)

    def test_no_server_means_the_file_fallback(self, tmp_path) -> None:
        """A ``None`` server (no port bound) returns ``None`` so the caller uses the file."""
        page = tmp_path / "x.html"
        page.write_text("<html></html>", encoding="utf-8")
        assert publish_html_file(None, page) is None

    def test_a_server_that_never_started_falls_back(self, tmp_path) -> None:
        """A locked-down machine that could not bind a port keeps the file."""
        page = tmp_path / "x.html"
        page.write_text("<html></html>", encoding="utf-8")
        assert publish_html_file(ViewerServer(), page) is None

    def test_a_missing_file_falls_back(self, server, tmp_path) -> None:
        """An unreadable view is reported as no-URL rather than raising."""
        assert publish_html_file(server, tmp_path / "nope.html") is None

    def test_binary_payloads_survive_intact(self, server: ViewerServer) -> None:
        """Geometry is deflated binary; any text handling would corrupt it."""
        blob = bytes(range(256)) * 4
        assert get(server.publish("g.bin", Payload(blob))) == blob


class TestItRefusesEverythingElse:
    """The reason this file is mostly negative assertions."""

    def test_no_token_is_a_404(self, server: ViewerServer) -> None:
        """404 rather than 403: a 403 confirms the key exists."""
        url = server.publish("index.html", Payload(b"x")).split("?")[0]
        with pytest.raises(urllib.error.HTTPError) as caught:
            get(url)
        assert caught.value.code == 404

    def test_a_wrong_token_is_a_404(self, server: ViewerServer) -> None:
        """Indistinguishable from a missing key, deliberately."""
        url = server.publish("index.html", Payload(b"x")).split("?")[0]
        with pytest.raises(urllib.error.HTTPError) as caught:
            get(f"{url}?t=not-the-token")
        assert caught.value.code == 404

    @pytest.mark.parametrize(
        "probe",
        [
            "../../../etc/passwd",
            "..%2f..%2f..%2fetc%2fpasswd",
            "wraithguard/viz/serve.py",
            "/etc/hostname",
            "....//....//etc/passwd",
        ],
    )
    def test_nothing_reaches_the_filesystem(self, server: ViewerServer, probe: str) -> None:
        """Not "traversal is blocked" -- there is nothing to traverse.

        The server has no code path from a URL to ``open``. A key that was
        never registered simply is not in the dictionary, so every one of these
        is an ordinary miss rather than a defence that could be outwitted.
        """
        server.publish("index.html", Payload(b"x"))
        with pytest.raises(urllib.error.HTTPError) as caught:
            get(f"http://127.0.0.1:{server.port}/{probe}?t={server.token}")
        assert caught.value.code == 404

    def test_each_server_has_its_own_token(self) -> None:
        """One window's token must not open another's."""
        first, second = ViewerServer(), ViewerServer()
        assert first.token != second.token

    def test_a_token_from_another_server_does_not_work(self, server: ViewerServer) -> None:
        """The concrete form of the property above."""
        other = ViewerServer()
        url = server.publish("index.html", Payload(b"x")).split("?")[0]
        with pytest.raises(urllib.error.HTTPError) as caught:
            get(f"{url}?t={other.token}")
        assert caught.value.code == 404


class TestItStaysOnLoopback:
    """Nothing off this machine may reach it."""

    def test_it_binds_loopback_only(self, server: ViewerServer) -> None:
        """Binding all interfaces would expose a user's meshes to the network.

        Checked by asking the OS what the socket is bound to rather than by
        reading the constant back, which would only test that a literal equals
        itself.
        """
        assert server._server is not None
        assert server._server.server_address[0] == "127.0.0.1"

    def test_the_port_is_ephemeral(self, server: ViewerServer) -> None:
        """Chosen by the OS, so nothing can be predicted or squatted."""
        assert server.port > 0
        assert server.port != 80

    def test_it_is_not_reachable_on_another_local_address(self, server: ViewerServer) -> None:
        """A bind to 127.0.0.1 must refuse a connection to a routable address.

        Skipped when the machine has no other address to try, which is the
        case in many containers.
        """
        try:
            outward = socket.gethostbyname(socket.gethostname())
        except OSError:  # pragma: no cover - depends on the host
            pytest.skip("no resolvable hostname")
        if outward.startswith("127."):
            pytest.skip("this machine only has loopback")
        with socket.socket() as probe:
            probe.settimeout(2)
            assert probe.connect_ex((outward, server.port)) != 0


class TestLifecycle:
    """Starting and stopping gets wired to more than one event."""

    def test_stopping_twice_is_safe(self, server: ViewerServer) -> None:
        """Window close and app exit both plausibly call it."""
        server.stop()
        server.stop()
        assert not server.running

    def test_starting_twice_keeps_one_port(self, server: ViewerServer) -> None:
        """Otherwise a second call would leak a socket."""
        first = server.port
        server.start()
        assert server.port == first

    def test_stopping_forgets_the_payloads(self, server: ViewerServer) -> None:
        """A closed viewer must not leave a user's geometry in memory."""
        server.publish("index.html", Payload(b"secret"))
        server.stop()
        assert server.fetch("index.html", server.token) is None

    def test_stopping_without_a_thread_is_safe(self, server: ViewerServer) -> None:
        """Stopping when the serving thread reference is already gone is fine."""
        server._thread = None  # no thread to join -> the join is skipped
        server.stop()
        assert not server.running


class TestPublishHtmlFileFallbacks:
    """Serving a written page over loopback, with the fallbacks."""

    def test_a_missing_server_falls_back_to_the_file(self, tmp_path: Path) -> None:
        """No server means the caller keeps the file path."""
        page = tmp_path / "v.html"
        page.write_bytes(b"<html>")
        assert publish_html_file(None, page) is None

    def test_an_unreadable_file_falls_back(self, server: ViewerServer, tmp_path: Path) -> None:
        """A page that cannot be read yields no URL."""
        assert publish_html_file(server, tmp_path / "does-not-exist.html") is None

    def test_a_stop_race_falls_back_to_the_file(
        self, server: ViewerServer, tmp_path: Path, monkeypatch
    ) -> None:
        """If the server stops mid-publish, the RuntimeError becomes a fallback."""
        page = tmp_path / "v.html"
        page.write_bytes(b"<html>")

        def stopped(_kind: str):
            raise RuntimeError("the viewer server is not running")

        monkeypatch.setattr(server, "publish_session", stopped)
        assert publish_html_file(server, page) is None

    def test_a_running_server_serves_the_page(self, server: ViewerServer, tmp_path: Path) -> None:
        """The ordinary path returns a loopback URL that serves the file."""
        page = tmp_path / "v.html"
        page.write_bytes(b"<h1>page</h1>")
        url = publish_html_file(server, page)
        assert url is not None
        assert get(url) == b"<h1>page</h1>"


def _raw_status(port: int, path: str, content_length: str) -> int:
    """Send a hand-built POST with a chosen Content-Length and read the status.

    Args:
        port: The server port.
        path: The request path, token included.
        content_length: The literal ``Content-Length`` header value to send.

    Returns:
        The numeric HTTP status from the response line.
    """
    request = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Content-Length: {content_length}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode()
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(request)
        line = b""
        while b"\r\n" not in line:
            chunk = sock.recv(256)
            if not chunk:
                break
            line += chunk
    return int(line.split()[1])


class TestPostRequestGuards:
    """Malformed POST framing is answered with a status, not a crash."""

    def test_a_non_numeric_content_length_is_a_400(self, server: ViewerServer) -> None:
        """A Content-Length that is not a number is a bad request."""
        server.register_post("apply", Payload)
        status = _raw_status(server.port, f"/apply?t={server.token}", "not-a-number")
        assert status == 400

    def test_an_oversized_content_length_is_a_413(self, server: ViewerServer) -> None:
        """A body larger than the cap is refused before it is read."""
        server.register_post("apply", Payload)
        status = _raw_status(server.port, f"/apply?t={server.token}", str(9 * 1024 * 1024))
        assert status == 413


class TestPublishRequiresARunningServer:
    """Publishing depends on the listener actually being up."""

    def test_publishing_before_start_raises(self) -> None:
        """A publish with no server behind it is a RuntimeError, not a silent URL."""
        idle = ViewerServer()
        with pytest.raises(RuntimeError, match="not running"):
            idle.publish("index.html", Payload(b"x"))

    def test_registering_a_post_before_start_raises(self) -> None:
        """Registering a handler before the listener is up is also refused."""
        idle = ViewerServer()
        with pytest.raises(RuntimeError, match="not running"):
            idle.register_post("apply", Payload)

    def test_a_session_namespaces_a_post_handler(self, server: ViewerServer) -> None:
        """A publish session registers POST handlers under its own prefix."""
        session = server.publish_session("mesh")
        url = session.register_post("apply", lambda body: Payload(body.upper()))
        status, out, _ = post(url, b"hi")
        assert status == 200
        assert out == b"HI"


class TestPayloadBundle:
    """The convenience that assembles one session."""

    def test_it_types_each_piece(self) -> None:
        """A wrong content type on the page shows HTML as plain text."""
        bundle = payloads_for("<html>", "var x = 1;", {"g0.bin": b"\x00\x01"})
        assert bundle["index.html"].content_type.startswith("text/html")
        assert bundle["three.js"].content_type.startswith("text/javascript")
        assert bundle["g0.bin"].body == b"\x00\x01"
