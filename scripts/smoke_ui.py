"""Browser check of the demo page, widget, and staff inbox against a running API (real model).

    uv run --with playwright python scripts/smoke_ui.py [screenshot_dir]
"""

import sys
import time

from playwright.sync_api import expect, sync_playwright

BASE = "http://localhost:8000"
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
KEY = "demo-aldau-staff-key"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 860})
    errors = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/")
    page.wait_for_function("() => window.HotelAgent")
    page.screenshot(path=f"{OUT}/1-landing.png")

    def idle(timeout=60000):
        page.wait_for_function("() => !window.HotelAgent.busy", timeout=timeout)
        page.wait_for_timeout(300)

    # 1) Booking entirely through forms: no text interview.
    page.locator("nav [data-book]").click()
    form = page.locator("form.card").last
    expect(form).to_be_visible(timeout=10000)
    form.locator(".stepper").nth(1).locator("button").last.click()  # + 1 child
    form.locator(".ages select").select_option("8")
    form.locator("button[type=submit]").click()
    expect(page.locator(".offer").first).to_be_visible(timeout=15000)
    rooms = page.locator(".offer .name").all_inner_texts()
    page.screenshot(path=f"{OUT}/2-offers.png")
    page.locator(".offer .btn").first.click()
    details = page.locator("form.card").last
    details.locator("input").nth(0).fill("Amina")
    details.locator("input").nth(1).fill("Haddad")
    details.locator("input[type=email]").fill("amina@example.com")
    details.locator("button[type=submit]").click()
    expect(page.locator(".card.quote")).to_be_visible(timeout=15000)
    page.screenshot(path=f"{OUT}/3-quote.png")
    page.locator(".card.quote .btn.confirm").click()
    expect(page.locator(".result").last).to_contain_text("FK-", timeout=20000)
    booking = page.locator(".result").last.inner_text()

    # 2) Arabic FAQ answered from approved Arabic facts.
    box = page.locator("form.compose input")
    box.fill("هل يوجد موقف سيارات؟")
    box.press("Enter")
    idle()
    arabic = page.locator(".msg.ai").last.inner_text()

    # 3) Typing a booking request opens the structured form.
    before = page.locator("form.card").count()
    box.fill("I want to book a room next Thursday")
    box.press("Enter")
    idle()
    form_offered = page.locator("form.card").count() > before

    # 4) Staff typing indicator and staff reply reach the guest via 2-second polling.
    inbox = browser.new_page(viewport={"width": 1280, "height": 860})
    inbox.goto(BASE + "/inbox")
    inbox.fill("#key", KEY)
    inbox.click("#load")
    inbox.locator(".conv").first.click()
    expect(inbox.locator(".m").first).to_be_visible(timeout=10000)
    inbox.locator("#reply").click()
    inbox.locator("#reply").press_sequentially("Hello Amina, this is Samir from reception", delay=20)
    t0 = time.time()
    expect(page.locator(".typing", has_text="Hotel team is typing")).to_be_visible(timeout=8000)
    typing_seen = round(time.time() - t0, 1)
    page.screenshot(path=f"{OUT}/4-staff-typing.png")
    inbox.screenshot(path=f"{OUT}/5-inbox.png")
    inbox.click("#send")
    t0 = time.time()
    expect(page.locator(".msg.staff").last).to_contain_text("Samir", timeout=8000)
    reply_seen = round(time.time() - t0, 1)
    expect(page.locator(".typing", has_text="Hotel team is typing")).to_have_count(0, timeout=5000)

    mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
    mobile.goto(BASE + "/")
    mobile.wait_for_function("() => window.HotelAgent")
    mobile.screenshot(path=f"{OUT}/6-mobile.png")
    mobile.evaluate("HotelAgent.openBooking()")
    expect(mobile.locator("form.card").last).to_be_visible(timeout=10000)
    mobile.screenshot(path=f"{OUT}/7-mobile-booking.png")
    overflow = mobile.evaluate("document.documentElement.scrollWidth > window.innerWidth")

    print("rooms offered (2 adults + child 8):", rooms)
    print("booking:", booking.replace("\n", " | "))
    print("arabic answer:", arabic)
    print("booking form offered after text request:", form_offered)
    print(f"staff typing shown after {typing_seen}s, staff reply shown after {reply_seen}s")
    print("mobile horizontal overflow:", overflow)
    print("console errors:", errors or "none")
    browser.close()
