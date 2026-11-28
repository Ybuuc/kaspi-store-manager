import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from app import create_app
from kaspibot.database import ArchiveConnection
from kaspibot.models import db, Product, ParserJob
from kaspibot.services.parser_integration import apply_observation
from tools.backup_archive import backup
from tools.finance import calculate
from tools.forecast import simulate


class ArchiveFirstTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / 'app.db'
        schema = (Path(__file__).parent / 'fixtures/archive-schema.sql').read_text(encoding='utf-8')
        with closing(sqlite3.connect(self.database, factory=ArchiveConnection)) as conn, conn:
            conn.executescript(schema)
            conn.execute('INSERT INTO products(id,name,sku,purchase_price,stock_qty,current_price) VALUES(?,?,?,?,?,?)',
                         (19, 'Archive tea', None, 500, 12, 2500))
            conn.execute('INSERT INTO product_aliases(id,product_id,alias) VALUES(43,19,?)', ('tea export',))
            self.original = self.snapshot(conn)
        self.app = create_app(dict(TESTING=True, SECRET_KEY='archive-test-only', DATABASE=str(self.database)))
        self.runner = self.app.test_cli_runner()
        result = self.runner.invoke(args=['init-db'])
        self.assertEqual(result.exit_code, 0, (result.output, result.exception))
        for role in ('admin', 'employee'):
            result = self.runner.invoke(args=['create-user', '--role', role, '--password', 'test-password', role])
            self.assertEqual(result.exit_code, 0, result.output)
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def snapshot(self, conn):
        result = {}
        for table, in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            result[table] = (columns, conn.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall())
        return result

    def login(self, role='admin'):
        self.client.post('/management/login', data=dict(username=role,password='test-password'))
        self.client.get('/management/products/new')
        with self.client.session_transaction() as session:
            return session['csrf_token']

    def test_migration_preserves_every_original_field_and_identifier(self):
        self.assertEqual(self.runner.invoke(args=['init-db']).exit_code, 0)
        with closing(sqlite3.connect(self.database)) as conn:
            for table, (columns, expected) in self.original.items():
                select = ','.join('"'+c+'"' for c in columns)
                self.assertEqual(conn.execute(f'SELECT {select} FROM "{table}" ORDER BY id').fetchall(), expected)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        with self.app.app_context():
            p = db.session.get(Product, 19)
            self.assertEqual(p.price_cents, 250000)
            self.assertIsNone(p.sku)

    def test_one_database_management_changes_are_visible_in_archive(self):
        token = self.login()
        self.assertEqual(self.client.post('/management/products/19/stock_qty', data=dict(stock_qty='8',csrf_token=token)).status_code, 302)
        self.assertEqual(self.client.post('/management/products/19/price', data=dict(price='2700.50',csrf_token=token)).status_code, 302)
        self.assertEqual(self.client.post('/management/products/19/threshold', data=dict(threshold='10',csrf_token=token)).status_code, 302)
        with self.app.app_context():
            p = db.session.get(Product,19)
            self.assertEqual((p.stock_qty,p.price_cents,p.current_price),(8,270050,2700.5))
        page = self.client.get('/products').get_data(as_text=True)
        self.assertIn('Низкий остаток по учёту:', page)
        self.assertIn('stock=low',page)
        self.assertIn('Archive tea',page)
        self.assertEqual(self.client.post('/management/products/19/archive',data=dict(csrf_token=token)).status_code,302)
        self.assertNotIn('Archive tea',self.client.get('/products').get_data(as_text=True))
        self.assertEqual(self.client.put('/api/products/19',json={'stock_qty':99},headers={'X-CSRF-Token':token}).status_code,409)
        self.client.post('/management/products/19/restore',data=dict(csrf_token=token))
        self.assertIn('Archive tea',self.client.get('/products').get_data(as_text=True))

    def test_archive_mutations_are_audited_and_hard_delete_is_disabled(self):
        token=self.login()
        headers={'X-CSRF-Token':token}
        result=self.client.post('/api/products',json=dict(name='Coffee',sku='COFFEE',category_name='Food',purchase_price=400,stock_qty=7),headers=headers)
        self.assertEqual(result.status_code,201,result.data)
        product_id=result.json['product']['id']
        self.assertEqual(self.client.put(f'/api/products/{product_id}',json=dict(stock_qty=9),headers=headers).status_code,200)
        self.assertEqual(self.client.delete(f'/api/products/{product_id}',headers=headers).status_code,200)
        with closing(sqlite3.connect(self.database)) as conn:
            rows=conn.execute('SELECT action,actor_id,occurred_at FROM audit_log WHERE product_id=?',(product_id,)).fetchall()
            self.assertTrue({'create','stock','archived_at'} <= {r[0] for r in rows})
            self.assertTrue(all(r[1]>0 and r[2] for r in rows))
            self.assertIsNotNone(conn.execute('SELECT id FROM products WHERE id=?',(product_id,)).fetchone())

    def test_auth_csrf_role_revocation_and_archive_pages(self):
        self.assertEqual(self.client.get('/products').status_code,302)
        self.assertEqual(self.client.post('/api/products',json={}).status_code,401)
        self.login('employee')
        self.assertEqual(self.client.get('/products').status_code,200)
        for path in ('/report','/chat','/products/parser/','/management/admin','/management/reports/sales'):
            self.assertEqual(self.client.get(path).status_code,403,path)
        self.client.post('/management/logout')
        token=self.login()
        self.assertEqual(self.client.post('/api/onway/add',json={'product_id':19,'sale_price':3000}).status_code,400)
        self.assertEqual(self.client.post('/api/onway/add',json={'product_id':19,'sale_price':3000},headers={'X-CSRF-Token':token}).status_code,200)
        for path in ('/','/products','/products/19','/products/19/edit','/incoming','/stock-in','/onway','/onway-add','/onway/19','/import','/report','/orders','/orders/archive','/returns','/chat','/products/parser/'):
            self.assertEqual(self.client.get(path).status_code,200,path)
        report=self.client.get('/report/export')
        self.assertEqual(report.status_code,200)
        self.assertEqual(len(load_workbook(BytesIO(report.data)).sheetnames),2)

    def test_worker_uses_same_archive_database_and_parser_preserves_stock(self):
        with self.app.app_context():
            job=ParserJob(status='running',active_slot='kaspi',config_json='{}')
            db.session.add(job)
            db.session.commit()
            apply_observation(job.id,dict(kaspi_id='123456',name='Archive tea',url='https://kaspi.kz/shop/p/tea-123456/',our_price=3000,error=None))
            p=db.session.get(Product,19)
            self.assertEqual((p.stock_qty,p.purchase_price,p.current_price),(12,500,3000))
        env=dict(os.environ,KSM_SECRET_KEY='worker-test',KASPIBOT_DATABASE=str(self.database))
        p=subprocess.run([sys.executable,'-m','kaspibot.parser_worker','-1'],env=env,cwd=self.app.root_path,capture_output=True,timeout=30)
        self.assertEqual(p.returncode,0,p.stderr.decode(errors='replace'))

    def test_backup_restores_all_archive_tables(self):
        target=backup(self.database,Path(self.temp.name)/'backups')
        with closing(sqlite3.connect(target)) as conn:
            self.assertEqual(conn.execute('SELECT alias FROM product_aliases WHERE id=43').fetchone()[0],'tea export')
            self.assertEqual(conn.execute('SELECT count(*) FROM users').fetchone()[0],3)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_local_assistant_is_read_only_and_does_not_call_paid_services(self):
        token=self.login()
        with patch('google.genai.Client',side_effect=AssertionError('External AI must remain off')):
            response=self.client.post('/api/chat',json={'message':'Как изменить цену?'},headers={'X-CSRF-Token':token})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json['mode'],'local_reference')
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT stock_qty FROM products WHERE id=19').fetchone()[0],12)

    def test_finance_arithmetic_forecast_reproducibility(self):
        inputs=json.loads((Path(self.app.root_path)/'docs/course/finance/inputs.json').read_text(encoding='utf-8'))
        results=calculate(inputs)
        for result in results.values():
            self.assertEqual(result['tco_36_kzt'],result['initial_kzt']+36*result['monthly_kzt'])
            self.assertLess(result['roi_cost_plus30'],result['roi'])
            self.assertLess(result['roi_effect_half'],result['roi'])
        self.assertEqual(simulate([0,1,2],7,runs=100),simulate([0,1,2],7,runs=100))
        self.assertEqual(simulate([2],5,runs=100)['quantiles_weeks']['P95'],3)
        with self.assertRaises(ValueError):
            simulate([0,0],5)
