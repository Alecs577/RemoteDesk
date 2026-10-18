"""Small OS-backed secret storage helpers."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


_DPAPI_TAG = b"DPAPI1\0"
_PLAIN_TAG = b"LOCAL1\0"
_ENTROPY = b"RemoteDesk secret storage v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


if os.name == "nt":
    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes) -> tuple[object, object]:
    storage = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    blob = _DataBlob(len(data), ctypes.cast(storage, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, storage


def protect_secret(data: bytes) -> bytes:
    """Protect with current-user DPAPI on Windows; keep dev builds portable."""
    if os.name != "nt":
        return _PLAIN_TAG + data
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, source_storage = _blob(data)
    entropy, entropy_storage = _blob(_ENTROPY)
    result = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "RemoteDesk", ctypes.byref(entropy), None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result),
    ):
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        encrypted = ctypes.string_at(result.pbData, result.cbData)
        return _DPAPI_TAG + encrypted
    finally:
        kernel32.LocalFree(result.pbData)
        _ = source_storage, entropy_storage


def unprotect_secret(data: bytes) -> bytes:
    if data.startswith(_PLAIN_TAG):
        return data[len(_PLAIN_TAG):]
    if not data.startswith(_DPAPI_TAG):
        raise ValueError("Formato del segreto protetto non riconosciuto")
    if os.name != "nt":
        raise ValueError("Il segreto DPAPI può essere letto solo su Windows con lo stesso utente")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, source_storage = _blob(data[len(_DPAPI_TAG):])
    entropy, entropy_storage = _blob(_ENTROPY)
    result = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, ctypes.byref(entropy), None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result),
    ):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)
        _ = source_storage, entropy_storage
