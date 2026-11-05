import unittest
from unittest.mock import patch

import google_maps_scraper as gms


class GoogleMapsBroadenTests(unittest.TestCase):
    def test_broadens_when_exact_city_returns_too_few(self):
        first = ["https://maps/A", "https://maps/B"]
        nearby_one = ["https://maps/A", "https://maps/C"]
        nearby_two = ["https://maps/D"]
        events = []

        with patch.object(
            gms, "_run_search", side_effect=[first, nearby_one, nearby_two],
        ) as run:
            urls = gms.search_google_maps(
                "Vets", "Abbots Langley", "United Kingdom", 10, object(),
                on_event=lambda kind, message: events.append((kind, message)),
            )

        self.assertEqual(run.call_count, 3)
        self.assertEqual(
            urls,
            ["https://maps/A", "https://maps/B", "https://maps/C", "https://maps/D"],
        )
        self.assertTrue(any("broadening search" in message for _, message in events))

    def test_does_not_broaden_when_enough_results_found(self):
        first = [f"https://maps/place/{i}" for i in range(5)]

        with patch.object(gms, "_run_search", return_value=first) as run:
            urls = gms.search_google_maps(
                "Vets", "London", "United Kingdom", 10, object(),
            )

        self.assertEqual(run.call_count, 1)
        self.assertEqual(urls, first)

    def test_broadening_respects_max_leads(self):
        first = ["https://maps/A"]

        with patch.object(
            gms, "_run_search",
            side_effect=[first, ["https://maps/A", "https://maps/B", "https://maps/C", "https://maps/D"]],
        ) as run:
            urls = gms.search_google_maps(
                "Vets", "Abbots Langley", "United Kingdom", 3, object(),
            )

        self.assertEqual(run.call_count, 2)
        self.assertEqual(urls, ["https://maps/A", "https://maps/B", "https://maps/C"])

    def test_still_broadens_when_exact_search_fails(self):
        with patch.object(
            gms, "_run_search",
            side_effect=[
                RuntimeError("navigation failed"),
                ["https://maps/A"],
                RuntimeError("second fallback failed"),
            ],
        ) as run:
            urls = gms.search_google_maps(
                "Vets", "Abbots Langley", "United Kingdom", 10, object(),
            )

        self.assertGreaterEqual(run.call_count, 2)
        self.assertEqual(urls, ["https://maps/A"])


if __name__ == "__main__":
    unittest.main()
