# frontend/components/bulkMoveAndNewFolder.integration_test.py
# Run with a dev server + backend already running. Usage:
#   cd frontend && bun run dev   (separate terminal)
#   python components/bulkMoveAndNewFolder.integration_test.py
import json
import urllib.request
import uuid

from playwright.sync_api import sync_playwright

FRONTEND_URL = "http://localhost:5173"
BACKEND_URL = "http://127.0.0.1:8000"


def set_api_override(page):
    page.add_init_script(
        f"window.localStorage.setItem('api-port-override', '{BACKEND_URL}');"
    )


def upload_test_model(name, folder_id="1"):
    boundary = "----bulkmovetestboundary"
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


def fetch_json(path):
    with urllib.request.urlopen(f"{BACKEND_URL}{path}") as resp:
        return json.loads(resp.read())


def delete_json(path):
    req = urllib.request.Request(f"{BACKEND_URL}{path}", method="DELETE")
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def main():
    # Unique per run so re-running this test against the same live dev
    # database never collides with a previous run's leftovers -- with a
    # fixed name, a second run would have two identically-named cards/folders
    # in the DOM and `.first` could silently resolve to the WRONG (earlier)
    # run's model/folder, letting the test pass without checking anything
    # about the run that just happened.
    run_id = uuid.uuid4().hex[:8]
    model_filename = f"bulk_new_folder_test_{run_id}.stl"
    folder_name = f"PlaywrightBulkTestFolder_{run_id}"

    created_model = None
    created_folder_id = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            set_api_override(page)

            created_model = upload_test_model(model_filename)

            page.goto(FRONTEND_URL)
            page.wait_for_load_state("networkidle")

            # Logical view: select the uploaded model, open "Move to New Folder",
            # pick Root, name it, and confirm the model lands in the new folder.
            # The selection checkbox is a sibling of (not nested inside)
            # `.MuiCard-root` in the DOM (see ModelList.tsx's card itemContent:
            # the Card and the checkbox `<div>` are both direct children of the
            # same outer wrapper), and it's only opacity-visible on hover of that
            # wrapper. Ctrl+clicking the card's name text is equivalent and more
            # robust: ModelList's outer-div onClick handler treats a ctrl/meta
            # click exactly like a checkbox click (`onToggleSelection`), and the
            # click event bubbles from the name text through the card's button
            # up to that handler regardless of virtualization/hover state.
            card_name = page.get_by_text(model_filename, exact=True)
            card_name.click(modifiers=["Control"])

            page.get_by_title("Move to New Folder").click()
            page.get_by_text("Root", exact=True).first.click()
            # get_by_placeholder does case-insensitive substring matching by
            # default, which also matches the sidebar's unrelated "Folder
            # Name..." inline-create input (id="folder-name-input") -- use
            # exact=True to target only this dialog's "Folder name" input.
            name_input = page.get_by_placeholder("Folder name", exact=True)
            name_input.fill(folder_name)
            page.get_by_text("Create & Move", exact=True).click()
            page.wait_for_timeout(1000)

            # Confirm the new folder now appears (both in the sidebar tree and as
            # a folder-card in the main grid -- the run-unique name means both
            # matches genuinely refer to this run's folder, so `.first` is safe
            # here, unlike with a fixed name across repeated runs).
            assert page.get_by_text(folder_name, exact=True).first.is_visible(), (
                "expected the newly created folder to appear in the sidebar"
            )
            page.get_by_text(folder_name, exact=True).first.click()
            page.wait_for_timeout(500)
            assert page.get_by_text(model_filename, exact=True).is_visible(), (
                "expected the moved model to appear inside the new folder"
            )

            # Real state check, not just DOM appearance: confirm the backend
            # actually persisted the folder and the model's new folderId --
            # since folder/model names are unique to this run, these lookups
            # can't accidentally match a different run's leftovers.
            folders = fetch_json("/api/folders")
            matching_folders = [f for f in folders if f["name"] == folder_name]
            assert len(matching_folders) == 1, (
                f"expected exactly one backend folder named {folder_name!r}, found {len(matching_folders)}"
            )
            created_folder_id = matching_folders[0]["id"]

            models = fetch_json("/api/models")
            matching_models = [m for m in models if m["id"] == created_model["id"]]
            assert len(matching_models) == 1, f"expected uploaded model {created_model['id']} to still exist"
            assert matching_models[0]["folderId"] == created_folder_id, (
                f"expected model {created_model['id']}'s folderId to be {created_folder_id!r} "
                f"(the newly created folder), got {matching_models[0]['folderId']!r}"
            )

            print("Logical view Move to New Folder: PASSED")
            browser.close()
    finally:
        # Best-effort cleanup so repeated runs -- including failed ones --
        # don't pile up junk in the live dev library. Runs regardless of
        # whether the test above passed or raised, so a failed assertion
        # doesn't strand this run's model/folder for the next run to trip
        # over. Not essential to the test's pass/fail verdict, so cleanup
        # failures are swallowed rather than raised.
        try:
            if created_model:
                delete_json(f"/api/models/{created_model['id']}")
            if created_folder_id:
                delete_json(f"/api/folders/{created_folder_id}")
        except Exception as cleanup_err:
            print(f"(non-fatal) cleanup failed: {cleanup_err}")

    print("ALL BULK-MOVE-AND-NEW-FOLDER TESTS PASSED")


if __name__ == "__main__":
    main()
