"""Unit tests for staff authentication and role access controls."""

import os
import unittest
from unittest import mock

from tests.helpers import auth_headers, get_test_client, get_token, PASSWORDS, WRONG_PASSWORD


class TestAuth(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()

    def test_login_barista_success(self):
        res = self.client.post(
            "/api/auth/login",
            json={"username": "barista", "password": PASSWORDS["barista"]},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["role"], "barista")
        self.assertEqual(data["username"], "barista")
        self.assertTrue("access_token" in data)

    def test_login_booth_lead_success(self):
        res = self.client.post(
            "/api/auth/login",
            json={"username": "booth_lead", "password": PASSWORDS["booth_lead"]},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["role"], "booth_lead")
        self.assertEqual(data["username"], "booth_lead")

    def test_login_invalid_password(self):
        res = self.client.post(
            "/api/auth/login",
            json={"username": "barista", "password": WRONG_PASSWORD},
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("Incorrect username or password", res.json()["detail"])

    def test_get_current_user_profile(self):
        token = get_token(self.client, "barista")
        res = self.client.get("/api/auth/me", headers=auth_headers(token))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["role"], "barista")

    def test_unauthenticated_access_denied(self):
        res = self.client.get("/api/orders/active")
        self.assertEqual(res.status_code, 401)

    def test_logout_records_staff_action(self):
        token = get_token(self.client)
        response = self.client.post('/api/auth/logout', headers=auth_headers(token))
        self.assertEqual(response.status_code, 200)
        admin = get_token(self.client, "admin")
        logs = self.client.get('/api/admin/audit-logs', headers=auth_headers(admin)).json()
        self.assertTrue(any(log['action'] == 'LOGOUT' and log['staff_username'] == 'barista' for log in logs))

    def test_role_enforcement_barista_forbidden_from_admin(self):
        barista_token = get_token(self.client, "barista")
        # Barista trying to update menu must return 403 Forbidden
        res = self.client.patch(
            "/api/menu/1",
            json={"price_cents": 999},
            headers=auth_headers(barista_token),
        )
        self.assertEqual(res.status_code, 403)

    def test_demo_logins_shown_only_off_the_live_server(self):
        """The live server (APP_ENV=production) tells the sign-in page to hide the demo logins."""
        with mock.patch.dict(os.environ, {"APP_ENV": "production"}):
            self.assertIs(self.client.get("/api/config").json()["show_demo_logins"], False)
        with mock.patch.dict(os.environ):
            os.environ.pop("APP_ENV", None)
            self.assertIs(self.client.get("/api/config").json()["show_demo_logins"], True)

    def test_wrong_passwords_are_rate_limited_per_login_and_address(self):
        wrong = {"username": "barista", "password": WRONG_PASSWORD}
        for _ in range(5):
            self.assertEqual(self.client.post("/api/auth/login", json=wrong).status_code, 401)
        right = {"username": "barista", "password": PASSWORDS["barista"]}
        self.assertEqual(self.client.post("/api/auth/login", json=right).status_code, 429)
        # Another login from the same address, and the same login from another address, still work.
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "booth_lead", "password": PASSWORDS["booth_lead"]}).status_code, 200)
        self.assertEqual(self.client.post("/api/auth/login", json=right, headers={"X-Forwarded-For": "10.0.0.9"}).status_code, 200)
        admin = auth_headers(get_token(self.client, "admin"))
        locked = [l for l in self.client.get("/api/admin/audit-logs", headers=admin).json() if l["action"] == "LOGIN_LOCKED"]
        self.assertEqual(len(locked), 1)
        # A correct password before the limit resets the count.
        other = {"X-Forwarded-For": "10.0.0.8"}
        for _ in range(4):
            self.client.post("/api/auth/login", json=wrong, headers=other)
        self.assertEqual(self.client.post("/api/auth/login", json=right, headers=other).status_code, 200)
        for _ in range(4):
            self.assertEqual(self.client.post("/api/auth/login", json=wrong, headers=other).status_code, 401)


if __name__ == "__main__":
    unittest.main()

