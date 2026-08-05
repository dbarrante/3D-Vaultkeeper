# File View: New Folder Creation Design

**Status:** Approved by Dave, ready for planning.

## Problem

File view's right-click menu (shipped in the previous feature, see
`2026-08-04-file-view-write-operations-design.md`) supports Rename, Move, and
Delete on folder nodes, but not creating a new one. Dave wants to be able to
start organizing directly from File view — right-click a folder and add a
subfolder, or start a brand-new top-level folder — the same way he can in a
normal file browser.

This is a genuinely new kind of operation for this menu: every existing
folder-level action (rename, move, delete) acts on a folder that already has
at least one model under it, which is how File-mode folder nodes come to
exist in the first place — they're derived entirely by grouping models'
`filePath` values (see `fileViewSegments` in `frontend/types.ts`). A
brand-new folder has no models in it yet, so with today's tree-building logic
it would be invisible the instant it was created.

## Goals

- Right-click **New Folder** on any real File-mode folder node, creating a
  child folder inside it.
- **New Folder** also available from the synthetic "Uploads" bucket node
  (today's flat pre-organized files) — creates a new top-level folder under
  the managed library root, since Uploads has no single real directory to
  nest into.
- A standalone **New Folder** control (not tied to right-clicking anything)
  for creating a top-level folder from scratch.
- A newly-created empty folder stays visible in the File-mode tree — and
  stays visible even if everything is later moved back out of it — until
  it's explicitly deleted. It behaves like a real folder in a real file
  browser, not like a lens that only shows where files already are.
- Naming via `window.prompt`, matching how Rename already works in this
  menu — no new interaction pattern introduced just for this.

## Non-goals

- No creation under a watch folder's root from the Uploads-bucket trigger or
  the standalone top-level control. There's no single unambiguous "root" to
  default to when multiple watch folders are configured, so top-level
  creation is scoped to the one root that's always unambiguous: the managed
  library (`UPLOAD_DIR`). Creating a new folder *inside* a watch folder still
  works fine — nest it by right-clicking an existing folder that's already
  under that watch root.
- No folder-level Copy — unchanged from the previous feature's scope.
- No change to how Logical mode creates folders — this is File-mode only.

## Architecture

Everything File-mode folder nodes need beyond "grouped from models' real
locations" is now: a small table of real paths the app should always show,
independent of whether they currently hold any models.

**New table**, `backend/app/db.py`:

```sql
CREATE TABLE IF NOT EXISTS file_view_tracked_folders (
    path TEXT PRIMARY KEY
)
```

A row means "this real directory should always appear in the File-mode tree,
even with zero models under it." Rows are added on creation, rewritten on
rename/move (mirroring exactly how model rows' `filePath`/`sourcePath` are
already rewritten by `rewrite_affected_paths`), and removed on delete. A
tracked folder's row is **never pruned automatically** when it gains its
first model — that's what makes "move everything out, folder stays visible"
work correctly, matching a real file browser rather than reverting to
today's "empty means invisible" behavior the moment it's empty again.

## Backend

**`POST /api/file-view/folder`** — body `{"parentPath": str, "name": str}`.

1. Validate `parentPath` resolves inside `UPLOAD_DIR` or some configured
   watch root, reusing the existing `resolve_storage_mode_for_path` check
   from `file_view_ops.py` (same containment guarantee every other write
   endpoint already has — this endpoint is not exempt just because it
   creates rather than mutates).
2. Sanitize `name` via the existing `sanitize_path_segment` from
   `backend/app/services/import_wizard.py` — same illegal-character,
   reserved-Windows-name, and length rules already used everywhere else a
   folder name becomes a real path segment. No new sanitization logic.
3. 409 if `parentPath / sanitized_name` already exists.
4. Create the real directory (`mkdir`).
5. Insert `(path)` into `file_view_tracked_folders`.
6. Return the new folder's real absolute path.

**`rename_folder` / `move_folder`** (existing, `file_view.py`): extend the
path-rewrite step they already run for model rows to also rewrite any
`file_view_tracked_folders` row whose path is at or under the
rename/move source, using the same prefix-replacement logic
`rewrite_affected_paths` already applies — same function, one more table.

**`delete_folder`** (existing): extend its existing cleanup to also delete
any `file_view_tracked_folders` rows at or under the deleted path, alongside
the model rows it already removes there.

## Frontend

- `Sidebar.tsx`'s `fileTree` build (already walks every model's `filePath`
  to construct nodes) additionally walks every tracked folder's path the
  same positional way, creating any missing intermediate nodes so a tracked
  folder shows its full chain in the tree even when nothing else under that
  chain has any models.
- Folder context menu gains a **New Folder** item, appearing:
  - On any real folder node — prompts for a name, calls the create endpoint
    with that node's real path as `parentPath`.
  - On the Uploads bucket node — same prompt, `parentPath` is `UPLOAD_DIR`.
    The Uploads bucket's existing guard (no Rename/Move/Delete, since it
    isn't a real single directory) narrows from "no menu at all" to "menu
    with only New Folder."
- A standalone **New Folder** button, visible only in File mode, placed near
  the Logical/File toggle — same prompt, same `UPLOAD_DIR`-rooted call as
  the Uploads-bucket trigger. Both entry points share one handler.
- After a successful create, refetch (same `fetchData`/`onFileViewMutated`
  pattern the rest of File view's write operations already use) so the new
  node appears immediately.

## Safety & Error Handling

- Destination containment is enforced server-side on the create endpoint,
  identical in spirit to every other File-view write endpoint — a crafted
  `parentPath` outside `UPLOAD_DIR` and every watch root is rejected before
  anything touches disk.
- Name sanitization reuses the existing, already-tested helper rather than
  introducing a second implementation of the same rules.
- A name collision with an existing directory is a clean 409, not a
  silent overwrite or a merge.

## Testing

**Backend (pytest, `tmp_path`-based, following existing conventions):**

- Create under a real copy-mode parent — directory exists on disk, tracked
  row inserted, response returns the new path.
- Create under a real watch-folder-rooted parent — same, reference-mode
  root.
- Create with a `parentPath` outside every allowed root — rejected, nothing
  created.
- Create where the target already exists — 409, nothing overwritten.
- Rename/move a folder that has a tracked (empty) child — the tracked row's
  path updates to match, still resolves to a real, existing directory
  afterward.
- Delete a folder containing a tracked (empty) subfolder — the tracked row
  is removed along with the directory.
- A tracked folder that later receives a model, then has that model moved
  back out, is still present in a subsequent tree fetch (i.e., the tracked
  row was never pruned).

**Frontend:** manual verification via the packaged build, same as every
other File-view write operation this session — create a nested folder,
create one from the Uploads bucket, create one from the standalone button,
confirm all three appear immediately and survive being emptied out again.
