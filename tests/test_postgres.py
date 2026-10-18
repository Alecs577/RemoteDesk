"""Real PostgreSQL integration. Set REMOTEDESK_TEST_DATABASE_URL to a disposable DB."""
import os
import secrets
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from remotedesk.accounts import Accounts, AccountError
from remotedesk.database import Database
from remotedesk.identity import ensure_host_identity, public_spki_b64, sign_register_auth
from remotedesk.relay_cluster import LOCK_ID, PostgresOwnerStore

URL = os.environ.get("REMOTEDESK_TEST_DATABASE_URL", "")


@unittest.skipUnless(URL, "PostgreSQL integration requires REMOTEDESK_TEST_DATABASE_URL")
class PostgresIntegrationTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        self.original_connect = psycopg.connect
        self.schema = "test_" + secrets.token_hex(8)
        with self.original_connect(URL, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        def connect(*args, **kwargs):
            connection = self.original_connect(*args, **kwargs)
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            if not kwargs.get("autocommit", False):
                connection.commit()
            return connection
        self.patcher = patch("psycopg.connect", side_effect=connect)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def tearDown(self):
        from psycopg import sql
        with self.original_connect(URL, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_election_and_persistent_identity_after_promotion(self):
        import psycopg
        first = psycopg.connect(URL, autocommit=True)
        second = psycopg.connect(URL, autocommit=True)
        try:
            self.assertTrue(first.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0])
            self.assertFalse(second.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0])
            root = Path(self.temporary.name)
            with patch("remotedesk.identity.config_dir", return_value=root):
                key_path, cert_path = ensure_host_identity("000000000")
            spki, secret = public_spki_b64(cert_path), secrets.token_hex(32)
            store = PostgresOwnerStore(first, threading.Event())
            signature = sign_register_auth(key_path, "NEW", secret, spki)
            status, desk_id = store.allocate(secret, spki, signature)
            self.assertEqual(status, "OK")
            self.assertEqual(store.allocate(secret, spki, signature), ("OK", desk_id))
            first.close()
            self.assertTrue(second.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0])
            replacement = PostgresOwnerStore(second, threading.Event())
            self.assertEqual(replacement.lookup_spki(desk_id), spki)
            self.assertEqual(replacement.verify_register(desk_id, secret, spki,
                sign_register_auth(key_path, desk_id, secret, spki)), "OK")
            wrong = secrets.token_hex(32)
            self.assertEqual(replacement.verify_register(desk_id, wrong, spki,
                sign_register_auth(key_path, desk_id, wrong, spki)), "ERR auth")
            with patch("remotedesk.identity.config_dir", return_value=root / "other"):
                other_key, other_cert = ensure_host_identity("000000000")
            other_secret, other_spki = secrets.token_hex(32), public_spki_b64(other_cert)
            status, other_id = replacement.allocate(other_secret, other_spki,
                sign_register_auth(other_key, "NEW", other_secret, other_spki))
            self.assertEqual(status, "OK")
            # An authenticated owner cannot crash the relay by rotating to another owner's key.
            self.assertEqual(replacement.verify_register(other_id, other_secret, spki,
                sign_register_auth(other_key, other_id, other_secret, spki)), "ERR auth")
            self.assertFalse(replacement.stop.is_set())
        finally:
            first.close()
            second.close()

    def test_accounts_and_persistent_shared_rate_limits(self):
        accounts = Accounts(Database(URL))
        password = "integration-account-password"
        code = accounts.register("alice", password)["recovery_code"]
        token = accounts.login("alice", password)["token"]
        self.assertEqual(accounts.authenticated(token, "devices", {}), {"devices": []})
        second = Accounts(Database(URL))
        second.limit("shared-test", maximum=1)
        with self.assertRaises(AccountError):
            accounts.limit("shared-test", maximum=1)
        second.reset("alice", code, "replacement-integration-password")
        with self.assertRaises(AccountError):
            accounts.authenticated(token, "devices", {})

    def test_database_loss_requests_relay_shutdown(self):
        import psycopg
        connection = psycopg.connect(URL, autocommit=True)
        stopped = threading.Event()
        store = PostgresOwnerStore(connection, stopped)
        connection.close()
        self.assertIsNone(store.lookup_spki("123456789"))
        self.assertTrue(stopped.is_set())

    def test_running_relays_promote_standby_and_accept_existing_identity_over_tls(self):
        import socket
        import ssl
        import time
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        from remotedesk.relay import run_relay, relay_handshake_allocate, relay_handshake_register
        from remotedesk.relay_cluster import run_cluster
        root = Path(self.temporary.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                       .public_key(key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
                       .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
                       .sign(key, hashes.SHA256()))
        cert_path, key_path = root / "relay-cert.pem", root / "relay-key.pem"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        ports = []
        for _ in range(2):
            with socket.socket() as temporary:
                temporary.bind(("127.0.0.1", 0))
                ports.append(temporary.getsockname()[1])
        active, failures = [], []
        ending = threading.Event()
        def captured(*args, **kwargs):
            active.append((args[1], kwargs["shutdown_event"]))
            if ending.is_set():
                kwargs["shutdown_event"].set()
            return run_relay(*args, **kwargs)
        def runner(port):
            try:
                run_cluster("127.0.0.1", port, cert_path, key_path, 16)
            except Exception as exc:
                failures.append(exc)
        def wait_active(count):
            deadline = time.monotonic()+15
            while len(active) < count and not failures and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(failures, str(failures))
            self.assertGreaterEqual(len(active), count)
            return active[count-1]
        context = ssl.create_default_context(cafile=str(cert_path))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        def connect(port):
            deadline = time.monotonic()+5
            while True:
                try:
                    raw = socket.create_connection(("127.0.0.1", port), timeout=2)
                    return context.wrap_socket(raw, server_hostname="localhost")
                except ConnectionRefusedError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        workers = []
        with patch.dict(os.environ, {"REMOTEDESK_DATABASE_URL": URL, "REMOTEDESK_TRUSTED_PROXIES": ""}), \
             patch("remotedesk.relay.run_relay", side_effect=captured):
            try:
                for port in ports:
                    worker = threading.Thread(target=runner, args=(port,), daemon=True)
                    workers.append(worker)
                    worker.start()
                first_port, first_stop = wait_active(1)
                with patch("remotedesk.identity.config_dir", return_value=root / "host"):
                    host_key, host_cert = ensure_host_identity("000000000")
                secret, spki = secrets.token_hex(32), public_spki_b64(host_cert)
                first_socket = connect(first_port)
                try:
                    status, desk_id = relay_handshake_allocate(first_socket, secret, spki, host_key)
                    self.assertEqual(status, "OK")
                finally:
                    first_socket.close()
                first_stop.set()
                second_port, _ = wait_active(2)
                self.assertNotEqual(second_port, first_port)
                second_socket = connect(second_port)
                try:
                    self.assertEqual(relay_handshake_register(second_socket, desk_id, secret, spki, host_key), "OK")
                finally:
                    second_socket.close()
            finally:
                ending.set()
                for _, stopped in active:
                    stopped.set()
                for worker in workers:
                    worker.join(timeout=15)
                self.assertFalse(any(worker.is_alive() for worker in workers), "Relay worker non terminato")
        self.assertFalse(failures, str(failures))
