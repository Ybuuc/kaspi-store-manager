import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from unittest.mock import patch

from app import create_app, hash_password


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.tempdir.name, "app.sqlite3")
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-only-secret", "DATABASE": self.database})
        self.client = self.app.test_client()
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                       ("employee", hash_password("correct-password")))

    def tearDown(self):
        self.tempdir.cleanup()

    def login(self):
        return self.client.post("/login", data={"username": "employee", "password": "correct-password"})

    def token(self):
        self.client.get("/products/new")
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def test_login_validation_and_protected_pages(self):
        for path in ("/orders", "/products", "/products/1", "/products/new", "/products/1/edit",
                     "/products/1/price", "/products/1/stock", "/products/1/threshold"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers["Location"].endswith("/login"))
        for data, expected in (
            ({"username": "", "password": "correct-password"}, "Логин мен құпиясөзді енгізіңіз"),
            ({"username": "employee", "password": ""}, "Логин мен құпиясөзді енгізіңіз"),
            ({"username": "employee", "password": "wrong"}, "Логин немесе құпиясөз қате"),
        ):
            response = self.client.post("/login", data=data)
            self.assertEqual(response.status_code, 400)
            self.assertIn(expected, response.get_data(as_text=True))
            self.assertEqual(self.client.get("/products").status_code, 302)
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.client.get("/products").status_code, 200)
        self.client.post("/logout")
        self.assertEqual(self.client.get("/products").status_code, 302)

    def test_catalog_empty_list_detail_and_not_found(self):
        self.login()
        self.assertIn("Әзірге тауарлар жоқ", self.client.get("/products").get_data(as_text=True))
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO products (name, sku, price_cents, stock) VALUES (?, ?, ?, ?)",
                       ("Шай", "TEA-1", 125050, 0))
        page = self.client.get("/products").get_data(as_text=True)
        for value in ("Шай", "TEA-1", "1250.50", ">0<", "/products/1"):
            self.assertIn(value, page)
        detail = self.client.get("/products/1")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("TEA-1", detail.get_data(as_text=True))
        self.assertEqual(self.client.get("/products/999").status_code, 404)

    def test_100_product_list_under_two_seconds(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executemany(
                "INSERT INTO products (name, sku, price_cents, stock) VALUES (?, ?, ?, ?)",
                ((f"Product {i:03}", f"SKU-{i:03}", 100 + i, i) for i in range(100)),
            )
        self.login()
        start = time.perf_counter()
        response = self.client.get("/products")
        elapsed = time.perf_counter() - start
        print(f"100-product list response in Flask test client: {elapsed:.3f} s")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True).count('<td><a href="/products/'), 100)
        self.assertLess(elapsed, 2.0)

    def test_five_failed_logins_lock_account(self):
        for _ in range(5):
            self.client.post("/login", data={"username": "employee", "password": "wrong"})
        response = self.login()
        self.assertEqual(response.status_code, 400)
        self.assertIn("15 минут", response.get_data(as_text=True))

    def test_add_product_command(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["add-product", "Шай", "TEA-1", "1250.50", "0"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.login()
        self.assertIn("TEA-1", self.client.get("/products").get_data(as_text=True))
        with closing(sqlite3.connect(self.database)) as db:
            actor = db.execute("SELECT users.username FROM audit_log JOIN users ON users.id = audit_log.actor_id WHERE audit_log.action = 'create'").fetchone()
        self.assertEqual(actor[0], "system")
        duplicate = runner.invoke(args=["add-product", "Other", "TEA-1", "5.00", "1"])
        self.assertNotEqual(duplicate.exit_code, 0)

    def test_demo_orders_list_filter_and_empty_states(self):
        self.assertEqual(self.client.get("/orders").status_code, 302)
        self.login()
        self.assertIn("Әзірге тапсырыстар жоқ", self.client.get("/orders").get_data(as_text=True))
        runner = self.app.test_cli_runner()
        for ordered_on, amount, status in (
            ("2026-01-15", "2500.00", "Жаңа"),
            ("2026-01-16", "1200.50", "Орындалды"),
        ):
            result = runner.invoke(args=["add-demo-order", ordered_on, amount, status])
            self.assertEqual(result.exit_code, 0, result.output)
        page = self.client.get("/orders").get_data(as_text=True)
        for value in ("2026-01-15", "2026-01-16", "2500.00", "1200.50", "Жаңа",
                      "Орындалды", "Оқу дерегі", ">1<", ">2<"):
            self.assertIn(value, page)
        self.assertLess(page.index("2026-01-16"), page.index("2026-01-15"))
        filtered = self.client.get("/orders?status=Жаңа").get_data(as_text=True)
        self.assertIn("2500.00", filtered)
        self.assertNotIn("1200.50", filtered)
        self.assertIn("тапсырыс табылмады", self.client.get("/orders?status=Басқа").get_data(as_text=True))
        self.assertIn("тапсырыс табылмады", self.client.get("/orders?status=' OR 1=1 --").get_data(as_text=True))
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute("SELECT ordered_on, amount_cents, status, source FROM orders ORDER BY id").fetchall()
        self.assertEqual(rows, [("2026-01-15", 250000, "Жаңа", "demo"),
                                ("2026-01-16", 120050, "Орындалды", "demo")])

    def test_demo_order_command_rejects_invalid_data(self):
        runner = self.app.test_cli_runner()
        for args in (
            ["2026-02-30", "100.00", "Жаңа"],
            ["2026-1-5", "100.00", "Жаңа"],
            ["2026-01-15", "0", "Жаңа"],
            ["2026-01-15", "12.345", "Жаңа"],
            ["2026-01-15", "100.00", "   "],
            ["2026-01-15", "100.00", "Басқа"],
        ):
            result = runner.invoke(args=["add-demo-order", *args])
            self.assertNotEqual(result.exit_code, 0)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    def test_create_edit_search_and_inventory_audit(self):
        self.login()
        token = self.token()
        data = {"name": "Қара шай", "sku": "TEA-1", "category": "Сусын",
                "price": "1250.50", "stock": "0", "csrf_token": token}
        self.assertEqual(self.client.post("/products/new", data=data).status_code, 302)
        detail = self.client.get("/products/1").get_data(as_text=True)
        for value in ("Қара шай", "TEA-1", "Сусын", "1250.50", ">0<"):
            self.assertIn(value, detail)
        self.assertIn("Қара шай", self.client.get("/products?q=ШАЙ").get_data(as_text=True))
        self.assertIn("Сұрауға сәйкес", self.client.get("/products?q=coffee").get_data(as_text=True))
        bad = data | {"sku": "tea-1"}
        self.assertEqual(self.client.post("/products/new", data=bad).status_code, 400)
        self.assertEqual(self.client.post("/products/new", data=data | {"price": "0"}).status_code, 400)
        self.assertEqual(self.client.post("/products/new", data=data | {"name": ""}).status_code, 400)
        self.assertEqual(self.client.post("/products/new", data=data | {"stock": "-1"}).status_code, 400)
        self.assertEqual(self.client.post("/products/new", data=data | {"stock": "1.5"}).status_code, 400)
        self.assertEqual(self.client.post("/products/new", data=data | {"csrf_token": "bad"}).status_code, 400)
        edit = data | {"name": "Көк шай", "price": "1400.00"}
        self.assertEqual(self.client.post("/products/1/edit", data=edit).status_code, 302)
        self.assertIn("Көк шай", self.client.get("/products").get_data(as_text=True))
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO products (name, sku, price_cents, stock) VALUES (?, ?, ?, ?)",
                       ("Кофе", "COFFEE-1", 100, 0))
        self.assertEqual(self.client.post("/products/1/edit", data=edit | {"sku": "coffee-1"}).status_code, 400)
        self.assertIn("TEA-1", self.client.get("/products/1").get_data(as_text=True))
        self.assertEqual(self.client.post("/products/1/stock", data={"stock": "7", "csrf_token": token}).status_code, 302)
        self.assertIn(">7<", self.client.get("/products/1").get_data(as_text=True))
        self.assertEqual(self.client.post("/products/1/stock", data={"stock": "2.5", "csrf_token": token}).status_code, 400)
        with closing(sqlite3.connect(self.database)) as db:
            entries = db.execute("SELECT action, old_value, new_value, actor_id, occurred_at FROM audit_log ORDER BY id").fetchall()
        self.assertEqual([entry[0] for entry in entries], ["create", "edit", "price", "stock"])
        self.assertEqual(entries[2][1:4], ("125050", "140000", 1))
        self.assertEqual((entries[-1][1], entries[-1][2], entries[-1][3]), ("0", "7", 1))
        self.assertTrue(all(entry[4] for entry in entries))

    def test_price_change_validation_card_and_audit(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO products (name, sku, price_cents, stock) VALUES (?, ?, ?, ?)",
                       ("Шай", "TEA-1", 125050, 2))
        self.assertEqual(self.client.post("/products/1/price", data={"price": "1400.00"}).status_code, 302)
        self.login()
        token = self.token()
        form = self.client.get("/products/1/price")
        self.assertIn("1250.50", form.get_data(as_text=True))
        for invalid in ("0", "-1", "1.234", "not-a-price"):
            response = self.client.post("/products/1/price", data={"price": invalid, "csrf_token": token})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/products/1/price", data={"price": "1400.00", "csrf_token": "bad"}).status_code, 400)
        self.assertEqual(self.client.post("/products/999/price", data={"price": "1400.00", "csrf_token": token}).status_code, 404)
        response = self.client.post("/products/1/price", data={"price": "1400.00", "csrf_token": token})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/products/1"))
        self.assertIn("1400.00", self.client.get("/products/1").get_data(as_text=True))
        self.assertEqual(self.client.post("/products/1/price", data={"price": "1400.00", "csrf_token": token}).status_code, 302)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT price_cents FROM products WHERE id = 1").fetchone()[0], 140000)
            entries = db.execute("SELECT action, old_value, new_value, actor_id, occurred_at FROM audit_log").fetchall()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][:4], ("price", "125050", "140000", 1))
        self.assertTrue(entries[0][4])
        self.client.post("/products/1/archive", data={"csrf_token": token})
        self.assertEqual(self.client.get("/products/1/price").status_code, 409)
        self.assertEqual(self.client.post("/products/1/price", data={"price": "1500", "csrf_token": token}).status_code, 409)

    def test_per_product_low_stock_threshold_recalculates_warning(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO products (name, sku, price_cents, stock) VALUES (?, ?, ?, ?)",
                       ("Шай", "TEA-1", 100, 2))
            db.execute("INSERT INTO products (name, sku, price_cents, stock) VALUES (?, ?, ?, ?)",
                       ("Кофе", "COFFEE-1", 200, 1))
        self.assertEqual(self.client.post("/products/1/threshold", data={"threshold": "5"}).status_code, 302)
        self.login()
        token = self.token()
        self.assertNotIn("<span class=\"warning\">", self.client.get("/products").get_data(as_text=True))
        for invalid in ("-1", "1.5", "abc"):
            self.assertEqual(self.client.post("/products/1/threshold", data={"threshold": invalid, "csrf_token": token}).status_code, 400)
        self.assertEqual(self.client.post("/products/1/threshold", data={"threshold": "5", "csrf_token": "bad"}).status_code, 400)
        self.assertEqual(self.client.post("/products/999/threshold", data={"threshold": "5", "csrf_token": token}).status_code, 404)
        self.assertEqual(self.client.post("/products/1/threshold", data={"threshold": "5", "csrf_token": token}).status_code, 302)
        self.assertIn("<span class=\"warning\">Төмен қалдық</span>", self.client.get("/products").get_data(as_text=True))
        self.assertIn("Төмен қалдық: тауар саны", self.client.get("/products/1").get_data(as_text=True))
        self.assertNotIn("Төмен қалдық: тауар саны", self.client.get("/products/2").get_data(as_text=True))
        self.assertEqual(self.client.post("/products/1/threshold", data={"threshold": "2", "csrf_token": token}).status_code, 302)
        self.assertNotIn("<span class=\"warning\">", self.client.get("/products").get_data(as_text=True))
        self.assertEqual(self.client.post("/products/1/stock", data={"stock": "1", "csrf_token": token}).status_code, 302)
        self.assertIn("Төмен қалдық: тауар саны", self.client.get("/products/1").get_data(as_text=True))
        self.assertEqual(self.client.post("/products/1/threshold", data={"threshold": "", "csrf_token": token}).status_code, 302)
        self.assertNotIn("<span class=\"warning\">", self.client.get("/products").get_data(as_text=True))
        self.assertEqual(self.client.post("/products/1/threshold", data={"threshold": "", "csrf_token": token}).status_code, 302)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertIsNone(db.execute("SELECT low_stock_threshold FROM products WHERE id = 1").fetchone()[0])
            entries = db.execute("SELECT old_value, new_value, actor_id, occurred_at FROM audit_log WHERE action = 'threshold' ORDER BY id").fetchall()
        self.assertEqual([entry[:3] for entry in entries], [(None, "5", 1), ("5", "2", 1), ("2", "unset", 1)])
        self.assertTrue(all(entry[3] for entry in entries))
        self.client.post("/products/1/archive", data={"csrf_token": token})
        self.assertEqual(self.client.get("/products/1/threshold").status_code, 409)
        self.assertEqual(self.client.post("/products/1/threshold", data={"threshold": "5", "csrf_token": token}).status_code, 409)
        self.assertNotIn("Төмен қалдық: тауар саны", self.client.get("/products/1").get_data(as_text=True))

    def test_10000_product_search_under_one_second(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executemany(
                "INSERT INTO products (name, sku, price_cents, stock) VALUES (?, ?, ?, ?)",
                ((f"Тауар {i:05}", f"SKU-{i:05}", 100 + i, i) for i in range(10000)),
            )
        self.login()
        start = time.perf_counter()
        response = self.client.get("/products?q=ТАУАР 09999")
        elapsed = time.perf_counter() - start
        self.assertEqual(response.status_code, 200)
        self.assertIn("SKU-09999", response.get_data(as_text=True))
        self.assertLess(elapsed, 1.0)
        print(f"10000-product case-insensitive search in Flask test client: {elapsed:.3f} s")

    def test_archive_restore_and_audit(self):
        self.login()
        token = self.token()
        self.client.post("/products/new", data={
            "name": "Шай", "sku": "TEA-1", "category": "Сусын",
            "price": "1250.50", "stock": "3", "csrf_token": token,
        })
        self.assertIn("TEA-1", self.client.get("/products").get_data(as_text=True))
        self.assertEqual(self.client.post("/products/1/archive", data={"csrf_token": token}).status_code, 302)
        self.assertNotIn("TEA-1", self.client.get("/products").get_data(as_text=True))
        self.assertNotIn("TEA-1", self.client.get("/products?q=Шай").get_data(as_text=True))
        self.assertIn("TEA-1", self.client.get("/products/archive").get_data(as_text=True))
        self.assertIn("Архивтен қайтару", self.client.get("/products/1").get_data(as_text=True))
        self.assertEqual(self.client.get("/products/1/edit").status_code, 409)
        self.assertEqual(self.client.post("/products/1/stock", data={"stock": "4", "csrf_token": token}).status_code, 409)
        self.assertEqual(self.client.post("/products/1/archive", data={"csrf_token": token}).status_code, 409)
        with closing(sqlite3.connect(self.database)) as db:
            archived_at = db.execute("SELECT archived_at FROM products WHERE id = 1").fetchone()[0]
        self.assertTrue(archived_at)
        self.assertEqual(self.client.post("/products/1/restore", data={"csrf_token": token}).status_code, 302)
        self.assertIn("TEA-1", self.client.get("/products").get_data(as_text=True))
        self.assertNotIn("TEA-1", self.client.get("/products/archive").get_data(as_text=True))
        self.assertEqual(self.client.post("/products/1/restore", data={"csrf_token": token}).status_code, 409)
        with closing(sqlite3.connect(self.database)) as db:
            entries = db.execute(
                "SELECT action, old_value, new_value, actor_id, occurred_at FROM audit_log ORDER BY id"
            ).fetchall()
            restored_at = db.execute("SELECT archived_at FROM products WHERE id = 1").fetchone()[0]
        self.assertIsNone(restored_at)
        self.assertEqual([entry[0] for entry in entries], ["create", "archive", "restore"])
        self.assertEqual(entries[1][1:4], (None, archived_at, 1))
        self.assertEqual(entries[2][1:4], (archived_at, "active", 1))
        self.assertTrue(all(entry[4] for entry in entries))

    def test_archive_requires_login_and_csrf(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO products (name, sku, price_cents, stock) VALUES (?, ?, ?, ?)",
                       ("Шай", "TEA-1", 125050, 0))
        self.assertEqual(self.client.get("/products/archive").status_code, 302)
        self.assertEqual(self.client.post("/products/1/archive").status_code, 302)
        self.assertEqual(self.client.post("/products/1/restore").status_code, 302)
        self.login()
        self.assertEqual(self.client.post("/products/1/archive", data={"csrf_token": "bad"}).status_code, 400)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertIsNone(db.execute("SELECT archived_at FROM products WHERE id = 1").fetchone()[0])
        token = self.token()
        self.client.post("/products/1/archive", data={"csrf_token": token})
        self.assertEqual(self.client.post("/products/1/restore", data={"csrf_token": "bad"}).status_code, 400)
        self.assertEqual(self.client.post("/products/999/archive", data={"csrf_token": token}).status_code, 404)
        self.assertEqual(self.client.post("/products/999/restore", data={"csrf_token": token}).status_code, 404)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertIsNotNone(db.execute("SELECT archived_at FROM products WHERE id = 1").fetchone()[0])

    def test_existing_database_migrates_category_and_archive(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("DROP TABLE audit_log")
            db.execute("DROP TABLE orders")
            db.execute("DROP INDEX products_sku_ci")
            db.execute("ALTER TABLE products DROP COLUMN category")
            db.execute("ALTER TABLE products DROP COLUMN archived_at")
            db.execute("ALTER TABLE products DROP COLUMN low_stock_threshold")
            db.execute("ALTER TABLE users DROP COLUMN role")
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        with closing(sqlite3.connect(self.database)) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(products)")]
            user_columns = [row[1] for row in db.execute("PRAGMA table_info(users)")]
        self.assertIn("category", columns)
        self.assertIn("archived_at", columns)
        self.assertIn("low_stock_threshold", columns)
        self.assertIn("role", user_columns)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name = 'orders'").fetchone())

    def test_admin_authorization_is_server_side(self):
        self.assertEqual(self.client.get("/admin").status_code, 302)
        self.login()
        self.assertEqual(self.client.get("/admin").status_code, 403)
        with self.client.session_transaction() as session:
            session["role"] = "admin"
        self.assertEqual(self.client.get("/admin").status_code, 403)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE users SET role = 'admin' WHERE username = 'employee'")
        self.assertEqual(self.client.get("/admin").status_code, 200)

    def test_inventory_report_counts_current_active_products(self):
        self.login()
        self.assertEqual(self.client.get("/reports/inventory").status_code, 403)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE users SET role = 'admin' WHERE username = 'employee'")
            db.executemany(
                "INSERT INTO products (name, sku, price_cents, stock, low_stock_threshold, archived_at) "
                "VALUES (?, ?, 100, ?, ?, ?)",
                [("Low", "L", 1, 2, None), ("Equal", "E", 2, 2, None),
                 ("No threshold", "N", 0, None, None),
                 ("Archived", "A", 0, 2, "2026-01-01")],
            )
        page = self.client.get("/reports/inventory").get_data(as_text=True)
        self.assertIn("<dd>3</dd>", page)
        self.assertIn("<dd>1</dd>", page)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE products SET stock = 2 WHERE sku = 'L'")
        self.assertIn("<dd>0</dd>", self.client.get("/reports/inventory").get_data(as_text=True))

    def test_admin_can_change_role_and_employee_loses_direct_access(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
                       ("owner", hash_password("owner-password")))
            employee_id = db.execute("SELECT id FROM users WHERE username = 'employee'").fetchone()[0]
            owner_id = db.execute("SELECT id FROM users WHERE username = 'owner'").fetchone()[0]
        self.client.post("/login", data={"username": "owner", "password": "owner-password"})
        token = self.token()
        path = f"/admin/users/{employee_id}/role"
        self.assertEqual(self.client.post(path, data={"role": "admin"}).status_code, 400)
        self.assertEqual(self.client.post(path, data={"csrf_token": token, "role": "invalid"}).status_code, 400)
        self.assertEqual(self.client.post(f"/admin/users/{owner_id}/role",
                                          data={"csrf_token": token, "role": "employee"}).status_code, 409)
        self.assertEqual(self.client.post(path, data={"csrf_token": token, "role": "admin"}).status_code, 302)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT role FROM users WHERE id = ?", (employee_id,)).fetchone()[0], "admin")
        self.client.post("/logout")
        self.login()
        self.assertEqual(self.client.get("/admin").status_code, 200)
        token = self.token()
        self.assertEqual(self.client.post(f"/admin/users/{owner_id}/role",
                                          data={"csrf_token": token, "role": "employee"}).status_code, 302)
        self.client.post("/logout")
        self.client.post("/login", data={"username": "owner", "password": "owner-password"})
        self.assertEqual(self.client.get("/admin").status_code, 403)
        self.assertEqual(self.client.get("/reports/inventory").status_code, 403)
        self.assertEqual(self.client.post(path, data={"csrf_token": token, "role": "employee"}).status_code, 403)
        page = self.client.get("/products").get_data(as_text=True)
        self.assertNotIn("Қойма есебі", page)
        self.assertNotIn("Әкімші</a>", page)

    def test_price_history_shows_chronological_actor_and_legacy_edits(self):
        self.login()
        self.assertEqual(self.client.get("/products/1/price-history").status_code, 403)
        self.client.post("/logout")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE users SET role = 'admin' WHERE username = 'employee'")
            db.execute("INSERT INTO products (name, sku, price_cents, stock) VALUES ('Tea', 'T', 300, 0)")
            actor = db.execute("SELECT id FROM users WHERE username = 'employee'").fetchone()[0]
            db.executemany(
                "INSERT INTO audit_log (product_id, actor_id, action, old_value, new_value, occurred_at) "
                "VALUES (1, ?, ?, ?, ?, ?)",
                [(actor, "edit", "Tea|T|General|100", "Tea|T|General|200", "2026-01-01T00:00:00Z"),
                 (actor, "edit", "Tea|T|General", "Tea|T|Other", "2026-01-02T00:00:00Z"),
                 (actor, "price", "200", "300", "2026-01-03T00:00:00Z")],
            )
        self.assertEqual(self.client.get("/products/1/price-history").status_code, 302)
        self.login()
        page = self.client.get("/products/1/price-history").get_data(as_text=True)
        self.assertLess(page.index("2026-01-01"), page.index("2026-01-03"))
        self.assertIn("1.00 ₸", page)
        self.assertIn("2.00 ₸", page)
        self.assertIn("3.00 ₸", page)
        self.assertEqual(page.count("<tr><td>2026-01-"), 2)
        self.assertIn("employee", page)

    def test_audit_report_is_admin_only_and_entries_are_immutable(self):
        self.login()
        self.assertEqual(self.client.get("/admin/audit").status_code, 403)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE users SET role = 'admin' WHERE username = 'employee'")
        token = self.token()
        self.client.post("/products/new", data={"name": "Tea", "sku": "T", "category": "Food",
                                                "price": "1.00", "stock": "2", "csrf_token": token})
        self.client.post("/products/1/price", data={"price": "2.00", "csrf_token": token})
        self.client.post("/products/1/stock", data={"stock": "1", "csrf_token": token})
        page = self.client.get("/admin/audit").get_data(as_text=True)
        for value in ("Tea", "employee", "create", "price", "stock", "UTC"):
            self.assertIn(value, page)
        self.assertEqual(self.client.get("/admin/audit?page=0").status_code, 400)
        with closing(sqlite3.connect(self.database)) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE audit_log SET new_value = 'tampered' WHERE id = 1")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM audit_log WHERE id = 1")

    def test_sales_report_date_range_empty_period_and_validation(self):
        self.login()
        self.assertEqual(self.client.get("/reports/sales").status_code, 403)
        self.client.post("/logout")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE users SET role = 'admin' WHERE username = 'employee'")
            db.executemany("INSERT INTO orders (ordered_on, amount_cents, status) VALUES (?, ?, ?)",
                           [("2026-01-01", 100, "Орындалды"), ("2026-01-02", 250, "Орындалды"),
                            ("2026-01-02", 500, "Бас тартылды"), ("2026-02-01", 400, "Жаңа")])
        self.assertEqual(self.client.get("/reports/sales").status_code, 302)
        self.login()
        for path in ("/reports/sales?from=2026-01-01", "/reports/sales?from=bad&to=2026-01-02",
                     "/reports/sales?from=2026-02-01&to=2026-01-01"):
            self.assertEqual(self.client.get(path).status_code, 400)
        page = self.client.get("/reports/sales?from=2026-01-01&to=2026-01-02").get_data(as_text=True)
        self.assertIn("<dd>3</dd>", page)
        self.assertIn("3.50 ₸", page)
        empty = self.client.get("/reports/sales?from=2026-03-01&to=2026-03-31").get_data(as_text=True)
        self.assertIn("<dd>0</dd>", empty)
        self.assertIn("0.00 ₸", empty)

    def test_order_status_follows_approved_transitions_and_persists(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["add-demo-order", "2026-01-01", "10.00", "Жаңа"])
        self.assertEqual(result.exit_code, 0, result.output)
        path = "/orders/1/status"
        self.assertEqual(self.client.post(path, data={"status": "Өңделуде"}).status_code, 302)
        self.login()
        token = self.token()
        page = self.client.get("/orders").get_data(as_text=True)
        self.assertIn("Өңделуде", page)
        self.assertIn("Бас тартылды", page)
        self.assertNotIn('<option value="Орындалды">', page)
        for payload in ({"status": "Өңделуде"}, {"csrf_token": "bad", "status": "Өңделуде"},
                        {"csrf_token": token, "status": "Орындалды"},
                        {"csrf_token": token, "status": "Басқа"}):
            self.assertEqual(self.client.post(path, data=payload).status_code, 400)
        self.assertEqual(self.client.post("/orders/999/status",
                                          data={"csrf_token": token, "status": "Өңделуде"}).status_code, 404)
        self.assertEqual(self.client.post(path, data={"csrf_token": token, "status": "Өңделуде"}).status_code, 302)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT status FROM orders WHERE id = 1").fetchone()[0], "Өңделуде")
        self.assertEqual(self.client.post(path, data={"csrf_token": token, "status": "Орындалды"}).status_code, 302)
        self.assertEqual(self.client.post(path, data={"csrf_token": token, "status": "Бас тартылды"}).status_code, 400)
        page = self.client.get("/orders").get_data(as_text=True)
        self.assertIn("Өзгерту мүмкін емес", page)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT status FROM orders WHERE id = 1").fetchone()[0], "Орындалды")
        self.assertEqual(runner.invoke(args=["add-demo-order", "2026-01-02", "5.00", "Жаңа"]).exit_code, 0)
        self.assertEqual(self.client.post("/orders/2/status",
                                          data={"csrf_token": token, "status": "Бас тартылды"}).status_code, 302)
        self.assertEqual(self.client.post("/orders/2/status",
                                          data={"csrf_token": token, "status": "Өңделуде"}).status_code, 400)

    def test_secure_session_cookie_configuration(self):
        with patch.dict(os.environ, {"KSM_HTTPS": "1", "KSM_SECRET_KEY": "production-test-only"}):
            app = create_app({"TESTING": True, "DATABASE": os.path.join(self.tempdir.name, "secure.sqlite3")})
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])

    def test_backup_command_creates_restorable_snapshot(self):
        self.login()
        token = self.token()
        self.client.post("/products/new", data={"name": "Tea", "sku": "T", "category": "Food",
                                                "price": "1.00", "stock": "2", "csrf_token": token})
        backup_dir = os.path.join(self.tempdir.name, "backups")
        runner = self.app.test_cli_runner()
        self.assertNotEqual(runner.invoke(args=["backup-db", backup_dir]).exit_code, 0)
        os.mkdir(backup_dir)
        result = runner.invoke(args=["backup-db", backup_dir])
        self.assertEqual(result.exit_code, 0, result.output)
        files = os.listdir(backup_dir)
        self.assertEqual(len(files), 1)
        with closing(sqlite3.connect(os.path.join(backup_dir, files[0]))) as snapshot:
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(snapshot.execute("SELECT sku FROM products").fetchone()[0], "T")
            self.assertEqual(snapshot.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
