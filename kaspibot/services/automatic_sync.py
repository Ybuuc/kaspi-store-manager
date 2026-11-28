"""One coordinator per web worker; independent shared-DB claims and a bounded XML task."""
import atexit
import secrets
import threading

from flask import current_app
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.schema import CreateTable

from kaspibot.models import db, KaspiAutoSyncState, KaspiSyncRun, SellerCatalogImport, SellerCatalogOffer
from kaspibot.services.seller_sync import utcnow
from kaspibot.services.sync_job import launch
from kaspibot.services.catalog_download import download_xml
from kaspibot.services.seller_catalog import import_xml
from kaspibot.services.catalog_source import refresh_source, source_status, save_status
from kaspibot.services import sync_schedule as schedule


def legacy():
    state=db.session.get(KaspiAutoSyncState,1)
    if state is None:
        db.session.add(KaspiAutoSyncState(id=1))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
    return db.session.get(KaspiAutoSyncState,1)


def tick_api(owner):
    saved=schedule.get('api')
    active=KaspiSyncRun.query.filter_by(active_slot='orders').first()
    heartbeat=saved['heartbeat_at'] or saved['last_attempt_at']
    if active and heartbeat and (utcnow()-heartbeat).total_seconds()<=600:
        return False
    if not schedule.claim('api',owner):
        return False
    try:
        state=legacy()
        if not current_app.config.get('KASPI_API_TOKEN'):
            schedule.put('api',state='not_configured',message='Укажите свой API-токен локально.')
            state.orders_status='not_configured'
        else:
            saved=schedule.get('api')
            heartbeat=saved['heartbeat_at'] or saved['last_attempt_at']
            if heartbeat and (utcnow()-heartbeat).total_seconds()>600:
                KaspiSyncRun.query.filter(KaspiSyncRun.active_slot=='orders',KaspiSyncRun.id==saved['run_id']).update(
                    {'active_slot':None,'status':'interrupted','message':'Процесс не возобновил heartbeat.'})
                db.session.commit()
            active=KaspiSyncRun.query.filter_by(active_slot='orders').first()
            run_id=active.id if active else launch(current_app.config['KASPI_HISTORY_DAYS'])
            schedule.put('api',run_id=run_id)
            state.orders_run_id=run_id
            state.orders_status='running' if active else 'queued'
            state.orders_message='API заказов работает независимо от XML.'
        db.session.commit()
    except schedule.Deferred:
        db.session.rollback()
    except ValueError:
        db.session.rollback()
        if not KaspiSyncRun.query.filter_by(active_slot='orders').first():
            schedule.finish('api','failed',errors=1,message='Не удалось поставить API-задачу в очередь.')
            state=legacy()
            state.orders_status='failed'
            state.orders_message='Не удалось поставить API-задачу в очередь.'
            db.session.commit()
    finally:
        schedule.release('api',owner)
    return True


def tick_xml(owner):
    if not schedule.claim('xml',owner):
        return False
    publication=None
    imported=False
    try:
        state=legacy()
        row=schedule.get('xml')
        state.lease_owner=owner
        state.lease_until=row['lease_until']
        state.last_attempt_at=row['last_attempt_at']
        state.next_attempt_at=row['next_attempt_at']
        db.session.commit()
        url=current_app.config.get('KASPI_CATALOG_URL','').strip()
        if not url:
            state.catalog_status='not_configured'
            schedule.put('xml',state='not_configured',message='Источник XML не настроен.')
        else:
            if current_app.config.get('KASPI_CATALOG_EXPORT'):
                publication=refresh_source()
            content=download_xml(url,allow_local=current_app.config['KASPI_ALLOW_LOCAL_CATALOG'],
                ca_bundle=current_app.config.get('KASPI_CATALOG_CA_BUNDLE'))
            if publication and publication.content!=content:
                raise ValueError('Источник XML: локальный сервер вернул другой файл; импорт отменён.')
            result=import_xml(content)
            imported=True
            if publication:
                publication.accept()
            state=legacy()
            snapshot=db.session.get(SellerCatalogImport,result['import_id'])
            state.catalog_status='unchanged' if result['duplicate'] else 'imported'
            state.catalog_checked_at=utcnow()
            state.catalog_source_date=snapshot.source_date
            state.catalog_message=f"Позиций в прайсе: {result['offers']}. Дата файла: {snapshot.source_date}."
            schedule.finish('xml',state.catalog_status,result['offers'],message=state.catalog_message)
        db.session.commit()
    except (ValueError,SQLAlchemyError) as error:
        db.session.rollback()
        if publication and not imported:
            publication.rollback()
            save_status('failed','Импорт не завершён; предыдущий XML восстановлен.')
        code=getattr(error,'code','failed')
        message=str(error) if isinstance(error,ValueError) else 'Ошибка записи XML; прежний каталог сохранён.'
        if code=='rate_limited':
            schedule.defer('xml',header=getattr(error,'retry_after',None))
        else:
            kind=code if code in ('needs_login','interrupted') else 'temporary_error' if code in ('timeout','network_error','response_error') else 'failed'
            schedule.finish('xml',kind,errors=1,message=message)
        state=legacy()
        state.catalog_status='failed'
        state.catalog_message=message
        db.session.commit()
    finally:
        schedule.release('xml',owner)
        db.session.rollback()
        state=legacy()
        state.lease_owner=None
        state.lease_until=None
        state.finished_at=utcnow()
        state.next_attempt_at=schedule.get('xml')['next_attempt_at']
        db.session.commit()
    return True


def tick(owner,boot=False,background=False):
    # boot never bypasses a durable deadline or server cooldown.
    api=tick_api(owner)
    if not background:
        return tick_xml(owner) or api
    app=current_app._get_current_object()
    task=app.extensions.get('kaspi_xml_task')
    if task and task.is_alive():
        return api
    row=schedule.get('xml')
    if (row['next_attempt_at'] and row['next_attempt_at']>utcnow()) or (row['lease_until'] and row['lease_until']>utcnow()):
        return api
    def export():
        with app.app_context():
            try:
                tick_xml(owner)
            except Exception:
                db.session.rollback()
                app.logger.error('xml_sync_task_failed')
            finally:
                db.session.remove()
    task=threading.Thread(target=export,name='kaspi-xml-export',daemon=True)
    app.extensions['kaspi_xml_task']=task
    task.start()
    return True


_START_LOCK=threading.Lock()


def stop(app,timeout=200):
    handle=app.extensions.get('kaspi_auto_sync')
    if not handle:
        return
    handle['stop'].set()
    handle['thread'].join(timeout=6)
    task=app.extensions.get('kaspi_xml_task')
    if task:
        task.join(timeout=timeout)


def start(app):
    with _START_LOCK:
        if app.testing or not app.config['KASPI_AUTO_SYNC']:
            return None
        if 'kaspi_auto_sync' in app.extensions:
            return app.extensions['kaspi_auto_sync']
        with app.app_context():
            if not {'users','products','kaspi_sync_runs','seller_catalog_imports'}<=set(inspect(db.engine).get_table_names()):
                app.extensions['kaspi_auto_sync_error']='Сначала выполните init-db.'
                return None
            with db.engine.begin() as connection:
                connection.execute(CreateTable(KaspiAutoSyncState.__table__,if_not_exists=True))
            schedule.ensure()
        event=threading.Event()
        owner=secrets.token_hex(16)
        def loop():
            while not event.is_set():
                with app.app_context():
                    try:
                        tick(owner,background=True)
                    except Exception:
                        db.session.rollback()
                        app.logger.error('automatic_kaspi_sync_tick_failed')
                    finally:
                        db.session.remove()
                event.wait(5)
        thread=threading.Thread(target=loop,name='kaspi-auto-sync',daemon=True)
        handle=dict(stop=event,thread=thread)
        app.extensions['kaspi_auto_sync']=handle
        thread.start()
        atexit.register(stop,app)
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
        result['catalog_imported_at'] = latest.imported_at.isoformat()
        result['catalog_offers'] = SellerCatalogOffer.query.filter_by(import_id=latest.id).count()
    result.setdefault('catalog_offers', 0)
    result['source'] = source_status()
    result['https_status'] = 'not_checked'
    if result['catalog_status'] in ('imported', 'unchanged'):
        result['https_status'] = 'available'
    elif result['catalog_status'] == 'failed':
        prefix = (result.get('catalog_message') or '').split(':', 1)[0]
        result['https_status'] = {'URL': 'invalid_url', 'TLS': 'tls_error', 'Сеть': 'unavailable',
                                  'XML': 'available', 'Импорт': 'available', 'Источник XML': 'not_checked'}.get(prefix, 'response_error')
    result['channels']={source:schedule.public(source) for source in ['api','xml']}
    if not current_app.config['KASPI_AUTO_SYNC']:
        for channel in result['channels'].values():
            channel['state']='disabled'
    xml=result['channels']['xml']
    api=result['channels']['api']
    if xml['state'] in ('imported','unchanged','success'):
        result['catalog_status']='imported' if xml['state']=='success' else xml['state']
        result['https_status']='available'
        result['catalog_checked_at']=xml['last_success_at']
    result['next_attempt_at']=xml['next_attempt_at']
    result['orders_status']=api['state']
    result['orders_message']=api['message']
    result['interval_seconds']=schedule.interval('xml')
    return result
