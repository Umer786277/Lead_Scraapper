import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

import db
from api.routers import leads


class LeadCsvImportTests(unittest.TestCase):
    def test_parses_exported_lead_columns_and_normalizes_domain(self):
        csv_data = (
            "\ufeffBusiness Name,Domain,Email,Phone,City,Country,Rating,Reviews,Maps URL\n"
            "Example Clinic,https://www.example.com/contact,HELLO@example.com,"
            "+1 555 0100,New York,USA,4.8,24,https://maps.example/place/1\n"
        ).encode("utf-8")

        records, errors = leads._parse_leads_csv(csv_data)

        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["business_name"], "Example Clinic")
        self.assertEqual(records[0]["domain"], "example.com")
        self.assertEqual(records[0]["email"], "hello@example.com")
        self.assertEqual(records[0]["rating"], 4.8)
        self.assertEqual(records[0]["reviews"], 24)

    def test_parses_extracted_email_export(self):
        csv_data = (
            "Domain,Email,Source URL,Source Type,Confidence\n"
            "example.org,contact@example.org,https://example.org/contact,"
            "mailto,high\n"
        ).encode("utf-8")

        records, errors = leads._parse_leads_csv(csv_data)

        self.assertEqual(errors, [])
        self.assertEqual(records[0]["business_name"], "example.org")
        self.assertEqual(records[0]["domain"], "example.org")
        self.assertEqual(records[0]["email"], "contact@example.org")

    def test_invalid_rows_are_reported_without_discarding_valid_rows(self):
        csv_data = (
            "Business Name,Email,Rating\n"
            "Good Clinic,hello@good.example,4.5\n"
            "Bad Clinic,not-an-email,5\n"
            "Other Clinic,hello@other.example,not-a-number\n"
        ).encode("utf-8")

        records, errors = leads._parse_leads_csv(csv_data)

        self.assertEqual(len(records), 1)
        self.assertEqual(len(errors), 2)
        self.assertIn("Row 3: invalid email address.", errors)
        self.assertIn("Row 4: rating must be a number.", errors)

    def test_unknown_headers_are_rejected(self):
        with self.assertRaises(leads.HTTPException) as raised:
            leads._parse_leads_csv(b"First,Last\nSam,Example\n")
        self.assertEqual(raised.exception.status_code, 400)

    def test_import_scopes_duplicate_checks_and_rows_to_active_org(self):
        statements = []

        class FakeConnection:
            def execute(self, query, params=()):
                sql = " ".join(query.split())
                statements.append((sql, tuple(params)))
                result = Mock()
                result.fetchone.return_value = None
                return result

        @contextmanager
        def fake_connection():
            yield FakeConnection()

        record = {
            "business_name": "Example Clinic",
            "domain": "example.com",
            "email": "hello@example.com",
            "phone": None,
            "website": None,
            "address": None,
            "city": None,
            "country": None,
            "rating": None,
            "reviews": None,
            "maps_url": None,
            "improvement_note": None,
            "lead_line": None,
            "last_review_days": None,
            "contact_name": None,
            "homepage_snippet": None,
            "notes": None,
        }
        with patch.object(db, "get_conn", side_effect=fake_connection), \
                patch.object(db, "now", return_value="2026-11-02T00:00:00"):
            imported, skipped = leads._import_lead_records([record], "active-org")

        self.assertEqual((imported, skipped), (1, 0))
        duplicate_query, duplicate_args = statements[0]
        self.assertIn("org_id=%s AND lower(email)=lower(%s)", duplicate_query)
        self.assertEqual(duplicate_args, ("active-org", "hello@example.com"))
        insert_query, insert_args = statements[1]
        self.assertIn("INSERT INTO leads", insert_query)
        self.assertEqual(insert_args[:4], (
            "active-org", "csv_import", "new", "2026-11-02T00:00:00",
        ))

    def test_existing_email_is_skipped(self):
        class FakeConnection:
            def execute(self, query, params=()):
                result = Mock()
                result.fetchone.return_value = {"id": 8}
                return result

        @contextmanager
        def fake_connection():
            yield FakeConnection()

        record = {
            "business_name": "Example Clinic",
            "domain": "example.com",
            "email": "hello@example.com",
            "phone": None,
            "website": None,
            "address": None,
            "city": None,
            "country": None,
            "rating": None,
            "reviews": None,
            "maps_url": None,
            "improvement_note": None,
            "lead_line": None,
            "last_review_days": None,
            "contact_name": None,
            "homepage_snippet": None,
            "notes": None,
        }
        with patch.object(db, "get_conn", side_effect=fake_connection):
            imported, skipped = leads._import_lead_records([record], "active-org")

        self.assertEqual((imported, skipped), (0, 1))

    def test_import_endpoint_returns_counts_and_row_errors(self):
        import asyncio
        from io import BytesIO
        from starlette.datastructures import UploadFile

        upload = UploadFile(
            filename="extracted.csv",
            file=BytesIO(
                b"Domain,Email\nexample.com,hello@example.com\n"
                b",invalid\n"
            ),
        )
        with patch.object(
            leads, "_import_lead_records", return_value=(1, 0),
        ) as importer:
            result = asyncio.run(leads.import_leads_csv(upload, "active-org"))

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["invalid"], 1)
        importer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
