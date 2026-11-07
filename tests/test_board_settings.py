"""Shared board layout persistence, public reading and Admin-only changes."""
import unittest

from sqlmodel import Session

from app.models import BoardSettings
from tests.helpers import auth_headers, get_test_client, get_token, test_engine


class TestBoardSettings(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()
        self.admin = auth_headers(get_token(self.client, "admin"))

    def test_only_admin_can_change_the_shared_layout(self):
        url = "/api/admin/board-settings"
        self.assertEqual(self.client.patch(url, json={"ready_board_position": "right"}).status_code, 401)
        for username, password in (("barista", None), ("booth_lead", None)):
            headers = auth_headers(get_token(self.client, username, password))
            self.assertEqual(self.client.patch(url, headers=headers, json={"ready_board_position": "right"}).status_code, 403)
        self.assertEqual(self.client.get("/api/orders/ready-board").json()["ready_board_position"], "left")

    def test_layout_persists_and_is_public_without_exposing_audit(self):
        for position in ("right", "left"):
            response = self.client.patch("/api/admin/board-settings", headers=self.admin, json={"ready_board_position": position})
            self.assertEqual(response.status_code, 200, response.text)
            with Session(test_engine) as session:
                self.assertEqual(session.get(BoardSettings, 1).ready_position, position)
            for endpoint in ("/api/config", "/api/orders/ready-board"):
                self.assertEqual(self.client.get(endpoint).json()["ready_board_position"], position)
        logs = self.client.get("/api/admin/audit-logs", headers=self.admin).json()
        changes = [log for log in logs if log["action"] == "BOARD_LAYOUT_CHANGED"]
        self.assertEqual(len(changes), 2)
        self.assertTrue(all(log["staff_username"] == "admin" for log in changes))
        self.assertEqual(self.client.get("/api/admin/audit-logs").status_code, 401)

    def test_invalid_layout_does_not_change_the_saved_setting(self):
        for payload in ({}, {"ready_board_position": "both"}, {"ready_board_position": None}):
            response = self.client.patch("/api/admin/board-settings", headers=self.admin, json=payload)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/config").json()["ready_board_position"], "left")
