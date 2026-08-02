from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_db_conn
from app.services.ingestion import ingest_file
from app.services.scan import scan_downloads_folder

router = APIRouter()


class FileInboxItem(BaseModel):
    folderId: str


def row_to_inbox_item(row) -> dict:
    return {"id": row["id"], "path": row["path"], "detectedAt": row["detectedAt"], "status": row["status"]}


@router.get("/api/inbox")
def list_inbox():
    conn = get_db_conn()
    rows = conn.execute("SELECT * FROM inbox_items WHERE status='pending'").fetchall()
    conn.close()
    return [row_to_inbox_item(r) for r in rows]


@router.post("/api/inbox/{item_id}/file")
def file_inbox_item(item_id: str, payload: FileInboxItem):
    conn = get_db_conn()
    row = conn.execute("SELECT * FROM inbox_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Inbox item not found")

    source_path = Path(row["path"])
    model = ingest_file(
        str(source_path), folder_id=payload.folderId, original_filename=source_path.name,
        move=False, pickup_sidecar_notes=True,
    )

    conn.execute("UPDATE inbox_items SET status='filed' WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return model


@router.post("/api/inbox/{item_id}/dismiss")
def dismiss_inbox_item(item_id: str):
    conn = get_db_conn()
    row = conn.execute("SELECT * FROM inbox_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Inbox item not found")
    conn.execute("UPDATE inbox_items SET status='dismissed' WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/inbox/scan-now")
def inbox_scan_now():
    count = scan_downloads_folder()
    return {"added": count}
