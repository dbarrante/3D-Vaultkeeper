# Watch-Folder Structure Mirroring + Master-Item Cards Design

**Goal:** When a watched folder has subdirectories (e.g. one folder per print,
each holding several part files), the scanner should mirror that structure
into library subfolders instead of flattening every file into one target
folder. A folder that directly contains files then displays as a
"master item" card (representative thumbnail + part count) rather than a
plain folder icon, so a multi-part print reads as one item, not a folder you
have to open to understand.

**Background:** Investigated live — the user reported "no folders/files
showing" after adding a watch folder. The real cause: `scan_watch_folder`
(`backend/app/services/scan.py`) uses `os.walk()` to recurse through every
subdirectory of the watched root, but ingests every file found — regardless
of which subdirectory it actually lives in — into the single library folder
the user picked when adding the watch folder. Confirmed against the user's
live database: hundreds of files, from what was clearly a nested source
folder, all landed flat in one "Unsorted" folder with zero subfolders
created. The existing folder-tile rendering in `ModelList.tsx` (lines
505–560) already works correctly for folders that do have subfolders — this
was never a rendering bug, only a scanning gap.

## Architecture

**Backend (`backend/app/services/scan.py`):**

- `find_new_files` already returns full paths via `os.walk`; `scan_watch_folder`
  will additionally compute each file's directory path relative to the
  watched root (`Path(dirpath).relative_to(root).parts`).
- A new `get_or_create_folder(name: str, parent_id: str | None) -> str`
  helper looks up an existing folder by `(name, parentId)` before creating
  one — re-scans (which re-check `should_run_now` every tick) and any
  manually-created folder with a matching name never produce duplicates.
- For a file whose relative directory has N parts (e.g. `PrintA/supports`),
  walk that chain under the watch folder's target `folderId`, calling
  `get_or_create_folder` once per level, and ingest the file into the
  resulting leaf folder. A file sitting directly in the watched root
  (zero relative parts) still ingests straight into the target folder,
  unchanged from today.
- `scan_downloads_folder` (the Inbox flow) is untouched — Inbox items are
  filed one at a time by explicit user action, not bulk-ingested, so
  mirroring doesn't apply there.
- Scope is going-forward only: already-ingested files are skipped by the
  existing `sourcePath` dedup check in `scan_watch_folder`, so nothing
  already in the library gets moved or duplicated. No migration of the
  existing flat "Unsorted" contents.

**Frontend (`ModelList.tsx` + `App.tsx`):**

- `App.tsx` gains a `folderPreviews` memo — `Record<folderId, {count:
  number, thumbnail: string | null}>` — built the same way `Sidebar.tsx`'s
  existing `folderCounts` memo already counts direct children from the full
  `models` array, extended to also track the first-added model's thumbnail
  per folder. Passed to `ModelList` as a new prop alongside the existing
  `folders`/`models`.
- In `ModelList.tsx`'s folder-tile rendering, a folder with a nonzero count
  in `folderPreviews` shows that thumbnail (or a generic file icon if the
  representative model has none) and a "`N` parts" badge instead of the
  current `Avatar` + `FolderIcon` + "Folder" label. A folder with no direct
  children (only subfolders) keeps today's plain folder-icon look.
- Clicking a master-item card behaves exactly like clicking a folder does
  today — `onNavigateFolder` — no new detail view. The card is a restyled
  folder tile, not a new entity.

## Data Model

No schema changes. A "master item" is not a new table or concept — it's a
folder that happens to contain files directly, using a folder's existing
`parentId` hierarchy. This keeps drag-and-drop, rename, delete, and move
all working unmodified, since they already operate on folders.

## Testing

- Backend: new tests in `backend/tests/` covering `get_or_create_folder`
  (creates once, reuses on second call with the same name/parent) and
  `scan_watch_folder` with a nested fixture tree (root file + one and two
  levels of subdirectories) asserting the resulting folder hierarchy and
  each file's `folderId`. Existing `scan_watch_folder` tests (flat root,
  dedup-by-sourcePath) must keep passing unchanged.
- Frontend: no existing automated test suite for components (confirmed
  earlier this session — `frontend/package.json` has no test script) —
  verify manually via the packaged desktop build, consistent with how
  every other frontend change this session was verified.

## Out of Scope

- Retroactive reorganization of already-flat existing library data.
- A distinct "master item" data type separate from folders.
- Changes to the Inbox/Downloads scanning flow.
