# In-Place File References for Watched Folders — Design

## Goal

Watched folders currently copy every file they find into `dev-data/uploads`
(the app-managed `UPLOAD_DIR`), recording the original path only as
metadata. This design changes watched folders to reference files in their
existing location instead — no copy, no managed duplicate — while manual
upload and drive-scan keep working exactly as they do today.

## Background

A watch folder was configured at `D:/Dropbox/3D Print Files` (24GB, 728
files). Because the app always copies on ingest, and an unrelated scheduler
bug (see `app/scheduler.py` — fixed separately, `asyncio.to_thread`) let
several backend restarts each rescan from scratch before finishing, the
watcher copied many files multiple times: 2864 model rows for 728 distinct
files, cleaned up back down to 2148 via a one-off dedupe pass. The
underlying problem — copying files the user already keeps organized in a
real folder — is what this design removes.

No Dropbox-specific code is needed anywhere in this design. Dropbox (or
OneDrive, or any other sync tool) syncs a folder because it's watching that
folder, not because STLVault does anything special. The app just reads and
writes ordinary local paths; if a watched folder happens to live inside a
Dropbox folder, Dropbox's own client handles it, entirely outside the app's
awareness.

## Scope

Reference-mode applies to **watched folders only**. Manual upload has no
pre-existing file to reference (it's a browser upload) and drive-scan
remains a one-off copy of hand-picked files — both are unchanged by this
design.

## Data model

One new column:

```sql
ALTER TABLE models ADD COLUMN storageMode TEXT NOT NULL DEFAULT 'copy'
```

Added via the same `ALTER TABLE ... ADD COLUMN` / `OperationalError`-swallow
pattern already used in `app/db.py` for `sourcePath` and friends. Every
existing row and every future manual-upload/drive-scan row is `'copy'`;
only rows created by a watch-folder scan are `'reference'`.

## Ingestion path

`app/services/ingestion.py::ingest_file()` gains one new parameter:

```python
def ingest_file(
    source_path: str,
    folder_id: str,
    original_filename: str,
    tags: Optional[List[str]] = None,
    thumbnail: Optional[str] = None,
    move: bool = False,
    record_source: bool = False,
    pickup_sidecar_notes: bool = False,
    reference_only: bool = False,   # new
) -> dict:
```

When `reference_only=True`:
- The `shutil.copyfile` / `shutil.move` step is skipped entirely.
- `sourcePath` is set to `source_path` — that path *is* the file's
  permanent location, not a hint for dedup.
- `size` is computed via `os.path.getsize(source_path)` directly.
- The row is inserted with `storageMode='reference'`.
- `record_source` is implied — reference mode always records source path,
  since there is no other location for the file.

`pickup_sidecar_notes` is unaffected: it reads a small nearby `.txt`/`.pdf`
file, not the model file itself, so it works the same in both modes.

`app/services/scan.py::scan_watch_folder()` changes its one `ingest_file`
call site to pass `reference_only=True`. This applies to all watch folders
unconditionally — there is no per-folder copy/reference toggle. (Nothing
stops adding one later if it's ever needed, but it isn't part of this
design.)

`scan_watch_folder`'s existing dedup logic — skip files whose path is
already in some model's `sourcePath` — needs no changes; it works
identically whether the matched row is a copy or a reference.

Drive-scan (`app/routers/watcher.py::drive_scan`) and manual upload
(`app/routers/models.py::upload_model`) keep calling `ingest_file()` with
`reference_only` left at its default `False`.

## Serving and missing-file detection

**Listing** (`GET /api/models`, `GET /api/models/{id}`): reference-mode
rows get a computed field, `missing: bool`, evaluated fresh on every
request as `not os.path.exists(sourcePath)` — never stored, never cached,
so it can't go stale. Copy-mode rows always report `missing: false`.

**Download** (`GET /api/models/{id}/download`): branches on
`storageMode`.
- `'copy'`: unchanged — existing `UPLOAD_DIR` filename-prefix lookup.
- `'reference'`: `FileResponse(sourcePath, ...)` directly. If the path
  doesn't exist, return `404` with
  `"File not found at <path> — it may have been moved or deleted outside STLVault."`
  instead of today's generic message.

If a referenced file is moved, renamed, or deleted outside the app, the
library entry stays and simply shows as missing — nothing is auto-removed.
Detecting a move to a *new* path (auto-relink) is out of scope; a moved
file just looks like "old path missing" + "new path not yet scanned."

## Delete flow

`DELETE /api/models/{id}` gains an optional field: `deleteFile: bool =
False`.

- **Copy-mode**: unchanged. Always deletes the managed copy from
  `UPLOAD_DIR` and the DB row — no prompt, since it was never the user's
  original file.
- **Reference-mode**: `deleteFile=False` (the default) removes only the
  library row. `deleteFile=True` also `os.remove()`s the real file at
  `sourcePath`, wrapped in try/except in case it's already gone. The
  frontend shows a two-button confirm — "Remove from library" / "Also
  delete file from disk" — only when the model being deleted is
  reference-mode; copy-mode delete keeps its current single-step
  confirmation.

**Bulk-delete** (`POST /api/models/bulk-delete`): reference-mode items in
the selection are always library-only removal (real file untouched);
copy-mode items are fully deleted as today. No per-item choice inside a
bulk action — deliberately deleting real files stays a one-at-a-time
action.

## Frontend

- `Model` TypeScript type gains `storageMode: 'copy' | 'reference'` and
  `missing: boolean`.
- A small badge/icon on reference-mode models (e.g. a link icon), and a
  warning icon when `missing: true`.
- Delete confirmation branches as described above.
- No new pages or views — the library grid stays unified.

## Migrating the existing 728 copies

A one-off script (not a permanent feature or endpoint), run once as part
of shipping this change:

For every `models` row whose `sourcePath` falls under the Dropbox watch
folder's root and is currently `storageMode='copy'`:
1. Verify the file still exists at `sourcePath`. If not, skip this row
   (leave it as `'copy'`) and log it — never delete a managed copy that
   would become the *only* remaining copy of that file.
2. Set `storageMode='reference'`.
3. Delete the now-redundant managed copy from `dev-data/uploads`.

This reclaims the disk space from tonight's incident and brings that one
folder fully onto the new model without touching rows from any other
source.

## Testing plan

Backend, TDD, extending the existing `pytest` suite:
- `ingest_file(reference_only=True)`: row gets `storageMode='reference'`,
  correct `sourcePath`/`size`, and — explicitly asserted — no file is
  created under `UPLOAD_DIR`.
- `scan_watch_folder`: after a scan, ingested models are reference-mode
  and `UPLOAD_DIR` gained no new files.
- List/get endpoints: `missing` is `False` for a present file, `True`
  once the file is deleted out from under it.
- `download_model`: serves reference-mode files from `sourcePath`; returns
  the descriptive 404 when the path is gone.
- Delete: default (`deleteFile=False`) leaves the file; `deleteFile=True`
  removes it; copy-mode delete ignores the flag and behaves as today.
- Bulk-delete: reference-mode items removed from the DB only; copy-mode
  items still fully deleted.
- Migration script: run against a small synthetic set (temp dir standing
  in for the watch folder, a couple of pre-existing `'copy'` rows, one row
  whose file is deliberately missing) — confirms it flips mode and deletes
  copies only when the source is confirmed present, and skips the missing
  one.

Frontend: no new automated tests beyond the existing type-check; a manual
live-browser verification pass confirming the missing-file badge and the
two-button delete confirm render correctly, following this project's
established discipline of live-verifying UI changes rather than trusting
type-checking alone.

## Explicitly out of scope

- Per-watch-folder copy/reference toggle (everything watched is
  reference-mode).
- Auto-detecting a moved/renamed file and relinking it.
- Auto-removing library entries for files that go missing.
- Replacing a reference-mode model's file via the existing re-upload
  endpoint — that endpoint continues to assume a managed copy exists and
  is left as copy-mode-only; the gap is noted here rather than solved.
- Any Dropbox-specific detection or API integration.
