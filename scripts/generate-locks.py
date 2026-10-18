"""Generate target-specific SHA256 locks from downloaded wheels, without executing them."""
import email
import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = {"pillow", "cryptography", "cffi", "pycparser"}
SERVER = RUNTIME | {"psycopg", "psycopg-binary", "tzdata", "setuptools", "wheel", "packaging"}


def generate(directory, destination, allowed=None):
    entries = []
    for wheel in sorted(directory.glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            metadata = email.message_from_bytes(archive.read(names[0]))
        name = re.sub(r"[-_.]+", "-", metadata["Name"]).lower()
        if allowed is not None and name not in allowed:
            continue
        entries.append(f"{name}=={metadata['Version']} --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}")
    if not entries:
        raise ValueError(f"Nessuna wheel disponibile in {directory}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("# Generated from PyPI wheels; install with --require-hashes --only-binary=:all:\n" +
                           "\n".join(sorted(entries)) + "\n", encoding="utf-8")


if __name__ == "__main__":
    generate(ROOT / "build/lock-wheels", ROOT / "requirements/windows-runtime.lock", RUNTIME)
    generate(ROOT / "build/lock-wheels", ROOT / "requirements/windows-server-tests.lock", SERVER)
    generate(ROOT / "build/lock-wheels", ROOT / "requirements/windows-build.lock",
             None if "--all" in sys.argv else RUNTIME | {"pyinstaller", "setuptools", "wheel", "packaging",
                    "altgraph", "pefile", "pyinstaller-hooks-contrib", "pywin32-ctypes"})
    generate(ROOT / "build/lock-linux", ROOT / "requirements/linux-relay.lock", SERVER)
