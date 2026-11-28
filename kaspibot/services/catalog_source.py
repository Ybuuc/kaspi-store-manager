"""Validate and atomically publish cabinet exports; restore the old file on import failure."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from flask import current_app

from kaspibot.models import SellerCatalogImport
from kaspibot.services.cabinet_export import fetch_export, ExportError
from kaspibot.services.seller_catalog import parse_xml


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.pending-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def folder():
    return Path(current_app.config.get('KASPI_CATALOG_PRIVATE_DIR') or Path(current_app.root_path)/'.local-catalog')


def save_status(state, message, **values):
    path = folder()/'source-status.json'
    try:
        previous = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        previous.update(state=state, message=message, checked_at=datetime.now(timezone.utc).isoformat(), **values)
        atomic_write(path, json.dumps(previous, ensure_ascii=False).encode())
    except (OSError, ValueError):
        pass  # Status reporting must not suppress the independent API worker.


def source_status():
    if not current_app.config.get('KASPI_CATALOG_EXPORT'):
        return {'enabled': False, 'state': 'disabled', 'message': 'Экспорт из кабинета выключен.'}
    try:
        result = json.loads((folder()/'source-status.json').read_text(encoding='utf-8'))
        result = {key: result[key] for key in ('state', 'message', 'checked_at', 'exported_at', 'source_date', 'offers') if key in result}
    except (OSError, ValueError, TypeError):
        result = {'state': 'waiting', 'message': 'Ожидается первая выгрузка из кабинета.'}
    return dict(enabled=True, **result)


class Publication:
    def __init__(self, path, content, previous, payload):
        self.path, self.content, self.previous, self.payload = path, content, previous, payload

    def accept(self):
        save_status('exported', 'XML получен из кабинета и импортирован.',
                    exported_at=datetime.now(timezone.utc).isoformat(),
                    source_date=self.payload['source_date'], offers=len(self.payload['offers']))

    def rollback(self):
        try:
            # Never overwrite a file which an operator changed after our publication.
            if self.path.read_bytes() == self.content:
                if self.previous is None:
                    self.path.unlink()
                else:
                    atomic_write(self.path, self.previous)
        except OSError:
            save_status('failed', 'Ошибка восстановления локального XML; проверьте файл и резервную копию.')


def refresh_source(content=None):
    merchant = current_app.config.get('KASPI_CATALOG_MERCHANT_ID', '').strip()
    url = urlsplit(current_app.config.get('KASPI_CATALOG_URL', ''))
    if url.scheme != 'https' or url.hostname not in ('localhost', '127.0.0.1', '::1') or url.port != 8443 or url.path != '/catalog.xml' or url.query:
        raise ExportError('экспортёр требует локальный HTTPS /catalog.xml на порту 8443.')
    try:
        save_status('running', 'Получение нового XML из кабинета Kaspi.')
        if content is None:
            from kaspibot.services.sync_schedule import check
            check('xml')
            content = fetch_export(folder()/'kaspi-session.json', merchant)
        payload = parse_xml(content)
        latest = SellerCatalogImport.query.order_by(SellerCatalogImport.source_date.desc(), SellerCatalogImport.id.desc()).first()
        if payload['merchant_id'] != merchant or (latest and latest.merchant_id != merchant):
            raise ExportError('магазин XML не совпадает с рабочей базой.', 'wrong_merchant')
        if latest and payload['source_date'] < latest.source_date:
            raise ExportError('Kaspi вернул устаревшую выгрузку; прежний XML сохранён.', 'stale')
        path = folder()/'catalog.xml'
        previous = path.read_bytes() if path.is_file() else None
        if previous != content:
            atomic_write(path, content)
        return Publication(path, content, previous, payload)
    except ExportError as exc:
        save_status(exc.code, str(exc))
        raise
    except (OSError, ValueError):
        save_status('failed', 'Неверный XML или ошибка сохранения; прежний каталог сохранён.')
        raise ExportError('не удалось проверить или сохранить выгрузку; прежний файл сохранён.') from None
