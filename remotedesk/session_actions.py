"""Azioni host Win32 (lock, privacy, disegno, tunnel, audio, ecc.)."""

from __future__ import annotations

import ctypes
import getpass
import json
import platform
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from remotedesk.grants import SessionGrants, action_allowed, resolve_under_home
from remotedesk import tunnel

if sys.platform == "win32":
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
else:
    user32 = None  # type: ignore[assignment]
    kernel32 = None  # type: ignore[assignment]

UiCallback = Callable[[Callable[[], None], int], None]


class SessionActionHost:
    """Gestisce azioni richieste dal client remoto sul PC host."""

    def __init__(
        self,
        root: object | None = None,
        schedule_ui: UiCallback | None = None,
        on_status: Callable[[str], None] | None = None,
        send_message: Callable[[int, bytes], None] | None = None,
        get_mapping: Callable[[], tuple[int, int, int, int, int, int]] | None = None,
        send_file_offer: Callable[[str, int], None] | None = None,
        confirm_tcp: Callable[[str, int], bool] | None = None,
    ) -> None:
        import tkinter as tk  # noqa: WPS433 — lazy per test headless

        self._root = root
        self._schedule_ui = schedule_ui or (lambda fn, ms: root.after(ms, fn) if root else None)
        self._on_status = on_status or (lambda _s: None)
        self._send = send_message or (lambda _t, _p: None)
        self._get_mapping = get_mapping or (lambda: (1, 1, 0, 0, 1920, 1080))
        self._send_file_offer = send_file_offer or (lambda _n, _s: None)
        self._confirm_tcp = confirm_tcp
        self._grants = SessionGrants()
        self._block_input_active = False
        self._privacy_windows: list = []
        self._draw_win = None
        self._draw_canvas = None
        self._audio_stop = threading.Event()
        self._audio_thread: threading.Thread | None = None
        self._tunnel_sock: socket.socket | None = None
        self._tunnel_remote: socket.socket | None = None
        self._tunnel_thread: threading.Thread | None = None
        self._tunnel_stop = threading.Event()
        self._recording_remote = False
        self._secure_windows_flag = False
        self._pending_file_path: Path | None = None
        self._pending_file_generation: int | None = None
        self._session_epoch = 0
        self._countdown_window = None

    def set_grants(self, grants: SessionGrants) -> None:
        self._grants = grants
        self._secure_windows_flag = grants.secure_windows

    def queue_file_send(
        self,
        path: Path,
        generation: int | None = None,
        offer_scoped: Callable[[str, int], None] | None = None,
    ) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size > tunnel.MAX_FILE_SIZE:
            return
        self._pending_file_path = path
        self._pending_file_generation = generation
        (offer_scoped or self._send_file_offer)(path.name, size)

    def take_pending_file_path(self, generation: int | None = None) -> Path | None:
        if generation is not None and self._pending_file_generation != generation:
            return None
        path = self._pending_file_path
        self._pending_file_path = None
        self._pending_file_generation = None
        return path

    def end_session(self) -> None:
        self._session_epoch += 1
        if self._countdown_window is not None:
            try:
                self._countdown_window.destroy()
            except Exception:
                pass
            self._countdown_window = None
        self._pending_file_path = None
        self._pending_file_generation = None
        if self._block_input_active:
            self._set_block_input(False)
        self._privacy_off()
        self._draw_clear()
        self._audio_stop.set()
        self._tunnel_close_all()
        self._recording_remote = False

    def handle_action(self, payload: bytes) -> None:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(data, dict):
            return
        op = str(data.get("op", ""))
        if not action_allowed(self._grants, op):
            return
        if op == "lock":
            self._do_lock()
        elif op == "restart":
            self._countdown_action(
                "Il computer remoto ha chiesto il riavvio. Si riavvia tra 10 secondi.",
                self._do_restart,
            )
        elif op == "disconnect_user":
            self._countdown_action(
                "Il remoto ha chiesto di disconnettere l'utente locale. "
                "Operazione tra 10 secondi.",
                self._do_disconnect_user,
            )
        elif op == "block_input":
            on = bool(data.get("on", True))
            self._set_block_input(on)
        elif op == "privacy":
            on = bool(data.get("on", True))
            if on:
                self._privacy_on()
            else:
                self._privacy_off()
        elif op == "cad":
            self._do_cad()
        elif op == "audio":
            on = bool(data.get("on", True))
            if on:
                self._audio_start()
            else:
                self._audio_stop.set()
        elif op == "secure_windows":
            self._secure_windows_flag = bool(data.get("on", True))

    def handle_draw(self, payload: bytes) -> None:
        if not action_allowed(self._grants, "draw"):
            return
        try:
            data = json.loads(payload.decode("utf-8"))
            x1, y1 = float(data["x1"]), float(data["y1"])
            x2, y2 = float(data["x2"]), float(data["y2"])
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError):
            return
        self._draw_line(x1, y1, x2, y2)

    def handle_sysinfo_request(
        self,
        session_valid: Callable[[], bool] | None = None,
        send_scoped: Callable[[int, bytes], None] | None = None,
    ) -> None:
        valid = session_valid or (lambda: True)
        if not valid():
            return
        if not action_allowed(self._grants, "sysinfo"):
            return
        info = collect_sysinfo()
        if valid():
            (send_scoped or self._send)(
                tunnel.SYSINFO, json.dumps(info, separators=(",", ":")).encode("utf-8")
            )

    def handle_file_list(
        self,
        payload: bytes,
        generation: int | None = None,
        session_valid: Callable[[], bool] | None = None,
        send_scoped: Callable[[int, bytes], None] | None = None,
        offer_scoped: Callable[[str, int], None] | None = None,
    ) -> None:
        valid = session_valid or (lambda: True)
        send = send_scoped or self._send
        offer = offer_scoped or self._send_file_offer
        if not valid():
            return
        if not message_allowed_files(self._grants):
            return
        try:
            data = json.loads(payload.decode("utf-8"))
            req_path = str(data.get("path", ""))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._file_list_reply(False, "", [], send)
            return
        home = Path.home()
        resolved = resolve_under_home(req_path, home)
        if resolved is None:
            self._file_list_reply(False, "", [], send)
            return
        if resolved.is_file():
            try:
                size = resolved.stat().st_size
            except OSError:
                self._file_list_reply(False, "", [], send)
                return
            if size > tunnel.MAX_FILE_SIZE:
                self._file_list_reply(False, "", [], send)
                return
            if self._grants.files and valid():
                self._pending_file_path = resolved
                self._pending_file_generation = generation
                offer(resolved.name, size)
            return
        if not resolved.is_dir():
            self._file_list_reply(False, "", [], send)
            return
        entries: list[dict] = []
        try:
            for child in sorted(resolved.iterdir(), key=lambda p: p.name.lower())[:200]:
                if resolve_under_home(str(child), home) is None:
                    continue
                try:
                    is_dir = child.is_dir()
                    size = 0 if is_dir else child.stat().st_size
                except OSError:
                    continue
                entries.append({"name": child.name, "dir": is_dir, "size": size})
        except OSError:
            self._file_list_reply(False, "", [], send)
            return
        if valid():
            self._file_list_reply(True, str(resolved), entries, send)

    def handle_rec(self, payload: bytes) -> None:
        try:
            data = json.loads(payload.decode("utf-8"))
            on = bool(data.get("on", False))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        self._recording_remote = on
        if on:
            self._on_status("Il remoto sta registrando")
        else:
            self._on_status("Condivisione attiva")

    def handle_tunnel_open(
        self,
        payload: bytes,
        session_valid: Callable[[], bool] | None = None,
        send_scoped: Callable[[int, bytes], None] | None = None,
    ) -> None:
        valid = session_valid or (lambda: True)
        send = send_scoped or self._send
        if not valid():
            return
        if not action_allowed(self._grants, "tcp"):
            return
        try:
            data = json.loads(payload.decode("utf-8"))
            host = str(data.get("target_host", ""))
            port = int(data.get("target_port", 0))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._tunnel_open_reply(False, 0, "Payload non valido", send)
            return
        if not (1 <= len(host) <= 253) or " " in host:
            self._tunnel_open_reply(False, 0, "Host non valido", send)
            return
        if port < 1 or port > 65535:
            self._tunnel_open_reply(False, 0, "Porta non valida", send)
            return
        if self._confirm_tcp is not None:
            if not self._confirm_tcp(host, port):
                if valid():
                    self._tunnel_open_reply(False, 0, "Tunnel non confermato.", send)
                return
        if not valid():
            return
        self._tunnel_close_all()
        try:
            remote = socket.create_connection((host, port), timeout=15.0)
        except OSError as e:
            if valid():
                self._tunnel_open_reply(False, 0, str(e), send)
            return
        if not valid():
            remote.close()
            return
        self._tunnel_remote = remote
        self._on_status(f"Tunnel TCP verso {host}:{port}")
        self._tunnel_open_reply(True, 0, "", send)
        self._tunnel_bridge(valid, send)

    def handle_tunnel_data(self, payload: bytes) -> None:
        if len(payload) < 4:
            return
        conn_id = struct.unpack(">I", payload[:4])[0]
        if conn_id != 1:
            return
        data = payload[4:]
        remote = self._tunnel_remote
        if remote is None:
            return
        try:
            remote.sendall(data)
        except OSError:
            self._tunnel_close_all()

    def handle_tunnel_close(self, _payload: bytes) -> None:
        self._tunnel_close_all()

    def _reply_action(self, op: str, ok: bool, detail: str = "") -> None:
        body = json.dumps({"op": op, "ok": ok, "detail": detail}, separators=(",", ":"))
        self._send(tunnel.ACTION, body.encode("utf-8"))

    def _do_lock(self) -> None:
        ok = False
        if user32 is not None:
            ok = bool(user32.LockWorkStation())
        self._reply_action("lock", ok, "" if ok else "LockWorkStation non riuscito")

    def _countdown_action(self, message: str, final_fn: Callable[[], None]) -> None:
        if self._root is None:
            return
        if self._countdown_window is not None:
            return
        import tkinter as tk
        from tkinter import ttk

        win = tk.Toplevel(self._root)
        self._countdown_window = win
        win.title("RemoteDesk")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        var = tk.StringVar(value=message)
        ttk.Label(win, textvariable=var, wraplength=360, padding=12).pack()
        cancelled = {"v": False}
        session_epoch = self._session_epoch

        def tick(remaining: int) -> None:
            if cancelled["v"]:
                return
            if session_epoch != self._session_epoch:
                try:
                    win.destroy()
                except tk.TclError:
                    pass
                if self._countdown_window is win:
                    self._countdown_window = None
                return
            if remaining <= 0:
                try:
                    win.destroy()
                except tk.TclError:
                    pass
                if self._countdown_window is win:
                    self._countdown_window = None
                final_fn()
                return
            var.set(f"{message}\n\n{remaining}…")
            self._schedule_ui(lambda: tick(remaining - 1), 1000)

        def cancel() -> None:
            cancelled["v"] = True
            try:
                win.destroy()
            except tk.TclError:
                pass
            if self._countdown_window is win:
                self._countdown_window = None

        ttk.Button(win, text="Annulla", command=cancel).pack(pady=(0, 12))
        tick(10)

    def _do_restart(self) -> None:
        try:
            subprocess.run(["shutdown", "/r", "/t", "0"], check=False)
            self._reply_action("restart", True)
        except OSError as e:
            self._reply_action("restart", False, str(e))

    def _do_disconnect_user(self) -> None:
        ok = False
        detail = ""
        if sys.platform == "win32":
            try:
                wts = ctypes.windll.wtsapi32
                WTS_CURRENT_SERVER_HANDLE = 0
                WTS_CURRENT_SESSION = -1
                if wts.WTSDisconnectSession(WTS_CURRENT_SERVER_HANDLE, WTS_CURRENT_SESSION, False):
                    ok = True
                else:
                    detail = f"WTSDisconnectSession errore {kernel32.GetLastError()}"
            except OSError as e:
                detail = str(e)
        else:
            detail = "Non supportato"
        self._reply_action("disconnect_user", ok, detail)

    def _set_block_input(self, on: bool) -> None:
        ok = False
        detail = ""
        if user32 is not None:
            res = user32.BlockInput(1 if on else 0)
            ok = bool(res)
            if not ok and on:
                detail = "Windows richiede un processo elevato per bloccare mouse e tastiera."
        self._block_input_active = on and ok
        body = json.dumps(
            {"op": "block_input", "ok": ok, "detail": detail, "on": on},
            separators=(",", ":"),
        )
        self._send(tunnel.ACTION, body.encode("utf-8"))

    def _privacy_on(self) -> None:
        if self._root is None or self._privacy_windows:
            return
        import tkinter as tk

        try:
            sm_x = user32.GetSystemMetrics(76) if user32 else 0
            sm_y = user32.GetSystemMetrics(77) if user32 else 0
            sm_w = user32.GetSystemMetrics(78) if user32 else self._root.winfo_screenwidth()
            sm_h = user32.GetSystemMetrics(79) if user32 else self._root.winfo_screenheight()
        except Exception:
            sm_x, sm_y = 0, 0
            sm_w = self._root.winfo_screenwidth()
            sm_h = self._root.winfo_screenheight()

        win = tk.Toplevel(self._root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="black")
        win.geometry(f"{sm_w}x{sm_h}+{sm_x}+{sm_y}")
        try:
            hwnd = win.winfo_id()
            if user32 is not None:
                root_hwnd = user32.GetAncestor(hwnd, 2)
                user32.SetWindowDisplayAffinity(root_hwnd or hwnd, 0x11)
        except Exception:
            pass
        self._privacy_windows.append(win)

    def _privacy_off(self) -> None:
        for w in self._privacy_windows:
            try:
                w.destroy()
            except Exception:
                pass
        self._privacy_windows.clear()

    def _do_cad(self) -> None:
        self._reply_action("cad", False,
                           "Ctrl+Alt+Canc richiede un servizio Windows dedicato, non incluso in questa versione.")

    def _draw_line(self, x1: float, y1: float, x2: float, y2: float) -> None:
        if self._root is None:
            return
        import tkinter as tk

        fw, fh, sl, st, sw, sh = self._get_mapping()
        if self._draw_win is None:
            win = tk.Toplevel(self._root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.attributes("-transparentcolor", "white")
            win.configure(bg="white")
            win.geometry(f"{sw}x{sh}+{sl}+{st}")
            try:
                hwnd = win.winfo_id()
                GWL_EXSTYLE = -20
                WS_EX_LAYERED = 0x80000
                WS_EX_TRANSPARENT = 0x20
                style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) if user32 else 0
                if user32:
                    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
            except Exception:
                pass
            canvas = tk.Canvas(win, width=sw, height=sh, bg="white", highlightthickness=0)
            canvas.pack()
            self._draw_win = win
            self._draw_canvas = canvas

        assert self._draw_canvas is not None
        sx1 = x1 / max(1, fw) * sw
        sy1 = y1 / max(1, fh) * sh
        sx2 = x2 / max(1, fw) * sw
        sy2 = y2 / max(1, fh) * sh
        self._draw_canvas.create_line(sx1, sy1, sx2, sy2, fill="red", width=3)

    def draw_clear_ui(self) -> None:
        self._draw_clear()

    def _draw_clear(self) -> None:
        if self._draw_win is not None:
            try:
                self._draw_win.destroy()
            except Exception:
                pass
        self._draw_win = None
        self._draw_canvas = None

    def _audio_start(self) -> None:
        self._audio_stop.clear()
        if self._audio_thread and self._audio_thread.is_alive():
            return

        def run() -> None:
            ok = False
            try:
                ok = self._audio_loop()
            finally:
                if not ok:
                    self._reply_action("audio", False, "Uscita audio non disponibile.")

        self._audio_thread = threading.Thread(target=run, daemon=True, name="audio-capture")
        self._audio_thread.start()

    def _audio_loop(self) -> bool:
        """Il loopback WASAPI non è disponibile senza un dispositivo audio dedicato."""
        return False

    def _file_list_reply(
        self,
        ok: bool,
        path: str,
        entries: list,
        send: Callable[[int, bytes], None] | None = None,
    ) -> None:
        body = json.dumps({"ok": ok, "path": path, "entries": entries}, separators=(",", ":"))
        (send or self._send)(tunnel.FILE_LIST, body.encode("utf-8"))

    def _tunnel_open_reply(
        self,
        ok: bool,
        listen_port: int,
        detail: str,
        send: Callable[[int, bytes], None] | None = None,
    ) -> None:
        body = json.dumps(
            {"ok": ok, "listen_port": listen_port, "detail": detail},
            separators=(",", ":"),
        )
        (send or self._send)(tunnel.TUNNEL_OPEN, body.encode("utf-8"))

    def _tunnel_bridge(
        self, session_valid: Callable[[], bool], send: Callable[[int, bytes], None]
    ) -> None:
        remote = self._tunnel_remote
        if remote is None:
            return
        stop = self._tunnel_stop

        def to_client() -> None:
            try:
                while not stop.is_set() and session_valid():
                    data = remote.recv(16384)
                    if not data:
                        break
                    if stop.is_set() or not session_valid():
                        break
                    packet = struct.pack(">I", 1) + data
                    send(tunnel.TUNNEL_DATA, packet)
            except OSError:
                pass
            if self._tunnel_remote is remote:
                self._tunnel_close_all()
            else:
                remote.close()

        self._tunnel_thread = threading.Thread(target=to_client, daemon=True, name="tunnel-bridge")
        self._tunnel_thread.start()

    def _tunnel_close_all(self) -> None:
        self._tunnel_stop.set()
        for s in (self._tunnel_sock, self._tunnel_remote):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        self._tunnel_sock = None
        self._tunnel_remote = None
        self._tunnel_stop = threading.Event()


def message_allowed_files(grants: SessionGrants) -> bool:
    from remotedesk.grants import message_allowed

    return message_allowed(grants, tunnel.FILE_LIST)


def collect_sysinfo() -> dict:
    info: dict = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "username": getpass.getuser(),
        "cpu_count": 0,
        "ram_total": 0,
        "screen": {"left": 0, "top": 0, "width": 0, "height": 0},
    }
    try:
        info["cpu_count"] = os.cpu_count() or 0
    except Exception:
        pass
    if sys.platform == "win32" and kernel32 is not None:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            info["ram_total"] = int(stat.ullTotalPhys)
        if user32 is not None:
            info["screen"] = {
                "left": user32.GetSystemMetrics(76),
                "top": user32.GetSystemMetrics(77),
                "width": user32.GetSystemMetrics(78),
                "height": user32.GetSystemMetrics(79),
            }
    return info


import os  # noqa: E402 — dopo collect_sysinfo setup
