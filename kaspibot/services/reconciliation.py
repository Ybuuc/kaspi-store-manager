"""Evidence-only offline reconciliation; never infer current status or physical stock."""
from collections import Counter
import hashlib
import hmac
import json

from flask import current_app
from kaspibot.models import db, KaspiOrderSync, Product
from kaspibot.services.order_payload import resolve_product, ProductMappingConflict, history_protected


def raw_data(row):
    try:
        return json.loads(row.raw_json or '{}')
    except (ValueError,TypeError):
        return {}


def summary():
    groups={}
    for row in KaspiOrderSync.query.all():
        groups.setdefault(row.order_code,[]).append((row,raw_data(row)))
    primary=Counter()
    flags=Counter()
    for rows in groups.values():
        legacy=all(raw.get('source')!='kaspi_api' for _,raw in rows)
        if legacy:
            primary['unverified_archive']+=1
            if any(row.is_onway_created or row.is_sale_created or row.is_cancelled for row,_ in rows):
                flags['protected_warehouse_history']+=1
            if any(not row.product_id for row,_ in rows):
                flags['missing_local_product_link']+=1
            # Old payloads did not save merchant SKU; this does not prove Kaspi lacks it.
            flags['merchant_sku_not_saved_in_archive']+=1
        elif any(raw.get('mapping_status')=='conflict' for _,raw in rows):
            primary['mapping_conflict']+=1
        elif any(not row.product_id for row,_ in rows):
            primary['missing_product_link']+=1
    return dict(primary=dict(primary),archive_flags=dict(flags),
        note='Архивные статусы не подтверждены повторным API-чтением. Наличие состава, SKU в Kaspi и текущий статус не угадываются.')


def repair_saved(apply=False):
    counts=Counter()
    for row in KaspiOrderSync.query.all():
        raw=raw_data(row)
        if raw.get('source')!='kaspi_api' or raw.get('mapping_status')!='conflict':
            continue
        if history_protected(row):
            counts['protected_history']+=1
            continue
        merchant=(raw.get('merchant_product') or {}).get('attributes') or {}
        master=(raw.get('master_product') or {}).get('attributes') or {}
        sku=str(merchant.get('code') or merchant.get('sku') or '').strip()
        title=str(merchant.get('name') or master.get('name') or '').strip()
        if not sku or not title:
            counts['insufficient_evidence']+=1
            continue
        matches=Product.query.filter_by(sku=sku).all()
        if len(matches)>1:
            counts['ambiguous_sku']+=1
            continue
        counts['eligible']+=1
        if not apply:
            continue
        try:
            product=resolve_product(dict(merchant_sku=sku,kaspi_code=str(master.get('code') or '').strip(),title=title,raw_json=raw))
        except ProductMappingConflict:
            counts['remaining_conflict']+=1
            continue
        if product is None:
            counts['remaining_conflict']+=1
            continue
        db.session.flush()
        raw.update(previous_product_id=row.product_id,previous_mapping_message=raw.pop('mapping_message',None),
            mapping_status='resolved_by_saved_merchant_sku')
        row.product_id=product.id
        row.raw_json=json.dumps(raw,ensure_ascii=False)
        counts['resolved']+=1
    if apply:
        db.session.commit()
    return dict(counts)


def public_errors(errors):
    result=[]
    for item in errors[:100]:
        code=item.get('code','unknown')
        safe={'unmatched_product':'Конфликт привязки товара; сумма и статус сохранены, склад не изменён.',
            'order_not_found':'Один из прежних заказов не найден в API; историческая запись сохранена.',
            'old_orders_pending':'Есть старые активные заказы для отдельной проверки.'}.get(code)
        if safe is None:
            safe='Запрос или проверка полей не завершены; предыдущая запись сохранена.'
        entry=dict(code=code,message=safe,kind=item.get('kind','mapping_conflict' if code=='unmatched_product' else 'permanent_error'))
        if isinstance(item.get('http_status'),int):
            entry['http_status']=item['http_status']
        if item.get('order_code'):
            entry['reference']=hmac.new(current_app.secret_key.encode(),str(item['order_code']).encode(),hashlib.sha256).hexdigest()[:12]
        result.append(entry)
    return result
