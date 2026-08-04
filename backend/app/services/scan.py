import os
import uuid
from pathlib import Path
from typing import List, Optional, Set

from app.db import get_db_conn, now_ms
from app.services.ingestion import ingest_file

SUPPORTED_EXTENSIONS = {".stl", ".3mf", ".obj", ".step", ".stp"}


def get_or_create_folder(name: str, parent_id: Optional[str]) -> str:
    """Looks up a folder by exact (name, parentId) match before creating
    one — idempotent so re-scans of the same watched subdirectory, and
    any manually-created folder that happens to share a name, never
    produce duplicates. `IS` (not `=`) is required for the parentId
    comparison: SQLite's `IS` is NULL-safe, matching a bound NULL
    parameter correctly, where plain `=` never matches NULL at all.
    """
    conn = get_db_conn()
    row = conn.execute(
        "SELECT id FROM folders WHERE name=? AND parentId IS ?",
        (name, parent_id),
    ).fetchone()
    if row:
        conn.close()
        return row["id"]

    folder_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO folders(id,name,parentId) VALUES (?,?,?)",
        (folder_id, name, parent_id),
    )
    conn.commit()
    conn.close()
    return folder_id


def is_supported_3d_file(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def find_new_files(root: Path, already_seen: Set[str]) -> List[Path]:
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            candidate = Path(dirpath) / fname
            if is_supported_3d_file(candidate) and str(candidate) not in already_seen:
                found.append(candidate)
    return found


def scan_watch_folder(watch_folder_row: dict) -> int:
    conn = get_db_conn()
    seen_rows = conn.execute("SELECT sourcePath FROM models WHERE sourcePath IS NOT NULL").fetchall()
    already_seen = {r["sourcePath"] for r in seen_rows}
    conn.close()

    root = Path(watch_folder_row["path"])
    if not root.exists():
        return 0  # folder deleted/unmounted since it was configured — skip, don't crash the loop

    ingested = 0
    for file_path in find_new_files(root, already_seen):
        # A file directly in the watched root has zero relative parts and
        # ingests straight into the target folder, same as before this
        # change. A file under one or more subdirectories walks/creates a
        # matching chain of library folders under the target, mirroring
        # the on-disk structure at whatever depth it's found — this is
        # what lets "PrintA/part1.stl" and "PrintA/supports/part.stl"
        # both land under a real "PrintA" library folder instead of every
        # file from every subdirectory being flattened into one folder.
        relative_parts = file_path.parent.relative_to(root).parts
        target_folder_id = watch_folder_row["folderId"]
        for part in relative_parts:
            target_folder_id = get_or_create_folder(part, target_folder_id)

        try:
            ingest_file(
                str(file_path),
                folder_id=target_folder_id,
                original_filename=file_path.name,
                record_source=True,
                pickup_sidecar_notes=True,
                reference_only=True,
            )
            ingested += 1
        except Exception:
            continue  # one bad file (permission error, vanished mid-scan) doesn't stop the rest

    conn = get_db_conn()
    conn.execute("UPDATE watch_folders SET lastScanAt=? WHERE id=?", (now_ms(), watch_folder_row["id"]))
    conn.commit()
    conn.close()
    return ingested


def default_downloads_dir() -> Path:
    return Path.home() / "Downloads"


def scan_downloads_folder(downloads_dir: Optional[Path] = None) -> int:
    target = downloads_dir if downloads_dir is not None else default_downloads_dir()
    if not target.exists():
        return 0

    conn = get_db_conn()
    seen_models = {r["sourcePath"] for r in conn.execute("SELECT sourcePath FROM models WHERE sourcePath IS NOT NULL")}
    seen_inbox = {r["path"] for r in conn.execute("SELECT path FROM inbox_items")}
    conn.close()
    already_seen = seen_models | seen_inbox

    added = 0
    for file_path in find_new_files(target, already_seen):
        conn = get_db_conn()
        conn.execute(
            "INSERT INTO inbox_items(id,path,detectedAt,status) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), str(file_path), now_ms(), "pending"),
        )
        conn.commit()
        conn.close()
        added += 1
    return added
