"""
System endpoints — worker health, queue depth, live logs, job triggers.
"""

import json
import os
from pathlib import Path

import db
from api.deps import get_user_id
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter()

STATE_FILE = Path(__file__).resolve().parent.parent.parent / "worker_state.json"

VALID_JOBS = {"enrich", "send", "inbox", "rotation", "calls"}


@router.get("/status")
def status(user_id: str = Depends(get_user_id)):
    """Return worker state + queue depth + env config checklist."""
    # Prefer in-process state (merged worker), fall back to DB, then file
    worker_state: dict = {}
    try:
        from api.worker_runner import get_state, is_running
        state = get_state()
        if state and state.get("started_at"):
            worker_state = dict(state)
            worker_state["scheduler_running"] = is_running()
    except Exception:
        pass

    if not worker_state:
        worker_state = db.worker_heartbeat_read()

    if not worker_state and STATE_FILE.exists():
        try:
            worker_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    try:
        queue = db.send_queue_summary()
    except Exception:
        queue = {}

    config = {
        "database":  bool(os.getenv("DATABASE_URL")),
        "smtp":      bool(os.getenv("SMTP_HOST")),
        "imap":      bool(os.getenv("IMAP_HOST")),
        "openai":    bool(os.getenv("OPENAI_API_KEY")),
        "supabase":  bool(os.getenv("SUPABASE_JWT_SECRET")),
    }

    return {
        "worker": worker_state,
        "queue":  queue,
        "config": config,
    }


@router.get("/logs")
def get_logs(last_n: int = 200, user_id: str = Depends(get_user_id)):
    """Return the last N lines from the in-process log buffer."""
    try:
        from api.worker_runner import log_buffer
        return {"lines": log_buffer.lines(last_n)}
    except Exception as e:
        return {"lines": [f"[error loading logs] {e}"]}


@router.post("/jobs/{job_id}/trigger")
def trigger_job(job_id: str, user_id: str = Depends(get_user_id)):
    """Immediately trigger a scheduled job by ID."""
    if job_id not in VALID_JOBS:
        raise HTTPException(status_code=400, detail=f"Unknown job '{job_id}'. Valid: {sorted(VALID_JOBS)}")
    try:
        from api.worker_runner import trigger_job as _trigger
        ok = _trigger(job_id)
        return {"ok": ok, "job_id": job_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}
