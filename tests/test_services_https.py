"""Exercise HTTPS trust and account/update flows over real TLS sockets."""
from __future__ import annotations
import base64
import hashlib
import ipaddress
import json
import ssl
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
from cryptography.x509.oid import NameOID
from remotedesk.account_client import AccountClient
from remotedesk.account_server import BoundedServer, handler_for
from remotedesk.accounts import Accounts
from remotedesk.database import Database
from remotedesk.distribution import Distribution
from remotedesk.updates import DOMAIN, canonical, download_update, https_request


class HttpsServicesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
                .sign(key, hashes.SHA256()))
        self.cert_path, key_path = self.root / "cert.pem", self.root / "key.pem"
        self.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_3
        self.context.load_cert_chain(self.cert_path, key_path)
        self.trust = ssl.create_default_context(cafile=str(self.cert_path))
        self.servers = []

    def tearDown(self):
        for server, worker in self.servers:
            server.shutdown()
            server.server_close()
            worker.join(5)
        self.temporary.cleanup()

    def serve(self, handler):
        server = BoundedServer(("127.0.0.1", 0), handler, self.context)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.servers.append((server, worker))
        return f"https://localhost:{server.server_port}"

    def test_account_https_trust_invite_login_and_logout(self):
        accounts = Accounts(Database(str(self.root / "accounts.sqlite")))
        url = self.serve(handler_for(accounts, "test-registration-secret"))
        client = AccountClient(url)
        with self.assertRaises(URLError):
            client.request("login", username="alice", password="long-password-for-account")
        with patch("remotedesk.updates.ssl.create_default_context", return_value=self.trust):
            with self.assertRaises(ValueError):
                client.request("register", username="alice", password="long-password-for-account",
                               registration_code="wrong")
            result = client.request("register", username="alice", password="long-password-for-account",
                                    registration_code="test-registration-secret")
            self.assertTrue(result["recovery_code"])
            client.request("login", username="alice", password="long-password-for-account")
            self.assertEqual(client.request("devices"), {"devices": []})
            client.request("logout")
            with self.assertRaises(ValueError):
                client.request("devices")
            # A trusted certificate for localhost still cannot authenticate an IP hostname.
            with self.assertRaises(URLError):
                https_request(url.replace("localhost", "127.0.0.1") + "/v1/login", b"{}")

    def test_update_download_verifies_bytes_and_removes_partial_files(self):
        key = ed25519.Ed25519PrivateKey.generate()
        artifact = b"MZ-test-artifact-content"
        service = {"manifest": b"", "artifact": artifact}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                body = service["manifest"] if self.path == "/manifest.json" else service["artifact"]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        url = self.serve(Handler)
        payload = {"version": "0.2.0", "sequence": 1, "expires": int(time.time())+3600,
                   "platform": "windows-x86_64", "url": url+"/app.exe", "size": len(artifact),
                   "sha256": hashlib.sha256(artifact).hexdigest()}
        def sign():
            service["manifest"] = json.dumps({"payload": payload, "signature": base64.b64encode(
                key.sign(DOMAIN+canonical(payload))).decode()}).encode()
        sign()
        profile = Distribution(updates_url=url+"/manifest.json",
            update_public_key=base64.b64encode(key.public_key().public_bytes_raw()).decode())
        with patch("remotedesk.updates.ssl.create_default_context", return_value=self.trust), \
             patch("remotedesk.updates.CURRENT", profile), \
             patch("remotedesk.updates.config_dir", return_value=self.root / "client"):
            path = download_update()
            self.assertEqual(path.read_bytes(), artifact)
            with self.assertRaises(ValueError):
                download_update()  # replayed sequence
            payload.update(sequence=2, version="0.3.0")
            sign()
            service["artifact"] = b"X" * len(artifact)
            with self.assertRaises(ValueError):
                download_update()
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            self.assertFalse((path.parent / "RemoteDesk-0.3.0.exe").exists())
            self.assertEqual(json.loads((path.parent / "sequence.json").read_text())["sequence"], 1)


class ProxyTests(unittest.TestCase):
    def test_only_trusted_proxy_can_supply_source_and_tls_bytes_remain_unread(self):
        import socket
        from remotedesk.proxy_protocol import source_ip
        client, server = socket.socketpair()
        try:
            client.sendall(b"PROXY TCP4 203.0.113.7 10.20.0.11 51111 8770\r\nTLS")
            networks = (ipaddress.ip_network("10.20.0.10/32"),)
            with self.assertRaises(ValueError):
                source_ip(server, "203.0.113.2", networks)
            self.assertEqual(source_ip(server, "10.20.0.10", networks), "203.0.113.7")
            self.assertEqual(server.recv(3), b"TLS")
        finally:
            client.close()
            server.close()
