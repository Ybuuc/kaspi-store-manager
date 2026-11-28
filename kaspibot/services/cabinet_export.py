"""Read the seller's own XML via the observed cabinet export UI, in a private session."""
import json
import re
import tempfile
import time
import random
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, Error as BrowserError, TimeoutError as BrowserTimeout

LIMIT = 16 * 1024 * 1024
CABINET = 'https://kaspi.kz/mc/#/products'
EXPORT_PATH = '/offers/api/v1/offer/export/'


class ExportError(ValueError):
    def __init__(self, message, code='failed', retry_after=None):
        super().__init__('Источник XML: ' + message)
        self.code = code
        self.retry_after = retry_after


def allowed_file_url(value):
    try:
        url = urlsplit(value)
        return (url.scheme == 'https' and url.hostname in ('kaspi.kz', 'mc.shop.kaspi.kz')
                and url.port in (None, 443) and url.username is None and url.password is None
                and not url.fragment and not any(ord(c) < 33 for c in value))
    except (ValueError, TypeError, AttributeError):
        return False


def download_links(value):
    """Use only actual file links returned by the observed export operation."""
    result = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('url', 'downloadUrl', 'download_url', 'fileUrl') and isinstance(item, str) and allowed_file_url(item):
                result.append(item)
            elif isinstance(item, (dict, list)):
                result.extend(download_links(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(download_links(item))
    return result


def read_request_allowed(request):
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return True
    url = urlsplit(request.url)
    if request.method != 'POST' or url.scheme != 'https' or url.hostname != 'mc.shop.kaspi.kz':
        return False
    if url.path == EXPORT_PATH + 'trigger':
        return True  # Creates only a downloadable export, never an offer update.
    if url.path == '/mc/facade/graphql':
        try:
            payload = request.post_data_json
            query = payload.get('query', '')
            return (payload.get('operationName') in ('getMerchant', 'getOrderCounters')
                    and isinstance(query, str) and re.match(r'^\s*query\b', query) is not None
                    and re.search(r'\b(mutation|subscription)\b', query) is None)
        except (ValueError, AttributeError, TypeError):
            return False
    if url.path == '/pricefeed/upload/merchant/upload/configuration':
        # Observed cabinet read: POST with no body. Never allow configuration values.
        return request.post_data in (None, '', 'null')
    return url.path in {
        '/bff/offer-view/v2/list',
        '/offers/api/v2/offer/filters/categories', '/offers/api/v2/offer/filters/brands',
        '/offers/api/v2/offer/filters/card', '/offers/api/v2/offer/filters/stock',
        '/offers/api/v2/offer/filters/discounts',
    }


def is_xml(content):
    return isinstance(content, bytes) and b'<kaspi_catalog' in content[:4096].replace(b'\x00', b'')


def export_from_page(context, page, merchant_id, timeout=90):
    """Click only the documented export control; never edit or upload seller data."""
    from kaspibot.services.seller_catalog import parse_xml
    from flask import has_app_context
    if has_app_context():
        from kaspibot.services.sync_schedule import check
        check('xml')
    deadline = time.monotonic() + timeout
    downloads, bodies, links, observations = [], [], [], []

    def observe(response):
        url = urlsplit(response.url)
        if url.hostname != 'mc.shop.kaspi.kz' or not url.path.startswith(EXPORT_PATH):
            return
        observations.append({'operation': url.path.rsplit('/', 1)[-1], 'status': response.status})
        try:
            length = int(response.headers.get('content-length', '0'))
            if response.status != 200 or length > LIMIT:
                return
            data = response.body()
            if len(data) > LIMIT:
                return
            if is_xml(data):
                bodies.append(data)
            else:
                payload = json.loads(data)
                observations[-1]['fields'] = list(payload) if isinstance(payload, dict) else type(payload).__name__
                links.extend(download_links(payload))
        except BrowserError as error:
            if not (page.is_closed() and is_closed_error(error)):
                raise
        except (ValueError,TypeError):
            pass

    page.on('response', observe)
    def on_download(download):
        downloads.append(download)
    page.on('download', on_download)
    try:
        page.goto(CABINET, wait_until='domcontentloaded', timeout=min(timeout*1000, 30000))
        action = page.get_by_role('button', name='Действия с файлами')
        try:
            action.wait_for(state='visible', timeout=20000)
        except BrowserTimeout:
            if 'login' in page.url or 'idmc.' in page.url:
                raise ExportError('сессия кабинета истекла; выполните локальный вход заново.', 'needs_login') from None
            raise ExportError('страница экспорта не загрузилась вовремя; проверка будет повторена.', 'timeout') from None
        # Match the account displayed by the cabinet, not a guessed merchant endpoint.
        headers = page.get_by_text('ID - ' + merchant_id, exact=True).all()
        if not any(header.is_visible() for header in headers):
            raise ExportError('вход выполнен в другой магазин; экспорт не применяется.', 'wrong_merchant')
        action.click()
        # The cabinet can download its previous file even when the new export is
        # rejected (for example HTTP 429). Require this click's successful trigger
        # before accepting any downloaded bytes as a newly requested export.
        downloads.clear()
        bodies.clear()
        links.clear()
        observations.clear()
        def is_trigger(response):
            url = urlsplit(response.url)
            return url.hostname == 'mc.shop.kaspi.kz' and url.path == EXPORT_PATH + 'trigger'
        remaining_ms = max(1, min(15000, int((deadline-time.monotonic())*1000)))
        with page.expect_response(is_trigger, timeout=remaining_ms) as triggered:
            page.get_by_text('Скачать прайс-лист в XML', exact=True).click()
        trigger_status = triggered.value.status
        if trigger_status == 429:
            raise ExportError('Kaspi ограничил частоту экспорта (HTTP 429). Сохранён предыдущий XML; следующая попытка по расписанию.', 'rate_limited',triggered.value.headers.get('retry-after'))
        if trigger_status in (401, 403):
            raise ExportError('кабинет отклонил сессию экспорта; выполните локальный вход.', 'needs_login')
        if not 200 <= trigger_status < 300:
            raise ExportError('Kaspi не подтвердил создание новой выгрузки; предыдущий XML сохранён.', 'response_error')
        attempted = set()
        while time.monotonic() < deadline:
            if downloads:
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory)/'export.xml'
                    downloads[0].save_as(path)
                    if path.stat().st_size > LIMIT:
                        raise ExportError('выгрузка больше 16 МиБ; прежний файл сохранён.')
                    bodies.append(path.read_bytes())
                break
            if bodies:
                break
            for url in list(links):
                if url in attempted:
                    continue
                attempted.add(url)
                response = context.request.get(url, max_redirects=0, timeout=15000)
                if response.status == 200:
                    data = response.body()
                    if len(data) <= LIMIT and is_xml(data):
                        bodies.append(data)
                        break
            if any(row['status'] in (401, 403) for row in observations):
                raise ExportError('кабинет отклонил сессию экспорта; выполните локальный вход.', 'needs_login')
            page.wait_for_timeout(250)
        if not bodies:
            error = ExportError('Kaspi не вернул XML за отведённое время; прежний файл сохранён.','timeout')
            error.observations = observations
            raise error
        content = bodies[0]
        payload = parse_xml(content)
        if payload['merchant_id'] != merchant_id:
            raise ExportError('XML принадлежит другому магазину; прежний файл сохранён.', 'wrong_merchant')
        return content
    finally:
        page.remove_listener('response', observe)
        page.remove_listener('download', on_download)


def is_closed_error(error):
    return 'Target page, context or browser has been closed' in str(error) or 'Browser has been closed' in str(error)


def _fetch_export_once(session_path, merchant_id, timeout=90):
    if not Path(session_path).is_file():
        raise ExportError('нужен вход в локальном окне: запустите tools/kaspi_catalog_login.py.', 'needs_login')
    if not merchant_id or not merchant_id.isascii() or not merchant_id.isdigit():
        raise ExportError('настройте KASPI_CATALOG_MERCHANT_ID.')
    try:
        with sync_playwright() as browser_runtime:
            from flask import current_app,has_app_context
            channel=current_app.config['KASPI_CATALOG_BROWSER_CHANNEL'] if has_app_context() else ('chrome' if os.name=='nt' else 'chromium')
            browser = browser_runtime.chromium.launch(channel=None if channel=='chromium' else channel, headless=True)
            context = None
            closing = False
            try:
                context = browser.new_context(storage_state=str(session_path), accept_downloads=True,
                                              viewport={'width': 1366, 'height': 900}, ignore_https_errors=False)
                context.set_default_timeout(10000)
                # Some observed cabinet READ operations use POST. Keep an exact allowlist.
                def read_only(route):
                    try:
                        if read_request_allowed(route.request):
                            route.continue_()
                        else:
                            route.abort()
                    except BrowserError as error:
                        if not (closing and is_closed_error(error)):
                            raise
                context.route('**/*', read_only)
                result = export_from_page(context, context.new_page(), merchant_id, timeout)
                # Persist session refreshes without copying another browser's credentials.
                from kaspibot.services.catalog_source import atomic_write
                atomic_write(Path(session_path), json.dumps(context.storage_state()).encode())
                return result
            finally:
                closing=True
                try:
                    if context is not None:
                        try:
                            # Drain in-flight routes while the context is alive, then close it.
                            context.unroute_all(behavior='wait')
                            context.close()
                        except BrowserError as error:
                            if not is_closed_error(error):
                                raise
                finally:
                    try:
                        browser.close()
                    except BrowserError as error:
                        if not is_closed_error(error):
                            raise
    except ExportError:
        raise
    except BrowserTimeout:
        raise ExportError('Kaspi не ответил вовремя; прежний каталог сохранён.', 'timeout') from None
    except BrowserError as error:
        if is_closed_error(error):
            raise ExportError('Браузер экспорта был закрыт; сохранённый XML доступен.','interrupted') from None
        # Inspect only the error category; never expose an embedded URL/session.
        if 'net::ERR_' in str(error):
            raise ExportError('соединение с Kaspi временно недоступно.', 'network_error') from None
        raise ExportError('не удалось выполнить экспорт; проверьте локальный вход, Chrome и сеть.') from None
    except (OSError, ValueError):
        raise ExportError('не удалось прочитать локальную сессию или сохранить выгрузку.') from None


def fetch_export(session_path, merchant_id, timeout=90):
    deadline = time.monotonic() + timeout
    for attempt in range(2):
        remaining = max(1, int(deadline - time.monotonic()))
        try:
            return _fetch_export_once(session_path, merchant_id, timeout=remaining)
        except ExportError as error:
            if error.code not in ('timeout', 'network_error') or attempt or deadline-time.monotonic() < 20:
                raise
            time.sleep(1+random.uniform(0,.5))
    raise ExportError('выгрузка не завершена; следующая попытка по расписанию.')
