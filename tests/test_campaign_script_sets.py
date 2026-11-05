import unittest
from unittest.mock import patch

import db
from api.routers import outreach as outreach_api
from fastapi import HTTPException
from pydantic import ValidationError


def _scripts(step_count=1):
    return [
        outreach_api.CampaignScriptVariantBody(
            name=f"Variant {index}",
            steps=[
                outreach_api.CampaignScriptStepBody(
                    subject=f"Subject {index}-{step}",
                    body=f"Body {index}-{step}",
                )
                for step in range(step_count)
            ],
        )
        for index in (1, 2)
    ]


class CampaignScriptSetApiTests(unittest.TestCase):
    def test_database_script_validation_rejects_malformed_steps(self):
        with self.assertRaises(ValueError):
            db._validated_script_variants([None])
        with self.assertRaises(ValueError):
            db._validated_script_variants([{
                "name": "Malformed",
                "steps": [None],
            }])
        with self.assertRaises(ValueError):
            db._validated_script_variants([{
                "name": "Non-text",
                "steps": [{"subject": 1, "body": "Body"}],
            }])

    def test_script_set_validates_mode_variant_count_and_step_count(self):
        body = outreach_api.CampaignScriptSetBody(
            name="Alternatives",
            scripts=_scripts(step_count=2),
        )
        self.assertEqual(len(body.scripts), 2)
        self.assertEqual(len(body.scripts[0].steps), 2)

        with self.assertRaises(ValidationError):
            outreach_api.CampaignScriptSetBody(
                name="Empty", scripts=[],
            )
        with self.assertRaises(ValidationError):
            outreach_api.CampaignScriptSetBody(
                name="Different lengths",
                scripts=[
                    outreach_api.CampaignScriptVariantBody(
                        name="One",
                        steps=[{
                            "subject": "Subject",
                            "body": "Body",
                        }],
                    ),
                    outreach_api.CampaignScriptVariantBody(
                        name="Two",
                        steps=[
                            {"subject": "Subject", "body": "Body"},
                            {"subject": "Subject 2", "body": "Body 2"},
                        ],
                    ),
                ],
            )

    def test_script_set_validates_shared_follow_up_delays(self):
        body = outreach_api.CampaignScriptSetBody(
            name="Timed variants",
            scripts=_scripts(step_count=3),
            step_delays=[0, 2, 5],
        )
        self.assertEqual(body.step_delays, [0, 2, 5])

        with self.assertRaises(ValidationError):
            outreach_api.CampaignScriptSetBody(
                name="Missing delay",
                scripts=_scripts(step_count=2),
                step_delays=[0],
            )
        with self.assertRaises(ValidationError):
            outreach_api.CampaignScriptSetBody(
                name="Delayed initial email",
                scripts=_scripts(step_count=2),
                step_delays=[1, 3],
            )

        self.assertEqual(db._validated_script_delays(None, 4), [0, 3, 3, 3])
        with self.assertRaises(ValueError):
            db._validated_script_delays([0, 366], 2)

    def test_script_step_rejects_whitespace_only_subject_or_body(self):
        with self.assertRaises(ValidationError):
            outreach_api.CampaignScriptStepBody(subject="  ", body="Body")
        with self.assertRaises(ValidationError):
            outreach_api.CampaignScriptStepBody(subject="Subject", body=" \n ")

    def test_campaign_passes_selected_script_set_to_database(self):
        body = outreach_api.CampaignBody(
            name="Variant campaign",
            lead_ids=[1, 2],
            sequence_id=17,
            script_set_id=23,
        )
        with patch.object(
            outreach_api.db, "get_sequence",
            return_value={"id": 17, "steps": []},
        ), patch.object(
            outreach_api.db, "create_campaign", return_value=99,
        ) as create:
            result = outreach_api.create_campaign(body, org_id="org-1")

        self.assertEqual(result, {"id": 99})
        self.assertEqual(create.call_args.kwargs["script_set_id"], 23)

    def test_campaign_can_use_script_set_without_a_saved_sequence(self):
        body = outreach_api.CampaignBody(
            name="Four-step automation",
            lead_ids=[1, 2],
            script_set_id=23,
        )
        with patch.object(
            outreach_api.db, "create_campaign", return_value=101,
        ) as create:
            result = outreach_api.create_campaign(body, org_id="org-1")

        self.assertEqual(result, {"id": 101})
        create.assert_called_once_with(
            "Four-step automation",
            [],
            [1, 2],
            notes=None,
            org_id="org-1",
            sequence_id=None,
            script_set_id=23,
        )

    def test_organization_script_selection_setting_is_shared(self):
        with patch.object(
            outreach_api.db, "get_script_selection_mode",
            return_value="random",
        ) as get_mode, patch.object(
            outreach_api.db, "set_script_selection_mode",
        ) as set_mode:
            result = outreach_api.get_script_selection_settings(org_id="org-1")
            updated = outreach_api.update_script_selection_settings(
                outreach_api.ScriptSelectionSettingsBody(mode="sequential"),
                org_id="org-1",
            )

        self.assertEqual(result, {"mode": "random"})
        self.assertEqual(updated, {"mode": "sequential"})
        get_mode.assert_called_once_with(org_id="org-1")
        set_mode.assert_called_once_with("sequential", org_id="org-1")

    def test_mismatched_campaign_sequence_length_is_reported_as_validation_error(self):
        body = outreach_api.CampaignScriptSetBody(
            name="Two steps",
            scripts=_scripts(step_count=2),
        )
        with patch.object(
            outreach_api.db, "update_script_set",
            side_effect=ValueError("Script variants must keep one step."),
        ):
            with self.assertRaises(HTTPException) as raised:
                outreach_api.update_script_set(1, body, org_id="org-1")

        self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
