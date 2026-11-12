"""End-to-end Uptime Kuma v2 synthetic UP -> DOWN -> UP drill.

Runs against *disposable GitHub Actions* Docker Compose volumes with a new
randomly generated admin password. Uses Kuma's actual browser interface and
does not access user data or pretend to represent production availability.
"""

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

COMPOSE = ["docker", "compose", "-f", "ops/lz11/compose.yaml"]
KUMA_URL = os.environ.get("KUMA_URL", "http://127.0.0.1:3001").rstrip("/")
MONITOR_URL = os.environ.get("KSM_MONITOR_URL", "http://app:5000/login")
ADMIN_USER = os.environ.get("KUMA_ADMIN_USER", "lz11-ci")
ADMIN_PASSWORD = os.environ["KUMA_ADMIN_PASSWORD"]
EVIDENCE_DIR = Path("reports/lz11")


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    evidence = {
        "kind": "isolated-synthetic-monitoring-drill",
        "production_incident": False,
        "application_url": MONITOR_URL,
        "started_at": now(),
        "checks": {},
    }
    app_stopped = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(locale="en-US")
                page.goto(KUMA_URL + "/setup", wait_until="domcontentloaded", timeout=60000)
                page.locator('[data-cy="username-input"]').fill(ADMIN_USER, timeout=45000)
                page.locator('[data-cy="password-input"]').fill(ADMIN_PASSWORD)
                page.locator('[data-cy="password-repeat-input"]').fill(ADMIN_PASSWORD)
                page.locator("#language").select_option("en")
                page.locator('[data-cy="submit-setup-form"]').click()
                page.wait_for_url(re.compile(r".*/dashboard(?:/.*)?$"), timeout=60000)
                evidence["checks"]["admin_created_via_browser"] = True
                print("Kuma synthetic administrator initialized through browser UI")

                page.goto(KUMA_URL + "/add", wait_until="domcontentloaded", timeout=45000)
                page.get_by_test_id("monitor-type-select").select_option("http")
                page.get_by_test_id("friendly-name-input").fill("KSM login (disposable CI)")
                page.get_by_test_id("url-input").fill(MONITOR_URL)
                page.locator("#interval").fill("20")
                page.get_by_test_id("save-button").click()
                page.wait_for_url(re.compile(r".*/dashboard/\d+$"), timeout=60000)
                evidence["monitor_detail_url_path"] = "/" + page.url.split(KUMA_URL + "/", 1)[-1]
                status = page.get_by_test_id("monitor-status")

                expect(status).to_have_text(re.compile(r"^Up$", re.IGNORECASE), timeout=120000)
                evidence["checks"]["initial_up"] = now()
                page.screenshot(path=str(EVIDENCE_DIR / "kuma-up.png"), full_page=True)
                print("Kuma recorded UP")

                subprocess.run(COMPOSE + ["stop", "app"], check=True)
                app_stopped = True
                evidence["injected_outage_at"] = now()
                expect(status).to_have_text(re.compile(r"^Down$", re.IGNORECASE), timeout=120000)
                evidence["checks"]["down"] = now()
                page.screenshot(path=str(EVIDENCE_DIR / "kuma-down.png"), full_page=True)
                print("Kuma recorded DOWN")

                subprocess.run(COMPOSE + ["start", "app"], check=True)
                app_stopped = False
                evidence["started_app_at"] = now()
                expect(status).to_have_text(re.compile(r"^Up$", re.IGNORECASE), timeout=120000)
                evidence["checks"]["recovered_up"] = now()
                page.screenshot(path=str(EVIDENCE_DIR / "kuma-recovered.png"), full_page=True)
                print("Kuma recorded recovery UP")
                evidence["result"] = "PASS"
            finally:
                browser.close()
    except Exception as exc:
        evidence["result"] = "FAIL"
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if app_stopped:
            subprocess.run(COMPOSE + ["start", "app"], check=False)
        evidence["finished_at"] = now()
        (EVIDENCE_DIR / "uptime-kuma-drill.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("Kuma drill:", evidence["result"])


if __name__ == "__main__":
    main()
