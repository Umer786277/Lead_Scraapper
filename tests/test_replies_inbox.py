import unittest
from unittest.mock import Mock, patch

import db
import inbox
import outreach


RAW_REPLY = (
    b"Message-ID: <reply@example.com>\r\n"
    b"In-Reply-To: <sent@example.com>\r\n"
    b"From: person@example.com\r\n"
    b"To: sender@example.com\r\n"
    b"Subject: Re: Hello\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Sounds good."
)


class FakeIMAP:
    def __init__(self, raw):
        self.raw = raw
        self.events = []

    def login(self, *_args):
        return "OK", []

    def select(self, *_args):
        return "OK", [b"1"]

    def search(self, *_args):
        return "OK", [b"1"]

    def fetch(self, *_args):
        return "OK", [(b"metadata", self.raw)]

    def store(self, *_args):
        self.events.append("store")

    def close(self):
        return "OK", []

    def logout(self):
        return "BYE", []


class Connection:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((" ".join(query.split()), params))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class RepliesInboxTests(unittest.TestCase):
    def test_check_inbox_accepts_account_id_and_cancels_future_sends(self):
        connection = FakeIMAP(RAW_REPLY)
        stored = []

        def insert(*_args, **_kwargs):
            stored.append("insert")

        with patch.object(inbox.imaplib, "IMAP4_SSL", return_value=connection), \
                patch.object(inbox.ssl, "create_default_context"), \
                patch.object(
                    inbox, "_find_lead_for_reply",
                    return_value=(23, {"message_id": "<sent@example.com>", "subject": "Hello"}),
                ), \
                patch.object(db, "get_or_create_thread", return_value=88), \
                patch.object(db, "insert_thread_message", side_effect=insert), \
                patch.object(inbox, "_is_bounce", return_value=False), \
                patch.object(inbox, "_is_auto_reply", return_value=False), \
                patch.object(
                    inbox, "_triage_reply",
                    return_value={"intent": "other", "follow_up_days": None, "reasoning": ""},
                ), \
                patch.object(db, "set_thread_last_intent"), \
                patch.object(db, "cancel_future_sends_for_lead") as cancel, \
                patch.object(db, "update_lead_status") as update_status, \
                patch.object(db, "log_event"):
            results = inbox.check_inbox(
                inbox.IMAPConfig(
                    host="imap.example.com",
                    username="sender@example.com",
                    password="secret",
                ),
                org_id="org-id",
                account_id=7,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["account_id"], 7)
        self.assertEqual(stored, ["insert"])
        self.assertEqual(connection.events, ["store"])
        cancel.assert_called_once_with(23, org_id="org-id")
        self.assertEqual(update_status.call_args.args[:2], (23, "replied"))

    def test_drain_pending_replies_uses_db_outbox(self):
        row = {
            "id": 31,
            "lead_id": 23,
            "thread_id": 88,
            "subject": "Re: Hello",
            "parent_message_id": "<sent@example.com>",
            "parent_references": "<sent@example.com>",
        }
        with patch.object(db, "due_pending_replies", return_value=[row]), \
                patch.object(
                    db, "get_lead",
                    return_value={
                        "id": 23,
                        "email": "person@example.com",
                        "business_name": "Clinic",
                    },
                ), \
                patch.object(db, "lead_has_event", return_value=False), \
                patch.object(
                    db, "enqueue_outbound",
                    return_value={"id": 99, "created": True, "status": "pending"},
                ) as enqueue, \
                patch.object(db, "mark_pending_reply") as mark, \
                patch.object(outreach, "booking_reply_body", return_value="Book here"):
            queued = inbox.drain_pending_replies(booking_link="https://book", org_id="org-id")

        self.assertEqual(queued, 1)
        self.assertEqual(enqueue.call_args.kwargs["source"], "booking_pending")
        self.assertEqual(enqueue.call_args.kwargs["pending_reply_id"], 31)
        mark.assert_called_once_with(31, "queued", org_id="org-id")

    def test_cancel_future_sends_also_cancels_queued_outbound(self):
        connection = Connection()
        with patch.object(db, "get_conn", return_value=connection):
            db.cancel_future_sends_for_lead(23, org_id="org-id")

        self.assertEqual(len(connection.calls), 2)
        query, params = connection.calls[1]
        self.assertIn("UPDATE outbound_messages", query)
        self.assertIn("status IN ('pending','retry')", query)
        self.assertEqual(params, (23, "org-id"))

    def test_delivery_skips_campaign_send_after_reply(self):
        item = {
            "id": 99,
            "org_id": "org-id",
            "source": "campaign",
            "lead_id": 23,
            "send_id": 17,
            "pending_reply_id": None,
            "to_email": "person@example.com",
            "subject": "Hello",
            "body": "Body",
        }
        with patch.object(db, "suppression_reason", return_value=None), \
                patch.object(db, "get_lead", return_value={"status": "replied"}), \
                patch.object(db, "mark_outbound_failed") as fail, \
                patch.object(db, "mark_send") as mark_send, \
                patch.object(db, "log_event"), \
                patch.object(outreach, "resolve_sender") as resolve, \
                patch.object(outreach, "send_email") as send:
            outcome, reason, paused = outreach._deliver_outbound(item)

        self.assertEqual((outcome, reason, paused), ("skipped", "lead status: replied", False))
        fail.assert_called_once_with(99, "skipped", "lead status: replied")
        mark_send.assert_called_once_with(17, "skipped", error="lead status: replied", org_id="org-id")
        resolve.assert_not_called()
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
