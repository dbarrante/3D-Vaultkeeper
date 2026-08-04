# File Organization: Import Wizard + Logical/File View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Dave point the app at a raw, unorganized directory on disk, manually sort its contents into his logical library folders inside a wizard, and have the app physically relocate the files there — plus add a read-only sidebar toggle that shows where files actually live on disk, independent of logical organization.

**Architecture:** A `filePath` column on `models` becomes the single source of truth for where a file physically lives, populated only by actual moves (never by logical `folderId` reorganizing). A new backend router/service pair (`import_wizard`) provides a read-only directory-tree endpoint and a commit endpoint that moves files via the existing `ingest_file` ingestion path (extended with a `dest_subpath` parameter). A new frontend `ImportWizard` component provides the two-pane drag-and-drop staging UI, reachable from Settings. `Sidebar.tsx` gains a Logical/File toggle that re-derives its tree from `filePath` instead of `folderId` when in File mode.

**Tech Stack:** FastAPI + SQLite (backend, existing), React + MUI + `@mui/x-tree-view` (frontend, existing). No new dependencies.

## Global Constraints

- No automatic logical-to-disk sync feature — only the Import Wizard causes physical file moves. Reorganizing in Logical mode (today's existing drag-and-drop) never touches `filePath` or the filesystem.
- No retroactive backfill of `filePath` that invents structure — existing copy-mode uploads that predate this feature keep their real flat storage location; the backfill records that location as-is, it doesn't reorganize anything.
- No tracking of "already imported from this root" — the wizard can be run again against the same directory; it will simply show whatever's left there.
- No deduplication against files already in the library.
- Physical filenames for **model files** stay the existing opaque `<model-id>.<ext>` convention (via `ingest_file`) — never the original filename — so filesystem name collisions are architecturally impossible for them; no auto-suffix logic should be added for model files. This does **not** apply to non-model sibling files (Task 4's `commit_placement_file`), which keep their real original names when moved and can therefore genuinely collide — Task 4's small auto-suffix loop for that path is correct and intentional, not a violation of this constraint.
- A dragged folder's contents move file-by-file (not as a single directory move), so results and retry operate at file granularity, grouped for display under the placement the user actually dragged.
- Folder names become real filesystem path segments for the first time in this feature — every segment must be sanitized before being joined into a path.

---

## File Structure

**Backend:**
- Modify `backend/app/db.py` — add `filePath` column, backfill helper, `row_to_model` update.
- Modify `backend/app/services/ingestion.py` — add `dest_subpath` parameter to `ingest_file`.
- Create `backend/app/services/import_wizard.py` — path sanitization, folder-path resolution, directory tree walk, per-file commit logic. Mirrors the existing `services/scan.py` split (routers stay thin, services hold logic).
- Create `backend/app/routers/import_wizard.py` — `GET /api/import/tree`, `POST /api/import/commit`. Mirrors `routers/watcher.py`'s thin-router style.
- Modify `backend/app/main.py` — register the new router.
- Create `backend/tests/test_import_wizard.py` — service-level tests for tree walk, path sanitization, commit.
- Modify `backend/tests/test_schema_migration.py` — `filePath` column + backfill tests.
- Modify `backend/tests/test_ingestion.py` — `dest_subpath` tests.

**Frontend:**
- Modify `frontend/types.ts` — `filePath` on `STLModel`; new `ImportTreeNode`/`ImportPlacement`/`ImportResult` types.
- Modify `frontend/services/api.ts` — `getImportTree`, `commitImport`.
- Create `frontend/components/ImportWizard.tsx` — two-pane staging, review, commit, results/retry.
- Modify `frontend/components/WatcherInbox.tsx` — "Import from folder..." entry point, opens the wizard.
- Modify `frontend/components/Sidebar.tsx` — Logical/File toggle, file-mode tree derivation.
- Modify `frontend/App.tsx` — file-mode-aware model filtering, pass toggle state through to `ModelList`.

---

### Task 1: `filePath` column, backfill, and type plumbing

**Files:**
- Modify: `backend/app/db.py:99-112` (column list), `backend/app/db.py:164-195` (`row_to_model`), add new function after `init_db`
- Modify: `frontend/types.ts:20-40` (`STLModel` interface)
- Test: `backend/tests/test_schema_migration.py`

**Interfaces:**
- Produces: `models.filePath` column (TEXT, nullable). `row_to_model()` includes `"filePath": <str | None>` in every returned model dict. Every later backend task that creates or reads a model relies on this.
- Produces: `STLModel.filePath?: string | null` on the frontend, consumed by Task 8 (Sidebar toggle) and Task 9 (App.tsx filtering).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_schema_migration.py`:

```python
def test_models_table_has_file_path_column(client):
    from app.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(models)")}
    conn.close()
    assert "filePath" in columns


def test_backfill_sets_file_path_for_reference_mode_from_source_path(client, tmp_path):
    from app.db import get_db_conn, init_db

    source = tmp_path / "linked.stl"
    source.write_bytes(b"solid endsolid")

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,sourcePath,storageMode) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("m1", "linked.stl", "1", "/api/models/m1/download", 10, 0, "[]", "", None, str(source), "reference"),
    )
    conn.commit()
    conn.close()

    init_db()  # re-run: triggers the backfill pass on the row just inserted

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id='m1'").fetchone()
    conn.close()
    assert row["filePath"] == str(source)


def test_backfill_sets_file_path_for_copy_mode_from_upload_dir(client):
    from app.db import get_db_conn, init_db, UPLOAD_DIR

    dest = UPLOAD_DIR / "m2.stl"
    dest.write_bytes(b"solid endsolid")

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,storageMode) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("m2", "x.stl", "1", "/api/models/m2/download", 10, 0, "[]", "", None, "copy"),
    )
    conn.commit()
    conn.close()

    init_db()

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id='m2'").fetchone()
    conn.close()
    assert row["filePath"] == str(dest)


def test_backfill_is_idempotent_and_does_not_overwrite_existing_file_path(client):
    from app.db import get_db_conn, init_db

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,storageMode,filePath) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("m3", "x.stl", "1", "/api/models/m3/download", 10, 0, "[]", "", None, "copy", "/already/set/path.stl"),
    )
    conn.commit()
    conn.close()

    init_db()

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id='m3'").fetchone()
    conn.close()
    assert row["filePath"] == "/already/set/path.stl"


def test_row_to_model_includes_file_path(client):
    from app.db import get_db_conn, row_to_model

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,storageMode,filePath) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("m4", "x.stl", "1", "/api/models/m4/download", 10, 0, "[]", "", None, "copy", "/some/path.stl"),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM models WHERE id='m4'").fetchone()
    conn.close()
    assert row_to_model(row)["filePath"] == "/some/path.stl"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_schema_migration.py -v`
Expected: FAIL — `filePath` column doesn't exist yet (`sqlite3.OperationalError: no such column`).

- [ ] **Step 3: Add the column and backfill to `backend/app/db.py`**

In the existing `for column, coltype in [...]` loop (around line 99), add `("filePath", "TEXT"),` to the list, alongside the existing `("sourcePath", "TEXT"),` line.

Immediately after that loop (still inside `init_db()`, before the `watch_folders` table creation), add:

```python
    _backfill_file_paths(cur)
```

After `init_db()`'s closing (before `now_ms()`), add the new function:

```python
def _backfill_file_paths(cur: sqlite3.Cursor) -> None:
    """One-time backfill for filePath on upgrade: the column starts NULL
    for every pre-existing row until this runs once. Reference-mode rows
    just mirror sourcePath -- the app never moves those files, so it's
    already their real location. Copy-mode rows are matched to their flat
    UPLOAD_DIR file using the same model-id-prefix convention the download
    endpoint (routers/models.py) already uses to locate them. Guarded by
    `filePath IS NULL` throughout, so re-running init_db() after the first
    successful backfill is a no-op.
    """
    cur.execute(
        "UPDATE models SET filePath = sourcePath "
        "WHERE storageMode = 'reference' AND filePath IS NULL AND sourcePath IS NOT NULL"
    )
    rows = cur.execute(
        "SELECT id FROM models WHERE storageMode = 'copy' AND filePath IS NULL"
    ).fetchall()
    if rows and UPLOAD_DIR.is_dir():
        existing_files = os.listdir(UPLOAD_DIR)
        for row in rows:
            model_id = row["id"]
            match = next((f for f in existing_files if f.startswith(model_id)), None)
            if match:
                cur.execute(
                    "UPDATE models SET filePath = ? WHERE id = ?",
                    (str(UPLOAD_DIR / match), model_id),
                )
```

Note: `init_db()` calls `conn.commit()` later in the function (around the existing `MAKERWORLD_BAMBU_TOKEN` block) — the backfill's writes ride along with that existing commit, no new commit call needed.

In `row_to_model()` (around line 171, alongside the existing `storage_mode`/`source_path` lines), add:

```python
    file_path = row["filePath"] if "filePath" in row.keys() else None
```

And add `"filePath": file_path,` to the returned dict (alongside the existing `"sourcePath": source_path,` line).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_schema_migration.py -v`
Expected: PASS (all tests, including the pre-existing ones in that file)

- [ ] **Step 5: Update the frontend type**

In `frontend/types.ts`, add to the `STLModel` interface (alongside the existing `sourcePath?: string | null;` line):

```typescript
  filePath?: string | null;
```

- [ ] **Step 6: Run the full backend suite to confirm no regressions**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -v`
Expected: PASS (all tests, including `test_ingestion.py` and `test_scan.py` which insert/read models)

- [ ] **Step 7: Commit**

```bash
git add backend/app/db.py frontend/types.ts backend/tests/test_schema_migration.py
git commit -m "feat: add filePath column as source of truth for real file location"
```

---

### Task 2: `dest_subpath` on `ingest_file`

**Files:**
- Modify: `backend/app/services/ingestion.py`
- Test: `backend/tests/test_ingestion.py`

**Interfaces:**
- Consumes: nothing new from Task 1 beyond the `filePath` column already existing.
- Produces: `ingest_file(..., dest_subpath: Optional[str] = None)` — when provided (and not `reference_only`), the copy-mode file lands at `UPLOAD_DIR/<dest_subpath>/<model-id><ext>` instead of flat in `UPLOAD_DIR`. Returned model dict and the DB row both get `filePath` set to that resolved path (or to `source_path` when `reference_only`). Task 4 (commit endpoint) is the first real caller.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_ingestion.py`:

```python
def test_ingest_file_with_dest_subpath_places_file_in_subdirectory(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR

    source = tmp_path / "hull.stl"
    source.write_bytes(b"solid hull endsolid")

    model = ingest_file(
        str(source),
        folder_id="1",
        original_filename="hull.stl",
        move=True,
        dest_subpath="Vehicles/Tanks",
    )

    expected_dir = UPLOAD_DIR / "Vehicles" / "Tanks"
    assert os.path.isdir(expected_dir)
    assert os.path.exists(os.path.join(expected_dir, f"{model['id']}.stl"))
    assert model["filePath"] == os.path.join(str(expected_dir), f"{model['id']}.stl")
    assert not source.exists()  # move=True


def test_ingest_file_without_dest_subpath_stays_flat(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR

    source = tmp_path / "flat.stl"
    source.write_bytes(b"solid flat endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="flat.stl")

    assert os.path.exists(os.path.join(UPLOAD_DIR, f"{model['id']}.stl"))
    assert model["filePath"] == os.path.join(str(UPLOAD_DIR), f"{model['id']}.stl")


def test_ingest_file_reference_only_sets_file_path_to_source(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "linked.stl"
    source.write_bytes(b"solid linked endsolid")

    model = ingest_file(
        str(source), folder_id="1", original_filename="linked.stl", reference_only=True
    )

    assert model["filePath"] == str(source)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_ingestion.py -v`
Expected: FAIL — `ingest_file() got an unexpected keyword argument 'dest_subpath'` and `KeyError: 'filePath'`.

- [ ] **Step 3: Modify `ingest_file`**

In `backend/app/services/ingestion.py`, update the signature and body:

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
    dest_subpath: Optional[str] = None,
) -> dict:
    """Put a file already on disk into the library and register it as a model.
    ...(existing docstring paragraphs unchanged)...

    dest_subpath (Import Wizard): places the copy-mode file inside
    UPLOAD_DIR/<dest_subpath>/ instead of flat in UPLOAD_DIR, creating the
    subdirectory if needed. The physical filename is still the opaque
    <id><ext> convention below -- this only changes which directory it
    lands in, so the existing collision-avoidance is unaffected. None
    (the default) preserves today's flat behavior for every existing
    caller (manual upload, the watcher, the acquisition queue).
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
        file_path_value = source_path
    else:
        dest_dir = os.path.join(UPLOAD_DIR, dest_subpath) if dest_subpath else UPLOAD_DIR
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, f"{mid}{ext}")
        if move:
            shutil.move(source_path, dest_path)
        else:
            shutil.copyfile(source_path, dest_path)
        size = os.path.getsize(dest_path)
        file_path_value = dest_path

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
        "filePath": file_path_value,
    }
    storage_mode = "reference" if reference_only else "copy"
    source_to_record = source_path if (record_source or reference_only) else None

    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,sourcePath,storageMode,filePath) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            model["id"], model["name"], model["folderId"], model["url"], model["size"],
            model["dateAdded"], json.dumps(model["tags"]), model["description"], model["thumbnail"],
            source_to_record, storage_mode, model["filePath"],
        ),
    )
    conn.commit()
    conn.close()
    return model
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_ingestion.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full backend suite to confirm no regressions**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ingestion.py backend/tests/test_ingestion.py
git commit -m "feat: add dest_subpath to ingest_file for subdirectory placement"
```

---

### Task 3: Tree-peek endpoint

**Files:**
- Create: `backend/app/services/import_wizard.py` (partial — `build_tree` only; Task 4 adds the rest)
- Create: `backend/app/routers/import_wizard.py` (partial — `GET /api/import/tree` only; Task 4 adds `POST /commit`)
- Modify: `backend/app/main.py` — register the router
- Test: `backend/tests/test_import_wizard.py` (new file)

**Interfaces:**
- Consumes: `app.services.scan.SUPPORTED_EXTENSIONS` (the existing `{".stl", ".3mf", ".obj", ".step", ".stp"}` set — reused, not redefined).
- Produces: `build_tree(root: Path) -> dict` with shape `{"name": str, "path": str, "folders": [<same shape>, ...], "files": [{"name": str, "path": str, "isModel": bool, "size": int}, ...]}`. `GET /api/import/tree?path=<root>` returns this directly. Task 4's `expand_placement` and the frontend wizard (Task 5) both consume this shape.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_import_wizard.py`:

```python
import os


def test_build_tree_walks_nested_directories_and_flags_model_files(tmp_path):
    from app.services.import_wizard import build_tree

    root = tmp_path / "raw"
    root.mkdir()
    (root / "loose.stl").write_bytes(b"solid loose endsolid")
    (root / "notes.txt").write_bytes(b"just notes")
    subdir = root / "Tank Kit"
    subdir.mkdir()
    (subdir / "hull.stl").write_bytes(b"solid hull endsolid")
    (subdir / "photo.jpg").write_bytes(b"fake jpg bytes")

    tree = build_tree(root)

    assert tree["name"] == "raw"
    assert tree["path"] == str(root)
    assert len(tree["files"]) == 2
    loose = next(f for f in tree["files"] if f["name"] == "loose.stl")
    assert loose["isModel"] is True
    notes = next(f for f in tree["files"] if f["name"] == "notes.txt")
    assert notes["isModel"] is False

    assert len(tree["folders"]) == 1
    kit = tree["folders"][0]
    assert kit["name"] == "Tank Kit"
    hull = next(f for f in kit["files"] if f["name"] == "hull.stl")
    assert hull["isModel"] is True
    photo = next(f for f in kit["files"] if f["name"] == "photo.jpg")
    assert photo["isModel"] is False


def test_build_tree_handles_empty_directory(tmp_path):
    from app.services.import_wizard import build_tree

    root = tmp_path / "empty"
    root.mkdir()

    tree = build_tree(root)

    assert tree["folders"] == []
    assert tree["files"] == []


def test_import_tree_endpoint_returns_walked_structure(client, tmp_path):
    root = tmp_path / "raw"
    root.mkdir()
    (root / "part.stl").write_bytes(b"solid part endsolid")

    resp = client.get("/api/import/tree", params={"path": str(root)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["files"][0]["name"] == "part.stl"
    assert body["files"][0]["isModel"] is True


def test_import_tree_endpoint_rejects_nonexistent_path(client, tmp_path):
    resp = client.get("/api/import/tree", params={"path": str(tmp_path / "does_not_exist")})

    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_import_wizard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.import_wizard'`

- [ ] **Step 3: Create `backend/app/services/import_wizard.py`**

```python
from pathlib import Path

from app.services.scan import SUPPORTED_EXTENSIONS


def build_tree(root: Path) -> dict:
    """Read-only recursive walk of a raw directory for the Import Wizard's
    left pane. No DB writes and no files are touched -- this never
    modifies anything, it only describes what's already there.
    """
    folders = []
    files = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name.lower()):
        if entry.is_dir():
            folders.append(build_tree(entry))
        elif entry.is_file():
            files.append({
                "name": entry.name,
                "path": str(entry),
                "isModel": entry.suffix.lower() in SUPPORTED_EXTENSIONS,
                "size": entry.stat().st_size,
            })
    return {"name": root.name, "path": str(root), "folders": folders, "files": files}
```

- [ ] **Step 4: Create `backend/app/routers/import_wizard.py`**

```python
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.services.import_wizard import build_tree

router = APIRouter(prefix="/api/import", tags=["import"])


@router.get("/tree")
def get_import_tree(path: str):
    root = Path(path)
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    return build_tree(root)
```

- [ ] **Step 5: Register the router in `backend/app/main.py`**

Add the import alongside the existing router imports:

```python
from app.routers import folders, models, manuals, settings, importers, watcher, inbox, ai, import_wizard
```

Add the registration alongside the existing `app.include_router(...)` calls:

```python
app.include_router(import_wizard.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_import_wizard.py -v`
Expected: PASS

- [ ] **Step 7: Run the full backend suite to confirm no regressions**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/import_wizard.py backend/app/routers/import_wizard.py backend/app/main.py backend/tests/test_import_wizard.py
git commit -m "feat: add read-only directory tree endpoint for import wizard"
```

---

### Task 4: Commit endpoint (path sanitization, folder resolution, per-file move)

**Files:**
- Modify: `backend/app/services/import_wizard.py` — add `sanitize_path_segment`, `folder_disk_path`, `expand_placement`, `commit_placement_file`
- Modify: `backend/app/routers/import_wizard.py` — add `POST /commit`
- Test: `backend/tests/test_import_wizard.py`

**Interfaces:**
- Consumes: `build_tree` (Task 3, for test fixtures only — not called by commit itself), `ingest_file` with `dest_subpath` (Task 2), `SUPPORTED_EXTENSIONS` (existing, from `scan.py`).
- Produces: `POST /api/import/commit` accepting `{"placements": [{"sourcePath": str, "isFolder": bool, "targetFolderId": str}, ...]}`, returning `{"results": [{"sourcePath": str, "placementSourcePath": str, "status": "ok"|"error", "error"?: str, "isModel": bool}, ...]}`. Task 6 (frontend results/retry) is the consumer.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_import_wizard.py`:

```python
def test_sanitize_path_segment_replaces_illegal_characters():
    from app.services.import_wizard import sanitize_path_segment

    assert sanitize_path_segment('Tanks: "Heavy"') == "Tanks_ _Heavy_"


def test_sanitize_path_segment_strips_trailing_dots_and_spaces():
    from app.services.import_wizard import sanitize_path_segment

    assert sanitize_path_segment("Vehicles.. ") == "Vehicles"


def test_sanitize_path_segment_suffixes_reserved_windows_names():
    from app.services.import_wizard import sanitize_path_segment

    assert sanitize_path_segment("CON") == "CON_"
    assert sanitize_path_segment("com3") == "com3_"


def test_folder_disk_path_walks_parent_chain(client):
    from app.db import get_db_conn
    from app.services.import_wizard import folder_disk_path

    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "Vehicles", None))
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f2", "Tanks", "f1"))
    conn.commit()
    conn.close()

    assert folder_disk_path("f2") == os.path.join("Vehicles", "Tanks")
    assert folder_disk_path("f1") == "Vehicles"


def test_folder_disk_path_sanitizes_each_segment(client):
    from app.db import get_db_conn
    from app.services.import_wizard import folder_disk_path

    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f3", "A/B", None))
    conn.commit()
    conn.close()

    assert folder_disk_path("f3") == "A_B"


def test_expand_placement_loose_file_returns_itself(tmp_path):
    from app.services.import_wizard import expand_placement

    f = tmp_path / "loose.stl"
    f.write_bytes(b"solid endsolid")

    result = expand_placement(str(f), is_folder=False)

    assert result == [f]


def test_expand_placement_folder_walks_recursively(tmp_path):
    from app.services.import_wizard import expand_placement

    root = tmp_path / "Tank Kit"
    root.mkdir()
    (root / "hull.stl").write_bytes(b"solid endsolid")
    sub = root / "extras"
    sub.mkdir()
    (sub / "turret.stl").write_bytes(b"solid endsolid")

    result = expand_placement(str(root), is_folder=True)

    names = {p.name for p in result}
    assert names == {"hull.stl", "turret.stl"}


def test_commit_placement_file_moves_model_and_creates_row(client, tmp_path):
    from app.db import get_db_conn
    from app.services.import_wizard import commit_placement_file

    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "Vehicles", None))
    conn.commit()
    conn.close()

    source = tmp_path / "hull.stl"
    source.write_bytes(b"solid endsolid")

    result = commit_placement_file(source, "f1")

    assert result["status"] == "ok"
    assert result["isModel"] is True
    assert not source.exists()

    conn = get_db_conn()
    row = conn.execute("SELECT folderId, storageMode, filePath FROM models WHERE filePath LIKE ?", (f"%Vehicles%",)).fetchone()
    conn.close()
    assert row["folderId"] == "f1"
    assert row["storageMode"] == "copy"
    assert "Vehicles" in row["filePath"]


def test_commit_placement_file_moves_non_model_without_db_row(client, tmp_path):
    from app.db import get_db_conn
    from app.services.import_wizard import commit_placement_file

    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "Vehicles", None))
    conn.commit()
    conn.close()

    source = tmp_path / "photo.jpg"
    source.write_bytes(b"fake jpg")

    result = commit_placement_file(source, "f1")

    assert result["status"] == "ok"
    assert result["isModel"] is False
    assert not source.exists()

    conn = get_db_conn()
    count = conn.execute("SELECT COUNT(*) as c FROM models").fetchone()["c"]
    conn.close()
    assert count == 0


def test_commit_placement_file_reports_error_without_raising(client, tmp_path):
    from app.services.import_wizard import commit_placement_file

    missing = tmp_path / "gone.stl"  # never created

    result = commit_placement_file(missing, "1")

    assert result["status"] == "error"
    assert "error" in result


def test_commit_endpoint_processes_batch_and_isolates_failures(client, tmp_path):
    conn_setup = [
        ("f1", "Vehicles", None),
    ]
    from app.db import get_db_conn
    conn = get_db_conn()
    for row in conn_setup:
        conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", row)
    conn.commit()
    conn.close()

    good = tmp_path / "good.stl"
    good.write_bytes(b"solid endsolid")
    missing = tmp_path / "missing.stl"  # never created, will fail

    resp = client.post("/api/import/commit", json={
        "placements": [
            {"sourcePath": str(good), "isFolder": False, "targetFolderId": "f1"},
            {"sourcePath": str(missing), "isFolder": False, "targetFolderId": "f1"},
        ]
    })

    assert resp.status_code == 200
    results = resp.json()["results"]
    statuses = {r["sourcePath"]: r["status"] for r in results}
    assert statuses[str(good)] == "ok"
    assert statuses[str(missing)] == "error"


def test_commit_endpoint_groups_folder_results_under_placement_source(client, tmp_path):
    from app.db import get_db_conn
    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "Vehicles", None))
    conn.commit()
    conn.close()

    kit = tmp_path / "Tank Kit"
    kit.mkdir()
    (kit / "hull.stl").write_bytes(b"solid endsolid")
    (kit / "turret.stl").write_bytes(b"solid endsolid")

    resp = client.post("/api/import/commit", json={
        "placements": [
            {"sourcePath": str(kit), "isFolder": True, "targetFolderId": "f1"},
        ]
    })

    results = resp.json()["results"]
    assert len(results) == 2
    assert all(r["placementSourcePath"] == str(kit) for r in results)
    assert all(r["status"] == "ok" for r in results)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_import_wizard.py -v`
Expected: FAIL — `ImportError: cannot import name 'sanitize_path_segment'` and 404 on `/api/import/commit`.

- [ ] **Step 3: Add the sanitization, resolution, and commit logic to `backend/app/services/import_wizard.py`**

Add near the top of the file (after the existing imports):

```python
import os
import re
import shutil

from app.db import get_db_conn, UPLOAD_DIR
from app.services.ingestion import ingest_file
```

Append these functions:

```python
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_path_segment(name: str) -> str:
    """Make a folder name safe to use as one segment of a real filesystem
    path. Folder names are free-text and user-renameable, but this is the
    first feature that turns them into actual on-disk directories -- a
    name with illegal characters, a reserved Windows device name, or
    trailing dots/spaces must not corrupt or misdirect the destination path.
    """
    cleaned = _ILLEGAL_CHARS.sub("_", name).strip(" .")
    if not cleaned:
        cleaned = "_"
    if cleaned.upper() in _RESERVED_NAMES:
        cleaned = f"{cleaned}_"
    return cleaned[:100]


def folder_disk_path(folder_id: str) -> str:
    """Full logical path for a folder, root-to-leaf, each segment
    sanitized -- e.g. "Tanks" under "Vehicles" returns "Vehicles/Tanks".
    Used as ingest_file's dest_subpath so wizard-imported files land in
    real subdirectories mirroring the logical folder the user placed
    them in.
    """
    conn = get_db_conn()
    segments = []
    current_id = folder_id
    while current_id is not None:
        row = conn.execute(
            "SELECT name, parentId FROM folders WHERE id=?", (current_id,)
        ).fetchone()
        if row is None:
            break
        segments.append(sanitize_path_segment(row["name"]))
        current_id = row["parentId"]
    conn.close()
    return os.path.join(*reversed(segments)) if segments else ""


def expand_placement(source_path: str, is_folder: bool) -> list:
    """A loose-file placement is itself; a folder placement is every file
    found by walking it recursively -- this is what makes dragging one
    folder bring every file inside it along, without the user having to
    select each file individually.
    """
    if not is_folder:
        return [Path(source_path)]
    found = []
    for dirpath, _dirnames, filenames in os.walk(source_path):
        for fname in filenames:
            found.append(Path(dirpath) / fname)
    return found


def commit_placement_file(file_path: Path, target_folder_id: str) -> dict:
    """Move one file into its resolved destination, independently of any
    other file in the batch -- a failure here (locked file, permission
    denied, vanished since staging) must never prevent a sibling file, in
    this or any other placement, from being moved.
    """
    is_model = file_path.suffix.lower() in SUPPORTED_EXTENSIONS
    try:
        dest_subpath = folder_disk_path(target_folder_id)
        if is_model:
            ingest_file(
                str(file_path),
                folder_id=target_folder_id,
                original_filename=file_path.name,
                move=True,
                dest_subpath=dest_subpath,
            )
        else:
            dest_dir = Path(UPLOAD_DIR) / dest_subpath
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / file_path.name
            counter = 1
            while dest_file.exists():
                dest_file = dest_dir / f"{file_path.stem}_{counter}{file_path.suffix}"
                counter += 1
            shutil.move(str(file_path), str(dest_file))
        return {"sourcePath": str(file_path), "status": "ok", "isModel": is_model}
    except Exception as exc:
        return {"sourcePath": str(file_path), "status": "error", "error": str(exc), "isModel": is_model}
```

- [ ] **Step 4: Add the commit endpoint to `backend/app/routers/import_wizard.py`**

Add these imports at the top:

```python
from typing import List

from pydantic import BaseModel

from app.services.import_wizard import expand_placement, commit_placement_file
```

Append below the existing `get_import_tree` route:

```python
class Placement(BaseModel):
    sourcePath: str
    isFolder: bool
    targetFolderId: str


class CommitRequest(BaseModel):
    placements: List[Placement]


@router.post("/commit")
def commit_import(body: CommitRequest):
    results = []
    for placement in body.placements:
        files = expand_placement(placement.sourcePath, placement.isFolder)
        for file_path in files:
            result = commit_placement_file(file_path, placement.targetFolderId)
            result["placementSourcePath"] = placement.sourcePath
            results.append(result)
    return {"results": results}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_import_wizard.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Run the full backend suite to confirm no regressions**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/import_wizard.py backend/app/routers/import_wizard.py backend/tests/test_import_wizard.py
git commit -m "feat: add import commit endpoint with per-file isolation and path sanitization"
```

---

### Task 5: Frontend API client + types for the wizard

**Files:**
- Modify: `frontend/types.ts` — add `ImportTreeNode`, `ImportPlacement`, `ImportResult`
- Modify: `frontend/services/api.ts` — add `getImportTree`, `commitImport`

**Interfaces:**
- Consumes: `GET /api/import/tree` and `POST /api/import/commit` (Tasks 3-4).
- Produces: `api.getImportTree(path: string): Promise<ImportTreeNode>`, `api.commitImport(placements: ImportPlacement[]): Promise<ImportResult[]>`. Task 6 (ImportWizard component) is the consumer.

- [ ] **Step 1: Add types to `frontend/types.ts`**

```typescript
export interface ImportTreeNode {
  name: string;
  path: string;
  folders: ImportTreeNode[];
  files: { name: string; path: string; isModel: boolean; size: number }[];
}

// Sentinel folder id for File-mode's synthetic bucket holding models whose
// filePath has no real subdirectory structure to group by (pre-feature flat
// copy-mode uploads). Shared between Sidebar.tsx (Task 9, builds this node)
// and App.tsx (Task 10, filters by it) so the two never drift apart.
export const FILE_VIEW_UPLOADS_BUCKET_ID = "__uploads__";

export interface ImportPlacement {
  sourcePath: string;
  isFolder: boolean;
  targetFolderId: string;
}

export interface ImportResult {
  sourcePath: string;
  placementSourcePath: string;
  status: "ok" | "error";
  error?: string;
  isModel: boolean;
}
```

- [ ] **Step 2: Add API functions to `frontend/services/api.ts`**

Add near the other folder/model functions, following the existing pattern:

```typescript
  // Import Wizard: read-only tree peek
  getImportTree: async (path: string): Promise<ImportTreeNode> => {
    const res = await fetch(
      `${getApiBaseUrl()}/import/tree?path=${encodeURIComponent(path)}`,
    );
    if (!res.ok) throw new Error("Failed to read directory");
    return res.json();
  },

  // Import Wizard: commit staged placements
  commitImport: async (placements: ImportPlacement[]): Promise<ImportResult[]> => {
    const res = await fetch(`${getApiBaseUrl()}/import/commit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ placements }),
    });
    if (!res.ok) throw new Error("Import commit failed");
    const body = await res.json();
    return body.results;
  },
```

Add the `ImportTreeNode`, `ImportPlacement`, `ImportResult` names to the existing `import { ... } from "../types"` line at the top of `api.ts`.

- [ ] **Step 3: Type-check**

Run: `cd frontend && bunx tsc --noEmit`
Expected: no new errors beyond the pre-existing baseline (`Sidebar.tsx`, `Viewer3D.tsx`, `index.tsx`, `thumbnailGenerator.ts` — confirm by comparing the error list against `git stash; bunx tsc --noEmit; git stash pop`, the baseline established earlier this session).

- [ ] **Step 4: Commit**

```bash
git add frontend/types.ts frontend/services/api.ts
git commit -m "feat: add frontend API client for import wizard endpoints"
```

---

### Task 6: `ImportWizard` component — staging UI

**Files:**
- Create: `frontend/components/ImportWizard.tsx`

**Interfaces:**
- Consumes: `api.getImportTree`, `api.createFolder` (existing), `api.getFolders` (existing) from Task 5 and existing `api.ts`; `Folder`, `ImportTreeNode`, `ImportPlacement` types.
- Produces: `<ImportWizard rootPath={string} folders={Folder[]} onClose={() => void} onComplete={() => void} />`. Task 7 (WatcherInbox entry point) renders this. Internally stages `ImportPlacement[]` in component state, handed to Task 7's review/commit UI (added to this same file).

- [ ] **Step 1: Create the component with tree fetch and two-pane rendering**

```tsx
import React, { useEffect, useState } from "react";
import { Folder as FolderIcon, File as FileIcon, X, Plus } from "lucide-react";
import { api } from "../services/api";
import { Folder, ImportTreeNode, ImportPlacement } from "../types";

interface ImportWizardProps {
  rootPath: string;
  folders: Folder[];
  onClose: () => void;
  onComplete: () => void;
}

interface StagedPlacement extends ImportPlacement {
  sourceLabel: string; // display name shown in the review step
  targetLabel: string;
}

const ImportWizard: React.FC<ImportWizardProps> = ({
  rootPath,
  folders: initialFolders,
  onClose,
  onComplete,
}) => {
  const [tree, setTree] = useState<ImportTreeNode | null>(null);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [folders, setFolders] = useState<Folder[]>(initialFolders);
  const [placements, setPlacements] = useState<StagedPlacement[]>([]);
  const [creatingUnderId, setCreatingUnderId] = useState<string | null>(null);
  const [newFolderName, setNewFolderName] = useState("");

  useEffect(() => {
    api
      .getImportTree(rootPath)
      .then(setTree)
      .catch((e) => setTreeError(e.message || "Failed to read directory"));
  }, [rootPath]);

  const folderLabel = (id: string): string => {
    const chain: string[] = [];
    let current = folders.find((f) => f.id === id);
    while (current) {
      chain.unshift(current.name);
      current = current.parentId
        ? folders.find((f) => f.id === current!.parentId)
        : undefined;
    }
    return chain.join(" / ") || id;
  };

  const stagePlacement = (sourcePath: string, isFolder: boolean, sourceLabel: string, targetFolderId: string) => {
    setPlacements((prev) => [
      ...prev.filter((p) => p.sourcePath !== sourcePath),
      { sourcePath, isFolder, targetFolderId, sourceLabel, targetLabel: folderLabel(targetFolderId) },
    ]);
  };

  const handleCreateFolder = async (parentId: string | null) => {
    if (!newFolderName.trim()) return;
    const created = await api.createFolder(newFolderName.trim(), parentId);
    setFolders((prev) => [...prev, created]);
    setNewFolderName("");
    setCreatingUnderId(null);
  };

  const rootFolders = folders.filter((f) => f.parentId === null);
  const childFolders = (parentId: string) => folders.filter((f) => f.parentId === parentId);

  const renderRawNode = (node: ImportTreeNode, isFolder: boolean) => (
    <div
      key={node.path}
      draggable
      onDragStart={(e) => e.dataTransfer.setData("text/plain", JSON.stringify({ path: node.path, isFolder }))}
      className="flex items-center gap-2 px-2 py-1 rounded cursor-grab hover:bg-vault-800"
    >
      {isFolder ? <FolderIcon className="w-4 h-4 text-blue-400" /> : <FileIcon className="w-4 h-4 text-slate-400" />}
      <span className="text-sm truncate">{node.name}</span>
      {placements.some((p) => p.sourcePath === node.path) && (
        <span className="text-xs text-green-400 ml-auto">
          → {placements.find((p) => p.sourcePath === node.path)?.targetLabel}
        </span>
      )}
    </div>
  );

  const renderLogicalNode = (folder: Folder) => (
    <div
      key={folder.id}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const data = e.dataTransfer.getData("text/plain");
        if (!data) return;
        const { path, isFolder } = JSON.parse(data);
        const label = path.split(/[\\/]/).pop() || path;
        stagePlacement(path, isFolder, label, folder.id);
      }}
      className="pl-2 py-1 rounded hover:bg-vault-800 border border-dashed border-vault-700"
    >
      <div className="flex items-center gap-2">
        <FolderIcon className="w-4 h-4 text-blue-400" />
        <span className="text-sm">{folder.name}</span>
        <button
          className="ml-auto text-xs text-slate-400 hover:text-white"
          onClick={() => setCreatingUnderId(folder.id)}
        >
          <Plus className="w-3 h-3" />
        </button>
      </div>
      {creatingUnderId === folder.id && (
        <div className="flex gap-1 pl-6 py-1">
          <input
            autoFocus
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreateFolder(folder.id)}
            className="bg-vault-900 border border-vault-700 rounded px-1 text-sm"
            placeholder="New folder name"
          />
        </div>
      )}
      <div className="pl-4">{childFolders(folder.id).map(renderLogicalNode)}</div>
    </div>
  );

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center">
      <div className="bg-vault-900 border border-vault-700 rounded-xl w-[90vw] h-[85vh] flex flex-col p-4">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-lg font-semibold text-white">Import from folder</h2>
          <button onClick={onClose} aria-label="Close"><X className="w-5 h-5" /></button>
        </div>
        {treeError && <p className="text-red-400 text-sm mb-2">{treeError}</p>}
        <div className="flex-1 flex gap-4 overflow-hidden">
          <div className="flex-1 overflow-y-auto border border-vault-700 rounded p-2">
            <p className="text-xs text-slate-500 mb-2">On disk: {rootPath}</p>
            {tree && (
              <>
                {tree.folders.map((f) => renderRawNode(f, true))}
                {tree.files.map((f) => renderRawNode(f, false))}
              </>
            )}
          </div>
          <div className="flex-1 overflow-y-auto border border-vault-700 rounded p-2">
            <p className="text-xs text-slate-500 mb-2">Your library</p>
            {rootFolders.map(renderLogicalNode)}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ImportWizard;
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && bunx tsc --noEmit`
Expected: no new errors beyond the established baseline.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/ImportWizard.tsx
git commit -m "feat: add ImportWizard two-pane staging UI"
```

---

### Task 7: `ImportWizard` — review, commit, results, retry

**Files:**
- Modify: `frontend/components/ImportWizard.tsx`

**Interfaces:**
- Consumes: `api.commitImport` (Task 5), `placements` state (Task 6).
- Produces: a `"review" | "results"` step added to the wizard's existing implicit "staging" step, with a `handleCommit`/`handleRetry` flow. `onComplete` (from Task 6's props) fires once after a successful commit so the caller (Task 8's WatcherInbox integration) can refresh its own folder/model lists.

- [ ] **Step 1: Add step state and review/results rendering**

In `frontend/components/ImportWizard.tsx`, add to the component's state (alongside the existing `useState` calls):

```tsx
  const [step, setStep] = useState<"stage" | "review" | "results">("stage");
  const [results, setResults] = useState<{ sourcePath: string; placementSourcePath: string; status: "ok" | "error"; error?: string; isModel: boolean }[]>([]);
  const [committing, setCommitting] = useState(false);
```

Add the commit/retry handlers (alongside `handleCreateFolder`):

```tsx
  const runCommit = async (toCommit: StagedPlacement[]) => {
    setCommitting(true);
    try {
      const newResults = await api.commitImport(
        toCommit.map(({ sourcePath, isFolder, targetFolderId }) => ({ sourcePath, isFolder, targetFolderId })),
      );
      setResults((prev) => [
        ...prev.filter((r) => !toCommit.some((p) => p.sourcePath === r.placementSourcePath)),
        ...newResults,
      ]);
      setStep("results");
      if (newResults.every((r) => r.status === "ok")) {
        onComplete();
      }
    } finally {
      setCommitting(false);
    }
  };

  const handleConfirm = () => runCommit(placements);

  const handleRetryFailed = () => {
    const failedSourcePaths = new Set(
      results.filter((r) => r.status === "error").map((r) => r.placementSourcePath),
    );
    const toRetry = placements.filter((p) => failedSourcePaths.has(p.sourcePath));
    runCommit(toRetry);
  };
```

Replace the component's final `return (...)` block's body to branch on `step`, keeping the existing "stage" JSX under the `step === "stage"` branch and adding review/results below it:

```tsx
  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center">
      <div className="bg-vault-900 border border-vault-700 rounded-xl w-[90vw] h-[85vh] flex flex-col p-4">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-lg font-semibold text-white">Import from folder</h2>
          <button onClick={onClose} aria-label="Close"><X className="w-5 h-5" /></button>
        </div>

        {step === "stage" && (
          <>
            {treeError && <p className="text-red-400 text-sm mb-2">{treeError}</p>}
            <div className="flex-1 flex gap-4 overflow-hidden">
              <div className="flex-1 overflow-y-auto border border-vault-700 rounded p-2">
                <p className="text-xs text-slate-500 mb-2">On disk: {rootPath}</p>
                {tree && (
                  <>
                    {tree.folders.map((f) => renderRawNode(f, true))}
                    {tree.files.map((f) => renderRawNode(f, false))}
                  </>
                )}
              </div>
              <div className="flex-1 overflow-y-auto border border-vault-700 rounded p-2">
                <p className="text-xs text-slate-500 mb-2">Your library</p>
                {rootFolders.map(renderLogicalNode)}
              </div>
            </div>
            <div className="flex justify-end mt-4">
              <button
                disabled={placements.length === 0}
                onClick={() => setStep("review")}
                className="bg-blue-600 disabled:bg-vault-700 disabled:text-slate-500 text-white px-4 py-2 rounded"
              >
                Review ({placements.length})
              </button>
            </div>
          </>
        )}

        {step === "review" && (
          <>
            <div className="flex-1 overflow-y-auto">
              {placements.map((p) => (
                <div key={p.sourcePath} className="flex justify-between px-2 py-1 border-b border-vault-800 text-sm">
                  <span>{p.sourceLabel}</span>
                  <span className="text-slate-500">→ {p.targetLabel}</span>
                </div>
              ))}
            </div>
            <div className="flex justify-between mt-4">
              <button onClick={() => setStep("stage")} className="text-slate-400 px-4 py-2">Back</button>
              <button
                disabled={committing}
                onClick={handleConfirm}
                className="bg-blue-600 disabled:opacity-50 text-white px-4 py-2 rounded"
              >
                {committing ? "Moving files..." : "Confirm"}
              </button>
            </div>
          </>
        )}

        {step === "results" && (
          <>
            <div className="flex-1 overflow-y-auto">
              {placements.map((p) => {
                const rowsForPlacement = results.filter((r) => r.placementSourcePath === p.sourcePath);
                const failCount = rowsForPlacement.filter((r) => r.status === "error").length;
                return (
                  <div key={p.sourcePath} className="px-2 py-1 border-b border-vault-800 text-sm">
                    <div className="flex justify-between">
                      <span>{p.sourceLabel}</span>
                      <span className={failCount > 0 ? "text-amber-400" : "text-green-400"}>
                        {failCount > 0 ? `${failCount} failed` : "Done"}
                      </span>
                    </div>
                    {rowsForPlacement.filter((r) => r.status === "error").map((r) => (
                      <p key={r.sourcePath} className="text-xs text-red-400 pl-2">{r.sourcePath}: {r.error}</p>
                    ))}
                  </div>
                );
              })}
            </div>
            <div className="flex justify-between mt-4">
              <button onClick={onClose} className="text-slate-400 px-4 py-2">Close</button>
              {results.some((r) => r.status === "error") && (
                <button
                  disabled={committing}
                  onClick={handleRetryFailed}
                  className="bg-amber-600 disabled:opacity-50 text-white px-4 py-2 rounded"
                >
                  {committing ? "Retrying..." : "Retry failed items"}
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && bunx tsc --noEmit`
Expected: no new errors beyond the established baseline.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/ImportWizard.tsx
git commit -m "feat: add review, commit, and retry steps to ImportWizard"
```

---

### Task 8: Entry point in `WatcherInbox.tsx`

**Files:**
- Modify: `frontend/components/WatcherInbox.tsx`

**Interfaces:**
- Consumes: `ImportWizard` (Task 7), `api.browseFolder` (existing, already used elsewhere in this same file).
- Produces: an "Import from folder..." button that opens the native folder picker, then renders `<ImportWizard>` on a chosen path. No new exports — this is a leaf UI wiring change.

- [ ] **Step 1: Add wizard state and entry point button**

In `frontend/components/WatcherInbox.tsx`, add the import (alongside existing imports):

```tsx
import ImportWizard from "./ImportWizard";
```

Add state (alongside the existing `useState` calls, near `browsing`/`browseNote`):

```tsx
  const [importRootPath, setImportRootPath] = useState<string | null>(null);
  const [importBrowsing, setImportBrowsing] = useState(false);
```

Add the handler (alongside `handleBrowseFolder`):

```tsx
  const handleStartImport = async () => {
    setImportBrowsing(true);
    try {
      const { path } = await api.browseFolder();
      if (path) setImportRootPath(path);
    } catch (err: any) {
      setError(err.message || "Folder browser unavailable");
    } finally {
      setImportBrowsing(false);
    }
  };
```

Add a button near the top of the component's return JSX (in the same section as the watch-folder management UI, before the watch-folders list):

```tsx
        <button
          onClick={handleStartImport}
          disabled={importBrowsing}
          className="flex items-center gap-2 px-3 py-2 bg-vault-800 hover:bg-vault-700 rounded text-sm text-slate-200 disabled:opacity-50"
        >
          <FolderInput className="w-4 h-4" />
          {importBrowsing ? "..." : "Import from folder..."}
        </button>
```

At the end of the component's return, alongside the closing tags, add the conditional wizard render:

```tsx
      {importRootPath && (
        <ImportWizard
          rootPath={importRootPath}
          folders={folders}
          onClose={() => setImportRootPath(null)}
          onComplete={refresh}
        />
      )}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && bunx tsc --noEmit`
Expected: no new errors beyond the established baseline.

- [ ] **Step 3: Build**

Run: `cd frontend && bun run build`
Expected: succeeds (same pre-existing chunk-size warning as every prior build this session — not a new issue).

- [ ] **Step 4: Manual verification**

Following this session's established pattern (rebuild the packaged app via `desktop/build.ps1`, uninstall the old install, install the fresh build, SHA-256 hash-verify the installed `.exe` matches the fresh build) — then manually exercise the wizard against a fixture directory with nested subfolders, mixed file types (model + non-model), and confirm:
- Raw tree renders correctly.
- Dragging a folder and a loose file both stage correctly.
- Creating a new logical folder mid-wizard works and appears as a valid drop target.
- Review step shows accurate source → destination summary.
- Confirm physically moves the files (verify via file explorer or `dir`/`ls` that the source is empty and the library's `UPLOAD_DIR` has the new subdirectory structure).
- The imported models appear in the main grid under the correct logical folder, un-thumbnailed (matching the spec).
- Deliberately induce one failure (e.g. stage a file, then delete it from disk before confirming) and verify the results screen reports it without blocking the rest of the batch, and that "Retry failed items" works once the issue is fixed.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/WatcherInbox.tsx
git commit -m "feat: add Import from folder entry point to watcher settings"
```

---

### Task 9: Sidebar Logical/File toggle

**Files:**
- Modify: `frontend/components/Sidebar.tsx`

**Interfaces:**
- Consumes: `STLModel.filePath` (Task 1).
- Produces: `viewMode: "logical" | "file"` local state in `Sidebar`, plus a new prop `onViewModeChange: (mode: "logical" | "file") => void` so `App.tsx` (Task 10) can filter the main grid consistently with whatever the sidebar shows. Also produces a derived file-mode tree structure consumed only within this component.

- [ ] **Step 1: Add the toggle and file-mode tree derivation**

In `frontend/components/Sidebar.tsx`, add the import (alongside the existing `import { Folder, STLModel, StorageStats } from "../types";`):

```tsx
import { FILE_VIEW_UPLOADS_BUCKET_ID } from "../types";
```

Add to `SidebarProps` (alongside `variant`):

```tsx
  onViewModeChange: (mode: "logical" | "file") => void;
```

Destructure it in the component's props list alongside `variant = "desktop"`.

Add state near the other `useState` calls:

```tsx
  const [viewMode, setViewMode] = useState<"logical" | "file">("logical");
```

Add a derived file-mode tree, alongside the existing `treefolders` function:

```tsx
  // Groups every model by its filePath's directory instead of folderId.
  // Models with no filePath (shouldn't normally happen post-migration, but
  // defensive) or whose filePath sits directly in the flat pre-feature
  // upload location (no real subdirectory under it) land in a single
  // synthetic "Uploads" bucket rather than fabricating structure that was
  // never there -- see the spec's Non-goals.
  const fileTree = useMemo(() => {
    type FileNode = { id: string; label: string; children: FileNode[]; childMap: Record<string, FileNode> };
    const root: FileNode = { id: "__root__", label: "", children: [], childMap: {} };

    models.forEach((m) => {
      if (!m.filePath) return;
      const normalized = m.filePath.replace(/\\/g, "/");
      const segments = normalized.split("/").slice(0, -1); // drop filename
      // Segments ending in the flat upload directory itself (no real
      // subdirectory) collapse to the Uploads bucket.
      const uploadDirIndex = segments.findIndex((s) => s.toLowerCase() === "uploads");
      const meaningfulSegments = uploadDirIndex >= 0 ? segments.slice(uploadDirIndex + 1) : segments;

      if (meaningfulSegments.length === 0) {
        if (!root.childMap[FILE_VIEW_UPLOADS_BUCKET_ID]) {
          const node: FileNode = { id: FILE_VIEW_UPLOADS_BUCKET_ID, label: "Uploads", children: [], childMap: {} };
          root.childMap[FILE_VIEW_UPLOADS_BUCKET_ID] = node;
          root.children.push(node);
        }
        return;
      }

      let cursor = root;
      let idPath = "file";
      meaningfulSegments.forEach((segment) => {
        idPath += `/${segment}`;
        if (!cursor.childMap[segment]) {
          const node: FileNode = { id: idPath, label: segment, children: [], childMap: {} };
          cursor.childMap[segment] = node;
          cursor.children.push(node);
        }
        cursor = cursor.childMap[segment];
      });
    });

    const strip = (node: FileNode): TreeViewDefaultItemModelProperties => ({
      id: node.id,
      label: node.label,
      children: node.children.map(strip),
    });
    return root.children.map(strip);
  }, [models]);
```

Add the toggle UI right above the `RichTreeView` (inside the existing `<div className="space-y-1 pb-4 ">` wrapper), and swap which tree/props the `RichTreeView` uses:

```tsx
        <div className="space-y-1 pb-4 ">
          <div className="flex gap-1 px-1 mb-2">
            <button
              onClick={() => {
                setViewMode("logical");
                onViewModeChange("logical");
              }}
              className={`flex-1 text-xs py-1 rounded ${viewMode === "logical" ? "bg-blue-600 text-white" : "bg-vault-800 text-slate-400"}`}
            >
              Logical
            </button>
            <button
              onClick={() => {
                setViewMode("file");
                onViewModeChange("file");
              }}
              className={`flex-1 text-xs py-1 rounded ${viewMode === "file" ? "bg-blue-600 text-white" : "bg-vault-800 text-slate-400"}`}
            >
              File
            </button>
          </div>
          {viewMode === "logical" ? (
            <RichTreeView
              items={treefolders()}
              slots={{ item: CustomTreeItem }}
              expansionTrigger="iconContainer"
              expandedItems={Array.from(expandedIds)}
              onItemExpansionToggle={handleItemExpansionToggle}
              isItemEditable
              onItemLabelChange={(itemId, label) => onRenameFolder(itemId, label)}
            />
          ) : (
            <RichTreeView
              items={fileTree}
              onSelectedItemsChange={(_e, itemId) => {
                if (itemId) onSelectFolder(itemId as string);
              }}
            />
          )}
        </div>
```

Note: the file-mode `RichTreeView` intentionally omits `slots={{ item: CustomTreeItem }}`, `isItemEditable`, and any drag handlers — File mode has no backing `folders` rows, so create/rename/delete/drag-to-move are correctly unavailable, per the spec.

- [ ] **Step 2: Type-check**

Run: `cd frontend && bunx tsc --noEmit`
Expected: no new errors beyond the established baseline.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/Sidebar.tsx
git commit -m "feat: add Logical/File toggle to sidebar, deriving tree from filePath"
```

---

### Task 10: App.tsx file-mode filtering

**Files:**
- Modify: `frontend/App.tsx:139-152` (`filteredModels`), and wherever `<Sidebar>` is rendered (pass `onViewModeChange`)

**Interfaces:**
- Consumes: `onViewModeChange` prop (Task 9).
- Produces: `filteredModels` becomes mode-aware — in file mode, `currentFolderId` is interpreted as a `filePath` prefix (or the `__uploads__` sentinel) instead of a `folderId` equality check.

- [ ] **Step 1: Add view-mode state and update filtering**

In `frontend/App.tsx`, add `FILE_VIEW_UPLOADS_BUCKET_ID` to the existing `import { STLModel, Folder, StorageStats, STLModelCollection } from "./types";` line.

Add state near the other `useState` calls (alongside `currentFolderId`):

```tsx
  const [viewMode, setViewMode] = useState<"logical" | "file">("logical");
```

Replace the `filteredModels` memo (currently at `App.tsx:139-152`):

```tsx
  const filteredModels = useMemo(() => {
    if (viewMode === "file") {
      if (currentFolderId === "all") return models;
      if (currentFolderId === FILE_VIEW_UPLOADS_BUCKET_ID) {
        return models.filter((m) => {
          if (!m.filePath) return false;
          const normalized = m.filePath.replace(/\\/g, "/");
          const segments = normalized.split("/").slice(0, -1);
          return segments.length === 0 || segments[segments.length - 1].toLowerCase() === "uploads";
        });
      }
      // fileTree node ids are built in Sidebar.tsx as "file/<segment>/<segment>/..."
      // (see Task 9) -- strip that prefix to get the real path segments this
      // node represents, then match any model whose filePath passes through
      // that directory.
      const prefix = currentFolderId.replace(/^file\//, "") + "/";
      return models.filter((m) => {
        if (!m.filePath) return false;
        const normalized = m.filePath.replace(/\\/g, "/");
        return normalized.includes(`/${prefix}`);
      });
    }
    return currentFolderId === "all"
      ? models
      : models.filter((m) => m.folderId === currentFolderId);
  }, [models, currentFolderId, viewMode]);
```

`filteredFolders` (the subfolder tiles shown in `ModelList`) stays logical-only
— in file mode, the sidebar itself is the only navigation surface (per the
spec, File mode has no backing folder entity to show as a tile). Find the
`<ModelList>` element's existing `folders={filteredFolders}` prop and change
it to:

```tsx
                  folders={viewMode === "file" ? [] : filteredFolders}
```

Find the `<Sidebar>` element (rendered where `currentFolderId`/`onSelectFolder` are passed) and add:

```tsx
                onViewModeChange={(mode) => {
                  setViewMode(mode);
                  setCurrentFolderId("all"); // avoid a stale id from one mode being misread as the other mode's id
                }}
```

(Resetting to `"all"` on every mode switch avoids a stale `currentFolderId` from one mode being misinterpreted as a folder-id in the other.)

- [ ] **Step 2: Type-check**

Run: `cd frontend && bunx tsc --noEmit`
Expected: no new errors beyond the established baseline.

- [ ] **Step 3: Build**

Run: `cd frontend && bun run build`
Expected: succeeds.

- [ ] **Step 4: Manual verification**

Following this session's established pattern (rebuild, reinstall, hash-verify), manually verify:
- With some models moved logically (drag to a different folder in Logical mode, which never touches `filePath`) but not physically, switching to File mode shows those models grouped by their *original* real location, not their new logical folder — directly demonstrating the drift the toggle exists to reveal.
- Models imported via the wizard (Task 8) show correctly grouped by their real subdirectory in File mode.
- Pre-existing flat copy-mode uploads (from before this feature) appear under the "Uploads" bucket.
- File mode shows no create/rename/delete/drag-drop affordances.
- Switching back to Logical mode restores today's exact original behavior.

- [ ] **Step 5: Commit**

```bash
git add frontend/App.tsx
git commit -m "feat: wire Logical/File toggle into main grid filtering"
```

---

## Self-Review Notes

- **Spec coverage:** `filePath` column + backfill (Task 1), `dest_subpath`/`ingest_file` reuse (Task 2), tree-peek (Task 3), commit with per-file isolation + sanitization (Task 4), frontend API client (Task 5), wizard staging UI (Task 6), review/commit/retry (Task 7), entry point (Task 8), sidebar toggle (Task 9), grid filtering (Task 10) — every section of the corrected spec maps to a task.
- **Type consistency:** `ImportPlacement`/`ImportResult`/`ImportTreeNode` (Task 5) match the backend's `Placement`/`CommitRequest` Pydantic models (Task 4) and the JSON shapes `build_tree`/`commit_import` actually return (Task 3-4) field-for-field.
- **No placeholders:** every step has literal code; no "TBD" or "add error handling" left unfollowed by an actual implementation.
