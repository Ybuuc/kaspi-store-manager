"""Small, runnable MVP for employee access and the product catalogue."""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

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
                locked_until INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                sku TEXT NOT NULL UNIQUE,
                price_cents INTEGER NOT NULL CHECK(price_cents > 0),
                stock INTEGER NOT NULL CHECK(stock >= 0)
            );
        """)
        db().commit()

    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        click.echo("Database initialized")

    @app.cli.command("create-user")
    @click.argument("username")
    @click.password_option()
    def create_user_command(username, password):
        init_db()
        if not username.strip() or not password:
            raise click.ClickException("Username and password are required")
        try:
            db().execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                         (username.strip(), hash_password(password)))
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
            amount = Decimal(price)
            cents = amount * 100
        except InvalidOperation as exc:
            raise click.ClickException("Price must be a positive amount") from exc
        if not name.strip() or not sku.strip() or not amount.is_finite() or cents != cents.to_integral_value() or cents <= 0 or stock < 0:
            raise click.ClickException("Provide name, SKU, positive price with two decimals and nonnegative stock")
        try:
            db().execute("INSERT INTO products (name, sku, price_cents, stock) VALUES (?, ?, ?, ?)",
                         (name.strip(), sku.strip(), int(cents), stock))
            db().commit()
        except sqlite3.IntegrityError as exc:
            raise click.ClickException("SKU already exists or product data is invalid") from exc
        click.echo("Product added")

    @app.before_request
    def load_user():
        g.user = None
        if "user_id" in session:
            g.user = db().execute("SELECT id, username FROM users WHERE id = ?",
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
                if user is not None and user["locked_until"] > int(time.time()):
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

    @app.get("/products")
    def products():
        denied = require_login()
        if denied:
            return denied
        rows = db().execute(
            "SELECT id, name, sku, price_cents, stock FROM products ORDER BY name, id"
        ).fetchall()
        return render_template("products.html", products=rows)

    @app.get("/products/<int:product_id>")
    def product_detail(product_id):
        denied = require_login()
        if denied:
            return denied
        product = db().execute(
            "SELECT id, name, sku, price_cents, stock FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
        if product is None:
            abort(404, description="Тауар табылмады.")
        return render_template("product.html", product=product)

    return app


if __name__ == "__main__":
    create_app().run()
