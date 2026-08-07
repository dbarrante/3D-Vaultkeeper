# frontend/components/fileViewNavigationAndPathToggle.integration_test.py
# Run with a dev server + backend already running. Usage:
#   cd frontend && bun run dev   (separate terminal)
#   python components/fileViewNavigationAndPathToggle.integration_test.py
import json
import urllib.request
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

FRONTEND_URL = "http://localhost:5173"
BACKEND_URL = "http://127.0.0.1:8000"

# backend/app/db.py defaults FILE_STORAGE/UPLOAD_DIR to the *string*
# "./app/uploads" whenever no FILE_STORAGE env var is set (true for a plain
# `run.sh`/uvicorn dev backend -- only the Docker image and the frozen
# desktop build set it to something already absolute). file_view.py's
# ensure_unambiguous_path() then deliberately rejects that relative string
# for any request that resolves to it -- notably POST /api/file-view/folder
# with parentPath=None, which defaults to str(UPLOAD_DIR) -- refusing to
# create a folder directly at the library root. Passing an already-absolute
# parentPath sidesteps this entirely (confirmed live: the same endpoint
# accepts an absolute parentPath under this exact directory without issue),
# so this test creates its scratch parent folder there instead of at the
# true root. Computed from this file's own location (not hardcoded to a
# particular machine) since this repo's convention is backend/ as the
# uvicorn working directory, matching UPLOAD_DIR's default.
BACKEND_UPLOAD_ROOT = str(
    (Path(__file__).resolve().parents[2] / "backend" / "app" / "uploads")
)

if not Path(BACKEND_UPLOAD_ROOT).is_dir():
    raise SystemExit(
        f"Expected the backend's upload root at {BACKEND_UPLOAD_ROOT}, but it "
        "doesn't exist. This test assumes a plain `run.sh`/uvicorn dev backend "
        "with no FILE_STORAGE override (see the comment above this constant). "
        "If your backend is running with FILE_STORAGE set to something else "
        "(Docker, the desktop build, or a dev who exported it), point "
        "BACKEND_UPLOAD_ROOT at that absolute path instead."
    )


def set_api_override(page):
    page.add_init_script(
        f"window.localStorage.setItem('api-port-override', '{BACKEND_URL}');"
    )


def upload_test_model(name, folder_id="1"):
    boundary = "----fileviewnavtestboundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="folderId"\r\n\r\n{folder_id}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
        f"solid test endsolid\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/models/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def bulk_move_file_view(ids, target_path):
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/file-view/models/bulk-move",
        data=json.dumps({"ids": ids, "targetPath": target_path}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def create_file_view_folder(parent_path, name):
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/file-view/folder",
        data=json.dumps({"parentPath": parent_path, "name": name}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def delete_model(model_id):
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/models/{model_id}?hard=true",
        method="DELETE",
    )
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass


def delete_file_view_folder(path):
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/file-view/folder",
        data=json.dumps({"path": path}).encode(),
        headers={"Content-Type": "application/json"},
        method="DELETE",
    )
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass


def main():
    # Unique per run so re-running this test against the same live dev
    # database never collides with a previous run's leftovers (matching the
    # convention established in bulkMoveAndNewFolder.integration_test.py).
    run_id = uuid.uuid4().hex[:8]
    parent_name = f"NavTestParent_{run_id}"
    child_name = f"NavTestChild_{run_id}"
    direct_model_name = f"direct_{run_id}.stl"
    nested_model_name = f"nested_{run_id}.stl"

    parent = create_file_view_folder(BACKEND_UPLOAD_ROOT, parent_name)
    child = create_file_view_folder(parent["path"], child_name)

    direct_model = upload_test_model(direct_model_name)
    nested_model = upload_test_model(nested_model_name)
    bulk_move_file_view([direct_model["id"]], parent["path"])
    bulk_move_file_view([nested_model["id"]], child["path"])

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # Default Playwright viewport (1280x720) satisfies App.tsx's
            # `isDesktop = useMediaQuery("(min-width: 1024px)")`, so the
            # desktop Sidebar (with its persistent "Settings" button and
            # inline Logical/File toggle) renders instead of the mobile
            # Navbar+drawer variant.
            page = browser.new_page()
            set_api_override(page)

            page.goto(FRONTEND_URL)
            page.wait_for_load_state("networkidle")
            page.get_by_text("File", exact=True).first.click()
            page.wait_for_timeout(500)

            # Navigate to the parent folder via the sidebar.
            page.get_by_text(parent_name, exact=True).first.click()
            page.wait_for_timeout(500)

            # Direct-child file must be visible; the nested (grandchild) file must not.
            assert page.get_by_text(direct_model_name, exact=True).first.is_visible(), (
                "expected the direct-child file to be visible"
            )
            assert page.get_by_text(nested_model_name, exact=True).count() == 0, (
                "expected the nested (grandchild) file to NOT be visible -- "
                "direct-children-only filtering regressed"
            )

            # A tile for the child folder must be visible in the main grid.
            tile = page.get_by_text(child_name, exact=True).first
            assert tile.is_visible(), "expected a folder tile for the direct subfolder"

            # Clicking the tile navigates into it and reveals the nested file.
            tile.click()
            page.wait_for_timeout(500)
            assert page.get_by_text(nested_model_name, exact=True).first.is_visible(), (
                "expected the nested file to be visible after navigating into the child folder"
            )

            # Sidebar selection must follow a main-grid tile click too, not
            # just sidebar-driven navigation -- the sidebar's File-view tree
            # uses MUI RichTreeView's `selectedItems` to highlight the current
            # node. Inspecting the live DOM (via page.content()) showed MUI
            # renders this as `aria-checked="true"` on the `<li role=
            # "treeitem">` (this tree has no `aria-selected` attribute at
            # all -- confirmed live, not guessed), so check that instead of
            # the brief's draft `aria-selected` selector, which never matches
            # anything in the real markup.
            selected_child = page.locator(
                f"li[role='treeitem'][aria-checked='true']:has-text('{child_name}')"
            )
            assert selected_child.count() > 0, (
                "expected the sidebar tree to show the child folder as selected "
                "after navigating into it via the main-grid tile"
            )

            print("Direct-children navigation + folder tiles: PASSED")

            # Settings toggle: card shows folder path only when enabled.
            page.get_by_text(parent_name, exact=True).first.click()  # back to parent
            page.wait_for_timeout(300)

            # The folder-path line is rendered as MUI Typography `variant="caption"`
            # directly under the card's size/date line, containing the model's
            # containing directory (model.filePath with the filename stripped).
            # Before enabling the toggle it must NOT be present.
            path_line = page.locator(f"text={parent['path']}")
            assert path_line.count() == 0, (
                "expected no folder-path line before the Settings toggle is enabled"
            )

            # Open Settings via the desktop Sidebar's "Settings" button (not
            # Navbar's "Open settings" icon button, which only renders in the
            # mobile layout that isn't active at this viewport).
            page.get_by_role("button", name="Settings", exact=True).first.click()
            page.wait_for_timeout(300)
            toggle = page.get_by_text("Show folder path on card", exact=True).first
            toggle.click()
            page.wait_for_timeout(300)

            # Settings has no URL/history entry (showSettings is plain React
            # state, not a route) -- go back via its own "Go back" button
            # rather than browser history navigation.
            page.get_by_label("Go back", exact=True).click()
            page.wait_for_timeout(500)

            # Now the folder-path line must be present and must show the
            # parent folder's real path (its directory, with the filename
            # stripped), proving the toggle actually drives the new card line
            # rather than just "the app didn't crash".
            assert page.locator(f"text={parent['path']}").count() > 0, (
                "expected the folder-path line to appear on File-view cards "
                "after enabling the Settings toggle"
            )

            # Toggle it back off and confirm the line disappears again.
            page.get_by_role("button", name="Settings", exact=True).first.click()
            page.wait_for_timeout(300)
            page.get_by_text("Show folder path on card", exact=True).first.click()
            page.wait_for_timeout(300)
            page.get_by_label("Go back", exact=True).click()
            page.wait_for_timeout(500)

            assert page.locator(f"text={parent['path']}").count() == 0, (
                "expected the folder-path line to disappear after disabling "
                "the Settings toggle"
            )

            print("Settings toggle round-trip: PASSED")

            browser.close()
    finally:
        # Deleting the parent folder recursively removes the child folder,
        # both models' DB rows, and their physical files (delete_folder's
        # find_affected_models + shutil.rmtree) -- the two delete_model
        # calls are a redundant, best-effort backstop in case the test
        # aborted before either bulk_move_file_view call above landed the
        # models inside the folder tree in the first place.
        delete_file_view_folder(parent["path"])
        delete_model(direct_model["id"])
        delete_model(nested_model["id"])

    print("ALL FILE-VIEW-NAVIGATION-AND-PATH-TOGGLE TESTS PASSED")


if __name__ == "__main__":
    main()
