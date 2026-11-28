# ruff: noqa: E402
"""Interactive local Kaspi login. Passwords/SMS are entered by the operator only."""
import json
import secrets
from contextlib import contextmanager
import sqlite3
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright, Error as BrowserError
from sqlalchemy.exc import SQLAlchemyError
from tools.setup_local_catalog import provision
from kaspibot.services.catalog_source import atomic_write, refresh_source
from kaspibot.services.cabinet_session import save_session
from kaspibot.services.cabinet_export import export_from_page, ExportError, CABINET
from kaspibot.services.catalog_download import download_xml
from kaspibot.services.seller_catalog import import_xml
from kaspibot.models import db
from app import create_app


def progress(state, message, **values):
    path = ROOT/'.runtime'/'kaspi-login-status.json'
    atomic_write(path, json.dumps(dict(state=state, message=message,
        updated_at=datetime.now(timezone.utc).isoformat(), **values), ensure_ascii=False).encode())
    print(message, flush=True)


@contextmanager
def export_lease(app):
    from kaspibot.services import sync_schedule as schedule
    owner=secrets.token_hex(16)
    with app.app_context():
        schedule.check('xml')
        if not schedule.claim('xml',owner,manual=True):
            raise ExportError('экспорт XML уже выполняется; дождитесь завершения.')
        schedule.put('xml',state='needs_login',message='Ожидается вход в отдельном локальном окне Kaspi.')
    try:
        yield
    finally:
        with app.app_context():
            schedule.release('xml',owner)


def main():
    private = ROOT/'.local-catalog'
    provision(private)  # Restricts the folder ACL before writing session credentials.
    app = create_app(dict(TESTING=True, KASPI_CATALOG_EXPORT=True))
    merchant = app.config.get('KASPI_CATALOG_MERCHANT_ID', '').strip()
    if not merchant:
        with sqlite3.connect(Path(app.config['DATABASE']).as_uri()+'?mode=ro', uri=True) as connection:
            row = connection.execute('SELECT merchant_id FROM seller_catalog_imports ORDER BY source_date DESC,id DESC LIMIT 1').fetchone()
        merchant = row[0] if row else ''
        app.config['KASPI_CATALOG_MERCHANT_ID'] = merchant
    if not merchant.isascii() or not merchant.isdigit():
        raise ExportError('укажите числовой KASPI_CATALOG_MERCHANT_ID для вашего магазина.')
    session_file = private/'kaspi-session.json'
    with export_lease(app), sync_playwright() as runtime:
        browser = runtime.chromium.launch(channel='chrome', headless=False)
        try:
            options = dict(accept_downloads=True, viewport={'width':1366,'height':900}, ignore_https_errors=False)
            if session_file.is_file():
                options['storage_state'] = str(session_file)
            context = browser.new_context(**options)
            context.set_default_timeout(10000)
            page = context.new_page()
            progress('waiting_login', 'Войдите в открытом отдельном окне Kaspi. Пароль и SMS вводите только там.')
            page.goto(CABINET, wait_until='domcontentloaded', timeout=30000)
            deadline = time.monotonic()+600
            while time.monotonic() < deadline:
                if page.get_by_role('link', name='Управление товарами', exact=True).is_visible():
                    break
                page.wait_for_timeout(1000)
            else:
                raise ExportError('время ожидания входа истекло.', 'needs_login')
            progress('authenticated', 'Вход обнаружен. Проверяется магазин и первая настоящая XML-выгрузка.')
            with app.app_context():
                try:
                    from kaspibot.services import sync_schedule as schedule
                    schedule.put('xml',state='running',message='Проверка выгрузки после входа.')
                    content = export_from_page(context, page, merchant,
                        on_authenticated=lambda:save_session(context,session_file))
                    save_session(context,session_file)
                except ExportError as error:
                    if error.code=='rate_limited':
                        from kaspibot.services.sync_schedule import defer
                        defer('xml',header=error.retry_after)
                    raise
            with app.app_context():
                publication = refresh_source(content)
                try:
                    served = download_xml(app.config['KASPI_CATALOG_URL'], allow_local=app.config['KASPI_ALLOW_LOCAL_CATALOG'],
                                          ca_bundle=app.config.get('KASPI_CATALOG_CA_BUNDLE'))
                    if served != content:
                        raise ExportError('локальный HTTPS-сервер вернул другой файл.')
                    result = import_xml(served)
                except Exception:
                    publication.rollback()
                    raise
                publication.accept()
                schedule.finish('xml','imported',result['offers'],message='Выгрузка после входа подтверждена и импортирована.')
                db.session.remove()
                db.engine.dispose()
            progress('ready', 'Свежий XML получен, опубликован через HTTPS и импортирован.',
                     offers=result['offers'], duplicate=result['duplicate'])
        finally:
            browser.close()


if __name__ == '__main__':
    try:
        main()
    except ExportError as error:
        progress(error.code, str(error))
        sys.exit(1)
    except (BrowserError, OSError, ValueError, SQLAlchemyError):
        progress('failed', 'Не удалось завершить локальный вход или экспорт. Прежние данные сохранены.')
        sys.exit(1)

    except Exception:
        progress('failed', 'Внутренняя ошибка локального экспортёра; сессия и прежний XML сохранены.')
        sys.exit(1)
