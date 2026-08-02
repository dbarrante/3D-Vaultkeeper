# Watcher & Inbox (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement requirements #2/#3/#4 (watch configured folders, auto-add new 3D files on a schedule), #9 (catch files landing in Downloads and hold them for a file/dismiss decision instead of auto-adding), and #16 (on-demand whole-drive/arbitrary-path consolidation) — all backend API + a background scheduler. Frontend panels (`WatcherSettings.tsx`, `InboxPanel.tsx` from `docs/ARCHITECTURE.md`) are a follow-up once this API exists, the same way Phase 0 added the vitest harness without yet building new UI.

**Architecture:** Two new DB tables (`watch_folders`, `inbox_items`) plus one new `models.sourcePath` column for dedup. A small, dependency-free scan layer (`services/scan.py`) with pure, directly-testable functions — no `watchdog` filesystem-event library, because requirement #2 literally asks for periodic polling ("scan... every set frequency"), and a poll is simpler and more predictable across local/NAS/network-mounted folders than OS-level file-event watching, which matters more for "stability is the core requirement" than real-time detection latency does. A single `asyncio` background task (no APScheduler dependency) drives the periodic side; on-demand scans (`#16`, "scan now" buttons) call the same functions synchronously from a route handler.

**Tech Stack:** Same as Phase 0 — Python 3.9-syntax-compatible, FastAPI, SQLite via `app/db.py`, pytest. No new third-party dependencies.

## Global Constraints

- Builds on Phase 0's `services/ingestion.py::ingest_file` — do not duplicate its file-copy/DB-insert logic; extend its signature if new behavior is needed (Task 2 does this once).
- Schema changes stay additive-only, same rule as Phase 0's Global Constraints.
- No existing route from Phase 0 changes shape — this phase only adds new routes (`/api/watch-folders*`, `/api/inbox*`, `/api/drive-scan`) and one additive `sourcePath` field on `GET/PATCH /api/models*` responses.
- The background scheduler must not block app startup or make the app unable to serve requests if a scan fails — one bad watched folder (deleted from disk, permission error) logs/records the failure and skips it, never crashes the loop.
- Python 3.9 syntax (`Optional`/`Union`, not `X | Y`) — same as Phase 0.

---

### Task 1: Schema — `watch_folders`, `inbox_items`, `models.sourcePath`

**Files:**
- Modify: `backend/app/db.py` (`init_db`)
- Create: `backend/tests/test_watcher_schema.py`

**Interfaces:**
- Produces: `watch_folders(id, path UNIQUE, folderId, frequencyMinutes, lastScanAt, enabled)`, `inbox_items(id, path UNIQUE, detectedAt, status)`, and `models.sourcePath TEXT` (nullable — the absolute path a watched-folder-ingested file came from; used by Task 2's dedup check; `NULL` for browser-uploaded or acquisition-queue-ingested models).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_watcher_schema.py
import sqlite3


def test_watch_folders_and_inbox_tables_exist(client):
    from app.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"watch_folders", "inbox_items"}.issubset(tables)


def test_models_table_has_source_path_column(client):
    from app.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(models)")}
    conn.close()
    assert "sourcePath" in columns
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && pytest tests/test_watcher_schema.py -v`
Expected: FAIL — tables/column don't exist yet.

- [ ] **Step 3: Add the schema, in `app/db.py::init_db`, after the Phase 0 column loop**

```python
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS watch_folders (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            folderId TEXT NOT NULL,
            frequencyMinutes INTEGER NOT NULL DEFAULT 60,
            lastScanAt INTEGER,
            enabled INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS inbox_items (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            detectedAt INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    try:
        cur.execute("ALTER TABLE models ADD COLUMN sourcePath TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
```

Also add `"sourcePath": row["sourcePath"] if "sourcePath" in row.keys() else None,` to `row_to_model` in the same file.

- [ ] **Step 4: Run again to confirm it passes**

Run: `cd backend && pytest tests/test_watcher_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite to confirm no Phase 0 regression**

Run: `cd backend && pytest -v`
Expected: all 28 Phase 0 tests + 2 new ones PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/tests/test_watcher_schema.py
git commit -m "feat: add watch_folders/inbox_items tables and models.sourcePath column"
```

---

### Task 2: `ingest_file` gains `record_source` — the dedup hook Task 4 needs

**Files:**
- Modify: `backend/app/services/ingestion.py`
- Modify: `backend/tests/test_ingestion.py`

**Interfaces:**
- Produces: `ingest_file(..., record_source: bool = False)` — when `True`, persists `source_path` (the function's existing first parameter — the file's on-disk location) into the new `models.sourcePath` column. Default `False` preserves Phase 0 behavior exactly for `upload_model`'s disposable temp files, where recording the temp path would be meaningless (it's deleted right after).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ingestion.py — append
def test_ingest_file_with_record_source_persists_source_path(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    source = tmp_path / "watched.stl"
    source.write_bytes(b"solid watched endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="watched.stl", record_source=True)

    conn = get_db_conn()
    row = conn.execute("SELECT sourcePath FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row["sourcePath"] == str(source)


def test_ingest_file_without_record_source_leaves_source_path_null(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    source = tmp_path / "uploaded.stl"
    source.write_bytes(b"solid uploaded endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="uploaded.stl")  # record_source defaults False

    conn = get_db_conn()
    row = conn.execute("SELECT sourcePath FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row["sourcePath"] is None
```

- [ ] **Step 2: Run to confirm the first test fails**

Run: `cd backend && pytest tests/test_ingestion.py -v`
Expected: `test_ingest_file_with_record_source_persists_source_path` FAILS (`sourcePath` column exists from Task 1, but nothing writes to it yet); the second test passes trivially since the column is `NULL` by default.

- [ ] **Step 3: Implement**

```python
# backend/app/services/ingestion.py
def ingest_file(
    source_path: str,
    folder_id: str,
    original_filename: str,
    tags: Optional[List[str]] = None,
    thumbnail: Optional[str] = None,
    move: bool = False,
    record_source: bool = False,
) -> dict:
    """...(existing docstring)...
    record_source=True additionally persists source_path into models.sourcePath,
    so a later scan of the same folder (Task 4) can tell this file was already
    ingested and skip it. Only meaningful with move=False (the watcher's case) —
    recording a path that ingest_file itself just deleted via move=True would
    record a path that no longer points at anything.
    """
    mid = str(uuid.uuid4())
    ext = os.path.splitext(original_filename)[1] or ".stl"
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
        "description": "",
        "thumbnail": thumbnail,
    }

    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,sourcePath) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            model["id"], model["name"], model["folderId"], model["url"], model["size"],
            model["dateAdded"], json.dumps(model["tags"]), model["description"], model["thumbnail"],
            source_path if record_source else None,
        ),
    )
    conn.commit()
    conn.close()
    return model
```

- [ ] **Step 4: Run to confirm both tests pass**

Run: `cd backend && pytest tests/test_ingestion.py -v`
Expected: 4/4 PASS (2 from Phase 0, 2 new).

- [ ] **Step 5: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all PASS — `upload_model` (Task 12 from Phase 0) doesn't pass `record_source`, so it keeps defaulting to `False`; unaffected.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ingestion.py backend/tests/test_ingestion.py
git commit -m "feat: ingest_file(record_source=) persists sourcePath for watched-folder dedup"
```

---

### Task 3: `services/scan.py` — extension filter + `find_new_files` (pure, no DB/network)

**Files:**
- Create: `backend/app/services/scan.py`
- Create: `backend/tests/test_scan.py`

**Interfaces:**
- Produces: `SUPPORTED_EXTENSIONS: set[str]`, `is_supported_3d_file(path: Path) -> bool`, `find_new_files(root: Path, already_seen: set[str]) -> list[Path]` — recursively walks `root`, returns absolute paths of files whose extension is in `SUPPORTED_EXTENSIONS` and whose absolute path (as a string) is not in `already_seen`. Deliberately takes `already_seen` as a plain parameter rather than querying the DB itself — keeps this function testable with nothing but a temp directory, and reusable for both the watch-folder case (`already_seen` = `models.sourcePath` values) and the Downloads-inbox case (`already_seen` = `models.sourcePath` ∪ `inbox_items.path`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_scan.py
from pathlib import Path


def test_is_supported_3d_file():
    from app.services.scan import is_supported_3d_file
    assert is_supported_3d_file(Path("model.stl")) is True
    assert is_supported_3d_file(Path("model.3MF")) is True  # case-insensitive
    assert is_supported_3d_file(Path("readme.txt")) is False
    assert is_supported_3d_file(Path("model.stl.zip")) is False


def test_find_new_files_returns_only_unseen_supported_files(tmp_path):
    from app.services.scan import find_new_files

    (tmp_path / "keep.stl").write_bytes(b"solid keep endsolid")
    (tmp_path / "already_ingested.3mf").write_bytes(b"fake 3mf")
    (tmp_path / "notes.txt").write_bytes(b"not a model")
    sub = tmp_path / "subfolder"
    sub.mkdir()
    (sub / "nested.step").write_bytes(b"fake step")

    already_seen = {str(tmp_path / "already_ingested.3mf")}
    found = find_new_files(tmp_path, already_seen)
    found_names = {p.name for p in found}

    assert found_names == {"keep.stl", "nested.step"}  # recursive, excludes seen, excludes non-3D
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/test_scan.py -v`
Expected: FAIL — `app.services.scan` doesn't exist.

- [ ] **Step 3: Implement**

```python
# backend/app/services/scan.py
import os
from pathlib import Path
from typing import List, Set

SUPPORTED_EXTENSIONS = {".stl", ".3mf", ".obj", ".step", ".stp"}


def is_supported_3d_file(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def find_new_files(root: Path, already_seen: Set[str]) -> List[Path]:
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            candidate = Path(dirpath) / fname
            if is_supported_3d_file(candidate) and str(candidate) not in already_seen:
                found.append(candidate)
    return found
```

- [ ] **Step 4: Run to confirm both pass**

Run: `cd backend && pytest tests/test_scan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scan.py backend/tests/test_scan.py
git commit -m "feat: add services/scan.py — supported-extension filter and recursive new-file finder"
```

---

### Task 4: `scan_watch_folder` — the #2/#3/#4 auto-ingest path

**Files:**
- Modify: `backend/app/services/scan.py`
- Modify: `backend/tests/test_scan.py`

**Interfaces:**
- Produces: `scan_watch_folder(watch_folder_row: dict) -> int` — finds new supported files under `watch_folder_row["path"]` not already recorded via `models.sourcePath`, ingests each with `ingest_file(..., folder_id=watch_folder_row["folderId"], record_source=True)`, updates `watch_folders.lastScanAt`, returns the count ingested. A file that fails to ingest (permission error mid-copy, disappears between discovery and copy) is skipped and logged, not fatal to the rest of the scan — satisfies the Global Constraint that one bad file/folder can't take down the scheduler.
- Consumes: `app.services.scan.find_new_files`, `app.services.ingestion.ingest_file`, `app.db.get_db_conn`, `app.db.now_ms`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_scan.py — append
def test_scan_watch_folder_ingests_new_files_and_updates_last_scan(client, tmp_path):
    from app.services.scan import scan_watch_folder
    from app.db import get_db_conn, now_ms

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    (watched_dir / "a.stl").write_bytes(b"solid a endsolid")
    (watched_dir / "b.3mf").write_bytes(b"fake b")

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId,frequencyMinutes,lastScanAt,enabled) VALUES (?,?,?,?,?,?)",
        ("wf1", str(watched_dir), "1", 60, None, 1),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM watch_folders WHERE id=?", ("wf1",)).fetchone()
    conn.close()

    before = now_ms()
    count = scan_watch_folder(dict(row))
    assert count == 2

    conn = get_db_conn()
    models = conn.execute("SELECT name, sourcePath, folderId FROM models").fetchall()
    updated = conn.execute("SELECT lastScanAt FROM watch_folders WHERE id=?", ("wf1",)).fetchone()
    conn.close()

    names = {m["name"] for m in models}
    assert names == {"a.stl", "b.3mf"}
    assert all(m["folderId"] == "1" for m in models)
    assert all(m["sourcePath"] is not None for m in models)
    assert updated["lastScanAt"] >= before


def test_scan_watch_folder_skips_already_ingested_files(client, tmp_path):
    from app.services.scan import scan_watch_folder
    from app.db import get_db_conn

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    (watched_dir / "a.stl").write_bytes(b"solid a endsolid")

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId,frequencyMinutes,lastScanAt,enabled) VALUES (?,?,?,?,?,?)",
        ("wf1", str(watched_dir), "1", 60, None, 1),
    )
    conn.commit()
    row = dict(conn.execute("SELECT * FROM watch_folders WHERE id=?", ("wf1",)).fetchone())
    conn.close()

    first_count = scan_watch_folder(row)
    second_count = scan_watch_folder(row)  # nothing new since the first scan

    assert first_count == 1
    assert second_count == 0
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/test_scan.py -v`
Expected: FAIL — `scan_watch_folder` not defined.

- [ ] **Step 3: Implement**

```python
# backend/app/services/scan.py — append
from app.db import get_db_conn, now_ms
from app.services.ingestion import ingest_file


def scan_watch_folder(watch_folder_row: dict) -> int:
    conn = get_db_conn()
    seen_rows = conn.execute("SELECT sourcePath FROM models WHERE sourcePath IS NOT NULL").fetchall()
    already_seen = {r["sourcePath"] for r in seen_rows}
    conn.close()

    root = Path(watch_folder_row["path"])
    if not root.exists():
        return 0  # folder deleted/unmounted since it was configured — skip, don't crash the loop

    ingested = 0
    for file_path in find_new_files(root, already_seen):
        try:
            ingest_file(
                str(file_path),
                folder_id=watch_folder_row["folderId"],
                original_filename=file_path.name,
                record_source=True,
            )
            ingested += 1
        except Exception:
            continue  # one bad file (permission error, vanished mid-scan) doesn't stop the rest

    conn = get_db_conn()
    conn.execute("UPDATE watch_folders SET lastScanAt=? WHERE id=?", (now_ms(), watch_folder_row["id"]))
    conn.commit()
    conn.close()
    return ingested
```

- [ ] **Step 4: Run to confirm both tests pass**

Run: `cd backend && pytest tests/test_scan.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/scan.py backend/tests/test_scan.py
git commit -m "feat: scan_watch_folder — auto-ingest new files from a configured watched folder"
```

---

### Task 5: `routers/watcher.py` — CRUD + on-demand scan for watched folders

**Files:**
- Create: `backend/app/routers/watcher.py`
- Create: `backend/tests/test_watcher_router.py`
- Modify: `backend/app/main.py` (mount the router)

**Interfaces:**
- Produces: `GET /api/watch-folders`, `POST /api/watch-folders` (`{path, folderId, frequencyMinutes}`), `DELETE /api/watch-folders/{id}`, `POST /api/watch-folders/{id}/scan-now` (calls `scan_watch_folder` synchronously, returns `{ingested: int}`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_watcher_router.py
def test_create_and_list_watch_folder(client, tmp_path):
    response = client.post(
        "/api/watch-folders",
        json={"path": str(tmp_path), "folderId": "1", "frequencyMinutes": 30},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == str(tmp_path)
    assert body["frequencyMinutes"] == 30

    listed = client.get("/api/watch-folders").json()
    assert any(w["id"] == body["id"] for w in listed)


def test_create_watch_folder_rejects_missing_path(client):
    response = client.post("/api/watch-folders", json={"path": "/does/not/exist", "folderId": "1"})
    assert response.status_code == 400


def test_delete_watch_folder(client, tmp_path):
    created = client.post("/api/watch-folders", json={"path": str(tmp_path), "folderId": "1"}).json()
    response = client.delete(f"/api/watch-folders/{created['id']}")
    assert response.status_code == 200
    listed = client.get("/api/watch-folders").json()
    assert all(w["id"] != created["id"] for w in listed)


def test_scan_now_ingests_and_returns_count(client, tmp_path):
    (tmp_path / "a.stl").write_bytes(b"solid a endsolid")
    created = client.post("/api/watch-folders", json={"path": str(tmp_path), "folderId": "1"}).json()

    response = client.post(f"/api/watch-folders/{created['id']}/scan-now")
    assert response.status_code == 200
    assert response.json() == {"ingested": 1}

    models = client.get("/api/models", params={"folderId": "1"}).json()
    assert any(m["name"] == "a.stl" for m in models)
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/test_watcher_router.py -v`
Expected: FAIL — 404s, route doesn't exist.

- [ ] **Step 3: Implement**

```python
# backend/app/routers/watcher.py
import os
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.db import get_db_conn
from app.services.scan import scan_watch_folder

router = APIRouter()


class WatchFolderData(BaseModel):
    path: str
    folderId: str
    frequencyMinutes: Optional[int] = 60


def row_to_watch_folder(row) -> dict:
    return {
        "id": row["id"],
        "path": row["path"],
        "folderId": row["folderId"],
        "frequencyMinutes": row["frequencyMinutes"],
        "lastScanAt": row["lastScanAt"],
        "enabled": bool(row["enabled"]),
    }


@router.get("/api/watch-folders")
def list_watch_folders():
    conn = get_db_conn()
    rows = conn.execute("SELECT * FROM watch_folders").fetchall()
    conn.close()
    return [row_to_watch_folder(r) for r in rows]


@router.post("/api/watch-folders")
def create_watch_folder(item: WatchFolderData):
    if not os.path.isdir(item.path):
        raise HTTPException(status_code=400, detail="Path does not exist or is not a directory")
    wid = str(uuid.uuid4())
    conn = get_db_conn()
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId,frequencyMinutes,lastScanAt,enabled) VALUES (?,?,?,?,?,?)",
        (wid, item.path, item.folderId, item.frequencyMinutes, None, 1),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM watch_folders WHERE id=?", (wid,)).fetchone()
    conn.close()
    return row_to_watch_folder(row)


@router.delete("/api/watch-folders/{watch_folder_id}")
def delete_watch_folder(watch_folder_id: str):
    conn = get_db_conn()
    conn.execute("DELETE FROM watch_folders WHERE id=?", (watch_folder_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/watch-folders/{watch_folder_id}/scan-now")
def scan_watch_folder_now(watch_folder_id: str):
    conn = get_db_conn()
    row = conn.execute("SELECT * FROM watch_folders WHERE id=?", (watch_folder_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Watch folder not found")
    count = scan_watch_folder(dict(row))
    return {"ingested": count}
```

- [ ] **Step 4: Mount it**

In `backend/app/main.py`: add `watcher` to the `from app.routers import ...` line and `app.include_router(watcher.router)`.

- [ ] **Step 5: Run to confirm the new tests pass**

Run: `cd backend && pytest tests/test_watcher_router.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/watcher.py backend/app/main.py backend/tests/test_watcher_router.py
git commit -m "feat: add /api/watch-folders CRUD + scan-now endpoint"
```

---

### Task 6: `scan_downloads_folder` — the #9 catch-and-hold path

**Files:**
- Modify: `backend/app/services/scan.py`
- Modify: `backend/tests/test_scan.py`

**Interfaces:**
- Produces: `default_downloads_dir() -> Path` (`Path.home() / "Downloads"` — works on Windows/macOS/Linux without extra dependencies), `scan_downloads_folder(downloads_dir: Optional[Path] = None) -> int` — finds new supported files in the Downloads folder not already in `models.sourcePath` or `inbox_items.path`, inserts an `inbox_items` row per file (status `pending`, does **not** ingest), returns the count added. `downloads_dir` param exists specifically so tests don't touch the real OS Downloads folder.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_scan.py — append
def test_scan_downloads_folder_creates_pending_inbox_items(client, tmp_path):
    from app.services.scan import scan_downloads_folder
    from app.db import get_db_conn

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "found_online.stl").write_bytes(b"solid found endsolid")
    (downloads / "not_a_model.pdf").write_bytes(b"%PDF-fake")

    count = scan_downloads_folder(downloads)
    assert count == 1

    conn = get_db_conn()
    items = conn.execute("SELECT path, status FROM inbox_items").fetchall()
    conn.close()
    assert len(items) == 1
    assert items[0]["path"] == str(downloads / "found_online.stl")
    assert items[0]["status"] == "pending"


def test_scan_downloads_folder_does_not_redetect_already_flagged_files(client, tmp_path):
    from app.services.scan import scan_downloads_folder

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "found_online.stl").write_bytes(b"solid found endsolid")

    first = scan_downloads_folder(downloads)
    second = scan_downloads_folder(downloads)

    assert first == 1
    assert second == 0


def test_default_downloads_dir_is_under_home():
    from app.services.scan import default_downloads_dir
    from pathlib import Path
    assert default_downloads_dir() == Path.home() / "Downloads"
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/test_scan.py -v`
Expected: FAIL — `scan_downloads_folder`/`default_downloads_dir` not defined.

- [ ] **Step 3: Implement**

```python
# backend/app/services/scan.py — append
import uuid


def default_downloads_dir() -> Path:
    return Path.home() / "Downloads"


def scan_downloads_folder(downloads_dir: Optional[Path] = None) -> int:
    target = downloads_dir if downloads_dir is not None else default_downloads_dir()
    if not target.exists():
        return 0

    conn = get_db_conn()
    seen_models = {r["sourcePath"] for r in conn.execute("SELECT sourcePath FROM models WHERE sourcePath IS NOT NULL")}
    seen_inbox = {r["path"] for r in conn.execute("SELECT path FROM inbox_items")}
    conn.close()
    already_seen = seen_models | seen_inbox

    added = 0
    for file_path in find_new_files(target, already_seen):
        conn = get_db_conn()
        conn.execute(
            "INSERT INTO inbox_items(id,path,detectedAt,status) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), str(file_path), now_ms(), "pending"),
        )
        conn.commit()
        conn.close()
        added += 1
    return added
```

Add `from typing import Optional` to the top of `scan.py` alongside the existing `List, Set` import.

- [ ] **Step 4: Run to confirm all three tests pass**

Run: `cd backend && pytest tests/test_scan.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/scan.py backend/tests/test_scan.py
git commit -m "feat: scan_downloads_folder — flag new Downloads files into the inbox instead of auto-ingesting"
```

---

### Task 7: `routers/inbox.py` — list, file, dismiss

**Files:**
- Create: `backend/app/routers/inbox.py`
- Create: `backend/tests/test_inbox_router.py`
- Modify: `backend/app/main.py` (mount the router)

**Interfaces:**
- Produces: `GET /api/inbox` (pending items only), `POST /api/inbox/{id}/file` (`{folderId}` — copies the file into the library via `ingest_file(..., move=False)` and marks the inbox row `filed`; **not** `move=True`, since the file is still sitting in the user's real Downloads folder and deleting it out from under them on a "file this" click would be surprising), `POST /api/inbox/{id}/dismiss` (marks `dismissed`, leaves the file where it is), `POST /api/inbox/scan-now` (manually triggers `scan_downloads_folder` against the real OS Downloads dir — useful before the background scheduler's first tick, and for support/debugging).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_inbox_router.py
def _seed_inbox_item(client, tmp_path, name="found.stl"):
    from app.db import get_db_conn, now_ms
    import uuid

    path = tmp_path / name
    path.write_bytes(b"solid found endsolid")
    item_id = str(uuid.uuid4())
    conn = get_db_conn()
    conn.execute(
        "INSERT INTO inbox_items(id,path,detectedAt,status) VALUES (?,?,?,?)",
        (item_id, str(path), now_ms(), "pending"),
    )
    conn.commit()
    conn.close()
    return item_id, path


def test_list_inbox_returns_only_pending(client, tmp_path):
    item_id, _ = _seed_inbox_item(client, tmp_path)
    listed = client.get("/api/inbox").json()
    assert any(i["id"] == item_id for i in listed)
    assert all(i["status"] == "pending" for i in listed)


def test_file_inbox_item_ingests_and_marks_filed(client, tmp_path):
    item_id, path = _seed_inbox_item(client, tmp_path)

    response = client.post(f"/api/inbox/{item_id}/file", json={"folderId": "1"})
    assert response.status_code == 200
    assert response.json()["name"] == "found.stl"

    listed = client.get("/api/inbox").json()
    assert all(i["id"] != item_id for i in listed)  # no longer pending
    assert path.exists()  # move=False — original file in "Downloads" untouched

    models = client.get("/api/models", params={"folderId": "1"}).json()
    assert any(m["name"] == "found.stl" for m in models)


def test_dismiss_inbox_item(client, tmp_path):
    item_id, path = _seed_inbox_item(client, tmp_path)

    response = client.post(f"/api/inbox/{item_id}/dismiss")
    assert response.status_code == 200

    listed = client.get("/api/inbox").json()
    assert all(i["id"] != item_id for i in listed)
    assert path.exists()  # dismiss never touches the file


def test_file_missing_inbox_item_is_404(client):
    response = client.post("/api/inbox/does-not-exist/file", json={"folderId": "1"})
    assert response.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/test_inbox_router.py -v`
Expected: FAIL — routes don't exist.

- [ ] **Step 3: Implement**

```python
# backend/app/routers/inbox.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path

from app.db import get_db_conn
from app.services.ingestion import ingest_file
from app.services.scan import scan_downloads_folder

router = APIRouter()


class FileInboxItem(BaseModel):
    folderId: str


def row_to_inbox_item(row) -> dict:
    return {"id": row["id"], "path": row["path"], "detectedAt": row["detectedAt"], "status": row["status"]}


@router.get("/api/inbox")
def list_inbox():
    conn = get_db_conn()
    rows = conn.execute("SELECT * FROM inbox_items WHERE status='pending'").fetchall()
    conn.close()
    return [row_to_inbox_item(r) for r in rows]


@router.post("/api/inbox/{item_id}/file")
def file_inbox_item(item_id: str, payload: FileInboxItem):
    conn = get_db_conn()
    row = conn.execute("SELECT * FROM inbox_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Inbox item not found")

    source_path = Path(row["path"])
    model = ingest_file(str(source_path), folder_id=payload.folderId, original_filename=source_path.name, move=False)

    conn.execute("UPDATE inbox_items SET status='filed' WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return model


@router.post("/api/inbox/{item_id}/dismiss")
def dismiss_inbox_item(item_id: str):
    conn = get_db_conn()
    row = conn.execute("SELECT * FROM inbox_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Inbox item not found")
    conn.execute("UPDATE inbox_items SET status='dismissed' WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/inbox/scan-now")
def inbox_scan_now():
    count = scan_downloads_folder()
    return {"added": count}
```

- [ ] **Step 4: Mount it**

In `backend/app/main.py`: add `inbox` to the routers import line and `app.include_router(inbox.router)`.

- [ ] **Step 5: Run to confirm the new tests pass**

Run: `cd backend && pytest tests/test_inbox_router.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/inbox.py backend/app/main.py backend/tests/test_inbox_router.py
git commit -m "feat: add /api/inbox list/file/dismiss endpoints"
```

---

### Task 8: `POST /api/drive-scan` — on-demand whole-drive consolidation (#16)

**Files:**
- Modify: `backend/app/routers/watcher.py`
- Modify: `backend/tests/test_watcher_router.py`

**Interfaces:**
- Produces: `POST /api/drive-scan` (`{paths: List[str], folderId: str}`) — runs `find_new_files` + `ingest_file(..., record_source=True)` against each given root path (reusing exactly the watch-folder ingestion logic, just invoked on-demand against a caller-supplied path list instead of a configured `watch_folders` row), returns `{ingested: int}`. Deliberately takes an explicit path list rather than "scan the whole C: drive" — scanning an entire filesystem by default is slow and likely to sweep up irrelevant files; the caller (eventually, a frontend "Consolidate" flow) supplies the specific roots to sweep (e.g., several old download/project folders the user names).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_watcher_router.py — append
def test_drive_scan_ingests_from_multiple_arbitrary_paths(client, tmp_path):
    root_a = tmp_path / "old_downloads"
    root_a.mkdir()
    (root_a / "a.stl").write_bytes(b"solid a endsolid")

    root_b = tmp_path / "external_drive_stash"
    root_b.mkdir()
    (root_b / "b.3mf").write_bytes(b"fake b")

    response = client.post(
        "/api/drive-scan",
        json={"paths": [str(root_a), str(root_b)], "folderId": "1"},
    )
    assert response.status_code == 200
    assert response.json() == {"ingested": 2}

    models = client.get("/api/models", params={"folderId": "1"}).json()
    names = {m["name"] for m in models}
    assert {"a.stl", "b.3mf"}.issubset(names)


def test_drive_scan_skips_nonexistent_paths_without_failing(client, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "c.stl").write_bytes(b"solid c endsolid")

    response = client.post(
        "/api/drive-scan",
        json={"paths": [str(real), str(tmp_path / "does_not_exist")], "folderId": "1"},
    )
    assert response.status_code == 200
    assert response.json() == {"ingested": 1}
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/test_watcher_router.py -v`
Expected: FAIL — `/api/drive-scan` doesn't exist (404).

- [ ] **Step 3: Implement**

```python
# backend/app/routers/watcher.py — add imports and route
from typing import List
from pathlib import Path

from app.services.scan import find_new_files


class DriveScanRequest(BaseModel):
    paths: List[str]
    folderId: str


@router.post("/api/drive-scan")
def drive_scan(payload: DriveScanRequest):
    conn = get_db_conn()
    already_seen = {r["sourcePath"] for r in conn.execute("SELECT sourcePath FROM models WHERE sourcePath IS NOT NULL")}
    conn.close()

    ingested = 0
    for path_str in payload.paths:
        root = Path(path_str)
        if not root.exists():
            continue
        for file_path in find_new_files(root, already_seen):
            try:
                ingest_file(
                    str(file_path), folder_id=payload.folderId,
                    original_filename=file_path.name, record_source=True,
                )
                already_seen.add(str(file_path))  # don't double-ingest if two paths overlap
                ingested += 1
            except Exception:
                continue
    return {"ingested": ingested}
```

Add `from app.services.ingestion import ingest_file` to `watcher.py`'s imports (not yet imported there — Task 5 only needed `scan_watch_folder`, which calls `ingest_file` internally).

- [ ] **Step 4: Run to confirm both tests pass**

Run: `cd backend && pytest tests/test_watcher_router.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/watcher.py backend/tests/test_watcher_router.py
git commit -m "feat: add /api/drive-scan for on-demand multi-path consolidation"
```

---

### Task 9: Background scheduler — the "every set frequency" half of #2

**Files:**
- Create: `backend/app/scheduler.py`
- Create: `backend/tests/test_scheduler.py`
- Modify: `backend/app/main.py` (start/stop the scheduler on app lifespan)

**Interfaces:**
- Produces: `should_run_now(watch_folder_row: dict, now_ms_value: int) -> bool` (pure, directly testable — the actual scheduling decision, separated from the `asyncio` loop so it isn't a timing-flaky test), `scheduler_tick() -> dict` (runs `should_run_now` against every enabled `watch_folders` row and calls `scan_watch_folder` for the due ones, plus always calls `scan_downloads_folder()`; returns a summary dict for logging/testing), `start_scheduler(app: FastAPI) -> None` / registers an `asyncio` background task via FastAPI's lifespan that calls `scheduler_tick()` once a minute.

- [ ] **Step 1: Write the failing tests for the pure, timing-independent logic**

```python
# backend/tests/test_scheduler.py
def test_should_run_now_true_when_never_scanned():
    from app.scheduler import should_run_now
    row = {"lastScanAt": None, "frequencyMinutes": 60, "enabled": 1}
    assert should_run_now(row, now_ms_value=1_000_000) is True


def test_should_run_now_false_before_frequency_elapsed():
    from app.scheduler import should_run_now
    row = {"lastScanAt": 1_000_000, "frequencyMinutes": 60, "enabled": 1}
    just_under_an_hour_later = 1_000_000 + (59 * 60 * 1000)
    assert should_run_now(row, now_ms_value=just_under_an_hour_later) is False


def test_should_run_now_true_after_frequency_elapsed():
    from app.scheduler import should_run_now
    row = {"lastScanAt": 1_000_000, "frequencyMinutes": 60, "enabled": 1}
    just_over_an_hour_later = 1_000_000 + (61 * 60 * 1000)
    assert should_run_now(row, now_ms_value=just_over_an_hour_later) is True


def test_should_run_now_false_when_disabled():
    from app.scheduler import should_run_now
    row = {"lastScanAt": None, "frequencyMinutes": 60, "enabled": 0}
    assert should_run_now(row, now_ms_value=1_000_000) is False


def test_scheduler_tick_scans_due_folders_and_downloads(client, tmp_path, monkeypatch):
    from app.scheduler import scheduler_tick
    from app.db import get_db_conn

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    (watched_dir / "a.stl").write_bytes(b"solid a endsolid")

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId,frequencyMinutes,lastScanAt,enabled) VALUES (?,?,?,?,?,?)",
        ("wf1", str(watched_dir), "1", 60, None, 1),
    )
    conn.commit()
    conn.close()

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr("app.scheduler.default_downloads_dir", lambda: downloads)

    summary = scheduler_tick()
    assert summary["watchFoldersScanned"] == 1
    assert summary["totalIngested"] == 1

    models = client.get("/api/models", params={"folderId": "1"}).json()
    assert any(m["name"] == "a.stl" for m in models)
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/test_scheduler.py -v`
Expected: FAIL — `app.scheduler` doesn't exist.

- [ ] **Step 3: Implement**

```python
# backend/app/scheduler.py
import asyncio
from typing import Optional

from fastapi import FastAPI

from app.db import get_db_conn
from app.services.scan import scan_watch_folder, scan_downloads_folder, default_downloads_dir

TICK_SECONDS = 60


def should_run_now(watch_folder_row: dict, now_ms_value: int) -> bool:
    if not watch_folder_row.get("enabled"):
        return False
    if watch_folder_row.get("lastScanAt") is None:
        return True
    elapsed_ms = now_ms_value - watch_folder_row["lastScanAt"]
    return elapsed_ms >= watch_folder_row["frequencyMinutes"] * 60 * 1000


def scheduler_tick() -> dict:
    from app.db import now_ms

    conn = get_db_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM watch_folders")]
    conn.close()

    now_value = now_ms()
    scanned = 0
    total_ingested = 0
    for row in rows:
        if should_run_now(row, now_value):
            total_ingested += scan_watch_folder(row)
            scanned += 1

    # bare-name call, not scan.default_downloads_dir() — Python resolves this
    # against app.scheduler's own module namespace at call time, which is exactly
    # what Test 5's monkeypatch.setattr("app.scheduler.default_downloads_dir", ...)
    # replaces, so the test never touches your real OS Downloads folder
    inbox_added = scan_downloads_folder(default_downloads_dir())

    return {"watchFoldersScanned": scanned, "totalIngested": total_ingested, "inboxAdded": inbox_added}


async def _scheduler_loop():
    while True:
        try:
            scheduler_tick()
        except Exception:
            pass  # one bad tick must never kill the loop — see Global Constraints
        await asyncio.sleep(TICK_SECONDS)


_background_task: Optional[asyncio.Task] = None


def start_scheduler(app: FastAPI) -> None:
    """Registers the background scan loop on the app's startup/shutdown events.
    Gated by DISABLE_SCHEDULER=1 — set by tests/conftest.py's client fixture so
    that merely instantiating the app in a test never spins up a background
    task that reaches out to the real OS Downloads folder. This is not a
    hypothetical risk: `_scheduler_loop` calls `scheduler_tick()` immediately
    on the very first event-loop iteration (before its first `asyncio.sleep`),
    and `asyncio.create_task` fires in every test that builds a TestClient —
    i.e. all of them. Confirmed by actually running the suite before this gate
    existed: every test's app startup scanned this machine's real Downloads
    folder — which, being an actual 3D-printing hobbyist's folder, contained
    dozens of real .3mf files — and raced real inbox_items rows into whatever
    temp DB that test happened to be using, which is exactly what made
    `test_scan_downloads_folder_creates_pending_inbox_items` fail intermittently
    only when run as part of the full suite, never in isolation, and made the
    full suite take ~67s instead of ~7s.
    """
    if os.getenv("DISABLE_SCHEDULER") == "1":
        return

    @app.on_event("startup")
    async def _on_startup():
        global _background_task
        _background_task = asyncio.create_task(_scheduler_loop())

    @app.on_event("shutdown")
    async def _on_shutdown():
        if _background_task:
            _background_task.cancel()
```

Add `import os` to the top of `scheduler.py`, alongside `import asyncio`.

- [ ] **Step 4: Gate the scheduler off in the test fixture**

In `backend/tests/conftest.py`, add one line next to the existing `DB_PATH`/`FILE_STORAGE` env vars:
```python
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("FILE_STORAGE", str(upload_dir))
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")  # see app/scheduler.py — never touch the real Downloads folder in tests
```

- [ ] **Step 5: Wire it into `app/main.py`**

Add `from app.scheduler import start_scheduler` and, after the `app.include_router(...)` calls, `start_scheduler(app)`.

- [ ] **Step 6: Run to confirm the new tests pass**

Run: `cd backend && pytest tests/test_scheduler.py -v`
Expected: PASS. (`test_scheduler_tick_scans_due_folders_and_downloads` calls `scheduler_tick()` directly, not through `start_scheduler`/app startup, so it's unaffected by the `DISABLE_SCHEDULER` gate either way.)

- [ ] **Step 7: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all PASS, in roughly the same ~7s the suite took before this task — if it suddenly takes closer to a minute, or `test_scan_downloads_folder_*` starts failing intermittently, Step 4's gate isn't actually wired up (check `conftest.py`'s env var is set *before* the `sys.modules` purge + `from app import main` a few lines down, same ordering requirement as `DB_PATH`/`FILE_STORAGE`).

- [ ] **Step 8: Commit**

```bash
git add backend/app/scheduler.py backend/app/main.py backend/tests/conftest.py backend/tests/test_scheduler.py
git commit -m "feat: background scheduler drives per-folder frequency scans + periodic Downloads inbox check"
```

---

## Known limitations (deliberate scope cuts, not oversights)

- **No frontend yet.** `WatcherSettings.tsx`/`InboxPanel.tsx` from `docs/ARCHITECTURE.md` aren't built here — this plan only delivers the API they'll call. Follow-up plan once this is reviewed.
- **`asyncio.create_task` scheduling, not APScheduler/Celery.** Deliberate — one more dependency isn't justified for "check a handful of folders once a minute," and fewer moving parts is more stable. If watch-folder counts or frequency precision needs ever outgrow this, that's a real reason to revisit, not a default.
- **No file-system event watching (`watchdog`).** Per the Architecture section above — polling matches the literal requirement text and is more robust across local/NAS paths than OS file-event APIs.
- **`/api/drive-scan` takes explicit paths, not "the whole drive."** A true whole-filesystem sweep by default is a footgun (slow, sweeps temp/system files); the caller names the roots.

## Found only by executing this plan, not by reading it

This whole plan was implemented and run task-by-task against the real forked repo, the same discipline as Phase 0. One bug only showed up that way: the original `start_scheduler` had no test gate, so every test's `TestClient` instantiation triggered a real background scan of this machine's actual Downloads folder (dozens of real `.3mf` files), racing extra `inbox_items` rows into whatever temp DB that test was using. It surfaced as `test_scan_downloads_folder_creates_pending_inbox_items` failing — but *only* when run as part of the full suite (67s), never in isolation — because the race depends on whether the background task's first `scheduler_tick()` lands before or after that specific test's own assertions run. Task 9's `DISABLE_SCHEDULER` gate (Steps 3–4 above) fixes it; the full suite dropped back to ~7s once it did. All 54 tests pass with the fix in place.

## Self-review notes

- **Spec coverage:** #2 (Task 4/9 — scheduled scan), #3 (Task 4 — auto-add), #4 (Task 3 — extension filter), #9 (Task 6/7 — inbox catch-and-hold, never auto-files), #16 (Task 8 — on-demand multi-path scan) are each covered by a task with its own tests.
- **Placeholder scan:** no "TBD"/"similar to Task N" — every step has real code.
- **Type/name consistency checked:** `ingest_file`'s `record_source` param (Task 2) is the exact name used in Tasks 4 and 8; `scan_watch_folder`/`scan_downloads_folder`/`find_new_files`/`default_downloads_dir` signatures introduced in Tasks 3/4/6 match exactly how Tasks 5/7/8/9 call them.
