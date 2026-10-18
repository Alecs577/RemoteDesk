"""Test del protocollo relay corrente, senza interfaccia grafica."""

from __future__ import annotations

import unittest

from remotedesk.identity import sign_relay_invite
from remotedesk.relay import (
    read_line_socket,
    relay_handshake_invite,
    relay_handshake_join,
)
from remotedesk.safe_name import safe_download_basename
from tests.relay_support import RelayHarness


class RelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.relay = RelayHarness()
        self.addCleanup(self.relay.close)

    def test_register_join_ok_line(self) -> None:
        host = self.relay.allocate("host")
        client = self.relay.allocate("client")
        client.sock.close()

        join_sock = self.relay.connect()
        self.assertEqual(
            relay_handshake_join(join_sock, host.desk_id, client.desk_id, client.key_path),
            "OK",
        )
        host.sock.settimeout(5.0)
        self.assertEqual(read_line_socket(host.sock), "OK")
        join_sock.close()

    def test_join_unknown_id(self) -> None:
        client = self.relay.allocate("client")
        join_sock = self.relay.connect()
        self.assertEqual(
            relay_handshake_join(join_sock, "999999999", client.desk_id, client.key_path),
            "ERR unknown",
        )
        join_sock.close()

    def test_invite_then_join_ok(self) -> None:
        host = self.relay.allocate("host")
        client = self.relay.allocate("client")
        blob = "YmxvYjEyMw=="
        signature = sign_relay_invite(client.key_path, host.desk_id, client.desk_id, blob)
        invite_sock = self.relay.connect()
        self.assertEqual(
            relay_handshake_invite(invite_sock, host.desk_id, client.desk_id, blob, signature),
            "OK",
        )
        invite_sock.close()
        host.sock.settimeout(5.0)
        self.assertEqual(read_line_socket(host.sock), f"INVITE {client.desk_id} {blob}")

        join_sock = self.relay.connect()
        self.assertEqual(
            relay_handshake_join(join_sock, host.desk_id, client.desk_id, client.key_path),
            "OK",
        )
        self.assertEqual(read_line_socket(host.sock), "OK")
        join_sock.close()

    def test_safe_name_rejects_slash(self) -> None:
        self.assertIsNone(safe_download_basename("evil/name.txt"))
        self.assertEqual(safe_download_basename("report.pdf"), "report.pdf")
