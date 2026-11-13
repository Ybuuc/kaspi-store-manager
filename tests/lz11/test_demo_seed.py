"""Regression coverage for the versioned, synthetic LZ11 demo fixture.

The fixture contains no credentials and never substitutes for independent UAT.
"""
import json
import sqlite3
from pathlib import Path

from app import create_app


DATA = Path(__file__).resolve().parents[2] / "demo" / "lz11_seed.json"


def demo_app(tmp_path):
    db_path = tmp_path / "seed-only.sqlite3"
    app = create_app({"TESTING": True, "SECRET_KEY": "synthetic-demo-test-only",
                      "DATABASE": str(db_path)})
    return app, db_path


def test_seed_fills_catalog_orders_history_and_reports(tmp_path):
    app, path = demo_app(tmp_path)
    cmd = app.test_cli_runner().invoke(args=["seed-demo"])
    assert cmd.exit_code == 0, cmd.output
    assert "32 products, 36 synthetic orders" in cmd.output
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM products").fetchone()[0] == 32
        assert conn.execute("SELECT count(*) FROM products WHERE archived_at IS NULL").fetchone()[0] == 30
        assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 36
        assert conn.execute("SELECT count(*) FROM orders WHERE source='demo'").fetchone()[0] == 36
        assert conn.execute("SELECT count(*) FROM orders WHERE status='Орындалды'").fetchone()[0] > 0
        assert conn.execute("SELECT count(*) FROM audit_log WHERE action='price'").fetchone()[0] >= 5
        assert conn.execute("SELECT count(*) FROM audit_log WHERE action='stock'").fetchone()[0] >= 6
        assert conn.execute("SELECT count(*) FROM products WHERE stock < low_stock_threshold").fetchone()[0] >= 5
        assert conn.execute("SELECT count(*) FROM demo_seed_state").fetchone()[0] == 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    client = app.test_client()
    assert client.get("/login").status_code == 200
    with sqlite3.connect(path) as conn:
        # No real user credentials or fixed passwords in the fixture.
        assert conn.execute("SELECT count(*) FROM users WHERE role='admin'").fetchone()[0] == 0


def test_seed_is_idempotent_and_keeps_human_edits(tmp_path):
    app, path = demo_app(tmp_path)
    runner = app.test_cli_runner()
    first = runner.invoke(args=["seed-demo"])
    assert first.exit_code == 0, first.output
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE products SET stock = 77 WHERE sku = 'DEMO-ACC-002'")
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin', 'x', 'admin')")
        conn.execute("INSERT INTO orders(ordered_on,amount_cents,status) VALUES ('2026-11-13',90000,'Жаңа')")
    again = runner.invoke(args=["seed-demo"])
    assert again.exit_code == 0, again.output
    assert "already installed" in again.output
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM products").fetchone()[0] == 32
        assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 37
        assert conn.execute("SELECT stock FROM products WHERE sku='DEMO-ACC-002'").fetchone()[0] == 77
        assert conn.execute("SELECT count(*) FROM users WHERE username='admin'").fetchone()[0] == 1


def test_collision_rolls_back_before_creating_orders(tmp_path):
    app, path = demo_app(tmp_path)
    assert app.test_cli_runner().invoke(args=["init-db"]).exit_code == 0
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO products (name,sku,category,price_cents,stock) "
                     "VALUES ('USER PRODUCT','DEMO-AUD-001','Test',100,1)")
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code != 0
    assert "aborted" in result.output
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM products").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM demo_seed_state").fetchone()[0] == 0


def test_fixture_has_no_accounts_or_external_order_sources():
    fixture = json.loads(DATA.read_text(encoding="utf-8"))
    assert fixture["fixture_id"] == "lz11-demo-v1"
    assert len(fixture["products"]) == 32
    assert len(fixture["orders"]) == 36
    assert set(fixture) == {"fixture_id", "description", "products", "orders"}
    assert all(item["sku"].startswith("DEMO-") for item in fixture["products"])
    assert len({item["sku"] for item in fixture["products"]}) == 32
    assert all(o["status"] in ("Жаңа", "Өңделуде", "Орындалды", "Бас тартылды")
               for o in fixture["orders"])
