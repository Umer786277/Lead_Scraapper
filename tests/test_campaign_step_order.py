import unittest

import db


class CampaignStepOrderTests(unittest.TestCase):
    def test_ascending_step_numbers_are_allowed(self):
        db.validate_campaign_step_order([1, 2, 3, 4])

    def test_missing_step_numbers_do_not_block_campaign_creation(self):
        db.validate_campaign_step_order([1, None, 2, None])

    def test_reversed_step_numbers_are_rejected(self):
        with self.assertRaisesRegex(ValueError, r"out of order \(4 -> 3 -> 2 -> 1\)"):
            db.validate_campaign_step_order([4, 3, 2, 1])

    def test_skipped_backwards_step_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "out of order"):
            db.validate_campaign_step_order([1, 3, 2, 4])


if __name__ == "__main__":
    unittest.main()
