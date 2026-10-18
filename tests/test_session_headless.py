"""Test headless del gate sessione (no Tk, no SendInput)."""

from __future__ import annotations

import json
import socket
import threading
import unittest
import urllib.error
import urllib.request

from remotedesk.server import HttpServerThread, handle_session_post
from remotedesk.state import ShareState


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post_session(port: int, code: str, timeout: float = 5.0) -> tuple[int, bytes]:
    url = f"http://127.0.0.1:{port}/session"
    body = json.dumps({"code": code}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _get_frame(port: int, code: str) -> int:
    url = f"http://127.0.0.1:{port}/frame.jpg?code={code}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


class SessionHeadlessTests(unittest.TestCase):
    def test_wrong_code_rejected(self) -> None:
        state = ShareState(session_code="123456")
        status, _ = handle_session_post(state, "000000")
        self.assertEqual(status, 403)

    def test_pending_blocks_frames_until_accept(self) -> None:
        state = ShareState(session_code="654321")
        code = state.session_code

        status, pending = state.begin_session(code)
        self.assertEqual(status, 0)
        self.assertIsNotNone(pending)

        frame_status, _ = state.get_frame_if_allowed(code)
        self.assertEqual(frame_status, 403)

        state.update_frame(b"fakejpeg", 100, 80, 0, 0, 1920, 1080)
        frame_status, _ = state.get_frame_if_allowed(code)
        self.assertEqual(frame_status, 403)

        state.resolve_session(True)
        self.assertEqual(state.wait_session(pending), 200)

        state.update_frame(b"fakejpeg", 100, 80, 0, 0, 1920, 1080)
        frame_status, data = state.get_frame_if_allowed(code)
        self.assertEqual(frame_status, 200)
        self.assertEqual(data, b"fakejpeg")

    def test_refuse_does_not_allow_frames(self) -> None:
        state = ShareState(session_code="111222")
        code = state.session_code
        status, pending = state.begin_session(code)
        self.assertEqual(status, 0)
        assert pending is not None
        state.resolve_session(False)
        self.assertEqual(state.wait_session(pending), 403)
        state.update_frame(b"x", 10, 10, 0, 0, 100, 100)
        frame_status, _ = state.get_frame_if_allowed(code)
        self.assertEqual(frame_status, 403)

    def test_http_session_flow(self) -> None:
        state = ShareState(session_code="999888")
        code = state.session_code
        port = _free_port()
        http = HttpServerThread(state, host="127.0.0.1", port=port)
        http.start()

        def accept_later() -> None:
            import time

            time.sleep(0.3)
            state.resolve_session(True)

        threading.Thread(target=accept_later, daemon=True).start()
        try:
            status, body = _post_session(port, code, timeout=10.0)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body.decode()), {"ok": True})
            state.update_frame(b"jpeg", 640, 480, 0, 0, 1920, 1080)
            self.assertEqual(_get_frame(port, code), 200)
        finally:
            http.stop()


if __name__ == "__main__":
    unittest.main()
