import unittest
from unittest.mock import Mock, patch

from api.routers import outreach as outreach_api
from fastapi import HTTPException


class OutreachImapAccountTests(unittest.TestCase):
    def test_create_account_persists_imap_settings(self):
        body = outreach_api.AccountCreate(
            from_email="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_password="smtp-secret",
            imap_host="imap.example.com",
            imap_port=993,
            imap_user="inbox@example.com",
            imap_password="imap-secret",
            imap_mailbox="INBOX",
        )
        with patch.object(
            outreach_api.db, "create_email_account", return_value=7
        ) as create:
            result = outreach_api.create_account(body, org_id="org-1")

        self.assertEqual(result, {"id": 7})
        self.assertEqual(create.call_args.kwargs["imap_host"], "imap.example.com")
        self.assertEqual(create.call_args.kwargs["imap_port"], 993)
        self.assertEqual(create.call_args.kwargs["imap_user"], "inbox@example.com")
        self.assertEqual(create.call_args.kwargs["imap_password"], "imap-secret")
        self.assertEqual(create.call_args.kwargs["imap_mailbox"], "INBOX")

    def test_test_imap_account_checks_mailbox_read_only(self):
        account = {
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_user": "inbox@example.com",
            "imap_password": "secret",
            "imap_mailbox": "INBOX",
        }
        connection = Mock()
        connection.select.return_value = ("OK", [b"0"])
        connection.logout.return_value = ("BYE", [])
        with (
            patch.object(outreach_api.db, "get_email_account", return_value=account),
            patch("imaplib.IMAP4_SSL", return_value=connection),
        ):
            result = outreach_api.test_imap_account(7, org_id="org-1")

        self.assertEqual(result, {"imap_ok": True, "mailbox": "INBOX"})
        connection.login.assert_called_once_with("inbox@example.com", "secret")
        connection.select.assert_called_once_with("INBOX", readonly=True)
        connection.logout.assert_called_once_with()

    def test_test_imap_account_rejects_missing_configuration(self):
        account = {"imap_host": None, "imap_user": None, "imap_password": None}
        with patch.object(outreach_api.db, "get_email_account", return_value=account):
            with self.assertRaises(HTTPException) as raised:
                outreach_api.test_imap_account(7, org_id="org-1")

        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
