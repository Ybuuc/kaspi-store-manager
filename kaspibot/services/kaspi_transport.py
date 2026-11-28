"""Authenticated GET requests to the official seller API, without response-body logs."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit
import time
import random
from kaspibot.services import sync_schedule as schedule

import requests
from flask import current_app


class KaspiAPIError(RuntimeError):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code or ('rate_limited' if status==429 else 'needs_login' if status in (401,403) else 'permanent_error')


def kaspi_headers():
    token = str(current_app.config.get('KASPI_API_TOKEN') or '').strip()
    if not token:
        raise KaspiAPIError('Заполните KASPI_API_TOKEN в локальном .env или переменных окружения.')
    return {'Accept':'application/vnd.api+json', 'Content-Type':'application/vnd.api+json',
            'X-Auth-Token':token, 'User-Agent':'KaspiStock/1.0'}


def to_ms(value):
    if value.tzinfo is None:
        raise ValueError('Timestamp must have an explicit timezone')
    return int(value.timestamp()*1000)


def get(path, params=None, optional=False):
    base = str(current_app.config.get('KASPI_API_BASE') or '').rstrip('/')
    url = urlsplit(base)
    if (url.scheme != 'https' or url.hostname != 'kaspi.kz' or url.path != '/shop/api/v2'
            or url.query or url.fragment or url.username or url.password or url.port not in (None,443)):
        raise KaspiAPIError('Адрес API должен быть https://kaspi.kz/shop/api/v2')
    schedule.check('api')
    headers = kaspi_headers()
    for attempt in range(3):
        schedule.check('api')
        try:
            response = requests.get(base+'/'+path, headers=headers, params=params,
                                    timeout=(10,20), allow_redirects=False)
        except requests.Timeout:
            if attempt < 2:
                time.sleep(2**attempt+random.uniform(0,.5))
                continue
            raise KaspiAPIError('Kaspi не ответил вовремя; сохранённые данные доступны.',code='temporary_error') from None
        except requests.exceptions.SSLError:
            raise KaspiAPIError('Сертификат Kaspi не прошёл проверку TLS; запрос отменён.',code='tls_error') from None
        except requests.RequestException:
            if attempt < 2:
                time.sleep(2**attempt+random.uniform(0,.5))
                continue
            raise KaspiAPIError('Не удалось подключиться к Kaspi. Старые данные сохранены.',code='temporary_error') from None
        status=response.status_code
        if status==429:
            schedule.defer('api',header=response.headers.get('Retry-After'))
            raise KaspiAPIError('Kaspi ограничил частоту запросов (HTTP 429); следующий запрос отложен.',429)
        if status in (500,502,503,504) and attempt < 2:
            time.sleep(2**attempt+random.uniform(0,.5))
            continue
        if status == 404 and optional:
            return {'data':None}
        messages={401:'Kaspi отклонил токен. Обновите API-токен кабинета.',
                  403:'Kaspi запретил доступ. Проверьте токен и права кабинета.',
                  400:'Kaspi отклонил параметры запроса.',
                  429:'Kaspi ограничил частоту запросов. Повторите позже.'}
        if status != 200:
            raise KaspiAPIError(messages.get(status, f'Kaspi вернул HTTP {status}.'),status,code='temporary_error' if status in (500,502,503,504) else None)
        try:
            payload=response.json()
        except ValueError:
            raise KaspiAPIError('Kaspi вернул не JSON; данные не применены.') from None
        if not isinstance(payload,dict) or payload.get('errors') or 'data' not in payload:
            raise KaspiAPIError('Ответ Kaspi не соответствует JSON:API; данные не применены.')
        return payload
    raise KaspiAPIError('Не удалось прочитать ответ Kaspi.')


def get_orders(page_number=0,page_size=100,state='ARCHIVE',days_back=14,start_ms=None,end_ms=None):
    if state not in {'NEW','SIGN_REQUIRED','PICKUP','DELIVERY','KASPI_DELIVERY','ARCHIVE'}:
        raise ValueError('Unsupported Kaspi state')
    if (start_ms is None) != (end_ms is None):
        raise ValueError('Both date boundaries are required')
    if start_ms is None:
        if not 1 <= days_back <= 14:
            raise ValueError('A Kaspi request may span at most 14 days')
        end=datetime.now(timezone.utc)
        start_ms,end_ms=to_ms(end-timedelta(days=days_back)),to_ms(end)
    if not 0 <= start_ms < end_ms or end_ms-start_ms > 14*86400000:
        raise ValueError('Invalid Kaspi date window')
    return get('orders',{'page[number]':page_number,'page[size]':min(max(page_size,1),100),
                        'filter[orders][state]':state,'filter[orders][creationDate][$ge]':start_ms,
                        'filter[orders][creationDate][$le]':end_ms})


def get_order_by_code(order_code):
    if not str(order_code).strip():
        raise ValueError('Empty order code')
    return get('orders',{'filter[orders][code]':str(order_code).strip()})


def get_order_details(order_id):
    return get('orders/'+quote(str(order_id),safe=''))


def get_order_entries(order_id):
    path='orders/'+quote(str(order_id),safe='')+'/entries'
    first=get(path)
    if not isinstance(first['data'],list):
        raise KaspiAPIError('Kaspi вернул неверный состав заказа.')
    count=(first.get('meta') or {}).get('pageCount',1)
    if not isinstance(count,int) or not 1 <= count <= 1000:
        raise KaspiAPIError('Неверное число страниц позиций.')
    rows=list(first['data'])
    included=list(first.get('included') or [])
    for page in range(1,count):
        payload=get(path,{'page[number]':page,'page[size]':100})
        if not isinstance(payload['data'],list) or not payload['data']:
            raise KaspiAPIError('Состав заказа получен не полностью.')
        rows.extend(payload['data'])
        included.extend(payload.get('included') or [])
    ids=[r.get('id') for r in rows]
    if any(not x for x in ids) or len(set(ids)) != len(ids):
        raise KaspiAPIError('В составе заказа отсутствуют ID или повторяются позиции.')
    return {**first,'data':rows,'included':included}


def resource(path):
    data=get(path,optional=True)['data']
    if data is None:
        return None
    if not isinstance(data,dict) or not data.get('id') or not isinstance(data.get('attributes'),dict):
        raise KaspiAPIError('Неверное описание товара Kaspi.')
    return data


def get_order_entry_product(entry_id):
    return resource('orderentries/'+quote(str(entry_id),safe='')+'/product')


def get_merchant_product(master_product_id):
    return resource('masterproducts/'+quote(str(master_product_id),safe='')+'/merchantProduct')


def get_products(page_number=0,page_size=100):
    raise KaspiAPIError('GET /products не подтверждён документацией. Товары читаются из реальных заказов; полный каталог и остатки требуют выгрузки кабинета.')
