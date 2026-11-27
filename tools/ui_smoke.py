"""Browser smoke on a disposable shop: menu, mobile layout, manual and live XML refresh."""
# ruff: noqa: E402
import argparse
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from app import create_app
from kaspibot.models import db
from kaspibot.services.seller_catalog import import_xml


def run(screenshot, channel=None):
    with tempfile.TemporaryDirectory() as directory:
        app=create_app(dict(TESTING=True,SECRET_KEY='browser-smoke-only',DATABASE=str(Path(directory)/'app.db')))
        runner=app.test_cli_runner()
        for command in (['init-db'],['create-user','--role','admin','--password','browser-test-only','admin'],
                        ['add-product','UI test tea','UI-1','2500.50','12']):
            result=runner.invoke(args=command)
            assert result.exit_code==0,(result.output,result.exception)
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
