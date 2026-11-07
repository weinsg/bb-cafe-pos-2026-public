"""Integration checks for the browser's durable outbox protocol."""
import asyncio
import datetime as dt
import unittest
import uuid

from tests.helpers import auth_headers, get_test_client, get_token
from app.routers.events import EventBroadcaster


class TestDeviceSync(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()
        self.headers = auth_headers(get_token(self.client))
        self.lead = auth_headers(get_token(self.client, "booth_lead"))
        self.device = str(uuid.uuid4())
        self.tablet = self.client.post('/api/sync/tablets', json={'id': self.device}, headers=self.headers).json()

    def action(self, kind, payload, headers=None, **overrides):
        data = dict(id=str(uuid.uuid4()), device_id=self.device,
                    occurred_at=dt.datetime.now(dt.timezone.utc).isoformat(), kind=kind, payload=payload)
        data.update(overrides)
        return self.client.post('/api/sync', json=data, headers=headers or self.headers)

    def create(self, **overrides):
        payload = dict(id=str(uuid.uuid4()), idempotency_key=str(uuid.uuid4()),
                       first_name='PrivateGuest', event_day=self.tablet['event_day'], sequence=1,
                       items=[dict(item_name='Latte', unit_price_cents=600, quantity=1)])
        payload.update(overrides)
        return self.action('order.create', payload)

    def test_receipt_replay_and_monotonic_status_are_atomic(self):
        response = self.create()
        self.assertEqual(response.status_code, 200, response.text)
        order = response.json()['order']
        action_id = str(uuid.uuid4())
        invalid = self.action('order.status', {'id': order['id'], 'status': 'invalid'}, id=action_id)
        self.assertEqual(invalid.status_code, 400)
        first = self.action('order.status', {'id': order['id'], 'status': 'ready'}, id=action_id)
        repeated = self.action('order.status', {'id': order['id'], 'status': 'ready'}, id=action_id)
        self.assertEqual(first.json(), repeated.json())
        stale = self.action('order.status', {'id': order['id'], 'status': 'making'})
        self.assertEqual(stale.json()['order']['status'], 'ready')
        logs = self.client.get('/api/admin/audit-logs', headers=auth_headers(get_token(self.client, "admin"))).json()
        self.assertEqual(sum(log['id'] == action_id for log in logs), 1)
        self.assertFalse(any('PrivateGuest' in log['details'] for log in logs))
        synced = next(log for log in logs if log['id'] == action_id)
        self.assertEqual(synced['device_id'], self.device)
        self.assertIsNotNone(synced['occurred_at'])
        from sqlmodel import Session, select
        from app.models import SyncReceipt
        from tests.helpers import test_engine
        with Session(test_engine) as session:
            self.assertTrue(all('PrivateGuest' not in r.result for r in session.exec(select(SyncReceipt)).all()))
        self.client.post('/api/admin/reset-day', json={'closed_event_day': order['event_day'], 'confirm': True}, headers=self.lead)
        replay_after_reset = self.action('order.status', {'id': order['id'], 'status': 'ready'}, id=action_id)
        self.assertIsNone(replay_after_reset.json()['order']['first_name'])
        self.assertEqual(replay_after_reset.json()['order']['status'], 'collected')

    def test_stable_tablet_number_and_no_number_collisions(self):
        again = self.client.post('/api/sync/tablets', json={'id': self.device}, headers=self.headers).json()
        self.assertEqual(again['prefix'], self.tablet['prefix'])
        second = self.client.post('/api/sync/tablets', json={'id': str(uuid.uuid4())}, headers=self.headers).json()
        self.assertNotEqual(second['prefix'], self.tablet['prefix'])
        first = self.create().json()['order']
        self.assertEqual(first['order_number'], self.tablet['prefix'] + '001')
        self.assertEqual(self.create().status_code, 409)
        second_order = self.create(sequence=2).json()['order']
        self.assertEqual(second_order['order_number'], self.tablet['prefix'] + '002')

    def test_two_devices_tapping_the_same_step_record_one_change(self):
        # One order, the same login on two devices, both tap Ready then Collected.
        other_headers = auth_headers(get_token(self.client))
        other = str(uuid.uuid4())
        self.client.post('/api/sync/tablets', json={'id': other}, headers=other_headers)
        order = self.create().json()['order']
        for status in ('ready', 'collected'):
            self.action('order.status', {'id': order['id'], 'status': status})
            late = self.action('order.status', {'id': order['id'], 'status': status},
                               other_headers, device_id=other)
            self.assertEqual(late.status_code, 200, late.text)
            self.assertEqual(late.json()['order']['status'], status)
        first_collected_at = late.json()['order']['collected_at']
        again = self.action('order.status', {'id': order['id'], 'status': 'collected'}, other_headers, device_id=other)
        self.assertEqual(again.json()['order']['collected_at'], first_collected_at)
        logs = self.client.get('/api/admin/audit-logs', headers=auth_headers(get_token(self.client, "admin"))).json()
        rows = [log for log in logs if log['order_number'] == order['order_number']]
        changes = [log for log in rows if log['action'] == 'STATUS_CHANGE']
        self.assertEqual(sorted(log['details'] for log in changes),
                         ['Requested collected; resulting status collected', 'Requested ready; resulting status ready'])
        self.assertEqual(sum(log['action'] == 'STATUS_UNCHANGED' for log in rows), 3)
        self.assertEqual({log['device_id'] for log in changes}, {self.device})
        report = self.client.get('/api/reports/daily?event_day=' + self.tablet['event_day'], headers=self.lead).json()
        self.assertEqual(report['total_revenue_cents'], 600)

    def test_menu_last_write_wins_and_removal_preserves_reports(self):
        now = dt.datetime.now(dt.timezone.utc)
        new = self.action('menu.update', {'id': 4, 'price_cents': 750}, self.lead)
        self.assertEqual(new.status_code, 200, new.text)
        old = self.action('menu.update', {'id': 4, 'price_cents': 500}, self.lead,
                          occurred_at=(now-dt.timedelta(minutes=2)).isoformat())
        self.assertFalse(old.json()['applied'])
        menu = self.client.get('/api/menu').json()
        self.assertEqual(next(i for i in menu if i['id'] == 4)['price_cents'], 750)
        self.create()
        response = self.action('menu.remove', {'id': 4}, self.lead)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(any(i['id'] == 4 for i in self.client.get('/api/menu').json()))
        report = self.client.get('/api/reports/daily?event_day='+self.tablet['event_day'], headers=self.lead).json()
        self.assertEqual(report['total_revenue_cents'], 600)

    def test_role_enforcement_and_failed_action_keeps_no_receipt(self):
        action_id = str(uuid.uuid4())
        rejected = self.action('menu.update', {'id': 1, 'price_cents': 900}, id=action_id)
        self.assertEqual(rejected.status_code, 403)
        accepted = self.action('menu.update', {'id': 1, 'price_cents': 900}, self.lead, id=action_id)
        self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_reset_rejects_late_offline_orders_and_anonymizes(self):
        day = self.tablet['event_day']
        order = self.create().json()['order']
        response = self.client.post('/api/admin/reset-day', json={'closed_event_day': day, 'confirm': True}, headers=self.lead)
        self.assertEqual(response.status_code, 200, response.text)
        delayed = self.create(sequence=2)
        self.assertEqual(delayed.status_code, 200, delayed.text)
        self.assertFalse(delayed.json()['applied'])
        self.assertEqual(delayed.json()['closed_day'], day)
        self.assertIsNone(self.client.get(f"/api/orders/{order['id']}/status").json()['first_name'])
        self.assertEqual(self.client.get('/api/orders/active', headers=self.headers).json(), [])

    def test_public_events_contain_no_guest_identifiers(self):
        async def check():
            broadcaster = EventBroadcaster()
            queue = await broadcaster.subscribe()
            await broadcaster.publish('order_created', {'id': 'private-random-id', 'first_name': 'PrivateGuest'})
            message = await queue.get()
            self.assertNotIn('PrivateGuest', message)
            self.assertNotIn('private-random-id', message)
        asyncio.run(check())
