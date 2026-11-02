"""Small cross-platform advisory file lock for persistence critical sections."""

from __future__ import annotations

import os
import errno
import hashlib
import time
from pathlib import Path
from typing import BinaryIO
from core.diagnostics import current_diagnostics


if os.name == "nt":
    import msvcrt

    def _lock(handle: BinaryIO) -> None:
        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError as error:
                # LK_LOCK's finite Windows contention retries do not establish
                # owner closure or a persistence/business failure. Keep waiting
                # on this exact OS-owned range; propagate every other error.
                if error.errno != errno.EDEADLK:
                    raise
                time.sleep(0.05)

    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

elif os.name == "posix":
    import fcntl

    def _lock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _unlock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

else:

    def _lock(_handle: BinaryIO) -> None:
        raise RuntimeError("inter-process file locking is unsupported")

    def _unlock(_handle: BinaryIO) -> None:
        raise RuntimeError("inter-process file locking is unsupported")


class InterProcessFileLock:
    """Hold an OS-owned lock on one byte of a stable coordination file.

    The file may remain after release, but its presence never represents lock
    ownership.  Ownership exists only while the operating system holds the
    advisory byte/range lock, which is released automatically on process exit.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("inter-process file lock is not reentrant")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b", buffering=0)
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            diagnostics = current_diagnostics()
            lock_ref = hashlib.sha256(str(self.path).encode()).hexdigest() if diagnostics.enabled else "disabled"
            with diagnostics.span(lock_ref, "LOCK_WAIT"):
                _lock(handle)
        except BaseException:
            handle.close()
            raise
        self._handle = handle
        self._diagnostic_hold = (diagnostics, lock_ref, time.monotonic_ns()) if diagnostics.enabled else None

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            _unlock(handle)
        finally:
            self._handle = None
            handle.close()
            observed = getattr(self, "_diagnostic_hold", None)
            if observed is not None:
                diagnostics, lock_ref, began = observed
                diagnostics.emit({"event_type": "LOCK_HELD_RESULT", "operation_ref": lock_ref, "category": "LOCK_HELD", "begin_ns": began, "end_ns": time.monotonic_ns()})
            self._diagnostic_hold = None

    def __enter__(self) -> InterProcessFileLock:
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


__all__ = ["InterProcessFileLock"]
