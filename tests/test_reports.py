"""Unit tests for Daily Sales Report and CSV Export."""

import unittest
import uuid
from tests.helpers import auth_headers, get_test_client, get_token


class TestReports(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()
        self.barista_token = get_token(self.client, "barista")
        self.lead_token = get_token(self.client, "booth_lead")
        self.b_headers = auth_headers(self.barista_token)
        self.l_headers = auth_headers(self.lead_token)

        # Create 2 orders for test day "2026-11-26"
        self.client.post(
            "/api/orders",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "device_id": "tablet_A",
                "first_name": "Mei",
                "event_day": "2026-11-26",
                "items": [
                    {"item_name": "Flat White", "unit_price_cents": 600, "quantity": 2},
                    {"item_name": "Espresso", "unit_price_cents": 450, "quantity": 1},
                ],
            },
            headers=self.b_headers,
        )
        self.client.post(
            "/api/orders",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "device_id": "tablet_A",
                "first_name": "Jon",
                "event_day": "2026-11-26",
                "items": [
                    {"item_name": "Flat White", "unit_price_cents": 600, "quantity": 1},
                ],
            },
            headers=self.b_headers,
        )

    def test_daily_sales_aggregation(self):
        res = self.client.get(
            "/api/reports/daily?event_day=2026-11-26",
            headers=self.l_headers,
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["event_day"], "2026-11-26")
        self.assertEqual(data["total_cups"], 4)  # 3 Flat Whites + 1 Espresso
        self.assertEqual(data["total_revenue_cents"], 2250)  # (3 * 600) + 450 = 2250 cents ($22.50)

        # Verify items breakdown
        items = {i["item_name"]: i for i in data["items"]}
        self.assertEqual(items["Flat White"]["cups_sold"], 3)
        self.assertEqual(items["Flat White"]["total_revenue_cents"], 1800)
        self.assertEqual(items["Espresso"]["cups_sold"], 1)

    def test_csv_export_format_and_zero_guest_names(self):
        res = self.client.get(
            "/api/reports/daily/csv?event_day=2026-11-26",
            headers=self.l_headers,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res.headers["content-type"])
        content = res.text

        # Verify CSV column headers
        lines = content.strip().split("\r\n" if "\r\n" in content else "\n")
        self.assertEqual(lines[0], "date,item,temperature,milk,cups,unit_price,total")

        # Crucial PDPA verification: No guest names ("Mei", "Jon") must appear in the CSV!
        self.assertNotIn("Mei", content)
        self.assertNotIn("Jon", content)

        # Verify summary line
        self.assertIn("2026-11-26,TOTAL,,,4,-,22.50", content)

    def test_barista_cannot_access_reports(self):
        res = self.client.get("/api/reports/daily", headers=self.b_headers)
        self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()

