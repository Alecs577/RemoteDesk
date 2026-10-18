"""TLS 1.3 end-to-end channel over either TCP or the TLS relay stream."""

from __future__ import annotations

import hmac
import io
import socket
import ssl
import threading
from pathlib import Path
from typing import Callable

from remotedesk.identity import cert_fingerprint_sha256, cert_spki_b64_from_der, host_key_password

ConfirmFingerprint = Callable[[str, str], bool]


class _TLSRawIO(io.RawIOBase):
    def __init__(self, channel: "_BIOChannel", readable: bool) -> None:
        super().__init__()
        self._channel = channel
        self._readable = readable

    def readable(self) -> bool:
        return self._readable

    def writable(self) -> bool:
        return not self._readable

    def readinto(self, buffer: bytearray) -> int:
        data = self._channel.recv(len(buffer))
        if not data:
            return 0
        buffer[:len(data)] = data
        return len(data)

    def write(self, data: bytes | bytearray) -> int:
        self._channel.sendall(bytes(data))
        return len(data)


class _BIOChannel:
    """Socket-like TLS endpoint; MemoryBIO permits TLS inside relay TLS."""

    def __init__(self, transport: socket.socket, context: ssl.SSLContext, server_side: bool) -> None:
        self._transport = transport
        self._incoming = ssl.MemoryBIO()
        self._outgoing = ssl.MemoryBIO()
        self._ssl = context.wrap_bio(
            self._incoming,
            self._outgoing,
            server_side=server_side,
            server_hostname=None,
        )
        self._lock = threading.RLock()
        self._recv_lock = threading.Lock()
        self._closed = False
        self._handshake()

    def _take_outgoing(self) -> bytes:
        with self._lock:
            chunks = []
            while self._outgoing.pending:
                chunks.append(self._outgoing.read())
            return b"".join(chunks)

    def _flush(self) -> None:
        # Keep SSL operations, BIO draining and transport writes in one order.
        with self._lock:
            data = self._take_outgoing()
            if data:
                self._transport.sendall(data)

    def _receive_transport(self) -> None:
        with self._recv_lock:
            data = self._transport.recv(65536)
            with self._lock:
                if data:
                    self._incoming.write(data)
                else:
                    self._incoming.write_eof()

    def _handshake(self) -> None:
        while True:
            try:
                with self._lock:
                    try:
                        self._ssl.do_handshake()
                    finally:
                        self._flush()
                return
            except ssl.SSLWantReadError:
                self._flush()
                self._receive_transport()
            except ssl.SSLWantWriteError:
                self._flush()

    def recv(self, size: int) -> bytes:
        if size <= 0:
            return b""
        while not self._closed:
            try:
                with self._lock:
                    try:
                        data = self._ssl.read(size)
                    finally:
                        self._flush()
                return data
            except ssl.SSLWantReadError:
                self._flush()
                self._receive_transport()
            except ssl.SSLWantWriteError:
                self._flush()
            except ssl.SSLZeroReturnError:
                return b""
        return b""

    def sendall(self, data: bytes) -> None:
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            try:
                with self._lock:
                    try:
                        written = self._ssl.write(view[offset:])
                    finally:
                        self._flush()
                offset += written
            except ssl.SSLWantReadError:
                self._flush()
                self._receive_transport()
            except ssl.SSLWantWriteError:
                self._flush()

    def makefile(self, mode: str = "r", buffering: int = -1, **_kwargs: object) -> io.IOBase:
        if "b" not in mode or mode not in ("rb", "wb", "r+b", "w+b"):
            raise ValueError("Il canale TLS supporta solo stream binari")
        if mode == "rb":
            raw = _TLSRawIO(self, readable=True)
            return io.BufferedReader(raw, buffer_size=io.DEFAULT_BUFFER_SIZE if buffering < 0 else buffering)
        if mode == "wb":
            raw = _TLSRawIO(self, readable=False)
            return io.BufferedWriter(raw, buffer_size=io.DEFAULT_BUFFER_SIZE if buffering < 0 else buffering)
        raise ValueError("Modalità makefile non supportata")

    def getpeercert(self, binary_form: bool = False) -> bytes | dict:
        return self._ssl.getpeercert(binary_form=binary_form)

    def settimeout(self, timeout: float | None) -> None:
        self._transport.settimeout(timeout)

    def setblocking(self, flag: bool) -> None:
        self._transport.setblocking(flag)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._transport.close()


def wrap_server(sock: socket.socket, cert_path: Path, key_path: Path) -> _BIOChannel:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(str(cert_path), str(key_path), password=host_key_password(key_path))
    sock.settimeout(30.0)
    channel = _BIOChannel(sock, ctx, server_side=True)
    sock.settimeout(120.0)
    return channel


def wrap_client(
    sock: socket.socket,
    remote_desk_id: str,
    trusted: dict[str, str],
    expected_spki: str,
    confirm_fingerprint: ConfirmFingerprint | None,
    timeout: float = 60.0,
) -> tuple[_BIOChannel | None, str]:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        sock.settimeout(timeout)
        tls_sock = _BIOChannel(sock, ctx, server_side=False)
        sock.settimeout(None)
    except (OSError, ssl.SSLError, TimeoutError, ValueError) as e:
        try:
            sock.close()
        except OSError:
            pass
        return None, f"TLS non riuscito: {e}"

    peer_cert = tls_sock.getpeercert(binary_form=True)
    if not peer_cert:
        tls_sock.close()
        return None, "Certificato peer assente."
    try:
        peer_spki = cert_spki_b64_from_der(peer_cert)
    except Exception:
        tls_sock.close()
        return None, "Certificato peer non valido."
    if not hmac.compare_digest(peer_spki, expected_spki):
        tls_sock.close()
        return None, "La chiave del peer non corrisponde alla directory del relay."

    fingerprint = cert_fingerprint_sha256(peer_cert)
    expected = trusted.get(remote_desk_id)
    if expected is not None:
        if not hmac.compare_digest(fingerprint, expected):
            tls_sock.close()
            return None, "Impronta diversa da quella salvata per questo ID."
        return tls_sock, ""

    if confirm_fingerprint is None:
        tls_sock.close()
        return None, "Impronta non confermata."

    result: list[bool | None] = [None]
    done = threading.Event()

    def run_confirm() -> None:
        try:
            result[0] = confirm_fingerprint(remote_desk_id, fingerprint)
        except Exception:
            result[0] = False
        finally:
            done.set()

    threading.Thread(target=run_confirm, daemon=True, name="tls-confirm").start()
    if not done.wait(timeout):
        tls_sock.close()
        return None, "Impronta non confermata."
    if not result[0]:
        tls_sock.close()
        return None, "Impronta non confermata."
    return tls_sock, ""
