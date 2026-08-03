import os
import sqlite3
import shutil
import time
import json
from pathlib import Path
from typing import Optional, Dict, Any

DB_PATH = os.getenv("DB_PATH", "data.db")
UPLOAD_DIR = Path(os.getenv("FILE_STORAGE", "./app/uploads"))
MANUAL_DIR = Path(os.getenv("MANUAL_STORAGE", UPLOAD_DIR / "manuals"))
MANUAL_DIR.mkdir(parents=True, exist_ok=True)
WEBUI_URL = os.getenv("WEBUI_URL", "http://localhost:8989")


def get_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS folders (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parentId TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS models (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            folderId TEXT NOT NULL,
            url TEXT NOT NULL,
            size INTEGER,
            dateAdded INTEGER,
            tags TEXT,
            description TEXT,
            thumbnail TEXT,
            manual TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    try:
        cur.execute("ALTER TABLE models ADD COLUMN manual TEXT")
    except sqlite3.OperationalError:
        pass
    for column, coltype in [
        ("author", "TEXT"),
        ("sourceUrl", "TEXT"),
        ("category", "TEXT"),
        ("colorCount", "INTEGER"),
        ("sliceSettings", "TEXT"),
        ("sourcePath", "TEXT"),
        ("storageMode", "TEXT NOT NULL DEFAULT 'copy'"),
    ]:
        try:
            cur.execute(f"ALTER TABLE models ADD COLUMN {column} {coltype}")
        except sqlite3.OperationalError:
            pass
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS watch_folders (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            folderId TEXT NOT NULL,
            frequencyMinutes INTEGER NOT NULL DEFAULT 60,
            lastScanAt INTEGER,
            enabled INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS inbox_items (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            detectedAt INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    if os.getenv("MAKERWORLD_BAMBU_TOKEN"):
        cur.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)",
            ("makerworld_bambu_token", os.getenv("MAKERWORLD_BAMBU_TOKEN")),
        )
    conn.commit()

    cur.execute("SELECT COUNT(*) as c FROM folders")
    if cur.fetchone()[0] == 0:
        seed = [
            ("1", "Characters", None),
            ("2", "Vehicles", None),
            ("3", "Terrain", None),
            ("4", "Tanks", "2"),
        ]
        cur.executemany("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", seed)
        conn.commit()

    conn.close()


def now_ms() -> int:
    return int(time.time() * 1000)


def row_to_folder(row: sqlite3.Row) -> Dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "parentId": row["parentId"]}


def row_to_model(row: sqlite3.Row) -> Dict[str, Any]:
    tags = []
    if row["tags"]:
        try:
            tags = json.loads(row["tags"])
        except Exception:
            tags = []
    storage_mode = row["storageMode"] if "storageMode" in row.keys() else "copy"
    source_path = row["sourcePath"] if "sourcePath" in row.keys() else None
    missing = storage_mode == "reference" and bool(source_path) and not os.path.exists(source_path)
    return {
        "id": row["id"],
        "name": row["name"],
        "folderId": row["folderId"],
        "url": row["url"],
        "size": row["size"],
        "dateAdded": row["dateAdded"],
        "tags": tags,
        "description": row["description"] or "",
        "thumbnail": row["thumbnail"],
        "manual": row["manual"] if "manual" in row.keys() else None,
        "author": row["author"] if "author" in row.keys() else None,
        "sourceUrl": row["sourceUrl"] if "sourceUrl" in row.keys() else None,
        "category": row["category"] if "category" in row.keys() else None,
        "colorCount": row["colorCount"] if "colorCount" in row.keys() else None,
        "sliceSettings": row["sliceSettings"] if "sliceSettings" in row.keys() else None,
        "sourcePath": source_path,
        "storageMode": storage_mode,
        "missing": missing,
    }


def save_upload_file(upload_file, dest_path: str) -> int:
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
    return os.path.getsize(dest_path)


def get_setting(key: str) -> Optional[str]:
    conn = get_db_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    conn = get_db_conn()
    conn.execute(
        "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def clear_setting(key: str) -> None:
    conn = get_db_conn()
    conn.execute("DELETE FROM settings WHERE key=?", (key,))
    conn.commit()
    conn.close()
