import io
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import requests

from app import create_app
from kaspibot.models import db, Product, SellerCatalogImport
from kaspibot.services.automatic_sync import tick, status
from kaspibot.services.catalog_download import download_xml, validate_url
from kaspibot.services.seller_catalog import import_xml, latest_catalog
from tools.local_catalog_server import make_server
from tools.setup_local_catalog import provision
from test_seller_catalog import xml


class LocalTLS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        provision(cls.folder)
        cls.catalog = cls.folder/'catalog.xml'
        cls.catalog.write_bytes(xml())
        cls.server = make_server('127.0.0.1', 0, cls.catalog, cls.folder/'server.pem', cls.folder/'server-key.pem')
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'https://127.0.0.1:{cls.server.server_port}/catalog.xml'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def test_local_policy_is_explicit_and_exact(self):
        for host in ['localhost', '127.0.0.1', '[::1]']:
            url = f'https://{host}:8443/catalog.xml'
            with self.assertRaisesRegex(ValueError, 'URL:'):
                validate_url(url)
            self.assertTrue(validate_url(url, allow_local=True)[1])
        for url in ['http://localhost:8443/catalog.xml', 'https://192.168.1.10:8443/a',
                    'https://10.0.0.1/a', 'https://169.254.169.254/a', 'https://[fc00::1]/a',
                    'https://127.0.0.2:8443/a', 'https://localhost:443/a', 'https://localhost/a',
                    'https://localhost.localdomain:8443/a', 'https://u:p@localhost:8443/a',
                    'https://localhost:8443/a#fragment', 'https://example.org:8443/a']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url, allow_local=True)

    def test_real_tls_trusted_ca_is_accepted_and_untrusted_is_rejected(self):
        # A random port avoids colliding with a real local server. Port policy is tested above.
        with patch('kaspibot.services.catalog_download.validate_url', return_value=(urlsplit(self.url), True)):
            with self.assertRaisesRegex(ValueError, 'TLS:'):
                download_xml(self.url, allow_local=True)
            self.assertEqual(download_xml(self.url, allow_local=True, ca_bundle=self.folder/'ca.pem'), xml())
        with requests.Session() as session:
            session.trust_env = False
            # The same certificate also covers localhost, independently of the pinned IP.
            response = session.get(self.url.replace('127.0.0.1', 'localhost'), verify=str(self.folder/'ca.pem'), timeout=3)
            self.assertEqual(response.content, xml())

    def test_server_never_exposes_project_or_accepts_writes(self):
        with requests.Session() as session:
            session.trust_env = False
            for path in ['/', '/.env', '/../app.db', '/catalog.xml?secret=x', '/server-key.pem']:
                response = session.get(self.url.replace('/catalog.xml', path), verify=str(self.folder/'ca.pem'), timeout=3)
                self.assertEqual(response.status_code, 404)
            response = session.post(self.url, data='write', verify=str(self.folder/'ca.pem'), timeout=3)
            self.assertEqual(response.status_code, 501)
            self.assertEqual(self.catalog.read_bytes(), xml())

    def test_dns_private_targets_and_rebinding_are_blocked(self):
        for address in ['192.168.1.1', '127.0.0.1', '169.254.169.254']:
            with patch('kaspibot.services.catalog_download.socket.getaddrinfo', return_value=[(socket.AF_INET, 1, 6, '', (address, 443))]), \
                    patch('requests.Session.get') as get, self.assertRaisesRegex(ValueError, 'URL:'):
                download_xml('https://feed.example/catalog.xml')
            get.assert_not_called()
        with patch('kaspibot.services.catalog_download.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('192.168.1.1', 8443))]), \
                self.assertRaises(ValueError):
            download_xml('https://localhost:8443/catalog.xml', allow_local=True)

    def test_ambient_token_proxy_and_verify_overrides_are_ignored(self):
        with patch.dict('os.environ', {'HTTPS_PROXY': 'http://127.0.0.1:1', 'REQUESTS_CA_BUNDLE': 'missing-secret-path'}), \
                patch('kaspibot.services.catalog_download.validate_url', return_value=(urlsplit(self.url), True)):
            self.assertEqual(download_xml(self.url, allow_local=True, ca_bundle=self.folder/'ca.pem'), xml())


class CatalogUpdates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(dict(TESTING=True, SECRET_KEY='test', DATABASE=str(Path(self.temp.name)/'app.db'),
            KASPI_API_TOKEN='fixture', KASPI_CATALOG_URL='https://localhost:8443/catalog.xml',
            KASPI_ALLOW_LOCAL_CATALOG=True, KASPI_CATALOG_CA_BUNDLE=None))
        result = self.app.test_cli_runner().invoke(args=['init-db'])
        self.assertEqual(result.exit_code, 0, result.output)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def test_price_and_model_update_is_visible_without_rewriting_card_or_inventory(self):
        with self.app.app_context():
            import_xml(xml())
            product = Product.query.one()
            product.purchase_price, product.stock_qty, product.reserved_qty, product.onway_qty = 111, 8, 3, 4
            db.session.commit()
            updated = xml(date='2026-11-20 22:07').replace(b'3900', b'4500').replace(b'Same name S', b'Updated model')
            import_xml(updated)
            self.assertEqual(Product.query.count(), 1)
            self.assertEqual(SellerCatalogImport.query.count(), 2)
            self.assertEqual((product.purchase_price, product.stock_qty, product.reserved_qty, product.onway_qty), (111, 8, 3, 4))
            self.assertEqual(latest_catalog()[1][0]['prices'], {'CITY': 4500})
            # A previously imported but now obsolete file is not a successful current check.
            with self.assertRaisesRegex(ValueError, 'старее'):
                import_xml(xml())
        self.app.test_cli_runner().invoke(args=['create-user', '--role', 'admin', '--password', 'test', 'admin'])
        client = self.app.test_client()
        client.post('/management/login', data={'username': 'admin', 'password': 'test'})
        for url in ['/products/seller-catalog', '/management/products', '/management/products/1']:
            response = client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertIn('4500', response.get_data(as_text=True))
            self.assertIn('2026-11-20 22:07', response.get_data(as_text=True))

    def test_exact_case_sensitive_sku_and_transaction_rollback(self):
        with self.app.app_context():
            import_xml(xml(sku='Exact'))
            with self.assertRaisesRegex(ValueError, 'регистром'):
                import_xml(xml(sku='exact', date='2026-11-20 22:07'))
            import_xml(xml(sku='OTHER', date='2026-11-20 22:07'))
            self.assertEqual(Product.query.count(), 2)
            self.assertEqual({p.sku for p in Product.query.all()}, {'Exact', 'OTHER'})
            with patch('kaspibot.services.seller_catalog.db.session.commit', side_effect=RuntimeError('write failed')):
                with self.assertRaises(RuntimeError):
                    import_xml(xml(sku='THIRD', date='2026-11-21 22:07'))
            self.assertEqual(Product.query.count(), 2)
            self.assertEqual(SellerCatalogImport.query.count(), 2)

    def test_concurrent_identical_imports_create_one_snapshot(self):
        barrier = threading.Barrier(2)
        results, errors = [], []
        def importer():
            with self.app.app_context():
                try:
                    barrier.wait(timeout=3)
                    results.append(import_xml(xml()))
                except Exception as error:
                    errors.append(error)
                finally:
                    db.session.remove()
        threads = [threading.Thread(target=importer) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=8)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(r['duplicate'] for r in results), [False, True])
        with self.app.app_context():
            self.assertEqual((Product.query.count(), SellerCatalogImport.query.count()), (1, 1))

    def test_api_failure_does_not_undo_xml_and_next_cycle_recovers(self):
        with self.app.app_context(), patch('kaspibot.services.automatic_sync.download_xml', return_value=xml()) as read, \
                patch('kaspibot.services.automatic_sync.launch', side_effect=ValueError('API unavailable')):
            tick('test')
            read.assert_called_once_with(self.app.config['KASPI_CATALOG_URL'], allow_local=True, ca_bundle=None)
            state = status()
            self.assertEqual((state['catalog_status'], state['orders_status'], state['https_status']), ('imported', 'failed', 'available'))
            self.assertEqual(state['catalog_offers'], 1)
            self.assertIsNotNone(state['catalog_imported_at'])

    def test_manual_upload_still_works_with_csrf(self):
        runner = self.app.test_cli_runner()
        runner.invoke(args=['create-user', '--role', 'admin', '--password', 'test', 'admin'])
        client = self.app.test_client()
        client.post('/management/login', data={'username': 'admin', 'password': 'test'})
        client.get('/products/seller-catalog')
        with client.session_transaction() as session:
            csrf = session['csrf_token']
        response = client.post('/products/seller-catalog', data={'csrf_token': csrf, 'file': (io.BytesIO(xml()), 'fixture.xml')})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get('/api/kaspi/catalog').json['offers'][0]['sku'], 'REAL-1')
