"""Startup + periodic sync, with a SQLite lease shared by all web workers."""
import secrets
import threading
from datetime import timedelta

from flask import current_app
from sqlalchemy import or_, inspect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.schema import CreateTable

from kaspibot.models import db, KaspiAutoSyncState, KaspiSyncRun, SellerCatalogImport
from kaspibot.services.seller_sync import utcnow
from kaspibot.services.sync_job import launch
from kaspibot.services.catalog_download import download_xml
from kaspibot.services.seller_catalog import import_xml


def tick(owner, boot=False):
    """One bounded catalogue refresh + queue an API worker; never block the HTTP request."""
    now = utcnow()
    interval = current_app.config['KASPI_SYNC_INTERVAL_SECONDS']
    if db.session.get(KaspiAutoSyncState, 1) is None:
        db.session.add(KaspiAutoSyncState(id=1))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
    due = or_(KaspiAutoSyncState.next_attempt_at.is_(None), KaspiAutoSyncState.next_attempt_at <= now)
    if boot:
        due = or_(due, KaspiAutoSyncState.last_attempt_at.is_(None),
                  KaspiAutoSyncState.last_attempt_at < now - timedelta(seconds=10))
    claimed = KaspiAutoSyncState.query.filter(
        KaspiAutoSyncState.id == 1, due,
        or_(KaspiAutoSyncState.lease_until.is_(None), KaspiAutoSyncState.lease_until <= now)
    ).update(dict(lease_owner=owner, lease_until=now + timedelta(minutes=10),
                  last_attempt_at=now, next_attempt_at=now + timedelta(seconds=interval)), synchronize_session=False)
    db.session.commit()
    if not claimed:
        return False
    try:
        state = db.session.get(KaspiAutoSyncState, 1)
        url = current_app.config.get('KASPI_CATALOG_URL', '').strip()
        if not url:
            state.catalog_status = 'not_configured'
            state.catalog_message = 'Укажите KASPI_CATALOG_URL: API заказов не заменяет полный каталог.'
            db.session.commit()
        else:
            try:
                content = download_xml(url)
                result = import_xml(content)
                state = db.session.get(KaspiAutoSyncState, 1)
                snapshot = db.session.get(SellerCatalogImport, result['import_id'])
                state.catalog_status = 'unchanged' if result['duplicate'] else 'imported'
                state.catalog_checked_at = utcnow()
                state.catalog_source_date = snapshot.source_date
                state.catalog_message = f"Позиций в прайсе: {result['offers']}. Дата файла: {snapshot.source_date}."
                db.session.commit()
            except (ValueError, SQLAlchemyError) as exc:
                db.session.rollback()
                state = db.session.get(KaspiAutoSyncState, 1)
                state.catalog_status = 'failed'
                state.catalog_message = str(exc) if isinstance(exc, ValueError) else 'Ошибка записи XML; прежний каталог сохранён.'
                db.session.commit()
        # A feed failure must not suppress API orders; a missing API token must not suppress a feed.
        state = db.session.get(KaspiAutoSyncState, 1)
        if not current_app.config.get('KASPI_API_TOKEN'):
            state.orders_status = 'not_configured'
            state.orders_message = 'Укажите свой KASPI_API_TOKEN локально.'
            db.session.commit()
        else:
            active = KaspiSyncRun.query.filter(KaspiSyncRun.active_slot == 'orders',
                KaspiSyncRun.started_at > utcnow() - timedelta(hours=2)).first()
            try:
                run_id = active.id if active else launch(current_app.config['KASPI_HISTORY_DAYS'])
                state = db.session.get(KaspiAutoSyncState, 1)
                state.orders_run_id = run_id
                state.orders_status = 'running' if active else 'queued'
                state.orders_message = 'Чтение API выполняется в фоне.'
            except ValueError as exc:
                state = db.session.get(KaspiAutoSyncState, 1)
                state.orders_status = 'failed'
                state.orders_message = str(exc)
            db.session.commit()
        return True
    finally:
        db.session.rollback()
        state = db.session.get(KaspiAutoSyncState, 1)
        if state and state.lease_owner == owner:
            state.lease_owner = None
            state.lease_until = None
            state.finished_at = utcnow()
            db.session.commit()


_START_LOCK = threading.Lock()


def start(app):
    with _START_LOCK:
        return _start(app)


def _start(app):
    """Only web entry points invoke this; app factories/CLI imports never perform network I/O."""
    if app.testing or not app.config['KASPI_AUTO_SYNC']:
        return None
    if 'kaspi_auto_sync' in app.extensions:
        return app.extensions['kaspi_auto_sync']
    with app.app_context():
        tables = set(inspect(db.engine).get_table_names())
        if not {'users', 'products', 'kaspi_sync_runs', 'seller_catalog_imports'} <= tables:
            app.extensions['kaspi_auto_sync_error'] = 'Сначала выполните init-db.'
            return None
        with db.engine.begin() as connection:
            connection.execute(CreateTable(KaspiAutoSyncState.__table__, if_not_exists=True))
    stop = threading.Event()
    owner = secrets.token_hex(16)

    def loop():
        boot = True
        while not stop.is_set():
            with app.app_context():
                try:
                    tick(owner, boot=boot)
                except Exception:
                    # No exception text: requests/SQL errors can contain signed URLs or private payloads.
                    db.session.rollback()
                    app.logger.error('automatic_kaspi_sync_tick_failed')
                finally:
                    db.session.remove()
            boot = False
            stop.wait(5)
    thread = threading.Thread(target=loop, name='kaspi-auto-sync', daemon=True)
    handle = dict(stop=stop, thread=thread)
    app.extensions['kaspi_auto_sync'] = handle
    thread.start()
    return handle


def status():
    state = db.session.get(KaspiAutoSyncState, 1) if inspect(db.engine).has_table('kaspi_auto_sync_state') else None
    handle = current_app.extensions.get('kaspi_auto_sync')
    result = dict(enabled=current_app.config['KASPI_AUTO_SYNC'],
                  running=bool(handle and handle['thread'].is_alive()),
                  interval_seconds=current_app.config['KASPI_SYNC_INTERVAL_SECONDS'],
                  catalog_configured=bool(current_app.config.get('KASPI_CATALOG_URL')),
                  startup_error=current_app.extensions.get('kaspi_auto_sync_error'),
                  last_attempt_at=None, next_attempt_at=None,
                  catalog_status='not_configured', catalog_message=None,
                  orders_status='not_configured', orders_message=None)
    if state:
        for field in ('last_attempt_at', 'next_attempt_at', 'catalog_checked_at'):
            value = getattr(state, field)
            result[field] = value.isoformat() if value else None
        for field in ('catalog_status', 'catalog_message', 'catalog_source_date', 'orders_status', 'orders_message'):
            result[field] = getattr(state, field)
        run = db.session.get(KaspiSyncRun, state.orders_run_id) if state.orders_run_id else None
        if run:
            result['orders_status'] = run.status
            result['orders_message'] = run.message
    latest = SellerCatalogImport.query.order_by(SellerCatalogImport.source_date.desc(), SellerCatalogImport.id.desc()).first()
    if latest:
        result['catalog_source_date'] = latest.source_date
    return result
