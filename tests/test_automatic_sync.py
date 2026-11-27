import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch, Mock

import requests
from app import create_app
from kaspibot.models import db, KaspiSyncRun, KaspiAutoSyncState, SellerCatalogImport
from kaspibot.services.automatic_sync import tick, start, status
from kaspibot.services.catalog_download import download_xml
from kaspibot.services.seller_sync import utcnow


FEED = b'''<kaspi_catalog date="2026-11-19 22:07"><merchantid>fixture-shop</merchantid><offers>
<offer sku="TEST-1"><model>Fixture</model><brand>Fixture</brand><availabilities>
<availability available="yes" storeId="PP1" stockCount="49.0" preOrder="16"/>
</availabilities><price>3900</price></offer></offers></kaspi_catalog>'''


class AutomaticSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(dict(TESTING=True, SECRET_KEY='test-only',
            DATABASE=str(Path(self.temp.name)/'app.db'), KASPI_API_TOKEN='fixture-token',
            KASPI_CATALOG_URL='https://feed.example/catalog.xml?access=fixture-secret'))
        result = self.app.test_cli_runner().invoke(args=['init-db'])
        self.assertEqual(result.exit_code, 0, result.output)

    def tearDown(self):
        handle = self.app.extensions.get('kaspi_auto_sync')
        if handle:
            handle['stop'].set()
            handle['thread'].join(timeout=5)
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def queue(self, days):
        row = KaspiSyncRun(active_slot='orders', status='queued')
        db.session.add(row)
        db.session.commit()
        return row.id

    def make_due(self):
        row = db.session.get(KaspiAutoSyncState, 1)
        row.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.session.commit()

    def test_initial_tick_periodic_duplicate_feed_and_no_overlapping_orders(self):
        with self.app.app_context(), patch('kaspibot.services.automatic_sync.download_xml', return_value=FEED) as read, \
                patch('kaspibot.services.automatic_sync.launch', side_effect=self.queue) as launch:
            self.assertTrue(tick('first-worker', boot=True))
            self.assertEqual(SellerCatalogImport.query.count(), 1)
            self.assertFalse(tick('second-worker', boot=True))
            self.assertEqual(read.call_count, 1)
            self.make_due()
            self.assertTrue(tick('first-worker'))
            self.assertEqual(read.call_count, 2)
            self.assertEqual(launch.call_count, 1)
            self.assertEqual(SellerCatalogImport.query.count(), 1)
            self.assertEqual(status()['catalog_status'], 'unchanged')
            self.assertEqual(status()['catalog_source_date'], '2026-11-19 22:07')

    def test_failure_of_catalogue_does_not_prevent_api_worker_and_never_exposes_url(self):
        with self.app.app_context(), patch('kaspibot.services.automatic_sync.download_xml', side_effect=ValueError('Safe download failure')), \
                patch('kaspibot.services.automatic_sync.launch', side_effect=self.queue):
            tick('owner')
            s = status()
            self.assertEqual((s['catalog_status'], s['orders_status']), ('failed', 'queued'))
            self.assertNotIn('fixture-secret', str(s))
            self.assertNotIn('fixture-token', str(s))
            self.assertIsNone(db.session.get(KaspiAutoSyncState, 1).lease_until)

    def test_catalogue_can_update_without_api_token_and_api_can_update_without_url(self):
        with self.app.app_context(), patch('kaspibot.services.automatic_sync.download_xml', return_value=FEED), \
                patch('kaspibot.services.automatic_sync.launch', side_effect=self.queue) as launch:
            self.app.config['KASPI_API_TOKEN'] = ''
            tick('owner')
            self.assertEqual(status()['catalog_status'], 'imported')
            self.assertEqual(status()['orders_status'], 'not_configured')
            launch.assert_not_called()
            self.app.config.update(KASPI_API_TOKEN='fixture-token', KASPI_CATALOG_URL='')
            self.make_due()
            tick('owner')
            self.assertEqual(status()['catalog_status'], 'not_configured')
            self.assertEqual(status()['orders_status'], 'queued')

    def test_expiring_lease_recovers_after_crash_and_blocks_other_workers(self):
        with self.app.app_context(), patch('kaspibot.services.automatic_sync.download_xml', return_value=FEED), \
                patch('kaspibot.services.automatic_sync.launch', side_effect=self.queue) as launch:
            db.session.add(KaspiAutoSyncState(id=1, lease_owner='dead-worker', lease_until=utcnow()+timedelta(minutes=10)))
            db.session.commit()
            self.assertFalse(tick('new-worker', boot=True))
            launch.assert_not_called()
            db.session.get(KaspiAutoSyncState, 1).lease_until = utcnow()-timedelta(seconds=1)
            db.session.commit()
            self.assertTrue(tick('new-worker', boot=True))
            self.assertEqual(launch.call_count, 1)

    def test_invalid_feed_preserves_last_snapshot_and_finished_worker_status_is_live(self):
        with self.app.app_context(), patch('kaspibot.services.automatic_sync.download_xml', return_value=FEED), \
                patch('kaspibot.services.automatic_sync.launch', side_effect=self.queue):
            tick('owner')
            row = KaspiSyncRun.query.one()
            row.status = 'partial'
            row.active_slot = None
            db.session.commit()
            self.assertEqual(status()['orders_status'], 'partial')
            self.make_due()
            with patch('kaspibot.services.automatic_sync.download_xml', return_value=b'<html>Login</html>'):
                tick('owner')
            self.assertEqual(SellerCatalogImport.query.count(), 1)
            self.assertEqual(status()['catalog_status'], 'failed')

    def test_disabled_testing_or_factory_use_does_not_start_network_workers(self):
        with patch('kaspibot.services.automatic_sync.tick') as work:
            self.assertIsNone(start(self.app))
            self.app.testing = False
            self.app.config['KASPI_AUTO_SYNC'] = False
            self.assertIsNone(start(self.app))
            work.assert_not_called()

    def test_web_start_runs_immediately_once_and_can_stop(self):
        happened = threading.Event()
        self.app.testing = False
        with patch('kaspibot.services.automatic_sync.tick', side_effect=lambda *a, **k: happened.set()) as work:
            handle = start(self.app)
            self.assertTrue(happened.wait(timeout=2))
            self.assertIs(handle, start(self.app))
            handle['stop'].set()
            handle['thread'].join(timeout=2)
            self.assertFalse(handle['thread'].is_alive())
            self.assertEqual(work.call_count, 1)

    @patch('kaspibot.services.catalog_download.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    def test_download_rejects_redirects_large_responses_and_never_sends_kaspi_token(self, _dns):
        response = Mock(status_code=200, headers={})
        response.iter_content.return_value = [FEED]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        url = 'https://feed.example/prices.xml?secret=fixture-secret'
        with patch('kaspibot.services.catalog_download.requests.Session.get', return_value=response) as get:
            self.assertEqual(download_xml(url), FEED)
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
            self.assertNotIn('X-Auth-Token', get.call_args.kwargs['headers'])
            for code in [301, 403, 429, 500]:
                response.status_code = code
                with self.assertRaises(ValueError) as error:
                    download_xml(url)
                self.assertNotIn('fixture-secret', str(error.exception))
            response.status_code = 200
            response.headers = {'Content-Length': str(17*1024*1024)}
            with self.assertRaises(ValueError):
                download_xml(url)
        with patch('kaspibot.services.catalog_download.requests.Session.get', side_effect=requests.Timeout('secret-url')):
            with self.assertRaises(ValueError) as error:
                download_xml(url)
            self.assertNotIn('secret-url', str(error.exception))
        for url in ['http://feed.example/a.xml', 'https://u:p@feed.example/a.xml', 'https://127.0.0.1/a.xml', 'https://localhost/a.xml']:
            with self.assertRaises(ValueError):
                download_xml(url)
