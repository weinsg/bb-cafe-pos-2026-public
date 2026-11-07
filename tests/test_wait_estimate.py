"""Waiting-time estimate (extra feature permitted in the README, 7 Nov 2026)."""
import datetime as dt
import json
import unittest

from app import estimate
from tests.test_cancel_discard_settings import Booth

T0 = dt.datetime(2026, 11, 26, 10, 0, tzinfo=dt.timezone.utc)


def at(seconds):
    return T0 + dt.timedelta(seconds=seconds)


class TestFormula(unittest.TestCase):
    def test_defaults_until_ten_gaps(self):
        """2 ahead with defaults -> (2 + 1) x 2.5-3.5 min = 7-11 min."""
        stats = estimate.compute_stats([(at(0), at(100)), (at(10), at(160))])
        self.assertEqual((stats.low_s, stats.high_s, stats.measured_gaps), (150, 210, 1))
        self.assertEqual(estimate.minutes(2, stats), (7, 11))

    def test_gaps_measured_only_while_a_queue_waits(self):
        """Median and 80th percentile of the last gaps; a gap after an idle spell is left out."""
        gaps = [60, 60, 90, 90, 120, 120, 120, 150, 180, 240, 300]  # 11 queued gaps
        rows, ready = [(at(0), at(0))], 0
        for gap in gaps:
            ready += gap
            rows.append((at(0), at(ready)))  # every order was placed at 10:00, so a queue was waiting
        # Idle: this order was placed 30 min after the last Ready tap, so its 31-min gap is not counted.
        rows.append((at(ready + 1800), at(ready + 1860)))
        stats = estimate.compute_stats(rows)
        self.assertEqual(stats.measured_gaps, 11)
        self.assertEqual(stats.low_s, 120)
        self.assertAlmostEqual(stats.high_s, 180)  # percentile_cont
        self.assertEqual(estimate.minutes(0, stats), (2, 3))
        self.assertEqual(estimate.minutes(4, stats), (10, 15))

    def test_high_always_leans_above_low(self):
        """At least 1 minute, and high is at least low + 1."""
        steady = estimate.DayStats(low_s=30, high_s=30, measured_gaps=20, average_wait_min=None)
        self.assertEqual(estimate.minutes(0, steady), (1, 2))
        self.assertEqual(estimate.minutes(3, steady), (2, 3))

    def test_average_wait_needs_five_ready_orders(self):
        """Mean order-to-Ready in whole minutes, from 5 orders."""
        rows = [(at(0), at(240)), (at(0), at(300)), (at(0), at(360)), (at(0), at(420))]
        self.assertIsNone(estimate.compute_stats(rows).average_wait_min)
        self.assertEqual(estimate.compute_stats(rows + [(at(0), at(480))]).average_wait_min, 6)


class TestOnTheBooth(Booth):
    def status(self, order):
        return self.client.get(f"/api/orders/{order['id']}/status").json()

    def test_guest_page_shows_the_estimate_and_orders_ahead(self):
        """2 ahead with the defaults shows 7-11 min."""
        first, second, third = self.order(), self.order(), self.order()
        page = self.status(third)
        self.assertEqual((page["orders_ahead"], page["estimate_low_min"], page["estimate_high_min"]), (2, 7, 11))
        self.assertEqual(self.status(first)["orders_ahead"], 0)
        ready = self.order("ready")
        self.assertIsNone(self.status(ready)["estimate_low_min"])
        self.assertIsNone(self.status(ready)["orders_ahead"])

    def test_only_waiting_orders_placed_earlier_are_ahead(self):
        """Cancelled, discarded, Ready and collected orders are not ahead."""
        self.order("collected")
        self.order("ready")
        cancelled = self.order()
        self.assertEqual(self.action("order.status", {"id": cancelled["id"], "status": "cancelled",
                                                      "reason": "guest_left", "money": "refunded"}).status_code, 200)
        making = self.order("making")
        mine = self.order()
        self.order()  # placed after mine: not ahead
        self.assertEqual(self.status(mine)["orders_ahead"], 1)
        self.assertEqual(self.status(making)["orders_ahead"], 0)

    def test_queue_gets_the_new_order_estimate_staff_only(self):
        """staff-only figures; average wait from 5 Ready orders."""
        self.assertEqual(self.client.get("/api/orders/wait-estimate").status_code, 401)
        for _ in range(3):
            self.order()
        figures = self.client.get("/api/orders/wait-estimate", headers=self.barista).json()
        self.assertEqual((figures["waiting"], figures["new_order_low_min"], figures["new_order_high_min"]), (3, 10, 14))
        self.assertIsNone(figures["average_wait_min"])
        for _ in range(5):
            self.order("ready")
        figures = self.client.get("/api/orders/wait-estimate", headers=self.barista).json()
        self.assertEqual(figures["average_wait_min"], 0)
        self.assertEqual(figures["waiting"], 3)

    def test_figures_carry_no_other_guest(self):
        """The estimate gives counts only; no other guest's name or order ID, no late flag."""
        other = self.order(name="Raj")
        mine = self.order(name="Mei")
        text = json.dumps(self.status(mine))
        self.assertNotIn("Raj", text)
        self.assertNotIn(other["id"], text)
        self.assertNotIn("late", text.lower())


if __name__ == "__main__":
    unittest.main()


class TestEstimateSetting(Booth):
    """The estimate on the ready board and on the guest's phone can each be switched off."""

    def switch(self, headers, **values):
        return self.client.patch("/api/admin/estimate-settings", headers=headers, json=values)

    def test_two_switches_default_on_lead_or_admin_only_audited(self):
        config = self.client.get("/api/config").json()
        self.assertEqual((config["estimate_on_board"], config["estimate_on_guest"]), (True, True))
        self.assertEqual(self.switch(self.barista, on_guest=False).status_code, 403)
        self.assertEqual(self.switch(self.lead, on_guest=False).json(), {"on_board": True, "on_guest": False, "display": "range"})
        self.assertEqual(self.switch(self.admin, on_board=False).json(), {"on_board": False, "on_guest": False, "display": "range"})
        details = [r["details"] for r in self.client.get("/api/admin/setting-changes", headers=self.barista).json()]
        self.assertEqual(sorted(details), ["Waiting-time estimate on the guest's phone switched off",
                                           "Waiting-time estimate on the ready board switched off"])
        self.switch(self.admin, on_board=False)  # no change: no new audit row
        self.assertEqual(len(self.client.get("/api/admin/setting-changes", headers=self.barista).json()), 2)

    def test_phone_switch_hides_guest_figures_board_switch_hides_board_staff_always(self):
        self.order()
        mine = self.order()
        url = f"/api/orders/{mine['id']}/status"
        self.assertEqual(self.client.get(url).json()["orders_ahead"], 1)
        self.assertEqual(self.client.get("/api/orders/ready-board").json()["estimate"], {"low_min": 7, "high_min": 11, "display": "range"})
        self.switch(self.lead, on_guest=False)
        page = self.client.get(url).json()
        self.assertEqual([page[k] for k in ("estimate_low_min", "estimate_high_min", "orders_ahead", "average_wait_min")],
                         [None, None, None, None])
        self.assertIsNotNone(self.client.get("/api/orders/ready-board").json()["estimate"])
        self.switch(self.lead, on_board=False)
        self.assertIsNone(self.client.get("/api/orders/ready-board").json()["estimate"])
        # Staff screens always get the figures.
        self.assertEqual(self.client.get("/api/orders/wait-estimate", headers=self.barista).json()["waiting"], 2)
        self.switch(self.lead, on_guest=True, on_board=True)
        self.assertEqual(self.client.get(url).json()["orders_ahead"], 1)

    def test_display_format_reaches_the_guest_page_and_the_board(self):
        """Range by default; upper / average / average_upper on request; audited."""
        self.order()
        mine = self.order()
        url = f"/api/orders/{mine['id']}/status"
        self.assertEqual(self.client.get("/api/config").json()["estimate_display"], "range")
        self.assertEqual(self.client.get(url).json()["estimate_display"], "range")
        self.assertEqual(self.switch(self.barista, display="upper").status_code, 403)
        self.assertEqual(self.switch(self.lead, display="mean").status_code, 422)
        self.assertEqual(self.switch(self.lead, display="upper").json()["display"], "upper")
        self.assertEqual(self.client.get(url).json()["estimate_display"], "upper")
        self.assertEqual(self.client.get("/api/orders/ready-board").json()["estimate"]["display"], "upper")
        self.switch(self.admin, display="average_upper")
        self.assertEqual(self.client.get("/api/config").json()["estimate_display"], "average_upper")
        details = [r["details"] for r in self.client.get("/api/admin/setting-changes", headers=self.barista).json()]
        self.assertIn("Waiting-time estimate shows as the upper bound (up to 11 min)", details)
        self.assertIn("Waiting-time estimate shows as average to upper bound (about 9–11 min)", details)
