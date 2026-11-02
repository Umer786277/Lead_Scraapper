import unittest
from unittest.mock import MagicMock, Mock, call, patch

import db
from api.routers import outreach as outreach_api
from fastapi import HTTPException
from pydantic import ValidationError


class OutreachTemplateTests(unittest.TestCase):
    def setUp(self):
        self.body = outreach_api.TemplateBody(
            step_number=1,
            name="Organization intro",
            subject="Hello {{business_name}}",
            body="Hi {{first_name}},\n\n{{hook}}",
        )

    def test_create_template_is_scoped_to_org(self):
        with (
            patch.object(outreach_api.db, "list_templates", return_value=[]),
            patch.object(
                outreach_api.db, "upsert_template", return_value=42
            ) as upsert,
        ):
            result = outreach_api.create_template(self.body, org_id="org-1")

        self.assertEqual(result, {"id": 42})
        upsert.assert_called_once_with(
            "Organization intro",
            "Hello {{business_name}}",
            "Hi {{first_name}},\n\n{{hook}}",
            org_id="org-1",
            step_number=1,
        )

    def test_create_rejects_an_occupied_step(self):
        with (
            patch.object(
                outreach_api.db,
                "list_templates",
                return_value=[{"id": 8, "step_number": 1}],
            ),
            patch.object(outreach_api.db, "upsert_template") as upsert,
        ):
            with self.assertRaises(HTTPException) as raised:
                outreach_api.create_template(self.body, org_id="org-1")

        self.assertEqual(raised.exception.status_code, 409)
        upsert.assert_not_called()

    def test_create_allows_additional_unassigned_script(self):
        body = outreach_api.TemplateBody(
            name="Alternate outreach",
            subject="A different subject",
            body="A different email body",
        )
        with (
            patch.object(outreach_api.db, "list_templates", return_value=[]),
            patch.object(
                outreach_api.db, "upsert_template", return_value=43
            ) as upsert,
        ):
            result = outreach_api.create_template(body, org_id="org-1")

        self.assertEqual(result, {"id": 43})
        upsert.assert_called_once_with(
            "Alternate outreach",
            "A different subject",
            "A different email body",
            org_id="org-1",
            step_number=None,
        )

    def test_rejects_blank_template_fields(self):
        body = outreach_api.TemplateBody(
            step_number=1,
            name="  ",
            subject="Subject",
            body="Body",
        )
        with patch.object(outreach_api.db, "upsert_template") as upsert:
            with self.assertRaises(HTTPException) as raised:
                outreach_api.create_template(body, org_id="org-1")

        self.assertEqual(raised.exception.status_code, 422)
        upsert.assert_not_called()

    def test_rejects_template_fields_over_limit(self):
        with self.assertRaises(ValidationError):
            outreach_api.TemplateBody(
                step_number=1,
                name="n" * 121,
                subject="Subject",
                body="Body",
            )

    def test_rejects_step_outside_four_step_sequence(self):
        with self.assertRaises(ValidationError):
            outreach_api.TemplateBody(
                step_number=5,
                name="Template",
                subject="Subject",
                body="Body",
            )

    def test_update_cannot_access_another_orgs_template(self):
        with (
            patch.object(outreach_api.db, "get_template", return_value=None),
            patch.object(outreach_api.db, "upsert_template") as upsert,
        ):
            with self.assertRaises(HTTPException) as raised:
                outreach_api.update_template(42, self.body, org_id="org-2")

        self.assertEqual(raised.exception.status_code, 404)
        upsert.assert_not_called()

    def test_update_and_delete_are_scoped_to_org(self):
        with (
            patch.object(
                outreach_api.db,
                "get_template",
                return_value={"id": 42},
            ) as get_template,
            patch.object(outreach_api.db, "upsert_template") as upsert,
            patch.object(outreach_api.db, "delete_template") as delete,
        ):
            result = outreach_api.update_template(42, self.body, org_id="org-1")
            deleted = outreach_api.delete_template(42, org_id="org-1")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(deleted, {"ok": True})
        self.assertEqual(get_template.call_args_list, [
            call(42, org_id="org-1"),
            call(42, org_id="org-1"),
        ])
        upsert.assert_called_once_with(
            "Organization intro",
            "Hello {{business_name}}",
            "Hi {{first_name}},\n\n{{hook}}",
            template_id=42,
            org_id="org-1",
            step_number=1,
        )
        delete.assert_called_once_with(42, org_id="org-1")

    def test_updating_step_swaps_with_occupied_template(self):
        connection = Mock()
        connection.execute.side_effect = [
            Mock(fetchone=Mock(
                return_value={"id": 42, "step_number": 1},
            )),
            Mock(fetchone=Mock(return_value={"id": 43})),
            Mock(),
            Mock(),
            Mock(),
            Mock(),
        ]
        get_conn = MagicMock()
        get_conn.return_value.__enter__.return_value = connection

        with patch.object(db, "get_conn", get_conn):
            db.upsert_template(
                "Organization intro",
                "Hello",
                "Body",
                template_id=42,
                org_id="org-1",
                step_number=2,
            )

        calls = connection.execute.call_args_list
        self.assertIn("SET step_number=NULL", calls[2].args[0])
        self.assertEqual(calls[2].args[1], (42,))
        self.assertIn("SET step_number=%s", calls[3].args[0])
        self.assertEqual(calls[3].args[1], (1, 43))
        self.assertIn("step_number=COALESCE(%s, step_number)", calls[4].args[0])
        self.assertEqual(calls[4].args[1][3], 2)


if __name__ == "__main__":
    unittest.main()
