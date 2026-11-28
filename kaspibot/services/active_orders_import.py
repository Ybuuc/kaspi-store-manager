import json
from kaspibot.services.order_payload import extract_lines as _extract_order_lines_from_entries, resolve_product as _find_product_for_line, ProductMappingConflict, history_protected
from datetime import datetime
from typing import Any

from kaspibot.models import (
    db,
    StockMovement,
    OnwayItem,
    KaspiOrderSync,
)


def normalize_alias(value: str) -> str:
    value = str(value or "").strip().lower()
    return " ".join(value.split())


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _is_active_state(state: str) -> bool:
    s = (state or "").upper().strip()
    return s in {
        "NEW",
        "SIGN_REQUIRED",
        "PICKUP",
        "DELIVERY",
        "KASPI_DELIVERY",
        "KASPI_DELIVERY_WAIT_FOR_POINT_DELIVERY",
        "KASPI_DELIVERY_CARGO_ASSEMBLY",
        "KASPI_DELIVERY_TRANSMITTING",
        "KASPI_DELIVERY_READY_FOR_TRANSMISSION",
        "KASPI_DELIVERY_TRANSMITTED",
        "KASPI_DELIVERY_DELIVERING",
    }


def _is_archive_state(state: str) -> bool:
    return (state or "").upper().strip() == "ARCHIVE"


def _is_cancel_status(status_text: str) -> bool:
    t = (status_text or "").lower()
    return ("отмен" in t) or ("cancel" in t)


def sync_order_and_entries_to_db(
    order: dict,
    entries_payload: dict,
    order_details: dict | None = None,
    apply_movements: bool = False,
) -> dict:
    """
    Бір заказ және оның entries/details мәліметін базаға сақтайды.
    """
    lines = _extract_order_lines_from_entries(order, entries_payload, order_details)

    created_onway = 0
    created_sale = 0
    cancelled = 0
    skipped = 0
    unmatched = 0
    unmatched_titles = []

    for line in lines:
        external_key = line["external_key"]

        row = KaspiOrderSync.query.filter_by(external_key=external_key).first()
        if not row:
            row = KaspiOrderSync(
                external_key=external_key,
                order_code=line["order_code"],
                entry_id=line["entry_id"],
                product_id=None,
                title=line["title"],
                qty=line["qty"],
                sale_sum=line["sale_sum"],
                customer_name=line["customer_name"],
                customer_phone=line["customer_phone"],
                state=line["state"],
                status_text=line["status_text"],
                detailed_state=line.get("detailed_state"),
                detailed_status=line.get("detailed_status"),
                raw_json=json.dumps(line["raw_json"], ensure_ascii=False),
            )
            db.session.add(row)
            db.session.flush()
        else:
            row.state = line["state"]
            row.status_text = line["status_text"]
            row.detailed_state = line.get("detailed_state")
            row.detailed_status = line.get("detailed_status")
            row.qty = line["qty"]
            row.sale_sum = line["sale_sum"]
            row.title = line["title"]
            row.customer_name = line["customer_name"]
            row.customer_phone = line["customer_phone"]
            row.raw_json = json.dumps(line["raw_json"], ensure_ascii=False)

        if not apply_movements and history_protected(row) and row.product_id:
            from kaspibot.models import Product
            original=db.session.get(Product,row.product_id)
            if original and line.get('merchant_sku') and original.sku and original.sku!=line['merchant_sku']:
                row.raw_json=json.dumps(dict(line['raw_json'],mapping_status='conflict',
                    mapping_message='Прежняя складская привязка требует сверки.'),ensure_ascii=False)
                unmatched+=1
                continue
        try:
            product = _find_product_for_line(line)
        except ProductMappingConflict as exc:
            if apply_movements:
                raise
            raw = dict(line['raw_json'], mapping_status='conflict', mapping_message=str(exc))
            row.raw_json = json.dumps(raw, ensure_ascii=False)
            unmatched += 1
            continue

        if product is None:
            unmatched += 1

            label = (
                f"{line['title']} "
                f"[SKU: {line.get('merchant_sku') or 'жоқ'}]"
            )

            if label not in unmatched_titles:
                unmatched_titles.append(label)

            # Бұрынғы байланысты өшірмейміз.
            # Бұл позицияға жаңа қойма қозғалысын жасамаймыз.
            continue

        # Бұрын өңделген позицияны басқа тауарға ауыстырмау.
        has_movements = history_protected(row)

        if has_movements and row.product_id != product.id:
            if not apply_movements:
                row.raw_json = json.dumps(dict(line['raw_json'], mapping_status='conflict',
                    mapping_message='Прежняя складская привязка требует сверки.'), ensure_ascii=False)
                unmatched += 1
                continue
            raise ValueError(
                f"Бұрын өңделген позицияның тауары өзгерді: "
                f"{line['order_code']} / {line['entry_id']}. "
                "Ескі қойма қозғалыстарын тексеру қажет."
            )

        db.session.flush()
        row.product_id = product.id
        if not apply_movements:
            skipped += 1
            continue

        qty = int(line["qty"] or 1)
        sale_sum = int(line["sale_sum"] or 0)

        state = (line["state"] or "").strip()
        status_text = (line["status_text"] or "").strip()
        detailed_state = (line.get("detailed_state") or state).strip()
        detailed_status = (line.get("detailed_status") or status_text).strip()

        # Cancelled statuses take precedence over a possibly stale active state.
        if _is_cancel_status(detailed_status) and detailed_status.upper() != "CANCELLING":
            detailed_state = "ARCHIVE"

        # Актив заказ -> onway бір рет қана жасаймыз
        if _is_active_state(detailed_state) and not row.is_onway_created:
            unit_price = sale_sum // qty if qty > 0 else sale_sum

            for _ in range(qty):
                db.session.add(OnwayItem(
                    product_id=product.id,
                    sale_price=unit_price,
                    is_active=True,
                    created_at=datetime.utcnow(),
                    kaspi_order_key=external_key,
                ))

            product.onway_qty = int(product.onway_qty or 0) + qty

            db.session.add(StockMovement(
                product_id=product.id,
                kind="ONWAY",
                qty_change=qty,
                sale_sum=sale_sum,
                delivery_cost=0,
                commission_pct=0,
                note=f"Kaspi sync ONWAY: {line['title']} ({line['order_code']})",
                created_at=datetime.utcnow(),
                external_code=external_key,
            ))

            row.is_onway_created = True
            created_onway += 1
            continue

        # Архив -> onway жабу + sale немесе cancel
        # Архив: тек аяқталған немесе жойылған тапсырысты өңдеу.
        if _is_archive_state(detailed_state):
            final_status = detailed_status.upper().strip()
            is_cancelled_order = _is_cancel_status(final_status)

            # Белгісіз, қайтарылған немесе басқа мәртебелерде
            # сатылым жасамаймыз және onway-ды жаппаймыз.
            if final_status != "COMPLETED" and not is_cancelled_order:
                skipped += 1
                continue

            active_items = (
                OnwayItem.query
                .filter_by(kaspi_order_key=external_key, is_active=True)
                .all()
            )

            closed_count = 0
            for item in active_items[:qty]:
                item.is_active = False
                closed_count += 1

            if closed_count > 0:
                product.onway_qty = max(0, int(product.onway_qty or 0) - closed_count)

            if is_cancelled_order:
                if not row.is_cancelled:
                    db.session.add(StockMovement(
                        product_id=product.id,
                        kind="ONWAY",
                        qty_change=-closed_count,
                        sale_sum=0,
                        delivery_cost=0,
                        commission_pct=0,
                        note=f"Kaspi cancel: {line['title']} ({line['order_code']})",
                        created_at=datetime.utcnow(),
                        external_code=external_key,
                    ))
                    row.is_cancelled = True
                    cancelled += 1
                continue

            if not row.is_sale_created:
                db.session.add(StockMovement(
                    product_id=product.id,
                    kind="SALE",
                    qty_change=-qty,
                    sale_sum=sale_sum,
                    delivery_cost=0,
                    commission_pct=12,
                    note=f"Kaspi sync SALE: {line['title']} ({line['order_code']})",
                    created_at=datetime.utcnow(),
                    external_code=external_key,
                ))

                row.is_sale_created = True
                created_sale += 1
                continue

        skipped += 1

    db.session.commit()

    return {
        "total_lines": len(lines),
        "observed": len(lines),
        "movements_applied": apply_movements,
        "created_onway": created_onway,
        "created_sale": created_sale,
        "cancelled": cancelled,
        "unmatched": unmatched,
        "unmatched_titles": unmatched_titles,
        "skipped": skipped,
    }