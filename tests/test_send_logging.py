import unittest

import worker


class SendLoggingTests(unittest.TestCase):
    def test_send_log_identifies_template_script_subject_and_mode(self):
        item = {
            "lead_id": 23,
            "business_name": "Clinic",
            "step_number": 2,
            "template_name": "Step 2 - Bump",
            "script_name": "Script A",
            "subject": "Following up",
            "script_mode": "sequential",
        }

        with self.assertLogs("worker", level="INFO") as logs:
            worker._on_send_item(item, "sent", "message-id")

        self.assertIn(
            'send: lead #23 (Clinic) step 2 template="Step 2 - Bump" '
            'script="Script A" subject="Following up" mode=sequential -> sent',
            logs.output[0],
        )


if __name__ == "__main__":
    unittest.main()
