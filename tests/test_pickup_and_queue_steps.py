"""Pickup scan lookup and the optional Making step."""
import datetime as dt
import unittest
import uuid

from tests.helpers import auth_headers, get_test_client, get_token


class TestQueueSteps(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()
        self.barista = auth_headers(get_token(self.client))
        self.lead = auth_headers(get_token(self.client, "booth_lead"))
        self.admin = auth_headers(get_token(self.client, "admin"))

    def test_making_step_defaults_on_and_only_lead_or_admin_change_it(self):
        url = "/api/admin/queue-settings"
        self.assertTrue(self.client.get("/api/config").json()["use_making_step"])
        self.assertEqual(self.client.patch(url, json={"use_making_step": False}).status_code, 401)
        self.assertEqual(self.client.patch(url, headers=self.barista, json={"use_making_step": False}).status_code, 403)
        self.assertEqual(self.client.patch(url, headers=self.lead, json={"use_making_step": "maybe"}).status_code, 422)
        for headers, value in ((self.lead, False), (self.admin, True), (self.lead, False)):
            response = self.client.patch(url, headers=headers, json={"use_making_step": value})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.client.get("/api/config").json()["use_making_step"], value)
        # Saving the same value again is not a change and is not audited.
        self.client.patch(url, headers=self.lead, json={"use_making_step": False})
        logs = self.client.get("/api/admin/audit-logs", headers=self.admin).json()
        changes = [log for log in logs if log["action"] == "QUEUE_STEPS_CHANGED"]
        self.assertEqual([log["details"] for log in changes],
                         ["Making step switched off", "Making step switched on", "Making step switched off"])

    def test_guest_status_carries_the_making_step_setting(self):
        """The guest page learns whether to show the Making step, and a change reaches it."""
        order = self.client.post("/api/orders", headers=self.barista, json={
            "idempotency_key": str(uuid.uuid4()), "device_id": "tablet_A", "first_name": "Mei",
            "items": [{"item_name": "Latte", "unit_price_cents": 800, "quantity": 1}]}).json()
        status = lambda: self.client.get(f"/api/orders/{order['id']}/status").json()
        self.assertTrue(status()["use_making_step"])
        self.client.patch("/api/admin/queue-settings", headers=self.lead, json={"use_making_step": False})
        self.assertFalse(status()["use_making_step"])
        self.client.patch("/api/admin/queue-settings", headers=self.lead, json={"use_making_step": True})
        self.assertTrue(status()["use_making_step"])

    def test_received_order_can_go_straight_to_ready_on_both_write_paths(self):
        self.client.patch("/api/admin/queue-settings", headers=self.lead, json={"use_making_step": False})
        direct = self.client.post("/api/orders", headers=self.barista, json={
            "idempotency_key": str(uuid.uuid4()), "device_id": "tablet_A", "first_name": "Mei",
            "items": [{"item_name": "Latte", "unit_price_cents": 800, "quantity": 1}]}).json()
        ready = self.client.patch(f"/api/orders/{direct['id']}/status", headers=self.barista, json={"status": "ready"})
        self.assertEqual(ready.status_code, 200, ready.text)
        self.assertIsNone(ready.json()["making_at"])
        self.assertIsNotNone(ready.json()["ready_at"])

        device = str(uuid.uuid4())
        tablet = self.client.post("/api/sync/tablets", json={"id": device}, headers=self.barista).json()
        def action(kind, payload):
            return self.client.post("/api/sync", headers=self.barista, json=dict(
                id=str(uuid.uuid4()), device_id=device, occurred_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                kind=kind, payload=payload))
        synced = action("order.create", dict(id=str(uuid.uuid4()), idempotency_key=str(uuid.uuid4()), first_name="Jun",
                                             event_day=tablet["event_day"], sequence=1,
                                             items=[dict(item_name="Espresso", unit_price_cents=300, quantity=1)])).json()["order"]
        self.assertEqual(action("order.status", {"id": synced["id"], "status": "ready"}).json()["order"]["status"], "ready")
        board = self.client.get("/api/orders/ready-board").json()
        self.assertEqual({o["order_number"] for o in board["ready"]}, {direct["order_number"], synced["order_number"]})
        self.assertEqual(board["making"], [])
        # With Making skipped, a Received order shows under In progress until Ready.
        waiting = self.client.post("/api/orders", headers=self.barista, json={
            "idempotency_key": str(uuid.uuid4()), "device_id": "tablet_A", "first_name": "Wai",
            "items": [{"item_name": "Latte", "unit_price_cents": 800, "quantity": 1}]}).json()
        board = self.client.get("/api/orders/ready-board").json()
        self.assertEqual([o["order_number"] for o in board["making"]], [waiting["order_number"]])
        self.client.patch("/api/admin/queue-settings", headers=self.lead, json={"use_making_step": True})
        self.assertEqual(self.client.get("/api/orders/ready-board").json()["making"], [])


class TestPickupLookup(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()
        self.barista = auth_headers(get_token(self.client))
        self.lead = auth_headers(get_token(self.client, "booth_lead"))

    def order(self, status=None):
        created = self.client.post("/api/orders", headers=self.barista, json={
            "idempotency_key": str(uuid.uuid4()), "device_id": "tablet_A", "first_name": "Mei",
            "items": [{"item_name": "Latte", "unit_price_cents": 1000, "quantity": 1, "milk": "oat", "temperature": "iced"}]}).json()
        # Collection needs Ready first.
        for step in {None: [], "ready": ["ready"], "collected": ["ready", "collected"]}[status]:
            self.client.patch(f"/api/orders/{created['id']}/status", headers=self.barista, json={"status": step})
        return created

    def test_lookup_returns_the_order_and_its_status_for_every_staff_role(self):
        order = self.order("ready")
        for headers in (self.barista, self.lead, auth_headers(get_token(self.client, "admin"))):
            response = self.client.get(f"/api/orders/{order['id']}/pickup", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["order"]["order_number"], order["order_number"])
            self.assertEqual(body["order"]["status"], "ready")
            self.assertEqual(body["order"]["items"][0]["milk"], "oat")
            self.assertFalse(body["day_closed"])
        self.assertEqual(self.client.get(f"/api/orders/{order['id']}/pickup").status_code, 401)

    def test_unknown_collected_and_closed_day_orders(self):
        self.assertEqual(self.client.get(f"/api/orders/{uuid.uuid4()}/pickup", headers=self.barista).status_code, 404)
        collected = self.order("collected")
        body = self.client.get(f"/api/orders/{collected['id']}/pickup", headers=self.barista).json()
        self.assertEqual(body["order"]["status"], "collected")
        self.assertIsNotNone(body["order"]["collected_at"])
        received = self.order()
        reset = self.client.post("/api/admin/reset-day", headers=self.lead,
                                 json={"closed_event_day": received["event_day"], "confirm": True})
        self.assertEqual(reset.status_code, 200, reset.text)
        closed = self.client.get(f"/api/orders/{received['id']}/pickup", headers=self.barista).json()
        self.assertTrue(closed["day_closed"])
        self.assertIsNone(closed["order"]["first_name"])


if __name__ == "__main__":
    unittest.main()


class TestCollectOnlyWhenReady(unittest.TestCase):
    """A drink that is not Ready can never be collected, on either write path."""

    def setUp(self):
        self.client = get_test_client()
        self.barista = auth_headers(get_token(self.client))
        self.device = str(uuid.uuid4())
        self.tablet = self.client.post("/api/sync/tablets", json={"id": self.device}, headers=self.barista).json()

    def action(self, kind, payload):
        return self.client.post("/api/sync", headers=self.barista, json=dict(
            id=str(uuid.uuid4()), device_id=self.device, occurred_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            kind=kind, payload=payload))

    def test_direct_api_refuses_collecting_received_or_making_orders(self):
        order = self.client.post("/api/orders", headers=self.barista, json={
            "idempotency_key": str(uuid.uuid4()), "device_id": "tablet_A", "first_name": "Early",
            "items": [{"item_name": "Latte", "unit_price_cents": 800, "quantity": 1}]}).json()
        url = f"/api/orders/{order['id']}/status"
        for step in ("received", "making"):
            if step == "making":
                self.client.patch(url, headers=self.barista, json={"status": "making"})
            refused = self.client.patch(url, headers=self.barista, json={"status": "collected"})
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertIn("not ready yet", refused.json()["detail"])
        self.client.patch(url, headers=self.barista, json={"status": "ready"})
        self.assertEqual(self.client.patch(url, headers=self.barista, json={"status": "collected"}).json()["status"], "collected")
        logs = self.client.get("/api/admin/audit-logs", headers=auth_headers(get_token(self.client, "admin"))).json()
        details = [log["details"] for log in logs if log["order_number"] == order["order_number"]]
        self.assertEqual(sum("collected" in d for d in details), 1)

    def test_sync_refuses_collecting_before_ready_and_keeps_no_receipt(self):
        order = self.action("order.create", dict(id=str(uuid.uuid4()), idempotency_key=str(uuid.uuid4()), first_name="Early",
                                                 event_day=self.tablet["event_day"], sequence=1,
                                                 items=[dict(item_name="Espresso", unit_price_cents=300, quantity=1)])).json()["order"]
        self.assertEqual(self.action("order.status", {"id": order["id"], "status": "collected"}).status_code, 409)
        self.action("order.status", {"id": order["id"], "status": "making"})
        refused = self.action("order.status", {"id": order["id"], "status": "collected"})
        self.assertEqual(refused.status_code, 409, refused.text)
        status = self.client.get(f"/api/orders/{order['id']}/status").json()["status"]
        self.assertEqual(status, "making")
        self.action("order.status", {"id": order["id"], "status": "ready"})
        self.assertEqual(self.action("order.status", {"id": order["id"], "status": "collected"}).json()["order"]["status"], "collected")
        # A repeat collect of a collected order is still accepted as a no-change tap.
        self.assertEqual(self.action("order.status", {"id": order["id"], "status": "collected"}).status_code, 200)
