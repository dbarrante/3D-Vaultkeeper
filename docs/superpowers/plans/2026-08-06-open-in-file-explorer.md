# Open in File Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a right-click "Open in File Explorer" action to File view's two existing context menus (model/file cards, and folders in the sidebar's file-tree), revealing the real file or folder on disk in Windows Explorer.

**Architecture:** One new backend endpoint shells out to `explorer.exe` via `subprocess.Popen` (fire-and-forget, no isolation/timeout machinery needed — unlike this codebase's existing native folder-picker dialog, which needs subprocess isolation because it runs an in-process tkinter GUI toolkit alongside uvicorn; launching a wholly separate OS application like Explorer has no such conflict). One new frontend API call wired into two existing menus.

**Tech Stack:** FastAPI (backend), React/TypeScript + MUI `Menu`/`MenuItem` (frontend), `subprocess.Popen` (Windows `explorer.exe`).

## Global Constraints

- Design doc: `docs/superpowers/specs/2026-08-06-open-in-file-explorer-design.md`.
- File view only. Logical folders are explicitly out of scope (no single meaningful disk path).
- Windows-only implementation — no cross-platform branching.
- The endpoint lives in `backend/app/routers/file_view.py` (not a new top-level router) at path `/api/file-view/reveal`, fitting that router's existing `/api/file-view` prefix — a small, deliberate refinement over the spec's literal `/api/reveal-in-explorer` path, since both of this feature's call sites are File-view-scoped and `file_view.py` already owns every other File-view-specific endpoint.
- `subprocess.Popen` must be called with a list argv (never `shell=True` or a concatenated string) to avoid any path-based shell-injection risk.

---

### Task 1: Backend reveal endpoint

**Files:**
- Modify: `backend/app/routers/file_view.py`
- Test: `backend/tests/test_reveal_in_explorer.py`

**Interfaces:**
- Produces: `POST /api/file-view/reveal`, body `{"path": string}`, returns `{"ok": true}` on success, `404` if the path doesn't exist. Task 2's frontend client calls this exact route and body shape.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_reveal_in_explorer.py
from unittest.mock import patch


def test_reveal_file_selects_it_in_parent_folder(client, tmp_path):
    target = tmp_path / "model.stl"
    target.write_text("data")

    with patch("app.routers.file_view.subprocess.Popen") as mock_popen:
        resp = client.post("/api/file-view/reveal", json={"path": str(target)})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_popen.assert_called_once_with(["explorer", "/select,", str(target)])


def test_reveal_directory_opens_it_directly(client, tmp_path):
    target = tmp_path / "SomeFolder"
    target.mkdir()

    with patch("app.routers.file_view.subprocess.Popen") as mock_popen:
        resp = client.post("/api/file-view/reveal", json={"path": str(target)})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_popen.assert_called_once_with(["explorer", str(target)])


def test_reveal_nonexistent_path_returns_404(client, tmp_path):
    missing = tmp_path / "does-not-exist.stl"

    with patch("app.routers.file_view.subprocess.Popen") as mock_popen:
        resp = client.post("/api/file-view/reveal", json={"path": str(missing)})

    assert resp.status_code == 404
    mock_popen.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_reveal_in_explorer.py -v`
Expected: FAIL — `404 Not Found` for all three (the route doesn't exist yet), or a collection error if `subprocess` isn't yet imported the way the patch target expects.

- [ ] **Step 3: Add the endpoint**

Add near the top of `backend/app/routers/file_view.py`, alongside the other imports (the file already imports `os`, `shutil`, `Path`, `Optional`, `APIRouter`, `HTTPException`, `BaseModel`):

```python
import subprocess
```

Add the request model and route (append near the end of the file, after `get_tracked_folders`):

```python
class RevealRequest(BaseModel):
    path: str


@router.post("/reveal")
def reveal_in_explorer(body: RevealRequest):
    target = Path(body.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {body.path}")
    if target.is_dir():
        subprocess.Popen(["explorer", str(target)])
    else:
        subprocess.Popen(["explorer", "/select,", str(target)])
    return {"ok": True}
```

(`router` already has `prefix="/api/file-view"`, so this route resolves to `POST /api/file-view/reveal` — matching the Global Constraints note above.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_reveal_in_explorer.py -v`
Expected: PASS (3/3).

- [ ] **Step 5: Run the full backend suite to confirm no regressions**

Run: `cd backend && python -m pytest -q`
Expected: same pass count as before this change, plus these 3 new passes. (If you see the pre-existing, unrelated `test_find_sidecar_notes_reads_sibling_pdf_file` failure, that is a known pre-existing issue in this repo unrelated to this plan — do not attempt to fix it here.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/file_view.py backend/tests/test_reveal_in_explorer.py
git commit -m "feat: add backend endpoint to reveal a file-view path in Windows Explorer"
```

---

### Task 2: Frontend wiring

**Files:**
- Modify: `frontend/services/api.ts`
- Modify: `frontend/components/ModelList.tsx`
- Modify: `frontend/components/Sidebar.tsx`
- Test: `frontend/components/revealInExplorer.integration_test.py`

**Interfaces:**
- Consumes: `POST /api/file-view/reveal` from Task 1, body `{path: string}`.
- Produces: `api.revealInExplorer(path: string): Promise<void>` — used by both menu wirings in this same task.

- [ ] **Step 1: Add the API client function**

In `frontend/services/api.ts`, add near the other `FileView*` functions (e.g. right after `deleteFileViewFolder`):

```ts
  revealInExplorer: async (path: string): Promise<void> => {
    const res = await fetch(`${getApiBaseUrl()}/file-view/reveal`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Failed to reveal in File Explorer");
    }
  },
```

- [ ] **Step 2: Wire into the model/file card context menu**

In `frontend/components/ModelList.tsx`, add a new handler near the existing `handleRenameFile`/`handleDeleteFile`/`handleCopyFile` (around line 128-162):

```tsx
  const handleRevealFile = async (model: STLModel) => {
    if (!model.filePath) return;
    try {
      await api.revealInExplorer(model.filePath);
    } catch (err) {
      console.error("Reveal in File Explorer failed:", err);
      alert(err instanceof Error ? err.message : "Reveal in File Explorer failed");
    }
  };
```

Add a 4th `MenuItem` to the file context menu (currently Rename/Copy/Delete, `ModelList.tsx:1028-1051`), placed first so it's not adjacent to the destructive Delete action:

```tsx
        <MenuItem
          onClick={() => {
            if (fileContextMenu) handleRevealFile(fileContextMenu.model);
            setFileContextMenu(null);
          }}
        >
          Open in File Explorer
        </MenuItem>
```

(Insert this immediately before the existing `Rename` `MenuItem` at line 1028, so the menu reads: Open in File Explorer, Rename, Copy, Delete.)

- [ ] **Step 3: Wire into the sidebar's folder context menu**

In `frontend/components/Sidebar.tsx`, this menu already conditionally renders Rename/Move/Delete only when `folderContextMenu.realPath !== null` (`Sidebar.tsx:853-895`, the Uploads bucket node has `realPath: null` and only ever gets "New Folder" — there is no single real directory to reveal for it). Add "Open in File Explorer" as a new item inside that same conditional array, since it needs a real path exactly like Rename/Move/Delete do:

```tsx
          <MenuItem
            key="reveal"
            onClick={() => {
              if (folderContextMenu?.realPath) {
                api.revealInExplorer(folderContextMenu.realPath).catch((err) => {
                  console.error("Reveal in File Explorer failed:", err);
                  alert(err instanceof Error ? err.message : "Reveal in File Explorer failed");
                });
              }
              setFolderContextMenu(null);
            }}
          >
            Open in File Explorer
          </MenuItem>,
```

Insert this as the first element of the array at `Sidebar.tsx:853-895` (immediately after the `{folderContextMenu !== null && folderContextMenu.realPath !== null && [` line, before the `key="rename"` item), so the menu reads: New Folder, Open in File Explorer, Rename, Move, Delete. Confirm `api` is already imported in this file (it is — used by `moveFileViewFolder`/`deleteFileViewFolder` elsewhere in this same component) before assuming the import exists; if for some reason it isn't, add `import { api } from "../services/api";` matching this file's existing import style.

- [ ] **Step 4: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds with no new TypeScript errors. (This repo has some pre-existing, unrelated type errors surfaced only by `tsc --noEmit` directly, not by `vite build` — if you want to double check this task introduces nothing new, `cd frontend && npx tsc --noEmit` should show the same pre-existing errors as before this change, none in the three files this task touches.)

- [ ] **Step 5: Write and run a Playwright verification**

This feature's actual effect (a real Explorer window opening) cannot be observed by headless Playwright/Chromium — verify only that the menu items exist and correctly call the API with the right path, using a network-request spy rather than a real subprocess call.

```python
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
        page.wait_for_timeout(1000)

        # Right-click the first file card and confirm the menu item exists,
        # then click it and confirm the intercepted request fired correctly.
        first_card = page.locator("[class*='card']").first
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
```

Run: `cd frontend && bun run dev` (separate terminal), then `python components/revealInExplorer.integration_test.py`
Expected: `ALL REVEAL-IN-EXPLORER TESTS PASSED`. If the File-view toggle or file-card selector text doesn't match what's actually rendered, inspect the live page (`page.screenshot()` or `page.content()`) and adjust the selector — don't guess blindly.

- [ ] **Step 6: Commit**

```bash
git add frontend/services/api.ts frontend/components/ModelList.tsx frontend/components/Sidebar.tsx frontend/components/revealInExplorer.integration_test.py
git commit -m "feat: add 'Open in File Explorer' to File view's file and folder context menus"
```

---

## Final Verification

1. `cd backend && python -m pytest -q` — all tests pass (aside from the known pre-existing, unrelated sidecar-notes failure).
2. `cd frontend && bun run build` — succeeds.
3. Re-run `python components/revealInExplorer.integration_test.py` against the final code.
4. Manual check (the one thing automated tests can't cover): in the actual running app, right-click a file and a folder in File view and confirm Explorer really opens with the right target selected/opened.
