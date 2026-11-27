"""Decode actual Kaspi order entries without inventing prices, quantities or stock."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from flask import g

from kaspibot.models import db, Product, ProductAlias
from kaspibot.services.kaspi_api import get_order_entry_product, get_merchant_product


def number(value, name, positive=False):
    try:
        value=Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f'Kaspi: отсутствует или неверно поле {name}') from None
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError(f'Kaspi: неверно поле {name}')
    return int(value) if value == value.to_integral_value() else float(value)


def normalize(value):
    return ' '.join(str(value or '').casefold().split())


def extract_lines(order, entries_payload, order_details=None):
    attrs={**(order.get('attributes') or {}), **(((order_details or {}).get('data') or {}).get('attributes') or {})}
    code=str(attrs.get('code') or order.get('id') or '').strip()
    if not code:
        raise ValueError('Kaspi: отсутствует ID заказа')
    customer=attrs.get('customer') or {}
    customer_name=customer.get('name') or ' '.join(str(customer.get(k) or '').strip() for k in ('firstName','lastName')).strip() or None
    entries=entries_payload.get('data')
    if not isinstance(entries,list) or not entries:
        raise ValueError('Kaspi: состав заказа отсутствует; прежние данные не заменены')
    included={(row.get('type'),row.get('id')):row for row in entries_payload.get('included',[]) if isinstance(row,dict)}
    lines=[]
    seen=set()
    for entry in entries:
        entry_id=str(entry.get('id') or '').strip()
        if not entry_id or entry_id in seen:
            raise ValueError('Kaspi: отсутствует ID позиции или получен дубль')
        seen.add(entry_id)
        item=entry.get('attributes') or {}
        qty=number(item.get('quantity',item.get('qty')), 'quantity',positive=True)
        if item.get('totalPrice') is not None:
            amount=number(item['totalPrice'],'totalPrice')
        elif item.get('basePrice') is not None:
            amount=number(Decimal(str(item['basePrice']))*Decimal(str(qty)),'basePrice*quantity')
        else:
            raise ValueError('Kaspi: у позиции нет totalPrice/basePrice; сумма не придумана')
        relation=((entry.get('relationships') or {}).get('product') or {}).get('data') or {}
        cache=getattr(g,'kaspi_product_cache',None)
        if cache is None:
            cache={}
            g.kaspi_product_cache=cache
        master_id=relation.get('id')
        master=included.get((relation.get('type'),master_id)) or cache.get(('master',master_id)) or get_order_entry_product(entry_id)
        if master:
            cache[('master',master['id'])]=master
        merchant=None
        if master:
            rel=((master.get('relationships') or {}).get('merchantProduct') or {}).get('data') or {}
            key=('merchant',master['id'])
            if key not in cache:
                cache[key]=included.get((rel.get('type'),rel.get('id'))) or get_merchant_product(master['id'])
            merchant=cache[key]
        master_attrs=(master or {}).get('attributes') or {}
        merchant_attrs=(merchant or {}).get('attributes') or {}
        title=str(merchant_attrs.get('name') or master_attrs.get('name') or item.get('offerName') or item.get('name') or '').strip()
        category=master_attrs.get('category') or item.get('category')
        if isinstance(category,dict):
            category=category.get('title')
        lines.append(dict(external_key=f'{code}::{entry_id}',order_code=code,entry_id=entry_id,
                          title=title,merchant_sku=str(merchant_attrs.get('code') or merchant_attrs.get('sku') or '').strip(),
                          kaspi_code=str(master_attrs.get('code') or '').strip(),category=category,
                          qty=qty,sale_sum=amount,customer_name=customer_name,customer_phone=customer.get('cellPhone'),
                          state=str(attrs.get('state') or ''),status_text=str(attrs.get('status') or attrs.get('statusText') or ''),
                          detailed_state=str(attrs.get('state') or ''),detailed_status=str(attrs.get('status') or attrs.get('statusText') or ''),
                          creation_ms=attrs.get('creationDate'),
                          raw_json=dict(source='kaspi_api',entries_checked_at=datetime.now(timezone.utc).isoformat(),
                                        order=order,entry=entry,details=order_details,
                                        master_product=master,merchant_product=merchant)))
    return lines


class ProductMappingConflict(ValueError):
    """Real observation is valid, but legacy product identity needs reconciliation."""


def resolve_product(line):
    sku=line['merchant_sku']
    code=line.get('kaspi_code')
    title=line['title']
    product=None
    for field,value in [('sku',sku),('kaspi_product_id',code)]:
        if value:
            matches=Product.query.filter(getattr(Product,field)==value).all()
            if len(matches)>1:
                raise ProductMappingConflict('Неоднозначная привязка Kaspi к товару; нужна ручная проверка')
            if matches:
                product=matches[0]
                break
    if product is None and title:
        aliases=ProductAlias.query.filter_by(alias=normalize(title)).all()
        ids={row.product_id for row in aliases}
        if len(ids)>1:
            raise ProductMappingConflict('Неоднозначный alias; товар не объединён автоматически')
        if ids:
            product=db.session.get(Product,next(iter(ids)))
        if product is None:
            matches=[p for p in Product.query.all() if normalize(p.name)==normalize(title)]
            if len(matches)>1:
                raise ProductMappingConflict('Неоднозначное название; товар не объединён автоматически')
            product=matches[0] if matches else None
    if product:
        if sku and product.sku and sku!=product.sku:
            raise ProductMappingConflict('SKU Kaspi конфликтует с привязкой товара; прежняя карточка сохранена')
        if code and product.kaspi_product_id and code!=product.kaspi_product_id:
            raise ProductMappingConflict('Kaspi ID конфликтует с привязкой товара; прежняя карточка сохранена')
    elif title and (sku or code):
        product=Product(name=title,sku=sku or None,kaspi_product_id=code or None,
                        purchase_price=0,stock_qty=0,reserved_qty=0,onway_qty=0,
                        inventory_source='unknown')
        db.session.add(product)
    if product is None:
        return None
    if sku and not product.sku:
        product.sku=sku
    if code and not product.kaspi_product_id:
        # A legacy name match may have claimed this public code for another SKU.
        # Exact seller SKU wins; keep the actual code in raw JSON without stealing its old link.
        owner=Product.query.filter_by(kaspi_product_id=code).first()
        if owner is None or owner.id==product.id:
            product.kaspi_product_id=code
    if line.get('category') and not product.category_name:
        product.category_name=line['category']
    product.last_synced_at=datetime.now(timezone.utc).replace(tzinfo=None)
    product.kaspi_raw_json=json.dumps(line['raw_json'],ensure_ascii=False)
    # Orders do not establish current offer price, purchase cost or physical stock.
    return product
