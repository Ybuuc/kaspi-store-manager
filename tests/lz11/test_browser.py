"""Manual Playwright UAT smoke: requires a running isolated demo and test credentials."""

import os
from pathlib import Path

import pytest


def test_employee_login_and_admin_forbidden():
    playwright = pytest.importorskip("playwright.sync_api")
    url = os.environ.get("KSM_BASE_URL")
    user = os.environ.get("KSM_UAT_USER")
    password = os.environ.get("KSM_UAT_PASSWORD")
    if not (url and user and password):
        pytest.skip("Set KSM_BASE_URL, KSM_UAT_USER and KSM_UAT_PASSWORD for isolated demo")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            response = page.goto(f"{url.rstrip('/')}/login")
            assert response is not None and response.status == 200
            page.locator('input[name="username"]').fill(user)
            page.locator('input[name="password"]').fill(password)
            page.locator('button[type="submit"]').click()
            page.wait_for_url("**/products")
            assert page.get_by_role("heading", name="Тауарлар").is_visible()
            evidence = os.environ.get("KSM_EVIDENCE_DIR")
            if evidence:
                directory = Path(evidence)
                directory.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(directory / "playwright-catalog.png"), full_page=True)
            forbidden = page.goto(f"{url.rstrip('/')}/admin")
            assert forbidden is not None and forbidden.status == 403
        finally:
            browser.close()
