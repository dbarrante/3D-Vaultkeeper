# Bulk Move & Move-to-New-Folder — Design

## Problem

Bulk multi-select-and-move already exists, but only for Logical view: the floating action bar's "Move" button (`App.tsx:1114-1139`) opens a folder picker (`App.tsx:1643-1692`) that is a flat, non-nested button list hardcoded to the logical `folders` array — it never checks which view mode the user is in. File view has no bulk-move at all: its only "move" affordance is a single-file drag-and-drop, or a `window.prompt` text box (`Sidebar.tsx:877-899`) asking the user to paste a raw absolute path. There is also no way to create a new folder and move a selection into it in one action, in either view mode.

## Goal

1. Make the existing bulk "Move" action view-aware: in File view it must present real, on-disk folders (respecting the real nested hierarchy), not logical folders.
2. Add a new bulk action, "Move to New Folder": select multiple models, choose a parent location, name a new folder, and move the selection into it — created fresh. Same view-mode split: File view presents real folder locations as the parent-choice tree, Logical view presents logical folders.
3. While building the folder-picker this requires, also replace File view's single-folder `window.prompt` "Move" with the same picker, removing that rough edge.

## Scope

Both view modes, for the existing bulk-selection mechanism only (`selectedIds` in `App.tsx`). Does not touch single-model drag-and-drop moves, the Upload-to-folder modal, or Import Wizard folder routing — none of those were part of the request, and each has different constraints (e.g. drag-and-drop already works per-view-mode via separate code paths).

## Architecture

### Shared tree-data hook

`frontend/hooks/useFolderTree.ts` (new): extracts Sidebar's two existing tree-builders — `treefolders()` (logical, `Sidebar.tsx:272-296`) and the `fileTree` `useMemo` (file view, `Sidebar.tsx:304-393`) — into a reusable hook. Signature mirrors what Sidebar already produces:

```ts
function useFolderTree(
  viewMode: "logical" | "file",
  folders: Folder[],
  models: STLModel[],
  trackedFolderPaths: string[]
): { items: TreeViewDefaultItemModelProperties[]; realPaths?: Map<string, string> }
```

`realPaths` is populated only for `viewMode === "file"` (translates a synthetic file-tree node id back to a real filesystem path, exactly as `Sidebar.tsx`'s `fileTree.realPaths` does today). Sidebar.tsx is refactored to call this hook instead of its inline versions — a pure extraction, no behavior change there. This is a mechanical refactor; the tree-construction logic itself is copied verbatim, not redesigned.

### FolderPicker component

`frontend/components/FolderPicker.tsx` (new): a dialog wrapping MUI's `RichTreeView` (the same tree component Sidebar already uses — `Sidebar.tsx:815,825` — so this introduces no new UI pattern), fed by `useFolderTree`.

```ts
interface FolderPickerProps {
  open: boolean;
  viewMode: "logical" | "file";
  folders: Folder[];
  models: STLModel[];
  trackedFolderPaths: string[];
  title: string; // e.g. "Move to folder" or "Choose a location for the new folder"
  onSelect: (target: { mode: "logical"; folderId: string | null } | { mode: "file"; realPath: string | null }) => void;
  onClose: () => void;
}
```

`onSelect`'s tagged union always matches the `viewMode` the picker was opened with — the tag exists so call sites can switch on it without an `in` check.

**The root node is conditional, not universal** (a correction found while grounding the plan against the actual schema): `models.folderId` is a `NOT NULL` column — there is no "no folder" state for a model in Logical view, so a root option must never be offered as a Logical **move** destination; doing so would send `folderId: null` and hit a database constraint violation. Root is valid in exactly two places: (1) File-view moves — a file can sit flat in the library root, which is exactly what the existing "Uploads" bucket already represents; (2) picking a parent when *creating* a new folder in either mode, since `folders.parentId` and file-view's `parentPath` are both genuinely nullable there (mirroring the existing "New Root Folder" affordance in Sidebar). The picker takes an `allowRoot: boolean` prop so each call site controls this: the plain "Move" picker passes `allowRoot={viewMode === "file"}`; the "Move to New Folder" parent picker always passes `allowRoot={true}`. Selecting root yields `{mode: "logical", folderId: null}` or `{mode: "file", realPath: null}` — `null` is deliberate, not a placeholder: it's the same "library root" convention `POST /api/file-view/folder`'s `parentPath: null` already uses, since the frontend has no other way to name the real UPLOAD_DIR path as a string. The file-view bulk-move endpoint (below) accepts the same `null`-means-root convention for consistency.

**Three call sites:**
1. Bulk "Move" — replaces `App.tsx:1643-1692`'s hardcoded flat list.
2. Bulk "Move to New Folder" — used for the parent-location step (see below).
3. Single File-view folder "Move" in the sidebar context menu — replaces the `window.prompt` at `Sidebar.tsx:877-899`.

(Upload-to-folder modal and Import Wizard are separate flows, out of scope per above — not additional call sites.)

### Bulk action bar

`App.tsx:1114-1139`: add a second button, "Move to New Folder", beside the existing "Move" button. Both are visible whenever `selectedIds.size > 0`, matching the existing bar's behavior.

- **"Move"** opens `FolderPicker` directly; `onSelect` calls the appropriate bulk-move API (see Data Flow) with the current selection.
- **"Move to New Folder"** opens `FolderPicker` in "choose a parent location" mode, then (on selection) shows a folder-name text field, then on submit runs the two-step create-then-move sequence below.

### "Move to New Folder" flow — frontend orchestration

No new combined backend endpoint. On submit:

1. Call the existing create-folder endpoint for the current view mode — `api.createFolder(name, parentId)` (logical) or `api.createFileViewFolder(parentPath, name)` (file view) — completely unchanged.
2. Read back the new folder's identity: `id` (logical) or `path` (file view) from the response.
3. Call the bulk-move endpoint (existing for logical, new for file view — see Data Flow) targeting that identity, with the current `selectedIds`.

If step 1 fails (e.g. a 409 name collision), stop before attempting step 2 — there is nothing to move into yet, and the error surfaces exactly as folder-creation errors already do elsewhere in the app.

## Data Flow & API Contracts

### Logical view bulk move — no backend change

`POST /api/models/bulk-move` already exists (`models.py:384-398`), body `{ids: string[], folderId: string | null}`, a pure DB write (`UPDATE models SET folderId=?`). The `FolderPicker`, in logical mode, resolves the chosen tree node to a `folderId` (or `null` for root) and calls the existing `api.bulkMoveModels(ids, folderId)` unchanged.

### File view bulk move — one new endpoint

`POST /api/file-view/models/bulk-move`, body `{ids: string[], targetPath: string | null}` (`null` means the library root — `UPLOAD_DIR` — same convention `FolderCreateRequest.parentPath` already uses), added to `backend/app/routers/file_view.py` (the router that already owns every other File-view-specific operation, per this codebase's established convention — see the reveal-in-Explorer endpoint added earlier today).

Implementation loops each id, reusing the exact per-file logic `update_model_location` already has (`models.py:199-264`):
- Resolve the model's current real path by `storageMode` (`sourcePath` if `reference`, else the resolved copy-mode file).
- Compute `destination = Path(targetPath) / Path(current_path).name` — the target directory plus the file's existing filename.
- `validate_destination(str(destination), storage_mode)` — same containment rules as every other File-view move.
- 409 if `destination` already exists.
- `shutil.move`, then update `filePath`/`sourcePath` in the DB; roll back the filesystem move if the DB write fails (same rollback pattern as `update_model_location`, including the "stranded file" log warning on double failure).

Per the best-effort decision below, each id's failure is **caught, not raised** — the loop continues to the next id. Response includes each moved model's new `filePath`, not just its id — the frontend needs this to optimistically update model state afterward, the same way the existing logical bulk-move already does by writing the new `folderId` onto each moved model in place (`App.tsx:832-836`) rather than refetching:

```json
{ "moved": [{"id": "id1", "filePath": "C:\\...\\NewFolder\\model1.stl"}], "failed": [{"id": "id3", "reason": "A file already exists at ..."}] }
```

`frontend/services/api.ts` gets a matching `bulkMoveFileViewModels(ids: string[], targetPath: string): Promise<{moved: {id: string, filePath: string}[], failed: {id: string, reason: string}[]}>`.

### Single-file File-view "Move" upgrade

`Sidebar.tsx:877-899`: the `window.prompt` is replaced by opening `FolderPicker` in file mode; on selection, calls the existing `api.moveFileViewFolder(sourcePath, targetPath)` unchanged (that endpoint already accepts a `targetPath` — no backend change needed).

## Error Handling

**Partial bulk-move failures (best-effort, not all-or-nothing):** chosen because one bad file (a stale path, a name collision) should not block moving the rest of a batch — matches the existing pattern in `deleteFileViewFolder`, which already tolerates partial filesystem failure and reports what happened rather than rolling everything back. After a bulk-move call, if `failed` is non-empty, the frontend shows a summary ("Moved 8 of 10 files. 2 failed: <reasons>") rather than silently reporting success.

**"Move to New Folder" folder-creation failure:** stops before any move is attempted (see Architecture above) — there is no partial state to reconcile since nothing has moved yet.

**Logical bulk-move:** unchanged from today — a pure DB write with no real failure mode beyond a nonexistent id, which the existing endpoint already silently skips (tombstoned/removed rows).

## Testing

**Backend:** new pytest coverage for `POST /api/file-view/models/bulk-move`, in the style of this afternoon's `test_reveal_in_explorer.py` — a happy-path test (multiple files move successfully, DB rows updated, files present at the new location), a partial-failure test (one collision among several; the others still move, and the response correctly lists both `moved` and `failed`), and a rollback test (a forced DB-write failure on one id doesn't affect ids already committed, and the file is confirmed moved back).

**Frontend:** Playwright integration tests for `FolderPicker` in both view modes (tree renders with the right nodes, the root option is selectable, selecting a node fires `onSelect` with the right shape), and for the "Move to New Folder" two-step flow end-to-end using a network-request spy — following the pattern established by `revealInExplorer.integration_test.py`.

**Manual verification:** as with the Explorer-reveal feature, actually confirming files land in the right real folder on disk isn't fully provable by a mocked test. Final Verification includes moving a multi-file selection via both "Move" and "Move to New Folder", in both view modes, and confirming the app's UI and the real filesystem/DB agree afterward.

## Out of Scope

- Single-model drag-and-drop move (already works per-view-mode; not part of this request).
- Upload-to-folder modal and Import Wizard folder routing (separate flows, not mentioned in the request).
- A combined "create folder and move" backend endpoint (rejected in favor of frontend orchestration reusing existing create-folder endpoints — see Architecture).
- Any change to how Logical bulk-move itself works server-side (`models.py:384-398` is reused as-is).
