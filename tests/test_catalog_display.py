import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from kaspibot.models import db, Product
from kaspibot.services.seller_catalog import import_xml
from kaspibot.services.cabinet_export import fetch_export, ExportError
from test_seller_catalog import xml


class CatalogDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(dict(TESTING=True,SECRET_KEY='display-test',DATABASE=str(Path(self.temp.name)/'app.db')))
        runner=self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=['init-db']).exit_code,0)
        for role in ['admin','employee']:
            result=runner.invoke(args=['create-user','--role',role,'--password','fixture-password',role])
            self.assertEqual(result.exit_code,0,result.output)
        with self.app.app_context():
            import_xml(xml(sku='UI-1',qty='1',preorder='2',date='2026-11-27 17:00'))
        self.client=self.app.test_client()
        self.client.post('/management/login',data={'username':'admin','password':'fixture-password'})

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def test_list_and_detail_show_declared_quantity_without_claiming_physical_stock(self):
        for path in ['/products','/products/1']:
            page=self.client.get(path)
            self.assertEqual(page.status_code,200,path)
            text=page.get_data(as_text=True)
            for expected in ['3900 ₸','1 шт.','2 дн.','2026-11-27 17:00','Физический остаток не подтверждён']:
                self.assertIn(expected,text)
            self.assertNotIn('нет данных API',text)
        with self.app.app_context():
            row=Product.query.one()
            self.assertEqual((row.stock_qty,row.reserved_qty,row.onway_qty,row.purchase_price),(0,0,0,0))
            self.assertEqual(row.inventory_source,'unknown')

    def test_new_zero_quantity_is_visible_and_version_advances_without_mutating_physical_stock(self):
        before=self.client.get('/api/products/catalog-version').json
        with self.app.app_context():
            content=xml(sku='UI-1',qty='0',preorder='0',date='2026-11-27 17:05').replace(b'available="yes"',b'available="no"').replace(b'3900',b'4500')
            import_xml(content)
        response=self.client.get('/api/products/catalog-version')
        self.assertGreater(response.json['revision'],before['revision'])
        self.assertIn('no-store',response.headers['Cache-Control'])
        page=self.client.get('/products').get_data(as_text=True)
        for value in ['4500 ₸','0 шт.','Без предзаказа','Недоступен']:
            self.assertIn(value,page)

    def test_lightweight_revision_has_inventory_permissions_and_does_not_open_admin_xml_api(self):
        anonymous=self.app.test_client()
        self.assertEqual(anonymous.get('/api/products/catalog-version').status_code,302)
        employee=self.app.test_client()
        employee.post('/management/login',data={'username':'employee','password':'fixture-password'})
        response=employee.get('/api/products/catalog-version')
        self.assertEqual(response.status_code,200)
        self.assertEqual(set(response.json),{'revision','source_date'})
        self.assertEqual(employee.get('/api/kaspi/catalog').status_code,403)
        page=employee.get('/products').get_data(as_text=True)
        self.assertIn('id="menuToggle"',page)
        self.assertNotIn('Сотрудники</a>',page)


class SourceRetryTests(unittest.TestCase):
    def test_transient_export_timeout_recovers_in_same_cycle(self):
        data=xml()
        with patch('kaspibot.services.cabinet_export._fetch_export_once',side_effect=[ExportError('timeout','timeout'),data]) as run:
            self.assertEqual(fetch_export('fixture-session','123456'),data)
            self.assertEqual(run.call_count,2)

    def test_login_and_wrong_shop_failures_are_not_retried(self):
        for code in ['needs_login','wrong_merchant','stale','failed','rate_limited','response_error']:
            with self.subTest(code=code),patch('kaspibot.services.cabinet_export._fetch_export_once',side_effect=ExportError('safe',code)) as run:
                with self.assertRaises(ExportError):
                    fetch_export('fixture-session','123456')
                self.assertEqual(run.call_count,1)

    def test_retry_is_not_started_when_cycle_budget_is_almost_used(self):
        with patch('kaspibot.services.cabinet_export.time.monotonic',side_effect=[0,0,85]), \
                patch('kaspibot.services.cabinet_export._fetch_export_once',side_effect=ExportError('timeout','timeout')) as run:
            with self.assertRaises(ExportError):
                fetch_export('fixture-session','123456',timeout=90)
            self.assertEqual(run.call_count,1)
