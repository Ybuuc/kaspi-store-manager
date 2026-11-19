"""Archive functionality hosted in the manager's authenticated Flask app."""
import hmac
import os
from pathlib import Path

import click
from flask import abort, g, redirect, request, session, url_for
from sqlalchemy.engine import make_url


def init_app(app, core_db):
    from kaspibot.app import bp, init_database
    from kaspibot.models import db
    from kaspibot.bridge import register_bridge

    app.config.setdefault(
        'SQLALCHEMY_DATABASE_URI',
        os.environ.get('KASPIBOT_DATABASE_URL') or
        'sqlite:///' + (Path(app.instance_path) / 'kaspibot.sqlite3').as_posix(),
    )
    bot_url = make_url(app.config['SQLALCHEMY_DATABASE_URI'])
    if bot_url.get_backend_name() == 'sqlite' and bot_url.database not in (None, '', ':memory:'):
        bot_path = Path(bot_url.database)
        if not bot_path.is_absolute():
            bot_path = Path(app.instance_path) / bot_path
        if bot_path.resolve() == Path(app.config['DATABASE']).resolve():
            raise RuntimeError('Manager and Kaspibot require separate database files')
    app.config.setdefault('SQLALCHEMY_TRACK_MODIFICATIONS', False)
    app.config.setdefault('KASPI_API_TOKEN', os.environ.get('KASPI_API_TOKEN', ''))
    app.config.setdefault('KASPI_API_BASE', os.environ.get('KASPI_API_BASE', 'https://kaspi.kz/shop/api/v2'))
    db.init_app(app)
    app.register_blueprint(bp, url_prefix='/bot')

    @app.before_request
    def protect_bot():
        if not request.blueprint or not request.blueprint.startswith('kaspibot'):
            return None
        if g.user is None:
            if request.is_json:
                abort(401)
            return redirect(url_for('login'))
        # All archive routes include financial or externally sourced business data.
        if g.user['role'] != 'admin':
            abort(403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
            if not hmac.compare_digest(token, session.get('csrf_token', 'missing')):
                abort(400, description='Invalid CSRF token')
        return None

    @app.cli.command('init-bot-db')
    def init_bot_db():
        """Initialize/migrate the archive database without changing manager data."""
        init_database()
        click.echo('Kaspibot database initialized')

    register_bridge(app, core_db)
