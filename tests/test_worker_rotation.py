import json
import unittest
from uuid import UUID
from unittest.mock import patch

import worker


class WorkerRotationTests(unittest.TestCase):
    def setUp(self):
        self.state = {"jobs": {"rotation": {}}}
        self.query = {
            "id": 17,
            "org_id": "org-id",
            "niche": "veterinary clinics",
            "city": "New York",
            "country": "USA",
        }

    def test_rotation_scrapes_due_query_with_organization_scope(self):
        with patch(
            "scrape_planner.pick_next_query", return_value=self.query,
        ) as pick_query, patch(
            "scrape_planner.query_target_leads", return_value=10,
        ) as get_target, patch(
            "scrape_planner.count_leads_for", side_effect=[4, 9],
        ) as count_leads, patch(
            "scrape_planner.record_run_result",
            return_value={"dedup_rate": 0.5, "next_in_hrs": 6},
        ) as record_result, patch(
            "pipeline.run_pipeline",
        ) as run_pipeline, patch.object(worker, "_write_state") as write_state:
            result = worker.job_scrape_rotation(self.state, org_id="org-id")

        pick_query.assert_called_once_with(org_id="org-id")
        get_target.assert_called_once_with(
            "org-id", "veterinary clinics", "USA",
        )
        self.assertEqual(
            count_leads.call_args_list[0].args,
            ("New York", "USA"),
        )
        self.assertEqual(
            count_leads.call_args_list[0].kwargs,
            {"org_id": "org-id"},
        )
        self.assertEqual(
            count_leads.call_args_list[1].kwargs,
            {"org_id": "org-id"},
        )
        run_pipeline.assert_called_once()
        self.assertEqual(run_pipeline.call_args.kwargs["org_id"], "org-id")
        self.assertEqual(run_pipeline.call_args.kwargs["enrich_emails"], False)
        record_result.assert_called_once_with(17, 5, 10)
        self.assertEqual(result["leads_added"], 5)
        self.assertEqual(result["next_in_hrs"], 6)
        self.assertIsNone(result["error"])
        self.assertEqual(self.state["jobs"]["rotation"], result)
        write_state.assert_called_once_with(self.state)

    def test_rotation_without_due_query_does_not_start_scrape(self):
        org_id = UUID("cca42a19-4f6e-43ef-b285-ed00c12b8640")
        with patch(
            "scrape_planner.pick_next_query", return_value=None,
        ), patch("pipeline.run_pipeline") as run_pipeline, patch.object(
            worker, "_write_state",
        ) as write_state:
            result = worker.job_scrape_rotation(
                self.state, org_id=org_id, persist_state=False,
            )

        run_pipeline.assert_not_called()
        write_state.assert_not_called()
        self.assertIsNone(result["error"])
        self.assertEqual(result["org_id"], str(org_id))
        self.assertEqual(self.state["jobs"]["rotation"], result)
        json.dumps(self.state)

    def test_rotation_records_scrape_error_for_backoff(self):
        with patch(
            "scrape_planner.pick_next_query", return_value=self.query,
        ), patch(
            "scrape_planner.query_target_leads", return_value=10,
        ), patch(
            "scrape_planner.count_leads_for", return_value=4,
        ), patch(
            "scrape_planner.record_run_result",
        ) as record_result, patch(
            "pipeline.run_pipeline", side_effect=RuntimeError("browser failed"),
        ), patch.object(worker, "_write_state"):
            result = worker.job_scrape_rotation(self.state, org_id="org-id")

        self.assertEqual(result["error"], "browser failed")
        record_result.assert_called_once_with(
            17, 0, 1, error="browser failed",
        )


if __name__ == "__main__":
    unittest.main()
