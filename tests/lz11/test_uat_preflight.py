"""Ten executable *internal QA preflight* checks mapped to UAT-01…UAT-10.

These tests DO NOT replace manual UAT by an independent customer team and
must not populate the acceptance protocol as though the customer signed off.
"""

import sqlite3

import pytest

from app import create_app, hash_password


@pytest.fixture()
def setup_demo(tmp_path):
    path = tmp_path / "uat-preflight.sqlite3"
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "synthetic-preflight-only",
        "DATABASE": str(path),
    })
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0, result.output
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO users(username, password_hash, role) VALUES(?,?,?)",
            [
                ("uat-employee", hash_password("employee-pass"), "employee"),
                ("uat-admin", hash_password("admin-pass"), "admin"),
            ],
        )
        conn.execute(
            "INSERT INTO products(name,sku,category,price_cents,stock) VALUES(?,?,?,?,?)",
            ("Учебный чай", "UAT-TEA", "Сусын", 150000, 2),
        )
    return app, path


def login(client, role="employee"):
    credentials = {
        "employee": ("uat-employee", "employee-pass"),
        "admin": ("uat-admin", "admin-pass"),
    }
    username, password = credentials[role]
    response = client.post("/login", data={"username": username, "password": password})
    assert response.status_code == 302
    return client


def csrf(client):
    client.get("/products/new")
    with client.session_transaction() as s:
        return s["csrf_token"]


def dbval(path, sql):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql).fetchone()[0]


def test_uat01_authentication_logout_and_invalid_password(setup_demo):
    app, _ = setup_demo
    c = app.test_client()
    assert c.get("/products").status_code == 302
    assert c.post("/login", data={"username":"uat-employee","password":"wrong"}).status_code == 400
    login(c)
    assert c.get("/products").status_code == 200
    assert c.post("/logout").status_code == 302
    assert c.get("/products").status_code == 302


def test_uat02_product_creation_and_unique_sku(setup_demo):
    app, dbpath = setup_demo
    c = login(app.test_client())
    token = csrf(c)
    product = {"name": "Учебный кофе", "sku": "UAT-COFFEE", "category": "Сусын",
               "price": "2000.00", "stock": "5", "csrf_token": token}
    assert c.post("/products/new", data=product).status_code == 302
    assert "UAT-COFFEE" in c.get("/products").get_data(as_text=True)
    assert c.post("/products/new", data=product | {"sku": "uat-coffee"}).status_code == 400
    assert dbval(dbpath, "SELECT COUNT(*) FROM products WHERE lower(sku)='uat-coffee'") == 1


def test_uat03_case_insensitive_search_and_empty_result(setup_demo):
    app, _ = setup_demo
    c = login(app.test_client())
    assert "UAT-TEA" in c.get("/products?q=УЧЕБНЫЙ").get_data(as_text=True)
    page = c.get("/products?q=NONEXISTENT").get_data(as_text=True)
    assert "Сұрауға сәйкес тауар табылмады" in page
    assert "UAT-TEA" not in page


def test_uat04_low_stock_warning_threshold_change_and_unset(setup_demo):
    app, path = setup_demo
    c = login(app.test_client())
    token = csrf(c)
    assert c.post("/products/1/threshold", data={"threshold":"3","csrf_token":token}).status_code == 302
    assert "Төмен қалдық" in c.get("/products/1").get_data(as_text=True)
    assert c.post("/products/1/stock", data={"stock":"4","csrf_token":token}).status_code == 302
    assert "Төмен қалдық: тауар саны" not in c.get("/products/1").get_data(as_text=True)
    assert c.post("/products/1/threshold", data={"threshold":"","csrf_token":token}).status_code == 302
    assert dbval(path, "SELECT low_stock_threshold FROM products WHERE id=1") is None


def test_uat05_price_change_and_admin_price_history(setup_demo):
    app, path = setup_demo
    employee = login(app.test_client())
    token = csrf(employee)
    assert employee.post("/products/1/price", data={"price":"1600.00","csrf_token":token}).status_code == 302
    assert dbval(path, "SELECT price_cents FROM products WHERE id=1") == 160000
    admin = login(app.test_client(), "admin")
    history = admin.get("/products/1/price-history").get_data(as_text=True)
    assert "1500.00" in history and "1600.00" in history and "uat-employee" in history


def test_uat06_archive_then_restore(setup_demo):
    app, path = setup_demo
    c = login(app.test_client())
    token = csrf(c)
    assert c.post("/products/1/archive", data={"csrf_token":token}).status_code == 302
    assert "UAT-TEA" not in c.get("/products").get_data(as_text=True)
    assert "UAT-TEA" in c.get("/products/archive").get_data(as_text=True)
    assert c.post("/products/1/restore", data={"csrf_token":token}).status_code == 302
    assert "UAT-TEA" in c.get("/products").get_data(as_text=True)
    assert dbval(path, "SELECT archived_at IS NULL FROM products WHERE id=1") == 1


def test_uat07_order_transitions_and_terminal_rejection(setup_demo):
    app, path = setup_demo
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO orders(ordered_on,amount_cents,status) VALUES(?,?,?)",
                     ("2026-11-12",100000,"Жаңа"))
    c = login(app.test_client())
    token = csrf(c)
    assert c.post("/orders/1/status", data={"status":"Өңделуде","csrf_token":token}).status_code == 302
    assert c.post("/orders/1/status", data={"status":"Орындалды","csrf_token":token}).status_code == 302
    assert c.post("/orders/1/status", data={"status":"Бас тартылды","csrf_token":token}).status_code == 400
    assert dbval(path, "SELECT status FROM orders WHERE id=1") == "Орындалды"


def test_uat08_completed_sales_only_count_all_orders(setup_demo):
    app, path = setup_demo
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO orders(ordered_on,amount_cents,status) VALUES(?,?,?)",
            [
                ("2026-11-12",100000,"Орындалды"),
                ("2026-11-12",50000,"Бас тартылды"),
                ("2026-11-12",20000,"Жаңа"),
            ],
        )
    c = login(app.test_client(), "admin")
    page = c.get("/reports/sales?from=2026-11-12&to=2026-11-12").get_data(as_text=True)
    assert "<dd>3</dd>" in page and "<dd>1000.00 ₸</dd>" in page
    assert "Орындалған тапсырыстар сомасы" in page


def test_uat09_employee_admin_urls_all_return_403(setup_demo):
    app, _ = setup_demo
    c = login(app.test_client())
    for url in ("/admin", "/reports/sales", "/reports/inventory", "/admin/audit"):
        assert c.get(url).status_code == 403, url


def test_uat10_admin_inventory_and_change_audit(setup_demo):
    app, path = setup_demo
    employee = login(app.test_client())
    token = csrf(employee)
    assert employee.post("/products/1/threshold", data={"threshold":"3","csrf_token":token}).status_code == 302
    assert employee.post("/products/1/stock", data={"stock":"1","csrf_token":token}).status_code == 302
    admin = login(app.test_client(), "admin")
    inventory = admin.get("/reports/inventory").get_data(as_text=True)
    audit = admin.get("/admin/audit").get_data(as_text=True)
    assert "Жалпы тауар саны" in inventory and "Төмен қалдықтағы тауарлар саны" in inventory
    assert inventory.count("<dd>1</dd>") == 2
    assert "uat-employee" in audit and "stock" in audit
    assert dbval(path, "SELECT COUNT(*) FROM audit_log WHERE action='stock'") == 1
