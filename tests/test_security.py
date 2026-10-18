"""Test sicurezza: identità, relay, TLS, password, invito."""

from __future__ import annotations

import base64
import secrets
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from remotedesk.identity import (
    ensure_host_identity,
    hash_access_password,
    invite_matches,
    open_token,
    public_spki_b64,
    seal_token,
    verify_access_password,
)
from remotedesk.relay import relay_handshake_register
from remotedesk.tls_channel import wrap_client, wrap_server
from tests.relay_support import RelayHarness


class IdentityTests(unittest.TestCase):
    def test_seal_open_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desk_id = "123456789"
            with patch("remotedesk.identity.config_dir", return_value=Path(tmp)):
                key_path, cert_path = ensure_host_identity(desk_id)
            key_path = Path(key_path)
            cert_path = Path(cert_path)
            spki = public_spki_b64(cert_path)
            token = b"secret-bytes"
            blob = seal_token(spki, token)
            opened = open_token(key_path, blob)
            self.assertEqual(opened, token)

    def test_open_wrong_key_returns_none(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization

        key1 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key2 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        spki = base64.b64encode(
            key1.public_key().public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode("ascii")
        blob = seal_token(spki, b"x")
        pem2 = key2.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self.assertIsNone(open_token(pem2, blob))

    def test_password_hash_verify(self) -> None:
        stored = hash_access_password("s3cret!")
        self.assertTrue(verify_access_password("s3cret!", stored))
        self.assertFalse(verify_access_password("wrong", stored))

    def test_invite_matches(self) -> None:
        t = secrets.token_urlsafe(32)
        self.assertTrue(invite_matches(t, t))
        self.assertFalse(invite_matches(t, ""))
        self.assertFalse(invite_matches("", t))
        self.assertFalse(invite_matches("abc", "abd"))


class RelayRegisterTests(unittest.TestCase):
    def test_register_same_secret_twice_ok(self) -> None:
        relay = RelayHarness()
        self.addCleanup(relay.close)
        identity = relay.allocate("same-secret")
        identity.sock.close()
        for _ in range(2):
            sock = relay.connect()
            self.assertEqual(
                relay_handshake_register(
                    sock, identity.desk_id, identity.secret, identity.spki, identity.key_path
                ),
                "OK",
            )
            sock.close()

    def test_register_wrong_secret_err_auth(self) -> None:
        relay = RelayHarness()
        self.addCleanup(relay.close)
        identity = relay.allocate("wrong-secret")
        identity.sock.close()
        sock = relay.connect()
        self.assertEqual(
            relay_handshake_register(
                sock, identity.desk_id, secrets.token_hex(32), identity.spki, identity.key_path
            ),
            "ERR auth",
        )
        sock.close()


class TlsWrapperTests(unittest.TestCase):
    def test_tls_fingerprint_match_and_mismatch(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch("remotedesk.identity.config_dir", return_value=Path(tmp.name)):
            key_path, cert_path = ensure_host_identity("333444555")
        key_path = Path(key_path)
        cert_path = Path(cert_path)
        from remotedesk.identity import cert_fingerprint_from_pem

        fp = cert_fingerprint_from_pem(cert_path)
        spki = public_spki_b64(cert_path)

        client_sock, server_sock = socket.socketpair()
        accepted: list[socket.socket] = []

        def accept_server() -> None:
            accepted.append(wrap_server(server_sock, cert_path, key_path))

        th = threading.Thread(target=accept_server, daemon=True)
        th.start()

        tls_client, err = wrap_client(
            client_sock,
            "333444555",
            {"333444555": fp},
            expected_spki=spki,
            confirm_fingerprint=None,
            timeout=5.0,
        )
        self.assertEqual(err, "")
        self.assertIsNotNone(tls_client)
        assert tls_client is not None
        tls_client.close()
        th.join(timeout=3.0)
        for channel in accepted:
            channel.close()

        c2, s2 = socket.socketpair()
        accepted.clear()

        def accept2() -> None:
            accepted.append(wrap_server(s2, cert_path, key_path))

        th2 = threading.Thread(target=accept2, daemon=True)
        th2.start()
        bad_fp = "0" * 64
        tls_bad, err_bad = wrap_client(
            c2,
            "333444555",
            {"333444555": bad_fp},
            expected_spki=spki,
            confirm_fingerprint=None,
            timeout=5.0,
        )
        self.assertIsNone(tls_bad)
        self.assertIn("Impronta diversa", err_bad)
        th2.join(timeout=3.0)
        for channel in accepted:
            channel.close()


if __name__ == "__main__":
    unittest.main()
