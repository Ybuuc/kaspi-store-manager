# app.py
from kaspibot.models import KaspiOrderSync
from datetime import datetime, date, time
from io import BytesIO
from kaspibot.services.chatbot_tools import top_profit_product_last_week, summary_numbers
import json
import openpyxl
from flask import (
    Blueprint, current_app, render_template, request, redirect, url_for, flash, send_file, jsonify
)
from sqlalchemy import func

from kaspibot.models import (
    db,
    Product,
    ProductAlias,
    StockMovement,
    ImportBatch,
    OnwayItem,
    IncomingItem,
)
from kaspibot.services.excel_import import read_alias_counts_from_excel, file_hash


# --- AI Chatbot (Gemini) ---
import os
from google import genai
from google.genai import types
from kaspibot.services.parser_routes import parser_bp
from kaspibot.services.parser_integration import latest_observations

bp = Blueprint('kaspibot', __name__, template_folder='templates')
bp.register_blueprint(parser_bp)

def _get_gemini_client() -> genai.Client:
    """Server-side Gemini client. Requires GEMINI_API_KEY (or GOOGLE_API_KEY) env var."""
    # Client will also auto-pick GEMINI_API_KEY/GOOGLE_API_KEY from environment.
    return genai.Client()


CHATBOT_INSTRUCTIONS = os.environ.get(
    "CHATBOT_INSTRUCTIONS",
    """You are a helpful assistant inside the Kazpi Stock MVP web app.
Answer briefly and practically. If the user asks about the app, explain what to click and what it does.
Language: use the same language as the user (Kazakh/Russian).
""",
)

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")


# ----------------------
# Utils
# ----------------------
def normalize_alias(value: str) -> str:
    value = str(value or "").strip().lower()
    return " ".join(value.split())


def calc_delivery_cost_by_price(price: int) -> int:
    p = int(price or 0)
    if p <= 1000:
        return 49
    if p <= 3000:
        return 179
    if p <= 5000:
        return 231
    if p <= 10000:
        return 957
    return 1507


def calc_onway_profit(sale_price: int, purchase_price: int) -> tuple[int, int, int]:
    sale = int(sale_price or 0)
    buy = int(purchase_price or 0)
    commission = int(round(sale * 12 / 100.0))
    delivery = calc_delivery_cost_by_price(sale)
    profit = sale - commission - delivery - buy
    return profit, commission, delivery


def consume_onway_items(product_id: int, qty: int) -> int:
    """Excel SALE кезінде: жолда тұрған позицияларды is_active=False қылып жауып тастайды."""
    if qty <= 0:
        return 0
    items = (
        OnwayItem.query
        .filter_by(product_id=product_id, is_active=True)
        .order_by(OnwayItem.created_at.asc())
        .limit(qty)
        .all()
    )
    for it in items:
        it.is_active = False
    return len(items)

def normalize_product_name(value: str) -> str:
    value = str(value or "").strip().lower()
    return " ".join(value.split())


def update_products_from_algatop_payload(payload: dict) -> tuple[int, int]:
    """
    Algatop productList JSON-нан products кестесін жаңарту.
    Қайтарымы: (updated_count, created_count)
    """
    data = payload.get("data") or {}
    rows = data.get("products") or []

    updated_count = 0
    created_count = 0

    for row in rows:
        if not isinstance(row, dict):
            continue

        sku = str(row.get("sku") or "").strip()
        product_id = str(row.get("product_id") or "").strip()
        netcost = int(row.get("netcost") or 0)
        is_top_speed = bool(row.get("is_top_speed") or False)

        master = row.get("MasterProduct") or {}
        master_name = str(master.get("name") or "").strip()
        master_url = str(master.get("url") or "").strip()
        master_img = str(master.get("img") or "").strip()

        master_category = master.get("MasterCategory") or {}
        category_title = str(master_category.get("title") or "").strip()

        city_rows = row.get("CityProduct") or []
        city_name = None
        city_price = 0
        min_price = 0
        max_price = 0
        step_price = 0
        position = 0
        first_position_cost = 0
        preorder = 0
        remainder = 0
        pickup_point_name = None
        is_demping = False
        is_increase = False

        if city_rows and isinstance(city_rows, list):
            city = city_rows[0] or {}

            city_name = str(((city.get("City") or {}).get("name")) or "").strip() or None
            city_price = int(city.get("price") or 0)
            min_price = int(city.get("min_price") or 0)
            max_price = int(city.get("max_price") or 0)
            step_price = int(city.get("step") or 0)
            position = int(city.get("position") or 0)
            first_position_cost = int(city.get("first_position_cost") or 0)
            preorder = int(city.get("preorder") or 0)
            remainder = int(city.get("remainder") or 0)
            is_demping = bool(city.get("is_demping") or False)
            is_increase = bool(city.get("is_increase") or False)

            pickup_rows = city.get("PickupPoint") or []
            if pickup_rows and isinstance(pickup_rows, list):
                pp = pickup_rows[0] or {}
                pp_data = pp.get("PickupPoint") or {}
                pickup_point_name = str(pp_data.get("display_name") or "").strip() or None

        product = None

        # 1) sku арқылы табу
        if sku:
            product = Product.query.filter_by(sku=sku).first()

        # 2) kaspi_product_id арқылы табу
        if not product and product_id:
            product = Product.query.filter_by(kaspi_product_id=product_id).first()

        # 3) name арқылы табу
        if not product and master_name:
            target_name = normalize_product_name(master_name)
            all_products = Product.query.all()
            for p in all_products:
                if normalize_product_name(p.name) == target_name:
                    product = p
                    break

        # 4) табылмаса жаңадан жасау
        if not product:
            fallback_name = master_name or f"Algatop item {sku or product_id}"
            product = Product(
                name=fallback_name,
                purchase_price=0,
                stock_qty=0,
                reserved_qty=0,
                onway_qty=0,
                is_active=True,
            )
            db.session.add(product)
            created_count += 1
        else:
            updated_count += 1

        if master_name:
            # UNKNOWN ITEM болса, нақты атпен ауыстыр
            if not product.name or product.name.upper().startswith("UNKNOWN ITEM"):
                product.name = master_name

        if sku:
            product.sku = sku

        if product_id:
            product.kaspi_product_id = product_id

        product.category_name = category_title or product.category_name
        product.image_url = master_img or product.image_url
        product.kaspi_url = master_url or product.kaspi_url
        product.netcost = netcost
        product.is_top_speed = is_top_speed

        product.city_name = city_name
        product.city_price = city_price
        product.min_price = min_price
        product.max_price = max_price
        product.step_price = step_price
        product.position = position
        product.first_position_cost = first_position_cost
        product.preorder = preorder
        product.remainder = remainder
        product.pickup_point_name = pickup_point_name
        product.is_demping = is_demping
        product.is_increase = is_increase

        product.current_price = city_price or product.current_price
        product.is_active = True
        product.last_synced_at = datetime.utcnow()

    db.session.commit()
    return updated_count, created_count
def ensure_sqlite_columns():
    """SQLite-та баған жетіспесе, ALTER TABLE (MVP)."""
    try:
        engine = db.engine
        if engine.dialect.name != "sqlite":
            return

        def has_col(table: str, col: str) -> bool:
            _ = db.session.execute(func.sqlite_version()).all()
            info = db.session.execute(db.text(f"PRAGMA table_info({table})")).fetchall()
            return any(r[1] == col for r in info)

        def ensure_index(index_name: str, table: str, column: str, unique: bool = False):
            rows = db.session.execute(
                db.text(f"PRAGMA index_list({table})")
            ).fetchall()

            exists = any(r[1] == index_name for r in rows)
            if exists:
                return

            unique_sql = "UNIQUE" if unique else ""
            db.session.execute(
                db.text(f"CREATE {unique_sql} INDEX {index_name} ON {table} ({column})")
            )

        # ======================
        # products
        # ======================
        if not has_col("products", "onway_qty"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN onway_qty INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "reserved_qty"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN reserved_qty INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "kaspi_product_id"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN kaspi_product_id TEXT")
            )

        if not has_col("products", "sku"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN sku TEXT")
            )

        if not has_col("products", "category_name"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN category_name TEXT")
            )

        if not has_col("products", "image_url"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN image_url TEXT")
            )

        if not has_col("products", "current_price"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN current_price INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "is_active"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
            )

        if not has_col("products", "last_synced_at"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN last_synced_at DATETIME")
            )

        if not has_col("products", "kaspi_raw_json"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN kaspi_raw_json TEXT")
            )

        # ======================
        # stock_movements
        # ======================
        if not has_col("stock_movements", "sale_sum"):
            db.session.execute(
                db.text("ALTER TABLE stock_movements ADD COLUMN sale_sum INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("stock_movements", "delivery_cost"):
            db.session.execute(
                db.text("ALTER TABLE stock_movements ADD COLUMN delivery_cost INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("stock_movements", "commission_pct"):
            db.session.execute(
                db.text("ALTER TABLE stock_movements ADD COLUMN commission_pct INTEGER NOT NULL DEFAULT 12")
            )

        if not has_col("stock_movements", "external_code"):
            db.session.execute(
                db.text("ALTER TABLE stock_movements ADD COLUMN external_code TEXT")
            )

        # ======================
        # onway_items
        # ======================
        if not has_col("onway_items", "kaspi_order_key"):
            db.session.execute(
                db.text("ALTER TABLE onway_items ADD COLUMN kaspi_order_key TEXT")
            )

        # ======================
        # kaspi_order_sync
        # ======================
        if not has_col("kaspi_order_sync", "customer_name"):
            db.session.execute(
                db.text("ALTER TABLE kaspi_order_sync ADD COLUMN customer_name TEXT")
            )

        if not has_col("kaspi_order_sync", "customer_phone"):
            db.session.execute(
                db.text("ALTER TABLE kaspi_order_sync ADD COLUMN customer_phone TEXT")
            )

        if not has_col("kaspi_order_sync", "bucket_name"):
            db.session.execute(
                db.text("ALTER TABLE kaspi_order_sync ADD COLUMN bucket_name TEXT")
            )

        if not has_col("kaspi_order_sync", "cancelled_at"):
            db.session.execute(
                db.text("ALTER TABLE kaspi_order_sync ADD COLUMN cancelled_at DATETIME")
            )

        if not has_col("kaspi_order_sync", "delivered_at"):
            db.session.execute(
                db.text("ALTER TABLE kaspi_order_sync ADD COLUMN delivered_at DATETIME")
            )

        if not has_col("kaspi_order_sync", "archived_at"):
            db.session.execute(
                db.text("ALTER TABLE kaspi_order_sync ADD COLUMN archived_at DATETIME")
            )

        if not has_col("kaspi_order_sync", "detailed_state"):
            db.session.execute(
                db.text("ALTER TABLE kaspi_order_sync ADD COLUMN detailed_state TEXT")
            )

        if not has_col("kaspi_order_sync", "detailed_status"):
            db.session.execute(
                db.text("ALTER TABLE kaspi_order_sync ADD COLUMN detailed_status TEXT")
            )

        if not has_col("products", "kaspi_url"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN kaspi_url TEXT")
            )

        if not has_col("products", "netcost"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN netcost INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "is_top_speed"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN is_top_speed INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "city_name"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN city_name TEXT")
            )

        if not has_col("products", "city_price"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN city_price INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "min_price"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN min_price INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "max_price"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN max_price INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "step_price"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN step_price INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "position"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "first_position_cost"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN first_position_cost INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "preorder"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN preorder INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "remainder"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN remainder INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "pickup_point_name"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN pickup_point_name TEXT")
            )

        if not has_col("products", "is_demping"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN is_demping INTEGER NOT NULL DEFAULT 0")
            )

        if not has_col("products", "is_increase"):
            db.session.execute(
                db.text("ALTER TABLE products ADD COLUMN is_increase INTEGER NOT NULL DEFAULT 0")
            )

        # ======================
        # indexes
        # ======================
        ensure_index("ix_products_kaspi_product_id", "products", "kaspi_product_id", unique=True)
        ensure_index("ix_products_sku", "products", "sku", unique=False)
        ensure_index("ix_products_is_active", "products", "is_active", unique=False)
        ensure_index("ix_stock_movements_external_code", "stock_movements", "external_code", unique=False)
        ensure_index("ix_onway_items_kaspi_order_key", "onway_items", "kaspi_order_key", unique=False)

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        raise RuntimeError("Kaspibot database migration failed") from e


def init_database():
    db.create_all()
    ensure_sqlite_columns()
    from kaspibot.database import upgrade_archive
    upgrade_archive()

# ======================
# HOME
# ======================
# ======================
@bp.route("/")
def index():
    products = (
        Product.query
        .filter(Product.is_active == True, Product.archived_at.is_(None))  # noqa: E712
        .order_by(Product.name.asc())
        .all()
    )

    # ✅ 1) Складтағы жалпы сумма = (қолжетімді * закуп)
    stock_total = 0
    for p in products:
        stock_qty = int(p.stock_qty or 0)
        onway_qty = int(getattr(p, "onway_qty", 0) or 0)
        available = max(0, stock_qty - onway_qty)
        stock_total += available * int(p.purchase_price or 0)

    # ✅ 2) Келе жатыр (Incoming) жалпы баға
    incoming_total = int(
        db.session.query(func.coalesce(func.sum(IncomingItem.qty * IncomingItem.purchase_price), 0))
        .filter(IncomingItem.is_active == True)  # noqa: E712
        .scalar()
        or 0
    )

    # ✅ 3) Жолда (Onway) жалпы сумма (active sale_price)
    onway_total = int(
        db.session.query(func.coalesce(func.sum(OnwayItem.sale_price), 0))
        .filter(OnwayItem.is_active == True)  # noqa: E712
        .scalar()
        or 0
    )

    # ✅ 4) Жалпы айналым
    turnover_total = int(stock_total) + int(incoming_total) + int(onway_total)

    return render_template("kaspibot/index.html",
        products=products,
        stock_total=stock_total,
        incoming_total=incoming_total,
        onway_total=onway_total,
        turnover_total=turnover_total,
    )
def _order_attributes(row):
    """Базада сақталған толық API жауабынан өрістерді аламыз."""
    try:
        raw = json.loads(row.raw_json or "{}")
    except (TypeError, ValueError):
        raw = {}

    if not isinstance(raw, dict):
        raw = {}

    order = raw.get("order") or {}
    details = raw.get("details") or {}

    if not isinstance(order, dict):
        order = {}

    if not isinstance(details, dict):
        details = {}

    details_data = details.get("data") or {}

    if not isinstance(details_data, dict):
        details_data = {}

    order_attrs = order.get("attributes") or {}
    details_attrs = details_data.get("attributes") or {}

    if not isinstance(order_attrs, dict):
        order_attrs = {}

    if not isinstance(details_attrs, dict):
        details_attrs = {}

    # Толық detail жауабының өрістері басым болады.
    return {
        **order_attrs,
        **details_attrs,
    }


def _kaspi_order_bucket(row):
    """Тапсырыстың бөлімін анықтаймыз."""
    attrs = _order_attributes(row)

    state = str(
        attrs.get("state")
        or row.detailed_state
        or row.state
        or ""
    ).upper().strip()

    status = str(
        attrs.get("status")
        or row.detailed_status
        or row.status_text
        or ""
    ).upper().strip()

    # Архив пен аяқталған тапсырыстар белсенді тізімге кірмейді.
    if state == "ARCHIVE":
        return None

    if status in {"COMPLETED", "DELIVERED", "RETURNED"}:
        return None

    # Самовывоз, өз жеткізуі және басқа арналарды
    # Kaspi Доставка бөлімдеріне араластырмаймыз.
    is_kaspi_delivery = (
        state == "KASPI_DELIVERY"
        or state.startswith("KASPI_DELIVERY_")
    )

    if not is_kaspi_delivery:
        return "other"

    # Тоқтатылған тапсырыстарды алдымен тексереміз.
    if status in {"CANCELLING", "CANCELLED"}:
        return "cancelled_delivery"

    # Егер API нақты кезеңді state арқылы қайтарса,
    # бұрын қолданылған нақты күйлерді сақтаймыз.
    explicit_states = {
        "KASPI_DELIVERY_CARGO_ASSEMBLY": "packing",
        "KASPI_DELIVERY_WAIT_FOR_POINT_DELIVERY": "packing",
        "KASPI_DELIVERY_TRANSMITTING": "transmission",
        "KASPI_DELIVERY_READY_FOR_TRANSMISSION": "transmission",
        "KASPI_DELIVERY_TRANSMITTED": "transmitted",
        "KASPI_DELIVERY_DELIVERING": "transmitted",
    }

    if state in explicit_states:
        return explicit_states[state]

    delivery = attrs.get("kaspiDelivery") or {}

    if not isinstance(delivery, dict):
        delivery = {}

    # Нақты берілген күн.
    # courierTransmissionPlanningDate — жоспарланған күн,
    # оны жеткізуге берілді деп қабылдамаймыз.
    actual_transmission = delivery.get(
        "courierTransmissionDate"
    )

    try:
        transmitted = (
            not isinstance(actual_transmission, bool)
            and float(actual_transmission or 0) > 0
        )
    except (TypeError, ValueError):
        transmitted = False

    if transmitted:
        return "transmitted"

    assembled = attrs.get("assembled")
    pre_order = attrs.get("preOrder")

    # Жиналған, бірақ нақты беру күні әлі жоқ.
    if assembled is True:
        return "transmission"

    if assembled is False:
        # Предзаказды ACCEPTED_BY_MERCHANT арқылы емес,
        # арнайы preOrder өрісі арқылы анықтаймыз.
        if pre_order is True:
            return "preorder"

        if pre_order is False:
            return "packing"

    # Ақпарат жетіспесе, болжап басқа бөлімге салмаймыз.
    return "unknown"


@bp.route("/orders")
def orders_page():
    bucket = (
        request.args.get("bucket") or "all"
    ).strip()

    all_lines = (
        KaspiOrderSync.query
        .order_by(
            KaspiOrderSync.updated_at.desc(),
            KaspiOrderSync.id.desc(),
        )
        .all()
    )

    legacy_lines = []
    if not current_app.config['DEMO_MODE']:
        verified = []
        for line in all_lines:
            try:
                raw = json.loads(line.raw_json or '{}')
            except (ValueError, TypeError):
                raw = {}
            (verified if raw.get('source') == 'kaspi_api' else legacy_lines).append(line)
        all_lines = verified

    # Бір тапсырыс бірнеше тауар позициясынан тұруы мүмкін.
    # Санақта бір тапсырысты бір рет қана есептейміз.
    unique_orders_map = {}

    for row in all_lines:
        order_code = (
            row.order_code or ""
        ).strip()

        if not order_code:
            continue

        if order_code not in unique_orders_map:
            unique_orders_map[order_code] = row

    buckets = {
        "all": [],
        "legacy": list({row.order_code: row for row in legacy_lines}.values()),
        "active": [],
        "archived": [],
        "preorder": [],
        "packing": [],
        "transmission": [],
        "transmitted": [],
        "cancelled_delivery": [],
        "unknown": [],
        "other": [],
    }

    for row in unique_orders_map.values():
        category = _kaspi_order_bucket(row)

        buckets['all'].append(row)
        if category is None:
            buckets['archived'].append(row)
            continue
        buckets[category].append(row)
        buckets['active'].append(row)

    if bucket not in buckets:
        bucket = "all"

    counts = {
        name: len(items)
        for name, items in buckets.items()
    }

    return render_template("kaspibot/orders.html",
        rows=buckets[bucket],
        bucket=bucket,
        counts=counts,
    )
@bp.route("/orders/<order_code>")
def order_detail_page(order_code):
    rows = (
        KaspiOrderSync.query
        .filter(KaspiOrderSync.order_code == order_code)
        .order_by(KaspiOrderSync.created_at.desc())
        .all()
    )

    if not rows:
        flash("Заказ табылмады", "error")
        return redirect(url_for("kaspibot.orders_page"))

    head = rows[0]

    return render_template("kaspibot/order_detail.html",
        head=head,
        rows=rows
    )

# ======================
# PRODUCTS / STOCK
@bp.route("/products/sync-kaspi", methods=["POST"])
def products_sync_kaspi():
    from kaspibot.services.sync_job import launch
    try:
        launch(current_app.config['KASPI_HISTORY_DAYS'])
        flash('Чтение кабинета запущено в фоне. Товары обновляются по реальным позициям заказов.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('kaspibot.orders_page'))

@bp.route("/products/import-algatop-json", methods=["POST"])
def products_import_algatop_json():
    try:
        f = request.files.get("file")
        if not f:
            flash("JSON файл таңдаңыз", "error")
            return redirect(url_for("kaspibot.products"))

        raw = f.read()
        if not raw:
            flash("Файл бос", "error")
            return redirect(url_for("kaspibot.products"))

        payload = json.loads(raw.decode("utf-8"))
        updated_count, created_count = update_products_from_algatop_payload(payload)

        flash(
            f"Algatop JSON импорт аяқталды: жаңартылды {updated_count}, жаңадан қосылды {created_count}",
            "success"
        )
    except Exception as e:
        flash(f"Algatop JSON импорт қатесі: {e}", "error")

    return redirect(url_for("kaspibot.products"))
@bp.route("/products", methods=["GET", "POST"])
def products():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        sku = request.form.get("sku", "").strip()
        category_name = request.form.get("category_name", "").strip()
        if not sku or not category_name:
            flash("SKU и категория обязательны", "error")
            return redirect(url_for("kaspibot.products"))
        if Product.query.filter(db.func.lower(Product.sku) == sku.lower()).first():
            flash("SKU уже существует", "error")
            return redirect(url_for("kaspibot.products"))

        purchase_price = request.form.get(
            "purchase_price", ""
        ).strip()

        stock_qty = request.form.get(
            "stock_qty", ""
        ).strip()

        if not name:
            flash("Тауар атауын енгізіңіз", "error")
            return redirect(url_for("kaspibot.products"))

        if Product.query.filter_by(name=name).first():
            flash("Бұл атаумен тауар бар", "error")
            return redirect(url_for("kaspibot.products"))

        try:
            purchase_price = int(purchase_price)
            stock_qty = int(stock_qty)

        except ValueError:
            flash(
                "Баға мен қалдық бүтін сан болуы керек",
                "error",
            )
            return redirect(url_for("kaspibot.products"))

        if purchase_price <= 0 or stock_qty < 0:
            flash("Дұрыс мән енгізіңіз", "error")
            return redirect(url_for("kaspibot.products"))

        p = Product(
            name=name,
            sku=sku, category_name=category_name,
            purchase_price=purchase_price,
            stock_qty=stock_qty,
            onway_qty=0,
        )

        db.session.add(p)
        db.session.commit()

        flash("Тауар қосылды", "success")
        return redirect(url_for("kaspibot.products"))

    query = request.args.get("q", "").strip().casefold()
    products_list = (
        Product.query
        .filter(Product.is_active == True, Product.archived_at.is_(None))
        .order_by(Product.name.asc())
        .all()
    )

    if query:
        products_list = [p for p in products_list if query in p.name.casefold() or query in (p.sku or "").casefold()]

    # Складтың жалпы сомасы: қолжетімді саны × закуп.
    total_value = 0

    for p in products_list:
        stock_qty = int(p.stock_qty or 0)

        onway_qty = int(
            getattr(p, "onway_qty", 0) or 0
        )

        available = max(
            0,
            stock_qty - onway_qty,
        )

        total_value += (
            available * int(p.purchase_price or 0)
        )

    observations = latest_observations()

    from kaspibot.services.seller_catalog import latest_catalog
    catalog_snapshot, catalog_rows = latest_catalog()
    return render_template("kaspibot/products.html",
        catalog_snapshot=catalog_snapshot,
        catalog_by_product={row['product_id']: row for row in catalog_rows},
        products=products_list,
        total_value=total_value,
        parser_results=observations,
        total_products=Product.query.count(),
        visible_products=len(products_list),
        with_photo=sum(
            1 for p in products_list
            if str(p.image_url or "").strip()
        ),
        with_parser=sum(
            1 for p in products_list
            if observations.get(p.id) is not None
        ),
    )

@bp.get('/api/products/catalog-version')
def api_catalog_version():
    from kaspibot.models import SellerCatalogImport
    snapshot = SellerCatalogImport.query.order_by(SellerCatalogImport.source_date.desc(), SellerCatalogImport.id.desc()).first()
    response = jsonify(revision=snapshot.id if snapshot else 0, source_date=snapshot.source_date if snapshot else None)
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.route("/products/<int:product_id>", methods=["GET", "POST"])
def product_detail(product_id: int):
    p = Product.query.get_or_404(product_id)

    # ---- POST: alias қосу ----
    if request.method == "POST":
        alias = request.form.get("alias", "").strip()
        if not alias:
            flash("Alias енгізіңіз", "error")
            return redirect(url_for("kaspibot.product_detail", product_id=product_id))

        alias_norm = normalize_alias(alias)

        exists = ProductAlias.query.filter_by(alias=alias_norm).first()
        if exists:
            flash("Бұл alias бұрын қосылған", "error")
            return redirect(url_for("kaspibot.product_detail", product_id=product_id))

        db.session.add(ProductAlias(product_id=p.id, alias=alias_norm))
        db.session.commit()

        flash("Alias қосылды", "success")
        return redirect(url_for("kaspibot.product_detail", product_id=product_id))

    aliases = ProductAlias.query.filter_by(product_id=p.id).all()

    start_str = request.args.get("start", "").strip()
    end_str = request.args.get("end", "").strip()

    q = StockMovement.query.filter_by(product_id=p.id)

    if start_str:
        try:
            sd = datetime.strptime(start_str, "%Y-%m-%d").date()
            q = q.filter(StockMovement.created_at >= datetime.combine(sd, time.min))
        except Exception:
            pass

    if end_str:
        try:
            ed = datetime.strptime(end_str, "%Y-%m-%d").date()
            q = q.filter(StockMovement.created_at <= datetime.combine(ed, time.max))
        except Exception:
            pass

    movements = q.order_by(StockMovement.created_at.desc()).limit(200).all()

    from kaspibot.services.seller_catalog import latest_catalog
    catalog_snapshot, catalog_rows = latest_catalog()
    return render_template("kaspibot/product_detail.html",
        catalog_snapshot=catalog_snapshot,
        seller=next((row for row in catalog_rows if row['product_id'] == p.id), None),
        product=p,
        aliases=aliases,
        movements=movements,
        start=start_str,
        end=end_str
    )


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
def product_edit(product_id: int):
    p = Product.query.get_or_404(product_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        purchase_price = request.form.get("purchase_price", "").strip()
        stock_qty = request.form.get("stock_qty", "").strip()

        if not name:
            flash("Атау бос болмауы керек", "error")
            return redirect(url_for("kaspibot.product_edit", product_id=product_id))

        try:
            purchase_price = int(purchase_price)
            stock_qty = int(stock_qty)
        except ValueError:
            flash("Қате мән", "error")
            return redirect(url_for("kaspibot.product_edit", product_id=product_id))

        if purchase_price <= 0 or stock_qty < 0:
            flash("Дұрыс мән енгізіңіз", "error")
            return redirect(url_for("kaspibot.product_edit", product_id=product_id))

        exists = Product.query.filter(Product.name == name, Product.id != p.id).first()
        if exists:
            flash("Бұл атаумен басқа тауар бар", "error")
            return redirect(url_for("kaspibot.product_edit", product_id=product_id))

        p.name = name
        p.purchase_price = purchase_price
        p.stock_qty = stock_qty

        db.session.commit()
        flash("Өзгертілді", "success")
        return redirect(url_for("kaspibot.products"))

    return render_template("kaspibot/product_edit.html", product=p)


# ======================
# INCOMING (Келе жатыр)
# ======================
@bp.route("/incoming", methods=["GET", "POST"])
def incoming():
    products_list = Product.query.order_by(Product.name.asc()).all()

    if request.method == "POST":
        try:
            product_id = int(request.form.get("product_id", "0"))
            qty = int(request.form.get("qty", "1"))
            purchase_price = int(request.form.get("purchase_price", "0"))
        except Exception:
            flash("Қате мән енгізілді", "error")
            return redirect(url_for("kaspibot.incoming"))

        if product_id <= 0:
            flash("Тауар таңдаңыз", "error")
            return redirect(url_for("kaspibot.incoming"))

        if qty <= 0:
            flash("Саны 1-ден үлкен болсын", "error")
            return redirect(url_for("kaspibot.incoming"))

        if purchase_price <= 0:
            flash("Сатып алынған бағасы 0-ден үлкен болсын", "error")
            return redirect(url_for("kaspibot.incoming"))

        p = Product.query.get_or_404(product_id)

        db.session.add(IncomingItem(
            product_id=p.id,
            qty=qty,
            purchase_price=purchase_price,
            is_active=True,
            created_at=datetime.utcnow()
        ))

        db.session.commit()
        flash("Келе жатқанға қосылды", "success")
        return redirect(url_for("kaspibot.incoming"))

    rows_raw = (
        db.session.query(
            IncomingItem.product_id,
            func.coalesce(func.sum(IncomingItem.qty), 0),
            func.coalesce(func.sum(IncomingItem.qty * IncomingItem.purchase_price), 0)
        )
        .filter(IncomingItem.is_active == True)  # noqa: E712
        .group_by(IncomingItem.product_id)
        .all()
    )

    prod_map = {p.id: p for p in products_list}

    rows = []
    total_incoming_qty = 0
    total_incoming_cost = 0

    for pid, qty_sum, cost_sum in rows_raw:
        p = prod_map.get(pid)
        if not p:
            continue

        qty_sum = int(qty_sum or 0)
        cost_sum = int(cost_sum or 0)

        if qty_sum < 1:
            continue

        rows.append({
            "product_id": pid,
            "name": p.name,
            "qty": qty_sum,
            "cost": cost_sum
        })

        total_incoming_qty += qty_sum
        total_incoming_cost += cost_sum

    rows.sort(key=lambda x: x["name"].lower())

    return render_template("kaspibot/incoming.html",
        products=products_list,
        rows=rows,
        total_incoming_qty=total_incoming_qty,
        total_incoming_cost=total_incoming_cost
    )


# ======================
# STOCK IN (✅ тек Incoming-тен)
# ======================
@bp.route("/stock-in", methods=["GET", "POST"])
def stock_in():
    incoming_rows = (
        db.session.query(
            IncomingItem.product_id,
            func.coalesce(func.sum(IncomingItem.qty), 0).label("qty_sum")
        )
        .filter(IncomingItem.is_active == True)  # noqa: E712
        .group_by(IncomingItem.product_id)
        .having(func.coalesce(func.sum(IncomingItem.qty), 0) > 0)
        .all()
    )

    incoming_map = {pid: int(qty_sum or 0) for pid, qty_sum in incoming_rows}
    incoming_product_ids = list(incoming_map.keys())

    products_list = []
    if incoming_product_ids:
        products_list = (
            Product.query
            .filter(Product.id.in_(incoming_product_ids))
            .order_by(Product.name.asc())
            .all()
        )

    if request.method == "POST":
        try:
            product_id = int(request.form.get("product_id", "0"))
            qty = int(request.form.get("qty", "0"))
        except Exception:
            flash("Қате мән", "error")
            return redirect(url_for("kaspibot.stock_in"))

        note = (request.form.get("note") or "").strip()

        if product_id <= 0:
            flash("Тауар таңдаңыз", "error")
            return redirect(url_for("kaspibot.stock_in"))

        if qty <= 0:
            flash("Саны 1-ден үлкен болсын", "error")
            return redirect(url_for("kaspibot.stock_in"))

        available_incoming = int(incoming_map.get(product_id, 0))
        if available_incoming <= 0:
            flash("Бұл тауар 'Келе жатыр' тізімінде жоқ", "error")
            return redirect(url_for("kaspibot.stock_in"))

        if qty > available_incoming:
            flash(f"Саны 'Келе жатыр' санынан көп болмауы керек (бар: {available_incoming})", "error")
            return redirect(url_for("kaspibot.stock_in"))

        p = Product.query.get_or_404(product_id)

        # FIFO: incoming-тан азайтамыз
        remaining = qty
        items = (
            IncomingItem.query
            .filter_by(product_id=product_id, is_active=True)
            .order_by(IncomingItem.created_at.asc())
            .all()
        )

        for it in items:
            if remaining <= 0:
                break

            it_qty = int(it.qty or 0)
            if it_qty <= 0:
                it.is_active = False
                continue

            take = min(it_qty, remaining)
            it.qty = it_qty - take
            remaining -= take

            if it.qty <= 0:
                it.is_active = False

        if remaining != 0:
            db.session.rollback()
            flash("Incoming азайту кезінде қате шықты (qty сәйкес емес)", "error")
            return redirect(url_for("kaspibot.stock_in"))

        # складқа қосу
        p.stock_qty = int(p.stock_qty or 0) + qty

        db.session.add(StockMovement(
            product_id=p.id,
            kind="IN",
            qty_change=qty,
            note=note or "Келе жатқаннан келіп түсті",
            created_at=datetime.utcnow()
        ))

        db.session.commit()
        flash("Келіп түсті (Incoming-тан алынды)", "success")
        return redirect(url_for("kaspibot.stock_in"))

    return render_template("kaspibot/stock_in.html", products=products_list, incoming_map=incoming_map)


# ======================
# ONWAY LIST
# ======================
@bp.route("/onway")
def onway():
    if not current_app.config['DEMO_MODE']:
        from kaspibot.services.real_report import render_onway
        return render_onway()
    products_list = (
        Product.query
        .filter(Product.onway_qty >= 1)
        .order_by(Product.name.asc())
        .all()
    )

    purchase_by_pid = {p.id: int(p.purchase_price or 0) for p in products_list}

    sums_by_pid = dict(
        db.session.query(
            OnwayItem.product_id,
            func.coalesce(func.sum(OnwayItem.sale_price), 0)
        )
        .filter(OnwayItem.is_active == True)  # noqa: E712
        .group_by(OnwayItem.product_id)
        .all()
    )

    profit_by_pid = {}
    total_onway_profit = 0

    active_items = (
        db.session.query(OnwayItem.product_id, OnwayItem.sale_price)
        .filter(OnwayItem.is_active == True)  # noqa: E712
        .all()
    )

    for pid, sale_price in active_items:
        if pid not in purchase_by_pid:
            continue

        sale_price_i = int(sale_price or 0)
        purchase_price_i = int(purchase_by_pid.get(pid, 0))
        profit, _commission, _delivery = calc_onway_profit(sale_price_i, purchase_price_i)

        profit_by_pid[pid] = profit_by_pid.get(pid, 0) + profit
        total_onway_profit += profit

    total_onway_qty = sum(int(p.onway_qty or 0) for p in products_list)
    total_onway_sum = sum(int(sums_by_pid.get(p.id, 0) or 0) for p in products_list)

    return render_template("kaspibot/onway.html",
        products=products_list,
        total_onway_qty=total_onway_qty,
        total_onway_sum=total_onway_sum,
        total_onway_profit=total_onway_profit,
        sums_by_pid=sums_by_pid,
        profit_by_pid=profit_by_pid
    )


# ✅ ONWAY ADD (alias dropdown + search)
@bp.route("/onway-add", methods=["GET", "POST"])
def onway_add():
    aliases = (
        db.session.query(ProductAlias)
        .order_by(ProductAlias.alias.asc())
        .all()
    )

    if request.method == "POST":
        alias_id_raw = (request.form.get("alias_id") or "").strip()
        sale_price_raw = (request.form.get("sale_price") or "").strip()

        try:
            alias_id = int(alias_id_raw)
        except Exception:
            alias_id = 0

        if alias_id <= 0:
            flash("Карточка атауын таңдаңыз", "error")
            return redirect(url_for("kaspibot.onway_add"))

        if not sale_price_raw:
            flash("Сатылған бағаны енгізіңіз", "error")
            return redirect(url_for("kaspibot.onway_add"))

        try:
            sale_price = int(float(sale_price_raw))
        except Exception:
            flash("Сатылған баға сан болуы керек", "error")
            return redirect(url_for("kaspibot.onway_add"))

        if sale_price <= 0:
            flash("Сатылған баға 0-ден үлкен болу керек", "error")
            return redirect(url_for("kaspibot.onway_add"))

        pa = ProductAlias.query.get_or_404(alias_id)
        p = Product.query.get_or_404(pa.product_id)

        p.onway_qty = int(p.onway_qty or 0) + 1

        db.session.add(OnwayItem(
            product_id=p.id,
            sale_price=sale_price,
            is_active=True,
            created_at=datetime.utcnow()
        ))

        db.session.add(StockMovement(
            product_id=p.id,
            kind="ONWAY",
            qty_change=1,
            sale_sum=sale_price,
            delivery_cost=0,
            commission_pct=0,
            note=f"Қолмен жолдаға қосылды (alias: {pa.alias})",
            created_at=datetime.utcnow()
        ))

        db.session.commit()
        flash("Жолдаға қосылды (+1)", "success")
        return redirect(url_for("kaspibot.onway"))

    return render_template("kaspibot/onway_add.html", aliases=aliases)


# ✅ ONWAY LIST -1 (өнім бойынша соңғы позицияны алып тастау)
@bp.route("/onway-remove/<int:product_id>", methods=["POST"])
def onway_remove(product_id: int):
    p = Product.query.get_or_404(product_id)

    if int(p.onway_qty or 0) <= 0:
        flash("Бұл тауар жолдада жоқ", "error")
        return redirect(url_for("kaspibot.onway"))

    last_item = (
        OnwayItem.query
        .filter_by(product_id=p.id, is_active=True)
        .order_by(OnwayItem.created_at.desc())
        .first()
    )
    if not last_item:
        p.onway_qty = 0
        db.session.commit()
        flash("Жолда деректері сәйкес емес еді, 0-ге түсті", "error")
        return redirect(url_for("kaspibot.onway"))

    last_item.is_active = False
    p.onway_qty = max(0, int(p.onway_qty or 0) - 1)

    db.session.add(StockMovement(
        product_id=p.id,
        kind="ONWAY",
        qty_change=-1,
        sale_sum=int(last_item.sale_price or 0),
        delivery_cost=0,
        commission_pct=0,
        note="Қолмен жолдадан алынды (-1)",
        created_at=datetime.utcnow()
    ))

    db.session.commit()
    flash("Жолдадан алынды (-1)", "success")
    return redirect(url_for("kaspibot.onway"))


# ✅ ONWAY PRODUCT DETAIL
@bp.route("/onway/<int:product_id>")
def onway_product_detail(product_id: int):
    p = Product.query.get_or_404(product_id)

    items = (
        OnwayItem.query
        .filter_by(product_id=p.id, is_active=True)
        .order_by(OnwayItem.created_at.desc())
        .all()
    )

    rows = []
    total_sum = 0
    total_profit = 0
    total_commission = 0
    total_delivery = 0

    for x in items:
        sale_price = int(x.sale_price or 0)
        profit, commission, delivery = calc_onway_profit(sale_price, int(p.purchase_price or 0))

        rows.append({
            "id": x.id,
            "created_at": x.created_at,
            "sale_price": sale_price,
            "commission": commission,
            "delivery": delivery,
            "sku": p.sku,
        "category_name": p.category_name,
        "current_price": p.current_price,
        "price_cents": p.price_cents,
        "low_stock_threshold": p.low_stock_threshold,
        "purchase_price": int(p.purchase_price or 0),
            "profit": profit
        })

        total_sum += sale_price
        total_profit += profit
        total_commission += commission
        total_delivery += delivery

    return render_template("kaspibot/onway_product_detail.html",
        product=p,
        rows=rows,
        total_sum=total_sum,
        total_profit=total_profit,
        total_commission=total_commission,
        total_delivery=total_delivery
    )


# ✅ ONWAY ITEM CANCEL (нақты позицияны -1)
@bp.route("/onway/item/<int:item_id>/cancel", methods=["POST"])
def onway_item_cancel(item_id: int):
    item = OnwayItem.query.get_or_404(item_id)

    if not item.is_active:
        flash("Бұл позиция бұрын алып тасталған", "error")
        return redirect(url_for("kaspibot.onway_product_detail", product_id=item.product_id))

    p = Product.query.get_or_404(item.product_id)

    item.is_active = False
    p.onway_qty = max(0, int(p.onway_qty or 0) - 1)

    db.session.add(StockMovement(
        product_id=p.id,
        kind="ONWAY",
        qty_change=-1,
        sale_sum=int(item.sale_price or 0),
        delivery_cost=0,
        commission_pct=0,
        note="Onway позициясы отмена (-1)",
        created_at=datetime.utcnow()
    ))

    db.session.commit()
    flash("Отмена жасалды (-1)", "success")
    return redirect(url_for("kaspibot.onway_product_detail", product_id=p.id))


# ======================
# EXCEL SALES IMPORT
# ======================
def parse_kaspi_archive_orders(file_bytes: bytes):
    wb = openpyxl.load_workbook(BytesIO(file_bytes), data_only=True)
    ws = wb.active

    headers = [ws.cell(1, c).value for c in range(1, 120)]
    header_map = {str(h).strip(): i for i, h in enumerate(headers, start=1) if h}

    required = [
        "Название товара в Kaspi Магазине",
        "Статус",
        "Количество",
        "Сумма",
        "Стоимость доставки для продавца"
    ]
    if not all(k in header_map for k in required):
        return None

    col_name = header_map["Название товара в Kaspi Магазине"]
    col_status = header_map["Статус"]
    col_qty = header_map["Количество"]
    col_sum = header_map["Сумма"]
    col_delivery = header_map["Стоимость доставки для продавца"]

    result = {}
    for r in range(2, ws.max_row + 1):
        status = ws.cell(r, col_status).value
        if not status:
            continue
        status = normalize_alias(status)

        if status not in ["выдан", "доставлен", "завершен", "завершён"]:
            continue

        name = ws.cell(r, col_name).value
        if not name:
            continue
        alias = normalize_alias(name)

        qty_val = ws.cell(r, col_qty).value or 0
        sum_val = ws.cell(r, col_sum).value or 0
        del_val = ws.cell(r, col_delivery).value or 0

        try:
            qty_i = int(float(qty_val))
        except Exception:
            qty_i = 0

        try:
            sum_i = int(round(float(sum_val)))
        except Exception:
            sum_i = 0

        try:
            del_i = int(round(float(del_val)))
        except Exception:
            del_i = 0

        if qty_i <= 0:
            continue

        if alias not in result:
            result[alias] = {"qty": 0, "sum": 0, "delivery": 0}
        result[alias]["qty"] += qty_i
        result[alias]["sum"] += sum_i
        result[alias]["delivery"] += del_i

    return result


@bp.route("/import", methods=["GET", "POST"])
def import_page():
    if request.method == "POST":
        f = request.files.get("file")
        sale_date_str = request.form.get("sale_date", "").strip()

        if not f:
            flash("Файл таңдаңыз", "error")
            return redirect(url_for("kaspibot.import_page"))

        if not sale_date_str:
            flash("Сатылған күнді таңдаңыз", "error")
            return redirect(url_for("kaspibot.import_page"))

        try:
            d = datetime.strptime(sale_date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Күн форматы қате", "error")
            return redirect(url_for("kaspibot.import_page"))

        fixed_dt = datetime.combine(d, time(12, 0, 0))

        file_bytes = f.read()
        h = file_hash(file_bytes)

        if ImportBatch.query.filter_by(file_hash=h).first():
            flash("Бұл файл бұрын жүктелген", "error")
            return redirect(url_for("kaspibot.import_page"))

        kaspi_map = parse_kaspi_archive_orders(file_bytes)

        updates = {}
        unknown = []

        if kaspi_map is not None:
            for alias, agg in kaspi_map.items():
                pa = ProductAlias.query.filter_by(alias=alias).first()
                if not pa:
                    unknown.append((alias, agg["qty"]))
                    continue
                if pa.product_id not in updates:
                    updates[pa.product_id] = {"qty": 0, "sum": 0, "delivery": 0}
                updates[pa.product_id]["qty"] += agg["qty"]
                updates[pa.product_id]["sum"] += agg["sum"]
                updates[pa.product_id]["delivery"] += agg["delivery"]
        else:
            counts = read_alias_counts_from_excel(file_bytes)
            for alias, qty in counts.items():
                pa = ProductAlias.query.filter_by(alias=alias).first()
                if not pa:
                    unknown.append((alias, qty))
                    continue
                if pa.product_id not in updates:
                    updates[pa.product_id] = {"qty": 0, "sum": 0, "delivery": 0}
                updates[pa.product_id]["qty"] += qty

        if unknown:
            return render_template("kaspibot/import_result.html", unknown=unknown, applied=[], blocked=True)

        # Қорғаныс: (склад + жолда) жетуі керек
        shortage = []
        for pid, agg in updates.items():
            p = Product.query.get(pid)
            qty = int(agg["qty"])
            can_supply = int(p.stock_qty or 0) + int(p.onway_qty or 0)
            if can_supply < qty:
                shortage.append((p.name, int(p.stock_qty or 0), int(p.onway_qty or 0), qty))

        if shortage:
            unknown_like = [
                (f"{name} (склад: {stock}, жолда: {onway})", need)
                for (name, stock, onway, need) in shortage
            ]
            return render_template("kaspibot/import_result.html", unknown=unknown_like, applied=[], blocked=True)

        # APPLY: алдымен жолдадан, қалғаны складтан
        for pid, agg in updates.items():
            p = Product.query.get(pid)
            qty = int(agg["qty"])
            sale_sum = int(agg["sum"])
            delivery = int(agg["delivery"])

            take_from_onway = min(int(p.onway_qty or 0), qty)
            if take_from_onway > 0:
                _ = consume_onway_items(p.id, take_from_onway)
                p.onway_qty = int(p.onway_qty or 0) - take_from_onway

            remaining = qty - take_from_onway
            if remaining > 0:
                p.stock_qty = int(p.stock_qty or 0) - remaining

            db.session.add(StockMovement(
                product_id=p.id,
                kind="SALE",
                qty_change=-qty,
                sale_sum=sale_sum,
                delivery_cost=delivery,
                commission_pct=12,
                note=f"Excel импорт: {f.filename} (onway:{take_from_onway}, stock:{remaining})",
                created_at=fixed_dt
            ))

        db.session.add(ImportBatch(file_name=f.filename, file_hash=h))
        db.session.commit()

        applied = [(pid, agg["qty"]) for pid, agg in updates.items()]
        return render_template("kaspibot/import_result.html", unknown=[], applied=applied, blocked=False)

    return render_template("kaspibot/import.html")


# ======================
# REPORT
# ======================
def _calc_profit_rows(start_d: date, end_d: date):
    start_dt = datetime.combine(start_d, time.min)
    end_dt = datetime.combine(end_d, time.max)

    sales = (
        db.session.query(StockMovement, Product)
        .join(Product, Product.id == StockMovement.product_id)
        .filter(StockMovement.kind == "SALE")
        .filter(StockMovement.created_at >= start_dt)
        .filter(StockMovement.created_at <= end_dt)
        .all()
    )

    by_product = {}
    by_day = {}

    for m, p in sales:
        qty = int(-m.qty_change)
        revenue = int(m.sale_sum or 0)
        delivery = int(m.delivery_cost or 0)
        pct = int(m.commission_pct or 12)

        commission = int(round(revenue * pct / 100.0))
        cogs = qty * int(p.purchase_price or 0)
        profit = revenue - commission - delivery - cogs

        if p.id not in by_product:
            by_product[p.id] = {
                "product_id": p.id,
                "name": p.name,
                "qty": 0,
                "revenue": 0,
                "commission": 0,
                "delivery": 0,
                "cogs": 0,
                "profit": 0
            }
        bp = by_product[p.id]
        bp["qty"] += qty
        bp["revenue"] += revenue
        bp["commission"] += commission
        bp["delivery"] += delivery
        bp["cogs"] += cogs
        bp["profit"] += profit

        day_key = m.created_at.date().isoformat()
        if day_key not in by_day:
            by_day[day_key] = {"day": day_key, "qty": 0, "profit": 0, "revenue": 0}
        by_day[day_key]["qty"] += qty
        by_day[day_key]["profit"] += profit
        by_day[day_key]["revenue"] += revenue

    prod_rows = sorted(by_product.values(), key=lambda x: x["name"].lower())
    day_rows = sorted(by_day.values(), key=lambda x: x["day"])

    totals = {
        "qty": sum(r["qty"] for r in prod_rows),
        "revenue": sum(r["revenue"] for r in prod_rows),
        "commission": sum(r["commission"] for r in prod_rows),
        "delivery": sum(r["delivery"] for r in prod_rows),
        "cogs": sum(r["cogs"] for r in prod_rows),
        "profit": sum(r["profit"] for r in prod_rows),
    }

    return prod_rows, day_rows, totals


@bp.route("/report")
def report():
    if not current_app.config['DEMO_MODE']:
        from kaspibot.services.real_report import render_report
        return render_report()
    today = datetime.utcnow().date()
    start_str = request.args.get("start", "")
    end_str = request.args.get("end", "")

    if start_str and end_str:
        try:
            start_d = datetime.strptime(start_str, "%Y-%m-%d").date()
            end_d = datetime.strptime(end_str, "%Y-%m-%d").date()
        except ValueError:
            start_d = today.replace(day=1)
            end_d = today
    else:
        start_d = today.replace(day=1)
        end_d = today

    prod_rows, day_rows, totals = _calc_profit_rows(start_d, end_d)

    return render_template("kaspibot/report.html",
        start=start_d.isoformat(),
        end=end_d.isoformat(),
        prod_rows=prod_rows,
        day_rows=day_rows,
        totals=totals
    )


@bp.route("/report/export")
def report_export():
    if not current_app.config['DEMO_MODE']:
        from kaspibot.services.real_report import render_report
        return render_report(export=True)
    today = datetime.utcnow().date()
    start_str = request.args.get("start", "")
    end_str = request.args.get("end", "")

    try:
        start_d = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_d = datetime.strptime(end_str, "%Y-%m-%d").date()
    except Exception:
        start_d = today.replace(day=1)
        end_d = today

    prod_rows, day_rows, totals = _calc_profit_rows(start_d, end_d)

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "By Product"

    ws1.append(["Тауар", "Сатылды (дана)", "Түсім", "Комиссия 12%", "Доставка (продавец)", "Закуп (COGS)", "Таза пайда"])
    for r in prod_rows:
        ws1.append([r["name"], r["qty"], r["revenue"], r["commission"], r["delivery"], r["cogs"], r["profit"]])

    ws1.append([])
    ws1.append(["ИТОГО", totals["qty"], totals["revenue"], totals["commission"], totals["delivery"], totals["cogs"], totals["profit"]])

    ws2 = wb.create_sheet("By Day")
    ws2.append(["Күн", "Сатылды (дана)", "Түсім", "Таза пайда"])
    for r in day_rows:
        ws2.append([r["day"], r["qty"], r["revenue"], r["profit"]])

    out = BytesIO()
    wb.save(out)
    out.seek(0)

    filename = f"report_{start_d.isoformat()}_{end_d.isoformat()}.xlsx"
    return send_file(
        out,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ======================
# API (Android үшін)
# ======================

def _product_to_dict(p: Product) -> dict:
    stock_qty = int(p.stock_qty or 0)
    onway_qty = int(getattr(p, "onway_qty", 0) or 0)
    available_qty = max(0, p.available_qty)

    incoming_qty = int(
        db.session.query(func.coalesce(func.sum(IncomingItem.qty), 0))
        .filter(
            IncomingItem.product_id == p.id,
            IncomingItem.is_active == True
        )
        .scalar()
        or 0
    )

    return {
        "id": p.id,
        "name": p.name,
        "purchase_price": int(p.purchase_price or 0),
        "inventory_source": p.inventory_source or "archive",
        "stock_qty": None if p.inventory_source == "unknown" else stock_qty,
        "onway_qty": onway_qty,
        "incoming_qty": incoming_qty,
        "available_qty": None if p.inventory_source == "unknown" or p.available_qty < 0 else available_qty,
        "reconciliation_required": p.available_qty < 0 or p.inventory_source == "unknown",
    }


@bp.get("/api/summary")
def api_summary():
    if not current_app.config['DEMO_MODE']:
        from kaspibot.services.real_report import live_summary
        return jsonify(live_summary())
    # 1) Складтағы жалпы сумма = (қолжетімді * закуп)
    products = Product.query.all()
    stock_total = 0
    for p in products:
        stock_qty = int(p.stock_qty or 0)
        onway_qty = int(getattr(p, "onway_qty", 0) or 0)
        available = max(0, stock_qty - onway_qty)
        stock_total += available * int(p.purchase_price or 0)

    # 2) Келе жатыр (Incoming) жалпы баға
    incoming_total = int(
        db.session.query(func.coalesce(func.sum(IncomingItem.qty * IncomingItem.purchase_price), 0))
        .filter(IncomingItem.is_active == True)  # noqa: E712
        .scalar()
        or 0
    )

    # 3) Жолда (Onway) жалпы сумма (active sale_price)
    onway_total = int(
        db.session.query(func.coalesce(func.sum(OnwayItem.sale_price), 0))
        .filter(OnwayItem.is_active == True)  # noqa: E712
        .scalar()
        or 0
    )

    turnover_total = int(stock_total) + int(incoming_total) + int(onway_total)

    return jsonify({
        "stockTotal": stock_total,
        "incomingTotal": incoming_total,
        "onwayTotal": onway_total,
        "turnoverTotal": turnover_total,
    })
@bp.get("/api/onway/summary")
def api_onway_summary():
    if not current_app.config['DEMO_MODE']:
        from kaspibot.services.real_report import live_summary
        return jsonify(live_summary())
    products_list = (
        Product.query
        .filter(Product.onway_qty >= 1)
        .order_by(Product.name.asc())
        .all()
    )

    purchase_by_pid = {p.id: int(p.purchase_price or 0) for p in products_list}

    sums_by_pid = dict(
        db.session.query(
            OnwayItem.product_id,
            func.coalesce(func.sum(OnwayItem.sale_price), 0)
        )
        .filter(OnwayItem.is_active == True)  # noqa: E712
        .group_by(OnwayItem.product_id)
        .all()
    )

    total_onway_profit = 0

    active_items = (
        db.session.query(OnwayItem.product_id, OnwayItem.sale_price)
        .filter(OnwayItem.is_active == True)  # noqa: E712
        .all()
    )

    for pid, sale_price in active_items:
        if pid not in purchase_by_pid:
            continue

        sale_price_i = int(sale_price or 0)
        purchase_price_i = int(purchase_by_pid.get(pid, 0))
        profit, _commission, _delivery = calc_onway_profit(sale_price_i, purchase_price_i)
        total_onway_profit += profit

    total_onway_qty = sum(int(p.onway_qty or 0) for p in products_list)
    total_onway_sum = sum(int(sums_by_pid.get(p.id, 0) or 0) for p in products_list)

    return jsonify({
        "total_onway_qty": total_onway_qty,
        "total_onway_sum": total_onway_sum,
        "total_onway_profit": total_onway_profit
    })
@bp.post("/api/onway/add")
def api_onway_add():
    data = request.get_json(silent=True) or {}

    try:
        product_id = int(data.get("product_id", 0))
        sale_price = int(data.get("sale_price", 0))
    except Exception:
        return jsonify({"ok": False, "error": "Қате мән"}), 400

    if product_id <= 0:
        return jsonify({"ok": False, "error": "Тауар таңдалмады"}), 400

    if sale_price <= 0:
        return jsonify({"ok": False, "error": "Сату бағасы 0-ден үлкен болу керек"}), 400

    p = Product.query.get(product_id)
    if not p:
        return jsonify({"ok": False, "error": "Тауар табылмады"}), 404

    p.onway_qty = int(p.onway_qty or 0) + 1

    db.session.add(OnwayItem(
        product_id=p.id,
        sale_price=sale_price,
        is_active=True,
        created_at=datetime.utcnow()
    ))

    db.session.add(StockMovement(
        product_id=p.id,
        kind="ONWAY",
        qty_change=1,
        sale_sum=sale_price,
        delivery_cost=0,
        commission_pct=0,
        note="Android арқылы жолдаға қосылды (+1)",
        created_at=datetime.utcnow()
    ))

    db.session.commit()

    return jsonify({
        "ok": True,
        "message": "Жолдаға қосылды",
        "product_id": p.id,
        "product_name": p.name
    })
@bp.get("/api/onway/items")
def api_onway_items():
    rows = (
        db.session.query(OnwayItem, Product)
        .join(Product, Product.id == OnwayItem.product_id)
        .filter(OnwayItem.is_active == True)  # noqa: E712
        .order_by(OnwayItem.id.desc())
        .all()
    )

    out = []
    for oi, p in rows:
        sale_price = int(oi.sale_price or 0)
        purchase_price = int(p.purchase_price or 0)
        profit, commission, delivery = calc_onway_profit(sale_price, purchase_price)

        out.append({
            "item_id": oi.id,
            "product_id": p.id,
            "product_name": p.name,
            "sale_price": sale_price,
            "purchase_price": purchase_price,
            "profit": int(profit or 0),
            "commission": int(commission or 0),
            "delivery_cost": int(delivery or 0),
            "created_at": oi.created_at.isoformat() if getattr(oi, "created_at", None) else ""
        })

    return jsonify(out)
@bp.post("/api/onway/remove")
def api_onway_remove():
    data = request.get_json(silent=True) or {}

    try:
        item_id = int(data.get("item_id", 0))
    except Exception:
        return jsonify({"ok": False, "error": "item_id қате"}), 400

    if item_id <= 0:
        return jsonify({"ok": False, "error": "item_id жоқ"}), 400

    oi = OnwayItem.query.get(item_id)
    if not oi or not bool(oi.is_active):
        return jsonify({"ok": False, "error": "Onway item табылмады"}), 404

    p = Product.query.get(oi.product_id)
    if not p:
        return jsonify({"ok": False, "error": "Product табылмады"}), 404

    oi.is_active = False
    p.onway_qty = max(0, int(p.onway_qty or 0) - 1)

    db.session.add(StockMovement(
        product_id=p.id,
        kind="ONWAY_REMOVE",
        qty_change=-1,
        sale_sum=int(oi.sale_price or 0),
        delivery_cost=0,
        commission_pct=0,
        note="Android арқылы жолдадан алынды (-1)",
        created_at=datetime.utcnow()
    ))

    db.session.commit()

    return jsonify({
        "ok": True,
        "message": "Жолдадан алынды",
        "item_id": oi.id,
        "product_id": p.id,
        "product_name": p.name
    })
@bp.get("/api/incoming/summary")
def api_incoming_summary():
    rows = (
        db.session.query(
            func.coalesce(func.sum(IncomingItem.qty), 0),
            func.coalesce(func.sum(IncomingItem.qty * IncomingItem.purchase_price), 0)
        )
        .filter(IncomingItem.is_active == True)
        .first()
    )

    total_incoming_qty = int(rows[0] or 0)
    total_incoming_sum = int(rows[1] or 0)

    return jsonify({
        "total_incoming_qty": total_incoming_qty,
        "total_incoming_sum": total_incoming_sum
    })
@bp.post("/api/incoming/add")
def api_incoming_add():
    data = request.get_json(silent=True) or {}

    try:
        product_id = int(data.get("product_id", 0))
        qty = int(data.get("qty", 0))
        purchase_price = int(data.get("purchase_price", 0))
    except Exception:
        return jsonify({"ok": False, "error": "Қате мән"}), 400

    if product_id <= 0:
        return jsonify({"ok": False, "error": "Тауар таңдалмады"}), 400

    if qty <= 0:
        return jsonify({"ok": False, "error": "Саны 0-ден үлкен болу керек"}), 400

    if purchase_price <= 0:
        return jsonify({"ok": False, "error": "Баға 0-ден үлкен болу керек"}), 400

    p = Product.query.get(product_id)
    if not p:
        return jsonify({"ok": False, "error": "Тауар табылмады"}), 404

    item = IncomingItem(
        product_id=p.id,
        qty=qty,
        purchase_price=purchase_price,
        is_active=True
    )

    db.session.add(item)

    db.session.add(StockMovement(
        product_id=p.id,
        kind="INCOMING_ADD",
        qty_change=qty,
        sale_sum=0,
        delivery_cost=0,
        commission_pct=0,
        note="Android арқылы келе жатырға қосылды",
        created_at=datetime.utcnow()
    ))

    db.session.commit()

    return jsonify({
        "ok": True,
        "message": "Келе жатырға қосылды",
        "product_id": p.id,
        "product_name": p.name,
        "qty": qty,
        "purchase_price": purchase_price
    })
@bp.get("/api/incoming/items")
def api_incoming_items():
    rows = (
        db.session.query(IncomingItem, Product)
        .join(Product, Product.id == IncomingItem.product_id)
        .filter(IncomingItem.is_active == True)
        .order_by(IncomingItem.id.desc())
        .all()
    )

    out = []
    for ii, p in rows:
        out.append({
            "item_id": ii.id,
            "product_id": p.id,
            "product_name": p.name,
            "qty": int(ii.qty or 0),
            "purchase_price": int(ii.purchase_price or 0),
            "stock_qty": int(p.stock_qty or 0),
            "available_qty": int(max(0, int(p.stock_qty or 0) - int(getattr(p, "onway_qty", 0) or 0))),
            "created_at": ii.created_at.isoformat() if getattr(ii, "created_at", None) else ""
        })

    return jsonify(out)
@bp.post("/api/incoming/remove")
def api_incoming_remove():
    data = request.get_json(silent=True) or {}

    try:
        item_id = int(data.get("item_id", 0))
    except Exception:
        return jsonify({"ok": False, "error": "item_id қате"}), 400

    if item_id <= 0:
        return jsonify({"ok": False, "error": "item_id жоқ"}), 400

    ii = IncomingItem.query.get(item_id)
    if not ii or not bool(ii.is_active):
        return jsonify({"ok": False, "error": "Incoming item табылмады"}), 404

    ii.is_active = False

    db.session.add(StockMovement(
        product_id=ii.product_id,
        kind="INCOMING_REMOVE",
        qty_change=-(int(ii.qty or 0)),
        sale_sum=0,
        delivery_cost=0,
        commission_pct=0,
        note="Android арқылы келе жатырдан алынды",
        created_at=datetime.utcnow()
    ))

    db.session.commit()

    return jsonify({
        "ok": True,
        "message": "Келе жатырдан алынды",
        "item_id": ii.id
    })
@bp.get("/api/in/items")
def api_in_items():
    products = Product.query.order_by(Product.id.desc()).all()

    out = []
    for p in products:
        incoming_qty = int(
            db.session.query(func.coalesce(func.sum(IncomingItem.qty), 0))
            .filter(
                IncomingItem.product_id == p.id,
                IncomingItem.is_active == True
            )
            .scalar()
            or 0
        )

        if incoming_qty <= 0:
            continue

        stock_qty = int(p.stock_qty or 0)
        onway_qty = int(getattr(p, "onway_qty", 0) or 0)
        available_qty = max(0, stock_qty - onway_qty)

        out.append({
            "product_id": p.id,
            "product_name": p.name,
            "incoming_qty": incoming_qty,
            "stock_qty": stock_qty,
            "purchase_price": int(p.purchase_price or 0),
            "onway_qty": onway_qty,
            "available_qty": available_qty
        })

    return jsonify(out)
@bp.route("/orders/archive")
def orders_archive_page():
    rows = (
        KaspiOrderSync.query
        .filter(KaspiOrderSync.state == "ARCHIVE")
        .order_by(KaspiOrderSync.created_at.desc())
        .all()
    )

    return render_template("kaspibot/orders_archive.html",
        rows=rows
    )


@bp.route("/returns")
def returns_page():
    rows = (
        KaspiOrderSync.query
        .filter(
            db.or_(
                KaspiOrderSync.status_text.ilike("%RETURN%"),
                KaspiOrderSync.status_text.ilike("%REFUND%"),
                KaspiOrderSync.status_text.ilike("%ВОЗВРАТ%"),
                KaspiOrderSync.status_text.ilike("%RETURNED%")
            )
        )
        .order_by(KaspiOrderSync.created_at.desc())
        .all()
    )

    return render_template("kaspibot/returns.html",
        rows=rows
    )
@bp.post("/api/in/remove")
def api_in_remove():
    data = request.get_json(silent=True) or {}

    try:
        product_id = int(data.get("product_id", 0))
    except Exception:
        return jsonify({"ok": False, "error": "product_id қате"}), 400

    if product_id <= 0:
        return jsonify({"ok": False, "error": "product_id жоқ"}), 400

    p = Product.query.get(product_id)
    if not p:
        return jsonify({"ok": False, "error": "Тауар табылмады"}), 404

    if int(p.stock_qty or 0) <= 0:
        return jsonify({"ok": False, "error": "Қолда саны 0"}), 400

    p.stock_qty = int(p.stock_qty or 0) - 1

    db.session.add(StockMovement(
        product_id=p.id,
        kind="IN_REMOVE",
        qty_change=-1,
        sale_sum=0,
        delivery_cost=0,
        commission_pct=0,
        note="Android арқылы келіп түскеннен алынды (-1)",
        created_at=datetime.utcnow()
    ))

    db.session.commit()

    return jsonify({
        "ok": True,
        "message": "Келіп түскеннен алынды",
        "product_id": p.id,
        "product_name": p.name
    })
@bp.post("/api/in/receive")
def api_in_receive():
    data = request.get_json(silent=True) or {}

    try:
        product_id = int(data.get("product_id", 0))
        qty = int(data.get("qty", 0))
    except Exception:
        return jsonify({"ok": False, "error": "Қате мән"}), 400

    if product_id <= 0:
        return jsonify({"ok": False, "error": "Тауар таңдалмады"}), 400

    if qty <= 0:
        return jsonify({"ok": False, "error": "Саны 0-ден үлкен болу керек"}), 400

    p = Product.query.get(product_id)
    if not p:
        return jsonify({"ok": False, "error": "Тауар табылмады"}), 404

    incoming_items = (
        IncomingItem.query
        .filter(
            IncomingItem.product_id == product_id,
            IncomingItem.is_active == True
        )
        .order_by(IncomingItem.id.asc())
        .all()
    )

    total_incoming_qty = sum(int(x.qty or 0) for x in incoming_items)

    if qty > total_incoming_qty:
        return jsonify({
            "ok": False,
            "error": "Qty incoming санынан көп болмауы керек"
        }), 400

    remain = qty

    for item in incoming_items:
        item_qty = int(item.qty or 0)

        if remain <= 0:
            break

        if item_qty <= remain:
            remain -= item_qty
            item.qty = 0
            item.is_active = False
        else:
            item.qty = item_qty - remain
            remain = 0

    p.stock_qty = int(p.stock_qty or 0) + qty

    db.session.add(StockMovement(
        product_id=p.id,
        kind="IN_RECEIVE",
        qty_change=qty,
        sale_sum=0,
        delivery_cost=0,
        commission_pct=0,
        note="Android арқылы келе жатқаннан складқа өткізілді",
        created_at=datetime.utcnow()
    ))

    db.session.commit()

    return jsonify({
        "ok": True,
        "message": "Складқа қосылды",
        "product_id": p.id,
        "product_name": p.name,
        "qty": qty
    })
@bp.route("/api/kaspi/sync-orders", methods=["POST"])
def kaspi_sync_orders():
    from kaspibot.services.sync_job import launch
    try:
        run_id = launch(current_app.config['KASPI_HISTORY_DAYS'])
        return jsonify(ok=True, queued=True, run_id=run_id), 202
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409


@bp.get('/api/kaspi/status')
def kaspi_sync_status():
    from kaspibot.models import KaspiSyncRun
    run = KaspiSyncRun.query.order_by(KaspiSyncRun.id.desc()).first()
    from kaspibot.services.automatic_sync import status as automatic_status
    return jsonify(automatic=automatic_status(), configured=bool(current_app.config.get('KASPI_API_TOKEN')),
                   last_run=None if run is None else dict(status=run.status, orders=run.orders_count,
                   lines=run.lines_count, errors=run.error_count, error_details=json.loads(run.errors_json or "[]"), message=run.message,
                   started_at=run.started_at.isoformat(),
                   finished_at=run.finished_at.isoformat() if run.finished_at else None))

# PRODUCTS
# ----------------------
# ✅ GET /api/products?q=...  (іздеу name бойынша)
@bp.get("/api/products")
def api_products():
    q = (request.args.get("q") or "").strip()

    qry = Product.query
    if q:
        # case-insensitive LIKE
        like = f"%{q.lower()}%"
        qry = qry.filter(func.lower(Product.name).like(like))

    rows = qry.order_by(Product.id.desc()).all()
    return jsonify([_product_to_dict(p) for p in rows])


# ✅ POST /api/products  (тауар қосу)
@bp.post("/api/products")
def api_products_create():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name") or "").strip()
    sku = str(data.get("sku") or "").strip()
    category_name = str(data.get("category_name") or "").strip()
    if not sku or not category_name:
        return jsonify({"ok": False, "error": "SKU и категория обязательны"}), 400
    if Product.query.filter(db.func.lower(Product.sku) == sku.lower()).first():
        return jsonify({"ok": False, "error": "SKU уже существует"}), 400
    purchase_price = data.get("purchase_price", 0)
    stock_qty = data.get("stock_qty", 0)

    if not name:
        return jsonify({"ok": False, "error": "name міндетті"}), 400

    if Product.query.filter_by(name=name).first():
        return jsonify({"ok": False, "error": "Бұл атаумен тауар бар"}), 400

    try:
        purchase_price = int(purchase_price)
        stock_qty = int(stock_qty)
    except Exception:
        return jsonify({"ok": False, "error": "purchase_price және stock_qty сан болу керек"}), 400

    if purchase_price <= 0 or stock_qty < 0:
        return jsonify({"ok": False, "error": "Дұрыс мән енгізіңіз"}), 400

    p = Product(
        name=name, sku=sku, category_name=category_name,
        purchase_price=purchase_price,
        stock_qty=stock_qty,
        onway_qty=0,
    )
    # егер модельде onway_qty бар болса, үстінде anyway 0 береміз:
    if hasattr(p, "onway_qty"):
        p.onway_qty = 0

    db.session.add(p)
    db.session.commit()

    return jsonify({"ok": True, "product": _product_to_dict(p)}), 201


# ✅ PUT /api/products/<id>  (тауар өңдеу)
@bp.put("/api/products/<int:product_id>")
def api_products_update(product_id: int):
    p = Product.query.get_or_404(product_id)
    data = request.get_json(silent=True) or {}

    name = str(data.get("name") or p.name).strip()
    purchase_price = data.get("purchase_price", p.purchase_price)
    stock_qty = data.get("stock_qty", p.stock_qty)

    if not name:
        return jsonify({"ok": False, "error": "name бос болмауы керек"}), 400

    exists = Product.query.filter(Product.name == name, Product.id != p.id).first()
    if exists:
        return jsonify({"ok": False, "error": "Бұл атаумен басқа тауар бар"}), 400

    try:
        purchase_price = int(purchase_price)
        stock_qty = int(stock_qty)
    except Exception:
        return jsonify({"ok": False, "error": "purchase_price және stock_qty сан болу керек"}), 400

    if purchase_price <= 0 or stock_qty < 0:
        return jsonify({"ok": False, "error": "Дұрыс мән енгізіңіз"}), 400

    p.name = name
    p.purchase_price = purchase_price
    p.stock_qty = stock_qty
    db.session.commit()

    return jsonify({"ok": True, "product": _product_to_dict(p)})


# ✅ DELETE /api/products/<id>  (тауар өшіру) — керек болса
@bp.delete("/api/products/<int:product_id>")
def api_products_delete(product_id: int):
    p = Product.query.get_or_404(product_id)

    # Қауіпсіздік: байланысқан деректер бар ма тексеруге болады
    # Қазір MVP: тікелей өшіреміз
    p.archived_at = datetime.utcnow().isoformat()
    db.session.commit()

    return jsonify({"ok": True})


# ----------------------
# INCOMING (Келе жатыр)
# ----------------------
# ✅ GET /api/incoming : актив incoming тізімі (group by product)
@bp.get("/api/incoming")
def api_incoming():
    rows_raw = (
        db.session.query(
            IncomingItem.product_id,
            func.coalesce(func.sum(IncomingItem.qty), 0).label("qty_sum"),
            func.coalesce(func.sum(IncomingItem.qty * IncomingItem.purchase_price), 0).label("cost_sum"),
        )
        .filter(IncomingItem.is_active == True)  # noqa: E712
        .group_by(IncomingItem.product_id)
        .all()
    )

    prod_ids = [pid for pid, _q, _c in rows_raw]
    prod_map = {}
    if prod_ids:
        for p in Product.query.filter(Product.id.in_(prod_ids)).all():
            prod_map[p.id] = p

    out_rows = []
    total_qty = 0
    total_cost = 0

    for pid, qty_sum, cost_sum in rows_raw:
        p = prod_map.get(pid)
        if not p:
            continue
        qty_sum = int(qty_sum or 0)
        cost_sum = int(cost_sum or 0)
        if qty_sum <= 0:
            continue

        out_rows.append({
            "product_id": pid,
            "name": p.name,
            "qty": qty_sum,
            "cost": cost_sum,
        })
        total_qty += qty_sum
        total_cost += cost_sum

    out_rows.sort(key=lambda x: x["name"].lower())

    return jsonify({
        "rows": out_rows,
        "total_qty": total_qty,
        "total_cost": total_cost,
    })


# ----------------------
# ONWAY (Жолда)
# ----------------------
# ✅ GET /api/onway : onway_qty>=1 тізімі (өнім бойынша)
@bp.get("/api/onway")
def api_onway():
    products_list = (
        Product.query
        .filter(Product.onway_qty >= 1)
        .order_by(Product.name.asc())
        .all()
    )

    sums_by_pid = dict(
        db.session.query(
            OnwayItem.product_id,
            func.coalesce(func.sum(OnwayItem.sale_price), 0)
        )
        .filter(OnwayItem.is_active == True)  # noqa: E712
        .group_by(OnwayItem.product_id)
        .all()
    )

    out = []
    total_onway_qty = 0
    total_onway_sum = 0

    for p in products_list:
        pid = p.id
        onway_qty = int(p.onway_qty or 0)
        ssum = int(sums_by_pid.get(pid, 0) or 0)

        out.append({
            "product_id": pid,
            "name": p.name,
            "onway_qty": onway_qty,
            "sum": ssum,
        })

        total_onway_qty += onway_qty
        total_onway_sum += ssum

    return jsonify({
        "rows": out,
        "total_onway_qty": total_onway_qty,
        "total_onway_sum": total_onway_sum,
    })


# ======================
# AI CHATBOT
# ======================
@bp.route("/chat")
def chat_page():
    """Chat UI page."""
    return render_template("kaspibot/chat.html")

@bp.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    msg = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "Empty message"}), 400

    from kaspibot.services.assistant_reference import local_reference
    reference = local_reference(msg)
    if reference:
        return jsonify(reference)

    msg_lower = msg.lower()
    if not current_app.config['DEMO_MODE'] and any(term in msg_lower for term in ('склад','summary','қорытынды','айналым','пайда')):
        return jsonify(reply='Данные физического склада, закупка и чистая прибыль пока не сверены с кабинетом. Откройте отчёт Kaspi: там только прочитанные из API заказы, без расчётной прибыли и подставленных остатков.', mode='local_reference')

    # ✅ 1) Нақты DB сұрақтарын ұстаймыз (AI-сыз, нақты жауап)
    if ("алдыңғы апта" in msg_lower or "өткен апта" in msg_lower) and ("ең көп" in msg_lower) and ("пайда" in msg_lower):
        best = top_profit_product_last_week()
        if not best:
            return jsonify({"reply": "Өткен апта бойынша сатылым деректері табылмады (SALE жоқ)."}), 200

        return jsonify({
            "reply": f"Өткен апта ({best.start} — {best.end}) ең көп пайда әкелген карточка: **{best.name}**.\nТаза пайда: **{best.profit} тг**."
        }), 200

    if ("қорытынды" in msg_lower or "summary" in msg_lower or "айналым" in msg_lower or "склад" in msg_lower):
        s = summary_numbers()
        return jsonify({
            "reply": (
                f"Қысқаша қорытынды:\n"
                f"- Склад сомасы: **{s['stockTotal']} тг**\n"
                f"- Келе жатыр: **{s['incomingTotal']} тг**\n"
                f"- Жолда: **{s['onwayTotal']} тг**\n"
                f"- Жалпы айналым: **{s['turnoverTotal']} тг**"
            )
        }), 200

    # ✅ 2) Басқа сұрақтар — Gemini-ге (жалпы көмек/инструкция)
    provider = os.environ.get('AI_PROVIDER', 'off')
    if provider == 'ollama':
        import requests
        model = os.environ.get('OLLAMA_MODEL', '')
        if not model:
            return jsonify(error='Set OLLAMA_MODEL for an installed local model'), 503
        try:
            response = requests.post(os.environ.get('OLLAMA_BASE_URL', 'http://127.0.0.1:11434').rstrip('/') + '/api/generate',
                                     json=dict(model=model, stream=False, prompt=CHATBOT_INSTRUCTIONS + '\n' + msg), timeout=60)
            response.raise_for_status()
            return jsonify(reply=response.json()['response'], mode='ai_ollama')
        except requests.RequestException:
            return jsonify(error='Local Ollama is unavailable; use the reference help'), 503
    if provider != 'gemini':
        return jsonify(error='Local help has no answer. AI is disabled; configure a free local model if needed.'), 503
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        return jsonify({"error": "Server is not configured: set GEMINI_API_KEY env var"}), 500

    try:
        client = _get_gemini_client()
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=msg,
            config=types.GenerateContentConfig(
                system_instruction=CHATBOT_INSTRUCTIONS,
                max_output_tokens=1200,
                temperature=0.6,
            ),
        )

        # толығырақ жинау (үзіліп қалмау үшін)
        text = (getattr(resp, "text", None) or "").strip()
        parts = []
        if getattr(resp, "candidates", None):
            for cand in resp.candidates:
                content = getattr(cand, "content", None)
                if content and getattr(content, "parts", None):
                    for p in content.parts:
                        t = getattr(p, "text", None)
                        if t:
                            parts.append(t)
        joined = "".join(parts).strip()
        if len(joined) > len(text):
            text = joined

        return jsonify({"reply": text or "(empty reply)", "mode": "ai_gemini"})
    except Exception as e:
        return jsonify({"error": f"AI request failed: {e}"}), 500

