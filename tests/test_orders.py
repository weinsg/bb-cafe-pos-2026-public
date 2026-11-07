"""Unit tests for Order processing, Barista Queue, Ready Board, and Guest Status (Stories 2, 3, 4, 6, 7)."""

import unittest
import uuid
from tests.helpers import auth_headers, get_test_client, get_token


class TestOrders(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()
        self.barista_token = get_token(self.client, "barista")
        self.lead_token = get_token(self.client, "booth_lead")
        self.headers = auth_headers(self.barista_token)

    def test_create_order_success(self):
        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "device_id": "tablet_A",
            "first_name": "Mei",
            "items": [
                {
                    "item_name": "Flat White",
                    "unit_price_cents": 600,
                    "milk": "oat",
                    "temperature": "hot",
                    "quantity": 1,
                }
            ],
        }
        res = self.client.post("/api/orders", json=payload, headers=self.headers)
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["first_name"], "Mei")
        self.assertEqual(data["order_number"], "A001")
        self.assertEqual(data["status"], "received")
        self.assertEqual(data["total_price_cents"], 600)
        self.assertEqual(len(data["items"]), 1)

    def test_idempotency_prevents_duplicate_orders(self):
        idem_key = "sync-key-" + str(uuid.uuid4())
        payload = {
            "idempotency_key": idem_key,
            "device_id": "tablet_A",
            "first_name": "Ken",
            "items": [
                {
                    "item_name": "Latte",
                    "unit_price_cents": 600,
                    "milk": "dairy",
                    "temperature": "iced",
                    "quantity": 1,
                }
            ],
        }
        res1 = self.client.post("/api/orders", json=payload, headers=self.headers)
        self.assertEqual(res1.status_code, 201)
        order_id1 = res1.json()["id"]

        # Re-sending the identical request (simulating offline retry or double-tap)
        res2 = self.client.post("/api/orders", json=payload, headers=self.headers)
        self.assertEqual(res2.status_code, 201)
        order_id2 = res2.json()["id"]

        # Must return the identical order without creating a duplicate row
        self.assertEqual(order_id1, order_id2)

    def test_tablet_prefix_numbering(self):
        p_a = {
            "idempotency_key": str(uuid.uuid4()),
            "device_id": "tablet_A",
            "first_name": "Alice",
            "items": [{"item_name": "Americano", "unit_price_cents": 500, "quantity": 1}],
        }
        res_a = self.client.post("/api/orders", json=p_a, headers=self.headers)
        self.assertEqual(res_a.json()["order_number"], "A001")

        p_b = {
            "idempotency_key": str(uuid.uuid4()),
            "device_id": "tablet_B",
            "first_name": "Bob",
            "items": [{"item_name": "Pourover", "unit_price_cents": 650, "quantity": 1}],
        }
        res_b = self.client.post("/api/orders", json=p_b, headers=self.headers)
        self.assertEqual(res_b.json()["order_number"], "B001")

    def test_sold_out_item_cannot_be_ordered(self):
        # 1. Mark Flat White as sold out
        self.client.patch(
            "/api/menu/3",
            json={"is_sold_out": True},
            headers=auth_headers(self.lead_token),
        )

        # 2. Barista attempts to order sold-out Flat White
        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "device_id": "tablet_A",
            "first_name": "Dev",
            "items": [{"item_name": "Flat White", "unit_price_cents": 600, "quantity": 1}],
        }
        res = self.client.post("/api/orders", json=payload, headers=self.headers)
        self.assertEqual(res.status_code, 400)
        self.assertIn("sold out", res.json()["detail"])

    def test_missing_first_name_rejected(self):
        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "device_id": "tablet_A",
            "first_name": "   ",
            "items": [{"item_name": "Latte", "unit_price_cents": 600, "quantity": 1}],
        }
        res = self.client.post("/api/orders", json=payload, headers=self.headers)
        self.assertEqual(res.status_code, 400)

    def test_status_progression_and_monotonic_rule(self):
        # Create order
        create_res = self.client.post(
            "/api/orders",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "device_id": "tablet_A",
                "first_name": "Sam",
                "items": [{"item_name": "Latte", "unit_price_cents": 600, "quantity": 1}],
            },
            headers=self.headers,
        )
        order_id = create_res.json()["id"]

        # Advance to Making
        res_making = self.client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "making"},
            headers=self.headers,
        )
        self.assertEqual(res_making.status_code, 200)
        self.assertEqual(res_making.json()["status"], "making")
        self.assertIsNotNone(res_making.json()["making_at"])

        # Advance to Ready
        res_ready = self.client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "ready"},
            headers=self.headers,
        )
        self.assertEqual(res_ready.status_code, 200)
        self.assertEqual(res_ready.json()["status"], "ready")
        self.assertIsNotNone(res_ready.json()["ready_at"])

        # Moving backward (Ready -> Making) must be rejected
        res_backward = self.client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "making"},
            headers=self.headers,
        )
        self.assertEqual(res_backward.status_code, 400)
        self.assertIn("Cannot move status backwards", res_backward.json()["detail"])

        # Advance to Collected
        res_col = self.client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "collected"},
            headers=self.headers,
        )
        self.assertEqual(res_col.status_code, 200)
        self.assertEqual(res_col.json()["status"], "collected")

    def test_repeated_status_tap_is_audited_as_unchanged(self):
        # Two staff tap Ready on the same order through the direct API.
        order = self.client.post(
            "/api/orders",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "device_id": "tablet_A",
                "first_name": "Sam",
                "items": [{"item_name": "Latte", "unit_price_cents": 800, "quantity": 1}],
            },
            headers=self.headers,
        ).json()
        for _ in range(2):
            res = self.client.patch(
                f"/api/orders/{order['id']}/status", json={"status": "ready"}, headers=self.headers
            )
            self.assertEqual(res.status_code, 200)
        logs = self.client.get(
            "/api/admin/audit-logs", headers=auth_headers(get_token(self.client, "admin"))
        ).json()
        actions = [log["action"] for log in logs if log["order_number"] == order["order_number"]]
        self.assertEqual(actions.count("STATUS_CHANGE"), 1)
        self.assertEqual(actions.count("STATUS_UNCHANGED"), 1)

    def test_guest_page_drinks_read_in_plain_words(self):
        """Name on its own line ("2 ×" only for more than one), then options in words."""
        res = self.client.post(
            "/api/orders",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "device_id": "tablet_A",
                "first_name": "Wen",
                "items": [
                    {"item_name": "Flat White", "unit_price_cents": 800, "milk": "dairy", "temperature": "hot", "quantity": 1},
                    {"item_name": "Latte", "unit_price_cents": 1000, "milk": "oat", "temperature": "iced", "quantity": 2},
                    {"item_name": "Espresso", "unit_price_cents": 300, "milk": "none", "temperature": "hot", "quantity": 1},
                ],
            },
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 201, res.text)
        guest = self.client.get(f"/api/orders/{res.json()['id']}/status").json()
        self.assertEqual(guest["items"], [
            {"name": "Flat White", "details": "Hot · Dairy milk"},
            {"name": "2 × Latte", "details": "Iced · Oat milk"},
            {"name": "Espresso", "details": "Hot"},
        ])
        for line in guest["items_summary"]:
            for unwanted in ("1x", "(", "["):
                self.assertNotIn(unwanted, line)

    def test_ready_board_and_guest_status(self):
        # Create order and advance to ready
        create_res = self.client.post(
            "/api/orders",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "device_id": "tablet_A",
                "first_name": "Aisha",
                "items": [{"item_name": "Latte", "unit_price_cents": 600, "quantity": 1}],
            },
            headers=self.headers,
        )
        order_id = create_res.json()["id"]
        order_no = create_res.json()["order_number"]

        self.client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "ready"},
            headers=self.headers,
        )

        # Check public ready board
        board_res = self.client.get("/api/orders/ready-board")
        self.assertEqual(board_res.status_code, 200)
        ready_items = board_res.json()["ready"]
        self.assertTrue(any(item["order_number"] == order_no for item in ready_items))

        # Check public guest status endpoint
        guest_res = self.client.get(f"/api/orders/{order_id}/status")
        self.assertEqual(guest_res.status_code, 200)
        guest_data = guest_res.json()
        self.assertEqual(guest_data["order_number"], order_no)
        self.assertEqual(guest_data["first_name"], "Aisha")
        self.assertEqual(guest_data["status"], "ready")


if __name__ == "__main__":
    unittest.main()

