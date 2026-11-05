import unittest
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import db


class _Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _CampaignConnection:
    def __init__(self):
        self.step_inserts = []
        self.scheduled_inserts = []
        self.next_step_id = 1
        self.assignments = {}
        self.campaign = None

    def execute(self, query, params=()):
        sql = " ".join(query.split())
        if "SELECT scripts, step_delays FROM outreach_script_sets" in sql:
            return _Cursor({
                "scripts": [
                    {
                        "name": "Variant A",
                        "steps": [
                            {"subject": f"Subject {index}", "body": f"Body {index}"}
                            for index in range(1, 5)
                        ],
                    },
                    {
                        "name": "Variant B",
                        "steps": [
                            {"subject": f"Alt {index}", "body": f"Alt body {index}"}
                            for index in range(1, 5)
                        ],
                    },
                ],
                "step_delays": [0, 2, 4, 3],
            })
        if sql.startswith("INSERT INTO campaigns"):
            self.campaign = {
                "id": 88,
                "org_id": params[0],
                "template_selection_mode": "sequential",
                "template_cycle_index": 0,
                "template_cycle_order": [],
                "script_variants": json.loads(params[7]),
                "script_rotation_index": 0,
                "script_rotation_order": [],
            }
            return _Cursor({"id": 88})
        if sql.startswith("SELECT id, org_id, template_selection_mode"):
            return _Cursor(self.campaign)
        if sql.startswith("SELECT org_id, script_variants"):
            return _Cursor(self.campaign)
        if sql.startswith("INSERT INTO campaign_steps"):
            self.step_inserts.append(params)
            step_id = self.next_step_id
            self.next_step_id += 1
            return _Cursor({"id": step_id})
        if sql.startswith("SELECT 1 FROM leads l"):
            return _Cursor()
        if "FROM outreach_settings" in sql:
            return _Cursor({"value": "sequential"})
        if "FROM campaign_script_assignments" in sql:
            return _Cursor(self.assignments.get((params[0], params[1])))
        if sql.startswith("UPDATE campaigns SET script_rotation_index="):
            self.campaign["script_rotation_index"] = params[0]
            return _Cursor()
        if sql.startswith("INSERT INTO campaign_script_assignments"):
            self.assignments[(params[0], params[1])] = {
                "script_name": params[2],
                "script_snapshot": json.loads(params[3]),
            }
            return _Cursor()
        if sql.startswith("INSERT INTO scheduled_sends"):
            self.scheduled_inserts.append(params)
            return _Cursor()
        if sql.startswith("UPDATE campaigns SET leads_count="):
            return _Cursor()
        raise AssertionError(f"Unexpected SQL: {sql}")


class CampaignScriptSetCampaignTests(unittest.TestCase):
    def test_campaign_uses_script_set_steps_and_delays_without_sequence(self):
        connection = _CampaignConnection()
        context = MagicMock()
        context.__enter__.return_value = connection

        with patch.object(db, "get_conn", return_value=context):
            campaign_id = db.create_campaign(
                "Four-step campaign",
                [],
                [101],
                org_id="org-1",
                script_set_id=7,
            )

        self.assertEqual(campaign_id, 88)
        self.assertEqual(len(connection.step_inserts), 4)
        self.assertEqual(
            [row[2] for row in connection.step_inserts],
            [0, 2, 4, 3],
        )
        self.assertTrue(all(row[3] is None for row in connection.step_inserts))
        self.assertEqual(
            [row[5] for row in connection.step_inserts],
            [f"Subject {index}" for index in range(1, 5)],
        )
        self.assertIn((88, 101), connection.assignments)
        assignment = connection.assignments[(88, 101)]
        self.assertEqual(assignment["script_name"], "Variant A")
        self.assertEqual(
            [step["subject"] for step in assignment["script_snapshot"]["steps"]],
            [f"Subject {index}" for index in range(1, 5)],
        )
        self.assertEqual(
            {row[6] for row in connection.scheduled_inserts},
            {"Variant A"},
        )

        scheduled = [
            datetime.fromisoformat(row[5])
            for row in connection.scheduled_inserts
        ]
        offsets = [(value - scheduled[0]).days for value in scheduled]
        self.assertEqual(offsets, [0, 2, 6, 9])


if __name__ == "__main__":
    unittest.main()
