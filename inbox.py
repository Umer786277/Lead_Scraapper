"""
IMAP inbox poller — matches inbound replies to the emails we sent,
then upserts them into vendor_threads + negotiation_messages.

Uses standard imaplib (no paid API). Gmail requires an App Password +
IMAP enabled. Outlook / custom domains work the same way.
"""

import email
import imaplib
import os
import re
import ssl
from dataclasses import dataclass
from email.header import decode_header

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import db


MSGID_RE = re.compile(r"<[^>]+>")


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


def _find_lead_for_reply(parent_msg_ids):
    """Match any of the quoted Message-IDs to a send we logged."""
    if not parent_msg_ids:
        return None, None
    with db.get_conn() as c:
        row = c.execute(
            "SELECT lead_id, campaign_id, subject, message_id FROM email_events "
            "WHERE event_type='sent' AND message_id = ANY(%s) LIMIT 1",
            (list(parent_msg_ids),),
        ).fetchone()
        if not row:
            return None, None
        return row["lead_id"], dict(row)


def check_inbox(cfg, limit=50, mark_seen=True, on_progress=None):
    """
    Connect, scan UNSEEN messages, match to our sent emails, store replies.

    Returns a summary list: [{lead_id, thread_id, subject, from_addr, matched_msg_id}].
    """
    if not cfg.ready:
        raise RuntimeError("IMAP config incomplete — need host, username, password.")

    results = []
    ctx = ssl.create_default_context()
    m = imaplib.IMAP4_SSL(cfg.host, cfg.port, ssl_context=ctx)
    try:
        m.login(cfg.username, cfg.password)
        m.select(cfg.mailbox)
        status, data = m.search(None, "UNSEEN")
        if status != "OK":
            return results
        ids = data[0].split()[:limit]

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
            lead_id, matched_event = _find_lead_for_reply(parent_ids)
            if not lead_id:
                continue  # not a reply to anything we sent — ignore

            thread_id = db.get_or_create_thread(lead_id, subject=subject)
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
            db.update_lead_status(lead_id, "replied")
            db.cancel_future_sends_for_lead(lead_id)
            db.log_event(
                lead_id=lead_id,
                event_type="replied",
                subject=subject,
                message_id=message_id,
                note=f"Reply to: {matched_event['subject'] if matched_event else ''}",
            )

            if mark_seen:
                m.store(num, "+FLAGS", "\\Seen")

            results.append({
                "lead_id":       lead_id,
                "thread_id":     thread_id,
                "subject":       subject,
                "from_addr":     from_addr,
                "matched_msg_id": matched_event["message_id"] if matched_event else None,
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

    return results
