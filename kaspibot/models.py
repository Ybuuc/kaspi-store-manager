from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)

    # Additive course features; existing archive IDs and original columns stay intact.
    inventory_source = db.Column(db.String(30), nullable=True)
    archived_at = db.Column(db.String(40), nullable=True)
    low_stock_threshold = db.Column(db.Integer, nullable=True)
    price_cents = db.Column(db.Integer, nullable=True)

    # Негізгі атау
    name = db.Column(
        db.String(255),
        nullable=False,
        unique=True,
    )

    # Закуп
    purchase_price = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    # Қолда бар (склад)
    stock_qty = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    # Резерв
    reserved_qty = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    # ЖОЛДА саны
    onway_qty = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    # Kaspi-дің сыртқы product id-і
    kaspi_product_id = db.Column(
        db.String(120),
        nullable=True,
        unique=True,
        index=True,
    )

    # Артикул / SKU
    sku = db.Column(
        db.String(120),
        nullable=True,
        index=True,
    )

    # Категория атауы
    category_name = db.Column(
        db.String(255),
        nullable=True,
    )

    # Фото URL
    image_url = db.Column(
        db.Text,
        nullable=True,
    )

    # Kaspi-дегі ағымдағы сату бағасы
    current_price = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    # Kaspi sync кезінде актив па
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    # Соңғы синхрон уақыты
    last_synced_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    # Kaspi-ден келген шикі JSON
    kaspi_raw_json = db.Column(
        db.Text,
        nullable=True,
    )

    # Algatop-like UI fields
    kaspi_url = db.Column(
        db.Text,
        nullable=True,
    )

    netcost = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    is_top_speed = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    city_name = db.Column(
        db.String(120),
        nullable=True,
    )

    city_price = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    min_price = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    max_price = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    step_price = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    position = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    first_position_cost = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    preorder = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    remainder = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    pickup_point_name = db.Column(
        db.String(120),
        nullable=True,
    )

    is_demping = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    is_increase = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    @property
    def available_qty(self) -> int:
        """Қолжетімді = қолда - резерв - жолда"""
        try:
            return (
                int(self.stock_qty or 0)
                - int(self.reserved_qty or 0)
                - int(self.onway_qty or 0)
            )
        except Exception:
            return 0


class ProductAlias(db.Model):
    __tablename__ = "product_aliases"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False,
    )

    alias = db.Column(
        db.String(255),
        nullable=False,
        unique=True,
    )


class StockMovement(db.Model):
    __tablename__ = "stock_movements"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False,
    )

    # IN / SALE / ONWAY / RETURN
    kind = db.Column(
        db.String(20),
        nullable=False,
    )

    # + немесе -
    qty_change = db.Column(
        db.Integer,
        nullable=False,
    )

    note = db.Column(
        db.String(255),
        nullable=True,
    )

    # SALE үшін
    sale_sum = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    delivery_cost = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    commission_pct = db.Column(
        db.Integer,
        nullable=False,
        default=12,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    # Kaspi sync үшін сыртқы кілт
    external_code = db.Column(
        db.String(120),
        nullable=True,
        index=True,
    )

    def calc_profit(self, purchase_price: int) -> int:
        qty_sold = (
            int(-self.qty_change)
            if self.kind == "SALE"
            else 0
        )

        revenue = int(self.sale_sum or 0)
        delivery = int(self.delivery_cost or 0)
        pct = int(self.commission_pct or 12)

        commission = int(
            round(revenue * pct / 100.0)
        )

        cogs = int(purchase_price or 0) * qty_sold

        return int(
            revenue - commission - delivery - cogs
        )


class ImportBatch(db.Model):
    __tablename__ = "import_batches"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    file_name = db.Column(
        db.String(255),
        nullable=False,
    )

    file_hash = db.Column(
        db.String(64),
        nullable=False,
        unique=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )


class OnwayItem(db.Model):
    """ЖОЛДА позициялары: әр жазба = 1 дана"""

    __tablename__ = "onway_items"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False,
    )

    # 1 дана позицияның сатылған бағасы
    sale_price = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    # active = жолда тұр, false = жабылды
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    # Kaspi sync үшін
    kaspi_order_key = db.Column(
        db.String(120),
        nullable=True,
        index=True,
    )


class IncomingItem(db.Model):
    """КЕЛЕ ЖАТЫР: заказ берілген, бірақ складқа келмеген."""

    __tablename__ = "incoming_items"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False,
    )

    qty = db.Column(
        db.Integer,
        nullable=False,
        default=1,
    )

    purchase_price = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )


class KaspiOrderSync(db.Model):
    __tablename__ = "kaspi_order_sync"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    # Бір заказдағы бір позицияға бір кілт
    external_key = db.Column(
        db.String(120),
        unique=True,
        nullable=False,
        index=True,
    )

    order_code = db.Column(
        db.String(64),
        nullable=False,
        index=True,
    )

    entry_id = db.Column(
        db.String(64),
        nullable=True,
        index=True,
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=True,
    )

    bucket_name = db.Column(
        db.String(40),
        nullable=True,
        index=True,
    )

    cancelled_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    delivered_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    archived_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    # Товар туралы
    title = db.Column(
        db.String(255),
        nullable=True,
    )

    qty = db.Column(
        db.Integer,
        nullable=False,
        default=1,
    )

    sale_sum = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    # Клиент туралы
    customer_name = db.Column(
        db.String(255),
        nullable=True,
    )

    customer_phone = db.Column(
        db.String(50),
        nullable=True,
    )

    # Жалпы API статусы
    state = db.Column(
        db.String(50),
        nullable=True,
    )

    status_text = db.Column(
        db.String(100),
        nullable=True,
    )

    # Detail API статусы
    detailed_state = db.Column(
        db.String(120),
        nullable=True,
        index=True,
    )

    detailed_status = db.Column(
        db.String(120),
        nullable=True,
        index=True,
    )

    # Sync белгілері
    is_onway_created = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )

    is_sale_created = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )

    is_cancelled = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )

    # Шикі JSON
    raw_json = db.Column(
        db.Text,
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ParserJob(db.Model):
    """Задание на запуск парсера."""

    __tablename__ = "parser_jobs"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    # Уникальное значение блокирует одновременные проверки.
    # После завершения задания становится None.
    active_slot = db.Column(
        db.String(20),
        unique=True,
        nullable=True,
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default="queued",
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    finished_at = db.Column(
        db.DateTime,
    )

    config_json = db.Column(
        db.Text,
        nullable=False,
    )

    message = db.Column(
        db.Text,
        default="Ожидает запуска",
    )

    processed = db.Column(
        db.Integer,
        default=0,
        nullable=False,
    )

    errors = db.Column(
        db.Integer,
        default=0,
        nullable=False,
    )


class ParserObservation(db.Model):
    """Результат проверки одного товара."""

    __tablename__ = "parser_observations"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    job_id = db.Column(
        db.Integer,
        db.ForeignKey("parser_jobs.id"),
        nullable=False,
        index=True,
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=True,
        index=True,
    )

    kaspi_id = db.Column(
        db.String(120),
        nullable=False,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    data_json = db.Column(
        db.Text,
        nullable=False,
    )

    @property
    def data(self):
        import json

        return json.loads(self.data_json)

class KaspiSyncRun(db.Model):
    __tablename__ = 'kaspi_sync_runs'
    errors_json = db.Column(db.Text, nullable=True)
    id = db.Column(db.Integer, primary_key=True)
    active_slot = db.Column(db.String(30), unique=True, nullable=True)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(30), nullable=False, default='running')
    orders_count = db.Column(db.Integer, nullable=False, default=0)
    lines_count = db.Column(db.Integer, nullable=False, default=0)
    error_count = db.Column(db.Integer, nullable=False, default=0)
    message = db.Column(db.Text, nullable=True)


class SellerCatalogImport(db.Model):
    __tablename__ = 'seller_catalog_imports'
    id = db.Column(db.Integer, primary_key=True)
    sha256 = db.Column(db.String(64), unique=True, nullable=False)
    source_date = db.Column(db.String(40), nullable=False)
    imported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    merchant_id = db.Column(db.String(120), nullable=False)
    raw_xml = db.Column(db.LargeBinary, nullable=False)


class SellerCatalogOffer(db.Model):
    __tablename__ = 'seller_catalog_offers'
    __table_args__ = (db.UniqueConstraint('import_id', 'sku'),)
    id = db.Column(db.Integer, primary_key=True)
    import_id = db.Column(db.Integer, db.ForeignKey('seller_catalog_imports.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    sku = db.Column(db.String(120), nullable=False, index=True)
    model = db.Column(db.Text, nullable=False)
    brand = db.Column(db.Text, nullable=False)
    stocks_json = db.Column(db.Text, nullable=False)
    prices_json = db.Column(db.Text, nullable=False)


class KaspiAutoSyncState(db.Model):
    __tablename__ = 'kaspi_auto_sync_state'
    id = db.Column(db.Integer, primary_key=True)
    lease_owner = db.Column(db.String(40), nullable=True)
    lease_until = db.Column(db.DateTime, nullable=True)
    next_attempt_at = db.Column(db.DateTime, nullable=True)
    last_attempt_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    catalog_status = db.Column(db.String(30), nullable=False, default='not_configured')
    catalog_message = db.Column(db.Text, nullable=True)
    catalog_checked_at = db.Column(db.DateTime, nullable=True)
    catalog_source_date = db.Column(db.String(40), nullable=True)
    orders_status = db.Column(db.String(30), nullable=False, default='not_configured')
    orders_message = db.Column(db.Text, nullable=True)
    orders_run_id = db.Column(db.Integer, nullable=True)
