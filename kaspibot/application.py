"""The archive is the application; manager features extend its existing data."""
import hmac
import os
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

import click
from flask import Flask, abort, g, redirect, request, session, url_for
from sqlalchemy import event

from kaspibot.models import db


def create_app(test_config=None):
    app = Flask(__name__, template_folder='templates', static_folder='static',
                root_path=str(Path(__file__).resolve().parents[1]))
    app.config.from_mapping(
        SECRET_KEY=os.environ.get('KSM_SECRET_KEY') or os.environ.get('SECRET_KEY'),
        DATABASE=os.environ.get('KASPIBOT_DATABASE') or str(Path(app.root_path) / 'app.db'),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('KSM_HTTPS') == '1',
        KASPI_API_TOKEN=os.environ.get('KASPI_API_TOKEN', ''),
        KASPI_API_BASE=os.environ.get('KASPI_API_BASE', 'https://kaspi.kz/shop/api/v2'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False, MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )
    from kaspibot.settings import local_settings
    local = local_settings(app.root_path)
    for key in ('KASPI_API_TOKEN', 'KASPI_API_BASE'):
        if key in local:
            app.config[key] = local[key]
    app.config['SECRET_KEY'] = local.get('KSM_SECRET_KEY') or local.get('SECRET_KEY') or app.secret_key
    app.config['DATABASE'] = local.get('KASPIBOT_DATABASE') or app.config['DATABASE']
    app.config['KASPI_HISTORY_DAYS'] = int(os.environ.get('KASPI_HISTORY_DAYS', local.get('KASPI_HISTORY_DAYS', '14')))
    app.config['DEMO_MODE'] = os.environ.get('KSM_DEMO_MODE', local.get('KSM_DEMO_MODE', '0')) == '1'
    if test_config:
        app.config.update(test_config)
    if not app.secret_key:
        raise RuntimeError('Set KSM_SECRET_KEY to a long random value before starting the app')
    path = Path(app.config['DATABASE']).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    app.config['DATABASE'] = str(path)
    uri = 'sqlite:///' + path.as_posix()
    if app.config.get('SQLALCHEMY_DATABASE_URI', uri) != uri:
        raise RuntimeError('Configure only KASPIBOT_DATABASE / DATABASE; this application uses one archive database')
    app.config['SQLALCHEMY_DATABASE_URI'] = uri
    from kaspibot.database import configure_defaults
    configure_defaults()
    db.init_app(app)
    with app.app_context():
        @event.listens_for(db.engine, 'connect')
        def sqlite_options(connection, _record):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA busy_timeout=5000')
    from management import init_management
    init_management(app)
    from kaspibot.app import bp
    app.register_blueprint(bp)
    from kaspibot.sync_cli import register
    register(app)
    from kaspibot.catalog_routes import register as register_catalog
    register_catalog(app)

    @app.before_request
    def access_control():
        g.request_id = secrets.token_hex(12)
        if request.endpoint in ('management.login', 'static', 'health'):
            return None
        if g.user is None:
            if request.is_json:
                abort(401)
            return redirect(url_for('management.login'))
        employee_routes = {'index', 'products', 'product_detail', 'product_edit', 'incoming',
                           'stock_in', 'onway', 'onway_add', 'onway_remove', 'onway_product_detail',
                           'onway_item_cancel', 'api_products', 'api_products_create', 'api_products_update',
                           'api_products_delete', 'api_incoming', 'api_onway', 'api_incoming_items',
                           'api_onway_items', 'api_in_items', 'api_incoming_add', 'api_incoming_remove',
                           'api_onway_add', 'api_onway_remove', 'api_in_remove', 'api_in_receive'}
        if request.blueprint and request.blueprint.startswith('kaspibot'):
            if g.user['role'] != 'admin' and request.endpoint.removeprefix('kaspibot.') not in employee_routes:
                abort(403)
            product_id = (request.view_args or {}).get('product_id')
            if product_id and request.method not in ('GET', 'HEAD', 'OPTIONS'):
                with closing(sqlite3.connect(path)) as conn:
                    row = conn.execute('SELECT archived_at FROM products WHERE id=?', (product_id,)).fetchone()
                if row and row[0]:
                    abort(409, description='Restore the archived product before editing it')
        if request.blueprint and request.blueprint.startswith('kaspibot') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
            if not hmac.compare_digest(token, session.get('csrf_token', 'missing')):
                abort(400, description='Invalid CSRF token')
        return None

    @app.after_request
    def response_id(response):
        response.headers['X-Request-ID'] = getattr(g, 'request_id', secrets.token_hex(12))
        if response.status_code >= 500:
            app.logger.error('http_5xx request_id=%s method=%s path=%s status=%s',
                             response.headers['X-Request-ID'], request.method, request.path, response.status_code)
        return response

    @app.get('/health')
    def health():
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
                conn.execute('SELECT 1 FROM users LIMIT 1').fetchone()
            return {'status': 'ok'}
        except sqlite3.Error:
            return {'status': 'uninitialized'}, 503

    @app.get('/login')
    def login_alias():
        return redirect(url_for('management.login'))

    # Backups cover the entire archive, including orders, aliases, jobs and audit.
    @app.cli.command('init-bot-db')
    @click.pass_context
    def old_init_alias(ctx):
        ctx.invoke(app.cli.commands['init-db'])

    return app
