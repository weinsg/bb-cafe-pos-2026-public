"""Charity counter on the ready board (extra feature permitted in the README, 7 Nov 2026)."""
import unittest
import uuid

from tests.test_cancel_discard_settings import Booth

OTHER_DAY = "2026-11-26"


class TestCharityCounter(Booth):
    def counter(self):
        return self.client.get("/api/orders/ready-board").json()["charity_counter"]

    def set_counter(self, show, period, headers=None):
        return self.client.patch("/api/admin/charity-counter", headers=headers or self.admin,
                                 json={"show": show, "period": period})

    def report(self, day):
        return self.client.get(f"/api/reports/daily?event_day={day}", headers=self.lead).json()

    def other_day_order(self, quantity=2):
        response = self.client.post("/api/orders", headers=self.barista, json={
            "idempotency_key": str(uuid.uuid4()), "device_id": "tablet_A", "first_name": "Rachel",
            "event_day": OTHER_DAY, "items": [{"item_name": "Latte", "unit_price_cents": 600, "quantity": quantity}]})
        self.assertEqual(response.status_code, 201, response.text)

    def test_admin_only_setting_defaults_audited_and_announced(self):
        config = self.client.get("/api/config").json()
        self.assertEqual((config["charity_show"], config["charity_period"]), ("cups", "today"))
        self.assertEqual(self.set_counter("cups_amount", "all", self.lead).status_code, 403)
        self.assertEqual(self.set_counter("cups_amount", "all", self.barista).status_code, 403)
        self.assertEqual(self.set_counter("lots", "today").status_code, 422)
        self.assertEqual(self.set_counter("cups_amount", "all").json(), {"show": "cups_amount", "period": "all"})
        [row] = self.client.get("/api/admin/setting-changes", headers=self.barista).json()
        self.assertEqual((row["staff_username"], row["details"]), ("admin", "Charity counter: cups and amount, all event days"))
        self.set_counter("cups_amount", "all")  # no change: no second audit row
        self.assertEqual(len(self.client.get("/api/admin/setting-changes", headers=self.barista).json()), 1)

    def test_board_shows_cups_amount_or_nothing(self):
        self.order(price=700)
        self.assertEqual(self.counter(), {"cups": 1, "amount_cents": None, "period": "today"})
        self.set_counter("cups_amount", "today")
        self.assertEqual(self.counter(), {"cups": 1, "amount_cents": 700, "period": "today"})
        self.set_counter("off", "today")
        self.assertIsNone(self.counter())
        board = self.client.get("/api/orders/ready-board").text
        self.assertNotIn("Mei", board.split('"ready"')[0])

    def test_counts_match_the_daily_sales_report(self):
        """Refunded cancel left out, discarded counted; all days = sum of reports."""
        self.set_counter("cups_amount", "today")
        self.order(price=800)
        refunded = self.order(price=900)
        self.action("order.status", {"id": refunded["id"], "status": "cancelled", "reason": "keyed_wrong",
                                     "money": "refunded"})
        kept = self.order(price=650)
        self.action("order.status", {"id": kept["id"], "status": "cancelled", "reason": "guest_left",
                                     "money": "no_refund"})
        ready = self.order("ready", price=500)
        self.assertEqual(self.action("order.status", {"id": ready["id"], "status": "discarded"}).status_code, 200)
        today = self.report(self.tablet["event_day"])
        self.assertEqual(self.counter()["cups"], today["total_cups"])
        self.assertEqual(self.counter()["amount_cents"], today["total_revenue_cents"])
        self.assertEqual((today["total_cups"], today["total_revenue_cents"]), (3, 1950))
        self.other_day_order()
        self.set_counter("cups_amount", "all")
        other = self.report(OTHER_DAY)
        self.assertEqual(self.counter()["cups"], today["total_cups"] + other["total_cups"])
        self.assertEqual(self.counter()["amount_cents"], today["total_revenue_cents"] + other["total_revenue_cents"])

    def test_reset_keeps_the_all_days_count(self):
        self.other_day_order(quantity=3)
        self.order()
        self.set_counter("cups", "all")
        self.assertEqual(self.counter()["cups"], 4)
        reset = self.client.post("/api/admin/reset-day", headers=self.lead,
                                 json={"closed_event_day": OTHER_DAY, "confirm": True})
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(self.counter()["cups"], 4)
        self.set_counter("cups", "today")
        self.assertEqual(self.counter()["cups"], 1)


if __name__ == "__main__":
    unittest.main()
