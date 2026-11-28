"""Credential-free cabinet dependency diagnostics; never persist URLs or payloads."""
from urllib.parse import urlsplit
import re
import time


def dependency(request):
    url=urlsplit(request.url)
    host=url.hostname or ''
    if host in ('idmc.kaspi.kz','id.kaspi.kz'):
        return 'authentication'
    if host!='mc.shop.kaspi.kz':
        return 'other'
    if url.path=='/mc/facade/graphql':
        try:
            operation=(request.post_data_json or {}).get('operationName')
            return operation if operation in ('getMerchant','getOrderCounters') else 'other'
        except (ValueError,TypeError,AttributeError):
            return 'other'
    if url.path.startswith('/bff/offer-view/'):
        return 'offer_list'
    if url.path.startswith('/offers/api/v1/offer/export/'):
        return 'export'
    return 'other'


class CabinetDiagnostics:
    def __init__(self):
        self.events=set()
        self.rate_header=None
        self.started=time.monotonic()
        self.timings={}
        self.network_failures=set()
        self.script_errors=0

    def mark(self,stage):
        if stage in ('navigation_committed','cabinet_ready','merchant_verified','export_trigger_received','xml_received'):
            self.timings[stage]=round(time.monotonic()-self.started,2)

    def request_failed(self,request):
        # ERR_* is a browser category only; never record the URL or failure text.
        for code in re.findall(r'ERR_[A-Z_]+',request.failure or ''):
            if code!='ERR_FAILED':
                self.network_failures.add(code)

    def script_error(self,_error):
        self.script_errors+=1

    def blocked(self,request):
        self.events.add((dependency(request),'blocked'))

    def response(self,response):
        kind=dependency(response.request)
        if kind!='other' and response.status>=400:
            self.events.add((kind,response.status))
            if response.status==429:
                self.rate_header=response.headers.get('retry-after')

    def failure(self):
        from kaspibot.services.cabinet_export import ExportError
        if any(status==429 for _,status in self.events):
            return ExportError('Kaspi ограничил загрузку кабинета (HTTP 429); повтор после разрешённой паузы.','rate_limited',self.rate_header)
        if any(status==401 for kind,status in self.events if kind in ('authentication','getMerchant','offer_list')):
            return ExportError('кабинет отклонил сохранённую авторизацию (HTTP 401).','needs_login')
        if ('authentication','blocked') in self.events:
            return ExportError('локальный фильтр заблокировал служебный запрос авторизации; требуется проверка экспортёра.','response_error')
        if any(status=='blocked' for kind,status in self.events if kind in ('getMerchant','offer_list')):
            return ExportError('локальный фильтр заблокировал загрузку данных кабинета; требуется проверка экспортёра.','response_error')
        if any(status==403 for kind,status in self.events if kind in ('authentication','getMerchant','offer_list')):
            return ExportError('кабинет запретил загрузку данных (HTTP 403); проверьте доступ магазина.','response_error')
        return None

    def save(self,path):
        import json
        from datetime import datetime,timezone
        from kaspibot.services.catalog_source import atomic_write
        try:
            atomic_write(path,json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),
                'events':[{'dependency':kind,'result':status} for kind,status in sorted(self.events,key=str)],
                'stage_seconds':self.timings,'network_categories':sorted(self.network_failures),'script_errors':self.script_errors}).encode())
        except OSError:
            pass
