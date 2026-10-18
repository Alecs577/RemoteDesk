"""Stato condiviso tra UI, cattura e server HTTP."""

from __future__ import annotations

import hmac
import secrets
import threading
from dataclasses import dataclass, field

from remotedesk.grants import SessionGrants


@dataclass
class PendingSession:
    event: threading.Event = field(default_factory=threading.Event)
    accepted: bool = False


@dataclass
class ShareState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    session_code: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    sharing: bool = False
    latest_jpeg: bytes | None = None
    frame_width: int = 0
    frame_height: int = 0
    screen_left: int = 0
    screen_top: int = 0
    screen_width: int = 0
    screen_height: int = 0
    grants: SessionGrants = field(default_factory=SessionGrants.all_on)
    quality: str = "balanced"
    monitor_index: int = -1
    _pending: PendingSession | None = field(default=None, init=False, repr=False)

    def set_quality(self, quality: str) -> None:
        with self.lock:
            self.quality = quality

    def set_monitor_index(self, index: int) -> None:
        with self.lock:
            self.monitor_index = index

    def get_capture_prefs(self) -> tuple[str, int]:
        with self.lock:
            return self.quality, self.monitor_index

    def set_grants(self, grants: SessionGrants) -> None:
        with self.lock:
            self.grants = grants

    def code_matches(self, code: str | None) -> bool:
        if not code or len(code) > 128:
            return False
        return hmac.compare_digest(self.session_code, code)

    def set_sharing(self, on: bool) -> None:
        with self.lock:
            self.sharing = on
            if not on:
                self.latest_jpeg = None

    def update_frame(
        self,
        jpeg: bytes,
        fw: int,
        fh: int,
        sl: int,
        st: int,
        sw: int,
        sh: int,
    ) -> None:
        with self.lock:
            if not self.sharing:
                return
            self.latest_jpeg = jpeg
            self.frame_width = fw
            self.frame_height = fh
            self.screen_left = sl
            self.screen_top = st
            self.screen_width = sw
            self.screen_height = sh

    def get_frame_if_allowed(self, code: str | None) -> tuple[int, bytes | None]:
        with self.lock:
            if not self.sharing or not self.code_matches(code):
                return 403, None
            if self.latest_jpeg is None:
                return 204, None
            return 200, self.latest_jpeg

    def input_allowed(self, code: str | None) -> bool:
        with self.lock:
            return self.sharing and self.code_matches(code) and self.grants.control

    def chat_allowed(self) -> bool:
        with self.lock:
            return self.sharing and self.grants.chat

    def clip_allowed(self) -> bool:
        with self.lock:
            return self.sharing and self.grants.clipboard

    def file_allowed(self) -> bool:
        with self.lock:
            return self.sharing and self.grants.files

    def get_mapping(self) -> tuple[int, int, int, int, int, int]:
        with self.lock:
            return (
                self.frame_width,
                self.frame_height,
                self.screen_left,
                self.screen_top,
                self.screen_width,
                self.screen_height,
            )

    def begin_session(self, code: str) -> tuple[int, PendingSession | None]:
        """Avvia handshake sessione. Ritorna (403|409|200|0, pending)."""
        with self.lock:
            if not self.code_matches(code):
                return 403, None
            if self.sharing:
                return 200, None
            if self._pending is not None:
                return 409, None
            pending = PendingSession()
            self._pending = pending
            return 0, pending

    def wait_session(self, pending: PendingSession, timeout: float = 60.0) -> int:
        """Attende risposta UI; ritorna 200 se accettato e sharing attivo, altrimenti 403."""
        pending.event.wait(timeout)
        with self.lock:
            if self._pending is pending:
                self._pending = None
            ok = pending.event.is_set() and pending.accepted and self.sharing
        return 200 if ok else 403

    def get_pending_session(self) -> PendingSession | None:
        with self.lock:
            return self._pending

    def resolve_session(self, accept: bool) -> None:
        with self.lock:
            pending = self._pending
            if pending is None:
                return
            pending.accepted = accept
            if accept:
                self.sharing = True
        pending.event.set()
