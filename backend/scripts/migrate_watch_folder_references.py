"""One-off migration: convert existing copy-mode models whose source file
lives under a given watch-folder root to reference-mode, and delete their
now-redundant managed copies.

Run once, manually, after deploying in-place file references — see
docs/superpowers/specs/2026-08-02-in-place-file-references-design.md.

Usage:
    cd backend
    .venv/Scripts/python.exe scripts/migrate_watch_folder_references.py "D:/Dropbox/3D Print Files"
"""
import os
import sqlite3
import sys
from pathlib import Path


def _is_under(path_str: str, root_str: str) -> bool:
    try:
        Path(path_str).resolve().relative_to(Path(root_str).resolve())
        return True
    except ValueError:
        return False


def migrate_watch_folder_to_references(conn: sqlite3.Connection, uploads_dir: str, watch_folder_path: str) -> dict:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, sourcePath FROM models WHERE storageMode='copy' AND sourcePath IS NOT NULL"
    ).fetchall()

    migrated = 0
    skipped_missing = []
    for row in rows:
        source_path = row["sourcePath"]
        if not _is_under(source_path, watch_folder_path):
            continue
        if not os.path.exists(source_path):
            skipped_missing.append(row["id"])
            continue

        ext = os.path.splitext(row["name"])[1] or ".stl"
        copy_path = os.path.join(uploads_dir, f"{row['id']}{ext}")
        conn.execute("UPDATE models SET storageMode='reference' WHERE id=?", (row["id"],))
        if os.path.exists(copy_path):
            os.remove(copy_path)
        migrated += 1

    conn.commit()
    return {"migrated": migrated, "skipped_missing": skipped_missing}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: migrate_watch_folder_references.py <watch_folder_path>")
        sys.exit(1)

    db_path = os.getenv("DB_PATH", "data.db")
    uploads_dir = os.getenv("FILE_STORAGE", "./app/uploads")
    conn = sqlite3.connect(db_path)
    result = migrate_watch_folder_to_references(conn, uploads_dir, sys.argv[1])
    conn.close()
    print(f"Migrated: {result['migrated']}")
    if result["skipped_missing"]:
        print(f"Skipped (source file missing, left as copy): {result['skipped_missing']}")
