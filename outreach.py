"""
Outreach sender + queue worker.

No paid APIs — uses standard SMTP (Gmail App Password, Outlook, SES SMTP,
self-hosted Postfix, etc.). Templates support {{placeholders}} from the
lead record.

Public API:
    render(text, lead) -> str
    send_email(smtp, to, subject, body, from_addr) -> str | None   # returns Message-ID
    process_queue(smtp, from_addr, limit=N, dry_run=False, on_item=cb) -> dict
"""

import os
import random
import re
import smtplib
import socket
import ssl
import time
import uuid
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid

import db


log = logging.getLogger(__name__)
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _env_int(name, default=0):
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _start_of_day():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).replace(tzinfo=None).isoformat(timespec="seconds")


def _start_of_hour():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    ).replace(tzinfo=None).isoformat(timespec="seconds")


def _effective_hourly_allocation_limit(org_id):
    """Apply both the tenant hourly allocation and its existing minute cap."""
    return min(
        db.get_hourly_allocation_limit(org_id=org_id),
        db.get_outreach_rate(org_id=org_id) * 60,
    )


def _send_jitter_max_seconds(org_id):
    """Configured max random delay between sends (0 = disabled)."""
    try:
        return db.get_send_jitter_max_seconds(org_id=org_id)
    except Exception:
        return 0


def _sleep_send_jitter(max_seconds):
    """Legacy helper kept for callers/tests; send paths schedule jitter instead."""
    if max_seconds > 0:
        time.sleep(random.uniform(0, max_seconds))


def _jittered_next_attempt_at(max_seconds):
    """Persist jitter as a due time instead of sleeping in a request/worker."""
    when = datetime.now(timezone.utc)
    if max_seconds > 0:
        when += timedelta(seconds=random.uniform(0, max_seconds))
    return when


def _message_id_for(from_addr):
    domain = from_addr.split("@", 1)[-1] if "@" in (from_addr or "") else "localhost"
    return make_msgid(domain=domain)


def _retry_delay_seconds(attempts):
    attempts = max(1, int(attempts or 1))
    return min(3600, 60 * (2 ** (attempts - 1))) + random.uniform(0, 15)


def _max_per_domain_per_hour(org_id=None):
    return db.get_send_max_per_domain_per_hour(org_id=org_id)


def _smtp_provider_key(smtp):
    return f"{(smtp.host or '').strip().lower()}:{int(smtp.port)}"


class _NoSenderAvailable(Exception):
    pass


def _classify_send_exception(exc):
    """Return retry | failed | unknown for an SMTP/send exception.

    Retry only when we know the message was not accepted. Anything ambiguous
    after the SMTP transaction started is marked unknown and never auto-retried.
    """
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in (exc.recipients or {}).values()]
        if codes and all(400 <= code < 500 for code in codes):
            return "retry"
        return "failed"
    if isinstance(exc, smtplib.SMTPResponseException):
        return "retry" if 400 <= exc.smtp_code < 500 else "failed"
    if isinstance(exc, (ConnectionRefusedError, socket.gaierror, TimeoutError)):
        return "retry"
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return "unknown"
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "failed"
    return "unknown"


@dataclass
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str
    use_tls: bool = True  # STARTTLS on port 587; set False for port 465 (SSL)

    @classmethod
    def from_env(cls):
        """Load from SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD env vars."""
        return cls(
            host=os.getenv("SMTP_HOST", ""),
            port=int(os.getenv("SMTP_PORT", "587")),
            username=os.getenv("SMTP_USER", ""),
            password=os.getenv("SMTP_PASSWORD", ""),
            use_tls=os.getenv("SMTP_USE_TLS", "1") != "0",
        )


def account_smtp(acct):
    """Build an SMTPConfig from an email_accounts row (decrypted)."""
    return SMTPConfig(
        host=acct["smtp_host"],
        port=int(acct.get("smtp_port") or 587),
        username=acct.get("smtp_user") or acct["from_email"],
        password=acct.get("smtp_password") or "",
        use_tls=bool(acct.get("smtp_use_tls", True)),
    )


def account_from_addr(acct):
    name = (acct.get("from_name") or "").strip()
    email = acct["from_email"]
    return f"{name} <{email}>" if name else email


def resolve_sender(org_id, lead_id=None, smtp=None, from_addr=None,
                   account_id=None, reservation_key=None,
                   allow_env_fallback=False):
    """Resolve which SMTP account should send this email.

    Selects and reserves an organization-owned account with remaining capacity.
    Shared environment SMTP fallback is limited to the default org unless the
    caller explicitly opts in for a system email such as an org invitation.
    """
    if org_id:
        reservation_key = reservation_key or f"sender:{uuid.uuid4()}"
        acct = db.reserve_email_account(
            org_id,
            reservation_key,
            lead_id=lead_id,
            preferred_account_id=account_id,
        )
        if acct:
            acct["_reservation_key"] = reservation_key
            return (account_smtp(acct), account_from_addr(acct),
                    acct.get("reply_to"), acct["id"], acct)
        if db.list_email_accounts(org_id=org_id) and not allow_env_fallback:
            return None, None, None, None, None
        if str(org_id) != str(db.DEFAULT_ORG_ID) and not allow_env_fallback:
            return None, None, None, None, None

    if smtp and smtp.host:
        return (smtp, from_addr or os.getenv("SMTP_FROM") or smtp.username,
                None, None, None)

    env = SMTPConfig.from_env()
    if env.host:
        return (env, from_addr or os.getenv("SMTP_FROM") or env.username,
                None, None, None)
    return None, None, None, None, None


def release_sender(account):
    if account:
        db.release_email_account_reservation(account.get("_reservation_key"))


_ROLE_LOCALS = {
    "info", "contact", "sales", "hello", "admin", "office", "support",
    "enquiries", "team", "help", "mail", "general", "reception",
    "bookings", "appointments",
}


def _derive_first_name(lead: dict) -> str:
    """Pick the best first_name we can: lead.contact_name → email-derived → 'there'.

    Never falls back to business_name — 'Hi London Pet Clinic,' reads worse
    than 'Hi there,'.
    """
    explicit = (lead.get("first_name") or "").strip()
    if explicit:
        return explicit

    contact = (lead.get("contact_name") or "").strip()
    if contact:
        return contact.split()[0]   # take first token if "Sarah Smith"

    local = (lead.get("email") or "").split("@", 1)[0].lower().strip()
    if local and local not in _ROLE_LOCALS:
        head = local.split(".")[0].split("_")[0]
        if len(head) >= 3:          # filter "j", "jp" — too short to be a name
            return head.capitalize()

    return "there"


def render(text, lead):
    """Replace {{field}} placeholders with lead values. Missing → empty string.

    Supported fields: business_name, domain, email, first_name, contact_name,
    website, city, country, phone, opener, hook — plus anything on the lead
    dict. {{demo_clip}} is special: if DEMO_CLIP_URL is set it expands to
    a link to the hosted clip, otherwise to the old plain-text offer.
    """
    if not text:
        return text

    enriched = dict(lead or {})
    enriched["first_name"] = _derive_first_name(enriched)

    def sub(match):
        key = match.group(1)
        if key == "demo_clip":
            url = os.getenv("DEMO_CLIP_URL")
            if url:
                return f"Hear a real call from a clinic here: {url}"
            return ("Happy to send a 60-second clip of a real call if you "
                    "want to hear it in action.")
        val = enriched.get(key)
        return str(val) if val is not None else ""

    return PLACEHOLDER_RE.sub(sub, text)


def send_email(smtp, to, subject, body, from_addr, reply_to=None,
               in_reply_to=None, references=None, message_id=None):
    """Send one email via SMTP. Returns Message-ID on success, raises on failure.

    Pass in_reply_to/references (the parent message's Message-ID, and the
    accumulated References chain) to thread a reply into an existing
    conversation instead of starting a new one in the recipient's inbox.
    Pass message_id when the caller has already persisted it to the outbox;
    that lets a crash after SMTP accept be detected instead of retried.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg["Message-ID"] = message_id or make_msgid(
        domain=from_addr.split("@", 1)[-1] if "@" in from_addr else "localhost"
    )
    msg.set_content(body)

    ctx = ssl.create_default_context()

    if smtp.use_tls:
        with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
            if smtp.username:
                server.login(smtp.username, smtp.password)
            server.send_message(msg)
    else:
        with smtplib.SMTP_SSL(smtp.host, smtp.port, context=ctx, timeout=30) as server:
            if smtp.username:
                server.login(smtp.username, smtp.password)
            server.send_message(msg)

    return msg["Message-ID"]


def ensure_opener(lead):
    """Fill in a lead's AI opener + contact_name if missing (lazy, one LLM call
    per lead does double duty) and expose {{hook}} for templates.

    {{hook}} prefers the client-facing lead_line (from the enrich AI call);
    falls back to the generated opener; final fallback is a generic opener
    so Step 1 always opens with a line. No-op on the LLM if the API key
    isn't set — the template still sends. Returns the (possibly updated)
    lead dict.
    """
    if not lead.get("opener"):
        try:
            from llm import generate_opener
            gen = generate_opener(lead)
        except Exception:
            gen = {"opener": "", "contact_name": None}

        opener  = gen.get("opener") or ""
        contact = gen.get("contact_name")
        lead_id = lead.get("id")

        if (opener or contact) and lead_id:
            with db.get_conn() as c:
                c.execute(
                    "UPDATE leads SET opener=%s, "
                    "contact_name=COALESCE(%s, contact_name) "
                    "WHERE id=%s",
                    (opener, contact, lead_id),
                )
            lead["opener"]       = opener
            lead["contact_name"] = contact or lead.get("contact_name")

    hook = (lead.get("lead_line") or "").strip() or (lead.get("opener") or "").strip()
    if not hook:
        name = lead.get("business_name") or "your clinic"
        hook = f"Quick question for {name} — how are after-hours calls handled?"
    lead["hook"] = hook

    return lead


def send_now(smtp=None, from_addr=None, lead_ids=(), template_id=None, dry_run=False, reply_to=None,
             org_id=None):
    """Queue a one-off template send to chosen leads (manual mode).

    The API/UI returns immediately; the outbox worker performs suppression,
    dispatch-slot, account and SMTP handling. dry_run renders previews
    synchronously and never enqueues anything.

    Returns a summary dict: {requested, queued, sent, failed, skipped, previews}.
    """
    summary = {"requested": len(lead_ids), "queued": 0, "sent": 0, "failed": 0,
               "skipped": 0, "deferred": 0, "paused": False, "previews": []}

    if db.is_outreach_paused(org_id=org_id):
        summary["error"] = "Outreach paused - resume via the System/Pause toggle."
        return summary
    dispatch_status = db.outreach_dispatch_status(org_id=org_id)
    if dispatch_status["pause_active"]:
        summary["paused"] = True
        summary["error"] = (
            f"Provider requested a pause until {dispatch_status['paused_until']}."
        )
        return summary
    jitter_max = _send_jitter_max_seconds(org_id)

    tmpl = db.get_template(template_id, org_id=org_id) if template_id else None
    if not tmpl:
        templates = db.list_templates(org_id=org_id)
        tmpl = next(
            (t for t in templates
             if "step 1" in (t.get("name") or "").lower()
             and "intro" in (t.get("name") or "").lower()),
            None,
        ) or (templates[0] if templates else None)
    if not tmpl:
        summary["error"] = "No email template available - create one first."
        return summary

    if not dry_run and not (smtp and smtp.host) \
            and not db.list_email_accounts(org_id=org_id):
        summary["error"] = "No email account configured - add one in Outreach > Accounts."
        return summary

    for lead_id in lead_ids:
        lead = db.get_lead(lead_id, org_id=org_id) or {}
        email = (lead.get("email") or "").strip()

        if not email or lead.get("do_not_contact"):
            summary["skipped"] += 1
            continue
        if lead.get("status") in ("replied", "converted", "dead"):
            summary["skipped"] += 1
            continue

        subject = render(tmpl["subject"], lead)
        body    = render(tmpl["body"], lead)

        if dry_run:
            summary["previews"].append({
                "lead_id": lead_id,
                "business_name": lead.get("business_name"),
                "email": email,
                "subject": subject,
            })
            continue

        try:
            db.enqueue_outbound(
                org_id=org_id,
                source="send_now",
                lead_id=lead_id,
                to_email=email,
                subject=tmpl["subject"],
                body=tmpl["body"],
                reply_to=reply_to,
                next_attempt_at=_jittered_next_attempt_at(jitter_max),
                idempotency_key=f"send-now:{uuid.uuid4()}",
                note="manual send-now",
            )
            summary["queued"] += 1
        except Exception as e:
            summary["failed"] += 1
            summary["error"] = str(e)[:300]

    return summary


def enqueue_draft_reply(org_id, thread, draft_id, subject, body):
    """Queue a human-approved draft reply through the central outbox."""
    if not thread or not thread.get("email"):
        raise ValueError("Thread has no recipient email")
    jitter_max = _send_jitter_max_seconds(org_id)
    return db.enqueue_outbound(
        org_id=org_id,
        source="draft_reply",
        lead_id=thread.get("lead_id"),
        thread_id=thread.get("id"),
        draft_id=draft_id,
        to_email=thread["email"],
        subject=subject,
        body=body,
        next_attempt_at=_jittered_next_attempt_at(jitter_max),
        idempotency_key=f"draft:{draft_id}",
        note=f"negotiation reply (draft #{draft_id})",
    )


def _scheduled_status_for_outbound(status):
    return {
        "sent": "sent",
        "failed": "failed",
        "unknown": "unknown",
        "suppressed": "skipped",
        "skipped": "skipped",
    }.get(status, "queued")


def enqueue_due_sends(limit=50, dry_run=False, on_item=None, org_id=None, smtp=None):
    """Move due campaign rows into the durable outbox.

    No SMTP happens here. The function keeps campaign-specific gates
    (lead state, predecessor step, business hours) and snapshots the rendered
    subject/body so later template edits cannot change an already-queued send.
    """
    summary = {"processed": 0, "queued": 0, "sent": 0, "skipped": 0,
               "failed": 0, "deferred": 0, "dry_run": dry_run,
               "paused": False, "limit_reached": False, "limit_reason": None,
               "circuit_broken": False, "circuit_reason": None}

    if db.is_outreach_paused(org_id=org_id):
        summary["paused"] = True
        summary["error"] = "Outreach paused - resume via the System/Pause toggle."
        return summary
    dispatch_status = db.outreach_dispatch_status(org_id=org_id)
    if dispatch_status["pause_active"]:
        summary["paused"] = True
        summary["error"] = (
            f"Provider requested a pause until {dispatch_status['paused_until']}."
        )
        return summary

    daily_cap, hourly_cap = _env_int("SEND_MAX_PER_DAY"), _env_int("SEND_MAX_PER_HOUR")
    sent_today, sent_this_hour = 0, 0
    if daily_cap:
        sent_today = db.count_sent_since(_start_of_day(), org_id=org_id)
        if sent_today >= daily_cap:
            summary["limit_reached"] = True
            summary["limit_reason"] = f"daily cap reached ({sent_today}/{daily_cap})"
            return summary
    if hourly_cap:
        sent_this_hour = db.count_sent_since(_start_of_hour(), org_id=org_id)
        if sent_this_hour >= hourly_cap:
            summary["limit_reached"] = True
            summary["limit_reason"] = f"hourly cap reached ({sent_this_hour}/{hourly_cap})"
            return summary
    tenant_hourly_cap = db.get_hourly_allocation_limit(org_id=org_id)
    sent_this_hour = db.count_sent_since(_start_of_hour(), org_id=org_id)
    if sent_this_hour >= tenant_hourly_cap:
        summary["limit_reached"] = True
        summary["limit_reason"] = (
            f"tenant hourly cap reached ({sent_this_hour}/{tenant_hourly_cap})"
        )
        return summary

    if not dry_run and not (smtp and smtp.host) and not db.list_email_accounts(org_id=org_id):
        summary["limit_reached"] = True
        summary["limit_reason"] = "no email accounts configured"
        return summary

    eff = limit
    if daily_cap:
        eff = min(eff, daily_cap - sent_today)
    if hourly_cap:
        eff = min(eff, hourly_cap - sent_this_hour)
    eff = min(eff, tenant_hourly_cap - sent_this_hour)
    jitter_max = _send_jitter_max_seconds(org_id)

    from timezones import is_business_hours, next_business_hour

    for _ in range(max(1, eff)):
        due = db.claim_due_sends(limit=1, org_id=org_id)
        if not due:
            break
        item = due[0]
        summary["processed"] += 1
        lead_status = item["lead_status"]

        if lead_status in ("replied", "converted", "dead") or item.get("do_not_contact"):
            reason = ("do_not_contact" if item.get("do_not_contact")
                      else f"lead status: {lead_status}")
            db.cancel_future_sends_for_lead(item["lead_id"], org_id=org_id)
            db.mark_send(item["send_id"], "skipped", error=reason, org_id=org_id)
            summary["skipped"] += 1
            if on_item:
                on_item(item, "skipped", reason)
            continue

        prev_status = db.predecessor_send_status(
            item["lead_id"], item["campaign_id"], item["step_number"], org_id=org_id
        )
        if prev_status is not None and prev_status != "sent":
            db.release_send_claim(item["send_id"], org_id=org_id)
            summary["skipped"] += 1
            if on_item:
                on_item(item, "skipped",
                        f"step {item['step_number'] - 1} hasn't sent yet (status={prev_status})")
            continue

        if not (item.get("email") or "").strip():
            db.mark_send(item["send_id"], "skipped", error="lead has no email", org_id=org_id)
            summary["skipped"] += 1
            if on_item:
                on_item(item, "skipped", "lead has no email")
            continue

        lead = db.get_lead(item["lead_id"], org_id=org_id) or {}

        if not dry_run and not is_business_hours(lead):
            new_at = next_business_hour(lead)
            db.reschedule_send(item["send_id"], new_at, org_id=org_id)
            summary["deferred"] += 1
            if on_item:
                on_item(item, "deferred", f"outside business hours -> {new_at.isoformat()}")
            continue

        subject = render(item["subject"], lead)
        body    = render(item["body"], lead)

        if dry_run:
            db.mark_send(item["send_id"], "skipped", error="dry_run", org_id=org_id)
            summary["skipped"] += 1
            if on_item:
                on_item(item, "dry_run", subject)
            continue

        result = db.enqueue_outbound(
            org_id=org_id,
            source="campaign",
            lead_id=item["lead_id"],
            campaign_id=item["campaign_id"],
            send_id=item["send_id"],
            to_email=item["email"],
            subject=item["subject"],
            body=item["body"],
            next_attempt_at=_jittered_next_attempt_at(jitter_max),
            idempotency_key=f"campaign:{item['send_id']}",
            note=f"step {item['step_number']}",
        )
        db.mark_send(
            item["send_id"],
            _scheduled_status_for_outbound(result["status"]),
            org_id=org_id,
        )
        summary["queued"] += 1
        if on_item:
            on_item(item, "queued", subject)

    return summary


def _deliver_outbound(item, smtp=None, from_addr=None, reply_to=None,
                      hourly_allocation_limit=None):
    """Send one claimed outbox row through the single SMTP gateway."""
    org_id = item["org_id"]
    lead_id = item.get("lead_id")
    send_id = item.get("send_id")
    pending_reply_id = item.get("pending_reply_id")
    source = item.get("source") or ""
    to_email = item.get("to_email") or ""
    account = None
    acct_id = None
    domain_reserved = False
    smtp_accepted = False
    send_started = False
    retain_domain_reservation = False
    retain_sender_reservation = False
    provider_key = None

    reason = db.suppression_reason(org_id, to_email, lead_id=lead_id)
    if reason:
        db.mark_outbound_failed(item["id"], "suppressed", reason)
        if send_id:
            db.mark_send(send_id, "skipped", error=reason, org_id=org_id)
        if lead_id:
            db.log_event(lead_id=lead_id, event_type="suppressed",
                         campaign_id=item.get("campaign_id"),
                         subject=item.get("subject"), note=reason,
                         org_id=org_id)
        if pending_reply_id:
            db.mark_pending_reply(pending_reply_id, "failed", error=reason,
                                  org_id=org_id)
        return "skipped", reason, False

    if source in ("campaign", "send_now") and lead_id:
        lead = db.get_lead(lead_id, org_id=org_id) or {}
        lead = ensure_opener(lead)
        item["subject"] = render(item["subject"], lead)
        item["body"] = render(item["body"], lead)

    if hourly_allocation_limit is None:
        hourly_allocation_limit = _effective_hourly_allocation_limit(org_id)

    try:
        with db.outreach_dispatch_slot(
            org_id,
            hourly_allocation_limit,
            claimed_send_id=send_id,
        ):
            domain_reserved = db.reserve_outbound_domain(
                org_id,
                item["id"],
                to_email,
                max_per_hour=_max_per_domain_per_hour(org_id),
            )
            use_smtp, use_from, acct_reply_to, acct_id, account = resolve_sender(
                org_id,
                lead_id=lead_id,
                smtp=smtp,
                from_addr=from_addr,
                account_id=item.get("account_id"),
                reservation_key=f"outbound:{item['id']}",
                allow_env_fallback=source == "organization_invitation",
            )
            if not use_smtp:
                raise _NoSenderAvailable(
                    "No sender account with remaining capacity."
                )
            provider_key = _smtp_provider_key(use_smtp)
            provider_status = db.outbound_provider_status(org_id, provider_key)
            if provider_status["pause_active"]:
                raise db.OutboundProviderPaused(
                    provider_status["paused_until"],
                    provider_status["pause_reason"] or "provider throttled",
                )
            message_id = item.get("message_id") or _message_id_for(use_from)
            db.set_outbound_message_id(item["id"], message_id)
            send_started = True
            msg_id = send_email(
                smtp=use_smtp,
                to=to_email,
                subject=item["subject"],
                body=item["body"],
                from_addr=use_from,
                reply_to=reply_to or item.get("reply_to") or acct_reply_to,
                in_reply_to=item.get("in_reply_to"),
                references=item.get("references_hdr"),
                message_id=message_id,
            )
            smtp_accepted = True
    except db.OutreachDispatchPaused as e:
        now_utc = datetime.now(timezone.utc)
        paused_until = e.paused_until
        if paused_until.tzinfo is None:
            paused_until = paused_until.replace(tzinfo=timezone.utc)
        db.mark_outbound_retry(
            item["id"], str(e), max(30, (paused_until - now_utc).total_seconds())
        )
        return "deferred", str(e), True
    except db.OutboundProviderPaused as e:
        outcome = db.release_outbound_claim_to_retry(
            item["id"], str(e), e.paused_until,
        )
        return ("dead_letter" if outcome == "dead_letter" else "deferred",
                str(e), False)
    except db.OutreachDomainRateLimited as e:
        outcome = db.release_outbound_claim_to_retry(
            item["id"], str(e), e.available_at,
        )
        return ("dead_letter" if outcome == "dead_letter" else "deferred",
                str(e), False)
    except _NoSenderAvailable as e:
        db.release_outbound_claim(item["id"])
        return "no_sender", str(e), False
    except db.OutreachDispatchBusy as e:
        db.release_outbound_claim(item["id"])
        return "deferred", str(e), False
    except Exception as e:
        if isinstance(e, smtplib.SMTPResponseException) and e.smtp_code in (421, 451):
            provider_key = provider_key or "unknown"
            paused_until = db.pause_outbound_provider(
                org_id,
                provider_key,
                300,
                f"SMTP {e.smtp_code} temporary provider response",
            )
            outcome = db.release_outbound_claim_to_retry(
                item["id"], str(e), paused_until,
            )
            return ("dead_letter" if outcome == "dead_letter" else "deferred",
                    str(e), False)
        err = str(e)[:400]
        kind = _classify_send_exception(e)
        if kind == "retry":
            status = db.mark_outbound_retry(
                item["id"], err, _retry_delay_seconds(item.get("attempts"))
            )
            return ("dead_letter" if status == "dead_letter" else "retry",
                    err, False)
        status = "failed" if kind == "failed" else "unknown"
        retain_domain_reservation = send_started and kind == "unknown"
        retain_sender_reservation = send_started and kind == "unknown"
        db.mark_outbound_failed(item["id"], status, err, account_id=acct_id)
        if send_id:
            db.mark_send(send_id, status, error=err, org_id=org_id,
                         account_id=acct_id)
        if lead_id:
            db.log_event(lead_id=lead_id, event_type="failed",
                         campaign_id=item.get("campaign_id"),
                         subject=item.get("subject"),
                         note=f"{status}: {err}", org_id=org_id,
                         account_id=acct_id)
        if pending_reply_id:
            db.mark_pending_reply(pending_reply_id, "failed", error=err,
                                  org_id=org_id)
        return status, err, False
    finally:
        if not smtp_accepted and not retain_sender_reservation:
            release_sender(account)
        if domain_reserved and not smtp_accepted and not retain_domain_reservation:
            db.release_outbound_domain(item["id"])

    outbound_recorded = False
    try:
        db.mark_outbound_sent(item["id"], account_id=acct_id, message_id=msg_id)
        outbound_recorded = True
        release_sender(account)
        if domain_reserved:
            db.release_outbound_domain(item["id"])
        if send_id:
            db.mark_send(send_id, "sent", org_id=org_id, account_id=acct_id)

        is_booking = source in ("booking_reply", "booking_pending")
        event_type = "booking_sent" if is_booking else "sent"
        if lead_id:
            db.log_event(
                lead_id=lead_id,
                campaign_id=item.get("campaign_id"),
                event_type=event_type,
                subject=item["subject"],
                message_id=msg_id,
                note=item.get("note") or source,
                org_id=org_id,
                account_id=acct_id,
            )
        if acct_id:
            db.touch_email_account(acct_id)

        if lead_id and not is_booking and item.get("lead_status") == "new":
            db.update_lead_status(lead_id, "contacted", org_id=org_id)

        if source == "draft_reply":
            if item.get("thread_id"):
                db.insert_thread_message(
                    thread_id=item["thread_id"],
                    direction="outbound",
                    subject=item["subject"],
                    body=item["body"],
                    message_id=msg_id,
                    from_addr=use_from,
                    to_addr=to_email,
                )
            if item.get("draft_id"):
                db.update_draft(item["draft_id"], org_id=org_id, status="sent")
            if item.get("thread_id"):
                db.update_thread(item["thread_id"], org_id=org_id,
                                 last_intent="human_replied")

        if is_booking:
            if item.get("thread_id"):
                db.insert_thread_message(
                    thread_id=item["thread_id"],
                    direction="outbound",
                    subject=item["subject"],
                    body=item["body"],
                    message_id=msg_id,
                    in_reply_to=item.get("in_reply_to"),
                )
            if pending_reply_id:
                db.mark_pending_reply(pending_reply_id, "sent", org_id=org_id)
    except Exception as exc:
        if outbound_recorded:
            log.exception(
                "Outbound %s was sent but follow-up state could not be recorded: %s",
                item["id"], exc,
            )
        else:
            log.exception(
                "SMTP accepted outbound %s but sent state could not be persisted: %s",
                item["id"], exc,
            )
            try:
                db.mark_outbound_failed(
                    item["id"], "unknown",
                    f"SMTP accepted; failed to persist sent state: {str(exc)[:300]}",
                    account_id=acct_id,
                )
            except Exception:
                log.exception(
                    "Could not persist unknown status for accepted outbound %s",
                    item["id"],
                )
            return "unknown", str(exc)[:400], False

    return "sent", item.get("subject"), False


def process_outbound_queue(smtp=None, from_addr=None, limit=50, on_item=None,
                           reply_to=None, fail_threshold=3, org_id=None):
    """Send due rows from the outbox. This is the only SMTP send loop."""
    summary = {"processed": 0, "sent": 0, "skipped": 0, "failed": 0,
               "unknown": 0, "retry": 0, "dead_letter": 0, "deferred": 0, "paused": False,
               "limit_reached": False, "limit_reason": None,
               "circuit_broken": False, "circuit_reason": None}
    consecutive_failures = 0

    if db.is_outreach_paused(org_id=org_id):
        summary["paused"] = True
        summary["error"] = "Outreach paused - resume via the System/Pause toggle."
        return summary
    dispatch_status = db.outreach_dispatch_status(org_id=org_id)
    if dispatch_status["pause_active"]:
        summary["paused"] = True
        summary["error"] = (
            f"Provider requested a pause until {dispatch_status['paused_until']}."
        )
        return summary

    daily_cap, hourly_cap = _env_int("SEND_MAX_PER_DAY"), _env_int("SEND_MAX_PER_HOUR")
    sent_today, sent_this_hour = 0, 0
    if daily_cap:
        sent_today = db.count_sent_since(_start_of_day(), org_id=org_id)
        if sent_today >= daily_cap:
            summary["limit_reached"] = True
            summary["limit_reason"] = f"daily cap reached ({sent_today}/{daily_cap})"
            return summary
    if hourly_cap:
        sent_this_hour = db.count_sent_since(_start_of_hour(), org_id=org_id)
        if sent_this_hour >= hourly_cap:
            summary["limit_reached"] = True
            summary["limit_reason"] = f"hourly cap reached ({sent_this_hour}/{hourly_cap})"
            return summary
    tenant_hourly_cap = db.get_hourly_allocation_limit(org_id=org_id)
    sent_this_hour = db.count_sent_since(_start_of_hour(), org_id=org_id)
    if sent_this_hour >= tenant_hourly_cap:
        summary["limit_reached"] = True
        summary["limit_reason"] = (
            f"tenant hourly cap reached ({sent_this_hour}/{tenant_hourly_cap})"
        )
        return summary

    if not (smtp and smtp.host) and not db.list_email_accounts(org_id=org_id):
        summary["limit_reached"] = True
        summary["limit_reason"] = "no email accounts configured"
        return summary

    eff = limit
    if daily_cap:
        eff = min(eff, daily_cap - sent_today)
    if hourly_cap:
        eff = min(eff, hourly_cap - sent_this_hour)
    eff = min(eff, tenant_hourly_cap - sent_this_hour)
    hourly_allocation_limit = _effective_hourly_allocation_limit(org_id)
    worker_id = f"outreach:{uuid.uuid4()}"

    for _ in range(max(1, eff)):
        due = db.claim_due_outbound(limit=1, org_id=org_id, worker_id=worker_id)
        if not due:
            break
        item = due[0]
        summary["processed"] += 1

        outcome, detail, paused = _deliver_outbound(
            item,
            smtp=smtp,
            from_addr=from_addr,
            reply_to=reply_to,
            hourly_allocation_limit=hourly_allocation_limit,
        )
        if on_item:
            on_item(item, outcome, detail)

        if outcome == "sent":
            summary["sent"] += 1
            consecutive_failures = 0
        elif outcome == "skipped":
            summary["skipped"] += 1
            consecutive_failures = 0
        elif outcome == "retry":
            summary["retry"] += 1
        elif outcome == "unknown":
            summary["unknown"] += 1
        elif outcome == "dead_letter":
            summary["dead_letter"] += 1
        elif outcome == "deferred":
            summary["deferred"] += 1
            if paused:
                summary["paused"] = True
                summary["error"] = detail
                break
        elif outcome == "failed":
            summary["failed"] += 1
            consecutive_failures += 1
            if consecutive_failures >= fail_threshold:
                summary["circuit_broken"] = True
                summary["circuit_reason"] = (
                    f"{consecutive_failures} sends in a row failed - "
                    f"stopped batch early (last error: {detail})"
                )
                if on_item:
                    on_item(item, "circuit_broken", summary["circuit_reason"])
                break
        elif outcome == "no_sender":
            summary["limit_reached"] = True
            summary["limit_reason"] = detail
            break

    return summary


def process_queue(smtp=None, from_addr=None, limit=50, dry_run=False, on_item=None, reply_to=None,
                  fail_threshold=3, org_id=None):
    """Queue due campaign sends, then process the outbox.

    Kept as the worker/legacy entry point. API endpoints call the enqueue
    helpers directly and return without waiting for SMTP.
    """
    enqueued = enqueue_due_sends(
        limit=limit, dry_run=dry_run, on_item=on_item, org_id=org_id,
        smtp=smtp,
    )
    if dry_run:
        return enqueued
    if enqueued.get("paused") or enqueued.get("limit_reached"):
        return enqueued

    processed = process_outbound_queue(
        smtp=smtp,
        from_addr=from_addr,
        limit=limit,
        on_item=on_item,
        reply_to=reply_to,
        fail_threshold=fail_threshold,
        org_id=org_id,
    )
    summary = dict(processed)
    summary["dry_run"] = False
    summary["processed"] = enqueued.get("processed", 0)
    summary["queued"] = enqueued.get("queued", 0)
    summary["skipped"] = enqueued.get("skipped", 0) + processed.get("skipped", 0)
    summary["deferred"] = enqueued.get("deferred", 0) + processed.get("deferred", 0)
    for key in ("paused", "limit_reached", "limit_reason", "error",
                "circuit_broken", "circuit_reason"):
        if enqueued.get(key):
            summary[key] = enqueued[key]
    return summary

DEFAULT_TEMPLATES = [
    {
        "name":    "Step 1 — Intro",
        "subject": "Missed calls at {{business_name}}?",
        "body":    (
            "Hi {{first_name}},\n\n"
            "{{hook}}\n\n"
            "How many calls does the front desk drop during surgery hours, "
            "lunch, or after 5pm? When clinics actually count, it's usually "
            "30–40% of incoming, and most are existing clients trying to "
            "rebook or chase a script.\n\n"
            "We built CallMissa, an AI receptionist for vet clinics. It books "
            "straight into EasyVet, ezyVet, ProVet Cloud, or RxWorks, handles "
            "prescription refill requests, and safely triages after-hours "
            "calls (worried owners, wildlife, strays) instead of sending "
            "them to voicemail.\n\n"
            "{{demo_clip}}\n\n"
            "Best,\n"
            "Umer\n"
            "Founder, CallMissa\n"
        ),
    },
    {
        "name":    "Step 2 — Bump",
        "subject": "One more for {{business_name}}",
        "body":    (
            "Hi {{first_name}},\n\n"
            "Bumping this one last time — know your inbox is mad this time "
            "of year.\n\n"
            "Honestly curious if missed-call volume is even on the radar at "
            "{{business_name}}, or it's a non-issue. Either answer is useful.\n\n"
            "Best,\n"
            "Umer\n"
            "Founder, CallMissa\n"
        ),
    },
    {
        "name":    "Step 3 — Value",
        "subject": "The number most clinics don't track",
        "body":    (
            "Hi {{first_name}},\n\n"
            "Different angle from my last note: most clinics don't actually "
            "know their missed-call rate until someone runs the numbers. Not "
            "guessing, not a vendor pitch, just the actual count of calls "
            "that hit voicemail during surgery blocks, lunch, and after "
            "5pm.\n\n"
            "Happy to have CallMissa run a free 24-hour audit of "
            "{{business_name}}'s call patterns. No commitment, just the "
            "data. Want me to send the form?\n\n"
            "Best,\n"
            "Umer\n"
            "Founder, CallMissa\n"
        ),
    },
    {
        "name":    "Step 4 — Break-up",
        "subject": "Closing the loop",
        "body":    (
            "Hi {{first_name}},\n\n"
            "Last note from me, going to stop pinging.\n\n"
            "If timing changes (new vet starting, busier season, anything), "
            "my line is open. Best of luck with the rest of the year.\n\n"
            "Best,\n"
            "Umer\n"
            "Founder, CallMissa\n"
        ),
    },
]


# ── Auto-reply sent when a lead's response is triaged as "interested" ──
BOOKING_REPLY_TEMPLATE = (
    "Hi {{first_name}},\n\n"
    "Great to hear from you. Grab whatever time works here: {{booking_link}}\n\n"
    "Talk soon,\n"
    "Umer\n"
    "Founder, CallMissa\n"
)


def booking_reply_body(lead, booking_link):
    """Render the booking-link auto-reply for a lead. `booking_link` is the
    scheduling URL (e.g. a Calendly link) — configured via BOOKING_LINK.
    """
    enriched = dict(lead or {})
    enriched["booking_link"] = booking_link
    return render(BOOKING_REPLY_TEMPLATE, enriched)


def seed_default_templates(org_id=None):
    """Insert the 4 starter templates for an org if it has none yet."""
    existing = db.list_templates(org_id=org_id)
    if existing:
        return [t["id"] for t in existing[:4]]
    ids = []
    for t in DEFAULT_TEMPLATES:
        ids.append(db.upsert_template(t["name"], t["subject"], t["body"], org_id=org_id))
    return ids
