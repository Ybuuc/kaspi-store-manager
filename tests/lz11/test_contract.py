"""LZ11 pytest integration smoke using an isolated, disposable SQLite database."""

import sqlite3

import pytest

from app import create_app, hash_password


@pytest.fixture()
def seeded_app(tmp_path):
    database = tmp_path / "lz11.sqlite3"
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "lz11-test-only-secret",
        "DATABASE": str(database),
    })
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0, result.output
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("employee-uat", hash_password("uat-password"), "employee"),
        )
        connection.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin-uat", hash_password("admin-password"), "admin"),
        )
        connection.execute(
            "INSERT INTO orders (ordered_on, amount_cents, status) VALUES (?, ?, ?)",
            ("2026-11-12", 100000, "Жаңа"),
        )
    return app, database


def login(client, username="employee-uat", password="uat-password"):
    response = client.post("/login", data={"username": username, "password": password})
    assert response.status_code == 302


def test_public_login_and_protected_catalog(seeded_app):
    app, _ = seeded_app
    client = app.test_client()
    assert client.get("/login").status_code == 200
    response = client.get("/products")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    bad = client.post("/login", data={"username": "employee-uat", "password": "invalid"})
    assert bad.status_code == 400
    assert client.get("/products").status_code == 302


def test_employee_cannot_access_admin_urls(seeded_app):
    app, _ = seeded_app
    client = app.test_client()
    login(client)
    for path in ("/admin", "/reports/inventory", "/reports/sales", "/admin/audit"):
        assert client.get(path).status_code == 403, path


def test_order_status_rules_persist_and_reject_terminal_changes(seeded_app):
    app, database = seeded_app
    client = app.test_client()
    login(client)
    client.get("/products/new")
    with client.session_transaction() as sess:
        csrf_token = sess["csrf_token"]
    invalid = client.post("/orders/1/status", data={
        "csrf_token": csrf_token,
        "status": "Орындалды",
    })
    assert invalid.status_code == 400  # Жаңа -> Орындалды is forbidden
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM orders WHERE id=1").fetchone()[0] == "Жаңа"
    for next_status in ("Өңделуде", "Орындалды"):
        response = client.post("/orders/1/status", data={
            "csrf_token": csrf_token,
            "status": next_status,
        })
        assert response.status_code == 302
    final = client.post("/orders/1/status", data={
        "csrf_token": csrf_token,
        "status": "Бас тартылды",
    })
    assert final.status_code == 400
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM orders WHERE id=1").fetchone()[0] == "Орындалды"


def test_post_requires_csrf(seeded_app):
    app, database = seeded_app
    client = app.test_client()
    login(client)
    response = client.post("/products/new", data={
        "name": "Тестовый чай", "sku": "UAT-CSRF",
        "category": "Тест", "price": "1000.00", "stock": "1",
    })
    assert response.status_code == 400
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
