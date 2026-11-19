"""Explicit, transactional SKU-based catalogue handoff; no stock reconciliation."""
import json

import click
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from kaspibot.models import db, Product


def register_bridge(app, core_db):
    @app.cli.command('transfer-catalogue')
    @click.option('--apply', is_flag=True, help='Commit the displayed changes; default is preview.')
    def transfer_catalogue(apply):
        """Seed new Kaspibot items from manager SKU, price and initial stock.

        Existing items are never overwritten. Neither orders nor reserves are merged.
        Resolve all SKU/name/price conflicts before applying the entire batch.
        """
        rows = core_db().execute(
            'SELECT * FROM products WHERE archived_at IS NULL ORDER BY id'
        ).fetchall()
        pending, conflicts, skipped = [], [], 0
        seen_names = set()
        for row in rows:
            matches = Product.query.filter(func.lower(Product.sku) == row['sku'].lower()).all()
            if len(matches) > 1:
                conflicts.append(f"Ambiguous SKU: {row['sku']}")
                continue
            if matches:
                skipped += 1
                continue
            normalized = row['name'].casefold()
            existing_name = Product.query.filter(func.lower(Product.name) == row['name'].lower()).first()
            if existing_name or normalized in seen_names:
                conflicts.append(f"Name already used by a different SKU: {row['name']}")
                continue
            if row['price_cents'] % 100:
                conflicts.append(f"Fractional tenge cannot be represented: {row['sku']}")
                continue
            seen_names.add(normalized)
            pending.append(dict(name=row['name'], sku=row['sku'], category_name=row['category'],
                                current_price=row['price_cents'] // 100, stock_qty=row['stock']))
        click.echo(json.dumps(dict(new=pending, existing=skipped, conflicts=conflicts), ensure_ascii=False, indent=2))
        if conflicts:
            raise click.ClickException('No changes applied: resolve catalogue conflicts first')
        if not apply:
            click.echo('Preview only. Run with --apply to create these items.')
            return
        try:
            db.session.add_all(Product(**item, purchase_price=0, reserved_qty=0, onway_qty=0)
                               for item in pending)
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise click.ClickException('Catalogue transfer rolled back') from exc
        click.echo(f'Created {len(pending)} products. Existing stock and orders unchanged.')
