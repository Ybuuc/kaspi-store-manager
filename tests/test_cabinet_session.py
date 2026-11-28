import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from kaspibot.services.cabinet_export import _fetch_export_once,ExportError


class SessionCheckpointTests(unittest.TestCase):
    def run_export(self,failure):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'session.json'
            original={'cookies':[{'name':'fixture','value':'original'}],'origins':[]}
            updated={'cookies':[{'name':'fixture','value':'rotated'}],'origins':[{'origin':'https://fixture.invalid','localStorage':[],'indexedDB':[{'name':'fixture-auth','version':1,'stores':[]}]}]}
            path.write_text(json.dumps(original))
            runtime=Mock()
            context=runtime.chromium.launch.return_value.new_context.return_value
            context.storage_state.return_value=updated
            def export(*_args,on_authenticated=None,**_kwargs):
                if failure!='wrong_merchant':
                    on_authenticated()
                if failure:
                    raise ExportError('fixture-only',failure)
                context.storage_state.return_value={**updated,'marker':'post-export-refresh'}
                return b'fixture-xml'
            with patch('kaspibot.services.cabinet_export.sync_playwright') as manager,patch('kaspibot.services.cabinet_export.export_from_page',side_effect=export):
                manager.return_value.__enter__.return_value=runtime
                if failure:
                    with self.assertRaises(ExportError) as error:
                        _fetch_export_once(path,'123456')
                    self.assertEqual(error.exception.code,failure)
                else:
                    self.assertEqual(_fetch_export_once(path,'123456'),b'fixture-xml')
            saved=json.loads(path.read_text())
            if failure=='wrong_merchant':
                self.assertEqual(saved,original)
                context.storage_state.assert_not_called()
            else:
                self.assertEqual(saved['cookies'],updated['cookies'])
                self.assertTrue(all(call.kwargs=={'indexed_db':True} for call in context.storage_state.call_args_list))
                if not failure:
                    self.assertEqual(saved['marker'],'post-export-refresh')
                    self.assertEqual(context.storage_state.call_count,2)
                else:
                    self.assertEqual(context.storage_state.call_count,1)
            context.unroute_all.assert_called_once_with(behavior='wait')
            context.close.assert_called_once()
            runtime.chromium.launch.return_value.close.assert_called_once()

    def test_valid_session_rotation_is_saved_even_when_trigger_is_rate_limited(self):
        self.run_export('rate_limited')

    def test_valid_session_rotation_is_saved_before_a_network_failure(self):
        self.run_export('network_error')

    def test_success_persists_both_pre_trigger_and_post_export_state(self):
        self.run_export(None)

    def test_another_merchant_never_overwrites_our_session(self):
        self.run_export('wrong_merchant')


class CabinetDiagnosticsTests(unittest.TestCase):
    def test_blocked_authentication_is_not_mislabeled_as_expired_session(self):
        from types import SimpleNamespace
        from kaspibot.services.cabinet_diagnostics import CabinetDiagnostics
        diag=CabinetDiagnostics()
        diag.blocked(SimpleNamespace(url='https://idmc.kaspi.kz/auth/token?secret=fixture'))
        self.assertEqual(diag.failure().code,'response_error')
        self.assertIn('фильтр',str(diag.failure()))
        self.assertNotIn('fixture',str(diag.failure()))

    def test_429_on_initial_dependency_carries_server_cooldown(self):
        from types import SimpleNamespace
        from kaspibot.services.cabinet_diagnostics import CabinetDiagnostics
        diag=CabinetDiagnostics()
        request=SimpleNamespace(url='https://mc.shop.kaspi.kz/bff/offer-view/v2/list')
        diag.response(SimpleNamespace(request=request,status=429,headers={'retry-after':'7200'}))
        self.assertEqual((diag.failure().code,diag.failure().retry_after),('rate_limited','7200'))

    def test_telemetry_does_not_trigger_false_login_and_diagnostics_are_credential_free(self):
        from types import SimpleNamespace
        from kaspibot.services.cabinet_diagnostics import CabinetDiagnostics
        diag=CabinetDiagnostics()
        diag.blocked(SimpleNamespace(url='https://telemetry.invalid/private-id?secret=fixture',post_data='fixture-token'))
        self.assertIsNone(diag.failure())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'diagnostics.json'
            diag.save(path)
            content=path.read_text()
            self.assertNotIn('fixture',content)
            self.assertNotIn('private-id',content)
            self.assertNotIn('telemetry.invalid',content)

    def test_401_and_403_are_distinguished(self):
        from types import SimpleNamespace
        from kaspibot.services.cabinet_diagnostics import CabinetDiagnostics
        for status,expected in [(401,'needs_login'),(403,'response_error')]:
            diag=CabinetDiagnostics()
            request=SimpleNamespace(url='https://mc.shop.kaspi.kz/bff/offer-view/v2/list')
            diag.response(SimpleNamespace(request=request,status=status,headers={}))
            self.assertEqual(diag.failure().code,expected)


class ColdCabinetReadinessTests(unittest.TestCase):
    def test_slow_module_startup_keeps_valid_session_and_stays_inside_cycle_budget(self):
        from kaspibot.services.cabinet_export import export_from_page
        from playwright.sync_api import TimeoutError as BrowserTimeout
        payload=b'<kaspi_catalog date="2026-11-28 18:00"><merchantid>123456</merchantid><offers><offer sku="FIXTURE"><model>Fixture</model><brand>Fixture</brand><availabilities><availability storeId="PP1" available="yes" stockCount="1"/></availabilities><price>1</price></offer></offers></kaspi_catalog>'
        clock=[0]
        from unittest.mock import MagicMock
        page=MagicMock()
        events={}
        page.on.side_effect=lambda event,callback:events.update({event:callback})
        def navigate(*_args,**kwargs):
            if kwargs['wait_until']!='commit':
                raise BrowserTimeout('fixture: module startup exceeds navigation timeout')
        page.goto.side_effect=navigate
        action=page.get_by_role.return_value
        def ready(**kwargs):
            if kwargs['timeout']<70000:
                raise BrowserTimeout('fixture: valid cabinet needs 70 seconds')
            clock[0]=70
        action.wait_for.side_effect=ready
        header=Mock()
        header.is_visible.return_value=True
        merchant=Mock()
        merchant.all.return_value=[header]
        link=Mock()
        page.get_by_text.side_effect=lambda text,**kwargs:merchant if text.startswith('ID - ') else link
        download=Mock()
        download.save_as.side_effect=lambda path:Path(path).write_bytes(payload)
        link.click.side_effect=lambda:events['download'](download)
        expected=page.expect_response.return_value.__enter__.return_value
        expected.value.status=200
        checkpoint=Mock()
        with patch('kaspibot.services.cabinet_export.time.monotonic',side_effect=lambda:clock[0]):
            self.assertEqual(export_from_page(Mock(),page,'123456',timeout=180,on_authenticated=checkpoint),payload)
        self.assertEqual(page.goto.call_args.kwargs['wait_until'],'commit')
        self.assertEqual(action.wait_for.call_args.kwargs['timeout'],120000)
        self.assertLessEqual(page.expect_response.call_args.kwargs['timeout'],15000)
        checkpoint.assert_called_once()
