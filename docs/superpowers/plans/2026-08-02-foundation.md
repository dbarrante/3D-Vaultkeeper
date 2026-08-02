# Foundation (Phase 0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the forked STLVault backend from one 719-line `app.py` into a tested, modular FastAPI package (`app/db.py`, `app/routers/*`, `app/services/*`) with zero behavior change, add the Phase-0 schema columns the rest of the roadmap needs, and stand up test harnesses (pytest, vitest) — the safety net every later phase (`docs/ARCHITECTURE.md`, Phases 1–6) builds on.

**Architecture:** Characterization-test-then-refactor. For every existing router, tests are written against the *current* monolithic `app.py` first and confirmed green; only then does the code move into its module, with the same tests proving nothing changed. New code (the shared `ingest_file` service, new schema columns) is built test-first (red → green) since there's no existing behavior to characterize.

**Tech Stack:** Python 3.9, FastAPI, SQLite (`sqlite3`, no ORM — matches upstream), pytest + httpx `TestClient`, React 19 + TypeScript + Vite (frontend, unchanged in this phase except adding vitest).

## Global Constraints

- Python 3.9 syntax compatibility — use `Optional[X]` / `Union[X, Y]` from `typing`, not `X | Y` syntax (matches existing code, and the Dockerfile pins `python:3.9-slim`). Note this is a syntax constraint, not a guarantee: `pytest` runs against whatever Python is on the host doing the work (confirmed 3.13.5 on this machine, and `pip install -r requirements.txt` resolves cleanly there — verified live, not assumed), while the shipped container is 3.9. A host-green suite is not proof of Docker-green — Task 15's `docker compose up --build` against the real `Dockerfile` is the actual parity check; don't skip it.
- SQLite only, via the standard-library `sqlite3` module — no ORM, no second database. Matches upstream and the user's explicit "stable free backend, SQLite" requirement.
- Schema changes are additive only (`ALTER TABLE ... ADD COLUMN`, `CREATE TABLE IF NOT EXISTS`) — never drop or rename an existing column, so upgrading an existing deployed `data.db` never loses data.
- No existing route is removed, renamed, or has a field taken away from its request or response — the current `frontend/services/api.ts` calls these routes as-is and must keep working unmodified. Additive fields (new optional request fields, new response keys) are permitted where a task specifically adds them — Task 13's five new `row_to_model` keys are additive, not a break, and are not blocked by this constraint.
- `uvicorn app:app` becomes `uvicorn app.main:app` — every place that string appears (`backend/run.sh`, `backend/Dockerfile`) gets updated in the same task that makes the move, never left inconsistent.
- MIT license retained (`LICENSE.md` unchanged, upstream attribution kept).

---

### Task 1: Backend test harness

**Files:**
- Create: `backend/tests/__init__.py` (empty)
- Create: `backend/tests/conftest.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces: a `client` pytest fixture (`httpx`-backed `fastapi.testclient.TestClient`) importable by every later test file, pointed at a fresh temp SQLite file per test so tests never touch a real `data.db` or each other's state.

- [ ] **Step 1: Add test dependencies**

Append to `backend/requirements.txt`:
```
pytest>=7.4.0
httpx>=0.24.0
```

- [ ] **Step 2: Install and verify pytest runs (with nothing to collect yet)**

Run: `cd backend && pip install -r requirements.txt && pytest --collect-only`
Expected: `no tests ran` (exits 0, no import errors)

- [ ] **Step 3: Write the DB-isolation + client fixture**

`DB_PATH`, `UPLOAD_DIR`, and `MANUAL_DIR` are read from environment variables once, at module import time (matching upstream's existing style — see `app.py`'s top-level `DB_PATH = os.getenv(...)`). A single `importlib.reload()` of just the entrypoint module only re-runs *that* module's top-level code — once this splits into `app/db.py` + `app/main.py` + routers (Task 7 on), any module that already did `from app.db import UPLOAD_DIR` keeps its stale, first-test value even after `app.db` itself is reloaded, because a plain value import isn't retroactively updated by reloading its source. Purging every `app`-rooted module from `sys.modules` before each test sidesteps that entirely — the next `import` is a fully fresh one, so it stays correct no matter how many router/service modules get added in later tasks, with no fixture changes required when they are:

```python
# backend/tests/conftest.py
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh temp SQLite DB + upload dir per test, fully re-imported app package."""
    db_path = tmp_path / "test.db"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("FILE_STORAGE", str(upload_dir))

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    import app as app_module  # backend/app.py at this point in the plan

    # raise_server_exceptions=False: an unhandled exception in a route surfaces
    # as the HTTP 500 a real deployed server would return, not as a Python
    # exception inside the test. Needed as-is: app.py's own get_model_info()
    # has a pre-existing bug (Task 3) that crashes instead of 404ing, and the
    # fixture must observe that as an HTTP response like any other client would.
    with TestClient(app_module.app, raise_server_exceptions=False) as test_client:
        yield test_client
```

- [ ] **Step 4: Write one smoke test to prove the fixture works**

```python
# backend/tests/test_smoke.py
def test_folders_endpoint_is_reachable(client):
    response = client.get("/api/folders")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
```

- [ ] **Step 5: Run it**

Run: `cd backend && pytest tests/test_smoke.py -v`
Expected: `test_folders_endpoint_is_reachable PASSED`

- [ ] **Step 6: Commit**

```bash
git add backend/tests/__init__.py backend/tests/conftest.py backend/tests/test_smoke.py backend/requirements.txt
git commit -m "test: add pytest harness with isolated temp-DB fixture"
```

---

### Task 2: Characterization tests — folders router

**Files:**
- Create: `backend/tests/test_folders.py`

**Interfaces:**
- Consumes: `client` fixture from Task 1.

- [ ] **Step 1: Write the failing/uncharacterized tests**

```python
# backend/tests/test_folders.py
def test_get_folders_returns_seeded_defaults(client):
    response = client.get("/api/folders")
    names = {f["name"] for f in response.json()}
    assert {"Characters", "Vehicles", "Terrain", "Tanks"}.issubset(names)


def test_create_folder(client):
    response = client.post("/api/folders", json={"name": "Minis", "parentId": None})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Minis"
    assert body["parentId"] is None
    assert "id" in body


def test_update_folder_name(client):
    created = client.post("/api/folders", json={"name": "Old", "parentId": None}).json()
    response = client.patch(f"/api/folders/{created['id']}", json={"name": "New"})
    assert response.status_code == 200
    assert response.json()["name"] == "New"


def test_update_missing_folder_returns_404(client):
    response = client.patch("/api/folders/does-not-exist", json={"name": "X"})
    assert response.status_code == 404


def test_delete_empty_folder(client):
    created = client.post("/api/folders", json={"name": "Temp", "parentId": None}).json()
    response = client.delete(f"/api/folders/{created['id']}")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_delete_folder_with_models_is_rejected(client):
    folder = client.post("/api/folders", json={"name": "HasModel", "parentId": None}).json()
    client.post(
        "/api/models/upload",
        data={"folderId": folder["id"]},
        files={"file": ("test.stl", b"solid test endsolid", "application/octet-stream")},
    )
    response = client.delete(f"/api/folders/{folder['id']}")
    assert response.status_code == 400
```

- [ ] **Step 2: Run and confirm they pass against the current monolithic `app.py`**

Run: `cd backend && pytest tests/test_folders.py -v`
Expected: all 6 tests PASS — this is the safety net for Task 8's extraction, not new behavior.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_folders.py
git commit -m "test: characterize existing folder endpoints before extraction"
```

---

### Task 3: Characterization tests — model core CRUD

**Files:**
- Create: `backend/tests/test_models_core.py`

- [ ] **Step 1: Write the tests**

```python
# backend/tests/test_models_core.py
def _upload(client, name="test.stl", folder_id="1"):
    return client.post(
        "/api/models/upload",
        data={"folderId": folder_id, "tags": "a,b"},
        files={"file": (name, b"solid test endsolid", "application/octet-stream")},
    ).json()


def test_upload_model_persists_and_lists(client):
    created = _upload(client)
    assert created["name"] == "test.stl"
    assert created["tags"] == ["a", "b"]

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert any(m["id"] == created["id"] for m in listed)


def test_update_model_allowed_fields_only(client):
    created = _upload(client)
    response = client.patch(
        f"/api/models/{created['id']}",
        json={"description": "new desc", "notAllowedField": "ignored"},
    )
    assert response.status_code == 200
    assert response.json()["description"] == "new desc"


def test_delete_model_removes_row_and_file(client):
    created = _upload(client)
    response = client.delete(f"/api/models/{created['id']}")
    assert response.status_code == 200
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert all(m["id"] != created["id"] for m in listed)


def test_download_model_returns_file_bytes(client):
    created = _upload(client)
    response = client.get(f"/api/models/{created['id']}/download")
    assert response.status_code == 200
    assert response.content == b"solid test endsolid"


def test_download_missing_model_currently_returns_500_known_bug(client):
    # Confirmed by actually running this against the real repo: app.py's
    # get_model_info() calls row_to_model(None) with no null check when the id
    # doesn't match any row, so it crashes instead of returning a clean 404.
    # This test locks in *actual* current behavior for the Phase 0
    # no-behavior-change refactor — see "Known pre-existing bugs" at the end of
    # this plan for the deliberate follow-up that fixes it for real.
    response = client.get("/api/models/does-not-exist/download")
    assert response.status_code == 500
```

- [ ] **Step 2: Run**

Run: `cd backend && pytest tests/test_models_core.py -v`
Expected: all 5 PASS against current `app.py` — confirmed by actually running this file against the unmodified repo, not just by inspection.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_models_core.py
git commit -m "test: characterize existing model CRUD endpoints before extraction"
```

---

### Task 4: Characterization tests — bulk ops, replace, storage stats

**Files:**
- Create: `backend/tests/test_models_bulk.py`

- [ ] **Step 1: Write the tests**

```python
# backend/tests/test_models_bulk.py
def _upload(client, name="test.stl", folder_id="1"):
    return client.post(
        "/api/models/upload",
        data={"folderId": folder_id},
        files={"file": (name, b"solid test endsolid", "application/octet-stream")},
    ).json()


def test_bulk_delete(client):
    a = _upload(client, "a.stl")
    b = _upload(client, "b.stl")
    response = client.post("/api/models/bulk-delete", json={"ids": [a["id"], b["id"]]})
    assert response.status_code == 200
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    remaining_ids = {m["id"] for m in listed}
    assert a["id"] not in remaining_ids and b["id"] not in remaining_ids


def test_bulk_move(client):
    folder = client.post("/api/folders", json={"name": "Dest", "parentId": None}).json()
    a = _upload(client, "a.stl")
    response = client.post("/api/models/bulk-move", json={"ids": [a["id"]], "folderId": folder["id"]})
    assert response.status_code == 200
    moved = client.get("/api/models", params={"folderId": folder["id"]}).json()
    assert any(m["id"] == a["id"] for m in moved)


def test_bulk_tag_merges_without_duplicates(client):
    a = _upload(client, "a.stl")
    client.post("/api/models/bulk-tag", json={"ids": [a["id"]], "tags": ["red"]})
    response = client.post("/api/models/bulk-tag", json={"ids": [a["id"]], "tags": ["red", "blue"]})
    assert response.status_code == 200
    updated = [m for m in client.get("/api/models", params={"folderId": "1"}).json() if m["id"] == a["id"]][0]
    assert updated["tags"] == ["red", "blue"]


def test_replace_model_file_updates_size(client):
    a = _upload(client, "a.stl")
    response = client.put(
        f"/api/models/{a['id']}/file",
        files={"file": ("a2.stl", b"solid bigger file content endsolid", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json()["size"] == len(b"solid bigger file content endsolid")


def test_replace_model_thumbnail_stores_base64_data_uri(client):
    a = _upload(client, "a.stl")
    response = client.put(
        f"/api/models/{a['id']}/thumbnail",
        files={"file": ("thumb.png", b"\x89PNG fake bytes", "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["thumbnail"].startswith("data:image/png;base64,")


def test_storage_stats_reports_used_bytes(client):
    _upload(client, "a.stl")
    response = client.get("/api/storage-stats")
    assert response.status_code == 200
    body = response.json()
    assert body["used"] > 0
    assert body["total"] == 5 * 1024 * 1024 * 1024
```

- [ ] **Step 2: Run**

Run: `cd backend && pytest tests/test_models_bulk.py -v`
Expected: all 6 PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_models_bulk.py
git commit -m "test: characterize bulk/replace/storage-stats endpoints before extraction"
```

---

### Task 5: Characterization tests — manuals

**Files:**
- Create: `backend/tests/test_manuals.py`

- [ ] **Step 1: Write the tests**

```python
# backend/tests/test_manuals.py
def _upload(client):
    return client.post(
        "/api/models/upload",
        data={"folderId": "1"},
        files={"file": ("a.stl", b"solid test endsolid", "application/octet-stream")},
    ).json()


def test_manual_round_trip(client):
    model = _upload(client)
    response = client.put(
        f"/api/models/{model['id']}/manual",
        files={"file": ("guide.md", b"# Print settings\n0.2mm layer", "text/markdown")},
    )
    assert response.status_code == 200
    assert response.json()["manual"] == "guide.md"

    fetched = client.get(f"/api/models/{model['id']}/manual")
    assert fetched.status_code == 200
    assert b"Print settings" in fetched.content

    deleted = client.delete(f"/api/models/{model['id']}/manual")
    assert deleted.status_code == 200
    assert deleted.json()["manual"] is None

    missing = client.get(f"/api/models/{model['id']}/manual")
    assert missing.status_code == 404
```

- [ ] **Step 2: Run**

Run: `cd backend && pytest tests/test_manuals.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_manuals.py
git commit -m "test: characterize manual endpoints before extraction"
```

---

### Task 6: Characterization tests — settings + importers

**Files:**
- Create: `backend/tests/test_settings_and_importers.py`

**Interfaces:**
- Consumes: `unittest.mock.patch` to stub `importers.printables.PrintablesImporter` / `importers.makerworld.MakerWorldImporter` so tests don't hit the real network.

- [ ] **Step 1: Write the tests**

```python
# backend/tests/test_settings_and_importers.py
from unittest.mock import patch


def test_makerworld_token_status_defaults_unconfigured(client):
    response = client.get("/api/settings/makerworld-token")
    assert response.status_code == 200
    assert response.json() == {"configured": False}


def test_makerworld_token_set_then_clear(client):
    set_resp = client.put("/api/settings/makerworld-token", json={"token": "abc123"})
    assert set_resp.json() == {"configured": True}

    status_resp = client.get("/api/settings/makerworld-token")
    assert status_resp.json() == {"configured": True}

    clear_resp = client.put("/api/settings/makerworld-token", json={"clear": True})
    assert clear_resp.json() == {"configured": False}


def test_makerworld_token_rejects_empty(client):
    response = client.put("/api/settings/makerworld-token", json={"token": "   "})
    assert response.status_code == 400


class _FakeImporter:
    def importfromId(self, model_id, parent_id, preview_path):
        class _Resp:
            content = b"solid fake endsolid"
        return _Resp(), "data:image/png;base64,fake"

    def getModelOptions(self, url):
        return {"files": [{"id": "123", "name": "Fake Model"}]}


def test_import_model_by_id_uses_printables_importer(client):
    with patch("app.printables.PrintablesImporter", return_value=_FakeImporter()):
        response = client.post(
            "/api/import/importid",
            json={"source": "printables", "id": "123", "name": "Fake Model", "folderId": "1", "typeName": "stl"},
        )
    assert response.status_code == 200
    assert response.json()["name"] == "Fake Model"


def test_import_options_rejects_missing_url(client):
    response = client.post("/api/import/options", json={})
    assert response.status_code == 400
```

- [ ] **Step 2: Run**

Run: `cd backend && pytest tests/test_settings_and_importers.py -v`
Expected: all 5 PASS. (The `patch("app.printables...")` target confirms `app.py` imports the importer module at module scope as `from importers import makerworld, printables` — patch path updates in Task 11 when this moves to `app.routers.importers`.)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_settings_and_importers.py
git commit -m "test: characterize settings and import endpoints with mocked importers"
```

---

### Task 7: Create the `app/` package (db.py + main.py), retire the flat `app.py` in the same step

**⚠️ Why this has to be one atomic task, not two:** Python treats a package directory and a same-named module file in the same parent directory as a naming collision — once `backend/app/__init__.py` exists, `import app` resolves to the **package**, and the sibling `backend/app.py` file becomes permanently unreachable dead code, even if it still holds routes nothing has moved out of yet. There is no safe intermediate state where `backend/app.py` and `backend/app/` coexist. So this task moves *everything* out of `app.py` into `app/db.py` + `app/main.py` and deletes `app.py`, all before the suite is run once. Tasks 8–11 then subdivide `app/main.py` into routers — safely, since `app.py` no longer exists to collide with anything.

**Files:**
- Modify: `.gitignore` (see Step 0 — a repo-root gitignore rule silently swallows this whole task otherwise)
- Create: `backend/app/__init__.py` (empty)
- Create: `backend/app/db.py`
- Create: `backend/app/main.py` (every route from `app.py`, verbatim, importing shared pieces from `app.db`)
- Delete: `backend/app.py`
- Modify: `backend/tests/conftest.py` (import target)
- Modify: `backend/run.sh`, `backend/Dockerfile` (uvicorn entrypoint string)

**Interfaces:**
- Produces (from `app/db.py`): `get_db_conn() -> sqlite3.Connection`, `init_db() -> None`, `now_ms() -> int`, `row_to_folder(row) -> dict`, `row_to_model(row) -> dict`, `save_upload_file(upload_file, dest_path) -> int`, `get_setting(key) -> Optional[str]`, `set_setting(key, value) -> None`, `clear_setting(key) -> None`, and module constants `DB_PATH`, `UPLOAD_DIR`, `MANUAL_DIR`, `WEBUI_URL`.
- Produces (from `app/main.py`): the FastAPI instance `app`, importable as `app.main:app`, with every existing route still attached exactly as before (Tasks 8–11 relocate them one router at a time).
- Consumes: nothing new — this is a pure move of Task 1–6's already-tested code.

- [ ] **Step 0: Fix `.gitignore` before creating anything — this is the step that actually matters most in this task**

The repo's root `.gitignore` has `/backend/app/*` — a rule from when `backend/app/` meant only `UPLOAD_DIR`'s local-dev fallback path (`FILE_STORAGE` defaults to `./app/uploads` in `app/db.py`), never source code. The instant `backend/app/` becomes a real Python package, that rule silently excludes every file this task (and every later one) creates. Confirmed live: running `git status --ignored --short backend/app/` after writing `db.py`/`main.py` shows them all as `!!` (ignored) — a normal `git add` + `git commit` would produce a repo that's missing the entire refactor while looking clean locally, since the files still exist on disk and imports still work until someone else clones it. Fix the rule *before* Step 1, not after:

In `.gitignore`, change:
```
# Backend
/backend/__pycache__/
/backend/app/*
/backend/data/
/backend/*.db
/backend/uploads/
```
to:
```
# Backend
/backend/__pycache__/
/backend/data/
/backend/*.db
/backend/uploads/
# app/uploads is FILE_STORAGE's local-dev fallback path (see app/db.py) when the
# env var isn't set — NOT the app/ Python package itself, which is real source.
/backend/app/uploads/
```

- [ ] **Step 1: Create the package and move DB/helper code verbatim**

```python
# backend/app/db.py
import os
import sqlite3
import shutil
import time
import json
from pathlib import Path
from typing import Optional, Dict, Any

DB_PATH = os.getenv("DB_PATH", "data.db")
UPLOAD_DIR = Path(os.getenv("FILE_STORAGE", "./app/uploads"))
MANUAL_DIR = Path(os.getenv("MANUAL_STORAGE", UPLOAD_DIR / "manuals"))
MANUAL_DIR.mkdir(parents=True, exist_ok=True)
WEBUI_URL = os.getenv("WEBUI_URL", "http://localhost:8989")


def get_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS folders (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parentId TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS models (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            folderId TEXT NOT NULL,
            url TEXT NOT NULL,
            size INTEGER,
            dateAdded INTEGER,
            tags TEXT,
            description TEXT,
            thumbnail TEXT,
            manual TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    try:
        cur.execute("ALTER TABLE models ADD COLUMN manual TEXT")
    except sqlite3.OperationalError:
        pass
    if os.getenv("MAKERWORLD_BAMBU_TOKEN"):
        cur.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)",
            ("makerworld_bambu_token", os.getenv("MAKERWORLD_BAMBU_TOKEN")),
        )
    conn.commit()

    cur.execute("SELECT COUNT(*) as c FROM folders")
    if cur.fetchone()[0] == 0:
        seed = [
            ("1", "Characters", None),
            ("2", "Vehicles", None),
            ("3", "Terrain", None),
            ("4", "Tanks", "2"),
        ]
        cur.executemany("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", seed)
        conn.commit()

    conn.close()


def now_ms() -> int:
    return int(time.time() * 1000)


def row_to_folder(row: sqlite3.Row) -> Dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "parentId": row["parentId"]}


def row_to_model(row: sqlite3.Row) -> Dict[str, Any]:
    tags = []
    if row["tags"]:
        try:
            tags = json.loads(row["tags"])
        except Exception:
            tags = []
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
    }


def save_upload_file(upload_file, dest_path: str) -> int:
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
    return os.path.getsize(dest_path)


def get_setting(key: str) -> Optional[str]:
    conn = get_db_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    conn = get_db_conn()
    conn.execute(
        "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def clear_setting(key: str) -> None:
    conn = get_db_conn()
    conn.execute("DELETE FROM settings WHERE key=?", (key,))
    conn.commit()
    conn.close()
```

```python
# backend/app/__init__.py
# (empty — marks app/ as a package)
```

- [ ] **Step 2: Create `app/main.py` — every remaining piece of `app.py`, moved verbatim**

Copy `backend/app.py` to `backend/app/main.py`, then edit the copy:

1. Delete everything Step 1 already moved into `app/db.py` (the `DB_PATH`/`UPLOAD_DIR`/`MANUAL_DIR`/`WEBUI_URL` constants, `get_db_conn`, `init_db`, `now_ms`, `row_to_folder`, `row_to_model`, `save_upload_file`, `get_setting`, `set_setting`, `clear_setting`).
2. Replace those deleted definitions with a single import line:
   ```python
   from app.db import (
       DB_PATH, UPLOAD_DIR, MANUAL_DIR, WEBUI_URL,
       get_db_conn, init_db, now_ms, row_to_folder, row_to_model,
       save_upload_file, get_setting, set_setting, clear_setting,
   )
   ```
3. Keep everything else unchanged and in place: the `FolderData` model, `app = FastAPI(...)`, the CORS middleware, every `@app.get/post/patch/delete/put(...)` route (folders, models, manuals, settings, imports), `get_model_info`, `importer_for_url`, `importer_for_source`, and the `from importers import makerworld, printables` import (this keeps resolving correctly — `backend/` is still the working directory both `pytest` and `uvicorn` run from, and `app/main.py` being one package level deeper doesn't change how top-level `sys.path` imports like `importers` resolve).
4. Keep the module-level `init_db()` call (now calling the imported `app.db.init_db`).
5. Keep the `if __name__ == "__main__":` block at the bottom, updating its `uvicorn.run("app:app", ...)` call to `uvicorn.run("app.main:app", ...)`.

- [ ] **Step 3: Delete the flat file**

```bash
git rm backend/app.py
```

- [ ] **Step 4: Point the test fixture at the new entrypoint**

In `backend/tests/conftest.py`, change:
```python
    import app as app_module  # backend/app.py at this point in the plan
```
to:
```python
    from app import main as app_module
```
The `sys.modules` purge loop above it (`if name == "app" or name.startswith("app.")`) already covers the whole package regardless of how it's structured internally, so it needs no change here — it's what makes this entrypoint swap a one-line fixture edit instead of a reload-ordering exercise. `TestClient(app_module.app)` on the next line also needs no change — `app_module.app` still resolves, now to `app/main.py`'s `app`.

- [ ] **Step 5: Update the entrypoint string everywhere it's referenced outside tests**

`backend/run.sh`, last line: `uvicorn app:app --host 0.0.0.0 --port 8000 --reload` → `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`

`backend/Dockerfile`, `CMD` line: `CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]` → `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]`

- [ ] **Step 6: Update the mocked import path in the settings/importers test**

`app` was a flat module in Tasks 1–6, so `patch("app.printables.PrintablesImporter", ...)` (Task 6) resolved `printables` as a direct attribute of `app`. Now that `app` is a package, that attribute doesn't exist on the package itself — `printables` is a name bound inside `app/main.py`'s own namespace (`from importers import makerworld, printables`). Running the suite unchanged at this point fails with `AttributeError: module 'app' has no attribute 'printables'` — confirmed by actually hitting that exact error while executing this task, not a hypothetical. In `backend/tests/test_settings_and_importers.py`, change:
```python
    with patch("app.printables.PrintablesImporter", return_value=_FakeImporter()):
```
to:
```python
    with patch("app.main.printables.PrintablesImporter", return_value=_FakeImporter()):
```
This target moves again in Task 11, to `app.routers.importers.printables`, once the import routes get their own module.

- [ ] **Step 7: Run the full existing suite to prove zero behavior change**

Run: `cd backend && pytest -v`
Expected: all tests from Tasks 1–6 still PASS, unmodified — same assertions, same routes, now served from `app/main.py` instead of `app.py`.

- [ ] **Step 8: Commit**

```bash
git add .gitignore backend/app/__init__.py backend/app/db.py backend/app/main.py backend/tests/conftest.py backend/tests/test_settings_and_importers.py backend/run.sh backend/Dockerfile
git commit -m "refactor: move app.py into app/ package (db.py + main.py) as one atomic step"
```

---

### Task 8: Extract `app/routers/folders.py` from `app/main.py`

**Files:**
- Create: `backend/app/routers/__init__.py` (empty)
- Create: `backend/app/routers/folders.py`
- Modify: `backend/app/main.py` (remove the four folder routes + `FolderData`, mount the new router instead)

**Interfaces:**
- Produces: `app/routers/folders.py` exports `router: APIRouter` mounted with no prefix (routes already include `/api/folders`).
- Consumes: `app.db.get_db_conn`, `app.db.row_to_folder`.

- [ ] **Step 1: Create the folders router**

```python
# backend/app/routers/folders.py
import uuid
from typing import Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_db_conn, row_to_folder

router = APIRouter()


class FolderData(BaseModel):
    name: str
    parentId: Union[str, None] = None


@router.get("/api/folders")
def get_folders():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id,name,parentId FROM folders")
    rows = cur.fetchall()
    conn.close()
    return [row_to_folder(r) for r in rows]


@router.post("/api/folders")
def create_folder(item: FolderData):
    fid = str(uuid.uuid4())
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO folders(id,name,parentId) VALUES (?,?,?)",
        (fid, item.name, item.parentId),
    )
    conn.commit()
    conn.close()
    return {"id": fid, "name": item.name, "parentId": item.parentId}


@router.patch("/api/folders/{folder_id}")
def update_folder(folder_id: str, item: FolderData):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE folders SET name=? WHERE id=?", (item.name, folder_id))
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Folder not found")
    conn.commit()
    cur.execute("SELECT id,name,parentId FROM folders WHERE id=?", (folder_id,))
    row = cur.fetchone()
    conn.close()
    return row_to_folder(row)


@router.delete("/api/folders/{folder_id}")
def delete_folder(folder_id: str):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM models WHERE folderId=? LIMIT 1", (folder_id,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Folder must be empty to delete")
    cur.execute("SELECT 1 FROM folders WHERE parentId=? LIMIT 1", (folder_id,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Folder must be empty to delete")
    cur.execute("DELETE FROM folders WHERE id=?", (folder_id,))
    conn.commit()
    conn.close()
    return {"ok": True}
```

- [ ] **Step 2: In `app/main.py`, delete the `class FolderData` definition and the four `@app.get/post/patch/delete("/api/folders...")` functions (now owned by `app/routers/folders.py`), and mount the router instead**

Add near the top of `app/main.py`, alongside the other imports:
```python
from app.routers import folders
```

After the `app.add_middleware(...)` block, add:
```python
app.include_router(folders.router)
```

- [ ] **Step 3: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all tests from Tasks 1–6 still PASS — folder tests now exercise `app/routers/folders.py` through the same HTTP contract, everything else still runs from `app/main.py` unchanged.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/__init__.py backend/app/routers/folders.py backend/app/main.py
git commit -m "refactor: extract folders router from app.main"
```

---

### Task 9: Extract `app/routers/models.py`

**Files:**
- Create: `backend/app/routers/models.py`
- Modify: `backend/app/main.py` (remove all `/api/models*` and `/api/storage-stats` routes)
- Modify: `backend/app/main.py` (mount the new router)

**Interfaces:**
- Consumes: `app.db.{get_db_conn, row_to_model, save_upload_file, now_ms}`, plus module constants `UPLOAD_DIR`, `MANUAL_DIR` from `app.db`.
- Produces: `router: APIRouter` covering `get_models`, `upload_model`, `update_model`, `delete_model`, `download_model`, `bulk_delete`, `bulk_move`, `bulk_tag`, `replace_model_file`, `replace_model_thumbnail`, `storage_stats`, plus the internal helper `get_model_info(model_id)` (used by `download_model` only — kept local to this router, not shared with `services/ingestion.py` in Task 12).

- [ ] **Step 1: Move every model/storage-stats route from `app/main.py` into the router, unchanged line-for-line except the import block and `@app.` → `@router.`**

```python
# backend/app/routers/models.py
import os
import uuid
import json
import base64
from typing import Optional, List

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse

from app.db import get_db_conn, row_to_model, save_upload_file, now_ms, UPLOAD_DIR, MANUAL_DIR

router = APIRouter()


def get_model_info(modelId):
    # NOTE: deliberately NOT adding an `if m else None` guard here, even though
    # a missing model makes row_to_model(None) crash below. Task 3 characterized
    # this exact crash (download_model on an unknown id returns 500, confirmed by
    # actually running it against the real repo) — fixing it here would be a
    # silent behavior change this phase isn't supposed to make. See "Known
    # pre-existing bugs" at the end of this plan for the real fix as its own task.
    conn = get_db_conn()
    cur = conn.cursor()
    m = None
    if modelId is not None:
        m = cur.execute("SELECT * FROM models WHERE id=?", (modelId,)).fetchone()
    else:
        return None
    conn.close()
    return row_to_model(m)


@router.get("/api/models")
def get_models(folderId: Optional[str] = None):
    conn = get_db_conn()
    cur = conn.cursor()
    if folderId and folderId != "all":
        cur.execute("SELECT * FROM models WHERE folderId=?", (folderId,))
    else:
        cur.execute("SELECT * FROM models")
    rows = cur.fetchall()
    conn.close()
    return [row_to_model(r) for r in rows]


@router.post("/api/models/upload")
def upload_model(
    file: UploadFile = File(...),
    folderId: str = Form("1"),
    thumbnail: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
):
    mid = str(uuid.uuid4())
    filename_str = file.filename or ".stl"
    ext = os.path.splitext(filename_str)[1] or ".stl"
    filename = f"{mid}{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    size = save_upload_file(file, path)

    tag_list: List[str] = []
    if tags:
        try:
            tag_list = json.loads(tags)
        except Exception:
            tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

    model = {
        "id": mid,
        "name": file.filename,
        "folderId": folderId if folderId != "all" else "1",
        "url": f"/api/models/{mid}/download",
        "size": size,
        "dateAdded": now_ms(),
        "tags": tag_list,
        "description": "",
        "thumbnail": thumbnail,
    }

    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            model["id"], model["name"], model["folderId"], model["url"], model["size"],
            model["dateAdded"], json.dumps(model["tags"]), model["description"], model["thumbnail"],
        ),
    )
    conn.commit()
    conn.close()
    return model


@router.patch("/api/models/{model_id}")
def update_model(model_id: str, updates: dict):
    conn = get_db_conn()
    cur = conn.cursor()
    m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    if not m:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")

    allowed = ["name", "folderId", "tags", "description", "thumbnail"]
    fields, values = [], []
    for k in allowed:
        if k in updates:
            values.append(json.dumps(updates[k] or []) if k == "tags" else updates[k])
            fields.append(f"{k}=?")

    if fields:
        cur.execute(f"UPDATE models SET {', '.join(fields)} WHERE id=?", (*values, model_id))
        conn.commit()

    row = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    conn.close()
    return row_to_model(row)


@router.delete("/api/models/{model_id}")
def delete_model(model_id: str):
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


@router.get("/api/models/{model_id}/download")
def download_model(model_id: str):
    m_info = get_model_info(model_id)
    for fname in os.listdir(UPLOAD_DIR):
        if fname.startswith(model_id):
            return FileResponse(
                os.path.join(UPLOAD_DIR, fname),
                media_type="application/octet-stream",
                filename=m_info["name"],
            )
    raise HTTPException(status_code=404, detail="File not found")


@router.post("/api/models/bulk-delete")
def bulk_delete(payload: dict):
    ids = payload.get("ids", [])
    conn = get_db_conn()
    cur = conn.cursor()
    for mid in ids:
        for fname in os.listdir(UPLOAD_DIR):
            if fname.startswith(mid):
                try:
                    os.remove(os.path.join(UPLOAD_DIR, fname))
                except Exception:
                    pass
        manual_path = MANUAL_DIR / f"{mid}.md"
        if manual_path.exists():
            try:
                manual_path.unlink()
            except Exception:
                pass
        cur.execute("DELETE FROM models WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/models/bulk-move")
def bulk_move(payload: dict):
    ids = payload.get("ids", [])
    folderId = payload.get("folderId")
    conn = get_db_conn()
    cur = conn.cursor()
    for mid in ids:
        cur.execute("UPDATE models SET folderId=? WHERE id=?", (folderId, mid))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/models/bulk-tag")
def bulk_tag(payload: dict):
    ids = payload.get("ids", [])
    tags = payload.get("tags", [])
    conn = get_db_conn()
    cur = conn.cursor()
    for mid in ids:
        row = cur.execute("SELECT tags FROM models WHERE id=?", (mid,)).fetchone()
        if not row:
            continue
        existing = []
        if row["tags"]:
            try:
                existing = json.loads(row["tags"])
            except Exception:
                existing = []
        merged = list(dict.fromkeys(existing + tags))
        cur.execute("UPDATE models SET tags=? WHERE id=?", (json.dumps(merged), mid))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.put("/api/models/{model_id}/file")
def replace_model_file(model_id: str, file: UploadFile = File(...), thumbnail: Optional[str] = Form(None)):
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
    filename_str = file.filename or ".stl"
    ext = os.path.splitext(filename_str)[-1] or ".stl"
    filename = f"{model_id}{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    size = save_upload_file(file, path)
    cur.execute(
        "UPDATE models SET url=?, size=?, thumbnail=? WHERE id=?",
        (f"/api/models/{model_id}/download", size, thumbnail, model_id),
    )
    conn.commit()
    row = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    conn.close()
    return row_to_model(row)


@router.put("/api/models/{model_id}/thumbnail")
def replace_model_thumbnail(model_id: str, file: UploadFile = File(...)):
    filename_str = file.filename
    ext = os.path.splitext(filename_str)[-1]
    if not ext:
        raise HTTPException(status_code=429, detail="File not Valid, Extension not found")
    filebytes = file.file.read()
    encoded_string = base64.b64encode(filebytes)
    thumbnail = "data:image/" + ext[1:] + ";base64," + encoded_string.decode()
    conn = get_db_conn()
    cur = conn.cursor()
    m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    if not m:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")
    cur.execute("UPDATE models SET thumbnail=? WHERE id=?", (thumbnail, model_id))
    conn.commit()
    row = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    conn.close()
    return row_to_model(row)


@router.get("/api/storage-stats")
def storage_stats():
    used = 0
    for root, _dirs, files in os.walk(UPLOAD_DIR):
        for fname in files:
            used += os.path.getsize(os.path.join(root, fname))
    return {"used": used, "total": 5 * 1024 * 1024 * 1024}
```

- [ ] **Step 2: Mount it in `app/main.py`**

```python
# backend/app/main.py — add alongside the folders import
from app.routers import folders, models
...
app.include_router(folders.router)
app.include_router(models.router)
```

- [ ] **Step 3: Delete the corresponding routes from `app/main.py`** (everything from `# --- Model endpoints ---` through the `storage_stats` function).

- [ ] **Step 4: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all tests from Tasks 1–6 PASS unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/models.py backend/app/main.py
git commit -m "refactor: extract models router (CRUD, bulk ops, storage stats)"
```

---

### Task 10: Extract `app/routers/manuals.py`

**Files:**
- Create: `backend/app/routers/manuals.py`
- Modify: `backend/app/main.py` (remove the three `/api/models/{model_id}/manual` routes, mount the new router)

**Interfaces:**
- Consumes: `app.db.{get_db_conn, row_to_model, save_upload_file, MANUAL_DIR}`.

- [ ] **Step 1: Create the router**

```python
# backend/app/routers/manuals.py
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse

from app.db import get_db_conn, row_to_model, save_upload_file, MANUAL_DIR

router = APIRouter()


@router.get("/api/models/{model_id}/manual")
def get_model_manual(model_id: str):
    path = MANUAL_DIR / f"{model_id}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Manual not found")
    return FileResponse(path, media_type="text/markdown")


@router.put("/api/models/{model_id}/manual")
def upload_model_manual(model_id: str, file: UploadFile = File(...)):
    conn = get_db_conn()
    cur = conn.cursor()
    m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    if not m:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")
    path = MANUAL_DIR / f"{model_id}.md"
    save_upload_file(file, str(path))
    cur.execute("UPDATE models SET manual=? WHERE id=?", (file.filename, model_id))
    conn.commit()
    row = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    conn.close()
    return row_to_model(row)


@router.delete("/api/models/{model_id}/manual")
def delete_model_manual(model_id: str):
    conn = get_db_conn()
    cur = conn.cursor()
    m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    if not m:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")
    path = MANUAL_DIR / f"{model_id}.md"
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass
    cur.execute("UPDATE models SET manual=NULL WHERE id=?", (model_id,))
    conn.commit()
    row = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    conn.close()
    return row_to_model(row)
```

- [ ] **Step 2: Mount it and remove the routes from `app/main.py`**

In `app/main.py`: `from app.routers import folders, models, manuals` and `app.include_router(manuals.router)`. Delete the three manual routes from `app/main.py`.

- [ ] **Step 3: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/manuals.py backend/app/main.py
git commit -m "refactor: extract manuals router"
```

---

### Task 11: Extract `app/routers/settings.py` + `app/routers/importers.py` — `app/main.py` becomes a pure router-mounting shell

**Files:**
- Create: `backend/app/routers/settings.py`
- Create: `backend/app/routers/importers.py`
- Move: `backend/importers/` → `backend/app/importers/` (the two upstream importer modules, untouched — only their import path changes)
- Modify: `backend/app/main.py` (remove the settings/import routes and the `importer_for_url`/`importer_for_source` helpers — now owned by the two new routers)
- Modify: `backend/tests/test_settings_and_importers.py` (patch target moves with the code)

**Interfaces:**
- Produces: `app/routers/importers.py` exports `router`, `importer_for_url(url)`, `importer_for_source(source)`.
- Consumes: `app.db.{get_db_conn, get_setting, set_setting, clear_setting, now_ms, UPLOAD_DIR}`, `app.importers.{makerworld, printables}`.

- [ ] **Step 1: Move the importer package**

```bash
git mv backend/importers backend/app/importers
```

- [ ] **Step 2: Create the settings router**

```python
# backend/app/routers/settings.py
from fastapi import APIRouter, HTTPException

from app.db import get_setting, set_setting, clear_setting

router = APIRouter()


@router.get("/api/settings/makerworld-token")
def makerworld_token_status():
    return {"configured": bool(get_setting("makerworld_bambu_token"))}


@router.put("/api/settings/makerworld-token")
def update_makerworld_token(payload: dict):
    if payload.get("clear") is True:
        clear_setting("makerworld_bambu_token")
        return {"configured": False}
    token = str(payload.get("token", "")).strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")
    set_setting("makerworld_bambu_token", token)
    return {"configured": True}
```

- [ ] **Step 3: Create the importers router**

```python
# backend/app/routers/importers.py
import os
import uuid
import json

from fastapi import APIRouter, HTTPException

from app.db import get_db_conn, get_setting, now_ms, UPLOAD_DIR
from app.importers import makerworld, printables

router = APIRouter()


def importer_for_url(url: str):
    if "makerworld.com" in url.lower():
        return makerworld.MakerWorldImporter(), "makerworld"
    return printables.PrintablesImporter(), "printables"


def importer_for_source(source: str):
    if source == "makerworld":
        return makerworld.MakerWorldImporter(get_setting("makerworld_bambu_token")), "MakerWorld"
    return printables.PrintablesImporter(), "Printables"


@router.post("/api/import/importid")
def import_model_by_id(payload: dict):
    source = payload.get("source", "printables")
    importer, source_label = importer_for_source(source)
    modelId = payload.get("id")
    modelName = payload.get("name")
    parentId = payload.get("parentId")
    previewPath = payload.get("previewPath")
    folderId = payload.get("folderId", "1")
    typeName = payload.get("typeName")
    mid = str(uuid.uuid4())
    ext = typeName if typeName is not None else ".stl"
    filename = f"{mid}.{ext}"
    path = os.path.join(UPLOAD_DIR, filename)

    try:
        if modelId is not None:
            file, thumbnail = importer.importfromId(modelId, parentId, previewPath)
            if file is not None:
                with open(path, "wb") as fh:
                    fh.write(file.content)
                size = os.path.getsize(path)
            else:
                raise ValueError("File Is Empty")
        else:
            raise ValueError("URL is None")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    model = {
        "id": mid, "name": modelName, "folderId": folderId if folderId != "all" else "1",
        "url": f"/api/models/{mid}/download", "size": size, "dateAdded": now_ms(),
        "tags": ["imported"], "description": f"Imported from {source_label}", "thumbnail": thumbnail,
    }
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail) VALUES (?,?,?,?,?,?,?,?,?)",
        (model["id"], model["name"], model["folderId"], model["url"], model["size"],
         model["dateAdded"], json.dumps(model["tags"]), model["description"], model["thumbnail"]),
    )
    conn.commit()
    conn.close()
    return model


@router.post("/api/import/options")
def import_model_options(payload: dict):
    url = payload.get("url")
    try:
        if url is not None:
            importer, _source_label = importer_for_url(url)
            modelData = importer.getModelOptions(url)
            if modelData is not None:
                return modelData
            raise ValueError("Collection Is Empty")
        raise ValueError("URL is None")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/printables/importid")
def import_printables_model_by_id(payload: dict):
    payload["source"] = "printables"
    return import_model_by_id(payload)


@router.post("/api/printables/options")
def import_printables_model_options(payload: dict):
    return import_model_options(payload)
```

- [ ] **Step 4: Remove the settings/import routes and helpers from `app/main.py`, mount the two new routers**

Delete `importer_for_url`, `importer_for_source`, the two `/api/settings/makerworld-token` routes, and the four `/api/import*`/`/api/printables*` routes from `app/main.py`. Delete the now-unused `from importers import makerworld, printables` import (nothing left in `main.py` calls it). Add:
```python
from app.routers import folders, models, manuals, settings, importers
```
and, after the existing `app.include_router(...)` lines:
```python
app.include_router(settings.router)
app.include_router(importers.router)
```

`app/main.py` is now just the app factory: constants import, `FastAPI()`, CORS middleware, five `include_router` calls, and (unchanged from the original file) the `if __name__ == "__main__":` uvicorn-run block.

- [ ] **Step 5: Update the mocked import path in the settings/importers test**

In `backend/tests/test_settings_and_importers.py`, change `patch("app.main.printables.PrintablesImporter", ...)` (set in Task 7) to `patch("app.routers.importers.printables.PrintablesImporter", ...)`.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all tests from Tasks 1–6 PASS. This is the milestone: every route from the original `app.py` now lives in a focused router module, and the characterization tests never had to change their assertions — only one `patch()` target string, because the import path moved.

- [ ] **Step 7: Commit**

```bash
git add backend/app backend/tests/test_settings_and_importers.py
git commit -m "refactor: extract settings/importers routers — app.main is now a pure router-mounting shell"
```

---

### Task 12: Extract `services/ingestion.py` (the shared seam every later phase depends on)

**Files:**
- Create: `backend/app/services/__init__.py` (empty)
- Create: `backend/app/services/ingestion.py`
- Create: `backend/tests/test_ingestion.py`
- Modify: `backend/app/routers/models.py` (`upload_model` calls the new service instead of inlining the logic)

**Interfaces:**
- Produces: `ingest_file(source_path: str, folder_id: str, original_filename: str, tags: Optional[List[str]] = None, thumbnail: Optional[str] = None, move: bool = False) -> dict` — copies (or moves, if `move=True`) the file into `UPLOAD_DIR`, inserts the `models` row, returns the same shape `row_to_model` produces. Phase 1 (watcher, inbox) and Phase 5 (acquisition) call this directly instead of re-implementing upload. **Must stay streaming, never buffer a whole file in memory** — this is the ingestion path for potentially large STL/3MF files, and every future phase routes through it.
- Consumes: `app.db.{get_db_conn, save_upload_file, now_ms}`, `app.db.UPLOAD_DIR`.

- [ ] **Step 1: Write the failing test first (this is new code, not a characterization)**

```python
# backend/tests/test_ingestion.py
import os


def test_ingest_file_copies_into_upload_dir_and_creates_model_row(client, tmp_path, monkeypatch):
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR

    source = tmp_path / "incoming.stl"
    source.write_bytes(b"solid source endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="incoming.stl", tags=["watched"])

    assert model["name"] == "incoming.stl"
    assert model["folderId"] == "1"
    assert model["tags"] == ["watched"]
    assert os.path.exists(os.path.join(UPLOAD_DIR, f"{model['id']}.stl"))
    assert source.exists()  # default move=False — the watcher must never delete the user's original


def test_ingest_file_with_move_true_relocates_instead_of_copying(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR

    source = tmp_path / "scratch.stl"
    source.write_bytes(b"solid scratch endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="scratch.stl", move=True)

    assert os.path.exists(os.path.join(UPLOAD_DIR, f"{model['id']}.stl"))
    assert not source.exists()  # move=True: the scratch file is gone, not duplicated

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert any(m["id"] == model["id"] for m in listed)
```

- [ ] **Step 2: Run and confirm it fails**

Run: `cd backend && pytest tests/test_ingestion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services'`

- [ ] **Step 3: Implement**

```python
# backend/app/services/ingestion.py
import os
import uuid
import json
import shutil
from typing import Optional, List

from app.db import get_db_conn, now_ms, UPLOAD_DIR


def ingest_file(
    source_path: str,
    folder_id: str,
    original_filename: str,
    tags: Optional[List[str]] = None,
    thumbnail: Optional[str] = None,
    move: bool = False,
) -> dict:
    """Put a file already on disk into the library and register it as a model.
    Shared by manual upload, the folder watcher (Phase 1), and the acquisition
    queue drain worker (Phase 5) so there is exactly one ingestion code path.

    move=False (default) copies source_path and leaves it in place — the right
    choice for the folder watcher (#2/#3): the user is watching a real folder
    they still browse elsewhere, so relocating their file out of it on ingest
    would be destructive and surprising. move=True renames instead of copying —
    for callers whose source_path is a disposable scratch file they made solely
    to hand off here (upload_model below; later, the acquisition drain worker's
    downloaded-to-a-temp-location files), a same-filesystem move is a single
    filesystem rename with no data copy at all.
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
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            model["id"], model["name"], model["folderId"], model["url"], model["size"],
            model["dateAdded"], json.dumps(model["tags"]), model["description"], model["thumbnail"],
        ),
    )
    conn.commit()
    conn.close()
    return model
```

```python
# backend/app/services/__init__.py
# (empty — marks services/ as a package)
```

- [ ] **Step 4: Run again to confirm it passes**

Run: `cd backend && pytest tests/test_ingestion.py -v`
Expected: PASS.

- [ ] **Step 5: Refactor `upload_model` to call the shared service, streaming the upload to a scratch file instead of buffering it, then moving (not copying) that scratch file into place**

The original inlined `upload_model` streamed straight to its final destination via `save_upload_file()`'s `shutil.copyfileobj`, never holding the whole file in memory. The extraction must preserve that — a naive `tmp.write(file.file.read())` would read the entire upload into a RAM buffer before writing it anywhere, a real regression for an app whose job is ingesting potentially large STL/3MF files. Streaming into a scratch file *in `UPLOAD_DIR` itself*, then handing it to `ingest_file(..., move=True)`, keeps the whole path to a single streamed write plus a same-filesystem rename — no buffering, no second full copy pass:

```python
# backend/app/routers/models.py — replace the body of upload_model
@router.post("/api/models/upload")
def upload_model(
    file: UploadFile = File(...),
    folderId: str = Form("1"),
    thumbnail: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
):
    tag_list: List[str] = []
    if tags:
        try:
            tag_list = json.loads(tags)
        except Exception:
            tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

    filename_str = file.filename or ".stl"
    suffix = os.path.splitext(filename_str)[1] or ".stl"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=UPLOAD_DIR)
    with os.fdopen(fd, "wb") as tmp:
        shutil.copyfileobj(file.file, tmp)  # streamed, not file.file.read() — never buffers the whole upload
    try:
        return ingest_file(tmp_path, folderId, filename_str, tags=tag_list, thumbnail=thumbnail, move=True)
    finally:
        if os.path.exists(tmp_path):  # already gone after a successful move; only cleans up on an ingest_file failure
            os.remove(tmp_path)
```

Add `import tempfile`, `import shutil`, and `from app.services.ingestion import ingest_file` to the top of `backend/app/routers/models.py` (`shutil` isn't already imported there — `os`, `uuid`, `json`, `base64` are, per Task 9's Step 1).

- [ ] **Step 6: Run the full suite — this is the regression check that matters most in this task**

Run: `cd backend && pytest -v`
Expected: `test_upload_model_persists_and_lists` and every other model test from Task 3/4 still PASS — the route's observable behavior (response shape, status codes, side effects) is identical, only its implementation now shares code with the future watcher/acquisition ingestion paths, and does so without ever holding a full upload in memory.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services backend/app/routers/models.py backend/tests/test_ingestion.py
git commit -m "refactor: extract services/ingestion.py, route upload_model through it"
```

---

### Task 13: Phase-0 schema additions

**Files:**
- Modify: `backend/app/db.py` (`init_db`)
- Create: `backend/tests/test_schema_migration.py`

**Interfaces:**
- Produces: five new nullable columns on `models` — `author TEXT`, `sourceUrl TEXT`, `category TEXT`, `colorCount INTEGER`, `sliceSettings TEXT` (JSON) — available to every later phase (`#8`, `#19`, `#20`, `#23` in `docs/ARCHITECTURE.md`'s requirement map).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_schema_migration.py
import sqlite3


def test_models_table_has_phase0_columns(client, monkeypatch):
    from app.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(models)")}
    conn.close()
    assert {"author", "sourceUrl", "category", "colorCount", "sliceSettings"}.issubset(columns)


def test_migration_is_idempotent_on_already_migrated_db(client):
    from app.db import init_db
    init_db()  # calling it twice must not raise
    init_db()
```

- [ ] **Step 2: Run and confirm it fails**

Run: `cd backend && pytest tests/test_schema_migration.py -v`
Expected: FAIL — `assert {...}.issubset(...)` fails, the columns don't exist yet.

- [ ] **Step 3: Add the migrations to `init_db()`**, right after the existing `manual` column migration in `backend/app/db.py`:

```python
    for column, coltype in [
        ("author", "TEXT"),
        ("sourceUrl", "TEXT"),
        ("category", "TEXT"),
        ("colorCount", "INTEGER"),
        ("sliceSettings", "TEXT"),
    ]:
        try:
            cur.execute(f"ALTER TABLE models ADD COLUMN {column} {coltype}")
        except sqlite3.OperationalError:
            pass
```

- [ ] **Step 4: Run again to confirm it passes**

Run: `cd backend && pytest tests/test_schema_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Update `row_to_model` so the new columns actually surface in API responses**

```python
# backend/app/db.py — row_to_model, add before the closing brace
        "author": row["author"] if "author" in row.keys() else None,
        "sourceUrl": row["sourceUrl"] if "sourceUrl" in row.keys() else None,
        "category": row["category"] if "category" in row.keys() else None,
        "colorCount": row["colorCount"] if "colorCount" in row.keys() else None,
        "sliceSettings": row["sliceSettings"] if "sliceSettings" in row.keys() else None,
```

- [ ] **Step 6: Allow the new fields through `update_model`'s allowlist**

In `backend/app/routers/models.py::update_model`, change:
```python
    allowed = ["name", "folderId", "tags", "description", "thumbnail"]
```
to:
```python
    allowed = ["name", "folderId", "tags", "description", "thumbnail", "author", "sourceUrl", "category", "colorCount", "sliceSettings"]
```

- [ ] **Step 7: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all PASS, including the new schema tests.

- [ ] **Step 8: Commit**

```bash
git add backend/app/db.py backend/app/routers/models.py backend/tests/test_schema_migration.py
git commit -m "feat: add author/sourceUrl/category/colorCount/sliceSettings columns (Phase 0 schema)"
```

---

### Task 14: Frontend test harness

**Files:**
- Create: `frontend/vitest.config.ts`
- Create: `frontend/tests/setup.ts`
- Create: `frontend/tests/ModelList.smoke.test.tsx`
- Modify: `frontend/package.json`

**Interfaces:**
- Produces: `npm test` runnable in `frontend/`, using Vitest + React Testing Library against the existing `ModelList` component (no new components to test yet — this task only proves the harness works).

- [ ] **Step 1: Add dev dependencies**

Append to `frontend/package.json` `devDependencies`:
```json
    "vitest": "^2.1.0",
    "@testing-library/react": "^16.0.0",
    "@testing-library/jest-dom": "^6.5.0",
    "jsdom": "^25.0.0"
```
Add to `scripts`: `"test": "vitest run"`.

- [ ] **Step 2: Configure Vitest**

```typescript
// frontend/vitest.config.ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    globals: true,
  },
})
```

```typescript
// frontend/tests/setup.ts
import '@testing-library/jest-dom/vitest'
```

- [ ] **Step 3: Write one smoke test against the existing `ModelList` component**

`ModelList` (`frontend/components/ModelList.tsx`) takes 16 required props (`ModelListProps`, lines 50–72) and pulls in MUI, `lucide-react`, and `getEnabledLaunchSlicers`/`SLICERS`/`api` from `../services/api` at module scope — so the smoke test needs every prop supplied, not a partial guess:

```tsx
// frontend/tests/ModelList.smoke.test.tsx
import { render } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import ModelList from '../components/ModelList'

describe('ModelList', () => {
  it('renders without crashing given an empty model list', () => {
    const { container } = render(
      <ModelList
        models={[]}
        folders={[]}
        currentFolderName="All Models"
        onBackNavigation={vi.fn()}
        onUpload={vi.fn()}
        onImport={vi.fn()}
        onSelectModel={vi.fn()}
        onDelete={vi.fn()}
        onOpenManual={vi.fn()}
        selectedModelId={null}
        selectedIds={new Set()}
        onToggleSelection={vi.fn()}
        onSelectAll={vi.fn()}
        onClearSelection={vi.fn()}
        onNavigateFolder={vi.fn()}
        onMoveToFolder={vi.fn()}
        onUploadToFolder={vi.fn()}
      />
    )
    expect(container).toBeTruthy()
  })
})
```

`getEnabledLaunchSlicers()` reads `localStorage`, which jsdom (this config's `test.environment`) provides natively, so no extra mocking is needed for this smoke test.

- [ ] **Step 4: Install and run**

Run: `cd frontend && npm install && npm test`
Expected: 1 test PASSES.

- [ ] **Step 5: Commit**

```bash
git add frontend/vitest.config.ts frontend/tests/setup.ts frontend/tests/ModelList.smoke.test.tsx frontend/package.json
git commit -m "test: add vitest + React Testing Library harness"
```

---

### Task 15: Docker Compose dev override

**Files:**
- Create: `docker-compose.override.yml`

**Interfaces:**
- Produces: a `docker compose up` path that bind-mounts source and enables reload, so every phase's routers/components can be verified running in the same containerized shape the real `docker-compose.yml` deploys — not just under bare `pytest`/`vitest`. This is the actual parity check the Global Constraints note above points at: host tests run under whatever Python is locally installed, this runs under the Dockerfile's pinned `python:3.9-slim`.

The real `backend/docker-compose.yml` (read directly from the repo, not the README's Docker-Hub-image snippet, which uses different service names — `stlvbackend`/`stlvfrontend` — that don't exist in this file) has services named **`backend`** and **`frontend`**, and its `volumes:` bind to `${UPLOAD_PATH}`/`${DATA_PATH}` — Linux absolute paths (`/root/stlv/...` in the committed `.env`) that don't resolve as Windows Docker Desktop bind mounts. An override file's `volumes:` list *replaces* the base file's list for that service (Compose merge semantics for list-valued keys), so pointing it at relative `./dev-data/...` paths sidesteps the `.env` path values entirely without editing `.env` itself. `APP_PORT`/`API_PORT`/`APP_URL`/`API_URL` in the committed `.env` are already sane localhost defaults (`8999`/`8998`) and don't need overriding.

- [ ] **Step 1: Write the override**

```yaml
# docker-compose.override.yml — local dev only, not for production
services:
  backend:
    volumes:
      - ./backend/app:/app/app
      - ./dev-data/uploads:/app/uploads
      - ./dev-data/data:/app/data
    command: uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
  frontend:
    volumes:
      - ./frontend:/app
      - /app/node_modules
    command: npm run dev -- --host 0.0.0.0
```

- [ ] **Step 2: Verify it builds and the API responds**

Run: `docker compose up --build -d && sleep 5 && curl -s http://localhost:8998/api/folders`
Expected: JSON array containing the seeded folders (`Characters`, `Vehicles`, `Terrain`, `Tanks`) — `8998` from the committed `.env`'s `API_PORT`, confirmed above, not guessed.

- [ ] **Step 3: Tear down**

Run: `docker compose down`

- [ ] **Step 4: Commit**

```bash
git add docker-compose.override.yml
echo "dev-data/" >> .gitignore
git add .gitignore
git commit -m "chore: add docker-compose dev override for hot-reload local iteration"
```

---

## Known pre-existing bugs (found by actually running the characterization tests against the real repo, not carried over from upstream's issue tracker)

- **`GET /api/models/{id}/download` on an unknown id returns 500, not 404.** `get_model_info()` calls `row_to_model(None)` with no null guard when the id doesn't match a row, crashing with `TypeError: 'NoneType' object is not subscriptable` instead of a clean 404. Confirmed live: running `test_download_missing_model_currently_returns_500_known_bug` (Task 3) against the unmodified upstream `app.py` reproduces exactly this traceback. Tasks 3 and 9 both deliberately preserve this behavior rather than silently fixing it mid-refactor — Phase 0's job is "same behavior, different files," not bug fixes. File a real fix as its own small task once Phase 0 lands: add the null check in `app/routers/models.py::get_model_info`, change the characterization test's expectation to 404, and note it as a deliberate, reviewable behavior change (not bundled into an "extraction" commit).

---

## Self-review notes (for whoever executes this plan)

- **Spec coverage:** every Phase-0-scoped requirement from `docs/ARCHITECTURE.md`'s table (`#19`, `#20`, partial `#8`, `#23`) is covered by Task 13; the ingestion seam Phases 1/5 depend on is covered by Task 12; every existing upstream endpoint is characterized (Tasks 2–6) before it's moved (Tasks 8–11).
- **Verify before moving on:** Task 8 through Task 11 each end with "run the full suite" for a reason — if any characterization test breaks during an extraction, stop and fix the extraction, don't edit the test to match new behavior. The tests are the spec for "unchanged" in this phase.
- **Next plans:** once this lands, write `docs/superpowers/plans/<date>-watcher-and-inbox.md` for Phase 1, following the same characterization-first discipline only where it touches existing code — Phase 1's watcher and inbox are net-new, so they're pure TDD (red → green) like Task 12/13 here, not characterize-then-extract.
