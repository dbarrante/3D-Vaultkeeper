import os
import json
import base64
import tempfile
import shutil
import uuid
from typing import Optional, List
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.db import get_db_conn, row_to_model, save_upload_file, now_ms, UPLOAD_DIR, MANUAL_DIR
from app.services.ingestion import ingest_file
from app.services.file_view_ops import validate_destination
from app.services.import_wizard import folder_disk_path

router = APIRouter()


def _resolve_copy_mode_file(model_id: str, file_path: Optional[str]) -> Optional[str]:
    """Locate a copy-mode model's physical file. Prefers filePath (the
    real current location -- correct for both flat legacy uploads and
    wizard-imported files placed in real subdirectories) and falls back
    to the historical flat os.listdir(UPLOAD_DIR) + id-prefix match only
    if filePath is missing or stale. Guards against an empty model_id
    matching every filename via startswith(""), and against the flat
    scan returning a directory (UPLOAD_DIR now contains real
    subdirectories: manuals/, and every logical folder the wizard
    creates).
    """
    if not model_id:
        return None
    if file_path and os.path.exists(file_path):
        return file_path
    for fname in os.listdir(UPLOAD_DIR):
        candidate = os.path.join(UPLOAD_DIR, fname)
        if fname.startswith(model_id) and os.path.isfile(candidate):
            return candidate
    return None


def get_model_info(modelId):
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
        cur.execute("SELECT * FROM models WHERE folderId=? AND removedAt IS NULL", (folderId,))
    else:
        cur.execute("SELECT * FROM models WHERE removedAt IS NULL")
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


@router.patch("/api/models/{model_id}")
def update_model(model_id: str, updates: dict):
    conn = get_db_conn()
    cur = conn.cursor()
    m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    if not m:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")

    allowed = ["name", "folderId", "tags", "description", "thumbnail", "author", "sourceUrl", "category", "colorCount", "sliceSettings"]
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
def delete_model(model_id: str, deleteFile: bool = False):
    conn = get_db_conn()
    cur = conn.cursor()
    m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    if not m:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")
    if m["storageMode"] == "reference" and not deleteFile:
        # Tombstone instead of hard-delete: the real file is still sitting in its
        # watch folder, and hard-deleting the row would make the next scan re-add
        # it as a brand-new entry (new id, stripped of tags/description/manual).
        # sourcePath stays intact so scan_watch_folder's dedup still recognizes it.
        cur.execute("UPDATE models SET removedAt=? WHERE id=?", (now_ms(), model_id))
        conn.commit()
        conn.close()
        return {"ok": True}
    resolved = _resolve_copy_mode_file(model_id, m["filePath"] if "filePath" in m.keys() else None)
    if resolved:
        try:
            os.remove(resolved)
        except Exception:
            pass
    if m["storageMode"] == "reference" and deleteFile and m["sourcePath"]:
        try:
            os.remove(m["sourcePath"])
        except OSError:
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


class LocationUpdate(BaseModel):
    newPath: str


@router.patch("/api/models/{model_id}/location")
def update_model_location(model_id: str, body: LocationUpdate):
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="Model not found")

        storage_mode = m["storageMode"] if "storageMode" in m.keys() else "copy"
        if storage_mode == "reference":
            current_path = m["sourcePath"]
        else:
            current_path = _resolve_copy_mode_file(model_id, m["filePath"] if "filePath" in m.keys() else None)
        if not current_path or not os.path.exists(current_path):
            raise HTTPException(status_code=404, detail="File not found on disk")

        try:
            destination = validate_destination(body.newPath, storage_mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if destination.exists():
            raise HTTPException(status_code=409, detail=f"A file already exists at {destination}")

        # Use normpath for persistence to match raw-path convention in the codebase,
        # not resolve() which can drift from what scan_watch_folder produces
        persisted_path = os.path.normpath(body.newPath)

        destination.parent.mkdir(parents=True, exist_ok=True)

        shutil.move(current_path, persisted_path)

        try:
            if storage_mode == "reference":
                cur.execute(
                    "UPDATE models SET filePath=?, sourcePath=? WHERE id=?",
                    (persisted_path, persisted_path, model_id),
                )
            else:
                cur.execute("UPDATE models SET filePath=? WHERE id=?", (persisted_path, model_id))
            conn.commit()
        except Exception:
            # Rollback: move file back to original location if DB write fails
            # to preserve atomicity (either both file and DB update succeed, or neither)
            try:
                shutil.move(persisted_path, current_path)
            except Exception:
                pass
            raise

        row = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
        return row_to_model(row)
    finally:
        conn.close()


@router.post("/api/models/{model_id}/duplicate")
def duplicate_model(model_id: str):
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="Model not found")

        storage_mode = m["storageMode"] if "storageMode" in m.keys() else "copy"
        if storage_mode == "reference":
            current_path = m["sourcePath"]
        else:
            current_path = _resolve_copy_mode_file(model_id, m["filePath"] if "filePath" in m.keys() else None)
        if not current_path or not os.path.exists(current_path):
            raise HTTPException(status_code=404, detail="File not found on disk")

        dest_subpath = folder_disk_path(m["folderId"])
        # Strip root folder if present: folder_disk_path includes the root folder,
        # but disk paths should only include child folders
        parts = dest_subpath.split(os.path.sep) if dest_subpath else []
        if len(parts) > 1:
            # Check if the first part is a root folder (has no parent)
            root_check = cur.execute(
                "SELECT parentId FROM folders WHERE name=?",
                (parts[0],)
            ).fetchone()
            if root_check and root_check["parentId"] is None:
                # This is a root folder, skip it
                dest_subpath = os.path.sep.join(parts[1:])

        dest_dir = Path(UPLOAD_DIR) / dest_subpath
        dest_dir.mkdir(parents=True, exist_ok=True)
        new_id = str(uuid.uuid4())
        ext = os.path.splitext(current_path)[-1]
        new_path = dest_dir / f"{new_id}{ext}"
        shutil.copy2(current_path, new_path)

        cur.execute(
            "INSERT INTO models "
            "(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,manual,author,"
            " sourceUrl,category,colorCount,sliceSettings,sourcePath,storageMode,filePath) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id, m["name"], m["folderId"], f"/api/models/{new_id}/download",
                os.path.getsize(new_path), now_ms(), m["tags"], m["description"], m["thumbnail"], None,
                m["author"] if "author" in m.keys() else None,
                m["sourceUrl"] if "sourceUrl" in m.keys() else None,
                m["category"] if "category" in m.keys() else None,
                m["colorCount"] if "colorCount" in m.keys() else None,
                m["sliceSettings"] if "sliceSettings" in m.keys() else None,
                None, "copy", str(new_path),
            ),
        )
        conn.commit()
        row = cur.execute("SELECT * FROM models WHERE id=?", (new_id,)).fetchone()
        return row_to_model(row)
    finally:
        conn.close()


@router.get("/api/models/{model_id}/download")
def download_model(model_id: str):
    m_info = get_model_info(model_id)
    if m_info["storageMode"] == "reference":
        source_path = m_info["sourcePath"]
        if source_path and os.path.exists(source_path):
            return FileResponse(
                source_path,
                media_type="application/octet-stream",
                filename=m_info["name"],
            )
        raise HTTPException(
            status_code=404,
            detail=f"File not found at {source_path} — it may have been moved or deleted outside STLVault.",
        )
    resolved = _resolve_copy_mode_file(model_id, m_info.get("filePath"))
    if resolved:
        return FileResponse(resolved, media_type="application/octet-stream", filename=m_info["name"])
    raise HTTPException(status_code=404, detail="File not found")


@router.post("/api/models/bulk-delete")
def bulk_delete(payload: dict):
    ids = payload.get("ids", [])
    conn = get_db_conn()
    cur = conn.cursor()
    for mid in ids:
        row = cur.execute("SELECT storageMode, filePath FROM models WHERE id=?", (mid,)).fetchone()
        if row and row["storageMode"] == "reference":
            # Tombstone reference-mode models: sourcePath stays intact so a future
            # watch-folder scan still recognizes the file as already ingested.
            cur.execute("UPDATE models SET removedAt=? WHERE id=?", (now_ms(), mid))
            continue
        resolved = _resolve_copy_mode_file(mid, row["filePath"] if row else None)
        if resolved:
            try:
                os.remove(resolved)
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
        # Skip tombstoned (removed) models — they're hidden from the UI and should not be acted upon
        row = cur.execute("SELECT removedAt FROM models WHERE id=?", (mid,)).fetchone()
        if row and row["removedAt"] is not None:
            continue
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
        row = cur.execute("SELECT tags, removedAt FROM models WHERE id=?", (mid,)).fetchone()
        if not row:
            continue
        # Skip tombstoned (removed) models — they're hidden from the UI and should not be acted upon
        if row["removedAt"] is not None:
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
    if m["storageMode"] == "reference":
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="This model references a file on disk — replacing its content isn't supported here. Delete it and re-add the file directly in its folder instead.",
        )
    resolved = _resolve_copy_mode_file(model_id, m["filePath"] if "filePath" in m.keys() else None)
    if resolved:
        try:
            os.remove(resolved)
        except Exception:
            pass
    filename_str = file.filename or ".stl"
    ext = os.path.splitext(filename_str)[-1] or ".stl"
    filename = f"{model_id}{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    size = save_upload_file(file, path)
    cur.execute(
        "UPDATE models SET url=?, size=?, thumbnail=?, filePath=? WHERE id=?",
        (f"/api/models/{model_id}/download", size, thumbnail, path, model_id),
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
