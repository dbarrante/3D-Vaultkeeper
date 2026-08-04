import os
from pathlib import Path

from app.db import get_db_conn, UPLOAD_DIR


def validate_destination(new_path: str, storage_mode: str) -> Path:
    """Ensure a rename/move destination resolves inside an allowed root.

    Copy-mode files may only land under UPLOAD_DIR (the app's managed
    storage) -- .resolve() neutralizes any ".." traversal in new_path
    before the containment check runs, so a crafted destination can't
    escape UPLOAD_DIR even if a caller doesn't sanitize it first.

    Reference-mode (watch-folder) files may land under any currently
    configured watch_folders.path. Moving one outside every watched root
    would mean the app permanently loses track of it -- nothing would
    ever scan that location again -- so that's rejected rather than
    silently allowed.
    """
    resolved = Path(new_path).resolve()

    if storage_mode == "copy":
        upload_root = Path(UPLOAD_DIR).resolve()
        if resolved == upload_root or upload_root in resolved.parents:
            return resolved
        raise ValueError(
            f"Destination must be inside the managed library folder: {upload_root}"
        )

    conn = get_db_conn()
    try:
        watch_roots = [
            Path(row["path"]).resolve()
            for row in conn.execute("SELECT path FROM watch_folders").fetchall()
        ]
    finally:
        conn.close()
    for root in watch_roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise ValueError(
        "A linked file has to stay somewhere the app is watching -- "
        "this destination isn't inside any watched folder."
    )


def resolve_storage_mode_for_path(path: Path) -> str:
    """Determine which containment rule applies to a real directory: "copy"
    if it's under UPLOAD_DIR, "reference" if it's under some configured
    watch_folders.path. Raises ValueError if it's under neither -- this
    should only happen if the path was never a legitimate File-mode node
    to begin with.
    """
    resolved = path.resolve()
    upload_root = Path(UPLOAD_DIR).resolve()
    if resolved == upload_root or upload_root in resolved.parents:
        return "copy"
    conn = get_db_conn()
    try:
        watch_roots = [
            Path(row["path"]).resolve()
            for row in conn.execute("SELECT path FROM watch_folders").fetchall()
        ]
    finally:
        conn.close()
    for root in watch_roots:
        if resolved == root or root in resolved.parents:
            return "reference"
    raise ValueError(f"{path} is not inside the managed library or any watched folder")


def find_affected_models(dir_path: str) -> list:
    """Every model row whose current file (filePath) lives at or under
    dir_path. Filters in Python rather than SQL LIKE to avoid needing to
    escape "%"/"_" wildcard characters that can legally appear in a real
    folder name.
    """
    conn = get_db_conn()
    try:
        rows = conn.execute("SELECT * FROM models").fetchall()
    finally:
        conn.close()
    prefix = os.path.normpath(dir_path)
    affected = []
    for row in rows:
        fp = row["filePath"] if "filePath" in row.keys() else None
        if not fp:
            continue
        norm = os.path.normpath(fp)
        if norm == prefix or norm.startswith(prefix + os.sep):
            affected.append(row)
    return affected


def rewrite_affected_paths(dir_path: str, new_dir_path: str) -> None:
    """After dir_path has already been physically moved/renamed to
    new_dir_path on disk, update every affected model's filePath (and
    sourcePath, for reference-mode rows) to match -- same lock-step
    requirement as the file-level rename/move endpoint, applied per row.
    Call this immediately after the physical move, passing the OLD
    dir_path so find_affected_models still matches what's in the DB.
    """
    old_prefix = os.path.normpath(dir_path)
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        for row in find_affected_models(dir_path):
            old_fp = os.path.normpath(row["filePath"])
            rel = os.path.relpath(old_fp, old_prefix)
            new_fp = os.path.normpath(os.path.join(new_dir_path, rel)) if rel != "." else os.path.normpath(new_dir_path)
            storage_mode = row["storageMode"] if "storageMode" in row.keys() else "copy"
            if storage_mode == "reference":
                cur.execute(
                    "UPDATE models SET filePath=?, sourcePath=? WHERE id=?",
                    (new_fp, new_fp, row["id"]),
                )
            else:
                cur.execute("UPDATE models SET filePath=? WHERE id=?", (new_fp, row["id"]))
        conn.commit()
    finally:
        conn.close()
