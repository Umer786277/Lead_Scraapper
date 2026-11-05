import json
import unittest
from unittest.mock import MagicMock, patch

import db
from api.routers import outreach as outreach_api
from fastapi import HTTPException
from pydantic import ValidationError


class _Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _ScriptSetConnection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _query, params=()):
        return _Cursor(self.rows.get(params[0]))


def _option(sequence_id, name, subject_prefix, delay_days=(0, 3, 3, 3)):
    return {
        "sequence_id": sequence_id,
        "name": name,
        "steps": [
            {
                "step_number": number,
                "delay_days": delay_days[number - 1],
                "template_id": sequence_id * 10 + number,
                "template_name": f"{name} {number}",
                "subject": f"{subject_prefix} subject {number}",
                "body": f"{subject_prefix} body {number}",
            }
            for number in range(1, 5)
        ],
    }


class _AssignmentConnection:
    def __init__(self, selection_mode="sequential", campaign=None):
        self.assignments = {}
        self.scheduled = []
        self.updates = []
        self.selection_mode = selection_mode
        self.campaign = campaign

    def execute(self, query, params=()):
        sql = " ".join(query.split())
        if sql.startswith("SELECT id, org_id FROM campaigns"):
            return _Cursor({
                "id": params[0],
                "org_id": params[1],
            })
        if "FROM outreach_settings" in sql:
            return _Cursor({"value": self.selection_mode})
        if sql.startswith("UPDATE campaigns SET template_selection_mode="):
            if self.campaign is not None:
                self.campaign["template_selection_mode"] = params[0]
                self.campaign["template_cycle_index"] = 0
                self.campaign["template_cycle_order"] = []
            return _Cursor()
        if "FROM campaign_script_assignments" in sql:
            return _Cursor(self.assignments.get((params[0], params[1])))
        if sql.startswith("UPDATE campaigns SET template_cycle_index="):
            self.updates.append(params)
            return _Cursor()
        if sql.startswith("INSERT INTO campaign_script_assignments"):
            self.assignments[(params[0], params[1])] = {
                "script_name": params[2],
                "script_snapshot": json.loads(params[3]),
                "selected_sequence_id": params[4],
                "option_position": params[5],
            }
            return _Cursor()
        if sql.startswith("SELECT 1 FROM leads l"):
            return _Cursor()
        if sql.startswith("INSERT INTO scheduled_sends"):
            self.scheduled.append(params)
            return _Cursor()
        raise AssertionError(f"Unexpected SQL: {sql}")


class CampaignTemplateOptionsTests(unittest.TestCase):
    def setUp(self):
        self.options = [
            _option(41, "Initial set A", "A"),
            _option(42, "Initial set B", "B"),
        ]
        self.campaign = {
            "id": 8,
            "org_id": "org-1",
            "template_selection_mode": "sequential",
            "template_cycle_index": 0,
            "template_cycle_order": [],
        }

    def test_sequential_assignment_round_robins_at_enrollment(self):
        connection = _AssignmentConnection(campaign=self.campaign)

        first = db._select_campaign_template(
            connection, self.campaign, 101, self.options,
        )
        second = db._select_campaign_template(
            connection, self.campaign, 102, self.options,
        )

        self.assertEqual(first["sequence_id"], 41)
        self.assertEqual(second["sequence_id"], 42)
        self.assertEqual(self.campaign["template_cycle_index"], 0)
        self.assertEqual(
            connection.assignments[(8, 101)]["script_snapshot"]["steps"][0]["body"],
            "A body 1",
        )
        self.assertEqual(connection.assignments[(8, 101)]["option_position"], 0)

    def test_random_assignment_persists_a_shuffled_cycle(self):
        connection = _AssignmentConnection(
            selection_mode="random", campaign=self.campaign,
        )

        with patch(
            "outreach_scripts.random.shuffle",
            side_effect=lambda values: values.reverse(),
        ):
            assigned = db._select_campaign_template(
                connection, self.campaign, 101, self.options,
            )

        self.assertEqual(assigned["sequence_id"], 42)
        self.assertEqual(self.campaign["template_cycle_order"], [1, 0])
        self.assertEqual(self.campaign["template_cycle_index"], 1)
        self.assertEqual(json.loads(connection.updates[0][1]), [1, 0])

    def test_existing_assignment_keeps_its_snapshot_after_option_edit(self):
        connection = _AssignmentConnection(campaign=self.campaign)
        original = db._select_campaign_template(
            connection, self.campaign, 101, self.options,
        )
        edited_options = [
            _option(43, "Replacement", "replacement"),
        ]

        assigned_again = db._select_campaign_template(
            connection, self.campaign, 101, edited_options,
        )

        self.assertEqual(assigned_again, original)
        self.assertEqual(assigned_again["sequence_id"], 41)
        self.assertEqual(len(connection.updates), 1)

    def test_options_response_reports_organization_selection_mode(self):
        connection = _AssignmentConnection(
            selection_mode="random", campaign=self.campaign,
        )
        connection_context = MagicMock()
        connection_context.__enter__.return_value = connection

        with patch.object(db, "org_id_or_default", return_value="org-1"), \
             patch.object(db, "get_conn", return_value=connection_context), \
             patch.object(
                 db, "_get_campaign_template_options", return_value=self.options,
             ):
            result = db.list_campaign_template_options(8, org_id="org-1")

        self.assertEqual(result["selection_mode"], "random")

    def test_enrollment_schedules_all_four_steps_under_one_saved_option(self):
        connection = _AssignmentConnection(campaign=self.campaign)

        enrolled = db._enroll_campaign_leads(
            connection,
            self.campaign,
            [101],
            [(11, 1, 0), (12, 2, 3), (13, 3, 3), (14, 4, 3)],
            self.options,
        )

        self.assertEqual(enrolled, [101])
        self.assertEqual(len(connection.scheduled), 4)
        self.assertEqual(
            [row[3] for row in connection.scheduled], [1, 2, 3, 4],
        )
        self.assertEqual(
            {row[6] for row in connection.scheduled}, {"Initial set A"},
        )
        self.assertEqual(
            connection.assignments[(8, 101)]["selected_sequence_id"], 41,
        )

    def test_template_options_request_rejects_duplicate_sequences(self):
        with self.assertRaises(ValidationError):
            outreach_api.CampaignTemplateOptionsBody(
                template_sequence_ids=[7, 7],
            )

    def test_campaign_create_accepts_template_options(self):
        body = outreach_api.CampaignBody(
            name="Options campaign",
            lead_ids=[101],
            template_sequence_ids=[41, 42],
            template_selection_mode="random",
        )
        with patch.object(
            outreach_api.db, "create_campaign", return_value=88,
        ) as create:
            result = outreach_api.create_campaign(body, org_id="org-1")

        self.assertEqual(result, {"id": 88})
        self.assertEqual(
            create.call_args.kwargs["template_sequence_ids"], [41, 42],
        )
        self.assertNotIn(
            "template_selection_mode", create.call_args.kwargs,
        )

    def test_campaign_create_accepts_multiple_automation_bundles(self):
        body = outreach_api.CampaignBody(
            name="Automatic templates",
            lead_ids=[101, 102],
            script_set_ids=[21, 22, 23],
        )
        with patch.object(
            outreach_api.db, "create_campaign", return_value=89,
        ) as create:
            result = outreach_api.create_campaign(body, org_id="org-1")

        self.assertEqual(result, {"id": 89})
        self.assertEqual(create.call_args.kwargs["script_set_ids"], [21, 22, 23])

    def test_multiple_automation_sets_flatten_to_ordered_four_step_bundles(self):
        scripts = {
            21: {
                "id": 21,
                "name": "First automation",
                "scripts": [
                    {"name": "Variant A", "steps": [
                        {"subject": f"A{step}", "body": f"Body A{step}"}
                        for step in range(1, 5)
                    ]},
                    {"name": "Variant B", "steps": [
                        {"subject": f"B{step}", "body": f"Body B{step}"}
                        for step in range(1, 5)
                    ]},
                ],
                "step_delays": [0, 3, 3, 3],
            },
            22: {
                "id": 22,
                "name": "Second automation",
                "scripts": [
                    {"name": "Variant C", "steps": [
                        {"subject": f"C{step}", "body": f"Body C{step}"}
                        for step in range(1, 5)
                    ]},
                ],
                "step_delays": [0, 3, 3, 3],
            },
        }
        variants, delays = db._load_campaign_script_set_options(
            _ScriptSetConnection(scripts), [21, 22], "org-1",
        )

        self.assertEqual(delays, [0, 3, 3, 3])
        self.assertEqual(
            [variant["name"] for variant in variants],
            [
                "First automation / Variant A",
                "First automation / Variant B",
                "Second automation / Variant C",
            ],
        )
        self.assertEqual(
            [variant["option_position"] for variant in variants],
            [0, 1, 2],
        )
        self.assertEqual(variants[2]["script_set_id"], 22)
        self.assertEqual(variants[2]["variant_name"], "Variant C")

    def test_campaign_create_rejects_duplicate_automation_bundles(self):
        with self.assertRaises(ValidationError):
            outreach_api.CampaignBody(
                name="Duplicate templates",
                lead_ids=[101],
                script_set_ids=[21, 21],
            )

    def test_template_options_cannot_mix_with_explicit_steps(self):
        with self.assertRaises(ValidationError):
            outreach_api.CampaignBody(
                name="Mixed campaign",
                lead_ids=[101],
                template_sequence_ids=[41],
                steps=[{"template_id": 1, "delay_days": 0}],
            )

    def test_campaign_options_update_is_exposed_for_existing_campaigns(self):
        body = outreach_api.CampaignTemplateOptionsBody(
            template_sequence_ids=[41, 42],
            selection_mode="random",
        )
        with patch.object(
            outreach_api.db, "set_campaign_template_options",
            return_value=True,
        ) as update:
            result = outreach_api.update_campaign_template_options(
                8, body, org_id="org-1",
            )

        self.assertEqual(result, {"ok": True})
        update.assert_called_once_with(
            8, [41, 42], org_id="org-1",
        )

    def test_campaign_enrollment_endpoint_reports_missing_campaign(self):
        body = outreach_api.CampaignEnrollmentBody(lead_ids=[101])
        with patch.object(
            outreach_api.db, "enroll_campaign_leads", return_value=None,
        ):
            with self.assertRaises(HTTPException) as raised:
                outreach_api.enroll_campaign_leads(8, body, org_id="org-1")

        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
