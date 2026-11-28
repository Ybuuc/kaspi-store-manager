"""Explicit UI localization; persisted shop values and business status codes stay canonical."""
import hmac
import json
from pathlib import Path
from urllib.parse import urlsplit,unquote

from flask import abort,current_app,g,redirect,request,session

CATALOG=json.loads((Path(__file__).parent/'locales/ui.json').read_text(encoding='utf-8'))
LANGUAGES={'ru':'ru-RU','kk':'kk-KZ'}
BROWSER_KEYS=json.loads((Path(__file__).parent/'locales/browser-keys.json').read_text(encoding='utf-8'))
BROWSER_MESSAGES={lang:{key:CATALOG[key][lang] for key in BROWSER_KEYS} for lang in LANGUAGES}


def language():
    return getattr(g,'ui_language','ru')


def translate(message,**values):
    if message is None:
        return ''
    text=str(message)
    key=' '.join(text.split())
    translated=CATALOG.get(key,{}).get(language())
    if translated is None:
        return text
    leading=text[:len(text)-len(text.lstrip())]
    trailing=text[len(text.rstrip()):]
    result=leading+translated+trailing
    return result % values if values else result


def safe_return(value):
    decoded=unquote(value or '/')
    parsed=urlsplit(decoded)
    if parsed.scheme or parsed.netloc or not decoded.startswith('/') or decoded.startswith('//') or '\\' in decoded or any(ord(char)<32 for char in decoded):
        return '/'
    return value or '/'


def install(app):
    app.jinja_env.policies["json.dumps_kwargs"]={"sort_keys":True,"ensure_ascii":False}
    @app.before_request
    def choose_language():
        selected=request.cookies.get('ksm_language','ru')
        g.ui_language=selected if selected in LANGUAGES else 'ru'

    @app.context_processor
    def ui_context():
        return dict(_=translate,ui_lang=language(),ui_locale=LANGUAGES[language()],
            ui_messages=BROWSER_MESSAGES[language()])

    @app.after_request
    def language_header(response):
        if response.mimetype=='text/html':
            response.headers['Content-Language']=language()
            response.vary.add('Cookie')
        return response

    @app.post('/language')
    def set_language():
        selected=request.form.get('language')
        if selected not in LANGUAGES:
            abort(400)
        if not hmac.compare_digest(request.form.get('csrf_token',''),session.get('csrf_token','missing')):
            abort(400,description='Invalid CSRF token')
        response=redirect(safe_return(request.form.get('next','/')))
        response.set_cookie('ksm_language',selected,max_age=365*86400,httponly=True,
            secure=current_app.config['SESSION_COOKIE_SECURE'],samesite='Lax')
        return response
