import unittest
from unittest.mock import MagicMock, Mock, patch

import db
from api.routers import outreach as outreach_api
from fastapi import HTTPException
from pydantic import ValidationError


class OutreachSequenceTests(unittest.TestCase):
    def setUp(self):
        self.body = outreach_api.SequenceBody(
            name="Clinic follow-up",
            steps=[
                outreach_api.SequenceStepBody(template_id=7, delay_days=0),
                outreach_api.SequenceStepBody(template_id=8, delay_days=3),
            ],
        )

    def test_create_sequence_is_scoped_to_org(self):
        with patch.object(
            outreach_api.db, "upsert_sequence", return_value=42
        ) as upsert:
            result = outreach_api.create_sequence(self.body, org_id="org-1")

        self.assertEqual(result, {"id": 42})
        upsert.assert_called_once_with(
            "Clinic follow-up",
            [
                {"template_id": 7, "delay_days": 0},
                {"template_id": 8, "delay_days": 3},
            ],
            org_id="org-1",
        )

    def test_sequence_requires_an_initial_email(self):
        with self.assertRaises(ValidationError):
            outreach_api.SequenceBody(name="Empty", steps=[])

    def test_initial_email_must_start_without_a_delay(self):
        with self.assertRaises(ValueError):
            db.upsert_sequence(
                "Delayed start",
                [{"template_id": 7, "delay_days": 1}],
                org_id="org-1",
            )

    def test_reversed_template_order_is_rejected_before_updating_sequence(self):
        connection = Mock()
        queried_templates = {14: 4, 13: 3, 12: 2, 11: 1}

        def execute(query, params=()):
            if "SELECT id, step_number FROM email_templates" in query:
                return Mock(fetchone=Mock(return_value={
                    "id": params[0],
                    "step_number": queried_templates[params[0]],
                }))
            raise AssertionError(f"Unexpected query after invalid order: {query}")

        connection.execute.side_effect = execute
        context = MagicMock()
        context.__enter__.return_value = connection

        with patch.object(db, "get_conn", return_value=context):
            with self.assertRaisesRegex(ValueError, "out of order"):
                db.upsert_sequence(
                    "Existing sequence",
                    [
                        {"template_id": 14, "delay_days": 0},
                        {"template_id": 13, "delay_days": 3},
                        {"template_id": 12, "delay_days": 3},
                        {"template_id": 11, "delay_days": 3},
                    ],
                    sequence_id=42,
                    org_id="org-1",
                )

        self.assertEqual(connection.execute.call_count, 4)

    def test_sequence_creation_persists_each_selected_template_and_delay(self):
        connection = Mock()
        inserts = []

        def execute(query, params=()):
            if "SELECT id, step_number FROM email_templates" in query:
                template_id = params[0]
                return Mock(fetchone=Mock(return_value={
                    "id": template_id,
                    "step_number": template_id,
                }))
            if query.startswith("INSERT INTO outreach_sequences"):
                return Mock(fetchone=Mock(return_value={"id": 42}))
            if query.startswith("INSERT INTO outreach_sequence_steps"):
                inserts.append(params)
                return Mock()
            raise AssertionError(f"Unexpected query: {query}")

        connection.execute.side_effect = execute
        context = MagicMock()
        context.__enter__.return_value = connection

        with patch.object(db, "get_conn", return_value=context):
            sequence_id = db.upsert_sequence(
                "Four ordered steps",
                [
                    {"template_id": 1, "delay_days": 0},
                    {"template_id": 2, "delay_days": 2},
                    {"template_id": 3, "delay_days": 4},
                    {"template_id": 4, "delay_days": 5},
                ],
                org_id="org-1",
            )

        self.assertEqual(sequence_id, 42)
        self.assertEqual(
            inserts,
            [
                (42, 1, 0, 1),
                (42, 2, 2, 2),
                (42, 3, 4, 3),
                (42, 4, 5, 4),
            ],
        )

    def test_sequence_allows_one_email_and_at_most_four_steps(self):
        single_email = outreach_api.SequenceBody(
            name="Single email",
            steps=[outreach_api.SequenceStepBody(template_id=7, delay_days=0)],
        )
        self.assertEqual(len(single_email.steps), 1)

        with self.assertRaises(ValidationError):
            outreach_api.SequenceBody(
                name="Too many",
                steps=[
                    outreach_api.SequenceStepBody(template_id=index, delay_days=1)
                    for index in range(1, 6)
                ],
            )

    def test_campaign_requires_an_automation_sequence_or_step(self):
        body = outreach_api.CampaignBody(name="Campaign", lead_ids=[1])
        with self.assertRaises(HTTPException) as raised:
            outreach_api.create_campaign(body, org_id="org-1")
        self.assertEqual(raised.exception.status_code, 422)

    def test_campaign_uses_org_sequence_id(self):
        body = outreach_api.CampaignBody(
            name="Campaign",
            lead_ids=[1],
            sequence_id=42,
        )
        sequence = {"id": 42, "steps": [{"template_id": 7, "delay_days": 0}]}
        with (
            patch.object(outreach_api.db, "get_sequence", return_value=sequence),
            patch.object(outreach_api.db, "create_campaign", return_value=99) as create,
        ):
            result = outreach_api.create_campaign(body, org_id="org-1")

        self.assertEqual(result, {"id": 99})
        create.assert_called_once_with(
            "Campaign",
            [],
            [1],
            notes=None,
            org_id="org-1",
            sequence_id=42,
            script_set_id=None,
        )

    def test_campaign_cannot_select_another_orgs_sequence(self):
        body = outreach_api.CampaignBody(
            name="Campaign",
            lead_ids=[1],
            sequence_id=42,
        )
        with (
            patch.object(outreach_api.db, "get_sequence", return_value=None),
            patch.object(outreach_api.db, "create_campaign") as create,
        ):
            with self.assertRaises(HTTPException) as raised:
                outreach_api.create_campaign(body, org_id="org-2")

        self.assertEqual(raised.exception.status_code, 404)
        create.assert_not_called()

    def test_campaign_copies_template_content_and_checks_suppression(self):
        connection = Mock()
        connection.execute.side_effect = [
            Mock(fetchone=Mock(return_value={
                "id": 7,
                "name": "Initial email",
                "subject": "Original subject",
                "body": "Original body",
            })),
            Mock(fetchone=Mock(return_value={"id": 99})),
            Mock(fetchone=Mock(return_value={"id": 101})),
            Mock(fetchone=Mock(return_value=None)),
            Mock(),
            Mock(),
        ]
        get_conn = MagicMock()
        get_conn.return_value.__enter__.return_value = connection

        with patch.object(db, "get_conn", get_conn):
            campaign_id = db.create_campaign(
                "One email",
                [{"template_id": 7, "delay_days": 0}],
                [1],
                org_id="org-1",
            )

        self.assertEqual(campaign_id, 99)
        step_insert = connection.execute.call_args_list[2]
        self.assertIn("template_name, subject, body", step_insert.args[0])
        self.assertEqual(
            step_insert.args[1],
            (99, 1, 0, 7, "Initial email", "Original subject", "Original body"),
        )
        enrollment_query = connection.execute.call_args_list[3]
        self.assertIn("suppression_list", enrollment_query.args[0])
        self.assertIn("do_not_contact", enrollment_query.args[0])
        self.assertEqual(enrollment_query.args[1], (1, "org-1"))


if __name__ == "__main__":
    unittest.main()
