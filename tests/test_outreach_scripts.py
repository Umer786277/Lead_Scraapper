import unittest
from unittest.mock import patch

from outreach_scripts import select_script_variant


class ScriptVariantSelectionTests(unittest.TestCase):
    def setUp(self):
        self.scripts = [
            {"name": "First", "steps": [{"subject": "1", "body": "one"}]},
            {"name": "Second", "steps": [{"subject": "2", "body": "two"}]},
            {"name": "Third", "steps": [{"subject": "3", "body": "three"}]},
        ]

    def test_sequential_selection_wraps_to_first_script(self):
        selected = []
        pointer = 0
        for _ in range(5):
            script, pointer, _ = select_script_variant(
                "sequential", self.scripts, pointer,
            )
            selected.append(script["name"])

        self.assertEqual(
            selected,
            ["First", "Second", "Third", "First", "Second"],
        )
        self.assertEqual(pointer, 2)

    def test_sequential_pointer_continues_after_restart(self):
        first, persisted_pointer, persisted_order = select_script_variant(
            "sequential", self.scripts, 0,
        )
        self.assertEqual(first["name"], "First")

        restarted_process_pointer = persisted_pointer
        second, _, _ = select_script_variant(
            "sequential",
            self.scripts,
            restarted_process_pointer,
            persisted_order,
        )
        self.assertEqual(second["name"], "Second")

    def test_random_selection_uses_every_script_once_per_shuffled_cycle(self):
        with patch(
            "outreach_scripts.random.shuffle",
            side_effect=lambda values: values.reverse(),
        ):
            selected = []
            pointer = 0
            order = []
            for _ in self.scripts:
                script, pointer, order = select_script_variant(
                    "random", self.scripts, pointer, order,
                )
                selected.append(script["name"])

        self.assertEqual(selected, ["Third", "Second", "First"])
        self.assertEqual(pointer, len(self.scripts))
        self.assertEqual(sorted(order), list(range(len(self.scripts))))

    def test_random_cycle_persists_and_next_cycle_avoids_boundary_repeat(self):
        with patch(
            "outreach_scripts.random.shuffle",
            side_effect=lambda values: values.reverse(),
        ):
            script, pointer, order = select_script_variant(
                "random", self.scripts,
            )
            self.assertEqual(script["name"], "Third")

            restarted_worker_result, pointer, order = select_script_variant(
                "random", self.scripts, pointer, order,
            )
            self.assertEqual(restarted_worker_result["name"], "Second")
            final_script, pointer, order = select_script_variant(
                "random", self.scripts, pointer, order,
            )
            self.assertEqual(final_script["name"], "First")
            self.assertEqual(pointer, len(self.scripts))

        # The prior cycle ended with First; the next cycle cannot start with it.
        with patch("outreach_scripts.random.choice", return_value=1) as choose_first, patch(
            "outreach_scripts.random.shuffle",
            side_effect=lambda values: values.reverse(),
        ):
            next_script, next_pointer, next_order = select_script_variant(
                "random", self.scripts, len(self.scripts), order,
            )

        choose_first.assert_called_once_with([1, 2])
        self.assertEqual(next_script["name"], "Second")
        self.assertEqual(next_pointer, 1)
        self.assertEqual(next_order[0], 1)

    def test_random_with_one_script_remains_valid(self):
        only_script = self.scripts[:1]
        with patch("outreach_scripts.random.shuffle") as shuffle:
            selected, pointer, order = select_script_variant(
                "random", only_script,
            )
        self.assertIs(selected, only_script[0])
        self.assertEqual(pointer, 1)
        self.assertEqual(order, [0])
        shuffle.assert_called_once_with([0])

    def test_invalid_mode_and_empty_variants_are_rejected(self):
        with self.assertRaises(ValueError):
            select_script_variant("round-robin", self.scripts)
        with self.assertRaises(ValueError):
            select_script_variant("sequential", [])


if __name__ == "__main__":
    unittest.main()
