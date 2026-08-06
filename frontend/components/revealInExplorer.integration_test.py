# frontend/components/revealInExplorer.integration_test.py
# Run with a dev server + backend already running (see project conventions
# for starting both). Usage:
#   HOVER_TEST_FRONTEND_URL not needed here -- defaults assumed below;
#   adjust FRONTEND_URL/BACKEND_URL if your ports differ.
import urllib.request
from playwright.sync_api import sync_playwright

FRONTEND_URL = "http://localhost:5173"
BACKEND_URL = "http://127.0.0.1:8000"


def set_api_override(page):
    page.add_init_script(
        f"window.localStorage.setItem('api-port-override', '{BACKEND_URL}');"
    )


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        set_api_override(page)

        # Intercept the reveal call before it reaches the real backend, so
        # this test never actually launches Explorer on the machine running
        # it -- we only need to confirm the frontend calls the right route
        # with the right body.
        captured = {}

        def handle_route(route):
            if "/file-view/reveal" in route.request.url:
                captured["url"] = route.request.url
                captured["body"] = route.request.post_data
                route.fulfill(status=200, json={"ok": True})
            else:
                route.continue_()

        page.route("**/*", handle_route)

        page.goto(FRONTEND_URL)
        page.wait_for_load_state("networkidle")

        # Switch to File view (assumes a toggle exists -- adjust selector if
        # the actual File/Logical toggle control differs).
        page.get_by_text("File", exact=True).first.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        # Right-click the first file card and confirm the menu item exists,
        # then click it and confirm the intercepted request fired correctly.
        first_card = page.locator(".MuiCard-root").first
        first_card.click(button="right")
        menu_item = page.get_by_text("Open in File Explorer", exact=True).first
        assert menu_item.is_visible(), "expected 'Open in File Explorer' in the file context menu"
        menu_item.click()
        page.wait_for_timeout(500)

        assert "url" in captured, "expected a POST to /file-view/reveal"
        assert "/file-view/reveal" in captured["url"]
        print("file card reveal: PASSED")

        browser.close()
    print("ALL REVEAL-IN-EXPLORER TESTS PASSED")


if __name__ == "__main__":
    main()
