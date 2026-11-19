"""One archive database, additive upgrades and complete ORM product audit."""
import json
import re
import sqlite3
from decimal import Decimal

from flask import g, has_request_context
from sqlalchemy import DefaultClause, event, inspect, text
from sqlalchemy.orm import Session

from kaspibot.models import Product, db


class ArchiveConnection(sqlite3.Connection):
    """Fill legacy non-null defaults when preserved manager SQL creates a product."""
    def execute(self, sql, parameters=(), /):
        match = re.fullmatch(r'INSERT INTO products\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)', sql, re.I)
        if match:
            columns = [name.strip() for name in match[1].split(',')]
            extras = {c.name: c.default.arg for c in Product.__table__.columns
                      if not c.nullable and c.default is not None and
                      not callable(c.default.arg) and c.name not in columns}
            if extras:
                sql = ('INSERT INTO products (' + ','.join(columns + list(extras)) + ') VALUES (' +
                       match[2] + ',' + ','.join('?' for _ in extras) + ')')
                parameters = tuple(parameters) + tuple(extras.values())
        return super().execute(sql, parameters)


def configure_defaults():
    # SQLite preserves existing column definitions. Defaults here affect new databases only.
    for column in Product.__table__.columns:
        if not column.nullable and column.default is not None and not callable(column.default.arg):
            value = column.default.arg
            literal = str(int(value)) if isinstance(value, (int, bool)) else "'" + str(value).replace("'", "''") + "'"
            column.server_default = DefaultClause(text(literal))


def upgrade_archive():
    """Add columns only; never rename, remove or rewrite original archive columns."""
    with db.engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql('PRAGMA table_info(products)')}
        for name, datatype in [('archived_at', 'TEXT'), ('low_stock_threshold', 'INTEGER'),
                               ('price_cents', 'INTEGER')]:
            if name not in columns:
                conn.exec_driver_sql(f'ALTER TABLE products ADD COLUMN {name} {datatype}')
        conn.exec_driver_sql('UPDATE products SET price_cents=CAST(round(current_price*100) AS INTEGER) WHERE price_cents IS NULL')
        duplicates = conn.exec_driver_sql("SELECT lower(sku) FROM products WHERE sku IS NOT NULL AND trim(sku)<>'' GROUP BY lower(sku) HAVING count(*)>1").fetchall()
        if duplicates:
            raise RuntimeError('Existing SKU duplicates: resolve explicitly on a backup copy; no records were removed')
        conn.exec_driver_sql('CREATE UNIQUE INDEX IF NOT EXISTS products_sku_ci ON products(lower(sku)) WHERE sku IS NOT NULL AND trim(sku)<>\'\'')
        for statement in [
            '''CREATE TRIGGER IF NOT EXISTS price_from_archive AFTER UPDATE OF current_price ON products
               WHEN NEW.current_price IS NOT OLD.current_price BEGIN
               UPDATE products SET price_cents=CAST(round(NEW.current_price*100) AS INTEGER) WHERE id=NEW.id; END''',
            '''CREATE TRIGGER IF NOT EXISTS price_from_manager AFTER UPDATE OF price_cents ON products
               WHEN NEW.price_cents IS NOT NULL AND NEW.price_cents IS NOT OLD.price_cents BEGIN
               UPDATE products SET current_price=NEW.price_cents/100.0 WHERE id=NEW.id; END''',
            '''CREATE TRIGGER IF NOT EXISTS initial_price AFTER INSERT ON products
               WHEN NEW.price_cents IS NOT NULL BEGIN
               UPDATE products SET current_price=NEW.price_cents/100.0 WHERE id=NEW.id; END''',
            '''CREATE TRIGGER IF NOT EXISTS nonnegative_product_insert BEFORE INSERT ON products
               WHEN NEW.stock_qty<0 OR NEW.purchase_price<0 OR NEW.current_price<0
               BEGIN SELECT RAISE(ABORT,'negative inventory or price'); END''',
            '''CREATE TRIGGER IF NOT EXISTS nonnegative_product_update BEFORE UPDATE ON products
               WHEN NEW.stock_qty<0 OR NEW.purchase_price<0 OR NEW.current_price<0 OR NEW.price_cents<0
               BEGIN SELECT RAISE(ABORT,'negative inventory or price'); END''',
        ]:
            conn.exec_driver_sql(statement)


@event.listens_for(Session, 'before_flush')
def prepare_audit(session, _context, _instances):
    pending = []
    for product in session.new.union(session.dirty):
        if not isinstance(product, Product):
            continue
        state = inspect(product)
        creating = product in session.new
        if creating or state.attrs.current_price.history.has_changes():
            product.price_cents = int(Decimal(str(product.current_price or 0)) * 100)
        changed = {a.key: a.history for a in state.attrs if a.history.has_changes()}
        if not creating and not changed:
            continue
        if creating:
            pending.append((product, 'create', None, product.name))
        else:
            for key, history in changed.items():
                if key == 'price_cents':
                    continue
                old = history.deleted[0] if history.deleted else None
                new = getattr(product, key)
                action = {'current_price': 'price', 'stock_qty': 'stock'}.get(key, key)
                if action == 'price':
                    old = int(Decimal(str(old or 0)) * 100)
                    new = int(Decimal(str(new or 0)) * 100)
                pending.append((product, action, old, new))
    session.info['product_audit'] = pending


@event.listens_for(Session, 'after_flush_postexec')
def write_audit(session, _context):
    pending = session.info.pop('product_audit', [])
    if not pending:
        return
    actor = g.user['id'] if has_request_context() and getattr(g, 'user', None) else None
    connection = session.connection()
    if actor is None:
        actor = connection.execute(text("SELECT id FROM users WHERE username='system'")).scalar_one()
    for product, action, old, new in pending:
        connection.execute(text('''INSERT INTO audit_log(product_id,actor_id,action,old_value,new_value)
                                   VALUES(:product,:actor,:action,:old,:new)'''),
                           dict(product=product.id, actor=actor, action=action,
                                old=None if old is None else json.dumps(old, ensure_ascii=False) if not isinstance(old, (int, str)) else str(old),
                                new='' if new is None else str(new)))
