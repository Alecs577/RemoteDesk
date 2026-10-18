"""Download original licenses for the exact native runtime versions, never execute remote code."""
import hashlib
import json
import re
import ssl
import tkinter
import urllib.request
from pathlib import Path
from cryptography.hazmat.backends.openssl.backend import backend

ROOT = Path(__file__).resolve().parents[1] / "legal/runtime-licenses"


def main():
    interpreter = tkinter.Tcl()
    tcl = interpreter.eval("info patchlevel")
    # Python for Windows ships Tcl and Tk together with matching versions.
    versions = {"tcl": tcl, "tk": tcl,
                "openssl-python": ssl.OPENSSL_VERSION.split()[1],
                "openssl-cryptography": backend.openssl_version_text().split()[1]}
    ROOT.mkdir(parents=True, exist_ok=True)
    records = []
    for name, version in versions.items():
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[a-z]?", version):
            raise ValueError(f"Versione non supportata: {name} {version}")
        if name in ("tcl", "tk"):
            url = f"https://raw.githubusercontent.com/tcltk/{name}/core-{version.replace('.', '-')}/license.terms"
        else:
            tag = ("OpenSSL_" + version.replace(".", "_")) if version.startswith("1.") else "openssl-" + version
            url = f"https://raw.githubusercontent.com/openssl/openssl/{tag}/LICENSE.txt"
        with urllib.request.urlopen(url, timeout=30) as response:
            contents = response.read(128 * 1024)
        filename = name + "-" + version + ".txt"
        (ROOT / filename).write_bytes(contents)
        records.append({"component": name, "version": version, "source": url, "file": filename,
                        "sha256": hashlib.sha256(contents).hexdigest()})
    (ROOT / "sources.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
