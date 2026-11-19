import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import subprocess
import sys

from openpyxl import Workbook, load_workbook

from app import create_app, hash_password
from kaspibot.models import db, Product, ParserJob
from kaspibot.services.excel_import import read_alias_counts_from_excel
from kaspibot.services.parser_integration import apply_observation
from kaspibot.services.pagination import collect_pages


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.core = str(Path(self.temp.name) / 'core.sqlite3')
        self.app = create_app(dict(TESTING=True, SECRET_KEY='test-only', DATABASE=self.core,
                                  SQLALCHEMY_DATABASE_URI='sqlite:///' +
                                  (Path(self.temp.name) / 'bot.sqlite3').as_posix()))
        self.runner = self.app.test_cli_runner()
        for command in ('init-db', 'init-bot-db'):
            result = self.runner.invoke(args=[command])
            self.assertEqual(result.exit_code, 0, (result.output, result.exception))
        with closing(sqlite3.connect(self.core)) as conn, conn:
            for role in ('admin', 'employee'):
                conn.execute('INSERT INTO users(username, password_hash, role) VALUES(?,?,?)',
                             (role, hash_password('test-password'), role))
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def login(self, role='admin'):
        self.client.post('/login', data=dict(username=role, password='test-password'))
        self.client.get('/products/new')
        with self.client.session_transaction() as session:
            return session['csrf_token']

    def test_auth_roles_and_csrf_all_archive_routes(self):
        rules = [r for r in self.app.url_map.iter_rules() if r.endpoint.startswith('kaspibot.')]
        self.assertGreater(len(rules), 40)
        for rule in rules:
            # Even uninitialized detail URLs must enforce access before data lookup.
            url = str(rule).replace('<int:product_id>', '1').replace('<int:item_id>', '1')
            url = url.replace('<order_code>', '1').replace('<int:job_id>', '1')
            method = next(iter(rule.methods - {'OPTIONS', 'HEAD'}))
            with self.subTest(url=url, method=method):
                self.assertEqual(self.client.open(url, method=method).status_code, 302)
        self.login('employee')
        self.assertEqual(self.client.get('/bot/').status_code, 403)
        self.client.post('/logout')
        token = self.login()
        self.assertEqual(self.client.post('/bot/api/products', json={}).status_code, 400)
        self.assertEqual(self.client.post('/bot/api/products', json={},
                                         headers={'X-CSRF-Token': token}).status_code, 400)

    def test_pages_links_reports_and_json(self):
        self.login()
        with self.app.app_context():
            db.session.add(Product(name='Tea', sku='TEA', purchase_price=500, stock_qty=4))
            db.session.commit()
        for path in ('/', '/products', '/products/1', '/products/1/edit', '/incoming',
                     '/stock-in', '/onway', '/onway-add', '/onway/1', '/import', '/report',
                     '/orders', '/orders/archive', '/returns', '/chat', '/products/parser/',
                     '/api/summary', '/api/products', '/api/onway', '/api/incoming',
                     '/api/onway/items', '/api/incoming/items', '/api/in/items'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get('/bot' + path).status_code, 200)
        page = self.client.get('/bot/products').get_data(as_text=True)
        self.assertIn('/bot/products/1', page)
        self.assertIn('csrf_token', page)
        report = self.client.get('/bot/report/export')
        self.assertEqual(report.status_code, 200)
        self.assertGreater(len(load_workbook(BytesIO(report.data)).sheetnames), 0)

    def test_product_onway_and_local_chat_leave_manager_unchanged(self):
        token = self.login()
        headers = {'X-CSRF-Token': token}
        result = self.client.post('/bot/api/products', json=dict(name='Coffee', purchase_price=500,
                                                               stock_qty=10), headers=headers)
        self.assertEqual(result.status_code, 201)
        product_id = result.json['product']['id']
        response = self.client.post('/bot/api/onway/add', json=dict(product_id=product_id,
                                                                  sale_price=2000), headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['ok'])
        response = self.client.post('/bot/api/chat', json=dict(message='склад'), headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn('reply', response.json)
        with closing(sqlite3.connect(self.core)) as conn, conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM products').fetchone()[0], 0)

    def test_excel_parser_and_worker_launch(self):
        book = Workbook()
        book.active.append(['Name'])
        book.active.append(['  Tea  '])
        book.active.append(['TEA'])
        stream = BytesIO()
        book.save(stream)
        self.assertEqual(read_alias_counts_from_excel(stream.getvalue(), start_row=2), {'tea': 2})
        self.login()
        page = self.client.get('/bot/products/parser/').get_data(as_text=True)
        self.assertIn('name="csrf"', page)
        with self.app.app_context():
            job = ParserJob(status='running', active_slot='kaspi', config_json='{}')
            db.session.add(job)
            db.session.commit()
            apply_observation(job.id, dict(kaspi_id='12345', name='Parser tea',
                                          url='https://kaspi.kz/shop/p/tea-12345/',
                                          our_price=2500, error=None))
            self.assertIsNotNone(Product.query.filter_by(kaspi_product_id='12345').first())
        env = dict(os.environ, KSM_SECRET_KEY='test-worker', KASPIBOT_DATABASE_URL=
                   self.app.config['SQLALCHEMY_DATABASE_URI'])
        process = subprocess.run([sys.executable, '-m', 'kaspibot.parser_worker', '-1'],
                                 cwd=self.app.root_path, env=env, capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors='replace'))

    def test_external_adapters_with_mocked_services(self):
        token = self.login()
        headers = {'X-CSRF-Token': token}
        with patch('kaspibot.app.get_products', return_value={'data': [
            {'id': '123', 'attributes': {'name': 'Kaspi tea', 'sku': 'KTEA', 'price': 3000}}
        ]}):
            response = self.client.post('/bot/products/sync-kaspi', data={'csrf_token': token})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(Product.query.filter_by(sku='KTEA').one().current_price, 3000)
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-placeholder'}), \
                patch('kaspibot.app._get_gemini_client') as client:
            client.return_value.models.generate_content.return_value = SimpleNamespace(text='Test reply')
            response = self.client.post('/bot/api/chat', json={'message': 'Как открыть импорт?'},
                                        headers=headers)
        self.assertEqual(response.json, {'reply': 'Test reply'})
        with patch('kaspibot.app.get_orders', return_value={'data': []}):
            response = self.client.post('/bot/api/kaspi/sync-orders', json={}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['ok'])

    def test_catalogue_preview_apply_idempotence_conflicts(self):
        with closing(sqlite3.connect(self.core)) as conn, conn:
            conn.execute('INSERT INTO products(name,sku,price_cents,stock) VALUES(?,?,?,?)',
                         ('Tea', 'TEA', 250000, 7))
        preview = self.runner.invoke(args=['transfer-catalogue'])
        self.assertEqual(preview.exit_code, 0, preview.output)
        with self.app.app_context():
            self.assertEqual(Product.query.count(), 0)
        result = self.runner.invoke(args=['transfer-catalogue', '--apply'])
        self.assertEqual(result.exit_code, 0, (result.output, result.exception))
        with self.app.app_context():
            product = Product.query.one()
            self.assertEqual((product.current_price, product.stock_qty), (2500, 7))
            product.stock_qty = 3
            db.session.commit()
        self.assertEqual(self.runner.invoke(args=['transfer-catalogue', '--apply']).exit_code, 0)
        with self.app.app_context():
            self.assertEqual(Product.query.one().stock_qty, 3)
        with closing(sqlite3.connect(self.core)) as conn, conn:
            conn.execute('INSERT INTO products(name,sku,price_cents,stock) VALUES(?,?,?,?)',
                         ('Fractional', 'FRAC', 250050, 1))
        self.assertNotEqual(self.runner.invoke(args=['transfer-catalogue', '--apply']).exit_code, 0)
        with self.app.app_context():
            self.assertEqual(Product.query.count(), 1)

    def test_pagination_completes_and_rejects_repeated_page(self):
        pages = [dict(data=[{'id': 'a'}], meta={'pageCount': 2}),
                 dict(data=[{'id': 'b'}], meta={'pageCount': 2})]
        self.assertEqual(collect_pages(lambda **kw: pages[kw['page_number']]),
                         [{'id': 'a'}, {'id': 'b'}])
        with self.assertRaises(ValueError):
            collect_pages(lambda **kw: pages[0])

    def test_multiple_app_instances_and_same_database_rejected(self):
        with self.assertRaises(RuntimeError):
            create_app(dict(TESTING=True, SECRET_KEY='test', DATABASE=self.core,
                            SQLALCHEMY_DATABASE_URI='sqlite:///' + Path(self.core).as_posix()))
        with self.assertRaises(RuntimeError):
            create_app(dict(TESTING=True, SECRET_KEY='test',
                            DATABASE=str(Path(self.app.instance_path) / 'same.sqlite3'),
                            SQLALCHEMY_DATABASE_URI='sqlite:///same.sqlite3'))
        with patch.dict(os.environ, {'KSM_SECRET_KEY': 'test-worker'}):
            other = create_app(dict(DATABASE=str(Path(self.temp.name) / 'other.sqlite3'),
                                    SQLALCHEMY_DATABASE_URI='sqlite:///:memory:'))
        self.assertEqual(other.test_client().get('/login').status_code, 200)
