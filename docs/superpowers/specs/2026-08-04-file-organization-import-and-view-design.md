# File Organization: Import Wizard + Logical/File View Design

**Status:** Approved by Dave, ready for planning.

## Problem

Dave has 3D print files scattered unstructured across a hard drive. He wants to:

1. Bring scattered files into the app's library with a logical folder structure he
   defines, physically relocating the files into a clean, app-managed location as
   part of that process (not just linking to them where they sit).
2. Be able to see, at any time, where his files *actually* live on disk — because
   day-to-day reorganizing in the app (dragging a model to a different folder)
   only ever updates a database column today; it never touches the file on disk.
   Logical organization and physical location can therefore drift apart over
   time, and there's currently no way to see that drift.

## Goals

- A one-time "Import from Folder" wizard: point at a raw, previously-unindexed
  directory, manually sort its contents into the existing logical folder
  structure (creating new folders as needed), and have the app physically move
  the files into its managed library storage on confirm.
- A `filePath` column on `models`, giving every model a single source of truth
  for "where does this file currently live on disk" — independent of `folderId`,
  and touched only by actual physical file moves.
- A Logical/File toggle in the sidebar: Logical is today's `folders`-table-driven
  tree (unchanged); File is a read-only tree derived from `filePath`, showing
  real disk structure.

## Non-goals

- No automatic "sync my logical folders to disk" action that moves files to match
  wherever you've dragged them in Logical mode. This spec only makes physical
  moves happen once, explicitly, through the wizard. A general logical→physical
  sync is a plausible future feature but is not built here.
- No retroactive backfill of `filePath` for existing copy-mode uploads that
  predate this feature. They keep their current flat storage location; File mode
  shows them honestly grouped under a single "Uploads" bucket rather than
  inventing structure that was never there.
- No support for repeated/ongoing use of the wizard against the same source
  directory as a sync mechanism — it's a one-time pass. Running it again against
  the same directory is fine (it will simply show whatever's left there), but
  there's no tracking of "already imported from this root."
- The wizard does not attempt to deduplicate against files already in the
  library (e.g. if the same model was already uploaded elsewhere). That's
  out of scope here.

## Architecture

Three pieces, in dependency order:

1. **`filePath` column** (schema + backend) — the shared source of truth both
   other pieces depend on.
2. **Import Wizard** (backend endpoints + frontend flow) — the only thing in this
   spec that writes `filePath` for copy-mode models.
3. **Logical/File sidebar toggle** (frontend) — a read-only consumer of
   `filePath`.

### Why a dedicated wizard instead of extending existing upload/import UI

Considered extending the existing "Upload models" modal to accept a folder
drop. Rejected: the requirements (two full trees side by side for manual
drag-and-drop, plus a distinct review-before-commit step) need more screen
space and different interaction patterns than the existing small modals
provide — building it there would end up reconstructing the same two-pane UI
inside a cramped space, with no real savings.

Considered piggybacking on the watch-folder mechanism (add the raw directory
as a temporary watch folder, let the scanner auto-mirror its structure).
Rejected: watch folders use `storageMode: "reference"` (link in place, never
move the file), which doesn't satisfy "physically relocate scattered files."
It also auto-mirrors structure, which conflicts with Dave's explicit choice of
manual drag-and-drop control over messy source structure that may not be worth
preserving as-is.

## Data Model

### New column: `models.filePath` (TEXT, nullable)

The absolute, current, on-disk path to the model's file, regardless of
`storageMode`:

- **`storageMode: "reference"`**: set equal to `sourcePath` at ingest time (the
  app never moves reference-mode files, so this never needs updating after
  creation — but see the invariant below).
- **`storageMode: "copy"`**:
  - Models ingested by the Import Wizard: the real path under the library's
    managed storage root, mirroring the target logical folder the user placed
    them in (e.g. `<UPLOAD_DIR>/Vehicles/Tanks/hull.stl`).
  - Models from the existing "Upload models" flow (unchanged by this spec):
    `filePath` is set to their current flat storage path
    (`<UPLOAD_DIR>/<existing filename convention>`) for consistency, but this
    location doesn't reflect any logical structure — see Non-goals.

**Invariant:** `filePath` changes **only** when a physical file move actually
happens. Changing a model's `folderId` (today's existing drag-and-drop
reorganize, in Logical mode) never touches `filePath`. This is what makes
`filePath` meaningful as a drift indicator rather than a mirror of `folderId`.

Migration: add the column with a backfill pass that sets `filePath` for
existing rows — `sourcePath` for reference-mode rows, and the derived flat
`UPLOAD_DIR` path for existing copy-mode rows (reusing the same filename-match
logic `models.py` already uses to locate a copy-mode file by id). No rows are
moved on disk by this migration; it only populates the new column to reflect
current, unchanged, reality.

## Import Wizard

### Entry point

Settings gains an "Import from folder..." action, opening a native folder
picker (reusing the existing frozen/non-frozen Browse dialog mechanism already
built for watch folders — see `backend/app/routers/watcher.py`'s
`run_folder_dialog_isolated`).

### Backend: tree peek

`GET /api/import/tree?path=<root>`

Walks `path` recursively (read-only, no DB writes) and returns a JSON tree:

```json
{
  "path": "D:/Prints/Unsorted",
  "folders": [
    {
      "name": "Tank Kit",
      "path": "D:/Prints/Unsorted/Tank Kit",
      "folders": [],
      "files": [
        { "name": "hull.stl", "path": "D:/Prints/Unsorted/Tank Kit/hull.stl", "isModel": true, "size": 4821000 },
        { "name": "photo.jpg", "path": "D:/Prints/Unsorted/Tank Kit/photo.jpg", "isModel": false, "size": 210000 }
      ]
    }
  ],
  "files": [
    { "name": "loose_part.stl", "path": "D:/Prints/Unsorted/loose_part.stl", "isModel": true, "size": 990000 }
  ]
}
```

`isModel` uses the same extension check (`.stl`, `.3mf`, `.step`, `.stp`)
`scan.py` already applies, so a raw directory full of non-print files is
visually distinguishable in the wizard without being treated specially by the
backend beyond that flag.

### Frontend: wizard UI

Two-pane layout:

- **Left — raw disk tree**, fetched once from the tree-peek endpoint when the
  wizard opens for the chosen root. Static for the duration of the wizard
  session (no live re-scan while the wizard is open).
- **Right — logical folder tree**, reusing `Sidebar.tsx`'s existing tree
  rendering (now that its expand/collapse behavior is fixed), plus an inline
  "new folder" action so a destination can be created without leaving the
  wizard.

Dragging a raw folder or loose file from the left pane onto a logical folder
node on the right pane stages a placement in client-side state:
`{ sourcePath, isFolder, targetFolderId }`. Nothing is sent to the backend at
this point.

### Review step

Before any backend write, a summary screen lists every staged placement as
`source → destination`, one row per top-level dragged item (a dragged folder's
contents are summarized under it, not listed file-by-file, to keep this
readable for large folders). Filename collisions against files already in the
destination are detected client-side (the wizard already has the full raw tree
and can fetch the destination folder's current model list) and shown with
their resolved auto-suffixed name (see Collision Handling below), so nothing
here is a surprise after commit.

### Backend: commit

`POST /api/import/commit`

Body: the full list of staged placements from the review step.

For each placement, in order, independently (a failure on one does not abort
the rest — see Safety):

1. Resolve/create the destination folder chain via the existing
   `get_or_create_folder` helper (same one `scan_watch_folder` uses), based on
   the target logical folder's full path.
2. Compute the destination directory under the library's managed storage root,
   mirroring that logical folder path.
3. `shutil.move()` the source (whole folder, or loose file) into that
   destination. `shutil.move()` handles cross-drive moves transparently.
4. For every file in the moved item where `isModel` was true, create a new
   `STLModel` row: `storageMode: "copy"`, `folderId` set to the resolved
   destination folder, `filePath` set to its new real path. Thumbnails are
   only ever generated client-side from a browser `File` object today (see
   `thumbnailGenerator.ts`), which wizard-imported files never pass through —
   same limitation watch-folder-scanned models already have. Wizard-imported
   models get `thumbnail: null`, consistent with that existing behavior; the
   folder-preview "master item" card already handles thumbnail-less models
   gracefully (falls back to the next model in the folder that has one, or a
   plain folder icon).
5. Non-model sibling files move along with their containing folder but are not
   given DB rows, consistent with how the rest of the library already ignores
   unrecognized file types.

Response: per-placement result — `{ sourcePath, status: "ok" | "error", error?: string, modelsCreated: number }` — so the frontend can render the results screen without a second round-trip.

### Results screen + retry

After commit, a results screen lists every placement with its outcome. Failed
placements (their source files remain untouched in the original location,
since a failed `shutil.move()` doesn't partially commit) are kept in wizard
state with a "Retry failed items" action that re-submits just those, without
re-fetching the tree or re-staging the rest of the batch.

### Collision handling

If a moved file's name already exists in its destination directory — whether
because a file already there has that name, or because two different staged
placements in the same batch both land a same-named file in the same
destination — the backend auto-suffixes it (`hull.stl` → `hull_1.stl`,
incrementing until unique) before the move, processing the batch in a fixed
order so repeated commits (e.g. a retry) produce the same result. The review
step surfaces this ahead of time using the same logic client-side, checking
both existing destination contents and the rest of the staged batch, so the
resolved name is never a surprise at commit time.

## Logical/File Sidebar Toggle

A **Logical | File** toggle at the top of the left column, above the existing
folder tree. Defaults to Logical.

- **Logical** (default): today's behavior, entirely unchanged — `folders`
  table driven, full create/rename/delete/drag-to-move.
- **File**: the tree is derived by grouping all models by `filePath`'s
  directory segments instead of `folderId`. There is no backing `folders` row
  for these nodes, so create/rename/delete and drag-to-move are not available
  in this mode — it's a read-only lens. Clicking a node filters the main grid
  the same way Logical mode does today, matching by path-prefix instead of
  `folderId` equality. Models whose `filePath` sits directly in the flat
  pre-feature upload location (see Non-goals) group under a single "Uploads"
  bucket rather than fabricating structure.

## Safety & Error Handling

- **Nothing touches disk before Confirm.** Tree-peek and all drag-and-drop
  staging are read-only/client-side. The only write is the single `commit`
  call (and its retry) at the end.
- **Per-item isolation.** Each placement's move is attempted independently;
  one failure (locked file, permission denied, source vanished since staging)
  is caught and reported without aborting sibling placements — the same
  failure-tolerant pattern already used in `scan_watch_folder`.
- **Deterministic, visible collision handling.** Auto-suffixed names are shown
  in the review step before commit, not discovered afterward.
- **No partial moves.** A failed `shutil.move()` leaves the source file
  exactly where it was; the app never deletes a source file without the move
  having actually succeeded.
- **Retry without re-staging.** Failures don't require restarting the wizard.

## Testing

**Backend (pytest, following existing `backend/tests/` conventions):**

- Tree-peek: nested directories, mixed model/non-model files, empty
  directories, `isModel` flagging correctness.
- Commit: successful moves create `STLModel` rows with correct `folderId`,
  `storageMode: "copy"`, and `filePath`; new folders are created via
  `get_or_create_folder` without duplicating existing ones; collision
  auto-suffixing; a simulated per-item failure (e.g. a locked file) doesn't
  abort sibling items in the same batch; failed items are retryable.
- Migration: backfill sets `filePath` correctly for both existing
  reference-mode and copy-mode rows without moving anything on disk.

**Frontend:** no automated suite exists in this project (consistent with the
rest of the codebase) — verified manually via the packaged build, using a
fixture directory with nested subfolders, mixed file types, and a deliberate
name collision to exercise the wizard end-to-end, plus the Logical/File
toggle against a library with some intentionally drifted (logically-moved but
not physically-moved) models.
