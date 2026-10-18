"""Offline release tooling: profile generation, encrypted signing keys, signed metadata."""
from __future__ import annotations
import argparse
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from remotedesk.distribution import validate_public_profile
from remotedesk.updates import DOMAIN, canonical


def profile(source, destination, public=False):
    raw = json.loads(Path(source).read_text("utf-8-sig"))
    fields = raw["client"]
    if public:
        validate_public_profile(fields)
        attestation = raw.get("release", {})
        for key in ("legal_reviewed", "independent_audit_completed", "windows_acceptance_completed"):
            if attestation.get(key) is not True:
                raise ValueError(f"Release pubblica bloccata: {key} non attestato.")
        for name in ("terms", "privacy"):
            path = ROOT / "legal" / (name + ".md")
            text = path.read_text("utf-8")
            if "DIMOSTRATIV" in text.upper() or "TODO" in text.upper() or "[DA " in text.upper():
                raise ValueError(f"Sostituire i dati dimostrativi in {path.name} prima della release.")
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_text("# Generated distribution profile. No private keys.\nPUBLIC = " + repr(public) +
                                "\nPROFILE = " + repr(fields) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("profile")
    generate.add_argument("--config", required=True)
    generate.add_argument("--output", required=True)
    generate.add_argument("--public", action="store_true")
    keys = sub.add_parser("keygen")
    keys.add_argument("--private-key", type=Path, required=True)
    keys.add_argument("--public-key", type=Path, required=True)
    manifest = sub.add_parser("manifest")
    manifest.add_argument("--private-key", type=Path, required=True)
    manifest.add_argument("--artifact", type=Path, required=True)
    manifest.add_argument("--version", required=True)
    manifest.add_argument("--url", required=True)
    manifest.add_argument("--sequence", type=int, required=True)
    manifest.add_argument("--expires-days", type=int, default=14)
    manifest.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "profile":
        profile(args.config, args.output, args.public)
        return
    password = getpass.getpass("Password della chiave di firma offline (almeno 16 caratteri): ")
    if len(password) < 16:
        parser.error("Password troppo corta.")
    if args.command == "keygen":
        if password != getpass.getpass("Ripeti password: "):
            parser.error("Le password non coincidono.")
        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.BestAvailableEncryption(password.encode("utf-8")))
        # Exclusive creation; never overwrite an existing signing identity.
        fd = os.open(args.private_key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(private)
        with args.public_key.open("x", encoding="ascii") as stream:
            stream.write(base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii") + "\n")
        return
    if not 1 <= args.expires_days <= 30 or args.sequence < 1:
        parser.error("Scadenza da 1 a 30 giorni e sequenza positiva obbligatorie.")
    # Do not sign metadata for an unsigned executable. Use Windows SDK signtool from PATH.
    subprocess.run(["signtool", "verify", "/pa", "/all", "/v", str(args.artifact)], check=True)
    key = serialization.load_pem_private_key(args.private_key.read_bytes(), password=password.encode("utf-8"))
    if not isinstance(key, Ed25519PrivateKey):
        parser.error("Usare una chiave Ed25519.")
    payload = {"version": args.version, "platform": "windows-x86_64", "sequence": args.sequence,
               "expires": int(time.time()) + args.expires_days * 86400, "url": args.url,
               "size": args.artifact.stat().st_size, "sha256": hashlib.sha256(args.artifact.read_bytes()).hexdigest()}
    envelope = {"payload": payload, "signature": base64.b64encode(key.sign(DOMAIN + canonical(payload))).decode("ascii")}
    from remotedesk.updates import verify_manifest
    verify_manifest(json.dumps(envelope).encode(), base64.b64encode(key.public_key().public_bytes_raw()).decode())
    args.output.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
