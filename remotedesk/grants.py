"""Permessi di sessione (controllo, appunti, file, chat, profilo invito)."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

from remotedesk import tunnel

@dataclass(frozen=True)
class SessionGrants:
    audio: bool = False
    control: bool = True
    restart: bool = False
    privacy: bool = False
    cad: bool = False
    block_input: bool = False
    lock: bool = False
    disconnect_user: bool = False
    pointer: bool = True
    clipboard: bool = True
    clipboard_files: bool = False
    files: bool = True
    sysinfo: bool = True
    draw: bool = False
    tcp: bool = False
    record: bool = False
    secure_windows: bool = False
    chat: bool = True

    @classmethod
    def all_on(cls) -> SessionGrants:
        return cls(**{f.name: True for f in fields(cls)})

    @classmethod
    def view_only(cls) -> SessionGrants:
        return cls(**{f.name: False for f in fields(cls)})

    def to_payload(self, version: str) -> bytes:
        data: dict[str, object] = {"v": version}
        for f in fields(self):
            data[f.name] = getattr(self, f.name)
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_payload(cls, payload: bytes) -> SessionGrants:
        if not payload:
            return cls.view_only()
        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return cls.view_only()
        if not isinstance(data, dict):
            return cls.view_only()

        kwargs: dict[str, bool] = {}
        for f in fields(cls):
            key = f.name
            kwargs[key] = data.get(key) is True
        return cls(**kwargs)


def action_allowed(grants: SessionGrants, op: str) -> bool:
    mapping = {
        "restart": "restart",
        "privacy": "privacy",
        "cad": "cad",
        "block_input": "block_input",
        "lock": "lock",
        "disconnect_user": "disconnect_user",
        "sysinfo": "sysinfo",
        "draw": "draw",
        "tcp": "tcp",
        "record": "record",
        "audio": "audio",
        "secure_windows": "secure_windows",
    }
    field = mapping.get(op)
    if field is None:
        return False
    return bool(getattr(grants, field))


def remote_allowed(from_id: str, whitelist: list[str], invite_ok: bool) -> bool:
    if invite_ok:
        return True
    if not whitelist:
        return True
    kid = from_id.strip()
    return len(kid) == 9 and kid.isdigit() and kid in whitelist


def message_allowed(grants: SessionGrants, msg_type: int) -> bool:
    if msg_type == tunnel.INPUT:
        return grants.control
    if msg_type == tunnel.CHAT:
        return grants.chat
    if msg_type == tunnel.CLIP:
        return grants.clipboard
    if msg_type in (
        tunnel.FILE_OFFER,
        tunnel.FILE_ACCEPT,
        tunnel.FILE_REJECT,
        tunnel.FILE_CHUNK,
    ):
        return grants.files
    if msg_type == tunnel.SYSINFO:
        return grants.sysinfo
    if msg_type == tunnel.DRAW:
        return grants.draw
    if msg_type == tunnel.AUDIO:
        return grants.audio
    if msg_type in (tunnel.TUNNEL_OPEN, tunnel.TUNNEL_DATA, tunnel.TUNNEL_CLOSE):
        return grants.tcp
    if msg_type == tunnel.FILE_LIST:
        return grants.files
    if msg_type == tunnel.REC:
        return grants.record
    if msg_type == tunnel.VIEW:
        return grants.control
    if msg_type == tunnel.GRANTS:
        return True
    if msg_type == tunnel.ACTION:
        return True
    return True


GRANT_UI_LABELS: tuple[tuple[str, str], ...] = (
    ("audio", "Ascoltare uscita audio"),
    ("control", "Controllo mouse e tastiera"),
    ("restart", "Riavviare"),
    ("privacy", "Modo privacy"),
    ("cad", "Ctrl+Alt+Canc"),
    ("block_input", "Bloccare tastiera e mouse locali"),
    ("lock", "Blocca il mio dispositivo"),
    ("disconnect_user", "Disconnetti utente"),
    ("pointer", "Puntatore colorato quando l'input fisico è bloccato"),
    ("clipboard", "Appunti"),
    ("clipboard_files", "Appunti per trasferire file"),
    ("files", "File manager"),
    ("sysinfo", "Informazioni di sistema"),
    ("draw", "Disegnare sullo schermo"),
    ("tcp", "Tunnel TCP"),
    ("record", "Registrazione sessione"),
    ("secure_windows", "Finestre proibite"),
    ("chat", "Chat"),
)

UNAVAILABLE_GRANTS = frozenset({"audio", "clipboard_files", "secure_windows", "cad"})


def grants_from_vars(vars_map: dict[str, bool]) -> SessionGrants:
    kwargs = {name: bool(vars_map.get(name, False)) for name, _ in GRANT_UI_LABELS}
    return SessionGrants(**kwargs)


def apply_grants_to_vars(grants: SessionGrants, vars_map: dict[str, object]) -> None:
    for name, _ in GRANT_UI_LABELS:
        var = vars_map.get(name)
        if var is not None and hasattr(var, "set"):
            var.set(getattr(grants, name))


def resolve_under_home(path: str, root: Path) -> Path | None:
    """Risolve path sotto root (home); None se fuori da root."""
    root = root.resolve()
    raw = path.strip() if path.strip() else "."
    try:
        candidate = (root / raw).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate
