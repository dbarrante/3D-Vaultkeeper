# File View Explorer-Style Navigation & Folder-Path Toggle — Design

## Problem

Clicking a folder in File view's sidebar already updates the main grid (`App.tsx:343-349`), but the filter is an ancestor/descendant **prefix match**, not a direct-children match — clicking `D:\Dropbox` shows every file in every nested subfolder, recursively, rather than only what's directly inside it. This isn't a regression; it's the filter's original, deliberate design (see its own code comment about avoiding false-positive name collisions at different depths — it never addressed depth itself). Separately, File view's main grid never renders folder tiles at all (`App.tsx:1097`: `folders={viewMode === "file" ? [] : filteredFolders}`) — subfolder navigation is sidebar-only, unlike Logical view where the grid shows both files and clickable subfolder tiles.

Separately, there's no way to see a model's containing folder on its card in the grid — useful once File view actually starts scoping to a single folder's direct contents, since the card is the only place left that could show *which* folder a file lives in without leaving the current view.

## Goal

1. File view's main grid, for any selected folder (including "all" and the synthetic Uploads bucket), shows only that folder's **direct** children: its own files, plus tiles for its own direct subfolders — matching how Windows Explorer's right pane works. Clicking a subfolder tile navigates into it, same as clicking it in the sidebar.
2. File-view folder tiles support drag-and-drop-to-move, at parity with Logical view's existing folder tiles.
3. A new Settings toggle, "Show folder path on card" — File view only — shows each card's containing directory (full absolute path) when enabled.

## Scope

Frontend only. The backend endpoints this needs (`POST /api/file-view/models/bulk-move`, `POST /api/file-view/folder/move`) already exist and are already tested from the prior "Bulk Move & Move-to-New-Folder" plan. Logical view's existing folder-tile behavior, model filtering, and card layout are unchanged — this only touches File view's filtering/tiles and adds one new, File-view-scoped card line.

## Architecture

### Filter fix

`App.tsx`'s `filteredModels` (File-view branch, `:343-349`) changes from a prefix/subset match to an exact-depth match. `fileViewSegments(filePath)` already strips the filename before returning segments (confirmed in `frontend/types.ts`), so "file sits directly in folder X" is simply `modelSegments.length === targetSegments.length` combined with the existing positional-prefix check — no depth arithmetic needed beyond dropping the old `>=` in favor of `===`. The synthetic Uploads-bucket branch is already exact-depth (`fileViewSegments(...).length === 0`) and needs no change.

### `useFolderTree` gets a `nodesById` lookup map

`frontend/hooks/useFolderTree.ts`'s return type gains one new field: `nodesById: Map<string, TreeViewDefaultItemModelProperties>`, built during the same tree-construction pass the hook already does (populated for both view modes, not just File view, since the type is shared — Logical view simply won't have a consumer that reads it yet). This is the single place "find a node's direct children by id" is computed — every consumer (Sidebar, `FolderPicker`, and now `App.tsx`) reads from the same tree, so it's structurally impossible for the grid's idea of "direct children" to drift from what the sidebar renders.

### `App.tsx` derives File-view folder tiles and previews

`App.tsx` gains a new call to `useFolderTree` (a third call site, alongside Sidebar's and `FolderPicker`'s existing ones — the hook is a pure `useMemo`, designed to be called independently by each consumer that needs it).

- **Direct-child folders for the current selection**: at `currentFolderId === "all"`, this is `folderTree.items` (the tree's top-level array, which already includes the synthetic Uploads-bucket node at that level — mirroring how Logical view's `filteredFolders` shows `parentId == null` folders at "all"). Otherwise, `nodesById.get(currentFolderId)?.children ?? []` (empty for a leaf or the Uploads bucket — no subfolders, same as Logical). Each child node maps to a lightweight object shaped like `Folder` (`{id, name: label, parentId: currentFolderId}`, no `description`) so `ModelList` needs **zero new tile-rendering code** — it already renders whatever `folders` array it's given, view-mode-agnostic; only the caller-side data changes.
- **Folder-tile previews** (count + representative thumbnail): mirrors `folderPreviews`' existing shape and selection rule exactly (`App.tsx:379-397` — count of direct children, thumbnail = the earliest-`dateAdded` model that actually has a thumbnail, not just the earliest model). Computed once over all `models`, grouped by each model's own synthetic parent id (`"file/" + fileViewSegments(m.filePath).join("/")`, or the Uploads-bucket sentinel when segments are empty) instead of `m.folderId`.

### Drag-and-drop

`handleDropMove(targetFolderId, modelIds)` (`App.tsx:899`, already shared by Sidebar's file-tree drop and Logical folder tiles) becomes view-mode-aware, branching the same way `handleBulkMoveSelect` (from the prior plan) already does: Logical → unchanged `api.bulkMoveModels`. File view → resolve `targetFolderId` (a synthetic tree id) to a real path via the same `realPaths` map the hook already produces, then `api.bulkMoveFileViewModels(modelIds, realPath)` + `fetchData()`, reusing that plan's best-effort partial-failure `alert(...)` convention.

### Settings toggle

- `frontend/services/api.ts`: `getShowFolderPathOnCard(): boolean` / `setShowFolderPathOnCard(value: boolean): void`, localStorage key `"stlvault-show-folder-path"`, `"true"`/`"false"` string values — matching this file's existing localStorage-boolean-flag pattern (e.g. `api-port-override`).
- `Settings.tsx`: one new toggle control. No `Switch`/checkbox precedent exists anywhere in this codebase (confirmed by search) — build a minimal on/off toggle visually consistent with the existing Default-Slicer button-toggle style already in this file, rather than introducing a new UI dependency for one control.
- `App.tsx` reads the setting into state (localStorage-initialized, matching `selectedApiPort`'s existing init pattern in `Settings.tsx`), passes it to `ModelList` as a new `showFolderPath` prop.
- `ModelList.tsx`: one new conditional `Typography` line in the card's `CardContent` (directly after the existing size/date line, `:869-876`), showing `model.filePath`'s directory (the full absolute path with the filename stripped) — rendered only when `viewMode === "file" && showFolderPath && model.filePath`.

## Error Handling

File-view drag-and-drop failures reuse `handleBulkMoveSelect`'s exact convention: a partial-failure summary via `alert(...)` when `bulkMoveFileViewModels` reports any `failed` entries, and `console.error` + `alert` on a thrown exception. No new error-handling pattern.

## Testing

**Frontend**: no unit-test framework exists in this repo (confirmed across every prior plan this session — no Jest/RTL/Vitest, no `test` script). Verification is `bun run build` plus one Playwright scenario, following the established `.integration_test.py` convention: upload files into a real nested folder chain, confirm clicking a folder in File view shows only its direct-child files and subfolder tiles (not deeper descendants), click a tile to navigate deeper, and confirm the Settings toggle actually shows/hides the path line on a card.

**Manual verification**: drag-and-drop onto a File-view folder tile — this codebase's established Playwright conventions don't attempt to simulate real drag events reliably (confirmed by every prior `.integration_test.py` in this repo). Drag a file onto a tile, confirm it physically relocates on disk.

**No backend changes** — both features are entirely frontend; the endpoints this reuses (`POST /api/file-view/models/bulk-move`, `POST /api/file-view/folder/move`) already exist and are already tested from the prior plan.

## Out of Scope

- Logical view's card display or folder-tile behavior — unchanged.
- The folder-path toggle applying to Logical view (no ancestor-chain path builder exists there today; explicitly deferred per the brainstorming discussion).
- Relative (vs. full absolute) path display on the card — explicitly chosen as full absolute path.
- Any change to the sidebar tree itself beyond `useFolderTree`'s additive `nodesById` field.
