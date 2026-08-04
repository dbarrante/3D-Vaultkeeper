import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.file_view_ops import (
    rewrite_affected_paths,
    resolve_storage_mode_for_path,
    validate_destination,
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
