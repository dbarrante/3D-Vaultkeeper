import os
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_db_conn, MANUAL_DIR, UPLOAD_DIR
from app.services.file_view_ops import (
    rewrite_affected_paths,
    resolve_storage_mode_for_path,
    validate_destination,
    find_affected_models,
)

router = APIRouter(prefix="/api/file-view", tags=["file-view"])


class FolderRenameRequest(BaseModel):
    path: str
    newName: str


@router.post("/folder/rename")
def rename_folder(body: FolderRenameRequest):
    source = Path(body.path)
    if not source.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {body.path}")
    destination = source.parent / body.newName
    if destination.exists():
        raise HTTPException(status_code=409, detail=f"A folder already exists at {destination}")
    source_resolved = source.resolve()
    destination_resolved = destination.resolve()
    if destination_resolved == source_resolved or source_resolved in destination_resolved.parents:
        raise HTTPException(status_code=400, detail="Cannot move a folder into itself or one of its own subfolders.")
    try:
        storage_mode = resolve_storage_mode_for_path(source)
        validate_destination(str(destination), storage_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    shutil.move(str(source), str(destination))
    rewrite_affected_paths(str(source), str(destination))
    return {"path": str(destination)}


class FolderMoveRequest(BaseModel):
    sourcePath: str
    targetPath: str


@router.post("/folder/move")
def move_folder(body: FolderMoveRequest):
    source = Path(body.sourcePath)
    if not source.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {body.sourcePath}")
    destination = Path(body.targetPath)
    if destination.exists():
        raise HTTPException(status_code=409, detail=f"A folder already exists at {destination}")
    source_resolved = source.resolve()
    destination_resolved = destination.resolve()
    if destination_resolved == source_resolved or source_resolved in destination_resolved.parents:
        raise HTTPException(status_code=400, detail="Cannot move a folder into itself or one of its own subfolders.")
    try:
        storage_mode = resolve_storage_mode_for_path(source)
        validate_destination(str(destination), storage_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    rewrite_affected_paths(str(source), str(destination))
    return {"path": str(destination)}


class FolderDeleteRequest(BaseModel):
    path: str


@router.delete("/folder")
def delete_folder(body: FolderDeleteRequest):
    target = Path(body.path)
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {body.path}")

    resolved = target.resolve()
    if resolved.parent == resolved:
        raise HTTPException(status_code=400, detail="Refusing to delete a drive root.")

    conn = get_db_conn()
    try:
        watch_roots = {
            Path(row["path"]).resolve()
            for row in conn.execute("SELECT path FROM watch_folders").fetchall()
        }
    finally:
        conn.close()
    if resolved in watch_roots:
        raise HTTPException(
            status_code=400,
            detail="Refusing to delete a watched folder's root. Remove it from Watch Folders first if you really want to delete it.",
        )

    if resolved == Path(UPLOAD_DIR).resolve():
        raise HTTPException(status_code=400, detail="Refusing to delete the entire managed library folder.")

    try:
        resolve_storage_mode_for_path(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    affected = find_affected_models(str(target))
    conn = get_db_conn()
    deleted = 0
    try:
        cur = conn.cursor()
        for row in affected:
            try:
                model_id = row["id"]
                fp = row["filePath"] if "filePath" in row.keys() else None
                if fp and os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
                manual_path = MANUAL_DIR / f"{model_id}.md"
                if manual_path.exists():
                    manual_path.unlink()
                cur.execute("DELETE FROM models WHERE id=?", (model_id,))
                deleted += 1
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()

    try:
        shutil.rmtree(target)
        directory_removed = True
    except OSError:
        # Model rows and their files are already gone at this point -- that part of
        # the operation genuinely succeeded. But if rmtree hit something it couldn't
        # remove (e.g. a file locked by another process on Windows), silently
        # swallowing that with ignore_errors=True would report full success while
        # the directory (or part of it) is still sitting on disk with no DB record
        # pointing at it anymore. Surface the partial failure instead.
        directory_removed = not target.exists()

    return {"deletedModels": deleted, "path": str(target), "directoryRemoved": directory_removed}
