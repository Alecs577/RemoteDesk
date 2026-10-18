"""PostgreSQL identity store and active/standby relay coordination."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time

from remotedesk.identity import register_auth_message, valid_identity_spki, verify_signed_message

LOCK_ID = 726443318531
logger = logging.getLogger(__name__)


class PostgresOwnerStore:
    """Use the elected leader's dedicated connection for ALL identity operations."""
    def __init__(self, connection, stop: threading.Event):
        self.connection, self.stop = connection, stop
        self.lock = threading.Lock()
        with self.lock:
            connection.execute("""CREATE TABLE IF NOT EXISTS relay_owners (
                desk_id TEXT PRIMARY KEY, secret_sha256 TEXT NOT NULL,
                spki TEXT UNIQUE NOT NULL)""")

    def _entry(self, desk_id):
        row = self.connection.execute("SELECT secret_sha256, spki FROM relay_owners WHERE desk_id=%s",
                                      (desk_id,)).fetchone()
        return row

    def _check(self, secret, spki):
        return (isinstance(secret, str) and len(secret) == 64 and
                all(c in "0123456789abcdefABCDEF" for c in secret) and valid_identity_spki(spki))

    def allocate(self, secret, spki, signature):
        if not self._check(secret, spki):
            return "ERR protocol", ""
        if not verify_signed_message(spki, register_auth_message("NEW", secret, spki), signature):
            return "ERR auth", ""
        secret_hash = hashlib.sha256(secret.encode("ascii")).hexdigest()
        try:
            with self.lock:
                row = self.connection.execute("SELECT desk_id, secret_sha256 FROM relay_owners WHERE spki=%s", (spki,)).fetchone()
                if row:
                    return ("OK", row[0]) if hmac.compare_digest(row[1], secret_hash) else ("ERR auth", "")
                for _ in range(100):
                    desk_id = f"{secrets.randbelow(1_000_000_000):09d}"
                    row = self.connection.execute("""INSERT INTO relay_owners VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING RETURNING desk_id""", (desk_id, secret_hash, spki)).fetchone()
                    if row:
                        return "OK", row[0]
                return "ERR storage", ""
        except Exception:
            self.stop.set()
            return "ERR storage", ""

    def verify_register(self, desk_id, secret, spki, signature):
        if not self._check(secret, spki):
            return "ERR protocol"
        try:
            with self.lock:
                row = self._entry(desk_id)
                if row is None:
                    return "ERR provisioning"
                if (not hmac.compare_digest(row[0], hashlib.sha256(secret.encode("ascii")).hexdigest()) or
                        not verify_signed_message(row[1], register_auth_message(desk_id, secret, spki), signature)):
                    return "ERR auth"
                duplicate = self.connection.execute("SELECT desk_id FROM relay_owners WHERE spki=%s AND desk_id<>%s",
                                                     (spki, desk_id)).fetchone()
                if duplicate is not None:
                    return "ERR auth"
                self.connection.execute("UPDATE relay_owners SET spki=%s WHERE desk_id=%s", (spki, desk_id))
                return "OK"
        except Exception:
            self.stop.set()
            return "ERR storage"

    def lookup_spki(self, desk_id):
        try:
            with self.lock:
                row = self._entry(desk_id)
                return row[1] if row else None
        except Exception:
            self.stop.set()
            return None

    def monitor(self):
        while not self.stop.wait(1):
            try:
                with self.lock:
                    self.connection.execute("SELECT 1").fetchone()
            except Exception:
                logger.error("Connessione al coordinatore persa: arresto del relay attivo")
                self.stop.set()


def run_cluster(host, port, cert_path, key_path, max_clients):
    import psycopg
    import ipaddress
    from remotedesk.relay import run_relay

    url = os.environ.get("REMOTEDESK_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("Configurare REMOTEDESK_DATABASE_URL con PostgreSQL.")
    proxy_networks = tuple(ipaddress.ip_network(part.strip()) for part in
                          os.environ.get("REMOTEDESK_TRUSTED_PROXIES", "").split(",") if part.strip())
    if any(network.prefixlen == 0 for network in proxy_networks):
        raise ValueError("Non è consentito fidarsi di qualsiasi IP per gli header PROXY.")
    # Session advisory locks require a dedicated connection, never PgBouncer transaction pooling.
    connection = psycopg.connect(url, autocommit=True, connect_timeout=5,
        keepalives=1, keepalives_idle=5, keepalives_interval=2, keepalives_count=2,
        options="-c statement_timeout=5000 -c lock_timeout=5000")
    try:
        logger.info("In attesa del ruolo relay attivo")
        while not connection.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
            time.sleep(2)
        stop = threading.Event()
        owners = PostgresOwnerStore(connection, stop)
        watchdog = threading.Thread(target=owners.monitor, daemon=True, name="relay-leader-watchdog")
        watchdog.start()
        try:
            run_relay(host, port, cert_path=cert_path, key_path=key_path,
                      max_clients=max_clients, owners_store=owners, shutdown_event=stop,
                      proxy_networks=proxy_networks)
        finally:
            stop.set()
            watchdog.join(timeout=6)
    finally:
        connection.close()


def migrate_owners(path):
    """Explicit one-time import; never silently replace existing ownership."""
    import psycopg
    from remotedesk.relay import OwnerStore
    if not path.is_file():
        raise ValueError("Archivio identità da importare non trovato.")
    store = OwnerStore(path)
    url = os.environ["REMOTEDESK_DATABASE_URL"]
    with psycopg.connect(url, connect_timeout=5) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        connection.execute("""CREATE TABLE IF NOT EXISTS relay_owners (
            desk_id TEXT PRIMARY KEY, secret_sha256 TEXT NOT NULL, spki TEXT UNIQUE NOT NULL)""")
        if connection.execute("SELECT COUNT(*) FROM relay_owners").fetchone()[0]:
            raise ValueError("Importazione consentita solo su una directory PostgreSQL vuota.")
        for desk_id, entry in store._data.items():
            connection.execute("INSERT INTO relay_owners VALUES (%s, %s, %s)",
                               (desk_id, entry["secret_sha256"], entry["spki"]))
