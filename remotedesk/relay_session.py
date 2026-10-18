"""Connessione relay lato host/client (tunnel)."""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import BinaryIO, Callable

from remotedesk.config import parse_relay_host_port
from remotedesk.identity import (
    open_token,
    sign_peer_auth,
    sign_relay_invite,
    update_host_certificate_identity,
    verify_peer_auth,
)
from remotedesk.input_handler import handle_input
from remotedesk.file_receive import FileReceiver
from remotedesk.relay import (
    read_line_socket,
    relay_handshake_allocate,
    relay_handshake_invite,
    relay_handshake_join,
    relay_handshake_lookup,
    relay_handshake_register,
)
from remotedesk.safe_name import safe_download_basename
from remotedesk.grants import SessionGrants, message_allowed
from remotedesk.state import ShareState
from remotedesk.tls_channel import ConfirmFingerprint, wrap_client, wrap_server
from remotedesk import tunnel


MessageCallback = Callable[[int, bytes], None]
StatusCallback = Callable[[str], None]


def connect_relay(relay: str, timeout: float = 15.0) -> socket.socket:
    parsed = parse_relay_host_port(relay)
    if parsed is None:
        raise ValueError("Indirizzo server non valido")
    host, port = parsed
    raw_sock = socket.create_connection((host, port), timeout=timeout)
    raw_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    try:
        sock = context.wrap_socket(raw_sock, server_hostname=host)
        sock.settimeout(timeout)
        return sock
    except (OSError, ssl.SSLError):
        raw_sock.close()
        raise


def lookup_relay_spki(relay: str, desk_id: str, timeout: float = 15.0) -> tuple[str, str]:
    try:
        sock = connect_relay(relay, timeout=timeout)
    except (OSError, ValueError) as exc:
        return "ERR", str(exc)
    try:
        try:
            return relay_handshake_lookup(sock, desk_id, timeout=timeout)
        except (OSError, UnicodeError, ValueError) as exc:
            return "ERR", str(exc)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def downloads_dir(override: str = "") -> Path:
    custom = override.strip()
    if custom:
        return Path(custom)
    home = Path(os.environ.get("USERPROFILE", Path.home()))
    return home / "Downloads" / "RemoteDesk"


def recordings_base_dir(override: str = "") -> Path:
    custom = override.strip()
    if custom:
        return Path(custom)
    return downloads_dir() / "registrazioni"


def screenshots_base_dir(override: str = "") -> Path:
    custom = override.strip()
    if custom:
        return Path(custom)
    return downloads_dir() / "schermate"


class HostRelayThread:
    """Thread host: registra l'identità e gestisce il tunnel col relay."""

    def __init__(
        self,
        desk_id: str,
        relay: str,
        state: ShareState,
        on_status: StatusCallback,
        on_hello: Callable[[bytes, int], None],
        on_chat: Callable[[str], None],
        on_clip: Callable[[str], None],
        on_file_offer: Callable[[str, int, int], None],
        on_file_reply: Callable[[bool, int], None],
        get_relay: Callable[[], str],
        host_secret: str,
        key_path: Path,
        cert_path: Path,
        spki_b64: str,
        identity_provisioned: bool = True,
        on_invite: Callable[[str, str], None] | None = None,
        on_identity: Callable[[str], None] | None = None,
        on_tunnel: Callable[[int, bytes], None] | None = None,
        on_session_end: Callable[[int], None] | None = None,
    ) -> None:
        self._desk_id = desk_id
        self._identity_provisioned = identity_provisioned
        self._relay = relay
        self._state = state
        self._on_status = on_status
        self._on_hello = on_hello
        self._on_chat = on_chat
        self._on_clip = on_clip
        self._on_file_offer = on_file_offer
        self._on_file_reply = on_file_reply
        self._get_relay = get_relay
        self._host_secret = host_secret
        self._key_path = key_path
        self._cert_path = cert_path
        self._spki_b64 = spki_b64
        self._on_invite = on_invite or (lambda _id, _tok: None)
        self._on_identity = on_identity or (lambda _id: None)
        self._on_tunnel = on_tunnel or (lambda _t, _p: None)
        self._on_session_end = on_session_end or (lambda _generation: None)
        self._writer: BinaryIO | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._sock_lock = threading.Lock()
        self._session_active = False
        self._generation = 0
        self._hello_pending: int | None = None
        self._sharing = False
        self._file_recv: FileReceiver | None = None
        self._file_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._grants = SessionGrants()

    def set_grants(self, grants: SessionGrants) -> None:
        self._grants = grants

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="host-relay")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._sock_lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass

    def set_sharing(self, on: bool) -> None:
        self._sharing = on

    def end_session(self) -> None:
        """Revoke the active peer's access immediately when sharing is stopped."""
        self._sharing = False
        self._session_active = False
        self._close_sock()

    def is_current_hello(self, generation: int) -> bool:
        with self._sock_lock:
            return self._hello_pending == generation and self._generation == generation

    def current_generation(self) -> int:
        with self._sock_lock:
            return self._generation

    def is_active_generation(self, generation: int) -> bool:
        with self._sock_lock:
            return self._generation == generation and self._session_active and self._sharing

    def send_accept(self, payload: bytes, generation: int) -> bool:
        with self._sock_lock:
            if self._generation != generation or self._hello_pending != generation:
                return False
            self._hello_pending = None
            self._session_active = True
            self._sharing = True
        if not self._send(tunnel.ACCEPT, payload, expected_generation=generation):
            self._close_sock(expected_generation=generation)
            return False
        return True

    def send_reject(self, generation: int) -> None:
        if not self.is_current_hello(generation):
            return
        self._send(tunnel.REJECT, b"", expected_generation=generation)
        self._close_sock(expected_generation=generation)

    def send_grants(self, payload: bytes) -> None:
        self._send(tunnel.GRANTS, payload)

    def send_chat(self, text: str) -> None:
        if not message_allowed(self._grants, tunnel.CHAT):
            return
        self._send(tunnel.CHAT, tunnel.encode_chat(text))

    def send_clip(self, text: str) -> None:
        if not message_allowed(self._grants, tunnel.CLIP):
            return
        self._send(tunnel.CLIP, tunnel.encode_clip(text))

    def send_file_offer(self, name: str, size: int, generation: int | None = None) -> None:
        if not message_allowed(self._grants, tunnel.FILE_OFFER):
            return
        self._send(tunnel.FILE_OFFER, tunnel.encode_file_offer(name, size), expected_generation=generation)

    def send_file_chunk(self, chunk: bytes, generation: int | None = None) -> None:
        if not message_allowed(self._grants, tunnel.FILE_CHUNK):
            return
        self._send(tunnel.FILE_CHUNK, chunk, expected_generation=generation)

    def send_file_accept(self, generation: int | None = None) -> None:
        if not message_allowed(self._grants, tunnel.FILE_ACCEPT):
            return
        self._send(tunnel.FILE_ACCEPT, b"", expected_generation=generation)

    def send_file_reject(self, generation: int | None = None) -> None:
        if not message_allowed(self._grants, tunnel.FILE_REJECT):
            return
        self._send(tunnel.FILE_REJECT, b"", expected_generation=generation)

    def send_frame(self, jpeg: bytes) -> None:
        self._send(tunnel.FRAME, jpeg)

    def reconnect(self) -> None:
        self._close_sock()

    def _close_sock(self, expected_generation: int | None = None) -> None:
        with self._sock_lock:
            if expected_generation is not None and self._generation != expected_generation:
                return
            self._generation += 1
            self._hello_pending = None
            self._session_active = False
            self._sharing = False
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            self._writer = None

    def _send(self, msg_type: int, payload: bytes, expected_generation: int | None = None) -> bool:
        with self._sock_lock:
            writer = self._writer
            generation = self._generation
        if writer is None or (expected_generation is not None and generation != expected_generation):
            return False
        if msg_type not in (tunnel.ACCEPT, tunnel.REJECT) and not self._session_active:
            return False
        try:
            with self._send_lock:
                with self._sock_lock:
                    if self._writer is not writer or self._generation != generation:
                        return False
                tunnel.write_message(writer, msg_type, payload)
            return True
        except (OSError, tunnel.TunnelError):
            return False

    def _wait_for_pair(self, sock: socket.socket) -> socket.socket | None:
        """Legge righe finché arriva OK; gestisce INVITE nel frattempo. Poi TLS server."""
        try:
            sock.settimeout(None)
            while True:
                line = read_line_socket(sock).strip()
                if line == "OK":
                    break
                if line.startswith("INVITE "):
                    parts = line.split(maxsplit=2)
                    if len(parts) == 3:
                        from_id, blob = parts[1], parts[2]
                        if len(from_id) == 9 and from_id.isdigit():
                            opened = open_token(self._key_path, blob)
                            if opened is not None:
                                self._on_invite(from_id, opened.decode("utf-8", errors="replace"))
                    continue
        except (EOFError, OSError, UnicodeError, RuntimeError):
            return None
        try:
            return wrap_server(sock, self._cert_path, self._key_path)
        except OSError:
            return None

    def _run(self) -> None:
        while not self._stop.is_set():
            relay = self._get_relay().strip()
            if not relay:
                self._on_status("Server non impostato")
                if self._stop.wait(2.0):
                    break
                continue
            sock: socket.socket | None = None
            try:
                sock = connect_relay(relay)
                if self._identity_provisioned:
                    reply = relay_handshake_register(
                        sock, self._desk_id, self._host_secret, self._spki_b64, self._key_path
                    )
                    needs_allocation = reply == "ERR provisioning"
                else:
                    reply = ""
                    needs_allocation = True
                if needs_allocation:
                    if self._identity_provisioned:
                        sock.close()
                        sock = connect_relay(relay)
                    status, assigned_id = relay_handshake_allocate(
                        sock, self._host_secret, self._spki_b64, self._key_path
                    )
                    if status == "OK":
                        update_host_certificate_identity(
                            self._cert_path, self._key_path, assigned_id
                        )
                        self._desk_id = assigned_id
                        self._identity_provisioned = True
                        self._on_identity(assigned_id)
                        reply = "OK"
                    else:
                        reply = f"{status} {assigned_id}".strip()
                if reply != "OK":
                    self._on_status(f"Errore relay: {reply}")
                    sock.close()
                    if self._stop.wait(3.0):
                        break
                    continue
                self._on_status("Connesso al server")
                tls_sock = self._wait_for_pair(sock)
                if tls_sock is None:
                    sock.close()
                    continue
                with self._sock_lock:
                    self._generation += 1
                    self._hello_pending = None
                    self._sock = tls_sock
                    self._writer = tls_sock.makefile("wb")
                self._read_loop(tls_sock)
            except (OSError, ValueError, RuntimeError) as e:
                self._on_status(f"Errore: {e}")
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            finally:
                self._session_active = False
                self._sharing = False
                self._state.set_sharing(False)
                self._close_sock()
                self._on_session_end(self.current_generation())
            if self._stop.wait(2.0):
                break

    def _read_loop(self, sock: socket.socket) -> None:
        stream = sock.makefile("rb")
        peer_authenticated = False
        try:
            challenge = secrets.token_bytes(32)
            with self._sock_lock:
                writer = self._writer
            if writer is None:
                return
            tunnel.write_message(writer, tunnel.AUTH_CHALLENGE, challenge)
            while not self._stop.is_set():
                msg_type, payload = tunnel.read_message(stream)
                if msg_type == tunnel.HELLO:
                    if peer_authenticated:
                        self._send(tunnel.REJECT, b"")
                        return
                    hello = decode_hello(payload)
                    from_id = hello["from"]
                    if len(from_id) != 9 or not from_id.isdigit():
                        self._send(tunnel.REJECT, b"")
                        return
                    try:
                        signature = base64.b64decode(hello["signature"], validate=True)
                    except (ValueError, UnicodeEncodeError):
                        self._send(tunnel.REJECT, b"")
                        return
                    status, spki = lookup_relay_spki(self._get_relay().strip(), from_id)
                    if status != "OK" or not verify_peer_auth(
                        spki, from_id, self._desk_id, challenge, signature
                    ):
                        self._send(tunnel.REJECT, b"")
                        return
                    peer_authenticated = True
                    with self._sock_lock:
                        generation = self._generation
                        self._hello_pending = generation
                    self._on_hello(payload, generation)
                elif not peer_authenticated or not self._session_active:
                    self._send(tunnel.REJECT, b"")
                    return
                elif not self._sharing:
                    self._send(tunnel.REJECT, b"")
                    return
                elif msg_type == tunnel.INPUT:
                    if self._sharing and message_allowed(self._grants, msg_type):
                        try:
                            data = tunnel.decode_input(payload)
                            handle_input(self._state, data)
                        except OSError:
                            now = time.monotonic()
                            if now - getattr(self, "_last_input_error", 0.0) >= 5:
                                self._last_input_error = now
                                self._on_status("Windows ha rifiutato l'input: controllare i privilegi sul PC locale.")
                        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError, OverflowError):
                            pass
                elif msg_type == tunnel.VIEW:
                    if self._sharing and self._grants.control:
                        from remotedesk.capture import QUALITY_PRESETS

                        quality, monitor = tunnel.decode_view(payload)
                        if quality in QUALITY_PRESETS:
                            self._state.set_quality(quality)
                        self._state.set_monitor_index(monitor)
                elif msg_type == tunnel.CHAT:
                    if not message_allowed(self._grants, msg_type):
                        continue
                    try:
                        self._on_chat(payload.decode("utf-8"))
                    except UnicodeDecodeError:
                        pass
                elif msg_type == tunnel.CLIP:
                    if not message_allowed(self._grants, msg_type):
                        continue
                    try:
                        self._on_clip(payload.decode("utf-8"))
                    except UnicodeDecodeError:
                        pass
                elif msg_type == tunnel.FILE_OFFER:
                    if not message_allowed(self._grants, msg_type):
                        continue
                    try:
                        name, size = tunnel.decode_file_offer(payload)
                        self._on_file_offer(name, size, self.current_generation())
                    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                        pass
                elif msg_type == tunnel.FILE_ACCEPT:
                    if not message_allowed(self._grants, msg_type):
                        continue
                    self._on_file_reply(True, self.current_generation())
                elif msg_type == tunnel.FILE_REJECT:
                    if not message_allowed(self._grants, msg_type):
                        continue
                    self._on_file_reply(False, self.current_generation())
                elif msg_type == tunnel.FILE_CHUNK:
                    if not message_allowed(self._grants, msg_type):
                        continue
                    with self._file_lock:
                        if self._file_recv is not None and self._file_recv.write(payload):
                            self._file_recv = None
                elif msg_type in (
                    tunnel.ACTION,
                    tunnel.DRAW,
                    tunnel.SYSINFO,
                    tunnel.TUNNEL_OPEN,
                    tunnel.TUNNEL_DATA,
                    tunnel.TUNNEL_CLOSE,
                    tunnel.FILE_LIST,
                    tunnel.REC,
                ):
                    if not message_allowed(self._grants, msg_type):
                        continue
                    self._on_tunnel(msg_type, payload)
        except (tunnel.TunnelError, OSError, UnicodeError, RuntimeError, ValueError):
            pass
        finally:
            self.cancel_file_receive()
            try:
                stream.close()
            except OSError:
                pass

    def start_file_receive(self, name: str, size: int, dest: Path, generation: int) -> bool:
        if not self.is_active_generation(generation):
            return False
        receiver = FileReceiver(dest, size)
        with self._sock_lock:
            if self._generation != generation or not self._session_active or not self._sharing:
                receiver.abort()
                return False
            with self._file_lock:
                if self._file_recv is not None:
                    self._file_recv.abort()
                self._file_recv = receiver
        return True

    def cancel_file_receive(self) -> None:
        with self._file_lock:
            if self._file_recv is not None:
                self._file_recv.abort()
            self._file_recv = None


class FrameSenderThread:
    """Invia FRAME ~8 Hz mentre sharing attivo."""

    def __init__(self, state: ShareState, send_frame: Callable[[bytes], None], is_sharing: Callable[[], bool]) -> None:
        self._state = state
        self._send_frame = send_frame
        self._is_sharing = is_sharing
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="frame-sender")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        interval = 1.0 / 8.0
        while not self._stop.is_set():
            if self._is_sharing():
                with self._state.lock:
                    jpeg = self._state.latest_jpeg
                if jpeg:
                    self._send_frame(jpeg)
            if self._stop.wait(interval):
                break


def send_relay_invite(
    relay: str,
    target_id: str,
    from_id: str,
    blob_b64: str,
    key_path: Path,
    timeout: float = 15.0,
) -> str:
    try:
        sock = connect_relay(relay, timeout=timeout)
    except OSError:
        return "ERR offline"
    try:
        status, spki_or_err = relay_handshake_lookup(sock, target_id, timeout=timeout)
        if status != "OK":
            if status == "ERR unknown":
                return "ERR unknown"
            return spki_or_err
        sock.close()
        sock = connect_relay(relay, timeout=timeout)
        signature = sign_relay_invite(key_path, target_id, from_id, blob_b64)
        return relay_handshake_invite(
            sock, target_id, from_id, blob_b64, signature, timeout=timeout
        )
    finally:
        try:
            sock.close()
        except OSError:
            pass


def encode_hello(from_id: str, token: str = "", password: str = "", signature: str = "") -> bytes:
    return json.dumps(
        {"from": from_id, "token": token, "password": password, "signature": signature},
        separators=(",", ":"),
    ).encode("utf-8")


def decode_hello(payload: bytes) -> dict[str, str]:
    out = {"from": "", "token": "", "password": "", "signature": ""}
    if not payload or len(payload) > 4096:
        return out
    try:
        data = json.loads(payload.decode("utf-8"))
        if isinstance(data, dict):
            for key in out:
                value = data.get(key, "")
                if not isinstance(value, str):
                    return {"from": "", "token": "", "password": "", "signature": ""}
                out[key] = value.strip() if key == "from" else value
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return out


def decode_hello_from(payload: bytes) -> str:
    return decode_hello(payload)["from"]


def client_connect(
    relay: str,
    remote_id: str,
    from_id: str = "",
    token: str = "",
    password: str = "",
    trusted: dict[str, str] | None = None,
    confirm_fingerprint: ConfirmFingerprint | None = None,
    on_trust: Callable[[str, str], None] | None = None,
    cert_path: Path | None = None,
    key_path: Path | None = None,
    timeout: float = 65.0,
) -> tuple[ClientTunnel | None, str]:
    """JOIN, TLS, HELLO, attende ACCEPT/REJECT."""
    trusted = trusted if trusted is not None else {}
    if len(remote_id) != 9 or not remote_id.isdigit():
        return None, "ID remoto non valido."
    if len(from_id) != 9 or not from_id.isdigit():
        return None, "ID locale non valido."
    if key_path is None:
        return None, "Chiave identità locale non disponibile."
    status, expected_spki = lookup_relay_spki(relay, remote_id, timeout=min(timeout, 15.0))
    if status != "OK":
        if status == "ERR unknown":
            return None, "ID sconosciuto o host non connesso."
        return None, f"Directory relay non disponibile: {expected_spki}"
    try:
        sock = connect_relay(relay, timeout=min(timeout, 15.0))
    except (OSError, ValueError) as e:
        return None, f"Errore di rete: {e}"
    try:
        reply = relay_handshake_join(sock, remote_id, from_id, key_path)
    except (OSError, ValueError, RuntimeError, TimeoutError) as e:
        sock.close()
        return None, f"Autenticazione relay non riuscita: {e}"
    if reply != "OK":
        sock.close()
        if reply == "ERR unknown":
            return None, "ID sconosciuto o host non connesso."
        if reply == "ERR busy":
            return None, "Host occupato."
        return None, f"Errore relay: {reply}"

    tls_sock, tls_err = wrap_client(
        sock,
        remote_id,
        trusted,
        expected_spki,
        confirm_fingerprint,
        timeout=min(timeout, 60.0),
    )
    if tls_sock is None:
        try:
            sock.close()
        except OSError:
            pass
        return None, tls_err or "TLS non riuscito."

    trusted_before = dict(trusted)
    peer_cert = tls_sock.getpeercert(binary_form=True)
    if peer_cert and on_trust and remote_id not in trusted_before:
        from remotedesk.identity import cert_fingerprint_sha256

        on_trust(remote_id, cert_fingerprint_sha256(peer_cert))

    tls_sock.settimeout(timeout)
    reader = tls_sock.makefile("rb")
    writer = tls_sock.makefile("wb")
    try:
        msg_type, challenge = tunnel.read_message(reader)
        if msg_type != tunnel.AUTH_CHALLENGE or len(challenge) != 32:
            tls_sock.close()
            return None, "Autenticazione peer non valida."
        signature = sign_peer_auth(key_path, from_id, remote_id, challenge)
        signature_b64 = base64.b64encode(signature).decode("ascii")
        tunnel.write_message(
            writer,
            tunnel.HELLO,
            encode_hello(from_id, token=token, password=password, signature=signature_b64),
        )
    except (OSError, tunnel.TunnelError, ValueError, TypeError, RuntimeError, TimeoutError) as e:
        tls_sock.close()
        return None, f"Autenticazione tunnel non riuscita: {e}"

    try:
        msg_type, accept_payload = tunnel.read_message(reader)
    except (tunnel.TunnelError, OSError, TimeoutError):
        tls_sock.close()
        return None, "Timeout in attesa di risposta dall'host."
    finally:
        tls_sock.settimeout(120.0)

    if msg_type == tunnel.REJECT:
        tls_sock.close()
        return None, "Connessione rifiutata dall'host."
    if msg_type != tunnel.ACCEPT:
        tls_sock.close()
        return None, "Risposta inattesa dall'host."
    grants = SessionGrants.from_payload(accept_payload)
    tunnel_obj = ClientTunnel(tls_sock, reader=reader, writer=writer)
    tunnel_obj.grants = grants
    return tunnel_obj, ""


class ClientTunnel:
    def __init__(
        self,
        sock: socket.socket,
        state: ShareState | None = None,
        reader: BinaryIO | None = None,
        writer: BinaryIO | None = None,
    ) -> None:
        self._sock = sock
        self._state = state
        self._reader = reader if reader is not None else sock.makefile("rb")
        self._writer = writer if writer is not None else sock.makefile("wb")
        self._send_lock = threading.Lock()
        self._file_reply = threading.Event()
        self._file_accepted = False
        self._natural_w = 1
        self._natural_h = 1
        self._file_recv: FileReceiver | None = None
        self.grants: SessionGrants = SessionGrants()

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def set_frame_size(self, w: int, h: int) -> None:
        self._natural_w = max(1, w)
        self._natural_h = max(1, h)

    def send_input(self, payload: dict) -> None:
        if not message_allowed(self.grants, tunnel.INPUT):
            return
        self._send(tunnel.INPUT, tunnel.encode_input(payload))

    def send_chat(self, text: str) -> None:
        if not message_allowed(self.grants, tunnel.CHAT):
            return
        self._send(tunnel.CHAT, tunnel.encode_chat(text))

    def send_clip(self, text: str) -> None:
        if not message_allowed(self.grants, tunnel.CLIP):
            return
        self._send(tunnel.CLIP, tunnel.encode_clip(text))

    def send_file_offer(self, name: str, size: int) -> None:
        if not message_allowed(self.grants, tunnel.FILE_OFFER):
            return
        self._send(tunnel.FILE_OFFER, tunnel.encode_file_offer(name, size))

    def send_file_chunk(self, chunk: bytes) -> None:
        if not message_allowed(self.grants, tunnel.FILE_CHUNK):
            return
        self._send(tunnel.FILE_CHUNK, chunk)

    def send_file_accept(self) -> None:
        if not message_allowed(self.grants, tunnel.FILE_ACCEPT):
            return
        self._send(tunnel.FILE_ACCEPT, b"")

    def send_file_reject(self) -> None:
        if not message_allowed(self.grants, tunnel.FILE_REJECT):
            return
        self._send(tunnel.FILE_REJECT, b"")

    def send_action(self, payload: dict) -> None:
        self._send(tunnel.ACTION, json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    def send_draw(self, x1: float, y1: float, x2: float, y2: float) -> None:
        if not message_allowed(self.grants, tunnel.DRAW):
            return
        body = json.dumps({"x1": x1, "y1": y1, "x2": x2, "y2": y2}, separators=(",", ":"))
        self._send(tunnel.DRAW, body.encode("utf-8"))

    def send_sysinfo_request(self) -> None:
        if not message_allowed(self.grants, tunnel.SYSINFO):
            return
        self._send(tunnel.SYSINFO, b"{}")

    def send_file_list(self, path: str) -> None:
        if not message_allowed(self.grants, tunnel.FILE_LIST):
            return
        body = json.dumps({"path": path}, separators=(",", ":"))
        self._send(tunnel.FILE_LIST, body.encode("utf-8"))

    def send_rec(self, on: bool) -> None:
        if not message_allowed(self.grants, tunnel.REC):
            return
        self._send(tunnel.REC, json.dumps({"on": on}, separators=(",", ":")).encode("utf-8"))

    def send_tunnel_open(self, target_host: str, target_port: int) -> None:
        if not message_allowed(self.grants, tunnel.TUNNEL_OPEN):
            return
        body = json.dumps(
            {"target_host": target_host, "target_port": target_port},
            separators=(",", ":"),
        )
        self._send(tunnel.TUNNEL_OPEN, body.encode("utf-8"))

    def send_tunnel_data(self, conn_id: int, data: bytes) -> None:
        if not message_allowed(self.grants, tunnel.TUNNEL_DATA):
            return
        import struct

        if len(data) > tunnel.MAX_TUNNEL_DATA:
            data = data[: tunnel.MAX_TUNNEL_DATA]
        self._send(tunnel.TUNNEL_DATA, struct.pack(">I", conn_id) + data)

    def send_tunnel_close(self) -> None:
        if not message_allowed(self.grants, tunnel.TUNNEL_CLOSE):
            return
        self._send(tunnel.TUNNEL_CLOSE, b"")

    def send_view(self, quality: str, monitor: int) -> None:
        if not message_allowed(self.grants, tunnel.VIEW):
            return
        self._send(tunnel.VIEW, tunnel.encode_view(quality, monitor))

    def _send(self, msg_type: int, payload: bytes) -> None:
        try:
            with self._send_lock:
                tunnel.write_message(self._writer, msg_type, payload)
        except (OSError, tunnel.TunnelError):
            pass

    def read_message(self) -> tuple[int, bytes] | None:
        try:
            return tunnel.read_message(self._reader)
        except (tunnel.TunnelError, OSError):
            return None

    def note_file_reply(self, accepted: bool) -> None:
        self._file_accepted = accepted
        self._file_reply.set()

    def wait_file_reply(self, timeout: float) -> bool:
        self._file_reply.clear()
        if not self._file_reply.wait(timeout):
            return False
        return self._file_accepted


def new_invite_token() -> str:
    return secrets.token_urlsafe(32)
