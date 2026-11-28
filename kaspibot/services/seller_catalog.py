"""Immutable seller XML snapshots; declared availability is separate from physical stock."""
import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from sqlalchemy import inspect
from xml.etree import ElementTree as ET

from kaspibot.models import db, Product, KaspiOrderSync, SellerCatalogImport, SellerCatalogOffer


def tag(node):
    return node.tag.rsplit('}', 1)[-1]


def integer(value, field):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or n != n.to_integral_value() or n > 10**12:
            raise ValueError
        return int(n)
    except (InvalidOperation, ValueError):
        raise ValueError(f'Некорректное числовое поле XML: {field}') from None


def parse_xml(content):
    if not isinstance(content, bytes) or len(content) > 16 * 1024 * 1024:
        raise ValueError('XML должен быть файлом до 16 МБ')
    # Reject DTD/entities including UTF-16 encodings before using the stdlib parser.
    probe = content.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in probe or b'<!ENTITY' in probe:
        raise ValueError('DTD и entities в прайс-листе запрещены')
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise ValueError('Не удалось прочитать XML') from None
    if tag(root) != 'kaspi_catalog':
        raise ValueError('Ожидается kaspi_catalog')
    source_date = root.attrib.get('date', '')
    try:
        datetime.strptime(source_date, '%Y-%m-%d %H:%M')
    except ValueError:
        raise ValueError('В XML отсутствует дата снимка YYYY-MM-DD HH:MM') from None
    merchant = next((n.text.strip() for n in root if tag(n) == 'merchantid' and n.text), '')
    if not merchant:
        raise ValueError('В XML отсутствует merchantid')
    offers = []
    seen = set()
    for node in root.iter():
        if tag(node) != 'offer':
            continue
        sku = node.attrib.get('sku', '').strip()
        if not sku or len(sku) > 120 or sku in seen:
            raise ValueError('В XML пустой или повторный SKU')
        seen.add(sku)
        model = next((n.text.strip() for n in node if tag(n) == 'model' and n.text), '')
        brand = next((n.text.strip() for n in node if tag(n) == 'brand' and n.text), '')
        if not model:
            raise ValueError('В XML отсутствует model')
        stocks, prices = [], {}
        stores = set()
        for n in node.iter():
            if tag(n) == 'availability':
                store = n.attrib.get('storeId', '').strip()
                available = n.attrib.get('available')
                if not store or store in stores or available not in ('yes', 'no'):
                    raise ValueError('Некорректный склад или доступность XML')
                stores.add(store)
                qty = integer(n.attrib['stockCount'], 'stockCount') if 'stockCount' in n.attrib else None
                preorder = integer(n.attrib.get('preOrder', 0), 'preOrder')
                if preorder > 30:
                    raise ValueError('Срок предзаказа XML больше 30 дней')
                stocks.append(dict(store_id=store, available=available, quantity=qty, preorder_days=preorder))
            elif tag(n) in ('price', 'cityprice'):
                city = n.attrib.get('cityId') if tag(n) == 'cityprice' else 'default'
                if not city or city in prices:
                    raise ValueError('Пустой или повторный город цены XML')
                prices[city] = integer(n.text, 'price')
        if not stocks or not prices:
            raise ValueError('В XML отсутствует склад или цена')
        offers.append(dict(sku=sku, model=model, brand=brand, stocks=stocks, prices=prices))
    if not offers:
        raise ValueError('Пустой прайс-лист не применяется')
    return dict(source_date=source_date, merchant_id=merchant, offers=offers)


def import_xml(content):
    payload = parse_xml(content)  # Validate every row before the first mutation.
    sha = hashlib.sha256(content).hexdigest()
    created = resolved = 0
    try:
        db.session.execute(db.text('BEGIN IMMEDIATE'))
        previous = SellerCatalogImport.query.order_by(SellerCatalogImport.source_date.desc(), SellerCatalogImport.id.desc()).first()
        if previous and (previous.merchant_id != payload['merchant_id'] or previous.source_date > payload['source_date']):
            raise ValueError('Другой продавец или снимок старее уже загруженного; импорт отменён')
        existing = SellerCatalogImport.query.filter_by(sha256=sha).first()
        if existing:
            result = dict(import_id=existing.id, duplicate=True, offers=SellerCatalogOffer.query.filter_by(import_id=existing.id).count(), resolved=0)
            db.session.rollback()
            return result
        snapshot = SellerCatalogImport(sha256=sha, source_date=payload['source_date'],
                                       merchant_id=payload['merchant_id'], raw_xml=content)
        db.session.add(snapshot)
        db.session.flush()
        by_sku = {}
        names = {p.name for p in Product.query.all()}
        for item in payload['offers']:
            matches = Product.query.filter(Product.sku == item['sku']).all()
            if len(matches) > 1:
                raise ValueError('Неоднозначный SKU в базе; импорт отменён')
            product = matches[0] if matches else None
            # A similar name/alias alone never identifies a seller SKU or size variant.
            if product is None:
                # Existing archive identity enforces case-insensitive uniqueness. A case
                # mismatch is a conflict to review, never evidence of an exact SKU match.
                if Product.query.filter(db.func.lower(Product.sku) == item['sku'].lower()).first():
                    raise ValueError('SKU отличается регистром от существующей карточки; требуется сверка, импорт отменён')
                name = item['model']
                if name in names:
                    name = f"{name} [SKU {item['sku']}]"
                if name in names:
                    raise ValueError('Конфликт имени SKU; импорт отменён')
                product = Product(name=name, sku=item['sku'], inventory_source='unknown',
                                  purchase_price=0, stock_qty=0, reserved_qty=0, onway_qty=0)
                db.session.add(product)
                db.session.flush()
                names.add(name)
                created += 1
            by_sku[item['sku']] = product
            db.session.add(SellerCatalogOffer(import_id=snapshot.id, product_id=product.id,
                sku=item['sku'], model=item['model'], brand=item['brand'],
                stocks_json=json.dumps(item['stocks'], ensure_ascii=False),
                prices_json=json.dumps(item['prices'], ensure_ascii=False)))
        for row in KaspiOrderSync.query.all():
            try:
                raw = json.loads(row.raw_json or '{}')
            except (ValueError, TypeError):
                continue
            if raw.get('source') != 'kaspi_api' or raw.get('mapping_status') != 'conflict':
                continue
            sku = str(((raw.get('merchant_product') or {}).get('attributes') or {}).get('code') or '').strip()
            product = by_sku.get(sku)
            if not product or row.is_onway_created or row.is_sale_created or row.is_cancelled:
                continue
            raw['previous_product_id'] = row.product_id
            raw['previous_mapping_message'] = raw.pop('mapping_message', None)
            raw['mapping_status'] = 'resolved_by_seller_xml'
            raw['catalog_sha256'] = sha
            row.product_id = product.id
            row.raw_json = json.dumps(raw, ensure_ascii=False)
            resolved += 1
        db.session.commit()
        return dict(import_id=snapshot.id, duplicate=False, offers=len(payload['offers']),
                    created_products=created, resolved=resolved, source_date=payload['source_date'])
    except Exception:
        db.session.rollback()
        raise


def latest_catalog():
    if not inspect(db.engine).has_table('seller_catalog_imports'):
        return None, []
    snapshot = SellerCatalogImport.query.order_by(SellerCatalogImport.source_date.desc(), SellerCatalogImport.id.desc()).first()
    rows = []
    if snapshot:
        for row in SellerCatalogOffer.query.filter_by(import_id=snapshot.id).order_by(SellerCatalogOffer.sku).all():
            rows.append(dict(sku=row.sku, model=row.model, brand=row.brand, product_id=row.product_id,
                             stocks=json.loads(row.stocks_json), prices=json.loads(row.prices_json)))
    return snapshot, rows
