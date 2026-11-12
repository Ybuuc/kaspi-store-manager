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

                # Configure an internal webhook notification before the monitor.
                # The receiver has no public port and only stores synthetic events.
                page.goto(KUMA_URL + "/settings/notifications", wait_until="domcontentloaded", timeout=45000)
                page.get_by_role("button", name="Setup Notification").click()
                modal = page.locator(".modal.show")
                modal.locator("#notification-type").select_option("webhook")
                modal.locator("#notification-name").fill("CI local alert sink")
                modal.locator("#webhook-url").fill("http://alert-sink:8765/alert")
                modal.locator("#webhook-http-method").select_option("post")
                modal.locator("#webhook-request-body").select_option("json")
                modal.locator("input.form-check-input").first.check()
                modal.get_by_role("button", name="Save").click()
                expect(page.locator(".modal.show")).to_have_count(0, timeout=30000)
                evidence["checks"]["webhook_notification_configured"] = True

                page.goto(KUMA_URL + "/add", wait_until="domcontentloaded", timeout=45000)
                alert_checkbox = page.locator('input[id^="notification"]')
                expect(alert_checkbox.first).to_be_visible(timeout=30000)
                alert_checkbox.first.check()
                page.get_by_test_id("monitor-type-select").select_option("http")
                page.get_by_test_id("friendly-name-input").fill("KSM login (disposable CI)")
                page.get_by_test_id("url-input").fill(MONITOR_URL)
                page.locator("#interval").fill("60")
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
                # Verify that an actual HTTP webhook was delivered for both state changes.
                import time
                deadline = time.monotonic() + 35
                delivered = []
                while time.monotonic() < deadline:
                    logs = subprocess.run(COMPOSE + ["exec", "-T", "alert-sink", "cat",
                                         "/app/alerts/notifications.jsonl"],
                                          capture_output=True, text=True, check=False)
                    if logs.returncode == 0:
                        delivered = [json.loads(line) for line in logs.stdout.splitlines() if line]
                        if any(x.get("status") == 0 for x in delivered) and any(
                            x.get("status") == 1 for x in delivered
                        ):
                            break
                    time.sleep(2)
                else:
                    raise AssertionError("Kuma did not deliver both DOWN and UP HTTP webhooks")
                evidence["checks"]["webhook_down_and_up_delivered"] = True
                evidence["webhook_delivery"] = {
                    "down_count": sum(1 for x in delivered if x.get("status") == 0),
                    "up_count": sum(1 for x in delivered if x.get("status") == 1),
                    "records": delivered,
                }
                (EVIDENCE_DIR / "kuma-webhook-events.json").write_text(
                    json.dumps(delivered, ensure_ascii=False, indent=2), encoding="utf-8"
                )
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
