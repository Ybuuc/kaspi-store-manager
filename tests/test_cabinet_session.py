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
