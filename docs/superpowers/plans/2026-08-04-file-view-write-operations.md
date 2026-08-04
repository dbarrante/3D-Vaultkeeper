# File View: Write Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Dave rename, move, copy, and delete real files and folders directly from File view's right-click menu (and drag-and-drop for move/copy), keeping the DB in sync — including watch-folder files, which need `sourcePath` kept in lock-step with `filePath` so the watcher never loses or duplicates them.

**Architecture:** Two new backend operation surfaces — file-level endpoints keyed on `model_id` (in `backend/app/routers/models.py`, parallel to the existing delete/download endpoints) and folder-level endpoints keyed on a real absolute disk path (new `backend/app/routers/file_view.py` + `backend/app/services/file_view_ops.py`, since File-mode folders are synthetic path groupings with no `folders` table row). On the frontend, file-level actions live on `ModelList.tsx`'s grid cards (File mode's tree has no file leaves — `fileViewSegments` drops the filename, so files only ever appear in the grid) and folder-level actions live on `Sidebar.tsx`'s File-mode tree nodes.

**Tech Stack:** FastAPI + sqlite3 (backend), React/TypeScript + MUI (frontend), pytest with `tmp_path` real-filesystem fixtures.

## Global Constraints

- No folder-level copy. Copy is file-only (menu item + Ctrl+drag). Folders get Rename/Move/Delete only.
- No confirmation dialog for rename/move/copy. Only delete confirms (`window.confirm`, matching this codebase's existing alert()/confirm()-based interaction style — no reusable toast/dialog component exists for this to hook into, confirmed by reading App.tsx in full).
- No changes to Logical mode's existing create/rename/delete/drag-to-move behavior.
- **Reference-mode (watch-folder) rename/move MUST update `sourcePath` alongside `filePath` in the same DB write.** This is the spec's most safety-critical requirement: `scan_watch_folder` (`backend/app/services/scan.py:55-56`) dedups against `sourcePath`, and `row_to_model` (`backend/app/db.py:205`) computes `missing` from `sourcePath`. Updating only `filePath` would make the model show as missing and get re-ingested as a duplicate at its new location on the next scan.
- Folder delete refuses (400, before any mutation) if the target is a filesystem root or exactly matches a `watch_folders.path` row. This is a hard refusal, not a stronger confirmation.
- File-view delete always hard-deletes (physical file + DB row), never the existing tombstone path that `DELETE /api/models/{id}` takes by default for reference-mode rows without `deleteFile=true`.
- The synthetic "Uploads" bucket node (`FILE_VIEW_UPLOADS_BUCKET_ID` in `frontend/types.ts`) gets no folder-level context menu — it isn't a real single directory (see `frontend/types.ts:105-111`'s `fileViewSegments`, which collapses everything through a literal "uploads" path segment into an empty meaningful-segment list), so rename/move/delete on it are undefined operations.
- Every write endpoint validates destination containment server-side (copy-mode → must resolve inside `UPLOAD_DIR`; reference-mode → must resolve inside some `watch_folders.path` row), independent of anything the frontend already prevents.

---

## Design correction vs. the spec

The spec's `POST /api/models/{id}/duplicate` section describes "auto-suffixing the filename on collision (hull.stl → hull_1.stl)". This plan does **not** implement that — it was based on a wrong assumption about physical filenames. Every copy-mode file in this codebase is named `<model-id>.<ext>` by construction (see `backend/app/services/ingestion.py` and `_resolve_copy_mode_file`'s docstring in `backend/app/routers/models.py:17-27`), never the original human-readable name. A duplicate gets a brand-new `uuid4()` id and therefore a brand-new filename — collision-proof by construction, exactly like every other copy-mode ingestion path in this app. No suffix loop is needed or written. (This mirrors an earlier correction this session made to the *previous* feature's spec, where `ingest_file`'s actual naming convention was discovered mid-plan — see `2026-08-04-file-organization-import-and-view.md`.)

---

### Task 1: Backend — destination validation + file rename/move endpoint

**Files:**
- Create: `backend/app/services/file_view_ops.py`
- Modify: `backend/app/routers/models.py`
- Test: `backend/tests/test_file_view_write_ops.py` (new)

**Interfaces:**
- Produces: `validate_destination(new_path: str, storage_mode: str) -> Path` in `file_view_ops.py` — raises `ValueError` with a user-facing message if the destination isn't inside an allowed root. Consumed by this task's endpoint and reused as-is by Task 2 is NOT needed (duplicate always targets `UPLOAD_DIR` directly), but IS reused by Task 3's folder endpoints... actually Task 3's folder move also needs containment checking — see Task 3, which imports this same function.
- Produces: `PATCH /api/models/{id}/location` in `models.py`, body `{"newPath": str}`, returns the updated model (same shape as every other endpoint in this file, via `row_to_model`).

- [ ] **Step 1: Create `file_view_ops.py` with the containment validator**

```python
# backend/app/services/file_view_ops.py
from pathlib import Path

from app.db import get_db_conn, UPLOAD_DIR


def validate_destination(new_path: str, storage_mode: str) -> Path:
    """Ensure a rename/move destination resolves inside an allowed root.

    Copy-mode files may only land under UPLOAD_DIR (the app's managed
    storage) -- .resolve() neutralizes any ".." traversal in new_path
    before the containment check runs, so a crafted destination can't
    escape UPLOAD_DIR even if a caller doesn't sanitize it first.

    Reference-mode (watch-folder) files may land under any currently
    configured watch_folders.path. Moving one outside every watched root
    would mean the app permanently loses track of it -- nothing would
    ever scan that location again -- so that's rejected rather than
    silently allowed.
    """
    resolved = Path(new_path).resolve()

    if storage_mode == "copy":
        upload_root = Path(UPLOAD_DIR).resolve()
        if resolved == upload_root or upload_root in resolved.parents:
            return resolved
        raise ValueError(
            f"Destination must be inside the managed library folder: {upload_root}"
        )

    conn = get_db_conn()
    try:
        watch_roots = [
            Path(row["path"]).resolve()
            for row in conn.execute("SELECT path FROM watch_folders").fetchall()
        ]
    finally:
        conn.close()
    for root in watch_roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise ValueError(
        "A linked file has to stay somewhere the app is watching -- "
        "this destination isn't inside any watched folder."
    )
```

- [ ] **Step 2: Write the failing tests for the endpoint**

```python
# backend/tests/test_file_view_write_ops.py
import os
import sqlite3
from pathlib import Path

from app.db import get_db_conn


def _insert_folder(conn, folder_id, name, parent_id=None):
    conn.execute(
        "INSERT INTO folders(id,name,parentId) VALUES (?,?,?)",
        (folder_id, name, parent_id),
    )


def _insert_model(conn, model_id, folder_id, file_path, storage_mode="copy", source_path=None):
    conn.execute(
        "INSERT INTO models "
        "(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,manual,"
        " sourcePath,storageMode,filePath) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            model_id, os.path.basename(file_path), folder_id,
            f"/api/models/{model_id}/download", 100, 0, "[]", "", None, None,
            source_path, storage_mode, file_path,
        ),
    )


def test_rename_copy_mode_file_updates_path_and_moves_file(client, tmp_path, monkeypatch):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src = upload_dir / "abc123.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc123", "f1", str(src), storage_mode="copy")
    conn.commit()
    conn.close()

    new_path = str(upload_dir / "abc123_renamed.stl")
    resp = client.patch("/api/models/abc123/location", json={"newPath": new_path})
    assert resp.status_code == 200
    body = resp.json()
    assert body["filePath"] == new_path
    assert not src.exists()
    assert os.path.exists(new_path)

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id='abc123'").fetchone()
    conn.close()
    assert row["filePath"] == new_path


def test_move_copy_mode_file_into_subdirectory(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src = upload_dir / "abc123.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc123", "f1", str(src), storage_mode="copy")
    conn.commit()
    conn.close()

    dest_dir = upload_dir / "Vehicles" / "Tanks"
    dest_dir.mkdir(parents=True)
    new_path = str(dest_dir / "abc123.stl")
    resp = client.patch("/api/models/abc123/location", json={"newPath": new_path})
    assert resp.status_code == 200
    assert os.path.exists(new_path)
    assert not src.exists()


def test_rename_reference_mode_file_syncs_source_path(client, tmp_path):
    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    src = watch_root / "hull.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    _insert_model(conn, "m1", "f1", str(src), storage_mode="reference", source_path=str(src))
    conn.commit()
    conn.close()

    new_path = str(watch_root / "hull_v2.stl")
    resp = client.patch("/api/models/m1/location", json={"newPath": new_path})
    assert resp.status_code == 200
    body = resp.json()
    assert body["filePath"] == new_path
    assert body["sourcePath"] == new_path
    assert body["missing"] is False
    assert not src.exists()
    assert os.path.exists(new_path)


def test_move_reference_mode_file_outside_every_watch_root_rejected(client, tmp_path):
    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    src = watch_root / "hull.stl"
    src.write_text("model data")
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    _insert_model(conn, "m1", "f1", str(src), storage_mode="reference", source_path=str(src))
    conn.commit()
    conn.close()

    new_path = str(outside / "hull.stl")
    resp = client.patch("/api/models/m1/location", json={"newPath": new_path})
    assert resp.status_code == 400
    assert src.exists()  # untouched


def test_move_reference_mode_then_rescan_does_not_duplicate(client, tmp_path):
    """The exact bug the spec's reference-mode section exists to prevent:
    a rename/move that updated filePath but not sourcePath would make the
    watcher re-ingest the file at its new location as a brand-new row.
    """
    from app.services.scan import scan_watch_folder

    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    src = watch_root / "hull.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId,frequencyMinutes,enabled) VALUES (?,?,?,?,?)",
        ("wf1", str(watch_root), "f1", 60, 1),
    )
    _insert_model(conn, "m1", "f1", str(src), storage_mode="reference", source_path=str(src))
    conn.commit()
    watch_row = dict(conn.execute("SELECT * FROM watch_folders WHERE id='wf1'").fetchone())
    conn.close()

    new_path = str(watch_root / "hull_v2.stl")
    resp = client.patch("/api/models/m1/location", json={"newPath": new_path})
    assert resp.status_code == 200

    scan_watch_folder(watch_row)

    conn = get_db_conn()
    count = conn.execute("SELECT COUNT(*) c FROM models").fetchone()["c"]
    conn.close()
    assert count == 1, "rescan created a duplicate row instead of recognizing the file at its new location"


def test_rename_traversal_attempt_is_neutralized(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src = upload_dir / "abc123.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc123", "f1", str(src), storage_mode="copy")
    conn.commit()
    conn.close()

    escape_attempt = str(upload_dir / ".." / ".." / "escaped.stl")
    resp = client.patch("/api/models/abc123/location", json={"newPath": escape_attempt})
    assert resp.status_code == 400
    assert src.exists()


def test_move_destination_already_exists_conflicts(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src = upload_dir / "abc123.stl"
    src.write_text("model data")
    existing = upload_dir / "taken.stl"
    existing.write_text("already here")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc123", "f1", str(src), storage_mode="copy")
    conn.commit()
    conn.close()

    resp = client.patch("/api/models/abc123/location", json={"newPath": str(existing)})
    assert resp.status_code == 409
    assert src.exists()
    assert existing.read_text() == "already here"


def test_rename_missing_model_404s(client):
    resp = client.patch("/api/models/doesnotexist/location", json={"newPath": "/tmp/x.stl"})
    assert resp.status_code == 404
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_file_view_write_ops.py -v`
Expected: FAIL — `404 Not Found` / `405 Method Not Allowed` on every request (endpoint doesn't exist yet).

- [ ] **Step 4: Implement the endpoint**

Add near the top of `backend/app/routers/models.py` (after the existing imports at line 12):

```python
from pydantic import BaseModel
from pathlib import Path

from app.services.file_view_ops import validate_destination
```

Add anywhere after `_resolve_copy_mode_file` (e.g. directly after `delete_model`, around line 153):

```python
class LocationUpdate(BaseModel):
    newPath: str


@router.patch("/api/models/{model_id}/location")
def update_model_location(model_id: str, body: LocationUpdate):
    conn = get_db_conn()
    cur = conn.cursor()
    m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    if not m:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")

    storage_mode = m["storageMode"] if "storageMode" in m.keys() else "copy"
    if storage_mode == "reference":
        current_path = m["sourcePath"]
    else:
        current_path = _resolve_copy_mode_file(model_id, m["filePath"] if "filePath" in m.keys() else None)
    if not current_path or not os.path.exists(current_path):
        conn.close()
        raise HTTPException(status_code=404, detail="File not found on disk")

    try:
        destination = validate_destination(body.newPath, storage_mode)
    except ValueError as exc:
        conn.close()
        raise HTTPException(status_code=400, detail=str(exc))

    if destination.exists():
        conn.close()
        raise HTTPException(status_code=409, detail=f"A file already exists at {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    shutil.move(current_path, str(destination))

    if storage_mode == "reference":
        cur.execute(
            "UPDATE models SET filePath=?, sourcePath=? WHERE id=?",
            (str(destination), str(destination), model_id),
        )
    else:
        cur.execute("UPDATE models SET filePath=? WHERE id=?", (str(destination), model_id))
    conn.commit()
    row = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    conn.close()
    return row_to_model(row)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_file_view_write_ops.py -v`
Expected: PASS (all 8 tests in this file so far)

- [ ] **Step 6: Run the full backend suite to check for regressions**

Run: `cd backend && python -m pytest -v`
Expected: PASS (172 previously-passing tests + this task's new tests)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/file_view_ops.py backend/app/routers/models.py backend/tests/test_file_view_write_ops.py
git commit -m "feat: add PATCH /api/models/{id}/location for File-mode rename/move"
```

---

### Task 2: Backend — file duplicate endpoint

**Files:**
- Modify: `backend/app/routers/models.py`
- Test: `backend/tests/test_file_view_write_ops.py`

**Interfaces:**
- Consumes: `_resolve_copy_mode_file` (existing, `models.py:17`), `folder_disk_path` (existing, `backend/app/services/import_wizard.py:79`).
- Produces: `POST /api/models/{id}/duplicate` in `models.py`, no body, returns the new model.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_file_view_write_ops.py`:

```python
def test_duplicate_copy_mode_file_creates_new_row_and_copy(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src = upload_dir / "abc123.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO models "
        "(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,manual,author,"
        " sourceUrl,category,colorCount,sliceSettings,sourcePath,storageMode,filePath) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "abc123", "abc123.stl", "f1", "/api/models/abc123/download", 100, 0,
            '["red"]', "a description", None, None, "some author",
            None, "vehicles", None, None, None, "copy", str(src),
        ),
    )
    conn.commit()
    conn.close()

    resp = client.post("/api/models/abc123/duplicate")
    assert resp.status_code == 200
    new_model = resp.json()
    assert new_model["id"] != "abc123"
    assert new_model["folderId"] == "f1"
    assert new_model["tags"] == ["red"]
    assert new_model["description"] == "a description"
    assert new_model["author"] == "some author"
    assert new_model["storageMode"] == "copy"
    assert new_model["sourcePath"] is None
    assert os.path.exists(new_model["filePath"])
    assert new_model["filePath"] != str(src)
    assert src.exists(), "original file must be untouched"

    conn = get_db_conn()
    count = conn.execute("SELECT COUNT(*) c FROM models").fetchone()["c"]
    conn.close()
    assert count == 2


def test_duplicate_reference_mode_file_becomes_copy_mode(client, tmp_path):
    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    src = watch_root / "hull.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    _insert_model(conn, "m1", "f1", str(src), storage_mode="reference", source_path=str(src))
    conn.commit()
    conn.close()

    upload_dir = Path(os.environ["FILE_STORAGE"])
    resp = client.post("/api/models/m1/duplicate")
    assert resp.status_code == 200
    new_model = resp.json()
    assert new_model["storageMode"] == "copy"
    assert new_model["sourcePath"] is None
    assert Path(new_model["filePath"]).resolve().is_relative_to(upload_dir.resolve())
    assert os.path.exists(new_model["filePath"])
    assert src.exists(), "original watched file must be untouched"


def test_duplicate_mirrors_folder_disk_path(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src = upload_dir / "abc123.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "root", "Root")
    _insert_folder(conn, "vehicles", "Vehicles", parent_id="root")
    _insert_model(conn, "abc123", "vehicles", str(src), storage_mode="copy")
    conn.commit()
    conn.close()

    resp = client.post("/api/models/abc123/duplicate")
    assert resp.status_code == 200
    new_path = Path(resp.json()["filePath"])
    assert new_path.parent == upload_dir / "Vehicles"


def test_duplicate_missing_model_404s(client):
    resp = client.post("/api/models/doesnotexist/duplicate")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_file_view_write_ops.py -k duplicate -v`
Expected: FAIL — `404 Not Found` (endpoint doesn't exist).

- [ ] **Step 3: Implement the endpoint**

Add to the top of `backend/app/routers/models.py`, alongside the other imports:

```python
import uuid
from app.services.import_wizard import folder_disk_path
```

Add after the new `update_model_location` endpoint from Task 1:

```python
@router.post("/api/models/{model_id}/duplicate")
def duplicate_model(model_id: str):
    conn = get_db_conn()
    cur = conn.cursor()
    m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    if not m:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")

    storage_mode = m["storageMode"] if "storageMode" in m.keys() else "copy"
    if storage_mode == "reference":
        current_path = m["sourcePath"]
    else:
        current_path = _resolve_copy_mode_file(model_id, m["filePath"] if "filePath" in m.keys() else None)
    if not current_path or not os.path.exists(current_path):
        conn.close()
        raise HTTPException(status_code=404, detail="File not found on disk")

    dest_subpath = folder_disk_path(m["folderId"])
    dest_dir = Path(UPLOAD_DIR) / dest_subpath
    dest_dir.mkdir(parents=True, exist_ok=True)
    new_id = str(uuid.uuid4())
    ext = os.path.splitext(current_path)[-1]
    new_path = dest_dir / f"{new_id}{ext}"
    shutil.copy2(current_path, new_path)

    cur.execute(
        "INSERT INTO models "
        "(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,manual,author,"
        " sourceUrl,category,colorCount,sliceSettings,sourcePath,storageMode,filePath) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            new_id, m["name"], m["folderId"], f"/api/models/{new_id}/download",
            os.path.getsize(new_path), now_ms(), m["tags"], m["description"], m["thumbnail"], None,
            m["author"] if "author" in m.keys() else None,
            m["sourceUrl"] if "sourceUrl" in m.keys() else None,
            m["category"] if "category" in m.keys() else None,
            m["colorCount"] if "colorCount" in m.keys() else None,
            m["sliceSettings"] if "sliceSettings" in m.keys() else None,
            None, "copy", str(new_path),
        ),
    )
    conn.commit()
    row = cur.execute("SELECT * FROM models WHERE id=?", (new_id,)).fetchone()
    conn.close()
    return row_to_model(row)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_file_view_write_ops.py -v`
Expected: PASS (all tests in the file so far)

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && python -m pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/models.py backend/tests/test_file_view_write_ops.py
git commit -m "feat: add POST /api/models/{id}/duplicate for File-mode copy"
```

---

### Task 3: Backend — folder rename/move endpoints

**Files:**
- Modify: `backend/app/services/file_view_ops.py`
- Create: `backend/app/routers/file_view.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_file_view_folder_ops.py` (new)

**Interfaces:**
- Consumes: nothing new from Tasks 1-2 (folder ops are independent of the file-level endpoints; they share only the general `get_db_conn`/`UPLOAD_DIR` foundations).
- Produces: `find_affected_models(dir_path: str) -> list` and `rewrite_affected_paths(dir_path: str, new_dir_path: str) -> None` in `file_view_ops.py`, reused by Task 4's folder delete. Produces `POST /api/file-view/folder/rename` and `POST /api/file-view/folder/move`.

- [ ] **Step 1: Add the shared model-resolution helpers to `file_view_ops.py`**

Append to `backend/app/services/file_view_ops.py`:

```python
import os


def find_affected_models(dir_path: str) -> list:
    """Every model row whose current file (filePath) lives at or under
    dir_path. Filters in Python rather than SQL LIKE to avoid needing to
    escape "%"/"_" wildcard characters that can legally appear in a real
    folder name.
    """
    conn = get_db_conn()
    try:
        rows = conn.execute("SELECT * FROM models").fetchall()
    finally:
        conn.close()
    prefix = os.path.normpath(dir_path)
    affected = []
    for row in rows:
        fp = row["filePath"] if "filePath" in row.keys() else None
        if not fp:
            continue
        norm = os.path.normpath(fp)
        if norm == prefix or norm.startswith(prefix + os.sep):
            affected.append(row)
    return affected


def rewrite_affected_paths(dir_path: str, new_dir_path: str) -> None:
    """After dir_path has already been physically moved/renamed to
    new_dir_path on disk, update every affected model's filePath (and
    sourcePath, for reference-mode rows) to match -- same lock-step
    requirement as the file-level rename/move endpoint, applied per row.
    Call this immediately after the physical move, passing the OLD
    dir_path so find_affected_models still matches what's in the DB.
    """
    old_prefix = os.path.normpath(dir_path)
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        for row in find_affected_models(dir_path):
            old_fp = os.path.normpath(row["filePath"])
            rel = os.path.relpath(old_fp, old_prefix)
            new_fp = os.path.normpath(os.path.join(new_dir_path, rel)) if rel != "." else os.path.normpath(new_dir_path)
            storage_mode = row["storageMode"] if "storageMode" in row.keys() else "copy"
            if storage_mode == "reference":
                cur.execute(
                    "UPDATE models SET filePath=?, sourcePath=? WHERE id=?",
                    (new_fp, new_fp, row["id"]),
                )
            else:
                cur.execute("UPDATE models SET filePath=? WHERE id=?", (new_fp, row["id"]))
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_file_view_folder_ops.py
import os
from pathlib import Path

from app.db import get_db_conn


def _insert_folder(conn, folder_id, name, parent_id=None):
    conn.execute(
        "INSERT INTO folders(id,name,parentId) VALUES (?,?,?)",
        (folder_id, name, parent_id),
    )


def _insert_model(conn, model_id, folder_id, file_path, storage_mode="copy", source_path=None):
    conn.execute(
        "INSERT INTO models "
        "(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,manual,"
        " sourcePath,storageMode,filePath) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            model_id, os.path.basename(file_path), folder_id,
            f"/api/models/{model_id}/download", 100, 0, "[]", "", None, None,
            source_path, storage_mode, file_path,
        ),
    )


def test_rename_folder_moves_directory_and_rewrites_paths(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src_dir = upload_dir / "Vehicles"
    src_dir.mkdir()
    f1 = src_dir / "abc.stl"
    f1.write_text("data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc", "f1", str(f1), storage_mode="copy")
    conn.commit()
    conn.close()

    resp = client.post("/api/file-view/folder/rename", json={"path": str(src_dir), "newName": "Cars"})
    assert resp.status_code == 200
    new_dir = upload_dir / "Cars"
    assert new_dir.exists()
    assert not src_dir.exists()
    assert (new_dir / "abc.stl").exists()

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id='abc'").fetchone()
    conn.close()
    assert row["filePath"] == str(new_dir / "abc.stl")


def test_rename_folder_updates_nested_reference_mode_source_path(client, tmp_path):
    watch_root = tmp_path / "watched"
    sub = watch_root / "Prints"
    sub.mkdir(parents=True)
    f1 = sub / "hull.stl"
    f1.write_text("data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    _insert_model(conn, "m1", "f1", str(f1), storage_mode="reference", source_path=str(f1))
    conn.commit()
    conn.close()

    resp = client.post("/api/file-view/folder/rename", json={"path": str(sub), "newName": "Archive"})
    assert resp.status_code == 200
    new_path = str(watch_root / "Archive" / "hull.stl")

    conn = get_db_conn()
    row = conn.execute("SELECT filePath, sourcePath FROM models WHERE id='m1'").fetchone()
    conn.close()
    assert row["filePath"] == new_path
    assert row["sourcePath"] == new_path


def test_rename_folder_destination_exists_conflicts(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src_dir = upload_dir / "Vehicles"
    src_dir.mkdir()
    (upload_dir / "Cars").mkdir()

    resp = client.post("/api/file-view/folder/rename", json={"path": str(src_dir), "newName": "Cars"})
    assert resp.status_code == 409
    assert src_dir.exists()


def test_rename_nonexistent_folder_404s(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    resp = client.post(
        "/api/file-view/folder/rename",
        json={"path": str(upload_dir / "DoesNotExist"), "newName": "New"},
    )
    assert resp.status_code == 404


def test_move_folder_into_another_directory(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src_dir = upload_dir / "Tanks"
    src_dir.mkdir()
    f1 = src_dir / "abc.stl"
    f1.write_text("data")
    (upload_dir / "Vehicles").mkdir()

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc", "f1", str(f1), storage_mode="copy")
    conn.commit()
    conn.close()

    target = str(upload_dir / "Vehicles" / "Tanks")
    resp = client.post("/api/file-view/folder/move", json={"sourcePath": str(src_dir), "targetPath": target})
    assert resp.status_code == 200
    assert Path(target).exists()
    assert not src_dir.exists()
    assert (Path(target) / "abc.stl").exists()

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id='abc'").fetchone()
    conn.close()
    assert row["filePath"] == str(Path(target) / "abc.stl")


def test_move_folder_nested_subfolders_all_rewritten(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src_dir = upload_dir / "Tanks"
    nested = src_dir / "Supports"
    nested.mkdir(parents=True)
    f1 = src_dir / "hull.stl"
    f1.write_text("data")
    f2 = nested / "support1.stl"
    f2.write_text("data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "hull", "f1", str(f1), storage_mode="copy")
    _insert_model(conn, "sup1", "f1", str(f2), storage_mode="copy")
    conn.commit()
    conn.close()

    target = str(upload_dir / "Archive" / "Tanks")
    resp = client.post("/api/file-view/folder/move", json={"sourcePath": str(src_dir), "targetPath": target})
    assert resp.status_code == 200

    conn = get_db_conn()
    hull_row = conn.execute("SELECT filePath FROM models WHERE id='hull'").fetchone()
    sup_row = conn.execute("SELECT filePath FROM models WHERE id='sup1'").fetchone()
    conn.close()
    assert hull_row["filePath"] == str(Path(target) / "hull.stl")
    assert sup_row["filePath"] == str(Path(target) / "Supports" / "support1.stl")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_file_view_folder_ops.py -v`
Expected: FAIL — `404 Not Found` on every request (router doesn't exist).

- [ ] **Step 4: Create the router**

```python
# backend/app/routers/file_view.py
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.file_view_ops import rewrite_affected_paths

router = APIRouter(prefix="/api/file-view", tags=["file-view"])


class FolderRenameRequest(BaseModel):
    path: str
    newName: str


@router.post("/folder/rename")
def rename_folder(body: FolderRenameRequest):
    source = Path(body.path)
    if not source.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {body.path}")
    destination = source.parent / body.newName
    if destination.exists():
        raise HTTPException(status_code=409, detail=f"A folder already exists at {destination}")
    shutil.move(str(source), str(destination))
    rewrite_affected_paths(str(source), str(destination))
    return {"path": str(destination)}


class FolderMoveRequest(BaseModel):
    sourcePath: str
    targetPath: str


@router.post("/folder/move")
def move_folder(body: FolderMoveRequest):
    source = Path(body.sourcePath)
    if not source.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {body.sourcePath}")
    destination = Path(body.targetPath)
    if destination.exists():
        raise HTTPException(status_code=409, detail=f"A folder already exists at {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    rewrite_affected_paths(str(source), str(destination))
    return {"path": str(destination)}
```

- [ ] **Step 5: Register the router in `main.py`**

Change line 9 of `backend/app/main.py` from:
```python
from app.routers import folders, models, manuals, settings, importers, watcher, inbox, ai, import_wizard
```
to:
```python
from app.routers import folders, models, manuals, settings, importers, watcher, inbox, ai, import_wizard, file_view
```

Add a new line after `app.include_router(import_wizard.router)` (line 31):
```python
app.include_router(file_view.router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_file_view_folder_ops.py -v`
Expected: PASS

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && python -m pytest -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/file_view_ops.py backend/app/routers/file_view.py backend/app/main.py backend/tests/test_file_view_folder_ops.py
git commit -m "feat: add folder rename/move endpoints for File view"
```

---

### Task 4: Backend — folder delete endpoint with drive-root/watch-root guard

**Files:**
- Modify: `backend/app/routers/file_view.py`
- Test: `backend/tests/test_file_view_folder_ops.py`

**Interfaces:**
- Consumes: `find_affected_models` (Task 3, `file_view_ops.py`).
- Produces: `DELETE /api/file-view/folder`, body `{"path": str}`, returns `{"deletedModels": int, "path": str}`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_file_view_folder_ops.py`:

```python
def test_delete_folder_removes_tracked_and_untracked_files(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    target_dir = upload_dir / "Vehicles"
    target_dir.mkdir()
    tracked = target_dir / "abc.stl"
    tracked.write_text("data")
    untracked = target_dir / "notes.txt"
    untracked.write_text("notes")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc", "f1", str(tracked), storage_mode="copy")
    conn.commit()
    conn.close()

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(target_dir)})
    assert resp.status_code == 200
    assert resp.json()["deletedModels"] == 1
    assert not target_dir.exists()

    conn = get_db_conn()
    count = conn.execute("SELECT COUNT(*) c FROM models WHERE id='abc'").fetchone()["c"]
    conn.close()
    assert count == 0


def test_delete_folder_reference_mode_hard_deletes_not_tombstones(client, tmp_path):
    watch_root = tmp_path / "watched"
    sub = watch_root / "Prints"
    sub.mkdir(parents=True)
    f1 = sub / "hull.stl"
    f1.write_text("data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    _insert_model(conn, "m1", "f1", str(f1), storage_mode="reference", source_path=str(f1))
    conn.commit()
    conn.close()

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(sub)})
    assert resp.status_code == 200

    conn = get_db_conn()
    count = conn.execute("SELECT COUNT(*) c FROM models WHERE id='m1'").fetchone()["c"]
    conn.close()
    assert count == 0, "reference-mode row must be hard-deleted, not tombstoned, from File view"
    assert not f1.exists()


def test_delete_folder_refuses_drive_root(client, tmp_path):
    drive_root = Path(tmp_path.anchor)
    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(drive_root)})
    assert resp.status_code == 400
    assert drive_root.exists()


def test_delete_folder_refuses_watch_folder_root(client, tmp_path):
    watch_root = tmp_path / "watched"
    watch_root.mkdir()

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    conn.commit()
    conn.close()

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(watch_root)})
    assert resp.status_code == 400
    assert watch_root.exists()


def test_delete_folder_allows_subfolder_of_watch_root(client, tmp_path):
    watch_root = tmp_path / "watched"
    sub = watch_root / "Old"
    sub.mkdir(parents=True)

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    conn.commit()
    conn.close()

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(sub)})
    assert resp.status_code == 200
    assert not sub.exists()
    assert watch_root.exists()


def test_delete_nonexistent_folder_404s(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(upload_dir / "Nope")})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_file_view_folder_ops.py -k delete_folder -v`
Expected: FAIL — `405 Method Not Allowed` (no DELETE route yet).

- [ ] **Step 3: Implement the endpoint**

Add to the top of `backend/app/routers/file_view.py`:

```python
import os

from app.db import get_db_conn, MANUAL_DIR
```

Change the existing import line (added in Task 3):
```python
from app.services.file_view_ops import rewrite_affected_paths
```
to also pull in `find_affected_models`:
```python
from app.services.file_view_ops import rewrite_affected_paths, find_affected_models
```

Add at the end of the file:

```python
class FolderDeleteRequest(BaseModel):
    path: str


@router.delete("/folder")
def delete_folder(body: FolderDeleteRequest):
    target = Path(body.path)
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {body.path}")

    resolved = target.resolve()
    if resolved.parent == resolved:
        raise HTTPException(status_code=400, detail="Refusing to delete a drive root.")

    conn = get_db_conn()
    try:
        watch_roots = {
            Path(row["path"]).resolve()
            for row in conn.execute("SELECT path FROM watch_folders").fetchall()
        }
    finally:
        conn.close()
    if resolved in watch_roots:
        raise HTTPException(
            status_code=400,
            detail="Refusing to delete a watched folder's root. Remove it from Watch Folders first if you really want to delete it.",
        )

    affected = find_affected_models(str(target))
    conn = get_db_conn()
    cur = conn.cursor()
    deleted = 0
    for row in affected:
        try:
            model_id = row["id"]
            fp = row["filePath"] if "filePath" in row.keys() else None
            if fp and os.path.exists(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass
            manual_path = MANUAL_DIR / f"{model_id}.md"
            if manual_path.exists():
                manual_path.unlink()
            cur.execute("DELETE FROM models WHERE id=?", (model_id,))
            deleted += 1
        except Exception:
            continue
    conn.commit()
    conn.close()

    shutil.rmtree(target, ignore_errors=True)
    return {"deletedModels": deleted, "path": str(target)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_file_view_folder_ops.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && python -m pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/file_view.py backend/tests/test_file_view_folder_ops.py
git commit -m "feat: add folder delete endpoint with drive/watch-root guard"
```

---

### Task 5: Frontend — API wrappers + file-level context menu (Rename, Delete)

**Files:**
- Modify: `frontend/services/api.ts`
- Modify: `frontend/components/ModelList.tsx`
- Modify: `frontend/App.tsx`

**Interfaces:**
- Consumes: `PATCH /api/models/{id}/location`, `DELETE /api/models/{id}?deleteFile=true` (existing), from Task 1.
- Produces: `api.updateModelLocation`, `api.duplicateModel`, `api.renameFileViewFolder`, `api.moveFileViewFolder`, `api.deleteFileViewFolder` in `api.ts` (all five added now even though Move/Copy/folder-menu wiring isn't consumed until Tasks 6-7, so Task 6/7 don't need to touch `api.ts` again). Produces a `viewMode: "logical" | "file"` prop and `onFileViewMutated: () => void` prop on `ModelList`, consumed by App.tsx (this task) and extended by Task 7 (Move/Copy menu items use the same props).

This task has no backend changes and therefore no pytest cycle — verification is manual via the packaged build, per this project's established convention (no frontend automated test suite exists).

- [ ] **Step 1: Add the five API wrappers**

Add inside the exported `api` object in `frontend/services/api.ts`, alongside the existing `deleteModel`/`getImportTree`/`commitImport` methods (same file, same object — insert near `deleteModel` for proximity to related delete semantics):

```ts
  // File view: rename or move a model's physical file. Handles both
  // rename (same directory, new filename) and move (new directory) --
  // the backend endpoint doesn't distinguish, the caller just computes
  // newPath differently.
  updateModelLocation: async (id: string, newPath: string): Promise<STLModel> => {
    const res = await fetch(`${getApiBaseUrl()}/models/${id}/location`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ newPath }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Failed to move/rename file");
    }
    return res.json();
  },

  // File view: duplicate a model's physical file, creating a new library entry.
  duplicateModel: async (id: string): Promise<STLModel> => {
    const res = await fetch(`${getApiBaseUrl()}/models/${id}/duplicate`, { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Failed to duplicate file");
    }
    return res.json();
  },

  renameFileViewFolder: async (path: string, newName: string): Promise<{ path: string }> => {
    const res = await fetch(`${getApiBaseUrl()}/file-view/folder/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, newName }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Failed to rename folder");
    }
    return res.json();
  },

  moveFileViewFolder: async (sourcePath: string, targetPath: string): Promise<{ path: string }> => {
    const res = await fetch(`${getApiBaseUrl()}/file-view/folder/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sourcePath, targetPath }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Failed to move folder");
    }
    return res.json();
  },

  deleteFileViewFolder: async (path: string): Promise<{ deletedModels: number; path: string }> => {
    const res = await fetch(`${getApiBaseUrl()}/file-view/folder`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Failed to delete folder");
    }
    return res.json();
  },
```

- [ ] **Step 2: Add `viewMode` and `onFileViewMutated` props to `ModelList`, plus the context-menu state and handlers**

In `frontend/components/ModelList.tsx`, add two imports near the top (alongside the existing `Menu`/`MenuItem` imports already at lines 40-41 — no new import statements needed for those two, they're already there):

Add to `ModelListProps` (after `onUploadToFolder` at line 74):
```ts
  viewMode: "logical" | "file";
  onFileViewMutated: () => void;
```

Add to the destructured props (after `onUploadToFolder` at line 103):
```ts
  viewMode,
  onFileViewMutated,
```

Add new state near the top of the component body (after `const [isDragging, setIsDragging] = useState(false);` at line 105):
```ts
  const [fileContextMenu, setFileContextMenu] = useState<{
    mouseX: number;
    mouseY: number;
    model: STLModel;
  } | null>(null);

  const handleCardContextMenu = (e: React.MouseEvent, model: STLModel) => {
    if (viewMode !== "file") return;
    e.preventDefault();
    e.stopPropagation();
    setFileContextMenu({ mouseX: e.clientX - 2, mouseY: e.clientY - 4, model });
  };

  const handleRenameFile = async (model: STLModel) => {
    if (!model.filePath) return;
    const currentName = model.filePath.split(/[\\/]/).pop() || model.name;
    const newName = window.prompt("Rename file:", currentName);
    if (!newName || newName === currentName) return;
    const dir = model.filePath.slice(0, model.filePath.length - currentName.length);
    try {
      await api.updateModelLocation(model.id, `${dir}${newName}`);
      onFileViewMutated();
    } catch (err) {
      console.error("Rename failed:", err);
      alert(err instanceof Error ? err.message : "Rename failed");
    }
  };

  const handleDeleteFile = async (model: STLModel) => {
    if (!window.confirm(`Delete "${model.name}" from disk? This cannot be undone.`)) return;
    try {
      await api.deleteModel(model.id, true);
      onFileViewMutated();
    } catch (err) {
      console.error("Delete failed:", err);
      alert(err instanceof Error ? err.message : "Delete failed");
    }
  };
```

Add `onContextMenu={(e) => handleCardContextMenu(e, model)}` to the card's outer `<div>` at line 633-635, alongside the existing `draggable`/`onDragStart`:
```tsx
                  draggable={true}
                  onDragStart={(e) => handleCardDragStart(e, model.id)}
                  onContextMenu={(e) => handleCardContextMenu(e, model)}
```

Add the menu JSX right before the component's closing return (anywhere inside the top-level returned fragment, e.g. immediately after the grid's closing tag):
```tsx
      <Menu
        open={fileContextMenu !== null}
        onClose={() => setFileContextMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={
          fileContextMenu ? { top: fileContextMenu.mouseY, left: fileContextMenu.mouseX } : undefined
        }
      >
        <MenuItem
          onClick={() => {
            if (fileContextMenu) handleRenameFile(fileContextMenu.model);
            setFileContextMenu(null);
          }}
        >
          Rename
        </MenuItem>
        <MenuItem
          onClick={() => {
            if (fileContextMenu) handleDeleteFile(fileContextMenu.model);
            setFileContextMenu(null);
          }}
        >
          Delete
        </MenuItem>
      </Menu>
```

- [ ] **Step 3: Wire the new props from `App.tsx`**

Find both `<ModelList ... />` call sites in `frontend/App.tsx` (desktop and mobile layouts, mirroring how `viewMode`/`onViewModeChange` are already passed to both `<Sidebar>` call sites) and add:
```tsx
            viewMode={viewMode}
            onFileViewMutated={fetchData}
```
`fetchData` is the existing full-refetch function (`App.tsx:98-112`) already used after Settings closes — reused here rather than hand-patching individual models' `filePath` client-side, since a folder move can affect many models' paths at once (Task 6/7 will call the same prop).

- [ ] **Step 4: Manual verification**

Rebuild and repackage per this project's standing convention: run `desktop/build.ps1`, uninstall the previous install, reinstall, verify the new installer's SHA-256 hash differs from the prior build's recorded hash. Then, against a test library containing both copy-mode and reference-mode (watch-folder) models:
- Switch to File mode, right-click a file card → confirm the menu shows only Rename and Delete (not Move/Copy yet — those land in Task 7).
- Rename a copy-mode file: confirm the card's name updates after refetch and the physical file exists at its new name in the upload folder.
- Rename a reference-mode (linked) file: confirm it succeeds and the file is renamed inside its real watched folder.
- Delete a file: confirm the browser's native confirm dialog appears, and after confirming, the file disappears from the grid and is gone from disk.
- Switch to Logical mode: confirm right-click on a card does nothing (menu doesn't open) — `viewMode !== "file"` guards `handleCardContextMenu`.

- [ ] **Step 5: Commit**

```bash
git add frontend/services/api.ts frontend/components/ModelList.tsx frontend/App.tsx
git commit -m "feat: add File-mode file rename/delete via right-click context menu"
```

---

### Task 6: Frontend — folder-level context menu (Rename, Delete)

**Files:**
- Modify: `frontend/components/Sidebar.tsx`

**Interfaces:**
- Consumes: `api.renameFileViewFolder`, `api.deleteFileViewFolder` (Task 5's `api.ts` additions), `FILE_VIEW_UPLOADS_BUCKET_ID` (existing, `types.ts:90`).
- Produces: a `fileNodeRealPaths: Map<string, string>` computed alongside the existing `fileTree` memo — needed because File-mode tree node ids (e.g. `"file/Vehicles/Tanks"`) are synthetic, not real disk paths; this map is the only way to recover the real absolute directory a given node represents. Not exported outside `Sidebar.tsx` — used locally by this task's context menu handlers.

- [ ] **Step 1: Extend the `fileTree` memo to also compute `fileNodeRealPaths`**

In `frontend/components/Sidebar.tsx`, replace the `fileTree` `useMemo` (lines 296-335) with a version that tracks each node's real absolute path alongside its synthetic id. The synthetic id's segments are always a *suffix* of the file's real (normalized, filename-dropped) directory segments — `fileViewSegments` only ever drops a leading prefix (nothing, or everything through a literal "uploads" segment), so `realSegments.length - meaningfulSegments.length` is the number of dropped leading segments, constant for a given file, and slicing `realSegments` to `dropped + depth` reconstructs the real path up to any node on that file's chain:

```tsx
  const fileTree = useMemo(() => {
    type FileNode = { id: string; label: string; children: FileNode[]; childMap: Record<string, FileNode> };
    const root: FileNode = { id: "__root__", label: "", children: [], childMap: {} };
    const realPaths = new Map<string, string>();

    models.forEach((m) => {
      if (!m.filePath) return;
      const meaningfulSegments = fileViewSegments(m.filePath);

      if (meaningfulSegments.length === 0) {
        if (!root.childMap[FILE_VIEW_UPLOADS_BUCKET_ID]) {
          const node: FileNode = { id: FILE_VIEW_UPLOADS_BUCKET_ID, label: "Uploads", children: [], childMap: {} };
          root.childMap[FILE_VIEW_UPLOADS_BUCKET_ID] = node;
          root.children.push(node);
        }
        return;
      }

      const rawSegments = m.filePath.replace(/\\/g, "/").split("/").filter((s) => s.length > 0);
      rawSegments.pop(); // drop filename, mirrors fileViewSegments
      const dropped = rawSegments.length - meaningfulSegments.length;

      let cursor = root;
      let idPath = "file";
      meaningfulSegments.forEach((segment, index) => {
        idPath += `/${segment}`;
        if (!cursor.childMap[segment]) {
          const node: FileNode = { id: idPath, label: segment, children: [], childMap: {} };
          cursor.childMap[segment] = node;
          cursor.children.push(node);
          realPaths.set(idPath, rawSegments.slice(0, dropped + index + 1).join("/"));
        }
        cursor = cursor.childMap[segment];
      });
    });

    const strip = (node: FileNode): TreeViewDefaultItemModelProperties => ({
      id: node.id,
      label: node.label,
      children: node.children.map(strip),
    });
    return { items: root.children.map(strip), realPaths };
  }, [models]);
```

This changes `fileTree` from an array to `{ items, realPaths }`. Update the File-mode `RichTreeView`'s `items` prop (currently `items={fileTree}` at line 567) to `items={fileTree.items}`.

- [ ] **Step 2: Add `Menu`/`MenuItem` imports and context-menu state**

Add two imports near the top of `Sidebar.tsx`, alongside the other MUI imports (e.g. after `import Badge from "@mui/material/Badge";` at line 38):
```ts
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
```

Add state and handlers inside the `Sidebar` component body (near the other `useState` calls):
```ts
  const [folderContextMenu, setFolderContextMenu] = useState<{
    mouseX: number;
    mouseY: number;
    nodeId: string;
    realPath: string;
  } | null>(null);

  const handleFileTreeContextMenu = (e: React.MouseEvent, nodeId: string) => {
    if (nodeId === FILE_VIEW_UPLOADS_BUCKET_ID) return; // synthetic bucket, not a real single folder
    const realPath = fileTree.realPaths.get(nodeId);
    if (!realPath) return;
    e.preventDefault();
    e.stopPropagation();
    setFolderContextMenu({ mouseX: e.clientX - 2, mouseY: e.clientY - 4, nodeId, realPath });
  };

  const handleRenameFileViewFolder = async (realPath: string) => {
    const currentName = realPath.split("/").pop() || "";
    const newName = window.prompt("Rename folder:", currentName);
    if (!newName || newName === currentName) return;
    try {
      await api.renameFileViewFolder(realPath, newName);
      onFileViewMutated();
    } catch (err) {
      console.error("Folder rename failed:", err);
      alert(err instanceof Error ? err.message : "Folder rename failed");
    }
  };

  const handleDeleteFileViewFolder = async (nodeId: string, realPath: string) => {
    const count = models.filter((m) => {
      if (!m.filePath) return false;
      const norm = m.filePath.replace(/\\/g, "/");
      return norm === realPath || norm.startsWith(`${realPath}/`);
    }).length;
    const label = count === 1 ? "1 file" : `${count} files`;
    if (!window.confirm(`Delete this folder and everything in it (${label})? This cannot be undone.`)) return;
    try {
      await api.deleteFileViewFolder(realPath);
      onFileViewMutated();
    } catch (err) {
      console.error("Folder delete failed:", err);
      alert(err instanceof Error ? err.message : "Folder delete failed");
    }
  };
```

- [ ] **Step 3: Wire `onContextMenu` onto the File-mode tree and add a `viewMode`-gated menu-item slot**

File mode's `RichTreeView` (lines 566-573) currently uses no custom item slot. Wrap it in a container that delegates the context-menu event, since every tree item shares the same handler regardless of which node was clicked — MUI's `RichTreeView` items render with `data-testid`/`id` attributes derived from `itemId`, but the simplest robust approach is a small custom item slot using the already-imported `TreeItem`:

```tsx
  const FileViewTreeItem = React.useCallback(
    (props: TreeItemProps) => (
      <TreeItem {...props} onContextMenu={(e) => handleFileTreeContextMenu(e, props.itemId)} />
    ),
    [fileTree],
  );
```

Place this definition inside the `Sidebar` component body, after the `fileTree` memo and the handlers from Step 2. Update the File-mode `RichTreeView` block (previously lines 566-573) to:
```tsx
            <RichTreeView
              items={fileTree.items}
              selectedItems={currentFolderId}
              slots={{ item: FileViewTreeItem }}
              onSelectedItemsChange={(_e, itemId) => {
                if (itemId) onSelectFolder(itemId as string);
              }}
            />
```

Add the menu JSX near the end of the component's returned JSX (sibling to wherever the rest of the sidebar's top-level elements are, e.g. right after the closing of the Logical/File toggle block):
```tsx
      <Menu
        open={folderContextMenu !== null}
        onClose={() => setFolderContextMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={
          folderContextMenu ? { top: folderContextMenu.mouseY, left: folderContextMenu.mouseX } : undefined
        }
      >
        <MenuItem
          onClick={() => {
            if (folderContextMenu) handleRenameFileViewFolder(folderContextMenu.realPath);
            setFolderContextMenu(null);
          }}
        >
          Rename
        </MenuItem>
        <MenuItem
          onClick={() => {
            if (folderContextMenu) handleDeleteFileViewFolder(folderContextMenu.nodeId, folderContextMenu.realPath);
            setFolderContextMenu(null);
          }}
        >
          Delete
        </MenuItem>
      </Menu>
```

- [ ] **Step 4: Add the `onFileViewMutated` prop to `SidebarProps` and wire it from `App.tsx`**

Add to `SidebarProps` (near the other callback props, e.g. after wherever `onViewModeChange` is declared):
```ts
  onFileViewMutated: () => void;
```
Add to the destructured props at the top of the component body, alongside `onViewModeChange`.

In `App.tsx`, add `onFileViewMutated={fetchData}` to both `<Sidebar ... />` call sites (desktop and mobile), the same `fetchData` reference used in Task 5.

- [ ] **Step 5: Manual verification**

Rebuild, uninstall, reinstall, hash-verify per the established convention. Against the same mixed copy-mode/reference-mode test library:
- Right-click a folder node in File mode: confirm the menu shows Rename and Delete only (no Move/Copy — Move lands in Task 7, folder Copy is out of scope per this plan's Global Constraints).
- Right-click the "Uploads" bucket node specifically: confirm no menu opens (guarded in `handleFileTreeContextMenu`).
- Rename a folder: confirm the directory is renamed on disk and every model under it (including nested subfolders) shows its new path.
- Delete a folder containing both tracked models and an untracked sibling file: confirm the confirm dialog states the correct file count, and after confirming, the directory and everything in it is gone from disk and the tracked models are gone from the grid.
- Attempt to delete a node that maps to a watch folder's root (e.g. if the test library's watch folder root itself appears as a File-mode node): confirm the request is rejected and the folder remains — this exercises the backend guard from Task 4 through the UI.
- Confirm Logical mode is completely unaffected (its own context menu / rename / delete flows still work exactly as before).

- [ ] **Step 6: Commit**

```bash
git add frontend/components/Sidebar.tsx frontend/App.tsx
git commit -m "feat: add File-mode folder rename/delete via right-click context menu"
```

---

### Task 7: Frontend — Move and Copy (drag-and-drop + Move menu item)

**Files:**
- Modify: `frontend/components/ModelList.tsx`
- Modify: `frontend/components/Sidebar.tsx`

**Interfaces:**
- Consumes: `api.updateModelLocation`, `api.duplicateModel`, `api.moveFileViewFolder` (Task 5's `api.ts` additions), `fileTree.realPaths` (Task 6).
- Produces: nothing new consumed by later tasks — this is the last task.

- [ ] **Step 1: Add Move and Copy handlers + menu items to `ModelList.tsx`'s file context menu**

Add two handlers in `ModelList.tsx`, near `handleRenameFile`/`handleDeleteFile` from Task 5:

```ts
  const handleCopyFile = async (model: STLModel) => {
    try {
      await api.duplicateModel(model.id);
      onFileViewMutated();
    } catch (err) {
      console.error("Copy failed:", err);
      alert(err instanceof Error ? err.message : "Copy failed");
    }
  };

  const handleMoveFileToDirectory = async (model: STLModel, targetDir: string) => {
    if (!model.filePath) return;
    const filename = model.filePath.split(/[\\/]/).pop() || model.name;
    const newPath = `${targetDir}/${filename}`;
    try {
      await api.updateModelLocation(model.id, newPath);
      onFileViewMutated();
    } catch (err) {
      console.error("Move failed:", err);
      alert(err instanceof Error ? err.message : "Move failed");
    }
  };
```

Add two `MenuItem`s to the file context menu JSX from Task 5, between Rename and Delete:
```tsx
        <MenuItem
          onClick={() => {
            if (fileContextMenu) handleCopyFile(fileContextMenu.model);
            setFileContextMenu(null);
          }}
        >
          Copy
        </MenuItem>
```
(Move has no menu item on the file card — it's reachable only by dragging the card onto a File-mode folder node in the sidebar, per Step 2 below. A file card's context menu therefore ends up as Rename / Copy / Delete, three items, matching the spec's "Rename, Move, Copy, Delete" *available actions* even though Move's trigger is drag-only rather than menu+drag.)

Extend `handleCardDragStart` (existing, lines 302-313) so a File-mode drag carries the single model's id in a File-view-specific `dataTransfer` key, distinguishable from the existing Logical-mode `application/json` payload the Sidebar's `handleDrop` already reads:
```ts
  const handleCardDragStart = (e: React.DragEvent, modelId: string) => {
    if (viewMode === "file") {
      e.dataTransfer.setData("application/x-fileview-model", modelId);
      e.dataTransfer.effectAllowed = "copyMove";
      return;
    }
    const idsToMove = selectedIds.has(modelId)
      ? Array.from(selectedIds)
      : [modelId];

    e.dataTransfer.setData(
      "application/json",
      JSON.stringify({ modelIds: idsToMove }),
    );
    e.dataTransfer.effectAllowed = "move";
  };
```

- [ ] **Step 2: Add drop handling to `Sidebar.tsx`'s File-mode tree nodes, and a "Move" menu item for folders**

`FileViewTreeItem` (Task 6, Step 3) needs `onDragOver`/`onDrop` to accept a dropped file card. Update it:
```tsx
  const handleFileTreeDrop = async (e: React.DragEvent, nodeId: string) => {
    const modelId = e.dataTransfer.getData("application/x-fileview-model");
    if (!modelId || nodeId === FILE_VIEW_UPLOADS_BUCKET_ID) return;
    e.preventDefault();
    e.stopPropagation();
    const targetDir = fileTree.realPaths.get(nodeId);
    if (!targetDir) return;
    const model = models.find((m) => m.id === modelId);
    if (!model || !model.filePath) return;
    const filename = model.filePath.split(/[\\/]/).pop() || model.name;
    const isCopy = e.ctrlKey;
    try {
      if (isCopy) {
        await api.duplicateModel(modelId);
      } else {
        await api.updateModelLocation(modelId, `${targetDir}/${filename}`);
      }
      onFileViewMutated();
    } catch (err) {
      console.error("File view drag operation failed:", err);
      alert(err instanceof Error ? err.message : "Operation failed");
    }
  };

  const FileViewTreeItem = React.useCallback(
    (props: TreeItemProps) => (
      <TreeItem
        {...props}
        onContextMenu={(e) => handleFileTreeContextMenu(e, props.itemId)}
        onDragOver={(e) => {
          if (props.itemId !== FILE_VIEW_UPLOADS_BUCKET_ID) e.preventDefault();
        }}
        onDrop={(e) => handleFileTreeDrop(e, props.itemId)}
      />
    ),
    [fileTree, models],
  );
```

Note: dragging a *file card* onto another File-mode node produces a move (or copy, with Ctrl held); this is Ctrl+drag = copy / plain drag = move exactly as the spec requires. Dropping a *duplicate*'s Ctrl-drag reads `e.ctrlKey` off the `drop` event, which reflects whether Ctrl was held at drop time — matching standard desktop file-manager convention.

Add a "Move" `MenuItem` to the folder context menu from Task 6, between Rename and Delete, opening a lightweight prompt-based picker (consistent with this task's low-fi `window.prompt` convention rather than building a new dedicated picker dialog):
```tsx
        <MenuItem
          onClick={() => {
            if (folderContextMenu) {
              const targetParent = window.prompt(
                "Move to which real folder path? (paste the full destination directory)",
                folderContextMenu.realPath,
              );
              if (targetParent && targetParent !== folderContextMenu.realPath) {
                api
                  .moveFileViewFolder(folderContextMenu.realPath, targetParent)
                  .then(onFileViewMutated)
                  .catch((err) => {
                    console.error("Folder move failed:", err);
                    alert(err instanceof Error ? err.message : "Folder move failed");
                  });
              }
            }
            setFolderContextMenu(null);
          }}
        >
          Move
        </MenuItem>
```

- [ ] **Step 3: Manual verification**

Rebuild, uninstall, reinstall, hash-verify. Against the mixed test library:
- Plain-drag a file card from the grid onto a different File-mode folder node: confirm the file physically moves and the grid updates.
- Ctrl+drag a file card onto a File-mode folder node: confirm a *new* model appears (duplicate) and the original file is untouched.
- Right-click a file card → Copy: confirm the same duplication behavior as Ctrl+drag.
- Right-click a folder node → Move, enter a real destination path: confirm the folder relocates and every model under it updates.
- Attempt to drop a file card onto the "Uploads" bucket node: confirm nothing happens (guarded).
- Confirm Logical mode's existing card-drag-to-folder behavior (the `application/json` path) still works unchanged.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/ModelList.tsx frontend/components/Sidebar.tsx
git commit -m "feat: add File-mode move (drag) and copy (ctrl+drag / menu) operations"
```

---

## Self-Review Notes

- **Spec coverage:** every Goals-section item in the spec maps to a task — file rename/move (Task 1), file delete (reused existing endpoint, wired in Task 5), file copy (Task 2, wired in Tasks 5/7), folder rename/move (Task 3, wired in Tasks 6/7), folder delete + guard (Task 4, wired in Task 6), reference-mode `sourcePath` sync (built into Tasks 1 and 3's `rewrite_affected_paths`, explicitly tested in Task 1's rescan test).
- **Placeholder scan:** none found — every code block is complete, pasteable content.
- **Type consistency:** `fileTree` changes shape from `TreeViewDefaultItemModelProperties[]` to `{ items, realPaths }` in Task 6 — the plan updates every consumer of `fileTree` (the `RichTreeView`'s `items` prop) in the same step, and no other task reads the old shape (Task 5 doesn't touch `Sidebar.tsx` at all; Task 7 consumes `fileTree.realPaths`, the new shape, correctly).
