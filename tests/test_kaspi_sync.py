import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import requests
from app import create_app
from kaspibot.models import db, Product, ProductAlias, KaspiOrderSync, StockMovement, KaspiSyncRun
from kaspibot.services.kaspi_api import get_orders, KaspiAPIError
from kaspibot.services.active_orders_import import sync_order_and_entries_to_db
from kaspibot.services.order_payload import extract_lines
from kaspibot.services.pagination import collect_pages
from kaspibot.services.seller_sync import synchronize
from kaspibot.settings import local_settings


def order(state='ARCHIVE',status='COMPLETED'):
    return dict(id='order-1',attributes=dict(code='CODE-1',state=state,status=status,
                creationDate=1760000000000,customer=dict(firstName='Fixture',lastName='Customer')))


def entries():
    return {'data':[{'id':'entry-1','attributes':{'quantity':2,'basePrice':1250.5}}]}


class SellerSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'app.db'
        self.app=create_app(dict(TESTING=True,SECRET_KEY='unit-test-only',DATABASE=str(self.path),KASPI_API_TOKEN='unit-token'))
        r=self.app.test_cli_runner().invoke(args=['init-db'])
        self.assertEqual(r.exit_code,0,(r.output,r.exception))
        self.master=patch('kaspibot.services.order_payload.get_order_entry_product',return_value={
            'id':'opaque-master-id','attributes':{'name':'Tea','code':'123456','category':'Food'}})
        self.merchant=patch('kaspibot.services.order_payload.get_merchant_product',return_value={
            'id':'opaque-merchant-id','attributes':{'name':'Tea','code':'SELLER-TEA'}})
        self.master.start()
        self.merchant.start()

    def tearDown(self):
        self.master.stop()
        self.merchant.stop()
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def test_real_fields_base_price_fractional_amount_and_no_invented_defaults(self):
        with self.app.app_context():
            line=extract_lines(order(),entries())[0]
            self.assertEqual((line['qty'],line['sale_sum'],line['merchant_sku'],line['kaspi_code']),
                             (2,2501,'SELLER-TEA','123456'))
            self.assertEqual(line['customer_name'],'Fixture Customer')
            self.assertEqual(line['raw_json']['source'],'kaspi_api')
            for attributes in ({'quantity':0,'totalPrice':100},{'quantity':2},{'totalPrice':100}):
                with self.assertRaises(ValueError):
                    extract_lines(order(),{'data':[{'id':'E','attributes':attributes}]})

    def test_match_existing_name_fill_real_sku_preserve_stock_and_idempotence(self):
        with self.app.app_context():
            p=Product(name='Tea',purchase_price=500,stock_qty=10,onway_qty=4)
            db.session.add(p)
            db.session.commit()
            before=db.session.query(StockMovement).count()
            for _ in range(2):
                result=sync_order_and_entries_to_db(order(),entries())
                self.assertFalse(result['movements_applied'])
            self.assertEqual(Product.query.count(),1)
            self.assertEqual((p.stock_qty,p.onway_qty,p.purchase_price,p.sku),(10,4,500,'SELLER-TEA'))
            self.assertEqual(KaspiOrderSync.query.count(),1)
            self.assertEqual(KaspiOrderSync.query.one().sale_sum,2501)
            self.assertEqual(StockMovement.query.count(),before)

    def test_new_real_card_has_unknown_stock_not_fake_available_quantity(self):
        with self.app.app_context():
            sync_order_and_entries_to_db(order(),entries())
            p=Product.query.one()
            self.assertEqual(p.inventory_source,'unknown')
            self.assertEqual(p.kaspi_product_id,'123456')
            self.assertEqual(p.current_price,0)  # no claim that historical sale price is current offer price
            row=KaspiOrderSync.query.one()
            self.assertEqual(row.product_id,p.id)

    def test_conflicting_alias_does_not_move_old_data_to_another_sku(self):
        with self.app.app_context():
            p=Product(name='Tea',sku='DIFFERENT',purchase_price=500,stock_qty=7)
            db.session.add(p)
            db.session.flush()
            db.session.add(ProductAlias(product_id=p.id,alias='tea'))
            db.session.commit()
            result=sync_order_and_entries_to_db(order(),entries())
            self.assertEqual(result['unmatched'],1)
            row=KaspiOrderSync.query.one()
            self.assertIsNone(row.product_id)
            self.assertEqual(row.sale_sum,2501)
            self.assertEqual(json.loads(row.raw_json)['mapping_status'],'conflict')
            self.assertEqual(p.sku,'DIFFERENT')
            self.assertEqual(p.stock_qty,7)

    def test_failed_api_does_not_claim_success_or_touch_products(self):
        with self.app.app_context(), patch('kaspibot.services.seller_sync.get_orders',side_effect=KaspiAPIError('timeout')):
            result=synchronize()
            self.assertFalse(result['ok'])
            self.assertEqual(KaspiSyncRun.query.one().status,'failed')
            self.assertIsNone(KaspiSyncRun.query.one().active_slot)
            self.assertEqual(Product.query.count(),0)

    def test_frozen_windows_all_six_states_and_completed_orders_are_visible(self):
        def fetch(**params):
            return {'data':[order()] if params['state']=='ARCHIVE' else [],'meta':{'pageCount':1 if params['state']=='ARCHIVE' else 0}}
        with self.app.app_context(), patch('kaspibot.services.seller_sync.get_orders',side_effect=fetch) as get, \
                patch('kaspibot.services.seller_sync.get_order_entries',return_value=entries()):
            result=synchronize(days=30)
            self.assertTrue(result['ok'],result)
            self.assertEqual(get.call_count,18)
            self.assertTrue(all(call.kwargs['end_ms']-call.kwargs['start_ms']<=14*86400000 for call in get.call_args_list))
            self.assertEqual(result['orders'],1)
            client=self.app.test_client()
            self.app.test_cli_runner().invoke(args=['create-user','--role','admin','--password','test-only','admin'])
            client.post('/management/login',data={'username':'admin','password':'test-only'})
            self.assertIn('CODE-1',client.get('/orders').get_data(as_text=True))
            self.assertIn('2501',client.get('/report').get_data(as_text=True))

    def test_truncated_and_repeated_pages_are_errors(self):
        with self.assertRaises(ValueError):
            collect_pages(lambda **k: {'data':[],'meta':{'pageCount':2}})
        with self.assertRaises(ValueError):
            collect_pages(lambda **k: {'data':[{'id':'same'}],'meta':{'pageCount':2}})

    def test_safe_transport_errors_without_customer_body_or_redirect(self):
        response=Mock(status_code=403,text='secret customer body')
        with self.app.app_context(),patch('kaspibot.services.kaspi_transport.requests.get',return_value=response) as get:
            with self.assertRaises(KaspiAPIError) as error:
                get_orders()
            self.assertNotIn('secret',str(error.exception))
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
        with self.app.app_context(),patch('kaspibot.services.kaspi_transport.requests.get',side_effect=requests.Timeout):
            with self.assertRaises(KaspiAPIError):
                get_orders()

    def test_env_is_loaded_and_environment_takes_precedence(self):
        root=Path(self.temp.name)
        (root/'.env').write_text('KASPI_API_TOKEN="file-token"\nUNRELATED=ignored\n',encoding='utf-8')
        with patch.dict('os.environ',{'KASPI_API_TOKEN':'environment-token'}):
            values=local_settings(root)
        self.assertEqual(values['KASPI_API_TOKEN'],'environment-token')
        self.assertNotIn('UNRELATED',values)

    def test_queue_lock_and_failed_worker_are_reported(self):
        from kaspibot.services.sync_job import launch
        from kaspibot.services.seller_sync import start_run
        with self.app.app_context():
            run_id=start_run()
            with self.assertRaises(ValueError):
                start_run()
            row=db.session.get(KaspiSyncRun,run_id)
            row.active_slot=None
            row.status='failed'
            db.session.commit()
            with patch('kaspibot.services.sync_job.subprocess.Popen',side_effect=OSError):
                with self.assertRaises(ValueError):
                    launch(14)
            self.assertEqual(KaspiSyncRun.query.order_by(KaspiSyncRun.id.desc()).first().status,'failed')

    def test_legacy_rows_are_preserved_but_not_presented_as_verified_live_orders(self):
        with self.app.app_context():
            db.session.add(KaspiOrderSync(external_key='OLD::E',order_code='OLD',entry_id='E',
                                         raw_json=json.dumps({'order':order()})))
            db.session.commit()
            self.app.test_cli_runner().invoke(args=['create-user','--role','admin','--password','test-only','admin'])
        client=self.app.test_client()
        client.post('/management/login',data={'username':'admin','password':'test-only'})
        self.assertNotIn('>OLD<',client.get('/orders').get_data(as_text=True))
        self.assertIn('OLD',client.get('/orders?bucket=legacy').get_data(as_text=True))
        self.assertNotIn('>OLD<',client.get('/report').get_data(as_text=True))
