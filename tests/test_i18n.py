import json
import tempfile
import unittest
from pathlib import Path

from app import create_app
from kaspibot.models import db,Product
from kaspibot.i18n import CATALOG,safe_return


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(dict(TESTING=True,SECRET_KEY='language-fixture',DATABASE=str(Path(self.temp.name)/'app.db')))
        runner=self.app.test_cli_runner()
        for command in [['init-db'],['create-user','--role','admin','--password','fixture-only','admin']]:
            result=runner.invoke(args=command)
            self.assertEqual(result.exit_code,0,result.output)
        with self.app.app_context():
            db.session.add(Product(name='Товары и остатки',sku='LANG-1',stock_qty=3,purchase_price=700))
            db.session.commit()
        self.client=self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def switch(self,lang,next_path='/management/login'):
        self.client.get('/management/login')
        with self.client.session_transaction() as session:
            csrf=session['csrf_token']
        return self.client.post('/language',data={'language':lang,'next':next_path,'csrf_token':csrf})

    def test_language_available_before_login_and_persists_through_login_logout(self):
        response=self.switch('kk','/products?q=LANG-1')
        self.assertEqual(response.status_code,302)
        self.assertEqual(response.headers['Location'],'/products?q=LANG-1')
        cookie=self.client.get_cookie('ksm_language')
        self.assertEqual(cookie.value,'kk')
        self.assertTrue(cookie.http_only)
        self.client.post('/management/login',data={'username':'admin','password':'fixture-only'})
        page=self.client.get('/products?q=LANG-1')
        self.assertEqual(page.headers['Content-Language'],'kk')
        text=page.get_data(as_text=True)
        self.assertIn('<html lang="kk">',text)
        self.assertIn('<h1>Тауарлар мен қалдықтар</h1>',text)
        self.assertIn('>Товары и остатки</a>',text)  # Real product name is never translated.
        self.assertIn('Kaspi тапсырыстары',text)
        self.assertIn('Қойма және сатып алу есебі',text)
        self.client.post('/management/logout')
        page=self.client.get('/management/login')
        self.assertIn('Құпиясөз',page.get_data(as_text=True))
        self.assertEqual(page.headers['Content-Language'],'kk')
        with self.app.app_context():
            row=Product.query.one()
            self.assertEqual((row.name,row.sku,row.stock_qty,row.purchase_price),('Товары и остатки','LANG-1',3,700))

    def test_ru_translates_previously_kazakh_management_and_errors(self):
        page=self.client.get('/management/login')
        self.assertIn('>Пароль</label>',page.get_data(as_text=True))
        self.assertIn('>Войти</button>',page.get_data(as_text=True))
        response=self.client.post('/management/login',data={'username':'missing','password':'not-real'})
        self.assertIn('Неверный логин или пароль.',response.get_data(as_text=True))
        self.switch('kk')
        response=self.client.post('/management/login',data={'username':'missing','password':'not-real'})
        self.assertIn('Логин немесе құпиясөз қате.',response.get_data(as_text=True))

    def test_cookie_is_browser_specific_unknown_language_falls_back_and_csrf_is_required(self):
        self.switch('kk')
        other=self.app.test_client()
        self.assertEqual(other.get('/management/login').headers['Content-Language'],'ru')
        self.assertEqual(other.post('/language',data={'language':'kk'}).status_code,400)
        self.assertEqual(self.switch('en').status_code,400)
        other.set_cookie('ksm_language','invalid')
        self.assertEqual(other.get('/management/login').headers['Content-Language'],'ru')

    def test_switch_never_redirects_to_an_external_site(self):
        for value in ['https://outside.invalid/','//outside.invalid/','/\\outside.invalid/','/%5coutside.invalid/','/%0d%0aX-Test:yes']:
            with self.subTest(value=value):
                self.assertEqual(safe_return(value),'/')
                self.assertEqual(self.switch('kk',value).headers['Location'],'/')

    def test_catalog_complete_and_all_templates_parse_in_both_languages(self):
        self.assertGreaterEqual(len(CATALOG),557)
        self.assertTrue(all(set(value)=={'ru','kk'} and all(isinstance(text,str) and text for text in value.values()) for value in CATALOG.values()))
        with self.app.app_context():
            for name in self.app.jinja_env.list_templates():
                self.app.jinja_env.get_template(name)
        self.assertEqual(json.loads(json.dumps(CATALOG,ensure_ascii=False)),CATALOG)
