"""Real Chromium smoke against a disposable database, never the user's app.db."""
import argparse
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from app import create_app
from kaspibot.models import db


def run(screenshot):
    with tempfile.TemporaryDirectory() as directory:
        print('Creating disposable browser fixture',flush=True)
        app=create_app(dict(TESTING=True,SECRET_KEY='browser-smoke-only',DATABASE=str(Path(directory)/'app.db')))
        runner=app.test_cli_runner()
        for command in (['init-db'],['create-user','--role','admin','--password','browser-test-only','admin'],
                        ['add-product','UI test tea','UI-1','2500.50','12']):
            print('CLI '+command[0],flush=True)
            result=runner.invoke(args=command)
            assert result.exit_code==0,(result.output,result.exception)
        print('Starting local HTTP server',flush=True)
        server=make_server('127.0.0.1',0,app,threaded=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                print('Launching Chromium',flush=True)
                browser=p.chromium.launch(headless=True)
                page=browser.new_page(viewport=dict(width=1440,height=1000))
                page.goto(f'http://127.0.0.1:{server.server_port}/management/login')
                page.locator('[name=username]').fill('admin')
                page.locator('[name=password]').fill('browser-test-only')
                page.locator('button[type=submit]').last.click()
                page.wait_for_url(f'http://127.0.0.1:{server.server_port}/')
                page.goto(f'http://127.0.0.1:{server.server_port}/products')
                assert page.get_by_text('UI test tea',exact=True).count()>0
                page.goto(f'http://127.0.0.1:{server.server_port}/management/products/1/price')
                page.locator('[name=price]').fill('2700.50')
                page.locator('button[type=submit]').last.click()
                page.wait_for_url('**/management/products/1')
                assert page.get_by_text('2700.50 ₸',exact=True).count()>0
                page.goto(f'http://127.0.0.1:{server.server_port}/products')
                catalog=Path(directory)/'seller.xml'
                catalog.write_text('<kaspi_catalog date="2026-11-19 22:07"><merchantid>browser-fixture</merchantid><offers><offer sku="UI-1"><model>UI test tea</model><brand>Fixture</brand><availabilities><availability available="yes" storeId="PP1" stockCount="49.0" preOrder="16"/></availabilities><price>3900</price></offer></offers></kaspi_catalog>',encoding='utf-8')
                page.goto(f'http://127.0.0.1:{server.server_port}/products/seller-catalog')
                page.locator('[name=file]').set_input_files(str(catalog))
                page.get_by_role('button',name='Импортировать XML').click()
                page.wait_for_url('**/products/seller-catalog')
                assert 'UI test tea' in page.locator('tbody').inner_text()
                assert 'предзаказ 16 дн.' in page.locator('tbody').inner_text()
                assert '49 шт.' in page.locator('tbody').inner_text()
                assert page.request.get(f'http://127.0.0.1:{server.server_port}/api/kaspi/catalog').json()['offers'][0]['stocks'][0]['quantity']==49
                screenshot=Path(screenshot)
                screenshot.parent.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(screenshot),full_page=True)
                print(f'Chromium {browser.version}: login, archive catalogue, shared price update, seller XML upload and snapshot passed')
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
    run(parser.parse_args().screenshot)
