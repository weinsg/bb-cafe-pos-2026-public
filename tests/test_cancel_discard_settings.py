"""Collect on scan + Undo, settings-change feed, personal logins, cancel, discard."""
import asyncio
import datetime as dt
import json
import unittest
import uuid

from sqlmodel import Session

from app import manage
from app.models import Order
from app.routers.events import broadcaster
from tests.helpers import auth_headers, get_test_client, get_token, test_engine


class Booth(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()
        self.barista = auth_headers(get_token(self.client))
        self.lead = auth_headers(get_token(self.client, "booth_lead"))
        self.admin = auth_headers(get_token(self.client, "admin"))
        self.device = str(uuid.uuid4())
        self.tablet = self.client.post("/api/sync/tablets", json={"id": self.device}, headers=self.barista).json()
        self.sequence = 0

    def action(self, kind, payload, headers=None):
        return self.client.post("/api/sync", headers=headers or self.barista, json=dict(
            id=str(uuid.uuid4()), device_id=self.device, occurred_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            kind=kind, payload=payload))

    def order(self, status="received", price=800, name="Mei"):
        self.sequence += 1
        made = self.action("order.create", dict(
            id=str(uuid.uuid4()), idempotency_key=str(uuid.uuid4()), first_name=name, event_day=self.tablet["event_day"],
            sequence=self.sequence, items=[dict(item_name="Latte", unit_price_cents=price, quantity=1,
                                                milk="dairy", temperature="hot")])).json()["order"]
        for step in ("making", "ready", "collected"):
            if ["received", "making", "ready", "collected"].index(status) >= ["received", "making", "ready", "collected"].index(step):
                made = self.action("order.status", {"id": made["id"], "status": step}).json()["order"]
        return made

    def cancel(self, order, reason="keyed_wrong", money="refunded", headers=None):
        return self.action("order.status", {"id": order["id"], "status": "cancelled", "reason": reason, "money": money,
                                            "status_revision": order.get("status_revision", 0)}, headers)

    def logs(self, action):
        return [log for log in self.client.get("/api/admin/audit-logs", headers=self.admin).json() if log["action"] == action]


class TestCollectOnScanAndUndo(Booth):
    def test_collect_on_scan_defaults_on_and_only_lead_or_admin_change_it(self):
        config = self.client.get("/api/config").json()
        self.assertEqual((config["collect_on_scan"], config["reminders"], config["warn_minutes"], config["discard_minutes"]),
                         (True, True, 30, 60))
        url = "/api/admin/pickup-settings"
        self.assertEqual(self.client.patch(url, headers=self.barista, json={"collect_on_scan": False}).status_code, 403)
        self.assertEqual(self.client.patch(url, headers=self.lead, json={"collect_on_scan": False}).status_code, 200)
        self.assertFalse(self.client.get("/api/config").json()["collect_on_scan"])
        self.assertEqual([log["details"] for log in self.logs("PICKUP_SETTINGS_CHANGED")], ["Collect on scan switched off"])

    def test_undo_returns_a_fresh_collection_to_ready_and_is_audited(self):
        order = self.order("collected")
        undone = self.client.post(f"/api/orders/{order['id']}/undo-collect", headers=self.barista,
                                  json={"status_revision": order["status_revision"]})
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual((undone.json()["status"], undone.json()["collected_at"]), ("ready", None))
        self.assertEqual(len(self.logs("COLLECTION_UNDONE")), 1)
        # A collection older than the undo window must go through a Booth Lead reopen.
        again = self.action("order.status", {"id": order["id"], "status": "collected",
                                             "status_revision": undone.json()["status_revision"]}).json()["order"]
        with Session(test_engine) as session:
            row = session.get(Order, order["id"])
            row.collected_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
            session.add(row)
            session.commit()
        late = self.client.post(f"/api/orders/{order['id']}/undo-collect", headers=self.barista,
                                json={"status_revision": again["status_revision"]})
        self.assertEqual(late.status_code, 409)


class TestCancel(Booth):
    def test_cancel_needs_a_reason_and_money_status_and_leaves_queue_and_board(self):
        order = self.order("ready")
        self.assertEqual(self.cancel(order, reason=None).status_code, 400)
        self.assertEqual(self.cancel(order, money="maybe").status_code, 400)
        response = self.cancel(order, reason="guest_left", money="no_refund")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["order"]["status"], "cancelled")
        self.assertNotIn(order["id"], [o["id"] for o in self.client.get("/api/orders/active", headers=self.barista).json()])
        self.assertEqual(self.client.get("/api/orders/ready-board").json()["ready"], [])
        self.assertEqual(self.client.get(f"/api/orders/{order['id']}/status").json()["status"], "cancelled")
        self.assertEqual(self.client.patch(f"/api/orders/{order['id']}/status", headers=self.barista,
                                           json={"status": "cancelled"}).status_code, 400)
        [log] = self.logs("ORDER_CANCELLED")
        self.assertEqual((log["staff_username"], log["details"]), ("barista", "ready -> cancelled; reason=guest_left; money=no_refund"))

    def test_first_final_status_wins(self):
        order = self.order("ready")
        self.assertEqual(self.action("order.status", {"id": order["id"], "status": "collected"}).json()["order"]["status"], "collected")
        late = self.cancel(order)
        self.assertEqual(late.status_code, 200)
        self.assertEqual(late.json()["order"]["status"], "collected")
        cancelled = self.order()
        self.cancel(cancelled)
        self.assertEqual(self.action("order.status", {"id": cancelled["id"], "status": "making"}).json()["order"]["status"], "cancelled")

    def test_lead_switches_a_baristas_cancel_right(self):
        barista_id = self.client.get("/api/auth/me", headers=self.barista).json()["id"]
        url = "/api/admin/cancel-permissions"
        self.assertEqual(self.client.patch(url, headers=self.barista, json={"user_ids": [barista_id], "enabled": False}).status_code, 403)
        self.assertEqual(self.client.patch(url, headers=self.lead, json={"user_ids": [barista_id], "enabled": False}).status_code, 200)
        self.assertFalse(self.client.get("/api/auth/me", headers=self.barista).json()["can_cancel"])
        self.assertEqual(self.cancel(self.order()).status_code, 403)
        self.assertEqual(self.cancel(self.order(), headers=self.lead).status_code, 200)
        self.client.patch(url, headers=self.lead, json={"user_ids": [barista_id], "enabled": True})
        self.assertEqual(self.cancel(self.order()).status_code, 200)
        self.assertEqual([log["details"] for log in self.logs("CANCEL_ACCESS_CHANGED")],
                         ["Cancel right on for barista", "Cancel right off for barista"])

    def test_daily_sales_match_the_pos(self):
        kept = self.order("collected", price=800)
        self.cancel(self.order(price=900), money="refunded")
        self.cancel(self.order(price=1000), money="not_paid")
        self.cancel(self.order(price=600), money="no_refund")
        discarded = self.order("ready", price=300)
        self.action("order.status", {"id": discarded["id"], "status": "discarded"})
        report = self.client.get(f"/api/reports/daily?event_day={kept['event_day']}", headers=self.lead)
        self.assertEqual(report.status_code, 200, report.text)
        data = report.json()
        self.assertEqual((data["total_cups"], data["total_revenue_cents"]), (3, 800 + 600 + 300))
        self.assertEqual((data["cancelled_orders"], data["refunded_cents"], data["discarded_orders"]), (3, 900, 1))


class TestDiscardAndReminders(Booth):
    def test_only_a_ready_order_can_be_discarded_and_it_is_audited(self):
        early = self.order("making")
        self.assertEqual(self.action("order.status", {"id": early["id"], "status": "discarded"}).status_code, 409)
        ready = self.order("ready")
        done = self.action("order.status", {"id": ready["id"], "status": "discarded"})
        self.assertEqual(done.json()["order"]["status"], "discarded")
        self.assertEqual(self.client.get("/api/orders/ready-board").json()["ready"], [])
        self.assertEqual(self.client.get(f"/api/orders/{ready['id']}/status").json()["status"], "discarded")
        self.assertEqual(len(self.logs("ORDER_DISCARDED")), 1)

    def test_reminder_times_reach_the_guest_page_and_switch_off(self):
        order = self.order("ready")
        guest = self.client.get(f"/api/orders/{order['id']}/status").json()
        self.assertEqual((guest["warn_minutes"], guest["discard_minutes"]), (30, 60))
        url = "/api/admin/pickup-settings"
        self.assertEqual(self.client.patch(url, headers=self.lead, json={"warn_minutes": 50, "discard_minutes": 40}).status_code, 400)
        self.assertEqual(self.client.patch(url, headers=self.lead, json={"warn_minutes": 20, "discard_minutes": 45}).status_code, 200)
        self.assertEqual(self.client.get(f"/api/orders/{order['id']}/status").json()["warn_minutes"], 20)
        self.client.patch(url, headers=self.admin, json={"reminders": False})
        guest = self.client.get(f"/api/orders/{order['id']}/status").json()
        self.assertEqual((guest["warn_minutes"], guest["discard_minutes"]), (None, None))


class TestSettingsStripAndLogins(Booth):
    def test_staff_read_who_changed_what_with_the_saving_devices_id(self):
        change = str(uuid.uuid4())
        self.client.patch("/api/admin/queue-settings", headers={**self.lead, "X-Change-Id": change},
                          json={"use_making_step": False})
        self.assertEqual(self.client.get("/api/admin/setting-changes").status_code, 401)
        [row] = self.client.get("/api/admin/setting-changes", headers=self.barista).json()
        self.assertEqual((row["id"], row["staff_username"], row["details"]), (change, "booth_lead", "Making step switched off"))

    def test_public_live_updates_never_carry_staff_details(self):
        async def capture():
            queue = await broadcaster.subscribe()
            await broadcaster.publish("setting_changed", {"staff_username": "admin_john", "details": "x"})
            message = queue.get_nowait()
            broadcaster.unsubscribe(queue)
            return json.loads(message.removeprefix("data: ").strip())
        self.assertEqual(asyncio.run(capture()), {"type": "setting_changed", "data": {}})

    def test_personal_login_is_created_with_a_typed_password(self):
        typed = iter(["short", "short"])
        with self.assertRaises(SystemExit):
            manage.ask_password(lambda prompt: next(typed))
        password = uuid.uuid4().hex
        typed = iter([password, password])
        with Session(test_engine) as session:
            manage.add_staff(session, "admin_john", "admin", manage.ask_password(lambda prompt: next(typed)))
            with self.assertRaises(SystemExit):
                manage.add_staff(session, "admin_john", "admin", password)
        token = get_token(self.client, "admin_john", password)
        self.assertEqual(self.client.get("/api/auth/me", headers=auth_headers(token)).json()["role"], "admin")
        change = self.client.patch("/api/admin/pickup-settings", headers=auth_headers(token), json={"reminders": False})
        self.assertEqual(change.status_code, 200)
        self.assertEqual(self.logs("PICKUP_SETTINGS_CHANGED")[0]["staff_username"], "admin_john")


if __name__ == "__main__":
    unittest.main()


class TestCancelPlaceAndCancelledTab(Booth):
    """Where Cancel order shows and the Queue's Cancelled tab."""

    def test_cancel_place_default_popup_lead_or_admin_only_audited(self):
        self.assertEqual(self.client.get("/api/config").json()["cancel_place"], "popup")
        url = "/api/admin/cancel-place"
        self.assertEqual(self.client.patch(url, headers=self.barista, json={"cancel_place": "both"}).status_code, 403)
        self.assertEqual(self.client.patch(url, headers=self.lead, json={"cancel_place": "header"}).status_code, 422)
        self.assertEqual(self.client.patch(url, headers=self.lead, json={"cancel_place": "both"}).json(), {"cancel_place": "both"})
        self.assertEqual(self.client.get("/api/config").json()["cancel_place"], "both")
        [row] = self.client.get("/api/admin/setting-changes", headers=self.barista).json()
        self.assertEqual((row["staff_username"], row["details"]), ("booth_lead", "Cancel button in the QR popup and below the main button"))
        self.client.patch(url, headers=self.admin, json={"cancel_place": "both"})  # no change: no new row
        self.assertEqual(len(self.client.get("/api/admin/setting-changes", headers=self.barista).json()), 1)

    def test_cancelled_tab_lists_the_days_cancels_with_reason_money_who_and_when(self):
        first, second, kept = self.order(), self.order("ready"), self.order()
        self.assertEqual(self.cancel(first, reason="guest_left", money="not_paid").status_code, 200)
        self.assertEqual(self.cancel(second, reason="keyed_wrong", money="refunded", headers=self.lead).status_code, 200)
        rows = self.client.get("/api/orders/cancelled", headers=self.barista).json()
        self.assertEqual([r["id"] for r in rows], [second["id"], first["id"]])  # newest first
        self.assertEqual({k: rows[0][k] for k in ("reason", "money", "cancelled_by", "status")},
                         {"reason": "keyed_wrong", "money": "refunded", "cancelled_by": "booth_lead", "status": "cancelled"})
        self.assertTrue(rows[0]["cancelled_at"] and rows[0]["items"] and rows[0]["first_name"])
        self.assertNotIn(kept["id"], [r["id"] for r in rows])
        self.assertEqual(self.client.get("/api/orders/cancelled").status_code, 401)
        me = self.client.get("/api/auth/me", headers=self.barista).json()
        self.client.patch("/api/admin/staff-permissions", headers=self.lead, json={"user_ids": [me["id"]], "enabled": False})
        self.assertEqual(self.client.get("/api/orders/cancelled", headers=self.barista).status_code, 403)
