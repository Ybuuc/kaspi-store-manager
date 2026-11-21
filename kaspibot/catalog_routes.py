import json
from flask import Blueprint, request, render_template, flash, redirect, url_for, jsonify
from sqlalchemy.exc import SQLAlchemyError
from kaspibot.models import db
from kaspibot.services.seller_catalog import import_xml, latest_catalog

bp = Blueprint('kaspibot_catalog', __name__)


@bp.route('/products/seller-catalog', methods=['GET', 'POST'])
def catalog():
    if request.method == 'POST':
        uploaded = request.files.get('file')
        try:
            if not uploaded:
                raise ValueError('Выберите XML из кабинета продавца')
            result = import_xml(uploaded.read())
            flash(f"Позиций в снимке: {result['offers']}. Разрешено связей: {result['resolved']}.", 'success')
        except ValueError as exc:
            flash(str(exc), 'error')
        except SQLAlchemyError:
            db.session.rollback()
            flash('Ошибка базы; импорт отменён целиком.', 'error')
        return redirect(url_for('kaspibot_catalog.catalog'))
    snapshot, rows = latest_catalog()
    return render_template('kaspibot/seller_catalog.html', snapshot=snapshot, rows=rows)


@bp.get('/api/kaspi/catalog')
def api_catalog():
    snapshot, rows = latest_catalog()
    return jsonify(source='seller_xml', source_date=snapshot.source_date if snapshot else None,
                   physical_stock_verified=False, offers=rows)


def register(app):
    import click
    from pathlib import Path

    @app.cli.command('kaspi-import-xml')
    @click.argument('file', type=click.Path(exists=True, dir_okay=False, path_type=Path))
    def import_command(file):
        """Import a dated seller catalogue without rewriting archive inventory."""
        try:
            result = import_xml(file.read_bytes())
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))

    app.register_blueprint(bp)
