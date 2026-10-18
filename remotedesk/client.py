"""Client HTTP per tab Connetti (stdlib)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


def normalize_base_url(address: str) -> str:
    addr = address.strip()
    if not addr:
        raise ValueError("Indirizzo vuoto")
    if not addr.startswith("http://") and not addr.startswith("https://"):
        addr = "http://" + addr
    parsed = urllib.parse.urlparse(addr)
    if not parsed.netloc:
        raise ValueError("Indirizzo non valido")
    return f"{parsed.scheme}://{parsed.netloc}"


def post_session(base_url: str, code: str, timeout: float = 65.0) -> tuple[int, bytes]:
    url = base_url.rstrip("/") + "/session"
    body = json.dumps({"code": code}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def get_frame(base_url: str, code: str, timeout: float = 10.0) -> tuple[int, bytes | None]:
    qs = urllib.parse.urlencode({"code": code})
    url = base_url.rstrip("/") + "/frame.jpg?" + qs
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 204):
            return e.code, None
        raise


def post_input(base_url: str, payload: dict, timeout: float = 5.0) -> int:
    url = base_url.rstrip("/") + "/input"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
