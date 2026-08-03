# In-Place File References for Watched Folders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Watched folders reference files where they already live on disk instead of copying them into `dev-data/uploads`, while manual upload and drive-scan keep copying exactly as they do today. Migrate the ~728 already-copied Dropbox-folder entries onto the new model and reclaim their disk space.

**Architecture:** One additive `models.storageMode` column (`'copy'` | `'reference'`, default `'copy'`). `ingest_file()` gains a `reference_only` flag that skips the copy/move step and records the source path as the file's permanent location. `scan_watch_folder()` is the only caller that sets it. Everything that reads a model (list/get, download, delete, bulk-delete) branches on `storageMode` at the single existing `row_to_model()` seam plus the two file-serving endpoints. A one-off script migrates the existing Dropbox-folder copies.

**Tech Stack:** Same as the rest of the backend — Python 3.9-syntax-compatible, FastAPI, raw `sqlite3` via `app/db.py`, pytest. Frontend: React 19 + TypeScript, existing `STLModel` type and delete-confirmation modal in `App.tsx`. No new dependencies anywhere.

## Global Constraints

- Schema changes stay additive-only (same rule as every prior phase in this repo) — one new nullable-with-default column, no destructive migrations of existing columns.
- `ingest_file()` remains the single ingestion code path (per its own docstring) — extend its signature, do not add a parallel function.
- Python 3.9 syntax (`Optional`/`Union`, not `X | Y`) in all backend code.
- Reference-mode applies **only** to watched folders (`scan_watch_folder`). Manual upload (`upload_model`) and drive-scan (`drive_scan`) are unchanged and keep calling `ingest_file()` with `reference_only` left at its default `False`.
- No Dropbox-specific code anywhere — the app only ever reads/writes ordinary local paths.
- Every backend task must leave `cd backend && pytest` fully green before moving to the next task.

---

### Task 1: Schema — `models.storageMode` column

**Files:**
- Modify: `backend/app/db.py` (`init_db`, the `ALTER TABLE models ADD COLUMN` loop)
- Test: `backend/tests/test_schema_migration.py`

**Interfaces:**
- Produces: `models.storageMode TEXT NOT NULL DEFAULT 'copy'` — every row, existing or new, is `'copy'` unless explicitly inserted otherwise.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_schema_migration.py`:

```python
def test_models_table_has_storage_mode_column_defaulting_to_copy(client):
    from app.db import DB_PATH, get_db_conn

    conn = sqlite3.connect(DB_PATH)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(models)")}
    conn.close()
    assert "storageMode" in columns

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("m1", "x.stl", "1", "/api/models/m1/download", 10, 0, "[]", "", None),
    )
    conn.commit()
    row = conn.execute("SELECT storageMode FROM models WHERE id='m1'").fetchone()
    conn.close()
    assert row["storageMode"] == "copy"
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && pytest tests/test_schema_migration.py -v`
Expected: FAIL — `storageMode` column doesn't exist yet (`sqlite3.OperationalError: table models has no column named storageMode`).

- [ ] **Step 3: Add the column**

In `backend/app/db.py`, inside `init_db`, extend the existing column-migration loop:

```python
    for column, coltype in [
        ("author", "TEXT"),
        ("sourceUrl", "TEXT"),
        ("category", "TEXT"),
        ("colorCount", "INTEGER"),
        ("sliceSettings", "TEXT"),
        ("sourcePath", "TEXT"),
        ("storageMode", "TEXT NOT NULL DEFAULT 'copy'"),
    ]:
```

- [ ] **Step 4: Run to confirm it passes**

Run: `cd backend && pytest tests/test_schema_migration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_schema_migration.py
git commit -m "feat: add models.storageMode column for reference-mode ingestion"
```

---

### Task 2: `ingest_file(reference_only=...)`

**Files:**
- Modify: `backend/app/services/ingestion.py`
- Test: `backend/tests/test_ingestion.py`

**Interfaces:**
- Consumes: `models.storageMode` column from Task 1.
- Produces: `ingest_file(..., reference_only: bool = False) -> dict`. When `True`, no file is copied/moved; `sourcePath` is always the given `source_path`; the inserted row has `storageMode='reference'`. Task 3 calls this with `reference_only=True`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_ingestion.py`:

```python
def test_ingest_file_with_reference_only_does_not_copy(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR

    source = tmp_path / "watched" / "model.stl"
    source.parent.mkdir()
    source.write_bytes(b"solid watched endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="model.stl", reference_only=True)

    assert model["name"] == "model.stl"
    assert not any(f.startswith(model["id"]) for f in os.listdir(UPLOAD_DIR))
    assert source.exists()


def test_ingest_file_with_reference_only_records_source_path_and_storage_mode(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    source = tmp_path / "model.stl"
    source.write_bytes(b"solid endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="model.stl", reference_only=True)

    conn = get_db_conn()
    row = conn.execute("SELECT sourcePath, storageMode FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row["sourcePath"] == str(source)
    assert row["storageMode"] == "reference"


def test_ingest_file_default_copy_mode_unaffected(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    source = tmp_path / "model.stl"
    source.write_bytes(b"solid endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="model.stl")

    conn = get_db_conn()
    row = conn.execute("SELECT storageMode FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row["storageMode"] == "copy"
```

- [ ] **Step 2: Run to confirm the new tests fail**

Run: `cd backend && pytest tests/test_ingestion.py -v`
Expected: FAIL — `ingest_file() got an unexpected keyword argument 'reference_only'`.

- [ ] **Step 3: Implement `reference_only`**

Replace the body of `ingest_file` in `backend/app/services/ingestion.py`:

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
    reference_only: bool = False,
) -> dict:
    """Put a file already on disk into the library and register it as a model.
    Shared by manual upload, the folder watcher (Phase 1), and the acquisition
    queue drain worker (Phase 5) so there is exactly one ingestion code path.

    move=False (default) copies source_path and leaves it in place — the right
    choice for the folder watcher (#2/#3): the user is watching a real folder
    they still browse elsewhere, so relocating their file out of it on ingest
    would be destructive and surprising. move=True renames instead of copying —
    for callers whose source_path is a disposable scratch file they made solely
    to hand off here (upload_model; later, the acquisition drain worker's
    downloaded-to-a-temp-location files), a same-filesystem move is a single
    filesystem rename with no data copy at all.

    reference_only=True skips copying or moving entirely and treats
    source_path itself as the file's permanent location — used only by the
    watch-folder scanner, so a folder you already keep organized never gets
    duplicated into the app's managed storage. record_source is implied
    (there is nowhere else sourcePath could point), and the row is stored
    with storageMode='reference' instead of 'copy'.

    record_source=True additionally persists source_path into models.sourcePath,
    so a later scan of the same folder can tell this file was already ingested
    and skip it. Only meaningful with move=False (the watcher's case) — recording
    a path that ingest_file itself just deleted via move=True would record a
    path that no longer points at anything.

    pickup_sidecar_notes=True (#5) looks for a same-basename .txt or .pdf next
    to source_path — moving or copying the model file never touches that
    sibling, so lookup order relative to the move/copy below doesn't matter —
    and uses its text as the model's initial description. Off by default:
    upload_model's source_path is a disposable temp file with no meaningful
    siblings, so there's nothing useful to look for there.
    """
    mid = str(uuid.uuid4())
    ext = os.path.splitext(original_filename)[1] or ".stl"

    description = ""
    if pickup_sidecar_notes:
        notes = find_sidecar_notes(source_path)
        if notes:
            description = notes

    if reference_only:
        size = os.path.getsize(source_path)
    else:
        dest_path = os.path.join(UPLOAD_DIR, f"{mid}{ext}")
        if move:
            shutil.move(source_path, dest_path)
        else:
            shutil.copyfile(source_path, dest_path)
        size = os.path.getsize(dest_path)

    model = {
        "id": mid,
        "name": original_filename,
        "folderId": folder_id if folder_id != "all" else "1",
        "url": f"/api/models/{mid}/download",
        "size": size,
        "dateAdded": now_ms(),
        "tags": tags or [],
        "description": description,
        "thumbnail": thumbnail,
    }
    storage_mode = "reference" if reference_only else "copy"
    source_to_record = source_path if (record_source or reference_only) else None

    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,sourcePath,storageMode) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            model["id"], model["name"], model["folderId"], model["url"], model["size"],
            model["dateAdded"], json.dumps(model["tags"]), model["description"], model["thumbnail"],
            source_to_record, storage_mode,
        ),
    )
    conn.commit()
    conn.close()
    return model
```

- [ ] **Step 4: Run full backend suite to confirm everything passes**

Run: `cd backend && pytest -v`
Expected: PASS — including the pre-existing `test_ingest_file_*` tests (copy/move behavior unchanged) and everything in `test_scan.py`/`test_scheduler.py` that calls `ingest_file` without `reference_only` (defaults to `False`, unaffected).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ingestion.py backend/tests/test_ingestion.py
git commit -m "feat: add reference_only mode to ingest_file, skipping copy/move"
```

---

### Task 3: `scan_watch_folder` references instead of copying

**Files:**
- Modify: `backend/app/services/scan.py:39-45` (the `ingest_file` call inside `scan_watch_folder`)
- Test: `backend/tests/test_scan.py`

**Interfaces:**
- Consumes: `ingest_file(..., reference_only=True)` from Task 2.
- Produces: no new interface — `scan_watch_folder(watch_folder_row: dict) -> int` keeps its existing signature and return value (count ingested).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_scan.py`:

```python
def test_scan_watch_folder_references_instead_of_copying(client, tmp_path):
    from app.services.scan import scan_watch_folder
    from app.db import get_db_conn, UPLOAD_DIR

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    (watched_dir / "a.stl").write_bytes(b"solid a endsolid")

    row = {"id": "wf1", "path": str(watched_dir), "folderId": "1"}
    ingested = scan_watch_folder(row)

    assert ingested == 1
    conn = get_db_conn()
    model = conn.execute("SELECT storageMode, sourcePath FROM models WHERE folderId='1'").fetchone()
    conn.close()
    assert model["storageMode"] == "reference"
    assert model["sourcePath"] == str(watched_dir / "a.stl")
    assert os.listdir(UPLOAD_DIR) == []
```

Add `import os` at the top of `backend/tests/test_scan.py` if it isn't already imported.

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && pytest tests/test_scan.py::test_scan_watch_folder_references_instead_of_copying -v`
Expected: FAIL — `storageMode` is `'copy'` and `UPLOAD_DIR` contains a copied file.

- [ ] **Step 3: Pass `reference_only=True`**

In `backend/app/services/scan.py`, in `scan_watch_folder`, change the `ingest_file` call:

```python
            ingest_file(
                str(file_path),
                folder_id=watch_folder_row["folderId"],
                original_filename=file_path.name,
                record_source=True,
                pickup_sidecar_notes=True,
                reference_only=True,
            )
```

- [ ] **Step 4: Run the full scan + scheduler test files to confirm nothing broke**

Run: `cd backend && pytest tests/test_scan.py tests/test_scheduler.py -v`
Expected: PASS — the pre-existing tests in these files only assert on `name`, `sourcePath`, `folderId`, and `lastScanAt`, none of which change; they never asserted a file was copied into `UPLOAD_DIR`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scan.py backend/tests/test_scan.py
git commit -m "feat: watch-folder scans reference files in place instead of copying"
```

---

### Task 4: `row_to_model` computes `missing` for reference-mode rows

**Files:**
- Modify: `backend/app/db.py` (`row_to_model`)
- Test: `backend/tests/test_models_core.py`

**Interfaces:**
- Consumes: `models.storageMode`/`models.sourcePath` columns.
- Produces: every API response built via `row_to_model()` (list, PATCH, and internally `get_model_info` used by download/delete) gains `"storageMode": str` and `"missing": bool`. `missing` is always `False` for `storageMode='copy'`, and computed fresh via `os.path.exists()` for `storageMode='reference'` — never stored, never stale.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_models_core.py`:

```python
def test_reference_model_reports_missing_false_when_file_present(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "present.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="present.stl", reference_only=True)

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    found = next(m for m in listed if m["id"] == model["id"])
    assert found["storageMode"] == "reference"
    assert found["missing"] is False


def test_reference_model_reports_missing_true_when_file_deleted(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "gone.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="gone.stl", reference_only=True)
    os.remove(source)

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    found = next(m for m in listed if m["id"] == model["id"])
    assert found["missing"] is True


def test_copy_mode_model_never_reports_missing(client):
    created = _upload(client)
    assert created["storageMode"] == "copy"
    assert created["missing"] is False
```

Add `import os` at the top of `backend/tests/test_models_core.py` if it isn't already imported.

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && pytest tests/test_models_core.py -v`
Expected: FAIL — `KeyError: 'storageMode'` / `KeyError: 'missing'` (neither field exists in the response yet).

- [ ] **Step 3: Add the fields in `row_to_model`**

In `backend/app/db.py`, replace `row_to_model`:

```python
def row_to_model(row: sqlite3.Row) -> Dict[str, Any]:
    tags = []
    if row["tags"]:
        try:
            tags = json.loads(row["tags"])
        except Exception:
            tags = []
    storage_mode = row["storageMode"] if "storageMode" in row.keys() else "copy"
    source_path = row["sourcePath"] if "sourcePath" in row.keys() else None
    missing = storage_mode == "reference" and bool(source_path) and not os.path.exists(source_path)
    return {
        "id": row["id"],
        "name": row["name"],
        "folderId": row["folderId"],
        "url": row["url"],
        "size": row["size"],
        "dateAdded": row["dateAdded"],
        "tags": tags,
        "description": row["description"] or "",
        "thumbnail": row["thumbnail"],
        "manual": row["manual"] if "manual" in row.keys() else None,
        "author": row["author"] if "author" in row.keys() else None,
        "sourceUrl": row["sourceUrl"] if "sourceUrl" in row.keys() else None,
        "category": row["category"] if "category" in row.keys() else None,
        "colorCount": row["colorCount"] if "colorCount" in row.keys() else None,
        "sliceSettings": row["sliceSettings"] if "sliceSettings" in row.keys() else None,
        "sourcePath": source_path,
        "storageMode": storage_mode,
        "missing": missing,
    }
```

(`os` is already imported at the top of `app/db.py`.)

- [ ] **Step 4: Run to confirm it passes**

Run: `cd backend && pytest tests/test_models_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_models_core.py
git commit -m "feat: compute missing flag for reference-mode models on every read"
```

---

### Task 5: `download_model` serves reference-mode files from `sourcePath`

**Files:**
- Modify: `backend/app/routers/models.py:119-129` (`download_model`)
- Test: `backend/tests/test_models_core.py`

**Interfaces:**
- Consumes: `storageMode`/`sourcePath`/`missing` fields from Task 4 (via `get_model_info`, which calls `row_to_model`).
- Produces: no new interface — `GET /api/models/{model_id}/download` keeps its existing route signature.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_models_core.py`:

```python
def test_download_reference_model_serves_from_source_path(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "download_me.stl"
    source.write_bytes(b"solid reference content endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="download_me.stl", reference_only=True)

    response = client.get(f"/api/models/{model['id']}/download")
    assert response.status_code == 200
    assert response.content == b"solid reference content endsolid"


def test_download_reference_model_returns_descriptive_404_when_missing(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "vanish.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="vanish.stl", reference_only=True)
    os.remove(source)

    response = client.get(f"/api/models/{model['id']}/download")
    assert response.status_code == 404
    assert "moved or deleted" in response.json()["detail"]
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && pytest tests/test_models_core.py -v`
Expected: FAIL — `download_model` doesn't know about `storageMode`; it looks for a file in `UPLOAD_DIR` that was never created, so both tests get a generic `404`/wrong content.

- [ ] **Step 3: Branch on `storageMode`**

In `backend/app/routers/models.py`, replace `download_model`:

```python
@router.get("/api/models/{model_id}/download")
def download_model(model_id: str):
    m_info = get_model_info(model_id)
    if m_info["storageMode"] == "reference":
        source_path = m_info["sourcePath"]
        if source_path and os.path.exists(source_path):
            return FileResponse(
                source_path,
                media_type="application/octet-stream",
                filename=m_info["name"],
            )
        raise HTTPException(
            status_code=404,
            detail=f"File not found at {source_path} — it may have been moved or deleted outside STLVault.",
        )
    for fname in os.listdir(UPLOAD_DIR):
        if fname.startswith(model_id):
            return FileResponse(
                os.path.join(UPLOAD_DIR, fname),
                media_type="application/octet-stream",
                filename=m_info["name"],
            )
    raise HTTPException(status_code=404, detail="File not found")
```

- [ ] **Step 4: Run to confirm it passes**

Run: `cd backend && pytest tests/test_models_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/models.py backend/tests/test_models_core.py
git commit -m "feat: serve reference-mode downloads from sourcePath with descriptive 404"
```

---

### Task 6: `delete_model` gains `deleteFile` for reference-mode models

**Files:**
- Modify: `backend/app/routers/models.py:93-116` (`delete_model`)
- Test: `backend/tests/test_models_core.py`

**Interfaces:**
- Consumes: `storageMode`/`sourcePath` columns directly from the fetched row (not `row_to_model` — this handler already queries the raw row via `cur.execute("SELECT * FROM models WHERE id=?", ...)`).
- Produces: `DELETE /api/models/{model_id}?deleteFile=<bool>` — `deleteFile` defaults to `False`. Note: copy-mode deletes were already unaffected by this change without any code change, because the existing `UPLOAD_DIR` filename-prefix loop never matches a reference-mode model's id (no file was ever copied there) — so today's code already leaves referenced files untouched by default. The only new behavior is `deleteFile=True` actively removing the real file.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_models_core.py`:

```python
def test_delete_reference_model_default_leaves_file_on_disk(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "keep.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="keep.stl", reference_only=True)

    response = client.delete(f"/api/models/{model['id']}")
    assert response.status_code == 200
    assert source.exists()
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert all(m["id"] != model["id"] for m in listed)


def test_delete_reference_model_with_delete_file_true_removes_it(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "remove_me.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="remove_me.stl", reference_only=True)

    response = client.delete(f"/api/models/{model['id']}", params={"deleteFile": "true"})
    assert response.status_code == 200
    assert not source.exists()


def test_delete_copy_mode_model_ignores_delete_file_param(client):
    created = _upload(client)

    response = client.delete(f"/api/models/{created['id']}", params={"deleteFile": "false"})
    assert response.status_code == 200
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert all(m["id"] != created["id"] for m in listed)
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && pytest tests/test_models_core.py -v`
Expected: `test_delete_reference_model_with_delete_file_true_removes_it` FAILS — the real file is never removed today (no such branch exists). The other two already pass with current behavior, which is fine — they lock in behavior this task must not break.

- [ ] **Step 3: Add the `deleteFile` param and reference-mode removal**

In `backend/app/routers/models.py`, replace `delete_model`:

```python
@router.delete("/api/models/{model_id}")
def delete_model(model_id: str, deleteFile: bool = False):
    conn = get_db_conn()
    cur = conn.cursor()
    m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    if not m:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")
    for fname in os.listdir(UPLOAD_DIR):
        if fname.startswith(model_id):
            try:
                os.remove(os.path.join(UPLOAD_DIR, fname))
            except Exception:
                pass
    if m["storageMode"] == "reference" and deleteFile and m["sourcePath"]:
        try:
            os.remove(m["sourcePath"])
        except OSError:
            pass
    manual_path = MANUAL_DIR / f"{model_id}.md"
    if manual_path.exists():
        try:
            manual_path.unlink()
        except Exception:
            pass
    cur.execute("DELETE FROM models WHERE id=?", (model_id,))
    conn.commit()
    conn.close()
    return {"ok": True}
```

- [ ] **Step 4: Run to confirm it passes**

Run: `cd backend && pytest tests/test_models_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/models.py backend/tests/test_models_core.py
git commit -m "feat: delete_model accepts deleteFile to also remove a referenced file's source"
```

---

### Task 7: Lock in that bulk-delete never touches referenced files

**Files:**
- Test: `backend/tests/test_models_bulk.py`

**Interfaces:**
- None new. This task adds a regression test only — no production code changes. `bulk_delete` in `backend/app/routers/models.py` already only ever removes files from `UPLOAD_DIR` whose name starts with the model's id; a reference-mode model never has such a file (nothing was copied there), so bulk-delete already leaves the real file untouched today. This test locks that in so a future change to `bulk_delete` can't regress it silently.

- [ ] **Step 1: Write the test**

Append to `backend/tests/test_models_bulk.py`:

```python
def test_bulk_delete_leaves_reference_mode_files_on_disk(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "bulk_keep.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="bulk_keep.stl", reference_only=True)

    response = client.post("/api/models/bulk-delete", json={"ids": [model["id"]]})
    assert response.status_code == 200
    assert source.exists()
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert all(m["id"] != model["id"] for m in listed)
```

- [ ] **Step 2: Run to confirm it already passes**

Run: `cd backend && pytest tests/test_models_bulk.py -v`
Expected: PASS immediately — no production code change needed for this task, as explained above.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_models_bulk.py
git commit -m "test: lock in that bulk-delete never removes a reference-mode model's real file"
```

---

### Task 8: Migration script for the existing Dropbox-folder copies

**Files:**
- Create: `backend/scripts/__init__.py` (empty)
- Create: `backend/scripts/migrate_watch_folder_references.py`
- Test: `backend/tests/test_migrate_watch_folder_references.py`

**Interfaces:**
- Produces: `migrate_watch_folder_to_references(conn: sqlite3.Connection, uploads_dir: str, watch_folder_path: str) -> dict` returning `{"migrated": int, "skipped_missing": List[str]}`. Pure function taking an open connection and paths, so it's directly testable without touching the real `dev-data` database; a thin `if __name__ == "__main__":` CLI wrapper connects to the real `DB_PATH`/`FILE_STORAGE` for the actual one-time run.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_migrate_watch_folder_references.py`:

```python
import sqlite3


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE models (id TEXT PRIMARY KEY, name TEXT, sourcePath TEXT, "
        "storageMode TEXT NOT NULL DEFAULT 'copy')"
    )
    return conn


def test_migrate_converts_and_deletes_redundant_copy(tmp_path):
    from scripts.migrate_watch_folder_references import migrate_watch_folder_to_references

    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    real_file = watch_root / "a.stl"
    real_file.write_bytes(b"solid endsolid")

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    copy_path = uploads_dir / "m1.stl"
    copy_path.write_bytes(b"solid endsolid")

    conn = _make_db()
    conn.execute(
        "INSERT INTO models(id,name,sourcePath,storageMode) VALUES (?,?,?,?)",
        ("m1", "a.stl", str(real_file), "copy"),
    )
    conn.commit()

    result = migrate_watch_folder_to_references(conn, str(uploads_dir), str(watch_root))

    assert result == {"migrated": 1, "skipped_missing": []}
    row = conn.execute("SELECT storageMode FROM models WHERE id='m1'").fetchone()
    assert row["storageMode"] == "reference"
    assert not copy_path.exists()
    conn.close()


def test_migrate_skips_row_whose_source_file_is_missing(tmp_path):
    from scripts.migrate_watch_folder_references import migrate_watch_folder_to_references

    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    missing_source = watch_root / "gone.stl"

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    copy_path = uploads_dir / "m2.stl"
    copy_path.write_bytes(b"solid endsolid")

    conn = _make_db()
    conn.execute(
        "INSERT INTO models(id,name,sourcePath,storageMode) VALUES (?,?,?,?)",
        ("m2", "gone.stl", str(missing_source), "copy"),
    )
    conn.commit()

    result = migrate_watch_folder_to_references(conn, str(uploads_dir), str(watch_root))

    assert result == {"migrated": 0, "skipped_missing": ["m2"]}
    row = conn.execute("SELECT storageMode FROM models WHERE id='m2'").fetchone()
    assert row["storageMode"] == "copy"
    assert copy_path.exists()
    conn.close()


def test_migrate_ignores_rows_outside_the_watch_folder(tmp_path):
    from scripts.migrate_watch_folder_references import migrate_watch_folder_to_references

    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    other_root = tmp_path / "elsewhere"
    other_root.mkdir()
    other_file = other_root / "b.stl"
    other_file.write_bytes(b"solid endsolid")

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    copy_path = uploads_dir / "m3.stl"
    copy_path.write_bytes(b"solid endsolid")

    conn = _make_db()
    conn.execute(
        "INSERT INTO models(id,name,sourcePath,storageMode) VALUES (?,?,?,?)",
        ("m3", "b.stl", str(other_file), "copy"),
    )
    conn.commit()

    result = migrate_watch_folder_to_references(conn, str(uploads_dir), str(watch_root))

    assert result == {"migrated": 0, "skipped_missing": []}
    row = conn.execute("SELECT storageMode FROM models WHERE id='m3'").fetchone()
    assert row["storageMode"] == "copy"
    assert copy_path.exists()
    conn.close()
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && pytest tests/test_migrate_watch_folder_references.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts'`.

- [ ] **Step 3: Create the script**

Create `backend/scripts/__init__.py` (empty file).

Create `backend/scripts/migrate_watch_folder_references.py`:

```python
"""One-off migration: convert existing copy-mode models whose source file
lives under a given watch-folder root to reference-mode, and delete their
now-redundant managed copies.

Run once, manually, after deploying in-place file references — see
docs/superpowers/specs/2026-08-02-in-place-file-references-design.md.

Usage:
    cd backend
    .venv/Scripts/python.exe scripts/migrate_watch_folder_references.py "D:/Dropbox/3D Print Files"
"""
import os
import sqlite3
import sys
from pathlib import Path


def _is_under(path_str: str, root_str: str) -> bool:
    try:
        Path(path_str).resolve().relative_to(Path(root_str).resolve())
        return True
    except ValueError:
        return False


def migrate_watch_folder_to_references(conn: sqlite3.Connection, uploads_dir: str, watch_folder_path: str) -> dict:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, sourcePath FROM models WHERE storageMode='copy' AND sourcePath IS NOT NULL"
    ).fetchall()

    migrated = 0
    skipped_missing = []
    for row in rows:
        source_path = row["sourcePath"]
        if not _is_under(source_path, watch_folder_path):
            continue
        if not os.path.exists(source_path):
            skipped_missing.append(row["id"])
            continue

        ext = os.path.splitext(row["name"])[1] or ".stl"
        copy_path = os.path.join(uploads_dir, f"{row['id']}{ext}")
        conn.execute("UPDATE models SET storageMode='reference' WHERE id=?", (row["id"],))
        if os.path.exists(copy_path):
            os.remove(copy_path)
        migrated += 1

    conn.commit()
    return {"migrated": migrated, "skipped_missing": skipped_missing}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: migrate_watch_folder_references.py <watch_folder_path>")
        sys.exit(1)

    db_path = os.getenv("DB_PATH", "data.db")
    uploads_dir = os.getenv("FILE_STORAGE", "./app/uploads")
    conn = sqlite3.connect(db_path)
    result = migrate_watch_folder_to_references(conn, uploads_dir, sys.argv[1])
    conn.close()
    print(f"Migrated: {result['migrated']}")
    if result["skipped_missing"]:
        print(f"Skipped (source file missing, left as copy): {result['skipped_missing']}")
```

- [ ] **Step 4: Run to confirm the tests pass**

Run: `cd backend && pytest tests/test_migrate_watch_folder_references.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/__init__.py backend/scripts/migrate_watch_folder_references.py backend/tests/test_migrate_watch_folder_references.py
git commit -m "feat: add one-off migration script for existing watch-folder copies"
```

- [ ] **Step 6: Run the migration against the real dev database**

With the backend **stopped** (use the control app's Stop button first — this touches the live `dev-data` DB directly):

```bash
cd backend
DB_PATH="../dev-data/data/data.db" FILE_STORAGE="../dev-data/uploads" .venv/Scripts/python.exe scripts/migrate_watch_folder_references.py "D:/Dropbox/3D Print Files"
```

Expected output: `Migrated: <N>` where N is close to 728 (minus any rows already reference-mode or genuinely missing from disk), and `Skipped (source file missing, left as copy): [...]` only if any files are actually gone from Dropbox. Verify afterward:

```bash
.venv/Scripts/python.exe -c "
import sqlite3
conn = sqlite3.connect('../dev-data/data/data.db')
print(conn.execute(\"SELECT storageMode, COUNT(*) FROM models GROUP BY storageMode\").fetchall())
"
```

---

### Task 9: Frontend — `storageMode`/`missing` types, delete-confirm branching, badges

**Files:**
- Modify: `frontend/types.ts` (`STLModel` interface)
- Modify: `frontend/services/api.ts` (`deleteModel`)
- Modify: `frontend/App.tsx` (delete confirmation modal + `executeDelete`)
- Modify: `frontend/components/ModelList.tsx` (card badges)

**Interfaces:**
- Consumes: `storageMode`/`missing` fields on every model object returned by the backend (Task 4), and the `deleteFile` query param on `DELETE /api/models/{id}` (Task 6).
- Produces: no new interfaces consumed elsewhere — this is the top of the stack.

- [ ] **Step 1: Add fields to `STLModel`**

In `frontend/types.ts`, extend the interface:

```typescript
export interface STLModel {
  id: string;
  name: string;
  folderId: string;
  url: string; // Blob URL
  size: number;
  dateAdded: number;
  tags: string[];
  description: string;
  dimensions?: { x: number; y: number; z: number };
  thumbnail?: string;
  manual?: string | null;
  author?: string | null;
  sourceUrl?: string | null;
  category?: string | null;
  colorCount?: number | null;
  sliceSettings?: string | null;
  storageMode?: "copy" | "reference";
  missing?: boolean;
}
```

- [ ] **Step 2: `deleteModel` accepts `deleteFile`**

In `frontend/services/api.ts`, replace the `deleteModel` method:

```typescript
  // 8. DELETE Model
  deleteModel: async (id: string, deleteFile: boolean = false): Promise<void> => {
    console.log("API: Deleting model", id, "deleteFile:", deleteFile);

    const res = await fetch(`${getApiBaseUrl()}/models/${id}?deleteFile=${deleteFile}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error("Delete failed");
  },
```

- [ ] **Step 3: Branch the delete confirmation modal and `executeDelete`**

In `frontend/App.tsx`, change `executeDelete` to accept a `deleteFile` flag and pass it through for single deletes:

```typescript
  const executeDelete = async (deleteFile: boolean = false) => {
    const { type, id } = deleteConfirmState;
    console.log(`Executing delete type: ${type}, id: ${id}, deleteFile: ${deleteFile}`);

    try {
      if (type === "single" && id) {
        await api.deleteModel(id, deleteFile);
        setModels((prev) => prev.filter((m) => m.id !== id));
        if (selectedModelId === id) setSelectedModelId(null);
      } else if (type === "bulk") {
```

(Leave the rest of the `bulk`/`folder` branches inside `executeDelete` exactly as they are today.)

Replace the modal's message paragraph and button area (the block starting at `{/* Delete Confirmation Modal */}`, message `<p>` and the `<div className="flex gap-3">` button row that follows it):

```tsx
                    <div className="flex flex-col items-center text-center mb-6">
                      <div className="w-12 h-12 bg-red-900/30 rounded-full flex items-center justify-center mb-4">
                        <AlertTriangle className="w-6 h-6 text-red-500" />
                      </div>
                      <h3 className="text-xl font-bold text-white mb-2">
                        Confirm Deletion
                      </h3>
                      <p className="text-slate-400 text-sm">
                        {deleteConfirmState.type === "single" &&
                        models.find((m) => m.id === deleteConfirmState.id)
                          ?.storageMode === "reference"
                          ? "This model references a file on disk rather than a copy stored by STLVault. Remove it from the library, or also delete the real file?"
                          : deleteConfirmState.type === "single" &&
                            "Are you sure you want to delete this model? This action cannot be undone."}
                        {deleteConfirmState.type === "bulk" &&
                          `Are you sure you want to delete ${selectedIds.size} models? This action cannot be undone.`}
                        {deleteConfirmState.type === "folder" &&
                          "Are you sure you want to delete this folder?"}
                      </p>
                    </div>

                    {deleteConfirmState.type === "single" &&
                    models.find((m) => m.id === deleteConfirmState.id)
                      ?.storageMode === "reference" ? (
                      <div className="flex flex-col gap-3">
                        <button
                          onClick={() => executeDelete(false)}
                          className="w-full py-2.5 rounded-lg bg-vault-700 hover:bg-vault-600 text-slate-200 font-medium transition-colors"
                        >
                          Remove from library
                        </button>
                        <button
                          onClick={() => executeDelete(true)}
                          className="w-full py-2.5 rounded-lg bg-red-600 hover:bg-red-500 text-white font-medium transition-colors"
                        >
                          Also delete file from disk
                        </button>
                        <button
                          onClick={() =>
                            setDeleteConfirmState((prev) => ({
                              ...prev,
                              isOpen: false,
                            }))
                          }
                          className="w-full py-2 text-slate-400 hover:text-slate-200 text-sm transition-colors"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <div className="flex gap-3">
                        <button
                          onClick={() =>
                            setDeleteConfirmState((prev) => ({
                              ...prev,
                              isOpen: false,
                            }))
                          }
                          className="flex-1 py-2.5 rounded-lg bg-vault-700 hover:bg-vault-600 text-slate-200 font-medium transition-colors"
                        >
                          Cancel
                        </button>
                        <button
                          onClick={() => executeDelete(false)}
                          className="flex-1 py-2.5 rounded-lg bg-red-600 hover:bg-red-500 text-white font-medium transition-colors"
                        >
                          Delete
                        </button>
                      </div>
                    )}
```

- [ ] **Step 4: Add badges in `ModelList.tsx`**

Add `Link2` to the `lucide-react` import list in `frontend/components/ModelList.tsx` (`XCircle` is already imported):

```typescript
import {
  CloudUpload,
  FileBox,
  Search,
  CheckSquare,
  MoreVertical,
  ExternalLink,
  Download,
  Globe,
  Folder as FolderIcon,
  DownloadIcon,
  ScreenShareIcon,
  XCircle,
  ChevronLeft,
  BookOpen,
  Link2,
} from "lucide-react";
```

Add a badge, mirroring the existing extension `Chip` (`absolute top-2 right-2`), just before it:

```tsx
                      {model.storageMode === "reference" && (
                        <div className="absolute top-2 left-2">
                          <Chip
                            sx={{ borderRadius: 1 }}
                            icon={
                              model.missing ? (
                                <XCircle className="w-3.5 h-3.5" />
                              ) : (
                                <Link2 className="w-3.5 h-3.5" />
                              )
                            }
                            label={model.missing ? "Missing" : "Linked"}
                            color={model.missing ? "error" : "default"}
                            size="small"
                          />
                        </div>
                      )}
                      <div className="absolute top-2 right-2">
```

(This replaces the existing standalone `<div className="absolute top-2 right-2">` opening tag with the badge block immediately preceding it — the rest of that `<div>`'s contents are unchanged.)

- [ ] **Step 5: Type-check**

Run: `cd frontend && bun x tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Live-verify in the browser**

With the backend and frontend both running (via the control app):
1. Confirm the Dropbox-folder models in the library now show the "Linked" badge.
2. Rename or move one referenced file's source on disk outside the app, refresh the library, and confirm that model now shows "Missing" instead, and its detail view / download attempt surfaces the descriptive not-found message rather than a broken viewer.
3. Delete a referenced (non-missing) model and confirm the two-button dialog appears; choose "Remove from library" and confirm the file is still on disk afterward.
4. Delete a different referenced model choosing "Also delete file from disk" and confirm the file is gone.
5. Delete a manually-uploaded (copy-mode) model and confirm the dialog is unchanged from today (single "Delete" button, no mention of "reference").

- [ ] **Step 7: Commit**

```bash
git add frontend/types.ts frontend/services/api.ts frontend/App.tsx frontend/components/ModelList.tsx
git commit -m "feat: frontend support for reference-mode models (badges, missing state, delete choice)"
```

---

## Self-Review

**Spec coverage:**
- New `storageMode` column → Task 1.
- `ingest_file(reference_only=...)` → Task 2.
- `scan_watch_folder` uses it, unconditionally, watch-folders-only scope → Task 3.
- `missing` computed on every read, never stored → Task 4.
- Download branches on `storageMode` with descriptive 404 → Task 5.
- Delete `deleteFile` param, copy-mode unchanged, reference-mode ask-each-time (frontend two-button, backend flag) → Task 6, Task 9 Step 3.
- Bulk-delete never touches referenced files → Task 7 (already true; test locks it in).
- Migration script, safety-skips missing sources, run once against real data → Task 8.
- Frontend types/badges/delete UX → Task 9.
- Explicitly-out-of-scope items from the spec (per-folder toggle, auto-relink, auto-remove, re-upload endpoint, Dropbox-specific code) are not implemented anywhere in this plan, matching the spec.

**Placeholder scan:** no TBD/TODO; every step has concrete code or an exact command.

**Type consistency:** `ingest_file(reference_only: bool = False)` (Task 2) is called with `reference_only=True` in Task 3 — matches. `row_to_model` (Task 4) fields `storageMode`/`missing`/`sourcePath` are consumed identically in Tasks 5, 6, 9. `delete_model(model_id: str, deleteFile: bool = False)` (Task 6) matches the frontend's `deleteModel(id, deleteFile)` (Task 9) and the query param name (`deleteFile`) matches on both sides. `migrate_watch_folder_to_references(conn, uploads_dir, watch_folder_path) -> dict` (Task 8) signature and return shape (`{"migrated": int, "skipped_missing": List[str]}`) are used consistently across all three of its tests.
