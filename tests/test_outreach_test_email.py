import unittest
from unittest.mock import patch

import outreach
from api.routers import outreach as outreach_api
from fastapi import HTTPException
from pydantic import ValidationError


class OutreachTestEmailTests(unittest.TestCase):
    def setUp(self):
        self.account = {
            "id": 7,
            "status": "active",
            "from_email": "sender@example.com",
            "from_name": "Test Sender",
            "reply_to": "replies@example.com",
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "sender@example.com",
            "smtp_password": "secret",
            "smtp_use_tls": True,
        }
        self.template = {
            "id": 12,
            "subject": "Hello {{business_name}}",
            "body": "Hi {{first_name}}. {{hook}}",
        }

    def test_sends_selected_template_to_test_recipient(self):
        body = outreach_api.TestEmailBody(
            to_email="owner@example.com",
            template_id=12,
        )
        with (
            patch.object(
                outreach_api.db, "get_email_account", return_value=self.account
            ) as get_account,
            patch.object(
                outreach_api.db, "get_template", return_value=self.template
            ) as get_template,
            patch.object(outreach, "send_email", return_value="<test-message-id>") as send,
            patch.object(outreach_api.db, "enqueue_outbound") as enqueue,
        ):
            result = outreach_api.send_test_email(7, body, org_id="org-1")

        self.assertEqual(result, {"ok": True, "message_id": "<test-message-id>"})
        get_account.assert_called_once_with(7, org_id="org-1", decrypt=True)
        get_template.assert_called_once_with(12, org_id="org-1")
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["to"], "owner@example.com")
        self.assertEqual(kwargs["subject"], "Hello Test Clinic")
        self.assertIn("This is a test email", kwargs["body"])
        self.assertEqual(kwargs["from_addr"], "Test Sender <sender@example.com>")
        self.assertEqual(kwargs["reply_to"], "replies@example.com")
        enqueue.assert_not_called()

    def test_rejects_malformed_test_recipient(self):
        with self.assertRaises(ValidationError):
            outreach_api.TestEmailBody(
                to_email="not-an-email",
                template_id=12,
            )

    def test_requires_existing_template(self):
        body = outreach_api.TestEmailBody(
            to_email="owner@example.com",
            template_id=12,
        )
        with (
            patch.object(
                outreach_api.db, "get_email_account", return_value=self.account
            ),
            patch.object(outreach_api.db, "get_template", return_value=None),
            patch.object(outreach, "send_email") as send,
        ):
            with self.assertRaises(HTTPException) as raised:
                outreach_api.send_test_email(7, body, org_id="org-1")

        self.assertEqual(raised.exception.status_code, 404)
        send.assert_not_called()

    def test_list_templates_seeds_default_templates_when_missing(self):
        with patch.object(outreach, "seed_default_templates") as seed, patch.object(
            outreach_api.db, "list_templates", return_value=[{"id": 1, "step_number": 1}]
        ) as list_templates:
            result = outreach_api.list_templates(org_id="org-1")

        self.assertEqual(result, [{"id": 1, "step_number": 1}])
        seed.assert_called_once_with(org_id="org-1")
        list_templates.assert_called_once_with(org_id="org-1")

    def test_reports_smtp_failure_without_recording_a_send(self):
        body = outreach_api.TestEmailBody(
            to_email="owner@example.com",
            template_id=12,
        )
        with (
            patch.object(
                outreach_api.db, "get_email_account", return_value=self.account
            ),
            patch.object(
                outreach_api.db, "get_template", return_value=self.template
            ),
            patch.object(outreach, "send_email", side_effect=OSError("connection refused")),
            patch.object(outreach_api.db, "log_event") as log_event,
        ):
            with self.assertRaises(HTTPException) as raised:
                outreach_api.send_test_email(7, body, org_id="org-1")

        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("connection refused", raised.exception.detail)
        log_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
