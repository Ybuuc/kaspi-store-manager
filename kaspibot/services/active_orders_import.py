import json
from kaspibot.services.kaspi_api import (
    get_order_entry_product,
    get_merchant_product,
)
from datetime import datetime
from typing import Any

from kaspibot.models import (
    db,
    Product,
    ProductAlias,
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


def _find_product_for_line(line: dict):
    sku = str(line.get("merchant_sku") or "").strip()
    title = str(line.get("title") or "").strip()

    # 1. Сатушының SKU коды бойынша іздеу.
    if sku:
        matches = Product.query.filter_by(sku=sku).all()

        if len(matches) > 1:
            raise ValueError(
                f"SKU бірнеше тауарға берілген: {sku}"
            )

        if matches:
            return matches[0]

    # 2. Бұрын байланыстырылған alias бойынша іздеу.
    alias_norm = normalize_alias(title)
    if not alias_norm:
        return None

    aliases = ProductAlias.query.filter_by(
        alias=alias_norm
    ).all()

    product_ids = {a.product_id for a in aliases}

    if len(product_ids) > 1:
        raise ValueError(
            f"Alias бірнеше тауарға байланысқан: {title}"
        )

    if not product_ids:
        return None

    product = db.session.get(Product, next(iter(product_ids)))

    if product is None:
        raise ValueError(
            f"Alias байланысқан тауар базада жоқ: {title}"
        )

    # Alias басқа SKU бар тауарға апарса, өңдеуді тоқтату.
    saved_sku = str(product.sku or "").strip()

    if sku and saved_sku and sku != saved_sku:
        raise ValueError(
            f"SKU мен alias сәйкес емес: {title}. "
            f"Kaspi SKU: {sku}, қойма SKU: {saved_sku}"
        )

    return product

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


def _extract_order_lines_from_entries(
    order: dict,
    entries_payload: dict,
    order_details: dict | None = None
) -> list[dict]:
    """
    Бір заказ + entries payload + details payload -> lines
    Әр line = бір товар позициясы
    """
    lines = []

    order_attrs = order.get("attributes") or {}
    order_code = str(order_attrs.get("code") or order.get("id") or "").strip()

    state = str(order_attrs.get("state") or "").strip()
    status_text = str(
        order_attrs.get("status")
        or order_attrs.get("statusText")
        or ""
    ).strip()

    customer = order_attrs.get("customer") or {}
    customer_name = customer.get("name")
    customer_phone = customer.get("cellPhone")

    details_data = (order_details or {}).get("data") or {}
    details_attrs = details_data.get("attributes") or {}

    detailed_state = str(
        details_attrs.get("state")
        or order_attrs.get("state")
        or ""
    ).strip()

    detailed_status = str(
        details_attrs.get("status")
        or details_attrs.get("statusText")
        or order_attrs.get("status")
        or order_attrs.get("statusText")
        or ""
    ).strip()

    order_total_price = _safe_int(
        order_attrs.get("totalPrice")
        or order_attrs.get("price")
        or order_attrs.get("amount")
        or 0,
        0
    )

    entries = entries_payload.get("data", []) or []

    print("DEBUG ORDER CODE:", order_code)
    print("DEBUG ENTRIES COUNT:", len(entries))
    print("DEBUG DETAILED STATE:", detailed_state)
    print("DEBUG DETAILED STATUS:", detailed_status)

    for idx, entry in enumerate(entries):
        entry_id = str(entry.get("id") or "").strip()

        if not entry_id:
            raise ValueError("Kaspi позициясының ID-і жоқ")
        e_attrs = entry.get("attributes") or {}

        offer_obj = e_attrs.get("offer") or {}

        title = str(
            e_attrs.get("offerName")
            or offer_obj.get("name")
            or e_attrs.get("title")
            or e_attrs.get("name")
            or ""
        ).strip()

        qty = _safe_int(
            e_attrs.get("quantity")
            or e_attrs.get("qty")
            or 1,
            1
        )

        sale_sum = _safe_int(
            e_attrs.get("totalPrice")
            or e_attrs.get("price")
            or e_attrs.get("amount")
            or 0,
            0
        )

        if sale_sum <= 0:
            sale_sum = order_total_price if len(entries) == 1 else 0

        # Позицияға байланысты толық тауар дерегі.
        master_product = get_order_entry_product(entry_id)
        master_attrs = master_product["attributes"]

        merchant_product = get_merchant_product(
            master_product["id"]
        )
        merchant_attrs = merchant_product["attributes"]

        merchant_sku = str(
            merchant_attrs.get("code")
            or merchant_attrs.get("sku")
            or ""
        ).strip()

        title = str(
            merchant_attrs.get("name")
            or master_attrs.get("name")
            or title
            or ""
        ).strip()

        if not merchant_sku and not title:
            raise ValueError(
                f"Позицияның SKU коды мен атауы жоқ: {entry_id}"
            )

        lines.append({
            "external_key": f"{order_code}::{entry_id}",
            "order_code": order_code,
            "entry_id": entry_id,
            "title": title,
            "merchant_sku": merchant_sku,
            "qty": max(1, qty),
            "sale_sum": max(0, sale_sum),
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "state": state,
            "status_text": status_text,
            "detailed_state": detailed_state,
            "detailed_status": detailed_status,
            "raw_json": {
                "order": order,
                "entry": entry,
                "details": order_details,
            },
        })

    return lines


def sync_order_and_entries_to_db(
    order: dict,
    entries_payload: dict,
    order_details: dict | None = None
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

        product = _find_product_for_line(line)

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
        has_movements = (
                row.is_onway_created
                or row.is_sale_created
                or row.is_cancelled
        )

        if has_movements and row.product_id != product.id:
            raise ValueError(
                f"Бұрын өңделген позицияның тауары өзгерді: "
                f"{line['order_code']} / {line['entry_id']}. "
                "Ескі қойма қозғалыстарын тексеру қажет."
            )

        row.product_id = product.id

        qty = int(line["qty"] or 1)
        sale_sum = int(line["sale_sum"] or 0)

        state = (line["state"] or "").strip()
        status_text = (line["status_text"] or "").strip()
        detailed_state = (line.get("detailed_state") or state).strip()
        detailed_status = (line.get("detailed_status") or status_text).strip()

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
        "created_onway": created_onway,
        "created_sale": created_sale,
        "cancelled": cancelled,
        "unmatched": unmatched,
        "unmatched_titles": unmatched_titles,
        "skipped": skipped,
    }