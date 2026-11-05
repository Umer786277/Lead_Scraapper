import unittest
from unittest.mock import patch

import db
import inbox


RAW_REPLY = (
    b"Message-ID: <reply@example.com>\r\n"
    b"In-Reply-To: <sent@example.com>\r\n"
    b"From: person@example.com\r\n"
    b"To: sender@example.com\r\n"
    b"Subject: Re: Hello\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Please send details."
)


class FakeIMAP:
    def __init__(self, raw):
        self.raw = raw
        self.stores = []

    def login(self, *_args):
        return "OK", []

    def select(self, *_args):
        return "OK", [b"1"]

    def search(self, *_args):
        return "OK", [b"1"]

    def fetch(self, *_args):
        return "OK", [(b"metadata", self.raw)]

    def store(self, number, flags, value):
        self.stores.append((number, flags, value))

    def close(self):
        return "OK", []

    def logout(self):
        return "BYE", []


class InboxDurableIngestTests(unittest.TestCase):
    def cfg(self):
        return inbox.IMAPConfig(
            host="imap.example.com",
            username="sender@example.com",
            password="secret",
        )

    def test_matched_reply_is_stored_before_it_is_marked_seen(self):
        connection = FakeIMAP(RAW_REPLY)
        events = []

        def insert(*_args, **_kwargs):
            events.append("insert")

        def store(*_args):
            events.append("store")

        connection.store = store
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
                patch.object(db, "cancel_future_sends_for_lead"), \
                patch.object(db, "update_lead_status"), \
                patch.object(db, "log_event"):
            inbox.check_inbox(self.cfg(), org_id="org-id")

        self.assertEqual(events, ["insert", "store"])

    def test_store_failure_leaves_message_unseen_for_retry(self):
        connection = FakeIMAP(RAW_REPLY)
        with patch.object(inbox.imaplib, "IMAP4_SSL", return_value=connection), \
                patch.object(inbox.ssl, "create_default_context"), \
                patch.object(
                    inbox, "_find_lead_for_reply",
                    return_value=(23, {"message_id": "<sent@example.com>", "subject": "Hello"}),
                ), \
                patch.object(db, "get_or_create_thread", return_value=88), \
                patch.object(
                    db, "insert_thread_message",
                    side_effect=RuntimeError("database unavailable"),
                ):
            with self.assertRaises(RuntimeError):
                inbox.check_inbox(self.cfg(), org_id="org-id")

        self.assertEqual(connection.stores, [])

    def test_unmatched_message_is_not_stored_or_marked_seen(self):
        connection = FakeIMAP(RAW_REPLY)
        with patch.object(inbox.imaplib, "IMAP4_SSL", return_value=connection), \
                patch.object(inbox.ssl, "create_default_context"), \
                patch.object(inbox, "_find_lead_for_reply", return_value=(None, None)), \
                patch.object(db, "insert_thread_message") as insert:
            results = inbox.check_inbox(self.cfg(), org_id="org-id")

        self.assertEqual(results, [])
        insert.assert_not_called()
        self.assertEqual(connection.stores, [])


if __name__ == "__main__":
    unittest.main()
