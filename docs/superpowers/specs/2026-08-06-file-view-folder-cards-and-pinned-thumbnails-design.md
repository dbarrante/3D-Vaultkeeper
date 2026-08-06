# File-View Folder Cards + Pinned Folder Thumbnails — Design

## Problem

Two related gaps:

1. **File view never shows folder cards.** `App.tsx` hard-codes `folders={viewMode === "file" ? [] : filteredFolders}` when passing props to `ModelList`, so in File view the main grid renders only file cards — subfolder navigation is only possible via the sidebar's file-tree. Logical view already shows folder cards mixed with model cards in the same grid; File view doesn't.
2. **Folder thumbnails can't be chosen.** Both Logical folder cards and (once built) File-view folder cards show a thumbnail borrowed from "the earliest-added model in that folder that happens to have a thumbnail" (`App.tsx:375-393`'s `folderPreviews` memo) — there's no way to pick a specific, more representative model's thumbnail to represent the folder.

## Goal

1. File view's grid shows folder cards for the current directory's direct subfolders, exactly like Logical view already does — same card component, same click-to-navigate behavior.
2. A new right-click action on a model card, "Set as Folder Thumbnail," pins that model's thumbnail as its containing folder's displayed image — in both Logical and File view — overriding the auto-pick until cleared.

## Architecture

### Part 1: File-view folder cards

**Shared tree-building helper (new): `frontend/lib/fileViewTree.ts`**

Today, the folder tree is built exactly once, privately inside `Sidebar.tsx`'s `fileTree` `useMemo` (`Sidebar.tsx:304-393`) — it walks every model's `filePath` via the existing `fileViewSegments()` helper (`types.ts:117-123`), builds a node per path segment keyed by a `childMap`, and separately merges in `trackedFolderPaths` (explicitly-created, possibly-empty folders) so they appear even with zero models. This walk needs to become reusable so the grid can ask for one level's worth of children, not just the sidebar's full recursive tree.

Extract the walk into a pure function:

```ts
export interface FileTreeNode {
  id: string;           // "file/Characters/Goblin" -- same id scheme the sidebar already uses
  label: string;        // "Goblin" -- the last segment
  realPath: string;     // the actual filesystem path this node represents
  children: FileTreeNode[];
}

export function buildFileViewTree(
  models: STLModel[],
  trackedFolderPaths: string[],
): FileTreeNode[]   // returns the root-level children (top-level folders)
```

This is a direct extraction of `Sidebar.tsx:304-393`'s existing logic (including its `childMap`/`realPaths` bookkeeping) with no behavior change — `Sidebar.tsx` calls it inside its own `fileTree` `useMemo` exactly as before (its output shape is compatible with what `RichTreeView` needs after a small `strip()` mapping, unchanged).

A second small helper finds one level's children:

```ts
export function getDirectChildren(
  tree: FileTreeNode[],
  currentFolderId: string,   // "all" | "__uploads__" | "file/A/B/..."
): FileTreeNode[]
```

Walks from the root down to the node matching `currentFolderId` (splitting on `/` the same way `App.tsx:339`'s existing `filteredModels` filter already does) and returns its immediate `children`. Returns the top-level `tree` itself when `currentFolderId` is `"all"`.

**Wiring in `App.tsx`:**

- Build the tree once per relevant re-render: `const fileViewTree = useMemo(() => buildFileViewTree(models, trackedFolderPaths), [models, trackedFolderPaths])` — computed independently from Sidebar's own identical call (not prop-drilled between components), since both are cheap single-pass walks over the model list and keeping them independent avoids new cross-component coupling for a modest, one-time-per-render cost.
- `const fileViewFolderCards = useMemo(() => getDirectChildren(fileViewTree, currentFolderId), [fileViewTree, currentFolderId])`, active only when `viewMode === "file"`.
- Synthesize each `FileTreeNode` into the existing `Folder` shape `ModelList` already knows how to render as a card: `{ id: node.id, name: node.label, parentId: null, description: null }`. `parentId` is unused by File-view navigation (folder-card clicks call `onSelectFolder(folder.id)`, which only ever does `setCurrentFolderId(id)` — no parentId dependency anywhere in that path).
- Replace the hard-coded `folders={viewMode === "file" ? [] : filteredFolders}` (`App.tsx:1041`) with `folders={viewMode === "file" ? fileViewFolderCards.map(toFolderShape) : filteredFolders}`.

`ModelList.tsx`'s existing folder-card rendering (`ModelList.tsx:612-693`) and click-to-navigate wiring need **no changes** — they already work generically on `Folder[]`.

### Part 2: Pinned folder thumbnails

**Data model.** Two places a folder's identity lives, so two places to store a pin:

- **Logical folders** (`folders` table, has a stable id): new nullable column `pinnedThumbnailModelId TEXT`, added the same way `description` was added earlier (`ALTER TABLE folders ADD COLUMN ...` in the startup migration path in `db.py`).
- **File-view folders** (usually no stable row — most exist only as a parsed path, not a DB record): the existing `file_view_tracked_folders` table already tracks a folder by its `path` and already survives renames/moves (`rewrite_tracked_folder_paths`, called from every rename/move/delete in `file_view.py`). Add the same nullable `pinnedThumbnailModelId TEXT` column there. Pinning a thumbnail on a folder that isn't already tracked (i.e., most folders, since today only explicitly-created *empty* folders get a row) inserts a new tracked-folder row for it — this is a natural, minor scope-widening of what that table means (from "explicitly-created empty folders" to "folders with any durable per-path metadata"), not a new table.

**New/changed backend endpoints:**

- `PATCH /api/folders/{folder_id}` (existing route, `backend/app/routers/folders.py`): extend its request/response model to accept and return `pinnedThumbnailModelId: string | null`.
- `POST /api/file-view/folder/pin-thumbnail`, body `{ path: string, modelId: string | null }` (new, in `file_view.py`): `INSERT OR IGNORE INTO file_view_tracked_folders(path) VALUES (?)` (creates the row if this folder was never tracked before), then `UPDATE file_view_tracked_folders SET pinnedThumbnailModelId=? WHERE path=?`. `modelId: null` clears the pin.
- `GET /api/file-view/folder-thumbnails` (new): returns `{ [path: string]: string }`, every tracked folder's `path` → `pinnedThumbnailModelId` where the pin is non-null. Kept separate from the existing `GET /api/file-view/tracked-folders` (which returns bare path strings and is consumed by the sidebar's empty-folder merge) so that endpoint's response shape and its one existing consumer (`Sidebar.tsx`) stay untouched.

**Frontend preview computation, extended in both places:**

- **Logical** (`App.tsx:375-393`'s `folderPreviews` memo): check `folder.pinnedThumbnailModelId` first (resolve to that model's `thumbnail` if the model still exists and has one) before falling back to today's "earliest model with a thumbnail" logic.
- **File view** (new, alongside `fileViewFolderCards`): for each folder node, check `folderThumbnailPins[node.realPath]` first (fetched via the new endpoint into App.tsx state, alongside the existing `trackedFolderPaths` fetch), falling back to "earliest model, by `dateAdded`, whose `filePath` starts with `node.realPath`, that has a thumbnail" — the same rule as Logical, keyed by path-prefix match instead of `folderId` equality.

**New context-menu items:**

- **Model cards, Logical view** (first-ever context menu there): one item, "Set as Folder Thumbnail" → `PATCH /api/folders/{model.folderId}` with `{ pinnedThumbnailModelId: model.id }`.
- **Model cards, File view** (existing menu, `ModelList.tsx:1028-1051`): same item added → `POST /api/file-view/folder/pin-thumbnail` with `{ path: <the model's own immediate parent directory>, modelId: model.id }`. That parent path is derived from the model's own `filePath` directly (take its directory, i.e. `filePath` with the filename dropped — the same computation `fileViewSegments`/`Sidebar.tsx`'s tree-walk already do per-model), **not** from whatever `currentFolderId` the grid happens to be scoped to. This matters because a model can be right-clicked while browsing `"all"` (every model system-wide, no specific folder selected) or the Uploads bucket — in both cases `currentFolderId` doesn't correspond to a single real path, but every individual model still has its own real, unambiguous parent directory regardless of what level is currently being browsed.
- **Folder cards, both views** (new — folder cards have no context menu today): one conditional item, "Clear Pinned Thumbnail," shown only when that folder currently has a pin set (so folder cards stay menu-free in the common unpinned case). Calls the same endpoints as above with `pinnedThumbnailModelId: null` / `modelId: null`.

## Error Handling

- Pinning a model whose thumbnail hasn't been generated yet (`thumbnail` is still null) is allowed — the pin is stored regardless, and the folder card falls back to showing the generic folder icon (same as today's "no models have thumbnails yet" case) until that model's thumbnail exists, at which point it appears automatically on the next fetch. No special-casing needed: the preview-resolution logic already treats "pinned model has no thumbnail" the same as "pinned model not found" by falling through to null (never silently reverting to the auto-pick fallback, since that would be confusing — the user's pin should visibly represent their choice, including "chose a model that has no thumbnail yet," not silently substitute a different model).
- If a pinned model is later deleted, its `pinnedThumbnailModelId` becomes a dangling reference; the preview-resolution logic must handle "referenced model no longer exists" as equivalent to "no thumbnail available" (generic folder icon), not by falling back to auto-pick (same reasoning as above — silently switching to a different model the user didn't choose would be surprising). Explicitly clearing the pin remains a manual action.

## Testing

Backend: unit tests for the new/extended endpoints (pin/clear on both folder types, `INSERT OR IGNORE` creating a previously-untracked folder's row, the new `folder-thumbnails` endpoint's response shape) and for `rewrite_tracked_folder_paths` continuing to correctly carry a `pinnedThumbnailModelId` across a rename/move (already covered generically by that function's existing tests, worth a specific pin-survives-rename case given this is new column).

Frontend: real Playwright verification per this project's established convention — pin a model's thumbnail, confirm the folder card shows it (both Logical and File view), confirm the auto-pick fallback still works when unpinned, confirm "Clear Pinned Thumbnail" only appears on a pinned folder and correctly reverts to auto-pick, confirm File-view folder cards render and navigate correctly (clicking one updates `currentFolderId` and the grid contents, matching Logical folder-card click behavior).

## Out of Scope

- Uploading/choosing a custom image as a folder thumbnail (only "pin an existing model's already-generated thumbnail" — no new image-handling pipeline).
- Any richer folder-card context menu beyond the one conditional "Clear Pinned Thumbnail" item.
- Reusing/reviving the existing-but-unused `Folder.icon` frontend type field — confirmed dead (no DB column, no backend read/write, no frontend read anywhere) and unrelated to this feature; not touched.
