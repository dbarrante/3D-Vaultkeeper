# Bulk Move & Move-to-New-Folder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bulk-selection "Move" action view-aware (File view shows real on-disk folders, not logical ones) and add a new "Move to New Folder" bulk action that creates a folder and moves the selection into it — both driven by one new shared, tree-based `FolderPicker` component reused across three call sites.

**Architecture:** Extract Sidebar's existing tree-building logic into a shared `useFolderTree` hook; build `FolderPicker` (a dialog wrapping MUI's `RichTreeView`, the same tree component Sidebar already uses) on top of it; add one new backend endpoint for File-view bulk move (mirroring the existing single-file move's validate→`shutil.move`→DB-update→rollback logic); wire three call sites (bulk Move, bulk Move-to-New-Folder, single-folder Move) to the new picker. "Move to New Folder" is frontend orchestration — create the folder via the existing create-folder endpoints, then call bulk-move targeting it — no new combined backend endpoint.

**Tech Stack:** FastAPI (backend), React/TypeScript + MUI `RichTreeView` (frontend), same Tailwind custom-modal styling this codebase already uses for other dialogs.

## Global Constraints

- Design doc: `docs/superpowers/specs/2026-08-06-bulk-move-and-new-folder-design.md`.
- Both view modes; scoped to the existing bulk-selection mechanism (`selectedIds` in `App.tsx`) only — no changes to drag-and-drop, Upload-to-folder modal, or Import Wizard.
- Partial bulk-move failures are best-effort: one bad file must not block the rest of the batch. The file-view bulk-move endpoint catches failures per-id and continues.
- `targetPath: string | null` (`null` = library root / `UPLOAD_DIR`) is the convention for every file-view folder-destination field in this feature, matching `FolderCreateRequest.parentPath`'s existing `null`-means-root convention. Never represent root as an empty string or a literal path string.
- `shutil.move`/`subprocess` calls must never use `shell=True` or string concatenation — list-argv or direct `shutil` calls only (existing project-wide convention).
- No new toast/notification system: error and partial-failure messages use `alert(...)`, matching every existing File-view error path in this codebase (e.g. `handleRevealFile`, Sidebar's rename/move/delete handlers).

---

### Task 1: Backend — File-view bulk-move endpoint

**Files:**
- Modify: `backend/app/routers/file_view.py`
- Test: `backend/tests/test_file_view_bulk_move.py`

**Interfaces:**
- Produces: `POST /api/file-view/models/bulk-move`, body `{"ids": string[], "targetPath": string | null}`, returns `{"moved": [{"id": string, "filePath": string}], "failed": [{"id": string, "reason": string}]}`. Task 4's frontend client calls this exact route and shape.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_file_view_bulk_move.py
import sqlite3
from pathlib import Path

from app.services.ingestion import ingest_file


def _make_model(tmp_path, name, subpath, storageMode_kwargs=None):
    source = tmp_path / f"{name}_source.stl"
    source.write_bytes(b"solid endsolid")
    kwargs = {"move": True, "dest_subpath": subpath}
    if storageMode_kwargs:
        kwargs.update(storageMode_kwargs)
    return ingest_file(str(source), folder_id="1", original_filename=f"{name}.stl", **kwargs)


def test_bulk_move_moves_multiple_files_successfully(client, tmp_path):
    a = _make_model(tmp_path, "a", "Source")
    b = _make_model(tmp_path, "b", "Source")

    target = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Dest"}).json()["path"]

    response = client.post(
        "/api/file-view/models/bulk-move",
        json={"ids": [a["id"], b["id"]], "targetPath": target},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == []
    moved_ids = {m["id"] for m in body["moved"]}
    assert moved_ids == {a["id"], b["id"]}

    for model, original in [(a, a["filePath"]), (b, b["filePath"])]:
        assert not Path(original).exists()
        moved_entry = next(m for m in body["moved"] if m["id"] == model["id"])
        assert Path(moved_entry["filePath"]).exists()
        assert Path(moved_entry["filePath"]).parent == Path(target)


def test_bulk_move_null_target_path_moves_to_library_root(client, tmp_path):
    from app.db import UPLOAD_DIR

    a = _make_model(tmp_path, "a", "Source")

    response = client.post(
        "/api/file-view/models/bulk-move",
        json={"ids": [a["id"]], "targetPath": None},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == []
    moved_entry = body["moved"][0]
    assert Path(moved_entry["filePath"]).parent == Path(UPLOAD_DIR).resolve()


def test_bulk_move_partial_failure_continues_other_files(client, tmp_path):
    a = _make_model(tmp_path, "a", "Source")
    b = _make_model(tmp_path, "b", "Source")

    target = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Dest"}).json()["path"]

    # Pre-create a collision at the destination for "a" only.
    (Path(target) / "a.stl").write_bytes(b"already here")

    response = client.post(
        "/api/file-view/models/bulk-move",
        json={"ids": [a["id"], b["id"]], "targetPath": target},
    )
    assert response.status_code == 200
    body = response.json()

    assert len(body["failed"]) == 1
    assert body["failed"][0]["id"] == a["id"]
    assert "already exists" in body["failed"][0]["reason"]

    assert len(body["moved"]) == 1
    assert body["moved"][0]["id"] == b["id"]
    assert Path(body["moved"][0]["filePath"]).exists()
    # "a" was left untouched at its original location since it was never moved
    assert Path(a["filePath"]).exists()


def test_bulk_move_rejects_relative_target_path(client, tmp_path):
    a = _make_model(tmp_path, "a", "Source")
    response = client.post(
        "/api/file-view/models/bulk-move",
        json={"ids": [a["id"]], "targetPath": "relative/path"},
    )
    assert response.status_code == 400


def test_bulk_move_404s_on_missing_target_folder(client, tmp_path):
    a = _make_model(tmp_path, "a", "Source")
    missing = str(tmp_path / "does-not-exist")
    response = client.post(
        "/api/file-view/models/bulk-move",
        json={"ids": [a["id"]], "targetPath": missing},
    )
    assert response.status_code == 404


def test_bulk_move_rolls_back_file_on_db_failure(client, tmp_path, monkeypatch):
    a = _make_model(tmp_path, "a", "Source")
    original_path = a["filePath"]

    target = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Dest"}).json()["path"]

    original_execute = sqlite3.Cursor.execute

    def failing_execute(self, sql, params=()):
        if sql.startswith("UPDATE models SET filePath"):
            raise sqlite3.OperationalError("simulated failure")
        return original_execute(self, sql, params)

    monkeypatch.setattr(sqlite3.Cursor, "execute", failing_execute)

    response = client.post(
        "/api/file-view/models/bulk-move",
        json={"ids": [a["id"]], "targetPath": target},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["moved"] == []
    assert body["failed"][0]["id"] == a["id"]

    monkeypatch.undo()
    # File was moved back to its original location by the rollback.
    assert Path(original_path).exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_file_view_bulk_move.py -v`
Expected: FAIL — `404 Not Found` for every request (the route doesn't exist yet).

- [ ] **Step 3: Add logging import and the endpoint**

In `backend/app/routers/file_view.py`, add near the top with the other imports (the file already imports `os`, `shutil`, `subprocess`, `sys`, `Path`, `Optional`, `APIRouter`, `HTTPException`, `BaseModel`):

```python
import logging
```

Add a module-level logger right after `router = APIRouter(prefix="/api/file-view", tags=["file-view"])`:

```python
logger = logging.getLogger(__name__)
```

Append the request model and endpoint at the end of the file (after `reveal_in_explorer`):

```python
class BulkMoveRequest(BaseModel):
    ids: list[str]
    targetPath: Optional[str] = None


@router.post("/models/bulk-move")
def bulk_move_models(body: BulkMoveRequest):
    # None means the library root, mirroring FolderCreateRequest.parentPath's
    # existing convention -- the frontend has no way to name UPLOAD_DIR's real
    # path as a string, so null stands in for it end-to-end.
    target_path_str = body.targetPath if body.targetPath else str(UPLOAD_DIR)
    try:
        ensure_unambiguous_path(target_path_str)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    target_dir = Path(target_path_str)
    if not target_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Target folder not found: {target_path_str}")

    moved = []
    failed = []
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        for mid in body.ids:
            row = cur.execute(
                "SELECT filePath, sourcePath, storageMode, removedAt FROM models WHERE id=?", (mid,)
            ).fetchone()
            if not row or row["removedAt"] is not None:
                failed.append({"id": mid, "reason": "Model not found"})
                continue

            model_storage_mode = row["storageMode"] if row["storageMode"] else "copy"
            current_path = row["sourcePath"] if model_storage_mode == "reference" else row["filePath"]
            if not current_path or not os.path.exists(current_path):
                failed.append({"id": mid, "reason": "File not found on disk"})
                continue

            destination = target_dir / Path(current_path).name
            try:
                validate_destination(str(destination), model_storage_mode)
            except ValueError as exc:
                failed.append({"id": mid, "reason": str(exc)})
                continue

            if destination.exists():
                failed.append({"id": mid, "reason": f"A file already exists at {destination}"})
                continue

            persisted_path = os.path.normpath(str(destination))
            shutil.move(current_path, persisted_path)

            try:
                if model_storage_mode == "reference":
                    cur.execute(
                        "UPDATE models SET filePath=?, sourcePath=? WHERE id=?",
                        (persisted_path, persisted_path, mid),
                    )
                else:
                    cur.execute("UPDATE models SET filePath=? WHERE id=?", (persisted_path, mid))
                conn.commit()
                moved.append({"id": mid, "filePath": persisted_path})
            except Exception:
                # Rollback: move the file back if the DB write fails, mirroring
                # update_model_location's identical atomicity guarantee.
                try:
                    shutil.move(persisted_path, current_path)
                except Exception:
                    logger.exception(
                        "Rollback move failed for model %s: file stranded at %s, "
                        "database still points at %s",
                        mid,
                        persisted_path,
                        current_path,
                    )
                failed.append({"id": mid, "reason": "Database update failed"})
    finally:
        conn.close()

    return {"moved": moved, "failed": failed}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_file_view_bulk_move.py -v`
Expected: PASS (6/6).

- [ ] **Step 5: Run the full backend suite to confirm no regressions**

Run: `cd backend && python -m pytest -q`
Expected: same pass count as before this change, plus these 6 new passes. (The pre-existing, unrelated `test_find_sidecar_notes_reads_sibling_pdf_file` failure is a known issue in this repo — do not attempt to fix it here.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/file_view.py backend/tests/test_file_view_bulk_move.py
git commit -m "feat: add bulk-move endpoint for File view (best-effort, per-file rollback)"
```

---

### Task 2: Frontend — extract shared `useFolderTree` hook

**Files:**
- Create: `frontend/hooks/useFolderTree.ts`
- Modify: `frontend/components/Sidebar.tsx`

**Interfaces:**
- Produces: `useFolderTree(viewMode, folders, models, trackedFolderPaths): { items: TreeViewDefaultItemModelProperties[], realPaths?: Map<string, string> }`. Task 3's `FolderPicker` and Sidebar both consume this exact signature and return shape.

This task is a pure extraction — the tree-construction logic is lifted verbatim from Sidebar.tsx's existing `treefolders()` function and `fileTree` `useMemo`, not redesigned. There is no new behavior to test beyond "Sidebar still renders the same trees it did before" — verified by the frontend build and a manual smoke check (Sidebar's own automated coverage is out of scope for this refactor task; its existing behavior is unchanged, not newly specified).

- [ ] **Step 1: Create the hook**

```ts
// frontend/hooks/useFolderTree.ts
import { useMemo } from "react";
import { TreeViewDefaultItemModelProperties } from "@mui/x-tree-view/models";
import {
  Folder,
  STLModel,
  FILE_VIEW_UPLOADS_BUCKET_ID,
  fileViewSegments,
  fileViewFolderSegments,
} from "../types";

export interface FolderTree {
  items: TreeViewDefaultItemModelProperties[];
  realPaths?: Map<string, string>;
}

// Builds the folder tree RichTreeView renders, in either view mode. Lifted
// verbatim from Sidebar.tsx's former treefolders()/fileTree so the sidebar
// and the FolderPicker dialog read from one source of truth instead of two
// trees that could silently drift apart.
export function useFolderTree(
  viewMode: "logical" | "file",
  folders: Folder[],
  models: STLModel[],
  trackedFolderPaths: string[],
): FolderTree {
  return useMemo(() => {
    if (viewMode === "logical") {
      const rootFolders = folders.filter((f) => f.parentId === null);
      const treeitems: TreeViewDefaultItemModelProperties[] = [];
      rootFolders.map((folder) => {
        treeitems.push({
          id: folder.id,
          label: folder.name,
          children: [],
        });
      });
      treeitems.map((folder) => {
        folders.map((subfolder) => {
          if (subfolder.parentId === folder.id) {
            folder.children.push({ id: subfolder.id, label: subfolder.name });
          }
        });
        folder.children.sort((a, b) => {
          return a.label.localeCompare(b.label);
        });
      });
      treeitems.sort((a, b) => {
        return a.label.localeCompare(b.label);
      });
      return { items: treeitems };
    }

    // File view: group every model by its filePath's directory instead of
    // folderId. Models with no filePath, or whose filePath sits directly in
    // the flat pre-feature upload location, land in a single synthetic
    // "Uploads" bucket rather than fabricating structure that was never there.
    type FileNode = { id: string; label: string; children: FileNode[]; childMap: Record<string, FileNode> };
    const root: FileNode = { id: "__root__", label: "", children: [], childMap: {} };
    const realPaths = new Map<string, string>();

    models.forEach((m) => {
      if (!m.filePath) return;
      const meaningfulSegments = fileViewSegments(m.filePath);

      if (meaningfulSegments.length === 0) {
        if (!root.childMap[FILE_VIEW_UPLOADS_BUCKET_ID]) {
          const node: FileNode = { id: FILE_VIEW_UPLOADS_BUCKET_ID, label: "Uploads", children: [], childMap: {} };
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
          const node: FileNode = { id: idPath, label: segment, children: [], childMap: {} };
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
  }, [viewMode, folders, models, trackedFolderPaths]);
}
```

- [ ] **Step 2: Replace Sidebar's inline tree logic with the hook**

In `frontend/components/Sidebar.tsx`:

1. Add the import near the other local imports (after the `import { api } from "../services/api";` line):

```ts
import { useFolderTree } from "../hooks/useFolderTree";
```

2. Find this block (currently around lines 269-393 — the `rootFolders` line, the `treefolders` function, and the `fileTree` `useMemo`):

```ts
  // Root folders
  const rootFolders = folders.filter((f) => f.parentId === null);

  //builds the treeview structure
  const treefolders = () => {
```

... through the end of the `fileTree` `useMemo`'s closing:

```ts
    return { items: root.children.map(strip), realPaths };
  }, [models, trackedFolderPaths]);
```

Delete that entire block (both `treefolders` and `fileTree`, plus the `rootFolders` line — `rootFolders` is used nowhere else in this file) and replace it with a single line:

```ts
  const folderTree = useFolderTree(viewMode, folders, models, trackedFolderPaths);
```

3. Update every remaining reference in the same file:
   - `items={treefolders()}` (in the `viewMode === "logical"` `RichTreeView`, around line 816) → `items={folderTree.items}`
   - `items={fileTree.items}` (in the `viewMode === "file"` `RichTreeView`, around line 826) → `items={folderTree.items}`
   - `fileTree.realPaths.get(nodeId)` (around line 409, inside the folder-context-menu handler) → `folderTree.realPaths?.get(nodeId)`
   - `fileTree.realPaths.get(nodeId)` (around line 497, inside the file-tree drop handler) → `folderTree.realPaths?.get(nodeId)`
   - `[fileTree, models]` (a `useCallback` dependency array around line 585) → `[folderTree, models]`
   - The two comment lines directly above that dependency array referencing `fileTree` by name → update the name to `folderTree` for accuracy (comment text only, no behavior change).

- [ ] **Step 3: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds with no new TypeScript errors.

- [ ] **Step 4: Manual smoke check**

Run: `cd frontend && bun run dev` (separate terminal) with the backend also running, open the app, and confirm both Sidebar trees still render exactly as before — switch between Logical and File view, expand/collapse nodes, confirm folder rename (Logical) and folder rename/move/delete/reveal (File view) context-menu actions still work. This is a pure refactor; anything different here is a regression.

- [ ] **Step 5: Commit**

```bash
git add frontend/hooks/useFolderTree.ts frontend/components/Sidebar.tsx
git commit -m "refactor: extract Sidebar's folder-tree builders into a shared useFolderTree hook"
```

---

### Task 3: Frontend — `FolderPicker` component

**Files:**
- Create: `frontend/components/FolderPicker.tsx`

**Interfaces:**
- Consumes: `useFolderTree` from Task 2 (`frontend/hooks/useFolderTree.ts`).
- Produces: `<FolderPicker open viewMode folders models trackedFolderPaths title allowRoot onSelect onClose />`, where `onSelect: (target: { mode: "logical"; folderId: string | null } | { mode: "file"; realPath: string | null }) => void`. Tasks 4, 5, and 6 all render this component with this exact prop shape. `allowRoot` must be `false` for a Logical-view move destination (see Step 1's comment) and `true` for the new-folder parent picker in either mode.

This task has no isolated unit-test framework in this repo (no Jest/RTL/Vitest configured — `frontend/package.json` has no `test` script). Verification is the type-check plus Task 7's Playwright coverage, matching how every other frontend component in this codebase is verified.

- [ ] **Step 1: Create the component**

```tsx
// frontend/components/FolderPicker.tsx
import React from "react";
import { X, FolderInput } from "lucide-react";
import { RichTreeView } from "@mui/x-tree-view/RichTreeView";
import { Folder, STLModel, FILE_VIEW_UPLOADS_BUCKET_ID } from "../types";
import { useFolderTree } from "../hooks/useFolderTree";

export type FolderPickerTarget =
  | { mode: "logical"; folderId: string | null }
  | { mode: "file"; realPath: string | null };

interface FolderPickerProps {
  open: boolean;
  viewMode: "logical" | "file";
  folders: Folder[];
  models: STLModel[];
  trackedFolderPaths: string[];
  title: string;
  // Whether a root/library-root option is offered at all. Must be false for
  // Logical-view Move: models.folderId is a NOT NULL database column, so
  // there is no "no folder" a model can be moved to in that mode -- offering
  // one there would send folderId: null and hit a constraint violation.
  // Root IS valid for a File-view move (a file can sit flat in the library
  // root -- that's what the existing "Uploads" bucket already represents)
  // and for picking a parent when creating a new folder in either mode
  // (folders.parentId and file-view's parentPath are both genuinely
  // nullable there).
  allowRoot: boolean;
  onSelect: (target: FolderPickerTarget) => void;
  onClose: () => void;
}

// Synthetic id for the root/library-root node this component injects ahead
// of the real tree. Prefixed distinctly from every id useFolderTree can ever
// produce (real folder UUIDs, "file/..." path ids, or FILE_VIEW_UPLOADS_BUCKET_ID)
// so it can never collide with a real node.
const FOLDER_PICKER_ROOT_ID = "__folder_picker_root__";

export default function FolderPicker({
  open,
  viewMode,
  folders,
  models,
  trackedFolderPaths,
  title,
  allowRoot,
  onSelect,
  onClose,
}: FolderPickerProps) {
  const folderTree = useFolderTree(viewMode, folders, models, trackedFolderPaths);

  if (!open) return null;

  const rootLabel = viewMode === "logical" ? "Root" : "Library Root";
  const items = allowRoot
    ? [{ id: FOLDER_PICKER_ROOT_ID, label: rootLabel, children: [] }, ...folderTree.items]
    : folderTree.items;

  const handleSelectedItemsChange = (_event: React.SyntheticEvent, nodeId: string | null) => {
    if (!nodeId) return;

    if (nodeId === FOLDER_PICKER_ROOT_ID) {
      // Unreachable when allowRoot is false since the node is never rendered
      // into items above, but guard explicitly rather than relying on that.
      if (!allowRoot) return;
      onSelect(
        viewMode === "logical" ? { mode: "logical", folderId: null } : { mode: "file", realPath: null },
      );
      return;
    }

    if (viewMode === "logical") {
      onSelect({ mode: "logical", folderId: nodeId });
      return;
    }

    // The synthetic Uploads bucket has no single real subdirectory (it
    // represents flat pre-feature storage) -- isItemDisabled below keeps it
    // unselectable, so realPaths.get should always resolve here, but the
    // fallback keeps this handler safe if that ever changes.
    const realPath = folderTree.realPaths?.get(nodeId) ?? null;
    if (realPath) onSelect({ mode: "file", realPath });
  };

  return (
    <div className="fixed left-0 top-0 z-50 w-full h-full bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-vault-800 border border-vault-600 rounded-xl p-6 w-80 shadow-2xl animate-in zoom-in-95 duration-200 overflow-y-auto max-h-[80vh]">
        <div className="flex justify-between items-center mb-4">
          <h3 className="font-bold text-white flex items-center gap-2">
            <FolderInput className="w-4 h-4" /> {title}
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="max-h-64 overflow-y-auto mb-2">
          <RichTreeView
            items={items}
            onSelectedItemsChange={handleSelectedItemsChange}
            isItemDisabled={(item) => item.id === FILE_VIEW_UPLOADS_BUCKET_ID}
          />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds with no new TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/FolderPicker.tsx
git commit -m "feat: add FolderPicker, a view-aware tree dialog for choosing a destination folder"
```

---

### Task 4: Frontend — wire bulk "Move" to `FolderPicker` in both view modes

**Files:**
- Modify: `frontend/services/api.ts`
- Modify: `frontend/App.tsx`

**Interfaces:**
- Produces: `api.bulkMoveFileViewModels(ids: string[], targetPath: string | null): Promise<{moved: {id: string, filePath: string}[], failed: {id: string, reason: string}[]}>`. Task 5 reuses this same function.
- Consumes: `FolderPicker` from Task 3, `api.bulkMoveModels` (existing, unchanged).

- [ ] **Step 1: Widen `bulkMoveModels` and add the new API client function**

`FolderPicker`'s Root option can now legitimately produce `folderId: null` for Logical view — `bulkMoveModels`'s existing signature only accepted `folderId: string`. Widen it (the backend endpoint it calls already accepts and handles `folderId: null` unchanged — this is a type-only fix, no behavior change):

In `frontend/services/api.ts`, find:

```ts
  bulkMoveModels: async (ids: string[], folderId: string): Promise<void> => {
```

Replace with:

```ts
  bulkMoveModels: async (ids: string[], folderId: string | null): Promise<void> => {
```

Then add directly after it (in the same "11. BULK MOVE" section):

```ts
  bulkMoveFileViewModels: async (
    ids: string[],
    targetPath: string | null,
  ): Promise<{ moved: { id: string; filePath: string }[]; failed: { id: string; reason: string }[] }> => {
    const res = await fetch(`${getApiBaseUrl()}/file-view/models/bulk-move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, targetPath }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Bulk move failed");
    }
    return res.json();
  },
```

- [ ] **Step 2: Import `FolderPicker` in App.tsx**

Add near App.tsx's other component imports:

```ts
import FolderPicker, { FolderPickerTarget } from "./components/FolderPicker";
```

- [ ] **Step 3: Replace the bulk-move submit handler**

Find `handleBulkMoveSubmit` in `frontend/App.tsx` (currently around lines 828-842):

```ts
  const handleBulkMoveSubmit = async (targetFolderId: string) => {
    try {
      const ids = Array.from(selectedIds) as string[];
      await api.bulkMoveModels(ids, targetFolderId);
      setModels((prev) =>
        prev.map((m) =>
          selectedIds.has(m.id) ? { ...m, folderId: targetFolderId } : m,
        ),
      );
      setShowMoveModal(false);
      setSelectedIds(new Set());
    } catch (e) {
      console.error("Bulk move failed", e);
    }
  };
```

Replace it with a view-aware version. Logical view keeps the exact same optimistic `setModels` patch it already does; File view calls the new endpoint and refetches afterward (`fetchData`), matching how every other File-view mutation in this app already refreshes state (Sidebar's `onFileViewMutated={fetchData}` wiring) rather than hand-patching `filePath` locally:

```ts
  const handleBulkMoveSelect = async (target: FolderPickerTarget) => {
    const ids = Array.from(selectedIds) as string[];
    try {
      if (target.mode === "logical") {
        await api.bulkMoveModels(ids, target.folderId);
        setModels((prev) =>
          prev.map((m) =>
            selectedIds.has(m.id) ? { ...m, folderId: target.folderId as string } : m,
          ),
        );
      } else {
        const result = await api.bulkMoveFileViewModels(ids, target.realPath);
        if (result.failed.length > 0) {
          alert(
            `Moved ${result.moved.length} of ${ids.length} files. ${result.failed.length} failed:\n` +
              result.failed.map((f) => f.reason).join("\n"),
          );
        }
        await fetchData();
      }
      setShowMoveModal(false);
      setSelectedIds(new Set());
    } catch (e) {
      console.error("Bulk move failed", e);
      alert(e instanceof Error ? e.message : "Bulk move failed");
    }
  };
```

(`target.folderId as string` is safe here: the existing Logical bulk-move endpoint already accepts `folderId: string | null` — `bulkMoveModels`'s current TypeScript signature declares `folderId: string`, so this cast matches that existing, unchanged signature. `null` selecting Logical's Root is a real case users can reach via `FolderPicker`; if it surfaces a rare mismatch it will show as the existing "Bulk move failed" catch path, not silently break, since `api.bulkMoveModels` still ships `folderId` straight through to the unmodified backend endpoint, which already treats `null` as root.)

Remove the old `handleBulkMoveSubmit` function entirely (replaced above).

- [ ] **Step 4: Replace the Move modal with `FolderPicker`**

Find the `showMoveModal` block in `frontend/App.tsx` (currently around lines 1643-1692):

```tsx
              {showMoveModal && (
                <div
                  className={`fixed left-0 top-0 z-50 bg-black/60 backdrop-blur-sm flex justify-center p-4 ${
                    visualViewport.keyboardOpen ? "items-start" : "items-center"
                  }`}
                  style={{
                    width: "100%",
                    height:
                      visualViewport.height ||
                      (typeof window !== "undefined" ? window.innerHeight : 0),
                    transform: `translate(${visualViewport.offsetLeft}px, ${visualViewport.offsetTop}px)`,
                  }}
                >
                  <div
                    className="bg-vault-800 border border-vault-600 rounded-xl p-6 w-80 shadow-2xl animate-in zoom-in-95 duration-200 overflow-y-auto"
                    style={{
                      maxHeight: Math.max(
                        200,
                        (visualViewport.height ||
                          (typeof window !== "undefined"
                            ? window.innerHeight
                            : 0)) - 32,
                      ),
                    }}
                  >
                    <div className="flex justify-between items-center mb-4">
                      <h3 className="font-bold text-white flex items-center gap-2">
                        <FolderInput className="w-4 h-4" /> Move to Folder
                      </h3>
                      <button
                        onClick={() => setShowMoveModal(false)}
                        className="text-slate-400 hover:text-white"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                    <div className="space-y-2 max-h-64 overflow-y-auto mb-4">
                      {folders.map((folder) => (
                        <button
                          key={folder.id}
                          onClick={() => handleBulkMoveSubmit(folder.id)}
                          className="w-full text-left px-3 py-2 rounded hover:bg-vault-700 text-slate-300 hover:text-white text-sm transition-colors"
                        >
                          {folder.name}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}
```

Replace it with:

```tsx
              <FolderPicker
                open={showMoveModal}
                viewMode={viewMode}
                folders={folders}
                models={models}
                trackedFolderPaths={trackedFolderPaths}
                title="Move to Folder"
                allowRoot={viewMode === "file"}
                onSelect={handleBulkMoveSelect}
                onClose={() => setShowMoveModal(false)}
              />
```

(`allowRoot={viewMode === "file"}` — never offer root as a Logical-view move destination; `models.folderId` is `NOT NULL`, so there is no "no folder" a model can be moved into in that mode. A File-view move to the library root is a real, valid destination — a file can sit flat there, matching the existing "Uploads" bucket.)

(The old modal's `visualViewport`-aware keyboard-avoidance positioning is dropped here — `FolderPicker` uses simple centered positioning matching Task 3's component. This is an accepted, intentional simplification: the mobile on-screen-keyboard edge case doesn't apply to a folder-tree picker the same way it did to a modal that could contain a text input, and no other dialog in this codebase does this positioning dance either.)

- [ ] **Step 5: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds with no new TypeScript errors. If `FolderInput`/`X` imports become unused elsewhere in `App.tsx` after this change, leave them — they're still used by other modals in the same file (verify with a quick search for `FolderInput` and `X` before removing anything; do not remove an import that's still referenced elsewhere).

- [ ] **Step 6: Commit**

```bash
git add frontend/services/api.ts frontend/App.tsx
git commit -m "feat: make bulk Move view-aware via FolderPicker (real folders in File view)"
```

---

### Task 5: Frontend — "Move to New Folder" bulk action

**Files:**
- Modify: `frontend/App.tsx`

**Interfaces:**
- Consumes: `FolderPicker` (Task 3), `api.bulkMoveFileViewModels` (Task 4), `api.createFolder`/`api.createFileViewFolder` (existing, unchanged), `handleBulkMoveSelect`'s error/refresh conventions (Task 4).

- [ ] **Step 1: Add state for the two-step flow**

Near the existing `const [showMoveModal, setShowMoveModal] = useState(false);` (around line 76), add:

```ts
  const [newFolderMoveStep, setNewFolderMoveStep] = useState<null | "pick-parent" | "name">(null);
  const [newFolderMoveParent, setNewFolderMoveParent] = useState<FolderPickerTarget | null>(null);
  const [newFolderMoveName, setNewFolderMoveName] = useState("");
```

- [ ] **Step 2: Add the orchestration handler**

Add directly after `handleBulkMoveSelect` (from Task 4):

```ts
  const handleNewFolderParentSelect = (target: FolderPickerTarget) => {
    setNewFolderMoveParent(target);
    setNewFolderMoveStep("name");
  };

  const handleNewFolderMoveSubmit = async () => {
    const name = newFolderMoveName.trim();
    if (!name || !newFolderMoveParent) return;
    const ids = Array.from(selectedIds) as string[];

    try {
      if (newFolderMoveParent.mode === "logical") {
        const created = await api.createFolder(name, newFolderMoveParent.folderId);
        await api.bulkMoveModels(ids, created.id);
        setModels((prev) =>
          prev.map((m) => (selectedIds.has(m.id) ? { ...m, folderId: created.id } : m)),
        );
        setFolders((prev) => [...prev, created]);
      } else {
        const created = await api.createFileViewFolder(newFolderMoveParent.realPath, name);
        const result = await api.bulkMoveFileViewModels(ids, created.path);
        if (result.failed.length > 0) {
          alert(
            `Created the folder, but only moved ${result.moved.length} of ${ids.length} files. ` +
              `${result.failed.length} failed:\n` + result.failed.map((f) => f.reason).join("\n"),
          );
        }
        await fetchData();
      }
      setNewFolderMoveStep(null);
      setNewFolderMoveParent(null);
      setNewFolderMoveName("");
      setSelectedIds(new Set());
    } catch (e) {
      console.error("Move to new folder failed", e);
      alert(e instanceof Error ? e.message : "Move to new folder failed");
    }
  };
```

(Folder creation failing — e.g. a 409 name collision — throws before `bulkMove*` is ever called, per the design's "stop before any move is attempted" rule: there is nothing to reconcile since nothing has moved yet.)

- [ ] **Step 3: Add the second bulk-action-bar button**

Find the existing "Move" button in the floating action bar (currently around lines 1130-1139):

```tsx
                    <button
                      onClick={() => setShowMoveModal(true)}
                      className="p-2 rounded-full hover:bg-vault-700 text-slate-300 hover:text-blue-400 transition-colors flex items-center gap-2"
                      title="Move Selected"
                    >
                      <FolderInput className="w-4 h-4" />
                      <span className="text-sm font-medium hidden sm:inline">
                        Move
                      </span>
                    </button>
```

Add a second button directly after it:

```tsx
                    <button
                      onClick={() => setNewFolderMoveStep("pick-parent")}
                      className="p-2 rounded-full hover:bg-vault-700 text-slate-300 hover:text-green-400 transition-colors flex items-center gap-2"
                      title="Move to New Folder"
                    >
                      <FolderInput className="w-4 h-4" />
                      <span className="text-sm font-medium hidden sm:inline">
                        Move to New Folder
                      </span>
                    </button>
```

- [ ] **Step 4: Render the picker step and the name-entry step**

Directly after the `<FolderPicker ... />` element added in Task 4, add:

```tsx
              <FolderPicker
                open={newFolderMoveStep === "pick-parent"}
                viewMode={viewMode}
                folders={folders}
                models={models}
                trackedFolderPaths={trackedFolderPaths}
                title="Choose a location for the new folder"
                allowRoot
                onSelect={handleNewFolderParentSelect}
                onClose={() => setNewFolderMoveStep(null)}
              />

              {newFolderMoveStep === "name" && (
                <div className="fixed left-0 top-0 z-50 w-full h-full bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
                  <div className="bg-vault-800 border border-vault-600 rounded-xl p-6 w-80 shadow-2xl animate-in zoom-in-95 duration-200">
                    <div className="flex justify-between items-center mb-4">
                      <h3 className="font-bold text-white flex items-center gap-2">
                        <FolderInput className="w-4 h-4" /> Name the New Folder
                      </h3>
                      <button
                        onClick={() => setNewFolderMoveStep(null)}
                        className="text-slate-400 hover:text-white"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                    <input
                      type="text"
                      autoFocus
                      value={newFolderMoveName}
                      onChange={(e) => setNewFolderMoveName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleNewFolderMoveSubmit();
                      }}
                      placeholder="Folder name"
                      className="w-full px-3 py-2 rounded bg-vault-700 border border-vault-600 text-white placeholder-slate-500 mb-4 focus:outline-none focus:border-blue-500"
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={() => setNewFolderMoveStep(null)}
                        className="flex-1 py-2.5 rounded-lg bg-vault-700 hover:bg-vault-600 text-slate-200 font-medium transition-colors"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={handleNewFolderMoveSubmit}
                        disabled={!newFolderMoveName.trim()}
                        className="flex-1 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium transition-colors"
                      >
                        Create & Move
                      </button>
                    </div>
                  </div>
                </div>
              )}
```

(`allowRoot` on the parent picker above is unconditionally true in both view modes: it chooses a *parent for a new folder*, not a model's destination — `folders.parentId` and file-view's `parentPath` are both genuinely nullable, unlike `models.folderId`.)

- [ ] **Step 5: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds with no new TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/App.tsx
git commit -m "feat: add 'Move to New Folder' bulk action (view-aware parent picker + name + move)"
```

---

### Task 6: Frontend — upgrade single-folder File-view "Move" to `FolderPicker`

**Files:**
- Modify: `frontend/components/Sidebar.tsx`

**Interfaces:**
- Consumes: `FolderPicker` (Task 3), `api.moveFileViewFolder` (existing, unchanged).

- [ ] **Step 1: Add picker state**

Near Sidebar's other `useState` declarations (e.g. alongside `folderContextMenu`), add:

```ts
  const [moveFolderPickerSource, setMoveFolderPickerSource] = useState<string | null>(null);
```

`null` means the picker is closed; a non-null value is the real path of the folder currently being moved.

- [ ] **Step 2: Import `FolderPicker`**

Add near Sidebar's other local imports:

```ts
import FolderPicker, { FolderPickerTarget } from "./FolderPicker";
```

- [ ] **Step 3: Replace the `window.prompt` Move menu item**

Find the `key="move"` `MenuItem` in the folder context menu (currently around lines 877-894):

```tsx
          <MenuItem
            key="move"
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
            }}
          >
            Move
          </MenuItem>,
```

Replace it with:

```tsx
          <MenuItem
            key="move"
            onClick={() => {
              if (folderContextMenu?.realPath) {
                setMoveFolderPickerSource(folderContextMenu.realPath);
              }
              setFolderContextMenu(null);
            }}
          >
            Move
          </MenuItem>,
```

(`setFolderContextMenu(null)` is added here to close the right-click menu immediately when the picker opens — every sibling `MenuItem` in this same array already does this in its `onClick`, so this is matching the existing pattern, not introducing a new one.)

- [ ] **Step 4: Render the picker and its select handler**

Add near the end of Sidebar's JSX, alongside the existing `<Menu>` block (after its closing `</Menu>`):

```tsx
      <FolderPicker
        open={moveFolderPickerSource !== null}
        viewMode="file"
        folders={folders}
        models={models}
        trackedFolderPaths={trackedFolderPaths}
        title="Move Folder"
        allowRoot={false}
        onSelect={(target: FolderPickerTarget) => {
          if (target.mode === "file" && moveFolderPickerSource && target.realPath) {
            api
              .moveFileViewFolder(moveFolderPickerSource, target.realPath)
              .then(onFileViewMutated)
              .catch((err) => {
                console.error("Folder move failed:", err);
                alert(err instanceof Error ? err.message : "Folder move failed");
              });
          }
          setMoveFolderPickerSource(null);
        }}
        onClose={() => setMoveFolderPickerSource(null)}
      />
```

(`allowRoot={false}`: `moveFileViewFolder`'s existing signature takes a real destination string, not null, so there is no way to act on a "Library Root" selection here — rather than show a selectable option that silently does nothing, it's simply not offered. Moving a folder to the literal library root wasn't requested by this feature and is out of scope. The `target.realPath` truthiness check in `onSelect` above is defensive belt-and-suspenders, not the primary guard.)

- [ ] **Step 5: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds with no new TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/Sidebar.tsx
git commit -m "feat: replace File view's single-folder Move window.prompt with FolderPicker"
```

---

### Task 7: Playwright integration tests + Final Verification

**Files:**
- Test: `frontend/components/bulkMoveAndNewFolder.integration_test.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6. This task adds no new production code.

- [ ] **Step 1: Write the integration test**

```python
# frontend/components/bulkMoveAndNewFolder.integration_test.py
# Run with a dev server + backend already running. Usage:
#   cd frontend && bun run dev   (separate terminal)
#   python components/bulkMoveAndNewFolder.integration_test.py
import json
import urllib.request

from playwright.sync_api import sync_playwright

FRONTEND_URL = "http://localhost:5173"
BACKEND_URL = "http://127.0.0.1:8000"


def set_api_override(page):
    page.add_init_script(
        f"window.localStorage.setItem('api-port-override', '{BACKEND_URL}');"
    )


def upload_test_model(name="bulktest.stl", folder_id="1"):
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


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        set_api_override(page)

        model = upload_test_model("bulk_new_folder_test.stl")

        page.goto(FRONTEND_URL)
        page.wait_for_load_state("networkidle")

        # Logical view: select the uploaded model, open "Move to New Folder",
        # pick Root, name it, and confirm the model lands in the new folder.
        card = page.locator("[class*='card']", has_text="bulk_new_folder_test").first
        card.hover()
        checkbox = card.locator("input[type='checkbox']").first
        checkbox.click()

        page.get_by_title("Move to New Folder").click()
        page.get_by_text("Root", exact=True).first.click()
        name_input = page.get_by_placeholder("Folder name")
        name_input.fill("PlaywrightBulkTestFolder")
        page.get_by_text("Create & Move", exact=True).click()
        page.wait_for_timeout(1000)

        # Confirm the new folder now appears in the sidebar and the model is in it.
        assert page.get_by_text("PlaywrightBulkTestFolder", exact=True).first.is_visible(), (
            "expected the newly created folder to appear in the sidebar"
        )
        page.get_by_text("PlaywrightBulkTestFolder", exact=True).first.click()
        page.wait_for_timeout(500)
        assert page.get_by_text("bulk_new_folder_test", exact=False).first.is_visible(), (
            "expected the moved model to appear inside the new folder"
        )

        print("Logical view Move to New Folder: PASSED")
        browser.close()
    print("ALL BULK-MOVE-AND-NEW-FOLDER TESTS PASSED")


if __name__ == "__main__":
    main()
```

(This covers the Logical-view "Move to New Folder" path end-to-end against the real running app and backend — the most representative full-stack path, since Logical view requires no real-filesystem fixture setup beforehand. It does not also drive the File-view path or the plain bulk "Move" picker: those were verified via Task 1's backend suite plus the Task 2-6 manual smoke checks, and this codebase's established Playwright coverage is deliberately light-touch — see `revealInExplorer.integration_test.py` and its own note that OS-level effects aren't provable by headless Chromium. Attempting a fully automated File-view fixture here (real nested directories, tracked folders, a live Explorer-adjacent flow) would cost more than it proves; the manual Final Verification pass below covers it directly.)

- [ ] **Step 2: Run it**

Run: `cd frontend && bun run dev` (separate terminal), then `python components/bulkMoveAndNewFolder.integration_test.py`
Expected: `ALL BULK-MOVE-AND-NEW-FOLDER TESTS PASSED`. If a selector doesn't match what's actually rendered, inspect the live page (`page.screenshot()` or `page.content()`) and adjust — don't guess blindly.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/bulkMoveAndNewFolder.integration_test.py
git commit -m "test: add Playwright coverage for bulk Move to New Folder (Logical view)"
```

## Final Verification

1. `cd backend && python -m pytest -q` — all tests pass (aside from the known pre-existing, unrelated sidecar-notes failure).
2. `cd frontend && bun run build` — succeeds.
3. Re-run `python components/bulkMoveAndNewFolder.integration_test.py` against the final code.
4. Manual check in the actual running app (the parts automated tests can't fully cover):
   - **File view bulk Move**: select several files in File view, click "Move", confirm the picker shows real on-disk folders (not logical ones), pick one, confirm the files actually move on disk and the UI updates.
   - **File view Move to New Folder**: select several files in File view, click "Move to New Folder", pick a real parent location (including "Library Root"), name it, confirm the folder is created on disk and the files land inside it.
   - **Logical view**: repeat both bulk actions in Logical view, confirm folders shown are logical (not real paths) and nothing filesystem-related happens.
   - **Single-folder Move upgrade**: right-click a real folder in File view's sidebar tree, click "Move", confirm the picker (not a text prompt) appears and completes a real move.
   - **Uploads bucket**: confirm the synthetic "Uploads" node appears in File-view pickers but cannot be selected as a destination.
