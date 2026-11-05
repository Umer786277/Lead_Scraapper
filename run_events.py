"""Durable per-run scrape event log.

Events are queued in-process and written to Postgres by a background thread,
so emitting a log line never blocks the scraper. Because the events live in
the database (rather than an in-memory buffer), the Scrape UI can read them
regardless of which process ran the scrape or whether the API restarted.
"""

import logging
import queue
import threading
from typing import Optional

import db

log = logging.getLogger("run_events")

_QUEUE: "queue.Queue[tuple]" = queue.Queue(maxsize=5000)
_WRITER: Optional[threading.Thread] = None
_WRITER_LOCK = threading.Lock()
_MAX_BATCH = 100


def _drain(limit: int = _MAX_BATCH) -> list:
    batch = []
    while len(batch) < limit:
        try:
            batch.append(_QUEUE.get_nowait())
        except queue.Empty:
            break
    return batch


def flush(limit: int = 10_000) -> int:
    """Synchronously write queued events. Safe to call from any thread."""
    written = 0
    while written < limit:
        batch = _drain()
        if not batch:
            break
        try:
            db.insert_run_events(batch)
        except Exception:
            log.warning("failed to persist %s scrape events", len(batch), exc_info=True)
        written += len(batch)
    return written


def _writer_loop():
    while True:
        try:
            first = _QUEUE.get(timeout=1.0)
        except queue.Empty:
            continue
        batch = [first]
        batch.extend(_drain(_MAX_BATCH - 1))
        try:
            db.insert_run_events(batch)
        except Exception:
            log.warning("failed to persist %s scrape events", len(batch), exc_info=True)


def _ensure_writer():
    global _WRITER
    if _WRITER is not None and _WRITER.is_alive():
        return
    with _WRITER_LOCK:
        if _WRITER is None or not _WRITER.is_alive():
            _WRITER = threading.Thread(
                target=_writer_loop, name="scrape-event-writer", daemon=True
            )
            _WRITER.start()


def record(run_id, org_id, kind, message):
    """Queue one scrape event for persistence. Never raises."""
    if run_id is None:
        return
    _ensure_writer()
    try:
        _QUEUE.put_nowait((run_id, org_id, kind, message))
    except queue.Full:
        pass
