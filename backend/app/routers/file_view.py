import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_db_conn, MANUAL_DIR, UPLOAD_DIR
from app.services.file_view_ops import (
    ensure_unambiguous_path,
    is_self_nested_move,
    path_conflicts_with_watch_root,
    rewrite_affected_paths,
    rewrite_tracked_folder_paths,
    resolve_storage_mode_for_path,
    validate_destination,
    find_affected_models,
    find_affected_tracked_folders,
)
from app.services.import_wizard import sanitize_path_segment

router = APIRouter(prefix="/api/file-view", tags=["file-view"])

# Shared by rename/move/delete: all three would orphan a watch_folders.path row
# in exactly the same way, so they say so in exactly the same words.
WATCH_ROOT_CONFLICT_DETAIL = (
    "Refusing to {verb} a folder that is, or contains, a watched folder. "
    "Remove it from Watch Folders first if you want to do this."
)

SELF_NESTED_DETAIL = "Cannot move a folder into itself or one of its own subfolders."


class FolderRenameRequest(BaseModel):
    path: str
    newName: str


@router.post("/folder/rename")
def rename_folder(body: FolderRenameRequest):
    source = Path(body.path)
    if not source.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {body.path}")
    # Absoluteness is checked before anything reasons about source.resolve(),
    # since a cwd-relative source makes every guard below reason about the
    # wrong directory entirely.
    try:
        ensure_unambiguous_path(source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    destination = source.parent / body.newName
    if destination.exists():
        raise HTTPException(status_code=409, detail=f"A folder already exists at {destination}")
    if is_self_nested_move(source, destination):
        raise HTTPException(status_code=400, detail=SELF_NESTED_DETAIL)
    try:
        storage_mode = resolve_storage_mode_for_path(source)
        validate_destination(str(destination), storage_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if path_conflicts_with_watch_root(source):
        raise HTTPException(status_code=400, detail=WATCH_ROOT_CONFLICT_DETAIL.format(verb="rename"))
    shutil.move(str(source), str(destination))
    rewrite_affected_paths(str(source), str(destination))
    rewrite_tracked_folder_paths(str(source), str(destination))
    return {"path": str(destination)}


class FolderMoveRequest(BaseModel):
    sourcePath: str
    targetPath: str


@router.post("/folder/move")
def move_folder(body: FolderMoveRequest):
    source = Path(body.sourcePath)
    if not source.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {body.sourcePath}")
    try:
        ensure_unambiguous_path(source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    destination = Path(body.targetPath)
    if destination.exists():
        raise HTTPException(status_code=409, detail=f"A folder already exists at {destination}")
    if is_self_nested_move(source, destination):
        raise HTTPException(status_code=400, detail=SELF_NESTED_DETAIL)
    try:
        storage_mode = resolve_storage_mode_for_path(source)
        validate_destination(str(destination), storage_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if path_conflicts_with_watch_root(source):
        raise HTTPException(status_code=400, detail=WATCH_ROOT_CONFLICT_DETAIL.format(verb="move"))
    # Only now, with every guard cleared, is it safe to create intermediate
    # directories -- a rejected move must never leave a stray one behind.
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    rewrite_affected_paths(str(source), str(destination))
    rewrite_tracked_folder_paths(str(source), str(destination))
    return {"path": str(destination)}


class FolderDeleteRequest(BaseModel):
    path: str


@router.delete("/folder")
def delete_folder(body: FolderDeleteRequest):
    target = Path(body.path)
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {body.path}")

    # Before resolved is computed, since Path("C:").resolve() silently yields
    # the backend's cwd rather than the drive root -- which is exactly why the
    # drive-root guard below never fired for the bare "C:" node the File-mode
    # tree emits.
    try:
        ensure_unambiguous_path(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    resolved = target.resolve()
    if resolved.parent == resolved:
        raise HTTPException(status_code=400, detail="Refusing to delete a drive root.")

    # Covers both "this IS a watch root" (the original exact-match check) and
    # "this CONTAINS a watch root", which previously slipped through and
    # orphaned the nested watch folder's row.
    if path_conflicts_with_watch_root(target):
        raise HTTPException(status_code=400, detail=WATCH_ROOT_CONFLICT_DETAIL.format(verb="delete"))

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

    tracked = find_affected_tracked_folders(str(target))
    if tracked:
        conn = get_db_conn()
        try:
            conn.executemany(
                "DELETE FROM file_view_tracked_folders WHERE path=?",
                [(p,) for p in tracked],
            )
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


class FolderCreateRequest(BaseModel):
    parentPath: Optional[str] = None
    name: str


@router.post("/folder")
def create_folder(body: FolderCreateRequest):
    parent_path = body.parentPath if body.parentPath else str(UPLOAD_DIR)
    try:
        ensure_unambiguous_path(parent_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    parent = Path(parent_path)
    if not parent.is_dir():
        raise HTTPException(status_code=404, detail=f"Parent folder not found: {parent_path}")

    try:
        storage_mode = resolve_storage_mode_for_path(parent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    sanitized_name = sanitize_path_segment(body.name)
    new_dir = parent / sanitized_name
    if new_dir.exists():
        raise HTTPException(status_code=409, detail=f"A folder already exists at {new_dir}")

    # Belt-and-suspenders with rename_folder/move_folder, which both
    # re-validate their actual destination rather than trusting the parent's
    # containment alone. Not currently reachable any other way here --
    # sanitize_path_segment already strips "/", "\\", ":" and leading/trailing
    # dots/spaces from body.name, so new_dir can only ever be a direct child of
    # an already-validated parent -- but keeping this check means a future
    # change to sanitize_path_segment or parent resolution can't silently
    # reopen an escape here without also tripping this guard.
    try:
        validate_destination(str(new_dir), storage_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        new_dir.mkdir()
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"A folder already exists at {new_dir}")

    # Normalized before it's ever stored or returned so this row's path always
    # matches the form find_affected_tracked_folders/rewrite_tracked_folder_paths
    # compare against (they key DELETE/UPDATE on the exact, normalized string,
    # not the row id). A parentPath containing an uncollapsed ".." segment that
    # still resolves inside an allowed root would otherwise get stored verbatim
    # here, and then never match on a later rename/move/delete -- silently
    # orphaning the row from all future sync. Physical directory creation above
    # is untouched: normalizing the string doesn't change where mkdir() already
    # created the directory on disk.
    normalized_new_dir = os.path.normpath(str(new_dir))

    conn = get_db_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO file_view_tracked_folders(path) VALUES (?)",
            (normalized_new_dir,),
        )
        conn.commit()
    finally:
        conn.close()

    return {"path": normalized_new_dir}


@router.get("/tracked-folders")
def get_tracked_folders():
    conn = get_db_conn()
    try:
        rows = conn.execute("SELECT path FROM file_view_tracked_folders").fetchall()
    finally:
        conn.close()
    return {"paths": [row["path"] for row in rows]}


class RevealRequest(BaseModel):
    path: str


@router.post("/reveal")
def reveal_in_explorer(body: RevealRequest):
    # This project's recommended deployment is Docker (Linux), where
    # explorer.exe doesn't exist and Popen would raise an uncaught
    # FileNotFoundError -> 500. Mirrors watcher.py's TKINTER_AVAILABLE /
    # run_folder_dialog_isolated precedent of reporting a clean 503 for a
    # platform-unavailable feature instead of crashing.
    if not sys.platform.startswith("win"):
        raise HTTPException(
            status_code=503,
            detail="Opening File Explorer is only available when the server runs on Windows.",
        )

    # Same path-safety convention as rename/move/create_folder above: a
    # relative body.path would otherwise silently resolve against the
    # backend's cwd instead of being rejected.
    try:
        ensure_unambiguous_path(body.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    target = Path(body.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {body.path}")
    if target.is_dir():
        subprocess.Popen(["explorer", str(target)])
    else:
        subprocess.Popen(["explorer", "/select,", str(target)])
    return {"ok": True}
