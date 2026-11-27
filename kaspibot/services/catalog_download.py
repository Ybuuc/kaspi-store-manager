"""Bounded TLS-only feed reads, isolated from API credentials and ambient auth."""
import ipaddress
import socket
import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter


class CatalogDownloadError(ValueError):
    """Only operator-safe messages; never include the URL or underlying exception."""


def validate_url(url, allow_local=False):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (not isinstance(url, str) or any(ord(c) < 33 for c in url)
                or parsed.scheme != 'https' or not host or parsed.username is not None
                or parsed.password is not None or parsed.fragment or '\\' in url):
            raise ValueError
        local = host.casefold() in ('localhost', '127.0.0.1', '::1')
        if local:
            if not allow_local or parsed.port != 8443:
                raise ValueError
        else:
            if parsed.port not in (None, 443) or host.casefold() == 'localhost.localdomain':
                raise ValueError
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address and not address.is_global:
                raise ValueError
        return parsed, local
    except (ValueError, TypeError, AttributeError):
        raise CatalogDownloadError('URL: нужна прямая HTTPS-ссылка без логина, пароля и фрагмента; '
            'локально разрешены только localhost, 127.0.0.1, [::1]:8443 при KASPI_ALLOW_LOCAL_CATALOG=1.') from None


class PinnedAddressAdapter(HTTPAdapter):
    """Connect to the validated IP, retaining the original SNI and certificate name."""

    def __init__(self, address, hostname):
        self.address = address
        self.hostname = hostname
        super().__init__(max_retries=0)

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        host, options = self.build_connection_pool_key_attributes(request, verify, cert)
        host['host'] = self.address
        options.update(assert_hostname=self.hostname, server_hostname=self.hostname)
        return self.poolmanager.connection_from_host(**host, pool_kwargs=options)


def download_xml(url, *, allow_local=False, ca_bundle=None):
    parsed, local = validate_url(url, allow_local)
    limit = 16 * 1024 * 1024
    start = time.monotonic()
    try:
        addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(
            parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)))
        if not addresses or any(
            (address not in ('127.0.0.1', '::1')) if local else not ipaddress.ip_address(address).is_global
            for address in addresses
        ):
            raise CatalogDownloadError('URL: DNS указывает на запрещённый адрес; запрос отменён.')
        # No proxies, .netrc, cookies or Requests environment overrides; CA applies only locally.
        with requests.Session() as session:
            session.trust_env = False
            session.mount('https://', PinnedAddressAdapter(addresses[0], parsed.hostname))
            with session.get(url, headers={'Accept': 'application/xml,text/xml', 'User-Agent': 'KaspiStock/1.0'},
                             verify=(str(ca_bundle) if local and ca_bundle else True),
                             timeout=(5, 20), allow_redirects=False, stream=True) as response:
                if 300 <= response.status_code < 400:
                    raise CatalogDownloadError('HTTPS: перенаправления запрещены; укажите прямой адрес XML.')
                if response.status_code != 200:
                    raise CatalogDownloadError(f'HTTPS: сервер прайса вернул HTTP {response.status_code}; прежний каталог сохранён.')
                try:
                    length = int(response.headers.get('Content-Length', '0'))
                    if length < 0:
                        raise ValueError
                except ValueError:
                    raise CatalogDownloadError('HTTPS: неверный Content-Length.') from None
                if length > limit:
                    raise CatalogDownloadError('HTTPS: XML-прайс больше 16 МБ; прежний каталог сохранён.')
                data = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    if time.monotonic() - start > 60:
                        raise CatalogDownloadError('Сеть: истекло время загрузки; прежний каталог сохранён.')
                    data.extend(chunk)
                    if len(data) > limit:
                        raise CatalogDownloadError('HTTPS: XML-прайс больше 16 МБ; прежний каталог сохранён.')
                return bytes(data)
    except requests.exceptions.SSLError:
        raise CatalogDownloadError('TLS: сертификат не прошёл проверку; проверьте SAN, срок и KASPI_CATALOG_CA_BUNDLE.') from None
    except (requests.RequestException, socket.gaierror):
        raise CatalogDownloadError('Сеть: HTTPS-сервер каталога недоступен или не ответил вовремя; прежний каталог сохранён.') from None
    except OSError:
        raise CatalogDownloadError('TLS: не удалось прочитать доверенный CA; проверьте KASPI_CATALOG_CA_BUNDLE.') from None
