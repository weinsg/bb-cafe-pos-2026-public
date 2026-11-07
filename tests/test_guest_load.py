"""Guest-page load: one request per guest check, and live updates tagged per order."""

import asyncio
import datetime as dt
import json
import unittest
import uuid

from app.routers.events import broadcaster
from tests.helpers import auth_headers, get_test_client, get_token


class TestGuestPageLoad(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()
        self.barista = auth_headers(get_token(self.client))
        self.lead = auth_headers(get_token(self.client, "booth_lead"))
        self.device = str(uuid.uuid4())
        self.tablet = self.client.post("/api/sync/tablets", json={"id": self.device}, headers=self.barista).json()
        self.sequence = 0

    def action(self, kind, payload):
        return self.client.post("/api/sync", headers=self.barista, json={
            "id": str(uuid.uuid4()), "device_id": self.device, "kind": kind, "payload": payload,
            "occurred_at": dt.datetime.now(dt.timezone.utc).isoformat()})

    def order(self, name="Mei"):
        self.sequence += 1
        response = self.action("order.create", {
            "id": str(uuid.uuid4()), "idempotency_key": str(uuid.uuid4()), "first_name": name,
            "event_day": self.tablet["event_day"], "sequence": self.sequence,
            "items": [{"item_name": "Latte", "unit_price_cents": 600, "quantity": 1}]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["order"]

    def capture(self, change):
        """Run a change and return the public live-update messages it published."""
        async def run():
            queue = await broadcaster.subscribe()
            try:
                await asyncio.to_thread(change)
                messages = []
                while not queue.empty():
                    messages.append(json.loads(queue.get_nowait().removeprefix("data: ").strip()))
                return messages
            finally:
                broadcaster.unsubscribe(queue)
        return asyncio.run(run())

    def test_guest_check_is_one_request_with_closed_days(self):
        """The guest status and the board carry the closed days, so /sync/state is not needed."""
        order = self.order()
        guest = self.client.get(f"/api/orders/{order['id']}/status").json()
        board = self.client.get("/api/orders/ready-board").json()
        self.assertEqual(guest["closed_days"], [])
        self.assertEqual(board["closed_days"], [])
        self.assertRegex(guest["event_tag"], r"^[0-9a-f]{16}$")
        self.assertNotIn(order["id"], guest["event_tag"])

    def test_a_status_change_is_tagged_for_that_guest_only(self):
        """The event names a tag that only this order's page knows; no ID or name on the stream."""
        mine, other = self.order("Mei"), self.order("Raj")
        tag = self.client.get(f"/api/orders/{mine['id']}/status").json()["event_tag"]
        other_tag = self.client.get(f"/api/orders/{other['id']}/status").json()["event_tag"]
        self.assertNotEqual(tag, other_tag)
        messages = self.capture(lambda: self.action("order.status", {"id": mine["id"], "status": "ready"}))
        self.assertEqual(messages, [{"type": "changed", "data": {"guest": tag}}])
        text = json.dumps(messages)
        self.assertNotIn(mine["id"], text)
        self.assertNotIn("Mei", text)
        # The online status route and an Undo of a collection are tagged the same way.
        messages = self.capture(lambda: self.client.patch(
            f"/api/orders/{other['id']}/status", headers=self.barista, json={"status": "ready"}))
        self.assertEqual(messages[-1]["data"], {"guest": other_tag})

    def test_settings_changes_carry_no_tag(self):
        """A settings change is untagged, so guest pages pick it up on their next check."""
        messages = self.capture(lambda: self.client.patch(
            "/api/admin/queue-settings", headers=self.lead, json={"use_making_step": False}))
        self.assertTrue(messages)
        self.assertTrue(all(m["data"] == {} for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
