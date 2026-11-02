"""
Outreach endpoints — templates, campaigns, queue, and negotiation threads
(human-in-the-loop reply drafting + sending).
"""

from typing import List, Optional

import db
from api.deps import get_org_id, require_org_admin
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


# ── Templates ────────────────────────────────────────────────
@router.get("/templates")
def list_templates(org_id: str = Depends(get_org_id)):
    return db.list_templates(org_id=org_id)


class TemplateBody(BaseModel):
    name: str
    subject: str
    body: str


@router.post("/templates")
def create_template(body: TemplateBody, org_id: str = Depends(get_org_id)):
    tid = db.upsert_template(body.name, body.subject, body.body, org_id=org_id)
    return {"id": tid}


@router.put("/templates/{template_id}")
def update_template(template_id: int, body: TemplateBody, org_id: str = Depends(get_org_id)):
    db.upsert_template(body.name, body.subject, body.body, template_id=template_id, org_id=org_id)
    return {"ok": True}


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, org_id: str = Depends(get_org_id)):
    db.delete_template(template_id, org_id=org_id)
    return {"ok": True}


# ── Campaigns ────────────────────────────────────────────────
@router.get("/campaigns")
def list_campaigns(org_id: str = Depends(get_org_id)):
    return db.list_campaigns(org_id=org_id)


class StepSpec(BaseModel):
    template_id: int
    delay_days: int


class CampaignBody(BaseModel):
    name: str
    lead_ids: List[int]
    steps: List[StepSpec]
    notes: Optional[str] = None


@router.post("/campaigns")
def create_campaign(body: CampaignBody, org_id: str = Depends(get_org_id)):
    steps = [s.model_dump() for s in body.steps]
    cid = db.create_campaign(body.name, steps, body.lead_ids, notes=body.notes, org_id=org_id)
    return {"id": cid}


@router.patch("/campaigns/{campaign_id}/status")
def set_status(campaign_id: int, status: str, org_id: str = Depends(get_org_id)):
    db.set_campaign_status(campaign_id, status, org_id=org_id)
    return {"ok": True}


# ── Sender accounts (multi-SMTP) ─────────────────────────────
class AccountCreate(BaseModel):
    from_email: str
    smtp_host: str
    label: Optional[str] = None
    from_name: Optional[str] = None
    reply_to: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True
    imap_host: Optional[str] = None
    imap_port: int = 993
    imap_user: Optional[str] = None
    imap_password: Optional[str] = None
    imap_mailbox: str = "INBOX"
    daily_limit: int = 50
    hourly_limit: int = 10
    warmup_days: int = 0


class AccountUpdate(BaseModel):
    from_email: Optional[str] = None
    smtp_host: Optional[str] = None
    label: Optional[str] = None
    from_name: Optional[str] = None
    reply_to: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: Optional[bool] = None
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_user: Optional[str] = None
    imap_password: Optional[str] = None
    imap_mailbox: Optional[str] = None
    daily_limit: Optional[int] = None
    hourly_limit: Optional[int] = None
    warmup_days: Optional[int] = None
    status: Optional[str] = None


@router.get("/accounts")
def list_accounts(org_id: str = Depends(get_org_id)):
    return {"items": db.list_email_accounts(org_id=org_id)}


@router.post("/accounts")
def create_account(body: AccountCreate, org_id: str = Depends(require_org_admin)):
    try:
        account_id = db.create_email_account(
            org_id,
            body.from_email.strip(),
            body.smtp_host.strip(),
            label=body.label, from_name=body.from_name,
            reply_to=body.reply_to, smtp_port=body.smtp_port,
            smtp_user=body.smtp_user, smtp_password=body.smtp_password,
            smtp_use_tls=body.smtp_use_tls, imap_host=body.imap_host,
            imap_port=body.imap_port, imap_user=body.imap_user,
            imap_password=body.imap_password, imap_mailbox=body.imap_mailbox,
            daily_limit=body.daily_limit, hourly_limit=body.hourly_limit,
            warmup_days=body.warmup_days,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)[:300])
    return {"id": account_id}


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, body: AccountUpdate,
                   org_id: str = Depends(require_org_admin)):
    fields = body.model_dump(exclude_unset=True)
    try:
        ok = db.update_email_account(account_id, org_id=org_id, **fields)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, org_id: str = Depends(require_org_admin)):
    if not db.delete_email_account(account_id, org_id=org_id):
        raise HTTPException(status_code=404, detail="Account not found")
    return {"ok": True}


@router.post("/accounts/{account_id}/test")
def test_account(account_id: int, org_id: str = Depends(require_org_admin)):
    """Attempt SMTP (and IMAP, if configured) login without sending mail."""
    from outreach import account_smtp
    import smtplib
    import ssl as _ssl

    acct = db.get_email_account(account_id, org_id=org_id, decrypt=True)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")

    result = {"smtp_ok": False, "imap_ok": False, "errors": []}
    cfg = account_smtp(acct)
    try:
        ctx = _ssl.create_default_context()
        if cfg.use_tls:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=15) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.ehlo()
                if cfg.username:
                    s.login(cfg.username, cfg.password)
        else:
            with smtplib.SMTP_SSL(cfg.host, cfg.port, context=ctx, timeout=15) as s:
                if cfg.username:
                    s.login(cfg.username, cfg.password)
        result["smtp_ok"] = True
    except Exception as e:
        result["errors"].append(f"SMTP: {e}")

    if acct.get("imap_host"):
        import imaplib
        try:
            m = imaplib.IMAP4_SSL(acct["imap_host"],
                                  int(acct.get("imap_port") or 993), timeout=15)
            m.login(acct.get("imap_user") or acct["from_email"],
                    acct.get("imap_password") or "")
            m.logout()
            result["imap_ok"] = True
        except Exception as e:
            result["errors"].append(f"IMAP: {e}")

    return result


# ── Queue ────────────────────────────────────────────────────
@router.get("/limits")
def outreach_limits(org_id: str = Depends(get_org_id)):
    """Current send-budget state and the organization dispatch rate."""
    from datetime import datetime, timezone
    from outreach import _env_int

    def _since(floor_micro):
        start = datetime.now(timezone.utc).replace(
            **floor_micro
        ).replace(tzinfo=None).isoformat(timespec="seconds")
        return db.count_sent_since(start, org_id=org_id)

    hourly_allocation_limit = db.get_hourly_allocation_limit(org_id=org_id)
    max_emails_per_minute = db.get_outreach_rate(org_id=org_id)
    return {
        "max_per_day":   _env_int("SEND_MAX_PER_DAY"),
        "max_per_hour":  _env_int("SEND_MAX_PER_HOUR"),
        "max_emails_per_minute": max_emails_per_minute,
        "hourly_allocation_limit": hourly_allocation_limit,
        "send_max_per_domain_per_hour": db.get_send_max_per_domain_per_hour(
            org_id=org_id,
        ),
        "send_jitter_max_seconds": db.get_send_jitter_max_seconds(org_id=org_id),
        "effective_hourly_allocation_limit": min(
            hourly_allocation_limit,
            max_emails_per_minute * 60,
        ),
        "sent_today":    _since({"hour": 0, "minute": 0, "second": 0, "microsecond": 0}),
        "sent_this_hour": _since({"minute": 0, "second": 0, "microsecond": 0}),
        "paused":        db.is_outreach_paused(org_id=org_id),
        **db.outreach_dispatch_status(org_id=org_id),
    }


class OutreachRateBody(BaseModel):
    max_emails_per_minute: int = Field(ge=1, le=60)


class OutreachHourlyAllocationBody(BaseModel):
    hourly_allocation_limit: int = Field(ge=1, le=3600, strict=True)


class OutreachJitterBody(BaseModel):
    send_jitter_max_seconds: int = Field(ge=0, le=3600, strict=True)


class OutreachDomainLimitBody(BaseModel):
    send_max_per_domain_per_hour: int = Field(ge=1, le=10000, strict=True)


@router.put("/limits")
def update_outreach_rate(
    body: OutreachRateBody,
    org_id: str = Depends(require_org_admin),
):
    """Set the fixed per-organization outbound email rate (1–60 per minute)."""
    db.set_setting(
        "max_emails_per_minute",
        body.max_emails_per_minute,
        org_id=org_id,
    )
    return {"max_emails_per_minute": body.max_emails_per_minute}


@router.put("/hourly-allocation-limit")
def update_hourly_allocation_limit(
    body: OutreachHourlyAllocationBody,
    org_id: str = Depends(require_org_admin),
):
    """Set the fixed number of dispatch slots available per tenant per hour."""
    db.set_hourly_allocation_limit(org_id, body.hourly_allocation_limit)
    return {"hourly_allocation_limit": body.hourly_allocation_limit}


@router.put("/send-jitter")
def update_send_jitter(
    body: OutreachJitterBody,
    org_id: str = Depends(require_org_admin),
):
    """Set the max random delay (0–3600s) inserted between consecutive sends."""
    db.set_send_jitter_max_seconds(org_id, body.send_jitter_max_seconds)
    return {"send_jitter_max_seconds": body.send_jitter_max_seconds}


@router.put("/domain-hourly-limit")
def update_domain_hourly_limit(
    body: OutreachDomainLimitBody,
    org_id: str = Depends(require_org_admin),
):
    """Set the organization's maximum messages per recipient domain per hour."""
    db.set_send_max_per_domain_per_hour(
        org_id, body.send_max_per_domain_per_hour,
    )
    return {
        "send_max_per_domain_per_hour": body.send_max_per_domain_per_hour,
    }


class SendNowBody(BaseModel):
    lead_ids: List[int]
    template_id: Optional[int] = None
    dry_run: bool = False


@router.post("/send-now")
def send_now_endpoint(body: SendNowBody, org_id: str = Depends(get_org_id)):
    """Immediately email the selected leads with a single template (manual mode).

    Does NOT touch campaigns/scheduled sends — sends right away, logs the
    event, and moves leads to 'contacted'. dry_run=true returns previews
    instead of sending.
    """
    import os
    from outreach import SMTPConfig, send_now

    # A user can't reasonably blast hundreds at once — bound the batch.
    lead_ids = list(dict.fromkeys(body.lead_ids))[:200]
    if not lead_ids:
        raise HTTPException(status_code=400, detail="No lead_ids provided")

    smtp = SMTPConfig.from_env()
    if not smtp.host and not db.list_email_accounts(org_id=org_id):
        raise HTTPException(status_code=400, detail="No sender account configured")

    from_addr = os.getenv("SMTP_FROM") or smtp.username
    summary = send_now(
        smtp, from_addr, lead_ids,
        template_id=body.template_id, dry_run=body.dry_run,
        org_id=org_id,
    )
    return summary


@router.get("/queue")
def queue_summary(org_id: str = Depends(get_org_id)):
    return {
        **db.send_queue_summary(org_id=org_id),
        "outbound": db.outbound_queue_summary(org_id=org_id),
    }


@router.get("/sends")
def list_sends(
    status: Optional[str] = None, limit: int = 50, offset: int = 0,
    org_id: str = Depends(get_org_id),
):
    """Per-lead send history — which leads were emailed (or failed/skipped) and why."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    items, total = db.list_sends(status=status, limit=limit, offset=offset, org_id=org_id)
    return {"items": items, "total": total}


@router.post("/send")
def trigger_send(dry_run: bool = False, limit: int = 50, org_id: str = Depends(get_org_id)):
    """Move due campaign sends into the durable outbox.

    SMTP delivery is performed by the worker through the single outbound
    gateway, so this endpoint returns as soon as the rows are queued.
    """
    from outreach import SMTPConfig, enqueue_due_sends

    limit = max(1, min(limit, 200))  # sane bounds - never 0, never a runaway blast

    smtp = SMTPConfig.from_env()
    if not smtp.host and not db.list_email_accounts(org_id=org_id):
        raise HTTPException(status_code=400, detail="No sender account configured")

    return enqueue_due_sends(
        limit=limit, dry_run=dry_run, org_id=org_id,
        smtp=smtp if smtp.host else None,
    )


# ── Threads / human-in-the-loop replies ──────────────────────
@router.get("/threads")
def list_threads(
    intent: Optional[str] = None, limit: int = 100,
    org_id: str = Depends(get_org_id),
):
    """Vendor threads. `intent=needs_info` gives the human-in-the-loop queue;
    with no intent it lists every thread (for the negotiator-style view)."""
    limit = max(1, min(limit, 200))
    return db.list_threads(intent=intent, limit=limit, org_id=org_id)


@router.get("/threads/inbox")
def list_reply_inbox(
    limit: int = 100,
    org_id: str = Depends(get_org_id),
):
    """List all organization-scoped conversations with received replies."""
    limit = max(1, min(limit, 200))
    return db.list_threads(
        limit=limit, org_id=org_id, inbound_only=True,
    )


@router.get("/threads/{thread_id}")
def get_thread(thread_id: int, org_id: str = Depends(get_org_id)):
    thread = db.get_thread(thread_id, org_id=org_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {
        "thread": thread,
        "messages": db.thread_messages(thread_id),
        "pending_draft": db.pending_draft_for_thread(thread_id),
    }


class DraftBody(BaseModel):
    model: Optional[str] = None


@router.post("/threads/{thread_id}/draft")
def generate_draft(thread_id: int, body: DraftBody, org_id: str = Depends(get_org_id)):
    """Gemini-draft a reply to this thread and save it for approval."""
    import os

    from email_text import strip_quotes
    from llm import DEFAULT_MODEL, generate_negotiation_draft

    thread = db.get_thread(thread_id, org_id=org_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    msgs = db.thread_messages(thread_id)
    if not msgs:
        raise HTTPException(status_code=400, detail="Thread has no messages yet")

    history = [
        {"direction": m["direction"], "subject": m["subject"],
         "body": strip_quotes(m["body"] or "")}
        for m in msgs
    ]
    try:
        draft = generate_negotiation_draft(
            history,
            price_floor=thread.get("price_floor"),
            currency=thread.get("currency") or "USD",
            goal=thread.get("goal"),
            our_company=os.getenv("OUR_COMPANY_NAME", "our team"),
            model=body.model or DEFAULT_MODEL,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini draft failed: {e}")

    draft_id = db.insert_draft(
        thread_id=thread_id,
        model=draft.get("model"),
        draft_subject=draft.get("draft_subject", ""),
        draft_body=draft.get("draft_body", ""),
        detected_price=draft.get("detected_price"),
        detected_currency=draft.get("detected_currency"),
        reasoning=draft.get("reasoning", ""),
        suggested_action=draft.get("suggested_action"),
    )
    return {"draft_id": draft_id, "draft": draft}


class SendDraftBody(BaseModel):
    subject: str
    body: str
    draft_id: Optional[int] = None


@router.post("/threads/{thread_id}/draft/{draft_id}/send")
def send_draft(thread_id: int, draft_id: int, body: SendDraftBody,
               org_id: str = Depends(get_org_id)):
    """Approve a draft and queue it for delivery through the outbox."""
    from outreach import enqueue_draft_reply

    thread = db.get_thread(thread_id, org_id=org_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    draft = db.get_draft(draft_id, org_id=org_id)
    if not draft or draft["thread_id"] != thread_id:
        raise HTTPException(status_code=404, detail="Draft not found for this thread")
    if draft.get("status") == "sent":
        raise HTTPException(status_code=409, detail="Draft has already been sent")

    result = enqueue_draft_reply(
        org_id, thread, draft_id, body.subject, body.body,
    )
    return {"ok": True, "queued": True, "outbound_id": result["id"],
            "message_id": None}


@router.post("/threads/{thread_id}/draft/{draft_id}/regenerate")
def regenerate_draft(thread_id: int, draft_id: int, body: DraftBody,
                     org_id: str = Depends(get_org_id)):
    """Mark the current draft rejected and generate a fresh one."""
    draft = db.get_draft(draft_id, org_id=org_id)
    if not draft or draft["thread_id"] != thread_id:
        raise HTTPException(status_code=404, detail="Draft not found for this thread")
    db.update_draft(draft_id, org_id=org_id, status="rejected")
    result = generate_draft(thread_id, body, org_id)
    return result
