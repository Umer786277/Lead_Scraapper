"""Shared cross-process browser locks, optionally scoped to an organization."""

import os
import sys
import threading
import time
import hashlib
from functools import lru_cache
from pathlib import Path

_LOCK_PATH = Path(__file__).resolve().parent / ".browser.lock"


class BrowserFileLock:
    """Cross-process, thread-reentrant, optionally non-blocking file lock."""

    def __init__(self, path=None, timeout: float | None = None, lease_key=None):
        self._path = str(path or os.getenv("BROWSER_LOCK_FILE") or _LOCK_PATH)
        self._timeout = timeout  # None = block until acquired
        self._lease_key = lease_key
        self._fh = None
        self._owner = (None, None)  # (pid, thread_id)
        self._depth = 0
        self._lease = None

    def _ensure_open(self):
        if self._fh is None:
            self._fh = open(self._path, "a+b")
            # Check size via the path, NOT by reading the handle: on Windows
            # reading a locked byte raises PermissionError while the lock is held.
            if os.path.getsize(self._path) == 0:
                self._fh.seek(0)
                self._fh.write(b"0")
                self._fh.flush()

    def _try_lock(self) -> bool:
        try:
            self._fh.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(self):
        try:
            self._fh.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    def acquire(self, blocking: bool = True) -> bool:
        current = (os.getpid(), threading.get_ident())
        if current == self._owner:
            self._depth += 1
            return True

        if self._lease_key:
            from coordination import DistributedLease

            lease = DistributedLease(self._lease_key)
            if not lease.acquire(blocking=blocking, timeout=self._timeout):
                return False
            self._lease = lease

        try:
            self._ensure_open()
        except Exception:
            self._release_lease()
            raise
        deadline = None if self._timeout is None else time.monotonic() + self._timeout
        while True:
            if self._try_lock():
                self._owner = current
                self._depth = 1
                return True
            if not blocking:
                self._close_file()
                self._release_lease()
                return False
            if deadline is not None and time.monotonic() >= deadline:
                self._close_file()
                self._release_lease()
                return False
            time.sleep(0.25)

    def _close_file(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def _release_lease(self):
        if self._lease:
            self._lease.release()
            self._lease = None

    def release(self):
        if self._depth > 0:
            self._depth -= 1
        if self._depth == 0 and self._owner[0] is not None:
            self._unlock()
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
            self._owner = (None, None)
            self._release_lease()

    def __enter__(self):
        self.acquire(blocking=True)
        return self

    def __exit__(self, *exc):
        self.release()


browser_lock = BrowserFileLock()


@lru_cache(maxsize=256)
def browser_lock_for(org_id):
    """Return a reentrant browser lock isolated to one organization."""
    import db

    normalized_org_id = str(org_id or db.DEFAULT_ORG_ID)
    digest = hashlib.sha256(normalized_org_id.encode("utf-8")).hexdigest()[:24]
    path = _LOCK_PATH.with_name(f".browser-{digest}.lock")
    return BrowserFileLock(path=path, lease_key=f"browser:{normalized_org_id}")