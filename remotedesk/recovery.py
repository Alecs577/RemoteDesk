"""Portable, authenticated identity backups. The server never receives the passphrase."""
from __future__ import annotations

import json
import secrets
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from remotedesk.config import AppConfig, parse_relay_host_port
from remotedesk.distribution import CURRENT
from remotedesk.identity import (_atomic_write, _private_key_pem, _store_encrypted_private_key,
                                 ensure_host_identity, load_host_private_key)

MAGIC = b"RemoteDesk identity backup v1\0"
MAX_BACKUP = 128 * 1024


def _derive(passphrase: str, salt: bytes) -> bytes:
    if not 16 <= len(passphrase) <= 1024:
        raise ValueError("La passphrase del backup deve contenere da 16 a 1024 caratteri.")
    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(passphrase.encode("utf-8"))


def export_identity(config: AppConfig, passphrase: str) -> bytes:
    if not config.identity_provisioned:
        raise ValueError("Collegare prima il dispositivo al relay per ottenere un ID definitivo.")
    key_path, cert_path = ensure_host_identity(config.desk_id)
    payload = json.dumps({"desk_id": config.desk_id, "relay": "" if config.relay == CURRENT.relay else config.relay,
                          "host_secret": config.host_secret,
                          "key": _private_key_pem(load_host_private_key(key_path)).decode("ascii"),
                          "cert": cert_path.read_text("ascii")}, sort_keys=True).encode()
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    ciphertext = AESGCM(_derive(passphrase, salt)).encrypt(nonce, payload, MAGIC)
    return MAGIC + salt + nonce + ciphertext


def decrypt_identity(blob: bytes, passphrase: str) -> dict:
    if not isinstance(blob, bytes) or not 72 <= len(blob) <= MAX_BACKUP or not blob.startswith(MAGIC):
        raise ValueError("Backup identità non valido.")
    start = len(MAGIC)
    payload = AESGCM(_derive(passphrase, blob[start:start+16])).decrypt(
        blob[start+16:start+28], blob[start+28:], MAGIC)
    data = json.loads(payload)
    if (not isinstance(data, dict) or not isinstance(data.get("desk_id"), str) or
            len(data["desk_id"]) != 9 or not data["desk_id"].isascii() or not data["desk_id"].isdigit() or
            len(data.get("host_secret", "")) != 64 or not isinstance(data.get("relay"), str) or
            (data["relay"] and parse_relay_host_port(data["relay"]) is None)):
        raise ValueError("Contenuto backup non valido.")
    bytes.fromhex(data["host_secret"])
    key = serialization.load_pem_private_key(data["key"].encode("ascii"), password=None)
    cert = x509.load_pem_x509_certificate(data["cert"].encode("ascii"))
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("Chiave identità non valida.")
    if key.public_key().public_numbers() != cert.public_key().public_numbers():
        raise ValueError("La chiave non corrisponde al certificato.")
    return data


def restore_identity(blob: bytes, passphrase: str) -> Path:
    """Restore ONLY into an empty profile. Never replace a running/existing identity."""
    data = decrypt_identity(blob, passphrase)
    from remotedesk.profile_lock import profile_lock
    with profile_lock() as directory:
        return _restore_into_profile(data, directory)


def _restore_into_profile(data: dict, directory: Path) -> Path:
    import os
    if any(item.name != ".profile.lock" for item in directory.iterdir()):
        raise ValueError("Ripristino consentito solo in un profilo RemoteDesk vuoto, con app chiusa.")
    # Exclusive marker prevents a simultaneous restore; leave it on error for operator inspection.
    marker = directory / "restore-in-progress"
    with marker.open("xb"):
        pass
    key_path = directory / "host-key.pem"
    key = serialization.load_pem_private_key(data["key"].encode("ascii"), password=None)
    if os.name == "nt":
        _store_encrypted_private_key(key_path, key_path.with_suffix(".pass.dpapi"), key)
    else:
        _atomic_write(key_path, _private_key_pem(key))
    _atomic_write(directory / "host-cert.pem", data["cert"].encode("ascii"))
    # Trust, access password and unattended grants deliberately require fresh consent.
    cfg = AppConfig(desk_id=data["desk_id"], relay=data["relay"] or CURRENT.relay,
                    host_secret=data["host_secret"], identity_provisioned=True)
    cfg.save()
    marker.unlink()
    return directory
