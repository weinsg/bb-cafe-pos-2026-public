"""Unit tests for PDPA Compliance, Daily Reset, and Audit Trail."""

import unittest
import uuid
from sqlmodel import Session, select
from tests.helpers import auth_headers, get_test_client, get_token, test_engine
from app.models import Order


class TestPDPAAndReset(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()
        self.barista_token = get_token(self.client, "barista")
        self.lead_token = get_token(self.client, "booth_lead")
        self.b_headers = auth_headers(self.barista_token)
        self.l_headers = auth_headers(self.lead_token)

        # Create orders for day 2026-11-26
        self.order1_id = self.client.post(
            "/api/orders",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "device_id": "tablet_A",
                "first_name": "Rachel",
                "event_day": "2026-11-26",
                "items": [{"item_name": "Latte", "unit_price_cents": 600, "quantity": 1}],
            },
            headers=self.b_headers,
        ).json()["id"]

        self.order2_id = self.client.post(
            "/api/orders",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "device_id": "tablet_A",
                "first_name": "Marcus",
                "event_day": "2026-11-26",
                "items": [{"item_name": "Pourover", "unit_price_cents": 650, "quantity": 1}],
            },
            headers=self.b_headers,
        ).json()["id"]

    def test_daily_reset_sanitizes_names_and_clears_queue(self):
        # 1. Before reset: verify names exist in database
        with Session(test_engine) as session:
            o1 = session.get(Order, self.order1_id)
            self.assertEqual(o1.first_name, "Rachel")

        # 2. Trigger daily reset
        reset_res = self.client.post(
            "/api/admin/reset-day",
            json={"closed_event_day": "2026-11-26", "confirm": True},
            headers=self.l_headers,
        )
        self.assertEqual(reset_res.status_code, 200)
        self.assertGreaterEqual(reset_res.json()["names_sanitized"], 2)

        # 3. After reset: verify guest first names are completely wiped (NULL)
        with Session(test_engine) as session:
            o1_after = session.get(Order, self.order1_id)
            o2_after = session.get(Order, self.order2_id)
            self.assertIsNone(o1_after.first_name)
            self.assertIsNone(o2_after.first_name)

        # 4. Verify sales reports still work accurately after sanitization
        rep_res = self.client.get(
            "/api/reports/daily?event_day=2026-11-26",
            headers=self.l_headers,
        )
        self.assertEqual(rep_res.status_code, 200)
        self.assertEqual(rep_res.json()["total_cups"], 2)
        self.assertEqual(rep_res.json()["total_revenue_cents"], 1250)

        # 5. Verify active queue is empty
        queue_res = self.client.get("/api/orders/active", headers=self.b_headers)
        self.assertEqual(queue_res.status_code, 200)
        self.assertEqual(len(queue_res.json()), 0)

    def test_daily_reset_requires_confirmation(self):
        res = self.client.post(
            "/api/admin/reset-day",
            json={"closed_event_day": "2026-11-26", "confirm": False},
            headers=self.l_headers,
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("confirmation", res.json()["detail"].lower())

    def test_audit_logs_never_contain_guest_names(self):
        logs_res = self.client.get(
            "/api/admin/audit-logs",
            headers=auth_headers(get_token(self.client, "admin")),
        )
        self.assertEqual(logs_res.status_code, 200)
        logs = logs_res.json()
        self.assertGreaterEqual(len(logs), 2)

        for log in logs:
            # Check staff username is present
            self.assertIn(log["staff_username"], ["barista", "booth_lead", "admin"])
            # Crucial PDPA test: guest names must never be written into the audit trail!
            self.assertNotIn("Rachel", log["details"])
            self.assertNotIn("Marcus", log["details"])

    def test_barista_forbidden_from_running_reset(self):
        res = self.client.post(
            "/api/admin/reset-day",
            json={"closed_event_day": "2026-11-26", "confirm": True},
            headers=self.b_headers,
        )
        self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
