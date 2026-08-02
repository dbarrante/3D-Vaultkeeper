import os
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_db_conn
from app.services.scan import scan_watch_folder, find_new_files
from app.services.ingestion import ingest_file

router = APIRouter()


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
