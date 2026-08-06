# frontend/components/revealInExplorer.integration_test.py
# Run with a dev server + backend already running (see project conventions
# for starting both). Usage:
#   HOVER_TEST_FRONTEND_URL not needed here -- defaults assumed below;
#   adjust FRONTEND_URL/BACKEND_URL if your ports differ.
import json
import urllib.request
from playwright.sync_api import sync_playwright

FRONTEND_URL = "http://localhost:5173"
BACKEND_URL = "http://127.0.0.1:8000"


def set_api_override(page):
    page.add_init_script(
        f"window.localStorage.setItem('api-port-override', '{BACKEND_URL}');"
    )


def fetch_models():
    # Used only to cross-check the file-card scenario below: the card shows
    # model.name (a friendly display name), which in "copy" storage mode is
    # NOT the same string as the on-disk filePath (files are stored under a
    # UUID-derived name) -- so the only reliable way to know what path the
    # backend *should* have been asked to reveal for a given visible card is
    # to ask the real backend for that model's actual filePath and compare.
    with urllib.request.urlopen(f"{BACKEND_URL}/api/models") as resp:
        return json.loads(resp.read())


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
                captured["method"] = route.request.method
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

        # --- Scenario 1: right-click a file card ---
        # Right-click the first file card and confirm the menu item exists,
        # then click it and confirm the intercepted request fired correctly.
        first_card = page.locator(".MuiCard-root").first
        card_name = first_card.inner_text().strip().splitlines()[0]
        first_card.click(button="right")
        menu_item = page.get_by_text("Open in File Explorer", exact=True).first
        assert menu_item.is_visible(), "expected 'Open in File Explorer' in the file context menu"
        menu_item.click()
        page.wait_for_timeout(500)

        assert "url" in captured, "expected a POST to /file-view/reveal"
        assert "/file-view/reveal" in captured["url"]
        assert captured["method"] == "POST"
        card_body = json.loads(captured["body"])
        revealed_card_path = card_body["path"].replace("\\", "/")
        models = fetch_models()
        # Card text is upper-cased by CSS (text-transform), so compare
        # case-insensitively -- the underlying model.name is untouched.
        matching_models = [m for m in models if m.get("name", "").lower() == card_name.lower()]
        assert matching_models, f"no backend model named {card_name!r} to cross-check against"
        expected_card_path = matching_models[0]["filePath"].replace("\\", "/")
        assert revealed_card_path == expected_card_path, (
            f"reveal request path {revealed_card_path!r} did not match the clicked "
            f"card's actual filePath {expected_card_path!r}"
        )
        print("file card reveal: PASSED")

        captured.clear()

        # --- Scenario 2: right-click a real folder in the sidebar's file-tree ---
        # The file-tree's root can include a synthetic "Uploads" bucket node
        # (realPath: null, flat pre-feature copy-mode storage with no real
        # subdirectory) alongside real folder nodes (realPath: a real disk
        # path). Only real folder nodes render Rename/Move/Delete/"Open in
        # File Explorer" in their context menu (see Sidebar.tsx), so probe
        # each tree node's context menu for "Rename" to find one deterministically
        # rather than assuming a fixed folder name or tree position.
        tree_items = page.locator("[role='treeitem']")
        node_count = tree_items.count()
        folder_label = None
        folder_locator = None
        for i in range(min(node_count, 30)):
            item = tree_items.nth(i)
            label_text = item.locator(".MuiTreeItem-label").first.inner_text().strip()
            item.click(button="right")
            page.wait_for_timeout(200)
            has_rename = page.get_by_text("Rename", exact=True).count() > 0
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
            if has_rename:
                folder_label = label_text
                folder_locator = item
                break

        assert folder_locator is not None, (
            "expected at least one real folder (non-Uploads-bucket, realPath != null) "
            "node in the sidebar's file-tree to right-click"
        )

        folder_locator.click(button="right")
        menu_item2 = page.get_by_text("Open in File Explorer", exact=True).first
        assert menu_item2.is_visible(), "expected 'Open in File Explorer' in the sidebar folder context menu"
        menu_item2.click()
        page.wait_for_timeout(500)

        assert "url" in captured, "expected a POST to /file-view/reveal"
        assert "/file-view/reveal" in captured["url"]
        assert captured["method"] == "POST"
        folder_body = json.loads(captured["body"])
        revealed_folder_path = folder_body["path"].replace("\\", "/").rstrip("/")
        assert revealed_folder_path.split("/")[-1] == folder_label, (
            f"revealed path {revealed_folder_path!r} doesn't match the clicked "
            f"folder node {folder_label!r}"
        )
        print("sidebar folder reveal: PASSED")

        browser.close()
    print("ALL REVEAL-IN-EXPLORER TESTS PASSED")


if __name__ == "__main__":
    main()
