import tempfile
import os
import base64
from types import SimpleNamespace
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import Error as BrowserError, sync_playwright

from app import create_app
from kaspibot.models import db, Product, SellerCatalogImport, KaspiAutoSyncState, KaspiSyncRun
from kaspibot.services.automatic_sync import tick, status
from kaspibot.services.cabinet_export import ExportError, fetch_export, allowed_file_url, download_links, export_from_page, read_request_allowed
from kaspibot.services.catalog_source import refresh_source, atomic_write
from kaspibot.services.seller_catalog import import_xml
from kaspibot.services.seller_sync import utcnow
from test_seller_catalog import xml


def feed(date='2026-11-19 22:07', price=b'3900'):
    return xml(date=date).replace(b'fixture-shop', b'123456').replace(b'3900', price)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.private = Path(self.temp.name)/'private'
        self.private.mkdir()
        self.path = self.private/'catalog.xml'
        self.path.write_bytes(feed())
        self.app = create_app(dict(TESTING=True, SECRET_KEY='fixture-only', DATABASE=str(Path(self.temp.name)/'app.db'),
            KASPI_CATALOG_EXPORT=True, KASPI_CATALOG_MERCHANT_ID='123456', KASPI_CATALOG_PRIVATE_DIR=str(self.private),
            KASPI_CATALOG_URL='https://localhost:8443/catalog.xml', KASPI_ALLOW_LOCAL_CATALOG=True,
            KASPI_CATALOG_CA_BUNDLE=None, KASPI_API_TOKEN='fixture-token'))
        result = self.app.test_cli_runner().invoke(args=['init-db'])
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            import_xml(feed())
            product = Product.query.one()
            product.stock_qty, product.reserved_qty, product.onway_qty, product.purchase_price = 7, 2, 4, 111
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def queue(self, _days):
        row = KaspiSyncRun(active_slot='orders', status='queued')
        db.session.add(row)
        db.session.commit()
        return row.id

    def test_new_cabinet_export_replaces_file_imports_price_and_preserves_inventory(self):
        newer = feed(date='2026-11-20 22:07', price=b'4800')
        with self.app.app_context(), patch('kaspibot.services.catalog_source.fetch_export', return_value=newer) as fetch, \
                patch('kaspibot.services.automatic_sync.download_xml', side_effect=lambda *_a, **_k: self.path.read_bytes()), \
                patch('kaspibot.services.automatic_sync.launch', side_effect=self.queue):
            tick('worker', boot=True)
            self.assertEqual(self.path.read_bytes(), newer)
            self.assertEqual(SellerCatalogImport.query.count(), 2)
            self.assertEqual(Product.query.count(), 1)
            product = Product.query.one()
            self.assertEqual((product.stock_qty, product.reserved_qty, product.onway_qty, product.purchase_price), (7, 2, 4, 111))
            state = status()
            self.assertEqual((state['source']['state'], state['catalog_status'], state['orders_status']), ('exported','imported','queued'))
            fetch.assert_called_once_with(self.private/'kaspi-session.json', '123456')
            self.assertNotIn('fixture-token', str(state))
            # A second worker is excluded by the same lease/interval, including the browser export.
            self.assertFalse(tick('other'))
            self.assertEqual(fetch.call_count, 1)

    def test_expired_session_preserves_file_and_snapshot_and_does_not_block_api(self):
        with self.app.app_context(), patch('kaspibot.services.catalog_source.fetch_export',
                side_effect=ExportError('Сессия истекла.', 'needs_login')), \
                patch('kaspibot.services.automatic_sync.download_xml') as download, \
                patch('kaspibot.services.automatic_sync.launch', side_effect=self.queue):
            tick('worker')
            self.assertEqual(self.path.read_bytes(), feed())
            self.assertEqual(SellerCatalogImport.query.count(), 1)
            state = status()
            self.assertEqual((state['source']['state'],state['catalog_status'],state['orders_status']), ('needs_login','failed','queued'))
            self.assertEqual(state['https_status'], 'not_checked')
            download.assert_not_called()
            row = db.session.get(KaspiAutoSyncState, 1)
            row.next_attempt_at = utcnow()-timedelta(seconds=1)
            db.session.commit()
            with patch('kaspibot.services.catalog_source.fetch_export', return_value=feed()), \
                    patch('kaspibot.services.automatic_sync.download_xml', return_value=feed()):
                tick('worker')
            self.assertEqual(status()['catalog_status'], 'unchanged')
            self.assertEqual(status()['source']['state'], 'exported')

    def test_invalid_older_and_other_merchant_xml_never_overwrite_good_file(self):
        for bad in [b'<html>login</html>', feed(date='2026-11-18 22:07'), feed().replace(b'123456', b'999999')]:
            with self.subTest(bad=bad[:30]), self.app.app_context(), \
                    patch('kaspibot.services.catalog_source.fetch_export', return_value=bad):
                with self.assertRaises(ValueError):
                    refresh_source()
                self.assertEqual(self.path.read_bytes(), feed())
                self.assertEqual(SellerCatalogImport.query.count(), 1)

    def test_failed_import_and_tls_restore_previous_file_and_leave_api_independent(self):
        for failure in ['import','tls']:
            with self.subTest(failure=failure), self.app.app_context(), \
                    patch('kaspibot.services.catalog_source.fetch_export', return_value=feed(date='2026-11-20 22:07')), \
                    patch('kaspibot.services.automatic_sync.launch', side_effect=self.queue):
                row = db.session.get(KaspiAutoSyncState, 1)
                if row:
                    row.next_attempt_at = utcnow()-timedelta(seconds=1)
                    db.session.commit()
                if failure == 'import':
                    with patch('kaspibot.services.automatic_sync.download_xml', side_effect=lambda *_a, **_k: self.path.read_bytes()), \
                            patch('kaspibot.services.automatic_sync.import_xml', side_effect=ValueError('SKU conflict')):
                        tick('worker')
                else:
                    with patch('kaspibot.services.automatic_sync.download_xml', side_effect=ValueError('TLS: failure')):
                        tick('worker')
                self.assertEqual(self.path.read_bytes(), feed())
                self.assertEqual(SellerCatalogImport.query.count(), 1)
                self.assertEqual(status()['orders_status'], 'queued')

    def test_publication_rollback_does_not_overwrite_a_later_operator_file(self):
        with self.app.app_context(), patch('kaspibot.services.catalog_source.fetch_export', return_value=feed(date='2026-11-20 22:07')):
            publication = refresh_source()
            operator_file = feed(date='2026-11-21 22:07')
            atomic_write(self.path, operator_file)
            publication.rollback()
            self.assertEqual(self.path.read_bytes(), operator_file)

    def test_unchanged_export_does_not_rewrite_file_or_create_snapshot(self):
        before = self.path.stat().st_mtime_ns
        with self.app.app_context(), patch('kaspibot.services.catalog_source.fetch_export', return_value=feed()):
            publication = refresh_source()
            result = import_xml(self.path.read_bytes())
            publication.accept()
            self.assertTrue(result['duplicate'])
            self.assertEqual(self.path.stat().st_mtime_ns, before)
            self.assertEqual(SellerCatalogImport.query.count(), 1)


class ExportBoundaryTests(unittest.TestCase):
    def test_read_post_queries_are_allowed_but_mutations_and_settings_writes_are_blocked(self):
        base = 'https://mc.shop.kaspi.kz'
        def request(path, payload=None, body=None):
            return SimpleNamespace(method='POST', url=base+path, post_data_json=payload, post_data=body)
        self.assertTrue(read_request_allowed(request('/mc/facade/graphql',
            {'operationName':'getMerchant', 'query':'query getMerchant { merchant { uid } }'})))
        self.assertFalse(read_request_allowed(request('/mc/facade/graphql',
            {'operationName':'getMerchant', 'query':'mutation getMerchant { changePrice }'})))
        self.assertFalse(read_request_allowed(request('/mc/facade/graphql',
            {'operationName':'updateOffer', 'query':'query updateOffer { uid }'})))
        self.assertTrue(read_request_allowed(request('/pricefeed/upload/merchant/upload/configuration')))
        self.assertFalse(read_request_allowed(request('/pricefeed/upload/merchant/upload/configuration', body='{"enabled":true}')))
        self.assertTrue(read_request_allowed(request('/bff/offer-view/v2/list')))
        self.assertFalse(read_request_allowed(request('/offers/api/v1/offer/update')))

    def test_missing_session_does_not_launch_browser(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('kaspibot.services.cabinet_export.sync_playwright') as runtime:
            with self.assertRaises(ExportError) as error:
                fetch_export(Path(directory)/'missing.json','123456')
            self.assertEqual(error.exception.code,'needs_login')
            runtime.assert_not_called()

    def test_private_file_urls_are_narrow_and_never_follow_external_hosts(self):
        valid = 'https://mc.shop.kaspi.kz/download.xml?signature=fixture-private'
        self.assertTrue(allowed_file_url(valid))
        for value in ['http://mc.shop.kaspi.kz/a','https://u:p@kaspi.kz/a', 'https://kaspi.kz.evil.example/a',
                      'https://localhost:8443/catalog.xml', 'https://kaspi.kz:8443/a',valid+'#x']:
            self.assertFalse(allowed_file_url(value))
        self.assertEqual(download_links({'data':{'downloadUrl':valid,'other':'https://evil.example/x'}}),[valid])

    def test_browser_exceptions_never_expose_tokens_or_private_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)/'session.json'
            state.write_text('{}')
            with patch('kaspibot.services.cabinet_export.sync_playwright', side_effect=BrowserError('fixture-secret https://private')):
                with self.assertRaises(ExportError) as error:
                    fetch_export(state, '123456')
                self.assertNotIn('fixture-secret',str(error.exception))
                self.assertNotIn('https://private',str(error.exception))


@unittest.skipUnless(os.environ.get('KASPI_CATALOG_BROWSER_TEST') == '1', 'Opt-in installed Chrome browser smoke')
class ExportBrowserSmoke(unittest.TestCase):
    def run_export(self, trigger_status):
        content = feed()
        fixture = '''<!doctype html><html><body><p hidden>ID - 123456</p><p>ID - 123456</p>
        <button onclick="document.getElementById('export').hidden=false">Действия с файлами</button>
        <a id="export" hidden href="#" onclick="event.preventDefault();
        fetch('https://mc.shop.kaspi.kz/offers/api/v1/offer/export/trigger').then(() => {
          const link=document.createElement('a');link.download='fixture.xml';
          link.href='data:application/xml;base64,PAYLOAD';link.click();
        });">Скачать прайс-лист в XML</a>
        </body></html>'''.replace('PAYLOAD', base64.b64encode(content).decode())
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(channel='chrome', headless=True)
            try:
                context = browser.new_context(accept_downloads=True)
                def serve(route):
                    if route.request.url.endswith('/offer/export/trigger'):
                        route.fulfill(status=trigger_status, content_type='application/json',
                            headers={'Access-Control-Allow-Origin':'*'}, body='{}')
                    else:
                        route.fulfill(status=200, content_type='text/html; charset=utf-8', body=fixture)
                context.route('**/*', serve)
                return export_from_page(context, context.new_page(), '123456', timeout=15)
            finally:
                browser.close()

    def test_real_chrome_receives_exact_export_bytes_without_any_kaspi_network(self):
        self.assertEqual(self.run_export(200), feed())

    def test_rejected_trigger_does_not_accept_old_download_from_cabinet(self):
        # Reproduce the real cabinet: a failed trigger still downloads an old file.
        for http_status,code in [(429,'rate_limited'),(503,'response_error'),(401,'needs_login')]:
            with self.subTest(status=http_status), self.assertRaises(ExportError) as error:
                self.run_export(http_status)
            self.assertEqual(error.exception.code, code)
