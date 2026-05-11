"""
Outreach endpoints — templates, campaigns, queue.
"""

from typing import List, Optional

import db
from api.deps import get_user_id
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter()


# ── Templates ────────────────────────────────────────────────
@router.get("/templates")
def list_templates(user_id: str = Depends(get_user_id)):
    return db.list_templates()


class TemplateBody(BaseModel):
    name: str
    subject: str
    body: str


@router.post("/templates")
def create_template(body: TemplateBody, user_id: str = Depends(get_user_id)):
    tid = db.upsert_template(body.name, body.subject, body.body)
    return {"id": tid}


@router.put("/templates/{template_id}")
def update_template(template_id: int, body: TemplateBody, user_id: str = Depends(get_user_id)):
    db.upsert_template(body.name, body.subject, body.body, template_id=template_id)
    return {"ok": True}


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, user_id: str = Depends(get_user_id)):
    db.delete_template(template_id)
    return {"ok": True}


# ── Campaigns ────────────────────────────────────────────────
@router.get("/campaigns")
def list_campaigns(user_id: str = Depends(get_user_id)):
    return db.list_campaigns()


class StepSpec(BaseModel):
    template_id: int
    delay_days: int


class CampaignBody(BaseModel):
    name: str
    lead_ids: List[int]
    steps: List[StepSpec]
    notes: Optional[str] = None


@router.post("/campaigns")
def create_campaign(body: CampaignBody, user_id: str = Depends(get_user_id)):
    steps = [s.model_dump() for s in body.steps]
    cid = db.create_campaign(body.name, steps, body.lead_ids, notes=body.notes)
    return {"id": cid}


@router.patch("/campaigns/{campaign_id}/status")
def set_status(campaign_id: int, status: str, user_id: str = Depends(get_user_id)):
    db.set_campaign_status(campaign_id, status)
    return {"ok": True}


# ── Queue ────────────────────────────────────────────────────
@router.get("/queue")
def queue_summary(user_id: str = Depends(get_user_id)):
    return db.send_queue_summary()


@router.post("/send")
def trigger_send(dry_run: bool = False, user_id: str = Depends(get_user_id)):
    """Manually trigger the send queue (same as worker job_send)."""
    import os
    from outreach import SMTPConfig, process_queue

    smtp = SMTPConfig.from_env()
    if not smtp.host:
        raise HTTPException(status_code=400, detail="SMTP not configured")

    from_addr = os.getenv("SMTP_FROM") or smtp.username
    summary = process_queue(smtp, from_addr, limit=50, dry_run=dry_run)
    return summary
