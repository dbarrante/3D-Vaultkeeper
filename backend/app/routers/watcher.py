import multiprocessing
import os
import uuid
from pathlib import Path
from typing import Callable, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_db_conn
from app.services.scan import scan_watch_folder, find_new_files
from app.services.ingestion import ingest_file

try:
    import tkinter  # noqa: F401 — presence check only; the dialog itself only ever runs in the child process
    from tkinter import filedialog  # noqa: F401

    TKINTER_AVAILABLE = True
except ImportError:
    # python:3.9-slim (this project's Docker base image) doesn't ship tkinter —
    # browse-folder degrades to "unavailable, type the path manually" there
    # rather than failing the whole app at import time.
    TKINTER_AVAILABLE = False

router = APIRouter()

BROWSE_DIALOG_TIMEOUT_SECONDS = 120


def _dialog_worker(result_queue) -> None:
    """Runs the native folder picker in its own OS process, spawned fresh —
    never imported/inherited from the parent. Must stay at module level:
    multiprocessing's Windows "spawn" start method pickles the target by
    its import path, so a nested/lambda function can't be used here.

    This isolation is the actual point of this design, confirmed necessary
    by a real report: on at least one user's machine, opening this dialog
    took down the whole backend process outright (crash-adjacent behavior
    consistent with a native-level Tcl/Tk fault, which no amount of Python
    try/except in the parent process can catch). Running it in a fully
    separate process means that failure — whatever its exact cause on a
    given machine — can only kill this one throwaway child, never the
    server handling everything else.
    """
    try:
        import tkinter
        from tkinter import filedialog as _filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = _filedialog.askdirectory()
        root.destroy()
        result_queue.put(("ok", selected or None))
    except Exception as e:
        result_queue.put(("error", str(e)))


def run_folder_dialog_isolated(
    timeout_seconds: int = BROWSE_DIALOG_TIMEOUT_SECONDS,
    worker: Callable = _dialog_worker,
) -> dict:
    """Spawns `worker` in an isolated child process and waits up to
    `timeout_seconds` for a result. `worker` is swappable so tests can
    substitute a fast, deterministic stand-in instead of a real GUI dialog —
    production code never passes it, always using the real `_dialog_worker`.
    """
    if not TKINTER_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Folder browser is unavailable on this server — enter the path manually.",
        )

    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue()
    process = ctx.Process(target=worker, args=(result_queue,), daemon=True)
    process.start()
    process.join(timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(5)
        raise HTTPException(
            status_code=504,
            detail="Folder browser timed out — enter the path manually.",
        )

    try:
        status, value = result_queue.get_nowait()
    except Exception:
        # Process exited (crashed, or was killed) without ever putting a
        # result on the queue — exactly the case this whole design exists
        # to survive without taking the server down with it.
        raise HTTPException(
            status_code=503,
            detail="Folder browser closed unexpectedly (it may have crashed) — enter the path manually.",
        )

    if status == "error":
        raise HTTPException(
            status_code=503,
            detail=f"Folder browser failed: {value} — enter the path manually.",
        )

    return {"path": value}


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
