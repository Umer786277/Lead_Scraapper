import json
import unittest
from unittest.mock import MagicMock, patch

import db


class _Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self, store):
        self.store = store

    def execute(self, query, params=()):
        sql = " ".join(query.split())
        if "FROM campaign_script_assignments" in sql:
            row = self.store["assignments"].get((params[0], params[1]))
            return _Cursor(row)
        if "FROM campaigns WHERE id=%s AND org_id=%s" in sql:
            row = self.store["campaign"]
            return _Cursor(row if (params[0], params[1]) == (
                row["id"], row["org_id"],
            ) else None)
        if "FROM outreach_settings" in sql:
            return _Cursor(self.store.get("selection_setting"))
        if sql.startswith("UPDATE campaigns SET script_rotation_index="):
            self.store["campaign"]["script_rotation_index"] = params[0]
            if "script_rotation_order=" in sql:
                self.store["campaign"]["script_rotation_order"] = json.loads(params[1])
            return _Cursor()
        if sql.startswith("INSERT INTO campaign_script_assignments"):
            self.store["assignments"][(params[0], params[1])] = {
                "script_name": params[2],
                "script_snapshot": json.loads(params[3]),
            }
            return _Cursor()
        raise AssertionError(f"Unexpected SQL: {sql}")


class _RowsCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _ClaimConnection:
    def __init__(self):
        self.queries = []
        self.assignment = {
            "name": "First",
            "snapshot": {
                "name": "First",
                "steps": [{"subject": "Variant subject", "body": "Variant body"}],
            },
        }
        self.updated_script_names = []

    def execute(self, query, params=()):
        sql = " ".join(query.split())
        self.queries.append(sql)
        if sql.startswith("WITH claimable AS"):
            return _RowsCursor([{"id": 31}])
        if "SELECT s.id, s.campaign_id, s.lead_id, s.org_id, c.script_variants" in sql:
            return _RowsCursor([{
                "id": 31,
                "campaign_id": 13,
                "lead_id": 101,
                "org_id": "org-1",
                "script_variants": [{"name": "First"}],
            }])
        if sql.startswith("UPDATE scheduled_sends SET script_name="):
            self.updated_script_names.append(params[0])
            return _Cursor()
        if "LEFT JOIN campaign_script_assignments a" in sql:
            return _RowsCursor([{
                "send_id": 31,
                "campaign_id": 13,
                "lead_id": 101,
                "step_number": 1,
                "script_name": "First",
                "script_snapshot": json.dumps(self.assignment["snapshot"]),
                "subject": "Legacy subject",
                "body": "Legacy body",
            }])
        raise AssertionError(f"Unexpected SQL: {sql}")


class CampaignScriptAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.store = {
            "campaign": {
                "id": 13,
                "org_id": "org-1",
                "script_variants": [
                    {"name": "First", "steps": [{"subject": "A", "body": "A"}]},
                    {"name": "Second", "steps": [{"subject": "B", "body": "B"}]},
                ],
                "script_rotation_index": 0,
                "script_rotation_order": [],
            },
            "assignments": {},
        }

    def assign(self, lead_id):
        return db._assign_campaign_script(
            _FakeConnection(self.store), 13, lead_id, "org-1",
        )

    def test_sequential_pointer_persists_across_worker_restart(self):
        first = self.assign(101)
        self.assertEqual(first["name"], "First")
        self.assertEqual(self.store["campaign"]["script_rotation_index"], 1)

        restarted_worker_assignment = db._assign_campaign_script(
            _FakeConnection(self.store), 13, 102, "org-1",
        )
        self.assertEqual(restarted_worker_assignment["name"], "Second")
        self.assertEqual(self.store["campaign"]["script_rotation_index"], 0)

    def test_assignment_uses_current_organization_mode(self):
        self.store["selection_setting"] = {"value": "random"}
        self.store["campaign"]["script_rotation_index"] = 0

        with patch(
            "outreach_scripts.random.shuffle",
            side_effect=lambda values: values.reverse(),
        ) as shuffle:
            assignment = self.assign(101)

        self.assertEqual(assignment["name"], "Second")
        shuffle.assert_called_once()
        self.assertEqual(self.store["campaign"]["script_rotation_index"], 1)
        self.assertEqual(self.store["campaign"]["script_rotation_order"], [1, 0])

    def test_assigned_lead_keeps_original_script_after_script_list_changes(self):
        first = self.assign(101)
        self.store["campaign"]["script_variants"] = [
            {"name": "Replacement", "steps": [{"subject": "C", "body": "C"}]},
        ]

        assigned_again = self.assign(101)

        self.assertEqual(assigned_again, first)
        self.assertEqual(
            assigned_again["snapshot"]["steps"][0]["subject"], "A",
        )
        self.assertEqual(self.store["campaign"]["script_rotation_index"], 1)

    def test_campaign_without_variants_preserves_legacy_assignment_behavior(self):
        self.store["campaign"]["script_variants"] = []

        self.assertIsNone(self.assign(101))
        self.assertEqual(self.store["assignments"], {})
        self.assertEqual(self.store["campaign"]["script_rotation_index"], 0)

    def test_due_send_claim_assigns_script_and_uses_its_step_copy(self):
        connection = _ClaimConnection()
        connection_context = MagicMock()
        connection_context.__enter__.return_value = connection

        with patch.object(db, "get_conn", return_value=connection_context), patch.object(
            db, "_assign_campaign_script", return_value=connection.assignment,
        ) as assign:
            items = db.claim_due_sends(org_id="org-1")

        assign.assert_called_once_with(connection, 13, 101, "org-1")
        self.assertEqual(connection.updated_script_names, ["First"])
        self.assertIn(
            "ORDER BY s.scheduled_at ASC, s.id ASC FOR UPDATE OF s SKIP LOCKED",
            connection.queries[0],
        )
        self.assertEqual(items[0]["subject"], "Variant subject")
        self.assertEqual(items[0]["body"], "Variant body")
        self.assertEqual(items[0]["template_name"], "First")


if __name__ == "__main__":
    unittest.main()
