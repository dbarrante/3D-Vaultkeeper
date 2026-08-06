# File-View Folder Cards + Pinned Folder Thumbnails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** File view's main grid shows folder cards for the current directory's subfolders (matching Logical view, which already does this). A new right-click "Set as Folder Thumbnail" action on a model card pins that model's thumbnail as its containing folder's displayed image, in both Logical and File view, overriding the existing auto-picked-earliest-model fallback until cleared.

**Architecture:** Extract the sidebar's private path-tree-building logic into a shared `frontend/lib/fileViewTree.ts` so the main grid can synthesize `Folder`-shaped card objects for File view's current directory (reusing `ModelList.tsx`'s existing folder-card rendering unchanged). Add a nullable `pinnedThumbnailModelId` column to both `folders` (Logical) and `file_view_tracked_folders` (File view, which already tracks folders by path and already survives renames/moves).

**Tech Stack:** FastAPI + SQLite (backend), React/TypeScript + MUI (frontend).

## Global Constraints

- Design doc: `docs/superpowers/specs/2026-08-06-file-view-folder-cards-and-pinned-thumbnails-design.md`.
- Pinning always targets a model's own immediate parent directory (derived from its `filePath`), never "whatever folder the view currently happens to be scoped to" — this matters because a model can be right-clicked while browsing `"all"` or the Uploads bucket, neither of which is a single real path.
- Pinning a model with no thumbnail yet is allowed (stores the pin; the card falls back to the generic folder icon until that model's thumbnail exists, then picks it up automatically). A dangling pin (pinned model later deleted) behaves the same way — never silently reverts to the auto-pick fallback.
- Logical folder-card previews (`count`) count **direct child models only** (`folderId` equality) — unchanged, existing behavior. File-view folder-card previews count **all descendant models recursively** (path-prefix match, any depth) — this is a deliberate difference, not a bug: it matches File view's own existing navigation semantics, where browsing into a folder already shows every model whose `filePath` starts with that folder's path, at any depth, not just direct children (see `App.tsx`'s existing `filteredModels` File-view branch).
- Backend endpoint naming: `PATCH /api/folders/{folder_id}/pin-thumbnail` (Logical) and `POST /api/file-view/folder/pin-thumbnail` (File view) — two small dedicated endpoints rather than extending the existing `PATCH /api/folders/{folder_id}` (which today silently ignores several fields in its own request body other than `name` — a pre-existing quirk this plan does not touch or depend on).

---

### Task 1: Backend — Logical folder pinning

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/routers/folders.py`
- Test: `backend/tests/test_folder_pinned_thumbnail.py`

**Interfaces:**
- Produces: `PATCH /api/folders/{folder_id}/pin-thumbnail`, body `{"modelId": string | null}`, returns the updated folder (via `row_to_folder`, now including `pinnedThumbnailModelId`). `GET /api/folders` and `POST /api/folders` responses also now include `pinnedThumbnailModelId` (always `null` for a freshly-created folder). Task 5's frontend consumes this field name exactly.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_folder_pinned_thumbnail.py
def test_get_folders_includes_pinned_thumbnail_field(client):
    resp = client.get("/api/folders")
    assert resp.status_code == 200
    folders = resp.json()
    assert len(folders) > 0
    assert "pinnedThumbnailModelId" in folders[0]


def test_new_folder_has_no_pinned_thumbnail_by_default(client):
    resp = client.post("/api/folders", json={"name": "Test Folder"})
    assert resp.status_code == 200
    assert resp.json()["pinnedThumbnailModelId"] is None


def test_pin_thumbnail_sets_field(client):
    create = client.post("/api/folders", json={"name": "Pinnable"})
    folder_id = create.json()["id"]

    resp = client.patch(f"/api/folders/{folder_id}/pin-thumbnail", json={"modelId": "some-model-id"})
    assert resp.status_code == 200
    assert resp.json()["pinnedThumbnailModelId"] == "some-model-id"

    refetched = client.get("/api/folders").json()
    match = next(f for f in refetched if f["id"] == folder_id)
    assert match["pinnedThumbnailModelId"] == "some-model-id"


def test_pin_thumbnail_null_clears_it(client):
    create = client.post("/api/folders", json={"name": "Pinnable2"})
    folder_id = create.json()["id"]
    client.patch(f"/api/folders/{folder_id}/pin-thumbnail", json={"modelId": "some-model-id"})

    resp = client.patch(f"/api/folders/{folder_id}/pin-thumbnail", json={"modelId": None})
    assert resp.status_code == 200
    assert resp.json()["pinnedThumbnailModelId"] is None


def test_pin_thumbnail_404_for_missing_folder(client):
    resp = client.patch("/api/folders/does-not-exist/pin-thumbnail", json={"modelId": "x"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_folder_pinned_thumbnail.py -v`
Expected: FAIL — `pinnedThumbnailModelId` missing from responses; the `pin-thumbnail` route doesn't exist (404 for all, including the ones that should be 200).

- [ ] **Step 3: Add the schema migration**

In `backend/app/db.py`, right after the existing `description` migration (the `try: cur.execute("ALTER TABLE folders ADD COLUMN description TEXT") ... except sqlite3.OperationalError: pass` block, immediately following the `CREATE TABLE IF NOT EXISTS folders` statement):

```python
    try:
        cur.execute("ALTER TABLE folders ADD COLUMN pinnedThumbnailModelId TEXT")
    except sqlite3.OperationalError:
        pass
```

- [ ] **Step 4: Update `row_to_folder`**

In `backend/app/db.py`, the `row_to_folder` function:

```python
def row_to_folder(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "parentId": row["parentId"],
        "description": row["description"],
        "pinnedThumbnailModelId": row["pinnedThumbnailModelId"],
    }
```

- [ ] **Step 5: Update the SELECT statements and add the new route**

In `backend/app/routers/folders.py`, both existing `SELECT id,name,parentId,description FROM folders` statements (in `get_folders` and `update_folder`) need `pinnedThumbnailModelId` added to their column list:

```python
    cur.execute("SELECT id,name,parentId,description,pinnedThumbnailModelId FROM folders")
```

(one occurrence in `get_folders`, one in `update_folder` — both become this same statement).

Add a new Pydantic model and route, placed after `update_folder`:

```python
class FolderPinThumbnailRequest(BaseModel):
    modelId: Union[str, None] = None


@router.patch("/api/folders/{folder_id}/pin-thumbnail")
def pin_folder_thumbnail(folder_id: str, item: FolderPinThumbnailRequest):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE folders SET pinnedThumbnailModelId=? WHERE id=?",
        (item.modelId, folder_id),
    )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Folder not found")
    conn.commit()
    cur.execute(
        "SELECT id,name,parentId,description,pinnedThumbnailModelId FROM folders WHERE id=?",
        (folder_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row_to_folder(row)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_folder_pinned_thumbnail.py -v`
Expected: PASS (5/5).

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && python -m pytest -q`
Expected: same pass count as before this task, plus these 5 new passes (aside from the known pre-existing, unrelated sidecar-notes failure).

- [ ] **Step 8: Commit**

```bash
git add backend/app/db.py backend/app/routers/folders.py backend/tests/test_folder_pinned_thumbnail.py
git commit -m "feat: add pinned-thumbnail support for Logical folders"
```

---

### Task 2: Backend — File-view folder pinning

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/routers/file_view.py`
- Test: `backend/tests/test_file_view_pinned_thumbnail.py`

**Interfaces:**
- Produces: `POST /api/file-view/folder/pin-thumbnail`, body `{"path": string, "modelId": string | null}`, returns `{"path": string, "pinnedThumbnailModelId": string | null}`. `GET /api/file-view/folder-thumbnails`, returns `{"pins": {[path: string]: string}}` — every tracked folder's path mapped to its non-null pinned model id. Task 5's frontend consumes both exactly.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_file_view_pinned_thumbnail.py
from pathlib import Path


def test_pin_thumbnail_creates_tracked_row_for_untracked_folder(client, tmp_path):
    from app.db import get_db_conn

    folder = tmp_path / "Vehicles"
    folder.mkdir()

    resp = client.post(
        "/api/file-view/folder/pin-thumbnail",
        json={"path": str(folder), "modelId": "abc"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"path": str(folder), "pinnedThumbnailModelId": "abc"}

    conn = get_db_conn()
    row = conn.execute(
        "SELECT pinnedThumbnailModelId FROM file_view_tracked_folders WHERE path=?",
        (str(folder),),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["pinnedThumbnailModelId"] == "abc"


def test_pin_thumbnail_null_clears_existing_pin(client, tmp_path):
    folder = tmp_path / "Terrain"
    folder.mkdir()
    client.post("/api/file-view/folder/pin-thumbnail", json={"path": str(folder), "modelId": "xyz"})

    resp = client.post("/api/file-view/folder/pin-thumbnail", json={"path": str(folder), "modelId": None})
    assert resp.status_code == 200
    assert resp.json()["pinnedThumbnailModelId"] is None


def test_pin_thumbnail_on_already_tracked_folder_preserves_tracking(client, tmp_path):
    folder = tmp_path / "EmptyOne"
    folder.mkdir()
    create_resp = client.post("/api/file-view/folder", json={"parentPath": str(tmp_path), "name": "EmptyOne"})
    assert create_resp.status_code == 200

    resp = client.post("/api/file-view/folder/pin-thumbnail", json={"path": str(folder), "modelId": "m1"})
    assert resp.status_code == 200

    tracked = client.get("/api/file-view/tracked-folders").json()
    assert str(folder) in tracked["paths"]


def test_get_folder_thumbnails_returns_only_pinned_paths(client, tmp_path):
    pinned = tmp_path / "Pinned"
    pinned.mkdir()
    unpinned = tmp_path / "Unpinned"
    unpinned.mkdir()
    client.post("/api/file-view/folder", json={"parentPath": str(tmp_path), "name": "Unpinned"})
    client.post("/api/file-view/folder/pin-thumbnail", json={"path": str(pinned), "modelId": "m2"})

    resp = client.get("/api/file-view/folder-thumbnails")
    assert resp.status_code == 200
    pins = resp.json()["pins"]
    assert pins.get(str(pinned)) == "m2"
    assert str(unpinned) not in pins
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_file_view_pinned_thumbnail.py -v`
Expected: FAIL — routes don't exist yet (404s), column doesn't exist.

- [ ] **Step 3: Add the schema migration**

In `backend/app/db.py`, right after the `CREATE TABLE IF NOT EXISTS file_view_tracked_folders (path TEXT PRIMARY KEY)` statement:

```python
    try:
        cur.execute("ALTER TABLE file_view_tracked_folders ADD COLUMN pinnedThumbnailModelId TEXT")
    except sqlite3.OperationalError:
        pass
```

- [ ] **Step 4: Add the two new routes**

In `backend/app/routers/file_view.py`, append after `get_tracked_folders`:

```python
class FileViewPinThumbnailRequest(BaseModel):
    path: str
    modelId: Optional[str] = None


@router.post("/folder/pin-thumbnail")
def pin_file_view_folder_thumbnail(body: FileViewPinThumbnailRequest):
    normalized = os.path.normpath(body.path)
    conn = get_db_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO file_view_tracked_folders(path) VALUES (?)",
            (normalized,),
        )
        conn.execute(
            "UPDATE file_view_tracked_folders SET pinnedThumbnailModelId=? WHERE path=?",
            (body.modelId, normalized),
        )
        conn.commit()
    finally:
        conn.close()
    return {"path": normalized, "pinnedThumbnailModelId": body.modelId}


@router.get("/folder-thumbnails")
def get_folder_thumbnails():
    conn = get_db_conn()
    try:
        rows = conn.execute(
            "SELECT path, pinnedThumbnailModelId FROM file_view_tracked_folders "
            "WHERE pinnedThumbnailModelId IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return {"pins": {row["path"]: row["pinnedThumbnailModelId"] for row in rows}}
```

(`router` has `prefix="/api/file-view"`, so these resolve to `POST /api/file-view/folder/pin-thumbnail` and `GET /api/file-view/folder-thumbnails`, matching the interfaces above. Path normalization via `os.path.normpath` before storing/querying matches the convention already established by `create_folder`'s `normalized_new_dir` handling in this same file, so a pin set against a path in one form matches lookups/rewrites keyed the same way.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_file_view_pinned_thumbnail.py -v`
Expected: PASS (4/4).

- [ ] **Step 6: Confirm rename/move correctly carries a pin along**

Add one more test to the same file, exercising the existing `rewrite_tracked_folder_paths` machinery (unchanged by this task) against a row that now has a pin set:

```python
def test_pin_survives_folder_rename(client, tmp_path):
    folder = tmp_path / "Original"
    folder.mkdir()
    client.post("/api/file-view/folder/pin-thumbnail", json={"path": str(folder), "modelId": "m3"})

    resp = client.post("/api/file-view/folder/rename", json={"path": str(folder), "newName": "Renamed"})
    assert resp.status_code == 200
    new_path = resp.json()["path"]

    pins = client.get("/api/file-view/folder-thumbnails").json()["pins"]
    assert pins.get(new_path) == "m3"
    assert str(folder) not in pins
```

Run: `cd backend && python -m pytest tests/test_file_view_pinned_thumbnail.py -v`
Expected: PASS (5/5) — this should pass with no further code changes, since `rewrite_tracked_folder_paths` already `UPDATE`s the `path` column of every affected row regardless of what other columns exist on it. If it fails, that's a real finding to investigate, not something to work around.

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && python -m pytest -q`
Expected: same baseline pass count plus these 5 new passes.

- [ ] **Step 8: Commit**

```bash
git add backend/app/db.py backend/app/routers/file_view.py backend/tests/test_file_view_pinned_thumbnail.py
git commit -m "feat: add pinned-thumbnail support for File-view folders"
```

---

### Task 3: Frontend — extract shared File-view tree helper

**Files:**
- Create: `frontend/lib/fileViewTree.ts`
- Modify: `frontend/components/Sidebar.tsx`

**Interfaces:**
- Produces: `buildFileViewTree(models: STLModel[], trackedFolderPaths: string[]): FileViewTree` and `getDirectChildren(tree: FileViewTree, currentFolderId: string): FileTreeNode[]`, where `FileTreeNode = {id: string; label: string; children: FileTreeNode[]}` and `FileViewTree = {items: FileTreeNode[]; realPaths: Map<string, string>}`. Task 4 consumes both exactly.
- Consumes: `fileViewSegments`, `fileViewFolderSegments`, `FILE_VIEW_UPLOADS_BUCKET_ID` from `frontend/types.ts` (all pre-existing, unchanged).

This is a pure extraction — no behavior change to the sidebar's tree. It is verified correct by confirming the sidebar file-tree still renders and navigates identically to before.

- [ ] **Step 1: Create the shared module**

```ts
// frontend/lib/fileViewTree.ts
import { STLModel, fileViewSegments, fileViewFolderSegments, FILE_VIEW_UPLOADS_BUCKET_ID } from "../types";

export interface FileTreeNode {
  id: string;
  label: string;
  children: FileTreeNode[];
}

export interface FileViewTree {
  items: FileTreeNode[];
  realPaths: Map<string, string>;
}

// Extracted verbatim (no behavior change) from Sidebar.tsx's private
// fileTree useMemo, so both the sidebar's full recursive tree and the main
// grid's "direct children of the current level" (see getDirectChildren
// below) derive from one shared implementation instead of two independent
// walks that could silently drift apart.
export function buildFileViewTree(
  models: STLModel[],
  trackedFolderPaths: string[],
): FileViewTree {
  type BuildNode = { id: string; label: string; children: BuildNode[]; childMap: Record<string, BuildNode> };
  const root: BuildNode = { id: "__root__", label: "", children: [], childMap: {} };
  const realPaths = new Map<string, string>();

  models.forEach((m) => {
    if (!m.filePath) return;
    const meaningfulSegments = fileViewSegments(m.filePath);

    if (meaningfulSegments.length === 0) {
      if (!root.childMap[FILE_VIEW_UPLOADS_BUCKET_ID]) {
        const node: BuildNode = { id: FILE_VIEW_UPLOADS_BUCKET_ID, label: "Uploads", children: [], childMap: {} };
        root.childMap[FILE_VIEW_UPLOADS_BUCKET_ID] = node;
        root.children.push(node);
      }
      return;
    }

    const rawSegments = m.filePath.replace(/\\/g, "/").split("/").filter((s) => s.length > 0);
    rawSegments.pop();
    const dropped = rawSegments.length - meaningfulSegments.length;

    let cursor = root;
    let idPath = "file";
    meaningfulSegments.forEach((segment, index) => {
      idPath += `/${segment}`;
      if (!cursor.childMap[segment]) {
        const node: BuildNode = { id: idPath, label: segment, children: [], childMap: {} };
        cursor.childMap[segment] = node;
        cursor.children.push(node);
        realPaths.set(idPath, rawSegments.slice(0, dropped + index + 1).join("/"));
      }
      cursor = cursor.childMap[segment];
    });
  });

  trackedFolderPaths.forEach((trackedPath) => {
    const rawSegments = trackedPath.replace(/\\/g, "/").split("/").filter((s) => s.length > 0);
    let meaningfulSegments = fileViewFolderSegments(trackedPath);
    if (meaningfulSegments.length === 0) meaningfulSegments = rawSegments;

    const dropped = rawSegments.length - meaningfulSegments.length;

    let cursor = root;
    let idPath = "file";
    meaningfulSegments.forEach((segment, index) => {
      idPath += `/${segment}`;
      if (!cursor.childMap[segment]) {
        const node: BuildNode = { id: idPath, label: segment, children: [], childMap: {} };
        cursor.childMap[segment] = node;
        cursor.children.push(node);
        realPaths.set(idPath, rawSegments.slice(0, dropped + index + 1).join("/"));
      }
      cursor = cursor.childMap[segment];
    });
  });

  const strip = (node: BuildNode): FileTreeNode => ({
    id: node.id,
    label: node.label,
    children: node.children.map(strip),
  });
  return { items: root.children.map(strip), realPaths };
}

// Direct children of one level, used by App.tsx to synthesize folder cards
// for the main grid in File view. Node ids already encode the full path
// from the root (see buildFileViewTree above), so this is an exact-id
// lookup down the tree rather than a re-derivation from segments.
export function getDirectChildren(
  tree: FileViewTree,
  currentFolderId: string,
): FileTreeNode[] {
  if (currentFolderId === "all") return tree.items;

  function find(nodes: FileTreeNode[]): FileTreeNode[] | null {
    for (const node of nodes) {
      if (node.id === currentFolderId) return node.children;
      const found = find(node.children);
      if (found) return found;
    }
    return null;
  }

  return find(tree.items) ?? [];
}
```

- [ ] **Step 2: Refactor Sidebar.tsx to use the shared module**

Add the import near `Sidebar.tsx`'s other local imports:

```tsx
import { buildFileViewTree } from "../lib/fileViewTree";
```

Replace the entire private `fileTree` `useMemo` (`Sidebar.tsx:304-393`, from `const fileTree = useMemo(() => {` through its closing `}, [models, trackedFolderPaths]);`) with:

```tsx
  // Groups every model by its filePath's directory instead of folderId.
  // Models with no filePath (shouldn't normally happen post-migration, but
  // defensive) or whose filePath sits directly in the flat pre-feature
  // upload location (no real subdirectory under it) land in a single
  // synthetic "Uploads" bucket rather than fabricating structure that was
  // never there -- see the spec's Non-goals. Tree-building itself lives in
  // frontend/lib/fileViewTree.ts, shared with App.tsx's File-view folder
  // cards, so both can never derive a different tree from the same data.
  const fileTree = useMemo(
    () => buildFileViewTree(models, trackedFolderPaths),
    [models, trackedFolderPaths],
  );
```

`fileTree.items` and `fileTree.realPaths` are consumed identically to before by the rest of `Sidebar.tsx` (e.g. `fileTree.realPaths.get(nodeId)` at the existing `handleFileTreeContextMenu`, and `fileTree.items` fed into `RichTreeView`'s `items` prop) — the extracted module's `FileTreeNode` shape (`{id, label, children}`) is identical to what the removed local `strip()` step used to produce, so no downstream consumer needs any change.

- [ ] **Step 3: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds with no new TypeScript errors.

- [ ] **Step 4: Manual verification that the sidebar tree is unchanged**

Run: `cd frontend && bun run dev` (with the backend running), switch to File view, confirm the sidebar's folder tree renders the same folders/hierarchy as before this refactor, and that clicking a tree node still navigates the grid correctly. This is a pure refactor — any visible difference here is a bug in the extraction, not an intended change.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/fileViewTree.ts frontend/components/Sidebar.tsx
git commit -m "refactor: extract Sidebar's File-view tree-building into a shared helper"
```

---

### Task 4: Frontend — File-view folder cards in the grid

**Files:**
- Modify: `frontend/App.tsx`

**Interfaces:**
- Consumes: `buildFileViewTree`, `getDirectChildren` from `frontend/lib/fileViewTree.ts` (Task 3).
- Produces: replaces the hard-coded `folders={viewMode === "file" ? [] : filteredFolders}` — `ModelList` now receives real folder cards in File view too. No new props on `ModelList` itself (it already accepts `Folder[]` generically).

- [ ] **Step 1: Add the tree + folder-card memos**

In `frontend/App.tsx`, add the import:

```tsx
import { buildFileViewTree, getDirectChildren } from "./lib/fileViewTree";
```

Add these memos near the existing `filteredFolders`/`folderPreviews` memos (`App.tsx:352-393`):

```tsx
  const fileViewTree = useMemo(
    () => buildFileViewTree(models, trackedFolderPaths),
    [models, trackedFolderPaths],
  );

  const fileViewFolderNodes = useMemo(
    () => (viewMode === "file" ? getDirectChildren(fileViewTree, currentFolderId) : []),
    [fileViewTree, currentFolderId, viewMode],
  );

  // Synthesized as Folder-shaped objects so ModelList's existing folder-card
  // rendering and onNavigateFolder wiring need no changes -- parentId is
  // unused for File-view navigation (folder-card clicks just call
  // onNavigateFolder(folder.id), which does setCurrentFolderId(id) with no
  // parentId dependency anywhere in that path).
  const fileViewFolderCards = useMemo(
    () =>
      fileViewFolderNodes.map((node) => ({
        id: node.id,
        name: node.label,
        parentId: null,
        description: null,
      })),
    [fileViewFolderNodes],
  );

  // Unlike Logical's folderPreviews (direct-child models only, by folderId
  // equality), this counts every descendant model at any depth via a
  // path-prefix match -- matching File view's own existing navigation
  // semantics, where browsing into a folder already shows every model
  // whose filePath starts with that folder's path, not just direct
  // children (see filteredModels's File-view branch above).
  const fileViewFolderPreviews = useMemo(() => {
    const previews: Record<string, { count: number; thumbnail: string | null }> = {};
    fileViewFolderNodes.forEach((node) => {
      const realPath = fileViewTree.realPaths.get(node.id);
      if (!realPath) return;
      const normalizedRealPath = realPath.replace(/\\/g, "/");
      const prefix = `${normalizedRealPath}/`;
      let count = 0;
      let earliestDateAdded: number | undefined;
      let thumbnail: string | null = null;
      models.forEach((m) => {
        if (!m.filePath) return;
        const normalized = m.filePath.replace(/\\/g, "/");
        if (normalized === normalizedRealPath || normalized.startsWith(prefix)) {
          count += 1;
          if (m.thumbnail && (earliestDateAdded === undefined || m.dateAdded < earliestDateAdded)) {
            earliestDateAdded = m.dateAdded;
            thumbnail = m.thumbnail;
          }
        }
      });
      previews[node.id] = { count, thumbnail };
    });
    return previews;
  }, [fileViewFolderNodes, fileViewTree, models]);
```

- [ ] **Step 2: Wire the new memos into `ModelList`'s props**

Replace (`App.tsx:1041` and the following `folderPreviews` line, `App.tsx:1043`):

```tsx
                  folders={viewMode === "file" ? [] : filteredFolders}
```

with:

```tsx
                  folders={viewMode === "file" ? fileViewFolderCards : filteredFolders}
```

and:

```tsx
                  folderPreviews={folderPreviews}
```

with:

```tsx
                  folderPreviews={viewMode === "file" ? fileViewFolderPreviews : folderPreviews}
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds with no new TypeScript errors.

- [ ] **Step 4: Write and run a Playwright verification**

```python
# frontend/components/fileViewFolderCards.integration_test.py
# Run with a dev server + backend already running.
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
        page = browser.new_page(viewport={"width": 1920, "height": 1200})
        set_api_override(page)
        page.goto(FRONTEND_URL)
        page.wait_for_load_state("networkidle")

        page.get_by_text("File", exact=True).first.click()
        page.wait_for_timeout(1000)

        # A dev library should have at least one real subfolder under some
        # watched root -- if this assertion fails on a genuinely empty/flat
        # library, seed one real subfolder with a model in it first rather
        # than weakening this check.
        folder_cards = page.locator("text=Folder").locator("..")
        count_before_click = folder_cards.count()
        print(f"File view folder cards found: {count_before_click}")
        assert count_before_click > 0, "expected at least one folder card in File view"

        # Click the first folder card and confirm navigation actually
        # changes what's shown (grid contents differ, or at minimum the
        # click doesn't throw/no-op).
        page.locator("[class*='card']").first.click()
        page.wait_for_timeout(500)
        print("folder card click: no error")

        browser.close()
    print("ALL FILE-VIEW FOLDER CARD TESTS PASSED")


if __name__ == "__main__":
    main()
```

Run: `cd frontend && bun run dev` (separate terminal), then `python components/fileViewFolderCards.integration_test.py`
Expected: `ALL FILE-VIEW FOLDER CARD TESTS PASSED`. If the dev library has no real subfolders to find, adjust the test to create one via the existing `POST /api/file-view/folder` endpoint first (matching the pattern already used in `backend/tests/test_file_view_folder_ops.py`), rather than skipping the assertion.

- [ ] **Step 5: Commit**

```bash
git add frontend/App.tsx frontend/components/fileViewFolderCards.integration_test.py
git commit -m "feat: show folder cards in File view's main grid"
```

---

### Task 5: Frontend — pinned thumbnails (both views), context menus

**Files:**
- Modify: `frontend/services/api.ts`
- Modify: `frontend/App.tsx`
- Modify: `frontend/components/ModelList.tsx`
- Test: `frontend/components/pinnedFolderThumbnails.integration_test.py`

**Interfaces:**
- Consumes: `PATCH /api/folders/{id}/pin-thumbnail`, `POST /api/file-view/folder/pin-thumbnail`, `GET /api/file-view/folder-thumbnails` (Tasks 1-2). `fileViewTree`, `fileViewFolderPreviews` (Task 4).
- Produces: `api.pinFolderThumbnail(folderId, modelId)`, `api.pinFileViewFolderThumbnail(path, modelId)`, `api.getFileViewFolderThumbnails()` — no other task depends on these (terminal task).

- [ ] **Step 1: Add the API client functions**

In `frontend/services/api.ts`:

```ts
  pinFolderThumbnail: async (folderId: string, modelId: string | null): Promise<Folder> => {
    const res = await fetch(`${getApiBaseUrl()}/folders/${folderId}/pin-thumbnail`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ modelId }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Failed to pin folder thumbnail");
    }
    return res.json();
  },

  pinFileViewFolderThumbnail: async (
    path: string,
    modelId: string | null,
  ): Promise<{ path: string; pinnedThumbnailModelId: string | null }> => {
    const res = await fetch(`${getApiBaseUrl()}/file-view/folder/pin-thumbnail`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, modelId }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Failed to pin folder thumbnail");
    }
    return res.json();
  },

  getFileViewFolderThumbnails: async (): Promise<Record<string, string>> => {
    const res = await fetch(`${getApiBaseUrl()}/file-view/folder-thumbnails`);
    if (!res.ok) throw new Error("Failed to fetch File-view folder thumbnail pins");
    const body = await res.json();
    return body.pins;
  },
```

`Folder` is already imported/used elsewhere in this file (it's the existing type returned by `getFolders`/`createFolder`/`updateFolder`) — no new import needed.

- [ ] **Step 2: Add `Folder.pinnedThumbnailModelId` to the frontend type**

In `frontend/types.ts`, the `Folder` interface:

```ts
export interface Folder {
  id: string;
  name: string;
  parentId: string | null;
  icon?: string;
  description?: string | null;
  pinnedThumbnailModelId?: string | null;
}
```

- [ ] **Step 3: Add a small path helper**

In `frontend/types.ts`, alongside the existing `fileViewSegments`/`fileViewFolderSegments`:

```ts
// The immediate parent directory of a model's own filePath (filename
// dropped, forward-slash normalized) -- the pin target for "Set as Folder
// Thumbnail" in File view, deliberately independent of whatever
// currentFolderId the grid happens to be scoped to (see this plan's
// Global Constraints).
export function fileParentDirectory(filePath: string): string {
  const normalized = filePath.replace(/\\/g, "/");
  const lastSlash = normalized.lastIndexOf("/");
  return lastSlash === -1 ? normalized : normalized.slice(0, lastSlash);
}
```

- [ ] **Step 4: Fetch File-view pins in App.tsx and extend both preview memos**

In `frontend/App.tsx`, add state and a fetch alongside the existing `trackedFolderPaths` state/fetch:

```tsx
  const [folderThumbnailPins, setFolderThumbnailPins] = useState<Record<string, string>>({});
```

In `fetchData`'s `Promise.all` (where `fetchedTrackedPaths` is already fetched via `api.getFileViewTrackedFolders().catch(...)`), add a parallel fetch:

```tsx
    api.getFileViewFolderThumbnails().catch((err) => {
      console.warn("Failed to fetch File-view folder thumbnail pins:", err);
      return {} as Record<string, string>;
    }),
```

(added as a 5th element of the existing `Promise.all` array; destructure it as `fetchedFolderThumbnailPins` and call `setFolderThumbnailPins(fetchedFolderThumbnailPins)` alongside the existing `setTrackedFolderPaths(fetchedTrackedPaths)`.)

Extend the **Logical** `folderPreviews` memo (`App.tsx:375-393`) to check the pin first:

```tsx
  const folderPreviews = useMemo(() => {
    const previews: Record<string, { count: number; thumbnail: string | null }> = {};
    const earliestThumbnailDateAddedByFolder: Record<string, number> = {};
    const modelsById: Record<string, STLModel> = {};
    models.forEach((m) => {
      modelsById[m.id] = m;
      if (!previews[m.folderId]) {
        previews[m.folderId] = { count: 0, thumbnail: null };
      }
      previews[m.folderId].count += 1;
      if (
        m.thumbnail &&
        (earliestThumbnailDateAddedByFolder[m.folderId] === undefined ||
          m.dateAdded < earliestThumbnailDateAddedByFolder[m.folderId])
      ) {
        earliestThumbnailDateAddedByFolder[m.folderId] = m.dateAdded;
        previews[m.folderId].thumbnail = m.thumbnail;
      }
    });
    // Pinned thumbnail overrides the auto-pick above, if the pinned model
    // still exists and has a thumbnail. A dangling pin (deleted model) or a
    // pin on a model with no thumbnail yet falls back to the generic
    // folder icon, never silently back to the auto-pick -- the user's pin
    // should visibly represent their choice.
    folders.forEach((f) => {
      if (!f.pinnedThumbnailModelId) return;
      if (!previews[f.id]) previews[f.id] = { count: 0, thumbnail: null };
      const pinnedModel = modelsById[f.pinnedThumbnailModelId];
      previews[f.id].thumbnail = pinnedModel?.thumbnail ?? null;
    });
    return previews;
  }, [models, folders]);
```

Extend the **File-view** `fileViewFolderPreviews` memo (Task 4) the same way — add this after the `models.forEach` loop inside that memo, before `previews[node.id] = { count, thumbnail };`:

```tsx
      const pinnedModelId = realPath ? folderThumbnailPins[realPath] : undefined;
      if (pinnedModelId) {
        const pinnedModel = models.find((m) => m.id === pinnedModelId);
        thumbnail = pinnedModel?.thumbnail ?? null;
      }
```

(insert immediately before `previews[node.id] = { count, thumbnail };` inside the `fileViewFolderNodes.forEach` loop, and add `folderThumbnailPins` to that memo's dependency array.)

- [ ] **Step 5: Add the Logical-view model-card context menu (new)**

In `frontend/components/ModelList.tsx`, add new state alongside `fileContextMenu`:

```tsx
  const [logicalContextMenu, setLogicalContextMenu] = useState<{
    mouseX: number;
    mouseY: number;
    model: STLModel;
  } | null>(null);
```

Modify `handleCardContextMenu` (currently returns early for non-file mode) to branch instead of bailing:

```tsx
  const handleCardContextMenu = (e: React.MouseEvent, model: STLModel) => {
    e.preventDefault();
    e.stopPropagation();
    if (viewMode === "file") {
      setFileContextMenu({ mouseX: e.clientX - 2, mouseY: e.clientY - 4, model });
    } else {
      setLogicalContextMenu({ mouseX: e.clientX - 2, mouseY: e.clientY - 4, model });
    }
  };
```

Add a handler near the other model-card handlers:

```tsx
  const handleSetAsFolderThumbnail = async (model: STLModel) => {
    try {
      await api.pinFolderThumbnail(model.folderId, model.id);
      onFileViewMutated();
    } catch (err) {
      console.error("Set as folder thumbnail failed:", err);
      alert(err instanceof Error ? err.message : "Set as folder thumbnail failed");
    }
  };
```

(`onFileViewMutated` is the existing prop this component already calls after other mutations to trigger `App.tsx`'s `fetchData` refresh — reused here rather than introducing a new refresh callback, even though this action isn't File-view-specific; it's already the generic "something changed, refetch" signal this component has.)

Add a new `Menu` for `logicalContextMenu`, placed right after the existing `fileContextMenu` `Menu` block (`ModelList.tsx:1020-1052`):

```tsx
      <Menu
        open={logicalContextMenu !== null}
        onClose={() => setLogicalContextMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={
          logicalContextMenu ? { top: logicalContextMenu.mouseY, left: logicalContextMenu.mouseX } : undefined
        }
      >
        <MenuItem
          onClick={() => {
            if (logicalContextMenu) handleSetAsFolderThumbnail(logicalContextMenu.model);
            setLogicalContextMenu(null);
          }}
        >
          Set as Folder Thumbnail
        </MenuItem>
      </Menu>
```

- [ ] **Step 6: Add "Set as Folder Thumbnail" to the existing File-view model-card menu**

Add a handler:

```tsx
  const handleSetAsFileViewFolderThumbnail = async (model: STLModel) => {
    if (!model.filePath) return;
    const parentDir = fileParentDirectory(model.filePath);
    try {
      await api.pinFileViewFolderThumbnail(parentDir, model.id);
      onFileViewMutated();
    } catch (err) {
      console.error("Set as folder thumbnail failed:", err);
      alert(err instanceof Error ? err.message : "Set as folder thumbnail failed");
    }
  };
```

Import `fileParentDirectory` alongside this file's other `types` imports.

Add a new `MenuItem` to the existing File-view menu (`ModelList.tsx:1028-1051`), placed after Delete:

```tsx
        <MenuItem
          onClick={() => {
            if (fileContextMenu) handleSetAsFileViewFolderThumbnail(fileContextMenu.model);
            setFileContextMenu(null);
          }}
        >
          Set as Folder Thumbnail
        </MenuItem>
```

- [ ] **Step 7: Add the folder-card "Clear Pinned Thumbnail" context menu (new, both views)**

Add state:

```tsx
  const [folderCardContextMenu, setFolderCardContextMenu] = useState<{
    mouseX: number;
    mouseY: number;
    folder: Folder;
  } | null>(null);
```

Add a right-click handler and wire it onto the folder-card `<div>` (the outer folder-tile wrapper at `ModelList.tsx:619-637`, alongside its existing `onClick`/`onDragOver`/etc. handlers):

```tsx
          onContextMenu={(e) => {
            e.preventDefault();
            e.stopPropagation();
            if (!folder.pinnedThumbnailModelId) return; // menu-free when nothing to clear
            setFolderCardContextMenu({ mouseX: e.clientX - 2, mouseY: e.clientY - 4, folder });
          }}
```

Add a clear handler:

```tsx
  const handleClearFolderThumbnail = async (folder: Folder) => {
    try {
      if (viewMode === "file") {
        const realPath = fileViewTree?.realPaths.get(folder.id);
        if (realPath) await api.pinFileViewFolderThumbnail(realPath, null);
      } else {
        await api.pinFolderThumbnail(folder.id, null);
      }
      onFileViewMutated();
    } catch (err) {
      console.error("Clear folder thumbnail failed:", err);
      alert(err instanceof Error ? err.message : "Clear folder thumbnail failed");
    }
  };
```

This needs `fileViewTree`'s `realPaths` map available inside `ModelList.tsx` for the File-view case — add a new prop `fileViewTree?: FileViewTree` to `ModelList`'s props interface (imported from `../lib/fileViewTree`), passed from `App.tsx` as `fileViewTree={viewMode === "file" ? fileViewTree : undefined}` (reusing the exact same `fileViewTree` memo Task 4 already built — no new computation).

Add the menu, alongside the other two menus:

```tsx
      <Menu
        open={folderCardContextMenu !== null}
        onClose={() => setFolderCardContextMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={
          folderCardContextMenu
            ? { top: folderCardContextMenu.mouseY, left: folderCardContextMenu.mouseX }
            : undefined
        }
      >
        <MenuItem
          onClick={() => {
            if (folderCardContextMenu) handleClearFolderThumbnail(folderCardContextMenu.folder);
            setFolderCardContextMenu(null);
          }}
        >
          Clear Pinned Thumbnail
        </MenuItem>
      </Menu>
```

- [ ] **Step 8: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds with no new TypeScript errors.

- [ ] **Step 9: Write and run a Playwright verification**

```python
# frontend/components/pinnedFolderThumbnails.integration_test.py
# Run with a dev server + backend already running.
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
        page = browser.new_page(viewport={"width": 1920, "height": 1200})
        set_api_override(page)
        page.goto(FRONTEND_URL)
        page.wait_for_load_state("networkidle")

        # Logical view: right-click a model card, confirm the new menu item
        # exists (doesn't assert the full pin-and-verify round trip against
        # real dev-library data, since that depends on which folder/model
        # happens to be first in this environment -- the menu item's
        # presence is the automatable part; a full round-trip is a manual
        # check).
        first_model_card = page.locator("[class*='card']").first
        first_model_card.click(button="right")
        item = page.get_by_text("Set as Folder Thumbnail", exact=True).first
        assert item.is_visible(), "expected 'Set as Folder Thumbnail' in the Logical-view model menu"
        page.keyboard.press("Escape")
        print("logical model context menu: PASSED")

        # File view: same check.
        page.get_by_text("File", exact=True).first.click()
        page.wait_for_timeout(1000)
        file_model_card = page.locator("[class*='card']").first
        file_model_card.click(button="right")
        item2 = page.get_by_text("Set as Folder Thumbnail", exact=True).first
        assert item2.is_visible(), "expected 'Set as Folder Thumbnail' in the File-view model menu"
        page.keyboard.press("Escape")
        print("file-view model context menu: PASSED")

        browser.close()
    print("ALL PINNED-FOLDER-THUMBNAIL TESTS PASSED")


if __name__ == "__main__":
    main()
```

Run: `cd frontend && bun run dev` (separate terminal), then `python components/pinnedFolderThumbnails.integration_test.py`
Expected: `ALL PINNED-FOLDER-THUMBNAIL TESTS PASSED`.

- [ ] **Step 10: Commit**

```bash
git add frontend/services/api.ts frontend/types.ts frontend/App.tsx frontend/components/ModelList.tsx frontend/components/pinnedFolderThumbnails.integration_test.py
git commit -m "feat: add pinned folder thumbnails for Logical and File-view folders"
```

---

## Final Verification

1. `cd backend && python -m pytest -q` — all pass (aside from the known pre-existing sidecar-notes failure).
2. `cd frontend && bun run build` — succeeds.
3. Re-run all three new Playwright scripts from this plan against the final code.
4. Manual round-trip check (the one thing automated tests in this plan don't fully cover): pin a model's thumbnail on a folder in each view, confirm the folder card updates; clear it, confirm it reverts to the auto-picked thumbnail; confirm File view's new folder cards navigate correctly and Logical view's folder cards (and their existing pin-unaware behavior) are unaffected by anything this plan didn't intend to change.
