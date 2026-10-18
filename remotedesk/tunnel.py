"""Protocollo tunnel: uint32 BE length + type byte + payload."""

from __future__ import annotations

import json
import struct
from typing import BinaryIO

MAX_MESSAGE_LENGTH = 8_000_000

HELLO = 1
ACCEPT = 2
REJECT = 3
FRAME = 4
INPUT = 5
CHAT = 6
CLIP = 7
FILE_OFFER = 8
FILE_ACCEPT = 9
FILE_REJECT = 10
FILE_CHUNK = 11
SYSINFO = 12
ACTION = 13
DRAW = 14
AUDIO = 15
TUNNEL_OPEN = 16
TUNNEL_DATA = 17
TUNNEL_CLOSE = 18
FILE_LIST = 19
REC = 20
VIEW = 21
GRANTS = 22
AUTH_CHALLENGE = 23

MAX_CHAT = 4000
MAX_TUNNEL_DATA = 16384
MAX_CLIP = 100_000
MAX_FILE_SIZE = 8_388_608


class TunnelError(Exception):
    pass


def _read_exact(stream: BinaryIO, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            raise TunnelError("connessione chiusa")
        buf.extend(chunk)
    return bytes(buf)


def read_message(stream: BinaryIO) -> tuple[int, bytes]:
    header = _read_exact(stream, 4)
    (length,) = struct.unpack(">I", header)
    if length > MAX_MESSAGE_LENGTH:
        raise TunnelError("messaggio troppo grande")
    if length == 0:
        raise TunnelError("messaggio vuoto")
    body = _read_exact(stream, length)
    msg_type = body[0]
    payload = body[1:]
    return msg_type, payload


def write_message(stream: BinaryIO, msg_type: int, payload: bytes = b"") -> None:
    body = bytes([msg_type]) + payload
    if len(body) > MAX_MESSAGE_LENGTH:
        raise TunnelError("messaggio troppo grande")
    stream.write(struct.pack(">I", len(body)))
    stream.write(body)
    if hasattr(stream, "flush"):
        stream.flush()


def encode_chat(text: str) -> bytes:
    if len(text) > MAX_CHAT:
        text = text[:MAX_CHAT]
    return text.encode("utf-8")


def encode_clip(text: str) -> bytes:
    if len(text) > MAX_CLIP:
        text = text[:MAX_CLIP]
    return text.encode("utf-8")


def encode_input(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def decode_input(payload: bytes) -> dict:
    return json.loads(payload.decode("utf-8"))


def encode_view(quality: str, monitor: int) -> bytes:
    return json.dumps({"quality": quality, "monitor": monitor}, separators=(",", ":")).encode("utf-8")


def decode_view(payload: bytes) -> tuple[str, int]:
    quality = "balanced"
    monitor = -1
    if not payload:
        return quality, monitor
    try:
        data = json.loads(payload.decode("utf-8"))
        if isinstance(data, dict):
            q = str(data.get("quality", "balanced")).strip()
            if q:
                quality = q
            try:
                monitor = int(data.get("monitor", -1))
            except (TypeError, ValueError):
                monitor = -1
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return quality, monitor


def encode_file_offer(name: str, size: int) -> bytes:
    return json.dumps({"name": name, "size": size}, separators=(",", ":")).encode("utf-8")


def decode_file_offer(payload: bytes) -> tuple[str, int]:
    data = json.loads(payload.decode("utf-8"))
    name = str(data.get("name", ""))
    size = int(data.get("size", 0))
    return name, size
