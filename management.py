"""Small, runnable MVP for employee access and the product catalogue."""

import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from functools import wraps

import click
from kaspibot.database import ArchiveConnection
from flask import Blueprint, abort, g, redirect, render_template, request, session, url_for


ORDER_TRANSITIONS = {
    "Жаңа": ("Өңделуде", "Бас тартылды"),
    "Өңделуде": ("Орындалды", "Бас тартылды"),
    "Орындалды": (),
    "Бас тартылды": (),
}


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        candidate = hash_password(password, bytes.fromhex(salt_hex))
        return hmac.compare_digest(candidate, stored)
    except (ValueError, TypeError):
        return False


def price_cents(value):
    try:
        amount = Decimal(value)
        cents = amount * 100
        if amount.is_finite() and cents == cents.to_integral_value() and cents > 0:
            return int(cents)
    except (InvalidOperation, ValueError, OverflowError):
        pass
    raise ValueError("Баға 0-ден үлкен, ең көбі екі ондық таңбамен болуы керек.")


def stock_value(value):
    if not value or not value.isdecimal():
        raise ValueError("Қалдық бүтін және 0-ден кем емес болуы керек.")
    return int(value)


def init_management(app):
    bp = Blueprint('management', __name__)

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"], factory=ArchiveConnection)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_db(_error):
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    def init_db():
        from kaspibot.app import init_database
        init_database()
        db().executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until INTEGER NOT NULL DEFAULT 0,
                role TEXT NOT NULL DEFAULT 'employee' CHECK(role IN ('employee', 'admin'))
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products(id),
                actor_id INTEGER NOT NULL REFERENCES users(id),
                action TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT NOT NULL,
                occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE TABLE IF NOT EXISTS demo_orders (
                id INTEGER PRIMARY KEY,
                ordered_on TEXT NOT NULL,
                amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
                status TEXT NOT NULL CHECK(length(trim(status)) > 0),
                source TEXT NOT NULL DEFAULT 'demo' CHECK(source IN ('demo', 'kaspi'))
            );
        """)
        columns = {row["name"] for row in db().execute("PRAGMA table_info(products)")}
        if "category_name" not in columns:
            db().execute("ALTER TABLE products ADD COLUMN category_name TEXT NOT NULL DEFAULT 'Жалпы'")
        if "archived_at" not in columns:
            db().execute("ALTER TABLE products ADD COLUMN archived_at TEXT")
        if "low_stock_threshold" not in columns:
            db().execute(
                "ALTER TABLE products ADD COLUMN low_stock_threshold INTEGER "
                "CHECK(low_stock_threshold >= 0)"
            )
        user_columns = {row["name"] for row in db().execute("PRAGMA table_info(users)")}
        if "role" not in user_columns:
            db().execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'employee'")
        db().execute("CREATE UNIQUE INDEX IF NOT EXISTS products_sku_ci ON products(lower(sku))")
        db().executescript("""
            CREATE TRIGGER IF NOT EXISTS audit_log_no_update
            BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit log is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
            BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit log is immutable'); END;
            CREATE INDEX IF NOT EXISTS audit_log_product_time ON audit_log(product_id, occurred_at, id);
        """)
        db().execute("INSERT OR IGNORE INTO users(id,username,password_hash,role) VALUES(0,?,?,'employee')",
                     ('system', hash_password(secrets.token_urlsafe(32))))
        db().commit()

    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        click.echo("Database initialized")

    @app.cli.command("backup-db")
    @click.argument("output_dir", type=click.Path(file_okay=False, path_type=Path))
    def backup_db_command(output_dir):
        if not output_dir.is_dir():
            raise click.ClickException("Backup directory must already exist")
        if not Path(app.config["DATABASE"]).is_file():
            raise click.ClickException("Initialize the database before backing it up")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = output_dir / f"ksm-{stamp}-{secrets.token_hex(4)}.sqlite3"
        try:
            with closing(sqlite3.connect(target)) as backup:
                db().backup(backup)
                integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise sqlite3.DatabaseError(integrity)
        except (OSError, sqlite3.Error) as exc:
            target.unlink(missing_ok=True)
            raise click.ClickException(f"Backup failed: {exc}") from exc
        click.echo(f"Backup created: {target}")

    @app.cli.command("create-user")
    @click.argument("username")
    @click.password_option()
    @click.option("--role", type=click.Choice(["employee", "admin"]), default="employee")
    def create_user_command(username, password, role):
        init_db()
        if not username.strip() or username.strip().casefold() == "system" or not password:
            raise click.ClickException("Username and password are required")
        try:
            db().execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                         (username.strip(), hash_password(password), role))
            db().commit()
        except sqlite3.IntegrityError as exc:
            raise click.ClickException("Username already exists") from exc
        click.echo("User created")

    @app.cli.command("add-product")
    @click.argument("name")
    @click.argument("sku")
    @click.argument("price")
    @click.argument("stock_qty", type=int)
    def add_product_command(name, sku, price, stock_qty):
        init_db()
        try:
            cents = price_cents(price)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        if not name.strip() or not sku.strip() or stock_qty < 0:
            raise click.ClickException("Provide name, SKU and nonnegative stock_qty")
        try:
            db().execute("INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                         ("system", hash_password(secrets.token_urlsafe(32)), "employee"))
            system_id = db().execute("SELECT id FROM users WHERE username = 'system'").fetchone()[0]
            cursor = db().execute("INSERT INTO products (name, sku, price_cents, stock_qty) VALUES (?, ?, ?, ?)",
                                  (name.strip(), sku.strip(), cents, stock_qty))
            db().execute(
                "INSERT INTO audit_log (product_id, actor_id, action, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
                (cursor.lastrowid, system_id, "create", None, f"{name.strip()}|{sku.strip()}|Жалпы|{cents}|{stock_qty}"),
            )
            db().commit()
        except sqlite3.IntegrityError as exc:
            db().rollback()
            raise click.ClickException("SKU already exists or product data is invalid") from exc
        click.echo("Product added")

    @app.cli.command("add-demo-order")
    @click.argument("ordered_on")
    @click.argument("amount")
    @click.argument("status")
    def add_demo_order_command(ordered_on, amount, status):
        init_db()
        try:
            if date.fromisoformat(ordered_on).isoformat() != ordered_on:
                raise ValueError("Date must use YYYY-MM-DD")
            cents = price_cents(amount)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        status = status.strip()
        if status not in ORDER_TRANSITIONS:
            raise click.ClickException("Use an approved order status: " + ", ".join(ORDER_TRANSITIONS))
        db().execute(
            "INSERT INTO demo_orders (ordered_on, amount_cents, status, source) VALUES (?, ?, ?, 'demo')",
            (ordered_on, cents, status),
        )
        db().commit()
        click.echo("Demo order added")

    @app.before_request
    def load_user():
        g.user = None
        if "user_id" in session:
            g.user = db().execute("SELECT id, username, role FROM users WHERE id = ?",
                                  (session["user_id"],)).fetchone()
            if g.user is None:
                session.clear()

    @bp.get("/")
    def index():
        return redirect(url_for("management.products" if g.user else "login"))

    @bp.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("management.products"))
        error = None
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if not username or not password:
                error = "Логин мен құпиясөзді енгізіңіз."
            else:
                user = db().execute("SELECT id, password_hash, failed_attempts, locked_until FROM users WHERE username = ?",
                                    (username,)).fetchone()
                if username.casefold() == "system":
                    error = "Логин немесе құпиясөз қате."
                elif user is not None and user["locked_until"] > int(time.time()):
                    error = "Кіру уақытша бұғатталған. 15 минуттан кейін қайталап көріңіз."
                elif user is None or not verify_password(password, user["password_hash"]):
                    if user is not None:
                        failures = user["failed_attempts"] + 1
                        locked_until = int(time.time()) + 900 if failures >= 5 else 0
                        db().execute("UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
                                     (failures, locked_until, user["id"]))
                        db().commit()
                    error = "Логин немесе құпиясөз қате."
                else:
                    db().execute("UPDATE users SET failed_attempts = 0, locked_until = 0 WHERE id = ?", (user["id"],))
                    db().commit()
                    session.clear()
                    session["user_id"] = user["id"]
                    return redirect(url_for("kaspibot.index"))
        return render_template("login.html", error=error), (400 if error else 200)

    @bp.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("management.login"))

    def require_login():
        if g.user is None:
            return redirect(url_for("management.login"))
        return None

    def login_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            denied = require_login()
            return denied if denied else fn(*args, **kwargs)
        return wrapped

    def admin_required(fn):
        @wraps(fn)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] != "admin":
                abort(403, description="Бұл бетке тек әкімші кіре алады.")
            return fn(*args, **kwargs)
        return wrapped

    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return session["csrf_token"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    def check_csrf():
        if not hmac.compare_digest(request.form.get("csrf_token", ""), session.get("csrf_token", "missing")):
            abort(400, description="Сұрауды растау мүмкін болмады.")

    def product_by_id(product_id):
        product = db().execute(
            "SELECT id, name, sku, category_name, price_cents, stock_qty, archived_at, low_stock_threshold "
            "FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
        if product is None:
            abort(404, description="Тауар табылмады.")
        return product

    def product_fields(form):
        name = form.get("name", "").strip()
        sku = form.get("sku", "").strip()
        category_name = form.get("category_name", "").strip()
        if not name or not sku or not category_name:
            raise ValueError("Атауы, SKU және санат міндетті.")
        return name, sku, category_name, price_cents(form.get("price", ""))

    def audit(product_id, action, old_value, new_value):
        db().execute(
            "INSERT INTO audit_log (product_id, actor_id, action, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
            (product_id, g.user["id"], action, old_value, new_value),
        )

    @bp.get("/products")
    @login_required
    def products():
        query = request.args.get("q", "").strip()
        rows = db().execute(
            "SELECT id, name, sku, category_name, price_cents, stock_qty, low_stock_threshold FROM products "
            "WHERE archived_at IS NULL ORDER BY name, id"
        ).fetchall()
        if query:
            needle = query.casefold()
            rows = [row for row in rows if needle in row["name"].casefold()]
        return render_template("products.html", products=rows, query=query)

    @bp.get("/orders")
    @login_required
    def orders():
        status = request.args.get("status", "").strip()
        if status:
            rows = db().execute(
                "SELECT id, ordered_on, amount_cents, status, source FROM demo_orders "
                "WHERE status = ? ORDER BY ordered_on DESC, id DESC", (status,),
            ).fetchall()
        else:
            rows = db().execute(
                "SELECT id, ordered_on, amount_cents, status, source FROM demo_orders "
                "ORDER BY ordered_on DESC, id DESC"
            ).fetchall()
        return render_template("orders.html", orders=rows, status=status, transitions=ORDER_TRANSITIONS)

    @bp.post("/orders/<int:order_id>/status")
    @login_required
    def change_order_status(order_id):
        check_csrf()
        order = db().execute("SELECT id, status FROM demo_orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            abort(404, description="Тапсырыс табылмады.")
        new_status = request.form.get("status", "")
        if new_status not in ORDER_TRANSITIONS.get(order["status"], ()):
            abort(400, description="Бұл статусқа ауысуға болмайды.")
        db().execute("UPDATE demo_orders SET status = ? WHERE id = ?", (new_status, order_id))
        db().commit()
        return redirect(url_for("management.orders"))

    @bp.get("/products/archive")
    @login_required
    def product_archive():
        rows = db().execute(
            "SELECT id, name, sku, category_name, price_cents, stock_qty FROM products "
            "WHERE archived_at IS NOT NULL ORDER BY name, id"
        ).fetchall()
        return render_template("archive.html", products=rows)

    @bp.get("/products/<int:product_id>")
    @login_required
    def product_detail(product_id):
        return render_template("product.html", product=product_by_id(product_id))

    @bp.get("/products/<int:product_id>/price-history")
    @admin_required
    def price_history(product_id):
        product = product_by_id(product_id)
        rows = db().execute("""
            SELECT audit_log.id, audit_log.action, audit_log.old_value, audit_log.new_value,
                   audit_log.occurred_at, users.username
            FROM audit_log JOIN users ON users.id = audit_log.actor_id
            WHERE audit_log.product_id = ? AND audit_log.action IN ('price', 'edit')
            ORDER BY audit_log.occurred_at, audit_log.id
        """, (product_id,)).fetchall()
        changes = []
        for row in rows:
            if row["action"] == "price":
                old, new = row["old_value"], row["new_value"]
            else:
                # Earlier versions stored a changed price at the end of an edit entry.
                if not row["old_value"] or row["old_value"].count("|") < 3 or row["new_value"].count("|") < 3:
                    continue
                old = row["old_value"].rsplit("|", 1)[-1]
                new = row["new_value"].rsplit("|", 1)[-1]
                if old == new:
                    continue
            if not old or not new or not old.isdecimal() or not new.isdecimal():
                continue
            changes.append({"old": int(old), "new": int(new), "occurred_at": row["occurred_at"],
                            "username": row["username"]})
        return render_template("price_history.html", product=product, changes=changes)

    @bp.post("/products/<int:product_id>/archive")
    @login_required
    def product_archive_add(product_id):
        check_csrf()
        product = product_by_id(product_id)
        if product["archived_at"] is not None:
            abort(409, description="Тауар архивте тұр.")
        db().execute(
            "UPDATE products SET archived_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
            (product_id,),
        )
        archived_at = db().execute("SELECT archived_at FROM products WHERE id = ?", (product_id,)).fetchone()[0]
        audit(product_id, "archive", None, archived_at)
        db().commit()
        return redirect(url_for("management.product_archive"))

    @bp.post("/products/<int:product_id>/restore")
    @login_required
    def product_archive_restore(product_id):
        check_csrf()
        product = product_by_id(product_id)
        if product["archived_at"] is None:
            abort(409, description="Тауар архивте жоқ.")
        db().execute("UPDATE products SET archived_at = NULL WHERE id = ?", (product_id,))
        audit(product_id, "restore", product["archived_at"], "active")
        db().commit()
        return redirect(url_for("management.product_detail", product_id=product_id))

    @bp.route("/products/new", methods=["GET", "POST"])
    @login_required
    def product_new():
        error = None
        if request.method == "POST":
            check_csrf()
            try:
                name, sku, category_name, cents = product_fields(request.form)
                stock_qty = stock_value(request.form.get("stock_qty", ""))
                cursor = db().execute(
                    "INSERT INTO products (name, sku, category_name, price_cents, stock_qty) VALUES (?, ?, ?, ?, ?)",
                    (name, sku, category_name, cents, stock_qty),
                )
                audit(cursor.lastrowid, "create", None, f"{name}|{sku}|{category_name}|{cents}|{stock_qty}")
                db().commit()
                return redirect(url_for("management.product_detail", product_id=cursor.lastrowid))
            except ValueError as exc:
                error = str(exc)
            except sqlite3.IntegrityError:
                db().rollback()
                error = "SKU қайталанбауы тиіс."
        return render_template("product_form.html", product=None, error=error), (400 if error else 200)

    @bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
    @login_required
    def product_edit(product_id):
        product = product_by_id(product_id)
        if product["archived_at"] is not None:
            abort(409, description="Алдымен тауарды архивтен қайтарыңыз.")
        error = None
        if request.method == "POST":
            check_csrf()
            try:
                name, sku, category_name, cents = product_fields(request.form)
                old = f"{product['name']}|{product['sku']}|{product['category_name']}"
                new = f"{name}|{sku}|{category_name}"
                db().execute(
                    "UPDATE products SET name = ?, sku = ?, category_name = ?, price_cents = ? WHERE id = ?",
                    (name, sku, category_name, cents, product_id),
                )
                if old != new:
                    audit(product_id, "edit", old, new)
                if product["price_cents"] != cents:
                    audit(product_id, "price", str(product["price_cents"]), str(cents))
                db().commit()
                return redirect(url_for("management.product_detail", product_id=product_id))
            except ValueError as exc:
                error = str(exc)
            except sqlite3.IntegrityError:
                db().rollback()
                error = "SKU қайталанбауы тиіс."
        return render_template("product_form.html", product=product, error=error), (400 if error else 200)

    @bp.route("/products/<int:product_id>/price", methods=["GET", "POST"])
    @login_required
    def product_price(product_id):
        product = product_by_id(product_id)
        if product["archived_at"] is not None:
            abort(409, description="Алдымен тауарды архивтен қайтарыңыз.")
        error = None
        if request.method == "POST":
            check_csrf()
            try:
                cents = price_cents(request.form.get("price", ""))
                if cents != product["price_cents"]:
                    db().execute("UPDATE products SET price_cents = ? WHERE id = ?", (cents, product_id))
                    audit(product_id, "price", str(product["price_cents"]), str(cents))
                    db().commit()
                return redirect(url_for("management.product_detail", product_id=product_id))
            except ValueError as exc:
                error = str(exc)
        return render_template("price_form.html", product=product, error=error), (400 if error else 200)

    @bp.route("/products/<int:product_id>/threshold", methods=["GET", "POST"])
    @login_required
    def product_threshold(product_id):
        product = product_by_id(product_id)
        if product["archived_at"] is not None:
            abort(409, description="Алдымен тауарды архивтен қайтарыңыз.")
        error = None
        if request.method == "POST":
            check_csrf()
            try:
                value = request.form.get("threshold", "").strip()
                threshold = stock_value(value) if value else None
                if threshold != product["low_stock_threshold"]:
                    db().execute(
                        "UPDATE products SET low_stock_threshold = ? WHERE id = ?",
                        (threshold, product_id),
                    )
                    old = product["low_stock_threshold"]
                    audit(product_id, "threshold", str(old) if old is not None else None,
                          str(threshold) if threshold is not None else "unset")
                    db().commit()
                return redirect(url_for("management.product_detail", product_id=product_id))
            except ValueError as exc:
                error = str(exc)
        return render_template("threshold_form.html", product=product, error=error), (400 if error else 200)

    @bp.route("/products/<int:product_id>/stock_qty", methods=["GET", "POST"])
    @login_required
    def product_stock(product_id):
        product = product_by_id(product_id)
        if product["archived_at"] is not None:
            abort(409, description="Алдымен тауарды архивтен қайтарыңыз.")
        error = None
        if request.method == "POST":
            check_csrf()
            try:
                stock_qty = stock_value(request.form.get("stock_qty", ""))
                db().execute("UPDATE products SET stock_qty = ? WHERE id = ?", (stock_qty, product_id))
                audit(product_id, "stock_qty", str(product["stock_qty"]), str(stock_qty))
                db().commit()
                return redirect(url_for("management.product_detail", product_id=product_id))
            except ValueError as exc:
                error = str(exc)
        return render_template("stock_form.html", product=product, error=error), (400 if error else 200)

    @bp.get("/admin")
    @admin_required
    def admin_dashboard():
        users = db().execute(
            "SELECT id, username, role FROM users WHERE username != 'system' ORDER BY username"
        ).fetchall()
        return render_template("admin.html", users=users)

    @bp.post("/admin/users/<int:user_id>/role")
    @admin_required
    def change_user_role(user_id):
        check_csrf()
        role = request.form.get("role", "")
        if role not in ("admin", "employee"):
            abort(400, description="Рөл дұрыс таңдалмаған.")
        user = db().execute(
            "SELECT id, username, role FROM users WHERE id = ? AND username != 'system'",
            (user_id,),
        ).fetchone()
        if user is None:
            abort(404, description="Пайдаланушы табылмады.")
        if user["id"] == g.user["id"] and role != "admin":
            abort(409, description="Өз әкімші рөліңізді өзгерте алмайсыз.")
        if role != user["role"]:
            db().execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
            db().commit()
        return redirect(url_for("management.admin_dashboard"))

    @bp.get("/reports/inventory")
    @admin_required
    def inventory_report():
        summary = db().execute("""
            SELECT COUNT(*) AS total_products,
                   COUNT(CASE WHEN low_stock_threshold IS NOT NULL
                                   AND stock_qty < low_stock_threshold THEN 1 END) AS low_stock_count
            FROM products WHERE archived_at IS NULL
        """).fetchone()
        return render_template("inventory_report.html", summary=summary)

    @bp.get("/reports/sales")
    @admin_required
    def sales_report():
        start = request.args.get("from", "")
        end = request.args.get("to", "")
        if bool(start) != bool(end):
            abort(400, description="Екі күнді де таңдаңыз.")
        if start:
            try:
                if date.fromisoformat(start).isoformat() != start or date.fromisoformat(end).isoformat() != end:
                    raise ValueError
            except ValueError:
                abort(400, description="Күндер YYYY-MM-DD форматында болуы керек.")
            if start > end:
                abort(400, description="Бастапқы күн соңғы күннен кейін болмауы керек.")
            summary = db().execute("""
                SELECT COUNT(*) AS order_count,
                       COALESCE(SUM(CASE WHEN status = 'Орындалды' THEN amount_cents ELSE 0 END), 0)
                           AS total_cents
                FROM demo_orders WHERE ordered_on BETWEEN ? AND ?
            """, (start, end)).fetchone()
        else:
            summary = {"order_count": 0, "total_cents": 0}
        return render_template("sales_report.html", summary=summary, start=start, end=end)

    @bp.get("/admin/audit")
    @admin_required
    def audit_report():
        try:
            page = int(request.args.get("page", "1"))
        except ValueError:
            abort(400, description="Бет нөмірі дұрыс емес.")
        if page < 1:
            abort(400, description="Бет нөмірі дұрыс емес.")
        total = db().execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        rows = db().execute("""
            SELECT audit_log.action, audit_log.old_value, audit_log.new_value,
                   audit_log.occurred_at, products.name, products.sku, users.username
            FROM audit_log JOIN products ON products.id = audit_log.product_id
                           JOIN users ON users.id = audit_log.actor_id
            ORDER BY audit_log.occurred_at DESC, audit_log.id DESC
            LIMIT 50 OFFSET ?
        """, ((page - 1) * 50,)).fetchall()
        return render_template("audit_report.html", entries=rows, page=page, has_next=page * 50 < total)

    app.register_blueprint(bp, url_prefix='/management')


