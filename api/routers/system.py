"""
System endpoints — worker heartbeat, queue depth, config status.
"""

import json
import os
from pathlib import Path

import db
from api.deps import get_user_id
from fastapi import APIRouter, Depends

router = APIRouter()

STATE_FILE = Path(__file__).resolve().parent.parent.parent / "worker_state.json"


@router.get("/status")
def status(user_id: str = Depends(get_user_id)):
    """Return worker state + queue depth + env config checklist."""
    worker_state = {}
    if STATE_FILE.exists():
        try:
            worker_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    queue = db.send_queue_summary()

    config = {
        "database":  bool(os.getenv("DATABASE_URL")),
        "smtp":      bool(os.getenv("SMTP_HOST")),
        "imap":      bool(os.getenv("IMAP_HOST")),
        "openai":    bool(os.getenv("OPENAI_API_KEY")),
        "supabase":  bool(os.getenv("SUPABASE_JWT_SECRET")),
    }

    return {
        "worker":   worker_state,
        "queue":    queue,
        "config":   config,
    }
