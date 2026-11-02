"""
Runs all background jobs inside the FastAPI process via APScheduler BackgroundScheduler.
Exposes a shared LogBuffer so GET /api/system/logs can serve recent lines.

Note: Render free-tier web services sleep after 15 min of inactivity.
Keep UptimeRobot (or similar) pinging / every 5 min so jobs never stop.
"""

import collections
import logging
import os
import threading
from datetime import datetime, timezone, timedelta


# ── In-memory log buffer ──────────────────────────────────────
class LogBuffer:
    def __init__(self, maxlen: int = 500):
        self._buf: collections.deque = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, line: str):
        with self._lock:
            self._buf.append(line)

    def lines(self, last_n: int = 200) -> list:
        with self._lock:
            buf = list(self._buf)
        return buf[-last_n:]


log_buffer = LogBuffer()


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        try:
            log_buffer.append(self.format(record))
        except Exception:
            pass


_buf_handler = _BufferHandler()
_buf_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
)
logging.getLogger().addHandler(_buf_handler)
logging.getLogger().setLevel(logging.INFO)

# ── Scheduler state ───────────────────────────────────────────
_scheduler = None
_state: dict = {}
_started = False
_lock = threading.Lock()

log = logging.getLogger("worker_runner")


def _run_coordinated(job_id: str, callback):
    from coordination import run_scheduled_job

    return run_scheduled_job(job_id, callback)


def start():
    global _scheduler, _state, _started

    with _lock:
        if _started:
            return
        _started = True

    log.info("Worker runner: initialising background scheduler")

    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import db
        db.init_db()

        from worker import (
            job_enrich, job_send, job_inbox, job_scrape_rotation_all, job_calls,
            job_booking_replies,
            _now_str, _write_state, _ensure_auto_campaign,
        )
        from api.deletion_worker import process_deletion_jobs

        campaign_id, steps = _ensure_auto_campaign()

        _state.update({
            "pid":              os.getpid(),
            "started_at":       _now_str(),
            "auto_campaign_id": campaign_id,
            "jobs": {
                "enrich":   {},
                "send":     {},
                "inbox":    {},
                "rotation": {},
                "calls":    {},
                "booking_replies": {},
            },
        })
        _write_state(_state)

        from apscheduler.schedulers.background import BackgroundScheduler

        now_utc = datetime.now(timezone.utc)
        _scheduler = BackgroundScheduler(timezone="UTC")

        _scheduler.add_job(
            lambda: _run_coordinated(
                "enrich", lambda: job_enrich(_state, campaign_id, steps)
            ),
            "interval", minutes=5, id="enrich", max_instances=1,
            next_run_time=now_utc,
        )
        _scheduler.add_job(
            lambda: _run_coordinated("send", lambda: job_send(_state)),
            "interval", minutes=5, id="send", max_instances=1,
            next_run_time=now_utc + timedelta(seconds=30),
        )
        _scheduler.add_job(
            lambda: _run_coordinated("inbox", lambda: job_inbox(_state)),
            "interval", minutes=2, id="inbox", max_instances=1,
            next_run_time=now_utc + timedelta(minutes=1),
        )
        _scheduler.add_job(
            lambda: _run_coordinated(
                "rotation", lambda: job_scrape_rotation_all(_state)
            ),
            # 7-10 min (8.5 min base ± 90s jitter) — was hourly. Fine for local
            # runs; if this ever runs on a resource-capped host again, or Google
            # Maps starts throwing CAPTCHAs, dial this back up.
            "interval", minutes=8.5, jitter=90, id="rotation", max_instances=1,
            next_run_time=now_utc + timedelta(minutes=5),
        )
        _scheduler.add_job(
            lambda: _run_coordinated("calls", lambda: job_calls(_state)),
            "interval", minutes=5, id="calls", max_instances=1,
            next_run_time=now_utc + timedelta(seconds=45),
        )
        _scheduler.add_job(
            lambda: _run_coordinated(
                "booking_replies", lambda: job_booking_replies(_state)
            ),
            "interval", minutes=5, id="booking_replies", max_instances=1,
            next_run_time=now_utc + timedelta(minutes=2),
        )
        _scheduler.add_job(
            process_deletion_jobs,
            "interval", seconds=15, id="deletion_queue", max_instances=1,
            next_run_time=now_utc,
        )

        _scheduler.start()
        log.info(f"Worker runner: scheduler started (campaign_id={campaign_id}, pid={os.getpid()})")

    except Exception as e:
        log.error(f"Worker runner failed to start: {e}", exc_info=True)


def stop():
    global _scheduler, _started
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _started = False


def trigger_job(job_id: str) -> bool:
    if not _scheduler or not _scheduler.running:
        return False
    job = _scheduler.get_job(job_id)
    if not job:
        return False
    job.modify(next_run_time=datetime.now(timezone.utc))
    return True


def get_state() -> dict:
    return _state


def is_running() -> bool:
    return bool(_scheduler and _scheduler.running)
