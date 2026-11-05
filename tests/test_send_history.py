import unittest
from unittest.mock import MagicMock, Mock, patch

import db


class SendHistoryTests(unittest.TestCase):
    def test_history_combines_campaign_and_manual_sends(self):
        manual_row = {
            "id": -81,
            "status": "sent",
            "step_number": None,
            "scheduled_at": "2026-11-03T01:00:00+00:00",
            "sent_at": "2026-11-03T01:01:00+00:00",
            "error": None,
            "lead_id": 12,
            "business_name": "Example Co",
            "email": "contact@example.com",
            "campaign_name": "Manual send",
            "source": "send_now",
            "template_name": "Manual send: Intro",
        }
        connection = Mock()
        connection.execute.side_effect = [
            Mock(fetchone=Mock(return_value={"n": 1})),
            Mock(fetchall=Mock(return_value=[manual_row])),
        ]
        get_conn = MagicMock()
        get_conn.return_value.__enter__.return_value = connection

        with patch.object(db, "get_conn", get_conn):
            rows, total = db.list_sends(status="sent", org_id="org-1")

        self.assertEqual(total, 1)
        self.assertEqual(rows, [manual_row])
        count_query, data_query = [
            call.args[0] for call in connection.execute.call_args_list
        ]
        self.assertIn("UNION ALL", count_query)
        self.assertIn("m.source='send_now'", count_query)
        self.assertIn("s.org_id=%s", count_query)
        self.assertIn("m.org_id=%s", count_query)
        self.assertIn("WHERE status=%s", count_query)
        self.assertIn("LIMIT %s OFFSET %s", data_query)
        self.assertEqual(
            connection.execute.call_args_list[0].args[1],
            ("org-1", "org-1", "sent"),
        )
        self.assertEqual(
            connection.execute.call_args_list[1].args[1],
            ("org-1", "org-1", "sent", 100, 0),
        )

    def test_retrying_manual_send_is_shown_as_pending(self):
        connection = Mock()
        connection.execute.side_effect = [
            Mock(fetchone=Mock(return_value={"n": 0})),
            Mock(fetchall=Mock(return_value=[])),
        ]
        get_conn = MagicMock()
        get_conn.return_value.__enter__.return_value = connection

        with patch.object(db, "get_conn", get_conn):
            db.list_sends(status="pending", org_id="org-1")

        query = connection.execute.call_args_list[0].args[0]
        self.assertIn("WHEN m.status IN ('retry','dead_letter')", query)
        self.assertIn("WHEN m.status='retry' THEN 'pending'", query)
        self.assertIn("WHEN m.status='suppressed' THEN 'skipped'", query)

    def test_expired_unknown_outbound_cannot_be_released_to_retry(self):
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = None
        context = MagicMock()
        context.__enter__.return_value = connection

        with patch.object(db, "get_conn", return_value=context):
            with self.assertRaisesRegex(RuntimeError, "no longer sending"):
                db.release_outbound_claim_to_retry(12, "late error", "2026-11-03T00:00:00Z")

        query = connection.execute.call_args.args[0]
        self.assertIn("WHERE id=%s AND status='sending' RETURNING status", query)


if __name__ == "__main__":
    unittest.main()
