"""Server relay: accoppia host e client per ID a 9 cifre."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import select
import secrets
import signal
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Callable, TextIO

from remotedesk.identity import (
    join_auth_message,
    register_auth_message,
    relay_invite_message,
    sign_join_auth,
    sign_register_auth,
    valid_identity_spki,
    verify_signed_message,
)

DEFAULT_PORT = 8770
READ_LINE_LIMIT = 4096
DEFAULT_MAX_CLIENTS = 512
MAX_CLIENTS_LIMIT = 4096
MAX_ALLOCATIONS_PER_IP_PER_HOUR = 100
logger = logging.getLogger("remotedesk.relay")


def read_line(stream: TextIO, limit: int = READ_LINE_LIMIT) -> str:
    line = stream.readline(limit + 1)
    if not line:
        raise EOFError("connessione chiusa")
    if len(line) > limit or not line.endswith("\n"):
        raise OSError("riga troppo lunga o terminatore mancante")
    return line.rstrip("\r\n")


def read_line_socket(sock: socket.socket, limit: int = READ_LINE_LIMIT) -> str:
    """Legge un solo byte alla volta, così i byte dopo il newline restano nel socket."""
    buf = bytearray()
    while len(buf) < limit:
        chunk = sock.recv(1)
        if not chunk:
            raise EOFError("connessione chiusa")
        if chunk == b"\n":
            break
        if chunk != b"\r":
            buf.append(chunk[0])
    else:
        raise OSError("riga troppo lunga")
    return buf.decode("utf-8")


def write_line(stream: TextIO, line: str) -> None:
    stream.write(line + "\n")
    stream.flush()


def splice_bidirectional(a: socket.socket, b: socket.socket) -> None:
    a.setblocking(True)
    b.setblocking(True)

    def copy(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    t1 = threading.Thread(target=copy, args=(a, b), daemon=True)
    t2 = threading.Thread(target=copy, args=(b, a), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()


def _valid_id9(desk_id: str) -> bool:
    return len(desk_id) == 9 and desk_id.isdigit()


class OwnerStore:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._marker = path.with_name(path.name + ".initialized") if path is not None else None
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, str]] = {}
        if path is not None and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("formato non valido")
                data: dict[str, dict[str, str]] = {}
                for key, value in raw.items():
                    desk_id = str(key)
                    if (
                        not _valid_id9(desk_id)
                        or not isinstance(value, dict)
                        or len(str(value.get("secret_sha256", ""))) != 64
                        or not valid_identity_spki(str(value.get("spki", "")))
                        or not all(c in "0123456789abcdef" for c in str(value.get("secret_sha256", "")))
                    ):
                        raise ValueError("record identità non valido")
                    data[desk_id] = {
                        "secret_sha256": str(value["secret_sha256"]),
                        "spki": str(value["spki"]),
                    }
                self._data = data
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise RuntimeError(f"Archivio identità relay non leggibile: {path}") from exc
        elif path is not None:
            if self._marker is not None and self._marker.exists():
                raise RuntimeError(f"Archivio identità relay mancante: {path}")
            if not self._persist({}):
                raise RuntimeError(f"Archivio identità relay non creabile: {path}")
        if self._marker is not None and not self._marker.exists():
            try:
                self._marker.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self._marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as marker_stream:
                    marker_stream.write(b"RemoteDesk owner store initialized v1\n")
                    marker_stream.flush()
                    os.fsync(marker_stream.fileno())
            except FileExistsError:
                pass
            except OSError as exc:
                raise RuntimeError(f"Stato archivio relay non salvabile: {self._marker}") from exc

    def _persist(self, data: dict[str, dict[str, str]]) -> bool:
        if self._path is None:
            return True
        temp = self._path.with_name(self._path.name + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(data, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self._path)
            return True
        except OSError:
            return False
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def verify_register(self, desk_id: str, secret: str, spki: str, signature: str) -> str:
        if len(secret) != 64 or not all(c in "0123456789abcdefABCDEF" for c in secret):
            return "ERR protocol"
        if not valid_identity_spki(spki):
            return "ERR protocol"
        secret_hash = hashlib.sha256(secret.encode()).hexdigest()
        message = register_auth_message(desk_id, secret, spki)
        with self._lock:
            entry = self._data.get(desk_id)
            if entry is None:
                return "ERR provisioning"
            stored_hash = entry.get("secret_sha256", "")
            if hmac.compare_digest(stored_hash, secret_hash) and verify_signed_message(
                entry.get("spki", ""), message, signature
            ):
                if hmac.compare_digest(entry.get("spki", ""), spki):
                    return "OK"
                candidate = dict(self._data)
                candidate[desk_id] = {"secret_sha256": stored_hash, "spki": spki}
                if not self._persist(candidate):
                    return "ERR storage"
                self._data = candidate
                return "OK"
            return "ERR auth"

    def allocate(self, secret: str, spki: str, signature: str) -> tuple[str, str]:
        """Assegna un ID casuale, idempotente per la coppia segreto/chiave."""
        if len(secret) != 64 or not all(c in "0123456789abcdefABCDEF" for c in secret):
            return "ERR protocol", ""
        if not valid_identity_spki(spki):
            return "ERR protocol", ""
        if not verify_signed_message(
            spki, register_auth_message("NEW", secret, spki), signature
        ):
            return "ERR auth", ""
        secret_hash = hashlib.sha256(secret.encode("ascii")).hexdigest()
        with self._lock:
            for existing_id, entry in self._data.items():
                if hmac.compare_digest(entry.get("spki", ""), spki):
                    if hmac.compare_digest(entry.get("secret_sha256", ""), secret_hash):
                        return "OK", existing_id
                    return "ERR auth", ""
            desk_id = ""
            for _ in range(100):
                candidate_id = f"{secrets.randbelow(1_000_000_000):09d}"
                if candidate_id not in self._data:
                    desk_id = candidate_id
                    break
            if not desk_id:
                return "ERR storage", ""
            candidate = dict(self._data)
            candidate[desk_id] = {"secret_sha256": secret_hash, "spki": spki}
            if not self._persist(candidate):
                return "ERR storage", ""
            self._data = candidate
            return "OK", desk_id

    def lookup_spki(self, desk_id: str) -> str | None:
        with self._lock:
            entry = self._data.get(desk_id)
            if entry is None:
                return None
            spki = entry.get("spki")
            return spki if spki else None


class RateLimiter:
    """Blocca temporaneamente gli IP che accumulano errori di protocollo."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, tuple[int, float, float]] = {}
        self._allocations: dict[str, list[float]] = {}
        self._window_seconds = 60.0
        self._failure_limit = 40
        self._ban_seconds = 60.0

    def allow_allocation(self, ip: str) -> bool:
        if ip in ("127.0.0.1", "::1"):
            return True
        now = time.monotonic()
        cutoff = now - 3600.0
        with self._lock:
            history = [stamp for stamp in self._allocations.get(ip, []) if stamp > cutoff]
            if len(history) >= MAX_ALLOCATIONS_PER_IP_PER_HOUR:
                self._allocations[ip] = history
                return False
            history.append(now)
            self._allocations[ip] = history
            if len(self._allocations) > 10_000:
                self._allocations = {
                    address: stamps
                    for address, stamps in self._allocations.items()
                    if stamps and stamps[-1] > cutoff
                }
                while len(self._allocations) > 10_000:
                    self._allocations.pop(next(iter(self._allocations)))
            return True

    def is_blocked(self, ip: str) -> bool:
        if ip in ("127.0.0.1", "::1"):
            return False
        now = time.monotonic()
        with self._lock:
            item = self._failures.get(ip)
            if item is None:
                return False
            _count, start, blocked_until = item
            if blocked_until > now:
                return True
            if blocked_until or now - start > self._window_seconds:
                self._failures.pop(ip, None)
            return False

    def note_err(self, ip: str) -> bool:
        """Registra ERR; ritorna True se la connessione va chiusa."""
        if ip in ("127.0.0.1", "::1"):
            return False
        now = time.monotonic()
        with self._lock:
            count, start, blocked_until = self._failures.get(ip, (0, now, 0.0))
            if now - start > self._window_seconds or (blocked_until and blocked_until <= now):
                count, start, blocked_until = 0, now, 0.0
            count += 1
            if count >= self._failure_limit:
                blocked_until = now + self._ban_seconds
            self._failures[ip] = (count, start, blocked_until)
            if len(self._failures) > 10_000:
                for candidate, (_n, candidate_start, candidate_blocked) in list(self._failures.items()):
                    if candidate_blocked <= now and now - candidate_start > self._window_seconds:
                        self._failures.pop(candidate, None)
                while len(self._failures) > 10_000:
                    self._failures.pop(next(iter(self._failures)))
            return blocked_until > now


class RelayState:
    def __init__(self, owners: OwnerStore) -> None:
        self._lock = threading.Lock()
        self._hosts: dict[str, socket.socket] = {}
        self._paired: set[str] = set()
        self._handed_off: set[socket.socket] = set()
        self._host_write_locks: dict[str, threading.Lock] = {}
        self._owners = owners

    def register_host(
        self, desk_id: str, secret: str, spki: str, signature: str, sock: socket.socket
    ) -> str:
        auth = self._owners.verify_register(desk_id, secret, spki, signature)
        if auth != "OK":
            return auth
        with self._lock:
            if desk_id in self._paired:
                return "ERR busy"
            existing = self._hosts.get(desk_id)
            # A freshly authenticated owner may replace a stale registration.
            # The old handler observes the close and releases its socket slot.
            if existing is not None and existing is not sock:
                try:
                    existing.close()
                except OSError:
                    pass
            self._hosts[desk_id] = sock
            self._host_write_locks[desk_id] = threading.Lock()
            return "OK"

    def allocate_host(
        self, secret: str, spki: str, signature: str, sock: socket.socket
    ) -> tuple[str, str]:
        status, desk_id = self._owners.allocate(secret, spki, signature)
        if status != "OK":
            return status, ""
        with self._lock:
            if desk_id in self._paired:
                return "ERR busy", ""
            existing = self._hosts.get(desk_id)
            if existing is not None and existing is not sock:
                try:
                    existing.close()
                except OSError:
                    pass
            self._hosts[desk_id] = sock
            self._host_write_locks[desk_id] = threading.Lock()
            return "OK", desk_id

    def lookup(self, desk_id: str) -> str | None:
        spki = self._owners.lookup_spki(desk_id)
        if spki is not None:
            return spki
        with self._lock:
            if desk_id in self._hosts:
                return self._owners.lookup_spki(desk_id)
        return self._owners.lookup_spki(desk_id)

    def take_host_for_join(self, desk_id: str) -> tuple[socket.socket | None, threading.Lock | None]:
        with self._lock:
            if desk_id not in self._hosts:
                return None, None
            if desk_id in self._paired:
                return None, None
            host_sock = self._hosts.pop(desk_id)
            try:
                host_sock.settimeout(None)
            except OSError:
                self._host_write_locks.pop(desk_id, None)
                return None, None
            wlock = self._host_write_locks.pop(desk_id, threading.Lock())
            self._paired.add(desk_id)
            self._handed_off.add(host_sock)
            return host_sock, wlock

    def monitor_host(self, desk_id: str, sock: socket.socket) -> bool:
        """Watch an idle registration until it is paired or disconnected.

        Returns True only when ownership of the socket passed to a JOIN worker.
        The state lock serializes the final read with that handoff.
        """
        while True:
            with self._lock:
                if self._hosts.get(desk_id) is not sock:
                    return self._take_handoff(sock)
            try:
                readable, _, _ = select.select([sock], [], [], 1.0)
            except (OSError, ValueError):
                with self._lock:
                    return self._take_handoff(sock)
            if not readable:
                continue
            with self._lock:
                if self._hosts.get(desk_id) is not sock:
                    return self._take_handoff(sock)
                try:
                    sock.settimeout(1.0)
                    # An idle host never sends application data to the relay.
                    # EOF or unexpected data both invalidate the registration.
                    sock.recv(1)
                except socket.timeout:
                    continue
                except OSError:
                    pass
                return False

    def _take_handoff(self, sock: socket.socket) -> bool:
        """Called with the state lock held by the registration monitor."""
        if sock not in self._handed_off:
            return False
        self._handed_off.remove(sock)
        return True

    def send_invite(self, target_id: str, from_id: str, blob_b64: str) -> str:
        with self._lock:
            if target_id not in self._hosts:
                if target_id in self._paired:
                    return "ERR offline"
                return "ERR offline"
            host_sock = self._hosts[target_id]
            wlock = self._host_write_locks.get(target_id)
        if wlock is None:
            return "ERR offline"
        try:
            with wlock:
                host_text = host_sock.makefile("w", encoding="utf-8", newline="\n")
                write_line(host_text, f"INVITE {from_id} {blob_b64}")
                host_text.close()
            return "OK"
        except OSError:
            return "ERR offline"

    def unpair(self, desk_id: str) -> None:
        with self._lock:
            self._paired.discard(desk_id)


def _reply_err(
    text_out: TextIO,
    line: str,
    limiter: RateLimiter,
    ip: str,
) -> bool:
    write_line(text_out, line)
    return limiter.note_err(ip)


def handle_connection(
    sock: socket.socket,
    state: RelayState,
    limiter: RateLimiter,
    peer_ip: str,
    on_socket_closed: Callable[[socket.socket], None] | None = None,
) -> None:
    desk_id: str | None = None
    role: str | None = None
    peer: socket.socket | None = None
    keep_host_registered = False
    try:
        sock.settimeout(30.0)
        text_in = sock.makefile("r", encoding="utf-8", newline="\n")
        text_out = sock.makefile("w", encoding="utf-8", newline="\n")

        line = read_line(text_in)
        parts = line.split()
        if len(parts) < 2:
            if _reply_err(text_out, "ERR protocol", limiter, peer_ip):
                return
            return
        cmd = parts[0].upper()

        if cmd == "LOOKUP":
            if len(parts) != 2:
                if _reply_err(text_out, "ERR protocol", limiter, peer_ip):
                    return
                return
            arg = parts[1]
            if not _valid_id9(arg):
                if _reply_err(text_out, "ERR id", limiter, peer_ip):
                    return
                return
            spki = state.lookup(arg)
            if spki is None:
                if _reply_err(text_out, "ERR unknown", limiter, peer_ip):
                    return
                return
            write_line(text_out, f"OK {spki}")
            text_in.close()
            text_out.close()
            return

        if cmd == "INVITE":
            if len(parts) != 5:
                if _reply_err(text_out, "ERR protocol", limiter, peer_ip):
                    return
                return
            target_id, from_id, blob, signature = parts[1], parts[2], parts[3], parts[4]
            if not _valid_id9(target_id) or not _valid_id9(from_id):
                if _reply_err(text_out, "ERR id", limiter, peer_ip):
                    return
                return
            if not blob:
                if _reply_err(text_out, "ERR protocol", limiter, peer_ip):
                    return
                return
            sender_spki = state.lookup(from_id)
            if sender_spki is None or not verify_signed_message(
                sender_spki, relay_invite_message(target_id, from_id, blob), signature
            ):
                if _reply_err(text_out, "ERR auth", limiter, peer_ip):
                    return
                return
            reply = state.send_invite(target_id, from_id, blob)
            write_line(text_out, reply)
            if reply.startswith("ERR"):
                limiter.note_err(peer_ip)
            text_in.close()
            text_out.close()
            return

        if cmd == "REGISTER":
            if len(parts) != 5:
                if _reply_err(text_out, "ERR protocol", limiter, peer_ip):
                    return
                return
            desk_id = parts[1]
            secret, spki, signature = parts[2], parts[3], parts[4]
            if not _valid_id9(desk_id):
                if _reply_err(text_out, "ERR id", limiter, peer_ip):
                    return
                return
            role = "host"
            reply = state.register_host(desk_id, secret, spki, signature, sock)
            write_line(text_out, reply)
            if reply == "OK":
                text_in.close()
                text_out.close()
                keep_host_registered = state.monitor_host(desk_id, sock)
                return
            if reply.startswith("ERR"):
                limiter.note_err(peer_ip)
            return

        if cmd == "ALLOCATE":
            if len(parts) != 4:
                if _reply_err(text_out, "ERR protocol", limiter, peer_ip):
                    return
                return
            secret, spki, signature = parts[1], parts[2], parts[3]
            if not limiter.allow_allocation(peer_ip):
                write_line(text_out, "ERR rate limit")
                limiter.note_err(peer_ip)
                return
            reply, assigned_id = state.allocate_host(secret, spki, signature, sock)
            if reply != "OK":
                write_line(text_out, reply)
                limiter.note_err(peer_ip)
                return
            desk_id = assigned_id
            role = "host"
            write_line(text_out, f"OK {assigned_id}")
            text_in.close()
            text_out.close()
            keep_host_registered = state.monitor_host(desk_id, sock)
            return

        if cmd == "JOIN":
            if len(parts) != 3:
                if _reply_err(text_out, "ERR protocol", limiter, peer_ip):
                    return
                return
            arg, from_id = parts[1], parts[2]
            if not _valid_id9(arg) or not _valid_id9(from_id):
                if _reply_err(text_out, "ERR id", limiter, peer_ip):
                    return
                return
            sender_spki = state.lookup(from_id)
            if sender_spki is None:
                if _reply_err(text_out, "ERR auth", limiter, peer_ip):
                    return
                return
            nonce = secrets.token_urlsafe(32)
            write_line(text_out, f"CHALLENGE {nonce}")
            auth_parts = read_line(text_in).split()
            if (
                len(auth_parts) != 2
                or auth_parts[0] != "AUTH"
                or not verify_signed_message(
                    sender_spki,
                    join_auth_message(arg, from_id, nonce),
                    auth_parts[1] if len(auth_parts) == 2 else "",
                )
            ):
                if _reply_err(text_out, "ERR auth", limiter, peer_ip):
                    return
                return
            role = "client"
            desk_id = arg
            host_sock, wlock = state.take_host_for_join(desk_id)
            if host_sock is None:
                with state._lock:
                    busy = desk_id in state._paired
                err = "ERR busy" if busy else "ERR unknown"
                if _reply_err(text_out, err, limiter, peer_ip):
                    return
                return
            # The JOIN worker owns this socket from here, including error paths.
            peer = host_sock
            write_line(text_out, "OK")
            text_in.close()
            text_out.close()

            assert wlock is not None
            with wlock:
                host_text = host_sock.makefile("w", encoding="utf-8", newline="\n")
                write_line(host_text, "OK")
                host_text.close()

            splice_bidirectional(sock, host_sock)
            return

        if _reply_err(text_out, "ERR protocol", limiter, peer_ip):
            return
    except (EOFError, OSError, UnicodeError):
        pass
    finally:
        if role == "host" and desk_id and not keep_host_registered:
            with state._lock:
                if state._hosts.get(desk_id) is sock:
                    del state._hosts[desk_id]
                    state._host_write_locks.pop(desk_id, None)
        if role == "client" and desk_id:
            state.unpair(desk_id)
        if not keep_host_registered:
            try:
                sock.close()
            except OSError:
                pass
            if on_socket_closed is not None:
                on_socket_closed(sock)
        if peer is not None:
            try:
                peer.close()
            except OSError:
                pass
            if on_socket_closed is not None:
                on_socket_closed(peer)


def run_relay(
    host: str,
    port: int,
    owners_path: Path | None = None,
    cert_path: Path | None = None,
    key_path: Path | None = None,
    max_clients: int = DEFAULT_MAX_CLIENTS,
    owners_store=None,
    shutdown_event: threading.Event | None = None,
    proxy_networks: tuple = (),
) -> None:
    if cert_path is None or key_path is None:
        raise ValueError("Il relay richiede --certfile e --keyfile per TLS.")
    if not 1 <= port <= 65535:
        raise ValueError("La porta deve essere compresa tra 1 e 65535.")
    if not 1 <= max_clients <= MAX_CLIENTS_LIMIT:
        raise ValueError(f"--max-clients deve essere compreso tra 1 e {MAX_CLIENTS_LIMIT}.")
    owners = owners_store if owners_store is not None else OwnerStore(owners_path)
    state = RelayState(owners)
    limiter = RateLimiter()
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.minimum_version = ssl.TLSVersion.TLSv1_3
    tls_context.load_cert_chain(str(cert_path), str(key_path))
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(min(max_clients, 128))
    server.settimeout(1.0)
    stop_event = shutdown_event if shutdown_event is not None else threading.Event()
    slots = threading.BoundedSemaphore(max_clients)
    registry_lock = threading.Lock()
    active_sockets: set[socket.socket] = set()
    workers: set[threading.Thread] = set()

    def untrack_socket(sock: socket.socket) -> None:
        with registry_lock:
            tracked = sock in active_sockets
            active_sockets.discard(sock)
        if tracked:
            slots.release()

    def replace_tracked_socket(old: socket.socket, new: socket.socket) -> None:
        with registry_lock:
            active_sockets.discard(old)
            active_sockets.add(new)

    def serve_client(raw_sock: socket.socket, remote_ip: str) -> None:
        client_sock: socket.socket = raw_sock
        handler_started = False
        try:
            from remotedesk.proxy_protocol import source_ip
            remote_ip = source_ip(raw_sock, remote_ip, proxy_networks)
            if limiter.is_blocked(remote_ip):
                return
            raw_sock.settimeout(12.0)
            secure_sock = tls_context.wrap_socket(raw_sock, server_side=True)
            client_sock = secure_sock
            replace_tracked_socket(raw_sock, secure_sock)
            secure_sock.settimeout(None)
            handler_started = True
            handle_connection(secure_sock, state, limiter, remote_ip, untrack_socket)
        except (OSError, ssl.SSLError, ValueError):
            logger.debug("Connessione relay terminata durante handshake o protocollo")
        except Exception:
            logger.exception("Errore interno gestendo una connessione relay")
            try:
                client_sock.close()
            except OSError:
                pass
            untrack_socket(raw_sock)
            untrack_socket(client_sock)
        finally:
            if not handler_started:
                try:
                    client_sock.close()
                except OSError:
                    pass
                untrack_socket(raw_sock)
                untrack_socket(client_sock)
            with registry_lock:
                workers.discard(threading.current_thread())

    stop_handler = lambda _signum, _frame: stop_event.set()
    old_handlers: dict[int, object] = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                old_handlers[signum] = signal.signal(signum, stop_handler)
            except (OSError, ValueError):
                pass

    logger.info("Relay in ascolto su %s:%d (massimo %d connessioni)", host, port, max_clients)

    try:
        while not stop_event.is_set():
            try:
                client_sock, addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                if stop_event.is_set():
                    break
                raise
            peer_ip = addr[0]
            if limiter.is_blocked(peer_ip) or not slots.acquire(blocking=False):
                client_sock.close()
                continue
            with registry_lock:
                active_sockets.add(client_sock)
            worker = threading.Thread(
                target=serve_client,
                args=(client_sock, peer_ip),
                daemon=True,
                name="relay-client",
            )
            with registry_lock:
                workers.add(worker)
            try:
                worker.start()
            except RuntimeError:
                with registry_lock:
                    workers.discard(worker)
                try:
                    client_sock.close()
                finally:
                    untrack_socket(client_sock)
    finally:
        stop_event.set()
        server.close()
        with registry_lock:
            sockets_to_close = list(active_sockets)
            workers_to_join = list(workers)
        for active_sock in sockets_to_close:
            try:
                active_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                active_sock.close()
            except OSError:
                pass
            untrack_socket(active_sock)
        join_deadline = time.monotonic() + 8.0
        for worker in workers_to_join:
            remaining = join_deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(timeout=min(1.0, remaining))
        for signum, old_handler in old_handlers.items():
            try:
                signal.signal(signum, old_handler)
            except (OSError, ValueError):
                pass
        logger.info("Relay arrestato")


def relay_handshake_register(
    sock: socket.socket,
    desk_id: str,
    secret: str,
    spki_b64: str,
    key_path: Path,
    timeout: float = 15.0,
) -> str:
    signature = sign_register_auth(key_path, desk_id, secret, spki_b64)
    sock.settimeout(timeout)
    f_in = sock.makefile("r", encoding="utf-8", newline="\n")
    f_out = sock.makefile("w", encoding="utf-8", newline="\n")
    write_line(f_out, f"REGISTER {desk_id} {secret} {spki_b64} {signature}")
    reply = read_line(f_in)
    f_in.close()
    f_out.close()
    sock.settimeout(None)
    return reply


def relay_handshake_allocate(
    sock: socket.socket,
    secret: str,
    spki_b64: str,
    key_path: Path,
    timeout: float = 15.0,
) -> tuple[str, str]:
    signature = sign_register_auth(key_path, "NEW", secret, spki_b64)
    sock.settimeout(timeout)
    f_in = sock.makefile("r", encoding="utf-8", newline="\n")
    f_out = sock.makefile("w", encoding="utf-8", newline="\n")
    write_line(f_out, f"ALLOCATE {secret} {spki_b64} {signature}")
    reply = read_line(f_in)
    f_in.close()
    f_out.close()
    sock.settimeout(None)
    parts = reply.split(maxsplit=1)
    if len(parts) == 2 and parts[0] == "OK" and _valid_id9(parts[1]):
        return "OK", parts[1]
    return (parts[0] if parts else "ERR"), (parts[1] if len(parts) > 1 else reply)


def relay_handshake_join(
    sock: socket.socket,
    desk_id: str,
    from_id: str,
    key_path: Path,
    timeout: float = 15.0,
) -> str:
    sock.settimeout(timeout)
    f_in = sock.makefile("r", encoding="utf-8", newline="\n")
    f_out = sock.makefile("w", encoding="utf-8", newline="\n")
    write_line(f_out, f"JOIN {desk_id} {from_id}")
    reply = read_line(f_in)
    parts = reply.split(maxsplit=1)
    if parts and parts[0] == "CHALLENGE" and len(parts) == 2:
        signature = sign_join_auth(key_path, desk_id, from_id, parts[1])
        write_line(f_out, f"AUTH {signature}")
        reply = read_line(f_in)
    f_in.close()
    f_out.close()
    sock.settimeout(None)
    return reply


def relay_handshake_lookup(sock: socket.socket, desk_id: str, timeout: float = 15.0) -> tuple[str, str]:
    """Ritorna (status, spki_b64 o messaggio errore)."""
    sock.settimeout(timeout)
    f_in = sock.makefile("r", encoding="utf-8", newline="\n")
    f_out = sock.makefile("w", encoding="utf-8", newline="\n")
    write_line(f_out, f"LOOKUP {desk_id}")
    reply = read_line(f_in)
    f_in.close()
    f_out.close()
    sock.settimeout(None)
    parts = reply.split(maxsplit=1)
    if len(parts) >= 1 and parts[0] == "OK" and len(parts) == 2:
        return "OK", parts[1]
    return parts[0] if parts else "ERR", reply


def relay_handshake_invite(
    sock: socket.socket,
    target_id: str,
    from_id: str,
    blob_b64: str,
    signature_b64: str,
    timeout: float = 15.0,
) -> str:
    sock.settimeout(timeout)
    f_in = sock.makefile("r", encoding="utf-8", newline="\n")
    f_out = sock.makefile("w", encoding="utf-8", newline="\n")
    write_line(f_out, f"INVITE {target_id} {from_id} {blob_b64} {signature_b64}")
    reply = read_line(f_in)
    f_in.close()
    f_out.close()
    sock.settimeout(None)
    return reply


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="RemoteDesk relay (TLS 1.3 obbligatorio)",
        epilog="Usa un certificato pubblico valido per il nome DNS configurato nei client.",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--certfile", required=True, help="Certificato PEM con la catena completa")
    parser.add_argument("--keyfile", required=True, help="Chiave privata PEM del certificato")
    parser.add_argument("--owners-file", default="relay-owners.json", help="Archivio identità persistente")
    parser.add_argument("--cluster", action="store_true", help="PostgreSQL e relay attivo/standby")
    parser.add_argument("--import-owners", action="store_true", help="Importa owners-file in PostgreSQL vuoto e termina")
    parser.add_argument(
        "--max-clients",
        type=int,
        default=DEFAULT_MAX_CLIENTS,
        help=f"Numero massimo di socket attivi (predefinito: {DEFAULT_MAX_CLIENTS})",
    )
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    args = parser.parse_args(argv)
    if args.import_owners:
        from remotedesk.relay_cluster import migrate_owners
        migrate_owners(Path(args.owners_file))
        return
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.cluster:
        from remotedesk.relay_cluster import run_cluster
        run_cluster(args.host, args.port, Path(args.certfile), Path(args.keyfile), args.max_clients)
        return
    run_relay(
        args.host,
        args.port,
        owners_path=Path(args.owners_file),
        cert_path=Path(args.certfile),
        key_path=Path(args.keyfile),
        max_clients=args.max_clients,
    )


if __name__ == "__main__":
    main()
