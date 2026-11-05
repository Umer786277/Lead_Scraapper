import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import db


class _Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.row or []


class _SendConnection:
    def __init__(self, next_send=None):
        self.next_send = next_send
        self.calls = []

    def execute(self, query, params=()):
        sql = " ".join(query.split())
        self.calls.append((sql, params))
        if sql.startswith("UPDATE scheduled_sends SET status="):
            return _Cursor({
                "campaign_id": 88,
                "lead_id": 101,
                "step_number": 1,
            })
        if sql.startswith("SELECT s.id, cs.delay_days"):
            return _Cursor(self.next_send)
        if sql.startswith("UPDATE scheduled_sends SET scheduled_at="):
            return _Cursor()
        raise AssertionError(f"Unexpected SQL: {sql}")


class _RescheduleConnection:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((" ".join(query.split()), params))
        return _Cursor()


class CampaignFollowUpTimingTests(unittest.TestCase):
    def test_follow_up_delay_starts_after_actual_previous_send(self):
        sent_at = "2026-11-03T08:30:00"
        connection = _SendConnection({"id": 202, "delay_days": 4})
        context = MagicMock()
        context.__enter__.return_value = connection

        with patch.object(db, "get_conn", return_value=context), patch.object(
            db, "now", return_value=sent_at,
        ):
            db.mark_send(201, "sent", org_id="org-1")

        next_update = connection.calls[-1]
        self.assertIn("UPDATE scheduled_sends SET scheduled_at=", next_update[0])
        self.assertEqual(next_update[1], ("2026-11-07T08:30:00", 202, "org-1"))

    def test_failed_email_does_not_schedule_follow_up_from_failure_time(self):
        connection = _SendConnection()
        context = MagicMock()
        context.__enter__.return_value = connection

        with patch.object(db, "get_conn", return_value=context):
            db.mark_send(201, "failed", error="smtp failure", org_id="org-1")

        self.assertEqual(len(connection.calls), 1)

    def test_due_follow_up_claim_requires_previous_step_to_be_sent(self):
        connection = _SendConnection()
        def capture_query(query, params=()):
            connection.calls.append((" ".join(query.split()), params))
            return _Cursor([])

        connection.execute = capture_query
        context = MagicMock()
        context.__enter__.return_value = connection

        with patch.object(db, "get_conn", return_value=context):
            db.claim_due_sends(org_id="org-1")

        query = connection.calls[0][0]
        self.assertIn("previous.step_number=s.step_number-1", query)
        self.assertIn("previous.status='sent'", query)
        self.assertIn("previous.sent_at::timestamp", query)
        self.assertIn("step.delay_days * INTERVAL '1 day'", query)

    def test_outbound_claim_skips_rows_for_paused_campaigns(self):
        connection = _RescheduleConnection()
        context = MagicMock()
        context.__enter__.return_value = connection

        with patch.object(db, "get_conn", return_value=context):
            db.claim_due_outbound(org_id="org-1")

        query = connection.calls[1][0]
        self.assertIn("m.campaign_id IS NULL OR EXISTS", query)
        self.assertIn("c.id=m.campaign_id AND c.status='active'", query)
        self.assertIn("m.org_id=%s", query)

    def test_reschedule_send_writes_canonical_iso_text(self):
        connection = _RescheduleConnection()
        context = MagicMock()
        context.__enter__.return_value = connection

        with patch.object(db, "get_conn", return_value=context):
            db.reschedule_send(201, datetime(2026, 11, 3, 14, 0), org_id="org-1")

        self.assertEqual(
            connection.calls[0][1], ("2026-11-03T14:00:00", 201, "org-1"),
        )

    def test_reschedule_send_normalizes_aware_datetime_to_utc(self):
        connection = _RescheduleConnection()
        context = MagicMock()
        context.__enter__.return_value = connection

        with patch.object(db, "get_conn", return_value=context):
            db.reschedule_send(
                201,
                datetime(2026, 11, 3, 9, 0, tzinfo=timezone(timedelta(hours=5))),
                org_id="org-1",
            )

        self.assertEqual(
            connection.calls[0][1], ("2026-11-03T04:00:00", 201, "org-1"),
        )


if __name__ == "__main__":
    unittest.main()
