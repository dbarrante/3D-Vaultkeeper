import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_db_conn
from app.services.scan import scan_watch_folder, find_new_files
from app.services.ingestion import ingest_file

try:
    import tkinter  # noqa: F401 — presence check only; the real check happens in the subprocess

    TKINTER_AVAILABLE = True
except ImportError:
    # python:3.9-slim (this project's Docker base image) doesn't ship tkinter —
    # browse-folder degrades to "unavailable, type the path manually" there
    # rather than failing the whole app at import time.
    TKINTER_AVAILABLE = False

router = APIRouter()

BROWSE_DIALOG_TIMEOUT_SECONDS = 120

# Deliberately a bare, self-contained script with zero relation to the app
# package — no `import app`, no FastAPI, nothing. A first version of this
# feature used multiprocessing.Process instead of subprocess.run, and that
# broke a real user's server: on Windows, multiprocessing's "spawn" start
# method bootstraps a child by re-invoking the *exact command line the
# parent process was launched with* — which, since this app runs as
# `python -m uvicorn app.main:app --host ... --port 8998`, meant clicking
# Browse spawned a second full copy of the server that immediately crashed
# trying to rebind the same port, taking the real one down with it.
# Confirmed live: the process tree showed a second `-m uvicorn app.main:app`
# process as a direct child of the real one. subprocess.run with an explicit
# `-c <script>` has no such entry point to re-invoke in a normal
# `python -m uvicorn ...` launch — it just runs exactly the script given to
# it, nothing else.
#
# This reasoning does NOT hold for the packaged desktop build (see
# docs/superpowers/plans/2026-08-03-local-installer.md). There,
# `sys.executable` is the frozen app's own .exe (PyInstaller), which
# ignores the `-c <script>` argument and re-launches the whole application
# instead of a bare interpreter — the exact same class of bug as the
# multiprocessing one above, just triggered a different way, and it used
# to spawn a second full app instance (its own server, its own window, a
# second writer against the same SQLite DB) that either hung for the full
# BROWSE_DIALOG_TIMEOUT_SECONDS or crashed fast with "closed unexpectedly"
# depending on whether it won the race to bind a WebView2 user-data folder
# already held by the first. Confirmed live, both ways.
#
# Fixed by giving the frozen .exe a second, lightweight entry point instead
# of trying to run it as a bare interpreter: desktop/launcher.py checks for
# a hidden `--browse-folder-worker <result-file>` argv, and — if present —
# branches to _run_browse_folder_worker() before importing uvicorn/webview
# or touching app.main at all, so the re-invoked process never becomes a
# second copy of the app; it only ever opens the tkinter dialog and exits.
# _run_frozen_folder_dialog() below invokes that flag instead of `-c
# <script>` whenever sys.frozen is true. The dev/Docker path above
# (`-c <script>` against a real interpreter) is untouched.
DIALOG_SCRIPT = """
import sys
try:
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    selected = filedialog.askdirectory()
    root.destroy()
    sys.stdout.write("OK:" + (selected or ""))
except Exception as e:
    sys.stdout.write("ERROR:" + str(e))
    sys.exit(1)
"""


def _run_frozen_folder_dialog(timeout_seconds: int) -> dict:
    """Frozen-build equivalent of the `-c <script>` path above. Re-invokes
    this same .exe with `--browse-folder-worker <result-file>`, which
    desktop/launcher.py's main() intercepts before doing any of its normal
    startup (see the comment above DIALOG_SCRIPT). Passes the result via a
    temp file rather than stdout: PyInstaller's windowed (console=False)
    bootloader does not reliably expose a capturable stdout pipe when the
    same windowed exe is re-invoked as a child process, so a file avoids
    that platform uncertainty entirely.
    """
    import tempfile

    fd, result_path = tempfile.mkstemp(prefix="vk-browse-", suffix=".txt")
    os.close(fd)
    try:
        try:
            result = subprocess.run(
                [sys.executable, "--browse-folder-worker", result_path],
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=504,
                detail="Folder browser timed out — enter the path manually.",
            )

        output = Path(result_path).read_text(encoding="utf-8", errors="replace").strip()
        if result.returncode != 0 or not output.startswith("OK:"):
            raise HTTPException(
                status_code=503,
                detail="Folder browser closed unexpectedly (it may have crashed) — enter the path manually.",
            )

        path = output[len("OK:"):]
        return {"path": path or None}
    finally:
        try:
            os.remove(result_path)
        except OSError:
            pass


def run_folder_dialog_isolated(timeout_seconds: int = BROWSE_DIALOG_TIMEOUT_SECONDS) -> dict:
    """Runs DIALOG_SCRIPT (dev/Docker) or the frozen-build worker (packaged
    desktop build) in a brand-new, throwaway process and waits up to
    `timeout_seconds` for it to finish. Whatever goes wrong inside that
    process — a crash, a hang, tkinter being broken on this machine — is
    contained to that one throwaway process and reported back as a clean
    HTTP error, never taking the real server down with it.
    """
    if getattr(sys, "frozen", False):
        return _run_frozen_folder_dialog(timeout_seconds)
    if not TKINTER_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Folder browser is unavailable on this server — enter the path manually.",
        )

    try:
        result = subprocess.run(
            [sys.executable, "-c", DIALOG_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail="Folder browser timed out — enter the path manually.",
        )

    output = result.stdout.strip()
    if result.returncode != 0 or not output.startswith("OK:"):
        raise HTTPException(
            status_code=503,
            detail="Folder browser closed unexpectedly (it may have crashed) — enter the path manually.",
        )

    path = output[len("OK:"):]
    return {"path": path or None}


class WatchFolderData(BaseModel):
    path: str
    folderId: str
    frequencyMinutes: Optional[int] = 60


class DriveScanRequest(BaseModel):
    paths: List[str]
    folderId: str


def row_to_watch_folder(row) -> dict:
    return {
        "id": row["id"],
        "path": row["path"],
        "folderId": row["folderId"],
        "frequencyMinutes": row["frequencyMinutes"],
        "lastScanAt": row["lastScanAt"],
        "enabled": bool(row["enabled"]),
    }


@router.post("/api/browse-folder")
def browse_folder():
    """Opens a native OS folder picker on the machine running the backend.
    Only meaningful because this app is self-hosted and run on your own
    machine — the dialog appears wherever the *server* process runs, not on
    whatever device the browser tab is open on. Runs in an isolated child
    process (see run_folder_dialog_isolated) so nothing it does — crash,
    hang, or otherwise — can take down the server handling everything else.
    """
    return run_folder_dialog_isolated()


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
                    pickup_sidecar_notes=True,
                )
                already_seen.add(str(file_path))  # don't double-ingest if two paths overlap
                ingested += 1
            except Exception:
                continue
    return {"ingested": ingested}
