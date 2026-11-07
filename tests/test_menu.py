"""Unit tests for menu management and controls."""

import unittest
from tests.helpers import auth_headers, get_test_client, get_token


class TestMenu(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()
        self.lead_token = get_token(self.client, "booth_lead")

    def test_get_menu_public(self):
        res = self.client.get("/api/menu")
        self.assertEqual(res.status_code, 200)
        items = res.json()
        self.assertGreaterEqual(len(items), 5)
        names = [item["name"] for item in items]
        self.assertIn("Flat White", names)
        self.assertIn("Latte", names)

    def test_booth_lead_update_price(self):
        # Update Latte price to $6.50 (650 cents)
        res = self.client.patch(
            "/api/menu/4",
            json={"price_cents": 650},
            headers=auth_headers(self.lead_token),
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["price_cents"], 650)

    def test_booth_lead_toggle_sold_out(self):
        # Mark Oat/Flat White sold out
        res = self.client.patch(
            "/api/menu/3",
            json={"is_sold_out": True},
            headers=auth_headers(self.lead_token),
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["is_sold_out"])

        # Re-enable item
        res2 = self.client.patch(
            "/api/menu/3",
            json={"is_sold_out": False},
            headers=auth_headers(self.lead_token),
        )
        self.assertEqual(res2.status_code, 200)
        self.assertFalse(res2.json()["is_sold_out"])

    def test_menu_change_creates_audit_log(self):
        self.client.patch(
            "/api/menu/1",
            json={"price_cents": 450},
            headers=auth_headers(self.lead_token),
        )
        logs_res = self.client.get(
            "/api/admin/audit-logs",
            headers=auth_headers(get_token(self.client, "admin")),
        )
        self.assertEqual(logs_res.status_code, 200)
        logs = logs_res.json()
        menu_logs = [l for l in logs if l["action"] == "MENU_UPDATE"]
        self.assertGreaterEqual(len(menu_logs), 1)
        self.assertIn("booth_lead", menu_logs[0]["staff_username"])


if __name__ == "__main__":
    unittest.main()

