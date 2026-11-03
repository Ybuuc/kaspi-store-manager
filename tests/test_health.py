import tempfile
import unittest
from pathlib import Path

from app import create_app


class HealthTests(unittest.TestCase):
    def test_health_returns_ok(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True,
                "SECRET_KEY": "test-only-secret",
                "DATABASE": str(Path(directory) / "test.sqlite3"),
            })

            response = app.test_client().get("/health")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {"status": "ok"})
