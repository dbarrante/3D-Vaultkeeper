import os
import uuid
import json
import shutil
from typing import Optional, List

from app.db import get_db_conn, now_ms, UPLOAD_DIR
from app.services.sidecar_notes import find_sidecar_notes


def ingest_file(
    source_path: str,
    folder_id: str,
    original_filename: str,
    tags: Optional[List[str]] = None,
    thumbnail: Optional[str] = None,
    move: bool = False,
    record_source: bool = False,
    pickup_sidecar_notes: bool = False,
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

    record_source=True additionally persists source_path into models.sourcePath,
    so a later scan of the same folder can tell this file was already ingested
    and skip it. Only meaningful with move=False (the watcher's case) — recording
    a path that ingest_file itself just deleted via move=True would record a
    path that no longer points at anything.

    pickup_sidecar_notes=True (#5) looks for a same-basename .txt or .pdf next
    to source_path — moving or copying the model file never touches that
    sibling, so lookup order relative to the move/copy below doesn't matter —
    and uses its text as the model's initial description. Off by default:
    upload_model's source_path is a disposable temp file with no meaningful
    siblings, so there's nothing useful to look for there.
    """
    mid = str(uuid.uuid4())
    ext = os.path.splitext(original_filename)[1] or ".stl"
    dest_path = os.path.join(UPLOAD_DIR, f"{mid}{ext}")

    description = ""
    if pickup_sidecar_notes:
        notes = find_sidecar_notes(source_path)
        if notes:
            description = notes

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
        "description": description,
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
