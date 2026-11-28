"""Bounded cache for public, fingerprinted cabinet assets, never seller/API data."""
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as BrowserError

MAX_ENTRY=4*1024*1024
MAX_TOTAL=64*1024*1024
MAX_AGE=86400


def asset_key(url):
    try:
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname!='kaspi.kz' or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None
        if not re.fullmatch(r'/mc/assets/[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{6,}\.(?:js|css)',parsed.path):
            return None
        return hashlib.sha256(url.encode()).hexdigest()
    except (ValueError,TypeError):
        return None


class CabinetAssets:
    def __init__(self,folder):
        self.folder=Path(folder)
        self.served=set()
        self.hits=0
        self.saved=0

    def respond(self,route):
        request=route.request
        key=asset_key(request.url) if request.method=='GET' else None
        if not key:
            return False
        try:
            path=self.folder/(key+'.asset')
            if path.stat().st_size>MAX_ENTRY+4096:
                return False
            metadata,body=path.read_bytes().split(b'\n',1)
            meta=json.loads(metadata)
            if meta['until']<=time.time() or meta['sha256']!=hashlib.sha256(body).hexdigest() or meta['type'] not in ('text/javascript','application/javascript','text/css'):
                return False
        except (OSError,ValueError,KeyError,TypeError):
            return False
        route.fulfill(status=200,content_type=meta['type'],body=body)
        self.served.add(request)
        self.hits+=1
        return True

    def capture(self,request):
        if request in self.served:
            self.served.discard(request)
            return
        key=asset_key(request.url) if request.method=='GET' else None
        if not key:
            return
        try:
            response=request.response()
            if response is None or response.status!=200:
                return
            headers=response.headers
            policy=headers.get('cache-control','').lower()
            vary=headers.get('vary','').lower()
            age=re.search(r'(?:^|[,\s])max-age=(\d+)',policy)
            content_type=headers.get('content-type','').split(';',1)[0].strip().lower()
            if (not age or int(age[1])<=0 or any(value in policy for value in ('private','no-store','no-cache'))
                    or 'set-cookie' in headers or any(value in vary for value in ('cookie','authorization','*'))
                    or content_type not in ('text/javascript','application/javascript','text/css')
                    or int(headers.get('content-length','0'))>MAX_ENTRY):
                return
            # requestfinished means the complete public resource has arrived already.
            body=response.body()
            if not body or len(body)>MAX_ENTRY:
                return
            metadata=dict(until=time.time()+min(MAX_AGE,int(age[1])),type=content_type,sha256=hashlib.sha256(body).hexdigest())
            from kaspibot.services.catalog_source import atomic_write
            self.folder.mkdir(parents=True,exist_ok=True)
            atomic_write(self.folder/(key+'.asset'),json.dumps(metadata,separators=(',',':')).encode()+b'\n'+body)
            self.saved+=1
            self.trim()
        except (OSError,ValueError,TypeError,BrowserError):
            pass  # A cache miss must not fail an otherwise valid export.

    def trim(self):
        files=sorted(self.folder.glob('*.asset'),key=lambda path:path.stat().st_mtime,reverse=True)
        total=0
        for index,path in enumerate(files):
            total+=path.stat().st_size
            if total>MAX_TOTAL or index>=256:
                path.unlink(missing_ok=True)
