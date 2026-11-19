"""Persist real API observations; replaying old orders must not debit legacy stock."""
import json
from concurrent.futures import ThreadPoolExecutor
from flask import g, current_app
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kaspibot.models import db, KaspiOrderSync, KaspiSyncRun
from kaspibot.services.kaspi_api import get_orders, get_order_by_code, get_order_details, get_order_entries, KaspiAPIError
from kaspibot.services.pagination import collect_pages
from kaspibot.services.active_orders_import import sync_order_and_entries_to_db

STATES=('NEW','SIGN_REQUIRED','PICKUP','DELIVERY','KASPI_DELIVERY','ARCHIVE')


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def start_run():
    KaspiSyncRun.query.filter(KaspiSyncRun.active_slot=='orders',
                             KaspiSyncRun.started_at < utcnow()-timedelta(hours=2)).update(
        {'active_slot':None,'status':'interrupted','message':'Предыдущий запуск прерван.'})
    run=KaspiSyncRun(active_slot='orders',status='queued')
    db.session.add(run)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ValueError('Синхронизация уже выполняется') from None
    return run.id


def synchronize(days=14, run_id=None, recheck_old=True):
    if not isinstance(days,int) or not 1 <= days <= 366:
        raise ValueError('History must be between 1 and 366 days')
    if run_id is None:
        run_id=start_run()
    run=db.session.get(KaspiSyncRun,run_id)
    if not run or run.status != 'queued' or run.active_slot != 'orders':
        raise ValueError('Задание синхронизации не находится в очереди')
    run.status='running'
    db.session.commit()
    g.kaspi_product_cache={}
    errors=[]
    orders_count=lines_count=0
    pool=None
    try:
        end=datetime.now(timezone.utc)
        begin=end-timedelta(days=days)
        collected={}
        while begin < end:
            stop=min(begin+timedelta(days=14),end)
            start_ms,end_ms=int(begin.timestamp()*1000),int(stop.timestamp()*1000)
            for state in STATES:
                run=db.session.get(KaspiSyncRun,run_id)
                run.message='Чтение Kaspi: '+state
                db.session.commit()
                rows=collect_pages(get_orders,state=state,start_ms=start_ms,end_ms=end_ms)
                for row in rows:
                    if not isinstance(row,dict) or not row.get('id') or not isinstance(row.get('attributes'),dict):
                        raise ValueError('Kaspi: неверная структура заказа')
                    previous=collected.get(row['id'])
                    if previous and previous['attributes'].get('state') != row['attributes'].get('state'):
                        row=get_order_details(row['id'])['data']
                    collected[row['id']]=row
            begin=stop
        # Old active orders can finish outside the latest 14-day search window.
        seen_codes={str(row['attributes'].get('code') or row['id']) for row in collected.values()}
        old_codes={row.order_code for row in KaspiOrderSync.query.filter(
            KaspiOrderSync.state.in_(STATES[:-1])).all()} - seen_codes
        if old_codes and not recheck_old:
            errors.append({'code':'old_orders_pending','message':f'Ранее активных заказов для отдельной сверки: {len(old_codes)}.'})
        for index,code in enumerate(sorted(old_codes) if recheck_old else (),1):
            run=db.session.get(KaspiSyncRun,run_id)
            run.message=f'Проверка старых активных заказов: {index}/{len(old_codes)}'
            db.session.commit()
            try:
                payload=get_order_by_code(code)
            except KaspiAPIError as exc:
                if exc.status in (401,403):
                    raise
                errors.append({'code':'old_order_unavailable','message':str(exc)})
                continue
            if not isinstance(payload['data'],list):
                raise ValueError('Kaspi: неверный ответ поиска заказа')
            if not payload['data']:
                errors.append({'code':'order_not_found','message':'Один из ранее активных заказов не найден; прежняя запись сохранена.'})
            for row in payload['data']:
                if str((row.get('attributes') or {}).get('code')) != str(code):
                    raise ValueError('Kaspi вернул другой заказ при поиске по коду')
                collected[row['id']]=row
        app=current_app._get_current_object()
        def read_entries(order_id):
            # HTTP reads only in worker threads; ORM and all SQLite writes remain sequential.
            with app.app_context():
                return get_order_entries(order_id)
        pool=ThreadPoolExecutor(max_workers=4)
        entry_reads={key:pool.submit(read_entries,value['id']) for key,value in collected.items()}
        for index,order in enumerate(collected.values(),1):
            run=db.session.get(KaspiSyncRun,run_id)
            run.message=f'Чтение состава заказа: {index}/{len(collected)}'
            run.orders_count=orders_count
            run.lines_count=lines_count
            db.session.commit()
            try:
                code=str(order['attributes'].get('code') or order['id'])
                previous=KaspiOrderSync.query.filter_by(order_code=code).all()
                unchanged=bool(previous)
                for row in previous:
                    try:
                        saved=json.loads(row.raw_json or '{}')
                    except (ValueError,TypeError):
                        saved={}
                    if saved.get('source')!='kaspi_api' or saved.get('mapping_status')=='conflict' or saved.get('order')!=order or not row.product_id:
                        unchanged=False
                        break
                if unchanged:
                    orders_count+=1
                    lines_count+=len(previous)
                    continue
                entries=entry_reads[order['id']].result()
                result=sync_order_and_entries_to_db(order,entries,apply_movements=False)
                orders_count+=1
                lines_count+=result['total_lines']
                if result['unmatched']:
                    errors.append({'code':'unmatched_product','message':f"Не привязано позиций: {result['unmatched']}. Суммы и статусы сохранены; склад не изменён."})
            except (KaspiAPIError, ValueError, SQLAlchemyError) as exc:
                db.session.rollback()
                message=str(exc) if not isinstance(exc,SQLAlchemyError) else 'Конфликт карточки или связи в базе; данные заказа не перезаписаны.'
                errors.append({'code':'order_failed','order_code':str(order['attributes'].get('code') or order['id']),'message':message})
        run=db.session.get(KaspiSyncRun,run_id)
        run.status='partial' if errors else 'success'
        run.message='Заказы прочитаны из API Kaspi. Физические остатки и закупка не выводятся из истории заказов.'
        return dict(ok=not errors,partial=bool(errors),orders=orders_count,lines=lines_count,
                    error_count=len(errors),errors=errors[:20],source='kaspi_api',
                    movements_applied=False,run_id=run_id,message=run.message)
    except (KaspiAPIError,ValueError,SQLAlchemyError) as exc:
        db.session.rollback()
        run=db.session.get(KaspiSyncRun,run_id)
        run.status='failed'
        message=str(exc) if not isinstance(exc,SQLAlchemyError) else 'Ошибка записи в базу; предыдущие данные сохранены.'
        run.message=message
        errors.append({'code':'fetch_failed','message':message})
        return dict(ok=False,partial=bool(orders_count),orders=orders_count,lines=lines_count,
                    error_count=len(errors),errors=errors[:20],error=message,run_id=run_id,source='kaspi_api')
    finally:
        if pool is not None:
            pool.shutdown(wait=True,cancel_futures=True)
        run=db.session.get(KaspiSyncRun,run_id)
        run.orders_count=orders_count
        run.lines_count=lines_count
        run.errors_json=json.dumps(errors[:100],ensure_ascii=False)
        run.error_count=len(errors)
        run.finished_at=utcnow()
        run.active_slot=None
        db.session.commit()
