"""Cattura schermo in background mentre la condivisione è attiva."""

from __future__ import annotations

import io
import sys
import threading
import time
from typing import Callable

from PIL import Image, ImageGrab

from remotedesk.state import ShareState

QUALITY_PRESETS: dict[str, tuple[int, int, float]] = {
    "fluid": (960, 40, 1.0 / 15.0),
    "balanced": (1280, 60, 1.0 / 8.0),
    "sharp": (1920, 80, 1.0 / 6.0),
}

_dpi_aware_done = False


def quality_capture_params(quality: str) -> tuple[int, int, float]:
    """Ritorna (lato lungo max, qualità JPEG, intervallo secondi)."""
    return QUALITY_PRESETS.get(quality, QUALITY_PRESETS["balanced"])


def enable_per_monitor_dpi() -> None:
    global _dpi_aware_done
    if _dpi_aware_done:
        return
    _dpi_aware_done = True
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (OSError, AttributeError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (OSError, AttributeError):
        pass


def _virtual_screen_metrics() -> tuple[int, int, int, int]:
    import ctypes

    user32 = ctypes.windll.user32
    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    width = user32.GetSystemMetrics(78)
    height = user32.GetSystemMetrics(79)
    return left, top, width, height


def list_monitors() -> list[tuple[int, int, int, int]]:
    """EnumDisplayMonitors: (left, top, width, height)."""
    if sys.platform != "win32":
        return []
    import ctypes
    import ctypes.wintypes as wintypes

    user32 = ctypes.windll.user32
    rects: list[tuple[int, int, int, int]] = []

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.RECT),
        ctypes.c_long,
    )

    @MONITORENUMPROC
    def _callback(_hmon, _hdc, lprc, _data) -> int:
        r = lprc.contents
        w = int(r.right - r.left)
        h = int(r.bottom - r.top)
        if w > 0 and h > 0:
            rects.append((int(r.left), int(r.top), w, h))
        return 1

    try:
        user32.EnumDisplayMonitors(None, None, _callback, 0)
    except (OSError, AttributeError):
        pass
    if rects:
        return rects
    left, top, width, height = _virtual_screen_metrics()
    if width > 0 and height > 0:
        return [(left, top, width, height)]
    return []


def capture_rect_for_monitor(monitor_index: int) -> tuple[int, int, int, int]:
    """Ritorna (left, top, width, height) per cattura e mappatura input."""
    monitors = list_monitors()
    if monitor_index >= 0 and monitor_index < len(monitors):
        return monitors[monitor_index]
    left, top, width, height = _virtual_screen_metrics()
    return left, top, width, height


def _scale_and_encode(img: Image.Image, max_long_side: int, jpeg_quality: int) -> tuple[bytes, int, int]:
    w, h = img.size
    long_side = max(w, h)
    if long_side > max_long_side:
        scale = max_long_side / long_side
        nw = max(1, int(w * scale))
        nh = max(1, int(h * scale))
        img = img.resize((nw, nh), Image.Resampling.BILINEAR)
        fw, fh = nw, nh
    else:
        fw, fh = w, h

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
    return buf.getvalue(), fw, fh


def capture_loop(state: ShareState, stop_event: threading.Event) -> None:
    enable_per_monitor_dpi()
    while not stop_event.is_set():
        with state.lock:
            sharing = state.sharing
        if not sharing:
            time.sleep(0.05)
            continue

        quality, monitor_index = state.get_capture_prefs()
        max_long, jpeg_q, target_interval = quality_capture_params(quality)
        sl, st, sw, sh = capture_rect_for_monitor(monitor_index)
        bbox = (sl, st, sl + sw, st + sh)
        t0 = time.monotonic()
        all_screens = monitor_index < 0
        img = ImageGrab.grab(bbox=bbox, all_screens=all_screens)
        jpeg, fw, fh = _scale_and_encode(img, max_long, jpeg_q)
        state.update_frame(jpeg, fw, fh, sl, st, sw, sh)

        elapsed = time.monotonic() - t0
        sleep_for = target_interval - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)


class CaptureThread:
    def __init__(self, state: ShareState) -> None:
        self._state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=capture_loop, args=(self._state, self._stop), daemon=True, name="capture"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
