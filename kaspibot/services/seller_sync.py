"""Persist real API observations; replaying old orders must not debit legacy stock."""
import json
from concurrent.futures import ThreadPoolExecutor
from flask import g, current_app
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kaspibot.models import db, KaspiOrderSync, KaspiSyncRun
from kaspibot.services.kaspi_api import get_orders, get_order_by_code, get_order_details, get_order_entries, KaspiAPIError
from kaspibot.services.pagination import collect_pages
from kaspibot.services import sync_schedule as schedule
from kaspibot.services.order_payload import history_protected
from kaspibot.services.active_orders_import import sync_order_and_entries_to_db

STATES=('NEW','SIGN_REQUIRED','PICKUP','DELIVERY','KASPI_DELIVERY','ARCHIVE')


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def start_run():
    schedule.check('api')
    # A dead subprocess leaves its unique slot until its heartbeat expires.
    saved=schedule.get('api')
    heartbeat=saved['heartbeat_at'] or saved['last_attempt_at']
    if heartbeat and heartbeat < utcnow()-timedelta(minutes=10):
        KaspiSyncRun.query.filter(KaspiSyncRun.active_slot=='orders',KaspiSyncRun.started_at<heartbeat).update(
            {'active_slot':None,'status':'interrupted','message':'Предыдущий процесс не возобновил heartbeat.'})
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
    schedule.put('api',run_id=run.id,state='queued',last_attempt_at=utcnow(),heartbeat_at=utcnow(),
        next_attempt_at=utcnow()+timedelta(seconds=schedule.interval('api')))
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
    schedule.put('api',state='running',records=0,errors=0,heartbeat_at=utcnow())
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
                schedule.put('api',heartbeat_at=utcnow())
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
            schedule.put('api',heartbeat_at=utcnow())
            run.message=f'Проверка старых активных заказов: {index}/{len(old_codes)}'
            db.session.commit()
            try:
                payload=get_order_by_code(code)
            except KaspiAPIError as exc:
                if exc.status in (401,403,429):
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
        cached_conflicts={}
        def unchanged_lines(order):
            if order['attributes'].get('state') != 'ARCHIVE':
                return None
            code=str(order['attributes'].get('code') or order['id'])
            previous=KaspiOrderSync.query.filter_by(order_code=code).all()
            if not previous:
                return None
            for row in previous:
                try:
                    saved=json.loads(row.raw_json or '{}')
                except (ValueError,TypeError):
                    return None
                try:
                    checked=datetime.fromisoformat(saved.get('entries_checked_at') or '')
                    if checked.tzinfo is None or datetime.now(timezone.utc)-checked >= timedelta(hours=24):
                        return None
                except (ValueError,TypeError):
                    return None
                if saved.get('source')!='kaspi_api' or saved.get('order')!=order:
                    return None
                if saved.get('mapping_status')=='conflict' or not row.product_id:
                    if not history_protected(row):
                        return None
                    cached_conflicts[order['id']]=cached_conflicts.get(order['id'],0)+1
            return len(previous)
        unchanged={key:unchanged_lines(order) for key,order in collected.items()}
        app=current_app._get_current_object()
        def read_entries(order_id):
            # HTTP reads only in worker threads; ORM and all SQLite writes remain sequential.
            with app.app_context():
                return get_order_entries(order_id)
        pool=ThreadPoolExecutor(max_workers=4)
        # Do not prefetch hundreds of unchanged archived orders on every automatic tick.
        entry_reads={}
        pending=iter(key for key in collected if unchanged[key] is None)
        def prefetch():
            while len(entry_reads)<4:
                key=next(pending,None)
                if key is None:
                    break
                entry_reads[key]=pool.submit(read_entries,collected[key]['id'])
        prefetch()
        for index,order in enumerate(collected.values(),1):
            run=db.session.get(KaspiSyncRun,run_id)
            schedule.put('api',heartbeat_at=utcnow(),records=orders_count,errors=len(errors))
            run.message=f'Чтение состава заказа: {index}/{len(collected)}'
            run.orders_count=orders_count
            run.lines_count=lines_count
            db.session.commit()
            try:
                if unchanged[order['id']] is not None:
                    orders_count+=1
                    lines_count+=unchanged[order['id']]
                    if cached_conflicts.get(order['id']):
                        errors.append({'code':'unmatched_product','kind':'mapping_conflict','order_code':str(order['attributes'].get('code') or order['id']),'message':'Сохранённый состав подтверждён; складская привязка требует сверки.'})
                    continue
                entries=entry_reads.pop(order['id']).result()
                prefetch()
                result=sync_order_and_entries_to_db(order,entries,apply_movements=False)
                orders_count+=1
                lines_count+=result['total_lines']
                if result['unmatched']:
                    errors.append({'code':'unmatched_product','order_code':str(order['attributes'].get('code') or order['id']),'kind':'mapping_conflict','message':f"Не привязано позиций: {result['unmatched']}. Суммы и статусы сохранены; склад не изменён."})
            except (KaspiAPIError, ValueError, SQLAlchemyError) as exc:
                db.session.rollback()
                if isinstance(exc,schedule.Deferred) or getattr(exc,'status',None) in (401,403,429):
                    raise
                message=str(exc) if not isinstance(exc,SQLAlchemyError) else 'Конфликт карточки или связи в базе; данные заказа не перезаписаны.'
                errors.append({'code':'order_failed','order_code':str(order['attributes'].get('code') or order['id']),'message':message,'http_status':getattr(exc,'status',None),'kind':getattr(exc,'code','permanent_error')})
        run=db.session.get(KaspiSyncRun,run_id)
        run.status='partial' if errors else 'success'
        run.message='Заказы прочитаны из API Kaspi. Физические остатки и закупка не выводятся из истории заказов.'
        return dict(ok=not errors,partial=bool(errors),orders=orders_count,lines=lines_count,
                    error_count=len(errors),errors=errors[:20],source='kaspi_api',
                    movements_applied=False,run_id=run_id,message=run.message)
    except (KaspiAPIError,ValueError,SQLAlchemyError) as exc:
        db.session.rollback()
        run=db.session.get(KaspiSyncRun,run_id)
        run.status='rate_limited' if isinstance(exc,schedule.Deferred) or getattr(exc,'status',None)==429 else 'needs_login' if getattr(exc,'status',None) in (401,403) else 'temporary_error' if getattr(exc,'code',None)=='temporary_error' else 'partial' if orders_count else 'failed'
        message=str(exc) if not isinstance(exc,SQLAlchemyError) else 'Ошибка записи в базу; предыдущие данные сохранены.'
        run.message=message
        errors.append({'code':'fetch_failed','message':message,'http_status':getattr(exc,'status',None),'kind':getattr(exc,'code','permanent_error')})
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
        schedule.finish('api',run.status,orders_count,len(errors),run.message,session=db.session)
        db.session.commit()
