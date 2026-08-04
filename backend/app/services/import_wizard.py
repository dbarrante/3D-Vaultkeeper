import os
import re
import shutil
from pathlib import Path

from app.services.scan import SUPPORTED_EXTENSIONS
from app.db import get_db_conn, UPLOAD_DIR
from app.services.ingestion import ingest_file


def build_tree(root: Path) -> dict:
    """Read-only recursive walk of a raw directory for the Import Wizard's
    left pane. No DB writes and no files are touched -- this never
    modifies anything, it only describes what's already there.
    """
    folders = []
    files = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name.lower()):
        if entry.is_dir():
            folders.append(build_tree(entry))
        elif entry.is_file():
            files.append({
                "name": entry.name,
                "path": str(entry),
                "isModel": entry.suffix.lower() in SUPPORTED_EXTENSIONS,
                "size": entry.stat().st_size,
            })
    return {"name": root.name, "path": str(root), "folders": folders, "files": files}


_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_path_segment(name: str) -> str:
    """Make a folder name safe to use as one segment of a real filesystem
    path. Folder names are free-text and user-renameable, but this is the
    first feature that turns them into actual on-disk directories -- a
    name with illegal characters, a reserved Windows device name, or
    trailing dots/spaces must not corrupt or misdirect the destination path.
    """
    cleaned = _ILLEGAL_CHARS.sub("_", name).strip(" .")
    if not cleaned:
        cleaned = "_"
    if cleaned.upper() in _RESERVED_NAMES:
        cleaned = f"{cleaned}_"
    return cleaned[:100]


def folder_disk_path(folder_id: str) -> str:
    """Full logical path for a folder, root-to-leaf, each segment
    sanitized -- e.g. "Tanks" under "Vehicles" returns "Vehicles/Tanks".
    Used as ingest_file's dest_subpath so wizard-imported files land in
    real subdirectories mirroring the logical folder the user placed
    them in. Raises ValueError if folder_id itself doesn't exist -- an
    orphaned *ancestor* further up an otherwise-valid chain is tolerated
    (uses whatever resolved so far), but the placement's own target
    folder must be real.
    """
    conn = get_db_conn()
    try:
        segments = []
        current_id = folder_id
        is_first = True
        while current_id is not None:
            row = conn.execute(
                "SELECT name, parentId FROM folders WHERE id=?", (current_id,)
            ).fetchone()
            if row is None:
                if is_first:
                    raise ValueError(f"Folder not found: {folder_id}")
                break
            segments.append(sanitize_path_segment(row["name"]))
            current_id = row["parentId"]
            is_first = False
        return os.path.join(*reversed(segments)) if segments else ""
    finally:
        conn.close()


def expand_placement(source_path: str, is_folder: bool) -> list:
    """A loose-file placement is itself; a folder placement is every file
    found by walking it recursively -- this is what makes dragging one
    folder bring every file inside it along, without the user having to
    select each file individually. Raises if the folder no longer exists
    or any subdirectory inside it can't be read, rather than silently
    returning an empty list indistinguishable from a genuinely empty
    (but fully readable) folder -- callers should treat a raise here as
    "nothing in this placement was moved, safe to retry once fixed,"
    not attempt to salvage a partial listing.
    """
    if not is_folder:
        return [Path(source_path)]
    root = Path(source_path)
    if not root.is_dir():
        raise FileNotFoundError(f"Folder no longer exists: {source_path}")
    found = []
    walk_errors = []
    for dirpath, _dirnames, filenames in os.walk(source_path, onerror=walk_errors.append):
        for fname in filenames:
            found.append(Path(dirpath) / fname)
    if walk_errors:
        raise OSError(f"Could not fully read {source_path}: {walk_errors[0]}")
    return found


def commit_placement_file(file_path: Path, target_folder_id: str) -> dict:
    """Move one file into its resolved destination, independently of any
    other file in the batch -- a failure here (locked file, permission
    denied, vanished since staging) must never prevent a sibling file, in
    this or any other placement, from being moved.
    """
    is_model = file_path.suffix.lower() in SUPPORTED_EXTENSIONS
    try:
        dest_subpath = folder_disk_path(target_folder_id)
        if is_model:
            ingest_file(
                str(file_path),
                folder_id=target_folder_id,
                original_filename=file_path.name,
                move=True,
                dest_subpath=dest_subpath,
            )
        else:
            dest_dir = Path(UPLOAD_DIR) / dest_subpath
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / file_path.name
            counter = 1
            while dest_file.exists():
                dest_file = dest_dir / f"{file_path.stem}_{counter}{file_path.suffix}"
                counter += 1
            shutil.move(str(file_path), str(dest_file))
        return {"sourcePath": str(file_path), "status": "ok", "isModel": is_model}
    except Exception as exc:
        return {"sourcePath": str(file_path), "status": "error", "error": str(exc), "isModel": is_model}
