import json
import tempfile
import unittest
from pathlib import Path
from app import create_app
from kaspibot.models import db, Product, KaspiOrderSync
from kaspibot.services.seller_catalog import import_xml

class WorkspaceRedesignTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(dict(TESTING=True, SECRET_KEY='workspace-fixture', DATABASE=str(Path(self.temp.name) / 'app.db')))
        runner = self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=['init-db']).exit_code, 0)
        for role in ['admin', 'employee']:
            self.assertEqual(runner.invoke(args=['create-user', '--role', role, '--password', 'fixture-only', role]).exit_code, 0)
        feed = '<kaspi_catalog date="2026-11-28 18:00"><merchantid>123456</merchantid><offers>\n        <offer sku="CAT-1"><model>Модель первая</model><brand>Fixture</brand><availabilities><availability storeId="PP1" available="yes" stockCount="8" preOrder="2"/><availability storeId="PP2" available="yes" stockCount="1"/></availabilities><cityprices><cityprice cityId="CITY-A">9000</cityprice><cityprice cityId="CITY-B">9500</cityprice></cityprices></offer>\n        <offer sku="CAT-2"><model>Модель вторая</model><brand>Fixture</brand><availabilities><availability storeId="PP1" available="yes" stockCount="3"/></availabilities><price>3000</price></offer></offers></kaspi_catalog>'
        with self.app.app_context():
            import_xml(feed.encode())
            product = Product.query.filter_by(sku='CAT-1').one()
            product.inventory_source = 'manual'
            product.stock_qty = 7
            product.reserved_qty = 2
            product.purchase_price = 700
            product.low_stock_threshold = 9
            for index in range(59):
                db.session.add(Product(name=f'Товар {index:03d}', sku=f'PAGE-{index:03d}', inventory_source='unknown'))
            db.session.add(KaspiOrderSync(external_key='archive-1', order_code='OLD-1', product_id=product.id, title='Архивный товар', is_sale_created=True, raw_json='{}'))
            for index in range(2):
                db.session.add(KaspiOrderSync(external_key=f'conflict-{index}', order_code='CONFLICT-1', product_id=product.id, title='Конфликтный товар', raw_json=json.dumps({'source': 'kaspi_api', 'mapping_status': 'conflict', 'merchant_product': {'attributes': {'code': 'API-DIFFERENT-SKU'}}})))
            db.session.commit()
        self.client = self.app.test_client()
        self.client.post('/management/login', data={'username': 'admin', 'password': 'fixture-only'})

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def test_catalog_is_bounded_and_page_validation_never_loses_filters(self):
        first = self.client.get('/products').get_data(as_text=True)
        self.assertEqual(first.count('<article class="algatop-card"'), 50)
        second = self.client.get('/products?page=999').get_data(as_text=True)
        self.assertEqual(second.count('<article class="algatop-card"'), 11)
        self.assertIn('PAGE-058', second)
        for page in ['invalid', '-5', '0']:
            text = self.client.get('/products?page=' + page).get_data(as_text=True)
            self.assertEqual(text.count('<article class="algatop-card"'), 50)
        filtered = self.client.get('/products?q=PAGE-&view=table&page=1').get_data(as_text=True)
        self.assertIn('q=PAGE-', filtered)
        self.assertIn('view=table', filtered)
        self.assertEqual(filtered.count('<tr data-product-id='), 50)

    def test_table_preference_persists_and_unknown_inventory_is_not_a_fabricated_zero(self):
        self.client.get('/products?view=table')
        self.assertEqual(self.client.get_cookie('ksm_catalog_view').value, 'table')
        text = self.client.get('/products?q=CAT-2').get_data(as_text=True)
        self.assertIn('id="catalogTable"', text)
        self.assertIn('Нет подтверждения', text)
        self.assertIn('3000 ₸', text)
        self.client.get('/products?view=cards')
        self.assertIn('class="algatop-card"', self.client.get('/products?q=CAT-2').get_data(as_text=True))

    def test_warehouse_and_preorder_are_correlated_and_city_selects_actual_price(self):
        none = self.client.get('/products?store=PP2&preorder=yes').get_data(as_text=True)
        self.assertNotIn('data-product-id=', none)
        first = self.client.get('/products?store=PP1&preorder=yes&city=CITY-B&view=table').get_data(as_text=True)
        self.assertIn('Модель первая', first)
        self.assertNotIn('Модель вторая', first)
        self.assertIn('9500 ₸', first)
        self.assertNotIn('9000 ₸', first)
        self.assertNotIn('PP2 ·', first)

    def test_inventory_filter_and_reconciliation_show_evidence_without_writing_stock(self):
        before = None
        with self.app.app_context():
            row = Product.query.filter_by(sku='CAT-1').one()
            before = (row.stock_qty, row.reserved_qty, row.purchase_price)
        low = self.client.get('/products?stock=low').get_data(as_text=True)
        self.assertIn('Модель первая', low)
        self.assertNotIn('Модель вторая', low)
        attention = self.client.get('/attention?kind=conflicts').get_data(as_text=True)
        self.assertEqual(attention.count('<article class="attention-card"'), 1)
        self.assertIn('API-DIFFERENT-SKU', attention)
        self.assertIn('CAT-1', attention)
        archive = self.client.get('/attention?kind=archive').get_data(as_text=True)
        self.assertIn('История склада защищена', archive)
        self.assertIn('OLD-1', archive)
        with self.app.app_context():
            row = Product.query.filter_by(sku='CAT-1').one()
            self.assertEqual((row.stock_qty, row.reserved_qty, row.purchase_price), before)

    def test_employee_can_filter_catalog_but_cannot_read_order_reconciliation(self):
        other = self.app.test_client()
        other.post('/management/login', data={'username': 'employee', 'password': 'fixture-only'})
        self.assertEqual(other.get('/products?view=table&preorder=yes').status_code, 200)
        self.assertEqual(other.get('/attention').status_code, 403)

    def test_order_search_is_bounded_and_mobile_contacts_are_collapsed(self):
        text = self.client.get('/orders?q=CONFLICT-1').get_data(as_text=True)
        self.assertIn('order-mobile-card', text)
        self.assertIn('Контакты и данные API', text)
        self.assertIn('CONFLICT-1', text)
        self.assertNotIn('OLD-1', text)
