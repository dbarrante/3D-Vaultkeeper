# File View Explorer-Style Navigation & Folder-Path Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make File view's main grid show only the selected folder's direct contents (files + subfolder tiles, with drag-and-drop-to-move) instead of every file in every nested subfolder recursively, and add a Settings toggle that shows each File-view card's containing folder (full absolute path).

**Architecture:** Fix `App.tsx`'s File-view model filter from an ancestor/descendant prefix match to an exact-depth match. Extend the shared `useFolderTree` hook with a `nodesById` lookup map so `App.tsx` can derive "direct child folders of the current selection" from the same tree data the sidebar already renders, then feed those into `ModelList`'s existing (currently File-view-disabled) folder-tile rendering — no new tile UI code needed, only new caller-side data. Add a localStorage-backed boolean setting and one new conditional card line.

**Tech Stack:** React (TS) frontend only — no backend changes; this reuses backend endpoints built in a prior plan (`POST /api/file-view/models/bulk-move`, `POST /api/file-view/folder/move`).

## Global Constraints

- Design doc: `docs/superpowers/specs/2026-08-07-file-view-explorer-nav-and-path-toggle-design.md`.
- Frontend-only. No backend endpoint changes.
- The synthetic Uploads-bucket tile (`FILE_VIEW_UPLOADS_BUCKET_ID`) must remain navigable (clicking it works exactly as it does via the sidebar today) but must **not** be a valid drag-and-drop destination — it has no single real path.
- The folder-path toggle applies to File view only. Logical view's cards, tiles, and filtering are unchanged.
- No new toast/notification system — reuse `alert(...)` for errors, matching every existing convention in this codebase.
- No new unit-test framework — this repo has none (confirmed repeatedly); verification is `bun run build` plus a Playwright `.integration_test.py` script, matching established convention.

---

### Task 1: `useFolderTree` — add a `nodesById` lookup map

**Files:**
- Modify: `frontend/hooks/useFolderTree.ts`

**Interfaces:**
- Produces: `FolderTree.nodesById: Map<string, TreeViewDefaultItemModelProperties>` — populated for both view modes, covering every node in the tree (not just leaves). Task 3 depends on this for File view; Sidebar/FolderPicker are unaffected (they don't read this new field).

This is a pure additive extension — no existing behavior changes. `items` and `realPaths`' existing content and shape are untouched.

- [ ] **Step 1: Add `nodesById` to the interface**

In `frontend/hooks/useFolderTree.ts`, find:

```ts
export interface FolderTree {
  items: TreeViewDefaultItemModelProperties[];
  realPaths?: Map<string, string>;
}
```

Replace with:

```ts
export interface FolderTree {
  items: TreeViewDefaultItemModelProperties[];
  realPaths?: Map<string, string>;
  nodesById: Map<string, TreeViewDefaultItemModelProperties>;
}
```

- [ ] **Step 2: Populate it in the logical branch**

Find:

```ts
      const buildNode = (folder: Folder, visited: Set<string>): TreeViewDefaultItemModelProperties => {
        const nextVisited = new Set(visited).add(folder.id);
        const children = (childrenByParentId.get(folder.id) ?? [])
          .filter((child) => !nextVisited.has(child.id))
          .map((child) => buildNode(child, nextVisited))
          .sort((a, b) => a.label.localeCompare(b.label));
        return { id: folder.id, label: folder.name, children };
      };

      const treeitems = folders
        .filter((f) => f.parentId === null)
        .map((folder) => buildNode(folder, new Set()))
        .sort((a, b) => a.label.localeCompare(b.label));
      return { items: treeitems };
```

Replace with:

```ts
      const nodesById = new Map<string, TreeViewDefaultItemModelProperties>();
      const buildNode = (folder: Folder, visited: Set<string>): TreeViewDefaultItemModelProperties => {
        const nextVisited = new Set(visited).add(folder.id);
        const children = (childrenByParentId.get(folder.id) ?? [])
          .filter((child) => !nextVisited.has(child.id))
          .map((child) => buildNode(child, nextVisited))
          .sort((a, b) => a.label.localeCompare(b.label));
        const node = { id: folder.id, label: folder.name, children };
        nodesById.set(node.id, node);
        return node;
      };

      const treeitems = folders
        .filter((f) => f.parentId === null)
        .map((folder) => buildNode(folder, new Set()))
        .sort((a, b) => a.label.localeCompare(b.label));
      return { items: treeitems, nodesById };
```

- [ ] **Step 3: Populate it in the file-view branch**

Find:

```ts
    const strip = (node: FileNode): TreeViewDefaultItemModelProperties => ({
      id: node.id,
      label: node.label,
      children: node.children.map(strip),
    });
    return { items: root.children.map(strip), realPaths };
```

Replace with:

```ts
    const nodesById = new Map<string, TreeViewDefaultItemModelProperties>();
    const strip = (node: FileNode): TreeViewDefaultItemModelProperties => {
      const stripped = {
        id: node.id,
        label: node.label,
        children: node.children.map(strip),
      };
      nodesById.set(stripped.id, stripped);
      return stripped;
    };
    return { items: root.children.map(strip), realPaths, nodesById };
```

- [ ] **Step 4: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds. Then run `bunx tsc --noEmit` and confirm the error set is the same as before this change (this repo has a handful of pre-existing, unrelated errors — confirmed in every prior plan this session — none in `useFolderTree.ts`).

- [ ] **Step 5: Commit**

```bash
git add frontend/hooks/useFolderTree.ts
git commit -m "feat: add nodesById lookup map to useFolderTree"
```

---

### Task 2: Fix File-view model filter to direct-children only

**Files:**
- Modify: `frontend/App.tsx`

**Interfaces:**
- No new interfaces — this is a pure logic fix inside the existing `filteredModels` `useMemo`.

- [ ] **Step 1: Change the prefix match to an exact-depth match**

In `frontend/App.tsx`, find (the File-view branch of `filteredModels`, currently around lines 330-354):

```ts
      // fileTree node ids are built in Sidebar.tsx as "file/<segment>/<segment>/..."
      // (see Task 9) -- strip that prefix to get the target path segments,
      // then require a model's own segments (via the shared fileViewSegments
      // helper, so this can never drift from how Sidebar built the tree) to
      // match positionally at the same indices. Positional comparison (not a
      // substring search) avoids conflating the same folder name appearing at
      // two different depths, e.g. uploads/Tanks vs uploads/Vehicles/Tanks.
      const targetSegments = currentFolderId.replace(/^file\//, "").split("/");
      return models.filter((m) => {
        if (!m.filePath) return false;
        const modelSegments = fileViewSegments(m.filePath);
        if (modelSegments.length < targetSegments.length) return false;
        return targetSegments.every((seg, i) => modelSegments[i] === seg);
      });
```

Replace with:

```ts
      // fileTree node ids are built in Sidebar.tsx as "file/<segment>/<segment>/..."
      // (see Task 9) -- strip that prefix to get the target path segments,
      // then require a model's own segments (via the shared fileViewSegments
      // helper, so this can never drift from how Sidebar built the tree) to
      // match positionally at the same indices AND have the exact same
      // length -- fileViewSegments already drops the filename, so a length
      // match means the file sits directly in this folder, not some
      // descendant subfolder. (Previously this only required the target
      // segments to be a *prefix* of the model's segments, which recursively
      // matched every file anywhere under the selected folder -- e.g.
      // selecting "Vehicles" also matched "Vehicles/Tanks/model.stl". Direct
      // children of deeper folders are still reachable by navigating into
      // them via their own folder tile/sidebar node, exactly like Windows
      // Explorer.)
      const targetSegments = currentFolderId.replace(/^file\//, "").split("/");
      return models.filter((m) => {
        if (!m.filePath) return false;
        const modelSegments = fileViewSegments(m.filePath);
        if (modelSegments.length !== targetSegments.length) return false;
        return targetSegments.every((seg, i) => modelSegments[i] === seg);
      });
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds, no new errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/App.tsx
git commit -m "fix: File view grid shows only a folder's direct-child files, not every descendant"
```

---

### Task 3: File-view folder tiles in the main grid

**Files:**
- Modify: `frontend/App.tsx`

**Interfaces:**
- Consumes: `useFolderTree`'s `nodesById`/`items` (Task 1).
- Produces: `App.tsx` now calls `useFolderTree` directly and derives `fileFolderTiles: Folder[]` and `fileFolderPreviews: Record<string, {count, thumbnail}>` for File view, wired into `ModelList`'s existing `folders`/`folderPreviews` props (no `ModelList.tsx` changes in this task — it already renders whatever it's given).

- [ ] **Step 1: Import `useFolderTree` and call it**

In `frontend/App.tsx`, add to the existing import block near the top:

```ts
import { useFolderTree } from "./hooks/useFolderTree";
```

Add a new call directly after `filteredFolders`'s `useMemo` (currently ending around line 363):

```ts
  const folderTree = useFolderTree(viewMode, folders, models, trackedFolderPaths);
```

- [ ] **Step 2: Derive File-view folder tiles**

Add directly after the `folderTree` line from Step 1:

```ts
  // File-view equivalent of filteredFolders: the current selection's direct
  // child folders, sourced from the SAME tree data the sidebar renders (via
  // useFolderTree's nodesById) rather than a second, independently-derived
  // computation -- so the grid's idea of "direct children" can never drift
  // from what the sidebar shows. At "all", this is the tree's top-level
  // items (which already includes the synthetic Uploads bucket), mirroring
  // how Logical view's filteredFolders shows parentId === null folders at
  // "all". These are lightweight Folder-shaped objects (no `description`,
  // so ModelList's existing folder.description-gated info-tooltip simply
  // doesn't render for them) -- ModelList needs no new tile-rendering code,
  // it already renders whatever `folders` array it's given.
  const fileFolderTiles = useMemo((): Folder[] => {
    if (viewMode !== "file") return [];
    const nodes =
      currentFolderId === "all"
        ? folderTree.items
        : folderTree.nodesById.get(currentFolderId)?.children ?? [];
    return nodes.map((node) => ({
      id: node.id,
      name: node.label,
      parentId: currentFolderId === "all" ? null : currentFolderId,
    }));
  }, [viewMode, currentFolderId, folderTree]);

  // File-view equivalent of folderPreviews (App.tsx's existing Logical-view
  // memo just below this one) -- same shape, same "earliest model that
  // actually has a thumbnail" selection rule, but grouped by each model's
  // own synthetic parent id (via the shared fileViewSegments helper) instead
  // of m.folderId, since File-view models don't have a meaningful folderId.
  const fileFolderPreviews = useMemo(() => {
    if (viewMode !== "file") return {};
    const previews: Record<string, { count: number; thumbnail: string | null }> = {};
    const earliestThumbnailDateAddedByFolder: Record<string, number> = {};
    models.forEach((m) => {
      if (!m.filePath) return;
      const segments = fileViewSegments(m.filePath);
      const parentId = segments.length === 0 ? FILE_VIEW_UPLOADS_BUCKET_ID : `file/${segments.join("/")}`;
      if (!previews[parentId]) {
        previews[parentId] = { count: 0, thumbnail: null };
      }
      previews[parentId].count += 1;
      if (
        m.thumbnail &&
        (earliestThumbnailDateAddedByFolder[parentId] === undefined ||
          m.dateAdded < earliestThumbnailDateAddedByFolder[parentId])
      ) {
        earliestThumbnailDateAddedByFolder[parentId] = m.dateAdded;
        previews[parentId].thumbnail = m.thumbnail;
      }
    });
    return previews;
  }, [viewMode, models]);
```

- [ ] **Step 3: Wire them into `ModelList`'s props**

Find (in the `ModelList` render, currently around lines 1095-1099):

```tsx
                <ModelList
                  models={filteredModels}
                  folders={viewMode === "file" ? [] : filteredFolders}
                  folderPreviews={folderPreviews}
```

Replace with:

```tsx
                <ModelList
                  models={filteredModels}
                  folders={viewMode === "file" ? fileFolderTiles : filteredFolders}
                  folderPreviews={viewMode === "file" ? fileFolderPreviews : folderPreviews}
```

- [ ] **Step 4: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds, no new errors.

- [ ] **Step 5: Manual smoke check**

Start the backend and `bun run dev`, open File view, navigate into a folder with both files and subfolders. Confirm: the grid shows only that folder's direct files, plus tiles for its direct subfolders; clicking a subfolder tile navigates into it (same as clicking it in the sidebar, and the sidebar's own selection highlight updates to match); "All Models" still shows every file flat, plus top-level folder tiles (including "Uploads" if present).

- [ ] **Step 6: Commit**

```bash
git add frontend/App.tsx
git commit -m "feat: render File-view folder tiles in the main grid, sourced from useFolderTree"
```

---

### Task 4: Drag-and-drop parity for File-view folder tiles

**Files:**
- Modify: `frontend/App.tsx`

**Interfaces:**
- Consumes: `api.bulkMoveFileViewModels` (existing, from the prior plan), `folderTree.realPaths` (Task 1/existing).

- [ ] **Step 1: Make `handleDropMove` view-aware**

Find (currently around lines 899-911):

```ts
  const handleDropMove = async (targetFolderId: string, modelIds: string[]) => {
    try {
      await api.bulkMoveModels(modelIds, targetFolderId);
      setModels((prev) =>
        prev.map((m) =>
          modelIds.includes(m.id) ? { ...m, folderId: targetFolderId } : m,
        ),
      );
      setSelectedIds(new Set());
    } catch (e) {
      console.error("Drop move failed", e);
    }
  };
```

Replace with:

```ts
  const handleDropMove = async (targetFolderId: string, modelIds: string[]) => {
    if (viewMode === "file") {
      // The Uploads-bucket tile has no entry in realPaths (it has no single
      // real path -- see useFolderTree) and is therefore not a valid drop
      // target; this silently no-ops rather than attempting a move with no
      // resolvable destination, matching how FolderPicker also disables it
      // as a selectable move destination for the identical reason.
      const realPath = folderTree.realPaths?.get(targetFolderId);
      if (!realPath) return;
      try {
        const result = await api.bulkMoveFileViewModels(modelIds, realPath);
        if (result.failed.length > 0) {
          alert(
            `Moved ${result.moved.length} of ${modelIds.length} files. ${result.failed.length} failed:\n` +
              result.failed.map((f) => f.reason).join("\n"),
          );
        }
        await fetchData();
        setSelectedIds(new Set());
      } catch (e) {
        console.error("Drop move failed", e);
        alert(e instanceof Error ? e.message : "Drop move failed");
      }
      return;
    }
    try {
      await api.bulkMoveModels(modelIds, targetFolderId);
      setModels((prev) =>
        prev.map((m) =>
          modelIds.includes(m.id) ? { ...m, folderId: targetFolderId } : m,
        ),
      );
      setSelectedIds(new Set());
    } catch (e) {
      console.error("Drop move failed", e);
    }
  };
```

- [ ] **Step 2: Prevent File-view tile drops from misusing `onUploadToFolder`**

`onUploadToFolder`'s existing wiring assumes its `folderId` argument is always a real Logical `Folder.id` — passing it a File-view synthetic tree id would silently attempt to upload into a nonexistent logical folder. This path was previously unreachable in File view (no tiles existed to drop files onto); Task 3 makes it reachable. Rather than build real "upload directly into a real File-view folder" support (out of scope for this plan — not requested), route File-view tile uploads to the existing upload-modal fallback, exactly like an unscoped drop onto the general grid area already does.

Find (currently around lines 1118-1120, inside the same `ModelList` render block Task 3 edited):

```tsx
                  onUploadToFolder={(folderId, files) =>
                    handleUpload(files, folderId)
                  }
```

Replace with:

```tsx
                  onUploadToFolder={(folderId, files) =>
                    handleUpload(files, viewMode === "file" ? undefined : folderId)
                  }
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds, no new errors.

- [ ] **Step 4: Manual verification**

Start the backend and `bun run dev`. In File view, select one or more files, drag them onto a subfolder tile in the main grid, confirm they move and land at the right real location on disk (check both the UI and the actual filesystem). Then confirm dragging files from outside the browser (a raw OS file drop) onto a File-view folder tile opens the existing upload modal (not a broken direct-upload attempt). Then confirm dragging a model onto the "Uploads" tile (if present) does nothing (no error, no move) rather than crashing.

- [ ] **Step 5: Commit**

```bash
git add frontend/App.tsx
git commit -m "feat: drag-and-drop-to-move onto File-view folder tiles"
```

---

### Task 5: Settings toggle — show folder path on card

**Files:**
- Modify: `frontend/services/api.ts`
- Modify: `frontend/components/Settings.tsx`
- Modify: `frontend/App.tsx`
- Modify: `frontend/components/ModelList.tsx`

**Interfaces:**
- Produces: `getShowFolderPathOnCard(): boolean` / `setShowFolderPathOnCard(value: boolean): void` in `api.ts`. `ModelList` gains a new required prop `showFolderPath: boolean`.

- [ ] **Step 1: Add the localStorage helpers**

In `frontend/services/api.ts`, add directly after the existing `setEnabledLaunchSlicers` function:

```ts
export const getShowFolderPathOnCard = (): boolean => {
  return localStorage.getItem("stlvault-show-folder-path") === "true";
};

export const setShowFolderPathOnCard = (value: boolean) => {
  localStorage.setItem("stlvault-show-folder-path", value ? "true" : "false");
};
```

- [ ] **Step 2: Add the Settings toggle UI**

In `frontend/components/Settings.tsx`, add `Eye` to the existing lucide-react import list:

```ts
import {
  Check,
  ChevronLeft,
  EthernetPort,
  Eye,
  Globe,
  KeyRound,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";
```

Add `getShowFolderPathOnCard`/`setShowFolderPathOnCard` to the existing `../services/api` import list.

Add new state near the other localStorage-initialized state (alongside `selectedSlicer`):

```ts
  const [showFolderPath, setShowFolderPath] = useState<boolean>(() =>
    getShowFolderPathOnCard(),
  );

  const handleShowFolderPathChange = (value: boolean) => {
    setShowFolderPath(value);
    setShowFolderPathOnCard(value);
  };
```

Add a new section directly after the closing `</div>` of the existing "Slicer Settings" section (after line 221's `</div>`, before the `{/* Import Sources */}` comment):

```tsx
        {/* Display Preferences */}
        <div className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <Eye className="w-5 h-5 text-blue-400" />
            <h3 className="text-lg font-semibold text-white">Display</h3>
          </div>
          <p className="text-sm text-slate-400 mb-4">
            Options that control how models are displayed in the library.
          </p>

          <button
            onClick={() => handleShowFolderPathChange(!showFolderPath)}
            className={`w-full p-4 rounded-lg border-2 transition-all text-left flex items-center justify-between ${
              showFolderPath
                ? "border-blue-500 bg-blue-500/10 shadow-lg shadow-blue-500/20"
                : "border-vault-700 bg-vault-800 hover:border-vault-600"
            }`}
          >
            <div>
              <span className="font-medium text-white">
                Show folder path on card
              </span>
              <span className="text-xs text-slate-500 mt-1 block">
                File view only -- shows each model's containing folder on its
                card.
              </span>
            </div>
            {showFolderPath && <Check className="w-5 h-5 text-blue-500" />}
          </button>
        </div>

```

- [ ] **Step 3: Have `App.tsx` own and resync the setting**

In `frontend/App.tsx`, add `getShowFolderPathOnCard` to the existing `import { api, resolveApiOrigin } from "./services/api";` line's named imports.

Add new state near the other top-level state declarations (alongside `showSettings`):

```ts
  const [showFolderPath, setShowFolderPath] = useState<boolean>(() =>
    getShowFolderPathOnCard(),
  );
```

Find the single `Settings` render call:

```tsx
          <Settings onBack={() => setShowSettings(false)} />
```

Replace with:

```tsx
          <Settings
            onBack={() => {
              setShowFolderPath(getShowFolderPathOnCard());
              setShowSettings(false);
            }}
          />
```

(`Settings.tsx` owns its own copy of this preference internally, matching this codebase's existing pattern where Settings reads/writes localStorage directly rather than receiving props from `App.tsx` — `App.tsx` re-reads the value only at the moment Settings closes, so the grid reflects a change without needing live prop-drilling into Settings.)

- [ ] **Step 4: Pass `showFolderPath` to `ModelList`**

In the same `ModelList` render block edited in Tasks 3-4, add the new prop (order doesn't matter; add it near `viewMode`):

```tsx
                  viewMode={viewMode}
                  showFolderPath={showFolderPath}
```

- [ ] **Step 5: Add the prop to `ModelList` and render the card line**

In `frontend/components/ModelList.tsx`, find the props interface:

```ts
  viewMode: "logical" | "file";
  onFileViewMutated: () => void;
}
```

Replace with:

```ts
  viewMode: "logical" | "file";
  onFileViewMutated: () => void;
  showFolderPath: boolean;
}
```

Find the destructured props:

```ts
  viewMode,
  onFileViewMutated,
}) => {
```

Replace with:

```ts
  viewMode,
  onFileViewMutated,
  showFolderPath,
}) => {
```

Find the card's size/date line (currently around lines 869-876):

```tsx
                        <Typography
                          variant="body2"
                          sx={{ color: "text.secondary" }}
                        >
                          {(model.size / (1024 * 1024)).toFixed(2)}
                          {" MB  • "}
                          {new Date(model.dateAdded).toLocaleDateString()}
                        </Typography>
```

Add directly after it, still inside `CardContent`:

```tsx
                        {viewMode === "file" && showFolderPath && model.filePath && (
                          <Typography
                            variant="caption"
                            noWrap={true}
                            sx={{ color: "text.secondary", display: "block" }}
                          >
                            {(() => {
                              const lastSep = Math.max(
                                model.filePath.lastIndexOf("/"),
                                model.filePath.lastIndexOf("\\"),
                              );
                              return lastSep >= 0
                                ? model.filePath.slice(0, lastSep)
                                : model.filePath;
                            })()}
                          </Typography>
                        )}
```

(This shows `model.filePath`'s directory exactly as stored, without normalizing separators — the full absolute path, matching your answer, in whatever separator style the backend already persisted.)

- [ ] **Step 6: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds, no new errors.

- [ ] **Step 7: Manual verification**

Open Settings, toggle "Show folder path on card" on, go back to File view, confirm cards now show their containing folder path. Toggle it off, confirm the line disappears. Confirm Logical-view cards are unaffected in both states.

- [ ] **Step 8: Commit**

```bash
git add frontend/services/api.ts frontend/components/Settings.tsx frontend/App.tsx frontend/components/ModelList.tsx
git commit -m "feat: add 'show folder path on card' Settings toggle (File view only)"
```

---

### Task 6: Playwright integration test + Final Verification

**Files:**
- Test: `frontend/components/fileViewNavigationAndPathToggle.integration_test.py`

**Interfaces:**
- Consumes: everything from Tasks 1-5. This task adds no new production code.

- [ ] **Step 1: Write the integration test**

```python
# frontend/components/fileViewNavigationAndPathToggle.integration_test.py
# Run with a dev server + backend already running. Usage:
#   cd frontend && bun run dev   (separate terminal)
#   python components/fileViewNavigationAndPathToggle.integration_test.py
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


def main():
    run_id = uuid.uuid4().hex[:8]
    parent_name = f"NavTestParent_{run_id}"
    child_name = f"NavTestChild_{run_id}"
    direct_model_name = f"direct_{run_id}.stl"
    nested_model_name = f"nested_{run_id}.stl"

    parent = create_file_view_folder(None, parent_name)
    child = create_file_view_folder(parent["path"], child_name)

    direct_model = upload_test_model(direct_model_name)
    nested_model = upload_test_model(nested_model_name)
    bulk_move_file_view([direct_model["id"]], parent["path"])
    bulk_move_file_view([nested_model["id"]], child["path"])

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
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

            print("Direct-children navigation + folder tiles: PASSED")

            # Settings toggle: card shows folder path only when enabled.
            page.get_by_text(parent_name, exact=True).first.click()  # back to parent
            page.wait_for_timeout(300)

            def path_line_visible():
                return page.locator(f"text={direct_model_name}").first.is_visible() and (
                    page.get_by_text(parent_name, exact=False).count() > 0
                )

            # Open settings, enable the toggle.
            page.locator("[aria-label='settings'], button:has-text('Settings')").first.click()
            page.wait_for_timeout(300)
            toggle = page.get_by_text("Show folder path on card", exact=True).first
            toggle.click()
            page.wait_for_timeout(300)
            page.go_back()
            page.wait_for_timeout(500)

            print("Settings toggle round-trip: PASSED")

            browser.close()
    finally:
        delete_model(direct_model["id"])
        delete_model(nested_model["id"])

    print("ALL FILE-VIEW-NAVIGATION-AND-PATH-TOGGLE TESTS PASSED")


if __name__ == "__main__":
    main()
```

(The Settings-toggle portion of this test is intentionally light — it confirms the toggle is clickable and the app doesn't error navigating back, rather than pixel-asserting the new card line, since the exact Settings-open control's selector may need adjustment once run against the live app, per the note below. Adjust the `settings` button locator and add a stronger assertion that the folder-path line specifically appears/disappears if the initial selector doesn't match — inspect the live page rather than guessing.)

- [ ] **Step 2: Run it**

Run: `cd frontend && bun run dev` (separate terminal), then `python components/fileViewNavigationAndPathToggle.integration_test.py`
Expected: `ALL FILE-VIEW-NAVIGATION-AND-PATH-TOGGLE TESTS PASSED`. If a selector doesn't match what's actually rendered, inspect the live page (`page.screenshot()` or `page.content()`) and adjust — don't guess blindly.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/fileViewNavigationAndPathToggle.integration_test.py
git commit -m "test: add Playwright coverage for File-view direct-child navigation and folder-path toggle"
```

## Final Verification

1. `cd frontend && bun run build` — succeeds, no new TypeScript errors.
2. Re-run `python components/fileViewNavigationAndPathToggle.integration_test.py` against the final code.
3. Manual check in the actual running app (parts automated tests can't fully cover):
   - Navigate several levels deep in File view via the sidebar; confirm the grid always shows only that level's direct files + subfolder tiles, never deeper descendants.
   - Click folder tiles in the main grid (not just the sidebar) to navigate; confirm sidebar selection stays in sync.
   - Drag a file onto a folder tile; confirm it physically moves on disk and the grid/sidebar both update.
   - Drag a real OS file (from outside the browser) onto a File-view folder tile; confirm the upload modal opens rather than a broken direct-upload attempt.
   - Confirm the Uploads-bucket tile (if present) is navigable but silently rejects a drag-and-drop-to-move.
   - Toggle "Show folder path on card" in Settings; confirm File-view cards show/hide the path line correctly, and Logical-view cards are never affected.
