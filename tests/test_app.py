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
        for path in ("/products", "/products/1", "/products/new", "/products/1/edit", "/products/1/stock"):
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
        duplicate = runner.invoke(args=["add-product", "Other", "TEA-1", "5.00", "1"])
        self.assertNotEqual(duplicate.exit_code, 0)

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
        self.assertEqual([entry[0] for entry in entries], ["create", "edit", "stock"])
        self.assertEqual((entries[-1][1], entries[-1][2], entries[-1][3]), ("0", "7", 1))
        self.assertTrue(all(entry[4] for entry in entries))

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

    def test_existing_database_migrates_category(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("DROP TABLE audit_log")
            db.execute("DROP INDEX products_sku_ci")
            db.execute("ALTER TABLE products DROP COLUMN category")
            db.execute("ALTER TABLE users DROP COLUMN role")
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        with closing(sqlite3.connect(self.database)) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(products)")]
            user_columns = [row[1] for row in db.execute("PRAGMA table_info(users)")]
        self.assertIn("category", columns)
        self.assertIn("role", user_columns)

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

    def test_secure_session_cookie_configuration(self):
        with patch.dict(os.environ, {"KSM_HTTPS": "1", "KSM_SECRET_KEY": "production-test-only"}):
            app = create_app({"TESTING": True, "DATABASE": os.path.join(self.tempdir.name, "secure.sqlite3")})
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])


if __name__ == "__main__":
    unittest.main()
