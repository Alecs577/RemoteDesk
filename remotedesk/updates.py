"""Explicit update downloads with signed, expiring metadata and hash verification."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from remotedesk import __version__
from remotedesk.config import config_dir
from remotedesk.distribution import CURRENT

DOMAIN = b"RemoteDesk update manifest v1\0"
MAX_ARTIFACT = 512 * 1024 * 1024


def canonical(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", value):
        raise ValueError("Versione aggiornamento non valida.")
    return tuple(map(int, value.split(".")))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirect rifiutato: usare gli URL HTTPS definitivi.")


def https_request(url: str, data: bytes | None = None, headers: dict | None = None):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Richiesto URL HTTPS valido.")
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context), NoRedirect())
    return opener.open(urllib.request.Request(url, data=data, headers=headers or {}), timeout=30)


def verify_manifest(envelope: bytes, public_key: str, *, now: float | None = None,
                    minimum_sequence: int = 0) -> dict:
    if len(envelope) > 65536:
        raise ValueError("Manifest troppo grande.")
    raw = json.loads(envelope)
    payload = raw["payload"]
    signature = base64.b64decode(raw["signature"], validate=True)
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True)).verify(
        signature, DOMAIN + canonical(payload))
    current_time = time.time() if now is None else now
    if (type(payload.get("sequence")) is not int or payload["sequence"] <= minimum_sequence or
            type(payload.get("expires")) is not int or
            not current_time < payload["expires"] <= current_time + 31 * 86400 or
            payload.get("platform") != "windows-x86_64" or
            type(payload.get("size")) is not int or not 1 <= payload["size"] <= MAX_ARTIFACT or
            not re.fullmatch(r"[a-f0-9]{64}", payload.get("sha256", ""))):
        raise ValueError("Manifest scaduto, già utilizzato o non valido.")
    version_tuple(payload["version"])
    parsed = urlsplit(payload["url"])
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("URL artefatto non valido.")
    return payload


def download_update() -> Path:
    if not CURRENT.updates_url or not CURRENT.update_public_key:
        raise ValueError("Aggiornamenti non configurati in questa build.")
    directory = config_dir() / "updates"
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / "sequence.json"
    sequence = int(json.loads(state_path.read_text())["sequence"]) if state_path.exists() else 0
    with https_request(CURRENT.updates_url) as response:
        payload = verify_manifest(response.read(65537), CURRENT.update_public_key,
                                  minimum_sequence=sequence)
    if version_tuple(payload["version"]) <= version_tuple(__version__):
        raise ValueError("La versione installata è già aggiornata.")
    fd, filename = tempfile.mkstemp(prefix="download-", suffix=".tmp", dir=directory)
    temporary = Path(filename)
    destination = directory / ("RemoteDesk-" + payload["version"] + ".exe")
    try:
        digest, total = hashlib.sha256(), 0
        with os.fdopen(fd, "wb") as stream, https_request(payload["url"]) as response:
            while chunk := response.read(65536):
                total += len(chunk)
                if total > payload["size"]:
                    raise ValueError("Dimensione aggiornamento non valida.")
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if total != payload["size"] or digest.hexdigest() != payload["sha256"]:
            raise ValueError("Integrità aggiornamento non valida.")
        # Authenticode is verified by the release pipeline before metadata is signed.
        os.replace(temporary, destination)
        from remotedesk.identity import _atomic_write
        _atomic_write(state_path, json.dumps({"sequence": payload["sequence"]}).encode())
        return destination
    finally:
        temporary.unlink(missing_ok=True)
