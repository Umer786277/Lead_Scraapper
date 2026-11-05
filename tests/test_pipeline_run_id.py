import unittest
from unittest.mock import patch

import pipeline


class PipelineRunIdTests(unittest.TestCase):
    def test_reuses_run_created_by_api_instead_of_starting_another(self):
        with patch.object(pipeline.db, "start_run") as start_run:
            with patch.object(pipeline.db, "finish_run") as finish_run:
                with patch.object(pipeline.run_events, "record"):
                    with patch.object(pipeline.run_events, "flush"):
                        with patch.object(
                            pipeline, "sync_playwright", side_effect=RuntimeError("boom")
                        ):
                            with self.assertRaises(RuntimeError):
                                pipeline.run_pipeline(
                                    [{"niche": "Vets", "city": "Birmingham", "country": "UK"}],
                                    run_id=42,
                                )

        start_run.assert_not_called()
        finish_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
