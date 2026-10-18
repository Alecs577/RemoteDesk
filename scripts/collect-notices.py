"""Collect actual wheel license files and SBOM for the built distribution."""
from __future__ import annotations
import importlib.metadata as metadata
import json
import hashlib
import ssl
from pathlib import Path
import sys

RUNTIME = {"pillow", "cryptography", "cffi", "pycparser"}


def main():
    destination = Path(sys.argv[1])
    destination.mkdir(parents=True, exist_ok=True)
    notices, packages = [], []
    for distribution in metadata.distributions():
        name = distribution.metadata["Name"].lower().replace("_", "-")
        files = distribution.files or []
        selected = [file for file in files if any(part.lower().startswith(("license", "copying", "notice")) for part in file.parts)
                    and not str(file).endswith(".py")]
        packages.append({"name": name, "version": distribution.version,
                         "license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License", ""),
                         "scope": "runtime" if name in RUNTIME else "build"})
        if name in RUNTIME and not selected:
            raise RuntimeError(f"Licenza mancante: {name}")
        if name not in RUNTIME | {"pyinstaller"}:
            continue
        notices.append(f"\n{'=' * 72}\n{name} {distribution.version}\n")
        for file in selected:
            path = Path(distribution.locate_file(file))
            if path.is_file():
                notices.append(f"\n--- {file} ---\n" + path.read_text("utf-8", errors="replace"))
    python_root = Path(sys.base_prefix)
    candidates = [python_root / "LICENSE.txt", python_root / "LICENSE"]
    python_license = next((p for p in candidates if p.is_file()), None)
    if python_license is None:
        raise RuntimeError("Licenza del runtime Python mancante")
    notices.append("\nPython runtime\n" + python_license.read_text("utf-8", errors="replace"))
    import tkinter
    interpreter = tkinter.Tcl()
    tcl_path = Path(interpreter.eval("info library"))
    tcl_licenses = list(tcl_path.parent.glob("*/license.terms")) + list(tcl_path.parent.glob("*/license*"))
    if not tcl_licenses:
        # The Windows Python installer may omit license.terms. Supply original runtime licenses explicitly.
        bundled = Path(__file__).resolve().parents[1] / "legal/runtime-licenses"
        tcl_licenses = list(bundled.glob("*.txt"))
    for path in sorted(set(tcl_licenses)):
        if path.is_file():
            notices.append(f"\nTcl/Tk: {path}\n" + path.read_text("utf-8", errors="replace"))
    from cryptography.hazmat.backends.openssl.backend import backend
    runtime_versions = {"tcl": interpreter.eval("info patchlevel"), "tk": interpreter.eval("info patchlevel"),
                        "openssl-python": ssl.OPENSSL_VERSION.split()[1],
                        "openssl-cryptography": backend.openssl_version_text().split()[1]}
    bundled = Path(__file__).resolve().parents[1] / "legal/runtime-licenses"
    records = json.loads((bundled / "sources.json").read_text("utf-8"))
    for name, version in runtime_versions.items():
        record = next((item for item in records if item["component"] == name and item["version"] == version), None)
        if record is None:
            raise RuntimeError(f"Licenza {name} {version} mancante: eseguire scripts/fetch-runtime-licenses.py.")
        contents = (bundled / record["file"]).read_bytes()
        if hashlib.sha256(contents).hexdigest() != record["sha256"]:
            raise RuntimeError(f"Hash licenza {name} non valido.")
        notices.append(f"\n{name} {version}\nSource: {record['source']}\n" + contents.decode("utf-8"))
        packages.append({"name": name, "version": version, "scope": "native-runtime"})
    (destination / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(notices), encoding="utf-8")
    (destination / "sbom.json").write_text(json.dumps({"format": "RemoteDesk dependency inventory v1", "packages": packages},
                                                     indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
