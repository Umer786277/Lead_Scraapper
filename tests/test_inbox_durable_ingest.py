import unittest
import sys
from unittest.mock import Mock, call, patch

import db
import inbox


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def login(self, *_args):
        return "OK", []

    def select(self, *_args):
        return "OK", [b"0"]

    def response(self, name):
        self.calls.append(("response", name))
        return name, [b"1234"]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "search":
            return "OK", [b" ".join(str(uid).encode() for uid in self.messages)]
        if command == "fetch":
            uid = int(args[0])
            raw = self.messages[uid]
            return ("OK", [(b"message metadata", raw)]) if raw else ("NO", [])
        return "OK", []

    def close(self):
        return "OK", []

    def logout(self):
        return "BYE", []


class InboxDurableIngestTests(unittest.TestCase):
    def setUp(self):
        self.cfg = inbox.IMAPConfig(
            host="imap.example.com",
            username="sender@example.com",
            password="secret",
        )

    def test_uid_messages_are_persisted_before_cursor_advances(self):
        messages = {7: b"first", 8: b"second"}
        connection = FakeIMAP(messages)
        with patch.object(inbox.imaplib, "IMAP4_SSL", return_value=connection), \
                patch.object(db, "get_imap_cursor", return_value=6), \
                patch.object(db, "persist_inbound_message") as persist, \
                patch.object(db, "advance_imap_cursor") as advance, \
                patch.object(db, "claim_inbound_messages", return_value=[]):
            result = inbox.check_inbox(self.cfg, org_id="org-id")

        self.assertEqual(result, [])
        self.assertEqual(
            persist.call_args_list,
            [
                call("org-id", inbox._source_key(self.cfg), "INBOX", 1234, 7, b"first"),
                call("org-id", inbox._source_key(self.cfg), "INBOX", 1234, 8, b"second"),
            ],
        )
        advance.assert_called_once_with(
            "org-id", inbox._source_key(self.cfg), "INBOX", 1234, 8,
        )
        self.assertIn(("search", None, "7:*"), connection.calls)

    def test_failed_uid_fetch_does_not_advance_past_unpersisted_message(self):
        connection = FakeIMAP({7: b"persisted", 8: None, 9: b"not reached"})
        with patch.object(inbox.imaplib, "IMAP4_SSL", return_value=connection), \
                patch.object(db, "get_imap_cursor", return_value=6), \
                patch.object(db, "persist_inbound_message") as persist, \
                patch.object(db, "advance_imap_cursor") as advance, \
                patch.object(db, "claim_inbound_messages", return_value=[]):
            inbox.check_inbox(self.cfg, org_id="org-id")

        persist.assert_called_once_with(
            "org-id", inbox._source_key(self.cfg), "INBOX", 1234, 7, b"persisted",
        )
        advance.assert_called_once_with(
            "org-id", inbox._source_key(self.cfg), "INBOX", 1234, 7,
        )
        self.assertNotIn(("fetch", "9", "(RFC822)"), connection.calls)

    def test_persisted_reply_is_triaged_and_marked_processed(self):
        raw = (
            b"Message-ID: <reply@example.com>\r\n"
            b"In-Reply-To: <sent@example.com>\r\n"
            b"From: person@example.com\r\n"
            b"To: sender@example.com\r\n"
            b"Subject: Re: Hello\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Thanks, please send details."
        )
        row = {
            "id": 31,
            "source_key": "account:4",
            "uid_validity": 1234,
            "uid": 7,
            "raw_message": raw,
        }
        matched = {"message_id": "<sent@example.com>", "subject": "Hello"}
        with patch.object(
            inbox, "_find_lead_for_reply", return_value=(23, matched),
        ), patch.object(
            db, "get_or_create_thread", return_value=88,
        ), patch.object(
            db, "insert_thread_message",
        ) as insert, patch.object(
            inbox, "_react_to_inbound", return_value="needs_info",
        ) as react, patch.object(
            db, "mark_inbound_message",
        ) as mark:
            result = inbox._process_inbound_row(row, None, None, None, "org-id")

        self.assertEqual(result["intent"], "needs_info")
        insert.assert_called_once()
        react.assert_called_once()
        mark.assert_called_once_with(31, "processed")

    def test_failed_reply_processing_is_left_for_retry(self):
        raw = (
            b"Message-ID: <reply@example.com>\r\n"
            b"In-Reply-To: <sent@example.com>\r\n"
            b"From: person@example.com\r\n"
            b"Subject: Re: Hello\r\n\r\n"
            b"Can you share details?"
        )
        row = {
            "id": 32,
            "source_key": "account:4",
            "uid_validity": 1234,
            "uid": 8,
            "raw_message": raw,
        }
        with patch.object(
            inbox, "_find_lead_for_reply",
            return_value=(23, {"message_id": "<sent@example.com>"}),
        ), patch.object(
            db, "get_or_create_thread", return_value=88,
        ), patch.object(
            db, "insert_thread_message",
        ), patch.object(
            inbox, "_react_to_inbound", side_effect=RuntimeError("triage unavailable"),
        ), patch.object(
            db, "mark_inbound_message", return_value="retry",
        ) as mark, patch.object(inbox.log, "exception") as log_error:
            result = inbox._process_inbound_row(row, None, None, None, "org-id")

        self.assertEqual(result["error"], "triage unavailable")
        log_error.assert_called_once()
        mark.assert_called_once_with(
            32, "retry", error="triage unavailable",
        )

    def test_unsubscribe_triage_suppresses_recipient(self):
        item = {
            "lead_id": 23,
            "thread_id": 88,
            "subject": "Re: Hello",
            "body": "Please unsubscribe.",
            "msg": Mock(),
            "message_id": "<reply@example.com>",
            "references": "<sent@example.com>",
            "matched_event": {"subject": "Hello"},
        }
        lead = {"email": "person@example.com"}
        with patch.object(
            inbox, "_triage_reply",
            return_value={
                "intent": "unsubscribe",
                "follow_up_days": None,
                "reasoning": "recipient requested removal",
            },
        ), patch.object(inbox, "_is_bounce", return_value=False), patch.object(
            inbox, "_is_auto_reply", return_value=False,
        ), patch.object(db, "get_lead", return_value=lead), patch.object(
            db, "add_suppression",
        ) as suppress, patch.object(db, "cancel_future_sends_for_lead"), patch.object(
            db, "set_thread_last_intent",
        ), patch.object(db, "update_lead_status"), patch.object(db, "log_event"):
            intent = inbox._react_to_inbound(item, None, None, None, "org-id")

        self.assertEqual(intent, "unsubscribe")
        suppress.assert_called_once_with(
            "org-id", "person@example.com",
            reason="unsubscribe", source="inbox",
        )

    def test_bounce_triage_suppresses_recipient(self):
        item = {
            "lead_id": 23,
            "thread_id": 88,
            "subject": "Delivery failed",
            "body": "",
            "msg": Mock(),
            "message_id": "<bounce@example.com>",
            "references": "",
            "matched_event": None,
        }
        with patch.object(inbox, "_is_bounce", return_value=True), patch.object(
            db, "get_lead", return_value={"email": "person@example.com"},
        ), patch.object(db, "add_suppression") as suppress, patch.object(
            db, "cancel_future_sends_for_lead",
        ), patch.object(db, "set_thread_last_intent"), patch.object(
            db, "update_lead_status",
        ), patch.object(db, "log_event"):
            intent = inbox._react_to_inbound(item, None, None, None, "org-id")

        self.assertEqual(intent, "bounce")
        suppress.assert_called_once_with(
            "org-id", "person@example.com", reason="bounce", source="inbox",
        )

    def test_reply_triage_reads_org_scoped_thread_history(self):
        classified = {
            "intent": "needs_info",
            "follow_up_days": None,
            "reasoning": "recipient asked a question",
        }
        llm = Mock(classify_reply=Mock(return_value=classified))
        history = [{"direction": "inbound", "subject": "Re: Hello", "body": "Details?"}]
        with patch.dict(sys.modules, {"llm": llm}), patch.object(
            db, "get_lead", return_value={"id": 23, "business_name": "Clinic"},
        ) as get_lead, patch.object(
            db, "get_or_create_thread", return_value=88,
        ) as get_thread, patch.object(
            db, "thread_messages", return_value=history,
        ):
            result = inbox._triage_reply(
                23, "Re: Hello", "Could you share details?", org_id="org-id",
            )

        self.assertEqual(result, classified)
        get_lead.assert_called_once_with(23, org_id="org-id")
        get_thread.assert_called_once_with(23, subject="Re: Hello", org_id="org-id")
        llm.classify_reply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
