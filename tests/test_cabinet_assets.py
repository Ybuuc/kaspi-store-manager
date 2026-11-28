import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from kaspibot.services.cabinet_assets import CabinetAssets, asset_key

class CabinetAssetTests(unittest.TestCase):
    url = 'https://kaspi.kz/mc/assets/main-AbCd1234.js'

    def request(self, headers=None, body=b'export const fixture=1;'):
        response = SimpleNamespace(status=200, headers=headers or {'content-type': 'application/javascript', 'cache-control': 'public, max-age=3600'}, body=lambda: body)
        request = Mock()
        request.url = self.url
        request.method = 'GET'
        request.response.return_value = response
        return request

    def test_cache_survives_context_restart_and_serves_exact_decoded_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CabinetAssets(directory)
            cache.capture(self.request())
            self.assertEqual(cache.saved, 1)
            next_context = CabinetAssets(directory)
            route = Mock()
            route.request = self.request()
            self.assertTrue(next_context.respond(route))
            route.fulfill.assert_called_once_with(status=200, content_type='application/javascript', body=b'export const fixture=1;')
            next_context.capture(route.request)
            self.assertEqual((next_context.hits, next_context.saved), (1, 0))

    def test_cache_never_handles_document_authentication_xml_api_private_links_or_external_hosts(self):
        for url in ['https://kaspi.kz/mc/', 'https://kaspi.kz/mc/variables.js', 'https://mc.shop.kaspi.kz/offers/api/v1/offer/export/trigger', 'https://kaspi.kz/catalog.xml', 'https://idmc.kaspi.kz/auth', 'https://outside.invalid/mc/assets/main-AbCd1234.js', self.url + '?token=fixture', self.url + '#fragment', self.url.replace('https://', 'http://'), self.url.replace('kaspi.kz', 'u:p@kaspi.kz')]:
            self.assertIsNone(asset_key(url), url)

    def test_private_noncacheable_cookie_and_unknown_content_types_are_never_saved(self):
        for changes in [{'cache-control': 'private, max-age=3600'}, {'cache-control': 'no-store, max-age=3600'}, {'cache-control': 'no-cache, max-age=3600'}, {'cache-control': 'max-age=0'}, {'set-cookie': 'fixture=secret'}, {'vary': 'Cookie'}, {'vary': 'Authorization'}, {'content-type': 'text/html'}]:
            with tempfile.TemporaryDirectory() as directory:
                request = self.request()
                request.response().headers.update(changes)
                cache = CabinetAssets(directory)
                cache.capture(request)
                self.assertEqual(cache.saved, 0, changes)
                self.assertEqual(list(Path(directory).glob('*.asset')), [])

    def test_expiry_integrity_and_size_limits_fall_back_to_network(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CabinetAssets(directory)
            cache.capture(self.request())
            route = Mock()
            route.request = self.request()
            with patch('kaspibot.services.cabinet_assets.time.time', return_value=10 ** 12):
                self.assertFalse(cache.respond(route))
            path = next(Path(directory).glob('*.asset'))
            metadata, body = path.read_bytes().split(b'\n', 1)
            path.write_bytes(metadata + b'\n' + body + b'corrupt')
            self.assertFalse(cache.respond(route))
            with patch('kaspibot.services.cabinet_assets.MAX_ENTRY', 4):
                cache.capture(self.request(body=b'too-long'))
                self.assertEqual(cache.saved, 1)
            self.assertIn('sha256', json.loads(metadata))


class CabinetCacheChromeTests(unittest.TestCase):
    @unittest.skipUnless(__import__('os').environ.get('KASPI_CATALOG_BROWSER_TEST')=='1','Opt-in installed Chrome cache smoke')
    def test_public_asset_is_reused_in_a_second_real_chrome_context_without_kaspi_network(self):
        from playwright.sync_api import sync_playwright
        url='https://kaspi.kz/mc/assets/fixture-AbCd1234.js'
        fetched=[]
        with tempfile.TemporaryDirectory() as directory,sync_playwright() as runtime:
            browser=runtime.chromium.launch(channel='chrome',headless=True)
            try:
                for attempt in range(2):
                    cache=CabinetAssets(directory)
                    context=browser.new_context(service_workers='block')
                    def serve(route):
                        if cache.respond(route):
                            return
                        if route.request.url==url:
                            fetched.append(url)
                            route.fulfill(status=200,content_type='application/javascript',headers={'Cache-Control':'public, max-age=3600'},body='window.fixtureAsset=42;')
                        else:
                            route.fulfill(status=200,content_type='text/html',body='<script src="'+url+'"></script>')
                    context.route('**/*',serve)
                    context.on('requestfinished',cache.capture)
                    page=context.new_page()
                    page.goto('https://kaspi.kz/mc/')
                    page.wait_for_function('window.fixtureAsset===42')
                    self.assertEqual(cache.hits,attempt)
                    self.assertEqual(cache.saved,1-attempt)
                    context.unroute_all(behavior='wait')
                    context.remove_listener('requestfinished',cache.capture)
                    context.close()
            finally:
                browser.close()
        self.assertEqual(fetched,[url])
