"""Replies worker: polls IMAP for lead replies and sends deferred booking invites.

Runs ONLY the reply-path jobs in a dedicated process so reply latency never
depends on browser-bound jobs (enrich, rotation) or the send-queue job in the
main worker.

  inbox            → every 2 min
  booking_replies  → every 5 min

Imports the shared job implementations from worker.py so behavior remains
consistent with the main worker.
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Load .env BEFORE any module that calls os.getenv at import time.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Windows asyncio fix — harmless here, kept so this module and worker.py
# behave identically on win32.
if sys.platform == "win32":
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

from apscheduler.schedulers.blocking import BlockingScheduler

import db
from coordination import run_scheduled_job
from worker import _now_str, _write_state, job_booking_replies, job_inbox

REPLIES_STATE_FILE = Path(__file__).resolve().parent / "worker_replies_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("worker.replies")


def main():
    log.info("Replies worker starting…")
    db.init_db()

    state: dict = {
        "pid":        os.getpid(),
        "started_at": _now_str(),
        "state_file": str(REPLIES_STATE_FILE),
        "process":    "replies",
        "jobs": {
            "inbox":           {},
            "booking_replies": {},
        },
    }
    _write_state(state)

    now_utc = datetime.now(timezone.utc)
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        lambda: run_scheduled_job("inbox", lambda: job_inbox(state)),
        "interval", minutes=2, id="inbox", max_instances=1,
        next_run_time=now_utc,                          # poll immediately on start
    )
    scheduler.add_job(
        lambda: run_scheduled_job(
            "booking_replies", lambda: job_booking_replies(state)
        ),
        "interval", minutes=5, id="booking_replies", max_instances=1,
        next_run_time=now_utc + timedelta(minutes=1),
        jitter=30,
    )

    log.info(
        "Replies worker running. Press Ctrl+C to stop.\n"
        "  inbox           → every 2 min\n"
        "  booking_replies → every 5 min\n"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Replies worker stopped.")
    finally:
        state["stopped_at"] = _now_str()
        _write_state(state)


if __name__ == "__main__":
    main()