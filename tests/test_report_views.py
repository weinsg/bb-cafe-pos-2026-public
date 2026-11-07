"""Daily sales views and CSV kinds: Sold / Cancelled / Discarded / All orders, all event days."""
import csv
import io
import json
import unittest
import uuid

from tests.test_cancel_discard_settings import Booth

OTHER_DAY = "2026-11-26"


class TestReportViews(Booth):
    def make_day(self):
        sold = self.order("collected", price=800)
        refunded = self.order("ready", price=1000)
        self.assertEqual(self.cancel(refunded, reason="keyed_wrong", money="refunded").status_code, 200)
        unpaid = self.order(price=600)
        self.assertEqual(self.cancel(unpaid, reason="guest_left", money="not_paid", headers=self.lead).status_code, 200)
        kept = self.order(price=700)
        self.assertEqual(self.cancel(kept, reason="changed_mind", money="no_refund").status_code, 200)
        wasted = self.order("ready", price=900)
        made = self.action("order.status", {"id": wasted["id"], "status": "discarded"})
        self.assertEqual(made.status_code, 200, made.text)
        return sold, refunded, unpaid, kept, wasted

    def get(self, path, headers=None):
        response = self.client.get(path, headers=headers or self.lead)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def rows(self, kind, scope="day"):
        response = self.get(f"/api/reports/csv?kind={kind}&event_day={self.tablet['event_day']}&scope={scope}")
        return response, list(csv.reader(io.StringIO(response.text)))

    def test_cancelled_view_tallies_refunds_and_the_final_amount(self):
        """Newest first, reason, money, who; tally; final sales = the Sold total."""
        self.make_day()
        day = self.tablet["event_day"]
        data = self.get(f"/api/reports/orders?kind=cancelled&event_day={day}").json()
        self.assertEqual([o["money"] for o in data["orders"]], ["Paid, no refund", "Not paid yet", "Refunded on POS"])
        self.assertEqual([o["reason"] for o in data["orders"]], ["Guest changed their mind", "Guest left", "Keyed wrong"])
        self.assertEqual([o["by"] for o in data["orders"]], ["barista", "booth_lead", "barista"])
        self.assertTrue(all(o["drinks"] == "1 × Latte (Hot, Dairy)" and o["time"] for o in data["orders"]))
        self.assertEqual((data["refunded_cents"], data["not_paid_cents"], data["no_refund_cents"]), (1000, 600, 700))
        sold = self.get(f"/api/reports/daily?event_day={day}").json()["total_revenue_cents"]
        self.assertEqual(sold, 800 + 700 + 900)  # sold + paid-no-refund + discarded (paid)
        self.assertEqual(data["final_sales_cents"], sold)
        self.assertNotIn("Mei", json.dumps(data))

    def test_discarded_and_all_orders_views(self):
        """Discarded with minutes waited and who; all orders with status and whether they count."""
        self.make_day()
        day = self.tablet["event_day"]
        [wasted] = self.get(f"/api/reports/orders?kind=discarded&event_day={day}").json()["orders"]
        self.assertEqual((wasted["status"], wasted["amount_cents"], wasted["by"]), ("discarded", 900, "barista"))
        self.assertIsInstance(wasted["waited_min"], int)
        every = self.get(f"/api/reports/orders?kind=all&event_day={day}").json()["orders"]
        self.assertEqual([(o["status"], o["counts_in_sales"]) for o in every],
                         [("collected", True), ("cancelled", False), ("cancelled", False), ("cancelled", True), ("discarded", True)])
        self.assertNotIn("Mei", json.dumps(every))

    def test_all_event_days_and_staff_only(self):
        """All event days sums every day; Booth Lead and Admin only."""
        self.order(price=800)
        made = self.client.post("/api/orders", headers=self.barista, json={
            "idempotency_key": str(uuid.uuid4()), "device_id": "tablet_A", "first_name": "Rachel", "event_day": OTHER_DAY,
            "items": [{"item_name": "Latte", "unit_price_cents": 500, "quantity": 2}]})
        self.assertEqual(made.status_code, 201, made.text)
        day = self.get(f"/api/reports/daily?event_day={self.tablet['event_day']}").json()["total_revenue_cents"]
        everything = self.get("/api/reports/daily?scope=all").json()
        self.assertEqual(everything["total_revenue_cents"], day + 1000)
        self.assertEqual(everything["total_cups"], 3)
        self.assertEqual(len(self.get("/api/reports/orders?kind=all&scope=all").json()["orders"]), 2)
        for path in ("/api/reports/orders?kind=all", "/api/reports/csv?kind=cancelled"):
            self.assertEqual(self.client.get(path, headers=self.barista).status_code, 403)
        self.assertEqual(self.client.get("/api/reports/orders?kind=all", headers=self.admin).status_code, 200)

    def test_four_csv_files_one_day_and_all_days(self):
        """Four files, tally rows, per-day and grand totals, no guest names, audited."""
        self.make_day()
        day = self.tablet["event_day"]
        response, sales = self.rows("sales")
        self.assertEqual(sales[0], ["date", "item", "temperature", "milk", "cups", "unit_price", "total"])
        self.assertEqual(sales[-1], [day, "TOTAL", "", "", "3", "-", "24.00"])
        self.assertIn(f"beansbeats_sales_{day}.csv", response.headers["content-disposition"])
        _, cancelled = self.rows("cancelled")
        self.assertEqual(cancelled[0][:5], ["date", "order", "time", "drinks", "amount"])
        self.assertEqual(len([r for r in cancelled[1:] if r and r[0] == day]), 3)
        tally = {r[1]: r[4] for r in cancelled if len(r) > 4 and r[0] == ""}
        self.assertEqual(tally, {"REFUNDED ON POS": "10.00", "NOT PAID YET": "6.00",
                                 "PAID, NO REFUND (counted in sales)": "7.00", "FINAL SALES (matches the POS)": "24.00"})
        _, discarded = self.rows("discarded")
        self.assertEqual(discarded[0][:6], ["date", "order", "time", "drinks", "amount", "waited_min"])
        self.assertEqual(len(discarded), 2)
        response, every = self.rows("all", scope="all")
        self.assertEqual(every[0][:4], ["date", "order", "time", "status"])
        self.assertEqual(len(every), 1 + 5)  # one row per drink
        self.assertIn("beansbeats_all_orders_all-days.csv", response.headers["content-disposition"])
        _, all_sales = self.rows("sales", scope="all")
        self.assertEqual(all_sales[-1], ["ALL EVENT DAYS", "GRAND TOTAL", "", "", "3", "-", "24.00"])
        for kind in ("sales", "cancelled", "discarded", "all"):
            self.assertNotIn("Mei", self.rows(kind)[0].text)
        details = [log["details"] for log in self.logs("CSV_EXPORT")]
        self.assertIn("Exported cancelled orders for " + day, details)
        self.assertIn("Exported all orders for all event days", details)


if __name__ == "__main__":
    unittest.main()
