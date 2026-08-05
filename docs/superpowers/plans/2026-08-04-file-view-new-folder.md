# File View: New Folder Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Dave create new folders from File view's right-click menu — nested inside an existing folder, from the synthetic "Uploads" bucket, or via a standalone top-level control — and have them stay visible in the tree even while empty.

**Architecture:** A new `file_view_tracked_folders` table records real paths the File-mode tree must always show regardless of whether any model currently lives there. A new `POST /api/file-view/folder` endpoint creates the real directory and a tracking row; the existing rename/move/delete folder endpoints are extended to keep that table in sync the same way they already keep model rows in sync. The frontend fetches tracked paths alongside models and merges them into the existing `fileTree` build.

**Tech Stack:** FastAPI + sqlite3 (backend), React/TypeScript + MUI (frontend), pytest with `tmp_path` real-filesystem fixtures.

## Global Constraints

- No creation under a watch folder's root from the Uploads-bucket trigger or the standalone top-level control — both are scoped to `UPLOAD_DIR` only. Nesting inside a watch folder still works via right-clicking an existing real folder already under that root.
- No folder-level Copy — unchanged scope from the previous feature.
- No change to Logical mode's folder creation — File-mode only.
- A tracked folder's row is **never auto-pruned** when it gains its first model. It stays tracked — and thus visible even after being emptied back out — until explicitly deleted via `delete_folder`.
- `parentPath` containment (must resolve inside `UPLOAD_DIR` or some watch root) is enforced server-side on the create endpoint, same as every other File-view write endpoint.
- Folder-name sanitization reuses the existing `sanitize_path_segment` helper (`backend/app/services/import_wizard.py:64-76`) — no second implementation of the same rules.

---

### Task 1: Backend — tracked-folders table + create endpoint + list endpoint

**Files:**
- Modify: `backend/app/db.py` (add table to `init_db()`)
- Modify: `backend/app/routers/file_view.py` (add two endpoints)
- Test: `backend/tests/test_file_view_folder_ops.py` (existing file — append)

**Interfaces:**
- Produces: `POST /api/file-view/folder`, body `{"parentPath": Optional[str], "name": str}` (a falsy `parentPath` means "the managed library root," resolved server-side to `UPLOAD_DIR` — the frontend has no way to know `UPLOAD_DIR`'s real value, confirmed no config-exposure endpoint exists in this codebase, so this sentinel avoids ever needing to add one). Returns `{"path": str}`.
- Produces: `GET /api/file-view/tracked-folders`, no body, returns `{"paths": [str, ...]}`.

- [ ] **Step 1: Add the table to `init_db()`**

In `backend/app/db.py`, add a new `cur.execute(...)` block immediately after the existing `inbox_items` table creation (the block ending `)` then `"""` then `)` right before the `if os.getenv("MAKERWORLD_BAMBU_TOKEN"):` line):

```python
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS file_view_tracked_folders (
            path TEXT PRIMARY KEY
        )
        """
    )
```

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_file_view_folder_ops.py` (the file already defines `_insert_folder`/`_insert_model` helpers near the top — reuse them, don't redefine):

```python
def test_create_folder_under_real_parent_creates_dir_and_tracks_it(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    parent = upload_dir / "Vehicles"
    parent.mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(parent), "name": "Tanks"})
    assert resp.status_code == 200
    new_path = Path(resp.json()["path"])
    assert new_path == parent / "Tanks"
    assert new_path.is_dir()

    conn = get_db_conn()
    row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (str(new_path),)
    ).fetchone()
    conn.close()
    assert row is not None


def test_create_folder_with_no_parent_path_defaults_to_upload_dir(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])

    resp = client.post("/api/file-view/folder", json={"parentPath": None, "name": "TopLevel"})
    assert resp.status_code == 200
    new_path = Path(resp.json()["path"])
    assert new_path == upload_dir / "TopLevel"
    assert new_path.is_dir()


def test_create_folder_under_watch_root_works(client, tmp_path):
    watch_root = tmp_path / "watched"
    watch_root.mkdir()

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    conn.commit()
    conn.close()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(watch_root), "name": "Prints"})
    assert resp.status_code == 200
    new_path = Path(resp.json()["path"])
    assert new_path == watch_root / "Prints"
    assert new_path.is_dir()


def test_create_folder_outside_every_allowed_root_rejected(client, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(outside), "name": "New"})
    assert resp.status_code == 400
    assert not (outside / "New").exists()


def test_create_folder_name_is_sanitized(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])

    resp = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Bad<>Name"})
    assert resp.status_code == 200
    new_path = Path(resp.json()["path"])
    assert "<" not in new_path.name
    assert ">" not in new_path.name
    assert new_path.is_dir()


def test_create_folder_collision_rejected(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    existing = upload_dir / "Existing"
    existing.mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Existing"})
    assert resp.status_code == 409


def test_create_folder_missing_parent_404s(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    missing_parent = upload_dir / "DoesNotExist"

    resp = client.post("/api/file-view/folder", json={"parentPath": str(missing_parent), "name": "X"})
    assert resp.status_code == 404


def test_get_tracked_folders_returns_created_paths(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])

    client.post("/api/file-view/folder", json={"parentPath": None, "name": "Alpha"})
    client.post("/api/file-view/folder", json={"parentPath": None, "name": "Beta"})

    resp = client.get("/api/file-view/tracked-folders")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert str(upload_dir / "Alpha") in paths
    assert str(upload_dir / "Beta") in paths
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_file_view_folder_ops.py -k "create_folder or tracked_folders" -v`
Expected: FAIL — `404 Not Found` on every request (endpoints don't exist yet).

- [ ] **Step 4: Implement the endpoints**

Add to the top of `backend/app/routers/file_view.py` (alongside the existing imports):

```python
from typing import Optional

from app.services.import_wizard import sanitize_path_segment
```

Add anywhere after the existing endpoint functions (e.g. at the end of the file):

```python
class FolderCreateRequest(BaseModel):
    parentPath: Optional[str] = None
    name: str


@router.post("/folder")
def create_folder(body: FolderCreateRequest):
    parent_path = body.parentPath if body.parentPath else str(UPLOAD_DIR)
    try:
        ensure_unambiguous_path(parent_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    parent = Path(parent_path)
    if not parent.is_dir():
        raise HTTPException(status_code=404, detail=f"Parent folder not found: {parent_path}")

    try:
        resolve_storage_mode_for_path(parent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    sanitized_name = sanitize_path_segment(body.name)
    new_dir = parent / sanitized_name
    if new_dir.exists():
        raise HTTPException(status_code=409, detail=f"A folder already exists at {new_dir}")

    new_dir.mkdir(parents=True)

    conn = get_db_conn()
    try:
        conn.execute(
            "INSERT INTO file_view_tracked_folders(path) VALUES (?)",
            (str(new_dir),),
        )
        conn.commit()
    finally:
        conn.close()

    return {"path": str(new_dir)}


@router.get("/tracked-folders")
def get_tracked_folders():
    conn = get_db_conn()
    try:
        rows = conn.execute("SELECT path FROM file_view_tracked_folders").fetchall()
    finally:
        conn.close()
    return {"paths": [row["path"] for row in rows]}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_file_view_folder_ops.py -v`
Expected: PASS (all tests in the file, including the new ones)

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && python -m pytest -v`
Expected: PASS (all tests except the one confirmed pre-existing, unrelated `test_find_sidecar_notes_reads_sibling_pdf_file` failure)

- [ ] **Step 7: Commit**

```bash
git add backend/app/db.py backend/app/routers/file_view.py backend/tests/test_file_view_folder_ops.py
git commit -m "feat: add File-view folder creation endpoint + tracked-folders table"
```

---

### Task 2: Backend — keep tracked folders in sync across rename/move/delete

**Files:**
- Modify: `backend/app/services/file_view_ops.py`
- Modify: `backend/app/routers/file_view.py`
- Test: `backend/tests/test_file_view_folder_ops.py`

**Interfaces:**
- Consumes: `find_affected_models`'s Python-side prefix-filtering pattern (`file_view_ops.py:151-171`) — mirrored, not reused directly, since it operates on a different table/column shape.
- Produces: `find_affected_tracked_folders(dir_path: str) -> list[str]` and `rewrite_tracked_folder_paths(dir_path: str, new_dir_path: str) -> None` in `file_view_ops.py`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_file_view_folder_ops.py`:

```python
def test_rename_folder_updates_tracked_child(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    parent = upload_dir / "Vehicles"
    parent.mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(parent), "name": "Tanks"})
    tracked_path = resp.json()["path"]

    resp = client.post("/api/file-view/folder/rename", json={"path": str(parent), "newName": "Cars"})
    assert resp.status_code == 200

    conn = get_db_conn()
    old_row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (tracked_path,)
    ).fetchone()
    new_expected = str(upload_dir / "Cars" / "Tanks")
    new_row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (new_expected,)
    ).fetchone()
    conn.close()
    assert old_row is None
    assert new_row is not None
    assert Path(new_expected).is_dir()


def test_move_folder_updates_tracked_descendant(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    source = upload_dir / "Tanks"
    source.mkdir()
    (upload_dir / "Archive").mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(source), "name": "Old"})
    tracked_path = resp.json()["path"]

    target = str(upload_dir / "Archive" / "Tanks")
    resp = client.post("/api/file-view/folder/move", json={"sourcePath": str(source), "targetPath": target})
    assert resp.status_code == 200

    conn = get_db_conn()
    old_row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (tracked_path,)
    ).fetchone()
    new_expected = str(Path(target) / "Old")
    new_row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (new_expected,)
    ).fetchone()
    conn.close()
    assert old_row is None
    assert new_row is not None


def test_delete_folder_removes_tracked_descendant(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    parent = upload_dir / "Vehicles"
    parent.mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(parent), "name": "Empty"})
    tracked_path = resp.json()["path"]

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(parent)})
    assert resp.status_code == 200

    conn = get_db_conn()
    row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (tracked_path,)
    ).fetchone()
    conn.close()
    assert row is None
    assert not Path(tracked_path).exists()


def test_tracked_folder_survives_being_emptied_out(client, tmp_path):
    """The exact behavior the spec's Goals section exists to guarantee: a
    tracked folder must NOT be pruned just because it (temporarily) has a
    model in it and then doesn't. Easy to get wrong by adding 'helpful'
    pruning logic that isn't wanted.
    """
    upload_dir = Path(os.environ["FILE_STORAGE"])
    parent = upload_dir / "Vehicles"
    parent.mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(parent), "name": "Tanks"})
    tracked_path = resp.json()["path"]
    tracked_dir = Path(tracked_path)

    # A model "lands" in the tracked folder.
    model_file = tracked_dir / "abc123.stl"
    model_file.write_text("model data")
    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc123", "f1", str(model_file), storage_mode="copy")
    conn.commit()
    conn.close()

    # Confirm the tracked row is still present with a model now inside it.
    resp = client.get("/api/file-view/tracked-folders")
    assert tracked_path in resp.json()["paths"]

    # Move the model back out.
    other_dir = upload_dir / "Elsewhere"
    other_dir.mkdir()
    resp = client.patch(
        "/api/models/abc123/location",
        json={"newPath": str(other_dir / "abc123.stl")},
    )
    assert resp.status_code == 200

    # The tracked folder must still be reported -- it was never pruned.
    resp = client.get("/api/file-view/tracked-folders")
    assert tracked_path in resp.json()["paths"]
    assert tracked_dir.is_dir()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_file_view_folder_ops.py -k "rename_folder_updates_tracked or move_folder_updates_tracked or delete_folder_removes_tracked or survives_being_emptied" -v`
Expected: FAIL — rename/move tests fail because the tracked row never moves (still points at the old path); delete test fails because the tracked row is never removed; the "survives being emptied" test should already PASS at this point (nothing in Task 1 prunes on its own) — if it doesn't, something is wrong with Task 1's implementation, stop and investigate rather than proceeding.

- [ ] **Step 3: Add the two helper functions**

Append to `backend/app/services/file_view_ops.py`:

```python
def find_affected_tracked_folders(dir_path: str) -> list:
    """Every tracked-folder path at or under dir_path. Mirrors
    find_affected_models's Python-side prefix filtering (not SQL LIKE) to
    avoid needing to escape "%"/"_" wildcard characters that can legally
    appear in a real folder name.
    """
    conn = get_db_conn()
    try:
        rows = conn.execute("SELECT path FROM file_view_tracked_folders").fetchall()
    finally:
        conn.close()
    prefix = os.path.normpath(dir_path)
    affected = []
    for row in rows:
        norm = os.path.normpath(row["path"])
        if norm == prefix or norm.startswith(prefix + os.sep):
            affected.append(norm)
    return affected


def rewrite_tracked_folder_paths(dir_path: str, new_dir_path: str) -> None:
    """After dir_path has already been physically moved/renamed to
    new_dir_path on disk, update every tracked-folder row at or under it to
    match -- same purpose as rewrite_affected_paths, applied to
    file_view_tracked_folders instead of models. Call this immediately
    after the physical move, passing the OLD dir_path so
    find_affected_tracked_folders still matches what's in the DB.
    """
    old_prefix = os.path.normpath(dir_path)
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        for old_path in find_affected_tracked_folders(dir_path):
            rel = os.path.relpath(old_path, old_prefix)
            new_path = (
                os.path.normpath(os.path.join(new_dir_path, rel))
                if rel != "."
                else os.path.normpath(new_dir_path)
            )
            cur.execute(
                "UPDATE file_view_tracked_folders SET path=? WHERE path=?",
                (new_path, old_path),
            )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Wire the helpers into `rename_folder`, `move_folder`, and `delete_folder`**

In `backend/app/routers/file_view.py`, update the import line that currently reads:
```python
from app.services.file_view_ops import (
    ensure_unambiguous_path,
    is_self_nested_move,
    path_conflicts_with_watch_root,
    rewrite_affected_paths,
    resolve_storage_mode_for_path,
    validate_destination,
    find_affected_models,
)
```
to also import the two new functions:
```python
from app.services.file_view_ops import (
    ensure_unambiguous_path,
    is_self_nested_move,
    path_conflicts_with_watch_root,
    rewrite_affected_paths,
    rewrite_tracked_folder_paths,
    resolve_storage_mode_for_path,
    validate_destination,
    find_affected_models,
    find_affected_tracked_folders,
)
```

In `rename_folder`, change the line `rewrite_affected_paths(str(source), str(destination))` to add a call right after it:
```python
    rewrite_affected_paths(str(source), str(destination))
    rewrite_tracked_folder_paths(str(source), str(destination))
    return {"path": str(destination)}
```

In `move_folder`, make the identical change to its own `rewrite_affected_paths(str(source), str(destination))` line:
```python
    rewrite_affected_paths(str(source), str(destination))
    rewrite_tracked_folder_paths(str(source), str(destination))
    return {"path": str(destination)}
```

In `delete_folder`, add tracked-folder cleanup alongside the existing model cleanup. Immediately after the existing model-cleanup block's closing (the `finally: conn.close()` that follows the `for row in affected:` loop, right before the `try: shutil.rmtree(target)` block), add:
```python
    tracked = find_affected_tracked_folders(str(target))
    if tracked:
        conn = get_db_conn()
        try:
            conn.executemany(
                "DELETE FROM file_view_tracked_folders WHERE path=?",
                [(p,) for p in tracked],
            )
            conn.commit()
        finally:
            conn.close()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_file_view_folder_ops.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && python -m pytest -v`
Expected: PASS (all except the one confirmed pre-existing, unrelated PDF sidecar-notes failure)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/file_view_ops.py backend/app/routers/file_view.py backend/tests/test_file_view_folder_ops.py
git commit -m "feat: keep file_view_tracked_folders in sync across rename/move/delete"
```

---

### Task 3: Frontend — API wrappers + fetch wiring + tree merging

**Files:**
- Modify: `frontend/services/api.ts`
- Modify: `frontend/App.tsx`
- Modify: `frontend/components/Sidebar.tsx`

**Interfaces:**
- Consumes: `POST /api/file-view/folder`, `GET /api/file-view/tracked-folders` (Task 1).
- Produces: `api.createFileViewFolder(parentPath: string | null, name: string): Promise<{path: string}>` and `api.getFileViewTrackedFolders(): Promise<string[]>` in `api.ts`. Produces a `trackedFolderPaths: string[]` prop on `Sidebar`, consumed by Task 4's UI additions.

This task has no backend changes and no automated test cycle — verification is manual via the packaged build, per this project's established convention.

- [ ] **Step 1: Add the two API wrappers**

Add inside the exported `api` object in `frontend/services/api.ts`, alongside the existing `renameFileViewFolder`/`moveFileViewFolder`/`deleteFileViewFolder` methods:

```ts
  createFileViewFolder: async (
    parentPath: string | null,
    name: string,
  ): Promise<{ path: string }> => {
    const res = await fetch(`${getApiBaseUrl()}/file-view/folder`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parentPath, name }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Failed to create folder");
    }
    return res.json();
  },

  getFileViewTrackedFolders: async (): Promise<string[]> => {
    const res = await fetch(`${getApiBaseUrl()}/file-view/tracked-folders`);
    if (!res.ok) throw new Error("Failed to fetch tracked folders");
    const body = await res.json();
    return body.paths;
  },
```

- [ ] **Step 2: Add `trackedFolderPaths` state and wire it into `fetchData` and both `<Sidebar>` call sites**

In `frontend/App.tsx`, add a new state declaration near the existing `folders`/`models`/`storageStats` state:

```tsx
  const [trackedFolderPaths, setTrackedFolderPaths] = useState<string[]>([]);
```

Change the existing `fetchData` function (currently):
```tsx
  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [fetchedFolders, fetchedModels, fetchedStats] = await Promise.all(
        [api.getFolders(), api.getModels("all"), api.getStorageStats()],
      );
      setFolders(fetchedFolders);
      setModels(fetchedModels);
      setStorageStats(fetchedStats);
    } catch (error) {
      console.error("Failed to fetch data:", error);
    } finally {
      setIsLoading(false);
    }
  }, []);
```
to:
```tsx
  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [fetchedFolders, fetchedModels, fetchedStats, fetchedTrackedPaths] = await Promise.all(
        [api.getFolders(), api.getModels("all"), api.getStorageStats(), api.getFileViewTrackedFolders()],
      );
      setFolders(fetchedFolders);
      setModels(fetchedModels);
      setStorageStats(fetchedStats);
      setTrackedFolderPaths(fetchedTrackedPaths);
    } catch (error) {
      console.error("Failed to fetch data:", error);
    } finally {
      setIsLoading(false);
    }
  }, []);
```

Add `trackedFolderPaths={trackedFolderPaths}` to **both** `<Sidebar ... />` call sites (desktop, currently ending `onFileViewMutated={fetchData}` then `variant="desktop"`; and mobile, currently ending `onFileViewMutated={fetchData}` then `variant="mobile"`) — add the new prop on its own line right after `onFileViewMutated={fetchData}` in each block.

- [ ] **Step 3: Add the prop to `SidebarProps` and destructure it**

In `frontend/components/Sidebar.tsx`, add to the `SidebarProps` interface, after the existing `onFileViewMutated: () => void;` line:
```tsx
  trackedFolderPaths: string[];
```

Add to the component's destructured props, after `onFileViewMutated,`:
```tsx
  trackedFolderPaths,
```

- [ ] **Step 4: Add a raw-folder-path segment helper and merge tracked folders into `fileTree`**

Add a new exported function to `frontend/types.ts`, placed directly after the existing `fileViewSegments` function:

```ts
/**
 * Same "meaningful segments" derivation as fileViewSegments, but for a raw
 * folder path rather than a file path -- there's no filename to drop, so
 * this skips the .pop() step fileViewSegments does. Used for tracked
 * (possibly empty) File-mode folders, which have no model file to derive
 * segments from.
 */
export function fileViewFolderSegments(folderPath: string): string[] {
  const normalized = folderPath.replace(/\\/g, "/");
  const segments = normalized.split("/").filter((s) => s.length > 0);
  const uploadDirIndex = segments.findIndex((s) => s.toLowerCase() === "uploads");
  return uploadDirIndex >= 0 ? segments.slice(uploadDirIndex + 1) : segments;
}
```

In `frontend/components/Sidebar.tsx`, add the import alongside the existing one:
```tsx
import { FILE_VIEW_UPLOADS_BUCKET_ID, fileViewSegments, fileViewFolderSegments } from "../types";
```

Extend the `fileTree` useMemo. The current version (ending with `return { items: root.children.map(strip), realPaths };`, dependency array `[models]`) gets a second walk added right before the `strip`/`return` lines, and the dependency array grows to include `trackedFolderPaths`:

```tsx
    // Tracked (possibly empty) folders get the same node-creation treatment
    // as model-derived ones, but seeded from a raw folder path instead of a
    // model's filePath -- there's no filename to drop, so this uses
    // fileViewFolderSegments instead of fileViewSegments. A tracked folder
    // that already has models under it is a no-op here: childMap already
    // has every node on its chain from the walk above.
    trackedFolderPaths.forEach((trackedPath) => {
      const meaningfulSegments = fileViewFolderSegments(trackedPath);
      if (meaningfulSegments.length === 0) return;

      const rawSegments = trackedPath.replace(/\\/g, "/").split("/").filter((s) => s.length > 0);
      const dropped = rawSegments.length - meaningfulSegments.length;

      let cursor = root;
      let idPath = "file";
      meaningfulSegments.forEach((segment, index) => {
        idPath += `/${segment}`;
        if (!cursor.childMap[segment]) {
          const node: FileNode = { id: idPath, label: segment, children: [], childMap: {} };
          cursor.childMap[segment] = node;
          cursor.children.push(node);
          realPaths.set(idPath, rawSegments.slice(0, dropped + index + 1).join("/"));
        }
        cursor = cursor.childMap[segment];
      });
    });

    const strip = (node: FileNode): TreeViewDefaultItemModelProperties => ({
      id: node.id,
      label: node.label,
      children: node.children.map(strip),
    });
    return { items: root.children.map(strip), realPaths };
  }, [models, trackedFolderPaths]);
```

(This replaces the existing `const strip = ...` through `}, [models]);` tail of the function — everything above the `trackedFolderPaths.forEach` block, i.e. the existing `models.forEach(...)` walk, stays exactly as it is today.)

- [ ] **Step 5: Manual verification**

Rebuild, uninstall, reinstall, hash-verify per this project's established convention. Against a test library:
- Confirm the app still loads with no console errors and the File-mode tree still shows all the same folders it did before this task (no regression from the added tracked-folder merge — with no tracked folders created yet, `trackedFolderPaths` is an empty array and the merge is a no-op).
- Confirm Logical mode is completely unaffected.

- [ ] **Step 6: Commit**

```bash
git add frontend/services/api.ts frontend/App.tsx frontend/components/Sidebar.tsx frontend/types.ts
git commit -m "feat: fetch and merge tracked File-view folders into the sidebar tree"
```

---

### Task 4: Frontend — New Folder menu items and standalone control

**Files:**
- Modify: `frontend/components/Sidebar.tsx`

**Interfaces:**
- Consumes: `api.createFileViewFolder` (Task 3).

- [ ] **Step 1: Add the create handler**

Add to `Sidebar.tsx`, near the other File-view handlers (`handleRenameFileViewFolder`, `handleDeleteFileViewFolder`):

```tsx
  const handleCreateFileViewFolder = async (parentPath: string | null) => {
    const name = window.prompt("New folder name:");
    if (!name) return;
    try {
      await api.createFileViewFolder(parentPath, name);
      onFileViewMutated();
    } catch (err) {
      console.error("Folder creation failed:", err);
      alert(err instanceof Error ? err.message : "Folder creation failed");
    }
  };
```

- [ ] **Step 2: Let the Uploads bucket open a context menu (today it opens none)**

Change `handleFileTreeContextMenu` and the `folderContextMenu` state's type. Currently:
```tsx
  const [folderContextMenu, setFolderContextMenu] = useState<{
    mouseX: number;
    mouseY: number;
    nodeId: string;
    realPath: string;
  } | null>(null);

  const handleFileTreeContextMenu = (e: React.MouseEvent, nodeId: string) => {
    if (nodeId === FILE_VIEW_UPLOADS_BUCKET_ID) return; // synthetic bucket, not a real single folder
    const realPath = fileTree.realPaths.get(nodeId);
    if (!realPath) return;
    e.preventDefault();
    e.stopPropagation();
    setFolderContextMenu({ mouseX: e.clientX - 2, mouseY: e.clientY - 4, nodeId, realPath });
  };
```
becomes:
```tsx
  const [folderContextMenu, setFolderContextMenu] = useState<{
    mouseX: number;
    mouseY: number;
    nodeId: string;
    realPath: string | null;
  } | null>(null);

  // The Uploads bucket isn't a real single directory, so it gets a menu
  // with only New Folder (realPath: null, meaning "create at the library
  // root" -- see handleCreateFileViewFolder's parentPath contract). Every
  // other node keeps the full Rename/Move/Delete/New Folder menu, gated on
  // having a resolvable real path.
  const handleFileTreeContextMenu = (e: React.MouseEvent, nodeId: string) => {
    const isUploadsBucket = nodeId === FILE_VIEW_UPLOADS_BUCKET_ID;
    const realPath = isUploadsBucket ? null : fileTree.realPaths.get(nodeId) ?? null;
    if (!isUploadsBucket && !realPath) return;
    e.preventDefault();
    e.stopPropagation();
    setFolderContextMenu({ mouseX: e.clientX - 2, mouseY: e.clientY - 4, nodeId, realPath });
  };
```

- [ ] **Step 3: Add "New Folder" to the menu JSX, and gate Rename/Move/Delete on a real path**

The existing folder context-menu JSX renders `Rename`, `Move`, `Delete` unconditionally. Change it to add a `New Folder` item first (always rendered) and wrap the existing three items so they only render when `folderContextMenu.realPath` isn't `null`. Each existing item's `onClick` also needs its own explicit `folderContextMenu?.realPath` truthy check (not just the outer conditional) so TypeScript narrows `string | null` to `string` correctly at the call site:

```tsx
      <Menu
        open={folderContextMenu !== null}
        onClose={() => setFolderContextMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={
          folderContextMenu ? { top: folderContextMenu.mouseY, left: folderContextMenu.mouseX } : undefined
        }
      >
        <MenuItem
          onClick={() => {
            if (folderContextMenu) handleCreateFileViewFolder(folderContextMenu.realPath);
            setFolderContextMenu(null);
          }}
        >
          New Folder
        </MenuItem>
        {folderContextMenu?.realPath !== null && (
          <>
            <MenuItem
              onClick={() => {
                if (folderContextMenu?.realPath) handleRenameFileViewFolder(folderContextMenu.nodeId, folderContextMenu.realPath);
                setFolderContextMenu(null);
              }}
            >
              Rename
            </MenuItem>
            <MenuItem
              onClick={() => {
                if (folderContextMenu?.realPath) {
                  const targetParent = window.prompt(
                    "Move to which real folder path? (paste the full destination directory)",
                    folderContextMenu.realPath,
                  );
                  if (targetParent && targetParent !== folderContextMenu.realPath) {
                    api
                      .moveFileViewFolder(folderContextMenu.realPath, targetParent)
                      .then(onFileViewMutated)
                      .catch((err) => {
                        console.error("Folder move failed:", err);
                        alert(err instanceof Error ? err.message : "Folder move failed");
                      });
                  }
                }
                setFolderContextMenu(null);
              }}
            >
              Move
            </MenuItem>
            <MenuItem
              onClick={() => {
                if (folderContextMenu?.realPath) handleDeleteFileViewFolder(folderContextMenu.nodeId, folderContextMenu.realPath);
                setFolderContextMenu(null);
              }}
            >
              Delete
            </MenuItem>
          </>
        )}
      </Menu>
```

This is a full replacement of the existing `<Menu>...</Menu>` block for the folder context menu — every `onClick` body changes (adding the `?.realPath` truthy check in place of the previous bare `if (folderContextMenu)`), not just the items being added.

- [ ] **Step 4: Add the standalone "New Folder" button, File mode only**

In the JSX block containing the Logical/File toggle buttons (the `<div className="flex gap-1 px-1 mb-2">...</div>` wrapping the "Logical"/"File" buttons), add a new button directly after that div's closing tag, before the conditional `{viewMode === "logical" ? (...) : (...)}` block that renders the two `RichTreeView`s:

```tsx
        {viewMode === "file" && (
          <button
            onClick={() => handleCreateFileViewFolder(null)}
            className="w-full text-xs py-1 mb-2 rounded bg-vault-800 text-slate-300 hover:bg-vault-700"
          >
            + New Folder
          </button>
        )}
```

- [ ] **Step 5: Manual verification**

Rebuild, uninstall, reinstall, hash-verify. Against a test library with both copy-mode and reference-mode models:
- Right-click a real folder node: confirm the menu now shows New Folder, Rename, Move, Delete (four items).
- Create a nested folder via that menu: confirm it appears in the tree immediately, is empty, and is a real directory on disk.
- Right-click the Uploads bucket: confirm the menu shows only New Folder (no Rename/Move/Delete).
- Create a folder from the Uploads bucket: confirm it appears as a new top-level node under the managed library root.
- Use the standalone "New Folder" button: confirm it creates a top-level folder the same way.
- Drag a model into a newly-created empty folder, then drag it back out: confirm the folder is still present and empty afterward, not vanished.
- Confirm Logical mode's own folder creation (the existing "New Root Folder" button) is completely unaffected.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/Sidebar.tsx
git commit -m "feat: add New Folder to File view's right-click menu and a standalone control"
```

---

## Self-Review Notes

- **Spec coverage:** every Goals-section item maps to a task — nested creation (Task 4), Uploads-bucket creation (Task 4), standalone top-level control (Task 4), persistence of empty tracked folders (Tasks 1-2, explicitly tested by the "survives being emptied out" test in Task 2), `window.prompt` naming (Task 4, matches existing Rename pattern).
- **Placeholder scan:** none found — every code block is complete, pasteable content.
- **Type consistency:** `folderContextMenu.realPath` changes from `string` to `string | null` in Task 4 — every consumer of that field (all three existing menu item handlers) is updated in the same step to add the narrowing check; Task 1-3 don't touch this field. `api.createFileViewFolder`'s `parentPath: string | null` parameter matches exactly how Task 4 calls it (`folderContextMenu.realPath` directly, or a literal `null` for the Uploads-bucket/standalone cases) and matches the backend's `Optional[str] = None` Pydantic field.
