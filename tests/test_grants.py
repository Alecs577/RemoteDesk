"""Test permessi di sessione (no Tk)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from remotedesk import tunnel
from remotedesk.grants import (
    SessionGrants,
    action_allowed,
    message_allowed,
    resolve_under_home,
)
from remotedesk.state import ShareState


class SessionGrantsTests(unittest.TestCase):
    def test_empty_payload_denies_permissions(self) -> None:
        g = SessionGrants.from_payload(b"")
        self.assertEqual(g, SessionGrants.view_only())

    def test_invalid_json_denies_permissions(self) -> None:
        g = SessionGrants.from_payload(b"not-json")
        self.assertEqual(g, SessionGrants.view_only())

    def test_control_false_blocks_input_only(self) -> None:
        payload = json.dumps(
            {"control": False, "clipboard": True, "files": True, "chat": True},
            separators=(",", ":"),
        ).encode("utf-8")
        g = SessionGrants.from_payload(payload)
        self.assertFalse(message_allowed(g, tunnel.INPUT))
        self.assertTrue(message_allowed(g, tunnel.CHAT))
        self.assertTrue(message_allowed(g, tunnel.CLIP))
        self.assertTrue(message_allowed(g, tunnel.FILE_OFFER))
        self.assertTrue(message_allowed(g, tunnel.FRAME))
        self.assertTrue(message_allowed(g, tunnel.HELLO))

    def test_files_false_blocks_file_messages(self) -> None:
        g = SessionGrants(control=True, clipboard=True, files=False, chat=True)
        for msg_type in (
            tunnel.FILE_OFFER,
            tunnel.FILE_ACCEPT,
            tunnel.FILE_REJECT,
            tunnel.FILE_CHUNK,
        ):
            self.assertFalse(message_allowed(g, msg_type))
        self.assertTrue(message_allowed(g, tunnel.INPUT))

    def test_missing_keys_default_false(self) -> None:
        g = SessionGrants.from_payload(b'{"control":false}')
        self.assertFalse(g.control)
        self.assertFalse(g.clipboard)
        self.assertFalse(g.files)
        self.assertFalse(g.chat)
        self.assertFalse(g.tcp)
        self.assertFalse(g.privacy)
        self.assertFalse(g.record)
        self.assertFalse(g.disconnect_user)
        self.assertFalse(g.secure_windows)

    def test_action_allowed_restart(self) -> None:
        g = SessionGrants(restart=False)
        self.assertFalse(action_allowed(g, "restart"))
        self.assertTrue(action_allowed(SessionGrants.all_on(), "restart"))

    def test_resolve_under_home(self) -> None:
        root = Path("C:/fake/home")
        inside = resolve_under_home("docs/file.txt", root)
        self.assertIsNotNone(inside)
        assert inside is not None
        self.assertEqual(inside, (root / "docs" / "file.txt").resolve())
        self.assertIsNone(resolve_under_home("..", root))
        self.assertIsNone(resolve_under_home("/etc/passwd", root))

    def test_view_only_all_false(self) -> None:
        g = SessionGrants.view_only()
        for f in SessionGrants.__dataclass_fields__:
            self.assertFalse(getattr(g, f))

    def test_input_allowed_respects_control_grant(self) -> None:
        state = ShareState(session_code="123456")
        code = state.session_code
        state.set_sharing(True)
        state.set_grants(SessionGrants(control=False, clipboard=True, files=True, chat=True))
        self.assertFalse(state.input_allowed(code))
        state.set_grants(SessionGrants.all_on())
        self.assertTrue(state.input_allowed(code))

    def test_chat_clip_file_allowed_need_sharing(self) -> None:
        state = ShareState()
        state.set_grants(SessionGrants(control=True, clipboard=False, files=False, chat=False))
        self.assertFalse(state.chat_allowed())
        self.assertFalse(state.clip_allowed())
        self.assertFalse(state.file_allowed())
        state.set_sharing(True)
        self.assertFalse(state.chat_allowed())
        state.set_grants(SessionGrants.all_on())
        self.assertTrue(state.chat_allowed())
        self.assertTrue(state.clip_allowed())
        self.assertTrue(state.file_allowed())


if __name__ == "__main__":
    unittest.main()
