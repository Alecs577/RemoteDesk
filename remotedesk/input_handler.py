"""Invio input mouse/tastiera via SendInput (Windows)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import math

from remotedesk.state import ShareState

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SendInput.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


def _send(inputs: list[INPUT]) -> None:
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        raise OSError(ctypes.get_last_error(),
                      f"Windows ha rifiutato il controllo ({sent}/{n} eventi). "
                      "Controllare privilegi e desktop protetto sul PC locale.")


def _virtual_screen() -> tuple[int, int, int, int]:
    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    width = user32.GetSystemMetrics(78)
    height = user32.GetSystemMetrics(79)
    return left, top, width, height


def _frame_to_screen(
    x: float, y: float, fw: int, fh: int, sl: int, st: int, sw: int, sh: int
) -> tuple[int, int]:
    if fw <= 0 or fh <= 0:
        left, top, width, height = _virtual_screen()
        return left + width // 2, top + height // 2
    sx = sl + int(x * sw / fw)
    sy = st + int(y * sh / fh)
    left, top, width, height = _virtual_screen()
    sx = max(left, min(left + width - 1, sx))
    sy = max(top, min(top + height - 1, sy))
    return sx, sy


def _to_absolute(x: int, y: int) -> tuple[int, int]:
    left, top, width, height = _virtual_screen()
    # Normalized 0..65535 across virtual desktop
    nx = int((x - left) * 65535 / max(1, width - 1))
    ny = int((y - top) * 65535 / max(1, height - 1))
    nx = max(0, min(65535, nx))
    ny = max(0, min(65535, ny))
    return nx, ny


def _button_flags(button: str, down: bool) -> int:
    b = (button or "left").lower()
    if b == "right":
        return MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP
    if b == "middle":
        return MOUSEEVENTF_MIDDLEDOWN if down else MOUSEEVENTF_MIDDLEUP
    return MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP


def _move_absolute(x: int, y: int) -> INPUT:
    ax, ay = _to_absolute(x, y)
    mi = MOUSEINPUT(
        dx=ax,
        dy=ay,
        mouseData=0,
        dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        time=0,
        dwExtraInfo=ULONG_PTR(0),
    )
    inp = INPUT(type=INPUT_MOUSE)
    inp.union.mi = mi
    return inp


def _key_vk(key: str) -> int | None:
    if not key:
        return None
    k = key.strip()
    if len(k) == 1:
        vk = user32.VkKeyScanW(ord(k))
        if vk != -1:
            return vk & 0xFF
    special = {
        "Enter": 0x0D,
        "Escape": 0x1B,
        "Backspace": 0x08,
        "Tab": 0x09,
        "Space": 0x20,
        "ArrowUp": 0x26,
        "ArrowDown": 0x28,
        "ArrowLeft": 0x25,
        "ArrowRight": 0x27,
        "Delete": 0x2E,
        "Home": 0x24,
        "End": 0x23,
        "PageUp": 0x21,
        "PageDown": 0x22,
    }
    return special.get(k)


def _wheel_scroll(delta: int) -> INPUT:
    mi = MOUSEINPUT(
        dx=0,
        dy=0,
        mouseData=ctypes.c_uint(delta).value,
        dwFlags=MOUSEEVENTF_WHEEL,
        time=0,
        dwExtraInfo=ULONG_PTR(0),
    )
    inp = INPUT(type=INPUT_MOUSE)
    inp.union.mi = mi
    return inp


def handle_input(state: ShareState, payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    kind = payload.get("kind")
    if kind not in ("move", "down", "up", "click", "key", "wheel"):
        return

    fw, fh, sl, st, sw, sh = state.get_mapping()
    x = float(payload.get("x") or 0)
    y = float(payload.get("y") or 0)
    if not math.isfinite(x) or not math.isfinite(y):
        return
    button = str(payload.get("button") or "left")

    if kind == "wheel":
        try:
            delta = int(payload.get("delta", 0))
        except (TypeError, ValueError):
            return
        if delta == 0:
            return
        sx, sy = _frame_to_screen(x, y, fw, fh, sl, st, sw, sh)
        _send([_move_absolute(sx, sy), _wheel_scroll(delta)])
        return

    if kind in ("move", "down", "up", "click"):
        sx, sy = _frame_to_screen(x, y, fw, fh, sl, st, sw, sh)
        if kind == "move":
            _send([_move_absolute(sx, sy)])
            return
        if kind == "down":
            _send([_move_absolute(sx, sy), _mouse_button(button, True)])
            return
        if kind == "up":
            _send([_mouse_button(button, False)])
            return
        if kind == "click":
            _send(
                [
                    _move_absolute(sx, sy),
                    _mouse_button(button, True),
                    _mouse_button(button, False),
                ]
            )
            return

    if kind == "key":
        key = payload.get("key")
        if key is None:
            return
        key_str = str(key)
        if len(key_str) == 1:
            _send_unicode_char(key_str)
            return
        vk = _key_vk(key_str)
        if vk is None:
            return
        _send([_key_event(vk, False), _key_event(vk, True)])


def _mouse_button(button: str, down: bool) -> INPUT:
    mi = MOUSEINPUT(
        dx=0,
        dy=0,
        mouseData=0,
        dwFlags=_button_flags(button, down),
        time=0,
        dwExtraInfo=ULONG_PTR(0),
    )
    inp = INPUT(type=INPUT_MOUSE)
    inp.union.mi = mi
    return inp


def _key_event(vk: int, key_up: bool) -> INPUT:
    ki = KEYBDINPUT(
        wVk=vk,
        wScan=0,
        dwFlags=KEYEVENTF_KEYUP if key_up else 0,
        time=0,
        dwExtraInfo=ULONG_PTR(0),
    )
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.union.ki = ki
    return inp


def _send_unicode_char(ch: str) -> None:
    code = ord(ch)
    down = KEYBDINPUT(
        wVk=0,
        wScan=code,
        dwFlags=KEYEVENTF_UNICODE,
        time=0,
        dwExtraInfo=ULONG_PTR(0),
    )
    up = KEYBDINPUT(
        wVk=0,
        wScan=code,
        dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
        time=0,
        dwExtraInfo=ULONG_PTR(0),
    )
    i1 = INPUT(type=INPUT_KEYBOARD)
    i1.union.ki = down
    i2 = INPUT(type=INPUT_KEYBOARD)
    i2.union.ki = up
    _send([i1, i2])
