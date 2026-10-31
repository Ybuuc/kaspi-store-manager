"""Small, runnable MVP for employee access and the product catalogue."""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from functools import wraps

import click
from flask import Flask, abort, g, redirect, render_template, request, session, url_for


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


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("KSM_SECRET_KEY"),
        DATABASE=str(Path(app.instance_path) / "kaspi-store-manager.sqlite3"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("KSM_HTTPS") == "1",
    )
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("Set KSM_SECRET_KEY to a long random value before starting the app")
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_db(_error):
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    def init_db():
        db().executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until INTEGER NOT NULL DEFAULT 0,
                role TEXT NOT NULL DEFAULT 'employee' CHECK(role IN ('employee', 'admin'))
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                sku TEXT NOT NULL UNIQUE,
                price_cents INTEGER NOT NULL CHECK(price_cents > 0),
                stock INTEGER NOT NULL CHECK(stock >= 0),
                archived_at TEXT
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
        """)
        columns = {row["name"] for row in db().execute("PRAGMA table_info(products)")}
        if "category" not in columns:
            db().execute("ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT 'Жалпы'")
        if "archived_at" not in columns:
            db().execute("ALTER TABLE products ADD COLUMN archived_at TEXT")
        user_columns = {row["name"] for row in db().execute("PRAGMA table_info(users)")}
        if "role" not in user_columns:
            db().execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'employee'")
        db().execute("CREATE UNIQUE INDEX IF NOT EXISTS products_sku_ci ON products(lower(sku))")
        db().commit()

    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        click.echo("Database initialized")

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
    @click.argument("stock", type=int)
    def add_product_command(name, sku, price, stock):
        init_db()
        try:
            cents = price_cents(price)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        if not name.strip() or not sku.strip() or stock < 0:
            raise click.ClickException("Provide name, SKU and nonnegative stock")
        try:
            db().execute("INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                         ("system", hash_password(secrets.token_urlsafe(32)), "employee"))
            system_id = db().execute("SELECT id FROM users WHERE username = 'system'").fetchone()[0]
            cursor = db().execute("INSERT INTO products (name, sku, price_cents, stock) VALUES (?, ?, ?, ?)",
                                  (name.strip(), sku.strip(), cents, stock))
            db().execute(
                "INSERT INTO audit_log (product_id, actor_id, action, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
                (cursor.lastrowid, system_id, "create", None, f"{name.strip()}|{sku.strip()}|Жалпы|{cents}|{stock}"),
            )
            db().commit()
        except sqlite3.IntegrityError as exc:
            db().rollback()
            raise click.ClickException("SKU already exists or product data is invalid") from exc
        click.echo("Product added")

    @app.before_request
    def load_user():
        g.user = None
        if "user_id" in session:
            g.user = db().execute("SELECT id, username, role FROM users WHERE id = ?",
                                  (session["user_id"],)).fetchone()
            if g.user is None:
                session.clear()

    @app.get("/")
    def index():
        return redirect(url_for("products" if g.user else "login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("products"))
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
                    return redirect(url_for("products"))
        return render_template("login.html", error=error), (400 if error else 200)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    def require_login():
        if g.user is None:
            return redirect(url_for("login"))
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
            "SELECT id, name, sku, category, price_cents, stock, archived_at FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
        if product is None:
            abort(404, description="Тауар табылмады.")
        return product

    def product_fields(form):
        name = form.get("name", "").strip()
        sku = form.get("sku", "").strip()
        category = form.get("category", "").strip()
        if not name or not sku or not category:
            raise ValueError("Атауы, SKU және санат міндетті.")
        return name, sku, category, price_cents(form.get("price", ""))

    def audit(product_id, action, old_value, new_value):
        db().execute(
            "INSERT INTO audit_log (product_id, actor_id, action, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
            (product_id, g.user["id"], action, old_value, new_value),
        )

    @app.get("/products")
    @login_required
    def products():
        query = request.args.get("q", "").strip()
        rows = db().execute(
            "SELECT id, name, sku, category, price_cents, stock FROM products "
            "WHERE archived_at IS NULL ORDER BY name, id"
        ).fetchall()
        if query:
            needle = query.casefold()
            rows = [row for row in rows if needle in row["name"].casefold()]
        return render_template("products.html", products=rows, query=query)

    @app.get("/products/archive")
    @login_required
    def product_archive():
        rows = db().execute(
            "SELECT id, name, sku, category, price_cents, stock FROM products "
            "WHERE archived_at IS NOT NULL ORDER BY name, id"
        ).fetchall()
        return render_template("archive.html", products=rows)

    @app.get("/products/<int:product_id>")
    @login_required
    def product_detail(product_id):
        return render_template("product.html", product=product_by_id(product_id))

    @app.post("/products/<int:product_id>/archive")
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
        return redirect(url_for("product_archive"))

    @app.post("/products/<int:product_id>/restore")
    @login_required
    def product_archive_restore(product_id):
        check_csrf()
        product = product_by_id(product_id)
        if product["archived_at"] is None:
            abort(409, description="Тауар архивте жоқ.")
        db().execute("UPDATE products SET archived_at = NULL WHERE id = ?", (product_id,))
        audit(product_id, "restore", product["archived_at"], "active")
        db().commit()
        return redirect(url_for("product_detail", product_id=product_id))

    @app.route("/products/new", methods=["GET", "POST"])
    @login_required
    def product_new():
        error = None
        if request.method == "POST":
            check_csrf()
            try:
                name, sku, category, cents = product_fields(request.form)
                stock = stock_value(request.form.get("stock", ""))
                cursor = db().execute(
                    "INSERT INTO products (name, sku, category, price_cents, stock) VALUES (?, ?, ?, ?, ?)",
                    (name, sku, category, cents, stock),
                )
                audit(cursor.lastrowid, "create", None, f"{name}|{sku}|{category}|{cents}|{stock}")
                db().commit()
                return redirect(url_for("product_detail", product_id=cursor.lastrowid))
            except ValueError as exc:
                error = str(exc)
            except sqlite3.IntegrityError:
                db().rollback()
                error = "SKU қайталанбауы тиіс."
        return render_template("product_form.html", product=None, error=error), (400 if error else 200)

    @app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
    @login_required
    def product_edit(product_id):
        product = product_by_id(product_id)
        if product["archived_at"] is not None:
            abort(409, description="Алдымен тауарды архивтен қайтарыңыз.")
        error = None
        if request.method == "POST":
            check_csrf()
            try:
                name, sku, category, cents = product_fields(request.form)
                old = f"{product['name']}|{product['sku']}|{product['category']}"
                new = f"{name}|{sku}|{category}"
                db().execute(
                    "UPDATE products SET name = ?, sku = ?, category = ?, price_cents = ? WHERE id = ?",
                    (name, sku, category, cents, product_id),
                )
                if old != new:
                    audit(product_id, "edit", old, new)
                if product["price_cents"] != cents:
                    audit(product_id, "price", str(product["price_cents"]), str(cents))
                db().commit()
                return redirect(url_for("product_detail", product_id=product_id))
            except ValueError as exc:
                error = str(exc)
            except sqlite3.IntegrityError:
                db().rollback()
                error = "SKU қайталанбауы тиіс."
        return render_template("product_form.html", product=product, error=error), (400 if error else 200)

    @app.route("/products/<int:product_id>/price", methods=["GET", "POST"])
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
                return redirect(url_for("product_detail", product_id=product_id))
            except ValueError as exc:
                error = str(exc)
        return render_template("price_form.html", product=product, error=error), (400 if error else 200)

    @app.route("/products/<int:product_id>/stock", methods=["GET", "POST"])
    @login_required
    def product_stock(product_id):
        product = product_by_id(product_id)
        if product["archived_at"] is not None:
            abort(409, description="Алдымен тауарды архивтен қайтарыңыз.")
        error = None
        if request.method == "POST":
            check_csrf()
            try:
                stock = stock_value(request.form.get("stock", ""))
                db().execute("UPDATE products SET stock = ? WHERE id = ?", (stock, product_id))
                audit(product_id, "stock", str(product["stock"]), str(stock))
                db().commit()
                return redirect(url_for("product_detail", product_id=product_id))
            except ValueError as exc:
                error = str(exc)
        return render_template("stock_form.html", product=product, error=error), (400 if error else 200)

    @app.get("/admin")
    @admin_required
    def admin_dashboard():
        count = db().execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return render_template("admin.html", user_count=count)

    return app


if __name__ == "__main__":
    create_app().run()
