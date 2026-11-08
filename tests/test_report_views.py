"""Daily sales views and CSV kinds: Sold / Cancelled / Discarded / All orders, all event days."""
import csv
import io
import json
import unittest
import uuid

from tests.test_cancel_discard_settings import Booth
from sqlmodel import Session, select
from tests.helpers import test_engine

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
        def detail_rows(rows):
            start = next(i for i, r in enumerate(rows) if r and r[:3] == ['date', 'order', 'order_id'])
            return list(csv.DictReader(io.StringIO('\n'.join(','.join('"'+str(v).replace('"','""')+'"' for v in r) for r in rows[start:]))))
        _, cancelled = self.rows("cancelled")
        cancelled_details = detail_rows(cancelled)
        self.assertEqual(len(cancelled_details), 3)
        self.assertEqual({r['money'] for r in cancelled_details}, {'Refunded on POS', 'Not paid yet', 'Paid, no refund'})
        self.assertEqual(sum(float(r['included_line_sales']) for r in cancelled_details), 7)
        tally = {r[0]: r[3] for r in cancelled if len(r) == 5 and r[0] in ('refunded','unpaid','no_refund','final')}
        self.assertEqual(tally, {'refunded':'10.00','unpaid':'6.00','no_refund':'7.00','final':'7.00'})
        _, discarded = self.rows("discarded")
        wasted = detail_rows(discarded)
        self.assertEqual(len(wasted), 1)
        self.assertEqual(wasted[0]['discarded_by'], 'barista')
        self.assertEqual(wasted[0]['included_line_sales'], '9.00')
        self.assertNotIn('reason', wasted[0])
        self.assertIn('ready_to_discarded_min', wasted[0])
        response, every = self.rows("all", scope="all")
        self.assertEqual(len(detail_rows(every)), 5)
        self.assertIn("beansbeats_all_orders_all-days.csv", response.headers["content-disposition"])
        complete, complete_rows = self.rows('complete')
        self.assertIn('COMPLETE', complete.text.upper())
        self.assertIn(['ALL ORDERS'], complete_rows)
        self.assertIn(['CANCELLED'], complete_rows)
        self.assertIn(['DISCARDED'], complete_rows)
        discard_section = complete_rows.index(['DISCARDED'])
        self.assertNotIn('reason', complete_rows[discard_section + 1])
        self.assertIn('reason', complete_rows[complete_rows.index(['CANCELLED']) + 1])
        _, all_sales = self.rows("sales", scope="all")
        self.assertEqual(all_sales[-1], ["ALL EVENT DAYS", "GRAND TOTAL", "", "", "3", "-", "24.00"])
        for kind in ("sales", "cancelled", "discarded", "all"):
            self.assertNotIn("Mei", self.rows(kind)[0].text)
        details = [log["details"] for log in self.logs("CSV_EXPORT")]
        self.assertIn("Exported cancelled orders for " + day, details)
        self.assertIn("Exported all orders for all event days", details)

    def test_cross_day_discard_and_multiline_master_reconcile_without_double_counting(self):
        day = self.tablet['event_day']
        old = self.client.post('/api/orders', headers=self.lead, json={
            'idempotency_key': str(uuid.uuid4()), 'first_name': 'NeverExportName', 'event_day': '2026-10-01',
            'items': [{'item_name':'Latte','unit_price_cents':1000,'quantity':2,'milk':'oat','temperature':'iced'},
                      {'item_name':'Americano','unit_price_cents':600,'quantity':1,'temperature':'hot'}]}).json()
        self.assertEqual(self.client.patch(f"/api/orders/{old['id']}/status", headers=self.lead, json={'status':'ready'}).status_code, 200)
        self.assertEqual(self.action('order.status', {'id':old['id'],'status':'discarded'}).status_code, 200)
        self.assertEqual(self.get(f'/api/reports/orders?kind=discarded&event_day={day}').json()['orders'], [])
        [o] = self.get('/api/reports/orders?kind=discarded&scope=all').json()['orders']
        self.assertEqual((o['order_id'],o['cups'],o['amount_cents'],o['counts_in_sales']), (old['id'],3,2600,True))
        self.assertEqual(o['stages']['ready']['by'], 'booth_lead')
        self.assertEqual(o['stages']['received']['by'], 'booth_lead')
        self.assertEqual(o['stages']['discarded']['device_id'], self.device)
        self.assertTrue(o['stages']['discarded']['synced_at'])
        daily = self.get('/api/reports/daily?scope=all').json()
        self.assertEqual(daily['summary']['discarded'], {'orders':1,'cups':3,'amount_cents':2600})
        self.assertEqual(daily['total_revenue_cents'], 2600)
        response = self.get('/api/reports/csv?kind=all&scope=all')
        rows = list(csv.reader(io.StringIO(response.text)))
        index = next(i for i,r in enumerate(rows) if r[:3]==['date','order','order_id'])
        details = [dict(zip(rows[index],r)) for r in rows[index+1:]]
        self.assertEqual(len(details), 2)
        self.assertEqual(sum(float(r['included_line_sales']) for r in details),26)
        self.assertTrue(all(r['order_total_repeated_do_not_sum']=='26.00' for r in details))
        self.assertNotIn('NeverExportName',response.text)
        self.assertTrue(all(r['discarded_at_sgt'].endswith('+08:00') for r in details))
        self.assertTrue(all(r['collected_by']=='—' for r in details))
        # The same number on another date must not inherit this order's actors.
        with Session(test_engine) as session:
            from app.models import OrderStage
            for row in session.exec(select(OrderStage)).all(): session.delete(row)
            session.commit()
        # REST-only historical stage has no unambiguous receipt; show missing rather than guess.
        [historical] = self.get('/api/reports/orders?kind=all&scope=all').json()['orders']
        self.assertIsNone(historical['stages']['ready']['by'])


if __name__ == "__main__":
    unittest.main()
