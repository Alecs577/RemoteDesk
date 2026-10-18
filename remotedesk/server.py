"""Server HTTP stdlib per visualizzatore e API."""

from __future__ import annotations

import json
import ipaddress
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from remotedesk.input_handler import handle_input
from remotedesk.state import ShareState
from remotedesk.viewer_page import VIEWER_HTML

PORT = 8765
SESSION_WAIT_SECONDS = 60.0
SESSION_JSON_OK = b'{"ok":true}'


def handle_session_post(state: ShareState, code: str | None) -> tuple[int, bytes]:
    if not isinstance(code, str):
        return 403, b"Forbidden"
    status, pending = state.begin_session(code)
    if status == 403:
        return 403, b"Forbidden"
    if status == 409:
        return 409, b"Conflict"
    if status == 200:
        return 200, SESSION_JSON_OK
    assert pending is not None
    final = state.wait_session(pending, SESSION_WAIT_SECONDS)
    if final == 200:
        return 200, SESSION_JSON_OK
    return 403, b"Forbidden"


class RemoteDeskHandler(BaseHTTPRequestHandler):
    state: ShareState

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        pass

    def _send(self, code: int, body: bytes | None = None, content_type: str = "text/plain") -> None:
        self.send_response(code)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'")
        if body is not None:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body is not None:
            self.wfile.write(body)

    def _allowed_request(self) -> bool:
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return False
        except ValueError:
            return False
        port = self.server.server_port
        host = self.headers.get("Host", "")
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{host}":
            return False
        fetch_site = self.headers.get("Sec-Fetch-Site")
        return fetch_site in (None, "same-origin", "none")

    def do_GET(self) -> None:  # noqa: N802
        if not self._allowed_request():
            self._send(403, b"Forbidden")
            return
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = VIEWER_HTML.encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
            return
        if parsed.path == "/frame.jpg":
            qs = parse_qs(parsed.query)
            code_list = qs.get("code")
            code = code_list[0] if code_list else None
            status, jpeg = self.state.get_frame_if_allowed(code)
            if status == 403:
                self._send(403, b"Forbidden")
                return
            if status == 204 or jpeg is None:
                self._send(204)
                return
            self._send(200, jpeg, "image/jpeg")
            return
        self._send(404, b"Not Found")

    def do_POST(self) -> None:  # noqa: N802
        if not self._allowed_request():
            self._send(403, b"Forbidden")
            return
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._send(400, b"Bad Request")
            return
        if length <= 0 or length > 65536:
            self._send(400, b"Bad Request")
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, b"Bad Request")
            return
        if not isinstance(payload, dict):
            self._send(400, b"Bad Request")
            return

        if parsed.path == "/session":
            code = payload.get("code")
            status, body = handle_session_post(self.state, code)
            ctype = "application/json" if status == 200 else "text/plain"
            self._send(status, body, ctype)
            return

        if parsed.path != "/input":
            self._send(404, b"Not Found")
            return

        code = payload.get("code")
        if not isinstance(code, str):
            self._send(403, b"Forbidden")
            return
        if not self.state.input_allowed(code):
            self._send(403, b"Forbidden")
            return
        handle_input(self.state, payload)
        self._send(204)


def make_handler(state: ShareState) -> type[RemoteDeskHandler]:
    class Handler(RemoteDeskHandler):
        pass

    Handler.state = state
    return Handler


class HttpServerThread:
    def __init__(self, state: ShareState, host: str = "127.0.0.1", port: int = PORT) -> None:
        if host != "127.0.0.1":
            raise ValueError("Il visualizzatore HTTP può ascoltare solo sul loopback")
        self._state = state
        self._host = host
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        handler = make_handler(self._state)
        self._httpd = ThreadingHTTPServer((self._host, self._port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="http")
        self._thread.start()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
