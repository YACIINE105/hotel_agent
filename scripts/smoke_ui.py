"""Browser check of the widget and staff inbox against a running API (real model).

    uv run --with playwright python scripts/smoke_ui.py [screenshot_dir]
"""

import sys
from datetime import date, timedelta

from playwright.sync_api import expect, sync_playwright

BASE = "http://localhost:8000"
OUT = sys.argv[1] if len(sys.argv) > 1 else "."

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    errors = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/?hotel=atlas-bay")

    expect(page.locator(".msg.ai").first).to_be_visible(timeout=10000)  # greeting
    box = page.locator("form input")

    def idle(timeout=60000):
        page.wait_for_function("() => window.HotelAgent && !window.HotelAgent.busy",
                               timeout=timeout)
        page.wait_for_timeout(500)

    def ask(text, wait_for, timeout=60000):
        box.fill(text)
        box.press("Enter")
        expect(page.locator(wait_for).last).to_be_visible(timeout=timeout)

    ask("Is breakfast included?", ".msg.ai >> nth=1")
    ci = date.today() + timedelta(days=30)
    ask(f"Room for 2 adults from {ci} to {ci + timedelta(days=2)}", ".card")
    page.locator(".card button").first.click()  # may be clicked mid-stream: it must be queued
    idle()
    if not page.locator(".quote").count():
        ask("Amina Haddad, amina@example.com", ".quote", timeout=60000)
        idle()
    page.locator(".quote button").last.click()
    expect(page.locator(".result").last).to_contain_text("FK-", timeout=20000)
    page.screenshot(path=f"{OUT}/widget.png")

    inbox = browser.new_page(viewport={"width": 1200, "height": 800})
    inbox.goto(BASE + "/inbox")
    inbox.fill("#key", "demo-atlas-staff-key")
    inbox.click("#load")
    expect(inbox.locator(".conv").first).to_be_visible(timeout=10000)
    inbox.locator(".conv").first.click()
    expect(inbox.locator(".audit").first).to_be_visible(timeout=10000)
    inbox.screenshot(path=f"{OUT}/inbox.png")

    print("widget messages:", page.locator(".msg").count(), "| console errors:", errors or "none")
    print("booking:", page.locator(".result").last.inner_text())
    browser.close()
