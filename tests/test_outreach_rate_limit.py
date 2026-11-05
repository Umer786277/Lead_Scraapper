import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import db
import outreach
from api.routers import outreach as outreach_api
from api.routers.outreach import (
    OutreachDomainLimitBody,
    OutreachHourlyAllocationBody,
    OutreachJitterBody,
    OutreachRateBody,
)
from pydantic import ValidationError


class OutreachRateLimitTests(unittest.TestCase):
    def run_queue(self, smtp_result):
        item = {
            "send_id": 17,
            "lead_id": 23,
            "campaign_id": 5,
            "step_number": 1,
            "subject": "Hello {{business_name}}",
            "body": "Hello",
            "email": "recipient@example.com",
            "lead_status": "new",
            "do_not_contact": False,
            "account_id": None,
        }
        slot_intervals = []

        @contextmanager
        def dispatch_slot(org_id, hourly_limit, claimed_send_id=None):
            slot_intervals.append((org_id, hourly_limit, claimed_send_id))
            yield

        outbound_item = {
            "id": 99,
            "org_id": "org-id",
            "source": "campaign",
            "lead_id": item["lead_id"],
            "campaign_id": item["campaign_id"],
            "send_id": item["send_id"],
            "thread_id": None,
            "draft_id": None,
            "pending_reply_id": None,
            "to_email": item["email"],
            "subject": item["subject"],
            "body": item["body"],
            "reply_to": None,
            "in_reply_to": None,
            "references_hdr": None,
            "account_id": None,
            "note": "step 1",
            "attempts": 1,
            "lead_status": "new",
            "business_name": "Clinic",
            "step_number": 1,
        }
        claim_calls = {"n": 0}

        def claim_outbound(**_kwargs):
            claim_calls["n"] += 1
            return [outbound_item] if claim_calls["n"] == 1 else []

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(db, "is_outreach_paused", return_value=False))
        stack.enter_context(patch.object(
            db, "outreach_dispatch_status",
            return_value={"pause_active": False, "paused_until": None, "pause_reason": None},
        ))
        stack.enter_context(patch.object(db, "get_outreach_rate", return_value=60))
        stack.enter_context(patch.object(db, "get_hourly_allocation_limit", return_value=60))
        stack.enter_context(patch.object(db, "get_send_max_per_domain_per_hour", return_value=10))
        stack.enter_context(patch.object(db, "get_send_jitter_max_seconds", return_value=0))
        stack.enter_context(patch.object(db, "count_sent_since", return_value=0))
        stack.enter_context(patch.object(db, "list_email_accounts", return_value=[{"id": 1}]))
        stack.enter_context(patch.object(db, "claim_due_sends", return_value=[item]))
        stack.enter_context(patch.object(
            db, "enqueue_outbound",
            return_value={"id": 99, "created": True, "status": "pending"},
        ))
        stack.enter_context(patch.object(db, "claim_due_outbound", side_effect=claim_outbound))
        stack.enter_context(patch.object(db, "suppression_reason", return_value=None))
        stack.enter_context(patch.object(db, "reserve_outbound_domain", return_value=True))
        stack.enter_context(patch.object(db, "release_outbound_domain"))
        stack.enter_context(patch.object(
            db, "outbound_provider_status",
            return_value={"pause_active": False, "paused_until": None, "pause_reason": None},
        ))
        stack.enter_context(patch.object(
            db, "pause_outbound_provider",
            return_value=datetime(2030, 1, 1, tzinfo=timezone.utc),
        ))
        stack.enter_context(patch.object(
            db, "release_outbound_claim_to_retry", return_value="retry",
        ))
        stack.enter_context(patch.object(db, "set_outbound_message_id"))
        stack.enter_context(patch.object(db, "mark_outbound_sent"))
        stack.enter_context(patch.object(db, "mark_outbound_retry"))
        stack.enter_context(patch.object(db, "mark_outbound_failed"))
        stack.enter_context(patch.object(db, "release_outbound_claim"))
        stack.enter_context(patch.object(db, "predecessor_send_status", return_value=None))
        stack.enter_context(patch.object(
            db, "get_lead",
            return_value={"business_name": "Clinic", "status": "new", "email": item["email"]},
        ))
        stack.enter_context(patch.object(db, "outreach_dispatch_slot", side_effect=dispatch_slot))
        stack.enter_context(patch.object(db, "release_send_claim"))
        stack.enter_context(patch.object(db, "mark_send"))
        stack.enter_context(patch.object(db, "log_event"))
        stack.enter_context(patch.object(db, "touch_email_account"))
        stack.enter_context(patch.object(db, "update_lead_status"))
        stack.enter_context(patch.object(db, "reschedule_send"))
        stack.enter_context(patch.object(outreach, "_env_int", return_value=0))
        stack.enter_context(patch.object(outreach, "ensure_opener", side_effect=lambda lead: lead))
        stack.enter_context(patch.object(
            outreach, "resolve_sender",
            return_value=(Mock(host="smtp.example.com", port=587),
                          "sender@example.com", None, None, None),
        ))
        stack.enter_context(patch.object(outreach, "release_sender"))
        send_patch = (
            {"side_effect": smtp_result}
            if isinstance(smtp_result, BaseException)
            else {"return_value": smtp_result}
        )
        stack.enter_context(patch.object(outreach, "send_email", **send_patch))
        stack.enter_context(patch("timezones.is_business_hours", return_value=True))

        summary = outreach.process_queue(
            limit=1,
            org_id="org-id",
        )
        return summary, slot_intervals

    def test_rate_setting_validation(self):
        self.assertEqual(
            OutreachRateBody(max_emails_per_minute=20).max_emails_per_minute,
            20,
        )
        for value in (0, 61, 20.5):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                OutreachRateBody(max_emails_per_minute=value)

    def test_rate_maps_to_fixed_interval(self):
        summary, intervals = self.run_queue("message-id")
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(intervals, [("org-id", 60, 17)])

    def test_send_jitter_validation(self):
        self.assertEqual(
            OutreachJitterBody(send_jitter_max_seconds=0).send_jitter_max_seconds,
            0,
        )
        for value in (-1, 3601, 1.5):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                OutreachJitterBody(send_jitter_max_seconds=value)

    def test_domain_hourly_limit_validation(self):
        self.assertEqual(
            OutreachDomainLimitBody(
                send_max_per_domain_per_hour=10,
            ).send_max_per_domain_per_hour,
            10,
        )
        for value in (0, 10001, 1.5, True):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                OutreachDomainLimitBody(send_max_per_domain_per_hour=value)

    def test_domain_hourly_limit_reads_and_saves_org_setting(self):
        with patch.object(
            db, "get_setting", return_value="25",
        ) as get_setting:
            limit = db.get_send_max_per_domain_per_hour("org-id")
        self.assertEqual(limit, 25)
        get_setting.assert_called_once_with(
            "send_max_per_domain_per_hour", "10", org_id="org-id",
        )

        with patch.object(db, "set_setting") as set_setting:
            db.set_send_max_per_domain_per_hour("org-id", 75)
        set_setting.assert_called_once_with(
            "send_max_per_domain_per_hour", 75, org_id="org-id",
        )

    def test_domain_hourly_limit_defaults_and_rejects_invalid_settings(self):
        with patch.object(db, "get_setting", return_value="10"):
            self.assertEqual(db.get_send_max_per_domain_per_hour("org-id"), 10)
        for value in (0, 10001, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                db.set_send_max_per_domain_per_hour("org-id", value)
        with patch.object(db, "get_setting", return_value="not-a-number"):
            with self.assertRaises(RuntimeError):
                db.get_send_max_per_domain_per_hour("org-id")

    def test_domain_hourly_limit_api_saves_for_active_organization(self):
        body = OutreachDomainLimitBody(send_max_per_domain_per_hour=42)
        with patch.object(
            db, "set_send_max_per_domain_per_hour",
        ) as save_limit:
            response = outreach_api.update_domain_hourly_limit(body, org_id="org-id")

        self.assertEqual(response, {"send_max_per_domain_per_hour": 42})
        save_limit.assert_called_once_with("org-id", 42)

    def test_send_jitter_sleeps_within_configured_maximum(self):
        with patch.object(outreach.random, "uniform", return_value=7.5) as uniform, \
                patch.object(outreach.time, "sleep") as sleep:
            outreach._sleep_send_jitter(30)
        uniform.assert_called_once_with(0, 30)
        sleep.assert_called_once_with(7.5)

    def test_send_jitter_disabled_does_not_sleep(self):
        with patch.object(outreach.time, "sleep") as sleep:
            outreach._sleep_send_jitter(0)
        sleep.assert_not_called()

    def test_hourly_allocation_interval(self):
        self.assertEqual(db.outreach_slot_interval(60), 60.0)
        self.assertEqual(db.outreach_slot_interval(3600), 1.0)
        self.assertEqual(db.outreach_slot_interval(1), 3600.0)
        with self.assertRaises(ValidationError):
            OutreachHourlyAllocationBody(hourly_allocation_limit=3601)

    def test_static_slot_selection_uses_database_hour_windows(self):
        now = datetime(2030, 1, 1, 12, 5, 20, tzinfo=timezone.utc)
        slot_hour, slot_index, slot_start, slot_end = db.select_outreach_slot(
            now,
            60,
        )
        self.assertEqual(slot_hour, datetime(2030, 1, 1, 12, tzinfo=timezone.utc))
        self.assertEqual(slot_index, 6)
        self.assertEqual(slot_start, datetime(2030, 1, 1, 12, 6, tzinfo=timezone.utc))
        self.assertEqual(slot_end, datetime(2030, 1, 1, 12, 7, tzinfo=timezone.utc))

        next_hour, next_index, next_start, _ = db.select_outreach_slot(
            now,
            60,
            allocation_hour=slot_hour,
            allocated_count=60,
        )
        self.assertEqual(next_hour, datetime(2030, 1, 1, 13, tzinfo=timezone.utc))
        self.assertEqual(next_index, 0)
        self.assertEqual(next_start, datetime(2030, 1, 1, 13, tzinfo=timezone.utc))

    def test_expired_slot_is_deferred_without_retrying_reservations(self):
        db_now = datetime(2030, 1, 1, 12, 5, 20, tzinfo=timezone.utc)
        slot_start = datetime(2030, 1, 1, 12, 6, tzinfo=timezone.utc)
        slot_end = datetime(2030, 1, 1, 12, 7, tzinfo=timezone.utc)

        with patch.object(
            db,
            "_reserve_outreach_slot",
            return_value=(slot_start, slot_end, db_now),
        ) as reserve:
            with patch.object(db.time, "monotonic", side_effect=(0, 100)):
                with patch.object(db.time, "sleep") as sleep:
                    with self.assertRaises(db.OutreachDispatchBusy):
                        with db.outreach_dispatch_slot("org-id", 60):
                            self.fail("An expired slot must not be used")

        reserve.assert_called_once_with("org-id", 60)
        sleep.assert_not_called()

    def test_claim_refresh_uses_an_independent_database_connection(self):
        connections = []

        class FakeConnection:
            def __init__(self):
                self.statements = []

            def execute(self, query, params=()):
                self.statements.append((" ".join(query.split()), params))
                return Mock(rowcount=1)

        @contextmanager
        def fake_connection():
            connection = FakeConnection()
            connections.append(connection)
            yield connection

        with patch.object(db, "get_conn", side_effect=fake_connection):
            with db.get_conn() as dispatch_connection:
                db.refresh_send_claim(17, org_id="org-id")

        self.assertEqual(len(connections), 2)
        self.assertIsNot(connections[0], connections[1])
        self.assertIs(dispatch_connection, connections[0])
        self.assertIn("UPDATE scheduled_sends SET claimed_at=%s", connections[1].statements[0][0])

    def test_smtp_throttle_defers_send_and_pauses_dispatch(self):
        throttled = outreach.smtplib.SMTPDataError(421, b"try later")
        summary, _ = self.run_queue(throttled)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["deferred"], 1)
        self.assertFalse(summary["paused"])
        db.pause_outbound_provider.assert_called_once_with(
            "org-id", "smtp.example.com:587", 300,
            "SMTP 421 temporary provider response",
        )
        db.release_outbound_claim_to_retry.assert_called_once()
        db.reschedule_send.assert_not_called()
        db.mark_send.assert_called_once_with(
            17, "queued", org_id="org-id",
        )

    def test_smtp_throttle_is_persisted_inside_dispatch_slot(self):
        db_now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        paused_until = db_now + timedelta(minutes=5)
        with patch.object(
            db, "_reserve_outreach_slot",
            return_value=(db_now, db_now + timedelta(minutes=1), db_now),
        ), patch.object(db.time, "monotonic", side_effect=(0, 0)), \
                patch.object(
                    db, "_extend_outreach_pause", return_value=paused_until,
                ) as extend_pause:
            with self.assertRaises(db.OutreachDispatchThrottled) as raised:
                with db.outreach_dispatch_slot("org-id", 60):
                    raise outreach.smtplib.SMTPDataError(451, b"try later")

        self.assertEqual(raised.exception.smtp_code, 451)
        self.assertEqual(raised.exception.paused_until, paused_until)
        extend_pause.assert_called_once()
        self.assertEqual(extend_pause.call_args.args[1], "org-id")

    def test_exception_classification(self):
        import smtplib

        self.assertEqual(
            outreach._classify_send_exception(
                smtplib.SMTPDataError(451, b"try later")
            ),
            "retry",
        )
        self.assertEqual(
            outreach._classify_send_exception(
                smtplib.SMTPRecipientsRefused({"a@b.c": (550, b"no such user")})
            ),
            "failed",
        )
        self.assertEqual(
            outreach._classify_send_exception(smtplib.SMTPServerDisconnected("gone")),
            "unknown",
        )

    def test_send_now_only_enqueues(self):
        template = {
            "id": 1,
            "name": "Intro",
            "subject": "Hi {{business_name}}",
            "body": "Hello",
        }
        lead = {"id": 23, "email": "recipient@example.com", "status": "new",
                "business_name": "Clinic"}
        with patch.object(db, "is_outreach_paused", return_value=False), \
                patch.object(db, "outreach_dispatch_status",
                             return_value={"pause_active": False, "paused_until": None}), \
                patch.object(db, "get_send_jitter_max_seconds", return_value=0), \
                patch.object(db, "get_template", return_value=template), \
                patch.object(db, "list_templates") as list_templates, \
                patch.object(db, "get_lead", return_value=lead), \
                patch.object(db, "enqueue_outbound",
                             return_value={"id": 1, "created": True, "status": "pending"}) as enqueue, \
                patch.object(outreach, "ensure_opener") as ensure_opener, \
                patch.object(outreach, "send_email") as send_email, \
                patch.object(outreach.time, "sleep") as sleep:
            summary = outreach.send_now(
                smtp=Mock(host="smtp.example.com"),
                from_addr="sender@example.com",
                lead_ids=[23],
                template_id=1,
                org_id="org-id",
            )

        self.assertEqual(summary["queued"], 1)
        self.assertEqual(summary["sent"], 0)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["subject"], template["subject"])
        self.assertEqual(enqueue.call_args.kwargs["body"], template["body"])
        ensure_opener.assert_not_called()
        send_email.assert_not_called()
        list_templates.assert_not_called()
        sleep.assert_not_called()

    def test_send_now_requires_a_selected_template(self):
        with patch.object(db, "is_outreach_paused", return_value=False), \
                patch.object(db, "outreach_dispatch_status",
                             return_value={"pause_active": False, "paused_until": None}), \
                patch.object(db, "get_send_jitter_max_seconds", return_value=0), \
                patch.object(db, "get_template") as get_template:
            summary = outreach.send_now(
                lead_ids=[23],
                org_id="org-id",
            )

        self.assertEqual(summary["error"], "Select a valid email script before sending.")
        get_template.assert_not_called()

    def test_campaign_enqueue_does_not_run_opener_generation(self):
        scheduled_item = {
            "send_id": 17,
            "lead_id": 23,
            "campaign_id": 5,
            "step_number": 1,
            "subject": "Hello {{business_name}}",
            "body": "Hello {{first_name}}",
            "email": "recipient@example.com",
            "lead_status": "new",
            "do_not_contact": False,
        }
        lead = {
            "business_name": "Clinic",
            "first_name": "Sam",
            "status": "new",
            "email": "recipient@example.com",
        }
        with patch.object(db, "is_outreach_paused", return_value=False), \
                patch.object(db, "outreach_dispatch_status",
                             return_value={"pause_active": False, "paused_until": None}), \
                patch.object(db, "get_hourly_allocation_limit", return_value=60), \
                patch.object(db, "count_sent_since", return_value=0), \
                patch.object(db, "get_send_jitter_max_seconds", return_value=0), \
                patch.object(db, "list_email_accounts", return_value=[{"id": 1}]), \
                patch.object(db, "claim_due_sends", side_effect=[ [scheduled_item], [] ]), \
                patch.object(db, "predecessor_send_status", return_value=None), \
                patch.object(db, "get_lead", return_value=lead), \
                patch.object(db, "enqueue_outbound",
                             return_value={"id": 99, "created": True, "status": "pending"}) as enqueue, \
                patch.object(db, "mark_send") as mark_send, \
                patch.object(outreach, "_env_int", return_value=0), \
                patch.object(outreach, "ensure_opener") as ensure_opener, \
                patch("timezones.is_business_hours", return_value=True):
            summary = outreach.enqueue_due_sends(limit=1, org_id="org-id")

        self.assertEqual(summary["queued"], 1)
        enqueue_args = enqueue.call_args.kwargs
        self.assertEqual(enqueue_args["subject"], scheduled_item["subject"])
        self.assertEqual(enqueue_args["body"], scheduled_item["body"])
        ensure_opener.assert_not_called()
        mark_send.assert_called_once_with(17, "queued", org_id="org-id")

    def test_send_endpoint_queues_without_smtp(self):
        from api.routers import outreach as outreach_api

        smtp = Mock(host="smtp.example.com", username="sender@example.com")
        with patch.object(outreach.SMTPConfig, "from_env", return_value=smtp), \
                patch.object(outreach, "enqueue_due_sends",
                             return_value={"queued": 2}) as enqueue, \
                patch.object(outreach, "send_email") as send_email:
            result = outreach_api.trigger_send(
                dry_run=False, limit=500, org_id="org-id",
            )

        self.assertEqual(result["queued"], 2)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["limit"], 200)
        send_email.assert_not_called()

    def test_send_now_endpoint_queues_without_smtp(self):
        from api.routers import outreach as outreach_api

        smtp = Mock(host="smtp.example.com", username="sender@example.com")
        body = outreach_api.SendNowBody(lead_ids=[23], template_id=1)
        with patch.object(outreach.SMTPConfig, "from_env", return_value=smtp), \
                patch.object(outreach, "send_now",
                             return_value={"queued": 1, "sent": 0}) as queue_send, \
                patch.object(outreach, "send_email") as send_email:
            result = outreach_api.send_now_endpoint(body, org_id="org-id")

        self.assertEqual(result, {"queued": 1, "sent": 0})
        queue_send.assert_called_once()
        self.assertEqual(queue_send.call_args.args[2], [23])
        self.assertEqual(queue_send.call_args.kwargs["template_id"], 1)
        send_email.assert_not_called()

    def test_send_draft_endpoint_only_enqueues(self):
        from api.routers import outreach as outreach_api

        thread = {"id": 3, "lead_id": 23, "email": "vendor@example.com"}
        draft = {"id": 7, "thread_id": 3, "status": "pending"}
        with patch.object(db, "get_thread", return_value=thread), \
                patch.object(db, "get_draft", return_value=draft), \
                patch.object(outreach, "enqueue_draft_reply",
                             return_value={"id": 9, "created": True, "status": "pending"}) as enqueue, \
                patch.object(outreach, "send_email") as send_email:
            result = outreach_api.send_draft(
                3, 7,
                outreach_api.SendDraftBody(subject="Re: hi", body="Hello"),
                org_id="org-id",
            )

        self.assertEqual(result, {
            "ok": True, "queued": True, "outbound_id": 9, "message_id": None,
        })
        enqueue.assert_called_once()
        send_email.assert_not_called()

    def test_organization_invitation_is_queued(self):
        from uuid import UUID
        from api.routers import orgs as orgs_api

        invitation_id = UUID("00000000-0000-0000-0000-000000000123")
        with patch.dict("os.environ", {
            "APP_BASE_URL": "https://app.example.com",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_FROM": "sender@example.com",
        }, clear=True), \
                patch.object(orgs_api.db, "get_membership_role", return_value="admin"), \
                patch.object(orgs_api.db, "is_super_admin", return_value=False), \
                patch.object(orgs_api.db, "membership_email_exists", return_value=False), \
                patch.object(orgs_api.db, "pending_invitation_exists", return_value=False), \
                patch.object(orgs_api.db, "list_email_accounts", return_value=[]), \
                patch.object(orgs_api.db, "create_org_invitation") as create_invitation, \
                patch.object(orgs_api.db, "hash_invitation_token", return_value="hashed-token"), \
                patch.object(orgs_api.db, "enqueue_outbound",
                             return_value={"id": 55, "created": True, "status": "pending"}) as enqueue, \
                patch.object(orgs_api.db, "discard_org_invitation") as discard_invitation, \
                patch.object(orgs_api, "uuid4", return_value=invitation_id), \
                patch.object(orgs_api.secrets, "token_urlsafe", return_value="opaque-token"), \
                patch.object(outreach, "send_email") as send_email:
            result = orgs_api.invite_member(
                orgs_api.MemberInvite(email="person@example.com", role="user"),
                org_id="org-id",
                user_id="admin-id",
            )

        self.assertTrue(result["email_queued"])
        self.assertEqual(result["email"], "person@example.com")
        create_invitation.assert_called_once()
        enqueue.assert_called_once()
        enqueue_args = enqueue.call_args.kwargs
        self.assertEqual(enqueue_args["org_id"], "org-id")
        self.assertEqual(enqueue_args["source"], "organization_invitation")
        self.assertEqual(enqueue_args["to_email"], "person@example.com")
        self.assertEqual(
            enqueue_args["idempotency_key"],
            f"organization-invitation:{invitation_id}",
        )
        self.assertIn("https://app.example.com/login?invite=opaque-token", enqueue_args["body"])
        send_email.assert_not_called()
        discard_invitation.assert_not_called()

    def test_invitation_can_use_shared_smtp_for_non_default_org(self):
        smtp = Mock(host="smtp.example.com")
        with patch.object(db, "reserve_email_account", return_value=None), \
                patch.object(db, "list_email_accounts", return_value=[]), \
                patch.object(outreach.SMTPConfig, "from_env", return_value=smtp):
            resolved = outreach.resolve_sender(
                "non-default-org",
                from_addr="sender@example.com",
                allow_env_fallback=True,
            )
            blocked = outreach.resolve_sender(
                "non-default-org",
                from_addr="sender@example.com",
            )

        self.assertIs(resolved[0], smtp)
        self.assertEqual(resolved[1], "sender@example.com")
        self.assertEqual(blocked, (None, None, None, None, None))

    def test_outbound_delivery_reuses_persisted_message_id(self):
        item = {
            "id": 99,
            "org_id": "org-id",
            "source": "campaign",
            "lead_id": 23,
            "send_id": 17,
            "to_email": "recipient@example.com",
            "subject": "Hello",
            "body": "Body",
            "message_id": "<persisted@example.com>",
            "lead_status": "contacted",
        }
        order = []

        @contextmanager
        def dispatch_slot(*_args, **_kwargs):
            order.append("slot")
            yield

        def resolve_sender(*_args, **_kwargs):
            self.assertEqual(order, ["slot"])
            return Mock(host="smtp.example.com", port=587), "sender@example.com", None, 4, {
                "_reservation_key": "outbound:99",
            }

        def send_email(**kwargs):
            order.append("send")
            self.assertEqual(kwargs["message_id"], "<persisted@example.com>")
            return kwargs["message_id"]

        with patch.object(db, "suppression_reason", return_value=None), \
                patch.object(db, "get_lead", return_value={
                    "id": 23, "business_name": "Clinic", "status": "contacted",
                }), \
                patch.object(db, "outreach_dispatch_slot", dispatch_slot), \
                patch.object(db, "get_send_max_per_domain_per_hour", return_value=10), \
                patch.object(db, "reserve_outbound_domain", return_value=True), \
                patch.object(db, "release_outbound_domain"), \
                patch.object(db, "outbound_provider_status",
                             return_value={"pause_active": False}), \
                patch.object(outreach, "resolve_sender", side_effect=resolve_sender), \
                patch.object(outreach, "_message_id_for") as make_message_id, \
                patch.object(db, "set_outbound_message_id") as persist_message_id, \
                patch.object(outreach, "send_email", side_effect=send_email), \
                patch.object(outreach, "release_sender"), \
                patch.object(db, "mark_outbound_sent") as mark_sent, \
                patch.object(db, "mark_send"), \
                patch.object(db, "log_event"):
            result = outreach._deliver_outbound(item, hourly_allocation_limit=60)

        self.assertEqual(result[0], "sent")
        self.assertEqual(order, ["slot", "send"])
        make_message_id.assert_not_called()
        persist_message_id.assert_called_once_with(99, "<persisted@example.com>")
        mark_sent.assert_called_once_with(
            99, account_id=4, message_id="<persisted@example.com>",
        )

    def test_approved_draft_is_sent_and_marked_through_outbox(self):
        item = {
            "id": 102,
            "org_id": "org-id",
            "source": "draft_reply",
            "thread_id": 3,
            "draft_id": 7,
            "to_email": "recipient@example.com",
            "subject": "Re: Hello",
            "body": "Thanks for the details.",
        }

        @contextmanager
        def dispatch_slot(*_args, **_kwargs):
            yield

        with patch.object(db, "suppression_reason", return_value=None), \
                patch.object(db, "outreach_dispatch_slot", dispatch_slot), \
                patch.object(db, "get_send_max_per_domain_per_hour", return_value=10), \
                patch.object(db, "reserve_outbound_domain", return_value=True), \
                patch.object(db, "release_outbound_domain"), \
                patch.object(db, "outbound_provider_status",
                             return_value={"pause_active": False}), \
                patch.object(outreach, "resolve_sender", return_value=(
                    Mock(host="smtp.example.com", port=587),
                    "sender@example.com", None, 4, None,
                )), \
                patch.object(db, "set_outbound_message_id"), \
                patch.object(outreach, "send_email",
                             return_value="<draft@example.com>") as send, \
                patch.object(outreach, "release_sender"), \
                patch.object(db, "mark_outbound_sent") as mark_sent, \
                patch.object(db, "insert_thread_message") as insert_message, \
                patch.object(db, "update_draft") as update_draft, \
                patch.object(db, "update_thread") as update_thread, \
                patch.object(db, "touch_email_account"):
            result = outreach._deliver_outbound(item, hourly_allocation_limit=60)

        self.assertEqual(result[0], "sent")
        send.assert_called_once()
        mark_sent.assert_called_once_with(
            102, account_id=4, message_id="<draft@example.com>",
        )
        insert_message.assert_called_once()
        update_draft.assert_called_once_with(7, org_id="org-id", status="sent")
        update_thread.assert_called_once_with(
            3, org_id="org-id", last_intent="human_replied",
        )

    def test_send_now_opener_and_rendering_run_in_outbox_worker(self):
        item = {
            "id": 100,
            "org_id": "org-id",
            "source": "send_now",
            "lead_id": 23,
            "to_email": "recipient@example.com",
            "subject": "Hello {{first_name}}",
            "body": "Hi {{first_name}}, {{hook}}",
            "lead_status": "new",
        }
        order = []

        @contextmanager
        def dispatch_slot(*_args, **_kwargs):
            order.append("slot")
            yield

        def prepare_opener(lead):
            order.append("opener")
            lead["first_name"] = "Sam"
            lead["hook"] = "Following up on your clinic."
            return lead

        def send_email(**kwargs):
            order.append("send")
            self.assertEqual(kwargs["subject"], "Hello Sam")
            self.assertEqual(kwargs["body"], "Hi Sam, Following up on your clinic.")
            return "<worker@example.com>"

        with patch.object(db, "suppression_reason", return_value=None), \
                patch.object(db, "get_lead", return_value={"id": 23}), \
                patch.object(outreach, "ensure_opener", side_effect=prepare_opener), \
                patch.object(db, "outreach_dispatch_slot", dispatch_slot), \
                patch.object(db, "get_send_max_per_domain_per_hour", return_value=10), \
                patch.object(outreach, "resolve_sender", return_value=(
                    Mock(host="smtp.example.com", port=587),
                    "sender@example.com", None, 4, None,
                )), \
                patch.object(db, "reserve_outbound_domain", return_value=True), \
                patch.object(db, "release_outbound_domain"), \
                patch.object(db, "outbound_provider_status",
                             return_value={"pause_active": False}), \
                patch.object(outreach, "_message_id_for", return_value="<worker@example.com>"), \
                patch.object(db, "set_outbound_message_id"), \
                patch.object(outreach, "send_email", side_effect=send_email), \
                patch.object(outreach, "release_sender"), \
                patch.object(db, "mark_outbound_sent"), \
                patch.object(db, "mark_send"), \
                patch.object(db, "log_event"), \
                patch.object(db, "update_lead_status"):
            result = outreach._deliver_outbound(item, hourly_allocation_limit=60)

        self.assertEqual(result[0], "sent")
        self.assertEqual(order, ["opener", "slot", "send"])

    def test_outbound_retry_persists_backoff_without_marking_failed(self):
        item = {
            "id": 99,
            "org_id": "org-id",
            "source": "campaign",
            "lead_id": 23,
            "send_id": 17,
            "to_email": "recipient@example.com",
            "subject": "Hello",
            "body": "Body",
            "attempts": 3,
            "lead_status": "contacted",
        }

        @contextmanager
        def dispatch_slot(*_args, **_kwargs):
            yield

        smtp_error = outreach.smtplib.SMTPDataError(450, b"try later")
        with patch.object(db, "suppression_reason", return_value=None), \
                patch.object(db, "get_lead", return_value={
                    "id": 23, "business_name": "Clinic", "status": "contacted",
                }), \
                patch.object(db, "outreach_dispatch_slot", dispatch_slot), \
                patch.object(db, "get_send_max_per_domain_per_hour", return_value=10), \
                patch.object(db, "reserve_outbound_domain", return_value=True), \
                patch.object(db, "release_outbound_domain"), \
                patch.object(db, "outbound_provider_status",
                             return_value={"pause_active": False}), \
                patch.object(outreach, "resolve_sender", return_value=(
                    Mock(host="smtp.example.com", port=587),
                    "sender@example.com", None, 4, None,
                )), \
                patch.object(outreach, "_message_id_for", return_value="<new@example.com>"), \
                patch.object(db, "set_outbound_message_id") as persist_message_id, \
                patch.object(outreach, "send_email", side_effect=smtp_error), \
                patch.object(outreach, "_retry_delay_seconds", return_value=321), \
                patch.object(outreach, "release_sender"), \
                patch.object(db, "mark_outbound_retry") as mark_retry, \
                patch.object(db, "mark_outbound_failed") as mark_failed:
            result = outreach._deliver_outbound(item, hourly_allocation_limit=60)

        self.assertEqual(result[0], "retry")
        persist_message_id.assert_called_once_with(99, "<new@example.com>")
        mark_retry.assert_called_once_with(99, str(smtp_error), 321)
        mark_failed.assert_not_called()

    def test_outbound_retry_dead_letters_at_configured_attempt_limit(self):
        result = Mock()
        result.fetchone.return_value = {"status": "dead_letter"}
        connection = Mock()
        connection.execute.return_value = result

        @contextmanager
        def fake_connection():
            yield connection

        with patch.object(db, "get_conn", side_effect=fake_connection), \
                patch.object(db, "_outbound_max_attempts", return_value=2):
            status = db.mark_outbound_retry(99, "temporary failure", 120)

        self.assertEqual(status, "dead_letter")
        query, params = connection.execute.call_args.args
        self.assertIn("status=CASE WHEN attempts >= %s THEN 'dead_letter'", query)
        self.assertEqual(params[0], 2)
        self.assertEqual(params[3], 2)

    def test_ambiguous_smtp_failure_is_not_retried(self):
        item = {
            "id": 101,
            "org_id": "org-id",
            "source": "manual",
            "to_email": "recipient@example.com",
            "subject": "Hello",
            "body": "Body",
        }

        @contextmanager
        def dispatch_slot(*_args, **_kwargs):
            yield

        error = outreach.smtplib.SMTPServerDisconnected("connection lost after DATA")
        with patch.object(db, "suppression_reason", return_value=None), \
                patch.object(db, "outreach_dispatch_slot", dispatch_slot), \
                patch.object(db, "get_send_max_per_domain_per_hour", return_value=10), \
                patch.object(db, "reserve_outbound_domain", return_value=True), \
                patch.object(db, "release_outbound_domain"), \
                patch.object(db, "outbound_provider_status",
                             return_value={"pause_active": False}), \
                patch.object(outreach, "resolve_sender", return_value=(
                    Mock(host="smtp.example.com", port=587),
                    "sender@example.com", None, 4, None,
                )), \
                patch.object(outreach, "_message_id_for",
                             return_value="<ambiguous@example.com>"), \
                patch.object(db, "set_outbound_message_id"), \
                patch.object(outreach, "send_email", side_effect=error), \
                patch.object(outreach, "release_sender"), \
                patch.object(db, "mark_outbound_failed") as mark_failed, \
                patch.object(db, "mark_outbound_retry") as mark_retry:
            result = outreach._deliver_outbound(item, hourly_allocation_limit=60)

        self.assertEqual(result[0], "unknown")
        mark_failed.assert_called_once_with(101, "unknown", str(error), account_id=4)
        mark_retry.assert_not_called()

    def test_domain_rate_limit_defers_until_capacity_is_available(self):
        available_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
        item = {
            "id": 103,
            "org_id": "org-id",
            "source": "manual",
            "to_email": "recipient@example.com",
            "subject": "Hello",
            "body": "Body",
        }

        @contextmanager
        def dispatch_slot(*_args, **_kwargs):
            yield

        with patch.object(db, "suppression_reason", return_value=None), \
                patch.object(db, "outreach_dispatch_slot", dispatch_slot), \
                patch.object(db, "get_send_max_per_domain_per_hour", return_value=10), \
                patch.object(
                    db, "reserve_outbound_domain",
                    side_effect=db.OutreachDomainRateLimited(
                        available_at, "example.com",
                    ),
                ), patch.object(
                    db, "release_outbound_claim_to_retry", return_value="retry",
                ) as defer, patch.object(outreach, "resolve_sender") as resolve_sender:
            result = outreach._deliver_outbound(item, hourly_allocation_limit=60)

        self.assertEqual(result[0], "deferred")
        defer.assert_called_once_with(
            103,
            "Recipient domain example.com is at its hourly send limit until "
            "2030-01-01T00:00:00+00:00",
            available_at,
        )
        resolve_sender.assert_not_called()

    def test_suppression_is_checked_before_sender_resolution(self):
        item = {
            "id": 99,
            "org_id": "org-id",
            "source": "campaign",
            "lead_id": 23,
            "send_id": 17,
            "to_email": "recipient@example.com",
            "subject": "Hello",
            "body": "Body",
        }
        with patch.object(db, "suppression_reason", return_value="unsubscribed"), \
                patch.object(db, "mark_outbound_failed") as mark_suppressed, \
                patch.object(db, "mark_send") as mark_send, \
                patch.object(db, "log_event"), \
                patch.object(outreach, "resolve_sender") as resolve_sender, \
                patch.object(outreach, "send_email") as send_email:
            result = outreach._deliver_outbound(item)

        self.assertEqual(result, ("skipped", "unsubscribed", False))
        mark_suppressed.assert_called_once_with(99, "suppressed", "unsubscribed")
        mark_send.assert_called_once_with(
            17, "skipped", error="unsubscribed", org_id="org-id",
        )
        resolve_sender.assert_not_called()
        send_email.assert_not_called()

    def test_enqueue_outbound_returns_existing_idempotent_row(self):
        statements = []

        class FakeConnection:
            def execute(self, query, params=()):
                sql = " ".join(query.split())
                statements.append((sql, params))
                result = Mock()
                if sql.startswith("INSERT INTO outbound_messages"):
                    result.fetchone.return_value = None
                else:
                    result.fetchone.return_value = {"id": 99, "status": "sent"}
                return result

        @contextmanager
        def fake_connection():
            yield FakeConnection()

        with patch.object(db, "get_conn", side_effect=fake_connection):
            result = db.enqueue_outbound(
                org_id="org-id",
                source="campaign",
                send_id=17,
                to_email="recipient@example.com",
                subject="Hello",
                body="Body",
                idempotency_key="campaign:17",
            )

        self.assertEqual(result, {"id": 99, "created": False, "status": "sent"})
        self.assertIn("ON CONFLICT (idempotency_key) DO NOTHING", statements[0][0])
        self.assertEqual(statements[0][1][14], "campaign:17")
        self.assertIn("WHERE idempotency_key=%s", statements[1][0])


if __name__ == "__main__":
    unittest.main()
