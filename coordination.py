"""PostgreSQL-backed renewable leases for coordinating multiple app instances."""

import logging
import threading
import time
import uuid
from contextlib import contextmanager

import db

log = logging.getLogger(__name__)


class DistributedLease:
    def __init__(self, lease_key: str, ttl_seconds: int = 180):
        self.lease_key = lease_key
        self.ttl_seconds = ttl_seconds
        self.owner_token = str(uuid.uuid4())
        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None
        self.lost = threading.Event()

    def acquire(self, blocking: bool = False, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if db.try_acquire_lease(
                self.lease_key, self.owner_token, self.ttl_seconds
            ):
                self._heartbeat = threading.Thread(
                    target=self._renew_loop,
                    name=f"lease-{self.lease_key[:32]}",
                    daemon=True,
                )
                self._heartbeat.start()
                return True
            if not blocking or (deadline is not None and time.monotonic() >= deadline):
                return False
            time.sleep(0.5)

    def _renew_loop(self):
        interval = max(1, self.ttl_seconds // 3)
        while not self._stop.wait(interval):
            try:
                if not db.renew_lease(
                    self.lease_key, self.owner_token, self.ttl_seconds
                ):
                    self.lost.set()
                    log.error("Lost distributed lease %s", self.lease_key)
                    return
            except Exception:
                self.lost.set()
                log.exception("Failed to renew distributed lease %s", self.lease_key)
                return

    def release(self):
        self._stop.set()
        if self._heartbeat and self._heartbeat is not threading.current_thread():
            self._heartbeat.join(timeout=2)
        db.release_lease(self.lease_key, self.owner_token)


@contextmanager
def hold_lease(
    lease_key: str,
    *,
    blocking: bool = False,
    timeout: float | None = None,
    ttl_seconds: int = 180,
):
    lease = DistributedLease(lease_key, ttl_seconds)
    acquired = lease.acquire(blocking=blocking, timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired:
            lease.release()


def run_scheduled_job(job_id: str, callback):
    with hold_lease(f"scheduled-job:{job_id}", ttl_seconds=300) as acquired:
        if not acquired:
            log.info("Scheduled job %s is already running on another instance", job_id)
            return False
        callback()
        return True
