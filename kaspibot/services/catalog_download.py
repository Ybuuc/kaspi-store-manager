"""Fetch an operator-configured seller feed, without sharing Kaspi credentials."""
import ipaddress
import time
from urllib.parse import urlsplit

import requests


def download_xml(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError
        if parsed.hostname.casefold() in ('localhost', 'localhost.localdomain'):
            raise ValueError
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError
    except ValueError:
        raise ValueError('Для прайса нужна прямая HTTPS-ссылка без встроенного логина и пароля.') from None
    limit = 16 * 1024 * 1024
    start = time.monotonic()
    try:
        with requests.get(url, headers={'Accept': 'application/xml,text/xml', 'User-Agent': 'KaspiStock/1.0'},
                          timeout=(5, 20), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError(f'Сервер прайса вернул HTTP {response.status_code}; прежний каталог сохранён.')
            try:
                length = int(response.headers.get('Content-Length', '0'))
            except ValueError:
                raise ValueError('Сервер прайса вернул неверный Content-Length.') from None
            if length > limit:
                raise ValueError('XML-прайс больше 16 МБ; прежний каталог сохранён.')
            data = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if time.monotonic() - start > 60:
                    raise ValueError('Истекло время загрузки прайса; прежний каталог сохранён.')
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError('XML-прайс больше 16 МБ; прежний каталог сохранён.')
            return bytes(data)
    except requests.RequestException:
        # Never expose a signed URL or its query string through errors/logs/status.
        raise ValueError('Не удалось скачать XML по HTTPS; проверьте ссылку и сеть. Прежний каталог сохранён.') from None
