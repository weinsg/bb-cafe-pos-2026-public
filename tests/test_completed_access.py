"""Completed history, explicit collection correction and account permissions."""
import unittest
import uuid
import datetime as dt
from sqlmodel import Session, select
from app.models import User
from tests.helpers import auth_headers, get_test_client, get_token, test_engine


class TestCompletedAccess(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()
        self.barista = auth_headers(get_token(self.client))
        self.lead = auth_headers(get_token(self.client, "booth_lead"))
        self.admin = auth_headers(get_token(self.client, "admin"))
        self.device = str(uuid.uuid4())
        self.tablet = self.client.post('/api/sync/tablets', json={'id': self.device}, headers=self.barista).json()

    def completed_order(self):
        order = self.client.post('/api/orders', headers=self.barista, json={
            'idempotency_key': str(uuid.uuid4()), 'first_name': 'PrivateGuest',
            'items': [{'item_name': 'Latte', 'unit_price_cents': 600}], 'event_day': self.tablet['event_day'],
        }).json()
        # Collection needs Ready first.
        self.client.patch(f"/api/orders/{order['id']}/status", json={'status': 'ready'}, headers=self.barista)
        return self.client.patch(f"/api/orders/{order['id']}/status", json={'status': 'collected'}, headers=self.barista).json()

    def test_completed_history_is_staff_only_and_preserves_order(self):
        order = self.completed_order()
        self.assertEqual(self.client.get('/api/orders/completed').status_code, 401)
        for headers in (self.barista, self.lead, self.admin):
            response = self.client.get('/api/orders/completed', headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()[0]['id'], order['id'])
            self.assertEqual(response.json()[0]['first_name'], 'PrivateGuest')
        self.assertEqual(self.client.get('/api/orders/active', headers=self.barista).json(), [])

    def test_audit_is_admin_only_and_admin_has_operational_access(self):
        for headers in (self.barista, self.lead):
            self.assertEqual(self.client.get('/api/admin/audit-logs', headers=headers).status_code, 403)
        self.assertEqual(self.client.get('/api/admin/audit-logs', headers=self.admin).status_code, 200)
        self.assertEqual(self.client.get('/api/reports/daily', headers=self.admin).status_code, 200)
        self.assertEqual(self.client.patch('/api/menu/1', json={'price_cents': 450}, headers=self.admin).status_code, 200)

    def test_batch_permissions_and_individual_override_apply_to_current_session(self):
        with Session(test_engine) as session:
            first = session.exec(select(User).where(User.username == 'barista')).one()
            second = User(username='barista_2', password_hash=first.password_hash, role='barista')
            session.add(second); session.commit(); session.refresh(second)
            ids = [first.id, second.id]
        self.assertEqual(self.client.patch('/api/admin/staff-permissions', json={'user_ids': ids, 'enabled': False}, headers=self.barista).status_code, 403)
        response = self.client.patch('/api/admin/staff-permissions', json={'user_ids': ids, 'enabled': False}, headers=self.lead)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(all(not row['can_view_completed'] for row in response.json()))
        self.assertFalse(self.client.get('/api/auth/me', headers=self.barista).json()['can_view_completed'])
        self.assertEqual(self.client.get('/api/orders/completed', headers=self.barista).status_code, 403)
        self.assertEqual(self.client.get('/api/orders/active', headers=self.barista).status_code, 200)
        response = self.client.patch('/api/admin/staff-permissions', json={'user_ids': [ids[0]], 'enabled': True}, headers=self.admin)
        by_id = {row['id']: row for row in response.json()}
        self.assertTrue(by_id[ids[0]]['can_view_completed'])
        self.assertFalse(by_id[ids[1]]['can_view_completed'])
        admin_id = self.client.get('/api/auth/me', headers=self.admin).json()['id']
        self.assertEqual(self.client.patch('/api/admin/staff-permissions', json={'user_ids': [admin_id], 'enabled': False}, headers=self.lead).status_code, 400)

    def test_admin_controls_lead_rights_without_allowing_peer_or_admin_changes(self):
        lead_id = self.client.get('/api/auth/me', headers=self.lead).json()['id']
        admin_id = self.client.get('/api/auth/me', headers=self.admin).json()['id']
        self.assertIn(lead_id, [u['id'] for u in self.client.get('/api/admin/staff-permissions', headers=self.admin).json()])
        self.assertNotIn(lead_id, [u['id'] for u in self.client.get('/api/admin/staff-permissions', headers=self.lead).json()])
        for endpoint, right in [('staff-permissions', 'can_view_completed'), ('cancel-permissions', 'can_cancel')]:
            url = '/api/admin/' + endpoint
            self.assertEqual(self.client.patch(url, headers=self.admin, json={'user_ids': [lead_id], 'enabled': False}).status_code, 200)
            self.assertFalse(self.client.get('/api/auth/me', headers=self.lead).json()[right])
            self.assertTrue(self.client.get('/api/auth/me', headers=self.admin).json()[right])
            for actor in [self.lead, self.admin]:
                self.assertEqual(self.client.patch(url, headers=actor, json={'user_ids': [admin_id], 'enabled': False}).status_code, 400)
            self.assertEqual(self.client.patch(url, headers=self.lead, json={'user_ids': [lead_id], 'enabled': True}).status_code, 400)
        self.assertEqual(self.client.get('/api/orders/completed', headers=self.lead).status_code, 403)
        # Turning these rights off does not remove the Lead's report/menu/Barista-management role.
        self.assertEqual(self.client.get('/api/reports/daily', headers=self.lead).status_code, 200)
        order = self.completed_order()
        device = self.device
        response = self.client.post('/api/sync', headers=self.lead, json={
            'id': str(uuid.uuid4()), 'device_id': device, 'occurred_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'kind': 'order.status', 'payload': {'id': order['id'], 'status': 'cancelled', 'reason': 'duplicate', 'money': 'no_refund'}})
        # Final orders cannot be cancelled by any role; verify a live order instead.
        fresh = self.client.post('/api/orders', headers=self.lead, json={
            'idempotency_key': str(uuid.uuid4()), 'first_name': 'Guest', 'items': [{'item_name': 'Latte', 'unit_price_cents': 800}]}).json()
        response = self.client.post('/api/sync', headers=self.lead, json={
            'id': str(uuid.uuid4()), 'device_id': device, 'occurred_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'kind': 'order.status', 'payload': {'id': fresh['id'], 'status': 'cancelled', 'reason': 'duplicate', 'money': 'no_refund'}})
        self.assertEqual(response.status_code, 403)
        for endpoint in ['staff-permissions', 'cancel-permissions']:
            self.assertEqual(self.client.patch('/api/admin/' + endpoint, headers=self.admin,
                json={'user_ids': [lead_id], 'enabled': True}).status_code, 200)
        self.assertTrue(self.client.get('/api/auth/me', headers=self.lead).json()['can_cancel'])

    def test_reopen_is_audited_and_old_offline_status_cannot_recollect(self):
        order = self.completed_order()
        payload = {'confirm': True, 'reason': 'accidental_tap', 'status_revision': 0}
        url = f"/api/orders/{order['id']}/reopen"
        self.assertEqual(self.client.post(url, json=payload, headers=self.barista).status_code, 403)
        result = self.client.post(url, json=payload, headers=self.lead)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['status'], 'ready')
        self.assertEqual(result.json()['status_revision'], 1)
        self.assertIsNone(result.json()['collected_at'])
        stale = self.client.post('/api/sync', headers=self.barista, json={
            'id': str(uuid.uuid4()), 'device_id': self.device, 'occurred_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'kind': 'order.status', 'payload': {'id': order['id'], 'status': 'collected', 'status_revision': 0},
        })
        self.assertEqual(stale.status_code, 200, stale.text)
        self.assertFalse(stale.json()['applied'])
        self.assertEqual(stale.json()['order']['status'], 'ready')
        self.assertEqual(self.client.patch(f"/api/orders/{order['id']}/status", headers=self.barista, json={'status': 'collected', 'status_revision': 0}).status_code, 409)
        response = self.client.patch(f"/api/orders/{order['id']}/status", headers=self.barista, json={'status': 'collected', 'status_revision': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post(url, json=payload, headers=self.admin).status_code, 409)
        payload['status_revision'] = 1
        self.assertEqual(self.client.post(url, json=payload, headers=self.admin).status_code, 200)
        logs = self.client.get('/api/admin/audit-logs', headers=self.admin).json()
        corrections = [log for log in logs if log['action'] == 'COLLECTION_CORRECTED']
        self.assertEqual(len(corrections), 2)
        self.assertTrue(all('PrivateGuest' not in log['details'] for log in corrections))

    def test_closed_day_cannot_be_reopened_and_confirmation_is_required(self):
        order = self.completed_order()
        url = f"/api/orders/{order['id']}/reopen"
        payload = {'confirm': False, 'reason': 'guest_not_collected', 'status_revision': 0}
        self.assertEqual(self.client.post(url, json=payload, headers=self.lead).status_code, 400)
        self.client.post('/api/admin/reset-day', headers=self.lead, json={'closed_event_day': order['event_day'], 'confirm': True})
        payload['confirm'] = True
        self.assertEqual(self.client.post(url, json=payload, headers=self.admin).status_code, 409)
        rows = self.client.get('/api/orders/completed', headers=self.admin).json()
        self.assertIsNone(rows[0]['first_name'])
