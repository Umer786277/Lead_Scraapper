import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from api.routers import pipeline as pipeline_api


class PipelineLocationTests(unittest.TestCase):
    @patch.object(pipeline_api.requests, "get")
    def test_lists_sorted_country_names(self, get):
        response = Mock()
        response.json.return_value = {
            "error": False,
            "data": [
                {"name": "United Kingdom"},
                {"name": "Australia"},
                {"name": "United Kingdom"},
            ],
        }
        get.return_value = response

        result = pipeline_api.list_location_countries(org_id="org-1")

        self.assertEqual(result, {"countries": ["Australia", "United Kingdom"]})
        get.assert_called_once_with(
            "https://countriesnow.space/api/v0.1/countries/iso", timeout=15,
        )

    @patch.object(pipeline_api.requests, "post")
    def test_lists_sorted_cities_for_country(self, post):
        response = Mock()
        response.json.return_value = {
            "error": False,
            "data": ["London", "Bristol", "London"],
        }
        post.return_value = response

        result = pipeline_api.list_location_cities(
            pipeline_api.CountryCitiesRequest(country=" United Kingdom "),
            org_id="org-1",
        )

        self.assertEqual(result, {"cities": ["Bristol", "London"]})
        post.assert_called_once_with(
            "https://countriesnow.space/api/v0.1/countries/cities",
            json={"country": "United Kingdom"},
            timeout=20,
        )

    @patch.object(pipeline_api.requests, "post")
    def test_reports_upstream_city_failures(self, post):
        post.side_effect = pipeline_api.requests.Timeout("timed out")

        with self.assertRaises(HTTPException) as raised:
            pipeline_api.list_location_cities(
                pipeline_api.CountryCitiesRequest(country="United Kingdom"),
                org_id="org-1",
            )

        self.assertEqual(raised.exception.status_code, 502)
