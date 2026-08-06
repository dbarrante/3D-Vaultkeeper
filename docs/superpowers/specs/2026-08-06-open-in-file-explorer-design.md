# Open in File Explorer — Design

## Problem

3D Vaultkeeper's File view has two right-click context menus today — one on model/file cards (`frontend/components/ModelList.tsx`), one on folders in the sidebar's file-tree (`frontend/components/Sidebar.tsx`) — and neither offers any way to jump from the app into the real file on disk. There is currently no OS-integration feature of this kind anywhere in the codebase (confirmed by repo-wide search).

## Goal

Add "Open in File Explorer" to both existing File-view context menus. For a model file, this opens its parent folder in Windows Explorer with the file pre-selected/highlighted. For a File-view folder, this opens that folder directly.

## Scope

File view only — the same scope these two context menus already have (`ModelList.tsx`'s file menu is gated `if (viewMode !== "file") return;`; `Sidebar.tsx`'s folder menu only exists on the file-tree). Logical folders are explicitly out of scope: a Logical folder is a named grouping in the `folders` table, not tied to one single physical directory (its models can live under any watched root), so there is no single meaningful path to reveal.

## Architecture

### New backend endpoint

`POST /api/reveal-in-explorer`, body `{ "path": string }`.

- Validates `path` exists on disk (`os.path.exists`), returning 404 if not.
- If `path` is a file: `subprocess.Popen(["explorer", "/select,", path])` — opens the containing folder with the file highlighted. Windows' `explorer /select,` requires the comma to be part of the same argument as the flag, not a separate arg — `["explorer", "/select,", path]` produces `explorer /select, path` as separate argv entries, which Explorer accepts identically to `/select,path`; using the list form (not a shell string) avoids any shell-injection concern from a path containing spaces or special characters.
- If `path` is a directory: `subprocess.Popen(["explorer", path])`.
- Windows-only, matching every other OS-integration point in this app (the packaged desktop build only ships for Windows). No cross-platform branching needed.
- No response body needed beyond a 200 — this is fire-and-forget from the frontend's perspective, matching how the existing native folder-picker dialog (`POST /api/browse-folder`) already works.

### Frontend

- `frontend/services/api.ts`: add `revealInExplorer(path: string): Promise<void>`, a thin `POST` wrapper matching the existing style of other one-shot action calls in that file.
- `frontend/components/ModelList.tsx`: add a 4th item to the file context menu (alongside Rename/Copy/Delete at lines 1028-1051), calling `api.revealInExplorer(model.filePath)`. Disabled/hidden if `model.filePath` is falsy (should not happen for a model that reached File view, but matches the existing `if (!model.filePath) return;` guard pattern already used by the Rename handler).
- `frontend/components/Sidebar.tsx`: add a 5th item to the folder context menu (alongside New Folder/Rename/Move/Delete at lines 838-890), calling `api.revealInExplorer(path)` using the same real path string every other folder operation in this menu already works with (this menu's handlers already operate on `path: string`, not an id — see `backend/app/routers/file_view.py`'s `FolderRenameRequest`/`FolderMoveRequest`/`FolderDeleteRequest`, which all take a `path` field).

## Error Handling

If the path no longer exists on disk (e.g. deleted externally since the app last scanned), the endpoint returns 404 and the frontend shows a toast/error consistent with how other File-view operation failures are already surfaced (e.g. rename/delete failures in `ModelList.tsx`/`Sidebar.tsx`).

## Testing

Backend: a test that mocks `subprocess.Popen` (not a real Explorer launch) and asserts it's called with the exact expected argv for both the file and directory cases, plus a 404 test for a nonexistent path. Frontend: no new Playwright coverage needed beyond confirming the menu item renders and calls the API with the right path — this feature's actual effect (a real Explorer window) isn't something a headless Playwright/Chromium test can observe, so exhaustive verification of the end effect is a manual check by Dave on his own machine, not something the automated suite can prove.

## Out of Scope

- Logical-folder support (no single meaningful path, as explained above).
- Cross-platform (macOS/Linux) support — this app ships Windows-only.
- Any richer "open with" / "show properties" style menu — just the one action.
