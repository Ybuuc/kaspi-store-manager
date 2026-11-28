"""Durable independent schedules, atomic claims and server-respecting cooldowns."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import random

from flask import current_app
from sqlalchemy import select, update, or_, inspect, true
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.schema import CreateTable

from kaspibot.models import db, SyncSchedule, KaspiAutoSyncState, KaspiSyncRun


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def retry_after(value, at=None):
    at=at or now()
    if not isinstance(value,str) or not value.strip():
        return None
    try:
        value=value.strip()
        if value.isascii() and value.isdigit():
            return at+timedelta(seconds=int(value))
        date=parsedate_to_datetime(value)
        if date.tzinfo is None:
            date=date.replace(tzinfo=timezone.utc)
        return max(at,date.astimezone(timezone.utc).replace(tzinfo=None))
    except (ValueError,TypeError,OverflowError):
        return None


def interval(source):
    return current_app.config['KASPI_'+source.upper()+'_SYNC_INTERVAL_SECONDS']


def ensure():
    if current_app.extensions.get('sync_schedule_ready'):
        return
    table=SyncSchedule.__table__
    with db.engine.begin() as conn:
        conn.execute(CreateTable(table,if_not_exists=True))
        tables=inspect(conn).get_table_names()
        old=conn.execute(select(KaspiAutoSyncState.__table__)).mappings().first() if 'kaspi_auto_sync_state' in tables else None
        run=conn.execute(select(KaspiSyncRun.__table__).order_by(KaspiSyncRun.id.desc()).limit(1)).mappings().first() if 'kaspi_sync_runs' in tables else None
        last_data=conn.execute(select(KaspiSyncRun.__table__).where(KaspiSyncRun.orders_count>0).order_by(KaspiSyncRun.id.desc()).limit(1)).mappings().first() if 'kaspi_sync_runs' in tables else None
        last_success=conn.execute(select(KaspiSyncRun.__table__).where(KaspiSyncRun.status=='success').order_by(KaspiSyncRun.id.desc()).limit(1)).mappings().first() if 'kaspi_sync_runs' in tables else None
        for source in ['api','xml']:
            values=dict(source=source,state='waiting',failures=0,records=0,errors=0)
            if source=='xml' and old:
                values.update(state=old['catalog_status'],message=old['catalog_message'],next_attempt_at=old['next_attempt_at'],last_attempt_at=old['last_attempt_at'],
                    last_success_at=old['catalog_checked_at'],last_data_at=old['catalog_checked_at'],
                    lease_owner=old['lease_owner'],lease_until=old['lease_until'])
                if '429' in (old['catalog_message'] or ''):
                    values.update(state='rate_limited',cooldown_until=old['next_attempt_at'])
            if source=='api' and run:
                values.update(last_attempt_at=run['started_at'],next_attempt_at=(run['finished_at'] or run['started_at'])+timedelta(seconds=interval('api')),
                    run_id=run['id'],heartbeat_at=run['finished_at'] or run['started_at'],state=run['status'],records=run['orders_count'],errors=run['error_count'])
                values['message']=run['message']
                if last_data:
                    values['last_data_at']=last_data['finished_at']
                if last_success:
                    values['last_success_at']=last_success['finished_at']
            conn.execute(insert(table).values(**values).on_conflict_do_nothing(index_elements=['source']))
    current_app.extensions['sync_schedule_ready']=True


def get(source):
    ensure()
    with db.engine.connect() as conn:
        return dict(conn.execute(select(SyncSchedule.__table__).where(SyncSchedule.source==source)).mappings().one())


def put(source,**values):
    ensure()
    with db.engine.begin() as conn:
        conn.execute(update(SyncSchedule).where(SyncSchedule.source==source).values(**values))


class Deferred(ValueError):
    def __init__(self,source,until):
        super().__init__('Kaspi ограничил запросы; следующая разрешённая попытка: '+until.isoformat()+' UTC.')
        self.code='rate_limited'
        self.until=until
        self.source=source


def check(source):
    row=get(source)
    if row['cooldown_until'] and row['cooldown_until']>now():
        raise Deferred(source,row['cooldown_until'])


def defer(source,code='rate_limited',header=None):
    ensure()
    table=SyncSchedule.__table__
    at=now()
    with db.engine.begin() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        row=conn.execute(select(table).where(table.c.source==source)).mappings().one()
        failures=min(row['failures']+1,20)
        maximum=current_app.config['KASPI_XML_MAX_BACKOFF_SECONDS'] if source=='xml' else 3600
        delay=min(maximum,interval(source)*2**(failures-1))
        until=retry_after(header,at) or at+timedelta(seconds=random.uniform(delay,min(maximum,delay*1.2)))
        until=max(until,row['cooldown_until'] or at,at+timedelta(seconds=1))
        conn.execute(update(table).where(table.c.source==source).values(
            state=code,failures=failures,cooldown_until=until,next_attempt_at=until,
            message='Ограничение Kaspi (HTTP 429).' if code=='rate_limited' else 'Временный сбой; запросы отложены.',errors=max(1,row['errors'])))
    return until


def claim(source,owner,manual=False):
    ensure()
    at=now()
    with db.engine.begin() as conn:
        result=conn.execute(update(SyncSchedule).where(SyncSchedule.source==source,
            true() if manual else or_(SyncSchedule.next_attempt_at.is_(None),SyncSchedule.next_attempt_at<=at),
            or_(SyncSchedule.cooldown_until.is_(None),SyncSchedule.cooldown_until<=at),
            or_(SyncSchedule.lease_until.is_(None),SyncSchedule.lease_until<=at)).values(
                lease_owner=owner,lease_until=at+timedelta(minutes=15 if manual else 10),last_attempt_at=at,
                next_attempt_at=at+timedelta(seconds=interval(source)),state='running',errors=0,records=0))
        return bool(result.rowcount)


def release(source,owner):
    with db.engine.begin() as conn:
        conn.execute(update(SyncSchedule).where(SyncSchedule.source==source,SyncSchedule.lease_owner==owner).values(lease_owner=None,lease_until=None))


def finish(source,state,records=0,errors=0,message=None,session=None):
    row=get(source)
    values=dict(state=state,records=records,errors=errors,message=message,heartbeat_at=now())
    if records and source=='api':
        values['last_data_at']=now()
    if state in ('success','partial','imported','unchanged'):
        values.update(failures=0,cooldown_until=None)
        if source=='xml':
            values['next_attempt_at']=now()+timedelta(seconds=interval('xml'))
        if records or source=='xml':
            values['last_data_at']=now()
        if state!='partial':
            values['last_success_at']=now()
    elif state=='temporary_error' and not (row['cooldown_until'] and row['cooldown_until']>now()):
        defer(source,state)
    if session is None:
        put(source,**values)
    else:
        session.execute(update(SyncSchedule).where(SyncSchedule.source==source).values(**values))


def public(source):
    row=get(source)
    at=now()
    result={key:(value.isoformat()+'Z' if isinstance(value,datetime) else value) for key,value in row.items() if key not in ('lease_owner','lease_until')}
    result['interval_seconds']=interval(source)
    result['retry_in_seconds']=max(0,int(((row['next_attempt_at'] or at)-at).total_seconds()))
    result['using_saved_data']=row['state'] not in ('success','imported','unchanged') and row['last_data_at'] is not None
    return result
