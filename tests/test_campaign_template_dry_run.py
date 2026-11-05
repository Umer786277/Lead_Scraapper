import json
import unittest
from contextlib import ExitStack, nullcontext
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import db
import outreach


class _Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _EnrollmentConnection:
    def __init__(self, campaign, selection_mode):
        self.campaign = campaign
        self.selection_mode = selection_mode
        self.assignments = {}
        self.scheduled = []

    def execute(self, query, params=()):
        sql = " ".join(query.split())
        if "FROM campaigns WHERE id=%s AND org_id=%s" in sql:
            return _Cursor(self.campaign)
        if "FROM outreach_settings" in sql:
            return _Cursor({"value": self.selection_mode})
        if sql.startswith("UPDATE campaigns SET script_rotation_index="):
            self.campaign["script_rotation_index"] = params[0]
            if "script_rotation_order=" in sql:
                self.campaign["script_rotation_order"] = json.loads(params[1])
            return _Cursor()
        if "FROM campaign_script_assignments" in sql:
            return _Cursor(self.assignments.get((params[0], params[1])))
        if sql.startswith("UPDATE campaigns SET template_selection_mode="):
            self.campaign["template_selection_mode"] = params[0]
            self.campaign["template_cycle_index"] = 0
            self.campaign["template_cycle_order"] = []
            return _Cursor()
        if sql.startswith("UPDATE campaigns SET template_cycle_index="):
            self.campaign["template_cycle_index"] = params[0]
            self.campaign["template_cycle_order"] = json.loads(params[1])
            return _Cursor()
        if sql.startswith("INSERT INTO campaign_script_assignments"):
            assignment = {
                "script_name": params[2],
                "script_snapshot": json.loads(params[3]),
            }
            if len(params) > 4:
                assignment["selected_sequence_id"] = params[4]
                assignment["option_position"] = params[5]
            self.assignments[(params[0], params[1])] = assignment
            return _Cursor()
        if sql.startswith("SELECT 1 FROM leads l"):
            return _Cursor()
        if sql.startswith("INSERT INTO scheduled_sends"):
            self.scheduled.append(params)
            return _Cursor()
        raise AssertionError(f"Unexpected SQL: {sql}")


def _template(sequence_id, name):
    return {
        "sequence_id": sequence_id,
        "name": name,
        "steps": [
            {
                "step_number": step_number,
                "delay_days": 0 if step_number == 1 else 3,
                "template_id": sequence_id * 10 + step_number,
                "template_name": f"{name} step {step_number}",
                "subject": f"{name} subject {step_number}",
                "body": f"{name} body {step_number}",
            }
            for step_number in range(1, 5)
        ],
    }


class CampaignTemplateDryRunTests(unittest.TestCase):
    def test_ten_leads_get_stable_templates_and_automatic_followups(self):
        base = datetime(2026, 1, 5, 10, 0, 0)
        options = [
            _template(41, "Template A"),
            _template(42, "Template B"),
            _template(43, "Template C"),
        ]
        script_variants = [
            {
                "name": option["name"],
                "steps": [
                    {"subject": step["subject"], "body": step["body"]}
                    for step in option["steps"]
                ],
                "script_set_id": option["sequence_id"],
                "script_set_name": option["name"],
                "variant_name": option["name"],
                "option_position": index,
            }
            for index, option in enumerate(options)
        ]
        smtp_config = outreach.SMTPConfig(
            host="mock.smtp.invalid",
            port=587,
            username="",
            password="",
            use_tls=True,
        )
        report_rows = []

        for mode in ("sequential", "random"):
            with self.subTest(mode=mode):
                campaign = {
                    "id": 8,
                    "org_id": "org-test",
                    "script_variants": script_variants,
                    "script_rotation_index": 0,
                    "script_rotation_order": [],
                }
                connection = _EnrollmentConnection(campaign, mode)
                lead_ids = list(range(1001, 1011))
                lead_data = {
                    lead_id: {
                        "id": lead_id,
                        "email": f"lead{lead_id}@example.test",
                        "business_name": f"Lead {lead_id}",
                        "status": "new",
                        "do_not_contact": False,
                        "opener": "",
                    }
                    for lead_id in lead_ids
                }
                with patch("outreach_scripts.random.shuffle", side_effect=lambda values: values.reverse()):
                    enrolled = db._enroll_campaign_leads(
                        connection,
                        campaign,
                        lead_ids,
                        [(index, index, 0 if index == 1 else 3) for index in range(1, 5)],
                        base=base,
                    )

                self.assertEqual(enrolled, lead_ids)
                self.assertEqual(len(connection.assignments), 10)
                self.assertEqual(len(connection.scheduled), 40)

                positions = [
                    connection.assignments[(campaign["id"], lead_id)]["script_snapshot"]["option_position"]
                    for lead_id in lead_ids
                ]
                for cycle_start in range(0, 9, 3):
                    self.assertCountEqual(positions[cycle_start:cycle_start + 3], [0, 1, 2])
                if mode == "sequential":
                    self.assertEqual(positions, [0, 1, 2, 0, 1, 2, 0, 1, 2, 0])

                # Changing options or the org mode after enrollment must not change saved copies.
                connection.selection_mode = "random" if mode == "sequential" else "sequential"
                for lead_id in lead_ids:
                    old_assignment = connection.assignments[(campaign["id"], lead_id)]
                    reassigned = db._assign_campaign_script(
                        connection,
                        campaign["id"],
                        lead_id,
                        campaign["org_id"],
                    )
                    self.assertEqual(
                        reassigned["snapshot"], old_assignment["script_snapshot"],
                    )

                scheduled = []
                for send_id, params in enumerate(connection.scheduled, 1):
                    org_id, campaign_id, step_id, step_number, lead_id, when, script_name = params
                    scheduled.append({
                        "id": send_id,
                        "org_id": org_id,
                        "campaign_id": campaign_id,
                        "step_id": step_id,
                        "step_number": step_number,
                        "lead_id": lead_id,
                        "scheduled_at": datetime.fromisoformat(when),
                        "script_name": script_name,
                        "status": "pending",
                    })

                current_time = [base]
                outbox = []
                sent_rows = []

                def claim_due_sends(limit=1, org_id=None):
                    for send in sorted(scheduled, key=lambda row: (row["scheduled_at"], row["id"])):
                        if send["status"] != "pending" or send["scheduled_at"] > current_time[0]:
                            continue
                        step_number = send["step_number"]
                        assignment = connection.assignments[(campaign["id"], send["lead_id"])]
                        step_copy = assignment["script_snapshot"]["steps"][step_number - 1]
                        send["status"] = "sending"
                        return [{
                            **send,
                            "send_id": send["id"],
                            "email": lead_data[send["lead_id"]]["email"],
                            "lead_status": "new",
                            "do_not_contact": False,
                            "subject": step_copy["subject"],
                            "body": step_copy["body"],
                        }]
                    return []

                def predecessor_status(lead_id, campaign_id, step_number, org_id=None):
                    if step_number == 1:
                        return None
                    previous = next(
                        row for row in scheduled
                        if row["lead_id"] == lead_id
                        and row["campaign_id"] == campaign_id
                        and row["step_number"] == step_number - 1
                    )
                    return previous["status"]

                def record_send(send_id, status, **_kwargs):
                    next(row for row in scheduled if row["id"] == send_id)["status"] = status

                def enqueue_outbound(**kwargs):
                    outbound = {
                        "id": len(outbox) + 1,
                        "status": "pending",
                        "attempts": 0,
                        "lead_status": "new",
                        **kwargs,
                    }
                    outbox.append(outbound)
                    return {"id": outbound["id"], "status": "pending"}

                def claim_due_outbound(limit=1, org_id=None, worker_id=None):
                    for outbound in outbox:
                        if outbound["status"] == "pending":
                            outbound["status"] = "sending"
                            return [outbound]
                    return []

                def record_outbound_sent(outbound_id, **_kwargs):
                    next(row for row in outbox if row["id"] == outbound_id)["status"] = "sent"

                smtp_server = MagicMock()
                smtp_server.__enter__.return_value = smtp_server

                def capture_message(message):
                    sent_rows.append({
                        "to": message["To"],
                        "subject": message["Subject"],
                        "body": message.get_content().strip(),
                    })

                smtp_server.send_message.side_effect = capture_message

                mock_smtp_patch = patch(
                    "outreach.smtplib.SMTP", return_value=smtp_server,
                )
                mock_smtp_ssl_patch = patch("outreach.smtplib.SMTP_SSL")
                patchers = [
                    patch.object(db, "is_outreach_paused", return_value=False),
                    patch.object(db, "outreach_dispatch_status", return_value={"pause_active": False}),
                    patch.object(db, "get_hourly_allocation_limit", return_value=1000),
                    patch.object(db, "count_sent_since", return_value=0),
                    patch.object(db, "claim_due_sends", side_effect=claim_due_sends),
                    patch.object(db, "predecessor_send_status", side_effect=predecessor_status),
                    patch.object(db, "get_lead", side_effect=lambda lead_id, **_kwargs: lead_data[lead_id]),
                    patch.object(db, "enqueue_outbound", side_effect=enqueue_outbound),
                    patch.object(db, "mark_send", side_effect=record_send),
                    patch.object(db, "claim_due_outbound", side_effect=claim_due_outbound),
                    patch.object(db, "mark_outbound_sent", side_effect=record_outbound_sent),
                    patch.object(db, "suppression_reason", return_value=None),
                    patch.object(db, "outreach_dispatch_slot", side_effect=lambda *args, **kwargs: nullcontext()),
                    patch.object(db, "reserve_outbound_domain", return_value=True),
                    patch.object(db, "release_outbound_domain"),
                    patch.object(db, "outbound_provider_status", return_value={"pause_active": False}),
                    patch.object(db, "set_outbound_message_id"),
                    patch.object(db, "log_event"),
                    patch.object(db, "touch_email_account"),
                    patch.object(db, "update_lead_status"),
                    patch.object(outreach, "ensure_opener", side_effect=lambda lead: lead),
                    patch.object(outreach, "resolve_sender", return_value=(
                        smtp_config, "sender@example.test", None, 1, object(),
                    )),
                    patch.object(outreach, "release_sender"),
                    patch.object(outreach, "_effective_hourly_allocation_limit", return_value=1000),
                    patch.object(outreach, "_max_per_domain_per_hour", return_value=1000),
                    patch.object(outreach, "_send_jitter_max_seconds", return_value=0),
                    patch.object(outreach, "_env_int", return_value=0),
                    patch("timezones.is_business_hours", return_value=True),
                    mock_smtp_patch,
                    mock_smtp_ssl_patch,
                ]
                with ExitStack() as stack:
                    mocks = [stack.enter_context(patcher) for patcher in patchers]
                    mock_smtp = mocks[-2]
                    mock_smtp_ssl = mocks[-1]
                    for step_number in range(1, 5):
                        if step_number > 1:
                            current_time[0] += timedelta(days=3)
                        summary = outreach.process_queue(
                            smtp=smtp_config,
                            from_addr="sender@example.test",
                            limit=20,
                            org_id="org-test",
                        )
                        self.assertEqual(summary["sent"], 10)

                self.assertEqual(len(sent_rows), 40)
                self.assertEqual(mock_smtp.call_count, 40)
                mock_smtp_ssl.assert_not_called()
                self.assertEqual(len({row["idempotency_key"] for row in outbox}), 40)
                self.assertTrue(all(row["status"] == "sent" for row in scheduled))

                messages_by_recipient = {}
                for message in sent_rows:
                    messages_by_recipient.setdefault(message["to"], []).append(message)
                for lead_id in lead_ids:
                    email = lead_data[lead_id]["email"]
                    messages = messages_by_recipient[email]
                    self.assertEqual(len(messages), 4)
                    assignment = connection.assignments[(campaign["id"], lead_id)]
                    template_name = assignment["script_name"]
                    self.assertEqual(
                        [message["subject"] for message in messages],
                        [f"{template_name} subject {step}" for step in range(1, 5)],
                    )
                    dates = [
                        next(
                            row["scheduled_at"].date().isoformat()
                            for row in scheduled
                            if row["lead_id"] == lead_id
                            and row["step_number"] == step_number
                        )
                        for step_number in range(1, 5)
                    ]
                    self.assertEqual(
                        [datetime.fromisoformat(date).date() for date in dates],
                        [(base + timedelta(days=3 * step)).date() for step in range(4)],
                    )
                    report_rows.append(
                        (mode, lead_id, template_name, *dates),
                    )

        print(
            "\n| Mode | Lead | Assigned template | Intro | Follow-up | Value | Break-up |"
        )
        print("|---|---:|---|---|---|---|---|")
        for row in report_rows:
            print("| " + " | ".join(map(str, row)) + " |")


if __name__ == "__main__":
    unittest.main()
