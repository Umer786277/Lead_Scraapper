import unittest
from unittest.mock import Mock, patch

import db
from api.routers import outreach as outreach_api
from fastapi import HTTPException


class RepliesInboxTests(unittest.TestCase):
    def test_inbox_query_returns_replies_of_every_classification_org_scoped(self):
        threads = [
            {
                "id": index,
                "last_intent": intent,
                "last_inbound_from": f"customer{index}@example.com",
                "last_inbound_at": "2026-11-02T10:00:00",
            }
            for index, intent in enumerate(
                ("needs_info", "interested", "bad_timing", "not_interested",
                 "unsubscribe", "other", "bounce", "auto_reply"),
                1,
            )
        ]
        result = Mock()
        result.fetchall.return_value = threads
        connection = Mock()
        connection.execute.return_value = result

        class ConnectionContext:
            def __enter__(self):
                return connection

            def __exit__(self, *_args):
                return False

        with patch.object(db, "_scope", return_value=(" AND vt.org_id=%s", ["org-a"])), \
                patch.object(db, "get_conn", return_value=ConnectionContext()):
            actual = db.list_threads(limit=100, org_id="org-a", inbound_only=True)

        query, params = connection.execute.call_args.args
        self.assertIn("vt.org_id=%s", query)
        self.assertIn("inbound.direction='inbound'", query)
        self.assertIn("AS last_inbound_from", query)
        self.assertIn("AS last_inbound_at", query)
        self.assertNotIn("vt.last_intent = %s", query)
        self.assertEqual(params, ["org-a", 100])
        self.assertEqual(
            [thread["last_intent"] for thread in actual],
            [thread["last_intent"] for thread in threads],
        )
        self.assertEqual(
            [thread["last_inbound_from"] for thread in actual],
            [thread["last_inbound_from"] for thread in threads],
        )

    def test_inbox_api_requests_all_reply_classifications_for_active_org(self):
        results = [{"id": 4, "last_intent": "unsubscribe"}]
        with patch.object(db, "list_threads", return_value=results) as list_threads:
            response = outreach_api.list_reply_inbox(limit=500, org_id="org-a")

        self.assertEqual(response, results)
        list_threads.assert_called_once_with(
            limit=200, org_id="org-a", inbound_only=True,
        )

    def test_another_organizations_thread_is_not_accessible(self):
        with patch.object(db, "get_thread", return_value=None) as get_thread, \
                patch.object(db, "thread_messages") as thread_messages:
            with self.assertRaises(HTTPException) as raised:
                outreach_api.get_thread(81, org_id="org-b")

        self.assertEqual(raised.exception.status_code, 404)
        get_thread.assert_called_once_with(81, org_id="org-b")
        thread_messages.assert_not_called()


if __name__ == "__main__":
    unittest.main()
