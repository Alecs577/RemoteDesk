"""Identità host: certificato TLS, segreto relay, token invito."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from remotedesk.config import config_dir
from remotedesk.secure_store import protect_secret, unprotect_secret


def cert_fingerprint_sha256(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def _b64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64_decode(text: str) -> bytes | None:
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        return None


def ensure_host_identity(desk_id: str) -> tuple[Path, Path]:
    """Genera/carica l'identità e migra la chiave legacy su storage protetto."""
    cfg = config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    key_path = cfg / "host-key.pem"
    cert_path = cfg / "host-cert.pem"
    password_path = key_path.with_suffix(".pass.dpapi")
    if cert_path.is_file() and not key_path.is_file():
        raise RuntimeError("Certificato RemoteDesk presente ma chiave privata mancante.")
    if key_path.is_file():
        key_data = key_path.read_bytes()
        password = _read_key_password(password_path) if _is_encrypted_private_key(key_data) else None
        key = serialization.load_pem_private_key(key_data, password=password)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise RuntimeError("La chiave privata RemoteDesk deve essere RSA.")
        if not cert_path.is_file():
            _write_certificate(cert_path, desk_id, key)
        if os.name == "nt" and password is None:
            _store_encrypted_private_key(key_path, password_path, key)
        return key_path, cert_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    _write_certificate(cert_path, desk_id, key)
    if os.name == "nt":
        _store_encrypted_private_key(key_path, password_path, key)
    else:
        key_path.write_bytes(_private_key_pem(key))
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
    return key_path, cert_path


def _private_key_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _is_encrypted_private_key(data: bytes) -> bool:
    return b"BEGIN ENCRYPTED PRIVATE KEY" in data or b"Proc-Type: 4,ENCRYPTED" in data


def _read_key_password(password_path: Path) -> bytes:
    return unprotect_secret(password_path.read_bytes())


def host_key_password(key_path: Path) -> bytes | None:
    if os.name != "nt":
        return None
    password_path = key_path.with_suffix(".pass.dpapi")
    if not password_path.is_file():
        raise RuntimeError("Password DPAPI della chiave RemoteDesk mancante.")
    return _read_key_password(password_path)


def load_host_private_key(key_path: Path) -> rsa.RSAPrivateKey:
    data = key_path.read_bytes()
    password = host_key_password(key_path) if _is_encrypted_private_key(data) else None
    key = serialization.load_pem_private_key(data, password=password)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("La chiave privata RemoteDesk non è RSA.")
    return key


def _store_encrypted_private_key(key_path: Path, password_path: Path, key: rsa.RSAPrivateKey) -> None:
    password = secrets.token_bytes(32)
    encrypted = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )
    _atomic_write(password_path, protect_secret(password))
    _atomic_write(key_path, encrypted)


def _atomic_write(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _write_certificate(cert_path: Path, desk_id: str, key: rsa.RSAPrivateKey) -> None:
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, desk_id)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    temp = cert_path.with_name(cert_path.name + ".tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(cert.public_bytes(serialization.Encoding.PEM))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, cert_path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def update_host_certificate_identity(cert_path: Path, key_path: Path, desk_id: str) -> None:
    """Rigenera il certificato locale dopo l'assegnazione relay dell'ID."""
    _write_certificate(cert_path, desk_id, load_host_private_key(key_path))


def peer_auth_message(from_id: str, to_id: str, challenge: bytes) -> bytes:
    return b"RemoteDesk peer authentication v1\0" + from_id.encode("ascii") + b"\0" + to_id.encode("ascii") + b"\0" + challenge


def sign_peer_auth(key_path: Path, from_id: str, to_id: str, challenge: bytes) -> bytes:
    key = load_host_private_key(key_path)
    return key.sign(
        peer_auth_message(from_id, to_id, challenge),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )


def verify_peer_auth(spki_b64: str, from_id: str, to_id: str, challenge: bytes, signature: bytes) -> bool:
    spki = _b64_decode(spki_b64)
    if spki is None:
        return False
    try:
        key = serialization.load_der_public_key(spki)
        if not isinstance(key, rsa.RSAPublicKey):
            return False
        key.verify(
            signature,
            peer_auth_message(from_id, to_id, challenge),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


def relay_invite_message(target_id: str, from_id: str, blob_b64: str) -> bytes:
    return (
        b"RemoteDesk relay invite v1\0"
        + target_id.encode("ascii")
        + b"\0"
        + from_id.encode("ascii")
        + b"\0"
        + blob_b64.encode("ascii")
    )


def join_auth_message(target_id: str, from_id: str, nonce: str) -> bytes:
    return (
        b"RemoteDesk relay join v1\0"
        + target_id.encode("ascii")
        + b"\0"
        + from_id.encode("ascii")
        + b"\0"
        + nonce.encode("ascii")
    )


def register_auth_message(desk_id: str, secret: str, spki_b64: str) -> bytes:
    secret_hash = hashlib.sha256(secret.encode("ascii")).hexdigest()
    return (
        b"RemoteDesk relay register v1\0"
        + desk_id.encode("ascii")
        + b"\0"
        + secret_hash.encode("ascii")
        + b"\0"
        + spki_b64.encode("ascii")
    )


def sign_join_auth(key_path: Path, target_id: str, from_id: str, nonce: str) -> str:
    signature = load_host_private_key(key_path).sign(
        join_auth_message(target_id, from_id, nonce),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    return _b64_encode(signature)


def sign_register_auth(key_path: Path, desk_id: str, secret: str, spki_b64: str) -> str:
    signature = load_host_private_key(key_path).sign(
        register_auth_message(desk_id, secret, spki_b64),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    return _b64_encode(signature)


def sign_relay_invite(key_path: Path, target_id: str, from_id: str, blob_b64: str) -> str:
    signature = load_host_private_key(key_path).sign(
        relay_invite_message(target_id, from_id, blob_b64),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    return _b64_encode(signature)


def verify_signed_message(spki_b64: str, message: bytes, signature_b64: str) -> bool:
    spki = _b64_decode(spki_b64)
    signature = _b64_decode(signature_b64)
    if spki is None or signature is None:
        return False
    try:
        key = serialization.load_der_public_key(spki)
        if not isinstance(key, rsa.RSAPublicKey):
            return False
        key.verify(
            signature,
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


def cert_spki_b64_from_der(der: bytes) -> str:
    cert = x509.load_der_x509_certificate(der)
    spki = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return _b64_encode(spki)


def valid_identity_spki(spki_b64: str) -> bool:
    spki = _b64_decode(spki_b64)
    if spki is None or len(spki) > 4096:
        return False
    try:
        key = serialization.load_der_public_key(spki)
        return isinstance(key, rsa.RSAPublicKey) and key.key_size >= 2048
    except Exception:
        return False


def _load_cert_der_pem(cert_path: Path) -> bytes:
    from cryptography.x509 import load_pem_x509_certificate

    pem = cert_path.read_bytes()
    return load_pem_x509_certificate(pem).public_bytes(serialization.Encoding.DER)


def cert_fingerprint_from_pem(cert_path: Path) -> str:
    return cert_fingerprint_sha256(_load_cert_der_pem(cert_path))


def public_spki_b64(cert_path: Path) -> str:
    from cryptography.x509 import load_pem_x509_certificate

    cert = load_pem_x509_certificate(cert_path.read_bytes())
    spki = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return _b64_encode(spki)


def seal_token(spki_b64: str, token: bytes) -> str:
    spki_der = _b64_decode(spki_b64)
    if spki_der is None:
        raise ValueError("spki non valido")
    pub = serialization.load_der_public_key(spki_der)
    encrypted = pub.encrypt(
        token,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return _b64_encode(encrypted)


def open_token(private_key_pem: bytes | Path, blob_b64: str) -> bytes | None:
    blob = _b64_decode(blob_b64)
    if blob is None:
        return None
    try:
        if isinstance(private_key_pem, Path):
            key = load_host_private_key(private_key_pem)
        else:
            key = serialization.load_pem_private_key(private_key_pem, password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            return None
        return key.decrypt(
            blob,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except (ValueError, TypeError, UnicodeError):
        return None


def new_host_secret() -> str:
    return secrets.token_hex(32)


def hash_access_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 600_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64_encode(salt)}${_b64_encode(dk)}"


def valid_new_access_password(password: str) -> bool:
    encoded = password.encode("utf-8")
    return len(password) >= 12 and len(encoded) <= 128


def verify_access_password(password: str, stored: str) -> bool:
    if not stored or not password or len(password.encode("utf-8")) > 128:
        return False
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256" or parts[1] not in {"200000", "600000"}:
        return False
    salt = _b64_decode(parts[2])
    expected = _b64_decode(parts[3])
    if salt is None or expected is None:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(parts[1]))
    return hmac.compare_digest(dk, expected)


def invite_matches(stored_token: str, presented: str) -> bool:
    if not stored_token or not presented:
        return False
    return hmac.compare_digest(stored_token, presented)
