"""
System endpoints — worker health, queue depth, live logs, job triggers.
"""

import os

import db
from api.deps import get_org_id, require_org_admin
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter()

VALID_JOBS = {"enrich", "send", "inbox", "rotation", "calls"}


class PauseBody(BaseModel):
    paused: bool


@router.get("/pause")
def get_pause(org_id: str = Depends(get_org_id)):
    """Outreach kill-switch state for the active organization."""
    return {"paused": db.is_outreach_paused(org_id=org_id)}


@router.post("/pause")
def set_pause(body: PauseBody, org_id: str = Depends(require_org_admin)):
    """Pause or resume automated + manual email sending with one toggle."""
    db.set_setting("outreach_paused", "1" if body.paused else "0", org_id=org_id)
    return {"paused": body.paused}


@router.get("/status")
def status(org_id: str = Depends(get_org_id)):
    """Return tenant-specific queue/config status and worker liveness."""
    scheduler_running = False
    try:
        from api.worker_runner import is_running
        scheduler_running = is_running()
    except Exception:
        pass

    try:
        queue = db.send_queue_summary(org_id=org_id)
    except Exception:
        queue = {}

    accounts = db.list_email_accounts(org_id=org_id)
    config = {
        "database":  bool(os.getenv("DATABASE_URL")),
        "smtp":      any(a.get("status") == "active" for a in accounts),
        "imap":      any(a.get("status") == "active" and a.get("imap_host") for a in accounts),
        "gemini":    bool(os.getenv("GOOGLE_AI_STUDIO_API_KEY")),
        "supabase":  bool(
            os.getenv("SUPABASE_JWT_SECRET")
            or os.getenv("SUPABASE_URL")
            or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
        ),
        "vapi":      bool(os.getenv("VAPI_API_KEY")) and bool(os.getenv("VAPI_PHONE_NUMBER_ID")),
        "booking_link": bool(os.getenv("BOOKING_LINK")),
    }

    return {
        "worker": {"scheduler_running": scheduler_running},
        "queue":  queue,
        "config": config,
    }


@router.get("/logs")
def get_logs(last_n: int = 200, org_id: str = Depends(get_org_id)):
    """Global worker logs are intentionally hidden from tenant-scoped users."""
    return {
        "lines": [],
        "message": "Worker logs are shared across organizations and are unavailable here.",
    }


@router.post("/jobs/{job_id}/trigger")
def trigger_job(job_id: str, org_id: str = Depends(get_org_id)):
    """Reject tenant-scoped requests for jobs that process all organizations."""
    if job_id not in VALID_JOBS:
        raise HTTPException(status_code=400, detail=f"Unknown job '{job_id}'. Valid: {sorted(VALID_JOBS)}")
    raise HTTPException(
        status_code=403,
        detail="Worker jobs process all organizations and cannot be triggered from a tenant-scoped session.",
    )
