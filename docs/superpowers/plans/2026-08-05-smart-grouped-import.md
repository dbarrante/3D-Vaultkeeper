# Smart Grouped Import (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pasting a multi-file print project's URL and confirming once downloads every selected file into one new project-named folder, with the project's title/description captured as folder metadata — for Printables, MakerWorld, and any other site via a generic scraping fallback.

**Architecture:** Three existing site-specific-vs-generic importers (`PrintablesImporter`, `MakerWorldImporter`, new `GenericImporter`) all return the same `{title, description, files}` shape from `getModelOptions`. A new `POST /api/import/batch` endpoint replaces the frontend's current one-request-per-file loop, doing folder creation/collision-resolution + all downloads + all model-row creation in one server-side call. The frontend's existing "select files to import" screen gains an editable, pre-filled folder-name field, pre-checks every file, and calls the new batch endpoint.

**Tech Stack:** FastAPI + SQLite (backend), React + TypeScript + MUI (frontend), `requests` + new `beautifulsoup4` dependency for HTML parsing.

## Global Constraints

- No OBJ support anywhere already existed in this app; unaffected by this feature — 3D-file extensions handled here are `.stl`, `.3mf`, `.step`, `.stp`, `.zip` (matches the spec's file-type list).
- No official Thingiverse API integration — Thingiverse is handled by the generic scraper like any other unknown site, per the approved design (avoids requiring a developer app token that expires every ~90 days).
- New project folders are always created at the library root (`parentId = NULL`) — never inside whatever folder happens to be open in the app.
- A folder-name collision is never silently resolved (never silently merged, never silently suffixed) — always returned to the caller as an explicit decision.
- A single file's download failure never aborts the rest of the batch — the folder is still created/reused with whatever files succeeded, and failures are reported back explicitly.
- Every file selection checkbox in the review screen starts checked (pre-selected) by default.
- No browser-trigger mechanism (bookmarklet / `vaultkeeper://` protocol) in this phase — that is Phase 2, a separate future plan.
- No folder-description editing UI in this phase — descriptions are only ever set automatically at import time.

---

### Task 1: `folders.description` schema + folders router

**Files:**
- Modify: `backend/app/db.py` (`init_db()`, `row_to_folder`)
- Modify: `backend/app/routers/folders.py`
- Test: `backend/tests/test_folders.py`

**Interfaces:**
- Produces: `row_to_folder(row) -> {"id": str, "name": str, "parentId": str|None, "description": str|None}` — every later task that reads a folder row (Task 4's batch endpoint, any frontend consumer) relies on this key being present.
- Produces: `FolderData` (Pydantic, in `folders.py`) gains `description: Union[str, None] = None`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_folders.py`:

```python
def test_create_folder_with_description(client):
    response = client.post(
        "/api/folders",
        json={"name": "Imported Project", "parentId": None, "description": "A cool print"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "A cool print"


def test_create_folder_without_description_defaults_to_none(client):
    response = client.post("/api/folders", json={"name": "Minis", "parentId": None})
    assert response.status_code == 200
    assert response.json()["description"] is None


def test_get_folders_includes_description_field(client):
    client.post("/api/folders", json={"name": "Has Desc", "parentId": None, "description": "hello"})
    response = client.get("/api/folders")
    match = next(f for f in response.json() if f["name"] == "Has Desc")
    assert match["description"] == "hello"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_folders.py -v`
Expected: the three new tests FAIL (`KeyError: 'description'` or similar — the column/field doesn't exist yet).

- [ ] **Step 3: Add the schema column**

In `backend/app/db.py`, immediately after the existing `folders` `CREATE TABLE IF NOT EXISTS` block (the one with just `id, name, parentId`), add:

```python
    try:
        cur.execute("ALTER TABLE folders ADD COLUMN description TEXT")
    except sqlite3.OperationalError:
        pass
```

Then update `row_to_folder` (currently `{"id": row["id"], "name": row["name"], "parentId": row["parentId"]}`) to:

```python
def row_to_folder(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "parentId": row["parentId"],
        "description": row["description"],
    }
```

- [ ] **Step 4: Update the folders router**

In `backend/app/routers/folders.py`, change `FolderData`:

```python
class FolderData(BaseModel):
    name: str
    parentId: Union[str, None] = None
    description: Union[str, None] = None
```

Change `get_folders`:

```python
@router.get("/api/folders")
def get_folders():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id,name,parentId,description FROM folders")
    rows = cur.fetchall()
    conn.close()
    return [row_to_folder(r) for r in rows]
```

Change `create_folder`:

```python
@router.post("/api/folders")
def create_folder(item: FolderData):
    fid = str(uuid.uuid4())
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO folders(id,name,parentId,description) VALUES (?,?,?,?)",
        (fid, item.name, item.parentId, item.description),
    )
    conn.commit()
    conn.close()
    return {"id": fid, "name": item.name, "parentId": item.parentId, "description": item.description}
```

Change `update_folder`'s final SELECT (the `UPDATE` itself stays name-only — editing description is out of scope this phase):

```python
    cur.execute("SELECT id,name,parentId,description FROM folders WHERE id=?", (folder_id,))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_folders.py -v`
Expected: all tests PASS, including the 3 new ones and the pre-existing ones (`test_get_folders_returns_seeded_defaults`, `test_create_folder`, `test_update_folder_name`, etc. — confirm no regressions).

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/app/routers/folders.py backend/tests/test_folders.py
git commit -m "feat: add description column to folders"
```

---

### Task 2: Unify Printables/MakerWorld importer return shape

**Files:**
- Modify: `backend/app/importers/printables.py`
- Modify: `backend/app/importers/makerworld.py`
- Modify: `backend/app/routers/importers.py` (`import_model_options`)
- Test: `backend/tests/test_settings_and_importers.py`

**Interfaces:**
- Consumes: nothing new from Task 1.
- Produces: `PrintablesImporter.getModelOptions(url)` and `MakerWorldImporter.getModelOptions(url)` now both return `{"title": str, "description": str, "files": [dict, ...]}` instead of a bare list — every list item keeps its existing shape (`id`, `name`, `folder`, `previewPath`, `typeName`, `parentId`, optional `source`). This is the shape Task 3's `GenericImporter` must also produce, and the shape Task 5's frontend `retrieveModelOptions` must expect.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_settings_and_importers.py`:

```python
def test_import_options_returns_title_description_and_files(client):
    class _FakeImporterWithMeta:
        def getModelOptions(self, url):
            return {"title": "My Print", "description": "A neat thing", "files": [{"id": "1", "name": "part.stl"}]}

    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_FakeImporterWithMeta()):
        response = client.post("/api/import/options", json={"url": "https://www.printables.com/model/1-x"})
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "My Print"
    assert body["description"] == "A neat thing"
    assert body["files"] == [{"id": "1", "name": "part.stl"}]


def test_import_options_errors_when_no_files_found(client):
    class _EmptyImporter:
        def getModelOptions(self, url):
            return {"title": "Empty", "description": "", "files": []}

    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_EmptyImporter()):
        response = client.post("/api/import/options", json={"url": "https://www.printables.com/model/1-x"})
    assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_settings_and_importers.py -v -k "title_description or no_files_found"`
Expected: FAIL — the route currently returns whatever the importer gives it verbatim with no `files`-emptiness check, and the real importers don't return this shape yet.

- [ ] **Step 3: Update `import_model_options` to require non-empty `files`**

In `backend/app/routers/importers.py`:

```python
@router.post("/api/import/options")
def import_model_options(payload: dict):
    url = payload.get("url")
    try:
        if url is not None:
            importer, _source_label = importer_for_url(url)
            modelData = importer.getModelOptions(url)
            if modelData is not None and modelData.get("files"):
                return modelData
            raise ValueError("No importable files found at that URL")
        raise ValueError("URL is None")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 4: Update `PrintablesImporter`**

In `backend/app/importers/printables.py`, add a new query constant near `MODELQUERY`:

```python
PRINT_META_QUERY = """
query PrintMeta($id: ID!) {
  model: print(id: $id) {
    id
    name
    description
    __typename
  }
}
"""
```

Add a new method to `PrintablesImporter` (near `_get_model_info`):

```python
    def _get_print_meta(self, modelId):
        """Best-effort fetch of the print's own title/description, kept
        in a separate try/except from _get_model_info's file-listing
        query so an unexpected field name in Printables' schema here can
        never break the core (already-proven) file-listing feature --
        any failure just falls back to empty strings, and getModelOptions
        falls back further to the first file's own name.
        """
        try:
            header = {
                "accept": "application/graphql-response+json, application/graphql+json, application/json, text/event-stream, multipart/mixed",
                "accept-language": "en",
                "client-uid": self.clientId,
                "cache-control": "no-cache",
                "content-type": "application/json",
                "graphql-client-version": "v3.0.11",
                "pragma": "no-cache",
                "priority": "u=1, i",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
            }
            response = self.session.post(
                self.graphurl,
                json={"query": PRINT_META_QUERY, "variables": {"id": modelId}},
                headers=header,
            )
            if response.status_code != 200:
                return "", ""
            data = response.json()
            model = data.get("data", {}).get("model") or {}
            return model.get("name") or "", model.get("description") or ""
        except Exception:
            return "", ""
```

Change `getModelOptions`:

```python
    def getModelOptions(self, url):
        self.session = requests.Session()
        modelId = re.search(r"model/(\d+)", url)[1]
        if modelId is None:
            return None
        try:
            self._set_client_data(url)
            time.sleep(0.2)
            modelData = self._get_model_info(modelId)
            title, description = self._get_print_meta(modelId)
            if not title and modelData:
                title = modelData[0]["name"].rsplit(".", 1)[0]
            return {"title": title, "description": description, "files": modelData}
        except Exception as e:
            raise e
        finally:
            self.session.close()
```

- [ ] **Step 5: Update `MakerWorldImporter`**

In `backend/app/importers/makerworld.py`, `getModelOptions` currently ends with `return options` — change the final line to:

```python
        return {"title": title, "description": design.get("description") or "", "files": options}
```

(`title` is already computed earlier in the same function via `title = design.get("title") or design.get("name") or f"MakerWorld {model_id}"` — reuse that existing local variable, don't recompute it.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_settings_and_importers.py -v`
Expected: all tests PASS, including pre-existing ones (`test_import_model_by_id_uses_printables_importer`, etc. — confirm no regressions, since those exercise `importfromId` which is unchanged).

- [ ] **Step 7: Commit**

```bash
git add backend/app/importers/printables.py backend/app/importers/makerworld.py backend/app/routers/importers.py backend/tests/test_settings_and_importers.py
git commit -m "feat: unify importer return shape with title/description"
```

---

### Task 3: Generic-site scraper importer

**Files:**
- Create: `backend/app/importers/generic.py`
- Modify: `backend/app/routers/importers.py` (`importer_for_url`, `importer_for_source`)
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_generic_importer.py`

**Interfaces:**
- Consumes: the `{"title", "description", "files": [...]}` shape established in Task 2 — `GenericImporter.getModelOptions` must produce the same shape from day one.
- Produces: `GenericImporter.getModelOptions(url) -> {"title": str, "description": str, "files": [{"source": "generic", "parentId": str, "id": str, "name": str, "folder": None, "previewPath": "", "typeName": str}, ...]}` and `GenericImporter.importfromId(fileUrl, parentId, previewPath) -> (requests.Response, "")`, matching the 3-positional-argument calling convention `import_batch` (Task 4) uses for every importer.

- [ ] **Step 1: Add the new dependency**

Append to `backend/requirements.txt`:

```
beautifulsoup4>=4.12.0
```

Run: `cd backend && .venv\Scripts\pip.exe install -r requirements.txt`
Expected: `beautifulsoup4` installs successfully.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_generic_importer.py`:

```python
from unittest.mock import MagicMock, patch

from app.importers.generic import GenericImporter

SAMPLE_HTML = """
<html>
<head>
  <meta property="og:title" content="Cool Robot Miniature" />
  <meta property="og:description" content="A fully articulated robot mini, 5 parts." />
</head>
<body>
  <a href="/files/robot-body.stl">Download body</a>
  <a href="/files/robot-arm.stl">Download arm</a>
  <a href="/files/robot-arm.stl">Download arm (again)</a>
  <a href="/images/preview.png">Preview image</a>
</body>
</html>
"""


def test_generic_importer_parses_og_tags_and_file_links():
    fake_response = MagicMock()
    fake_response.text = SAMPLE_HTML
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        result = GenericImporter().getModelOptions("https://example.com/thing/42")

    assert result["title"] == "Cool Robot Miniature"
    assert result["description"] == "A fully articulated robot mini, 5 parts."
    names = {f["name"] for f in result["files"]}
    assert names == {"robot-body.stl", "robot-arm.stl"}
    assert all(f["source"] == "generic" for f in result["files"])


def test_generic_importer_falls_back_to_title_tag_when_no_og_tags():
    html = "<html><head><title>Fallback Title</title></head><body></body></html>"
    fake_response = MagicMock()
    fake_response.text = html
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        result = GenericImporter().getModelOptions("https://example.com/thing/1")

    assert result["title"] == "Fallback Title"
    assert result["description"] == ""
    assert result["files"] == []


def test_generic_importer_download_fetches_file_url_directly():
    fake_response = MagicMock()
    fake_response.content = b"solid fake endsolid"
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response) as mock_get:
        file, thumbnail = GenericImporter().importfromId(
            "https://example.com/files/robot-body.stl", None, ""
        )

    mock_get.assert_called_once()
    assert mock_get.call_args[0][0] == "https://example.com/files/robot-body.stl"
    assert file.content == b"solid fake endsolid"
    assert thumbnail == ""
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_generic_importer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.importers.generic'`.

- [ ] **Step 4: Implement `GenericImporter`**

Create `backend/app/importers/generic.py`:

```python
import os
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

FILE_EXTENSIONS = (".stl", ".3mf", ".step", ".stp", ".zip")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)


class GenericImporter:
    """Fallback importer for any site without a dedicated API-backed
    importer (Printables/MakerWorld) -- including Thingiverse, per the
    2026-08-05 design decision to avoid Thingiverse's official API and
    its 90-day-token requirement. Works by parsing the page's own HTML:
    Open Graph tags for title/description, and any link whose target
    ends in a known 3D-file extension as a downloadable file.
    """

    def getModelOptions(self, url):
        session = requests.Session()
        try:
            response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            title = self._meta_property(soup, "og:title")
            if not title and soup.title and soup.title.string:
                title = soup.title.string.strip()
            description = self._meta_property(soup, "og:description") or self._meta_name(
                soup, "description"
            )

            files = []
            seen = set()
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if not href.lower().split("?")[0].endswith(FILE_EXTENSIONS):
                    continue
                file_url = urljoin(url, href)
                if file_url in seen:
                    continue
                seen.add(file_url)
                filename = os.path.basename(urlparse(file_url).path)
                files.append(
                    {
                        "source": "generic",
                        "parentId": url,
                        "id": file_url,
                        "name": filename,
                        "folder": None,
                        "previewPath": "",
                        "typeName": filename.rsplit(".", 1)[-1],
                    }
                )

            return {"title": title or "", "description": description or "", "files": files}
        finally:
            session.close()

    def importfromId(self, fileUrl, parentId, previewPath):
        """`fileUrl` is the file's own absolute download URL, exactly as
        placed into each option's `id` field by getModelOptions above --
        unlike Printables/MakerWorld there is no separate "id vs. real
        download link" resolution step for a generic site.
        """
        session = requests.Session()
        try:
            file = session.get(fileUrl, headers={"User-Agent": USER_AGENT}, allow_redirects=True, timeout=120)
            file.raise_for_status()
            return file, ""
        finally:
            session.close()

    def _meta_property(self, soup, property_name):
        tag = soup.find("meta", property=property_name)
        return tag["content"].strip() if tag and tag.get("content") else None

    def _meta_name(self, soup, name):
        tag = soup.find("meta", attrs={"name": name})
        return tag["content"].strip() if tag and tag.get("content") else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_generic_importer.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 6: Wire the generic importer into the dispatch functions**

In `backend/app/routers/importers.py`, replace the existing `from app.importers import makerworld, printables` line with:

```python
from app.importers import generic, makerworld, printables
```

Change `importer_for_url` (it previously had no explicit Printables check and defaulted everything non-MakerWorld to Printables — now a third case needs an explicit fallback instead of that default):

```python
def importer_for_url(url: str):
    lowered = url.lower()
    if "makerworld.com" in lowered:
        return makerworld.MakerWorldImporter(), "makerworld"
    if "printables.com" in lowered:
        return printables.PrintablesImporter(), "printables"
    return generic.GenericImporter(), "generic"
```

Change `importer_for_source`:

```python
def importer_for_source(source: str):
    if source == "makerworld":
        return makerworld.MakerWorldImporter(get_setting("makerworld_bambu_token")), "MakerWorld"
    if source == "generic":
        return generic.GenericImporter(), "Web"
    return printables.PrintablesImporter(), "Printables"
```

- [ ] **Step 7: Add a router-level test confirming a non-Printables/MakerWorld URL dispatches to the generic importer**

Add to `backend/tests/test_settings_and_importers.py`:

```python
def test_import_options_uses_generic_importer_for_unknown_site(client):
    class _FakeGenericImporter:
        def getModelOptions(self, url):
            return {"title": "A Thing", "description": "desc", "files": [{"id": "x", "name": "x.stl"}]}

    with patch("app.routers.importers.generic.GenericImporter", return_value=_FakeGenericImporter()):
        response = client.post("/api/import/options", json={"url": "https://www.thingiverse.com/thing:12345"})
    assert response.status_code == 200
    assert response.json()["title"] == "A Thing"
```

- [ ] **Step 8: Run the full importer test suite**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_settings_and_importers.py tests/test_generic_importer.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/importers/generic.py backend/app/routers/importers.py backend/requirements.txt backend/tests/test_generic_importer.py backend/tests/test_settings_and_importers.py
git commit -m "feat: add generic-site fallback importer"
```

---

### Task 4: Batch import endpoint

**Files:**
- Modify: `backend/app/routers/importers.py` (new `import_batch` route)
- Test: `backend/tests/test_import_batch.py`

**Interfaces:**
- Consumes: `row_to_folder` from Task 1, `importer_for_source` from Task 3 (now generic-aware).
- Produces: `POST /api/import/batch` — request body `{"source": str, "folderName": str, "description": str, "files": [ {id, name, parentId, previewPath, typeName}, ... ], "folderResolution": "reuse"|"createNew"|null}`; response `{"folder": {...row_to_folder shape...}, "models": [...model dicts, same shape as /api/import/importid returns...], "failed": [{"name": str, "error": str}, ...]}` on success (200), or `{"detail": {"reason": "folder_name_collision", "existingFolderId": str, "existingFolderName": str}}` on a 409. Task 6 (frontend) calls this and handles both response shapes.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_import_batch.py`:

```python
from unittest.mock import patch


class _FakeBatchImporter:
    def importfromId(self, model_id, parent_id, preview_path):
        class _Resp:
            content = b"solid fake endsolid"

        return _Resp(), ""


class _PartialFailImporter:
    def __init__(self):
        self.calls = 0

    def importfromId(self, model_id, parent_id, preview_path):
        self.calls += 1
        if self.calls == 2:
            raise ValueError("download failed")

        class _Resp:
            content = b"solid fake endsolid"

        return _Resp(), ""


def _files_payload():
    return [
        {"id": "1", "name": "body.stl", "parentId": "p", "previewPath": "", "typeName": "stl"},
        {"id": "2", "name": "arm.stl", "parentId": "p", "previewPath": "", "typeName": "stl"},
    ]


def test_import_batch_creates_folder_and_models(client):
    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_FakeBatchImporter()):
        response = client.post(
            "/api/import/batch",
            json={
                "source": "printables",
                "folderName": "Cool Robot",
                "description": "A robot mini",
                "files": _files_payload(),
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["folder"]["name"] == "Cool Robot"
    assert body["folder"]["description"] == "A robot mini"
    assert len(body["models"]) == 2
    assert body["failed"] == []


def test_import_batch_reports_name_collision(client):
    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_FakeBatchImporter()):
        client.post(
            "/api/import/batch",
            json={"source": "printables", "folderName": "Dup Project", "description": "", "files": _files_payload()},
        )
        response = client.post(
            "/api/import/batch",
            json={"source": "printables", "folderName": "Dup Project", "description": "", "files": _files_payload()},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "folder_name_collision"


def test_import_batch_reuse_adds_to_existing_folder(client):
    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_FakeBatchImporter()):
        first = client.post(
            "/api/import/batch",
            json={"source": "printables", "folderName": "Reuse Me", "description": "", "files": _files_payload()},
        ).json()
        second = client.post(
            "/api/import/batch",
            json={
                "source": "printables",
                "folderName": "Reuse Me",
                "description": "",
                "files": _files_payload(),
                "folderResolution": "reuse",
            },
        ).json()
    assert second["folder"]["id"] == first["folder"]["id"]


def test_import_batch_create_new_makes_a_second_distinct_folder(client):
    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_FakeBatchImporter()):
        first = client.post(
            "/api/import/batch",
            json={"source": "printables", "folderName": "Twice Named", "description": "", "files": _files_payload()},
        ).json()
        second = client.post(
            "/api/import/batch",
            json={
                "source": "printables",
                "folderName": "Twice Named",
                "description": "",
                "files": _files_payload(),
                "folderResolution": "createNew",
            },
        ).json()
    assert second["folder"]["id"] != first["folder"]["id"]
    assert second["folder"]["name"] == "Twice Named"


def test_import_batch_partial_failure_still_creates_folder_and_succeeding_models(client):
    with patch(
        "app.routers.importers.printables.PrintablesImporter",
        return_value=_PartialFailImporter(),
    ):
        response = client.post(
            "/api/import/batch",
            json={
                "source": "printables",
                "folderName": "Partial Project",
                "description": "",
                "files": _files_payload(),
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body["models"]) == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["name"] == "arm.stl"
    assert body["folder"]["name"] == "Partial Project"


def test_import_batch_requires_folder_name(client):
    response = client.post(
        "/api/import/batch",
        json={"source": "printables", "folderName": "", "description": "", "files": _files_payload()},
    )
    assert response.status_code == 400


def test_import_batch_requires_at_least_one_file(client):
    response = client.post(
        "/api/import/batch",
        json={"source": "printables", "folderName": "No Files", "description": "", "files": []},
    )
    assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_import_batch.py -v`
Expected: FAIL with 404s — the route doesn't exist yet.

- [ ] **Step 3: Implement the endpoint**

In `backend/app/routers/importers.py`, add `row_to_folder` to the existing `from app.db import ...` line:

```python
from app.db import get_db_conn, get_setting, now_ms, row_to_folder, UPLOAD_DIR
```

Add the new route (after `import_model_by_id`):

```python
@router.post("/api/import/batch")
def import_batch(payload: dict):
    source = payload.get("source", "printables")
    folder_name = (payload.get("folderName") or "").strip()
    description = payload.get("description") or ""
    files = payload.get("files") or []
    folder_resolution = payload.get("folderResolution")

    if not folder_name:
        raise HTTPException(status_code=400, detail="folderName is required")
    if not files:
        raise HTTPException(status_code=400, detail="No files selected")

    importer, source_label = importer_for_source(source)

    conn = get_db_conn()
    cur = conn.cursor()

    existing = cur.execute(
        "SELECT id, name FROM folders WHERE name=? AND parentId IS NULL",
        (folder_name,),
    ).fetchone()

    if existing and folder_resolution is None:
        conn.close()
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "folder_name_collision",
                "existingFolderId": existing["id"],
                "existingFolderName": existing["name"],
            },
        )

    if existing and folder_resolution == "reuse":
        folder_id = existing["id"]
    else:
        folder_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO folders(id,name,parentId,description) VALUES (?,?,?,?)",
            (folder_id, folder_name, None, description),
        )
        conn.commit()

    created_models = []
    failed = []
    for f in files:
        model_id = f.get("id")
        model_name = f.get("name")
        try:
            file_resp, thumbnail = importer.importfromId(
                model_id, f.get("parentId"), f.get("previewPath")
            )
            if file_resp is None:
                raise ValueError("File is empty")
            ext = f.get("typeName") or "stl"
            mid = str(uuid.uuid4())
            filename = f"{mid}.{ext}"
            path = os.path.join(UPLOAD_DIR, filename)
            with open(path, "wb") as fh:
                fh.write(file_resp.content)
            size = os.path.getsize(path)
            model = {
                "id": mid,
                "name": model_name,
                "folderId": folder_id,
                "url": f"/api/models/{mid}/download",
                "size": size,
                "dateAdded": now_ms(),
                "tags": ["imported"],
                "description": f"Imported from {source_label}",
                "thumbnail": thumbnail,
                "filePath": path,
            }
            cur.execute(
                "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,filePath) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    model["id"], model["name"], model["folderId"], model["url"], model["size"],
                    model["dateAdded"], json.dumps(model["tags"]), model["description"],
                    model["thumbnail"], model["filePath"],
                ),
            )
            conn.commit()
            created_models.append(model)
        except Exception as e:
            failed.append({"name": model_name, "error": str(e)})

    cur.execute("SELECT id,name,parentId,description FROM folders WHERE id=?", (folder_id,))
    folder_row = cur.fetchone()
    conn.close()

    return {
        "folder": row_to_folder(folder_row),
        "models": created_models,
        "failed": failed,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_import_batch.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Run the full backend suite to confirm no regressions**

Run: `cd backend && .venv\Scripts\python.exe -m pytest -v`
Expected: all tests PASS (pre-existing count plus everything added in Tasks 1-4).

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/importers.py backend/tests/test_import_batch.py
git commit -m "feat: add batch import endpoint with folder collision handling"
```

---

### Task 5: Frontend API client + types

**Files:**
- Modify: `frontend/types.ts`
- Modify: `frontend/services/api.ts`

**Interfaces:**
- Consumes: the `{folder, models, failed}` success shape and `{reason, existingFolderId, existingFolderName}` collision shape from Task 4.
- Produces: `Folder` type gains `description: string | null`. New `ImportOptionsResult` type. `api.retrieveModelOptions(url): Promise<ImportOptionsResult>` (changed return type). New `api.importBatch(params): Promise<ImportBatchResult>`, throwing `ImportCollisionError` on a 409. Task 6 consumes all of this directly.

- [ ] **Step 1: Update `Folder` type**

In `frontend/types.ts`, change:

```ts
export interface Folder {
  id: string;
  name: string;
  parentId: string | null;
  icon?: string;
  description?: string | null;
}
```

Add a new type near `STLModelCollection`:

```ts
export interface ImportOptionsResult {
  title: string;
  description: string;
  files: STLModelCollection[];
}

export interface ImportBatchFailure {
  name: string;
  error: string;
}

export interface ImportBatchResult {
  folder: Folder;
  models: STLModel[];
  failed: ImportBatchFailure[];
}
```

- [ ] **Step 2: Update `retrieveModelOptions` and add `importBatch`**

In `frontend/services/api.ts`, add to the top-level imports:

```ts
import {
  // ...existing named imports stay...
  ImportOptionsResult,
  ImportBatchResult,
} from "../types";
```

Change `retrieveModelOptions`'s return type:

```ts
  // 13. RETRIEVE MODEL OPTIONS
  retrieveModelOptions: async (url: string): Promise<ImportOptionsResult> => {
    const res = await fetch(`${getApiBaseUrl()}/import/options`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!res.ok) throw new Error("Import failed");
    return res.json();
  },
```

Add a new method right after `importModelFromId`:

```ts
  // 13b. IMPORT BATCH (grouped, folder-aware)
  importBatch: async (params: {
    source: string;
    folderName: string;
    description: string;
    files: STLModelCollection[];
    folderResolution?: "reuse" | "createNew";
  }): Promise<ImportBatchResult> => {
    const res = await fetch(`${getApiBaseUrl()}/import/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    if (res.status === 409) {
      const body = await res.json();
      throw new ImportCollisionError(
        body.detail.existingFolderId,
        body.detail.existingFolderName,
      );
    }
    if (!res.ok) throw new Error("Import failed");
    return res.json();
  },
```

Add the error class near the top of the file (after the imports, before the `api` object definition):

```ts
export class ImportCollisionError extends Error {
  existingFolderId: string;
  existingFolderName: string;
  constructor(existingFolderId: string, existingFolderName: string) {
    super("Folder name collision");
    this.name = "ImportCollisionError";
    this.existingFolderId = existingFolderId;
    this.existingFolderName = existingFolderName;
  }
}
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && ./node_modules/.bin/tsc.exe --noEmit`
Expected: no NEW errors introduced by this task (compare against the pre-existing baseline — this repo already has a handful of unrelated pre-existing errors in `ModelList.tsx`/`STEPLoader.tsx`; none of them are in `api.ts` or `types.ts`).

- [ ] **Step 4: Commit**

```bash
git add frontend/types.ts frontend/services/api.ts
git commit -m "feat: add importBatch API client and updated import types"
```

---

### Task 6: Import review screen — grouping, pre-selection, collision UI

**Files:**
- Modify: `frontend/App.tsx`

**Interfaces:**
- Consumes: `ImportOptionsResult`, `ImportBatchResult`, `ImportCollisionError`, `api.importBatch` from Task 5.
- Produces: nothing consumed by later tasks — this is the last piece of the user-facing pipeline for this phase.

- [ ] **Step 1: Add new state**

Near the existing "Import Modal State" block (`showImportModal`, `modelsOptions`, etc.) in `App.tsx`, add:

```tsx
  const [importProjectTitle, setImportProjectTitle] = useState("");
  const [importProjectDescription, setImportProjectDescription] = useState("");
  const [importCollision, setImportCollision] = useState<{
    existingFolderId: string;
    existingFolderName: string;
  } | null>(null);
```

Add the import at the top of `App.tsx`:

```tsx
import { ImportCollisionError } from "./services/api";
```

- [ ] **Step 2: Update `handleImportSubmit` to consume the new response shape and pre-select everything**

Replace the current body:

```tsx
  const handleImportSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!importUrl || !importFolderId) return;

    try {
      const result = await api.retrieveModelOptions(importUrl);
      const NewSet = new Set("");
      result.files.forEach((m) => {
        if (m.folder && !NewSet.has(m.folder)) {
          NewSet.add(m.folder);
        }
      });
      setFolderOptions(NewSet);
      setModelsOptions(result.files);
      setImportProjectTitle(
        result.title ||
          result.files[0]?.name.replace(/\.[^.]+$/, "") ||
          "Imported Project",
      );
      setImportProjectDescription(result.description || "");
      setSelectedOptions(new Set(result.files.map((m) => m.id)));
      setImportCollision(null);
      setShowImportModal(false);
      setShowImportOptionsModal(true);
    } catch (error) {
      console.error("Import failed:", error);
      alert("Failed to import from URL");
    }
  };
```

- [ ] **Step 3: Replace `handleUpdateSTEPThumbnail`'s state update (no longer the first thing to add the model to state — the batch response already does that)**

Replace the current body:

```tsx
  const handleUpdateSTEPThumbnail = async (newModel: STLModel) => {
    const tbuff = await fetch(port + newModel.url);
    const thumbnailBuffer = await tbuff.bytes();
    try {
      const thumbnail = await generateThumbnail(
        new File([thumbnailBuffer], newModel.name),
      );
      const updated = await api.updateModel(newModel.id, { thumbnail });
      setModels((prev) =>
        prev.map((m) => (m.id === updated.id ? updated : m)),
      );
    } catch (e) {
      console.warn("Thumbnail generation failed, uploading without thumbnail");
    }
  };
```

(This preserves the existing behavior of re-rendering a client-side thumbnail for every freshly imported model — the site's own preview image is often lower quality or, for STEP files, may not exist at all. The only change from today's version is using `.map` to replace the model already in state by id, instead of prepending a duplicate — necessary because Step 4 below now adds the model to state itself, before this function runs.)

- [ ] **Step 4: Replace `handleImportChoice` with the batch call + collision handling**

Replace the current body:

```tsx
  const handleImportChoice = async (resolution?: "reuse" | "createNew") => {
    const filesToImport = modelsOptions.filter((m) => selectedOptions.has(m.id));
    if (!importUrl || filesToImport.length === 0) return;

    setIsLoading(true);
    setShowImportOptionsModal(false);
    try {
      const result = await api.importBatch({
        source: filesToImport[0]?.source || "printables",
        folderName: importProjectTitle,
        description: importProjectDescription,
        files: filesToImport,
        folderResolution: resolution,
      });
      setModels((prev) => [...result.models, ...prev]);
      setFolders((prev) => [...prev, result.folder]);
      result.models.forEach((m) => {
        handleUpdateSTEPThumbnail(m);
      });
      setImportCollision(null);
      if (result.failed.length > 0) {
        alert(
          `Imported ${result.models.length} of ${filesToImport.length} file(s). Failed: ${result.failed
            .map((f) => f.name)
            .join(", ")}`,
        );
      }
    } catch (error) {
      if (error instanceof ImportCollisionError) {
        setImportCollision({
          existingFolderId: error.existingFolderId,
          existingFolderName: error.existingFolderName,
        });
        setShowImportOptionsModal(true);
        return;
      }
      console.error("Import failed:", error);
      alert("Failed to import from URL");
    } finally {
      setIsLoading(false);
    }
  };
```

Note this replaces the old per-file loop and the `importFolderId`/`uploadQueue` usage that loop relied on — `importFolderId` and `uploadQueue` remain used elsewhere in the file (manual upload flow) and are NOT being removed, just no longer read by this specific function.

- [ ] **Step 5: Update the "select files to import" modal JSX**

In the Import Options Modal (the block starting `{showImportOptionsModal && (`), immediately after the header `<div className="static flex top-0 justify-between items-center mb-6">...</div>` and before the `{/* File List */}` div, add:

```tsx
                    <TextField
                      fullWidth
                      size="small"
                      label="Project folder name"
                      value={importProjectTitle}
                      onChange={(e) => setImportProjectTitle(e.target.value)}
                      sx={{ mb: 3 }}
                    />

                    {importCollision && (
                      <div className="mb-4 p-3 rounded-lg bg-amber-900/30 border border-amber-700 text-sm text-amber-200">
                        <p className="mb-2">
                          A folder named "{importCollision.existingFolderName}"
                          already exists.
                        </p>
                        <div className="flex gap-2">
                          <button
                            type="button"
                            onClick={() => handleImportChoice("reuse")}
                            className="px-3 py-1.5 text-xs rounded bg-vault-700 hover:bg-vault-600 text-white"
                          >
                            Add to existing folder
                          </button>
                          <button
                            type="button"
                            onClick={() => handleImportChoice("createNew")}
                            className="px-3 py-1.5 text-xs rounded bg-vault-700 hover:bg-vault-600 text-white"
                          >
                            Create a new folder anyway
                          </button>
                        </div>
                      </div>
                    )}
```

Change the final "Import" button's `onClick` — currently `onClick={() => handleImportChoice()}` — leave it as-is (calling with no args is exactly the `resolution === undefined` first-attempt case). When `importCollision` is set, hide this default button in favor of the two above (it's confusing to show both at once):

```tsx
                    {!importCollision && (
                      <div
                        onClick={() => handleImportChoice()}
                        className="static bottom-0 p-2 mt-4 cursor-pointer rounded-lg bg-vault-700 hover:bg-vault-600 text-slate-200 font-medium transition-colors text-center"
                      >
                        {" "}
                        Import{" "}
                      </div>
                    )}
```

(Wrap the pre-existing `<div onClick={() => handleImportChoice()}>Import</div>` block in this new conditional — don't rewrite its contents.)

- [ ] **Step 6: Type-check**

Run: `cd frontend && ./node_modules/.bin/tsc.exe --noEmit`
Expected: no NEW errors beyond the established pre-existing baseline.

- [ ] **Step 7: Build**

Run: `cd frontend && bun run build`
Expected: builds cleanly.

- [ ] **Step 8: Manual verification (no automated frontend test suite exists in this project)**

Using the packaged desktop build or `bun run dev` against a real backend:
1. Paste a real Printables or MakerWorld project URL with multiple files. Confirm the review screen shows every file pre-checked, and the folder-name field is pre-filled with a sensible title.
2. Click Import. Confirm one new folder appears with all files inside it.
3. Paste a Thingiverse (or any other) URL. Confirm the generic scraper still produces a usable title and file list (may need a real Thingiverse thing page with visible file links — verify against one you know has downloadable STL files).
4. Re-import the exact same project URL again (same folder name). Confirm the collision prompt appears with both buttons, and each button does what it says (reuse merges into the existing folder; create-new makes a second folder with the same name).
5. Click a folder that now has a description (in Task 7, once built) and confirm it shows correctly — can be re-checked after Task 7 lands.

- [ ] **Step 9: Commit**

```bash
git add frontend/App.tsx
git commit -m "feat: grouped batch import with pre-selected files and collision UI"
```

---

### Task 7: Folder description display

**Files:**
- Modify: `frontend/components/ModelList.tsx`

**Interfaces:**
- Consumes: `folder.description` (from the `Folder` type updated in Task 5).
- Produces: nothing consumed elsewhere — final task in this plan.

- [ ] **Step 1: Add the `Info` icon import**

In `frontend/components/ModelList.tsx`, add `Info` to the existing `from "lucide-react"` import list (alongside `FileBox`, `ChevronLeft`, etc.).

- [ ] **Step 2: Add the info affordance to the folder tile**

In the folder-tile rendering block (inside `processedFolders.map((folder) => { ... })`, around the `<Stack>` containing the `Avatar` and folder name/subtitle `Stack`), add a `Tooltip`-wrapped icon rendered only when `folder.description` is truthy. Change:

```tsx
                <div
                  key={folder.id}
                  onClick={() => onNavigateFolder(folder.id)}
                  onDragOver={(e) => {
```

to keep the outer `onClick` for navigation, but stop the new icon's click from also triggering navigation. Inside the `<Stack direction="row" spacing={2}>` that currently holds `<Avatar>...</Avatar>` and the name/subtitle `<Stack>`, add a third child at the end:

```tsx
                          {folder.description && (
                            <Tooltip title={folder.description} placement="top">
                              <IconButton
                                size="small"
                                onClick={(e) => {
                                  e.stopPropagation();
                                }}
                                sx={{ ml: "auto" }}
                              >
                                <Info className="w-4 h-4 text-slate-400" />
                              </IconButton>
                            </Tooltip>
                          )}
```

(`Tooltip` and `IconButton` are already imported in this file — confirmed via the existing `import Tooltip from "@mui/material/Tooltip"` and `import IconButton from "@mui/material/IconButton"` lines near the top.)

- [ ] **Step 3: Type-check**

Run: `cd frontend && ./node_modules/.bin/tsc.exe --noEmit`
Expected: no NEW errors beyond the established baseline.

- [ ] **Step 4: Build**

Run: `cd frontend && bun run build`
Expected: builds cleanly.

- [ ] **Step 5: Manual verification**

Using the packaged build or dev server: after completing an import from Task 6's verification (a folder with a real scraped description), confirm the info icon appears on that folder's tile and clicking/hovering it shows the description. Confirm a folder with no description (e.g. one created via "+ New Folder") shows no icon at all.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/ModelList.tsx
git commit -m "feat: show folder description via info icon on folder tiles"
```
