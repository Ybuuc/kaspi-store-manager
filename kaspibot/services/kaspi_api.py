import requests
from urllib.parse import quote
from datetime import datetime, timedelta
from flask import current_app


def kaspi_headers() -> dict:
    token = current_app.config.get("KASPI_API_TOKEN", "").strip()

    return {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "X-Auth-Token": token,
        "User-Agent": "KazpiStockMVP/1.0",
    }


def to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _build_orders_url() -> str:
    base = str(current_app.config.get("KASPI_API_BASE", "")).strip().rstrip("/")
    if not base:
        raise ValueError("KASPI_API_BASE бос")
    return f"{base}/orders"


def _build_order_url(order_id: str) -> str:
    base = str(current_app.config.get("KASPI_API_BASE", "")).strip().rstrip("/")
    if not base:
        raise ValueError("KASPI_API_BASE бос")

    order_id = str(order_id or "").strip()
    if not order_id:
        raise ValueError("order_id бос")

    return f"{base}/orders/{order_id}"


def _build_order_entries_url(order_id: str) -> str:
    base = str(current_app.config.get("KASPI_API_BASE", "")).strip().rstrip("/")
    if not base:
        raise ValueError("KASPI_API_BASE бос")

    order_id = str(order_id or "").strip()
    if not order_id:
        raise ValueError("order_id бос")

    return f"{base}/orders/{order_id}/entries"


def _safe_response_json(resp: requests.Response):
    try:
        return resp.json()
    except Exception:
        return {
            "raw_text": resp.text[:1000]
        }


def get_orders(
    page_number: int = 0,
    page_size: int = 10,
    state: str = "ARCHIVE",
    days_back: int = 7,
) -> dict:
    """
    Kaspi orders алу.
    state: NEW, SIGN_REQUIRED, PICKUP, DELIVERY, KASPI_DELIVERY, ARCHIVE
    """

    token = str(current_app.config.get("KASPI_API_TOKEN", "")).strip()
    if not token:
        raise ValueError("KASPI_API_TOKEN табылмады")

    if page_number < 0:
        page_number = 0

    if page_size < 1:
        page_size = 10
    if page_size > 100:
        page_size = 100

    if days_back < 1:
        days_back = 1

    url = _build_orders_url()

    now = datetime.now()
    start_dt = now - timedelta(days=days_back)

    params = {
        "page[number]": page_number,
        "page[size]": page_size,
        "filter[orders][state]": state,
        "filter[orders][creationDate][$ge]": to_ms(start_dt),
        "filter[orders][creationDate][$le]": to_ms(now),
    }

    print("KASPI URL:", url)
    print("KASPI PARAMS:", params)
    print("KASPI TOKEN EXISTS:", bool(token))

    try:
        resp = requests.get(
            url,
            headers=kaspi_headers(),
            params=params,
            timeout=(10, 60),
        )
    except requests.exceptions.Timeout as e:
        raise RuntimeError(f"Kaspi API timeout: {e}") from e
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Kaspi API connection error: {e}") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Kaspi API request error: {e}") from e

    print("KASPI STATUS:", resp.status_code)
    print("KASPI RESPONSE TEXT:", resp.text[:500])

    if resp.status_code == 200:
        data = _safe_response_json(resp)
        if not isinstance(data, dict):
            raise RuntimeError("Kaspi API JSON dict қайтармады")
        return data

    if resp.status_code == 400:
        raise RuntimeError(f"Kaspi API 400 Bad Request: {resp.text[:500]}")

    if resp.status_code == 401:
        raise RuntimeError("Kaspi API 401 Unauthorized: токен қате немесе жарамсыз")

    if resp.status_code == 403:
        raise RuntimeError("Kaspi API 403 Forbidden: рұқсат жоқ")

    if resp.status_code == 404:
        raise RuntimeError("Kaspi API 404 Not Found: endpoint қате болуы мүмкін")

    raise RuntimeError(f"Kaspi API error {resp.status_code}: {resp.text[:500]}")


def get_order_by_code(order_code: str) -> dict:
    """
    Бір заказды code бойынша табу.
    """
    token = str(current_app.config.get("KASPI_API_TOKEN", "")).strip()
    if not token:
        raise ValueError("KASPI_API_TOKEN табылмады")

    order_code = str(order_code or "").strip()
    if not order_code:
        raise ValueError("order_code бос")

    url = _build_orders_url()

    params = {
        "page[number]": 0,
        "page[size]": 1,
        "filter[orders][code]": order_code,
    }

    print("KASPI ORDER BY CODE URL:", url)
    print("KASPI ORDER BY CODE PARAMS:", params)

    try:
        resp = requests.get(
            url,
            headers=kaspi_headers(),
            params=params,
            timeout=(10, 60),
        )
    except requests.exceptions.Timeout as e:
        raise RuntimeError(f"Kaspi API timeout: {e}") from e
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Kaspi API connection error: {e}") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Kaspi API request error: {e}") from e

    print("KASPI ORDER BY CODE STATUS:", resp.status_code)
    print("KASPI ORDER BY CODE TEXT:", resp.text[:500])

    if resp.status_code == 200:
        data = _safe_response_json(resp)
        if not isinstance(data, dict):
            raise RuntimeError("Kaspi API JSON dict қайтармады")
        return data

    if resp.status_code == 400:
        raise RuntimeError(f"Kaspi API 400 Bad Request: {resp.text[:500]}")

    if resp.status_code == 401:
        raise RuntimeError("Kaspi API 401 Unauthorized: токен қате немесе жарамсыз")

    if resp.status_code == 403:
        raise RuntimeError("Kaspi API 403 Forbidden: рұқсат жоқ")

    if resp.status_code == 404:
        raise RuntimeError("Kaspi API 404 Not Found: endpoint қате болуы мүмкін")

    raise RuntimeError(f"Kaspi API error {resp.status_code}: {resp.text[:500]}")


def get_order_details(order_id: str) -> dict:
    """
    Бір заказдың толық detail мәліметін алу.
    Kaspi docs: GET /shop/api/v2/orders/{orderId}
    """
    token = str(current_app.config.get("KASPI_API_TOKEN", "")).strip()
    if not token:
        raise ValueError("KASPI_API_TOKEN табылмады")

    url = _build_order_url(order_id)

    print("KASPI ORDER DETAILS URL:", url)

    try:
        resp = requests.get(
            url,
            headers=kaspi_headers(),
            timeout=(10, 60),
        )
    except requests.exceptions.Timeout as e:
        raise RuntimeError(f"Kaspi API timeout: {e}") from e
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Kaspi API connection error: {e}") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Kaspi API request error: {e}") from e

    print("KASPI ORDER DETAILS STATUS:", resp.status_code)
    print("KASPI ORDER DETAILS TEXT:", resp.text[:1000])

    if resp.status_code == 200:
        data = _safe_response_json(resp)
        if not isinstance(data, dict):
            raise RuntimeError("Kaspi API JSON dict қайтармады")
        return data

    if resp.status_code == 400:
        raise RuntimeError(f"Kaspi API 400 Bad Request: {resp.text[:500]}")

    if resp.status_code == 401:
        raise RuntimeError("Kaspi API 401 Unauthorized: токен қате немесе жарамсыз")

    if resp.status_code == 403:
        raise RuntimeError("Kaspi API 403 Forbidden: рұқсат жоқ")

    if resp.status_code == 404:
        raise RuntimeError("Kaspi API 404 Not Found: order details endpoint қате болуы мүмкін")

    raise RuntimeError(f"Kaspi API error {resp.status_code}: {resp.text[:500]}")


def get_order_entries(order_id: str) -> dict:
    """
    Бір заказдың ішіндегі тауарлар тізімін алу.
    Kaspi docs: GET /shop/api/v2/orders/{orderId}/entries
    """
    token = str(current_app.config.get("KASPI_API_TOKEN", "")).strip()
    if not token:
        raise ValueError("KASPI_API_TOKEN табылмады")

    url = _build_order_entries_url(order_id)

    print("KASPI ORDER ENTRIES URL:", url)

    try:
        resp = requests.get(
            url,
            headers=kaspi_headers(),
            timeout=(10, 60),
        )
    except requests.exceptions.Timeout as e:
        raise RuntimeError(f"Kaspi API timeout: {e}") from e
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Kaspi API connection error: {e}") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Kaspi API request error: {e}") from e

    print("KASPI ORDER ENTRIES STATUS:", resp.status_code)
    print("KASPI ORDER ENTRIES TEXT:", resp.text[:500])

    if resp.status_code == 200:
        data = _safe_response_json(resp)
        if not isinstance(data, dict):
            raise RuntimeError("Kaspi API JSON dict қайтармады")
        return data

    if resp.status_code == 400:
        raise RuntimeError(f"Kaspi API 400 Bad Request: {resp.text[:500]}")

    if resp.status_code == 401:
        raise RuntimeError("Kaspi API 401 Unauthorized: токен қате немесе жарамсыз")

    if resp.status_code == 403:
        raise RuntimeError("Kaspi API 403 Forbidden: рұқсат жоқ")

    if resp.status_code == 404:
        raise RuntimeError("Kaspi API 404 Not Found: entries endpoint қате болуы мүмкін")

    raise RuntimeError(f"Kaspi API error {resp.status_code}: {resp.text[:500]}")
import json


def _build_products_url() -> str:
    base = str(current_app.config.get("KASPI_API_BASE", "")).strip().rstrip("/")
    if not base:
        raise ValueError("KASPI_API_BASE бос")
    return f"{base}/products"


def get_products(page_number: int = 0, page_size: int = 100) -> dict:
    """
    Kaspi-дегі товарлар тізімін алу.
    Endpoint жобаңа қарай /products болуы мүмкін.
    Егер нақты endpoint басқа болса, кейін ауыстырамыз.
    """
    token = str(current_app.config.get("KASPI_API_TOKEN", "")).strip()
    if not token:
        raise ValueError("KASPI_API_TOKEN табылмады")

    if page_number < 0:
        page_number = 0

    if page_size < 1:
        page_size = 100
    if page_size > 100:
        page_size = 100

    url = _build_products_url()

    params = {
        "page[number]": page_number,
        "page[size]": page_size,
    }

    print("KASPI PRODUCTS URL:", url)
    print("KASPI PRODUCTS PARAMS:", params)

    try:
        resp = requests.get(
            url,
            headers=kaspi_headers(),
            params=params,
            timeout=(10, 60),
        )
    except requests.exceptions.Timeout as e:
        raise RuntimeError(f"Kaspi API timeout: {e}") from e
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Kaspi API connection error: {e}") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Kaspi API request error: {e}") from e

    print("KASPI PRODUCTS STATUS:", resp.status_code)
    print("KASPI PRODUCTS TEXT:", resp.text[:1000])

    if resp.status_code == 200:
        data = _safe_response_json(resp)
        if not isinstance(data, dict):
            raise RuntimeError("Kaspi API JSON dict қайтармады")
        return data

    if resp.status_code == 400:
        raise RuntimeError(f"Kaspi API 400 Bad Request: {resp.text[:500]}")

    if resp.status_code == 401:
        raise RuntimeError("Kaspi API 401 Unauthorized: токен қате немесе жарамсыз")

    if resp.status_code == 403:
        raise RuntimeError("Kaspi API 403 Forbidden: рұқсат жоқ")

    if resp.status_code == 404:
        raise RuntimeError("Kaspi API 404 Not Found: products endpoint қате болуы мүмкін")

    raise RuntimeError(f"Kaspi API error {resp.status_code}: {resp.text[:500]}")


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
def _get_kaspi_resource(path: str) -> dict:
    base = str(
        current_app.config.get("KASPI_API_BASE") or ""
    ).strip().rstrip("/")

    token = str(
        current_app.config.get("KASPI_API_TOKEN") or ""
    ).strip()

    if not base or not token:
        raise ValueError("Kaspi API мекенжайы немесе токені орнатылмаған")

    try:
        response = requests.get(
            f"{base}/{path}",
            headers=kaspi_headers(),
            timeout=(10, 30),
            allow_redirects=False,
        )
    except requests.RequestException:
        raise RuntimeError(
            "Kaspi-мен байланыс орнатылмады немесе уақыт шегі аяқталды"
        ) from None

    if response.status_code != 200:
        raise RuntimeError(
            f"Kaspi тауар сұрауы: HTTP {response.status_code}"
        )

    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError("Kaspi жарамсыз JSON қайтарды") from None

    if not isinstance(payload, dict) or payload.get("errors"):
        raise RuntimeError("Kaspi жауабының құрылымы жарамсыз")

    data = payload.get("data")

    if not isinstance(data, dict) or not data.get("id"):
        raise RuntimeError("Kaspi жауабында тауар немесе оның ID-і жоқ")

    if not isinstance(data.get("attributes"), dict):
        raise RuntimeError("Kaspi жауабында тауар атрибуттары жоқ")

    return data


def get_order_entry_product(entry_id: str) -> dict:
    """Тапсырыс позициясына байланысты тауарды алу."""
    entry_id = str(entry_id or "").strip()

    if not entry_id:
        raise ValueError("Тапсырыс позициясының ID-і бос")

    return _get_kaspi_resource(
        f"orderentries/{quote(entry_id, safe='')}/product"
    )


def get_merchant_product(master_product_id: str) -> dict:
    """Kaspi тауарымен байланысты сатушы тауарын алу."""
    master_product_id = str(master_product_id or "").strip()

    if not master_product_id:
        raise ValueError("Kaspi тауарының ID-і бос")

    return _get_kaspi_resource(
        f"masterproducts/{quote(master_product_id, safe='')}"
        "/merchantProduct"
    )