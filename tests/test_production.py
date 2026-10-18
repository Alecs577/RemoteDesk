"""Security boundaries for account vaults, portable recovery, release profiles and updates."""
from __future__ import annotations
import base64
import json
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from remotedesk.accounts import Accounts, AccountError
from remotedesk.config import AppConfig
from remotedesk.database import Database
from remotedesk.distribution import Distribution, validate_public_profile
from remotedesk.identity import public_spki_b64, ensure_host_identity
from remotedesk.recovery import export_identity, decrypt_identity, restore_identity
from remotedesk.updates import canonical, DOMAIN, verify_manifest


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.accounts = Accounts(Database(str(Path(self.temporary.name) / "accounts.sqlite")))
        self.password = "a-long-account-password-2026"
        self.code = self.accounts.register("alice", self.password)["recovery_code"]
        self.alice = self.accounts.login("alice", self.password)["token"]

    def tearDown(self):
        self.temporary.cleanup()

    def test_password_recovery_rotates_code_and_revokes_sessions(self):
        replacement = "replacement-password-2026"
        result = self.accounts.reset("alice", self.code, replacement)
        self.assertNotEqual(result["recovery_code"], self.code)
        with self.assertRaises(AccountError):
            self.accounts.authenticated(self.alice, "devices", {})
        with self.assertRaises(AccountError):
            self.accounts.reset("alice", self.code, replacement)
        with self.assertRaises(AccountError):
            self.accounts.login("alice", self.password)
        self.assertTrue(self.accounts.login("alice", replacement)["token"])

    def test_account_cannot_read_or_delete_another_accounts_backup(self):
        from remotedesk.recovery import MAGIC
        blob = base64.b64encode(MAGIC + secrets.token_bytes(100)).decode()
        self.accounts.authenticated(self.alice, "upload", {"device": "pc", "blob": blob})
        self.accounts.register("bob", self.password)
        bob = self.accounts.login("bob", self.password)["token"]
        with self.assertRaises(AccountError):
            self.accounts.authenticated(bob, "download", {"device": "pc"})
        self.accounts.authenticated(bob, "delete-device", {"device": "pc"})
        self.assertEqual(self.accounts.authenticated(self.alice, "download", {"device": "pc"})["blob"], blob)
        self.accounts.authenticated(self.alice, "delete-account", {})
        with self.assertRaises(AccountError):
            self.accounts.login("alice", self.password)

    def test_expired_sessions_and_persistent_limit(self):
        with self.accounts.db.connect() as db:
            db.execute("UPDATE account_sessions SET expires=0")
        with self.assertRaises(AccountError):
            self.accounts.authenticated(self.alice, "devices", {})
        for _ in range(3):
            self.accounts.limit("sensitive", 3)
        with self.assertRaises(AccountError):
            self.accounts.limit("sensitive", 3)


class RecoveryTests(unittest.TestCase):
    def test_roundtrip_to_another_profile_reprotects_key_and_does_not_restore_permissions(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            source, target = Path(source), Path(target)
            password = "portable-secret-passphrase-2026"
            config = AppConfig(desk_id="123456789", relay="relay.example.net:8770",
                               host_secret=secrets.token_hex(32), identity_provisioned=True)
            with patch("remotedesk.identity.config_dir", return_value=source):
                blob = export_identity(config, password)
                _, cert = ensure_host_identity(config.desk_id)
                original_spki = public_spki_b64(cert)
            self.assertNotIn(config.host_secret.encode(), blob)
            with self.assertRaises(InvalidTag):
                decrypt_identity(blob, "wrong-but-long-passphrase")
            changed = bytearray(blob)
            changed[-1] ^= 1
            with self.assertRaises(InvalidTag):
                decrypt_identity(bytes(changed), password)
            with patch("remotedesk.profile_lock.config_dir", return_value=target), \
                 patch("remotedesk.config.config_path", return_value=target / "config.json"):
                restore_identity(blob, password)
                loaded = AppConfig.load()
                self.assertEqual(loaded.desk_id, config.desk_id)
                self.assertEqual(loaded.host_secret, config.host_secret)
                self.assertEqual(loaded.trusted, {})
                self.assertEqual(loaded.access_password_hash, "")
                self.assertEqual(public_spki_b64(target / "host-cert.pem"), original_spki)
                with self.assertRaises(ValueError):
                    restore_identity(blob, password)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes_raw()).decode()
        self.payload = {"version": "0.2.0", "sequence": 10, "expires": int(time.time())+3600,
                        "platform": "windows-x86_64", "size": 123, "sha256": "ab"*32,
                        "url": "https://updates.example.net/RemoteDesk.exe"}

    def envelope(self, data):
        return json.dumps({"payload": data, "signature": base64.b64encode(
            self.key.sign(DOMAIN + canonical(data))).decode()}).encode()

    def test_tampered_manifest_rejected_even_with_valid_https(self):
        envelope = json.loads(self.envelope(self.payload))
        envelope["payload"]["url"] = "https://attacker.example.net/trojan.exe"
        with self.assertRaises(InvalidSignature):
            verify_manifest(json.dumps(envelope).encode(), self.public)

    def test_expiry_replay_and_insecure_urls_rejected(self):
        self.assertEqual(verify_manifest(self.envelope(self.payload), self.public)["sequence"], 10)
        with self.assertRaises(ValueError):
            verify_manifest(self.envelope(self.payload), self.public, minimum_sequence=10)
        for changes in ({"expires": 0}, {"url": "http://example.net/app.exe"},
                        {"size": 2**40}, {"version": "../malicious"}):
            with self.assertRaises(ValueError):
                verify_manifest(self.envelope(self.payload | changes), self.public)


class DistributionTests(unittest.TestCase):
    def test_default_relay_is_not_pinned_to_an_old_build(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            first = Distribution(relay="relay1.example.net:8770")
            second = Distribution(relay="relay2.example.net:8770")
            with patch("remotedesk.config.config_path", return_value=path):
                with patch("remotedesk.config.CURRENT", first):
                    config = AppConfig(desk_id="123456789", host_secret="a"*64)
                    config.save()
                with patch("remotedesk.config.CURRENT", second):
                    self.assertEqual(AppConfig.load().relay, second.relay)
    def test_corrupt_profile_is_preserved_and_a_second_process_cannot_share_it(self):
        from remotedesk.profile_lock import profile_lock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            path.write_bytes(b"{damaged")
            with patch("remotedesk.config.config_path", return_value=path):
                with self.assertRaises(RuntimeError):
                    AppConfig.load()
            self.assertEqual(path.read_bytes(), b"{damaged")
            with patch("remotedesk.profile_lock.config_dir", return_value=root):
                with profile_lock():
                    with self.assertRaises(RuntimeError):
                        with profile_lock():
                            self.fail("Un secondo processo ha acquisito il profilo")
    def test_empty_legacy_relay_uses_distribution_default_and_custom_relay_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for stored, expected in (("", "relay.example.net:8770"),
                                     ("private.example.net:8770", "private.example.net:8770")):
                path.write_text(json.dumps({"desk_id": "123456789", "host_secret": "a"*64, "relay": stored}))
                with patch("remotedesk.config.config_path", return_value=path), \
                     patch("remotedesk.config.CURRENT", Distribution(relay="relay.example.net:8770")):
                    self.assertEqual(AppConfig.load().relay, expected)

    def test_demo_profile_cannot_pass_public_release(self):
        profile = json.loads((Path(__file__).resolve().parents[1] / "deploy/client-profile.example.json").read_text())
        with self.assertRaises(ValueError):
            validate_public_profile(profile["client"])


if __name__ == "__main__":
    unittest.main()
