import json
import tempfile
import threading
import unittest
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from playwright.sync_api import Error as BrowserError

from app import create_app
from kaspibot.models import db, Product, KaspiOrderSync, KaspiSyncRun
from kaspibot.services import sync_schedule as clock
from kaspibot.services.automatic_sync import tick, tick_api, tick_xml
from kaspibot.services.kaspi_transport import get_orders, KaspiAPIError
from kaspibot.services.reconciliation import repair_saved, public_errors, summary
from kaspibot.services.cabinet_export import _fetch_export_once, ExportError
from test_seller_catalog import xml


class StabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.config=dict(TESTING=True,SECRET_KEY='stability-fixture',DATABASE=str(Path(self.temp.name)/'app.db'),
            KASPI_API_TOKEN='fixture-token',KASPI_CATALOG_URL='https://fixture.example/feed.xml',
            KASPI_API_SYNC_INTERVAL_SECONDS=300,KASPI_XML_SYNC_INTERVAL_SECONDS=1800)
        self.app=create_app(self.config)
        result=self.app.test_cli_runner().invoke(args=['init-db'])
        self.assertEqual(result.exit_code,0,result.output)
        with self.app.app_context():
            clock.ensure()

    def tearDown(self):
        with self.app.app_context():
            task=self.app.extensions.get('kaspi_xml_task')
            if task:
                task.join(timeout=3)
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def response(self,status=200,header=None):
        return Mock(status_code=status,headers={} if header is None else {'Retry-After':header},json=Mock(return_value={'data':[]}))

    def queue(self,_days):
        from kaspibot.services.seller_sync import start_run
        return start_run()

    def test_retry_after_seconds_http_date_and_invalid_values(self):
        from datetime import timezone
        at=clock.now()
        self.assertEqual(clock.retry_after('3600',at),at+timedelta(hours=1))
        expected=(at+timedelta(hours=2)).replace(microsecond=0)
        self.assertEqual(clock.retry_after(format_datetime(expected.replace(tzinfo=timezone.utc)),at),expected)
        for value in [None,'invalid','-1','1e99','9'*99]:
            self.assertIsNone(clock.retry_after(value,at))

    def test_429_is_one_request_and_both_manual_and_restart_respect_deadline(self):
        with self.app.app_context(),patch('kaspibot.services.kaspi_transport.requests.get',return_value=self.response(429,'3600')) as http:
            with self.assertRaises(KaspiAPIError):
                get_orders()
            self.assertEqual(http.call_count,1)
            before=clock.get('api')['next_attempt_at']
            with self.assertRaises(clock.Deferred):
                get_orders()
            with self.assertRaises(clock.Deferred):
                from kaspibot.services.sync_job import launch
                launch(14)
            self.assertEqual(http.call_count,1)
        restarted=create_app(self.config)
        with restarted.app_context(),patch('kaspibot.services.automatic_sync.launch') as launch:
            self.assertEqual(clock.get('api')['next_attempt_at'],before)
            self.assertFalse(tick_api('restarted'))
            launch.assert_not_called()
            db.session.remove()
            db.engine.dispose()

    def test_exponential_jitter_is_capped_and_success_resets_it(self):
        with self.app.app_context(),patch('kaspibot.services.sync_schedule.random.uniform',side_effect=lambda a,b:b):
            initial=clock.now()
            first=clock.defer('xml')
            second=clock.defer('xml')
            self.assertGreater(second,first)
            for _ in range(8):
                clock.defer('xml')
            self.assertLessEqual((clock.get('xml')['next_attempt_at']-initial).total_seconds(),21601)
            clock.finish('xml','imported',360)
            self.assertEqual(clock.get('xml')['failures'],0)
            self.assertGreater(clock.get('xml')['next_attempt_at'],clock.now()+timedelta(minutes=29))
            self.assertLess(clock.get('xml')['next_attempt_at'],clock.now()+timedelta(minutes=31))
            self.assertIsNone(clock.get('xml')['cooldown_until'])

    def test_authorization_does_not_retry_and_transient_request_retries_only_failure(self):
        for status in [401,403]:
            with self.subTest(status=status),self.app.app_context(),patch('kaspibot.services.kaspi_transport.requests.get',return_value=self.response(status)) as http:
                with self.assertRaises(KaspiAPIError) as error:
                    get_orders()
                self.assertEqual(error.exception.code,'needs_login')
                self.assertEqual(http.call_count,1)
        for failure in [self.response(500),self.response(503),requests.Timeout(),requests.ConnectionError()]:
            with self.subTest(failure=type(failure).__name__),self.app.app_context(),patch('kaspibot.services.kaspi_transport.time.sleep') as sleep,patch('kaspibot.services.kaspi_transport.requests.get',side_effect=[failure,self.response()]) as http:
                self.assertEqual(get_orders(),{'data':[]})
                self.assertEqual(http.call_count,2)
                sleep.assert_called_once()

    def test_tls_failure_is_not_retried_or_disabled(self):
        with self.app.app_context(),patch('kaspibot.services.kaspi_transport.requests.get',side_effect=requests.exceptions.SSLError('private URL')) as http:
            with self.assertRaises(KaspiAPIError) as error:
                get_orders()
            self.assertEqual(error.exception.code,'tls_error')
            self.assertEqual(http.call_count,1)
            self.assertNotIn('private',str(error.exception))

    def test_expired_cooldown_allows_request_but_boot_never_bypasses_future_xml_deadline(self):
        with self.app.app_context(),patch('kaspibot.services.automatic_sync.download_xml') as read,patch('kaspibot.services.automatic_sync.launch',side_effect=self.queue):
            clock.put('xml',next_attempt_at=clock.now()+timedelta(hours=1))
            tick('boot',boot=True)
            read.assert_not_called()
            clock.put('api',cooldown_until=clock.now()-timedelta(seconds=1))
            with patch('kaspibot.services.kaspi_transport.requests.get',return_value=self.response()):
                self.assertEqual(get_orders(),{'data':[]})

    def test_atomic_source_claim_and_independent_xml_lease(self):
        barrier=threading.Barrier(2)
        results=[]
        def claim(owner):
            with self.app.app_context():
                barrier.wait(timeout=3)
                results.append(clock.claim('xml',owner))
        threads=[threading.Thread(target=claim,args=(str(index),)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(sorted(results),[False,True])
        with self.app.app_context(),patch('kaspibot.services.automatic_sync.launch',side_effect=self.queue) as launch:
            self.assertTrue(tick_api('api'))
            launch.assert_called_once()

    def test_slow_xml_does_not_block_api_dispatch(self):
        entered=threading.Event()
        release=threading.Event()
        def export(*_args,**_kwargs):
            entered.set()
            self.assertTrue(release.wait(timeout=6))
            return xml()
        with self.app.app_context(),patch('kaspibot.services.automatic_sync.download_xml',side_effect=export),patch('kaspibot.services.automatic_sync.launch',side_effect=self.queue) as launch:
            try:
                tick('owner',background=True)
                self.assertTrue(entered.wait(timeout=3))
                row=KaspiSyncRun.query.one()
                row.active_slot=None
                row.status='success'
                db.session.commit()
                clock.put('api',next_attempt_at=clock.now()-timedelta(seconds=1))
                self.assertTrue(tick_api('api-again'))
                self.assertEqual(launch.call_count,2)
            finally:
                release.set()
                self.app.extensions['kaspi_xml_task'].join(timeout=5)

    def test_xml_429_preserves_catalog_and_has_independent_api_deadline(self):
        from kaspibot.services.seller_catalog import import_xml
        with self.app.app_context():
            import_xml(xml())
            with patch('kaspibot.services.automatic_sync.download_xml',side_effect=ExportError('limit','rate_limited','7200')):
                tick_xml('xml')
            self.assertGreater(clock.get('xml')['next_attempt_at'],clock.now()+timedelta(minutes=119))
            self.assertEqual(clock.get('xml')['state'],'rate_limited')
            self.assertEqual(Product.query.count(),1)
            with patch('kaspibot.services.automatic_sync.launch',side_effect=self.queue):
                self.assertTrue(tick_api('api'))
            self.assertEqual(clock.get('xml')['failures'],1)

    def test_safe_sku_repair_keeps_warehouse_history_and_is_idempotent(self):
        with self.app.app_context():
            original=Product(name='Тестовый товар',sku='OLD',kaspi_product_id='PUBLIC',purchase_price=700,stock_qty=9)
            db.session.add(original)
            db.session.flush()
            raw=dict(source='kaspi_api',mapping_status='conflict',master_product={'attributes':{'code':'PUBLIC','name':'Тестовый товар'}},merchant_product={'attributes':{'code':'NEW','name':'Тестовый товар'}})
            for index,history in enumerate([False,True]):
                db.session.add(KaspiOrderSync(external_key=str(index),order_code=str(index),product_id=original.id,
                    raw_json=json.dumps(raw),is_sale_created=history))
            db.session.commit()
            result=repair_saved(apply=True)
            self.assertEqual(result['resolved'],1)
            self.assertEqual(result['protected_history'],1)
            created=Product.query.filter_by(sku='NEW').one()
            self.assertEqual(created.name,'Тестовый товар [SKU NEW]')
            self.assertEqual(created.inventory_source,'unknown')
            self.assertEqual(created.stock_qty,0)
            self.assertEqual((original.stock_qty,original.purchase_price),(9,700))
            self.assertIsNone(created.kaspi_product_id)
            self.assertEqual(KaspiOrderSync.query.filter_by(external_key='1').one().product_id,original.id)
            self.assertEqual(repair_saved(apply=True).get('resolved',0),0)

    def test_error_details_are_anonymized_and_partial_is_not_success(self):
        with self.app.app_context():
            errors=public_errors([{'code':'order_failed','order_code':'BUYER-PRIVATE','message':'https://private/path?token=secret','http_status':503,'kind':'temporary_error'}])
            self.assertNotIn('PRIVATE',str(errors))
            self.assertNotIn('secret',str(errors))
            self.assertEqual(errors[0]['http_status'],503)
            clock.finish('api','partial',3,1)
            self.assertIsNone(clock.get('api')['last_success_at'])
            self.assertIsNotNone(clock.get('api')['last_data_at'])
            self.assertEqual(summary()['primary'],{})

    def test_rate_limit_after_a_committed_order_keeps_partial_data_and_deadline(self):
        from test_kaspi_sync import order,entries
        from kaspibot.services.seller_sync import synchronize
        from kaspibot.services.active_orders_import import sync_order_and_entries_to_db
        committed=threading.Event()
        first=order()
        second=order()
        second['id']='order-2'
        second['attributes']['code']='CODE-2'
        def listing(**params):
            return {'data':[first,second] if params['state']=='ARCHIVE' else [],'meta':{'pageCount':1 if params['state']=='ARCHIVE' else 0}}
        def read(ident):
            if ident=='order-2':
                self.assertTrue(committed.wait(timeout=3))
                clock.defer('api',header='3600')
                raise KaspiAPIError('limit',429)
            return entries()
        def save(*args,**kwargs):
            result=sync_order_and_entries_to_db(*args,**kwargs)
            committed.set()
            return result
        with self.app.app_context(),patch('kaspibot.services.seller_sync.get_orders',side_effect=listing),patch('kaspibot.services.seller_sync.get_order_entries',side_effect=read),patch('kaspibot.services.seller_sync.sync_order_and_entries_to_db',side_effect=save),patch('kaspibot.services.order_payload.get_order_entry_product',return_value={'id':'M','attributes':{'name':'Tea','code':'PUBLIC'}}),patch('kaspibot.services.order_payload.get_merchant_product',return_value={'id':'S','attributes':{'name':'Tea','code':'SKU'}}):
            result=synchronize()
            self.assertTrue(result['partial'])
            self.assertFalse(result['ok'])
            self.assertEqual(result['orders'],1)
            self.assertEqual(KaspiOrderSync.query.count(),1)
            self.assertEqual(KaspiSyncRun.query.one().status,'rate_limited')
            self.assertIsNone(KaspiSyncRun.query.one().active_slot)
            self.assertEqual(clock.get('api')['records'],1)
            self.assertIsNotNone(clock.get('api')['last_data_at'])
            self.assertIsNone(clock.get('api')['last_success_at'])

    def test_dead_api_slot_recovers_from_persisted_heartbeat(self):
        from kaspibot.services.seller_sync import start_run
        with self.app.app_context():
            run=start_run()
            row=db.session.get(KaspiSyncRun,run)
            row.started_at=clock.now()-timedelta(minutes=20)
            db.session.commit()
            clock.put('api',run_id=run,heartbeat_at=clock.now()-timedelta(minutes=15),next_attempt_at=clock.now()-timedelta(minutes=1))
            with patch('kaspibot.services.automatic_sync.launch',side_effect=self.queue) as launch:
                self.assertTrue(tick_api('recovery'))
                launch.assert_called_once()
            db.session.expire_all()
            self.assertEqual(db.session.get(KaspiSyncRun,run).status,'interrupted')

    def test_old_schema_deadline_is_seeded_without_rewriting_history(self):
        from kaspibot.models import KaspiAutoSyncState,SyncSchedule
        with self.app.app_context():
            deadline=clock.now()+timedelta(hours=2)
            db.session.add(KaspiAutoSyncState(id=1,next_attempt_at=deadline,catalog_status='failed',catalog_message='HTTP 429'))
            db.session.commit()
            SyncSchedule.__table__.drop(db.engine)
            self.app.extensions.pop('sync_schedule_ready',None)
            clock.ensure()
            self.assertEqual(clock.get('xml')['next_attempt_at'],deadline)
            self.assertEqual(clock.get('xml')['cooldown_until'],deadline)
            with patch('kaspibot.services.automatic_sync.download_xml') as read:
                self.assertFalse(tick_xml('boot'))
                read.assert_not_called()

    def test_manual_xml_lease_can_run_early_but_never_bypasses_server_cooldown(self):
        with self.app.app_context():
            clock.put('xml',next_attempt_at=clock.now()+timedelta(hours=1))
            self.assertTrue(clock.claim('xml','login',manual=True))
            self.assertFalse(clock.claim('xml','other-login',manual=True))
            clock.release('xml','login')
            clock.defer('xml',header='7200')
            self.assertFalse(clock.claim('xml','login',manual=True))

    def test_api_finish_metadata_is_in_the_same_transaction_as_slot_release(self):
        with self.app.app_context():
            clock.put('api',state='running')
            clock.finish('api','success',3,session=db.session)
            db.session.rollback()
            self.assertEqual(clock.get('api')['state'],'running')
            clock.finish('api','success',3,session=db.session)
            db.session.commit()
            self.assertEqual(clock.get('api')['state'],'success')


class BrowserLifecycleTests(unittest.TestCase):
    def test_expected_close_in_drained_route_is_ignored_but_real_failure_propagates(self):
        for closing in [True,False]:
            with self.subTest(closing=closing),tempfile.TemporaryDirectory() as directory:
                session=Path(directory)/'session.json'
                session.write_text('{}')
                runtime=Mock()
                context=runtime.chromium.launch.return_value.new_context.return_value
                context.storage_state.return_value={}
                handler=[]
                context.route.side_effect=lambda _pattern,callback:handler.append(callback)
                def callback():
                    route=Mock(request=SimpleNamespace(method='GET',url='https://kaspi.kz/',resource_type='document'))
                    route.continue_.side_effect=BrowserError('Target page, context or browser has been closed')
                    handler[0](route)
                context.unroute_all.side_effect=lambda **_kwargs:callback() if closing else None
                def export(*_args,**_kwargs):
                    if not closing:
                        callback()
                    return xml()
                with patch('kaspibot.services.cabinet_export.sync_playwright') as manager,patch('kaspibot.services.cabinet_export.export_from_page',side_effect=export):
                    manager.return_value.__enter__.return_value=runtime
                    if closing:
                        self.assertEqual(_fetch_export_once(session,'123456'),xml())
                    else:
                        with self.assertRaises(ExportError) as error:
                            _fetch_export_once(session,'123456')
                        self.assertEqual(error.exception.code,'interrupted')
                context.unroute_all.assert_called_once_with(behavior='wait')
                context.close.assert_called_once()
                runtime.chromium.launch.return_value.close.assert_called_once()
