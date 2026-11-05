import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException

from api.routers import pipeline as pipeline_api


class PipelineRunEventsTests(unittest.TestCase):
    def test_streams_persisted_lines_for_owned_run(self):
        run = {
            "id": 7,
            "status": "running",
            "input_count": 1,
            "output_count": 0,
            "started_at": "2026-01-01T12:00:00",
            "finished_at": None,
        }
        events = [
            {
                "kind": "search",
                "message": "Searching auto repair in Boston, United States",
                "created_at": datetime(2026, 1, 1, 12, 0, 1, tzinfo=timezone.utc),
            },
            {
                "kind": "lead",
                "message": "Acme Auto",
                "created_at": datetime(2026, 1, 1, 12, 0, 2, tzinfo=timezone.utc),
            },
        ]
        with patch.object(pipeline_api.db, "get_run", return_value=run) as get_run:
            with patch.object(
                pipeline_api.db, "list_run_events", return_value=events
            ) as list_events:
                result = pipeline_api.run_events(7, org_id="org-1")

        get_run.assert_called_once_with(7, org_id="org-1")
        list_events.assert_called_once_with(
            7, org_id="org-1", limit=pipeline_api._RUN_LOG_MAX_LINES
        )
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["output_count"], 0)
        self.assertEqual(len(result["lines"]), 2)
        self.assertIn("[search] Searching auto repair", result["lines"][0])
        self.assertIn("[lead] Acme Auto", result["lines"][1])

    def test_returns_no_lines_when_run_has_no_events(self):
        run = {
            "id": 8,
            "status": "completed",
            "input_count": 1,
            "output_count": 3,
            "started_at": "2026-01-01T12:00:00",
            "finished_at": "2026-01-01T12:05:00",
        }
        with patch.object(pipeline_api.db, "get_run", return_value=run):
            with patch.object(pipeline_api.db, "list_run_events", return_value=[]):
                result = pipeline_api.run_events(8, org_id="org-1")

        self.assertEqual(result["lines"], [])
        self.assertEqual(result["output_count"], 3)

    def test_rejects_unknown_or_other_org_run(self):
        with patch.object(pipeline_api.db, "get_run", return_value=None):
            with self.assertRaises(HTTPException) as raised:
                pipeline_api.run_events(99, org_id="org-1")

        self.assertEqual(raised.exception.status_code, 404)
