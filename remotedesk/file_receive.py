"""Receive a file without exposing a partial download at its final path."""

from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path

from remotedesk.tunnel import MAX_FILE_SIZE


class FileReceiver:
    def __init__(self, path: Path, size: int) -> None:
        if not 0 < size <= MAX_FILE_SIZE:
            raise ValueError("invalid file size")
        self.path = path
        self._size = size
        self._received = 0
        self._done = False
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._temp = path.with_name(path.name + "." + secrets.token_hex(12) + ".part")
        fd = os.open(self._temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._file = os.fdopen(fd, "wb")

    def write(self, chunk: bytes) -> bool:
        """Write one protocol chunk; return True after an atomic completion."""
        with self._lock:
            if self._done or self._file is None:
                return self._done
            if not chunk or len(chunk) > self._size - self._received:
                raise ValueError("invalid file chunk")
            self._file.write(chunk)
            self._received += len(chunk)
            if self._received == self._size:
                self._file.flush()
                os.fsync(self._file.fileno())
                self._file.close()
                self._file = None
                os.replace(self._temp, self.path)
                self._done = True
            return self._done

    def abort(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None
            if not self._done:
                self._temp.unlink(missing_ok=True)
