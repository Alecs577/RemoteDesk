"""Account credentials and encrypted device vault; no plaintext private keys stored here."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time

from remotedesk.database import Database
from remotedesk.identity import hash_access_password, verify_access_password
from remotedesk.recovery import MAGIC, MAX_BACKUP


class AccountError(ValueError):
    pass


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _password(value: object) -> str:
    if not isinstance(value, str) or not 16 <= len(value) <= 256:
        raise AccountError("La password account deve contenere da 16 a 256 caratteri.")
    return value


class Accounts:
    def __init__(self, database: Database):
        self.db = database
        self._dummy_hash = hash_access_password(secrets.token_urlsafe(32))
        with self.db.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS accounts (
                username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                recovery_hash TEXT NOT NULL, created BIGINT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS account_sessions (
                token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES accounts(username) ON DELETE CASCADE,
                expires BIGINT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS device_backups (
                username TEXT NOT NULL REFERENCES accounts(username) ON DELETE CASCADE,
                device TEXT NOT NULL, blob TEXT NOT NULL, updated BIGINT NOT NULL,
                PRIMARY KEY(username, device))""")
            db.execute("""CREATE TABLE IF NOT EXISTS account_limits (
                bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires BIGINT NOT NULL)""")

    def limit(self, key: str, maximum: int = 20, period: int = 600) -> None:
        now = int(time.time())
        bucket = _hash(key + ":" + str(now // period))
        with self.db.connect() as db:
            db.execute("DELETE FROM account_limits WHERE expires < ?", (now,))
            row = db.execute("""INSERT INTO account_limits(bucket, attempts, expires) VALUES (?, 1, ?)
                ON CONFLICT(bucket) DO UPDATE SET attempts=account_limits.attempts+1 RETURNING attempts""",
                (bucket, now + period)).fetchone()
        if row["attempts"] > maximum:
            raise AccountError("Troppi tentativi. Riprovare più tardi.")

    def _username(self, value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{3,64}", value):
            raise AccountError("Username: da 3 a 64 lettere, numeri, punti, trattini o underscore.")
        return value.lower()

    def register(self, username: str, password: str) -> dict:
        username, password = self._username(username), _password(password)
        recovery = secrets.token_urlsafe(32)
        password_hash = hash_access_password(password)
        with self.db.connect() as db:
            created = db.execute("""INSERT INTO accounts VALUES (?, ?, ?, ?)
                ON CONFLICT(username) DO NOTHING RETURNING username""",
                (username, password_hash, _hash(recovery), int(time.time()))).fetchone()
            if created is None:
                raise AccountError("Username non disponibile.")
        return {"recovery_code": recovery}

    def login(self, username: str, password: str) -> dict:
        username = self._username(username)
        _password(password)
        self.limit("login-user:" + username, 10)
        with self.db.connect() as db:
            lock = " FOR UPDATE" if self.db.postgres else ""
            row = db.execute("SELECT password_hash FROM accounts WHERE username=?" + lock, (username,)).fetchone()
            if not verify_access_password(password, row["password_hash"] if row else self._dummy_hash) or row is None:
                raise AccountError("Credenziali non valide.")
            token = secrets.token_urlsafe(32)
            now = int(time.time())
            db.execute("DELETE FROM account_sessions WHERE expires < ?", (now,))
            db.execute("INSERT INTO account_sessions VALUES (?, ?, ?)", (_hash(token), username, now+3600))
        return {"token": token, "expires": now + 3600}

    def reset(self, username: str, code: str, password: str) -> dict:
        username, password = self._username(username), _password(password)
        self.limit("reset:" + username, 5, 3600)
        if not isinstance(code, str) or len(code) > 128:
            raise AccountError("Codice di recupero non valido.")
        replacement = secrets.token_urlsafe(32)
        password_hash = hash_access_password(password)
        with self.db.connect() as db:
            row = db.execute("SELECT recovery_hash FROM accounts WHERE username=?", (username,)).fetchone()
            if row is None or not hmac.compare_digest(row["recovery_hash"], _hash(code)):
                raise AccountError("Codice di recupero non valido.")
            changed = db.execute("""UPDATE accounts SET password_hash=?, recovery_hash=?
                WHERE username=? AND recovery_hash=? RETURNING username""",
                (password_hash, _hash(replacement), username, _hash(code))).fetchone()
            if not changed:
                raise AccountError("Codice già utilizzato.")
            db.execute("DELETE FROM account_sessions WHERE username=?", (username,))
        return {"recovery_code": replacement}

    def authenticated(self, token: str, operation: str, data: dict) -> dict:
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise AccountError("Sessione non valida o scaduta.")
        with self.db.connect() as db:
            row = db.execute("SELECT username FROM account_sessions WHERE token_hash=? AND expires>?",
                             (_hash(token), int(time.time()))).fetchone()
            if row is None:
                raise AccountError("Sessione non valida o scaduta.")
            username = row["username"]
            if self.db.postgres:
                # Serialize vault quotas, account deletion, login and password reset per account.
                account = db.execute("SELECT username FROM accounts WHERE username=? FOR UPDATE", (username,)).fetchone()
                still_valid = db.execute("SELECT username FROM account_sessions WHERE token_hash=? AND expires>?",
                                         (_hash(token), int(time.time()))).fetchone()
                if account is None or still_valid is None:
                    raise AccountError("Sessione non valida o scaduta.")
            if operation == "logout":
                db.execute("DELETE FROM account_sessions WHERE token_hash=?", (_hash(token),))
                return {}
            if operation == "delete-account":
                db.execute("DELETE FROM accounts WHERE username=?", (username,))
                return {}
            if operation == "devices":
                rows = db.execute("SELECT device, updated FROM device_backups WHERE username=? ORDER BY device",
                                  (username,)).fetchall()
                return {"devices": [dict(item) for item in rows]}
            device = data.get("device", "")
            if not isinstance(device, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", device):
                raise AccountError("Nome dispositivo non valido.")
            if operation == "upload":
                try:
                    blob = base64.b64decode(data["blob"], validate=True)
                except (KeyError, ValueError, TypeError):
                    raise AccountError("Backup non valido.") from None
                if not 72 <= len(blob) <= MAX_BACKUP or not blob.startswith(MAGIC):
                    raise AccountError("Backup non valido.")
                count = db.execute("SELECT COUNT(*) AS n FROM device_backups WHERE username=?", (username,)).fetchone()["n"]
                existing = db.execute("SELECT device FROM device_backups WHERE username=? AND device=?", (username, device)).fetchone()
                if count >= 20 and existing is None:
                    raise AccountError("Limite di 20 dispositivi raggiunto.")
                db.execute("""INSERT INTO device_backups VALUES (?, ?, ?, ?)
                    ON CONFLICT(username,device) DO UPDATE SET blob=excluded.blob, updated=excluded.updated""",
                    (username, device, base64.b64encode(blob).decode("ascii"), int(time.time())))
                return {}
            if operation == "download":
                item = db.execute("SELECT blob FROM device_backups WHERE username=? AND device=?", (username,device)).fetchone()
                if item is None:
                    raise AccountError("Backup non trovato.")
                return {"blob": item["blob"]}
            if operation == "delete-device":
                db.execute("DELETE FROM device_backups WHERE username=? AND device=?", (username,device))
                return {}
            raise AccountError("Operazione non valida.")
