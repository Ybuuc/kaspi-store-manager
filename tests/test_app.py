import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing

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

    def test_login_validation_and_protected_pages(self):
        for path in ("/products", "/products/1"):
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
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True).count("/products/"), 100)
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


if __name__ == "__main__":
    unittest.main()
