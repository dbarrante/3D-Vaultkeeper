# Watch-Folder Structure Mirroring + Master-Item Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a watched folder has subdirectories (e.g. one folder per print, each with several part files), the scanner mirrors that structure into library subfolders instead of flattening every file into one target folder — and a folder that directly contains files displays as a "master item" card (thumbnail + part count) instead of a plain folder icon.

**Architecture:** `backend/app/services/scan.py`'s `scan_watch_folder` gains a `get_or_create_folder` helper and, for each file found, walks/creates a matching chain of library folders mirroring the file's on-disk subdirectory path before ingesting. `frontend/App.tsx` gains a `folderPreviews` derived value (direct-child count + representative thumbnail per folder, same shape as `Sidebar.tsx`'s existing `folderCounts`), passed to `ModelList.tsx`, whose existing folder-tile rendering shows that preview instead of a plain folder icon when a folder has files directly inside.

**Tech Stack:** Existing FastAPI/SQLite backend (`sqlite3`, `pathlib`), existing React/TypeScript frontend (MUI components already in use in `ModelList.tsx`). No new dependencies.

## Global Constraints

- No changes to `scan_downloads_folder` or the Inbox flow — this plan only touches `scan_watch_folder`.
- Going-forward only: no migration of already-ingested, already-flat library data. The existing `sourcePath` dedup check already prevents re-ingesting files a prior scan already found.
- No new backend data model or table. A folder is the master item when it has files directly inside — no separate "master item" concept.
- All 6 existing tests in `backend/tests/test_scan.py` must keep passing unchanged — this is an additive behavior change to `scan_watch_folder`, not a rewrite.
- No frontend automated test suite exists in this project (`frontend/package.json` has no test script) — frontend tasks are verified via `bunx tsc --noEmit`, `bun run build`, and a manual checklist against the packaged desktop build, matching how every other frontend change this session was verified.

---

### Task 1: `get_or_create_folder` helper

**Files:**
- Modify: `backend/app/services/scan.py`
- Test: `backend/tests/test_scan.py`

**Interfaces:**
- Produces: `get_or_create_folder(name: str, parent_id: Optional[str]) -> str` — looks up a folder by exact `(name, parentId)` match; returns its `id` if found, otherwise inserts a new folder row (same columns/shape as `backend/app/routers/folders.py`'s `create_folder`: `id` a fresh `uuid.uuid4()` string, `name`, `parentId`) and returns the new `id`. `parent_id` may be `None` for a top-level folder — the lookup must treat `None` as matching SQL `NULL`, not as never-matching.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_scan.py`:

```python
def test_get_or_create_folder_creates_new_folder(client):
    from app.services.scan import get_or_create_folder
    from app.db import get_db_conn

    folder_id = get_or_create_folder("PrintA", None)

    conn = get_db_conn()
    row = conn.execute(
        "SELECT name, parentId FROM folders WHERE id=?", (folder_id,)
    ).fetchone()
    conn.close()
    assert row["name"] == "PrintA"
    assert row["parentId"] is None


def test_get_or_create_folder_reuses_existing_folder(client):
    from app.services.scan import get_or_create_folder
    from app.db import get_db_conn

    first_id = get_or_create_folder("PrintA", "1")
    second_id = get_or_create_folder("PrintA", "1")

    assert first_id == second_id
    conn = get_db_conn()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM folders WHERE name=? AND parentId=?",
        ("PrintA", "1"),
    ).fetchone()["c"]
    conn.close()
    assert count == 1


def test_get_or_create_folder_distinguishes_by_parent(client):
    """Same name under a different parent (or no parent) is a different
    folder — never merged into one."""
    from app.services.scan import get_or_create_folder

    under_none = get_or_create_folder("PrintA", None)
    under_1 = get_or_create_folder("PrintA", "1")

    assert under_none != under_1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_scan.py -k get_or_create_folder -v`
Expected: FAIL with `ImportError: cannot import name 'get_or_create_folder'`

- [ ] **Step 3: Implement the helper**

In `backend/app/services/scan.py`, add (near the top, after the existing imports — `uuid` is already imported by this file for `scan_downloads_folder`, no new import needed):

```python
def get_or_create_folder(name: str, parent_id: Optional[str]) -> str:
    """Looks up a folder by exact (name, parentId) match before creating
    one — idempotent so re-scans of the same watched subdirectory, and
    any manually-created folder that happens to share a name, never
    produce duplicates. `IS` (not `=`) is required for the parentId
    comparison: SQLite's `IS` is NULL-safe, matching a bound NULL
    parameter correctly, where plain `=` never matches NULL at all.
    """
    conn = get_db_conn()
    row = conn.execute(
        "SELECT id FROM folders WHERE name=? AND parentId IS ?",
        (name, parent_id),
    ).fetchone()
    if row:
        conn.close()
        return row["id"]

    folder_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO folders(id,name,parentId) VALUES (?,?,?)",
        (folder_id, name, parent_id),
    )
    conn.commit()
    conn.close()
    return folder_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_scan.py -k get_or_create_folder -v`
Expected: 3 passed

- [ ] **Step 5: Run the full scan test file to confirm no regression**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_scan.py -v`
Expected: all passing (the 3 new tests plus the pre-existing ones — `get_or_create_folder` isn't called from `scan_watch_folder` yet, so none of today's behavior changes in this task)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/scan.py backend/tests/test_scan.py
git commit -m "feat: add get_or_create_folder helper for structure mirroring"
```

---

### Task 2: Mirror on-disk subdirectory structure in `scan_watch_folder`

**Files:**
- Modify: `backend/app/services/scan.py`
- Test: `backend/tests/test_scan.py`

**Interfaces:**
- Consumes: `get_or_create_folder(name: str, parent_id: Optional[str]) -> str` from Task 1.
- Produces: no change to `scan_watch_folder`'s own signature (`scan_watch_folder(watch_folder_row: dict) -> int`) or return value — only its internal folder-resolution behavior changes.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_scan.py`:

```python
def test_scan_watch_folder_mirrors_subdirectory_structure(client, tmp_path):
    from app.services.scan import scan_watch_folder
    from app.db import get_db_conn

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    (watched_dir / "loose.stl").write_bytes(b"solid loose endsolid")
    print_a = watched_dir / "PrintA"
    print_a.mkdir()
    (print_a / "part1.stl").write_bytes(b"solid part1 endsolid")
    (print_a / "part2.stl").write_bytes(b"solid part2 endsolid")
    supports = print_a / "supports"
    supports.mkdir()
    (supports / "support1.stl").write_bytes(b"solid support1 endsolid")

    row = {"id": "wf1", "path": str(watched_dir), "folderId": "1"}
    ingested = scan_watch_folder(row)
    assert ingested == 4

    conn = get_db_conn()
    folders = {f["name"]: dict(f) for f in conn.execute("SELECT id, name, parentId FROM folders")}
    models = {m["name"]: dict(m) for m in conn.execute("SELECT name, folderId FROM models")}
    conn.close()

    assert "PrintA" in folders
    print_a_folder = folders["PrintA"]
    assert print_a_folder["parentId"] == "1"

    assert "supports" in folders
    supports_folder = folders["supports"]
    assert supports_folder["parentId"] == print_a_folder["id"]

    assert models["loose.stl"]["folderId"] == "1"
    assert models["part1.stl"]["folderId"] == print_a_folder["id"]
    assert models["part2.stl"]["folderId"] == print_a_folder["id"]
    assert models["support1.stl"]["folderId"] == supports_folder["id"]


def test_scan_watch_folder_reuses_existing_folder_on_rescan(client, tmp_path):
    """A second scan tick that finds the same subdirectory again (e.g. a
    new file dropped into an already-mirrored PrintA folder) reuses the
    folder created on the first tick, not "PrintA (2)"."""
    from app.services.scan import scan_watch_folder
    from app.db import get_db_conn

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    print_a = watched_dir / "PrintA"
    print_a.mkdir()
    (print_a / "part1.stl").write_bytes(b"solid part1 endsolid")

    row = {"id": "wf1", "path": str(watched_dir), "folderId": "1"}
    scan_watch_folder(row)

    (print_a / "part2.stl").write_bytes(b"solid part2 endsolid")
    scan_watch_folder(row)

    conn = get_db_conn()
    print_a_folders = conn.execute("SELECT id FROM folders WHERE name='PrintA'").fetchall()
    conn.close()
    assert len(print_a_folders) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_scan.py -k "mirrors_subdirectory or reuses_existing_folder_on_rescan" -v`
Expected: FAIL — both new tests assert a `parentId` of `"1"` and a `PrintA`/`supports` hierarchy that today's flat ingestion never creates (`assert "PrintA" in folders` fails; today all 4 files ingest directly into folder `"1"`)

- [ ] **Step 3: Implement structure mirroring**

In `backend/app/services/scan.py`, replace the body of `scan_watch_folder`'s ingestion loop:

```python
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
        # A file directly in the watched root has zero relative parts and
        # ingests straight into the target folder, same as before this
        # change. A file under one or more subdirectories walks/creates a
        # matching chain of library folders under the target, mirroring
        # the on-disk structure at whatever depth it's found — this is
        # what lets "PrintA/part1.stl" and "PrintA/supports/part.stl"
        # both land under a real "PrintA" library folder instead of every
        # file from every subdirectory being flattened into one folder.
        relative_parts = file_path.parent.relative_to(root).parts
        target_folder_id = watch_folder_row["folderId"]
        for part in relative_parts:
            target_folder_id = get_or_create_folder(part, target_folder_id)

        try:
            ingest_file(
                str(file_path),
                folder_id=target_folder_id,
                original_filename=file_path.name,
                record_source=True,
                pickup_sidecar_notes=True,
                reference_only=True,
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_scan.py -v`
Expected: all passing, including the 2 new tests and every pre-existing test in this file (the pre-existing tests all use flat, single-level watched directories, so `relative_parts` is always `()` for them and their behavior is unchanged)

- [ ] **Step 5: Run the full backend test suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all passing, no regressions outside `test_scan.py` either

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/scan.py backend/tests/test_scan.py
git commit -m "feat: mirror on-disk subdirectory structure when scanning watch folders"
```

---

### Task 3: `folderPreviews` derived value in `App.tsx`

**Files:**
- Modify: `frontend/App.tsx`

**Interfaces:**
- Consumes: `models: STLModel[]` (existing App.tsx state — `STLModel` has `folderId: string`, `thumbnail?: string`, `dateAdded: number`, per `frontend/types.ts`).
- Produces: `folderPreviews: Record<string, { count: number; thumbnail: string | null }>` — for each folder that has at least one model whose `folderId` matches it directly (not recursive into sub-subfolders), the count of those models and the `thumbnail` of whichever has the smallest `dateAdded` (the first one added). A folder with no direct models has no entry in this record at all. Passed to `ModelList` as a new prop.

This task has no automated test — there is no frontend test runner in this project (confirmed: `frontend/package.json` has no test script). Verification is a TypeScript compile check plus a build, with the actual visual behavior verified in Task 4 once `ModelList.tsx` consumes this value.

- [ ] **Step 1: Add the derived value**

In `frontend/App.tsx`, find the existing `filteredFolders` derivation (a plain, non-memoized `const`, immediately after `filteredModels`):

```tsx
  // Filter subfolders based on selection
  const filteredFolders =
    currentFolderId === "all"
      ? folders.filter((f) => f.parentId == null)
      : folders.filter((f) => f.parentId === currentFolderId);
```

Add directly after it:

```tsx
  // One card per folder in ModelList shows a representative thumbnail +
  // part count instead of a plain folder icon when the folder has models
  // directly inside (see ModelList.tsx's folder-tile rendering) — this is
  // built the same way Sidebar.tsx's own folderCounts already counts
  // direct children from the full models array, extended to also track
  // whichever direct child was added first (smallest dateAdded) as the
  // representative thumbnail. A plain const, not useMemo, to match this
  // component's existing filteredModels/filteredFolders right above it.
  const folderPreviews: Record<string, { count: number; thumbnail: string | null }> = {};
  const earliestDateAddedByFolder: Record<string, number> = {};
  models.forEach((m) => {
    if (!folderPreviews[m.folderId]) {
      folderPreviews[m.folderId] = { count: 0, thumbnail: null };
    }
    folderPreviews[m.folderId].count += 1;
    if (
      earliestDateAddedByFolder[m.folderId] === undefined ||
      m.dateAdded < earliestDateAddedByFolder[m.folderId]
    ) {
      earliestDateAddedByFolder[m.folderId] = m.dateAdded;
      folderPreviews[m.folderId].thumbnail = m.thumbnail || null;
    }
  });
```

- [ ] **Step 2: Pass it to `ModelList`**

Find the `<ModelList ...>` usage (around line 714):

```tsx
                <ModelList
                  models={filteredModels}
                  folders={filteredFolders}
```

Add the new prop right after `folders`:

```tsx
                <ModelList
                  models={filteredModels}
                  folders={filteredFolders}
                  folderPreviews={folderPreviews}
```

(Task 4 adds `folderPreviews` to `ModelListProps` — until then this is a harmless extra prop TypeScript will flag; that's expected and resolved by the next task, not a defect in this one.)

- [ ] **Step 3: Type-check and build**

Run: `cd frontend && bunx tsc --noEmit`
Expected: one error, `Property 'folderPreviews' does not exist on type 'IntrinsicAttributes & ModelListProps'` (or equivalent) — this is the expected, temporary state until Task 4. Confirm there are no *other* new errors beyond this one (compare against the pre-existing baseline errors already known in this codebase: `App.tsx(759,19)`, three in `ModelList.tsx`, two in `STEPLoader.tsx` — all pre-existing and unrelated to this change).

- [ ] **Step 4: Commit**

```bash
git add frontend/App.tsx
git commit -m "feat: compute per-folder preview (count + representative thumbnail)"
```

---

### Task 4: Master-item card rendering in `ModelList.tsx`

**Files:**
- Modify: `frontend/components/ModelList.tsx`

**Interfaces:**
- Consumes: `folderPreviews: Record<string, { count: number; thumbnail: string | null }>` from Task 3.

- [ ] **Step 1: Add the prop to the interface**

In `frontend/components/ModelList.tsx`, find `ModelListProps`:

```tsx
interface ModelListProps {
  models: STLModel[];
  folders: Folder[];
  currentFolderName: string;
```

Add directly after `folders`:

```tsx
interface ModelListProps {
  models: STLModel[];
  folders: Folder[];
  folderPreviews: Record<string, { count: number; thumbnail: string | null }>;
  currentFolderName: string;
```

- [ ] **Step 2: Destructure it in the component**

Find where the component destructures its props:

```tsx
const ModelList: React.FC<ModelListProps> = ({
  models,
  folders,
  currentFolderName,
```

Add `folderPreviews` after `folders`:

```tsx
const ModelList: React.FC<ModelListProps> = ({
  models,
  folders,
  folderPreviews,
  currentFolderName,
```

- [ ] **Step 3: Use it in the folder-tile rendering**

Find the folder-tile block (the `processedFolders.map` inside the "Folders" grid):

```tsx
            {/* Render Folders First */}
            {processedFolders.map((folder) => (
              <div
                key={folder.id}
                onClick={() => onNavigateFolder(folder.id)}
                onDragOver={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setDragOverFolderId(folder.id);
                }}
                onDragLeave={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setDragOverFolderId(null);
                }}
                onDrop={(e) => handleFolderDrop(e, folder.id)}
                className={`cursor-pointer transition-all flex items-center relative overflow-hidden hover:-translate-y-1 ${
                  dragOverFolderId === folder.id
                    ? " -translate-y-1 brightness-150 ring-2 ring-white rounded-md"
                    : " "
                }`}
              >
                <Card className="w-full">
                  <CardActionArea>
                    <CardContent>
                      <Stack
                        sx={{
                          justifyContent: "start",
                          alignItems: "center",
                        }}
                        direction="row"
                        spacing={2}
                      >
                        <Avatar sx={{}}>
                          <FolderIcon />
                        </Avatar>
                        <Stack>
                          <Typography variant="body1" component="div">
                            {folder.name}
                          </Typography>
                          <Typography
                            variant="body2"
                            sx={{ color: "text.secondary" }}
                          >
                            Folder
                          </Typography>
                        </Stack>
                      </Stack>
                    </CardContent>
                  </CardActionArea>
                </Card>
              </div>
            ))}
```

Replace it with:

```tsx
            {/* Render Folders First */}
            {processedFolders.map((folder) => {
              const preview = folderPreviews[folder.id];
              const hasDirectFiles = !!preview && preview.count > 0;
              return (
                <div
                  key={folder.id}
                  onClick={() => onNavigateFolder(folder.id)}
                  onDragOver={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setDragOverFolderId(folder.id);
                  }}
                  onDragLeave={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setDragOverFolderId(null);
                  }}
                  onDrop={(e) => handleFolderDrop(e, folder.id)}
                  className={`cursor-pointer transition-all flex items-center relative overflow-hidden hover:-translate-y-1 ${
                    dragOverFolderId === folder.id
                      ? " -translate-y-1 brightness-150 ring-2 ring-white rounded-md"
                      : " "
                  }`}
                >
                  <Card className="w-full">
                    <CardActionArea>
                      <CardContent>
                        <Stack
                          sx={{
                            justifyContent: "start",
                            alignItems: "center",
                          }}
                          direction="row"
                          spacing={2}
                        >
                          {/* Avatar falls back to the FolderIcon child
                              automatically whenever src is undefined (no
                              direct files, or a direct file with no
                              thumbnail) or fails to load — no extra
                              fallback logic needed here. */}
                          <Avatar
                            src={hasDirectFiles ? preview.thumbnail || undefined : undefined}
                          >
                            <FolderIcon />
                          </Avatar>
                          <Stack>
                            <Typography variant="body1" component="div">
                              {folder.name}
                            </Typography>
                            <Typography
                              variant="body2"
                              sx={{ color: "text.secondary" }}
                            >
                              {hasDirectFiles
                                ? `${preview.count} ${preview.count === 1 ? "part" : "parts"}`
                                : "Folder"}
                            </Typography>
                          </Stack>
                        </Stack>
                      </CardContent>
                    </CardActionArea>
                  </Card>
                </div>
              );
            })}
```

- [ ] **Step 4: Type-check and build**

Run: `cd frontend && bunx tsc --noEmit`
Expected: back to exactly the same pre-existing baseline errors as before Task 3 (the `folderPreviews`-not-found error from Task 3 Step 3 is now gone; no new errors introduced)

Run: `cd frontend && bun run build`
Expected: builds successfully (matches the pattern used for every other frontend change this session)

- [ ] **Step 5: Manual verification via the packaged desktop build**

Run: `cd desktop && powershell -ExecutionPolicy Bypass -File build.ps1`, then install the resulting `desktop/installer_output/3DVaultkeeper-Setup.exe` and launch the app. Verify:

1. A folder with files directly inside (e.g. "Unsorted", which already has hundreds of files) shows a thumbnail from one of its files and "`N` parts" text, not a plain folder icon and "Folder" text.
2. A folder with only subfolders inside (e.g. "Vehicles", which contains "Tanks" but no files directly) still shows the plain folder icon and "Folder" text, unchanged.
3. Clicking either kind of tile still navigates into that folder and shows its contents, exactly as before.
4. Add a watch folder pointed at a real directory containing a subdirectory with 2+ files in it (e.g. create a temp folder with a "TestPrint" subfolder holding 2 STL files, and add the parent as a watch folder targeting "Unsorted"). After the scheduler runs (up to 60s) or clicking "Scan now", confirm a new "TestPrint" subfolder appears under "Unsorted" showing the master-item card look, and that navigating into it shows both files.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/ModelList.tsx
git commit -m "feat: show master-item card for folders with direct files"
```
