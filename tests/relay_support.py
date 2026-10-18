"""In-process relay protocol harness for loopback tests."""

from __future__ import annotations

import secrets
import socket
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from remotedesk.identity import ensure_host_identity, public_spki_b64
from remotedesk.relay import (
    OwnerStore,
    RateLimiter,
    RelayState,
    handle_connection,
    relay_handshake_allocate,
)


@dataclass
class TestIdentity:
    desk_id: str
    secret: str
    spki: str
    key_path: Path
    sock: socket.socket


class RelayHarness:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        self._state = RelayState(OwnerStore(self._root / "owners.json"))
        self._limiter = RateLimiter()
        self._clients: list[socket.socket] = []
        self._threads: list[threading.Thread] = []

    def connect(self) -> socket.socket:
        client, server = socket.socketpair()
        self._clients.append(client)
        worker = threading.Thread(
            target=handle_connection,
            args=(server, self._state, self._limiter, "127.0.0.1"),
            daemon=True,
        )
        self._threads.append(worker)
        worker.start()
        return client

    def allocate(self, label: str) -> TestIdentity:
        key_dir = self._root / label
        with patch("remotedesk.identity.config_dir", return_value=key_dir):
            key_path, cert_path = ensure_host_identity("000000000")
        secret = secrets.token_hex(32)
        spki = public_spki_b64(cert_path)
        sock = self.connect()
        status, desk_id = relay_handshake_allocate(sock, secret, spki, key_path)
        if status != "OK":
            raise AssertionError(f"allocation failed: {status} {desk_id}")
        return TestIdentity(desk_id, secret, spki, key_path, sock)

    def close(self) -> None:
        for client in self._clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
        for worker in self._threads:
            worker.join(timeout=2.0)
        self._tmp.cleanup()
