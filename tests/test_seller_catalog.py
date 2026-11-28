import json
import tempfile
import unittest
from pathlib import Path

from app import create_app
from kaspibot.models import db, Product, KaspiOrderSync, SellerCatalogImport, SellerCatalogOffer
from kaspibot.services.seller_catalog import parse_xml, import_xml, latest_catalog
from kaspibot.services.order_payload import resolve_product


def xml(sku='REAL-1', qty='49.0', preorder='16', date='2026-11-19 22:07'):
    return f'''<kaspi_catalog xmlns="kaspiShopping" date="{date}"><merchantid>fixture-shop</merchantid><offers>
    <offer sku="{sku}"><model>Same name S</model><brand>Fixture</brand><availabilities>
    <availability available="yes" storeId="PP1" preOrder="{preorder}" stockCount="{qty}"/>
    </availabilities><cityprices><cityprice cityId="CITY">3900</cityprice></cityprices></offer>
    </offers></kaspi_catalog>'''.encode()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(dict(TESTING=True, SECRET_KEY='test-only', DATABASE=str(Path(self.temp.name)/'app.db')))
        result = self.app.test_cli_runner().invoke(args=['init-db'])
        self.assertEqual(result.exit_code, 0, result.output)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def test_namespace_decimal_integer_and_preorder_kept_separate(self):
        p = parse_xml(xml())
        self.assertEqual(p['offers'][0]['stocks'][0]['quantity'], 49)
        self.assertEqual(p['offers'][0]['stocks'][0]['preorder_days'], 16)
        self.assertEqual(p['offers'][0]['prices'], {'CITY': 3900})

    def test_invalid_or_unknown_counts_never_coerced_to_zero(self):
        for qty in ['-1', '0.5', 'NaN', 'Infinity', 'yes', '']:
            with self.assertRaises(ValueError):
                parse_xml(xml(qty=qty))
        data = xml().replace(b' stockCount="49.0"', b'')
        self.assertIsNone(parse_xml(data)['offers'][0]['stocks'][0]['quantity'])
        for data in [b'<!DOCTYPE x [<!ENTITY a "x">]><x/>', xml(preorder='31'), xml(date=''), b'<broken', b'<kaspi_catalog/>']:
            with self.assertRaises(ValueError):
                parse_xml(data)

    def test_snapshot_does_not_rewrite_legacy_stock_price_or_identity(self):
        with self.app.app_context():
            old = Product(name='Same name S', sku='OLD', stock_qty=7, onway_qty=9,
                          reserved_qty=2, purchase_price=500, current_price=3000)
            db.session.add(old)
            db.session.commit()
            result = import_xml(xml())
            self.assertEqual(result['created_products'], 1)
            new = Product.query.filter_by(sku='REAL-1').one()
            self.assertEqual(new.name, 'Same name S [SKU REAL-1]')
            self.assertEqual(new.inventory_source, 'unknown')
            self.assertEqual((old.stock_qty, old.onway_qty, old.reserved_qty, old.purchase_price, old.current_price),
                             (7, 9, 2, 500, 3000))
            self.assertEqual(SellerCatalogOffer.query.one().product_id, new.id)
            repeat = import_xml(xml())
            self.assertTrue(repeat['duplicate'])
            self.assertEqual((Product.query.count(), SellerCatalogImport.query.count()), (2, 1))

    def test_exact_sku_resolves_conflicts_except_rows_with_historical_movements(self):
        with self.app.app_context():
            for i, has_movement in enumerate([False, True]):
                db.session.add(KaspiOrderSync(external_key=f'KEY-{i}', order_code=f'CODE-{i}', entry_id=str(i),
                    is_sale_created=has_movement, raw_json=json.dumps(dict(source='kaspi_api', mapping_status='conflict',
                    merchant_product={'attributes': {'code': 'REAL-1'}}, entry={'attributes': {'quantity': 1}}))))
            db.session.commit()
            result = import_xml(xml())
            self.assertEqual(result['resolved'], 1)
            rows = KaspiOrderSync.query.order_by(KaspiOrderSync.id).all()
            self.assertEqual(rows[0].product_id, Product.query.one().id)
            self.assertEqual(json.loads(rows[0].raw_json)['mapping_status'], 'resolved_by_seller_xml')
            self.assertIsNone(rows[1].product_id)

    def test_duplicate_sku_or_older_snapshot_rolls_back_whole_import(self):
        with self.app.app_context():
            data = xml()
            offer = data[data.index(b'<offer sku'):data.index(b'</offer>')+len(b'</offer>')]
            with self.assertRaises(ValueError):
                import_xml(data.replace(b'</offers>', offer+b'</offers>'))
            self.assertEqual(Product.query.count(), 0)
            import_xml(xml())
            with self.assertRaises(ValueError):
                import_xml(xml(sku='OTHER', date='2026-11-18 20:00'))
            self.assertEqual(SellerCatalogImport.query.count(), 1)

    def test_active_snapshot_is_not_full_catalogue_and_does_not_archive_missing_sku(self):
        with self.app.app_context():
            import_xml(xml())
            import_xml(xml(sku='OTHER', date='2026-11-20 22:07'))
            self.assertEqual(len(latest_catalog()[1]), 1)
            self.assertEqual(Product.query.count(), 2)
            self.assertTrue(Product.query.filter_by(sku='REAL-1').one().is_active)

    def test_exact_xml_sku_does_not_steal_public_id_claimed_by_legacy_card(self):
        with self.app.app_context():
            db.session.add(Product(name='Old', sku='OLD', kaspi_product_id='PUBLIC-CODE'))
            db.session.commit()
            import_xml(xml())
            product = resolve_product(dict(merchant_sku='REAL-1', kaspi_code='PUBLIC-CODE', title='Same name S', raw_json={}, category=None))
            db.session.commit()
            self.assertEqual(product.sku, 'REAL-1')
            self.assertIsNone(product.kaspi_product_id)
            self.assertEqual(Product.query.filter_by(sku='OLD').one().kaspi_product_id, 'PUBLIC-CODE')

    def test_routes_require_admin_and_csrf_and_escape_catalogue_content(self):
        runner = self.app.test_cli_runner()
        for role in ['admin', 'employee']:
            result = runner.invoke(args=['create-user', '--role', role, '--password', 'test-only', role])
            self.assertEqual(result.exit_code, 0)
        client = self.app.test_client()
        self.assertEqual(client.get('/products/seller-catalog').status_code, 302)
        client.post('/management/login', data={'username': 'employee', 'password': 'test-only'})
        self.assertEqual(client.get('/api/kaspi/catalog').status_code, 403)
        client = self.app.test_client()
        client.post('/management/login', data={'username': 'admin', 'password': 'test-only'})
        self.assertEqual(client.post('/products/seller-catalog').status_code, 400)
        with self.app.app_context():
            import_xml(xml().replace(b'Same name S', b'&lt;script&gt;alert(1)&lt;/script&gt;'))
        page = client.get('/products/seller-catalog').get_data(as_text=True)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('2026-11-19 22:07', page)
        self.assertEqual(client.get('/api/kaspi/catalog').json['offers'][0]['stocks'][0]['preorder_days'], 16)
