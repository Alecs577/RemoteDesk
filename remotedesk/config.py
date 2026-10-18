"""Configurazione persistente in %APPDATA%\\RemoteDesk\\config.json."""

from __future__ import annotations

import json
import base64
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from remotedesk.secure_store import protect_secret, unprotect_secret
from remotedesk.distribution import CURRENT


def config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "RemoteDesk"
    return Path.home() / ".remotedesk"


def config_path() -> Path:
    return config_dir() / "config.json"


def _new_desk_id() -> str:
    return f"{secrets.randbelow(1_000_000_000):09d}"


_VALID_QUALITIES = frozenset({"fluid", "balanced", "sharp"})
_VALID_INVITE_MINUTES = frozenset({1, 2, 5, 10})
_VALID_VIEW_MODES = frozenset({"fit", "stretch"})
_VALID_RECORD_INTERVALS = frozenset({125, 250, 500})
_VALID_DOWNLOAD_CONFLICTS = frozenset({"ask", "rename", "overwrite"})
_VALID_CONNECTION_TIMEOUTS = frozenset({30, 60, 120})


def normalize_default_quality(value: object) -> str:
    q = str(value).strip() if value is not None else ""
    return q if q in _VALID_QUALITIES else "balanced"


def normalize_invite_minutes(value: object) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 2
    return n if n in _VALID_INVITE_MINUTES else 2


def normalize_view_mode(value: object) -> str:
    mode = str(value).strip() if value is not None else ""
    return mode if mode in _VALID_VIEW_MODES else "fit"


def normalize_record_interval(value: object) -> int:
    try:
        interval = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 500
    return interval if interval in _VALID_RECORD_INTERVALS else 500


def normalize_download_conflict(value: object) -> str:
    policy = str(value).strip() if value is not None else ""
    return policy if policy in _VALID_DOWNLOAD_CONFLICTS else "ask"


def normalize_connection_timeout(value: object) -> int:
    try:
        timeout = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 60
    return timeout if timeout in _VALID_CONNECTION_TIMEOUTS else 60


@dataclass
class ContactEntry:
    alias: str
    desk_id: str


@dataclass
class AppConfig:
    desk_id: str
    relay: str = field(default_factory=lambda: CURRENT.relay)
    alias: str = ""
    contacts: list[ContactEntry] = field(default_factory=list)
    recent: list[ContactEntry] = field(default_factory=list)
    whitelist: list[str] = field(default_factory=list)
    host_secret: str = ""
    trusted: dict[str, str] = field(default_factory=dict)
    access_password_hash: str = ""
    start_with_windows: bool = False
    show_banner: bool = True
    interactive_access: bool = True
    confirm_incoming_files: bool = True
    default_quality: str = "balanced"
    show_remote_cursor: bool = True
    transmit_audio: bool = False
    invite_minutes: int = 2
    download_dir: str = ""
    record_dir: str = ""
    screenshot_dir: str = ""
    default_grants: dict[str, bool] = field(default_factory=dict)
    view_mode: str = "fit"
    start_fullscreen: bool = False
    auto_record_outgoing: bool = False
    record_interval_ms: int = 500
    ask_download_path: bool = False
    download_conflict: str = "ask"
    connection_timeout_seconds: int = 60
    identity_provisioned: bool = False

    @staticmethod
    def load() -> AppConfig:
        path = config_path()
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("La configurazione deve essere un oggetto JSON.")
                desk_id = str(raw.get("desk_id", "")).strip()
                relay = str(raw.get("relay", "")).strip() or CURRENT.relay
                alias = str(raw.get("alias", "")).strip()
                stored_host_secret = str(raw.get("host_secret", "")).strip()
                if stored_host_secret.startswith("secure:v1:"):
                    try:
                        secret_blob = base64.b64decode(stored_host_secret[10:], validate=True)
                        host_secret = unprotect_secret(secret_blob).decode("ascii")
                    except (ValueError, UnicodeDecodeError, OSError) as exc:
                        raise RuntimeError("Il segreto dell'identità RemoteDesk non è leggibile.") from exc
                else:
                    host_secret = stored_host_secret
                access_password_hash = str(raw.get("access_password_hash", "")).strip()
                trusted_raw = raw.get("trusted", {})
                trusted: dict[str, str] = {}
                if isinstance(trusted_raw, dict):
                    for k, v in trusted_raw.items():
                        kid = str(k).strip()
                        fp = str(v).strip().lower()
                        if len(kid) == 9 and kid.isdigit() and fp:
                            trusted[kid] = fp
                contacts_raw = raw.get("contacts", [])
                contacts: list[ContactEntry] = []
                if isinstance(contacts_raw, list):
                    for item in contacts_raw:
                        if not isinstance(item, dict):
                            continue
                        a = str(item.get("alias", "")).strip()
                        i = str(item.get("id", "")).strip()
                        if a and len(i) == 9 and i.isdigit():
                            contacts.append(ContactEntry(alias=a, desk_id=i))
                recent_raw = raw.get("recent", [])
                recent: list[ContactEntry] = []
                if isinstance(recent_raw, list):
                    for item in recent_raw:
                        if not isinstance(item, dict):
                            continue
                        a = str(item.get("alias", "")).strip()
                        i = str(item.get("id", "")).strip()
                        if len(i) == 9 and i.isdigit():
                            recent.append(ContactEntry(alias=a or i, desk_id=i))
                whitelist_raw = raw.get("whitelist", [])
                whitelist: list[str] = []
                if isinstance(whitelist_raw, list):
                    for item in whitelist_raw:
                        kid = str(item).strip()
                        if len(kid) == 9 and kid.isdigit():
                            whitelist.append(kid)
                start_with_windows = bool(raw.get("start_with_windows", False))
                show_banner = bool(raw.get("show_banner", True))
                interactive_access = bool(raw.get("interactive_access", True))
                confirm_incoming_files = bool(raw.get("confirm_incoming_files", True))
                default_quality = normalize_default_quality(raw.get("default_quality", "balanced"))
                show_remote_cursor = bool(raw.get("show_remote_cursor", True))
                transmit_audio = bool(raw.get("transmit_audio", False))
                invite_minutes = normalize_invite_minutes(raw.get("invite_minutes", 2))
                download_dir = str(raw.get("download_dir", "")).strip()
                record_dir = str(raw.get("record_dir", "")).strip()
                screenshot_dir = str(raw.get("screenshot_dir", "")).strip()
                view_mode = normalize_view_mode(raw.get("view_mode", "fit"))
                start_fullscreen = bool(raw.get("start_fullscreen", False))
                auto_record_outgoing = bool(raw.get("auto_record_outgoing", False))
                record_interval_ms = normalize_record_interval(raw.get("record_interval_ms", 500))
                ask_download_path = bool(raw.get("ask_download_path", False))
                download_conflict = normalize_download_conflict(raw.get("download_conflict", "ask"))
                connection_timeout_seconds = normalize_connection_timeout(
                    raw.get("connection_timeout_seconds", 60)
                )
                # Config legacy: prova a ri-autenticare l'ID esistente prima di richiederne uno nuovo.
                identity_provisioned = bool(raw.get("identity_provisioned", True))
                from remotedesk.grants import SessionGrants

                grants_raw = raw.get("default_grants", {})
                grant_defaults = SessionGrants(audio=transmit_audio)
                default_grants = {
                    name: value
                    for name, value in (
                        (name, grants_raw.get(name, getattr(grant_defaults, name)))
                        for name in grant_defaults.__dataclass_fields__
                    )
                    if isinstance(value, bool)
                } if isinstance(grants_raw, dict) else {}
                if len(desk_id) == 9 and desk_id.isdigit():
                    cfg = AppConfig(
                        desk_id=desk_id,
                        relay=relay,
                        alias=alias,
                        contacts=contacts,
                        recent=recent,
                        whitelist=whitelist,
                        host_secret=host_secret,
                        trusted=trusted,
                        access_password_hash=access_password_hash,
                        start_with_windows=start_with_windows,
                        show_banner=show_banner,
                        interactive_access=interactive_access,
                        confirm_incoming_files=confirm_incoming_files,
                        default_quality=default_quality,
                        show_remote_cursor=show_remote_cursor,
                        transmit_audio=transmit_audio,
                        invite_minutes=invite_minutes,
                        download_dir=download_dir,
                        record_dir=record_dir,
                        screenshot_dir=screenshot_dir,
                        default_grants=default_grants,
                        view_mode=view_mode,
                        start_fullscreen=start_fullscreen,
                        auto_record_outgoing=auto_record_outgoing,
                        record_interval_ms=record_interval_ms,
                        ask_download_path=ask_download_path,
                        download_conflict=download_conflict,
                        connection_timeout_seconds=connection_timeout_seconds,
                        identity_provisioned=identity_provisioned,
                    )
                    if not cfg.host_secret:
                        cfg.host_secret = secrets.token_hex(32)
                        cfg.save()
                    elif not stored_host_secret.startswith("secure:v1:"):
                        cfg.save()
                    return cfg
            except (OSError, ValueError, TypeError) as exc:
                raise RuntimeError("Configurazione RemoteDesk non valida: preservare il profilo e ripristinare un backup.") from exc
            raise RuntimeError("ID del profilo RemoteDesk non valido: la configurazione non è stata sovrascritta.")
        cfg = AppConfig(desk_id=_new_desk_id(), host_secret=secrets.token_hex(32))
        cfg.save()
        return cfg

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "desk_id": self.desk_id,
            # Empty means "use this build's default", so later domain changes migrate automatically.
            "relay": "" if self.relay == CURRENT.relay else self.relay,
            "alias": self.alias,
            "contacts": [{"alias": c.alias, "id": c.desk_id} for c in self.contacts],
            "recent": [{"alias": c.alias, "id": c.desk_id} for c in self.recent],
            "whitelist": list(self.whitelist),
            "host_secret": "secure:v1:" + base64.b64encode(
                protect_secret(self.host_secret.encode("ascii"))
            ).decode("ascii"),
            "trusted": self.trusted,
            "access_password_hash": self.access_password_hash,
            "start_with_windows": self.start_with_windows,
            "show_banner": self.show_banner,
            "interactive_access": self.interactive_access,
            "confirm_incoming_files": self.confirm_incoming_files,
            "default_quality": self.default_quality,
            "show_remote_cursor": self.show_remote_cursor,
            "transmit_audio": self.transmit_audio,
            "invite_minutes": self.invite_minutes,
            "download_dir": self.download_dir,
            "record_dir": self.record_dir,
            "screenshot_dir": self.screenshot_dir,
            "default_grants": self.default_grants,
            "view_mode": self.view_mode,
            "start_fullscreen": self.start_fullscreen,
            "auto_record_outgoing": self.auto_record_outgoing,
            "record_interval_ms": self.record_interval_ms,
            "ask_download_path": self.ask_download_path,
            "download_conflict": self.download_conflict,
            "connection_timeout_seconds": self.connection_timeout_seconds,
            "identity_provisioned": self.identity_provisioned,
        }
        temp = path.with_name(path.name + ".tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(data, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def resolve_invite_target(self, text: str) -> str | None:
        text = text.strip()
        if len(text) == 9 and text.isdigit():
            return text
        for c in self.contacts:
            if c.alias == text:
                return c.desk_id
        return None

    def add_contact(self, alias: str, desk_id: str) -> None:
        alias = alias.strip()
        desk_id = desk_id.strip()
        if not alias or len(desk_id) != 9 or not desk_id.isdigit():
            return
        self.contacts = [c for c in self.contacts if c.desk_id != desk_id]
        self.contacts.append(ContactEntry(alias=alias, desk_id=desk_id))
        self.save()

    def remove_contact(self, desk_id: str) -> None:
        desk_id = desk_id.strip()
        self.contacts = [c for c in self.contacts if c.desk_id != desk_id]
        self.save()

    def touch_recent(self, desk_id: str, alias: str = "") -> None:
        desk_id = desk_id.strip()
        if len(desk_id) != 9 or not desk_id.isdigit():
            return
        alias = alias.strip()
        if not alias:
            for c in self.contacts:
                if c.desk_id == desk_id:
                    alias = c.alias
                    break
            if not alias:
                alias = desk_id
        self.recent = [c for c in self.recent if c.desk_id != desk_id]
        self.recent.insert(0, ContactEntry(alias=alias, desk_id=desk_id))
        self.recent = self.recent[:12]
        self.save()


def parse_relay_host_port(relay: str) -> tuple[str, int] | None:
    relay = relay.strip()
    if not relay:
        return None
    if relay.startswith("["):
        return None
    if ":" not in relay:
        return None
    host, _, port_s = relay.rpartition(":")
    host = host.strip()
    port_s = port_s.strip()
    if not host or not port_s.isdigit():
        return None
    port = int(port_s)
    if port < 1 or port > 65535:
        return None
    return host, port
