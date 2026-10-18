"""Identity recovery commands executed before capture/UI initialization."""
from __future__ import annotations

import argparse
import base64
import getpass
from pathlib import Path

from remotedesk.account_client import AccountClient
from remotedesk.config import AppConfig
from remotedesk.identity import _atomic_write
from remotedesk.recovery import MAX_BACKUP, export_identity, restore_identity


def main(argv=None):
    parser = argparse.ArgumentParser(description="Backup e ripristino identità RemoteDesk (app chiusa)")
    parser.add_argument("operation", choices=("export", "restore", "cloud-restore"))
    parser.add_argument("--file", type=Path)
    parser.add_argument("--username")
    parser.add_argument("--device")
    parser.add_argument("--accounts-url", help="Override HTTPS solo per ambienti di prova")
    args = parser.parse_args(argv)
    if args.operation in ("export", "restore") and args.file is None:
        parser.error("Specificare --file.")
    if args.operation == "export":
        password = getpass.getpass("Nuova passphrase backup (almeno 16 caratteri): ")
        if password != getpass.getpass("Ripeti passphrase: "):
            parser.error("Le passphrase non coincidono.")
        if args.file.exists():
            parser.error("Il file di destinazione esiste già.")
        _atomic_write(args.file, export_identity(AppConfig.load(), password))
        print("Backup cifrato salvato.")
        return
    if args.operation == "cloud-restore":
        if not args.username or not args.device:
            parser.error("Specificare --username e --device.")
        client = AccountClient(args.accounts_url)
        client.request("login", username=args.username, password=getpass.getpass("Password account: "))
        try:
            result = client.request("download", device=args.device)
            blob = base64.b64decode(result["blob"], validate=True)
        finally:
            client.request("logout")
    else:
        with args.file.open("rb") as stream:
            blob = stream.read(MAX_BACKUP + 1)
    path = restore_identity(blob, getpass.getpass("Passphrase backup: "))
    print(f"Identità ripristinata in {path}. Puoi avviare RemoteDesk.")


if __name__ == "__main__":
    main()
