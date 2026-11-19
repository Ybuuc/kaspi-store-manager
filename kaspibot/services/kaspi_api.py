import json
from kaspibot.services.kaspi_transport import (
    KaspiAPIError as KaspiAPIError,
    kaspi_headers as kaspi_headers,
    to_ms as to_ms,
    get_orders as get_orders,
    get_order_by_code as get_order_by_code,
    get_order_details as get_order_details,
    get_order_entries as get_order_entries,
    get_order_entry_product as get_order_entry_product,
    get_merchant_product as get_merchant_product,
    get_products as get_products,
)

def extract_products_from_response(payload: dict) -> list[dict]:
    """
    Kaspi JSON:API жауабынан товарларды нормализациялау.
    Әртүрлі форматқа шыдайтын етіп жазылған.
    """
    if not isinstance(payload, dict):
        return []

    items = payload.get("data") or []
    included = payload.get("included") or []

    included_map = {}
    for inc in included:
        if not isinstance(inc, dict):
            continue
        inc_type = str(inc.get("type") or "").strip()
        inc_id = str(inc.get("id") or "").strip()
        if inc_type and inc_id:
            included_map[(inc_type, inc_id)] = inc

    result = []

    for row in items:
        if not isinstance(row, dict):
            continue

        row_id = str(row.get("id") or "").strip()
        attrs = row.get("attributes") or {}
        rels = row.get("relationships") or {}

        name = (
            attrs.get("name")
            or attrs.get("title")
            or attrs.get("displayName")
            or ""
        )
        sku = (
            attrs.get("sku")
            or attrs.get("code")
            or attrs.get("merchantCode")
            or attrs.get("article")
            or ""
        )

        category_name = (
            attrs.get("categoryName")
            or attrs.get("category")
            or ""
        )

        image_url = (
            attrs.get("imageUrl")
            or attrs.get("masterImage")
            or attrs.get("photoUrl")
            or ""
        )

        current_price = (
            attrs.get("price")
            or attrs.get("salePrice")
            or attrs.get("currentPrice")
            or 0
        )

        # relationships арқылы category/image келуі мүмкін
        category_rel = (((rels.get("category") or {}).get("data")) or {})
        if isinstance(category_rel, dict):
            c_type = str(category_rel.get("type") or "").strip()
            c_id = str(category_rel.get("id") or "").strip()
            cat_obj = included_map.get((c_type, c_id))
            if cat_obj:
                cat_attrs = cat_obj.get("attributes") or {}
                category_name = (
                    category_name
                    or cat_attrs.get("name")
                    or cat_attrs.get("title")
                    or ""
                )

        image_rel = (((rels.get("images") or {}).get("data")) or [])
        if isinstance(image_rel, list) and image_rel:
            first_img = image_rel[0]
            if isinstance(first_img, dict):
                i_type = str(first_img.get("type") or "").strip()
                i_id = str(first_img.get("id") or "").strip()
                img_obj = included_map.get((i_type, i_id))
                if img_obj:
                    img_attrs = img_obj.get("attributes") or {}
                    image_url = (
                        image_url
                        or img_attrs.get("url")
                        or img_attrs.get("large")
                        or img_attrs.get("medium")
                        or ""
                    )

        try:
            current_price = int(float(current_price or 0))
        except Exception:
            current_price = 0

        result.append({
            "kaspi_product_id": row_id,
            "name": str(name or "").strip(),
            "sku": str(sku or "").strip(),
            "category_name": str(category_name or "").strip(),
            "image_url": str(image_url or "").strip(),
            "current_price": current_price,
            "raw_json": json.dumps(row, ensure_ascii=False),
        })

    return result
