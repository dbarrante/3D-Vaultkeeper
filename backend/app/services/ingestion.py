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
    to hand off here (upload_model; later, the acquisition drain worker's
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
