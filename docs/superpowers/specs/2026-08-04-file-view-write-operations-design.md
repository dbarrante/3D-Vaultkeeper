# File View: Write Operations (Rename, Move, Copy, Delete) Design

**Status:** Approved by Dave, ready for planning.

## Problem

File mode (shipped in the previous feature, see
`2026-08-04-file-organization-import-and-view-design.md`) is currently a
read-only lens: it shows where files really live on disk, grouped by
`filePath`, but offers no way to act on what it shows. Dave wants to manage
files directly from this view — right-click a file or folder node to rename,
delete, copy, or move it, the same way he'd manage files in a normal OS file
browser — instead of having to leave the app to fix drift between logical
organization and physical location.

This reverses File mode's original "no create/rename/delete/drag-to-move"
constraint, deliberately: that constraint existed because File mode had no
backing `folders` table entity to write to. It still doesn't — these
operations write directly to the filesystem and to `models` rows located by
path, never to a `folders` row.

## Goals

- Right-click context menu in the File-mode tree: **Rename, Move, Copy,
  Delete** on a file node; **Rename, Move, Delete** on a folder node (no
  folder-level copy — see Non-goals).
- Drag-and-drop within the File tree as a move/copy shortcut for files:
  plain drag = move, Ctrl+drag = copy.
- Rename and delete operate on the *real file on disk*, not just a display
  name or a DB row — File mode's whole premise is showing physical reality,
  so these operations change physical reality and keep the DB in sync.
- Folder delete removes everything under it, including files File mode
  never indexed as models (non-model siblings), matching what a user expects
  from "delete this folder" in a normal file browser.
- Linked (reference-mode/watch-folder) files keep working correctly after
  rename/move — the watcher must not lose track of them or re-ingest them as
  duplicates.

## Non-goals

- **No folder-level copy.** Recursively duplicating a folder (every file on
  disk, plus a new model row per model file, plus collision handling at
  every level of the copy) is substantially more complex than anything Dave
  asked for by name. File-level copy covers the actual request; folder copy
  can be revisited later if it turns out to be wanted.
- **No confirmation dialog for rename/move/copy.** Only delete confirms.
  This matches how Dave uses a normal file browser day to day.
- **No new Logical-mode behavior.** Logical mode's existing create/rename/
  delete/drag-to-move is untouched by this spec.
- **No general logical-to-disk sync.** Still out of scope, as stated in the
  prior spec — these are direct, explicit, one-node-at-a-time operations
  triggered by the user, not an automatic reconciliation feature.

## Architecture

Two operation shapes, because File-mode folders and files are fundamentally
different kinds of thing:

- **File-level operations** act on an existing `models` row, identified by
  `model_id`. They live in `backend/app/routers/models.py`, next to the
  existing `delete_model`/`download_model`/`replace_model_file` endpoints
  they parallel.
- **Folder-level operations** act on a real disk path with no corresponding
  DB row — File-mode folders are synthetic, derived from grouping `filePath`
  values, exactly like the read-only tree already is. They live in a new
  `backend/app/services/file_view_ops.py` + `backend/app/routers/file_view.py`,
  keyed on the folder's absolute path rather than an id, and resolve
  affected models by `filePath`/`sourcePath` prefix match.

Rename and move are the same underlying operation at both levels — both
just relocate a real path to a new real path — so each level gets one
"set new location" endpoint rather than two.

### The reference-mode (linked file) problem

`row_to_model` already computes `missing` for a reference-mode model by
checking `os.path.exists(sourcePath)`, and `scan_watch_folder` dedups new
finds against the set of existing `sourcePath` values
(`backend/app/services/scan.py`). If a rename/move only updated `filePath`
and left `sourcePath` pointing at the old location, the model would
immediately show as missing, and the next scan would re-ingest the file at
its new location as a brand-new row — losing tags/description and leaving a
phantom "missing" duplicate behind.

So: for reference-mode models, rename/move update **both** `filePath` and
`sourcePath` to the new location, in the same operation. This only works
when the destination is still inside *some* watched root (the file has to
stay somewhere the watcher can see it) — see Validation below for what
happens when it isn't.

Delete for a reference-mode model in File view always hard-deletes: removes
the physical file and the DB row. This is different from the existing
`DELETE /api/models/{id}` endpoint's *default* behavior, which tombstones
a reference-mode row (`removedAt`, row kept) unless `deleteFile=True` is
passed, specifically so the watcher doesn't resurrect a file the user only
meant to unlink from the library. File-mode delete has no such ambiguity —
right-clicking a real file and choosing Delete means delete the real file —
so it always calls the existing endpoint with `deleteFile=true`.

## Backend

### File-level endpoints (`backend/app/routers/models.py`)

**`PATCH /api/models/{id}/location`**

Body: `{"newPath": "<absolute path>"}`.

1. Load the model row. 404 if it doesn't exist.
2. Validate `newPath` (see Validation below).
3. `shutil.move(currentPath, newPath)`, where `currentPath` is resolved the
   same way `_resolve_copy_mode_file` / `sourcePath` already resolve it
   today for copy-mode vs reference-mode.
4. Update the row: `filePath = newPath` always; `sourcePath = newPath` too,
   if `storageMode == "reference"`.
5. Return the updated model (`row_to_model`).

Both rename (same directory, new filename) and move (new directory) call
this one endpoint — the frontend computes `newPath` differently depending on
which gesture triggered the call, but the backend doesn't need to know which
one it was.

**`POST /api/models/{id}/duplicate`**

Body: none (operates on the model referenced in the URL).

1. Load the model row. 404 if it doesn't exist.
2. Resolve the current physical file the same way delete/download do.
3. Copy the file to a new destination in the same directory, auto-suffixing
   the filename on collision (`hull.stl` → `hull_1.stl`, incrementing).
4. Insert a new `models` row: new `id`, `storageMode: "copy"` (a duplicate
   is always a new managed copy, regardless of the source's mode — cloning a
   reference-mode row as another reference into the same watched file would
   just be two rows pointing at one physical file, which breaks the
   watcher's dedup assumption), same `name`/`tags`/`description`/`folderId`
   as the source, new `filePath` pointing at the copy, `sourcePath: null`.
5. Return the new model.

**`DELETE /api/models/{id}`** — unchanged. File-mode's delete action calls
it with `?deleteFile=true`.

### Folder-level endpoints (new `backend/app/routers/file_view.py`,
registered in `main.py` alongside the other routers)

All three take a real absolute path, not a folder id, and resolve affected
models via `SELECT * FROM models WHERE filePath LIKE ? || '%'` (parameterized
prefix match, not string-formatted) plus the same check against `sourcePath`
for reference-mode rows.

**`POST /api/file-view/folder/rename`** — `{"path": "...", "newName": "..."}`.
Renames the directory itself (`shutil.move` from `path` to
`sibling/newName`), then updates every affected model's `filePath` (and
`sourcePath`, for reference-mode rows under it) by replacing the `path`
prefix with the new prefix.

**`POST /api/file-view/folder/move`** — `{"sourcePath": "...", "targetPath": "..."}`,
where `targetPath` is the full destination directory path (e.g. dropping
`Vehicles/Tanks` onto `Archive` produces `targetPath: ".../Archive/Tanks"`,
computed by the frontend the same way it already computes drop destinations
for file-level drag-and-drop). Same shape as rename: moves the directory,
then rewrites every affected model's `filePath`/`sourcePath` prefix.

**`DELETE /api/file-view/folder`** — `{"path": "..."}`.

1. **Guard first, before touching anything:** reject with 400 if `path` is
   a filesystem root (`Path(path).parent == Path(path)`, e.g. `D:\`) or
   exactly matches a `watch_folders.path` value. This is a hard refusal, not
   a stronger confirmation — a confirm dialog isn't enough protection against
   deleting an entire drive or someone's whole Dropbox folder by mistake.
2. For every model row whose `filePath`/`sourcePath` is under `path`: delete
   its physical file (if not already removed by step 3) and its DB row,
   per-row try/except so one failure doesn't abort the rest.
3. `shutil.rmtree(path)` to remove the directory and any untracked sibling
   files/subfolders inside it.
4. Return `{"deletedModels": N, "path": "..."}`.

### Validation (shared by all four write endpoints)

A destination path must resolve (via `.resolve()`, guarding against `..`
traversal) inside one of:
- `UPLOAD_DIR`, for copy-mode files/folders, or
- some row in `watch_folders.path`, for reference-mode files/folders being
  renamed/moved (never an arbitrary path outside every watched root — if the
  destination isn't under a watched root, the operation 400s with a message
  explaining a linked file has to stay somewhere the app is watching).

This mirrors the containment check already used by `import_wizard.py`'s
`folder_disk_path` / commit path.

## Frontend

- **Context menu** (new, e.g. `frontend/components/FileViewContextMenu.tsx`):
  right-click a tree node in File mode opens a menu. File nodes get Rename /
  Move / Copy / Delete; folder nodes get Rename / Move / Delete.
- **Rename**: inline text field (reuse the pattern Logical mode's folder
  rename already uses), submits the new filename, calls
  `PATCH /api/models/{id}/location` (file) or
  `POST /api/file-view/folder/rename` (folder).
- **Move**: triggered either from the menu (opens a small folder picker
  scoped to the current File tree) or by plain drag-and-drop onto another
  node in the same tree.
- **Copy**: triggered either from the menu (file nodes only) or by
  Ctrl+drag. Folder nodes never show Copy and never respond to Ctrl+drag.
- **Delete**: confirm dialog (states "this file" or, for a folder, the
  number of files it will remove), then calls the corresponding delete
  endpoint.
- After any successful operation, refetch models (and the file tree derives
  from that same data, so it updates automatically) so the grid and sidebar
  both reflect the new state immediately.
- Errors from any endpoint (containment violation, locked file, guard
  refusal on drive/watch root) surface as a toast/snackbar with the
  backend's message, not a silent failure.

## Safety & Error Handling

- **Delete always confirms; nothing else does** — matches Dave's explicit
  choice and ordinary file-browser behavior.
- **Drive-root and watch-root folder delete is refused outright**, not just
  confirmed harder.
- **Reference-mode rename/move keeps `sourcePath` and `filePath` in lock
  step**, so the watcher never loses or duplicates a linked file.
- **Per-file isolation on folder operations** — one bad file inside a folder
  being renamed/moved/deleted doesn't abort the rest, consistent with the
  failure-tolerant pattern already used by `scan_watch_folder` and the
  Import Wizard's commit path.
- **Destination containment is enforced server-side** on every write
  endpoint, independent of whatever the frontend already prevents in the UI.

## Testing

**Backend (pytest, `tmp_path`-based real filesystem tests, following
existing `backend/tests/` conventions):**

- File rename/move: physical file relocates, `filePath` updates correctly,
  copy-mode and reference-mode both covered.
- Reference-mode rename/move: `sourcePath` updates alongside `filePath`; a
  subsequent simulated watch-folder scan does *not* re-ingest the file as a
  duplicate at its new location.
- Move outside every watched root (reference-mode) is rejected with a clear
  error and the file is left untouched.
- File duplicate: new row created with a new id and a new physical copy;
  filename collision auto-suffixes; original file/row untouched.
- Folder rename/move: directory relocates, every affected model's
  `filePath`/`sourcePath` prefix updates correctly, including nested
  subfolders.
- Folder delete: removes tracked model rows and their files, removes
  untracked sibling files, removes the directory itself.
- Folder delete guard: a drive-root path and a `watch_folders.path` value
  are both rejected with 400 and nothing on disk is touched.
- A per-file failure during a folder operation (e.g. a locked file) doesn't
  abort the rest of the folder.

**Frontend:** no automated suite exists in this project (consistent with the
rest of the codebase) — verified manually via the packaged build: rename,
move (menu and drag), copy (menu and Ctrl+drag), and delete (file and
folder) exercised against a fixture library with both copy-mode and
reference-mode (watch-folder) models, including an attempted delete on a
drive-root node to confirm the guard blocks it in the UI.
