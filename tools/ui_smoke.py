"""Browser smoke on a disposable shop: menu, mobile layout, manual and live XML refresh."""
# ruff: noqa: E402
import argparse
import json
from datetime import datetime,timezone,timedelta
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from app import create_app
from kaspibot.models import db, KaspiOrderSync
from kaspibot.services.seller_catalog import import_xml


def run(screenshot, channel=None):
    with tempfile.TemporaryDirectory() as directory:
        app=create_app(dict(TESTING=True,SECRET_KEY='browser-smoke-only',DATABASE=str(Path(directory)/'app.db')))
        runner=app.test_cli_runner()
        for command in (['init-db'],['create-user','--role','admin','--password','browser-test-only','admin'],
                        ['add-product','UI test tea','UI-1','2500.50','12']):
            result=runner.invoke(args=command)
            assert result.exit_code==0,(result.output,result.exception)
        with app.app_context():
            for index in range(3):
                db.session.add(KaspiOrderSync(external_key=f'layout-{index}',order_code=f'UI-ORDER-{index}',
                    customer_name='Тестовый покупатель' if index else 'ПокупательБезПробелов'*12,
                    customer_phone='+7(000)000-00-00',
                    title='Тестовый товар · чёрный размер L' if index else 'ОченьДлинноеНазваниеТовараБезПробелов'*7,
                    qty=1,sale_sum=11547,state='ARCHIVE',status_text='COMPLETED',raw_json='{"source":"kaspi_api"}'))
            db.session.commit()
        server=make_server('127.0.0.1',0,app,threaded=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with sync_playwright() as runtime:
                browser=runtime.chromium.launch(headless=True,channel=channel)
                context=browser.new_context(viewport={'width':1440,'height':1000})
                page=context.new_page()
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                base=f'http://127.0.0.1:{server.server_port}'
                page.goto(base+'/management/login')
                page.locator('[name=username]').fill('admin')
                page.locator('[name=password]').fill('browser-test-only')
                page.locator('button[type=submit]').last.click()
                page.wait_for_url(base+'/')
                page.locator('#menuToggle').click()
                assert page.locator('#appSidebar').is_visible()
                page.keyboard.press('Escape')
                assert page.locator('#appSidebar').is_hidden()
                assert page.locator('#menuToggle').evaluate('(el)=>el===document.activeElement')
                page.goto(base+'/products')
                assert page.get_by_text('UI test tea',exact=True).count()>0
                page.goto(base+'/management/products/1/price')
                page.locator('[name=price]').fill('2700.50')
                page.locator('button[type=submit]').last.click()
                page.wait_for_url('**/management/products/1')
                assert page.get_by_text('2700.50 ₸',exact=True).count()>0
                content='''<kaspi_catalog date="2026-11-27 17:00"><merchantid>browser-fixture</merchantid><offers>
                <offer sku="UI-1"><model>UI test tea</model><brand>Fixture</brand><availabilities><availability available="yes" storeId="PP1" stockCount="49" preOrder="16"/></availabilities><price>3900</price></offer>
                <offer sku="UI-XML"><model>Тестовый товар · размер L</model><brand>Тестовая марка</brand><availabilities><availability available="yes" storeId="PP1" stockCount="1" preOrder="2"/></availabilities><price>4999</price></offer>
                </offers></kaspi_catalog>'''.encode()
                catalog=Path(directory)/'seller.xml'
                catalog.write_bytes(content)
                page.goto(base+'/products/seller-catalog')
                page.locator('[name=file]').set_input_files(str(catalog))
                page.get_by_role('button',name='Импортировать XML').click()
                page.wait_for_url('**/products/seller-catalog')
                assert '49 шт.' in page.locator('tbody').inner_text()
                page.goto(base+'/products?q=UI-XML')
                panel=page.locator('.seller-panel')
                assert '4999 ₸' in panel.inner_text()
                assert '1 шт.' in panel.inner_text() and '2 дн.' in panel.inner_text()
                assert 'нет данных API' not in page.locator('#productResults').inner_text()
                page.locator('.inventory-details summary').click()
                assert 'Не подтверждён' in page.locator('.inventory-details').inner_text()
                before=page.locator('#productResults').get_attribute('data-catalog-version')
                with app.app_context():
                    import_xml(content.replace(b'17:00',b'17:05').replace(b'4999',b'5499'))
                # Real timer: no reload, button click or injected refresh function.
                page.get_by_text('5499 ₸',exact=True).wait_for(timeout=45000)
                assert page.locator('#productResults').get_attribute('data-catalog-version')!=before
                assert page.locator('.inventory-details').get_attribute('open') is not None
                page.locator('.inventory-details summary').click()
                page.locator('#menuToggle').click()
                assert page.locator('#appSidebar').is_visible()
                screenshot=Path(screenshot)
                screenshot.parent.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(screenshot),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                page.locator('#sidebarBackdrop').click(position={'x':380,'y':100})
                assert page.locator('#appSidebar').is_hidden()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.locator('#menuToggle').click()
                assert page.locator('#appSidebar').is_visible()
                page.keyboard.press('Escape')
                assert page.locator('#appSidebar').is_hidden()
                mobile=screenshot.with_name(screenshot.stem+'-mobile'+screenshot.suffix)
                page.screenshot(path=str(mobile),full_page=True)
                # Orders must remain contained while the navigation width changes.
                page.set_viewport_size({'width':1440,'height':1000})
                page.goto(base+'/orders')
                assert page.locator('.orders-table tbody tr').count()==3
                if page.locator('#appSidebar').is_hidden():
                    page.locator('#menuToggle').click()
                def contained():
                    assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth'), 'Page overflow'
                    assert page.locator('.orders-table').evaluate("el=>[...el.querySelectorAll('th,td')].every(cell=>cell.scrollWidth<=cell.clientWidth+1)"), 'Cell text overflow'
                    outer=page.locator('.content-card').bounding_box()
                    inner=page.locator('.orders-table-wrap').bounding_box()
                    assert inner['x']>=outer['x'] and inner['x']+inner['width']<=outer['x']+outer['width']+1
                def drag_edge(target):
                    box=page.locator('#sidebarResizer').bounding_box()
                    page.mouse.move(box['x']+box['width']/2,box['y']+160)
                    page.mouse.down()
                    destination=box['x']+box['width']/2+target-sidebar_width()
                    page.mouse.move(destination,box['y']+160,steps=12)
                    page.mouse.up()
                def sidebar_width():
                    return round(page.locator('#appSidebar').bounding_box()['width'])
                initial=page.locator('.content-card').bounding_box()['width']
                drag_edge(384)
                assert sidebar_width()==384,sidebar_width()
                assert page.locator('.content-card').bounding_box()['width']<initial-100
                assert page.evaluate("getComputedStyle(document.body).paddingLeft")=='384px'
                contained()
                page.reload()
                assert page.locator('#appSidebar').is_visible() and sidebar_width()==384
                assert page.locator('#sidebarResizer').get_attribute('aria-valuenow')=='384'
                page.keyboard.press('Escape')
                assert page.evaluate("getComputedStyle(document.body).paddingLeft")=='0px'
                contained()
                page.locator('#menuToggle').click()
                drag_edge(620)
                assert sidebar_width()==480
                contained()
                assert page.locator('.orders-table-wrap').evaluate('el=>el.scrollWidth>el.clientWidth')
                drag_edge(100)
                assert sidebar_width()==240
                page.locator('#sidebarResizer').dblclick(position={'x':5,'y':150})
                assert sidebar_width()==272
                page.locator('#sidebarResizer').focus()
                page.keyboard.press('End')
                assert sidebar_width()==480
                page.keyboard.press('ArrowLeft')
                assert sidebar_width()==464
                page.keyboard.press('ArrowRight')
                assert sidebar_width()==480
                page.keyboard.press('Home')
                assert sidebar_width()==240
                page.keyboard.press('End')
                for viewport in [1920,1536,1280,1200,1024]:
                    page.set_viewport_size({'width':viewport,'height':1000})
                    contained()
                page.set_viewport_size({'width':1536,'height':1000})
                orders=screenshot.with_name(screenshot.stem+'-orders'+screenshot.suffix)
                page.screenshot(path=str(orders),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                page.goto(base+'/orders')
                contained()
                assert page.locator('#sidebarResizer').is_hidden()
                page.locator('#menuToggle').click()
                assert sidebar_width()==350
                page.keyboard.press('Escape')
                wrap=page.locator('.orders-table-wrap')
                wrap.evaluate('el=>el.scrollLeft=el.scrollWidth')
                assert wrap.evaluate('el=>el.scrollLeft>0')
                wrap.evaluate('el=>el.scrollLeft=0')
                orders_mobile=screenshot.with_name(screenshot.stem+'-orders-mobile'+screenshot.suffix)
                page.screenshot(path=str(orders_mobile),full_page=True)
                # Corrupt browser preferences are bounded as well.
                page.evaluate("localStorage.setItem('ksm.navWidth','999999')")
                page.set_viewport_size({'width':1440,'height':1000})
                page.reload()
                assert sidebar_width()==480
                contained()
                state=page.request.get(base+'/api/kaspi/status').json()
                channels=state['automatic']['channels']
                channels['xml'].update(state='rate_limited',next_attempt_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),interval_seconds=1800,using_saved_data=True,message='Ограничение Kaspi (HTTP 429).')
                channels['api'].update(state='partial',interval_seconds=300)
                state['last_run']={'error_details':[{'code':'unmatched_product','message':'Конфликт SKU; склад не изменён.','kind':'mapping_conflict'}]}
                page.route('**/api/kaspi/status',lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
                page.reload()
                page.locator('#syncXmlChip').filter(has_text='429').wait_for()
                assert 'частично успешно' in page.locator('#syncApiChip').inner_text()
                page.locator('.sync-panel details summary').first.click()
                detail=page.locator('#syncDetails').inner_text()
                assert '5 мин' in detail and '30 мин' in detail
                assert 'Сохранённый XML' in page.locator('#syncNotice').inner_text()
                assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
                status_preview=screenshot.with_name(screenshot.stem+'-sync-state'+screenshot.suffix)
                page.screenshot(path=str(status_preview))
                print('Sync panel PASS — API 5 min, XML 30 min, rate limit deadline, partial API, saved XML and safe error details.',flush=True)
                print('Orders layout PASS — long unbroken text, local table scroll, drag limits, live page resizing, persisted width, keyboard, 1920/1536/1280/1200/1024/390px.',flush=True)
                print('Orders screenshots:',orders,orders_mobile,flush=True)
                page.locator('#language-kk').click()
                assert page.locator('html').get_attribute('lang')=='kk'
                assert page.locator('#ordersHeading').inner_text()=='Kaspi тапсырыстары'
                page.locator('#syncXmlChip').filter(has_text='429').wait_for()
                assert 'ішінара сәтті' in page.locator('#syncApiChip').inner_text()
                page.goto(base+'/products?q=UI-XML')
                assert page.locator('.page-heading h1').inner_text()=='Тауарлар мен қалдықтар'
                assert 'Тестовый товар · размер L' in page.locator('.algatop-title').inner_text()
                page.reload()
                assert page.locator('html').get_attribute('lang')=='kk'
                page.locator('#menuToggle').click()
                if page.locator('#appSidebar').is_hidden():
                    page.locator('#menuToggle').click()
                page.screenshot(path=str(screenshot.with_name(screenshot.stem+'-kk'+screenshot.suffix)))
                page.set_viewport_size({'width':390,'height':844})
                page.keyboard.press('Escape')
                assert page.evaluate('document.documentElement.scrollWidth<=document.documentElement.clientWidth')
                page.screenshot(path=str(screenshot.with_name(screenshot.stem+'-kk-mobile'+screenshot.suffix)))
                page.locator('#language-ru').click()
                assert page.locator('html').get_attribute('lang')=='ru'
                assert 'q=UI-XML' in page.url
                assert page.locator('.page-heading h1').inner_text()=='Товары и остатки'
                print('RU/KK PASS — persisted language, dynamic sync labels, preserved query/product data, desktop and mobile.',flush=True)
                assert not errors,errors
                print('Chrome',browser.version,': PASS — login, role navigation, Escape/focus, manual XML import, real timer refresh, preserved details, 390px layout; no JS errors.',flush=True)
                print('Screenshots:',screenshot,mobile,flush=True)
                browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            with app.app_context():
                db.session.remove()
                db.engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screenshot',type=Path,default=Path('dist/ui-smoke.png'))
    parser.add_argument('--browser-channel',default=None,help='Use chrome for the installed Google Chrome.')
    args=parser.parse_args()
    run(args.screenshot,args.browser_channel)
