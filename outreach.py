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
import re
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid

import db


PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


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
    website, city, country, phone, opener — plus anything on the lead dict.
    """
    if not text:
        return text

    enriched = dict(lead or {})
    enriched["first_name"] = _derive_first_name(enriched)

    def sub(match):
        key = match.group(1)
        val = enriched.get(key)
        return str(val) if val is not None else ""

    return PLACEHOLDER_RE.sub(sub, text)


def send_email(smtp, to, subject, body, from_addr, reply_to=None):
    """Send one email via SMTP. Returns Message-ID on success, raises on failure."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Message-ID"] = make_msgid(domain=from_addr.split("@", 1)[-1] if "@" in from_addr else "localhost")
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


def process_queue(smtp, from_addr, limit=50, dry_run=False, on_item=None, reply_to=None):
    """
    Process due scheduled sends.

    - Skips leads whose status is 'replied'/'converted'/'dead' (cancels their pending rows).
    - Sends remaining emails through SMTP.
    - Logs each send to email_events and bumps lead.status → 'contacted' on first send.

    Returns a summary dict: {processed, sent, skipped, failed, dry_run}.
    """
    due = db.due_sends(limit=limit)
    summary = {"processed": 0, "sent": 0, "skipped": 0, "failed": 0,
               "deferred": 0, "dry_run": dry_run}

    from timezones import is_business_hours, next_business_hour

    for item in due:
        summary["processed"] += 1
        lead_status = item["lead_status"]

        # Gate: if lead already replied / converted / dead, cancel all their pending sends
        if lead_status in ("replied", "converted", "dead"):
            db.cancel_future_sends_for_lead(item["lead_id"])
            db.mark_send(item["send_id"], "skipped", error=f"lead status: {lead_status}")
            summary["skipped"] += 1
            if on_item:
                on_item(item, "skipped", f"lead status: {lead_status}")
            continue

        lead = db.get_lead(item["lead_id"]) or {}

        # Business-hours gate: skip + reschedule if it's outside 9-5 local for the lead.
        # Never blocks dry-runs (we want to preview output anytime).
        if not dry_run and not is_business_hours(lead):
            new_at = next_business_hour(lead)
            db.reschedule_send(item["send_id"], new_at)
            summary["deferred"] += 1
            if on_item:
                on_item(item, "deferred", f"outside business hours → {new_at.isoformat()}")
            continue

        # Lazy: one LLM call per lead returns BOTH opener and contact_name.
        # No-op if OPENAI_API_KEY isn't set — template still sends.
        if not lead.get("opener"):
            try:
                from llm import generate_opener
                gen = generate_opener(lead)
            except Exception:
                gen = {"opener": "", "contact_name": None}

            opener  = gen.get("opener") or ""
            contact = gen.get("contact_name")

            if opener or contact:
                with db.get_conn() as c:
                    c.execute(
                        "UPDATE leads SET opener=%s, "
                        "contact_name=COALESCE(%s, contact_name) "
                        "WHERE id=%s",
                        (opener, contact, item["lead_id"]),
                    )
                lead["opener"]       = opener
                lead["contact_name"] = contact or lead.get("contact_name")

        subject = render(item["subject"], lead)
        body    = render(item["body"], lead)

        if dry_run:
            db.mark_send(item["send_id"], "skipped", error="dry_run")
            summary["skipped"] += 1
            if on_item:
                on_item(item, "dry_run", subject)
            continue

        try:
            msg_id = send_email(
                smtp=smtp,
                to=item["email"],
                subject=subject,
                body=body,
                from_addr=from_addr,
                reply_to=reply_to,
            )
            db.mark_send(item["send_id"], "sent")
            db.log_event(
                lead_id=item["lead_id"],
                campaign_id=item["campaign_id"],
                event_type="sent",
                subject=subject,
                message_id=msg_id,
                note=f"step {item['step_number']}",
            )
            # First send moves lead to 'contacted' (keep if already beyond)
            if lead_status == "new":
                db.update_lead_status(item["lead_id"], "contacted")
            summary["sent"] += 1
            if on_item:
                on_item(item, "sent", subject)
        except Exception as e:
            db.mark_send(item["send_id"], "failed", error=str(e)[:400])
            summary["failed"] += 1
            if on_item:
                on_item(item, "failed", str(e))

    return summary


# ── Default starter templates ──────────────────────────────
# Tone notes:
#   - Sound like a real person (not an SDR running a sequence)
#   - Lead with a question or observation, not the pitch
#   - Specific numbers > vague claims ("8-12 appointments" beats "lots of calls")
#   - Short. Vet owners read on a phone between consults.
#   - The {{opener}} field is filled per-lead by the LLM — keeps each send unique
DEFAULT_TEMPLATES = [
    {
        "name":    "Step 1 — Intro",
        "subject": "Quick one for {{business_name}}",
        "body":    (
            "Hi {{first_name}},\n\n"
            "{{opener}}\n\n"
            "Random question — how many calls does the front desk drop during "
            "surgery hours, lunch, or after 5pm? When clinics actually count, "
            "it's usually 30–40% of incoming, and most are existing clients "
            "trying to rebook or chase a script.\n\n"
            "We've built an AI receptionist for vets — books straight into "
            "ezyVet / Covetrus / Petbooqz, handles prescription refill "
            "requests, and triages after-hours calls (worried owners, "
            "wildlife, strays). Sounds like a real person, not a bot.\n\n"
            "Worth a 10-minute look? Happy to send a 60-second clip of a "
            "real call instead if that's easier.\n\n"
            "Best,\n"
            "Umer\n"
        ),
    },
    {
        "name":    "Step 2 — Bump",
        "subject": "Re: Quick one for {{business_name}}",
        "body":    (
            "Hi {{first_name}},\n\n"
            "Just bumping this — know your inbox is mad this time of year.\n\n"
            "Honestly curious if missed-call volume is even on the radar at "
            "{{business_name}}, or it's a non-issue. Either answer is useful.\n\n"
            "Best,\n"
            "Umer\n"
        ),
    },
    {
        "name":    "Step 3 — Value",
        "subject": "The 37% number most clinics don't want to know",
        "body":    (
            "Hi {{first_name}},\n\n"
            "{{opener}}\n\n"
            "One thing we keep finding in call audits: 30–40% of incoming "
            "calls go unanswered, and almost all of those are existing "
            "clients trying to rebook or refill scripts. That's not a "
            "marketing problem — that's revenue walking out the door, plus a "
            "team that never gets a proper lunch break.\n\n"
            "Happy to run a free audit of {{business_name}}'s call patterns "
            "in 24 hours — no commitment, just the numbers. Want me to send "
            "the form?\n\n"
            "Best,\n"
            "Umer\n"
        ),
    },
    {
        "name":    "Step 4 — Break-up",
        "subject": "Closing the loop",
        "body":    (
            "Hi {{first_name}},\n\n"
            "Last note from me — going to stop pinging.\n\n"
            "If timing changes (new vet starting, busier season, anything), "
            "my line is open. Best of luck with the rest of the year.\n\n"
            "Best,\n"
            "Umer\n"
        ),
    },
]


def seed_default_templates():
    """Insert the 4 starter templates if the templates table is empty."""
    existing = db.list_templates()
    if existing:
        return [t["id"] for t in existing[:4]]
    ids = []
    for t in DEFAULT_TEMPLATES:
        ids.append(db.upsert_template(t["name"], t["subject"], t["body"]))
    return ids
