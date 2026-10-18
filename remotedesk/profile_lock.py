"""Prevent two client processes or a client and a restore from sharing an identity."""
from contextlib import contextmanager
import os

from remotedesk.config import config_dir


@contextmanager
def profile_lock():
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".profile.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Il profilo RemoteDesk è già in uso: chiudere l'altra istanza prima di procedere.") from exc
        locked = True
        yield directory
    finally:
        if locked:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        # Keep the file: unlinking while another process opens it can split the lock inode.
