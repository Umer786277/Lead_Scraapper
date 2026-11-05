"""
IMAP inbox poller — matches inbound replies to the emails we sent,
then upserts them into vendor_threads + negotiation_messages.

Uses standard imaplib (no paid API). Gmail requires an App Password +
IMAP enabled. Outlook / custom domains work the same way.

Reply handling:
  - delivery failures (mailer-daemon)   -> bounce, cancel sequence, lead = dead
  - auto-responders / OOO (by headers)  -> park sequence ~7 days, never cancel
  - human replies                        -> Gemini triage -> interested | needs_info
                                          | bad_timing | not_interested | unsubscribe | other
  ('interested' gets an auto booking-link reply, sent immediately if it's
   business hours in the lead's TZ, otherwise queued in pending_replies and
   sent by the booking_replies worker at their next business hour.)
The IMAP connection is only held for fetch+store; triage runs in a worker
pool afterwards so a slow LLM call can't stall the poll.
"""

import email
import imaplib
import os
import re
import ssl
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from email.header import decode_header

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import db
from email_text import strip_quotes
from timezones import is_business_hours


MSGID_RE = re.compile(r"<[^>]+>")

# Auto-responders (out-of-office, vacation) — park the sequence instead of
# burning the lead. Subject patterns that signal "not a human reply".
_AUTO_SUBJECT_RE = re.compile(
    r"(?:automatic reply|auto[- ]?response|auto[- ]?reply|"
    r"out[ -]?of[ -]?office|vacation(?: auto[- ]?reply)?|"
    r"away from (?:the )?(?:office|desk)|extended absence|oo[o]? reply)",
    re.IGNORECASE,
)

# If the reply never leaves the mail loop — a human didn't write it.
_AUTO_SUBMITTED_VALUES = {"", "no"}   # anything else = machine generated
_AUTO_PRECEDENCE = {"auto", "bulk", "junk"}

# Delivery-failure notices — the address is dead, not the lead.
_BOUNCE_FROM_RE = re.compile(
    r"(?:mailer[- ]daemon|postmaster|mail (?:delivery |delivery )?(?:system|subsystem)|undeliverable)",
    re.IGNORECASE,
)

AUTO_REPLY_DELAY_DAYS = 7   # how far to push the sequence on an OOO hit


def _is_auto_reply(msg) -> bool:
    """True when the message is an auto-responder, not a human reply.

    Email clients signal these reliably via Auto-Submitted / Precedence
    headers + a null Return-Path — cheaper and more accurate than guessing
    from body text, so this runs BEFORE the LLM triage.
    """
    auto_submitted = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto_submitted not in _AUTO_SUBMITTED_VALUES:
        return True
    if (msg.get("Precedence") or "").strip().lower() in _AUTO_PRECEDENCE:
        return True
    if (msg.get("X-Auto-Response-Suppress") or "").strip():
        return True
    if (msg.get("Return-Path") or "").strip() == "<>":
        return True
    subject = (_decode_header(msg.get("Subject")) or "").lower()
    return bool(subject and _AUTO_SUBJECT_RE.search(subject))


def _is_bounce(msg) -> bool:
    """True when the message is a delivery-failure notice from a mail daemon."""
    frm = (_decode_header(msg.get("From")) or "").strip().lower()
    return bool(_BOUNCE_FROM_RE.search(frm))


# ── Quote / signature stripping ───────────────────────────────
# Shared implementation lives in email_text.py (also used by the API-backed
# negotiation drafts). Exposed here as _strip_quotes for inbox-internal use.
_strip_quotes = strip_quotes


@dataclass
class IMAPConfig:
    host: str
    port: int = 993
    username: str = ""
    password: str = ""
    mailbox: str = "INBOX"

    @classmethod
    def from_env(cls):
        return cls(
            host=os.getenv("IMAP_HOST", ""),
            port=int(os.getenv("IMAP_PORT", "993")),
            username=os.getenv("IMAP_USER", os.getenv("SMTP_USER", "")),
            password=os.getenv("IMAP_PASSWORD", os.getenv("SMTP_PASSWORD", "")),
            mailbox=os.getenv("IMAP_MAILBOX", "INBOX"),
        )

    @classmethod
    def from_account(cls, acct):
        """Build from a decrypted email_accounts row."""
        return cls(
            host=acct.get("imap_host") or "",
            port=int(acct.get("imap_port") or 993),
            username=acct.get("imap_user") or acct.get("from_email") or "",
            password=acct.get("imap_password") or "",
            mailbox=acct.get("imap_mailbox") or "INBOX",
        )

    @property
    def ready(self):
        return bool(self.host and self.username and self.password)


def _decode_header(value):
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(text)
    return " ".join(out).strip()


def _extract_text_body(msg):
    """Return the plain-text body, preferring text/plain over text/html."""
    plain, html = None, None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html is None:
                html = text
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        try:
            plain = payload.decode(charset, errors="replace")
        except LookupError:
            plain = payload.decode("utf-8", errors="replace")

    if plain:
        return plain.strip()
    if html:
        # Strip tags very crudely — just enough to be readable
        stripped = re.sub(r"<[^>]+>", "", html)
        return re.sub(r"\n{3,}", "\n\n", stripped).strip()
    return ""


def _extract_parent_ids(in_reply_to, references):
    """Pull out all quoted Message-IDs from In-Reply-To + References headers."""
    combined = f"{in_reply_to or ''} {references or ''}"
    return [m.group(0) for m in MSGID_RE.finditer(combined)]


# Fallback for clients that drop the threading headers: pull an address out
# of the From header (e.g. "Sarah <sarah@vet.com.au>" -> sarah@vet.com.au).
_EMAIL_IN_FROM_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _extract_email(header: str) -> str | None:
    m = _EMAIL_IN_FROM_RE.search(header or "")
    return m.group(0).lower() if m else None


def _find_lead_for_reply(parent_msg_ids, from_addr=None, subject=None, org_id=None):
    """Match a reply to a send we logged.

    Primary match is by Message-ID headers (In-Reply-To / References, the
    reliable path). Fallback is by From-address + subject: the reply's From
    must be the lead's email AND the subject must contain the subject we
    actually sent, so unrelated mail to the same address doesn't false-match.
    """
    clause, org_args = db._scope(org_id, "org_id")
    if parent_msg_ids:
        with db.get_conn() as c:
            row = c.execute(
                "SELECT lead_id, campaign_id, subject, message_id FROM email_events "
                f"WHERE event_type='sent' AND message_id = ANY(%s){clause} LIMIT 1",
                (list(parent_msg_ids), *org_args),
            ).fetchone()
        if row:
            return row["lead_id"], dict(row)

    addr = _extract_email(from_addr)
    if not addr or not subject:
        return None, None

    ee_clause = clause.replace("org_id", "ee.org_id")
    with db.get_conn() as c:
        row = c.execute(
            f"""
            SELECT l.id AS lead_id, ee.subject, ee.message_id
            FROM email_events ee
            JOIN leads l ON l.id = ee.lead_id
            WHERE ee.event_type='sent'
              AND l.email ILIKE %s
              AND %s ILIKE '%%' || ee.subject || '%%'
              {ee_clause}
            ORDER BY ee.created_at DESC
            LIMIT 1
            """,
            (addr, subject, *org_args),
        ).fetchone()
    if not row:
        return None, None
    return row["lead_id"], dict(row)


def _triage_reply(lead_id, subject, body):
    """Classify a just-received reply so the sequence can react intelligently.

    Falls back to intent="other" (treated as "stop and let a human handle
    it", the old blanket behavior) if the LLM call fails or isn't configured.
    """
    fallback = {"intent": "other", "follow_up_days": None, "reasoning": ""}
    try:
        from llm import classify_reply

        lead = db.get_lead(lead_id) or {}
        thread_id = db.get_or_create_thread(lead_id, subject=subject)
        history = [
            {"direction": m["direction"], "subject": m["subject"], "body": _strip_quotes(m["body"])}
            for m in db.thread_messages(thread_id)
        ]
        return classify_reply(lead, history)
    except Exception:
        return fallback


def _send_booking_reply(lead_id, thread_id, parent_message_id, parent_references,
                         subject, smtp, send_from_addr, booking_link, org_id=None):
    """Queue a scheduling-link reply when a lead is triaged as 'interested'.

    Delivery goes through the durable outbox / central gateway, so the same
    suppression, dispatch-slot and retry rules apply as every other send.
    Out-of-hours replies are parked in pending_replies and queued by the
    booking_replies worker at the lead's next business hour.
    """
    if not booking_link:
        return
    if db.lead_has_event(lead_id, "booking_sent", org_id=org_id):
        return  # already sent once - don't resend on every follow-up reply

    lead = db.get_lead(lead_id, org_id=org_id) or {}
    if not lead.get("email"):
        return

    from outreach import booking_reply_body

    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    reference_bits = f"{parent_references} {parent_message_id}".strip()

    if not is_business_hours(lead):
        if db.pending_reply_exists(lead_id, org_id=org_id):
            return
        from timezones import next_business_hour

        due_at = next_business_hour(lead).isoformat(timespec="seconds")
        db.enqueue_pending_reply(
            lead_id=lead_id,
            thread_id=thread_id,
            subject=reply_subject,
            parent_message_id=parent_message_id,
            parent_references=reference_bits,
            due_at=due_at,
            org_id=org_id,
        )
        db.log_event(
            lead_id=lead_id, event_type="booking_pending",
            subject=reply_subject,
            note=f"outside business hours - booking reply scheduled for {due_at}",
            org_id=org_id,
        )
        return

    body = booking_reply_body(lead, booking_link)
    db.enqueue_outbound(
        org_id=org_id,
        source="booking_reply",
        lead_id=lead_id,
        thread_id=thread_id,
        to_email=lead["email"],
        subject=reply_subject,
        body=body,
        in_reply_to=parent_message_id,
        references_hdr=reference_bits,
        idempotency_key=f"booking:{db.org_id_or_default(org_id)}:{lead_id}",
        note="auto booking reply",
    )

def drain_pending_replies(smtp=None, send_from_addr=None, booking_link=None,
                          limit=10, org_id=None):
    """Queue deferred booking replies for delivery through the outbox.

    Called by the booking_replies worker job before it processes the outbox.
    Returns the number of replies newly queued this pass.
    """
    if not booking_link:
        return 0
    from outreach import booking_reply_body

    queued = 0
    for row in db.due_pending_replies(limit=limit, org_id=org_id):
        lead = db.get_lead(row["lead_id"], org_id=org_id) or {}
        if not lead.get("email"):
            db.mark_pending_reply(row["id"], "failed", error="lead has no email", org_id=org_id)
            continue
        if db.lead_has_event(lead["id"], "booking_sent", org_id=org_id):
            db.mark_pending_reply(row["id"], "sent", org_id=org_id)
            continue

        subject = row["subject"] or f"Re: {lead.get('business_name') or 'booking'}"
        body = booking_reply_body(lead, booking_link)
        try:
            db.enqueue_outbound(
                org_id=org_id,
                source="booking_pending",
                lead_id=lead["id"],
                thread_id=row.get("thread_id"),
                pending_reply_id=row["id"],
                to_email=lead["email"],
                subject=subject,
                body=body,
                in_reply_to=row.get("parent_message_id"),
                references_hdr=row.get("parent_references"),
                idempotency_key=f"booking-pending:{row['id']}",
                note="deferred booking reply",
            )
            db.mark_pending_reply(row["id"], "queued", org_id=org_id)
            queued += 1
        except Exception as e:
            db.mark_pending_reply(row["id"], "failed", error=str(e)[:300], org_id=org_id)
    return queued

def check_inbox(cfg, limit=50, mark_seen=True, on_progress=None,
                 smtp=None, send_from_addr=None, booking_link=None,
                 max_triage_workers=4, org_id=None, account_id=None):
    """
    Connect, scan UNSEEN messages, match to our sent emails, store replies.

    Two phases, so the (LLM-bound) triage never holds the IMAP connection open:
      1. fetch + parse + store every matched reply, mark \\Seen
      2. react to each reply in a worker pool — header-based bounce/OOO
         detection first, then Gemini intent triage, status transitions and
         the optional booking auto-reply.

    smtp/send_from_addr/booking_link are optional — when all three are set, a
    reply triaged as 'interested' gets an automatic booking-link reply sent
    back (once per lead). Omit any of them to disable auto-reply and keep
    the old behavior (status update only, human sends the link manually).
    (Named send_from_addr, not from_addr, because from_addr is already used
    below for the inbound message's From header.)

    Returns a summary list:
    [{lead_id, thread_id, subject, from_addr, matched_msg_id, intent}]
    where intent is 'bounce' | 'auto_reply' | any of the LLM intents.
    """
    if not cfg.ready:
        raise RuntimeError("IMAP config incomplete — need host, username, password.")

    results = []
    pending = []
    ctx = ssl.create_default_context()
    m = imaplib.IMAP4_SSL(cfg.host, cfg.port, ssl_context=ctx)
    try:
        m.login(cfg.username, cfg.password)
        m.select(cfg.mailbox)
        status, data = m.search(None, "UNSEEN")
        if status != "OK":
            return results
        ids = data[0].split()[:limit]

        # Phase 1 — parse + store while the IMAP connection is open, but ONLY
        # the fast stuff (header parsing, DB insert). No LLM calls here, which
        # keeps the session short and the poll cheap.
        for idx, num in enumerate(ids, 1):
            if on_progress:
                on_progress(idx, len(ids))
            status, msg_data = m.fetch(num, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            message_id  = _decode_header(msg.get("Message-ID"))
            in_reply_to = _decode_header(msg.get("In-Reply-To"))
            references  = _decode_header(msg.get("References"))
            subject     = _decode_header(msg.get("Subject"))
            from_addr   = _decode_header(msg.get("From"))
            to_addr     = _decode_header(msg.get("To"))
            body        = _extract_text_body(msg)

            parent_ids = _extract_parent_ids(in_reply_to, references)
            lead_id, matched_event = _find_lead_for_reply(parent_ids, from_addr, subject, org_id=org_id)
            if not lead_id:
                continue  # not a reply to anything we sent — ignore

            thread_id = db.get_or_create_thread(lead_id, subject=subject, org_id=org_id)
            db.insert_thread_message(
                thread_id=thread_id,
                direction="inbound",
                subject=subject,
                body=body,
                message_id=message_id,
                in_reply_to=matched_event["message_id"] if matched_event else None,
                from_addr=from_addr,
                to_addr=to_addr,
            )

            # Mark seen immediately so a slow triage never makes us re-process
            # the same reply on the next poll.
            if mark_seen:
                m.store(num, "+FLAGS", "\\Seen")

            pending.append({
                "lead_id":        lead_id,
                "thread_id":      thread_id,
                "subject":        subject,
                "body":           body,
                "msg":            msg,
                "message_id":     message_id,
                "references":     references,
                "matched_event":  matched_event,
            })
            results.append({
                "lead_id":       lead_id,
                "thread_id":     thread_id,
                "subject":       subject,
                "from_addr":     from_addr,
                "matched_msg_id": matched_event["message_id"] if matched_event else None,
                "account_id":    account_id,
            })
    finally:
        try:
            m.close()
        except Exception:
            pass
        try:
            m.logout()
        except Exception:
            pass

    # Phase 2 — react to each reply WITHOUT holding the IMAP connection.
    # Auto-replies/bounces are decided by headers (no LLM). Everything else
    # goes through Gemini triage; running it in parallel means one slow call
    # can't gate the rest of the batch.
    def _react(item):
        lead_id   = item["lead_id"]
        thread_id = item["thread_id"]
        subject   = item["subject"]
        message_id = item["message_id"]
        matched   = item["matched_event"]

        if _is_bounce(item["msg"]):
            db.cancel_future_sends_for_lead(lead_id, org_id=org_id)
            bounced_lead = db.get_lead(lead_id, org_id=org_id) or {}
            if bounced_lead.get("email"):
                db.add_suppression(
                    org_id, bounced_lead["email"], reason="bounce", source="inbox",
                )
            db.update_lead_status(
                lead_id, "dead",
                note="Delivery failure (mailer-daemon) — verify this address",
                org_id=org_id,
            )
            db.log_event(lead_id=lead_id, event_type="bounce",
                         subject=subject, message_id=message_id,
                         note="Bounced — daemon delivery failure", org_id=org_id)
            db.set_thread_last_intent(thread_id, "bounce", org_id=org_id)
            return "bounce"

        if _is_auto_reply(item["msg"]):
            db.push_pending_sends_for_lead(lead_id, AUTO_REPLY_DELAY_DAYS, org_id=org_id)
            db.update_lead_status(
                lead_id, "snoozed",
                note=f"Auto-reply/OOO detected — sequence paused {AUTO_REPLY_DELAY_DAYS}d",
                org_id=org_id,
            )
            db.log_event(lead_id=lead_id, event_type="auto_reply",
                         subject=subject, message_id=message_id,
                         note="Auto-responder/OOO — treated as bad timing", org_id=org_id)
            db.set_thread_last_intent(thread_id, "auto_reply", org_id=org_id)
            return "auto_reply"

        triage = _triage_reply(lead_id, subject, _strip_quotes(item["body"]))
        db.set_thread_last_intent(thread_id, triage["intent"], org_id=org_id)
        if triage["intent"] == "bad_timing" and triage["follow_up_days"]:
            db.push_pending_sends_for_lead(lead_id, triage["follow_up_days"], org_id=org_id)
            db.update_lead_status(
                lead_id, "snoozed",
                note=f"Reply triage: bad_timing, resuming in {triage['follow_up_days']}d — {triage['reasoning']}",
                org_id=org_id,
            )
        else:
            db.cancel_future_sends_for_lead(lead_id, org_id=org_id)
            new_status = "dead" if triage["intent"] in ("not_interested", "unsubscribe") else "replied"
            db.update_lead_status(
                lead_id, new_status,
                note=f"Reply triage: {triage['intent']} — {triage['reasoning']}" if triage["reasoning"] else None,
                org_id=org_id,
            )
            if triage["intent"] == "unsubscribe":
                replied_lead = db.get_lead(lead_id, org_id=org_id) or {}
                if replied_lead.get("email"):
                    db.add_suppression(
                        org_id, replied_lead["email"],
                        reason="unsubscribe", source="inbox",
                    )
            if triage["intent"] == "interested":
                _send_booking_reply(
                    lead_id, thread_id, message_id, item["references"],
                    subject, smtp, send_from_addr, booking_link, org_id=org_id,
                )
        db.log_event(
            lead_id=lead_id,
            event_type="replied",
            subject=subject,
            message_id=message_id,
            note=f"Reply to: {matched['subject'] if matched else ''} [{triage['intent']}]",
            org_id=org_id,
        )
        return triage["intent"]

    intents = []
    if pending:
        workers = max(1, min(max_triage_workers, len(pending)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            intents = list(ex.map(_react, pending))

    for i, res in enumerate(results):
        res["intent"] = intents[i] if i < len(intents) else None

    return results
