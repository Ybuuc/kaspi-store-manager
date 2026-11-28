"""Bounded, read-only views of catalogue and reconciliation evidence."""
from math import ceil
from urllib.parse import urlencode
from flask import request

def page_items(items, size=50):
    try:
        number = max(1, int(request.args.get('page', '1')))
    except (ValueError, TypeError):
        number = 1
    pages = max(1, ceil(len(items) / size))
    number = min(number, pages)
    return (items[(number - 1) * size:number * size], dict(page=number, pages=pages, matched=len(items), page_size=size))

def view_url(**changes):
    values = {key: value for key, value in request.args.items() if key in ('q', 'store', 'city', 'preorder', 'stock', 'view', 'page', 'bucket', 'kind') and value}
    values.update(changes)
    return request.path + '?' + urlencode({key: value for key, value in values.items() if value is not None and value != ''})

def catalogue(products, rows):
    by_product = {row['product_id']: row for row in rows}
    stores = sorted({stock['store_id'] for row in rows for stock in row['stocks']})
    cities = sorted({city for row in rows for city in row['prices']})
    selected = {key: request.args.get(key, '').strip() for key in ('store', 'city', 'preorder', 'stock')}
    if selected['store'] not in stores:
        selected['store'] = ''
    if selected['city'] not in cities:
        selected['city'] = ''
    if selected['preorder'] not in ('yes', 'no'):
        selected['preorder'] = ''
    if selected['stock'] not in ('known', 'unknown', 'low'):
        selected['stock'] = ''
    query = request.args.get('q', '').strip().casefold()
    filtered = []
    displayed = {}
    for product in products:
        seller = by_product.get(product.id)
        if query and (not any((query in str(value or '').casefold() for value in (product.name, product.sku, seller['model'] if seller else '')))):
            continue
        stocks = [stock for stock in seller['stocks'] if not selected['store'] or stock['store_id'] == selected['store']] if seller else []
        if selected['store'] and (not stocks):
            continue
        if selected['city'] and (not seller or (selected['city'] not in seller['prices'] and 'default' not in seller['prices'])):
            continue
        if selected['preorder'] and (not seller or any((stock['preorder_days'] > 0 for stock in stocks)) != (selected['preorder'] == 'yes')):
            continue
        known = product.inventory_source != 'unknown'
        if selected['stock'] == 'known' and (not known):
            continue
        if selected['stock'] == 'unknown' and known:
            continue
        if selected['stock'] == 'low' and (not (known and product.low_stock_threshold is not None and (product.available_qty < product.low_stock_threshold))):
            continue
        filtered.append(product)
        if seller:
            prices = seller['prices']
            if selected['city']:
                prices = {selected['city']: prices[selected['city']]} if selected['city'] in prices else {'default': prices['default']}
            displayed[product.id] = {**seller, 'stocks': stocks, 'prices': prices}
    page, paging = page_items(filtered)
    view = request.args.get('view', request.cookies.get('ksm_catalog_view', 'cards'))
    if view not in ('cards', 'table'):
        view = 'cards'
    return dict(products=page, catalog_by_product=displayed, stores=stores, cities=cities, filters=selected, low_count=sum(p.inventory_source!='unknown' and p.available_qty>=0 and p.low_stock_threshold is not None and p.available_qty<p.low_stock_threshold for p in filtered), catalog_view=view, visible_products=len(page), paging=paging, view_url=view_url)

def attention_items(lines, products):
    from kaspibot.services.reconciliation import raw_data
    from kaspibot.services.order_payload import history_protected
    groups = {}
    for row in lines:
        raw = raw_data(row)
        kind = 'archive' if raw.get('source') != 'kaspi_api' else 'conflicts' if raw.get('mapping_status') == 'conflict' or not row.product_id else None
        if not kind:
            continue
        key = (kind, row.order_code)
        issue = groups.setdefault(key, dict(kind=kind, order_code=row.order_code, product_id=None, title=row.title or row.order_code, merchant_skus=set(), local_skus=set(), protected=False, lines=0))
        sku = str(((raw.get('merchant_product') or {}).get('attributes') or {}).get('code') or '').strip()
        if sku:
            issue['merchant_skus'].add(sku)
        if row.product_id in products:
            issue['local_skus'].add(products[row.product_id].sku or '')
        issue['protected'] |= history_protected(row)
        issue['lines'] += 1
    issues = list(groups.values())
    counts = {kind: sum((item['kind'] == kind for item in issues)) for kind in ('archive', 'conflicts')}
    counts['inventory'] = sum((product.inventory_source == 'unknown' for product in products.values()))
    kind = request.args.get('kind', 'all')
    if kind not in ('all', 'archive', 'conflicts', 'inventory'):
        kind = 'all'
    if kind == 'inventory':
        issues = [dict(kind='inventory', product_id=product.id, title=product.name, sku=product.sku) for product in products.values() if product.inventory_source == 'unknown']
    elif kind != 'all':
        issues = [item for item in issues if item['kind'] == kind]
    page, paging = page_items(issues)
    return dict(issues=page, issue_counts=counts, kind=kind, paging=paging, view_url=view_url)
