"""Finestre Tkinter dell'host (italiano)."""

from __future__ import annotations

import io
import json
import socket
import struct
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import tkinter.messagebox as messagebox
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk
from typing import Callable

from PIL import Image, ImageTk

from remotedesk import __version__
from remotedesk.config import AppConfig, parse_relay_host_port
from remotedesk.file_receive import FileReceiver
from remotedesk.identity import (
    cert_fingerprint_from_pem,
    ensure_host_identity,
    hash_access_password,
    valid_new_access_password,
    invite_matches,
    public_spki_b64,
    seal_token,
    sign_relay_invite,
    verify_access_password,
)
from remotedesk.capture import list_monitors
from remotedesk.grants import (
    GRANT_UI_LABELS,
    SessionGrants,
    UNAVAILABLE_GRANTS,
    apply_grants_to_vars,
    grants_from_vars,
    remote_allowed,
)
from remotedesk.relay_session import (
    ClientTunnel,
    FrameSenderThread,
    HostRelayThread,
    client_connect,
    connect_relay,
    decode_hello,
    downloads_dir,
    new_invite_token,
    recordings_base_dir,
    screenshots_base_dir,
)
from remotedesk.relay import relay_handshake_invite, relay_handshake_lookup
from remotedesk.session_actions import SessionActionHost
from remotedesk.safe_name import safe_download_basename
from remotedesk.state import PendingSession, ShareState
from remotedesk import tunnel
from remotedesk.settings import SettingsWindow
from remotedesk.theme import ACTION, FIELD, INK, MUTED, PAPER, SKY, apply_theme, group_desk_id


@dataclass
class PendingInvite:
    remote_id: str
    grants: SessionGrants
    expires: float
    token: str


def choose_download_destination(
    parent: tk.Misc,
    directory: Path,
    filename: str,
    *,
    ask_path: bool,
    conflict_policy: str,
) -> Path | None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    destination = directory / filename
    if ask_path:
        selected = filedialog.asksaveasfilename(
            parent=parent,
            title="Salva file ricevuto",
            initialdir=str(directory),
            initialfile=filename,
        )
        if not selected:
            return None
        destination = Path(selected)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

    if not destination.exists():
        return destination
    if conflict_policy == "overwrite":
        return destination
    if conflict_policy == "rename":
        return _unique_download_path(destination)
    overwrite = messagebox.askyesnocancel(
        "File già esistente",
        f"{destination.name} esiste già.\nSì: sovrascrivi.\nNo: salva con un altro nome.\nAnnulla: rifiuta il trasferimento.",
        parent=parent,
    )
    if overwrite is True:
        return destination
    if overwrite is False:
        return _unique_download_path(destination)
    return None


def _unique_download_path(destination: Path) -> Path:
    stem, suffix = destination.stem, destination.suffix
    index = 1
    candidate = destination
    while candidate.exists():
        candidate = destination.with_name(f"{stem} ({index}){suffix}")
        index += 1
    return candidate


class BannerWindow:
    def __init__(self, root: tk.Tk, on_stop: Callable[[], None]) -> None:
        self._on_stop = on_stop
        self._win: tk.Toplevel | None = None
        self._root = root

    def show(self) -> None:
        if self._win is not None:
            try:
                self._win.deiconify()
                self._win.lift()
                return
            except tk.TclError:
                self._win = None

        win = tk.Toplevel(self._root)
        self._win = win
        win.title("RemoteDesk")
        win.configure(bg=INK)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._stop)

        frame = ttk.Frame(win, style="Banner.TFrame", padding=12)
        frame.pack()

        ttk.Label(
            frame,
            text="RemoteDesk sta condividendo questo schermo",
            style="Banner.TLabel",
        ).pack(pady=(0, 8))
        ttk.Button(frame, text="Interrompi", style="BannerStop.TButton", command=self._stop).pack()

        win.update_idletasks()
        sw = win.winfo_screenwidth()
        w = win.winfo_width()
        win.geometry(f"+{max(0, sw - w - 24)}+24")

    def hide(self) -> None:
        if self._win is not None:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None

    def _stop(self) -> None:
        self.hide()
        self._on_stop()


class ChatExtrasPanel:
    """Chat, appunti e file (condiviso host tab e client tab)."""

    def __init__(
        self,
        parent: ttk.Frame,
        root: tk.Tk,
        send_chat: Callable[[str], None],
        send_clip: Callable[[], None],
        send_file: Callable[[], None],
    ) -> None:
        self._root = root
        self._send_chat = send_chat
        self._send_clip = send_clip
        self._send_file = send_file

        self._chat_log = scrolledtext.ScrolledText(parent, height=6, state=tk.DISABLED, wrap=tk.WORD)
        self._chat_log.configure(
            background=PAPER,
            foreground=INK,
            insertbackground=INK,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Segoe UI", 11),
            padx=10,
            pady=8,
        )
        self._chat_log.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=(8, 0))
        self._chat_var = tk.StringVar()
        self._chat_entry = ttk.Entry(row, textvariable=self._chat_var)
        self._chat_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self._chat_send = ttk.Button(row, text="Invia", command=self._on_send_chat)
        self._chat_send.pack(side=tk.LEFT)

        btn_row = ttk.Frame(parent)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        self._clip_btn = ttk.Button(btn_row, text="Invia appunti", command=self._send_clip)
        self._clip_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._file_btn = ttk.Button(btn_row, text="Invia file", command=self._send_file)
        self._file_btn.pack(side=tk.LEFT)

    def set_enabled(self, chat: bool, clipboard: bool, files: bool) -> None:
        chat_state = tk.NORMAL if chat else tk.DISABLED
        self._chat_entry.config(state=chat_state)
        self._chat_send.config(state=chat_state)
        clip_state = tk.NORMAL if clipboard else tk.DISABLED
        self._clip_btn.config(state=clip_state)
        file_state = tk.NORMAL if files else tk.DISABLED
        self._file_btn.config(state=file_state)

    def append_chat(self, text: str, prefix: str = "") -> None:
        line = f"{prefix}{text}\n" if prefix else f"{text}\n"

        def ui() -> None:
            self._chat_log.config(state=tk.NORMAL)
            self._chat_log.insert(tk.END, line)
            self._chat_log.see(tk.END)
            self._chat_log.config(state=tk.DISABLED)

        self._root.after(0, ui)

    def _on_send_chat(self) -> None:
        text = self._chat_var.get().strip()
        if not text:
            return
        self._chat_var.set("")
        self._send_chat(text)
        self.append_chat(text, "Tu: ")


class ConnectPanel:
    """Pannello sessione remota (video, toolbar, chat)."""

    _QUALITY_LABELS = (("Fluido", "fluid"), ("Bilanciato", "balanced"), ("Nitido", "sharp"))

    def __init__(
        self,
        parent: ttk.Frame,
        root: tk.Tk,
        get_relay: Callable[[], str],
        get_desk_id: Callable[[], str],
        get_trusted: Callable[[], dict[str, str]],
        key_path: Path,
        confirm_fingerprint: Callable[[str, str], bool],
        on_trust: Callable[[str, str], None],
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
        touch_recent: Callable[[str, str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        get_download_dir: Callable[[], str] | None = None,
        get_record_base_dir: Callable[[], str] | None = None,
        confirm_incoming_files: Callable[[], bool] | None = None,
        show_remote_cursor: bool = True,
        default_quality: str = "balanced",
        view_mode: str = "fit",
        start_fullscreen: bool = False,
        auto_record_outgoing: bool = False,
        record_interval_ms: int = 500,
        get_screenshot_dir: Callable[[], str] | None = None,
        ask_download_path: Callable[[], bool] | None = None,
        download_conflict: Callable[[], str] | None = None,
        connection_timeout: Callable[[], int] | None = None,
    ) -> None:
        self._root = root
        self._get_relay = get_relay
        self._get_desk_id = get_desk_id
        self._get_trusted = get_trusted
        self._key_path = key_path
        self._confirm_fingerprint = confirm_fingerprint
        self._on_trust = on_trust
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._touch_recent = touch_recent
        self._on_status = on_status
        self._get_download_dir = get_download_dir or (lambda: "")
        self._get_record_base_dir = get_record_base_dir or (lambda: "")
        self._confirm_incoming_files = confirm_incoming_files or (lambda: True)
        self._show_remote_cursor = show_remote_cursor
        self._view_mode = view_mode
        self._start_fullscreen = start_fullscreen
        self._auto_record_outgoing = auto_record_outgoing
        self._record_interval_ms = record_interval_ms
        self._get_screenshot_dir = get_screenshot_dir or (lambda: "")
        self._ask_download_path = ask_download_path or (lambda: False)
        self._download_conflict = download_conflict or (lambda: "ask")
        self._connection_timeout = connection_timeout or (lambda: 60)
        self._connect_token = ""
        self._remote_id = ""
        self._connected = False
        self._connected_at = 0.0
        self._duration_after: str | None = None
        self._fullscreen_win: tk.Toplevel | None = None
        self._fullscreen_canvas: tk.Canvas | None = None
        self._fullscreen_image_id: int | None = None
        self._draw_mode = False
        self._draw_last: tuple[float, float] | None = None
        self._block_input_remote = False
        self._last_mouse_canvas: tuple[float, float] | None = None
        self._pointer_id: int | None = None
        self._rec_label_id: int | None = None
        self._recording = False
        self._rec_dir: Path | None = None
        self._rec_last_ts = 0.0
        self._rec_index = 0
        self._audio_receiving = False
        self._tunnel_local: socket.socket | None = None
        self._tunnel_listen: socket.socket | None = None
        self._tunnel_thread: threading.Thread | None = None
        self._tunnel: ClientTunnel | None = None
        self._reader_stop = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._poll_after_id: str | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._fullscreen_photo: ImageTk.PhotoImage | None = None
        self._last_frame_data: bytes | None = None
        self._natural_w = 1
        self._natural_h = 1
        self._file_receiver: FileReceiver | None = None

        shell = ttk.Frame(parent)
        shell.pack(fill=tk.BOTH, expand=True)
        shell.rowconfigure(3, weight=1)
        shell.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(shell, padding=(8, 6, 8, 2))
        toolbar.grid(row=0, column=0, sticky="ew")

        self._btn_disconnect = ttk.Button(
            toolbar, text="Disconnetti", style="Danger.TButton", command=self._disconnect, state=tk.DISABLED
        )
        self._btn_disconnect.pack(side=tk.LEFT, padx=(0, 6))
        self._btn_fullscreen = ttk.Button(
            toolbar, text="Schermo intero", style="Quiet.TButton", command=self._enter_fullscreen, state=tk.DISABLED
        )
        self._btn_fullscreen.pack(side=tk.LEFT, padx=(0, 10))
        self._btn_screenshot = ttk.Button(
            toolbar, text="Schermata", style="Quiet.TButton", command=self._save_screenshot,
            state=tk.DISABLED,
        )
        self._btn_screenshot.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(toolbar, text="Qualità").pack(side=tk.LEFT)
        self._quality_var = tk.StringVar(value="Bilanciato")
        quality_combo = ttk.Combobox(
            toolbar,
            textvariable=self._quality_var,
            values=[label for label, _ in self._QUALITY_LABELS],
            state="readonly",
            width=11,
        )
        quality_combo.pack(side=tk.LEFT, padx=(4, 10))
        quality_combo.bind("<<ComboboxSelected>>", lambda _e: self._send_view_prefs())

        ttk.Label(toolbar, text="Monitor").pack(side=tk.LEFT)
        self._monitor_var = tk.StringVar(value="Tutti")
        self._monitor_combo = ttk.Combobox(toolbar, textvariable=self._monitor_var, state="readonly", width=14)
        self._monitor_combo.pack(side=tk.LEFT, padx=(4, 10))
        self._monitor_combo.bind("<<ComboboxSelected>>", lambda _e: self._send_view_prefs())
        self._refresh_monitor_choices()

        actions = ttk.Frame(shell, padding=(8, 0, 8, 4))
        actions.grid(row=1, column=0, sticky="ew")
        self._action_buttons: dict[str, ttk.Button] = {}
        action_specs = (
            ("audio", "Audio", self._toggle_audio),
            ("restart", "Riavvia", lambda: self._send_action({"op": "restart"})),
            ("privacy", "Privacy", self._toggle_privacy),
            ("cad", "Ctrl+Alt+Canc", lambda: self._send_action({"op": "cad"})),
            ("block_input", "Blocca input", self._toggle_block_input),
            ("lock", "Blocca PC", lambda: self._send_action({"op": "lock"})),
            ("disconnect_user", "Esci utente", lambda: self._send_action({"op": "disconnect_user"})),
            ("sysinfo", "Sistema", self._request_sysinfo),
            ("files", "File", self._open_file_manager),
            ("draw", "Disegna", self._toggle_draw),
            ("tcp", "Tunnel", self._open_tunnel_dialog),
            ("record", "Registra", self._toggle_record),
        )
        for index, (key, label, cmd) in enumerate(action_specs):
            btn = ttk.Button(actions, text=label, style="Quiet.TButton", command=cmd, state=tk.DISABLED)
            btn.grid(row=index // 6, column=index % 6, sticky="ew", padx=2, pady=2)
            self._action_buttons[key] = btn
        for col in range(6):
            actions.columnconfigure(col, weight=1)

        self._session_status_var = tk.StringVar(value="")
        ttk.Label(shell, textvariable=self._session_status_var, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", padx=10, pady=(0, 4)
        )

        screen = ttk.Frame(shell, style="Stage.TFrame")
        screen.grid(row=3, column=0, sticky="nsew")
        screen.rowconfigure(0, weight=1)
        screen.columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(screen, background=PAPER, highlightthickness=0)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._canvas_image_id: int | None = None
        self._paint_screen_wait()

        self._canvas.bind("<Motion>", self._on_mouse_move)
        self._canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self._canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self._canvas.bind("<ButtonPress-3>", self._on_mouse_down)
        self._canvas.bind("<ButtonRelease-3>", self._on_mouse_up)
        self._canvas.bind("<ButtonPress-2>", self._on_mouse_down)
        self._canvas.bind("<ButtonRelease-2>", self._on_mouse_up)
        self._canvas.bind("<KeyPress>", self._on_key)
        self._canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self._canvas.bind("<Enter>", lambda _e: self._canvas.focus_set())

        extras = ttk.Frame(shell, padding=(8, 0, 8, 8))
        extras.grid(row=4, column=0, sticky="ew")
        self._extras = ChatExtrasPanel(
            extras,
            root,
            send_chat=self._send_chat,
            send_clip=self._send_clip,
            send_file=self._pick_send_file,
        )
        self._status_var = tk.StringVar(value="")
        self.apply_config_quality(default_quality)

    def apply_config_quality(self, key: str) -> None:
        for label, qkey in self._QUALITY_LABELS:
            if qkey == key:
                self._quality_var.set(label)
                break
        self._send_view_prefs()

    def set_show_remote_cursor(self, on: bool) -> None:
        self._show_remote_cursor = on
        self._update_pointer_overlay()

    def _paint_screen_wait(self) -> None:
        self._canvas.delete("placeholder")
        self._canvas.create_text(
            28,
            32,
            anchor="nw",
            text="Lo schermo arriva dopo Accetta.",
            fill=MUTED,
            font=("Segoe UI", 13),
            tags="placeholder",
        )

    def _quality_key(self) -> str:
        label = self._quality_var.get()
        for ui_label, key in self._QUALITY_LABELS:
            if ui_label == label:
                return key
        return "balanced"

    def _monitor_index(self) -> int:
        label = self._monitor_var.get()
        if label == "Tutti":
            return -1
        if label.startswith("Schermo "):
            try:
                return int(label.split()[1]) - 1
            except (IndexError, ValueError):
                return -1
        return -1

    def _refresh_monitor_choices(self) -> None:
        monitors = list_monitors()
        labels = ["Tutti"] + [f"Schermo {i + 1}" for i in range(len(monitors))]
        self._monitor_combo.configure(values=labels)
        if self._monitor_var.get() not in labels:
            self._monitor_var.set("Tutti")

    def _send_view_prefs(self) -> None:
        if self._tunnel and self._connected:
            self._tunnel.send_view(self._quality_key(), self._monitor_index())

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)
        if self._on_status:
            self._on_status(msg)

    def start_connect(self, remote_id: str, password: str = "", token: str = "") -> None:
        if self._connected:
            return
        if len(remote_id) != 9 or not remote_id.isdigit():
            self._set_status("L'ID deve essere di 9 cifre numeriche.")
            return
        relay = self._get_relay().strip()
        if not relay:
            self._set_status("Imposta il server relay (pulsante Server…).")
            return

        self._remote_id = remote_id
        self._set_status("Connessione in corso (in attesa consenso host)…")

        def worker() -> None:
            from_id = self._get_desk_id()
            use_token = token or self._connect_token
            self._connect_token = ""
            sock, err = client_connect(
                relay,
                remote_id,
                from_id=from_id,
                token=use_token,
                password=password,
                trusted=self._get_trusted(),
                confirm_fingerprint=self._confirm_fingerprint,
                on_trust=self._on_trust,
                key_path=self._key_path,
                timeout=float(self._connection_timeout()),
            )
            self._root.after(0, lambda: self._connect_result(sock, err, remote_id))

        threading.Thread(target=worker, daemon=True, name="connect-relay").start()

    def _connect_result(self, tunnel_obj: ClientTunnel | None, err: str, remote_id: str) -> None:
        if tunnel_obj is None:
            self._set_status(err)
            if self._on_disconnected:
                self._on_disconnected()
            return
        self._tunnel = tunnel_obj
        self._connected = True
        self._connected_at = time.monotonic()
        self._btn_disconnect.config(state=tk.NORMAL)
        self._btn_fullscreen.config(state=tk.NORMAL)
        self._btn_screenshot.config(state=tk.NORMAL)
        if self._touch_recent:
            self._touch_recent(remote_id, "")
        grants = tunnel_obj.grants
        if grants.control:
            self._set_status("Connesso.")
        else:
            self._set_status("Solo visualizzazione: il mouse e la tastiera sono disattivati.")
        self._extras.set_enabled(grants.chat, grants.clipboard, grants.files)
        self._apply_grant_buttons(grants)
        self._refresh_monitor_choices()
        self._send_view_prefs()
        self._update_session_status()
        if self._on_connected:
            self._on_connected()
        if self._start_fullscreen:
            self._root.after_idle(self._enter_fullscreen)
        if self._auto_record_outgoing and grants.record:
            self._root.after_idle(self._toggle_record)
        self._reader_stop.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True, name="client-tunnel")
        self._reader_thread.start()

    def _reader_loop(self) -> None:
        assert self._tunnel is not None
        while not self._reader_stop.is_set():
            msg = self._tunnel.read_message()
            if msg is None:
                self._root.after(0, self._disconnect)
                return
            msg_type, payload = msg
            if msg_type == tunnel.FRAME:
                self._root.after(0, lambda p=payload: self._apply_frame(p))
            elif msg_type == tunnel.CHAT:
                try:
                    text = payload.decode("utf-8")
                    self._root.after(0, lambda t=text: self._extras.append_chat(t, "Remoto: "))
                except UnicodeDecodeError:
                    pass
            elif msg_type == tunnel.CLIP:
                try:
                    text = payload.decode("utf-8")
                    self._root.after(0, lambda t=text: self._apply_clip(t))
                except UnicodeDecodeError:
                    pass
            elif msg_type == tunnel.FILE_OFFER:
                try:
                    name, size = tunnel.decode_file_offer(payload)
                    self._root.after(0, lambda n=name, s=size: self._offer_file(n, s))
                except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                    pass
            elif msg_type == tunnel.FILE_ACCEPT:
                if self._tunnel:
                    self._tunnel.note_file_reply(True)
            elif msg_type == tunnel.FILE_REJECT:
                if self._tunnel:
                    self._tunnel.note_file_reply(False)
            elif msg_type == tunnel.FILE_CHUNK:
                self._write_file_chunk(payload)
            elif msg_type == tunnel.ACTION:
                try:
                    data = json.loads(payload.decode("utf-8"))
                    detail = str(data.get("detail", ""))
                    ok = bool(data.get("ok", False))
                    op = str(data.get("op", ""))
                    if op == "block_input" and ok:
                        self._block_input_remote = bool(data.get("on", True))
                    if op == "audio" and not ok:
                        self._audio_receiving = False
                    msg = f"{op}: {'ok' if ok else 'errore'}"
                    if detail:
                        msg += f" — {detail}"
                    self._root.after(0, lambda m=msg: self._set_status(m))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            elif msg_type == tunnel.SYSINFO:
                try:
                    text = payload.decode("utf-8")
                    self._root.after(0, lambda t=text: self._show_sysinfo(t))
                except UnicodeDecodeError:
                    pass
            elif msg_type == tunnel.AUDIO:
                self._audio_receiving = True
            elif msg_type == tunnel.FILE_LIST:
                try:
                    data = json.loads(payload.decode("utf-8"))
                    self._root.after(0, lambda d=data: self._show_file_list(d))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            elif msg_type == tunnel.TUNNEL_OPEN:
                try:
                    data = json.loads(payload.decode("utf-8"))
                    if data.get("ok"):
                        self._root.after(0, lambda: self._set_status("Tunnel TCP connesso alla destinazione"))
                    else:
                        detail = str(data.get("detail") or "rifiutato")
                        self._root.after(0, lambda d=detail: self._fail_local_tunnel(d))
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
                    pass
            elif msg_type == tunnel.TUNNEL_DATA:
                self._forward_tunnel_data(payload)
            elif msg_type == tunnel.TUNNEL_CLOSE:
                self._close_local_tunnel()
            elif msg_type == tunnel.GRANTS:
                grants = SessionGrants.from_payload(payload)
                if self._tunnel:
                    self._tunnel.grants = grants
                self._root.after(0, lambda g=grants: self._apply_remote_grants(g))

    def _apply_remote_grants(self, grants: SessionGrants) -> None:
        self._extras.set_enabled(grants.chat, grants.clipboard, grants.files)
        self._apply_grant_buttons(grants)

    def connect_to_id(self, remote_id: str, token: str = "", password: str = "") -> None:
        self._connect_token = token
        self.start_connect(remote_id, password=password, token=token)

    def _apply_grant_buttons(self, grants: SessionGrants) -> None:
        for key, btn in self._action_buttons.items():
            allowed = getattr(grants, key, False)
            btn.config(state=tk.NORMAL if allowed else tk.DISABLED)

    def _send_action(self, payload: dict) -> None:
        if self._tunnel:
            self._tunnel.send_action(payload)

    def _toggle_audio(self) -> None:
        if not self._tunnel:
            return
        self._audio_receiving = not self._audio_receiving
        self._tunnel.send_action({"op": "audio", "on": self._audio_receiving})
        self._set_status("Audio in ricezione" if self._audio_receiving else "Audio disattivato")

    def _toggle_privacy(self) -> None:
        if not self._tunnel:
            return
        on = not getattr(self, "_privacy_on", False)
        self._privacy_on = on
        self._tunnel.send_action({"op": "privacy", "on": on})

    def _toggle_block_input(self) -> None:
        if not self._tunnel:
            return
        on = not self._block_input_remote
        self._tunnel.send_action({"op": "block_input", "on": on})

    def _toggle_draw(self) -> None:
        self._draw_mode = not self._draw_mode
        self._draw_last = None
        self._set_status("Modalità disegno attiva" if self._draw_mode else "Modalità disegno disattivata")

    def _request_sysinfo(self) -> None:
        if self._tunnel:
            self._tunnel.send_sysinfo_request()

    def _show_sysinfo(self, text: str) -> None:
        win = tk.Toplevel(self._root)
        win.title("Informazioni di sistema")
        st = scrolledtext.ScrolledText(win, width=60, height=16)
        st.pack(padx=8, pady=8)
        try:
            pretty = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pretty = text
        st.insert(tk.END, pretty)
        st.config(state=tk.DISABLED)

    def _open_file_manager(self) -> None:
        if self._tunnel:
            self._tunnel.send_file_list("")

    def _show_file_list(self, data: dict) -> None:
        if not data.get("ok"):
            self._set_status("Elenco file non disponibile.")
            return
        win = tk.Toplevel(self._root)
        win.title("File remoti")
        path = str(data.get("path", ""))
        ttk.Label(win, text=path, wraplength=400).pack(anchor=tk.W, padx=8, pady=4)
        lb = tk.Listbox(win, width=60, height=14)
        lb.pack(padx=8, pady=4)
        entries = data.get("entries") or []
        for ent in entries:
            name = ent.get("name", "")
            suffix = "/" if ent.get("dir") else ""
            lb.insert(tk.END, f"{name}{suffix}")

        def on_open(_evt=None) -> None:
            sel = lb.curselection()
            if not sel or not self._tunnel:
                return
            name = entries[sel[0]].get("name", "")
            if entries[sel[0]].get("dir"):
                new_path = str(Path(path) / name)
                self._tunnel.send_file_list(new_path)
                win.destroy()
                return
            file_path = str(Path(path) / name)
            self._tunnel.send_file_list(file_path)
            win.destroy()

        lb.bind("<Double-Button-1>", on_open)

    def _open_tunnel_dialog(self) -> None:
        if not self._tunnel:
            return
        win = tk.Toplevel(self._root)
        win.title("Tunnel TCP")
        frame = ttk.Frame(win, padding=12)
        frame.pack()
        host_var = tk.StringVar(value="127.0.0.1")
        port_var = tk.StringVar(value="8080")
        ttk.Label(frame, text="Host destinazione:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(frame, textvariable=host_var, width=24).grid(row=0, column=1)
        ttk.Label(frame, text="Porta:").grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(frame, textvariable=port_var, width=8).grid(row=1, column=1, sticky=tk.W)

        def open_tunnel() -> None:
            try:
                port = int(port_var.get())
            except ValueError:
                return
            if port < 1 or port > 65535:
                return
            self._begin_local_tunnel(host_var.get().strip(), port)
            win.destroy()

        ttk.Button(frame, text="Apri tunnel", command=open_tunnel).grid(row=2, column=0, columnspan=2, pady=8)

    def _begin_local_tunnel(self, target_host: str, target_port: int) -> None:
        self._close_local_tunnel()
        if not self._tunnel:
            return
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
        except OSError as e:
            self._set_status(f"Tunnel locale: {e}")
            return
        listen_port = srv.getsockname()[1]
        self._tunnel_listen = srv
        self._tunnel.send_tunnel_open(target_host, target_port)
        self._set_status(
            f"In ascolto su 127.0.0.1:{listen_port} verso {target_host}:{target_port}"
        )

        def accept_loop() -> None:
            try:
                srv.settimeout(60.0)
                local, _ = srv.accept()
            except OSError:
                srv.close()
                return
            srv.close()
            self._tunnel_local = local

            def pump() -> None:
                assert self._tunnel is not None
                try:
                    while self._connected:
                        data = local.recv(16384)
                        if not data:
                            break
                        self._tunnel.send_tunnel_data(1, data)
                except OSError:
                    pass
                self._close_local_tunnel()

            self._tunnel_thread = threading.Thread(target=pump, daemon=True, name="client-tunnel-local")
            self._tunnel_thread.start()

        threading.Thread(target=accept_loop, daemon=True, name="client-tunnel-accept").start()

    def _forward_tunnel_data(self, payload: bytes) -> None:
        if len(payload) < 4 or self._tunnel_local is None:
            return
        data = payload[4:]
        try:
            self._tunnel_local.sendall(data)
        except OSError:
            self._close_local_tunnel()

    def _fail_local_tunnel(self, detail: str) -> None:
        self._close_local_tunnel()
        self._set_status(f"Tunnel TCP: {detail}")

    def _close_local_tunnel(self) -> None:
        if self._tunnel_listen is not None:
            try:
                self._tunnel_listen.close()
            except OSError:
                pass
            self._tunnel_listen = None
        if self._tunnel_local is not None:
            try:
                self._tunnel_local.close()
            except OSError:
                pass
            self._tunnel_local = None
        if self._tunnel:
            self._tunnel.send_tunnel_close()

    def _toggle_record(self) -> None:
        if not self._tunnel or not self._tunnel.grants.record:
            return
        self._recording = not self._recording
        if self._recording:
            ts = time.strftime("%Y%m%d-%H%M%S")
            self._rec_dir = recordings_base_dir(self._get_record_base_dir()) / ts
            self._rec_dir.mkdir(parents=True, exist_ok=True)
            self._rec_index = 0
            self._rec_last_ts = 0.0
            if self._rec_label_id is None:
                self._rec_label_id = self._canvas.create_text(
                    8, 8, text="REC", fill="red", anchor=tk.NW, font=("", 12, "bold")
                )
            else:
                self._canvas.itemconfig(self._rec_label_id, state=tk.NORMAL)
        else:
            if self._rec_label_id is not None:
                self._canvas.delete(self._rec_label_id)
                self._rec_label_id = None
            self._rec_dir = None
        self._tunnel.send_rec(self._recording)

    def apply_display_settings(
        self,
        *,
        quality: str,
        show_cursor: bool,
        view_mode: str,
        start_fullscreen: bool,
        auto_record: bool,
        record_interval_ms: int,
    ) -> None:
        self.apply_config_quality(quality)
        self.set_show_remote_cursor(show_cursor)
        self._view_mode = view_mode
        self._start_fullscreen = start_fullscreen
        self._auto_record_outgoing = auto_record
        self._record_interval_ms = record_interval_ms

    def _save_screenshot(self) -> None:
        if not self._connected or self._last_frame_data is None:
            self._set_status("Nessuna schermata disponibile da salvare.")
            return
        try:
            directory = screenshots_base_dir(self._get_screenshot_dir())
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = directory / f"RemoteDesk-{stamp}.jpg"
            suffix = 1
            while path.exists():
                path = directory / f"RemoteDesk-{stamp}-{suffix}.jpg"
                suffix += 1
            path.write_bytes(self._last_frame_data)
        except OSError as exc:
            self._set_status(f"Impossibile salvare la schermata: {exc}")
            return
        self._set_status(f"Schermata salvata in {path}")

    def _update_pointer_overlay(self) -> None:
        if not self._show_remote_cursor or not self._tunnel or not self._tunnel.grants.pointer:
            return
        if not self._block_input_remote or self._last_mouse_canvas is None:
            if self._pointer_id is not None:
                self._canvas.delete(self._pointer_id)
                self._pointer_id = None
            return
        x, y = self._last_mouse_canvas
        r = 12
        if self._pointer_id is None:
            self._pointer_id = self._canvas.create_oval(
                x - r, y - r, x + r, y + r, outline="red", width=2
            )
        else:
            self._canvas.coords(self._pointer_id, x - r, y - r, x + r, y + r)

    def _apply_clip(self, text: str) -> None:
        self._root.clipboard_clear()
        self._root.clipboard_append(text)
        self._set_status("Testo remoto copiato negli appunti.")

    def _offer_file(self, name: str, size: int) -> None:
        if self._tunnel is None:
            return
        safe = safe_download_basename(name)
        if safe is None or size <= 0 or size > tunnel.MAX_FILE_SIZE:
            self._tunnel.send_file_reject()
            return

        if not self._tunnel.grants.files:
            self._tunnel.send_file_reject()
            return
        if self._confirm_incoming_files():
            ok = messagebox.askyesno("RemoteDesk", f"Accetti il file {safe} ({size} byte)?")
            if not ok:
                self._tunnel.send_file_reject()
                return
        dest = choose_download_destination(
            self._root,
            downloads_dir(self._get_download_dir()),
            safe,
            ask_path=self._ask_download_path(),
            conflict_policy=self._download_conflict(),
        )
        if dest is None:
            self._tunnel.send_file_reject()
            return
        try:
            if self._file_receiver is not None:
                self._file_receiver.abort()
            self._file_receiver = FileReceiver(dest, size)
        except (OSError, ValueError) as exc:
            self._tunnel.send_file_reject()
            self._set_status(f"Impossibile creare il file: {exc}")
            return
        if self._tunnel:
            self._tunnel.send_file_accept()

    def _write_file_chunk(self, chunk: bytes) -> None:
        receiver = self._file_receiver
        if receiver is None:
            return
        try:
            complete = receiver.write(chunk)
        except (OSError, ValueError):
            receiver.abort()
            self._file_receiver = None
            self._root.after(0, lambda: self._set_status("Trasferimento file interrotto."))
            return
        if complete:
            self._file_receiver = None
            path = receiver.path
            self._root.after(0, lambda: self._set_status(f"File salvato in {path}"))

    def _update_session_status(self) -> None:
        if not self._connected:
            self._session_status_var.set("")
            if self._duration_after is not None:
                try:
                    self._root.after_cancel(self._duration_after)
                except tk.TclError:
                    pass
                self._duration_after = None
            return
        elapsed = int(time.monotonic() - self._connected_at)
        mm, ss = divmod(elapsed, 60)
        rid = group_desk_id(self._remote_id) if self._remote_id else ""
        self._session_status_var.set(f"ID remoto {rid}  ·  {mm:02d}:{ss:02d}")
        self._duration_after = self._root.after(1000, self._update_session_status)

    def _exit_fullscreen(self) -> None:
        if self._fullscreen_win is not None:
            try:
                self._fullscreen_win.destroy()
            except tk.TclError:
                pass
        self._fullscreen_win = None
        self._fullscreen_canvas = None
        self._fullscreen_image_id = None

    def _enter_fullscreen(self) -> None:
        if not self._connected or self._fullscreen_win is not None:
            return
        win = tk.Toplevel(self._root)
        self._fullscreen_win = win
        win.attributes("-fullscreen", True)
        win.configure(bg=INK)
        win.bind("<Escape>", lambda _e: self._exit_fullscreen())
        top = ttk.Frame(win, padding=8)
        top.pack(fill=tk.X)
        ttk.Button(top, text="Esci", command=self._exit_fullscreen).pack(side=tk.RIGHT)
        canvas = tk.Canvas(win, background=SKY, highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        self._fullscreen_canvas = canvas
        canvas.bind("<Motion>", self._on_mouse_move)
        canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        canvas.bind("<ButtonPress-3>", self._on_mouse_down)
        canvas.bind("<ButtonRelease-3>", self._on_mouse_up)
        canvas.bind("<ButtonPress-2>", self._on_mouse_down)
        canvas.bind("<ButtonRelease-2>", self._on_mouse_up)
        canvas.bind("<KeyPress>", self._on_key)
        canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        canvas.focus_set()
        if self._photo is not None:
            cw = max(1, canvas.winfo_width())
            ch = max(1, canvas.winfo_height())
            self._fullscreen_image_id = canvas.create_image(cw // 2, ch // 2, image=self._photo, anchor=tk.CENTER)

    def _on_mouse_wheel(self, event: tk.Event) -> None:
        if not self._connected:
            return
        delta = int(getattr(event, "delta", 0))
        if delta == 0:
            return
        x, y = self._image_coords(event)
        self._send_input({"kind": "wheel", "x": x, "y": y, "delta": delta})

    def _disconnect(self) -> None:
        self._exit_fullscreen()
        self._connected = False
        self._reader_stop.set()
        if self._tunnel:
            if self._audio_receiving:
                self._tunnel.send_action({"op": "audio", "on": False})
            self._tunnel.send_action({"op": "privacy", "on": False})
            self._tunnel.send_action({"op": "block_input", "on": False})
            self._close_local_tunnel()
            if self._recording:
                self._toggle_record()
            self._tunnel.close()
            self._tunnel = None
        self._draw_mode = False
        self._block_input_remote = False
        self._audio_receiving = False
        if self._file_receiver is not None:
            self._file_receiver.abort()
            self._file_receiver = None
        self._btn_disconnect.config(state=tk.DISABLED)
        self._btn_fullscreen.config(state=tk.DISABLED)
        self._btn_screenshot.config(state=tk.DISABLED)
        self._update_session_status()
        self._canvas.delete("all")
        self._canvas_image_id = None
        self._photo = None
        self._fullscreen_photo = None
        self._last_frame_data = None
        self._paint_screen_wait()
        self._extras.set_enabled(True, True, True)
        for btn in self._action_buttons.values():
            btn.config(state=tk.DISABLED)
        if self._pointer_id is not None:
            self._canvas.delete(self._pointer_id)
            self._pointer_id = None
        self._set_status("Disconnesso.")
        if self._on_disconnected:
            self._on_disconnected()

    def _apply_frame(self, data: bytes) -> None:
        if not self._connected:
            return
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            self._natural_w, self._natural_h = img.size
            self._last_frame_data = data
            if self._tunnel:
                self._tunnel.set_frame_size(self._natural_w, self._natural_h)
            self._photo = self._render_frame_on_canvas(img, self._canvas, self._canvas_image_id)
            self._canvas_image_id = self._canvas.find_withtag("remote-frame")[0]
            self._canvas.delete("placeholder")
            if self._fullscreen_canvas is not None:
                self._fullscreen_photo = self._render_frame_on_canvas(
                    img, self._fullscreen_canvas, self._fullscreen_image_id
                )
                self._fullscreen_image_id = self._fullscreen_canvas.find_withtag("remote-frame")[0]
            if self._recording and self._rec_dir is not None:
                now = time.monotonic()
                if now - self._rec_last_ts >= self._record_interval_ms / 1000.0:
                    self._rec_last_ts = now
                    out = self._rec_dir / f"frame_{self._rec_index:05d}.jpg"
                    self._rec_index += 1
                    try:
                        out.write_bytes(data)
                    except OSError:
                        pass
            self._update_pointer_overlay()
        except OSError:
            pass

    def _render_frame_on_canvas(
        self,
        frame: Image.Image,
        canvas: tk.Canvas,
        image_id: int | None,
    ) -> ImageTk.PhotoImage:
        cw = max(1, canvas.winfo_width())
        ch = max(1, canvas.winfo_height())
        rendered = frame.copy()
        if self._view_mode == "stretch":
            rendered = rendered.resize((cw, ch), Image.Resampling.BILINEAR)
        else:
            rendered.thumbnail((cw, ch), Image.Resampling.BILINEAR)
        photo = ImageTk.PhotoImage(rendered)
        if image_id is None or not canvas.type(image_id):
            canvas.delete("remote-frame")
            image_id = canvas.create_image(
                cw // 2, ch // 2, image=photo, anchor=tk.CENTER, tags=("remote-frame",)
            )
        else:
            canvas.itemconfig(image_id, image=photo)
            canvas.coords(image_id, cw // 2, ch // 2)
        return photo

    def _image_coords(self, event: tk.Event) -> tuple[float, float]:
        canvas = event.widget if isinstance(event.widget, tk.Canvas) else self._canvas
        image_id = self._fullscreen_image_id if canvas is self._fullscreen_canvas else self._canvas_image_id
        if image_id is None or self._photo is None:
            return 0.0, 0.0
        bbox = canvas.bbox(image_id)
        if not bbox:
            return 0.0, 0.0
        x0, y0, x1, y1 = bbox
        disp_w = max(1, x1 - x0)
        disp_h = max(1, y1 - y0)
        x = (event.x - x0) * (self._natural_w / disp_w)
        y = (event.y - y0) * (self._natural_h / disp_h)
        x = max(0.0, min(float(self._natural_w), x))
        y = max(0.0, min(float(self._natural_h), y))
        return x, y

    def _send_input(self, payload: dict) -> None:
        if not self._connected or not self._tunnel:
            return
        if not self._tunnel.grants.control:
            return
        self._tunnel.send_input(payload)

    def _on_mouse_move(self, event: tk.Event) -> None:
        if not self._connected:
            return
        self._last_mouse_canvas = (float(event.x), float(event.y))
        x, y = self._image_coords(event)
        if self._draw_mode and self._draw_last is not None and self._tunnel:
            self._tunnel.send_draw(self._draw_last[0], self._draw_last[1], x, y)
            self._draw_last = (x, y)
        self._send_input({"kind": "move", "x": x, "y": y})
        self._update_pointer_overlay()

    def _on_mouse_down(self, event: tk.Event) -> None:
        if not self._connected:
            return
        x, y = self._image_coords(event)
        if self._draw_mode and event.num == 1:
            self._draw_last = (x, y)
            return
        button = "right" if event.num == 3 else ("middle" if event.num == 2 else "left")
        self._send_input({"kind": "down", "x": x, "y": y, "button": button})

    def _on_mouse_up(self, event: tk.Event) -> None:
        if not self._connected:
            return
        button = "right" if event.num == 3 else ("middle" if event.num == 2 else "left")
        self._send_input({"kind": "up", "x": 0, "y": 0, "button": button})

    def _on_key(self, event: tk.Event) -> None:
        if not self._connected:
            return
        if event.char and len(event.char) == 1 and event.char.isprintable():
            key = event.char
        else:
            key = event.keysym
        self._send_input({"kind": "key", "key": key})

    def _send_chat(self, text: str) -> None:
        if self._tunnel:
            self._tunnel.send_chat(text)

    def _send_clip(self) -> None:
        if not self._tunnel:
            return
        try:
            text = self._root.clipboard_get()
        except tk.TclError:
            self._set_status("Nessun testo negli appunti.")
            return
        if isinstance(text, str) and text.strip():
            self._tunnel.send_clip(text)

    def _pick_send_file(self) -> None:
        if not self._tunnel:
            return
        path = filedialog.askopenfilename(title="Seleziona file da inviare")
        if not path:
            return
        p = Path(path)
        name = p.name
        size = p.stat().st_size
        if size > tunnel.MAX_FILE_SIZE:
            self._set_status("File troppo grande.")
            return

        def worker() -> None:
            tunnel_obj = self._tunnel
            if not tunnel_obj:
                return
            tunnel_obj.send_file_offer(name, size)
            if not tunnel_obj.wait_file_reply(120.0):
                self._root.after(0, lambda: self._set_status("Invio file rifiutato o scaduto."))
                return
            try:
                with open(p, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        tunnel_obj.send_file_chunk(chunk)
            except OSError:
                self._root.after(0, lambda: self._set_status("Errore lettura file."))

        threading.Thread(target=worker, daemon=True, name="client-send-file").start()


class HostApp:
    def __init__(
        self,
        state: ShareState,
        on_sharing_changed: Callable[[bool], None],
    ) -> None:
        self._state = state
        self._on_sharing_changed = on_sharing_changed
        self._config = AppConfig.load()
        self._state.set_quality(self._config.default_quality)
        key_path, cert_path = ensure_host_identity(self._config.desk_id)
        self._key_path = key_path
        self._cert_path = cert_path
        self._fingerprint = cert_fingerprint_from_pem(cert_path)
        self._spki_b64 = public_spki_b64(cert_path)
        self.root = tk.Tk()
        self.root.title("RemoteDesk")
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = min(1200, max(700, int(screen_width * 0.92)))
        window_height = min(820, max(520, int(screen_height * 0.86)))
        window_x = max(0, (screen_width - window_width) // 2)
        window_y = max(0, (screen_height - window_height) // 2)
        self.root.minsize(700, 520)
        self.root.geometry(f"{window_width}x{window_height}+{window_x}+{window_y}")
        apply_theme(self.root)

        self._banner = BannerWindow(self.root, self._stop_sharing)
        self._consent_dialog: tk.Toplevel | None = None
        self._consent_for: PendingSession | None = None
        self._relay_consent = False
        self._relay_request_generation: int | None = None
        self._file_reply = threading.Event()
        self._file_accepted = False
        self._file_send_lock = threading.Lock()
        self._file_receiver = None
        self._pending_invites: list[PendingInvite] = []
        self._password_failures: dict[str, list[float]] = {}
        self._consent_vars: dict[str, tk.BooleanVar] = {}
        self._connect_panel: ConnectPanel | None = None
        self._settings = SettingsWindow(self)

        self._host_relay = HostRelayThread(
            desk_id=self._config.desk_id,
            identity_provisioned=self._config.identity_provisioned,
            relay=self._config.relay,
            state=self._state,
            on_status=lambda s: self.root.after(0, lambda: self._relay_status_var.set(s)),
            on_hello=lambda p, g: self.root.after(0, lambda: self._on_relay_hello(p, g)),
            on_invite=lambda i, t: self.root.after(0, lambda: self._on_invite(i, t)),
            on_identity=lambda i: self.root.after(0, lambda: self._apply_identity_id(i)),
            on_tunnel=self._host_tunnel_message,
            on_session_end=lambda g: self.root.after(0, lambda: self._host_session_end(g)),
            on_chat=lambda t: self.root.after(0, lambda: self._host_extras.append_chat(t, "Remoto: ")),
            on_clip=lambda t: self.root.after(0, lambda: self._apply_host_clip(t)),
            on_file_offer=lambda n, s, g: self.root.after(0, lambda: self._host_file_offer(n, s, g)),
            on_file_reply=lambda ok, g: self.root.after(0, lambda: self.file_reply(ok, g)),
            get_relay=lambda: self._config.relay,
            host_secret=self._config.host_secret,
            key_path=self._key_path,
            cert_path=self._cert_path,
            spki_b64=self._spki_b64,
        )
        self._session_actions = SessionActionHost(
            root=self.root,
            schedule_ui=lambda fn, ms: self.root.after(ms, fn),
            on_status=lambda s: self.root.after(0, lambda: self._status_var.set(s)),
            send_message=lambda t, p: self._host_relay._send(t, p),
            get_mapping=lambda: self._state.get_mapping(),
            send_file_offer=lambda n, s: self._host_relay.send_file_offer(n, s),
            confirm_tcp=self._confirm_tcp_tunnel,
        )
        self._frame_sender = FrameSenderThread(
            self._state,
            send_frame=lambda jpeg: self._host_relay.send_frame(jpeg),
            is_sharing=lambda: self._state.sharing,
        )
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_session_requests()
        self._host_relay.start()
        self._frame_sender.start()

    def _host_tunnel_message(self, msg_type: int, payload: bytes) -> None:
        if msg_type == tunnel.ACTION:
            generation = self._host_relay.current_generation()
            self.root.after(0, lambda: self._handle_host_ui_message(generation, msg_type, payload))
        elif msg_type == tunnel.DRAW:
            generation = self._host_relay.current_generation()
            self.root.after(0, lambda: self._handle_host_ui_message(generation, msg_type, payload))
        elif msg_type == tunnel.SYSINFO:
            generation = self._host_relay.current_generation()
            self._session_actions.handle_sysinfo_request(
                session_valid=lambda: self._host_relay.is_active_generation(generation),
                send_scoped=lambda t, p: self._host_relay._send(t, p, expected_generation=generation),
            )
        elif msg_type == tunnel.FILE_LIST:
            generation = self._host_relay.current_generation()
            self._session_actions.handle_file_list(
                payload,
                generation=generation,
                session_valid=lambda: self._host_relay.is_active_generation(generation),
                send_scoped=lambda t, p: self._host_relay._send(t, p, expected_generation=generation),
                offer_scoped=lambda n, s: self._host_relay.send_file_offer(n, s, generation),
            )
        elif msg_type == tunnel.REC:
            self._session_actions.handle_rec(payload)
        elif msg_type == tunnel.TUNNEL_OPEN:
            generation = self._host_relay.current_generation()
            self._session_actions.handle_tunnel_open(
                payload,
                session_valid=lambda: self._host_relay.is_active_generation(generation),
                send_scoped=lambda t, p: self._host_relay._send(t, p, expected_generation=generation),
            )
        elif msg_type == tunnel.TUNNEL_DATA:
            self._session_actions.handle_tunnel_data(payload)
        elif msg_type == tunnel.TUNNEL_CLOSE:
            self._session_actions.handle_tunnel_close(payload)

    def _handle_host_ui_message(self, generation: int, msg_type: int, payload: bytes) -> None:
        if not self._host_relay.is_active_generation(generation):
            return
        if msg_type == tunnel.ACTION:
            self._session_actions.handle_action(payload)
        elif msg_type == tunnel.DRAW:
            self._session_actions.handle_draw(payload)

    def _host_session_end(self, generation: int) -> None:
        if generation != self._host_relay.current_generation():
            return
        self._relay_request_generation = None
        self._close_relay_consent()
        self._relay_consent = False
        self._session_actions.end_session()
        self._state.set_sharing(False)
        self._host_relay.set_sharing(False)
        self._sharing_idle_ui()
        self._banner.hide()
        self._on_sharing_changed(False)

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, style="Plate.TFrame", padding=(18, 12, 18, 14))
        top.pack(fill=tk.X, padx=16, pady=(14, 12))
        brand_row = ttk.Frame(top, style="Card.TFrame")
        brand_row.pack(fill=tk.X)
        ttk.Label(brand_row, text="RemoteDesk", style="Brand.TLabel").pack(side=tk.LEFT)
        ttk.Label(brand_row, text="Accesso remoto", style="CardMuted.TLabel").pack(
            side=tk.LEFT, padx=(10, 0), pady=(4, 0)
        )
        ttk.Button(brand_row, text="Impostazioni", style="Quiet.TButton", command=self._open_settings).pack(
            side=tk.RIGHT
        )

        ttk.Label(top, text="Connetti a una scrivania", style="CardSection.TLabel").pack(
            anchor=tk.W, pady=(12, 6)
        )
        connect_row = ttk.Frame(top, style="Card.TFrame")
        connect_row.pack(fill=tk.X)
        connect_row.columnconfigure(1, weight=1)
        connect_row.columnconfigure(5, weight=1)
        ttk.Label(connect_row, text="ID o alias", style="FieldLabel.TLabel").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 8)
        )
        self._connect_target_var = tk.StringVar()
        connect_entry = ttk.Entry(connect_row, textvariable=self._connect_target_var, width=20)
        connect_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 14))
        ttk.Label(connect_row, text="Password", style="FieldLabel.TLabel").grid(
            row=0, column=2, sticky=tk.W, padx=(0, 8)
        )
        self._connect_pwd_var = tk.StringVar()
        ttk.Entry(connect_row, textvariable=self._connect_pwd_var, show="*", width=14).grid(
            row=1, column=2, sticky="ew", padx=(0, 14)
        )
        self._btn_top_connect = ttk.Button(
            connect_row, text="Connetti", style="Primary.TButton", command=self._on_top_connect
        )
        self._btn_top_connect.grid(row=1, column=3, sticky=tk.W)
        self._connect_status_var = tk.StringVar(value="")
        ttk.Label(connect_row, textvariable=self._connect_status_var, style="Muted.TLabel", wraplength=200).grid(
            row=1, column=5, sticky="ew", padx=(14, 0)
        )

        body = ttk.Frame(self.root, padding=(16, 0, 16, 14))
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=0, minsize=182)
        body.columnconfigure(1, weight=5, minsize=250)
        body.columnconfigure(2, weight=4, minsize=200)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="Plate.TFrame", padding=(14, 14, 14, 14))
        left.grid(row=0, column=0, sticky="nsew")

        ttk.Label(left, text="Rubrica", style="CardSection.TLabel").pack(anchor=tk.W)
        ttk.Label(left, text="Dispositivi salvati", style="CardMuted.TLabel").pack(anchor=tk.W, pady=(2, 6))
        rubrica_frame = ttk.Frame(left)
        rubrica_frame.pack(fill=tk.X, pady=(4, 6))
        self._contacts_list = tk.Listbox(
            rubrica_frame,
            height=8,
            activestyle="none",
            highlightthickness=0,
            borderwidth=0,
            background=FIELD,
            foreground=INK,
            selectbackground=ACTION,
            selectforeground=PAPER,
            font=("Segoe UI", 10),
            selectborderwidth=0,
        )
        scroll_c = ttk.Scrollbar(rubrica_frame, orient=tk.VERTICAL, command=self._contacts_list.yview)
        self._contacts_list.configure(yscrollcommand=scroll_c.set)
        self._contacts_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_c.pack(side=tk.RIGHT, fill=tk.Y)
        rubrica_btns = ttk.Frame(left)
        rubrica_btns.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(rubrica_btns, text="Aggiungi", style="Quiet.TButton", command=self._add_contact_dialog).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(rubrica_btns, text="Rimuovi", style="Quiet.TButton", command=self._remove_contact).pack(side=tk.LEFT)
        self._contacts_list.bind("<Double-Button-1>", lambda _e: self._connect_from_contacts_list(self._contacts_list))

        ttk.Label(left, text="Recenti", style="FieldLabel.TLabel").pack(anchor=tk.W, pady=(4, 0))
        self._recent_list = tk.Listbox(
            left,
            height=5,
            activestyle="none",
            highlightthickness=0,
            borderwidth=0,
            background=FIELD,
            foreground=INK,
            selectbackground=ACTION,
            selectforeground=PAPER,
            font=("Segoe UI", 10),
            selectborderwidth=0,
        )
        self._recent_list.pack(fill=tk.X, pady=(4, 0))
        self._recent_list.bind("<Double-Button-1>", lambda _e: self._connect_from_contacts_list(self._recent_list))
        self._refresh_contact_lists()

        self._local_card = ttk.Frame(body, style="Plate.TFrame", padding=(18, 22, 18, 18))
        self._local_card.grid(row=0, column=1, sticky="nsew", padx=8)

        alias = self._config.alias.strip()
        self._alias_var = tk.StringVar(value=self._config.alias)
        self._alias_heading = ttk.Label(
            self._local_card,
            text=alias or "Questa scrivania",
            style="CardTitle.TLabel",
        )
        self._alias_heading.pack(anchor=tk.W)

        displayed_id = (
            group_desk_id(self._config.desk_id)
            if self._config.identity_provisioned
            else "In assegnazione"
        )
        self._desk_id_var = tk.StringVar(value=displayed_id)
        self._desk_id_font = tkfont.Font(root=self.root, family="Segoe UI", size=34, weight="bold")
        self._desk_id_label = ttk.Label(
            self._local_card,
            textvariable=self._desk_id_var,
            style="Id.TLabel",
            font=self._desk_id_font,
            anchor=tk.W,
        )
        self._desk_id_label.pack(fill=tk.X, pady=(10, 0))
        self._desk_id_label.bind("<Configure>", self._fit_desk_id_font)

        self._status_var = tk.StringVar(value="In attesa di una connessione")
        self._status_label = ttk.Label(
            self._local_card,
            textvariable=self._status_var,
            style="Notice.TLabel",
            wraplength=240,
        )
        self._status_label.pack(fill=tk.X, pady=(14, 0))
        self._hint_label = ttk.Label(
            self._local_card,
            text="Nove cifre. Lo schermo esce solo dopo Accetta.",
            style="CardMuted.TLabel",
            wraplength=240,
        )
        self._hint_label.pack(fill=tk.X, pady=(6, 0))
        self._local_card.bind("<Configure>", self._fit_local_text)

        local_actions = ttk.Frame(self._local_card, style="Stage.TFrame")
        local_actions.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))
        local_actions.columnconfigure(0, weight=1, uniform="session-actions")
        local_actions.columnconfigure(1, weight=1, uniform="session-actions")
        ttk.Button(local_actions, text="Invita", style="Primary.TButton", command=self._open_invite_dialog).grid(
            row=0, column=0, sticky="ew"
        )
        self._btn_stop = ttk.Button(
            local_actions,
            text="Interrompi",
            style="Danger.TButton",
            command=self._stop_sharing,
            state=tk.DISABLED,
        )
        self._btn_stop.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        desk_tools = ttk.Frame(self._local_card, style="Stage.TFrame")
        desk_tools.pack(side=tk.BOTTOM, fill=tk.X)
        desk_tools.columnconfigure(0, weight=1, uniform="desk-tools")
        desk_tools.columnconfigure(1, weight=1, uniform="desk-tools")
        ttk.Button(desk_tools, text="Copia ID", style="Quiet.TButton", command=self._copy_desk_id).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(desk_tools, text="Whitelist", style="Quiet.TButton", command=self._open_whitelist_dialog).grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )
        self._btn_permissions = ttk.Button(
            desk_tools, text="Permessi", style="Quiet.TButton", command=self._open_permissions_dialog, state=tk.DISABLED
        )
        self._btn_permissions.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self._work_column = ttk.Frame(body, style="Plate.TFrame", padding=(18, 18, 18, 16))
        self._work_column.grid(row=0, column=2, sticky="nsew")
        details = self._work_column

        ttk.Label(details, text="Configurazione", style="CardSection.TLabel").pack(anchor=tk.W)
        ttk.Label(details, text="Personalizza il tuo accesso", style="CardMuted.TLabel").pack(
            anchor=tk.W, pady=(2, 14)
        )

        ttk.Label(details, text="Alias", style="FieldLabel.TLabel").pack(anchor=tk.W)
        alias_entry = ttk.Entry(details, textvariable=self._alias_var)
        alias_entry.pack(fill=tk.X, pady=(4, 12))

        def save_alias(_evt=None) -> None:
            name = self._alias_var.get().strip()
            self._config.alias = name
            self._config.save()
            self._alias_heading.configure(text=name or "Questa scrivania")

        alias_entry.bind("<FocusOut>", save_alias)

        ttk.Label(details, text="Password di accesso", style="FieldLabel.TLabel").pack(anchor=tk.W)
        self._access_pwd_var = tk.StringVar()
        pwd_entry = ttk.Entry(details, textvariable=self._access_pwd_var, show="*")
        pwd_entry.pack(fill=tk.X, pady=(4, 2))
        ttk.Label(details, text="Minimo 12 caratteri. Vuota: password invariata.", style="Muted.TLabel").pack(
            anchor=tk.W, pady=(0, 12)
        )

        def save_access_pwd(_evt=None) -> None:
            raw = self._access_pwd_var.get()
            if not valid_new_access_password(raw):
                messagebox.showerror("RemoteDesk", "Usa una password di almeno 12 caratteri (massimo 128 byte).")
                return
            self._config.access_password_hash = hash_access_password(raw)
            self._config.save()
            self._access_pwd_var.set("")
            self._status_var.set("Password di accesso aggiornata")

        ttk.Button(details, text="Imposta password", style="Quiet.TButton", command=save_access_pwd).pack(
            anchor=tk.W, pady=(0, 12)
        )

        relay_row = ttk.Frame(details)
        relay_row.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(relay_row, text="Relay", style="FieldLabel.TLabel").pack(side=tk.LEFT)
        self._relay_status_var = tk.StringVar(
            value="Server non impostato" if not self._config.relay else "…"
        )
        ttk.Label(relay_row, textvariable=self._relay_status_var).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(relay_row, text="Server", style="Quiet.TButton", command=self._open_server_dialog).pack(
            side=tk.RIGHT
        )

        ttk.Button(
            details, text="Cancella disegno", style="Quiet.TButton", command=self._session_actions.draw_clear_ui
        ).pack(anchor=tk.W, pady=(0, 0))

        host_extras_frame = ttk.Frame(details)
        host_extras_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self._host_extras = ChatExtrasPanel(
            host_extras_frame,
            self.root,
            send_chat=lambda t: self._host_relay.send_chat(t),
            send_clip=self._host_send_clip,
            send_file=self._host_pick_file,
        )

        self._remote_panel = ttk.Frame(body)
        self._remote_panel.grid(row=0, column=1, columnspan=2, sticky="nsew")
        self._remote_panel.grid_remove()

        self._connect_panel = ConnectPanel(
            self._remote_panel,
            self.root,
            get_relay=lambda: self._config.relay,
            get_desk_id=lambda: self._config.desk_id,
            get_trusted=lambda: dict(self._config.trusted),
            key_path=self._key_path,
            confirm_fingerprint=self._confirm_remote_fingerprint,
            on_trust=self._save_trusted_fingerprint,
            on_connected=self._show_remote_session,
            on_disconnected=self._show_local_card,
            touch_recent=self._touch_recent_and_refresh,
            on_status=lambda s: self._connect_status_var.set(s),
            get_download_dir=lambda: self._config.download_dir,
            get_record_base_dir=lambda: self._config.record_dir,
            confirm_incoming_files=lambda: self._config.confirm_incoming_files,
            show_remote_cursor=self._config.show_remote_cursor,
            default_quality=self._config.default_quality,
            view_mode=self._config.view_mode,
            start_fullscreen=self._config.start_fullscreen,
            auto_record_outgoing=self._config.auto_record_outgoing,
            record_interval_ms=self._config.record_interval_ms,
            get_screenshot_dir=lambda: self._config.screenshot_dir,
            ask_download_path=lambda: self._config.ask_download_path,
            download_conflict=lambda: self._config.download_conflict,
            connection_timeout=lambda: self._config.connection_timeout_seconds,
        )

    def _fit_desk_id_font(self, _event: tk.Event | None = None) -> None:
        available = max(120, self._desk_id_label.winfo_width() - 8)
        text = self._desk_id_var.get()
        for size in range(34, 19, -1):
            self._desk_id_font.configure(size=size)
            if self._desk_id_font.measure(text) <= available:
                break

    def _fit_local_text(self, _event: tk.Event | None = None) -> None:
        wraplength = max(130, self._local_card.winfo_width() - 76)
        for label in (self._status_label, self._hint_label):
            if int(label.cget("wraplength")) != wraplength:
                label.configure(wraplength=wraplength)

    def _open_settings(self) -> None:
        self._settings.open()

    def _apply_identity_id(self, desk_id: str) -> None:
        if len(desk_id) != 9 or not desk_id.isdigit():
            return
        self._config.desk_id = desk_id
        self._config.identity_provisioned = True
        self._config.save()
        self._fingerprint = cert_fingerprint_from_pem(self._cert_path)
        self._desk_id_var.set(group_desk_id(desk_id))
        self._fit_desk_id_font()

    def _default_session_grants(self) -> SessionGrants:
        base = SessionGrants(audio=self._config.transmit_audio)
        valid = {name: value for name, value in self._config.default_grants.items() if hasattr(base, name)}
        valid.update({name: False for name in UNAVAILABLE_GRANTS})
        return replace(base, **valid)

    def _maybe_show_banner(self) -> None:
        if self._config.show_banner:
            self._banner.show()

    def _on_top_connect(self) -> None:
        if not self._connect_panel:
            return
        target = self._connect_target_var.get()
        remote_id = self._config.resolve_invite_target(target)
        if remote_id is None:
            self._connect_status_var.set("ID non valido (9 cifre o alias in rubrica).")
            return
        self._btn_top_connect.config(state=tk.DISABLED)
        self._connect_panel.start_connect(remote_id, password=self._connect_pwd_var.get())

    def _show_remote_session(self) -> None:
        self._local_card.grid_remove()
        self._work_column.grid_remove()
        self._remote_panel.grid()
        self._btn_top_connect.config(state=tk.DISABLED)

    def _show_local_card(self) -> None:
        self._remote_panel.grid_remove()
        self._local_card.grid()
        self._work_column.grid()
        self._btn_top_connect.config(state=tk.NORMAL)

    def _refresh_contact_lists(self) -> None:
        self._contacts_list.delete(0, tk.END)
        for c in self._config.contacts:
            self._contacts_list.insert(tk.END, f"{c.alias}  ·  {group_desk_id(c.desk_id)}")
        self._recent_list.delete(0, tk.END)
        for c in self._config.recent:
            label = c.alias if c.alias != c.desk_id else group_desk_id(c.desk_id)
            self._recent_list.insert(tk.END, f"{label}  ·  {group_desk_id(c.desk_id)}")

    def _touch_recent_and_refresh(self, desk_id: str, alias: str) -> None:
        self._config.touch_recent(desk_id, alias)
        self._refresh_contact_lists()

    def _connect_from_contacts_list(self, listbox: tk.Listbox) -> None:
        sel = listbox.curselection()
        if not sel:
            return
        text = listbox.get(sel[0])
        if "  ·  " not in text:
            return
        grouped = text.rsplit("  ·  ", 1)[-1].strip()
        remote_id = grouped.replace(" ", "")
        if len(remote_id) != 9 or not remote_id.isdigit():
            return
        self._connect_target_var.set(remote_id)
        self._on_top_connect()

    def _add_contact_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Aggiungi contatto")
        win.transient(self.root)
        frame = ttk.Frame(win, padding=12)
        frame.pack()
        alias_var = tk.StringVar()
        id_var = tk.StringVar()
        ttk.Label(frame, text="Alias").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(frame, textvariable=alias_var, width=28).grid(row=0, column=1, pady=4)
        ttk.Label(frame, text="ID (9 cifre)").grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(frame, textvariable=id_var, width=28).grid(row=1, column=1, pady=4)

        def save() -> None:
            desk_id = id_var.get().strip().replace(" ", "")
            alias = alias_var.get().strip()
            if len(desk_id) != 9 or not desk_id.isdigit() or not alias:
                messagebox.showerror("RemoteDesk", "Alias e ID a 9 cifre obbligatori.")
                return
            self._config.add_contact(alias, desk_id)
            self._refresh_contact_lists()
            win.destroy()

        ttk.Button(frame, text="Salva", command=save).grid(row=2, column=0, columnspan=2, pady=(8, 0))

    def _remove_contact(self) -> None:
        sel = self._contacts_list.curselection()
        if not sel:
            return
        text = self._contacts_list.get(sel[0])
        grouped = text.rsplit("  ·  ", 1)[-1].strip().replace(" ", "")
        if len(grouped) == 9:
            self._config.remove_contact(grouped)
            self._refresh_contact_lists()

    def _copy_desk_id(self) -> None:
        if not self._config.identity_provisioned:
            messagebox.showinfo("RemoteDesk", "L'ID sarà disponibile quando il relay assegnerà l'identità.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._config.desk_id)
        self._status_var.set("ID copiato negli appunti.")

    def _open_whitelist_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Whitelist")
        win.transient(self.root)
        frame = ttk.Frame(win, padding=12)
        frame.pack()
        ttk.Label(frame, text="Solo questi ID possono connettersi (lista vuota = tutti).").pack(anchor=tk.W)
        lb = tk.Listbox(frame, width=36, height=10)
        lb.pack(pady=8)
        for kid in self._config.whitelist:
            lb.insert(tk.END, group_desk_id(kid))
        id_var = tk.StringVar()

        def reload() -> None:
            lb.delete(0, tk.END)
            for kid in self._config.whitelist:
                lb.insert(tk.END, group_desk_id(kid))

        def add_id() -> None:
            kid = id_var.get().strip().replace(" ", "")
            if len(kid) != 9 or not kid.isdigit():
                messagebox.showerror("RemoteDesk", "ID a 9 cifre non valido.")
                return
            if kid not in self._config.whitelist:
                self._config.whitelist.append(kid)
                self._config.save()
            id_var.set("")
            reload()

        def remove_id() -> None:
            sel = lb.curselection()
            if not sel:
                return
            kid = self._config.whitelist[sel[0]]
            self._config.whitelist = [x for x in self._config.whitelist if x != kid]
            self._config.save()
            reload()

        row = ttk.Frame(frame)
        row.pack(fill=tk.X)
        ttk.Entry(row, textvariable=id_var, width=20).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row, text="Aggiungi", command=add_id).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row, text="Rimuovi", command=remove_id).pack(side=tk.LEFT)

    def _open_permissions_dialog(self) -> None:
        with self._state.lock:
            current = self._state.grants
        win = tk.Toplevel(self.root)
        win.title("Permessi sessione")
        win.transient(self.root)
        frame = ttk.Frame(win, padding=12)
        frame.pack()
        perm_vars: dict[str, tk.BooleanVar] = {}
        for name, label in GRANT_UI_LABELS:
            perm_vars[name] = tk.BooleanVar(value=False if name in UNAVAILABLE_GRANTS else getattr(current, name))
            checkbox = ttk.Checkbutton(frame, text=label, variable=perm_vars[name])
            checkbox.pack(anchor=tk.W)
            if name in UNAVAILABLE_GRANTS:
                checkbox.state(["disabled"])

        def save() -> None:
            grants = grants_from_vars({k: v.get() for k, v in perm_vars.items()})
            self._state.set_grants(grants)
            self._host_relay.set_grants(grants)
            self._session_actions.set_grants(grants)
            self._host_relay.send_grants(grants.to_payload(__version__))
            win.destroy()

        ttk.Button(frame, text="Salva", command=save).pack(pady=(10, 0))

    def _open_invite_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Invita")
        win.transient(self.root)
        frame = ttk.Frame(win, padding=12)
        frame.pack()
        ttk.Label(frame, text="Manda un invito alla sessione al seguente ID").pack(anchor=tk.W)
        target_var = tk.StringVar()
        ttk.Entry(frame, textvariable=target_var, width=20).pack(pady=6, anchor=tk.W)
        profile_var = tk.StringVar(value="Predefinito")
        ttk.Combobox(
            frame,
            textvariable=profile_var,
            values=("Predefinito", "Solo visualizzazione"),
            state="readonly",
            width=22,
        ).pack(anchor=tk.W)
        extra_frame = ttk.Frame(frame)
        invite_vars: dict[str, tk.BooleanVar] = {}
        default_grants = self._default_session_grants()
        for name, label in GRANT_UI_LABELS:
            invite_vars[name] = tk.BooleanVar(value=getattr(default_grants, name))
            checkbox = ttk.Checkbutton(extra_frame, text=label, variable=invite_vars[name])
            checkbox.pack(anchor=tk.W)
            if name in UNAVAILABLE_GRANTS:
                checkbox.state(["disabled"])
        extra_frame.pack_forget()

        def apply_profile(*_args: object) -> None:
            g = (
                SessionGrants.view_only()
                if profile_var.get() == "Solo visualizzazione"
                else self._default_session_grants()
            )
            apply_grants_to_vars(g, invite_vars)

        profile_var.trace_add("write", apply_profile)

        def toggle_extra() -> None:
            if extra_frame.winfo_ismapped():
                extra_frame.pack_forget()
            else:
                extra_frame.pack(fill=tk.BOTH, expand=True, pady=8)

        ttk.Button(frame, text="Altro", command=toggle_extra).pack(anchor=tk.W, pady=4)
        invite_mins = self._config.invite_minutes
        invite_unit = "minuto" if invite_mins == 1 else "minuti"
        ttk.Label(
            frame,
            text=f"La durata dell'invito è impostata su {invite_mins} {invite_unit}.",
        ).pack(anchor=tk.W, pady=4)

        def do_invite() -> None:
            target = self._config.resolve_invite_target(target_var.get())
            if target is None:
                messagebox.showerror("RemoteDesk", "ID non valido (9 cifre o alias salvato).")
                return
            grants = grants_from_vars({k: v.get() for k, v in invite_vars.items()})
            token = new_invite_token()
            relay = self._config.relay.strip()
            if not relay:
                messagebox.showerror("RemoteDesk", "Imposta il server relay.")
                return

            def worker() -> None:
                try:
                    ls = connect_relay(relay)
                except (OSError, ValueError):
                    self.root.after(
                        0,
                        lambda: messagebox.showerror("RemoteDesk", "Quel PC non è connesso al relay."),
                    )
                    return
                try:
                    status, spki = relay_handshake_lookup(ls, target)
                    if status != "OK":
                        self.root.after(
                            0,
                            lambda: messagebox.showerror(
                                "RemoteDesk", "Quel PC non è connesso al relay."
                            ),
                        )
                        return
                    blob = seal_token(spki, token.encode("utf-8"))
                    signature = sign_relay_invite(
                        self._key_path, target, self._config.desk_id, blob
                    )
                    ls.close()
                    ls = connect_relay(relay)
                    reply = relay_handshake_invite(
                        ls, target, self._config.desk_id, blob, signature
                    )
                except (OSError, ValueError, RuntimeError) as exc:
                    self.root.after(
                        0,
                        lambda e=str(exc): messagebox.showerror(
                            "RemoteDesk", f"Invito non inviato: {e}"
                        ),
                    )
                    return
                finally:
                    try:
                        ls.close()
                    except OSError:
                        pass
                def show_result() -> None:
                    if reply == "OK":
                        self._pending_invites.append(
                            PendingInvite(
                                remote_id=target,
                                grants=grants,
                                expires=time.monotonic() + self._config.invite_minutes * 60.0,
                                token=token,
                            )
                        )
                    messagebox.showinfo("RemoteDesk", f"Invito: {reply}")

                self.root.after(0, show_result)

            threading.Thread(target=worker, daemon=True, name="send-invite").start()
            win.destroy()

        ttk.Button(frame, text="Invita", command=do_invite).pack(pady=(8, 0))

    def _purge_invites(self) -> None:
        now = time.monotonic()
        self._pending_invites = [i for i in self._pending_invites if i.expires > now]

    def _find_invite(self, remote_id: str) -> PendingInvite | None:
        self._purge_invites()
        for inv in self._pending_invites:
            if inv.remote_id == remote_id:
                return inv
        return None

    def _confirm_remote_fingerprint(self, desk_id: str, fingerprint: str) -> bool:
        result = {"ok": False}
        done = threading.Event()

        def ui() -> None:
            ok = messagebox.askyesno(
                "RemoteDesk",
                f"Accetti l'impronta del certificato per l'ID {desk_id}?\n\n{fingerprint}",
            )
            result["ok"] = ok
            done.set()

        self.root.after(0, ui)
        done.wait(60.0)
        return result["ok"]

    def _save_trusted_fingerprint(self, desk_id: str, fingerprint: str) -> None:
        self._config.trusted[desk_id] = fingerprint
        self._config.save()

    def _confirm_tcp_tunnel(self, host: str, port: int) -> bool:
        result = {"ok": False}
        done = threading.Event()

        def ui() -> None:
            result["ok"] = messagebox.askyesno(
                "RemoteDesk",
                f"Aprire un tunnel verso {host}:{port}?",
            )
            done.set()

        self.root.after(0, ui)
        done.wait(60.0)
        return result["ok"]

    def _on_invite(self, from_id: str, token: str) -> None:
        win = tk.Toplevel(self.root)
        win.title("RemoteDesk")
        win.transient(self.root)
        frame = ttk.Frame(win, padding=12)
        frame.pack()
        mins = self._config.invite_minutes
        mins_label = "minuto" if mins == 1 else "minuti"
        ttk.Label(
            frame,
            text=f"L'ID {from_id} ti invita alla sua sessione ({mins} {mins_label}).",
            wraplength=320,
        ).pack(pady=(0, 12))

        def connect() -> None:
            win.destroy()
            if self._connect_panel:
                self._connect_panel.connect_to_id(from_id, token=token)

        ttk.Button(frame, text="Connetti", command=connect).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(frame, text="Chiudi", command=win.destroy).pack(side=tk.LEFT)

    def _consume_invite(self, remote_id: str, token: str) -> PendingInvite | None:
        self._purge_invites()
        for inv in self._pending_invites:
            if inv.remote_id == remote_id and invite_matches(inv.token, token):
                self._pending_invites.remove(inv)
                return inv
        return None

    def _on_relay_hello(self, payload: bytes, generation: int) -> None:
        if not self._host_relay.is_current_hello(generation):
            return
        # The previous session's queued UI cleanup may arrive after this hello.
        # Clear its grants, tunnels, and pending actions before considering a new peer.
        self._host_session_end(generation)
        self._relay_request_generation = generation
        hello = decode_hello(payload)
        from_id = hello["from"]
        token = hello["token"]
        password = hello["password"]

        invite = self._consume_invite(from_id, token) if from_id and token else None
        if invite is not None:
            self._accept_invite_grants(invite.grants, generation)
            return

        if self._config.access_password_hash:
            now = time.monotonic()
            failures = [t for t in self._password_failures.get(from_id, []) if now - t < 300.0]
            if len(failures) >= 5:
                self._password_failures[from_id] = failures
                self._host_relay.send_reject(generation)
                self._status_var.set("Troppi tentativi: riprova tra qualche minuto")
                return
            password_ok = verify_access_password(password, self._config.access_password_hash)
            if password_ok and remote_allowed(from_id, self._config.whitelist, invite_ok=False):
                self._password_failures.pop(from_id, None)
                self._accept_password_grants(generation)
            else:
                if not password_ok:
                    self._password_failures[from_id] = failures + [now]
                    if len(self._password_failures) > 512:
                        self._password_failures.pop(next(iter(self._password_failures)))
                self._host_relay.send_reject(generation)
                if password_ok:
                    self._status_var.set("ID non autorizzato")
            return

        if not remote_allowed(from_id, self._config.whitelist, invite_ok=False):
            self._host_relay.send_reject(generation)
            self._status_var.set("ID non autorizzato")
            return
        if not self._config.interactive_access:
            self._host_relay.send_reject(generation)
            self._status_var.set("Accesso interattivo disattivato")
            return
        self._show_relay_consent()

    def _accept_password_grants(self, generation: int) -> None:
        grants = self._default_session_grants()
        self._state.set_grants(grants)
        self._host_relay.set_grants(grants)
        self._session_actions.set_grants(grants)
        if not self._host_relay.send_accept(grants.to_payload(__version__), generation):
            return
        self._host_relay.set_sharing(True)
        self._state.set_sharing(True)
        self._sharing_active_ui()
        self._maybe_show_banner()
        self._on_sharing_changed(True)

    def _accept_invite_grants(self, grants: SessionGrants, generation: int) -> None:
        self._state.set_grants(grants)
        self._host_relay.set_grants(grants)
        self._session_actions.set_grants(grants)
        if not self._host_relay.send_accept(grants.to_payload(__version__), generation):
            return
        self._host_relay.set_sharing(True)
        self._state.set_sharing(True)
        self._sharing_active_ui()
        self._maybe_show_banner()
        self._on_sharing_changed(True)

    def _open_server_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Server relay")
        win.transient(self.root)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=12)
        frame.pack()
        ttk.Label(frame, text="Indirizzo host:port del relay:").pack(anchor=tk.W)
        var = tk.StringVar(value=self._config.relay)
        ttk.Entry(frame, textvariable=var, width=32).pack(pady=8)

        def save() -> None:
            value = var.get().strip()
            if value and parse_relay_host_port(value) is None:
                messagebox.showerror("RemoteDesk", "Indirizzo non valido (usa host:port).")
                return
            self._config.relay = value
            self._config.save()
            self._host_relay.reconnect()
            win.destroy()

        ttk.Button(frame, text="Salva", command=save).pack()
        ttk.Button(frame, text="Annulla", command=win.destroy).pack(pady=(4, 0))

    def _apply_host_clip(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._status_var.set("Testo remoto copiato negli appunti.")

    def _host_send_clip(self) -> None:
        sent = False
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            text = ""
        if isinstance(text, str) and text.strip():
            self._host_relay.send_clip(text)
            sent = True
        with self._state.lock:
            grants = self._state.grants
        if grants.clipboard_files:
            sent = self._send_clipboard_files() or sent
        if not sent:
            self._status_var.set("Nessun testo negli appunti.")

    def _send_clipboard_files(self) -> bool:
        import sys

        if sys.platform != "win32":
            return False
        generation = self._host_relay.current_generation()
        if not self._host_relay.is_active_generation(generation):
            return False
        from remotedesk.grants import resolve_under_home

        CF_HDROP = 15
        user32 = __import__("ctypes").windll.user32
        kernel32 = __import__("ctypes").windll.kernel32
        if not user32.OpenClipboard(None):
            return False
        try:
            if not user32.IsClipboardFormatAvailable(CF_HDROP):
                return False
            hdrop = user32.GetClipboardData(CF_HDROP)
            if not hdrop:
                return False
            shell32 = __import__("ctypes").windll.shell32
            count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            home = Path.home()
            sent_any = False
            buf = __import__("ctypes").create_unicode_buffer(260)
            for i in range(count):
                shell32.DragQueryFileW(hdrop, i, buf, 260)
                p = Path(buf.value)
                resolved = resolve_under_home(str(p), home)
                if resolved is None or not resolved.is_file():
                    continue
                try:
                    size = resolved.stat().st_size
                except OSError:
                    continue
                if size > tunnel.MAX_FILE_SIZE:
                    continue
                if not self._host_relay.is_active_generation(generation):
                    break
                self._session_actions.queue_file_send(
                    resolved,
                    generation,
                    lambda n, s: self._host_relay.send_file_offer(n, s, generation),
                )
                sent_any = True
                break
            return sent_any
        finally:
            user32.CloseClipboard()

    def _host_file_offer(self, name: str, size: int, generation: int) -> None:
        if not self._host_relay.is_active_generation(generation):
            return
        safe = safe_download_basename(name)
        if safe is None or size <= 0 or size > tunnel.MAX_FILE_SIZE:
            self._host_relay.send_file_reject(generation)
            return
        if self._config.confirm_incoming_files:
            ok = messagebox.askyesno("RemoteDesk", f"Accetti il file {safe} ({size} byte)?")
            if not self._host_relay.is_active_generation(generation):
                return
            if not ok:
                self._host_relay.send_file_reject(generation)
                return
        dest = choose_download_destination(
            self.root,
            downloads_dir(self._config.download_dir),
            safe,
            ask_path=self._config.ask_download_path,
            conflict_policy=self._config.download_conflict,
        )
        if not self._host_relay.is_active_generation(generation):
            return
        if dest is None:
            self._host_relay.send_file_reject(generation)
            return
        try:
            if not self._host_relay.start_file_receive(safe, size, dest, generation):
                return
        except OSError as exc:
            self._host_relay.send_file_reject(generation)
            self._status_var.set(f"Impossibile creare il file: {exc}")
            return
        self._host_relay.send_file_accept(generation)

    def _host_pick_file(self) -> None:
        generation = self._host_relay.current_generation()
        if not self._host_relay.is_active_generation(generation):
            return
        path = filedialog.askopenfilename(title="Seleziona file da inviare")
        if not self._host_relay.is_active_generation(generation):
            return
        if not path:
            return
        p = Path(path)
        size = p.stat().st_size
        if size > tunnel.MAX_FILE_SIZE:
            self._status_var.set("File troppo grande.")
            return

        def worker() -> None:
            with self._file_send_lock:
                if not self._host_relay.is_active_generation(generation):
                    return
                self._file_accepted = False
                self._file_reply.clear()
                self._host_relay.send_file_offer(p.name, size, generation)
                if not self._file_reply.wait(120.0) or not self._file_accepted:
                    return
                try:
                    with open(p, "rb") as f:
                        while self._host_relay.is_active_generation(generation):
                            chunk = f.read(65536)
                            if not chunk:
                                break
                            self._host_relay.send_file_chunk(chunk, generation)
                except OSError:
                    self.root.after(0, lambda: self._status_var.set("Errore invio file."))

        threading.Thread(target=worker, daemon=True, name="host-send-file").start()

    def file_reply(self, accepted: bool, generation: int) -> None:
        if not self._host_relay.is_active_generation(generation):
            return
        self._file_accepted = accepted
        self._file_reply.set()
        if accepted:
            pending = self._session_actions.take_pending_file_path(generation)
            if pending is not None:

                def worker() -> None:
                    try:
                        with open(pending, "rb") as f:
                            while self._host_relay.is_active_generation(generation):
                                chunk = f.read(65536)
                                if not chunk:
                                    break
                                self._host_relay.send_file_chunk(chunk, generation)
                    except OSError:
                        self.root.after(0, lambda: self._status_var.set("Errore invio file."))

                threading.Thread(target=worker, daemon=True, name="host-send-listed-file").start()

    def _sharing_active_ui(self) -> None:
        self._status_var.set("Condivisione attiva")
        self._btn_stop.config(state=tk.NORMAL)
        self._btn_permissions.config(state=tk.NORMAL)

    def _sharing_idle_ui(self) -> None:
        self._status_var.set("In attesa di una connessione")
        self._btn_stop.config(state=tk.DISABLED)
        self._btn_permissions.config(state=tk.DISABLED)

    def _consent_grants_from_ui(self) -> SessionGrants:
        return grants_from_vars({k: v.get() for k, v in self._consent_vars.items()})

    def _pack_consent_grants(self, frame: ttk.Frame) -> None:
        self._consent_vars = {}
        outer = ttk.Frame(frame)
        outer.pack(pady=(0, 12), fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(outer, height=220, highlightthickness=0, background=FIELD)
        scroll = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        defaults = self._default_session_grants()
        for name, label in GRANT_UI_LABELS:
            self._consent_vars[name] = tk.BooleanVar(value=getattr(defaults, name))
            checkbox = ttk.Checkbutton(inner, text=label, variable=self._consent_vars[name])
            checkbox.pack(anchor=tk.W)
            if name in UNAVAILABLE_GRANTS:
                checkbox.state(["disabled"])

    def _accept_session(self) -> None:
        grants = self._consent_grants_from_ui()
        self._state.set_grants(grants)
        self._host_relay.set_grants(grants)
        self._session_actions.set_grants(grants)
        self._state.resolve_session(True)
        self._close_consent_dialog()
        self._sharing_active_ui()
        self._maybe_show_banner()
        self._host_relay.set_sharing(True)
        self._on_sharing_changed(True)

    def _refuse_session(self) -> None:
        self._state.resolve_session(False)
        self._close_consent_dialog()

    def _accept_relay(self) -> None:
        generation = self._relay_request_generation
        if generation is None or not self._host_relay.is_current_hello(generation):
            self._close_relay_consent()
            self._relay_consent = False
            return
        grants = self._consent_grants_from_ui()
        self._state.set_grants(grants)
        self._host_relay.set_grants(grants)
        self._session_actions.set_grants(grants)
        self._close_relay_consent()
        self._relay_consent = False
        if not self._host_relay.send_accept(grants.to_payload(__version__), generation):
            self._status_var.set("Richiesta scaduta")
            return
        self._host_relay.set_sharing(True)
        self._state.set_sharing(True)
        self._sharing_active_ui()
        self._maybe_show_banner()
        self._on_sharing_changed(True)

    def _refuse_relay(self) -> None:
        generation = self._relay_request_generation
        self._close_relay_consent()
        self._relay_consent = False
        if generation is not None:
            self._host_relay.send_reject(generation)

    def _close_consent_dialog(self) -> None:
        self._consent_for = None
        if self._consent_dialog is not None:
            try:
                self._consent_dialog.grab_release()
                self._consent_dialog.destroy()
            except tk.TclError:
                pass
            self._consent_dialog = None

    def _close_relay_consent(self) -> None:
        if self._consent_dialog is not None and self._relay_consent:
            try:
                self._consent_dialog.grab_release()
                self._consent_dialog.destroy()
            except tk.TclError:
                pass
            self._consent_dialog = None

    def _show_consent_dialog(self, pending: PendingSession) -> None:
        if self._consent_dialog is not None:
            return
        self._consent_for = pending
        self._relay_consent = False
        win = tk.Toplevel(self.root)
        self._consent_dialog = win
        win.title("RemoteDesk")
        win.transient(self.root)
        win.resizable(False, False)
        win.grab_set()

        frame = ttk.Frame(win, padding=16)
        frame.pack()
        ttk.Label(
            frame,
            text="Un altro computer chiede di controllare questo PC. Accetti?",
            wraplength=320,
        ).pack(pady=(0, 12))
        self._pack_consent_grants(frame)
        btns = ttk.Frame(frame)
        btns.pack()
        ttk.Button(btns, text="Accetta", style="Primary.TButton", command=self._accept_session).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(btns, text="Rifiuta", style="Danger.TButton", command=self._refuse_session).pack(side=tk.LEFT)

        win.update_idletasks()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{max(0, rx)}+{max(0, ry)}")

    def _show_relay_consent(self) -> None:
        if self._consent_dialog is not None:
            return
        self._relay_consent = True
        win = tk.Toplevel(self.root)
        self._consent_dialog = win
        win.title("RemoteDesk")
        win.transient(self.root)
        win.resizable(False, False)
        win.grab_set()

        frame = ttk.Frame(win, padding=16)
        frame.pack()
        ttk.Label(
            frame,
            text="Un altro computer chiede di controllare questo PC. Accetti?",
            wraplength=320,
        ).pack(pady=(0, 12))
        self._pack_consent_grants(frame)
        btns = ttk.Frame(frame)
        btns.pack()
        ttk.Button(btns, text="Accetta", style="Primary.TButton", command=self._accept_relay).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(btns, text="Rifiuta", style="Danger.TButton", command=self._refuse_relay).pack(side=tk.LEFT)

        win.update_idletasks()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{max(0, rx)}+{max(0, ry)}")

    def _poll_session_requests(self) -> None:
        pending = self._state.get_pending_session()
        if pending is None:
            if self._consent_dialog is not None and not self._relay_consent:
                self._close_consent_dialog()
        elif self._consent_dialog is None:
            self._show_consent_dialog(pending)
        elif self._consent_for is not pending and not self._relay_consent:
            self._close_consent_dialog()
            self._show_consent_dialog(pending)

        with self._state.lock:
            sharing = self._state.sharing
        if sharing and self._btn_stop.cget("state") == tk.DISABLED:
            self._sharing_active_ui()
            self._maybe_show_banner()
        elif not sharing and self._btn_stop.cget("state") == tk.NORMAL:
            self._sharing_idle_ui()
            self._banner.hide()

        self.root.after(200, self._poll_session_requests)

    def _stop_sharing(self) -> None:
        self._session_actions.end_session()
        self._state.set_sharing(False)
        self._host_relay.end_session()
        self._sharing_idle_ui()
        self._banner.hide()
        self._on_sharing_changed(False)

    def _on_close(self) -> None:
        self._stop_sharing()
        self._host_relay.stop()
        self._frame_sender.stop()
        self.root.quit()

    def run(self) -> None:
        self.root.mainloop()
