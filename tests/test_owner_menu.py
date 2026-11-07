"""Owner menu, add-ons and order validation."""
import datetime as dt
import unittest
import uuid

from sqlmodel import Session, SQLModel

from app.main import seed_default_data
from app.models import MenuItem
from tests.helpers import auth_headers, get_test_client, get_token, test_engine


class TestOwnerMenu(unittest.TestCase):
    def setUp(self):
        self.client = get_test_client()
        self.headers = auth_headers(get_token(self.client))
        self.lead = auth_headers(get_token(self.client, "booth_lead"))
        self.admin = auth_headers(get_token(self.client, "admin"))
        self.device = str(uuid.uuid4())
        self.tablet = self.client.post('/api/sync/tablets', json={'id': self.device}, headers=self.headers).json()
        self.sequence = 0

    def action(self, kind, payload, headers=None):
        data = dict(id=str(uuid.uuid4()), device_id=self.device,
                    occurred_at=dt.datetime.now(dt.timezone.utc).isoformat(), kind=kind, payload=payload)
        return self.client.post('/api/sync', json=data, headers=headers or self.headers)

    def create(self, *items, name='Mei'):
        self.sequence += 1
        return self.action('order.create', dict(
            id=str(uuid.uuid4()), idempotency_key=str(uuid.uuid4()), first_name=name,
            event_day=self.tablet['event_day'], sequence=self.sequence, items=list(items)))

    def test_seeded_menu_is_the_owner_menu_with_milk_flags_and_add_ons(self):
        menu = {i['name']: i for i in self.client.get('/api/menu').json()}
        self.assertEqual({n: i['price_cents'] for n, i in menu.items()},
                         {'Espresso': 300, 'Americano': 600, 'Flat White': 800, 'Latte': 800, 'Pourover': 1000})
        self.assertEqual({n for n, i in menu.items() if not i['takes_milk']}, {'Espresso', 'Americano'})
        config = self.client.get('/api/config').json()
        self.assertEqual((config['iced_surcharge_cents'], config['oat_surcharge_cents']), (100, 100))

    def test_existing_database_switches_once_and_keeps_later_edits(self):
        SQLModel.metadata.drop_all(test_engine)
        SQLModel.metadata.create_all(test_engine)
        with Session(test_engine) as session:
            for name, price in [('Espresso', 400), ('Latte', 600), ('Butter Croissant', 450)]:
                session.add(MenuItem(name=name, category='coffee', price_cents=price))
            session.commit()
            seed_default_data(session)
        self.lead = auth_headers(get_token(self.client, "booth_lead"))
        menu = {i['name']: i['price_cents'] for i in self.client.get('/api/menu').json()}
        self.assertNotIn('Butter Croissant', menu)
        self.assertEqual((menu['Espresso'], menu['Latte'], menu['Pourover']), (300, 800, 1000))
        latte = next(i for i in self.client.get('/api/menu').json() if i['name'] == 'Latte')
        self.client.patch(f"/api/menu/{latte['id']}", json={'price_cents': 750}, headers=self.lead)
        with Session(test_engine) as session:
            seed_default_data(session)  # A restart must not overwrite the Booth Lead's price.
        menu = {i['name']: i['price_cents'] for i in self.client.get('/api/menu').json()}
        self.assertEqual(menu['Latte'], 750)

    def test_add_on_price_is_kept_as_captured(self):
        response = self.create(dict(item_name='Latte', unit_price_cents=1000, milk='oat', temperature='iced', quantity=2))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['order']['total_price_cents'], 2000)

    def test_impossible_order_values_are_rejected_on_both_write_paths(self):
        bad = [dict(item_name='Espresso', unit_price_cents=300, milk='oat', temperature='hot'),
               dict(item_name='Latte', unit_price_cents=800, milk='dairy', temperature='lukewarm'),
               dict(item_name='Latte', unit_price_cents=800, milk='soy', temperature='hot'),
               dict(item_name='Latte', unit_price_cents=800, milk='dairy', temperature='hot', quantity=21),
               dict(item_name='Latte', unit_price_cents=3_000_000_000, milk='dairy', temperature='hot'),
               dict(item_name='Hot chocolate', unit_price_cents=500, temperature='hot')]
        for item in bad:
            self.assertEqual(self.create(item).status_code, 400, item)
            legacy = self.client.post('/api/orders', json=dict(idempotency_key=str(uuid.uuid4()), first_name='Mei',
                                                               items=[item]), headers=self.headers)
            self.assertIn(legacy.status_code, (400, 422), item)
        ok = self.create(dict(item_name='Americano', unit_price_cents=700, milk='none', temperature='iced'))
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_add_on_prices_are_booth_lead_only_and_audited(self):
        self.assertEqual(self.action('surcharge.update', {'name': 'iced', 'price_cents': 150}).status_code, 403)
        self.assertEqual(self.action('surcharge.update', {'name': 'soy', 'price_cents': 150}, self.lead).status_code, 400)
        self.assertEqual(self.action('surcharge.update', {'name': 'oat', 'price_cents': 0}, self.lead).status_code, 400)
        response = self.action('surcharge.update', {'name': 'iced', 'price_cents': 150}, self.lead)
        self.assertTrue(response.json()['applied'])
        self.assertEqual(self.client.get('/api/config').json()['iced_surcharge_cents'], 150)
        logs = self.client.get('/api/admin/audit-logs', headers=self.admin).json()
        self.assertTrue(any('iced add-on: 100 -> 150' in log['details'] for log in logs))

    def test_removed_item_can_be_added_again(self):
        latte = next(i for i in self.client.get('/api/menu').json() if i['name'] == 'Latte')
        self.action('menu.remove', {'id': latte['id']}, self.lead)
        again = self.action('menu.create', {'name': 'Latte', 'category': 'coffee', 'price_cents': 850}, self.lead)
        self.assertEqual(again.status_code, 200, again.text)
        menu = {i['name']: i for i in self.client.get('/api/menu').json()}
        self.assertEqual((menu['Latte']['id'], menu['Latte']['price_cents']), (latte['id'], 850))
        duplicate = self.action('menu.create', {'name': 'Latte', 'category': 'coffee', 'price_cents': 850}, self.lead)
        self.assertEqual(duplicate.status_code, 409)
        black = self.action('menu.create', {'name': 'Long Black', 'category': 'coffee', 'price_cents': 600,
                                            'takes_milk': False}, self.lead)
        self.assertEqual(black.status_code, 200, black.text)
        self.assertFalse(next(i for i in self.client.get('/api/menu').json() if i['name'] == 'Long Black')['takes_milk'])

    def test_reopened_order_returns_to_the_top_of_ready(self):
        first = self.create(dict(item_name='Espresso', unit_price_cents=300), name='Ann').json()['order']
        second = self.create(dict(item_name='Espresso', unit_price_cents=300), name='Ben').json()['order']
        for status in ('making', 'ready', 'collected'):
            self.action('order.status', {'id': first['id'], 'status': status})
        for status in ('making', 'ready'):
            self.action('order.status', {'id': second['id'], 'status': status})
        reopened = self.client.post(f"/api/orders/{first['id']}/reopen", headers=self.lead,
                                    json={'confirm': True, 'status_revision': 0, 'reason': 'guest_not_collected'})
        self.assertEqual(reopened.status_code, 200, reopened.text)
        ready = self.client.get('/api/orders/ready-board').json()['ready']
        self.assertEqual([o['order_number'] for o in ready], [first['order_number'], second['order_number']])

    def test_report_counts_add_ons_and_csv_has_one_row_per_option(self):
        self.create(dict(item_name='Latte', unit_price_cents=1000, milk='oat', temperature='iced', quantity=2),
                    dict(item_name='Latte', unit_price_cents=800, milk='dairy', temperature='hot'),
                    dict(item_name='Espresso', unit_price_cents=400, temperature='iced'))
        day = self.tablet['event_day']
        report = self.client.get(f'/api/reports/daily?event_day={day}', headers=self.lead).json()
        self.assertEqual((report['total_cups'], report['total_revenue_cents']), (4, 3200))
        self.assertEqual((report['iced_cups'], report['oat_cups']), (3, 2))
        self.assertEqual({d['item_name']: d['cups_sold'] for d in report['drinks']}, {'Latte': 3, 'Espresso': 1})
        lines = self.client.get(f'/api/reports/daily/csv?event_day={day}', headers=self.lead).text.splitlines()
        self.assertEqual(lines[0], 'date,item,temperature,milk,cups,unit_price,total')
        self.assertIn(f'{day},Latte,iced,oat,2,10.00,20.00', lines)
        self.assertIn(f'{day},TOTAL,,,4,-,32.00', lines)
        self.assertNotIn('Mei', '\n'.join(lines))


if __name__ == '__main__':
    unittest.main()
