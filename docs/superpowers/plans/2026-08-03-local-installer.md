# Local Windows Installer + License Compliance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package 3D Vaultkeeper as a proper Windows installer (no Python/Node/Docker required on the target machine) and make the codebase legally clean to sell, with no licensing/activation mechanism.

**Architecture:** A new `desktop/launcher.py` starts the existing FastAPI app in a background thread on a dynamically-assigned free port, then shows it in a native `pywebview` window. `app/main.py` gains a conditional static-file mount so the backend serves the built frontend directly — no Node runtime needs to ship. `app/db.py` gains a frozen-build branch defaulting data storage to `%LOCALAPPDATA%\3D Vaultkeeper\`. PyInstaller (`--onedir`) bundles the Python side; Inno Setup wraps that into `setup.exe`.

**Tech Stack:** Existing FastAPI/SQLite backend and Vite/React frontend, unchanged. New: PyInstaller, pywebview (desktop-only, not part of the Docker/web deployment), Inno Setup (external tool, not a Python package).

## Global Constraints

- License compliance only — no licensing/activation/trial/copy-protection mechanism in this plan.
- Windows only.
- A proper installer (Start Menu shortcut, "Add or Remove Programs" entry, clean uninstall) — not a portable zip.
- Dev (`cd backend && uvicorn ...`) and Docker deployment paths must be completely unaffected by every change in this plan — all new behavior is additive and only activates in a frozen PyInstaller build.
- No CI/CD automation, no auto-update, no code signing in this plan (all explicitly deferred, confirmed with the user).
- Uninstalling must never delete the user's data directory.

---

## Status: Complete

All 6 tasks implemented, task-reviewed, and passed a final whole-branch
review (6 Important findings found and fixed, including one fix-wave
regression caught and reverted). `THIRD-PARTY-LICENSES.md` reached full
closure across 7 review rounds (343-package frontend dependency tree via
`frontend/scripts/generate-license-report.py`). Two remaining Important
findings (a misleading `watcher.py` comment, missing README desktop-build
docs) were fixed in a final doc round; the user explicitly chose to defer
the rest as polish (app icon, `AppVersion`/`package.json` version sync,
WebView2 prerequisite check, a `db.py` `UPLOAD_DIR.mkdir()` edge case).

Final smoke test (2026-08-03): built `desktop\installer_output\3DVaultkeeper-Setup.exe`
end-to-end (`desktop/build.ps1`), then ran a real silent
install → launch → uninstall cycle. Installed to `C:\Program Files\3D
Vaultkeeper` with a proper uninstall registry entry; launched to a real
window and created `%LOCALAPPDATA%\3D Vaultkeeper\` (`data.db`,
`uploads/manuals`) on first run; uninstall removed `Program Files` and the
registry entry while leaving `%LOCALAPPDATA%\3D Vaultkeeper\` completely
untouched — the one safety property that matters most for a sold product.

The SDD workspace (`.superpowers/sdd/2026-08-03-local-installer/`, a
git-ignored scratch ledger) has been deleted — git history from
`docs/superpowers/plans/2026-08-03-local-installer.md`'s companion commits
onward is the record.

---

### Task 1: Frozen-aware data directory defaults

**Files:**
- Modify: `backend/app/db.py:1-13`
- Test: `backend/tests/test_frozen_data_dir.py`

**Interfaces:**
- Produces: `app.db.DB_PATH` (str) and `app.db.UPLOAD_DIR` (Path) — same names and types as today. When `sys.frozen` is truthy and no `DB_PATH`/`FILE_STORAGE` env var is set, they default under `%LOCALAPPDATA%\3D Vaultkeeper\` instead of today's relative dev paths. An explicit env var always wins, frozen or not.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_frozen_data_dir.py`:

```python
import sys
from pathlib import Path


def _reimport_db():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app import db
    return db


def test_db_path_defaults_to_relative_when_not_frozen(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("FILE_STORAGE", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)

    db = _reimport_db()

    assert db.DB_PATH == "data.db"
    assert db.UPLOAD_DIR == Path("./app/uploads")


def test_db_path_defaults_to_localappdata_when_frozen(monkeypatch, tmp_path):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("FILE_STORAGE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    db = _reimport_db()

    assert db.DB_PATH == str(tmp_path / "3D Vaultkeeper" / "data.db")
    assert db.UPLOAD_DIR == tmp_path / "3D Vaultkeeper" / "uploads"


def test_env_override_wins_even_when_frozen(monkeypatch, tmp_path):
    custom_db = str(tmp_path / "custom.db")
    custom_uploads = str(tmp_path / "custom_uploads")
    monkeypatch.setenv("DB_PATH", custom_db)
    monkeypatch.setenv("FILE_STORAGE", custom_uploads)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    db = _reimport_db()

    assert db.DB_PATH == custom_db
    assert db.UPLOAD_DIR == Path(custom_uploads)
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_frozen_data_dir.py -v`
Expected: the frozen/localappdata test fails (current code has no frozen branch — `db.DB_PATH` is still `"data.db"` regardless of `sys.frozen`); the other two already pass with today's code (they lock in existing behavior).

- [ ] **Step 3: Add the frozen branch**

In `backend/app/db.py`, replace lines 1-13:

```python
import os
import sys
import sqlite3
import shutil
import time
import json
from pathlib import Path
from typing import Optional, Dict, Any


def _frozen_data_dir() -> Optional[Path]:
    """Where DB_PATH/FILE_STORAGE default to when no env var override is
    set and this is a packaged desktop build (sys.frozen, set by
    PyInstaller). A normal dev checkout or Docker container is unaffected
    — this only ever returns non-None in a frozen build. Program Files is
    typically read-only for standard users, and a relative path is
    unreliable when launched from a Start Menu shortcut, so the frozen
    case gets a real per-user directory instead. LOCALAPPDATA rather than
    the Roaming APPDATA: a 3D-print library can get large, and Roaming
    profiles sync across machines in domain-joined environments, which a
    multi-gigabyte uploads folder should never do.
    """
    if getattr(sys, "frozen", False):
        return Path(os.environ["LOCALAPPDATA"]) / "3D Vaultkeeper"
    return None


_frozen_dir = _frozen_data_dir()

DB_PATH = os.getenv("DB_PATH", str(_frozen_dir / "data.db") if _frozen_dir else "data.db")
UPLOAD_DIR = Path(os.getenv("FILE_STORAGE", str(_frozen_dir / "uploads") if _frozen_dir else "./app/uploads"))
MANUAL_DIR = Path(os.getenv("MANUAL_STORAGE", UPLOAD_DIR / "manuals"))
MANUAL_DIR.mkdir(parents=True, exist_ok=True)
WEBUI_URL = os.getenv("WEBUI_URL", "http://localhost:8989")
```

- [ ] **Step 4: Run to confirm it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_frozen_data_dir.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite to confirm nothing else broke**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: all passing (this change is additive — every existing test runs unfrozen with explicit env vars set by the `client` fixture, so `_frozen_dir` is always `None` for them regardless).

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/tests/test_frozen_data_dir.py
git commit -m "feat: default data storage to %LOCALAPPDATA% in frozen desktop builds"
```

---

### Task 2: FastAPI serves the built frontend

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_frontend_static_mount.py`

**Interfaces:**
- Consumes: nothing new from Task 1.
- Produces: a conditional mount at `GET /` (and any path not matched by an `/api/*` router) serving `frontend/dist/index.html` and its assets when that directory exists. In a frozen build, the directory it looks for is `Path(sys._MEIPASS) / "frontend_dist"` — Task 4's PyInstaller spec MUST bundle the built frontend under that exact name (`frontend_dist`) for this to resolve correctly at runtime.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_frontend_static_mount.py`:

```python
import sys


def test_frontend_static_mount_serves_index_when_present(tmp_path, monkeypatch):
    frontend_dist = tmp_path / "frontend_dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text("<html><body>Vaultkeeper</body></html>")

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("FILE_STORAGE", str(tmp_path / "uploads"))
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    from app import main as app_module
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as test_client:
        response = test_client.get("/")
        assert response.status_code == 200
        assert "Vaultkeeper" in response.text

        # the mount must not shadow existing API routes
        api_response = test_client.get("/api/folders")
        assert api_response.status_code == 200
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_frontend_static_mount.py -v`
Expected: FAIL — `GET /` currently 404s (no such route exists yet).

- [ ] **Step 3: Add the conditional mount**

In `backend/app/main.py`, add near the top (after existing imports) and at the end of the file:

```python
import os
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from app.db import init_db, UPLOAD_DIR, WEBUI_URL
from app.routers import folders, models, manuals, settings, importers, watcher, inbox, ai
from app.scheduler import start_scheduler

init_db()

app = FastAPI(title="STLVault API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development, or use [WEBUI_URL] for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(folders.router)
app.include_router(models.router)
app.include_router(manuals.router)
app.include_router(settings.router)
app.include_router(importers.router)
app.include_router(watcher.router)
app.include_router(inbox.router)
app.include_router(ai.router)

start_scheduler(app)


def _frontend_dist_dir() -> Path:
    """The built frontend's static files. In a frozen desktop build,
    PyInstaller's `datas` bundling (see desktop/launcher.spec) places it
    under sys._MEIPASS/frontend_dist — that exact name must match on both
    sides. In a normal dev checkout it's the sibling frontend/dist/ that
    `bun run build` produces, which usually doesn't exist (nobody builds
    the frontend to run the backend test suite) — the mount below is
    skipped entirely in that case, exactly like it always has been.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "frontend_dist"
    return Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


FRONTEND_DIST = _frontend_dist_dir()
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    # Ensure upload directory exists
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    port = int(os.getenv("PORT", "5173"))

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info"
    )
```

- [ ] **Step 4: Run to confirm it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_frontend_static_mount.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite to confirm nothing else broke**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: all passing — every other test runs with `sys.frozen` unset and no real `frontend/dist` on disk, so `FRONTEND_DIST.is_dir()` is `False` and the mount is skipped exactly as before this change.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_frontend_static_mount.py
git commit -m "feat: serve the built frontend directly from FastAPI when present"
```

---

### Task 3: `desktop/launcher.py`

**Files:**
- Create: `desktop/launcher.py`
- Test: `desktop/tests/test_launcher.py`

**Interfaces:**
- Consumes: `app.main.app` (imported lazily inside `_run_server`, not at module level — see rationale in Step 3).
- Produces: `find_free_port() -> int` and `wait_for_health(url: str, timeout_seconds: float = 15.0) -> bool`, both pure and directly testable. `main()` orchestrates them plus `pywebview` — not unit tested (needs a real display), covered by Task 6's manual verification checklist instead.

- [ ] **Step 1: Write the failing tests**

Create `desktop/tests/test_launcher.py`:

```python
import http.server
import socket
import threading


def test_find_free_port_returns_a_bindable_port():
    from launcher import find_free_port

    port = find_free_port()
    assert 1024 <= port <= 65535
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))  # raises OSError if somehow still held


def test_wait_for_health_returns_true_once_server_responds():
    from launcher import find_free_port, wait_for_health

    port = find_free_port()

    class OKHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass  # keep test output quiet

    server = http.server.HTTPServer(("127.0.0.1", port), OKHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert wait_for_health(f"http://127.0.0.1:{port}/", timeout_seconds=5) is True
    finally:
        server.shutdown()


def test_wait_for_health_returns_false_when_nothing_is_listening():
    from launcher import find_free_port, wait_for_health

    port = find_free_port()  # guaranteed free — nothing listens on it
    assert wait_for_health(f"http://127.0.0.1:{port}/", timeout_seconds=1) is False
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd desktop && ../backend/.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'launcher'` (doesn't exist yet).

- [ ] **Step 3: Create `desktop/launcher.py`**

```python
"""Desktop entry point for the packaged (PyInstaller) build: starts the
existing FastAPI app on a background thread and shows it in a native
window via pywebview, instead of requiring a browser tab.
"""
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

import uvicorn
import webview


def find_free_port() -> int:
    """Binds to port 0 so the OS assigns a free ephemeral port, then
    releases it immediately — avoids colliding with anything else already
    running on the user's machine, which is unknown for a packaged app."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_health(url: str, timeout_seconds: float = 15.0) -> bool:
    """Polls `url` until it responds or `timeout_seconds` elapses. Holds
    the pywebview window open until uvicorn (started on a separate
    thread) is actually ready to serve requests."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


def _run_server(port: int) -> None:
    # Imported here, not at module level: this keeps find_free_port/
    # wait_for_health importable and testable (desktop/tests/test_launcher.py)
    # without needing the whole backend app package (and its dependencies)
    # importable in that test environment.
    backend_dir = str((__import__("pathlib").Path(__file__).resolve().parent.parent / "backend"))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    port = find_free_port()
    server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{port}/"
    if not wait_for_health(url):
        raise RuntimeError(f"Backend did not become ready at {url} within the timeout")

    webview.create_window("3D Vaultkeeper", url, width=1400, height=900)
    webview.start()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to confirm it passes**

Run: `cd desktop && ../backend/.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: PASS (all 3 tests) — note these tests never import `app.main`, so they don't need `uvicorn`/`webview` installed yet either; Task 4 installs those.

- [ ] **Step 5: Commit**

```bash
git add desktop/launcher.py desktop/tests/test_launcher.py
git commit -m "feat: add desktop launcher (dynamic port, health check, pywebview window)"
```

---

### Task 4: PyInstaller bundle

**Files:**
- Create: `desktop/requirements.txt`
- Create: `desktop/launcher.spec`

**Interfaces:**
- Consumes: `desktop/launcher.py` (Task 3), `app.main.app` (Task 2 — must already have the frontend-serving mount).
- Produces: `desktop/dist/3D Vaultkeeper/` — the `--onedir` PyInstaller output. Task 6's `installer.iss` packages this directory verbatim.

- [ ] **Step 1: Add desktop-only dependencies**

Create `desktop/requirements.txt`:

```
pyinstaller>=6.0
pywebview>=5.0
```

These are desktop-packaging-only — kept out of `backend/requirements.txt` so the Docker image (which never runs the desktop launcher) doesn't carry them.

- [ ] **Step 2: Install them into the existing backend venv**

Run: `backend/.venv/Scripts/pip.exe install -r desktop/requirements.txt`
Expected: installs cleanly, no conflicts with existing backend dependencies (PyInstaller and pywebview are self-contained tools with no overlap with FastAPI/uvicorn/etc.).

- [ ] **Step 3: Confirm `desktop/tests/test_launcher.py` still passes now that `uvicorn`/`webview` are importable**

Run: `cd desktop && ../backend/.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: PASS (unchanged from Task 3 — this just confirms the new install didn't break anything).

- [ ] **Step 4: Build the frontend** (prerequisite for the next step — the spec's `datas` bundling copies from this literal path)

Run: `cd frontend && bun run build`
Expected: produces `frontend/dist/` with `index.html` and an `assets/` directory.

- [ ] **Step 5: Verify occt-import-js's WASM ships as a separate asset, not inlined**

Run (from repo root): `ls frontend/dist/assets/*.wasm` or `dir frontend\dist\assets\*.wasm`
Expected: at least one `.wasm` file listed. This is the concrete check behind Task 5's LGPL compliance note — Vite's default behavior copies `.wasm` as a standalone asset rather than inlining it into a JS bundle, and this step confirms that's actually what happened for this build, not just assumed.

- [ ] **Step 6: Create the PyInstaller spec**

Create `desktop/launcher.spec`:

```python
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

repo_root = Path(SPECPATH).resolve().parent

a = Analysis(
    ['launcher.py'],
    pathex=[str(repo_root / 'backend')],
    binaries=[],
    datas=[
        (str(repo_root / 'frontend' / 'dist'), 'frontend_dist'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='3D Vaultkeeper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='3D Vaultkeeper',
)
```

`console=False` suppresses a console window from appearing when the packaged app launches — the desktop equivalent of the `CREATE_NO_WINDOW` discipline used in `control-app/process_manager.py` earlier. `pathex` includes `backend/` so `from app.main import app` resolves. The `datas` entry bundles the frontend build under the exact name `frontend_dist`, matching what `app/main.py`'s `_frontend_dist_dir()` (Task 2) looks for via `sys._MEIPASS`.

- [ ] **Step 7: Build and manually verify**

Run: `cd desktop && ../backend/.venv/Scripts/python.exe -m PyInstaller launcher.spec --distpath dist --workpath build --noconfirm`
Expected: completes without error, produces `desktop/dist/3D Vaultkeeper/3D Vaultkeeper.exe`.

Run the built exe directly: `desktop/dist/3D Vaultkeeper/3D Vaultkeeper.exe`
Expected: no console window appears; a window opens showing the real app UI within a few seconds; `%LOCALAPPDATA%\3D Vaultkeeper\data.db` exists after it starts. Close the window and confirm the process exits (no orphaned background process — check with `tasklist`).

- [ ] **Step 8: Commit**

```bash
git add desktop/requirements.txt desktop/launcher.spec
git commit -m "feat: PyInstaller bundle for the desktop launcher"
```

(`desktop/dist/` and `desktop/build/` are PyInstaller's own output directories — add them to `.gitignore` in this same commit: append `/desktop/dist/` and `/desktop/build/` to `.gitignore`.)

---

### Task 5: `THIRD-PARTY-LICENSES.md`

**Files:**
- Create: `THIRD-PARTY-LICENSES.md`

**Interfaces:**
- Produces: a repo-root file that Task 6 also copies into the installed app's directory via `installer.iss`.

- [ ] **Step 1: Assemble the dependency table**

Create `THIRD-PARTY-LICENSES.md` starting with this table (every package actually shipped in the built app — `pytest`/`httpx` are test-only and excluded, `typescript`/`vite`/`@vitejs/plugin-react`/`@types/*` are frontend build-time-only and excluded):

```markdown
# Third-Party Licenses

3D Vaultkeeper is built on the following open-source software. Each
component's full license text is included below, grouped by license type
to avoid repeating identical boilerplate — see the table for which
license applies to which component.

| Component | License |
|---|---|
| fastapi | MIT |
| pydantic | MIT |
| react | MIT |
| react-dom | MIT |
| react-markdown | MIT |
| remark-gfm | MIT |
| three | MIT |
| uuid | MIT |
| serve | MIT |
| jszip | MIT |
| @emotion/react | MIT |
| @emotion/styled | MIT |
| @mui/material | MIT |
| @mui/x-tree-view | MIT |
| @react-three/drei | MIT |
| @react-three/fiber | MIT |
| uvicorn | BSD-3-Clause |
| starlette | BSD-3-Clause |
| pypdf | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| aiofiles | Apache-2.0 |
| requests | Apache-2.0 |
| lucide-react | ISC |
| occt-import-js | LGPL-2.1 (see dedicated section below) |
```

- [ ] **Step 2: Copy in the MIT license text**

Read `frontend/node_modules/react/LICENSE` and copy its full contents verbatim under a `## MIT License` heading.

- [ ] **Step 3: Copy in the BSD-3-Clause license text**

Read `backend/.venv/Lib/site-packages/starlette-0.38.5.dist-info/licenses/LICENSE.md` and copy its full contents verbatim under a `## BSD-3-Clause License` heading.

- [ ] **Step 4: Copy in the Apache-2.0 license text**

Read `backend/.venv/Lib/site-packages/requests-2.34.2.dist-info/licenses/LICENSE` and copy its full contents verbatim under a `## Apache License 2.0` heading. (If the installed `requests` version differs by the time this task runs, find its actual `.dist-info/licenses/LICENSE` path with `find backend/.venv -iname "LICENSE*" -path "*requests*"` and use that instead — the text itself is standard Apache-2.0 either way.)

- [ ] **Step 5: Copy in the ISC license text**

Read `frontend/node_modules/lucide-react/LICENSE` and copy its full contents verbatim under a `## ISC License` heading.

- [ ] **Step 6: Write the dedicated LGPL-2.1 section for occt-import-js**

Add a `## occt-import-js (LGPL-2.1)` section, before the full license text, with this exact explanatory paragraph:

```markdown
`occt-import-js` (the STEP/STP file importer) is licensed under the GNU
Lesser General Public License v2.1. Unlike the MIT/BSD/Apache-licensed
dependencies above, LGPL requires that this component remain separately
replaceable rather than compiled into a single opaque binary. This is
satisfied structurally: `occt-import-js` ships as its own WebAssembly
module (`occt-import-js.wasm`) and JavaScript loader, loaded by the
frontend as separate files rather than bundled into the backend
executable — confirmed present as standalone files in the built
`frontend/dist/assets/` output. Its source is available from the
project's own repository: https://github.com/kovacsv/occt-import-js.
```

Then read `frontend/node_modules/occt-import-js/LICENSE.md` and copy its full contents verbatim immediately after that paragraph.

- [ ] **Step 7: Commit**

```bash
git add THIRD-PARTY-LICENSES.md
git commit -m "docs: add third-party license compliance document"
```

---

### Task 6: Inno Setup installer + build script

**Files:**
- Create: `desktop/installer.iss`
- Create: `desktop/build.ps1`
- Modify: `.gitignore` (add `/desktop/installer_output/`)

**Interfaces:**
- Consumes: `desktop/dist/3D Vaultkeeper/` (Task 4's PyInstaller output), `THIRD-PARTY-LICENSES.md` (Task 5).
- Produces: `desktop/installer_output/3DVaultkeeper-Setup.exe` — the final deliverable of this whole plan.

- [ ] **Step 1: Install Inno Setup if not already present**

Run: `where iscc.exe`
If not found, run: `winget install JRSoftware.InnoSetup`
Expected: `iscc.exe` (the Inno Setup compiler) becomes available on PATH — `winget` installs it silently without requiring manual download/click-through.

- [ ] **Step 2: Create the Inno Setup script**

Create `desktop/installer.iss`:

```ini
[Setup]
AppName=3D Vaultkeeper
AppVersion=0.1.0
AppPublisher=3D Vaultkeeper
DefaultDirName={autopf}\3D Vaultkeeper
DefaultGroupName=3D Vaultkeeper
UninstallDisplayIcon={app}\3D Vaultkeeper.exe
OutputDir=installer_output
OutputBaseFilename=3DVaultkeeper-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64

[Files]
Source: "dist\3D Vaultkeeper\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs
Source: "..\THIRD-PARTY-LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\3D Vaultkeeper"; Filename: "{app}\3D Vaultkeeper.exe"
Name: "{group}\Uninstall 3D Vaultkeeper"; Filename: "{uninstallexe}"
Name: "{autodesktop}\3D Vaultkeeper"; Filename: "{app}\3D Vaultkeeper.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
```

This installs to `Program Files\3D Vaultkeeper\`, registers a Start Menu group with the app and an uninstaller, offers an optional desktop shortcut, and bundles `THIRD-PARTY-LICENSES.md` alongside the executable. It deliberately does **not** touch `%LOCALAPPDATA%\3D Vaultkeeper\` (the user's data directory, per Task 1) — Inno Setup only ever manages what's listed under `[Files]`, so uninstalling removes the program files and leaves user data untouched with no extra configuration needed.

- [ ] **Step 3: Create the build script**

Create `desktop/build.ps1`:

```powershell
# Builds a release: frontend -> PyInstaller bundle -> installer.
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "Building frontend..."
Push-Location "$repoRoot\frontend"
bun run build
Pop-Location

Write-Host "Building PyInstaller bundle..."
Push-Location "$repoRoot\desktop"
& "$repoRoot\backend\.venv\Scripts\python.exe" -m PyInstaller launcher.spec --distpath dist --workpath build --noconfirm

Write-Host "Compiling installer..."
& iscc.exe installer.iss
Pop-Location

Write-Host "Done: desktop\installer_output\3DVaultkeeper-Setup.exe"
```

- [ ] **Step 4: Add build output to `.gitignore`**

Append to `.gitignore`:

```
/desktop/installer_output/
```

(`/desktop/dist/` and `/desktop/build/` were already added in Task 4.)

- [ ] **Step 5: Run the full build and manually verify**

Run: `cd desktop && powershell -ExecutionPolicy Bypass -File build.ps1`
Expected: completes without error, produces `desktop/installer_output/3DVaultkeeper-Setup.exe`.

Run the resulting `3DVaultkeeper-Setup.exe`:
- [ ] Installer runs, offers the desktop-shortcut checkbox, completes without error.
- [ ] Start Menu has a "3D Vaultkeeper" group with the app and an uninstaller.
- [ ] Launching from the Start Menu shortcut opens the app with no console window and the real UI loads.
- [ ] `%LOCALAPPDATA%\3D Vaultkeeper\data.db` exists after first launch.
- [ ] `THIRD-PARTY-LICENSES.md` exists in the install directory (`Program Files\3D Vaultkeeper\`).
- [ ] Uninstalling via "Add or Remove Programs" removes the program files but leaves `%LOCALAPPDATA%\3D Vaultkeeper\` (and the user's data) intact.

- [ ] **Step 6: Final regression check**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: all passing — nothing in this task touches application code, only packaging.

- [ ] **Step 7: Commit**

```bash
git add desktop/installer.iss desktop/build.ps1 .gitignore
git commit -m "feat: Inno Setup installer + release build script"
```

---

## Self-Review

**Spec coverage:**
- Frozen-aware data directories → Task 1.
- FastAPI serves the frontend directly → Task 2.
- `desktop/launcher.py` (dynamic port, health check, pywebview) → Task 3.
- PyInstaller `--onedir` bundle → Task 4.
- `THIRD-PARTY-LICENSES.md` with the LGPL-2.1 dedicated section + WASM-separateness verification → Task 5.
- Inno Setup installer (Program Files, Start Menu + optional Desktop shortcut, uninstaller preserving user data) + `build.ps1` chaining the whole pipeline → Task 6.
- Out-of-scope items from the spec (activation, cross-platform, auto-update, CI/CD, code signing) are not implemented anywhere in this plan, matching the spec.
- The code-signing call-out from the spec is not an implementation task (deliberately deferred) — no plan action needed for it beyond what the spec already documents.

**Placeholder scan:** no TBD/TODO; every step has concrete code, an exact file path to read from, or an exact command.

**Type consistency:** `_frozen_data_dir()` (Task 1) returns `Optional[Path]`, consumed correctly in the `DB_PATH`/`UPLOAD_DIR` expressions in the same task. `_frontend_dist_dir()` (Task 2) returns `Path`, matching `FRONTEND_DIST: Path` used in the `is_dir()` check. `find_free_port() -> int` and `wait_for_health(url: str, timeout_seconds: float = 15.0) -> bool` (Task 3) are used with matching signatures in Task 3's own tests. The bundled-data name `frontend_dist` is identical in Task 2's `_frontend_dist_dir()` and Task 4's `launcher.spec` `datas` entry — verified by re-reading both.
